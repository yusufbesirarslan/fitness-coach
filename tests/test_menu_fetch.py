"""Tests for the menu fetch layer (app/services/menu_fetch.py).

SSRF koruması bu dosyanın kalbi: iç-ağ IP'leri (alternatif kodlamalar ve
IPv4-mapped IPv6 dahil) engellenir, her redirect hop'u yeniden doğrulanır ve
doğrulanan IP DNS-rebinding'e karşı pinlenir. Google Drive indirme hattı da
(PDF/metin/görsel dispatch) sahte yanıtlarla test edilir. Ağ YOK.

    python -m pytest tests/test_menu_fetch.py -v
"""
import socket

import pytest
import requests

from app.services import menu_fetch
from app.services.menu_fetch import (
    _extract_drive_file_id,
    _get_drive_direct_url,
    _is_google_drive_url,
    _is_safe_public_ip,
    _pin_getaddrinfo,
    _resolve_host_safely,
    _validate_menu_url,
)


# ---------------------------------------------------------------------------
# IP güvenlik sınıflandırması
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34", "2001:4860:4860::8888"])
def test_public_ips_allowed(ip):
    assert _is_safe_public_ip(ip) is True


@pytest.mark.parametrize("ip", [
    "10.0.0.1", "172.16.5.5", "192.168.1.1",   # RFC1918
    "127.0.0.1", "::1",                        # loopback
    "169.254.169.254", "fe80::1",              # link-local (cloud metadata!)
    "224.0.0.1", "0.0.0.0",                    # multicast / unspecified
    "::ffff:10.0.0.1",                         # IPv4-mapped IPv6 kaçağı
    "fc00::1",                                 # IPv6 ULA
    "100.64.1.1",                              # CGNAT / paylaşımlı adres alanı (S1, RFC 6598)
    "::ffff:100.64.1.1",                       # CGNAT, IPv4-mapped IPv6 kaçağı
    "198.18.0.1",                              # benchmark bloğu (RFC 2544)
    "192.0.2.10",                              # TEST-NET-1
    "saçma", "",                               # IP bile değil
])
def test_internal_and_invalid_ips_blocked(ip):
    assert _is_safe_public_ip(ip) is False


def test_resolve_host_blocks_private_resolution(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, *a, **kw: [(socket.AF_INET, 1, 6, "", ("10.0.0.5", 0))])
    with pytest.raises(ValueError, match="İç ağ"):
        _resolve_host_safely("evil.example")


def test_resolve_host_returns_public_ips(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, *a, **kw: [(socket.AF_INET, 1, 6, "", ("93.184.216.34", 0))])
    assert _resolve_host_safely("example.com") == ["93.184.216.34"]


def test_resolve_host_dns_failure(monkeypatch):
    def boom(host, port, *a, **kw):
        raise socket.gaierror("NXDOMAIN")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(ValueError, match="çözümlenemedi"):
        _resolve_host_safely("yok.example")
    with pytest.raises(ValueError):
        _resolve_host_safely("")


def test_pin_getaddrinfo_pins_only_target_host():
    original = socket.getaddrinfo
    with _pin_getaddrinfo("pinned.example", "9.9.9.9"):
        result = socket.getaddrinfo("pinned.example", 443)
        assert result == [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("9.9.9.9", 443))]
    assert socket.getaddrinfo is original  # çıkışta restore edilir


# ---------------------------------------------------------------------------
# URL doğrulama / temizleme
# ---------------------------------------------------------------------------

def test_validate_url_blocks_internal_hosts():
    # localhost gerçekten 127.0.0.1'e çözülür — mock'suz uçtan uca koruma.
    _, _, err = _validate_menu_url("http://localhost/menu")
    assert err == "İç ağ adresleri engellendi."
    # Port yok → host kontrolü çalışır (port 8080 olsaydı önce port kuralına takılırdı,
    # bkz test_validate_url_blocks_nonstandard_ports).
    _, _, err = _validate_menu_url("http://127.0.0.1/admin")
    assert err == "İç ağ adresleri engellendi."


def test_validate_url_formats(monkeypatch):
    monkeypatch.setattr(menu_fetch, "_resolve_host_safely", lambda h: ["93.184.216.34"])

    _, _, err = _validate_menu_url("")
    assert err == "URL gerekli."
    _, _, err = _validate_menu_url("!!!")
    assert err == "Geçersiz URL formatı."

    parsed, clean, err = _validate_menu_url("restoran.example/menu")
    assert err is None
    assert clean == "https://restoran.example/menu"  # şema otomatik eklenir


def test_validate_url_strips_tracking_params_and_fragment(monkeypatch):
    monkeypatch.setattr(menu_fetch, "_resolve_host_safely", lambda h: ["93.184.216.34"])
    _, clean, err = _validate_menu_url(
        "https://restoran.example/menu?utm_source=ig&fbclid=x&kategori=ana#bolum")
    assert err is None
    assert clean == "https://restoran.example/menu?kategori=ana"


@pytest.mark.parametrize("url", [
    "https://restoran.example:22/menu",      # SSH — iç servis tarama yüzeyi
    "http://restoran.example:6379/menu",     # Redis
    "https://restoran.example:99999/menu",   # bozuk port → .port ValueError
])
def test_validate_url_blocks_nonstandard_ports(url):
    # Port kontrolü DNS çözümlemeden ÖNCE — mock gerekmez (S3, SSRF).
    _, _, err = _validate_menu_url(url)
    assert err == "Yalnızca 80/443 portlarına izin verilir."


@pytest.mark.parametrize("url", [
    "https://restoran.example/menu",
    "https://restoran.example:443/menu",
    "http://restoran.example:80/menu",
])
def test_validate_url_allows_standard_ports(monkeypatch, url):
    monkeypatch.setattr(menu_fetch, "_resolve_host_safely", lambda h: ["93.184.216.34"])
    _, _, err = _validate_menu_url(url)
    assert err is None


def test_safe_get_redirect_to_nonstandard_port_blocked(monkeypatch):
    monkeypatch.setattr(menu_fetch, "_resolve_host_safely", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(requests, "get",
                        lambda url, **kw: _Resp(302, headers={"Location": "http://evil.example:22/"}))
    with pytest.raises(ValueError, match="80/443"):
        menu_fetch._safe_requests_get("https://ilk.example/", timeout=5)


# ---------------------------------------------------------------------------
# _safe_requests_get — hop-hop doğrulama + boyut sınırı
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, headers=None, body=b"", cookies=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.cookies = cookies or {}
        self.closed = False

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True

    @property
    def content(self):
        return getattr(self, "_content", self._body)

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        import json as _json
        return _json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_safe_get_validates_every_redirect_hop(monkeypatch):
    validated_hosts = []
    monkeypatch.setattr(menu_fetch, "_resolve_host_safely",
                        lambda h: validated_hosts.append(h) or ["93.184.216.34"])
    responses = iter([
        _Resp(302, headers={"Location": "https://ikinci.example/menu"}),
        _Resp(200, body=b"tamam"),
    ])

    def fake_get(url, **kwargs):
        assert kwargs["allow_redirects"] is False  # manuel takip şart
        return next(responses)
    monkeypatch.setattr(requests, "get", fake_get)

    resp = menu_fetch._safe_requests_get("https://ilk.example/menu", timeout=5)
    assert resp.status_code == 200
    assert validated_hosts == ["ilk.example", "ikinci.example"]


def test_safe_get_redirect_to_internal_blocked(monkeypatch):
    def resolve(host):
        if host == "evil.example":
            raise ValueError("İç ağ adresleri engellendi.")
        return ["93.184.216.34"]
    monkeypatch.setattr(menu_fetch, "_resolve_host_safely", resolve)
    monkeypatch.setattr(requests, "get",
                        lambda url, **kw: _Resp(302, headers={"Location": "http://evil.example/"}))
    with pytest.raises(ValueError, match="İç ağ"):
        menu_fetch._safe_requests_get("https://ilk.example/", timeout=5)


def test_safe_get_size_cap(monkeypatch):
    monkeypatch.setattr(menu_fetch, "_resolve_host_safely", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(requests, "get", lambda url, **kw: _Resp(200, body=b"x" * 1000))
    with pytest.raises(ValueError, match="boyut limiti"):
        menu_fetch._safe_requests_get("https://a.example/", timeout=5, max_bytes=500)


def test_safe_get_too_many_redirects(monkeypatch):
    monkeypatch.setattr(menu_fetch, "_resolve_host_safely", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(requests, "get",
                        lambda url, **kw: _Resp(301, headers={"Location": "https://a.example/yine"}))
    with pytest.raises(requests.exceptions.TooManyRedirects):
        menu_fetch._safe_requests_get("https://a.example/", timeout=5, max_redirects=3)


# ---------------------------------------------------------------------------
# _fetch_page — UA retry + boyut sınırı
# ---------------------------------------------------------------------------

def test_fetch_page_retries_with_new_user_agent_on_403(monkeypatch):
    monkeypatch.setattr(menu_fetch, "_resolve_host_safely", lambda h: ["93.184.216.34"])
    seen_uas = []
    responses = iter([_Resp(403, body=b"blocked"),
                      _Resp(200, body=b"<html>" + b"menu " * 200 + b"</html>")])

    def fake_session_get(self, url, **kwargs):
        seen_uas.append(self.headers["User-Agent"])
        return next(responses)
    monkeypatch.setattr(requests.Session, "get", fake_session_get)
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: None)

    resp = menu_fetch._fetch_page("https://restoran.example/menu")
    assert resp.status_code == 200
    assert seen_uas[0] != seen_uas[1]  # ikinci denemede farklı UA


def test_fetch_page_size_cap(monkeypatch):
    monkeypatch.setattr(menu_fetch, "_resolve_host_safely", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(requests.Session, "get",
                        lambda self, url, **kw: _Resp(200, body=b"x" * (menu_fetch._MAX_FETCH_BYTES + 1)))
    with pytest.raises(ValueError, match="boyut limiti"):
        menu_fetch._fetch_page("https://restoran.example/menu")


# ---------------------------------------------------------------------------
# Google Drive URL çözümleme
# ---------------------------------------------------------------------------

def test_is_google_drive_url():
    assert _is_google_drive_url("https://drive.google.com/file/d/abc/view") is True
    assert _is_google_drive_url("https://docs.google.com/document/d/abc") is True
    assert _is_google_drive_url("https://restoran.example/menu") is False
    assert _is_google_drive_url("") is False


@pytest.mark.parametrize("url,expected", [
    ("https://drive.google.com/file/d/FILE123/view", "FILE123"),
    ("https://docs.google.com/document/d/DOC-1_x/edit", "DOC-1_x"),
    ("https://docs.google.com/spreadsheets/d/SHEET9/edit", "SHEET9"),
    ("https://drive.google.com/open?id=OPEN42", "OPEN42"),
    ("https://drive.google.com/uc?export=download&id=UC7", "UC7"),
    ("https://drive.google.com/something-else", None),
])
def test_extract_drive_file_id(url, expected):
    assert _extract_drive_file_id(url) == expected


def test_drive_direct_url_types():
    url, t = _get_drive_direct_url("https://docs.google.com/document/d/X/edit", "X")
    assert t == "doc" and "export?format=txt" in url
    url, t = _get_drive_direct_url("https://docs.google.com/spreadsheets/d/X/edit", "X")
    assert t == "sheet" and "format=csv" in url
    url, t = _get_drive_direct_url("https://drive.google.com/file/d/X/view", "X")
    assert t == "file" and "uc?export=download" in url


# ---------------------------------------------------------------------------
# _process_google_drive_url — içerik tipine göre dispatch
# ---------------------------------------------------------------------------

MENU_TEXT = "Mercimek Çorbası 45\nAdana Kebap 250\nIzgara Tavuk Salata 180"


def _drive(monkeypatch, resp):
    monkeypatch.setattr(menu_fetch, "_safe_requests_get", lambda url, **kw: resp)


def test_drive_requires_file_id(app):
    result, err = menu_fetch._process_google_drive_url("https://drive.google.com/garip")
    assert result is None and "kimliği çıkarılamadı" in err


def test_drive_restricted_returns_structured_error(app, monkeypatch):
    _drive(monkeypatch, _Resp(403))
    result, err = menu_fetch._process_google_drive_url(
        "https://drive.google.com/file/d/GIZLI/view")
    assert result is None
    assert "GOOGLE_DRIVE_LINK_RESTRICTED" in err


def test_drive_not_found_and_server_error(app, monkeypatch):
    _drive(monkeypatch, _Resp(404))
    _, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/YOK/view")
    assert "bulunamadı" in err

    _drive(monkeypatch, _Resp(500))
    _, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/X/view")
    assert "HTTP 500" in err


def test_drive_oversized_file_rejected(app, monkeypatch):
    _drive(monkeypatch, _Resp(200, headers={"Content-Length": str(60 * 1024 * 1024)}))
    _, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/BUYUK/view")
    assert "çok büyük" in err


def test_drive_pdf_dispatch(app, monkeypatch):
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "application/pdf"},
                              body=b"%PDF-1.4 fake"))
    monkeypatch.setattr(menu_fetch, "_extract_text_from_pdf", lambda b: MENU_TEXT)
    result, err = menu_fetch._process_google_drive_url(
        "https://drive.google.com/file/d/PDF1/view")
    assert err is None
    assert result["menu_source"] == "google_drive"
    assert "Adana Kebap" in result["body_text"]


def test_drive_encrypted_pdf_message(app, monkeypatch):
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "application/pdf"},
                              body=b"%PDF-1.4 fake"))

    def enc(b):
        raise ValueError("PDF_ENCRYPTED")
    monkeypatch.setattr(menu_fetch, "_extract_text_from_pdf", enc)
    _, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/S/view")
    assert "şifre korumalı" in err


def test_drive_google_doc_text_dispatch(app, monkeypatch):
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "text/plain"},
                              body=MENU_TEXT.encode()))
    result, err = menu_fetch._process_google_drive_url(
        "https://docs.google.com/document/d/DOC1/edit")
    assert err is None
    assert result["title"] == "Google Drive Doküman Menü"
    assert "Mercimek Çorbası" in result["body_text"]


def test_drive_image_dispatch_to_ocr(app, monkeypatch):
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "image/png"},
                              body=b"\x89PNG fake"))
    monkeypatch.setattr(menu_fetch, "_extract_text_from_image", lambda b, ct: MENU_TEXT)
    result, err = menu_fetch._process_google_drive_url(
        "https://drive.google.com/file/d/IMG1/view")
    assert err is None
    assert result["title"] == "Google Drive Görsel Menü"


def test_drive_unsupported_type(app, monkeypatch):
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "application/zip"},
                              body=b"PK\x03\x04"))
    _, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/Z/view")
    assert "Desteklenmeyen dosya tipi: application/zip" == err


def test_drive_timeout_message(app, monkeypatch):
    def boom(url, **kw):
        raise requests.exceptions.Timeout()
    monkeypatch.setattr(menu_fetch, "_safe_requests_get", boom)
    _, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/T/view")
    assert "zaman aşımı" in err


def test_drive_blocked_host_value_error(app, monkeypatch):
    def boom(url, **kw):
        raise ValueError("İç ağ adresleri engellendi.")
    monkeypatch.setattr(menu_fetch, "_safe_requests_get", boom)
    _, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/V/view")
    assert "çözümlenemedi" in err


def test_drive_connection_error_message(app, monkeypatch):
    def boom(url, **kw):
        raise requests.exceptions.ConnectionError("reset")
    monkeypatch.setattr(menu_fetch, "_safe_requests_get", boom)
    _, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/C/view")
    assert "bağlantı hatası" in err and "ConnectionError" in err


def test_drive_streamed_body_exceeds_cap(app, monkeypatch):
    # Content-Length yok ama akış sırasında 50MB aşılır → indirme kesilir.
    big = b"x" * (50 * 1024 * 1024 + 8)
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "text/plain"}, body=big))
    _, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/BIG/view")
    assert "çok büyük" in err


def test_drive_virus_scan_confirmation_followed(app, monkeypatch):
    # İlk yanıt Drive'ın "virus scan" onay HTML'i → uc-download-link takip edilir.
    confirm_html = (b"<html><body>virus scan warning"
                    b"<a id='uc-download-link' href='/uc?confirm=t&id=X'>download anyway</a>"
                    b"</body></html>")
    first = _Resp(200, headers={"Content-Type": "text/html"}, body=confirm_html)
    second = _Resp(200, headers={"Content-Type": "text/plain"}, body=MENU_TEXT.encode())
    calls = iter([first, second])
    monkeypatch.setattr(menu_fetch, "_safe_requests_get", lambda url, **kw: next(calls))
    result, err = menu_fetch._process_google_drive_url(
        "https://drive.google.com/uc?export=download&id=X")
    assert err is None
    assert "Adana Kebap" in result["body_text"]


def test_drive_pdf_text_too_short(app, monkeypatch):
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "application/pdf"},
                              body=b"%PDF-1.4 fake"))
    monkeypatch.setattr(menu_fetch, "_extract_text_from_pdf", lambda b: "kısa")
    result, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/P/view")
    assert result is None and "çıkarılamadı" in err


def test_drive_corrupt_pdf_message(app, monkeypatch):
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "application/pdf"},
                              body=b"%PDF-1.4 fake"))
    monkeypatch.setattr(menu_fetch, "_extract_text_from_pdf",
                        lambda b: (_ for _ in ()).throw(ValueError("PDF_CORRUPT")))
    _, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/Q/view")
    assert "bozuk" in err


def test_drive_plain_text_too_short(app, monkeypatch):
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "text/plain"}, body=b"az"))
    result, err = menu_fetch._process_google_drive_url(
        "https://docs.google.com/document/d/D/edit")
    assert result is None and "çıkarılamadı" in err


def test_drive_image_too_big(app, monkeypatch):
    big_img = b"\x89PNG" + b"x" * (10 * 1024 * 1024 + 4)
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "image/png"}, body=big_img))
    _, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/IMG/view")
    assert "çok büyük" in err


def test_drive_image_ocr_too_short(app, monkeypatch):
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "image/jpeg"}, body=b"\xff\xd8 fake"))
    monkeypatch.setattr(menu_fetch, "_extract_text_from_image", lambda b, ct: "x")
    result, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/J/view")
    assert result is None and "okunamadı" in err


def test_drive_html_fallback_extraction(app, monkeypatch):
    html = (b"<html><head><style>x</style></head><body>"
            + MENU_TEXT.encode() + b"</body></html>")
    _drive(monkeypatch, _Resp(200, headers={"Content-Type": "text/html"}, body=html))
    result, err = menu_fetch._process_google_drive_url("https://drive.google.com/file/d/H/view")
    assert err is None
    assert result["title"] == "Google Drive Menü"
    assert "Adana Kebap" in result["body_text"]
