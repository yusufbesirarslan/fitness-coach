"""R6-03A hermetic proof of the exact-SHA blue/green deploy transaction.

The orchestration (``Transaction``) runs against ``World``: a deterministic
model of the production host -- nginx route state behind the root helper, the
legacy main-project web, the blue/green slots and their admission gate, the
worker, Redis, images with baked revisions, the database, the checkout, the
public edge (/health and the anonymous mobile envelope), old-backend sockets
and a monotonic clock. Every test asserts the exact final serving state, not
just an exit code.

``HostOps`` (the only code that touches the host) is pinned separately at the
argv level, and the migration overlap gate is exercised on real migration
files. Real runtime and nginx primitives are qualified separately by the existing
Docker slot and nginx integration suites; this module models orchestration.
"""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import deploy_contract as contract  # noqa: E402
import migration_expand_contract as mec  # noqa: E402
import r6_deploy_transaction as tx  # noqa: E402

PREV = "a" * 40
CAND = "c" * 40
OTHER = "e" * 40
URL = "https://fitx.example/health"
IMG = {rev: "sha256:" + hashlib.sha256(rev.encode()).hexdigest() for rev in (PREV, CAND, OTHER)}
BACKEND = tx.ROUTE_BACKENDS


# â”€â”€ world model â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@dataclass
class Box:
    id: str
    image_id: str
    revision: str
    app_revision: str
    status: str = "running"
    health: str = "healthy"
    started_at: str = "t0"
    deep_ok: bool = True


@dataclass
class World:
    route: str = "legacy"
    route_unknown: bool = False
    legacy: Box | None = None
    slots: dict = field(default_factory=dict)          # slot -> Box
    worker: Box | None = None
    redis: Box | None = None
    images: dict = field(default_factory=dict)         # tag -> image id
    baked: dict = field(default_factory=dict)          # image id -> revision
    head: str = PREV
    db_revision: str = "old-head"
    connections: dict = field(default_factory=dict)    # backend -> list of counts
    mem_ok: bool = True
    now: float = 1000.0
    calls: list = field(default_factory=list)
    faults: dict = field(default_factory=dict)
    counter: int = 0
    events: list = field(default_factory=list)

    def fault(self, name, *, consume=True):
        value = self.faults.get(name)
        if value and consume and isinstance(value, int) and value is not True:
            self.faults[name] = value - 1
        return bool(value)

    def new_id(self, prefix):
        self.counter += 1
        return f"{prefix}{self.counter:04d}".ljust(64, "0")

    def running_webs(self):
        count = 1 if self.legacy and self.legacy.status == "running" else 0
        return count + sum(1 for b in self.slots.values() if b.status == "running")

    def serving_box(self):
        if self.route_unknown:
            return None
        return self.legacy if self.route == "legacy" else self.slots.get(self.route)


def first_cutover_world(**faults):
    w = World(faults=dict(faults))
    w.legacy = Box(id="legacy".ljust(64, "0"), image_id=IMG[PREV], revision=PREV,
                   app_revision=PREV)
    w.worker = Box(id="worker".ljust(64, "0"), image_id=IMG[PREV], revision=PREV,
                   app_revision=PREV)
    w.redis = Box(id="redis".ljust(64, "0"), image_id="sha256:" + "9" * 64,
                  revision="", app_revision="", started_at="redis-start")
    w.images = {f"axisai-web:{PREV}": IMG[PREV]}
    w.baked = {IMG[PREV]: PREV, IMG[CAND]: CAND, IMG[OTHER]: OTHER}
    return w


def slot_world(active, **faults):
    w = first_cutover_world(**faults)
    w.legacy = None
    w.route = active
    w.slots[active] = Box(id=f"{active}".ljust(64, "0"), image_id=IMG[PREV],
                          revision=PREV, app_revision=PREV)
    return w


class Clock:
    def __init__(self, world):
        self.world = world

    def __call__(self):
        return self.world.now

    def sleep(self, seconds):
        assert seconds > 0
        self.world.now += seconds
        assert self.world.now < 1000 + 4000, "unbounded wait"


class FakeOps:
    """Contract-level model of HostOps. Each method mirrors the real one's
    success/failure semantics; faults are injected by name."""

    def __init__(self, world):
        self.w = world

    def _rec(self, name, *args, timeout):
        assert timeout > 0, f"{name} called with no time left"
        self.w.calls.append((name, *args))
        cost = self.w.faults.get(f"{name}:cost", 0)
        self.w.now += cost
        if self.w.fault(f"{name}:raise"):
            raise RuntimeError(f"unexpected {name} crash")

    def _fail(self, name):
        if self.w.fault(name):
            raise tx.OpError(f"injected {name} failure")

    def prepare_workspace(self, timeout):
        self._rec("prepare_workspace", timeout=timeout)

    def link_context_env(self, timeout):
        self._rec("link_context_env", timeout=timeout)

    def migration_delta(self, timeout):
        self._rec("migration_delta", timeout=timeout)
        delta = self.w.faults.get("delta", {"e3f4a5b6c7d8": "expand"})
        problems = [f"{rev}: contract" for rev, cls in delta.items()
                    if cls not in ("expand", "historical-expand")]
        return delta, problems

    def route_status(self, timeout):
        self._rec("route_status", timeout=timeout)
        drift = self.w.faults.get("drift_on_status")
        if drift and len([c for c in self.w.calls if c[0] == "route_status"]) == drift[0]:
            self.w.route = drift[1]
        if self.w.route_unknown or self.w.fault("route_status"):
            raise tx.OpError("route status unproven (exit 14, result=state-unknown)")
        return tx.Route(self.w.route, BACKEND[self.w.route])

    def route_switch(self, target, timeout):
        self._rec("route_switch", target, timeout=timeout)
        if target == self.w.faults.get("switch_fail_to"):
            mode = self.w.faults.get("switch_mode", "restored")
            if mode == "unknown":
                self.w.route_unknown = True
            raise tx.OpError(f"route switch to {target} failed ({mode})")
        self.w.route_unknown = False
        self.w.route = target
        return {"result": "switched", "to": target}

    def control_plane_mismatches(self, timeout):
        self._rec("control_plane_mismatches", timeout=timeout)
        return ["installed helper differs"] if self.w.fault("parity") else []

    def checkout_head(self, timeout):
        self._rec("checkout_head", timeout=timeout)
        return self.w.head

    def checkout_reset(self, revision, timeout):
        self._rec("checkout_reset", revision, timeout=timeout)
        if revision == CAND and self.w.fault("checkout"):
            raise tx.OpError("git reset failed")
        if revision == PREV and self.w.fault("checkout_restore"):
            raise tx.OpError("git reset failed")
        self.w.head = revision

    def _container(self, box, service):
        return tx.Container(id=box.id, image_id=box.image_id, status=box.status,
                            health=box.health, started_at=box.started_at,
                            app_revision=box.app_revision,
                            labels={"com.docker.compose.service": service})

    def main_web(self, timeout):
        self._rec("main_web", timeout=timeout)
        return [self._container(self.w.legacy, "web")] if self.w.legacy else []

    def worker(self, timeout):
        self._rec("worker", timeout=timeout)
        return [self._container(self.w.worker, "worker")] if self.w.worker else []

    def redis(self, timeout):
        self._rec("redis", timeout=timeout)
        return [self._container(self.w.redis, "redis")]

    def _box(self, container_id):
        for box in [self.w.legacy, self.w.worker, *self.w.slots.values()]:
            if box is not None and box.id == container_id:
                return box
        return None

    def container(self, container_id, timeout):
        self._rec("container", container_id, timeout=timeout)
        box = self._box(container_id)
        return self._container(box, "web") if box else None

    def container_build_revision(self, container_id, timeout):
        self._rec("container_build_revision", container_id, timeout=timeout)
        box = self._box(container_id)
        if box is None or box.status != "running":
            raise tx.OpError("container is not running")
        return box.revision

    def container_deep_health_revision(self, container_id, timeout):
        self._rec("container_deep_health_revision", container_id, timeout=timeout)
        box = self._box(container_id)
        if box is None or not box.deep_ok or self.w.fault("legacy_deep"):
            raise tx.OpError("deep health is not ok")
        return box.revision

    def slot_start(self, slot, revision, timeout):
        self._rec("slot_start", slot, revision, timeout=timeout)
        existing = self.w.slots.get(slot)
        if existing is not None:
            if existing.revision == revision and existing.status == "running":
                return {"action": "already-running"}
            raise tx.OpError("slot holds a different container (exit 3)")
        if not self.w.mem_ok or self.w.running_webs() >= 2:
            raise tx.OpError("capacity admission refused (exit 3)")
        image = self.w.images.get(f"axisai-web:{revision}")
        if image is None or self.w.baked[image] != revision:
            raise tx.OpError("image refused (exit 3)")
        box = Box(id=self.w.new_id(slot), image_id=image, revision=revision,
                  app_revision=revision)
        if self.w.fault("boot"):
            box.status = "exited"
        if self.w.fault("deep"):
            box.deep_ok = False
        self.w.slots[slot] = box
        return {"action": "started"}

    def slot_verify(self, slot, revision, timeout):
        self._rec("slot_verify", slot, revision, timeout=timeout)
        box = self.w.slots.get(slot)
        n = len([c for c in self.w.calls if c[:2] == ("slot_verify", slot)])
        if box is None or box.revision != revision or box.status != "running" \
                or not box.deep_ok:
            raise tx.OpError("verify failed (exit 4)")
        if self.w.faults.get("verify_fail_on") == (slot, n):
            raise tx.OpError("verify failed (exit 4)")
        return {"deep_health_revision": revision, "container": {"id": box.id}}

    def slot_inspect(self, slot, timeout):
        self._rec("slot_inspect", slot, timeout=timeout)
        box = self.w.slots.get(slot)
        return [] if box is None else [{"app_revision": box.app_revision,
                                        "status": box.status}]

    def slot_remove(self, slot, revision, timeout):
        self._rec("slot_remove", slot, revision, timeout=timeout)
        box = self.w.slots.get(slot)
        if box is None:
            return "absent"
        if box.revision != revision:
            return "foreign"
        if self.w.fault(f"remove:{slot}"):
            raise tx.OpError("slot remove failed (exit 5)")
        del self.w.slots[slot]
        return "removed"

    def build_image(self, revision, timeout):
        self._rec("build_image", revision, timeout=timeout)
        self._fail("build")
        image = IMG[OTHER] if self.w.fault("misbake") else IMG[revision]
        self.w.images[f"axisai-web:{revision}"] = image

    def image_id(self, reference, timeout):
        self._rec("image_id", reference, timeout=timeout)
        if reference not in self.w.images:
            raise tx.OpError(f"{reference} absent")
        return self.w.images[reference]

    def image_baked_revision(self, reference, timeout):
        self._rec("image_baked_revision", reference, timeout=timeout)
        return self.w.baked[self.w.images[reference]]

    def tag_image(self, image_id, tag, timeout):
        self._rec("tag_image", image_id, tag, timeout=timeout)
        self.w.images[tag] = IMG[OTHER] if self.w.fault("bad_rollback_tag") else image_id

    def release_prepare(self, revision, timeout):
        self._rec("release_prepare", revision, timeout=timeout)
        self._fail("release_prepare")
        self.w.db_revision = "new-head"

    def worker_up(self, kind, image_tag, revision, timeout):
        self._rec("worker_up", kind, image_tag, revision, timeout=timeout)
        if kind == "previous" and self.w.fault("worker_restore"):
            raise tx.OpError("compose up failed")
        image = self.w.images[image_tag]
        box = Box(id=self.w.new_id("worker"), image_id=image,
                  revision=self.w.baked[image], app_revision=revision)
        if kind == "candidate" and self.w.fault("worker_unhealthy"):
            box.health = "unhealthy"
        if kind == "candidate" and self.w.fault("worker_wrong_app_revision"):
            box.app_revision = OTHER
        if kind == "candidate" and self.w.fault("worker_wrong_baked"):
            box.revision = OTHER
        if kind == "candidate" and self.w.fault("redis_restart"):
            self.w.redis.started_at = "redis-restarted"
        self.w.worker = box

    def established_connections(self, backend, timeout):
        self._rec("established_connections", backend, timeout=timeout)
        if self.w.fault("ss"):
            raise tx.OpError("ss failed")
        series = self.w.connections.get(backend, [])
        return series.pop(0) if len(series) > 1 else (series[0] if series else 0)

    def stop_and_remove(self, container_id, timeout):
        self._rec("stop_and_remove", container_id, timeout=timeout)
        if self.w.fault("legacy_remove"):
            raise tx.OpError("docker stop failed")
        assert self.w.legacy is not None and self.w.legacy.id == container_id
        self.w.legacy = None

    def http_get(self, url, timeout):
        self._rec("http_get", url, timeout=timeout)
        box = self.w.serving_box()
        healthy = box is not None and box.status == "running"
        if url == URL:
            if self.w.route != "legacy" and self.w.fault("public"):
                return 502, b"bad gateway"
            return (200, b'{"status":"ok"}') if healthy else (502, b"")
        assert url == "https://fitx.example/api/v1/account/me"
        if self.w.route != "legacy" and self.w.fault("ingress"):
            return 500, b"{}"
        if self.w.route != "legacy" and self.w.fault("ingress_retryable"):
            body = {"error": {"code": "AUTH_SESSION_EXPIRED", "retryable": True}}
            return 401, json.dumps(body).encode()
        body = {"error": {"code": "AUTH_SESSION_EXPIRED", "message": "x",
                          "retryable": False, "request_id": "r"}}
        return (401, json.dumps(body).encode()) if healthy else (502, b"")

    def diagnostics(self, timeout):
        self._rec("diagnostics", timeout=timeout)
        return "NAMES\n"

    def housekeeping(self, keep, previous, timeout_for):
        assert timeout_for() > 0
        self.w.calls.append(("housekeeping", tuple(sorted(keep)), previous))
        if self.w.fault("housekeeping"):
            raise tx.OpError("prune failed")
        return []


def run(world, **kwargs):
    clock = Clock(world)
    budget = tx.Budget(clock, clock() + contract.HOST_PHASE_SECONDS["release_forward"])
    transaction = tx.Transaction(
        FakeOps(world), deploy_sha=CAND, previous_commit=PREV, public_health_url=URL,
        clock=clock, sleep=clock.sleep, log=world.events.append, budget=budget,
        **kwargs)
    code = transaction.run()
    return code, transaction


def names(world):
    return [call[0] for call in world.calls]


def index(world, *call):
    for position, recorded in enumerate(world.calls):
        if recorded[:len(call)] == call:
            return position
    raise AssertionError(f"{call} never called: {names(world)}")


def index_after(world, start, *call):
    for position in range(start + 1, len(world.calls)):
        if world.calls[position][:len(call)] == call:
            return position
    raise AssertionError(f"{call} never called after {start}: {names(world)}")


def assert_untouched_first_cutover(world):
    assert world.route == "legacy" and not world.route_unknown
    assert world.legacy is not None and world.legacy.status == "running"
    assert world.legacy.revision == PREV
    assert world.worker.id == "worker".ljust(64, "0")
    assert world.worker.image_id == IMG[PREV] and world.worker.revision == PREV
    assert world.redis.started_at == "redis-start"
    assert world.head == PREV


def assert_redis_untouched(world):
    assert world.redis.id == "redis".ljust(64, "0")
    assert world.redis.started_at == "redis-start"
    assert not any("redis" in str(arg) for call in world.calls
                   for arg in call[1:] if call[0] not in ("redis",))


# â”€â”€ success paths â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_first_cutover_legacy_to_blue_reaches_the_exact_steady_state():
    world = first_cutover_world()
    world.connections = {BACKEND["legacy"]: [2, 1, 0]}
    code, transaction = run(world)

    assert code == tx.EXIT_OK
    assert world.route == "blue"
    assert set(world.slots) == {"blue"} and world.slots["blue"].revision == CAND
    assert world.legacy is None                                    # retired
    assert world.worker.image_id == IMG[CAND] and world.worker.revision == CAND
    assert world.worker.app_revision == CAND and world.worker.health == "healthy"
    assert world.head == CAND
    assert world.db_revision == "new-head"
    assert_redis_untouched(world)
    assert [c for c in world.calls if c[0] == "release_prepare"] == [
        ("release_prepare", CAND)]                                   # exactly once
    order = [
        index(world, "route_status"),
        index(world, "control_plane_mismatches"),
        index(world, "migration_delta"),
        index(world, "build_image", CAND),
        index(world, "image_baked_revision", f"axisai-web:{CAND}"),
        index(world, "tag_image", IMG[PREV], f"axisai-worker-rollback:{PREV}"),
        index(world, "release_prepare", CAND),
        index(world, "slot_start", "blue", CAND),
        index(world, "route_switch", "blue"),
        index(world, "http_get", URL),
        index(world, "http_get", "https://fitx.example/api/v1/account/me"),
        index(world, "worker_up", "candidate"),
        index(world, "checkout_reset", CAND),
        index(world, "established_connections", BACKEND["legacy"]),
        index(world, "stop_and_remove"),
        index(world, "housekeeping"),
    ]
    assert order == sorted(order), names(world)
    # Immediate pre-switch re-verify and route re-read sit right before the switch.
    switch = index(world, "route_switch", "blue")
    assert world.calls[switch - 3][:2] == ("slot_verify", "blue")
    assert world.calls[switch - 2][0] == "container"
    assert world.calls[switch - 1][0] == "route_status"
    assert ("drain-complete", ) == tuple(
        e.split(" ")[0] for e in world.events if e.startswith("drain-"))
    assert world.calls.count(("established_connections", BACKEND["legacy"])) == 3


@pytest.mark.parametrize("active,candidate", [("blue", "green"), ("green", "blue")])
def test_normal_slot_release_switches_and_retires_the_previous_slot(active, candidate):
    world = slot_world(active)
    code, _ = run(world)

    assert code == tx.EXIT_OK
    assert world.route == candidate
    assert set(world.slots) == {candidate}
    assert world.slots[candidate].revision == CAND
    assert world.legacy is None
    assert ("slot_remove", active, PREV) in world.calls
    assert world.worker.revision == CAND and world.worker.image_id == IMG[CAND]
    assert world.head == CAND
    assert_redis_untouched(world)
    assert index(world, "established_connections", BACKEND[active]) < \
        index(world, "slot_remove", active, PREV)


def test_candidate_slot_is_deterministic_and_never_an_input():
    assert tx.CANDIDATE_SLOT == {"legacy": "blue", "blue": "green", "green": "blue"}
    source = (ROOT / "scripts" / "r6_deploy_transaction.py").read_text(encoding="utf-8")
    assert "--slot\"" not in source.split("def main(")[1]


def test_drain_deadline_forces_bounded_retirement_without_rollback():
    world = first_cutover_world()
    world.connections = {BACKEND["legacy"]: [4]}
    start = world.now
    code, transaction = run(world)

    assert code == tx.EXIT_OK
    assert world.route == "blue" and world.legacy is None
    deadline_events = [e for e in world.events if e.startswith("drain-deadline")]
    assert len(deadline_events) == 1 and "residual=4" in deadline_events[0]
    drain_calls = [i for i, c in enumerate(world.calls) if c[0] == "established_connections"]
    assert 2 < len(drain_calls) <= contract.OLD_BACKEND_DRAIN_MAX_SECONDS // 2 + 1
    assert world.now - start < contract.HOST_PHASE_SECONDS["release_forward"] + \
        contract.HOST_SUCCESS_TAIL_SECONDS


def test_unobservable_drain_waits_the_ceiling_then_retires():
    world = first_cutover_world(ss=10**6)
    code, _ = run(world)
    assert code == tx.EXIT_OK and world.legacy is None
    assert any("residual=unknown" in e for e in world.events)


def test_housekeeping_failure_never_rolls_a_committed_release_back():
    world = first_cutover_world(housekeeping=True)
    code, _ = run(world)
    assert code == tx.EXIT_OK
    assert world.route == "blue" and world.worker.revision == CAND and world.head == CAND


def test_existing_running_candidate_of_the_same_revision_is_reused():
    world = first_cutover_world()
    world.images[f"axisai-web:{CAND}"] = IMG[CAND]
    world.slots["blue"] = Box(id="blueold".ljust(64, "0"), image_id=IMG[CAND],
                              revision=CAND, app_revision=CAND)
    code, _ = run(world)
    assert code == tx.EXIT_OK and world.slots["blue"].id.startswith("blueold")


# â”€â”€ refusals before any mutation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

MUTATING = {"release_prepare", "slot_start", "route_switch", "worker_up",
            "checkout_reset", "stop_and_remove", "slot_remove"}


def assert_nothing_mutated(world):
    assert not MUTATING & set(names(world)), names(world)
    assert world.db_revision == "old-head"
    assert_untouched_first_cutover(world)
    assert world.slots == {}


@pytest.mark.parametrize("faults,setup", [
    ({"parity": True}, None),
    ({"delta": {"e3f4a5b6c7d8": "contract"}}, None),
    ({"delta": {"0001": "expand", "0002": "historical-unsafe"}}, None),
    ({"build": True}, None),
    ({"misbake": True}, None),
    ({"bad_rollback_tag": True}, None),
    ({}, lambda w: setattr(w, "route_unknown", True)),
    ({}, lambda w: setattr(w, "head", OTHER)),
    ({}, lambda w: setattr(w.legacy, "revision", OTHER)),
    ({"legacy_deep": True}, None),
    ({}, lambda w: setattr(w.worker, "revision", OTHER)),
    ({}, lambda w: setattr(w.worker, "app_revision", OTHER)),
    ({}, lambda w: setattr(w.worker, "health", "unhealthy")),
    ({}, lambda w: setattr(w.worker, "image_id", "fitness-coach-worker")),
], ids=["control-plane-parity", "contract-migration", "historical-unsafe-migration",
        "image-build-failure", "image-baked-revision-mismatch", "rollback-tag-drift",
        "unknown-route", "checkout-drift", "legacy-revision-drift",
        "legacy-deep-health", "worker-revision", "worker-app-revision",
        "worker-unhealthy", "worker-mutable-image"])
def test_refusals_before_any_mutation_leave_production_exactly_as_it_was(faults, setup):
    world = first_cutover_world(**faults)
    if setup:
        setup(world)
    snapshot = (world.legacy and world.legacy.revision, world.worker.revision,
                world.worker.app_revision)
    code, _ = run(world)

    assert code == tx.EXIT_ROLLED_BACK
    assert not MUTATING & set(names(world)), names(world)
    assert world.db_revision == "old-head"
    assert world.slots == {}
    assert world.worker.id == "worker".ljust(64, "0")
    assert (world.legacy and world.legacy.revision, world.worker.revision,
            world.worker.app_revision) == snapshot
    assert world.redis.started_at == "redis-start"
    assert any(e.startswith("rollback-verified") for e in world.events)


def test_contract_migration_is_refused_before_build_and_database():
    world = first_cutover_world(delta={"e3f4a5b6c7d8": "contract"})
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert "build_image" not in names(world) and "release_prepare" not in names(world)
    assert any("overlap gate refused before any database mutation" in e
               for e in world.events)


def test_route_state_must_carry_its_canonical_backend():
    world = first_cutover_world()
    ops = FakeOps(world)
    ops.route_status = lambda timeout: tx.Route("legacy", "127.0.0.1:5001")
    clock = Clock(world)
    transaction = tx.Transaction(ops, deploy_sha=CAND, previous_commit=PREV,
                                 public_health_url=URL, clock=clock, sleep=clock.sleep,
                                 log=world.events.append,
                                 budget=tx.Budget(clock, clock() + 1200))
    assert transaction.run() == tx.EXIT_ROLLED_BACK
    assert not MUTATING & set(names(world))


def test_slot_route_with_a_lingering_legacy_web_is_ambiguous():
    world = slot_world("blue")
    world.legacy = Box(id="legacy".ljust(64, "0"), image_id=IMG[PREV], revision=PREV,
                       app_revision=PREV)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert not MUTATING & set(names(world))


# â”€â”€ failures after release-prepare, before the switch â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def assert_pre_switch_rollback(world, code, switched=False):
    assert code == tx.EXIT_ROLLED_BACK
    assert ("route_switch" in names(world)) == switched
    assert "worker_up" not in names(world)
    assert_untouched_first_cutover(world)
    assert world.slots == {}


def test_release_prepare_failure_never_starts_the_candidate():
    world = first_cutover_world(release_prepare=True)
    code, _ = run(world)
    assert_pre_switch_rollback(world, code)
    assert "slot_start" not in names(world)
    assert [c for c in world.calls if c[0] == "release_prepare"] == [
        ("release_prepare", CAND)]                         # never retried


def test_capacity_rejection_leaves_active_service_untouched():
    world = first_cutover_world()
    world.mem_ok = False
    code, _ = run(world)
    assert_pre_switch_rollback(world, code)


def test_third_running_web_is_refused_by_admission():
    world = slot_world("blue")
    world.slots["green"] = Box(id="greenx".ljust(64, "0"), image_id=IMG[OTHER],
                               revision=OTHER, app_revision=OTHER)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == "blue" and world.slots["green"].revision == OTHER  # foreign kept
    assert world.slots["blue"].revision == PREV


@pytest.mark.parametrize("fault", ["boot", "deep"])
def test_candidate_boot_or_deep_health_failure_removes_only_the_candidate(fault):
    world = first_cutover_world(**{fault: True})
    code, _ = run(world)
    assert_pre_switch_rollback(world, code)
    assert ("slot_remove", "blue", CAND) in world.calls


def test_pre_switch_reverify_failure_rolls_back_without_switching():
    world = first_cutover_world(verify_fail_on=("blue", 2))
    code, _ = run(world)
    assert_pre_switch_rollback(world, code)


def test_pre_switch_route_drift_is_never_repaired_or_switched():
    world = first_cutover_world(drift_on_status=(2, "green"))
    code, _ = run(world)

    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert "route_switch" not in names(world)
    assert world.route == "green"                             # drift left as found
    assert world.slots["blue"].revision == CAND               # candidate kept
    assert world.legacy.status == "running" and world.worker.revision == PREV
    assert ("slot_remove", "blue", CAND) not in world.calls


# â”€â”€ switch and post-switch failures â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_route_switch_failure_with_restored_previous_route():
    world = first_cutover_world(switch_fail_to="blue")
    code, _ = run(world)
    assert_pre_switch_rollback(world, code, switched=True)


def test_route_switch_failure_left_unknown_is_converged_back_to_previous():
    world = first_cutover_world(switch_fail_to="blue", switch_mode="unknown")
    code, _ = run(world)
    assert_pre_switch_rollback(world, code, switched=True)
    assert world.calls.count(("route_switch", "legacy")) == 1


@pytest.mark.parametrize("fault", ["public", "ingress", "ingress_retryable"])
def test_post_switch_proof_failure_restores_previous_route_then_removes_candidate(fault):
    world = first_cutover_world(**{fault: True})
    code, _ = run(world)

    assert code == tx.EXIT_ROLLED_BACK
    assert_untouched_first_cutover(world)
    assert world.slots == {}
    assert "worker_up" not in names(world)
    restore = index(world, "route_switch", "legacy")
    assert restore < index(world, "slot_remove", "blue", CAND)
    assert restore < index_after(world, restore, "container_deep_health_revision",
                                 world.legacy.id) < index(world, "slot_remove", "blue", CAND)


def test_post_switch_route_readback_mismatch_rolls_back():
    world = first_cutover_world(drift_on_status=(3, "green"))
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert_untouched_first_cutover(world)
    assert world.slots == {}


def test_route_rollback_failure_keeps_both_backends_and_reports_manual_action():
    world = first_cutover_world(public=True, switch_fail_to="legacy")
    code, _ = run(world)

    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "blue"
    assert world.slots["blue"].revision == CAND                 # never removed
    assert world.legacy.status == "running" and world.worker.revision == PREV
    assert any("route is not proven legacy" in e for e in world.events)


def test_previous_web_failing_re_verification_keeps_the_candidate():
    world = first_cutover_world(public=True)
    original = FakeOps.container_deep_health_revision

    def flaky(self, container_id, timeout):
        if ("route_switch", "legacy") in self.w.calls:
            raise tx.OpError("deep health is not ok")
        return original(self, container_id, timeout)

    FakeOps.container_deep_health_revision = flaky
    try:
        code, _ = run(world)
    finally:
        FakeOps.container_deep_health_revision = original
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "legacy" and world.slots["blue"].revision == CAND


def test_normal_release_post_switch_failure_returns_to_the_previous_slot():
    world = slot_world("blue", ingress=True)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == "blue" and set(world.slots) == {"blue"}
    assert world.slots["blue"].revision == PREV
    assert ("slot_verify", "blue", PREV) in world.calls[index(world, "route_switch", "blue"):]
    assert world.worker.revision == PREV and world.head == PREV


# â”€â”€ worker and checkout failures â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_worker_failure_restores_route_web_and_exact_previous_worker_in_order():
    world = first_cutover_world(worker_unhealthy=True)
    code, _ = run(world)

    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == "legacy" and world.legacy.status == "running"
    assert world.worker.image_id == IMG[PREV]
    assert world.worker.revision == PREV and world.worker.app_revision == PREV
    assert world.worker.health == "healthy"
    assert world.slots == {} and world.head == PREV
    assert world.redis.started_at == "redis-start"
    restore = index(world, "route_switch", "legacy")
    order = [restore,
             index_after(world, restore, "container_deep_health_revision", world.legacy.id),
             index(world, "worker_up", "previous"),
             index(world, "slot_remove", "blue", CAND)]
    assert order == sorted(order)
    assert ("worker_up", "previous", f"axisai-worker-rollback:{PREV}", PREV) in world.calls


def test_worker_rollback_failure_is_reported_and_keeps_the_failed_candidate():
    world = first_cutover_world(worker_unhealthy=True, worker_restore=True)
    code, _ = run(world)

    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "legacy" and world.legacy.status == "running"
    assert world.slots["blue"].revision == CAND
    assert any("WORKER ROLLBACK NOT PROVEN" in e for e in world.events)


@pytest.mark.parametrize("fault", ["worker_wrong_app_revision", "worker_wrong_baked"])
def test_candidate_worker_with_a_wrong_revision_is_rolled_back(fault):
    world = first_cutover_world(**{fault: True})
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == "legacy" and world.slots == {}
    assert world.worker.image_id == IMG[PREV] and world.worker.revision == PREV
    assert world.worker.app_revision == PREV


def test_serving_revision_must_equal_the_checkout_head_even_when_consistent():
    world = first_cutover_world()
    for box in (world.legacy, world.worker):
        box.revision = box.app_revision = OTHER
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert not MUTATING & set(names(world))
    assert any("serving revision differs from the checkout HEAD" in e for e in world.events)


def test_route_rollback_failure_after_worker_failure_still_restores_the_worker():
    world = first_cutover_world(worker_unhealthy=True, switch_fail_to="legacy")
    code, _ = run(world)

    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "blue" and world.slots["blue"].revision == CAND
    assert world.worker.image_id == IMG[PREV] and world.worker.revision == PREV
    assert world.legacy.status == "running"


def test_redis_restart_during_worker_update_rolls_back():
    world = first_cutover_world(redis_restart=True)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == "legacy" and world.worker.revision == PREV
    assert world.slots == {}


def test_checkout_failure_rolls_everything_back_including_the_worker():
    world = first_cutover_world(checkout=True)
    code, _ = run(world)

    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == "legacy" and world.legacy.status == "running"
    assert world.worker.revision == PREV and world.worker.image_id == IMG[PREV]
    assert world.slots == {} and world.head == PREV
    assert ("checkout_reset", PREV) in world.calls


def test_checkout_restore_failure_reports_manual_action():
    world = first_cutover_world(checkout=True, checkout_restore=True)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "legacy" and world.worker.revision == PREV


# â”€â”€ retirement â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_old_backend_removal_failure_keeps_the_committed_release():
    world = first_cutover_world(legacy_remove=True)
    code, _ = run(world)

    assert code == tx.EXIT_COMMITTED_RESIDUE
    assert world.route == "blue" and world.slots["blue"].revision == CAND
    assert world.legacy is not None                          # residue, not rolled back
    assert world.worker.revision == CAND and world.head == CAND
    assert "route_switch" not in [c[0] for c in world.calls[index(world, "checkout_reset", CAND):]]


def test_previous_slot_removal_failure_keeps_the_committed_release():
    world = slot_world("green", **{"remove:green": True})
    code, _ = run(world)
    assert code == tx.EXIT_COMMITTED_RESIDUE
    assert world.route == "blue" and set(world.slots) == {"blue", "green"}
    assert world.worker.revision == CAND


def test_legacy_container_with_a_new_revision_is_never_retired():
    world = first_cutover_world()
    original = FakeOps.container_build_revision

    def swapped(self, container_id, timeout):
        if ("checkout_reset", CAND) in self.w.calls and container_id == self.w.legacy.id:
            return OTHER
        return original(self, container_id, timeout)

    FakeOps.container_build_revision = swapped
    try:
        code, _ = run(world)
    finally:
        FakeOps.container_build_revision = original
    assert code == tx.EXIT_COMMITTED_RESIDUE and world.legacy is not None


def test_old_backend_is_never_retired_before_the_commit_point():
    for faults in ({"worker_unhealthy": True}, {"checkout": True}, {"public": True}):
        world = first_cutover_world(**faults)
        run(world)
        assert "stop_and_remove" not in names(world), faults
        assert "established_connections" not in names(world), faults


# â”€â”€ budgets â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_forward_deadline_exhaustion_rolls_back_without_starting_anything():
    world = first_cutover_world(**{"build_image:cost": 1300})
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert "release_prepare" not in names(world)
    assert_nothing_mutated(world)


def test_unexpected_exception_still_enters_the_verified_rollback():
    world = first_cutover_world(**{"worker_up:raise": 1})
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == "legacy" and world.slots == {}


def test_every_step_runs_inside_the_contract_budget():
    world = first_cutover_world()
    world.connections = {BACKEND["legacy"]: [5]}
    start = world.now
    run(world)
    assert world.now - start <= (contract.HOST_PHASE_SECONDS["release_forward"]
                                 + contract.HOST_SUCCESS_TAIL_SECONDS)


def test_timing_contract_algebra():
    phases = contract.HOST_PHASE_SECONDS
    pre = sum(phases[name] for name in contract.HOST_PRE_TRANSACTION_PHASES)
    tail = max(phases["diagnostics"] + phases["release_rollback"], phases["retirement"])
    assert contract.HOST_WORST_CASE_SECONDS == (
        pre + phases["git_preparation"] + phases["release_forward"] + tail
        + phases["cleanup"]) == 1980
    assert contract.HOST_WORST_CASE_SECONDS < contract.SSM_EXECUTION_TIMEOUT_SECONDS
    assert contract.SSM_EXECUTION_MARGIN_SECONDS == 220
    assert contract.POLL_HORIZON_SECONDS - (
        60 + contract.SSM_EXECUTION_TIMEOUT_SECONDS) == 240
    assert contract.CONTROLLER_REQUIRED_SECONDS < contract.CONTROLLER_STEP_MINUTES * 60
    assert sum(contract.RELEASE_ROLLBACK_STEP_SECONDS.values()) <= phases["release_rollback"]
    assert sum(contract.RETIREMENT_STEP_SECONDS.values()) <= phases["retirement"]
    assert contract.RETIREMENT_STEP_SECONDS["drain"] > contract.OLD_BACKEND_DRAIN_MAX_SECONDS
    # The drain ceiling is derived, not a guess: coach turn + one provider call.
    from app import config as app_config
    assert contract.OLD_BACKEND_DRAIN_MAX_SECONDS >= (
        app_config.AI_COACH_TURN_TIMEOUT_SECONDS + app_config.BEDROCK_CALL_TIMEOUT_SECONDS)
    assert contract.OLD_BACKEND_DRAIN_MAX_SECONDS > 30


@pytest.mark.parametrize("old,new", [
    ("SSM_EXECUTION_TIMEOUT_SECONDS: int = 2200", "SSM_EXECUTION_TIMEOUT_SECONDS: int = 2100"),
    ("POLL_HORIZON_SECONDS: int = 2500", "POLL_HORIZON_SECONDS: int = 2400"),
    ("CONTROLLER_STEP_MINUTES: int = 50", "CONTROLLER_STEP_MINUTES: int = 48"),
    ('"release_rollback": 500,', '"release_rollback": 400,'),
    ('"retirement": 400,', '"retirement": 300,'),
    ('"image_build": 600,', '"image_build": 1300,'),
])
def test_contract_refuses_invalid_timing_algebra(old, new):
    source = (ROOT / "scripts" / "deploy_contract.py").read_text(encoding="utf-8")
    assert source.count(old) == 1
    with pytest.raises(RuntimeError):
        exec(compile(source.replace(old, new), "deploy_contract_mutant", "exec"),
             {"__name__": "deploy_contract_mutant"})


# â”€â”€ migration overlap gate on real files â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _versions(tmp_path, name, files):
    directory = tmp_path / name
    directory.mkdir()
    for file in files:
        shutil.copy(file, directory / Path(file).name)
    return directory


def _real_versions():
    return sorted((ROOT / "migrations" / "versions").glob("*.py"))


def test_repository_delta_from_the_serving_revision_is_overlap_safe(tmp_path):
    """Production serves 9e21be5, which predates three migrations: two pinned
    historical ones and #421's. All are classified by the generic rules."""
    later = {"e1f2a3b4c5d6", "e2f3a4b5c6d7", "e3f4a5b6c7d8"}
    previous = _versions(tmp_path, "previous", [
        f for f in _real_versions() if f.name.split("_", 1)[0] not in later])
    candidate = _versions(tmp_path, "candidate", _real_versions())
    delta, problems = tx.classify_migration_delta(previous, candidate, mec)
    assert problems == []
    assert delta == {"e1f2a3b4c5d6": "historical-expand",
                     "e2f3a4b5c6d7": "historical-expand",
                     "e3f4a5b6c7d8": "expand"}


def test_no_delta_is_trivially_safe(tmp_path):
    previous = _versions(tmp_path, "previous", _real_versions())
    candidate = _versions(tmp_path, "candidate", _real_versions())
    assert tx.classify_migration_delta(previous, candidate, mec) == ({}, [])


def _forward(revision, down, body, declared='expand_contract = "expand"\n'):
    return (f'revision = "{revision}"\ndown_revision = "{down}"\n{declared}'
            "from alembic import op\nimport sqlalchemy as sa\n\n"
            f"def upgrade():\n{body}\n\ndef downgrade():\n    op.drop_table('t')\n")


def _head(directory):
    revisions = {}
    for path in directory.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        revisions[tx._module_literal(tree, "revision")] = tx._module_literal(
            tree, "down_revision")
    downs = set(revisions.values())
    return next(r for r in revisions if r not in downs)


@pytest.mark.parametrize("declared,body,expected", [
    ('expand_contract = "expand"\n', "    op.create_table('t', sa.Column('id', sa.Integer))",
     "expand"),
    ('expand_contract = "contract"\nexpand_contract_reason = "drops the legacy column after a full release"\n',
     "    op.drop_column('u', 'legacy')", "contract"),
    ("", "    op.create_table('t', sa.Column('id', sa.Integer))", "unclassified"),
])
def test_generic_classification_of_a_new_migration(tmp_path, declared, body, expected):
    previous = _versions(tmp_path, "previous", _real_versions())
    candidate = _versions(tmp_path, "candidate", _real_versions())
    (candidate / "ffff00000001_new.py").write_text(
        _forward("ffff00000001", _head(previous), body, declared), encoding="utf-8")
    delta, problems = tx.classify_migration_delta(previous, candidate, mec)
    assert delta == {"ffff00000001": expected}
    assert bool(problems) == (expected != "expand")


def test_historical_migration_in_the_delta_must_pass_the_expand_rules(tmp_path, monkeypatch):
    previous = _versions(tmp_path, "previous", [
        f for f in _real_versions() if not f.name.startswith("e2f3a4b5c6d7")])
    candidate = _versions(tmp_path, "candidate", _real_versions())
    target = next(candidate.glob("e2f3a4b5c6d7_*.py"))
    unsafe = target.read_text(encoding="utf-8").replace(
        "def upgrade():\n", "def upgrade():\n    op.drop_column('user', 'email')\n", 1)
    target.write_text(unsafe, encoding="utf-8")
    monkeypatch.setitem(mec.HISTORICAL_BASELINE, "e2f3a4b5c6d7",
                        mec.normalized_digest(unsafe.encode("utf-8")))
    delta, problems = tx.classify_migration_delta(previous, candidate, mec)
    assert delta["e2f3a4b5c6d7"] == "historical-unsafe"
    assert any("drop_column" in p for p in problems)


def test_removed_or_rewritten_shipped_migration_is_refused(tmp_path):
    previous = _versions(tmp_path, "previous", _real_versions())
    candidate = _versions(tmp_path, "candidate", _real_versions())
    victim = next(candidate.glob("e3f4a5b6c7d8_*.py"))
    victim.write_text(victim.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    _, problems = tx.classify_migration_delta(previous, candidate, mec)
    assert any("modified after it shipped" in p for p in problems)
    victim.unlink()
    _, problems = tx.classify_migration_delta(previous, candidate, mec)
    assert any("removed by the candidate" in p for p in problems)


# â”€â”€ HostOps: exact argv for every host-side effect â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class Recorder:
    def __init__(self, replies=None):
        self.calls = []
        self.replies = replies or {}

    def __call__(self, argv, timeout, env=None):
        assert timeout > 0
        self.calls.append(list(argv))
        for key, reply in self.replies.items():
            if key in " ".join(argv):
                return reply(argv) if callable(reply) else reply
        return 0, "", ""


def host(tmp_path, recorder, **kwargs):
    deploy = tmp_path / "fitness-coach"
    context = deploy / ".axisai-build-context.x"
    work = deploy / ".axisai-r6-work.x"
    for directory in (context / "scripts", context / "deploy" / "nginx", work):
        directory.mkdir(parents=True, exist_ok=True)
    (deploy / ".env").write_text("SECRET=1\n", encoding="utf-8")
    return tx.HostOps(deploy_dir=deploy, context_dir=context, work_dir=work,
                      main_project="fitness-coach", deploy_sha=CAND,
                      previous_commit=PREV, run=recorder, python="/usr/bin/python3",
                      environ={"PATH": "/usr/bin"}, **kwargs)


def test_route_helper_is_the_only_route_authority(tmp_path):
    recorder = Recorder({"status": (0, "op=status state=blue backend=127.0.0.1:5001 result=ok\n", ""),
                         "switch green": (0, "op=switch from=blue to=green backend=127.0.0.1:5002 result=switched changed=yes\n", "")})
    ops = host(tmp_path, recorder)
    assert ops.route_status(30) == tx.Route("blue", "127.0.0.1:5001")
    ops.route_switch("green", 30)
    assert recorder.calls == [
        ["sudo", "-n", "/usr/local/sbin/axisai-switch-web-slot", "status"],
        ["sudo", "-n", "/usr/local/sbin/axisai-switch-web-slot", "switch", "green"]]
    source = (ROOT / "scripts" / "r6_deploy_transaction.py").read_text(encoding="utf-8")
    for forbidden in ("nginx -s", "systemctl", "active-web-upstream", "proxy_pass",
                      "sites-available"):
        assert forbidden not in source


@pytest.mark.parametrize("reply", [
    (14, "op=status result=state-unknown\n", "pending"),
    (0, "op=status state=blue backend=127.0.0.1:5001 result=ok\nextra\n", ""),
    (0, "", ""),
])
def test_unproven_route_status_fails_closed(tmp_path, reply):
    ops = host(tmp_path, Recorder({"status": reply}))
    with pytest.raises(tx.OpError):
        ops.route_status(30)


@pytest.mark.parametrize("reply", [
    (11, "op=switch from=legacy to=blue result=candidate-nginx-invalid restored=yes\n", ""),
    (0, "op=switch from=blue to=blue result=already-active changed=no\n", ""),
])
def test_route_switch_requires_a_real_switch(tmp_path, reply):
    ops = host(tmp_path, Recorder({"switch": reply}))
    with pytest.raises(tx.OpError):
        ops.route_switch("blue", 30)


def test_worker_update_touches_only_the_worker_with_the_exact_image(tmp_path):
    recorder = Recorder()
    ops = host(tmp_path, recorder)
    ops.worker_up("candidate", f"axisai-web:{CAND}", CAND, 60)
    (argv,) = recorder.calls
    assert argv[:9] == ["docker", "compose", "--project-directory", str(ops.deploy_dir),
                        "-p", "fitness-coach", "-f",
                        str(ops.context / "docker-compose.yml"), "-f"]
    assert argv[10:] == ["up", "-d", "--no-deps", "--no-build", "--pull", "never",
                         "worker"]
    override = Path(argv[9]).read_text(encoding="utf-8")
    assert f"image: 'axisai-web:{CAND}'" in override
    assert "pull_policy: never" in override and f"APP_REVISION: '{CAND}'" in override
    import yaml
    assert set(yaml.safe_load(override)["services"]) == {"worker"}
    for forbidden in ("--remove-orphans", "down", "redis", "web", "--force-recreate"):
        assert forbidden not in argv


def test_worker_restore_uses_the_previous_compose_and_pinned_image(tmp_path):
    recorder = Recorder()
    ops = host(tmp_path, recorder)
    ops.worker_up("previous", f"axisai-worker-rollback:{PREV}", PREV, 60)
    (argv,) = recorder.calls
    assert argv[7] == str(ops.work / "previous-compose.yml")
    assert "--no-deps" in argv and argv[-1] == "worker"


def test_release_prepare_is_a_one_off_skip_db_init_command(tmp_path):
    recorder = Recorder()
    ops = host(tmp_path, recorder)
    ops.release_prepare(CAND, 120)
    (argv,) = recorder.calls
    assert argv[-7:] == ["run", "--rm", "--no-deps", "-T", "--name",
                         f"axisai-release-prepare-{CAND[:12]}", "release-prepare"]
    override = (ops.work / "release-prepare.yml").read_text(encoding="utf-8")
    assert f"image: 'axisai-web:{CAND}'" in override
    assert "FITX_SKIP_DB_INIT: '1'" in override
    assert "['flask', '--app', 'starter', 'release-prepare']" in override
    assert "ports" not in override and "FITX_STARTUP_MODE" not in override


def test_release_prepare_timeout_removes_the_one_off_container(tmp_path):
    recorder = Recorder({"run --rm": (124, "", "timed out")})
    ops = host(tmp_path, recorder)
    with pytest.raises(tx.OpError):
        ops.release_prepare(CAND, 120)
    assert recorder.calls[-1] == ["docker", "rm", "-f",
                                  f"axisai-release-prepare-{CAND[:12]}"]


def test_slot_operations_go_through_the_slot_runtime_only(tmp_path):
    recorder = Recorder({
        " start ": (0, json.dumps({"action": "started"}), ""),
        " verify ": (0, json.dumps({"deep_health_revision": CAND}), ""),
        " remove ": (0, json.dumps({"action": "removed"}), "")})
    ops = host(tmp_path, recorder)
    ops.slot_start("blue", CAND, 60)
    ops.slot_verify("blue", CAND, 60)
    assert ops.slot_remove("blue", CAND, 60) == "removed"
    prefix = ["/usr/bin/python3", "-I", str(ops.context / "scripts/web_slot_runtime.py"),
              "--deploy-dir", str(ops.context), "--main-project", "fitness-coach"]
    assert recorder.calls == [
        prefix + ["start", "--slot", "blue", "--revision", CAND],
        prefix + ["verify", "--slot", "blue", "--revision", CAND],
        prefix + ["remove", "--slot", "blue", "--expected-revision", CAND]]


@pytest.mark.parametrize("rc,payload", [
    (3, {"error": "Refused", "detail": "capacity admission refused: insufficient_mem_available"}),
    (3, {"error": "Refused", "detail": "too_many_running_web_containers"}),
    (5, {"error": "DockerFailed"}),
    (0, {"action": "preflight-only"}),
])
def test_slot_start_refusal_including_capacity_is_a_failure(tmp_path, rc, payload):
    ops = host(tmp_path, Recorder({" start ": (rc, json.dumps(payload), "")}))
    with pytest.raises(tx.OpError):
        ops.slot_start("blue", CAND, 60)


def test_slot_verify_requires_the_exact_deep_health_revision(tmp_path):
    ops = host(tmp_path, Recorder({" verify ": (0, json.dumps(
        {"deep_health_revision": OTHER}), "")}))
    with pytest.raises(tx.OpError):
        ops.slot_verify("blue", CAND, 60)


def test_foreign_slot_content_is_never_removed(tmp_path):
    ops = host(tmp_path, Recorder({" remove ": (3, json.dumps({"error": "Refused"}), "")}))
    assert ops.slot_remove("blue", CAND, 60) == "foreign"


def test_image_proof_runs_without_network_and_the_build_is_from_the_context(tmp_path):
    recorder = Recorder({"image inspect": (0, "sha256:" + "1" * 64 + "\n", "")})
    ops = host(tmp_path, recorder)
    ops.build_image(CAND, 600)
    assert ops.image_id(f"axisai-web:{CAND}", 30) == "sha256:" + "1" * 64
    ops.image_baked_revision(f"axisai-web:{CAND}", 30)
    assert recorder.calls[0] == ["docker", "build", "--build-arg", f"BUILD_REVISION={CAND}",
                                 "-t", f"axisai-web:{CAND}", str(ops.context)]
    assert recorder.calls[2] == ["docker", "run", "--rm", "--pull", "never", "--network",
                                 "none", "--entrypoint", "cat", f"axisai-web:{CAND}",
                                 "/app/BUILD_REVISION"]


def test_mutable_image_reference_is_not_an_identity(tmp_path):
    ops = host(tmp_path, Recorder({"image inspect": (0, "fitness-coach-worker\n", "")}))
    with pytest.raises(tx.OpError):
        ops.image_id("axisai-worker-rollback:x", 30)


def test_drain_observation_is_a_read_only_socket_query(tmp_path):
    recorder = Recorder({"ss ": (0, "ESTAB 0 0 127.0.0.1:41000 127.0.0.1:5000\n"
                                    "ESTAB 0 0 127.0.0.1:41002 127.0.0.1:5000\n", "")})
    ops = host(tmp_path, recorder)
    assert ops.established_connections("127.0.0.1:5000", 10) == 2
    assert recorder.calls == [["ss", "-H", "-t", "-n", "state", "established", "dst",
                               "127.0.0.1:5000"]]


def test_legacy_retirement_stops_only_that_container_with_the_slot_grace(tmp_path):
    recorder = Recorder()
    ops = host(tmp_path, recorder)
    ops.stop_and_remove("abc123", 100)
    assert recorder.calls == [["docker", "stop", "--time", "45", "abc123"],
                              ["docker", "rm", "abc123"]]


def test_housekeeping_never_prunes_all_images_and_keeps_rollback_material(tmp_path):
    listing = "\n".join([f"axisai-web:{CAND}", f"axisai-web:{PREV}", f"axisai-web:{OTHER}",
                         f"axisai-worker-rollback:{PREV}", f"axisai-worker-rollback:{OTHER}",
                         "redis:8.8.0-alpine", "fitness-coach-web:latest"])
    recorder = Recorder({"image ls": (0, listing, "")})
    ops = host(tmp_path, recorder)
    assert ops.housekeeping({CAND, PREV}, PREV, lambda: 30) == []
    removed = [c[-1] for c in recorder.calls if c[:3] == ["docker", "image", "rm"]]
    assert removed == [f"axisai-web:{OTHER}", f"axisai-worker-rollback:{OTHER}"]
    flat = [" ".join(c) for c in recorder.calls]
    assert "docker image prune -f" in flat
    assert not any(" -a" in c.split("prune", 1)[-1] for c in flat if "prune" in c)
    assert any(c.startswith("docker builder prune --force --keep-storage 4294967296")
               for c in flat)


def test_housekeeping_stops_when_its_budget_is_gone(tmp_path):
    def timeout_for():
        return 0
    ops = host(tmp_path, Recorder())
    ops._run = tx._bounded_run
    notes = ops.housekeeping({CAND}, PREV, timeout_for)
    assert notes and all("did not complete" in n for n in notes)


def test_control_plane_parity_compares_installed_bytes(tmp_path):
    ops = host(tmp_path, Recorder())
    installed = tmp_path / "installed"
    installed.mkdir()
    helper, mapping = installed / "helper", installed / "web-slots.conf"
    helper.write_bytes((ROOT / "scripts/axisai_switch_web_slot.py").read_bytes())
    mapping.write_bytes((ROOT / "deploy/nginx/web-slots.conf").read_bytes())
    for rel in ("scripts/axisai_switch_web_slot.py", "deploy/nginx/web-slots.conf"):
        shutil.copy(ROOT / rel, ops.context / rel)
    for installed_file in (helper, mapping):
        installed_file.chmod(0o444)
    uid = helper.stat().st_uid
    ops.control_plane_files = ((str(helper), "scripts/axisai_switch_web_slot.py"),
                               (str(mapping), "deploy/nginx/web-slots.conf"))
    ops.trusted_uid = uid
    assert ops.control_plane_mismatches(10) == []
    (ops.context / "scripts/axisai_switch_web_slot.py").write_bytes(b"# changed\n")
    assert ops.control_plane_mismatches(10) == [
        f"{helper} differs from the candidate's scripts/axisai_switch_web_slot.py"]
    ops.trusted_uid = uid + 1
    assert any("not a root-owned" in p for p in ops.control_plane_mismatches(10))


def test_production_identity_constants_match_the_control_plane_and_slots():
    mapping = (ROOT / "deploy/nginx/web-slots.conf").read_text(encoding="utf-8")
    for state, backend in tx.ROUTE_BACKENDS.items():
        assert f"{state} {backend}\n" in mapping
    import web_slot_runtime as slots
    assert {s: f"127.0.0.1:{p}" for s, p in slots.SLOT_PORTS.items()} == {
        s: tx.ROUTE_BACKENDS[s] for s in ("blue", "green")}
    import axisai_switch_web_slot as sw
    assert tx.ROUTE_STATES == sw.STATES
    assert tx.ROUTE_HELPER == "/usr/local/sbin/axisai-switch-web-slot"
    assert tx.ROUTE_MAPPING == sw.MAPPING_PATH
    assert tx.CONTROL_PLANE_FILES == (
        ("/usr/local/sbin/axisai-switch-web-slot", "scripts/axisai_switch_web_slot.py"),
        ("/etc/axisai/web-slots.conf", "deploy/nginx/web-slots.conf"))


def test_ingress_envelope_must_be_the_exact_anonymous_contract():
    ok = json.dumps({"error": {"code": "AUTH_SESSION_EXPIRED", "retryable": False}}).encode()
    assert tx.Transaction._ingress_ok(401, ok)
    for status, body in ((200, ok), (500, ok), (401, b"not json"),
                         (401, json.dumps({"error": {"code": "AUTH_SESSION_EXPIRED",
                                                     "retryable": True}}).encode()),
                         (401, json.dumps({"error": {"code": "OTHER",
                                                     "retryable": False}}).encode())):
        assert not tx.Transaction._ingress_ok(status, body)


def test_anonymous_probe_carries_no_credentials(monkeypatch):
    seen = {}

    class Response:
        status = 401

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"{}"

    def urlopen(request, timeout):
        seen["headers"] = {k.lower() for k in request.headers}
        return Response()

    monkeypatch.setattr(tx.urllib.request, "urlopen", urlopen)
    tx._http_get("https://fitx.example/api/v1/account/me", 5)
    assert seen["headers"] <= {"user-agent", "accept"}


# â”€â”€ command line â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

GOOD_ARGS = ["run", "--deploy-sha", CAND, "--previous-commit", PREV,
             "--deploy-dir", "/home/ubuntu/fitness-coach",
             "--context", "/home/ubuntu/fitness-coach/.axisai-build-context.x",
             "--work-dir", "/home/ubuntu/fitness-coach/.axisai-r6-work.x",
             "--public-health-url", URL, "--transaction-epoch", "1000"]


@pytest.mark.parametrize("change", [
    {"--deploy-sha": "C" * 40}, {"--previous-commit": "abc"},
    {"--context": "/tmp/elsewhere"}, {"--work-dir": "/home/ubuntu/fitness-coach/.axisai-build-context.x"},
    {"--public-health-url": "http://fitx.example/health"},
    {"--public-health-url": "https://user:pw@fitx.example/health"},
    {"--transaction-epoch": "-5"}, {"--deploy-dir": "/home/ubuntu/axisai-web-blue"},
])
def test_cli_rejects_invalid_invocations_before_anything(change):
    args = list(GOOD_ARGS)
    for flag, value in change.items():
        args[args.index(flag) + 1] = value
    if change.get("--deploy-dir"):
        args[args.index("--context") + 1] = "/home/ubuntu/axisai-web-blue/.c"
        args[args.index("--work-dir") + 1] = "/home/ubuntu/axisai-web-blue/.w"
    assert tx.main(args, environ=contract.host_timeout_environment()) == tx.EXIT_USAGE


def test_cli_rejects_a_non_canonical_timing_contract():
    environ = {**contract.host_timeout_environment(), "HOST_RELEASE_FORWARD_SECONDS": "9999"}
    assert tx.main(GOOD_ARGS, environ=environ) == tx.EXIT_CONTRACT

def test_worker_rollback_tag_drift_keeps_candidate_and_reports_incomplete(monkeypatch):
    world = first_cutover_world(worker_unhealthy=True)
    original = FakeOps.worker_up
    def drift(self, kind, image_tag, revision, timeout):
        original(self, kind, image_tag, revision, timeout)
        if kind == "candidate":
            self.w.images[f"axisai-worker-rollback:{PREV}"] = IMG[OTHER]
    monkeypatch.setattr(FakeOps, "worker_up", drift)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "legacy" and world.slots["blue"].revision == CAND
    assert not any(c[:2] == ("worker_up", "previous") for c in world.calls)
    assert any("WORKER ROLLBACK NOT PROVEN" in event for event in world.events)

def test_route_drift_during_drain_never_retires_the_now_serving_previous_backend(monkeypatch):
    world = first_cutover_world()
    original = FakeOps.established_connections
    def drift(self, backend, timeout):
        observed = original(self, backend, timeout)
        self.w.route = "legacy"
        return observed
    monkeypatch.setattr(FakeOps, "established_connections", drift)
    code, _ = run(world)
    assert code == tx.EXIT_COMMITTED_RESIDUE
    assert world.route == "legacy" and world.legacy.status == "running"
    assert world.slots["blue"].revision == CAND
    assert "stop_and_remove" not in names(world)
    assert "slot_remove" not in names(world)

def test_previous_backend_dying_before_switch_cannot_report_verified_rollback(monkeypatch):
    world = first_cutover_world()
    def failed_start(self, slot, revision, timeout):
        self.w.legacy.status = "exited"
        raise tx.OpError("candidate admission failed")
    monkeypatch.setattr(FakeOps, "slot_start", failed_start)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "legacy" and world.legacy.status == "exited"
    assert not any(event.startswith("rollback-verified") for event in world.events)

@pytest.mark.parametrize("public_failures,ingress_failures", [(2, 0), (0, 2), (2, 2)])
def test_post_switch_probes_retry_transient_failures_then_commit(public_failures, ingress_failures):
    world = first_cutover_world(public=public_failures, ingress=ingress_failures)
    code, _ = run(world)
    assert code == tx.EXIT_OK
    assert world.route == "blue" and world.worker.revision == CAND
    assert world.calls.count(("http_get", URL)) == public_failures + 1
    assert world.calls.count(("http_get", "https://fitx.example/api/v1/account/me")) == ingress_failures + 1

@pytest.mark.parametrize("attribute", ["revision", "app_revision"])
def test_previous_worker_missing_revision_fails_closed(attribute):
    world = first_cutover_world()
    setattr(world.worker, attribute, "")
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert not MUTATING & set(names(world))
    assert world.route == "legacy" and world.legacy.status == "running"

def test_previous_serving_backend_missing_baked_revision_fails_closed():
    world = first_cutover_world()
    world.legacy.revision = ""
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert not MUTATING & set(names(world))
    assert world.route == "legacy" and world.legacy.status == "running"

def test_existing_same_revision_candidate_must_use_the_built_image():
    world = first_cutover_world()
    world.slots["blue"] = Box(id="leftover".ljust(64, "0"), image_id=IMG[OTHER],
                              revision=CAND, app_revision=CAND)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLED_BACK
    assert world.route == "legacy" and world.legacy.status == "running"
    assert "worker_up" not in names(world) and "route_switch" not in names(world)

def test_rollback_route_drift_during_worker_restore_keeps_the_active_candidate(monkeypatch):
    world = first_cutover_world(worker_unhealthy=True)
    original = FakeOps.worker_up
    def drift(self, kind, image_tag, revision, timeout):
        original(self, kind, image_tag, revision, timeout)
        if kind == "previous":
            self.w.route = "blue"
    monkeypatch.setattr(FakeOps, "worker_up", drift)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert world.route == "blue" and world.slots["blue"].status == "running"
    assert ("slot_remove", "blue", CAND) not in world.calls

def test_failed_release_prepare_cleanup_cannot_report_verified_rollback(monkeypatch):
    world = first_cutover_world()
    def prepare(self, revision, timeout):
        error = getattr(tx, "PreparationCleanupUnproven", tx.OpError)
        raise error("release-prepare cleanup unproven")
    monkeypatch.setattr(FakeOps, "release_prepare", prepare)
    code, _ = run(world)
    assert code == tx.EXIT_ROLLBACK_INCOMPLETE
    assert "slot_start" not in names(world)
    assert not any(event.startswith("rollback-verified") for event in world.events)

def test_host_release_prepare_reports_failed_container_cleanup(tmp_path):
    recorder = Recorder({"run --rm": (124, "", "timed out"),
                         "docker rm -f": (42, "", "daemon unavailable")})
    ops = host(tmp_path, recorder)
    with pytest.raises(tx.OpError, match="cleanup"):
        ops.release_prepare(CAND, 120)

def test_compound_host_operation_decreases_each_command_timeout(tmp_path):
    readings = {"now": 1000.0}
    grants = []
    def record(argv, timeout, env=None):
        grants.append(timeout)
        readings["now"] += 7
        return 0, "", ""
    ops = host(tmp_path, record)
    ops.clock = lambda: readings["now"]
    ops.stop_and_remove("abc123", 10)
    assert len(grants) == 2
    assert grants[1] <= 3

@pytest.mark.parametrize("prefix", ["", "Error: ", "Error response from daemon: "])
def test_release_prepare_auto_removed_container_is_proven_absent(tmp_path, prefix):
    recorder = Recorder({
        "run --rm": (1, "", "migration failed"),
        "docker rm -f": (1, "", "No such container"),
        "docker container inspect": (1, "", f"{prefix}No such container: axisai-release-prepare-{CAND[:12]}")})
    ops = host(tmp_path, recorder)
    with pytest.raises(tx.OpError, match="compose run") as caught:
        ops.release_prepare(CAND, 120)
    assert not isinstance(caught.value, tx.PreparationCleanupUnproven)
    assert any(call[:3] == ["docker", "container", "inspect"] for call in recorder.calls)

def test_missing_docker_socket_cannot_prove_migrator_absence(tmp_path):
    recorder = Recorder({
        "run --rm": (124, "", "timed out"),
        "docker rm -f": (1, "", "daemon unavailable"),
        "docker container inspect": (1, "", "dial unix /var/run/docker.sock: connect: no such file or directory")})
    ops = host(tmp_path, recorder)
    with pytest.raises(tx.PreparationCleanupUnproven, match="cleanup"):
        ops.release_prepare(CAND, 120)
