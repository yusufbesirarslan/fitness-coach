from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_SOURCE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
ENV_EXAMPLE_SOURCE = (ROOT / ".env.example").read_text(encoding="utf-8")
COMPOSE_CONFIG = yaml.safe_load(COMPOSE_SOURCE)
REDIS_SERVICE = COMPOSE_CONFIG["services"]["redis"]


def test_compose_requires_a_redis_password():
    assert REDIS_SERVICE["environment"]["REDIS_PASSWORD"] == (
        "${REDIS_PASSWORD:?REDIS_PASSWORD must be set}"
    )


def test_redis_server_requires_the_configured_password():
    # Triage 2026-07-19 #7: parola redis-server argv'sine KONMAZ (host ps /
    # /proc/<pid>/cmdline / docker inspect sızıntısı). Başlangıçta 0600'lük
    # geçici conf'a yazılır ve dosyadan okunur; sır olmayan ayarlar argv'de kalır.
    assert REDIS_SERVICE["command"] == [
        "sh",
        "-c",
        "umask 077 && "
        "printf 'requirepass \"%s\"\\n' \"$$REDIS_PASSWORD\" > /tmp/requirepass.conf && "
        "exec redis-server /tmp/requirepass.conf "
        "--maxmemory ${REDIS_MAXMEMORY:-200mb} "
        "--maxmemory-policy allkeys-lru --save 60 1",
    ]


def test_redis_server_argv_carries_no_secret():
    # exec sonrası redis-server'ın kendi argümanlarında parola referansı geçmez.
    script = REDIS_SERVICE["command"][2]
    server_argv = script.split("exec redis-server ", 1)[1]
    assert "REDIS_PASSWORD" not in server_argv
    assert "--requirepass" not in script


def test_redis_healthcheck_authenticates():
    # Aynı sızıntı sınıfı: redis-cli -a <parola> da argv'de görünürdü —
    # REDISCLI_AUTH ortam değişkeniyle kimlik doğrula.
    assert REDIS_SERVICE["healthcheck"]["test"] == [
        "CMD-SHELL",
        'REDISCLI_AUTH="$$REDIS_PASSWORD" redis-cli ping | grep -q PONG',
    ]


def test_redis_password_references_escape_compose_interpolation():
    assert COMPOSE_SOURCE.count("$$REDIS_PASSWORD") == 2


def test_redis_runs_as_non_root_user():
    assert REDIS_SERVICE["user"] == "redis"


def test_env_example_documents_authenticated_redis_connection():
    assert "REDIS_PASSWORD=replace-with-a-long-random-secret" in ENV_EXAMPLE_SOURCE
    assert "REDIS_URL=redis://:URL_ENCODED_PASSWORD@redis:6379/0" in ENV_EXAMPLE_SOURCE
