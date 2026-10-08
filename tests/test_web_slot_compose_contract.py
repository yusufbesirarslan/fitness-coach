"""R6-01 / P1 + P6: blue/green web-slot Compose contract and nginx indirection.

Static contract plus a real `docker compose config` render (skipped when the
Compose CLI is unavailable). No container is started.

    python -m pytest tests/test_web_slot_compose_contract.py -v
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

BASE = Path("docker-compose.web-slot.yml")
MAIN = Path("docker-compose.yml")
SLOTS = {"blue": Path("deploy/compose/web-slot-blue.yml"),
         "green": Path("deploy/compose/web-slot-green.yml")}
MAPPING = Path("deploy/nginx/web-slots.conf")
ACTIVE_INCLUDE = Path("deploy/nginx/active-web-upstream.conf")
HELPER = Path("scripts/axisai_switch_web_slot.py")
NGINX_TEMPLATE = Path("nginx.conf")
# The production deploy dir is /home/ubuntu/fitness-coach and docker-compose.yml
# sets no `name:`, so the main project is the directory basename.
MAIN_PROJECT = "fitness-coach"
SHARED_NETWORK = f"{MAIN_PROJECT}_default"


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _mapping():
    pairs = {}
    for line in MAPPING.read_text(encoding="ascii").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            slot, backend = line.split(" ")
            pairs[slot] = backend
    return pairs


def test_base_defines_only_the_web_service():
    services = _yaml(BASE)["services"]
    assert set(services) == {"web"}  # no slot-local redis/worker/db


def test_base_has_no_fixed_identity_build_ports_or_cross_project_dependency():
    web = _yaml(BASE)["services"]["web"]
    assert "container_name" not in web
    assert "build" not in web          # a slot runs an exact, prebuilt revision image
    assert "ports" not in web          # ports come only from the per-slot file
    assert "depends_on" not in web     # redis lives in the main project
    assert web["image"].startswith("${AXISAI_WEB_IMAGE:?")
    assert "name" not in _yaml(BASE)   # identity comes only from the per-slot file


def test_base_keeps_main_web_runtime_contract():
    web = _yaml(BASE)["services"]["web"]
    main_web = _yaml(MAIN)["services"]["web"]
    assert web["logging"] == main_web["logging"]
    assert web["logging"]["options"]["labels"] == "com.docker.compose.service"
    assert web["env_file"] == main_web["env_file"]
    assert web["restart"] == main_web["restart"]
    assert web["mem_limit"] == main_web["mem_limit"]  # P3 tightens this later


def test_base_joins_the_main_projects_network_as_external():
    doc = _yaml(BASE)
    assert doc["services"]["web"]["networks"] == [SHARED_NETWORK]
    assert doc["networks"] == {SHARED_NETWORK: {"external": True}}


@pytest.mark.parametrize("slot", sorted(SLOTS))
def test_slot_overlay_sets_only_identity_and_loopback_port(slot):
    doc = _yaml(SLOTS[slot])
    assert set(doc) == {"name", "services"}
    assert doc["name"] == f"axisai-web-{slot}"
    assert set(doc["services"]) == {"web"}
    assert set(doc["services"]["web"]) == {"ports"}
    (port,) = doc["services"]["web"]["ports"]
    assert re.fullmatch(r"127\.0\.0\.1:\d{4,5}:5000", port), port


def test_slot_ports_are_distinct_match_trusted_mapping_and_avoid_legacy_port():
    mapping = _mapping()
    assert set(mapping) == set(SLOTS)
    published = {}
    for slot, path in SLOTS.items():
        (port,) = _yaml(path)["services"]["web"]["ports"]
        host_ip, host_port, _ = port.split(":")
        published[slot] = f"{host_ip}:{host_port}"
    assert published == mapping
    assert len(set(published.values())) == 2
    main_ports = {p.rsplit(":", 1)[0] for s in _yaml(MAIN)["services"].values()
                  for p in s.get("ports", [])}
    assert not set(published.values()) & main_ports


def test_slot_projects_are_isolated_from_the_main_project_and_each_other():
    # `docker compose up --remove-orphans` only considers containers whose
    # com.docker.compose.project label equals the invoking project name, so a
    # distinct exact name is what keeps slots out of the main project's orphans.
    assert "name" not in _yaml(MAIN)
    names = {_yaml(p)["name"] for p in SLOTS.values()}
    assert len(names) == 2
    assert MAIN_PROJECT not in names


def test_the_web_service_key_survives_into_the_cloudwatch_log_filter():
    agent = Path("deploy/cloudwatch-agent/file_config.json").read_text(encoding="utf-8")
    assert "(web|worker)" in agent
    for path in SLOTS.values():
        assert set(_yaml(path)["services"]) == {"web"}


# --- real render -------------------------------------------------------------

def _compose_cli():
    exe = shutil.which("docker")
    if not exe:
        return None
    probe = subprocess.run([exe, "compose", "version"], capture_output=True, text=True)
    return exe if probe.returncode == 0 else None


@pytest.mark.parametrize("slot", sorted(SLOTS))
def test_slot_renders_with_compose(slot, tmp_path, monkeypatch):
    exe = _compose_cli()
    if not exe:
        pytest.skip("docker compose CLI unavailable")
    (tmp_path / "deploy/compose").mkdir(parents=True)
    shutil.copy(BASE, tmp_path / BASE.name)
    shutil.copy(SLOTS[slot], tmp_path / SLOTS[slot])
    (tmp_path / ".env").write_text("", encoding="utf-8")
    for var in ("COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "COMPOSE_PROFILES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AXISAI_WEB_IMAGE", "fitness-coach-web:0123abc")
    proc = subprocess.run(
        [exe, "compose", "-f", BASE.name, "-f", str(SLOTS[slot]), "config", "--format", "json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    rendered = json.loads(proc.stdout)
    assert rendered["name"] == f"axisai-web-{slot}"
    assert set(rendered["services"]) == {"web"}
    web = rendered["services"]["web"]
    assert web["image"] == "fitness-coach-web:0123abc"
    assert "container_name" not in web
    (port,) = web["ports"]
    assert port["host_ip"] == "127.0.0.1"
    assert f"127.0.0.1:{port['published']}" == _mapping()[slot]
    assert int(port["target"]) == 5000
    assert rendered["networks"][SHARED_NETWORK]["external"] is True


def test_slot_without_an_exact_image_refuses_to_render(tmp_path, monkeypatch):
    exe = _compose_cli()
    if not exe:
        pytest.skip("docker compose CLI unavailable")
    (tmp_path / "deploy/compose").mkdir(parents=True)
    shutil.copy(BASE, tmp_path / BASE.name)
    shutil.copy(SLOTS["blue"], tmp_path / SLOTS["blue"])
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.delenv("AXISAI_WEB_IMAGE", raising=False)
    proc = subprocess.run(
        [exe, "compose", "-f", BASE.name, "-f", str(SLOTS["blue"]), "config"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0
    assert "AXISAI_WEB_IMAGE" in proc.stderr


# --- nginx indirection (P1) --------------------------------------------------

def test_initial_active_include_is_the_legacy_backend_single_directive():
    assert ACTIVE_INCLUDE.read_bytes() == b"server 127.0.0.1:5000;\n"


def test_template_routes_the_app_through_the_named_upstream_only():
    text = NGINX_TEMPLATE.read_text(encoding="utf-8")
    assert re.search(
        r"upstream axisai_web \{\s*include /etc/nginx/axisai/active-web-upstream\.conf;\s*\}", text)
    assert text.count("proxy_pass http://axisai_web;") == 1
    assert "proxy_pass http://127.0.0.1:5000" not in text


def test_helper_writes_the_include_the_template_reads_and_reads_the_shipped_mapping():
    source = HELPER.read_text(encoding="utf-8")
    assert 'INCLUDE_PATH = "/etc/nginx/axisai/active-web-upstream.conf"' in source
    assert 'MAPPING_PATH = "/etc/axisai/web-slots.conf"' in source
    assert "/etc/nginx/axisai/active-web-upstream.conf" in NGINX_TEMPLATE.read_text(encoding="utf-8")
