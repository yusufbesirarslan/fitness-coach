"""Only the adult cutover delta: deterministic host/race/failure models."""
import json
from pathlib import Path

import pytest

from tests.test_r6_deploy_transaction import (
    CAND, PREV, IMG, Box, FakeOps, slot_world, run, tx, names,
)


@pytest.fixture
def adult(monkeypatch, request):
    old_slot = getattr(request, "param", "blue")
    new_slot = tx.CANDIDATE_SLOT[old_slot]
    w = slot_world(old_slot)
    from tests.test_r6_completion_capability import capable_candidate, capable_previous, raised
    raised(capable_previous(capable_candidate(w)))
    w.source_revisions["candidate"].add(tx.ADULT_MIGRATION)
    w.registration_open = False
    w.generations = 0
    w.migration_attempts = []
    w.reloads = 0

    def inventory(self, old, old_revision, new, revision, closed, timeout):
        self._rec("adult_web_inventory", closed, timeout=timeout)
        if closed:
            assert old not in w.slots
        self._fail("direct_origin")
        return True

    def ingress(self, state, candidate, timeout):
        self._rec("ingress_snapshot", state, timeout=timeout)
        switched = w.route == new_slot
        self._fail("ingress_proof")
        generation = 3 + w.reloads if switched and not w.fault("buffered") else 2
        return {"configuration": "d"*64, "master": [1, 10],
                "workers": [[generation, 10 * generation]]}

    def reload_generation(self, slot, timeout):
        self._rec("reload_generation", slot, timeout=timeout)
        assert slot == w.route
        w.reloads += 1

    def latch(self, slot, revision, action, timeout):
        self._rec("registration_latch", action, timeout=timeout)
        w.registration_open = action == "open"
        if action == "open":
            assert old_slot not in w.slots
            assert w.worker.revision == CAND and w.head == CAND
            self._fail("open_readback")
            return "a" * 64

    def runtime(self, slot, revision, timeout):
        self._rec("adult_runtime_proof", timeout=timeout)
        assert not w.registration_open
        self._fail("acl")
        self._fail("web_utc")
        self._fail("worker_utc")

    def candidate(self, slot, revision, image, timeout):
        self._rec("adult_candidate_closed", timeout=timeout)
        assert not w.registration_open
        self._fail("eligibility")
        self._fail("missing_latch")

    def close_worker(self, old_revision, timeout):
        self._rec("close_old_worker", timeout=timeout)
        self._fail("worker_close")
        if w.worker is not None and w.worker.revision == old_revision:
            w.worker = None  # no unless-stopped old container may restart after DDL

    original = FakeOps.release_prepare

    def prepare(self, revision, timeout):
        # Both a request already buffered by nginx and a direct old-origin
        # request have no old process to execute at the migration boundary.
        w.migration_attempts.append((w.slots.get(old_slot), w.registration_open))
        assert old_slot not in w.slots and not w.registration_open
        assert w.worker is None
        original(self, revision, timeout)
        if w.fault("migration_timeout"):
            raise tx.OpError("timeout AFTER schema commit; outcome uncertain")

    monkeypatch.setattr(FakeOps, "adult_web_inventory", inventory, raising=False)
    monkeypatch.setattr(FakeOps, "ingress_snapshot", ingress, raising=False)
    monkeypatch.setattr(FakeOps, "reload_generation", reload_generation, raising=False)
    monkeypatch.setattr(FakeOps, "registration_latch", latch, raising=False)
    monkeypatch.setattr(FakeOps, "adult_runtime_proof", runtime, raising=False)
    monkeypatch.setattr(FakeOps, "coach_release_complete", lambda self, timeout: "a" * 64, raising=False)
    monkeypatch.setattr(FakeOps, "adult_candidate_closed", candidate, raising=False)
    monkeypatch.setattr(FakeOps, "close_old_worker", close_worker, raising=False)
    monkeypatch.setattr(FakeOps, "release_prepare", prepare)
    return w


def assert_no_old_recovery(w, old_slot="blue"):
    assert not any(c[:2] == ("route_switch", old_slot) for c in w.calls)
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
    endpoint = "auth.register" if path == "/register" else "mobile_api.register"
    monkeypatch.setitem(app.view_functions, endpoint, lambda: ({}, 204))
    client = app.test_client()
    # Boundary precedes view/body processing, even for malformed requests.
    assert client.post(path, data="bad", content_type="application/json").status_code == 503
    marker.write_text(json.dumps({"boot": "stale", "revision": CAND}))
    assert client.post(path, json={}).status_code == 503
    marker.write_text(json.dumps({"boot": "boot", "revision": CAND}))
    assert client.post(path, json={}).status_code == 204


def test_slot_source_pins_closed_admission():
    import yaml
    doc = yaml.safe_load(Path("docker-compose.web-slot.yml").read_text())
    assert doc["services"]["web"]["environment"]["R6_REGISTRATION_ADMISSION"] == "closed"


# ── First adult transition from a pre-adult previous release (R6 first-adult baseline fix) ──
# Production shape: the serving release predates ad17de1e0001 and its loaded
# ACCOUNT_ELIGIBILITY_ENABLED is unset. Enabling it there would switch on the
# legacy (pre-18+) rules, so the transaction must not require it — but only for
# the first adult transition, and never for the candidate.
MUTATIONS = ("slot_remove", "close_old_worker", "release_prepare", "slot_start",
             "route_switch", "registration_latch", "build_image")


def first_adult(adult):
    adult.faults["delta"] = {tx.ADULT_MIGRATION: "expand"}
    adult.ineligible_revisions.add(PREV)
    return adult


def test_first_adult_transition_needs_no_legacy_eligibility_and_opens_only_verified_candidate(adult):
    code, transaction = run(first_adult(adult))
    assert code == tx.EXIT_OK
    prev_checks = [c for c in adult.eligibility_checks if c[1] == PREV]
    cand_checks = [c for c in adult.eligibility_checks if c[1] == CAND]
    assert prev_checks and all(required is False for _, _, required in prev_checks)
    assert cand_checks and all(required is True for _, _, required in cand_checks)
    events = [e[0] for e in transaction.events]
    assert "previous-eligibility-not-required" in events and "previous-eligibility" not in events
    assert events.index("previous-eligibility-not-required") < events.index("old-registration-closed")
    # Old registration is closed before migration and only the verified candidate opens.
    assert adult.migration_attempts == [(None, False)]
    n = names(adult)
    assert n.index("slot_remove") < n.index("release_prepare")
    assert adult.registration_open and adult.calls.index(("registration_latch", "open")) > n.index("checkout_reset")
    assert transaction.previous_eligibility_proven is False


def test_first_adult_transition_still_refuses_an_ineligible_candidate_after_closure(adult):
    first_adult(adult).ineligible_revisions.add(CAND)
    code, _ = run(adult)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert_no_old_recovery(adult)


@pytest.mark.parametrize("delta", [{"e3f4a5b6c7d8": "expand"}, {}])
def test_later_adult_release_still_requires_previous_eligibility_before_any_mutation(adult, delta):
    # ad17de1e0001 is already in the previous source: not the first adult transition.
    adult.faults["delta"] = delta
    adult.ineligible_revisions.add(PREV)
    code, transaction = run(adult)
    assert code == tx.EXIT_ROLLED_BACK
    assert not set(MUTATIONS) & set(names(adult))
    assert "blue" in adult.slots and adult.slots["blue"].revision == PREV
    assert not any(e[0] == "previous-eligibility-not-required" for e in transaction.events)


def test_non_adult_candidate_with_ineligible_previous_refuses_before_any_mutation():
    from tests.test_r6_completion_capability import capable_candidate, capable_previous, raised
    w = slot_world("blue")
    raised(capable_previous(capable_candidate(w)))
    w.ineligible_revisions.add(PREV)
    code, transaction = run(w)
    assert code == tx.EXIT_ROLLED_BACK
    assert not set(MUTATIONS) & set(names(w))
    assert transaction.adult_transition is False


def test_first_adult_failure_before_closure_rolls_back_cleanly_without_demanding_legacy_eligibility(adult):
    first_adult(adult).faults["direct_origin"] = True
    code, transaction = run(adult)
    assert code == tx.EXIT_ROLLED_BACK
    assert "release_prepare" not in names(adult) and "slot_remove" not in names(adult)
    assert adult.slots["blue"].revision == PREV
    assert all(required is False for _, rev, required in adult.eligibility_checks if rev == PREV)


def test_adult_delta_outside_adult_graph_is_refused(adult):
    adult.faults["delta"] = {tx.ADULT_MIGRATION: "expand"}
    adult.source_revisions["candidate"].discard(tx.ADULT_MIGRATION)
    code, _ = run(adult)
    assert code == tx.EXIT_ROLLED_BACK
    assert not set(MUTATIONS) & set(names(adult))


def test_host_slot_verify_never_waives_candidate_and_omits_flag_only_for_previous(tmp_path):
    from tests.test_r6_deploy_transaction import Recorder, host
    recorder = Recorder({" verify ": (0, json.dumps({"deep_health_revision": PREV}), "")})
    ops = host(tmp_path, recorder)
    ops.slot_verify("blue", PREV, 60, require_eligibility=False)
    assert "--require-eligibility" not in recorder.calls[-1]
    assert recorder.calls[-1][-1] == "--allow-existing-files"
    with pytest.raises(tx.OpError, match="verify failed"):
        ops.slot_verify("blue", PREV, 60)            # default still requires eligibility_enforced
    calls = len(recorder.calls)
    with pytest.raises(tx.OpError, match="cannot be waived"):
        ops.slot_verify("green", CAND, 60, require_eligibility=False)
    assert len(recorder.calls) == calls              # refused before any host command


def test_non_adult_legacy_route_with_ineligible_previous_refuses_before_any_mutation():
    from tests.test_r6_deploy_transaction import first_cutover_world
    w = first_cutover_world()
    w.ineligible_revisions.add(PREV)
    code, transaction = run(w)
    assert code == tx.EXIT_ROLLED_BACK
    assert not set(MUTATIONS) & set(names(w))
    assert transaction.previous_eligibility_proven is False
    assert any(c[0] == "container_deep_health_revision" for c in w.calls)


def test_non_adult_rollback_reverifies_previous_with_eligibility():
    from tests.test_r6_completion_capability import capable_candidate, capable_previous, raised
    w = slot_world("blue", public=True)
    raised(capable_previous(capable_candidate(w)))
    w.faults["verify_fail_on"] = ("green", 2)          # candidate fails after switch
    code, transaction = run(w)
    assert code == tx.EXIT_ROLLED_BACK and w.route == "blue"
    assert transaction.previous_eligibility_proven is True
    prev = [required for _, rev, required in w.eligibility_checks if rev == PREV]
    # baseline identity proof (deferred) -> strict gate -> strict rollback re-verifies
    assert prev[0] is False and len(prev) >= 3 and all(prev[1:])
