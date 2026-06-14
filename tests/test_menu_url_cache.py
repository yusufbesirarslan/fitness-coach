"""Menü URL tarama önbelleği testleri (app/blueprints/menu.py).

Aynı menü URL'i ikinci kez taranınca siteyi yeniden fetch etmeden Redis
önbelleğinden döner. redis_client None ise (lokal/test varsayılanı) cache
no-op'tur ve her tarama fetch eder.

    python -m pytest tests/test_menu_url_cache.py -v
"""
import requests

from app.blueprints import menu as menu_bp

MENU_HTML = """<html><head><title>Lezzet Durağı</title></head><body>
<h2>Çorbalar</h2><li>Mercimek Çorbası</li>
<h2>Ana Yemekler</h2><li>Adana Kebap</li><li>Izgara Tavuk</li>
</body></html>"""


class _Resp:
    def __init__(self, body=MENU_HTML, status=200, content_type="text/html; charset=utf-8"):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.text = body


class _FakeRedis:
    """get/setex destekleyen minimal in-memory Redis taklidi (TTL yok sayılır)."""
    def __init__(self):
        self.store = {}
    def get(self, key):
        return self.store.get(key)
    def setex(self, key, ttl, value):
        self.store[key] = value


def _patch_scan(monkeypatch, redis=None):
    """scan-menu için ağ + doğrulama mock'la; fetch çağrı listesini döndür."""
    monkeypatch.setattr(menu_bp, "redis_client", redis)
    monkeypatch.setattr(menu_bp, "_validate_menu_url",
                        lambda url: (requests.utils.urlparse(url), url, None))
    calls = []

    def _fetch(url, timeout=10):
        calls.append(url)
        return _Resp()
    monkeypatch.setattr(menu_bp, "_fetch_page", _fetch)
    return calls


# ---------------------------------------------------------------------------
# Cache key normalizasyonu (saf fonksiyon — app context gerektirmez)
# ---------------------------------------------------------------------------

def test_cache_key_normalizes_host_case_and_trailing_slash():
    k = menu_bp._menu_scan_cache_key
    assert k("https://Restoran.Example/menu") == k("https://restoran.example/menu/")


def test_cache_key_normalizes_query_param_order():
    k = menu_bp._menu_scan_cache_key
    assert k("https://a.example/m?b=2&a=1") == k("https://a.example/m?a=1&b=2")


def test_cache_key_distinguishes_different_paths():
    k = menu_bp._menu_scan_cache_key
    assert k("https://a.example/menu") != k("https://a.example/baska")


# ---------------------------------------------------------------------------
# Endpoint davranışı
# ---------------------------------------------------------------------------

def test_same_url_second_scan_hits_cache(client, auth_user, monkeypatch):
    fake = _FakeRedis()
    calls = _patch_scan(monkeypatch, redis=fake)
    url = "https://restoran.example/menu"

    first = client.post("/api/proxy/scan-menu", json={"url": url}).get_json()
    assert first.get("cached") is not True
    assert len(calls) == 1

    second = client.post("/api/proxy/scan-menu", json={"url": url}).get_json()
    assert second.get("cached") is True          # önbellekten geldi
    assert len(calls) == 1                        # yeniden fetch YOK
    assert second["title"] == first["title"]


def test_normalized_urls_share_cache_entry(client, auth_user, monkeypatch):
    fake = _FakeRedis()
    calls = _patch_scan(monkeypatch, redis=fake)

    client.post("/api/proxy/scan-menu", json={"url": "https://Restoran.example/menu"})
    client.post("/api/proxy/scan-menu", json={"url": "https://restoran.example/menu/"})
    assert len(calls) == 1                        # host-case + trailing slash → tek anahtar


def test_no_redis_disables_cache(client, auth_user, monkeypatch):
    calls = _patch_scan(monkeypatch, redis=None)
    url = "https://restoran.example/menu"

    client.post("/api/proxy/scan-menu", json={"url": url})
    body = client.post("/api/proxy/scan-menu", json={"url": url}).get_json()
    assert len(calls) == 2                        # cache yok → her tarama fetch eder
    assert body.get("cached") is not True


def test_failed_scan_not_cached(client, auth_user, monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(menu_bp, "redis_client", fake)
    monkeypatch.setattr(menu_bp, "_validate_menu_url",
                        lambda url: (requests.utils.urlparse(url), url, None))
    monkeypatch.setattr(menu_bp, "_try_wordpress_api", lambda base, html: (None, []))
    calls = []

    def _fetch(url, timeout=10):
        calls.append(url)
        return _Resp(body="<html><body></body></html>")     # okunamayan → 422
    monkeypatch.setattr(menu_bp, "_fetch_page", _fetch)

    url = "https://bos.example/menu"
    assert client.post("/api/proxy/scan-menu", json={"url": url}).status_code == 422
    assert client.post("/api/proxy/scan-menu", json={"url": url}).status_code == 422
    assert len(calls) == 2                        # hata cache'lenmez → tekrar fetch
