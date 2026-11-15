"""Only the adult cutover delta: deterministic host/race/failure models."""
import json
from pathlib import Path

import pytest

from tests.test_r6_deploy_transaction import (
    CAND, PREV, IMG, Box, FakeOps, slot_world, run, tx, names,
)


@pytest.fixture
def adult(monkeypatch):
    w = slot_world("blue")
    from tests.test_r6_completion_capability import capable_candidate, capable_previous, raised
    raised(capable_previous(capable_candidate(w)))
    w.source_revisions["candidate"].add(tx.ADULT_MIGRATION)
    w.registration_open = False
    w.generations = 0
    w.migration_attempts = []

    def inventory(self, old, old_revision, new, revision, closed, timeout):
        self._rec("adult_web_inventory", closed, timeout=timeout)
        if closed:
            assert old not in w.slots
        self._fail("direct_origin")
        return True

    def ingress(self, state, candidate, timeout):
        self._rec("ingress_snapshot", state, timeout=timeout)
        switched = w.route == "green"
        self._fail("ingress_proof")
        return {"configuration": "d"*64, "master": [1, 10],
                "workers": [[3, 30] if switched and not w.fault("buffered") else [2, 20]]}

    def latch(self, slot, revision, action, timeout):
        self._rec("registration_latch", action, timeout=timeout)
        w.registration_open = action == "open"
        if action == "open":
            assert "blue" not in w.slots
            assert w.worker.revision == CAND and w.head == CAND
            self._fail("open_readback")

    def runtime(self, slot, revision, timeout):
        self._rec("adult_runtime_proof", timeout=timeout)
        assert not w.registration_open
        self._fail("acl")
        self._fail("web_utc")
        self._fail("worker_utc")

    def candidate(self, slot, revision, timeout):
        self._rec("adult_candidate_closed", timeout=timeout)
        assert not w.registration_open
        self._fail("eligibility")
        self._fail("missing_latch")

    def close_worker(self, old_revision, timeout):
        self._rec("close_old_worker", timeout=timeout)
        self._fail("worker_close")
        w.worker = None  # no unless-stopped old container may restart after DDL

    original = FakeOps.release_prepare

    def prepare(self, revision, timeout):
        # Both a request already buffered by nginx and a direct old-origin
        # request have no old process to execute at the migration boundary.
        w.migration_attempts.append((w.slots.get("blue"), w.registration_open))
        assert "blue" not in w.slots and not w.registration_open
        assert w.worker is None
        original(self, revision, timeout)
        if w.fault("migration_timeout"):
            raise tx.OpError("timeout AFTER schema commit; outcome uncertain")

    monkeypatch.setattr(FakeOps, "adult_web_inventory", inventory, raising=False)
    monkeypatch.setattr(FakeOps, "ingress_snapshot", ingress, raising=False)
    monkeypatch.setattr(FakeOps, "registration_latch", latch, raising=False)
    monkeypatch.setattr(FakeOps, "adult_runtime_proof", runtime, raising=False)
    monkeypatch.setattr(FakeOps, "adult_candidate_closed", candidate, raising=False)
    monkeypatch.setattr(FakeOps, "close_old_worker", close_worker, raising=False)
    monkeypatch.setattr(FakeOps, "release_prepare", prepare)
    return w


def assert_no_old_recovery(w):
    assert not any(c[:2] == ("route_switch", "blue") for c in w.calls)
    assert not any(c[:2] == ("worker_up", "previous") for c in w.calls)
    assert not w.registration_open


def test_adult_success_closes_before_migration_and_opens_last(adult):
    code, transaction = run(adult)
    assert code == tx.EXIT_OK
    assert adult.registration_open and adult.migration_attempts == [(None, False)]
    assert adult.worker.image_id == IMG[CAND]
    n = names(adult)
    assert n.index("slot_remove") < n.index("release_prepare")
    assert n.index("adult_runtime_proof") < n.index("checkout_reset")
    assert adult.calls.index(("registration_latch", "open")) > n.index("checkout_reset")
    assert any(e[0] == "adult-reopen-ready" for e in transaction.events)


@pytest.mark.parametrize("failure", ["migration_timeout", "release_prepare", "eligibility",
    "missing_latch", "acl", "web_utc", "worker_utc", "public", "worker_unhealthy",
    "open_readback", "buffered", "worker_close"])
def test_failure_never_reopens_old_or_restarts_old_worker(adult, failure):
    adult.faults[failure] = True
    code, transaction = run(adult)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert_no_old_recovery(adult)
    assert not any(e[0] == "rollback-verified" for e in transaction.events)
    if failure == "migration_timeout":
        assert adult.db_revision == "new-head"  # timeout never means undone


def test_unknown_origin_refuses_before_closure_or_migration(adult):
    adult.faults["direct_origin"] = True
    code, _ = run(adult)
    assert code == tx.EXIT_ROLLED_BACK
    assert "release_prepare" not in names(adult)
    assert "blue" in adult.slots


def test_generation_deadline_keeps_closed_and_does_not_force_retirement(adult):
    adult.faults["buffered"] = True
    code, _ = run(adult)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert 2 <= sum(c[0] == "ingress_snapshot" for c in adult.calls) <= 78
    assert_no_old_recovery(adult)


def test_invalid_full_fence_blocks_public_reopening(adult):
    adult.floor["fence"] = "invalid"
    code, _ = run(adult)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert_no_old_recovery(adult)
    assert not any(c[:2] == ("registration_latch", "open") for c in adult.calls)


def test_postcommit_failure_never_rolls_back(adult, monkeypatch):
    monkeypatch.setattr(tx.Transaction, "_retire", lambda self: (_ for _ in ()).throw(
        tx.OpError("postcommit housekeeping")))
    code, _ = run(adult)
    assert code == tx.EXIT_COMMITTED_RESIDUE
    assert adult.registration_open and adult.head == CAND
    assert not any(c[:2] == ("worker_up", "previous") for c in adult.calls)


def test_authorized_forward_recovery_recreates_stopped_exact_candidate(adult):
    adult.slots.pop("blue")
    adult.worker = None
    adult.db_heads = frozenset({tx.ADULT_MIGRATION})
    adult.slots["green"] = Box(id="stopped", image_id=IMG[CAND], revision=CAND,
        app_revision=CAND, status="exited", health=None)
    code, transaction = run(adult, mode="forward-recovery")
    assert code == tx.EXIT_OK
    assert adult.registration_open and adult.worker.revision == CAND
    assert any(e[0] == "recovery-schema-evidence" for e in transaction.events)
    assert ("slot_remove", "green", CAND) in adult.calls


def test_forward_recovery_never_inferred_from_pre_migration_head(adult):
    adult.db_heads = frozenset({"d7e3a9c1f5b8"})
    code, transaction = run(adult, mode="forward-recovery")
    assert code == tx.EXIT_ROLLED_BACK  # proven untouched baseline; recovery refused
    assert not transaction.registration_closure_attempted
    assert "release_prepare" not in names(adult)


def test_partial_closure_failure_never_claims_processes_unchanged(adult):
    adult.faults["worker_close"] = True
    code, transaction = run(adult)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert "blue" not in adult.slots
    outcome = next(e[1]["manual"] for e in transaction.events if e[0] == "rollback-incomplete")
    assert "FORWARD_RECOVERY_INCOMPLETE" in outcome
    assert "slots, worker and schema unchanged" not in outcome


@pytest.mark.parametrize("path", ["/register", "/api/v1/auth/register"])
def test_loaded_latch_blocks_both_registration_routes(app, monkeypatch, tmp_path, path):
    from app.services import r6_runtime_admission as a
    app.config.update(R6_REGISTRATION_CLOSED=True, R6_ADMISSION_BOOT="boot",
                      BUILD_REVISION=CAND)
    marker = tmp_path / "open.json"
    monkeypatch.setattr(a, "WEB_RECEIPT", str(marker))
    client = app.test_client()
    # Boundary precedes view/body processing, even for malformed requests.
    assert client.post(path, data="bad", content_type="application/json").status_code == 503
    marker.write_text(json.dumps({"boot": "stale", "revision": CAND}))
    assert client.post(path, json={}).status_code == 503
    marker.write_text(json.dumps({"boot": "boot", "revision": CAND}))
    response = client.post(path, json={})
    error = (response.get_json() or {}).get("error")
    assert not isinstance(error, dict) or error.get("code") != "REGISTRATION_TEMPORARILY_CLOSED"


def test_slot_source_pins_closed_admission():
    import yaml
    doc = yaml.safe_load(Path("docker-compose.web-slot.yml").read_text())
    assert doc["services"]["web"]["environment"]["R6_REGISTRATION_ADMISSION"] == "closed"
