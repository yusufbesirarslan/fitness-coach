"""PRIV-K6 rollback gate, Layer B: release admission in the r6 transaction.

Hermetic, on the same ``World``/``FakeOps`` host model as the R6-03A suite
(``tests/test_r6_deploy_transaction.py``). Layer A -- the database fence --
holds completion SAFETY whatever this layer does; this layer is about which
image the pipeline serves, restores and activates. Each test asserts the final
serving state and the floor, not just an exit code.

Synthetic only: no Docker, no database, no AWS. A purely modelled r6 run is not
proof of production enforcement; it pins the orchestration rules.
"""
from __future__ import annotations

import pytest

from tests.test_r6_deploy_transaction import (
    CAND, IMG, PREV, FakeOps, assert_untouched_first_cutover, first_cutover_world,
    index, names, run, slot_world, tx,
)

CAPABLE = "workout_completion_claims_v1\n"
FENCE = "d7e3a9c1f5b8"
WORKER_ONLY = "sha256:" + "7" * 64


class FenceOps(FakeOps):
    """release-prepare of a capable candidate installs (and verifies) the fence."""

    def release_prepare(self, revision, timeout):
        super().release_prepare(revision, timeout)
        if self.w.faults.get("prepare_installs_fence", True):
            self.w.floor["fence"] = "verified"


def capable_candidate(world):
    world.manifests[IMG[CAND]] = CAPABLE
    world.source_manifests["candidate"] = CAPABLE
    world.source_revisions["candidate"] = {FENCE, "f9c6d0a1b2e3"}
    return world


def capable_previous(world):
    world.manifests[IMG[PREV]] = CAPABLE
    world.source_manifests["previous"] = CAPABLE
    world.source_revisions["previous"] = {FENCE, "f9c6d0a1b2e3"}
    return world


def raised(world, *, fence="verified", claims=True):
    world.floor.update(floor="raised", fence=fence, claims_present=claims,
                       raised_by_revision=PREV)
    return world


def go(world, **kwargs):
    import tests.test_r6_deploy_transaction as base
    original = base.FakeOps
    base.FakeOps = FenceOps
    try:
        return run(world, **kwargs)
    finally:
        base.FakeOps = original


def events(world, prefix):
    return [e for e in world.events if e.startswith(prefix)]


def floor_calls(world, action):
    return [c for c in world.calls if c[:2] == ("completion_floor", action)]


# ── first activation (the K6 release itself) ───────────────────────────────

def test_first_k6_release_is_admitted_dormant_and_activates_only_after_retirement():
    world = capable_candidate(first_cutover_world())
    code, transaction = go(world)

    assert code == tx.EXIT_OK, world.events
    assert world.floor["floor"] == "raised" and world.floor["raised_by_revision"] == CAND
    assert transaction.candidate_capabilities == {"workout_completion_claims_v1"}
    assert transaction.previous_capabilities == frozenset()
    raise_at = index(world, "completion_floor", "raise")
    # Activation strictly after the old backend is proven retired, and after
    # the commit point (checkout), never before.
    assert index(world, "stop_and_remove") < raise_at
    assert index(world, "checkout_reset", CAND) < raise_at
    assert raise_at < index(world, "housekeeping")
    assert events(world, "capability-activated")
    assert events(world, "release-complete")


def test_admission_reads_exact_image_ids_never_tags():
    world = capable_candidate(first_cutover_world())
    go(world)
    for call in world.calls:
        if call[0] == "image_capability_manifest":
            assert call[1].startswith("sha256:"), call
        if call[0] == "completion_floor":
            assert call[2] == IMG[CAND], call


def test_admission_runs_before_any_database_mutation():
    world = capable_candidate(first_cutover_world())
    go(world)
    first_status = index(world, "completion_floor", "status")
    assert index(world, "image_baked_revision", f"axisai-web:{CAND}") < first_status
    assert first_status < index(world, "release_prepare", CAND)


# ── candidate admission ─────────────────────────────────────────────────────

def test_compatible_candidate_is_accepted_after_activation():
    world = raised(capable_previous(capable_candidate(first_cutover_world())))
    code, _ = go(world)
    assert code == tx.EXIT_OK
    assert world.legacy is None and world.slots["blue"].revision == CAND
    assert floor_calls(world, "raise") == []          # already raised: no-op
    assert events(world, "capability-floor-present")


@pytest.mark.parametrize("floor_state", ["raised", "claims", "unknown", "malformed",
                                         "tampered"])
def test_incapable_candidate_is_refused_before_any_mutation(floor_state):
    world = first_cutover_world()
    if floor_state == "raised":
        raised(world)
    elif floor_state == "claims":
        world.floor.update(claims_present=True)       # claims without a floor
    elif floor_state == "unknown":
        world.faults["floor_status"] = True
    elif floor_state == "malformed":
        world.faults["floor_malformed"] = True
    else:
        world.floor.update(fence="invalid")
    code, _ = go(world)

    assert code == tx.EXIT_ROLLED_BACK
    assert "release_prepare" not in names(world)
    assert "slot_start" not in names(world) and "route_switch" not in names(world)
    assert_untouched_first_cutover(world)
    assert any("below the completion capability floor" in e for e in world.events)


def test_incapable_candidate_is_admitted_only_while_the_floor_is_proven_absent():
    world = first_cutover_world()                      # no manifests anywhere
    code, transaction = go(world)
    assert code == tx.EXIT_OK
    assert transaction.candidate_capabilities == frozenset()
    assert floor_calls(world, "raise") == []           # never activates


@pytest.mark.parametrize("forgery", ["image_only", "differs", "source_only"])
def test_declaration_not_matching_the_reviewed_source_is_refused(forgery):
    world = first_cutover_world()
    world.source_revisions["candidate"] = {FENCE}
    if forgery in ("image_only", "differs"):
        world.manifests[IMG[CAND]] = CAPABLE
    if forgery == "differs":
        world.source_manifests["candidate"] = "\n"
    if forgery == "source_only":
        world.source_manifests["candidate"] = CAPABLE
    code, _ = go(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert "release_prepare" not in names(world)
    assert any("capability declaration refused" in e for e in world.events)


@pytest.mark.parametrize("text", ["workout_completion_claims_v1",
                                  "workout_completion_claims_v1\nworkout_completion_claims_v1\n",
                                  "workout_completion_claims_v2\n", "\n", ""])
def test_malformed_manifest_is_refused(text):
    world = first_cutover_world()
    world.manifests[IMG[CAND]] = text
    world.source_manifests["candidate"] = text
    world.source_revisions["candidate"] = {FENCE}
    code, _ = go(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert "release_prepare" not in names(world)


def test_declaration_without_its_implementing_migration_is_refused():
    world = first_cutover_world()
    world.manifests[IMG[CAND]] = CAPABLE
    world.source_manifests["candidate"] = CAPABLE
    world.source_revisions["candidate"] = {"f9c6d0a1b2e3"}   # fence migration missing
    code, _ = go(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert any("declared without its implementation" in e for e in world.events)


def test_floor_raised_between_admission_and_switch_refuses_an_incapable_candidate():
    world = first_cutover_world()

    class RaisingOps(FenceOps):
        def release_prepare(self, revision, timeout):
            super().release_prepare(revision, timeout)
            raised(self.w)                             # someone raised it meanwhile

    import tests.test_r6_deploy_transaction as base
    original, base.FakeOps = base.FakeOps, RaisingOps
    try:
        code, _ = run(world)
    finally:
        base.FakeOps = original
    assert code == tx.EXIT_ROLLED_BACK
    assert "route_switch" not in names(world)
    assert world.route == "legacy" and world.legacy.revision == PREV


def test_capable_candidate_whose_fence_does_not_verify_never_takes_traffic():
    world = capable_candidate(first_cutover_world(prepare_installs_fence=False))
    code, _ = go(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert "route_switch" not in names(world)
    assert any("fence is not verified" in e for e in world.events)
    assert world.route == "legacy" and world.legacy.revision == PREV


# ── automatic rollback ──────────────────────────────────────────────────────

def test_pre_activation_rollback_to_an_incapable_previous_is_allowed():
    """Before the floor is raised no claim can exist (Layer A I2): restoring the
    pre-K6 image is safe, and the zero-outage rollback is preserved."""
    world = capable_candidate(first_cutover_world(public=True))
    code, _ = go(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert_untouched_first_cutover(world)
    assert floor_calls(world, "raise") == []


@pytest.mark.parametrize("failure", ["public", "worker_unhealthy", "checkout"])
def test_post_activation_rollback_never_restores_an_incapable_previous(failure):
    world = raised(capable_candidate(slot_world("blue", **{failure: True})))
    code, _ = go(world)

    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "green", "the route must stay on the capable candidate"
    assert world.slots["green"].revision == CAND
    assert ("route_switch", "blue") not in world.calls[index(world, "route_switch", "green") + 1:]
    assert not any(c[:2] == ("worker_up", "previous") for c in world.calls)
    assert events(world, "rollback-refused-below-floor")
    assert any("ROLLBACK_BELOW_FLOOR" in e for e in world.events)
    assert world.floor["floor"] == "raised"            # never lowered


def test_post_activation_rollback_to_a_capable_previous_is_the_normal_rollback():
    world = raised(capable_previous(capable_candidate(slot_world("blue", public=True))))
    code, _ = go(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == "blue" and set(world.slots) == {"blue"}
    assert not events(world, "rollback-refused-below-floor")


def test_partial_capability_web_capable_worker_not_refuses_both():
    world = raised(capable_previous(capable_candidate(slot_world("blue", public=True))))
    world.worker.image_id = WORKER_ONLY                # worker image differs, no manifest
    world.baked[WORKER_ONLY] = PREV
    world.images[f"axisai-worker-rollback:{PREV}"] = WORKER_ONLY
    code, transaction = go(world)
    assert transaction.previous_capabilities == frozenset()
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "green"
    assert events(world, "rollback-refused-below-floor")


def test_unknown_floor_at_rollback_time_is_treated_as_raised():
    world = capable_candidate(slot_world("blue", public=True))

    class UnknownAtRollback(FenceOps):
        def completion_floor(self, image_id, revision, action, timeout):
            if "route_switch" in names(self.w):
                raise tx.OpError("database unreachable")
            return super().completion_floor(image_id, revision, action, timeout)

    import tests.test_r6_deploy_transaction as base
    original, base.FakeOps = base.FakeOps, UnknownAtRollback
    try:
        code, _ = run(world)
    finally:
        base.FakeOps = original
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "green"


def test_previous_with_an_unknown_image_identity_is_never_capable():
    world = raised(capable_previous(capable_candidate(slot_world("blue", public=True))))

    class NoIdentity(FenceOps):
        def slot_verify(self, slot, revision, timeout):
            proof = super().slot_verify(slot, revision, timeout)
            return {**proof, "container": {}} if slot == "blue" and \
                "build_image" not in names(self.w) else proof

    import tests.test_r6_deploy_transaction as base
    original, base.FakeOps = base.FakeOps, NoIdentity
    try:
        code, transaction = run(world)
    finally:
        base.FakeOps = original
    assert transaction.previous_web_image_id is None
    assert transaction.previous_capabilities == frozenset()
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE and world.route == "green"


# ── activation tail ─────────────────────────────────────────────────────────

def test_activation_never_runs_while_the_old_backend_is_retained():
    world = capable_candidate(first_cutover_world(legacy_remove=True))
    code, _ = go(world)
    assert code == tx.EXIT_COMMITTED_RESIDUE
    assert floor_calls(world, "raise") == []
    assert world.floor["floor"] == "absent"


def test_activation_failure_is_committed_residue_with_the_floor_untouched():
    world = capable_candidate(first_cutover_world(floor_raise=True))
    code, _ = go(world)
    assert code == tx.EXIT_COMMITTED_RESIDUE
    assert world.floor["floor"] == "absent"
    assert events(world, "capability-activation-incomplete")
    assert not events(world, "release-complete")


def test_activation_refuses_an_unverified_fence():
    world = capable_candidate(first_cutover_world())

    class FenceGoneAfterSwitch(FenceOps):
        def stop_and_remove(self, container_id, timeout):
            super().stop_and_remove(container_id, timeout)
            self.w.floor["fence"] = "invalid"

    import tests.test_r6_deploy_transaction as base
    original, base.FakeOps = base.FakeOps, FenceGoneAfterSwitch
    try:
        code, _ = run(world)
    finally:
        base.FakeOps = original
    assert code == tx.EXIT_COMMITTED_RESIDUE
    assert floor_calls(world, "raise") == []


def test_incapable_release_never_activates_and_rerun_after_activation_is_idempotent():
    world = capable_candidate(first_cutover_world())
    assert go(world)[0] == tx.EXIT_OK
    assert world.floor["raised_by_revision"] == CAND
    # A capable successor sees the floor present and never re-raises.
    again = raised(capable_previous(capable_candidate(slot_world("blue"))))
    assert go(again)[0] == tx.EXIT_OK
    assert floor_calls(again, "raise") == []


# ── pure helpers ────────────────────────────────────────────────────────────

def test_manifest_grammar_and_floor_status_parsing_fail_closed():
    assert tx.parse_capability_manifest(CAPABLE) == {"workout_completion_claims_v1"}
    for bad in (None, "", "\n", "x\n", CAPABLE * 2, CAPABLE.rstrip(), " " + CAPABLE):
        assert tx.parse_capability_manifest(bad) is None
    good = {"result": "ok", "capability": "workout_completion_claims_v1",
            "floor": "absent", "fence": "verified", "claims_present": False}
    assert tx.parse_floor_status(good) == {"floor": "absent", "fence": "verified",
                                           "claims_present": False}
    for mutate in ({"result": "error"}, {"floor": "lowered"}, {"fence": "maybe"},
                   {"claims_present": 0}, {"capability": "other"}):
        assert tx.parse_floor_status({**good, **mutate}) is None
    assert tx.Transaction._floor_requires_capability(None) is True
