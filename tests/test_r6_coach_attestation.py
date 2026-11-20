"""Synthetic proofs and actual loaded-app cache negatives, without provider calls."""
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
