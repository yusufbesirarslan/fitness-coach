"""R6-R1-28: forward recovery after the previous web dies post-migration.

Same hermetic ``World``/``FakeOps`` host model as the R6-03A suite. The
production fact being modelled: release-prepare moves the schema past the
previous revision, whose read-only boot then refuses (``not_at_head``,
proved on real PostgreSQL in ``tests/test_r6_forward_recovery_pg.py``), so a
dead previous web can never be re-verified and the NORMAL baseline refuses
every later run. Forward recovery must restore service from the exact
candidate without ever depending on, restoring or restarting that writer.

Matrix (task MON-R6-R1-POST-MIGRATION-RECOVERY-REMEDIATION-28):
A normal R1 unchanged · B death before migration refuses · C death after
migration recovers · D switch failure · E worker failure · F floor/fence
failure · G interrupted / uncertain attempts converge · H stale, foreign or
ambiguous evidence refuses · I the old writer is never resurrected · J Redis
identity. Every test asserts the final serving state, not only an exit code.
"""
from __future__ import annotations

import pytest

from tests.test_r6_completion_capability import (
    CAPABLE, FENCE, FenceOps, capable_candidate, capable_previous,
)
from tests.test_r6_deploy_transaction import (
    CAND, GOOD_ARGS, IMG, OTHER, PREV, Box, FakeOps, World, assert_redis_untouched,
    contract, names, run, slot_world, tx,
)

RECOVERY = {"mode": "forward-recovery"}


# ── world builders ──────────────────────────────────────────────────────────

def r1_world(**faults):
    """Production as R1 finds it: green serves PREV, both graphs known."""
    w = slot_world("green", **faults)
    w.source_revisions = {"previous": {"p1"}, "candidate": {"p1", "c1"}}
    return w


def kill_previous(w, *, worker=True):
    """The previous web can never boot again against the migrated schema (its
    read-only startup refuses): restart-looping, never healthy. The worker
    skips DB init, so after a reboot it may or may not be healthy; both cases
    are exercised (``worker=False`` keeps it healthy)."""
    w.slots["green"].status = "restarting"
    w.slots["green"].health = None
    w.slots["green"].deep_ok = False
    if worker:
        w.worker.status = "restarting"
        w.worker.health = "unhealthy"
    return w


def migrated(w):
    """Release-prepare of the candidate already committed (12 R1 migrations)."""
    w.db_heads = w.candidate_heads
    w.db_revision = "new-head"
    return w


def green_dead_after_migration(**faults):
    return kill_previous(migrated(r1_world(**faults)))


def recover(world, **kwargs):
    return run(world, **RECOVERY, **kwargs)


def fence_recover(world, **kwargs):
    import tests.test_r6_deploy_transaction as base
    original = base.FakeOps
    base.FakeOps = FenceOps
    try:
        return recover(world, **kwargs)
    finally:
        base.FakeOps = original


def calls(world, *prefix):
    return [c for c in world.calls if c[:len(prefix)] == prefix]


def assert_never_resurrected(world):
    """I: no route back to, restart of, or start of the previous writer."""
    assert not calls(world, "route_switch", "green")
    assert not calls(world, "worker_up", "previous")
    assert not calls(world, "slot_start", "green")
    assert not calls(world, "slot_start", "blue", PREV)


def assert_recovered(world):
    assert world.route == "blue" and not world.route_unknown
    assert world.slots["blue"].revision == CAND and world.slots["blue"].status == "running"
    assert "green" not in world.slots                         # dead writer retired
    assert world.worker.revision == CAND and world.worker.health == "healthy"
    assert world.worker.image_id == IMG[CAND]
    assert world.head == CAND
    assert world.db_heads == world.candidate_heads
    assert_redis_untouched(world)
    assert_never_resurrected(world)


def assert_no_host_mutation_past_build(world):
    for name in ("release_prepare", "slot_start", "slot_remove", "route_switch",
                 "worker_up", "checkout_reset", "stop_and_remove"):
        assert name not in names(world), name


# ── A. normal R1 is unchanged ───────────────────────────────────────────────

def test_a_normal_green_healthy_r1_succeeds_without_recovery_evidence():
    world = r1_world()
    code, transaction = run(world)
    assert code == tx.EXIT_OK and not transaction.forward_recovery
    assert world.route == "blue" and world.head == CAND and "green" not in world.slots
    assert "schema_heads" not in names(world)                 # normal path untouched
    assert not any(e.startswith("recovery-") for e in world.events)


def test_a_normal_mode_is_the_default_and_unknown_modes_refuse():
    assert tx.Transaction(FakeOps(World()), deploy_sha=CAND, previous_commit=PREV,
                          public_health_url="https://x/health").forward_recovery is False
    with pytest.raises(ValueError):
        tx.Transaction(FakeOps(World()), deploy_sha=CAND, previous_commit=PREV,
                       public_health_url="https://x/health", mode="skip-health-check")


def test_a_cli_requires_an_explicit_mode():
    args = list(GOOD_ARGS)
    position = args.index("--mode")
    del args[position:position + 2]
    with pytest.raises(SystemExit):
        tx.main(args, environ=contract.host_timeout_environment())


def test_a_recovery_with_a_healthy_previous_keeps_the_full_rollback():
    """Green survived the failed attempt: recovery proves it and, on failure,
    returns to it exactly like the normal path."""
    world = migrated(r1_world(worker_unhealthy=True))
    code, transaction = recover(world)
    assert transaction.previous_proven
    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == "green" and world.slots["green"].revision == PREV
    assert world.worker.revision == PREV and "blue" not in world.slots


# ── B. green dies BEFORE migration ──────────────────────────────────────────

def test_b_normal_run_refuses_a_dead_previous_before_any_mutation():
    world = kill_previous(r1_world())
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert "build_image" not in names(world)
    assert_no_host_mutation_past_build(world)


def test_b_recovery_refuses_when_the_schema_has_not_moved_past_previous():
    world = kill_previous(r1_world())                         # db still at p1
    code, transaction = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE and not transaction.previous_proven
    assert any("has not moved past the previous revision" in e for e in world.events)
    assert_no_host_mutation_past_build(world)
    assert world.db_heads == frozenset({"p1"}) and world.route == "green"
    assert any("release_prepared=no" in e for e in world.events)


# ── C. green dies AFTER migration ───────────────────────────────────────────

def test_c_normal_run_cannot_recover_this_state():
    world = green_dead_after_migration()
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK and world.route == "green"
    assert "release_prepare" not in names(world)


def test_c_forward_recovery_restores_service_without_the_previous_web():
    world = green_dead_after_migration()
    code, transaction = recover(world)
    assert code == tx.EXIT_OK and not transaction.previous_proven
    assert_recovered(world)
    assert calls(world, "route_switch", "blue")
    # Evidence ordering: schema proof before the (idempotent) release-prepare.
    order = names(world)
    assert order.index("schema_heads") < order.index("release_prepare") \
        < order.index("slot_start") < order.index("route_switch")
    assert "tag_image" not in order                           # no unproven rollback material
    assert any(e.startswith("recovery-schema-evidence current=c1 advanced=c1")
               for e in world.events)


def test_c_previous_web_dead_but_worker_alive_still_recovers_forward_only():
    world = kill_previous(migrated(r1_world()), worker=False)
    code, transaction = recover(world)
    assert code == tx.EXIT_OK and not transaction.previous_proven
    assert_recovered(world)


def test_c_previous_slot_already_gone_recovers():
    world = green_dead_after_migration()
    del world.slots["green"]
    code, _ = recover(world)
    assert code == tx.EXIT_OK
    assert_recovered(world)


def test_c_running_candidate_left_by_an_earlier_attempt_is_reused():
    world = green_dead_after_migration()
    world.slots["blue"] = Box(id="blue-left".ljust(64, "0"), image_id=IMG[CAND],
                              revision=CAND, app_revision=CAND)
    world.images[f"axisai-web:{CAND}"] = IMG[CAND]
    code, _ = recover(world)
    assert code == tx.EXIT_OK and world.slots["blue"].id.startswith("blue-left")
    assert not calls(world, "slot_remove", "blue")
    assert_recovered(world)


def test_c_dead_candidate_left_by_an_earlier_attempt_is_replaced():
    world = green_dead_after_migration()
    world.slots["blue"] = Box(id="blue-dead".ljust(64, "0"), image_id=IMG[CAND],
                              revision=CAND, app_revision=CAND, status="exited",
                              health=None)
    code, _ = recover(world)
    assert code == tx.EXIT_OK
    assert calls(world, "slot_remove", "blue", CAND)
    assert not world.slots["blue"].id.startswith("blue-dead")
    assert any(e.startswith("stale-candidate-removed slot=blue") for e in world.events)
    assert_recovered(world)


def test_c_attempt_that_switched_before_dying_is_resumed_without_switching():
    world = green_dead_after_migration()
    world.slots["blue"] = Box(id="blue-live".ljust(64, "0"), image_id=IMG[CAND],
                              revision=CAND, app_revision=CAND)
    world.images[f"axisai-web:{CAND}"] = IMG[CAND]
    world.route = "blue"                                      # switched, not committed
    code, transaction = recover(world)
    assert code == tx.EXIT_OK and transaction.already_switched
    assert "route_switch" not in names(world)
    assert any(e.startswith("route-already-candidate") for e in world.events)
    assert_recovered(world)


def test_c_worker_already_replaced_by_an_earlier_attempt_is_proven_again():
    world = green_dead_after_migration()
    world.worker = Box(id="worker-cand".ljust(64, "0"), image_id=IMG[CAND],
                       revision=CAND, app_revision=CAND)
    code, _ = recover(world)
    assert code == tx.EXIT_OK
    assert_recovered(world)


# ── D. candidate starts, route switch fails ─────────────────────────────────

@pytest.mark.parametrize("mode", ["restored", "unknown"])
def test_d_switch_failure_keeps_the_candidate_and_never_routes_back(mode):
    world = green_dead_after_migration(switch_fail_to="blue", switch_mode=mode)
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.slots["blue"].revision == CAND and world.slots["blue"].status == "running"
    assert not calls(world, "slot_remove", "blue")
    assert world.worker.revision == PREV                      # untouched, not restarted
    assert world.head == PREV
    assert_never_resurrected(world)
    assert any("FORWARD_RECOVERY_INCOMPLETE" in e and "candidate=blue" in e
               for e in world.events)
    # A new attempt-bound recovery attempt converges from exactly this state.
    world.faults.pop("switch_fail_to")
    world.route_unknown = False
    code, _ = recover(world)
    assert code == tx.EXIT_OK
    assert_recovered(world)


# ── E. candidate serves, worker replacement fails ───────────────────────────

@pytest.mark.parametrize("fault", ["worker_unhealthy", "worker_wrong_app_revision",
                                   "worker_wrong_baked"])
def test_e_worker_failure_keeps_the_candidate_serving_and_converges_on_rerun(fault):
    world = green_dead_after_migration(**{fault: 1})
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "blue" and world.slots["blue"].revision == CAND
    assert world.head == PREV
    assert_never_resurrected(world)
    assert any("worker_replaced=attempted" in e for e in world.events)
    code, transaction = recover(world)
    if fault == "worker_wrong_app_revision":
        # A worker labelled with a revision that is neither previous nor
        # candidate is foreign evidence: the next attempt refuses, unmutated.
        assert code == tx.EXIT_ROLLBACK_INCOMPLETE
        assert any("worker carries a foreign revision" in e for e in world.events)
        assert world.route == "blue" and world.slots["blue"].revision == CAND
        return
    assert code == tx.EXIT_OK and transaction.already_switched
    assert_recovered(world)


# ── F. migration succeeds but the completion floor / fence fails ────────────

def test_f_fence_not_verified_after_release_prepare_never_takes_traffic():
    world = capable_candidate(green_dead_after_migration(prepare_installs_fence=False))
    world.source_revisions["candidate"] |= {"p1", "c1"}
    code, _ = fence_recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert "route_switch" not in names(world) and world.route == "green"
    assert any("fence is not verified" in e for e in world.events)
    assert world.slots["blue"].revision == CAND               # kept for fix-forward
    assert_never_resurrected(world)


def test_f_floor_activation_failure_after_recovery_is_committed_residue():
    world = capable_candidate(green_dead_after_migration(floor_raise=1))
    world.source_revisions["candidate"] |= {"p1", "c1"}
    code, _ = fence_recover(world)
    assert code == tx.EXIT_COMMITTED_RESIDUE
    assert world.floor["floor"] == "absent"                   # never lowered, retryable
    assert_recovered(world)
    assert any(e.startswith("capability-activation-incomplete") for e in world.events)


def test_f_raised_floor_admits_only_a_capable_candidate_in_recovery():
    world = green_dead_after_migration()
    world.floor.update(floor="raised", fence="verified", claims_present=True)
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert "release_prepare" not in names(world)
    assert any("below the completion capability floor" in e for e in world.events)


def test_f_capable_recovery_activates_after_the_dead_writer_is_retired():
    world = capable_candidate(green_dead_after_migration())
    capable_previous(world)
    world.source_revisions["candidate"] |= {"p1", "c1"}
    world.source_revisions["previous"] |= {"p1"}
    code, _ = fence_recover(world)
    assert code == tx.EXIT_OK and world.floor["floor"] == "raised"
    order = names(world)
    assert order.index("slot_remove") < max(
        i for i, c in enumerate(world.calls) if c[:2] == ("completion_floor", "raise"))
    assert_recovered(world)


# ── G. interrupted or uncertain attempts ────────────────────────────────────

def test_g_migration_interrupted_midway_is_completed_forward():
    world = kill_previous(r1_world())
    world.source_revisions["candidate"] = {"p1", "c0", "c1"}
    world.db_heads = frozenset({"c0"})                        # 5 of 12 applied
    code, _ = recover(world)
    assert code == tx.EXIT_OK
    assert_recovered(world)


@pytest.mark.parametrize("crash", ["slot_start:raise", "route_switch:raise",
                                   "worker_up:raise", "checkout_reset:raise"])
def test_g_crashed_recovery_attempt_converges_on_the_next_attempt(crash):
    world = green_dead_after_migration(**{crash: 1})
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert_never_resurrected(world)
    world.route_unknown = False
    code, _ = recover(world)
    assert code == tx.EXIT_OK
    assert_recovered(world)


def test_g_deadline_exhaustion_in_recovery_never_returns_to_the_previous_writer():
    world = green_dead_after_migration(**{"slot_start:cost": 2000})
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert_never_resurrected(world)
    assert world.head == PREV


@pytest.mark.parametrize("payload_fault", ["unreachable", "no-table", "malformed"])
def test_g_uncertain_schema_evidence_refuses_before_release_prepare(payload_fault, monkeypatch):
    world = green_dead_after_migration()
    if payload_fault == "unreachable":
        world.faults["schema_heads"] = True
    elif payload_fault == "no-table":
        world.db_heads = None
    else:
        monkeypatch.setattr(FakeOps, "schema_heads",
                            lambda self, i, r, timeout: {"result": "ok", "has_table": True,
                                                         "current": ["c1", "c1"]})
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert_no_host_mutation_past_build(world)


# ── H. stale, foreign or ambiguous evidence ─────────────────────────────────

def _foreign_routed(w):
    w.slots["green"].app_revision = OTHER


def _foreign_candidate_slot(w):
    w.slots["blue"] = Box(id="blue-x".ljust(64, "0"), image_id=IMG[OTHER],
                          revision=OTHER, app_revision=OTHER)


def _foreign_worker(w):
    w.worker.app_revision = OTHER


def _two_workers(w):
    original = FakeOps.worker

    def two(self, timeout):
        return original(self, timeout) * 2
    return two


def _legacy_route(w):
    w.route = "legacy"


def _unknown_db_revision(w):
    w.db_heads = frozenset({"zz9"})                           # wrong database / foreign graph


def _checkout_moved(w):
    w.head = OTHER


def _parity(w):
    w.faults["parity"] = True


@pytest.mark.parametrize("setup", [_foreign_routed, _foreign_candidate_slot,
                                   _foreign_worker, _legacy_route, _unknown_db_revision,
                                   _checkout_moved, _parity])
def test_h_foreign_or_stale_state_refuses_without_mutation(setup):
    world = green_dead_after_migration()
    if setup is _legacy_route:
        world.legacy = Box(id="legacy".ljust(64, "0"), image_id=IMG[PREV],
                           revision=PREV, app_revision=PREV, status="exited")
    setup(world)
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert_no_host_mutation_past_build(world)
    assert_never_resurrected(world)


def test_h_two_workers_are_ambiguous(monkeypatch):
    world = green_dead_after_migration()
    monkeypatch.setattr(FakeOps, "worker", _two_workers(world))
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert "build_image" not in names(world)


def test_h_cli_refuses_recovery_of_an_already_committed_candidate():
    args = list(GOOD_ARGS)
    args[args.index("--mode") + 1] = "forward-recovery"
    args[args.index("--previous-commit") + 1] = CAND
    assert tx.main(args, environ=contract.host_timeout_environment()) == tx.EXIT_USAGE


# ── I. the old writer is never resurrected ──────────────────────────────────

@pytest.mark.parametrize("fault", [
    {"switch_fail_to": "blue"}, {"public": 99}, {"ingress": 99}, {"worker_unhealthy": 1},
    {"redis_restart": 1}, {"checkout": 1}, {"boot": 1}, {"deep": 1},
    {"release_prepare": 1}, {"build": 1},
])
def test_i_no_failure_ever_routes_back_restarts_or_restores_the_previous_writer(fault):
    world = green_dead_after_migration(**fault)
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert_never_resurrected(world)
    assert world.slots["green"].status == "restarting"        # never touched
    assert "image_id" not in {c[0] for c in world.calls
                              if len(c) > 1 and "worker-rollback" in str(c[1])}


# ── J. Redis identity ───────────────────────────────────────────────────────

def test_j_redis_recreated_during_recovery_is_refused_and_reported():
    world = green_dead_after_migration(redis_restart=1)
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert any("redis was recreated" in e for e in world.events)
    assert world.route == "blue"                              # candidate keeps serving
    assert_never_resurrected(world)


def test_j_successful_recovery_never_touches_redis():
    world = green_dead_after_migration()
    assert recover(world)[0] == tx.EXIT_OK
    assert_redis_untouched(world)


# ── parser ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [
    None, {}, {"result": "error"}, {"result": "ok", "has_table": False, "current": ["c1"]},
    {"result": "ok", "has_table": True, "current": []},
    {"result": "ok", "has_table": True, "current": "c1"},
    {"result": "ok", "has_table": True, "current": ["c1", "c1"]},
    {"result": "ok", "has_table": True, "current": ["C1;DROP"]},
    {"result": "ok", "has_table": "yes", "current": ["c1"]},
])
def test_schema_heads_parser_fails_closed(payload):
    assert tx.parse_schema_heads(payload) is None


def test_schema_heads_parser_accepts_the_exact_contract():
    assert tx.parse_schema_heads(
        {"result": "ok", "has_table": True, "current": ["b1e4f5a6c7d8", "e7a1c3d5f9b2"]}
    ) == frozenset({"b1e4f5a6c7d8", "e7a1c3d5f9b2"})


def test_capability_constants_are_shared_with_the_capability_suite():
    assert CAPABLE == "workout_completion_claims_v1\n" and FENCE in tx.CAPABILITY_IMPLEMENTATION.values()


# ── HostOps argv: the shared one-off runner ─────────────────────────────────

from tests.test_r6_deploy_transaction import Recorder, host  # noqa: E402


def _one_off_reply(prefix, payload):
    import json
    return lambda argv: (0, f"noise\n{prefix}{json.dumps(payload)}\n", "")


def test_completion_floor_argv_and_override_are_unchanged_by_the_refactor(tmp_path):
    """Byte-pinned to the pre-R6-R1-28 completion_floor body."""
    recorder = Recorder({"completion-floor": _one_off_reply(
        "completion-floor: ", {"result": "ok"})})
    ops = host(tmp_path, recorder)
    image = "sha256:" + "a" * 64
    assert ops.completion_floor(image, CAND, "raise", 60) == {"result": "ok"}
    (argv,) = recorder.calls
    override = ops.work / "completion-floor-raise.yml"
    assert argv == ["docker", "compose", "--project-directory", str(ops.deploy_dir),
                    "-p", "fitness-coach", "-f", str(ops.context / "docker-compose.yml"),
                    "-f", str(override), "run", "--rm", "--no-deps", "-T", "--name",
                    f"axisai-completion-floor-raise-{CAND[:12]}", "completion-floor"]
    assert override.read_text(encoding="utf-8") == (
        "services:\n"
        "  completion-floor:\n"
        f"    image: '{image}'\n"
        "    pull_policy: never\n"
        "    env_file: [.env]\n"
        "    environment:\n"
        "      FITX_SKIP_DB_INIT: '1'\n"
        f"      APP_REVISION: '{CAND}'\n"
        '    entrypoint: ["flask", "--app", "starter", "completion-floor", "raise", '
        f'"--revision", "{CAND}"]\n'
        "    mem_limit: 640m\n"
        "    logging:\n"
        "      driver: json-file\n"
        "      options: {max-size: '10m', max-file: '3'}\n")


def test_schema_heads_runs_read_only_tool_in_the_exact_image(tmp_path):
    payload = {"result": "ok", "has_table": True, "current": ["c1"]}
    recorder = Recorder({"schema-heads": _one_off_reply("schema-heads: ", payload)})
    ops = host(tmp_path, recorder)
    image = "sha256:" + "b" * 64
    assert ops.schema_heads(image, CAND, 60) == payload
    (argv,) = recorder.calls
    assert argv == ["docker", "compose", "--project-directory", str(ops.deploy_dir),
                    "-p", "fitness-coach", "-f", str(ops.context / "docker-compose.yml"),
                    "-f", str(ops.work / "schema-heads.yml"), "run", "--rm", "--no-deps",
                    "-T", "--name", f"axisai-schema-heads-{CAND[:12]}", "schema-heads"]
    override = (ops.work / "schema-heads.yml").read_text(encoding="utf-8")
    assert f"    image: '{image}'\n" in override                # immutable ID, never a tag
    assert "      FITX_SKIP_DB_INIT: '1'\n" in override           # no startup DB work
    assert '["flask", "--app", "starter", "schema-heads"]' in override


@pytest.mark.parametrize("reply", [
    (0, "no machine line\n", ""),
    (0, "schema-heads: {\"a\": 1}\nschema-heads: {\"b\": 2}\n", ""),
    (0, "schema-heads: not-json\n", ""),
    (1, 'schema-heads: {"result": "error", "reason": "OperationalError"}\n', ""),
])
def test_schema_heads_unproven_output_is_an_error(tmp_path, reply):
    ops = host(tmp_path, Recorder({"schema-heads": reply}))
    with pytest.raises(tx.OpError):
        ops.schema_heads("sha256:" + "b" * 64, CAND, 60)


def test_schema_heads_refuses_a_tag_instead_of_an_image_id(tmp_path):
    recorder = Recorder()
    with pytest.raises(tx.OpError):
        host(tmp_path, recorder).schema_heads(f"axisai-web:{CAND}", CAND, 60)
    assert recorder.calls == []


# ── independent-review regressions (R6-R1-28 R2) ────────────────────────────

def test_review_proven_previous_web_is_routed_back_even_when_the_worker_is_unproven():
    """A verifying previous web is restorable on its own: a post-switch failure
    routes back to it (never leaving traffic on a broken candidate), while the
    unproven worker is reported and never restored."""
    world = migrated(r1_world(public=True))
    world.worker.status, world.worker.health = "restarting", "unhealthy"
    code, transaction = recover(world)
    assert transaction.previous_proven and transaction.previous_worker is None
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "green" and world.slots["green"].revision == PREV
    assert calls(world, "route_switch", "green")
    assert not calls(world, "worker_up", "previous")
    assert any("previous worker was unproven at the recovery baseline" in e
               for e in world.events)


def test_review_unhealthy_routed_candidate_is_never_removed_while_serving():
    world = green_dead_after_migration()
    world.slots["blue"] = Box(id="blue-flap".ljust(64, "0"), image_id=IMG[CAND],
                              revision=CAND, app_revision=CAND, health="unhealthy")
    world.images[f"axisai-web:{CAND}"] = IMG[CAND]
    world.route = "blue"
    code, transaction = recover(world)
    assert transaction.already_switched and code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert not calls(world, "slot_remove", "blue")
    assert world.slots["blue"].id.startswith("blue-flap") and world.route == "blue"
    assert any("a serving backend is never removed" in e for e in world.events)
    assert_never_resurrected(world)


def test_review_dead_routed_candidate_is_replaced_and_recovers():
    world = green_dead_after_migration()
    world.slots["blue"] = Box(id="blue-gone".ljust(64, "0"), image_id=IMG[CAND],
                              revision=CAND, app_revision=CAND, status="exited",
                              health=None)
    world.route = "blue"
    code, transaction = recover(world)
    assert code == tx.EXIT_OK and transaction.already_switched
    assert calls(world, "slot_remove", "blue", CAND)
    assert_recovered(world)


def test_review_healthy_candidate_from_a_different_image_build_is_replaced():
    world = green_dead_after_migration()
    world.slots["blue"] = Box(id="blue-stale".ljust(64, "0"), image_id="sha256:" + "e" * 64,
                              revision=CAND, app_revision=CAND)
    code, _ = recover(world)
    assert code == tx.EXIT_OK
    assert calls(world, "slot_remove", "blue", CAND)
    assert any("reason=different image build" in e for e in world.events)
    assert_recovered(world)


def test_review_schema_gate_shares_the_capability_admission_budget():
    """The gate adds no step budget: after a slow schema probe, admission gets
    only what is left of the SAME capability-admission step (a fresh 150 s
    would let this run proceed), and refuses before release-prepare."""
    step = contract.RELEASE_FORWARD_STEP_SECONDS["capability_admission"]
    world = green_dead_after_migration(**{"schema_heads:cost": step - 10,
                                          "image_capability_manifest:cost": 20})
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert "image_capability_manifest" in names(world)
    assert any("DeadlineExhausted" in e for e in world.events)
    assert_no_host_mutation_past_build(world)


def test_review_refusal_before_any_mutation_says_so():
    world = kill_previous(r1_world())                         # schema not migrated
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert any("FORWARD_RECOVERY_REFUSED" in e
               and "route, slots, worker and schema unchanged" in e
               for e in world.events)
    assert not any("FORWARD_RECOVERY_INCOMPLETE" in e for e in world.events)


def test_review_resumed_route_baseline_names_the_real_slots():
    world = green_dead_after_migration()
    world.slots["blue"] = Box(id="blue-live".ljust(64, "0"), image_id=IMG[CAND],
                              revision=CAND, app_revision=CAND)
    world.images[f"axisai-web:{CAND}"] = IMG[CAND]
    world.route = "blue"
    recover(world)
    baselines = [e for e in world.events if e.startswith("route-baseline")]
    assert baselines == ["route-baseline previous=green candidate=blue"]


def test_review_non_object_previous_proof_is_recorded_not_fatal(monkeypatch):
    world = migrated(r1_world())
    original = FakeOps.slot_verify

    def odd(self, slot, revision, timeout):
        if slot == "green":
            self._rec("slot_verify", slot, revision, timeout=timeout)
            return ["not", "a", "proof"]
        return original(self, slot, revision, timeout)

    monkeypatch.setattr(FakeOps, "slot_verify", odd)
    code, transaction = recover(world)
    assert code == tx.EXIT_OK and not transaction.previous_proven
    assert any(e.startswith("previous-web-unproven slot=green") for e in world.events)


# ── independent-review regressions, round 2 ─────────────────────────────────

def test_review2_previous_web_not_reported_healthy_is_never_probed():
    """A dying web that Docker does not report healthy is recorded unproven
    from the inspect view; its verify (which would poll for minutes) never
    runs inside the baseline step."""
    world = migrated(r1_world())
    world.slots["green"].health = "unhealthy"
    code, transaction = recover(world)
    assert code == tx.EXIT_OK and not transaction.previous_proven
    assert not calls(world, "slot_verify", "green")
    assert any(e.startswith("previous-web-unproven slot=green reason=running/unhealthy")
               for e in world.events)
    assert_recovered(world)


def test_review2_hung_previous_web_verify_cannot_starve_the_baseline(monkeypatch):
    """A verify that only returns when killed at its timeout (the real
    subprocess bound) consumes a bounded share of the baseline step; the
    worker/Redis proofs still run and recovery proceeds."""
    world = migrated(r1_world())
    original = FakeOps.slot_verify

    def hang(self, slot, revision, timeout):
        if slot == "green":
            self._rec("slot_verify", slot, revision, timeout=timeout)
            self.w.now += timeout
            raise tx.OpError("slot verify timed out")
        return original(self, slot, revision, timeout)

    monkeypatch.setattr(FakeOps, "slot_verify", hang)
    code, transaction = recover(world)
    assert code == tx.EXIT_OK and not transaction.previous_proven
    assert_recovered(world)


def test_review2_schema_probe_is_capped_like_every_one_off(monkeypatch):
    world = green_dead_after_migration()
    seen = []
    original = FakeOps.schema_heads

    def record(self, image_id, revision, timeout):
        seen.append(timeout)
        return original(self, image_id, revision, timeout)

    monkeypatch.setattr(FakeOps, "schema_heads", record)
    code, _ = recover(world)
    assert code == tx.EXIT_OK and seen and max(seen) <= tx.CAPABILITY_PROBE_SECONDS


def test_review2_previous_web_is_never_paired_with_the_candidate_worker():
    """Proven previous web, but the only worker is already the candidate's
    (an earlier attempt replaced it): a failure keeps the candidate pair
    serving instead of routing back to a mixed-revision web/worker pair."""
    world = migrated(r1_world(public=True))
    world.worker = Box(id="worker-cand".ljust(64, "0"), image_id=IMG[CAND],
                       revision=CAND, app_revision=CAND)
    code, transaction = recover(world)
    assert transaction.previous_proven and transaction.previous_worker is None
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert not calls(world, "route_switch", "green") and world.route == "blue"
    assert world.worker.revision == CAND
    assert any("never paired with it" in e for e in world.events)


# ── independent-review regressions, round 3 ─────────────────────────────────

def test_review3_candidate_stuck_starting_from_an_earlier_attempt_is_replaced():
    world = green_dead_after_migration()
    world.slots["blue"] = Box(id="blue-stuck".ljust(64, "0"), image_id=IMG[CAND],
                              revision=CAND, app_revision=CAND, health="starting")
    world.images[f"axisai-web:{CAND}"] = IMG[CAND]
    code, _ = recover(world)
    assert code == tx.EXIT_OK
    assert calls(world, "slot_remove", "blue", CAND)
    assert not world.slots["blue"].id.startswith("blue-stuck")
    assert_recovered(world)


def test_review3_refusal_before_the_build_does_not_claim_a_build():
    world = green_dead_after_migration()
    world.head = OTHER                                        # checkout drifted
    code, _ = recover(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE and "build_image" not in names(world)
    refused = [e for e in world.events if "FORWARD_RECOVERY_REFUSED" in e]
    assert refused and "image was built" not in refused[0]


def test_review3_refusal_after_the_build_says_the_image_was_built():
    world = kill_previous(r1_world())                         # schema not migrated
    recover(world)
    assert any("FORWARD_RECOVERY_REFUSED" in e and "the candidate image was built" in e
               for e in world.events)


# ── independent-review regressions, final round ─────────────────────────────

def test_review4_worker_without_an_image_id_is_recorded_unproven_not_a_crash():
    world = kill_previous(migrated(r1_world()), worker=False)
    world.worker.image_id = None
    code, transaction = recover(world)
    assert transaction.previous_worker is None
    assert code == tx.EXIT_OK
    assert_recovered(world)
