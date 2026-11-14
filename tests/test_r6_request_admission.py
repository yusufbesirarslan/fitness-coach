"""Loaded request eligibility and rollback ordering; no host/provider access."""
import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from scripts import web_slot_runtime as rt
from tests.test_r6_deploy_transaction import (
    CAND, PREV, MUTATING, FakeOps, Recorder, first_cutover_world, host,
    names, run, slot_world, tx,
)
from tests.test_r6_slot_runtime import (
    REV_A, FakeDocker, _container, _runtime, deploy_dir,
)


@pytest.mark.parametrize("code", [rt._DEEP_PROBE_CODE, tx._DEEP_PROBE_CODE])
@pytest.mark.parametrize("flags,expected", [
    ({"ACCOUNT_ELIGIBILITY_ENABLED": True, "unrelated": "not-projected"}, True),
    ({"ACCOUNT_ELIGIBILITY_ENABLED": False}, False),
    ({}, None), (None, None), ("not-a-map", None),
])
def test_probe_projects_only_the_serving_boolean(monkeypatch, capsys, code, flags, expected):
    import urllib.request
    body = {"status": "ok", "revision": CAND, "flags": flags}
    response = io.StringIO(json.dumps(body))
    response.status = 200
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: response)
    exec(code, {})
    result = json.loads(capsys.readouterr().out)
    assert result == {"http": 200, "status": "ok", "revision": CAND,
                      "eligibility_enforced": expected}


@pytest.mark.parametrize("value", [False, None, 0, 1, "1", "true", {}, []])
def test_slot_refuses_unproven_loaded_eligibility(deploy_dir, value):
    fake = FakeDocker(containers={"green": [_container("green")]}, deep={
        "http": 200, "status": "ok", "revision": REV_A,
        "eligibility_enforced": value})
    with pytest.raises(rt.VerificationFailed, match="deep health"):
        _runtime(deploy_dir, fake).verify("green", REV_A, require_eligibility=True)
    assert sum(1 for call in fake.verbs() if call == "exec") == 1 + rt.DEEP_PROBE_ATTEMPTS


def test_slot_qualifies_explicit_true_and_returns_the_proof(deploy_dir):
    fake = FakeDocker(containers={"green": [_container("green")]}, deep={
        "http": 200, "status": "ok", "revision": REV_A,
        "eligibility_enforced": True})
    assert _runtime(deploy_dir, fake).verify(
        "green", REV_A, require_eligibility=True)["eligibility_enforced"] is True


@pytest.mark.parametrize("value", [False, None, 0, 1, "1", "true"])
def test_legacy_deep_read_refuses_non_true(tmp_path, value):
    ops = host(tmp_path, Recorder())
    ops._docker = lambda *a, **k: json.dumps({"http": 200, "status": "ok",
        "revision": PREV, "eligibility_enforced": value})
    with pytest.raises(tx.OpError, match="eligibility"):
        ops.container_deep_health_revision("old", 30)


@pytest.mark.parametrize("revision", [PREV, CAND])
@pytest.mark.parametrize("value", [False, None, 1, "1"])
def test_transaction_refuses_a_slot_helper_without_explicit_proof(tmp_path, revision, value):
    ops = host(tmp_path, Recorder())
    ops._slot = lambda *a: (0, {"deep_health_revision": revision,
                              "eligibility_enforced": value})
    with pytest.raises(tx.OpError, match="verify failed"):
        ops.slot_verify("blue", revision, 30)


@pytest.mark.parametrize("active", ["blue", "green"])
def test_unqualified_previous_refuses_before_image_or_release_preparation(monkeypatch, active):
    world = slot_world(active)
    real_verify = FakeOps.slot_verify

    def verify(self, slot, revision, timeout):
        if revision == PREV:
            raise tx.OpError("loaded eligibility off")
        return real_verify(self, slot, revision, timeout)

    monkeypatch.setattr(FakeOps, "slot_verify", verify)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == active
    assert not MUTATING.intersection(names(world))


@pytest.mark.parametrize("active", ["blue", "green"])
def test_unqualified_candidate_never_gets_a_route(monkeypatch, active):
    world = slot_world(active)
    real_verify = FakeOps.slot_verify

    def verify(self, slot, revision, timeout):
        if revision == CAND:
            raise tx.OpError("loaded eligibility off")
        return real_verify(self, slot, revision, timeout)

    monkeypatch.setattr(FakeOps, "slot_verify", verify)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == active and set(world.slots) == {active}
    assert "route_switch" not in names(world)


@pytest.mark.parametrize("active", ["blue", "green"])
def test_concurrent_retained_web_drift_cannot_be_reopened(monkeypatch, active):
    """Pause after switch, change the retained web, fail public proof.

    Events force the adverse ordering without timing sleeps. The fake host
    models an out-of-band retained-container replacement, not a real host.
    """
    world = slot_world(active, public=True)
    switched, drifted = Event(), Event()
    unsafe = {"value": False}
    real_get, real_verify = FakeOps.http_get, FakeOps.slot_verify

    def get(self, url, timeout):
        if world.route != active:
            switched.set()
            assert drifted.wait(5)
        return real_get(self, url, timeout)

    def verify(self, slot, revision, timeout):
        if revision == PREV and unsafe["value"]:
            raise tx.OpError("retained loaded eligibility off")
        return real_verify(self, slot, revision, timeout)

    monkeypatch.setattr(FakeOps, "http_get", get)
    monkeypatch.setattr(FakeOps, "slot_verify", verify)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run, world)
        assert switched.wait(5)
        unsafe["value"] = True
        drifted.set()
        code, _ = future.result(timeout=5)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route != active
    assert ("route_switch", active) not in world.calls
    assert CAND in [box.revision for box in world.slots.values()]
    assert not any(event.startswith("rollback-verified") for event in world.events)


def test_legacy_rollback_reverifies_before_route_restore(monkeypatch):
    world = first_cutover_world(public=True)
    real_verify = FakeOps.container_deep_health_revision

    def verify(self, cid, timeout):
        if world.route == "blue":
            raise tx.OpError("retained loaded eligibility off")
        return real_verify(self, cid, timeout)

    monkeypatch.setattr(FakeOps, "container_deep_health_revision", verify)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "blue"
    assert ("route_switch", "legacy") not in world.calls
