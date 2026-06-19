"""Unit tests for the input validators (app/services/validators.py).

Bu fonksiyonlar güvenlik sınırıdır: profil fotoğrafı doğrulaması stored-XSS /
SSRF / decompression-bomb savunması, username regex'i HTML/JS/URL bağlamlarına
güvenli interpolasyon, şifre kuralları brute-force barajıdır. Saf fonksiyon
testleri: DB / ağ / OpenAI çağrısı yok (Pillow gerçek görsel doğrulamada kullanılır).

    python -m pytest tests/test_validators.py -v
"""
import base64
import io
import struct
import zlib

import pytest
from PIL import Image

from app.services.validators import (
    _to_float,
    _to_int,
    _verify_image_bytes,
    validate_email,
    validate_meal_photo,
    validate_password,
    validate_profile_picture,
    validate_pump_check_image,
    validate_username,
)


def _image_data_url(fmt="PNG", size=(2, 2)):
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, format=fmt)
    mime = "jpeg" if fmt == "JPEG" else fmt.lower()
    return f"data:image/{mime};base64," + base64.b64encode(buf.getvalue()).decode()


def _bomb_png_data_url():
    """Geçerli ama 50000×50000 (2.5 gigapiksel) bildiren minimal PNG — açılırsa
    belleği şişirir; Pillow'un MAX_IMAGE_PIXELS sınırına takılmalı."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", 50_000, 50_000, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(b"\x00" * 10))
           + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


# ---------------------------------------------------------------------------
# _to_float — kullanıcı JSON'undan gelen sayılar 500 yerine default'a düşmeli.
# ---------------------------------------------------------------------------

def test_to_float_accepts_numbers_and_numeric_strings():
    assert _to_float(3) == 3.0
    assert _to_float("2.5") == 2.5
    assert _to_float(0) == 0.0


def test_to_float_falls_back_on_garbage():
    assert _to_float(None) == 0.0
    assert _to_float("abc") == 0.0
    assert _to_float({"x": 1}, default=7.5) == 7.5
    assert _to_float("", default=None) is None


def test_to_int_accepts_numeric_and_falls_back():
    assert _to_int("3.0") == 3      # önce float'tan geçer
    assert _to_int(7) == 7
    assert _to_int("yüksek") == 0   # geçersiz → default
    assert _to_int(None, default=-1) == -1
    assert _to_int("", default=5) == 5


# ---------------------------------------------------------------------------
# _verify_image_bytes — sahte image/* MIME ve desteklenmeyen format reddi.
# ---------------------------------------------------------------------------

def test_verify_image_accepts_real_image(app):
    buf = io.BytesIO()
    Image.new("RGB", (3, 3), "blue").save(buf, format="PNG")
    with app.app_context():
        assert _verify_image_bytes(buf.getvalue()) is None


def test_verify_image_rejects_garbage(app):
    with app.app_context():
        assert _verify_image_bytes(b"not an image at all") == "Geçerli bir görsel dosyası değil."


def test_verify_image_rejects_unsupported_format(app):
    # Gerçek bir görsel ama izinli set (JPEG/PNG/GIF/WEBP) dışında → reddedilir.
    buf = io.BytesIO()
    Image.new("RGB", (3, 3), "green").save(buf, format="BMP")
    with app.app_context():
        assert _verify_image_bytes(buf.getvalue()) == "Desteklenmeyen görsel formatı."


# ---------------------------------------------------------------------------
# validate_username — [A-Za-z0-9_.-] dışı her şey reddedilir (stored XSS savunması).
# ---------------------------------------------------------------------------

def test_username_valid_charset_passes():
    assert validate_username("ali_baba-1.2") is None
    assert validate_username("abc") is None
    assert validate_username("A" * 80) is None


@pytest.mark.parametrize("bad", [
    None, "", "ab",                      # boş / çok kısa
    "A" * 81,                            # çok uzun
    "ali baba",                          # boşluk
    "ali<script>",                       # HTML meta karakterleri
    "şeker",                             # regex ASCII dışını kabul etmez
    "user@host",
])
def test_username_invalid_inputs_rejected(bad):
    assert validate_username(bad) is not None


# ---------------------------------------------------------------------------
# validate_password — min 8, harf + rakam, 128 üst sınırı (hash DoS koruması).
# ---------------------------------------------------------------------------

def test_password_strong_enough_passes():
    assert validate_password("Sifre123") is None
    assert validate_password("a1" * 64) is None  # tam 128 karakter


@pytest.mark.parametrize("bad", [
    None, "", "kisa1",        # çok kısa
    "a1" * 65,                # 130 karakter — üst sınır aşıldı
    "12345678",               # harf yok
    "abcdefgh",               # rakam yok
])
def test_password_weak_inputs_rejected(bad):
    assert validate_password(bad) is not None


# ---------------------------------------------------------------------------
# validate_email
# ---------------------------------------------------------------------------

def test_email_valid_passes():
    assert validate_email("a@b.co") is None
    assert validate_email("yusuf.besir+test@example.com") is None


@pytest.mark.parametrize("bad", [
    None, "", "a@b", "a b@c.de", "@b.co", "a@.co",
    "a" * 116 + "@b.co",  # 121 karakter — 120 sınırı aşılır
])
def test_email_invalid_inputs_rejected(bad):
    assert validate_email(bad) is not None


# ---------------------------------------------------------------------------
# validate_profile_picture — yalnızca gerçek base64 image data-URL kabul edilir.
# ---------------------------------------------------------------------------

def test_profile_picture_empty_means_remove():
    assert validate_profile_picture("") == (None, None)
    assert validate_profile_picture(None) == (None, None)


def test_profile_picture_real_png_accepted():
    value = _image_data_url("PNG")
    cleaned, error = validate_profile_picture(value)
    assert error is None
    assert cleaned == value


@pytest.mark.parametrize("bad", [
    "https://evil.example/x.png",                     # dış URL — SSRF/tracking
    "javascript:alert(1)",                            # XSS payload
    "data:text/html;base64,PHNjcmlwdD4=",             # image olmayan MIME
    "data:image/svg+xml;base64,PHN2Zz4=",             # SVG (script taşıyabilir) listede yok
    "data:image/png;base64,%%%not-base64%%%",         # bozuk base64 gövdesi
])
def test_profile_picture_non_image_payloads_rejected(bad):
    cleaned, error = validate_profile_picture(bad)
    assert cleaned is None
    assert error is not None


def test_profile_picture_fake_image_mime_rejected():
    # image/png MIME'ı giymiş rastgele baytlar — Pillow doğrulaması yakalamalı.
    fake = "data:image/png;base64," + base64.b64encode(b"definitely not a png").decode()
    cleaned, error = validate_profile_picture(fake)
    assert cleaned is None
    assert error is not None


def test_profile_picture_decompression_bomb_rejected():
    cleaned, error = validate_profile_picture(_bomb_png_data_url())
    assert cleaned is None
    assert error is not None


def test_profile_picture_oversized_payload_rejected():
    value = "data:image/png;base64," + "A" * 500_001
    cleaned, error = validate_profile_picture(value)
    assert cleaned is None
    assert "büyük" in error


# ---------------------------------------------------------------------------
# validate_pump_check_image / validate_meal_photo — data-URL → ham bayt çözümü.
# ---------------------------------------------------------------------------

def test_pump_check_image_required():
    image_bytes, content_type, error = validate_pump_check_image("")
    assert image_bytes is None
    assert "gerekli" in error


def test_pump_check_image_decodes_png_and_jpeg():
    image_bytes, content_type, error = validate_pump_check_image(_image_data_url("PNG"))
    assert error is None
    assert content_type == "image/png"
    assert image_bytes[:8] == b"\x89PNG\r\n\x1a\n"

    image_bytes, content_type, error = validate_pump_check_image(_image_data_url("JPEG"))
    assert error is None
    assert content_type == "image/jpeg"


def test_pump_check_image_too_big_rejected():
    value = "data:image/png;base64," + "A" * 8_000_001
    image_bytes, _, error = validate_pump_check_image(value)
    assert image_bytes is None
    assert "büyük" in error


def test_meal_photo_is_optional():
    assert validate_meal_photo("") == (None, None, None)
    assert validate_meal_photo(None) == (None, None, None)


def test_meal_photo_valid_and_invalid():
    image_bytes, content_type, error = validate_meal_photo(_image_data_url("JPEG"))
    assert error is None
    assert content_type == "image/jpeg"

    image_bytes, _, error = validate_meal_photo("https://evil.example/x.jpg")
    assert image_bytes is None
    assert error is not None


def test_pump_check_image_undecodable_base64_rejected():
    # Regex'e uyan ama base64 olarak çözülemeyen gövde (geçersiz uzunluk) → decode_failed.
    image_bytes, _, error = validate_pump_check_image("data:image/png;base64,A")
    assert image_bytes is None
    assert "çözümlenemedi" in error


def test_pump_check_image_valid_dataurl_but_not_an_image_rejected():
    # Geçerli base64 ama görsel DEĞİL (sahte image/* MIME) → bad_image.
    fake = base64.b64encode(b"this is plain text, not an image").decode()
    image_bytes, _, error = validate_pump_check_image(f"data:image/png;base64,{fake}")
    assert image_bytes is None
    assert "görsel" in error


def test_profile_picture_undecodable_base64_rejected():
    cleaned, error = validate_profile_picture("data:image/png;base64,A")
    assert cleaned is None
    assert "çözümlenemedi" in error


# ---------------------------------------------------------------------------
# _meal_photo_url — S3 etkinse pre-signed URL üretir, değilse None.
# ---------------------------------------------------------------------------

def test_meal_photo_url_none_without_key_or_s3(monkeypatch):
    import app.services.validators as v
    from types import SimpleNamespace
    monkeypatch.setattr(v.s3_helper, "is_enabled", lambda: True)
    # anahtar yok → None
    assert v._meal_photo_url(SimpleNamespace(photo_key=None, user_id=1)) is None
    # anahtar var ama S3 kapalı → None
    monkeypatch.setattr(v.s3_helper, "is_enabled", lambda: False)
    assert v._meal_photo_url(SimpleNamespace(photo_key="k", user_id=1)) is None


def test_meal_photo_url_returns_presigned_when_enabled(monkeypatch):
    import app.services.validators as v
    from types import SimpleNamespace
    monkeypatch.setattr(v.s3_helper, "is_enabled", lambda: True)
    captured = {}

    def fake_presign(key, expires_in, expected_user_id):
        captured.update(key=key, user=expected_user_id)
        return "https://signed.example/x"

    monkeypatch.setattr(v.s3_helper, "generate_presigned_url", fake_presign)
    url = v._meal_photo_url(SimpleNamespace(photo_key="meals/7.jpg", user_id=42))
    assert url == "https://signed.example/x"
    assert captured == {"key": "meals/7.jpg", "user": 42}
