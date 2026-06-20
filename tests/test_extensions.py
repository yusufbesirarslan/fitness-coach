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
    # /health DAİMA 200 döner (Redis kaybı konteyneri unhealthy yapmamalı) ve
    # limiter depolama durumunu izleme için raporlar.
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["limiter_storage"] in ("memory", "redis", "degraded")
