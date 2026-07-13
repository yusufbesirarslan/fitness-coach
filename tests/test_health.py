"""/health ve /health?deep=1 testleri.

M3: derin görünüm iç duruşu ifşa eder (login offline mı, Redis ayakta mı,
Bedrock açık mı) ve her çağrıda bir DIŞ HTTP isteği tetikler. Anonim bir
saldırgan "login: offline"ı izleyerek Redis'in düştüğü ve login'in fail-closed
olduğu ANI öğrenebilirdi — yani saldırıya başlamak için en uygun pencereyi.
Derin görünüm artık yalnızca iç ağdan (loopback / private) verilir.

    python -m pytest tests/test_health.py -v
"""
import pytest

_DEEP_KEYS = ("redis", "login", "bedrock", "fatsecret_proxy")


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


def test_deep_health_allowed_from_docker_bridge(client):
    """Deploy gate `curl 127.0.0.1:5000` yapar ama compose portu docker-proxy
    üzerinden yayınlar → konteyner kaynak adresi olarak KÖPRÜ GEÇİDİNİ görür.
    Katı loopback kontrolü deploy gate'ini bozardı."""
    resp = client.get("/health?deep=1", environ_base={"REMOTE_ADDR": "172.17.0.1"})
    assert resp.status_code == 200
    assert "login" in resp.get_json()


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
