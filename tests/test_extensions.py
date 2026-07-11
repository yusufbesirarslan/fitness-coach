"""Tests for extension wiring (app/extensions.py).

OpenAI istemcisi lazy kurulur: OPENAI_API_KEY olmayan ortamlar (test,
migration, CLI) import'ta patlamamalı; anahtar yalnızca gerçek bir AI
çağrısında gerekir.

    python -m pytest tests/test_extensions.py -v
"""
import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_extensions_import_without_openai_key():
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    result = subprocess.run(
        [sys.executable, "-c", "import app.extensions"],
        cwd=_REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_lazy_client_delegates_to_real_client():
    from app.extensions import openai_client
    # İlk öznitelik erişimi gerçek istemciyi kurar ve delege eder
    # (conftest sahte bir anahtar sağlar; ağ çağrısı yapılmaz).
    assert hasattr(openai_client, "chat")
    assert openai_client._client is not None


class _FakePingRedis:
    def __init__(self, ok):
        self._ok = ok

    def ping(self):
        if not self._ok:
            raise ConnectionError("redis down")
        return True


def test_login_throttle_available_no_redis(monkeypatch):
    import app.extensions as ext
    monkeypatch.setattr(ext, "redis_client", None)
    # Redis yapılandırılmamış → tek-süreç store kasıtlı → True.
    assert ext.login_throttle_available() is True


def test_login_throttle_available_redis_up_and_down(monkeypatch):
    import app.extensions as ext
    monkeypatch.setattr(ext, "redis_client", _FakePingRedis(ok=True))
    ext._LOGIN_THROTTLE_HEALTH["checked_at"] = 0.0   # cache'i sıfırla
    assert ext.login_throttle_available() is True

    monkeypatch.setattr(ext, "redis_client", _FakePingRedis(ok=False))
    ext._LOGIN_THROTTLE_HEALTH["checked_at"] = 0.0   # yeniden kontrolü zorla
    assert ext.login_throttle_available() is False


def test_limiter_storage_status_memory_without_redis(monkeypatch):
    import app.extensions as ext
    # REDIS_URL yapılandırılmadığında (lokal/test) durum "memory" olmalı.
    monkeypatch.setattr(ext, "redis_client", None)
    assert ext.limiter_storage_status() == "memory"


def test_limiter_storage_status_degraded_when_ping_fails(monkeypatch):
    import app.extensions as ext

    class _DeadRedis:
        def ping(self):
            raise ConnectionError("redis down")

    monkeypatch.setattr(ext, "redis_client", _DeadRedis())
    assert ext.limiter_storage_status() == "degraded"


def test_health_reports_limiter_storage(app):
    # DB erişilebilirken /health 200 döner ve limiter depolama durumunu
    # izleme için raporlar. (Redis kaybı tek başına unhealthy saymaz.)
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["limiter_storage"] in ("memory", "redis", "degraded")


def test_deep_health_reports_login_redis_and_bedrock(app):
    # I2/I3: deploy gate'in kullandığı derin sağlık görünümü — Redis/login/bedrock
    # alt sistemlerini raporlar; sağlıklı durumda 200.
    client = app.test_client()
    resp = client.get("/health?deep=1")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["login"] == "ok"
    assert body["redis"] in ("ok", "unconfigured", "error")
    assert body["bedrock"] in ("enabled", "disabled")


def test_deep_health_503_when_login_fail_closed(app, monkeypatch):
    # I2: Redis-down + LOGIN_FAIL_CLOSED=1 → hiç kimse giriş yapamaz; derin
    # sağlık 503 dönmeli ki deploy gate "yeşilken login kapalı" durumunu yakalasın.
    import app.extensions as ext
    monkeypatch.setattr(ext, "login_throttle_available", lambda: False)
    app.config["LOGIN_FAIL_CLOSED"] = True
    client = app.test_client()
    resp = client.get("/health?deep=1")
    assert resp.status_code == 503
    assert resp.get_json()["login"] == "offline"
    # Sığ /health (Docker liveness) etkilenmez — konteyner restart-loop'a girmesin.
    assert client.get("/health").status_code == 200


def test_health_returns_503_when_db_unreachable(app, monkeypatch):
    # DB erişilemezse /health 503 dönmeli ki bozuk deploy rollback tetiklesin.
    from app.extensions import db
    client = app.test_client()

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db.session, "execute", boom)
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "error"
    assert body["db"] == "error"
