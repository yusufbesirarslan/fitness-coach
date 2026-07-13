from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_SOURCE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
ENV_EXAMPLE_SOURCE = (ROOT / ".env.example").read_text(encoding="utf-8")


def test_compose_requires_a_redis_password():
    assert "REDIS_PASSWORD: ${REDIS_PASSWORD:?REDIS_PASSWORD must be set}" in COMPOSE_SOURCE


def test_redis_server_requires_the_configured_password():
    assert '--requirepass "$$REDIS_PASSWORD"' in COMPOSE_SOURCE


def test_redis_healthcheck_authenticates():
    assert (
        'test: ["CMD-SHELL", "redis-cli --no-auth-warning -a \\"$$REDIS_PASSWORD\\" '
        'ping | grep -q PONG"]'
        in COMPOSE_SOURCE
    )


def test_env_example_documents_authenticated_redis_connection():
    assert "REDIS_PASSWORD=replace-with-a-long-random-secret" in ENV_EXAMPLE_SOURCE
    assert "REDIS_URL=redis://:URL_ENCODED_PASSWORD@redis:6379/0" in ENV_EXAMPLE_SOURCE
