"""Synthetic proofs and actual loaded-app cache negatives, without provider calls."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts import r6_coach_acceptance as acceptance
from scripts import r6_coach_attestation as coach
from tests.test_r6_deploy_transaction import CAND, Recorder, host, tx

IMAGE = "sha256:" + "3" * 64
TREE = "4" * 40
HEADS = ["ad17de1e0001"]
STARTED = "2026-01-01T00:00:00.123456789Z"
BOUND = coach.binding(CAND, TREE, IMAGE, "blue", HEADS)
CHALLENGE = "a" * 32


def process(role):
    return dict(version=coach.VERSION, role=role, revision=CAND, heads=HEADS[:],
                boot=("b" if role == "web" else "c") * 64, pid=2 if role == "web" else 1,
                start_ticks=41, flag=False, guard=False)


def container(role):
    return [("1" if role == "web" else "2") * 64, IMAGE, STARTED, "running", "healthy", 0, False]


@pytest.mark.parametrize("field,value", [
    ("flag", True), ("flag", "0"), ("flag", 0), ("flag", None), ("guard", True),
    ("guard", 0), ("boot", "replayed"), ("pid", True), ("start_ticks", 0),
    ("revision", "2" * 40), ("version", "old"), ("role", "web"),
    ("heads", ["foreign"]), ("heads", []), ("unexpected", "must not be returned"),
])
def test_exact_web_worker_agreement_and_schema(field, value):
    assert coach.bind_actor(process("web"), container("web"), container("web"), BOUND, "web")
    invalid = {**process("worker"), field: value}
    with pytest.raises(ValueError):
        coach.bind_actor(invalid, container("worker"), container("worker"), BOUND, "worker")


@pytest.mark.parametrize("index,value", [
    (0, "3" * 64), (1, "sha256:" + "4" * 64), (2, "2026-01-01T00:01:00Z"),
    (3, "restarting"), (4, "unhealthy"), (5, 1), (6, True),
])
def test_identity_must_not_change_during_probe(index, value):
    after = container("web")
    after[index] = value
    with pytest.raises(ValueError, match="ACTOR_BINDING_INVALID"):
        coach.bind_actor(process("web"), container("web"), after, BOUND, "web")


def test_pair_acceptance_refuses_wrong_tree_route_and_recreated_process(tmp_path):
    ops = host(tmp_path, Recorder())
    ops._ok = lambda *args: TREE
    route = tx.Route("blue", coach.ROUTES["blue"])
    worker = tx.Container(container("worker")[0], IMAGE, "running", "healthy", STARTED, CAND)
    ops.route_status = lambda t: route
    ops._adult_cid = lambda *a: container("web")[0]
    ops.worker = lambda t: [worker]
    proofs = {role: process(role) for role in ("web", "worker")}
    ops._coach_actor = lambda cid, role, *a, approved: (
        approved, coach.bind_actor(proofs[role], container(role), container(role), approved, role))
    digest = ops.coach_release_proof(BOUND, 30)["digest"]
    assert acceptance.collect(ops, BOUND, digest, CHALLENGE)["observation"] == coach.boot_digest(CHALLENGE)
    with pytest.raises(tx.OpError, match="source tree"):
        ops.coach_release_proof({**BOUND, "tree": "a" * 40}, 30)
    route = tx.Route("green", coach.ROUTES["green"])
    with pytest.raises(tx.OpError, match="serving route"):
        ops.coach_release_proof(BOUND, 30)
    route = tx.Route("blue", coach.ROUTES["blue"])
    proofs["worker"]["start_ticks"] += 1
    with pytest.raises(ValueError, match="BOOTS_CHANGED"):
        acceptance.collect(ops, BOUND, digest, CHALLENGE)


def test_loaded_worker_cache_drift_and_guard_drift_are_refused(app, monkeypatch):
    from flask import Flask
    from app.jobs import tasks
    from app.services import r6_runtime_admission as admission
    from app.services.coach_plan_tools import executor

    app.extensions["r6_coach_containment"] = True
    app.config.update(BUILD_REVISION=CAND, R6_ADMISSION_BOOT="d" * 32)
    app.config[executor.FLAG_KEY] = False
    monkeypatch.setattr(tasks, "_worker_app", app)
    monkeypatch.setattr(admission, "process_identity", lambda: {"pid": 1, "start_ticks": 41})
    _, probe = admission.loaded_coach_proof(
        app, "worker", cached_worker_app=lambda: tasks._worker_app, worker_heads=HEADS)

    assert probe() == {**process("worker"), "boot": coach.boot_digest("d" * 32)}
    monkeypatch.setattr(tasks, "_worker_app", Flask("replacement"))
    with pytest.raises(ValueError, match="CACHE_CHANGED"):
        probe()
    monkeypatch.setattr(tasks, "_worker_app", app)
    with app.app_context():
        app.config[executor.FLAG_KEY] = True
        assert executor.plan_mutation_tools_enabled() is False
        with pytest.raises(ValueError, match="CONTAINMENT_REQUIRED"):
            probe()
        app.config[executor.FLAG_KEY] = False
    monkeypatch.setattr(executor, "plan_mutation_tools_enabled", lambda: True)
    with pytest.raises(ValueError, match="CONTAINMENT_REQUIRED"):
        probe()


def test_deploy_log_requires_one_ordered_attestation_before_success():
    events = [f"adult-registration-open revision={CAND}",
              f"release-committed revision={CAND} route=blue",
              f"coach-containment-proven digest={'f' * 64} version={coach.VERSION} "
              "web_flag=false web_guard=false worker_flag=false worker_guard=false",
              f"release-complete route=blue revision={CAND}"]

    def log(entries):
        return "\n".join(f"2026-01-01T00:00:00Z r6-deploy: {event}" for event in entries)

    assert acceptance.deployment_digest(log(events), CAND, "blue") == "f" * 64
    for invalid in (events[:2] + events[3:], events[:2] + events[2:3] * 2 + events[3:],
                    events[:2] + events[3:] + events[2:3],
                    [event.replace("worker_guard=false", "worker_guard=true") for event in events]):
        with pytest.raises(ValueError):
            acceptance.deployment_digest(log(invalid), CAND, "blue")


def test_response_rejects_stale_challenge_and_conflicting_fields():
    reply = {**process("web"), "challenge": CHALLENGE}
    payload = json.dumps(reply).encode("ascii") + b"\n"
    assert coach.decode_response(payload, CHALLENGE, "web", CAND) == process("web")
    with pytest.raises(ValueError, match="CHALLENGE_UNPROVEN"):
        coach.decode_response(payload, "b" * 32, "web", CAND)
    with pytest.raises(ValueError, match="DUPLICATE_FIELDS"):
        coach.decode_response(b'{"flag": true, ' + payload[1:], CHALLENGE, "web", CAND)


def test_pair_rechecks_gunicorn_worker_after_rq_observation(tmp_path):
    ops = host(tmp_path, Recorder())
    ops._ok = lambda *args: TREE
    ops.route_status = lambda t: tx.Route("blue", coach.ROUTES["blue"])
    ops._adult_cid = lambda *a: container("web")[0]
    ops.worker = lambda t: [tx.Container(container("worker")[0], IMAGE,
                                         "running", "healthy", STARTED, CAND)]
    web = process("web")

    def actor(cid, role, *args, approved):
        if role == "worker":
            web.update(pid=3, start_ticks=50, boot="a" * 64)
        proof = web if role == "web" else process("worker")
        return approved, coach.bind_actor(proof, container(role), container(role), approved, role)

    ops._coach_actor = actor
    with pytest.raises(tx.OpError, match="inventory changed"):
        ops.coach_release_proof(BOUND, 30)


def test_recreated_worker_cannot_disagree_with_the_recovery_actor(tmp_path):
    ops = host(tmp_path, Recorder())
    web = coach.bind_actor(process("web"), container("web"), container("web"), BOUND, "web")
    current = container("worker")
    worker = coach.bind_actor(process("worker"), current, current, BOUND, "worker")
    ops.coach_web = BOUND, web
    ops.coach_release_proof = lambda *args: {"web": web, "worker": worker}
    ops._adult_cid = lambda *args: web["container"]
    ops._adult_web_proof = lambda *args: {"closed": True}
    previous = tx.Container("a" * 64, IMAGE, "running", "healthy", STARTED, CAND)
    with pytest.raises(tx.OpError, match="verified worker update"):
        ops.adult_runtime_proof("blue", CAND, previous, 30)
    assert ops.coach_pair is None


def test_outer_checker_refuses_saved_observation_and_modified_evidence():
    web, worker = (coach.bind_actor(process(role), container(role), container(role), BOUND, role)
                   for role in ("web", "worker"))
    digest = coach.fingerprint(BOUND, web, worker)
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(seconds=1), now + timedelta(seconds=1)
    report = dict(binding=BOUND, web=web, worker=worker, digest=digest,
                  observation=coach.boot_digest(CHALLENGE), observed_at=now.isoformat())
    acceptance.verify_observation(report, BOUND, digest, CHALLENGE, start, end)
    with pytest.raises(ValueError, match="TIME_INVALID"):
        acceptance.verify_observation(report, BOUND, digest, CHALLENGE, end, end)
    with pytest.raises(ValueError, match="BINDING_INVALID"):
        acceptance.verify_observation(report, BOUND, digest, "b" * 32, start, end)
    with pytest.raises(ValueError, match="PROCESS_UNPROVEN"):
        acceptance.verify_observation({**report, "worker": {**worker, "flag": 0}},
                                      BOUND, digest, CHALLENGE, start, end)
    with pytest.raises(ValueError, match="DIGEST_INVALID"):
        acceptance.verify_observation({**report, "worker": {**worker, "pid": 2}},
                                      BOUND, digest, CHALLENGE, start, end)


_FLAG_ABSENT = object()


@pytest.mark.parametrize("value", [False, True, 0, 1, "", "0", None, _FLAG_ABSENT])
def test_ordinary_coach_guard_preserves_existing_config_semantics(app, value):
    from app.services.coach_plan_tools import executor

    if value is _FLAG_ABSENT:
        app.config.pop(executor.FLAG_KEY, None)
        expected = False
    else:
        app.config[executor.FLAG_KEY] = value
        expected = bool(value)
    assert "r6_coach_containment" not in app.extensions
    with app.app_context():
        assert executor.plan_mutation_tools_enabled() is expected


@pytest.mark.parametrize("role,env,value", [
    ("web", "R6_REGISTRATION_ADMISSION", "closed"),
    ("worker", "R6_RUNTIME_ADMISSION", "required"),
])
def test_protected_loaded_and_recreated_apps_always_deny_flag_drift(
        role, env, value, monkeypatch):
    from flask import Flask
    from app import schema_safety
    from app.services import r6_runtime_admission as admission
    from app.services.coach_plan_tools import executor

    monkeypatch.setenv(env, value)
    monkeypatch.setattr(schema_safety, "verify_schema_ready", lambda app: HEADS)
    monkeypatch.setattr(admission, "process_identity", lambda: {"pid": 7, "start_ticks": 42})
    cache = None

    def boot():
        app = Flask("protected")
        app.config.update(BUILD_REVISION=CAND, **{executor.FLAG_KEY: False})
        admission.install(app)
        return app

    cache = boot()
    arguments = {"cached_worker_app": lambda: cache, "worker_heads": HEADS} if role == "worker" else {}
    _, observe = admission.loaded_coach_proof(cache, role, **arguments)
    first = observe()
    for drifted in (True, 1, "1", 0, "0", None, _FLAG_ABSENT):
        if drifted is _FLAG_ABSENT:
            cache.config.pop(executor.FLAG_KEY)
        else:
            cache.config[executor.FLAG_KEY] = drifted
        with cache.app_context():
            assert executor.plan_mutation_tools_enabled() is False
        with pytest.raises(ValueError, match="CONTAINMENT_REQUIRED"):
            observe()
        with pytest.raises(RuntimeError, match="CONTAINMENT_REQUIRED"):
            admission.loaded_coach_proof(cache, role, **arguments)

    cache = boot()
    _, observe_recreated = admission.loaded_coach_proof(cache, role, **arguments)
    assert observe_recreated()["boot"] != first["boot"]
    with cache.app_context():
        assert executor.plan_mutation_tools_enabled() is False
