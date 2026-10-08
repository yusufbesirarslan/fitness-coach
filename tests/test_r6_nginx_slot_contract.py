"""R6-02A <-> R6-01B cross-contract: nginx route states must name exactly the
backends the slot runtime and the main project publish.

One relationship, derived rather than restated: the route mapping that root
installs (deploy/nginx/web-slots.conf, parsed by the helper's own parser) must
equal {legacy: the main-project web host port, **web_slot_runtime.SLOT_PORTS},
and SLOT_PORTS must equal what the Compose overlays actually publish. Moving
any one of them alone fails CI.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import axisai_switch_web_slot as sw  # noqa: E402
import web_slot_runtime as rt  # noqa: E402


def _published(compose_path, service="web"):
    doc = yaml.safe_load((ROOT / compose_path).read_text(encoding="utf-8"))
    out = []
    for entry in doc["services"][service].get("ports", []):
        host_ip, host_port, container_port = str(entry).split(":")
        out.append((host_ip, int(host_port), int(container_port)))
    return out


def runtime_contract():
    """Route state -> loopback port, as the RUNTIME side publishes it."""
    [(ip, legacy, target)] = _published("docker-compose.yml")
    assert (ip, target) == ("127.0.0.1", 5000)
    contract = {"legacy": legacy}
    for slot, port in rt.SLOT_PORTS.items():
        assert _published(rt.OVERLAY_TEMPLATE.format(slot=slot)) == [(rt.LOOPBACK, port, rt.INTERNAL_PORT)]
        contract[slot] = port
    return contract


def test_route_mapping_equals_the_runtime_contract():
    mapping = sw.parse_mapping((ROOT / "deploy" / "nginx" / "web-slots.conf").read_bytes())
    assert mapping == runtime_contract()


def test_route_states_are_exactly_legacy_plus_the_runtime_slots():
    assert sw.STATES == ("legacy",) + tuple(sorted(rt.SLOT_PORTS))
    assert sw.LOOPBACK == rt.LOOPBACK == "127.0.0.1"


def test_route_backends_are_loopback_and_distinct():
    contract = runtime_contract()
    assert len(set(contract.values())) == len(contract) == 3
    for port in contract.values():
        assert sw.render_include(port) == f"server 127.0.0.1:{port};\n".encode()


def test_bootstrap_include_is_the_legacy_backend():
    mapping = sw.parse_mapping((ROOT / "deploy" / "nginx" / "web-slots.conf").read_bytes())
    data = (ROOT / "deploy" / "nginx" / "active-web-upstream.conf").read_bytes()
    assert data == sw.render_include(mapping["legacy"])
    assert sw.parse_include(data, mapping) == "legacy"


def test_drift_in_either_direction_is_detected():
    mapping = sw.parse_mapping((ROOT / "deploy" / "nginx" / "web-slots.conf").read_bytes())
    contract = runtime_contract()
    for slot in rt.SLOT_PORTS:
        drifted = dict(contract, **{slot: contract[slot] + 2})
        assert drifted != mapping
