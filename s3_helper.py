"""S3 yardımcı modülü — kullanıcı görsellerini (Pump Check doğrulamaları, öğün
fotoğrafları) özel bir S3 bucket'ına yükler ve gösterim için geçici pre-signed
URL üretir.

Kimlik doğrulama: boto3 client'ı kimlik bilgilerini EC2 IAM Instance Profile'dan
OTOMATİK alır. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY ne okunur ne de hardcode
edilir — kimlik bilgileri EC2 IAM rolü tarafından güvenle sağlanır.

Bucket "Block all public access" (özel) olduğu için nesneler doğrudan açılamaz;
frontend görselleri yalnızca generate_presigned_url() ile üretilen kısa ömürlü
URL'lerle render eder.

boto3 lokal geliştirmede kurulu olmayabilir; import korumalıdır, böylece
starter.py lokalde de sorunsuz import edilir. is_enabled() False dönerse çağıran
taraflar görsel yüklemeyi sessizce atlar (temel akış bloklanmaz).
"""
import os
import uuid
from datetime import datetime

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    _BOTO3_AVAILABLE = True
except Exception as _imp_err:  # boto3 kurulu değil (örn. lokal geliştirme)
    boto3 = None
    BotoCoreError = ClientError = Exception
    _BOTO3_AVAILABLE = False
    print(f"[S3] boto3 import edilemedi, S3 devre dışı: {type(_imp_err).__name__}: {_imp_err}")

# EC2 örneğinin bulunduğu bölge (bkz. .env). Yoksa boto3 metadata'dan çözer.
AWS_REGION = os.environ.get("AWS_REGION", "eu-central-1")
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME", "").strip()

# İçerik tipinden dosya uzantısı (benzersiz anahtar üretirken kullanılır).
_EXT_BY_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}

_client = None  # tembel (lazy) oluşturulup önbelleğe alınır


class S3Error(Exception):
    """S3 işlemi başarısız olduğunda yükseltilir; çağıran taraf yakalayıp
    temel kullanıcı akışını bloklamadan zarifçe düşebilsin diye."""


def is_enabled():
    """boto3 mevcut VE bir bucket yapılandırılmışsa True. Aksi halde çağıranlar
    yükleme/presign adımlarını atlar (lokal geliştirme güvenli kalır)."""
    return _BOTO3_AVAILABLE and bool(S3_BUCKET_NAME)


def _get_client():
    """S3 client'ını tembel oluştur ve önbelleğe al. Kimlik bilgileri IAM
    Instance Profile'dan otomatik gelir — açıkça anahtar verilmez."""
    global _client
    if not is_enabled():
        raise S3Error("S3 yapılandırılmadı (boto3 yok ya da S3_BUCKET_NAME boş).")
    if _client is None:
        _client = boto3.client("s3", region_name=AWS_REGION)
    return _client


def _build_key(prefix, content_type, user_id):
    """Çakışmayı önlemek için global benzersiz bir nesne anahtarı üret:
    <prefix>/<user_id>/<YYYY>/<MM>/<uuid4hex>.<ext>"""
    ext = _EXT_BY_MIME.get((content_type or "").lower(), "bin")
    now = datetime.utcnow()
    uid = user_id if user_id is not None else "anon"
    return f"{prefix}/{uid}/{now:%Y/%m}/{uuid.uuid4().hex}.{ext}"


def upload_image(image_bytes, content_type="image/jpeg", prefix="uploads", user_id=None):
    """Bir görseli S3'e yükle ve nesne anahtarını döndür. Anahtar UUID ile
    global benzersizdir, böylece var olan nesnelerin üzerine yazılmaz.

    Hata durumunda S3Error yükseltir (çağıran taraf yakalayıp fail-open olur)."""
    if not image_bytes:
        raise S3Error("Yüklenecek görsel verisi boş.")
    key = _build_key(prefix, content_type, user_id)
    try:
        client = _get_client()
        client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=key,
            Body=image_bytes,
            ContentType=content_type or "application/octet-stream",
            ServerSideEncryption="AES256",
        )
        print(f"[S3] Yüklendi: s3://{S3_BUCKET_NAME}/{key} ({len(image_bytes)} bayt)")
        return key
    except (BotoCoreError, ClientError) as e:
        print(f"[S3] Yükleme başarısız ({key}): {type(e).__name__}: {e}")
        raise S3Error(str(e)) from e


def generate_presigned_url(key, expires_in=3600):
    """Özel bir nesne için geçici pre-signed GET URL'i üret (varsayılan 1 saat).
    Frontend görseli bu URL ile güvenle render eder. Hata/boş anahtarda None."""
    if not key:
        return None
    try:
        client = _get_client()
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET_NAME, "Key": key},
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError, S3Error) as e:
        print(f"[S3] Pre-signed URL üretilemedi ({key}): {type(e).__name__}: {e}")
        return None
