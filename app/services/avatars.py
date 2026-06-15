"""Avatar (profil fotoğrafı) depolama yardımcısı.

S3 yapılandırılmışsa avatar oraya yüklenir ve yalnızca nesne anahtarı DB'de
tutulur (User.profile_picture_key); böylece ~245 KB base64 her HTML yanıtına
gömülmez ve görsel ayrı, önbelleklenebilir bir kaynak olur. S3 kapalıysa (lokal
geliştirme, test) eski base64 davranışına zarifçe düşülür — temel akış bloklanmaz.

Gösterim tarafı: User.avatar_src (modelde) anahtardan pre-signed URL üretir ya da
eski base64'ü döndürür.
"""
import s3_helper
from app.services.validators import _decode_data_url_image

# 500 KB üst sınır (base64 dize uzunluğu) — yükleme guard'ı olarak korunur.
_MAX_AVATAR_LEN = 500_000

_ERRORS = {
    "too_big": "Profil fotoğrafı çok büyük (maks 2MB).",
    "bad_format": "Geçersiz profil fotoğrafı formatı.",
    "decode_failed": "Profil fotoğrafı çözümlenemedi.",
    "bad_image": "Geçerli bir görsel dosyası değil.",
}


def set_user_avatar(user, data_url):
    """Kullanıcının avatarını ayarla/kaldır. Hata mesajı (str) veya None döner.

    - Boş değer → avatarı kaldır (hem S3 anahtarı hem base64 temizlenir).
    - S3 açık → S3'e yükle, anahtarı sakla, base64'ü temizle.
    - S3 kapalı veya yükleme hatası → base64'ü kolonda sakla (eski davranış).

    Çağıran commit eder.
    """
    data_url = (data_url or "").strip()
    if not data_url:
        user.profile_picture = None
        user.profile_picture_key = None
        return None

    image_bytes, content_type, code = _decode_data_url_image(data_url, _MAX_AVATAR_LEN)
    if code:
        return _ERRORS.get(code, "Geçersiz profil fotoğrafı.")

    if s3_helper.is_enabled():
        try:
            key = s3_helper.upload_image(image_bytes, content_type=content_type,
                                         prefix="avatars", user_id=user.id)
            user.profile_picture_key = key
            user.profile_picture = None  # base64'ü HTML yanıtlarından çıkar
            return None
        except s3_helper.S3Error:
            # S3 geçici hatası — kullanıcıyı bloklamadan base64'e düş.
            pass

    user.profile_picture = data_url
    user.profile_picture_key = None
    return None
