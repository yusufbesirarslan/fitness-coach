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
    out_bytes, mime = _compress_image_for_vision(_png_bytes())
    assert mime == "image/jpeg"
    img = Image.open(io.BytesIO(out_bytes))
    assert img.format == "JPEG"
    assert max(img.size) <= 1600


def test_compress_passes_through_non_image(app):
    out_bytes, mime = _compress_image_for_vision(b"gorsel degil")
    assert out_bytes == b"gorsel degil"
    assert mime == "image/jpeg"


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
