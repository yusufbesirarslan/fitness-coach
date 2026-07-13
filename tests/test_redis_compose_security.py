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
    assert REDIS_SERVICE["command"] == [
        "sh",
        "-c",
        "exec redis-server --maxmemory ${REDIS_MAXMEMORY:-200mb} "
        "--maxmemory-policy allkeys-lru --save 60 1 "
        '--requirepass "$$REDIS_PASSWORD"',
    ]


def test_redis_healthcheck_authenticates():
    assert REDIS_SERVICE["healthcheck"]["test"] == [
        "CMD-SHELL",
        'redis-cli --no-auth-warning -a "$$REDIS_PASSWORD" ping | grep -q PONG',
    ]


def test_redis_password_references_escape_compose_interpolation():
    assert COMPOSE_SOURCE.count("$$REDIS_PASSWORD") == 2


def test_redis_runs_as_non_root_user():
    assert REDIS_SERVICE["user"] == "redis"


def test_env_example_documents_authenticated_redis_connection():
    assert "REDIS_PASSWORD=replace-with-a-long-random-secret" in ENV_EXAMPLE_SOURCE
    assert "REDIS_URL=redis://:URL_ENCODED_PASSWORD@redis:6379/0" in ENV_EXAMPLE_SOURCE
