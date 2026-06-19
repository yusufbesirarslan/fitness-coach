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
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    _BOTO3_AVAILABLE = True
except Exception as _imp_err:  # boto3 kurulu değil (örn. lokal geliştirme)
    boto3 = None
    BotoCoreError = ClientError = Exception
    _BOTO3_AVAILABLE = False
    logger.warning("[S3] boto3 import edilemedi, S3 devre dışı: %s: %s", type(_imp_err).__name__, _imp_err)

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

# Ters eşleme: nesne anahtarındaki uzantıdan media_type (Bedrock görsel bloğu
# media_type'ı bekler). Bilinmeyen uzantı → güvenli varsayılan image/jpeg.
_MIME_BY_EXT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


def media_type_for_key(key):
    """Bir S3 anahtarının uzantısından Bedrock-uyumlu media_type döndür."""
    ext = (key or "").rsplit(".", 1)[-1].lower() if "." in (key or "") else ""
    return _MIME_BY_EXT.get(ext, "image/jpeg")

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
        logger.info("[S3] Yüklendi: s3://%s/%s (%d bayt)", S3_BUCKET_NAME, key, len(image_bytes))
        return key
    except (BotoCoreError, ClientError) as e:
        logger.warning("[S3] Yükleme başarısız (%s): %s: %s", key, type(e).__name__, e)
        raise S3Error(str(e)) from e


def get_object_bytes(key, expected_user_id=None):
    """Bir S3 nesnesinin ham baytlarını indir (örn. Bedrock'a görsel bloğu olarak
    göndermek için). Hata/boş anahtarda S3Error yükseltir — çağıran yakalayıp
    zarifçe düşer.

    expected_user_id verilirse, anahtarın o kullanıcıya ait olduğu (yol içinde
    '/<user_id>/' geçtiği — bkz _build_key) ÖNCE doğrulanır; eşleşmezse indirme
    yapılmaz. LLM'in sağladığı anahtarlarda IDOR sınırı (savunma derinliği)."""
    if not key:
        raise S3Error("Nesne anahtarı boş.")
    if expected_user_id is not None and f"/{expected_user_id}/" not in key:
        logger.warning("[S3] İndirme reddedildi: anahtar (%s) kullanıcı %s ile eşleşmiyor.",
                       key, expected_user_id)
        raise S3Error("Sahiplik doğrulaması başarısız.")
    try:
        client = _get_client()
        resp = client.get_object(Bucket=S3_BUCKET_NAME, Key=key)
        return resp["Body"].read()
    except (BotoCoreError, ClientError) as e:
        logger.warning("[S3] İndirme başarısız (%s): %s: %s", key, type(e).__name__, e)
        raise S3Error(str(e)) from e


def generate_presigned_url(key, expires_in=3600, expected_user_id=None):
    """Özel bir nesne için geçici pre-signed GET URL'i üret (varsayılan 1 saat).
    Frontend görseli bu URL ile güvenle render eder. Hata/boş anahtarda None.

    expected_user_id verilirse, anahtarın o kullanıcıya ait olduğu (yol içinde
    '/<user_id>/' geçtiği — bkz _build_key) doğrulanır; eşleşmezse imzalanmaz.
    Sahiplik sınırında savunma derinliği (S5)."""
    if not key:
        return None
    if expected_user_id is not None and f"/{expected_user_id}/" not in key:
        logger.warning("[S3] Pre-signed URL reddedildi: anahtar (%s) kullanıcı %s ile eşleşmiyor.",
                       key, expected_user_id)
        return None
    try:
        client = _get_client()
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET_NAME, "Key": key},
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError, S3Error) as e:
        logger.warning("[S3] Pre-signed URL üretilemedi (%s): %s: %s", key, type(e).__name__, e)
        return None
