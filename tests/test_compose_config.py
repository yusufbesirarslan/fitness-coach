"""docker-compose.yml sözleşmesi (M2).

Docker'ın varsayılan json-file log sürücüsünün max-size'ı YOKTUR. gunicorn
--access-logfile - ve observability.log_request istek BAŞINA satır yazar → log
sınırsız büyür. Tek EC2 host'unda sabit EBS diskiyle bu, eninde sonunda diski
doldurur ve web + redis + deploy'u BİRLİKTE düşürür; mem_limit disk için hiçbir
şey yapmaz. Rotasyon sözleşmesini teste bağla.

    python -m pytest tests/test_compose_config.py -v
"""
from pathlib import Path

import pytest
import yaml

COMPOSE = Path("docker-compose.yml")


@pytest.fixture(scope="module")
def services():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_compose_parses_and_has_expected_services(services):
    # worker (Sprint 4 WS8): AI iş kuyruğunu tüketen ayrı süreç (aynı imaj).
    assert set(services) == {"web", "redis", "worker"}


@pytest.mark.parametrize("service", ["web", "redis", "worker"])
def test_log_rotation_configured(services, service):
    logging = services[service].get("logging")
    assert logging, f"{service}: logging bloğu YOK → sınırsız log → dolu disk"
    assert logging["driver"] == "json-file"
    options = logging["options"]
    assert options.get("max-size"), f"{service}: max-size YOK"
    assert options.get("max-file"), f"{service}: max-file YOK"


@pytest.mark.parametrize("service", ["web", "redis", "worker"])
def test_ports_bound_to_loopback_only(services, service):
    # Regresyon kapısı: servisler internete AÇILMAMALI (host nginx tek giriş).
    # worker hiç port yayınlamaz (yalnızca Redis'e giden istemci) → döngü boş, geçer.
    for mapping in services[service].get("ports", []):
        assert str(mapping).startswith("127.0.0.1:"), \
            f"{service}: {mapping} loopback'e bağlı değil"


@pytest.mark.parametrize("service", ["web", "redis", "worker"])
def test_memory_limit_set(services, service):
    assert services[service].get("mem_limit")
