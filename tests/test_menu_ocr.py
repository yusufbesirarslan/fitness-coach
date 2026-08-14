"""Tests for the menu OCR layer (app/services/menu_ocr.py).

Metin temizleme (mojibake/boşluk), PDF metin çıkarımı (gerçek minimal PDF +
bozuk dosya), vision için görsel sıkıştırma ve OpenAI vision OCR sarmalayıcısı
(istemci mock'lu — ağ yok).

    python -m pytest tests/test_menu_ocr.py -v
"""
import io

import pytest
from PIL import Image

from app.services import menu_ocr
from app.services import vision_images
from app.services.menu_ocr import (
    _compress_image_for_vision,
    _extract_text_from_image,
    _extract_text_from_pdf,
    _sanitize_menu_text,
)


# ---------------------------------------------------------------------------
# Metin temizleme
# ---------------------------------------------------------------------------

def test_sanitize_fixes_mojibake_and_whitespace():
    # UTF-8'in latin-1 okunması: 'ı' → 'Ä±' (C4 B1), 'ğ' → 'Ä\x9f' (C4 9F).
    assert _sanitize_menu_text("KahvaltÄ±   tabaÄÄ±") == "Kahvaltı tabağı"
    assert _sanitize_menu_text("a\n\n\n\n\nb") == "a\n\nb"
    assert _sanitize_menu_text("") == ""
    assert _sanitize_menu_text(None) == ""


def test_sanitize_repairs_lowercase_turkish_mojibake():
    # A1: eski elle-yazılı tablo küçük harf ü/ş/ç onarımlarını çakışan anahtarlar
    # yüzünden kaybediyordu. Guard'lı round-trip hepsini onarır.
    # "Güveç şiş" → UTF-8 baytları latin-1 olarak çözülmüş hâli.
    clean = "Güveç şiş"
    # UTF-8 baytlarını latin-1 sanarak çöz → klasik mojibake (Ã¼/Ã§/ÅŸ ...).
    mojibake = clean.encode("utf-8").decode("latin-1")
    assert _sanitize_menu_text(mojibake) == clean
    # Zaten temiz Türkçe metin bozulmadan geçer (ı/ş latin-1'e kodlanamaz → atlanır).
    assert _sanitize_menu_text("Mercimek Çorbası ışıl") == "Mercimek Çorbası ışıl"


# ---------------------------------------------------------------------------
# PDF çıkarımı
# ---------------------------------------------------------------------------

def _minimal_pdf(text="Mercimek Corbasi 45 TL"):
    """Tek sayfalık, metin içeren geçerli bir PDF üret (xref ofsetleri doğru)."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(len(objs) + 1).encode() +
              b" /Root 1 0 R >>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF")
    return out.getvalue()


def test_pdf_text_extraction(app):
    text = _extract_text_from_pdf(_minimal_pdf())
    assert "Mercimek Corbasi 45 TL" in text


def test_pdf_corrupt_raises_value_error(app):
    with pytest.raises(ValueError, match="PDF_CORRUPT"):
        _extract_text_from_pdf(b"bu bir pdf degil")


def test_scanned_pdf_falls_back_to_vision_ocr(app, monkeypatch):
    # Metinsiz (taranmış) sayfa → vision OCR'a yönlendirilmeli.
    monkeypatch.setattr(menu_ocr, "_extract_pdf_pages_via_vision",
                        lambda pdf_bytes, pages: "Adana Kebap 250")
    text = _extract_text_from_pdf(_minimal_pdf(text=" "))
    assert "Adana Kebap" in text


# ---------------------------------------------------------------------------
# Görsel sıkıştırma
# ---------------------------------------------------------------------------

def _png_bytes(size=(2000, 2000)):
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, format="PNG")
    return buf.getvalue()


def test_compress_resizes_and_converts_to_jpeg(app):
    out_bytes, mime = _compress_image_for_vision(_png_bytes() + b"\x00" * 1_500_001, "image/png")
    assert mime == "image/jpeg"
    img = Image.open(io.BytesIO(out_bytes))
    assert img.format == "JPEG"
    assert max(img.size) <= 1600


def test_compress_passes_through_non_image(app):
    out_bytes, mime = _compress_image_for_vision(b"gorsel degil", "image/jpeg")
    assert out_bytes == b"gorsel degil"
    assert mime == "image/jpeg"


def test_compress_rejects_decompression_bomb(app, monkeypatch):
    # Tavanı küçült: 2000x2000 (4 MP) görsel sınırı aşar → decode etmeden reddedilir (3.1).
    monkeypatch.setattr(vision_images, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(menu_ocr.ImageTooLargeError):
        _compress_image_for_vision(_png_bytes(size=(2000, 2000)) + b"\x00" * 1_500_001, "image/png")


def test_extract_text_returns_empty_on_oversized_image(app, monkeypatch):
    monkeypatch.setattr(vision_images, "MAX_IMAGE_PIXELS", 100)
    # >1.5MB olsun ki compress çağrılsın; trailing padding PNG'yi bozmaz.
    payload = _png_bytes(size=(2000, 2000)) + b"\x00" * 1_500_001
    assert _extract_text_from_image(payload, "image/png") == ""


# ---------------------------------------------------------------------------
# Vision OCR sarmalayıcısı
# ---------------------------------------------------------------------------

class _FakeVision:
    def __init__(self, reply):
        self.calls = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)

                class _Msg:
                    content = reply

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]
                return _Resp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_vision_ocr_returns_text_and_sends_data_url(app, monkeypatch):
    fake = _FakeVision("  Mercimek Çorbası\nAdana Kebap  ")
    monkeypatch.setattr(menu_ocr, "openai_client", fake)
    text = _extract_text_from_image(b"kucuk gorsel", "image/png")
    assert text == "Mercimek Çorbası\nAdana Kebap"
    image_part = fake.calls[0]["messages"][1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_vision_ocr_unknown_mime_falls_back_to_jpeg(app, monkeypatch):
    fake = _FakeVision("x")
    monkeypatch.setattr(menu_ocr, "openai_client", fake)
    _extract_text_from_image(b"img", "application/octet-stream")
    image_part = fake.calls[0]["messages"][1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_vision_ocr_api_failure_returns_empty(app, monkeypatch):
    class _Boom:
        def __getattr__(self, name):
            raise RuntimeError("openai down")
    monkeypatch.setattr(menu_ocr, "openai_client", _Boom())
    assert _extract_text_from_image(b"img") == ""


def test_vision_ocr_empty_choices_returns_empty(app, monkeypatch):
    # A2: içerik filtresi boş choices döndürebilir. Guard olmadan resp.choices[0]
    # IndexError fırlatır; guard varsa "" döner. choices hem falsy hem de indeksleme
    # yapılınca "sızıntı" üretecek şekilde kurulur — yalnızca guard çalışırsa "" gelir.
    class _Choices:
        def __bool__(self): return False
        def __len__(self): return 0
        def __getitem__(self, i):
            return type("C", (), {"message": type("M", (), {"content": "SIZAN"})()})()

    class _Resp:
        choices = _Choices()

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _Resp()
    monkeypatch.setattr(menu_ocr, "openai_client", _Client())
    assert _extract_text_from_image(b"img", "image/png") == ""


def test_vision_ocr_compresses_oversized_image(app, monkeypatch):
    fake = _FakeVision("ok")
    monkeypatch.setattr(menu_ocr, "openai_client", fake)
    compressed = {}
    monkeypatch.setattr(menu_ocr, "_compress_image_for_vision",
                        lambda b, *a, **k: (compressed.setdefault("hit", True), (b"tiny", "image/jpeg"))[1])
    _extract_text_from_image(b"x" * 1_500_001, "image/png")  # >1.5MB → sıkıştırılır
    assert compressed.get("hit") is True
    image_part = fake.calls[0]["messages"][1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


# ---------------------------------------------------------------------------
# _extract_pdf_pages_via_vision — taranmış sayfaları görsele çevirip OCR'a verir.
# ---------------------------------------------------------------------------

class _FakePage:
    def __init__(self, raises=False):
        self._raises = raises

    def to_image(self, resolution=200):
        if self._raises:
            raise RuntimeError("render failed")

        class _Original:
            def save(self, buf, format):
                buf.write(b"FAKEPNGBYTES")

        return type("Img", (), {"original": _Original()})()


class _FakePdf:
    def __init__(self, pages):
        self.pages = pages
        self.closed = False

    def close(self):
        self.closed = True


def test_extract_pdf_pages_via_vision_renders_and_ocrs(app, monkeypatch):
    import pdfplumber
    pdf = _FakePdf([_FakePage(), _FakePage()])
    monkeypatch.setattr(pdfplumber, "open", lambda b: pdf)
    monkeypatch.setattr(menu_ocr, "_extract_text_from_image",
                        lambda img_bytes, content_type: "Adana Kebap 250")
    with app.app_context():
        out = menu_ocr._extract_pdf_pages_via_vision(b"pdfdata", [0, 1])
    assert "[Sayfa 1]" in out and "[Sayfa 2]" in out
    assert out.count("Adana Kebap 250") == 2
    assert pdf.closed is True  # finally: pdf.close()


def test_extract_pdf_pages_via_vision_skips_out_of_range_and_render_errors(app, monkeypatch):
    import pdfplumber
    # Tek sayfa; index 5 aralık dışı (atlanır), sayfa 0 render'da patlar (yutulur).
    monkeypatch.setattr(pdfplumber, "open", lambda b: _FakePdf([_FakePage(raises=True)]))
    monkeypatch.setattr(menu_ocr, "_extract_text_from_image",
                        lambda *a, **k: "ASLA ÇAĞRILMAZ")
    with app.app_context():
        out = menu_ocr._extract_pdf_pages_via_vision(b"pdfdata", [5, 0])
    assert out == ""  # hiçbir sayfa OCR metni üretmedi


def test_extract_pdf_pages_via_vision_open_failure_returns_empty(app, monkeypatch):
    import pdfplumber
    def boom(b):
        raise RuntimeError("cannot open")
    monkeypatch.setattr(pdfplumber, "open", boom)
    with app.app_context():
        assert menu_ocr._extract_pdf_pages_via_vision(b"pdfdata", [0]) == ""
