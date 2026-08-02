"""/health ve /health?deep=1 testleri.

M3: derin görünüm iç duruşu ifşa eder (login offline mı, Redis ayakta mı,
Bedrock açık mı) ve her çağrıda bir DIŞ HTTP isteği tetikler. Anonim bir
saldırgan "login: offline"ı izleyerek Redis'in düştüğü ve login'in fail-closed
olduğu ANI öğrenebilirdi — yani saldırıya başlamak için en uygun pencereyi.
Derin görünüm artık yalnızca iç ağdan (loopback / private) verilir.

    python -m pytest tests/test_health.py -v
"""
import pytest

_DEEP_KEYS = ("redis", "login", "bedrock", "fatsecret_proxy", "worker",
              "flags", "capacity")


@pytest.fixture(autouse=True)
def _no_outbound(monkeypatch):
    """Derin probe FatSecret proxy'sine dış istek atar — testte ağa çıkma."""
    import requests

    class _Resp:
        status_code = 404  # canlı proxy auth'suz istekte 4xx döner

    monkeypatch.setattr(requests, "get", lambda *a, **kw: _Resp())


def test_shallow_health_is_public(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    # Sığ görünüm iç duruşu ASLA sızdırmaz.
    for key in _DEEP_KEYS:
        assert key not in body


def test_deep_health_allowed_from_loopback(client):
    resp = client.get("/health?deep=1", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert resp.status_code == 200
    body = resp.get_json()
    for key in _DEEP_KEYS:
        assert key in body


def test_deep_health_allowed_from_configured_gateway(client, monkeypatch):
    """Deploy gate `curl 127.0.0.1:5000` yapar ama compose portu docker-proxy
    üzerinden yayınlar → konteyner kaynak adresi olarak KÖPRÜ GEÇİDİNİ görür.
    Katı loopback kontrolü deploy gate'ini bozardı."""
    monkeypatch.setenv("DEEP_HEALTH_TRUSTED_CIDRS", "172.17.0.1/32")
    resp = client.get("/health?deep=1", environ_base={"REMOTE_ADDR": "172.17.0.1"})
    assert resp.status_code == 200
    assert "login" in resp.get_json()


@pytest.mark.parametrize("private_ip", ["10.0.0.8", "192.168.1.20", "172.17.0.2"])
def test_deep_health_ignored_from_other_private_address(client, monkeypatch, private_ip):
    """Only the configured Docker gateway, not all RFC1918 sources, gets details."""
    monkeypatch.setenv("DEEP_HEALTH_TRUSTED_CIDRS", "172.17.0.1/32")
    resp = client.get("/health?deep=1", environ_base={"REMOTE_ADDR": private_ip})
    assert resp.status_code == 200
    for key in _DEEP_KEYS:
        assert key not in resp.get_json()



def test_deep_health_ignores_invalid_configured_cidrs(client, monkeypatch):
    monkeypatch.setenv("DEEP_HEALTH_TRUSTED_CIDRS", "not-a-network, 300.1.1.1/24")
    resp = client.get("/health?deep=1", environ_base={"REMOTE_ADDR": "172.17.0.1"})
    assert resp.status_code == 200
    for key in _DEEP_KEYS:
        assert key not in resp.get_json()


# NOT: gerçekten GLOBAL yönlendirilebilir adresler kullan. Python'un
# ipaddress'i TEST-NET/dokümantasyon aralıklarını (203.0.113.0/24, 192.0.2.0/24)
# `is_private` sayar — doğrudur, çünkü onlar da internetten yönlendirilemez;
# ama bu yüzden "public istemci" testinde kullanılamazlar.
@pytest.mark.parametrize("public_ip", ["8.8.8.8", "93.184.216.34"])
def test_deep_health_ignored_from_public_ip(client, public_ip):
    """Public istemci deep=1 istese bile SIĞ gövde alır — ve 403 DEĞİL:
    403'ün kendisi de bir sinyal (uç var, korunuyor) olurdu."""
    resp = client.get("/health?deep=1", environ_base={"REMOTE_ADDR": public_ip})
    assert resp.status_code == 200
    body = resp.get_json()
    for key in _DEEP_KEYS:
        assert key not in body


# ── Production Hardening PR1: bayrak + kapasite görünürlüğü ────────────────

def test_deep_health_reports_flag_state(client):
    """Bayraklar host .env'inde yaşar ve deploy pipeline'ı onları TAŞIMAZ →
    "canlıda ne açık?" sorusu yalnızca çalışan süreçten yanıtlanabilir."""
    from app.config import FEATURE_FLAG_KEYS
    body = client.get("/health?deep=1",
                      environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
    assert set(body["flags"]) == set(FEATURE_FLAG_KEYS)
    assert all(isinstance(v, bool) for v in body["flags"].values())


def test_deep_health_flags_expose_only_names_and_booleans(client):
    """Sır/bağlantı dizesi/anahtar ASLA sızmamalı — yalnızca ad + boolean."""
    body = client.get("/health?deep=1",
                      environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
    for name, value in body["flags"].items():
        assert isinstance(value, bool), name
    blob = repr(body)
    for secret in ("SECRET_KEY", "DATABASE_URL", "postgresql://", "sk-",
                   "DERIVATION_KEYRING", "RESEND_API_KEY"):
        assert secret not in blob


def test_deep_health_reports_capacity_model(client):
    """Sunum kapasitesi ve DB havuzu YAN YANA raporlanır ki uyumsuzluk görülsün."""
    body = client.get("/health?deep=1",
                      environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
    capacity = body["capacity"]
    assert capacity["workers"] >= 1
    assert capacity["threads"] >= 1
    assert capacity["ai_max_concurrency"] >= 1
    assert capacity["scrape_max_concurrency"] >= 1


def test_shallow_health_hides_flags_and_capacity(client):
    """Sığ görünüm public'tir — bayrak/kapasite duruşu oradan ASLA sızmaz."""
    body = client.get("/health").get_json()
    assert "flags" not in body
    assert "capacity" not in body


def test_deep_health_not_spoofable_via_forwarded_for(client):
    """Sahte X-Forwarded-For kapıyı GEÇEMEZ.

    Prod zinciri birebir modellenir: saldırgan "X-Forwarded-For: 127.0.0.1"
    gönderir, nginx bunu $proxy_add_x_forwarded_for ile KORUR ve GERÇEK istemci
    IP'sini SONA EKLER → "127.0.0.1, 8.8.8.8". ProxyFix(x_for=1) en SAĞDAKİ
    girdiyi okur → remote_addr = 8.8.8.8 (gerçek public IP) → derin görünüm yok.
    """
    resp = client.get(
        "/health?deep=1",
        environ_base={"REMOTE_ADDR": "172.17.0.1"},   # nginx → docker köprüsü
        headers={"X-Forwarded-For": "127.0.0.1, 8.8.8.8"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    for key in _DEEP_KEYS:
        assert key not in body
