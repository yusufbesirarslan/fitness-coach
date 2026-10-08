#!/usr/bin/env python3
"""R6-01B: same-host two-slot web runtime primitive (no traffic switching).

A web SLOT is a separate Compose project (axisai-web-blue / axisai-web-green)
that owns exactly one service, ``web``, published on a fixed loopback port and
joined to the main project's network as EXTERNAL so it reaches the main
project's ``redis`` without owning it. This module is the only supported way to
drive a slot. It validates every input, fails closed on anything ambiguous and
never touches the main project's web, worker or Redis, nginx, or AWS.

Commands (all print one JSON object on stdout):

    render     validate slot/revision/network and the rendered Compose model
    admission  read-only host-capacity gate (MemAvailable + running web count)
    preflight  everything start would check, without starting anything
    start      preflight, then `compose up` the candidate (never recreates)
    verify     container healthy + baked revision + in-container deep health
               revision + host loopback port answers + port belongs to it
    inspect    current slot state
    remove     stop + remove ONE slot whose revision matches --expected-revision

Choosing which slot is the candidate (the one NOT receiving traffic) is the
caller's job: traffic authority is R6-02 and the transaction is R6-03. This
module never reads or changes nginx.

Exit codes: 0 ok, 2 invalid input, 3 refused (fail closed, nothing started or
removed), 4 verification failed, 5 docker command failed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

# ── Slot identity contract ──────────────────────────────────────────────────
SLOT_PORTS = MappingProxyType({"blue": 5001, "green": 5002})
SLOT_PROJECT_PREFIX = "axisai-web-"
SERVICE = "web"
INTERNAL_PORT = 5000
LOOPBACK = "127.0.0.1"
IMAGE_REPO = "axisai-web"
BASE_FILE = "docker-compose.web-slot.yml"
OVERLAY_TEMPLATE = "deploy/compose/web-slot-{slot}.yml"
CANDIDATE_STARTUP_MODE = "read-only"
# Production deploy dir is /home/ubuntu/fitness-coach and docker-compose.yml has
# no `name:`, so the main project (and its network prefix) is the basename.
DEFAULT_MAIN_PROJECT = "fitness-coach"
_REVISION_RE = re.compile(r"[0-9a-f]{40}")
_PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")

# ── Capacity contract (t3.small: 1905 MiB usable, no swap) ──────────────────
# Evidence (R6-00, 2026-10-08): web 201 MiB RSS post-boot, worker 122 MiB,
# redis 15 MiB; host used 7-day mean 33.25 % (633 MiB), 7-day max 40.9 %
# (779 MiB, includes deploy builds); lowest MemAvailable observed 1088 MiB.
#
# SLOT_MEM_LIMIT_MIB: cgroup ceiling per slot (mirrors docker-compose.web-slot.yml
#   mem_limit). >= 3x the observed 201 MiB working set and ~1.8x the largest
#   web footprint the 7-day host peak allows (<= ~360 MiB), so normal AI and
#   image work fits; startup transients happen inside this same cgroup.
# RUNTIME_OVERHEAD_MIB: per-container memory OUTSIDE the cgroup
#   (containerd-shim + docker-proxy for the published port).
# HOST_RESERVE_MIB: memory that must remain available AFTER the candidate is
#   at its full ceiling: the observed 7-day host excursion (779 - 633 = 146 MiB:
#   the active web, worker and host daemons growing) + kernel watermarks/slab
#   (~64 MiB) + rounding.
#
#   required_available = SLOT_MEM_LIMIT + RUNTIME_OVERHEAD + HOST_RESERVE
#                      = 640 + 32 + 256 = 928 MiB  (allow iff MemAvailable >= it)
#
# At the worst observed MemAvailable (1088 MiB) this admits with 160 MiB spare;
# a host that has drifted below 928 MiB is refused before anything starts.
SLOT_MEM_LIMIT_MIB = 640
RUNTIME_OVERHEAD_MIB = 32
DEFAULT_HOST_RESERVE_MIB = 256
MIN_HOST_RESERVE_MIB = 192
MAX_HOST_RESERVE_MIB = 768
# Active + candidate. A third concurrent web (e.g. legacy + blue + green) is
# outside both the memory model and the /health limiter budget.
MAX_RUNNING_WEB_CONTAINERS = 2

# ── Stop contract ───────────────────────────────────────────────────────────
GUNICORN_GRACEFUL_TIMEOUT_SECONDS = 30   # gunicorn.conf.py graceful_timeout
SHUTDOWN_FLUSH_BOUND_SECONDS = 5         # runtime_metrics: connect 2 s + read 3 s
STOP_GRACE_SECONDS = 45                  # = 30 + 5 + 10 s margin

# ── Verification budget (bounded; see /health limiter budget in DEPLOYMENT.md)
HEALTH_WAIT_POLLS = 36          # docker inspect only, never an HTTP request
HEALTH_POLL_SECONDS = 5
DEEP_PROBE_ATTEMPTS = 6         # in-container /health?deep=1 (may probe Bedrock)
HOST_PROBE_ATTEMPTS = 6         # host loopback shallow /health
PROBE_DELAY_SECONDS = 5
DOCKER_TIMEOUT_SECONDS = 60
COMPOSE_UP_TIMEOUT_SECONDS = 180
COMPOSE_DOWN_TIMEOUT_SECONDS = STOP_GRACE_SECONDS + 45

EXIT_INVALID, EXIT_REFUSED, EXIT_VERIFY, EXIT_DOCKER = 2, 3, 4, 5

_DEEP_PROBE_CODE = (
    "import json,urllib.request\n"
    "r=urllib.request.urlopen('http://127.0.0.1:5000/health?deep=1',timeout=5)\n"
    "p=json.load(r)\n"
    "print(json.dumps({'http':r.status,'status':p.get('status'),"
    "'revision':p.get('revision')}))\n"
)


class SlotError(Exception):
    exit_code = EXIT_REFUSED


class InvalidInput(SlotError):
    exit_code = EXIT_INVALID


class Refused(SlotError):
    exit_code = EXIT_REFUSED


class VerificationFailed(SlotError):
    exit_code = EXIT_VERIFY


class DockerFailed(SlotError):
    exit_code = EXIT_DOCKER


# ── Pure validation ─────────────────────────────────────────────────────────

def validate_slot(slot):
    if not isinstance(slot, str) or slot not in SLOT_PORTS:
        raise InvalidInput(f"slot must be one of {sorted(SLOT_PORTS)}")
    return slot


def validate_revision(revision):
    if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
        raise InvalidInput("revision must be lowercase 40-hex")
    return revision


def validate_main_project(name):
    if (not isinstance(name, str) or not _PROJECT_RE.fullmatch(name)
            or name.startswith(SLOT_PROJECT_PREFIX)):
        raise InvalidInput("main project name is invalid")
    return name


def slot_project(slot):
    return SLOT_PROJECT_PREFIX + validate_slot(slot)


def slot_port(slot):
    return SLOT_PORTS[validate_slot(slot)]


def slot_image(revision):
    return f"{IMAGE_REPO}:{validate_revision(revision)}"


def shared_network(main_project):
    return f"{validate_main_project(main_project)}_default"


def parse_host_reserve_mib(raw):
    """None -> default. Anything else must be a plain integer inside bounds;
    empty, zero, negative or garbage never disables the reserve."""
    if raw is None:
        return DEFAULT_HOST_RESERVE_MIB
    text = str(raw).strip()
    if not re.fullmatch(r"[0-9]{1,6}", text):
        raise InvalidInput("host reserve must be a positive integer MiB")
    value = int(text)
    if not MIN_HOST_RESERVE_MIB <= value <= MAX_HOST_RESERVE_MIB:
        raise InvalidInput(
            f"host reserve {value} MiB outside "
            f"[{MIN_HOST_RESERVE_MIB}, {MAX_HOST_RESERVE_MIB}]")
    return value


def parse_mem_available_kib(meminfo_text):
    """Exactly one well-formed `MemAvailable: <n> kB` line, else refuse."""
    matches = [line for line in str(meminfo_text).splitlines()
               if line.startswith("MemAvailable:")]
    if len(matches) != 1:
        raise Refused("MemAvailable is missing or ambiguous in /proc/meminfo")
    found = re.fullmatch(r"MemAvailable:\s+([0-9]{1,12}) kB\s*", matches[0])
    if not found:
        raise Refused("MemAvailable is malformed in /proc/meminfo")
    return int(found.group(1))


def required_available_kib(host_reserve_mib):
    return (SLOT_MEM_LIMIT_MIB + RUNTIME_OVERHEAD_MIB + host_reserve_mib) * 1024


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str
    mem_available_kib: int
    required_kib: int
    running_web_containers: int
    host_reserve_mib: int


def decide_admission(mem_available_kib, running_web_containers, host_reserve_mib):
    """Pure gate. `>=` makes the exact boundary deterministic (admit)."""
    if (not isinstance(running_web_containers, int)
            or isinstance(running_web_containers, bool)
            or running_web_containers < 0):
        raise Refused("running web container count is invalid")
    required = required_available_kib(host_reserve_mib)
    if running_web_containers >= MAX_RUNNING_WEB_CONTAINERS:
        return Admission(False, "too_many_running_web_containers", mem_available_kib,
                         required, running_web_containers, host_reserve_mib)
    if mem_available_kib < required:
        return Admission(False, "insufficient_mem_available", mem_available_kib,
                         required, running_web_containers, host_reserve_mib)
    return Admission(True, "ok", mem_available_kib, required,
                     running_web_containers, host_reserve_mib)


def _bytes(value):
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def validate_rendered(doc, slot, revision, network):
    """The rendered model must be exactly one loopback-only web slot."""
    problems = []
    project = slot_project(slot)
    if doc.get("name") != project:
        problems.append("project name")
    services = doc.get("services") or {}
    if set(services) != {SERVICE}:
        problems.append("services must be exactly {web}")
    web = services.get(SERVICE) or {}
    if web.get("image") != slot_image(revision):
        problems.append("image")
    if web.get("pull_policy") != "never":
        problems.append("pull_policy")
    if "build" in web or "container_name" in web or "depends_on" in web:
        problems.append("build/container_name/depends_on present")
    if web.get("volumes"):
        problems.append("volumes present")
    if _bytes(web.get("mem_limit")) != SLOT_MEM_LIMIT_MIB * 1024 * 1024:
        problems.append("mem_limit")
    if web.get("stop_grace_period") != f"{STOP_GRACE_SECONDS}s":
        problems.append("stop_grace_period")
    env = web.get("environment") or {}
    if env.get("FITX_STARTUP_MODE") != CANDIDATE_STARTUP_MODE:
        problems.append("FITX_STARTUP_MODE")
    if env.get("APP_REVISION") != revision:
        problems.append("APP_REVISION")
    if "FITX_SKIP_DB_INIT" in env:
        problems.append("FITX_SKIP_DB_INIT present")
    ports = web.get("ports") or []
    expected_port = {"host_ip": LOOPBACK, "target": INTERNAL_PORT,
                     "published": str(slot_port(slot)), "protocol": "tcp"}
    if len(ports) != 1 or {k: (str(v) if k == "published" else v)
                           for k, v in ports[0].items() if k in expected_port} \
            != expected_port:
        problems.append("ports")
    logging_cfg = web.get("logging") or {}
    labels = (logging_cfg.get("options") or {}).get("labels", "")
    if logging_cfg.get("driver") != "json-file" or \
            "com.docker.compose.service" not in labels.split(","):
        problems.append("logging")
    networks = doc.get("networks") or {}
    if set(web.get("networks") or {}) != {"shared"} or set(networks) != {"shared"}:
        problems.append("networks")
    shared = networks.get("shared") or {}
    if shared.get("name") != network or shared.get("external") is not True:
        problems.append("shared network must be external and named exactly")
    if problems:
        raise Refused("rendered slot model violates contract: " + ", ".join(problems))
    return True


# ── Host side effects (all injectable) ──────────────────────────────────────

def _run_subprocess(args, timeout, env=None):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        raise DockerFailed(f"{args[0]} {args[1]} timed out") from exc
    except OSError as exc:
        raise DockerFailed(f"{args[0]} unavailable: {type(exc).__name__}") from exc


def _read_meminfo():
    with open("/proc/meminfo", encoding="ascii") as handle:
        return handle.read()


def _port_bindable(port):
    """True iff nothing holds 127.0.0.1:<port> (bind test, closed at once)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((LOOPBACK, port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _http_get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.read(65536)


class SlotRuntime:
    def __init__(self, deploy_dir, main_project=DEFAULT_MAIN_PROJECT, *,
                 run=_run_subprocess, read_meminfo=_read_meminfo,
                 port_bindable=_port_bindable, http_get=_http_get,
                 sleep=time.sleep, environ=None):
        self.deploy_dir = Path(deploy_dir)
        if not self.deploy_dir.is_absolute():
            raise InvalidInput("deploy dir must be absolute")
        self.main_project = validate_main_project(main_project)
        self.network = shared_network(self.main_project)
        self._run = run
        self._read_meminfo = read_meminfo
        self._port_bindable = port_bindable
        self._http_get = http_get
        self._sleep = sleep
        self._environ = dict(os.environ if environ is None else environ)

    # docker plumbing
    def _docker(self, *args, timeout=DOCKER_TIMEOUT_SECONDS, env=None, ok=True):
        result = self._run(["docker", *args], timeout, env)
        if ok and result.returncode != 0:
            raise DockerFailed(
                f"docker {args[0]} failed: {(result.stderr or '').strip()[:300]}")
        return result

    def _compose_env(self, revision):
        env = {k: v for k, v in self._environ.items()
               if not k.startswith("COMPOSE_")}
        env["AXISAI_WEB_SLOT_REVISION"] = validate_revision(revision)
        env["AXISAI_SHARED_NETWORK"] = self.network
        return env

    def _compose(self, slot, revision, *args, timeout=DOCKER_TIMEOUT_SECONDS):
        files = []
        for rel in (BASE_FILE, OVERLAY_TEMPLATE.format(slot=validate_slot(slot))):
            path = self.deploy_dir / rel
            if not path.is_file():
                raise Refused(f"slot definition missing: {rel}")
            files += ["-f", str(path)]
        return self._docker(
            "compose", "--project-directory", str(self.deploy_dir),
            "-p", slot_project(slot), *files, *args,
            timeout=timeout, env=self._compose_env(revision))

    # observations
    def render(self, slot, revision):
        result = self._compose(slot, revision, "config", "--format", "json",
                               "--no-env-resolution")
        try:
            doc = json.loads(result.stdout)
        except ValueError as exc:
            raise Refused("compose config did not return JSON") from exc
        validate_rendered(doc, slot, revision, self.network)
        return doc

    def check_shared_network(self):
        result = self._docker("network", "inspect", self.network,
                              "--format", "{{json .Labels}}", ok=False)
        if result.returncode != 0:
            raise Refused(f"shared network {self.network} does not exist; "
                          "refusing to create a replacement")
        try:
            labels = json.loads(result.stdout or "null") or {}
        except ValueError as exc:
            raise Refused("shared network labels unreadable") from exc
        if (labels.get("com.docker.compose.project") != self.main_project
                or labels.get("com.docker.compose.network") != "default"):
            raise Refused(f"shared network {self.network} is not the main "
                          f"project's default network")
        redis = self._docker(
            "ps", "--filter", f"label=com.docker.compose.project={self.main_project}",
            "--filter", "label=com.docker.compose.service=redis",
            "--filter", "status=running", "--filter", "health=healthy",
            "--filter", f"network={self.network}", "--format", "{{.ID}}")
        if len(redis.stdout.split()) != 1:
            raise Refused("main project redis is not exactly one healthy "
                          "container on the shared network")
        return True

    def running_web_containers(self):
        result = self._docker("ps", "--filter",
                              f"label=com.docker.compose.service={SERVICE}",
                              "--filter", "status=running", "--format", "{{.ID}}")
        return len(result.stdout.split())

    def slot_containers(self, slot):
        listed = self._docker("ps", "-a", "--filter",
                              f"label=com.docker.compose.project={slot_project(slot)}",
                              "--format", "{{.ID}}")
        ids = listed.stdout.split()
        if not ids:
            return []
        inspected = self._docker("inspect", *ids)
        try:
            return json.loads(inspected.stdout)
        except ValueError as exc:
            raise Refused("docker inspect returned invalid JSON") from exc

    def verify_image(self, revision):
        image = slot_image(revision)
        if self._docker("image", "inspect", image, "--format", "{{.Id}}",
                        ok=False).returncode != 0:
            raise Refused(f"image {image} is not present locally")
        baked = self._docker("run", "--rm", "--pull", "never", "--network", "none",
                             "--entrypoint", "cat", image, "/app/BUILD_REVISION")
        if baked.stdout.strip() != revision:
            raise Refused(f"image {image} bakes a different BUILD_REVISION")
        return True

    def admission(self, host_reserve_mib):
        available = parse_mem_available_kib(self._read_meminfo())
        return decide_admission(available, self.running_web_containers(),
                                host_reserve_mib)

    def port_available(self, slot):
        port = slot_port(slot)
        published = self._docker("ps", "-a", "--filter", f"publish={port}",
                                 "--format", "{{.ID}}")
        return not published.stdout.split() and self._port_bindable(port)

    @staticmethod
    def _describe(container):
        env = dict(item.split("=", 1) for item in
                   (container.get("Config") or {}).get("Env") or [] if "=" in item)
        state = container.get("State") or {}
        labels = (container.get("Config") or {}).get("Labels") or {}
        return {
            "id": (container.get("Id") or "")[:12],
            "name": (container.get("Name") or "").lstrip("/"),
            "status": state.get("Status"),
            "health": (state.get("Health") or {}).get("Status"),
            "image": (container.get("Config") or {}).get("Image"),
            "app_revision": env.get("APP_REVISION"),
            "startup_mode": env.get("FITX_STARTUP_MODE"),
            "project": labels.get("com.docker.compose.project"),
            "service": labels.get("com.docker.compose.service"),
            "port_bindings": (container.get("HostConfig") or {}).get("PortBindings"),
        }

    def _matches(self, info, slot, revision):
        return (info["project"] == slot_project(slot)
                and info["service"] == SERVICE
                and info["image"] == slot_image(revision)
                and info["app_revision"] == revision
                and info["startup_mode"] == CANDIDATE_STARTUP_MODE
                and info["port_bindings"] == {
                    f"{INTERNAL_PORT}/tcp": [{"HostIp": LOOPBACK,
                                              "HostPort": str(slot_port(slot))}]})

    # lifecycle
    def preflight(self, slot, revision, host_reserve_mib):
        validate_slot(slot)
        validate_revision(revision)
        existing = [self._describe(c) for c in self.slot_containers(slot)]
        if existing:
            # Only a running, still-plausible candidate of exactly this
            # revision is reused; verify() then decides readiness. Anything
            # else (other revision, stopped, unhealthy, foreign binding) is
            # ambiguous and is never overwritten or repaired here.
            if (len(existing) == 1 and existing[0]["status"] == "running"
                    and existing[0]["health"] in ("healthy", "starting")
                    and self._matches(existing[0], slot, revision)):
                return {"action": "already-running", "container": existing[0]}
            raise Refused(f"slot {slot} already holds a container that is not "
                          f"a running {revision[:12]} candidate; remove it "
                          "explicitly (never overwritten)")
        self.render(slot, revision)
        self.verify_image(revision)
        self.check_shared_network()
        if not self.port_available(slot):
            raise Refused(f"{LOOPBACK}:{slot_port(slot)} is already in use")
        decision = self.admission(host_reserve_mib)
        if not decision.allowed:
            raise Refused(f"capacity admission refused: {decision.reason} "
                          f"(available {decision.mem_available_kib} kB, "
                          f"required {decision.required_kib} kB, "
                          f"running web {decision.running_web_containers})")
        return {"action": "start", "admission": asdict(decision)}

    def start(self, slot, revision, host_reserve_mib):
        plan = self.preflight(slot, revision, host_reserve_mib)
        if plan["action"] == "already-running":
            return plan
        self._compose(slot, revision, "up", "-d", "--no-build", "--pull", "never",
                      "--no-deps", "--no-recreate", SERVICE,
                      timeout=COMPOSE_UP_TIMEOUT_SECONDS)
        return {"action": "started", "admission": plan["admission"]}

    def _single(self, slot):
        containers = self.slot_containers(slot)
        if len(containers) != 1:
            raise VerificationFailed(
                f"slot {slot} has {len(containers)} containers, expected 1")
        return containers[0]

    def verify(self, slot, revision):
        validate_revision(revision)
        info = None
        for _ in range(HEALTH_WAIT_POLLS):
            container = self._single(slot)
            info = self._describe(container)
            if not self._matches(info, slot, revision):
                raise VerificationFailed("slot container identity does not match "
                                         "the expected candidate")
            if info["status"] != "running":
                raise VerificationFailed(f"candidate is {info['status']}")
            if info["health"] == "healthy":
                break
            self._sleep(HEALTH_POLL_SECONDS)
        else:
            raise VerificationFailed("candidate never became healthy")
        cid = container["Id"]
        baked = self._docker("exec", cid, "cat", "/app/BUILD_REVISION")
        if baked.stdout.strip() != revision:
            raise VerificationFailed("baked BUILD_REVISION mismatch")
        deep = self._retry(DEEP_PROBE_ATTEMPTS, lambda: self._deep_probe(cid, revision))
        if not deep:
            raise VerificationFailed("in-container deep health never proved "
                                     "the expected revision")
        host = self._retry(HOST_PROBE_ATTEMPTS, lambda: self._host_probe(slot))
        if not host:
            raise VerificationFailed("candidate loopback port never answered 200")
        return {"action": "verified", "container": info,
                "deep_health_revision": revision,
                "host_health": f"http://{LOOPBACK}:{slot_port(slot)}/health"}

    def _retry(self, attempts, probe):
        for attempt in range(attempts):
            if probe():
                return True
            if attempt + 1 < attempts:
                self._sleep(PROBE_DELAY_SECONDS)
        return False

    def _deep_probe(self, cid, revision):
        result = self._docker("exec", cid, "python3", "-c", _DEEP_PROBE_CODE, ok=False)
        if result.returncode != 0:
            return False
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return False
        return (payload.get("http") == 200 and payload.get("status") == "ok"
                and payload.get("revision") == revision)

    def _host_probe(self, slot):
        try:
            status, body = self._http_get(
                f"http://{LOOPBACK}:{slot_port(slot)}/health", 5)
            return status == 200 and json.loads(body).get("status") == "ok"
        except Exception:
            return False

    def inspect(self, slot):
        return {"slot": slot, "project": slot_project(slot),
                "port": slot_port(slot),
                "containers": [self._describe(c) for c in self.slot_containers(slot)]}

    def remove(self, slot, expected_revision):
        validate_revision(expected_revision)
        containers = [self._describe(c) for c in self.slot_containers(slot)]
        if not containers:
            return {"action": "absent"}
        if not all(self._matches(c, slot, expected_revision) for c in containers):
            raise Refused(f"slot {slot} does not hold revision "
                          f"{expected_revision[:12]}; refusing to remove it")
        # No -v, no --rmi, no --remove-orphans: only this project's web goes.
        # The external shared network is never removed by `down`.
        self._compose(slot, expected_revision, "down", "--timeout",
                      str(STOP_GRACE_SECONDS), timeout=COMPOSE_DOWN_TIMEOUT_SECONDS)
        return {"action": "removed", "containers": containers}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--deploy-dir", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--main-project", default=DEFAULT_MAIN_PROJECT)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("render", "preflight", "start", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--slot", required=True)
        command.add_argument("--revision", required=True)
        if name in ("preflight", "start"):
            command.add_argument("--host-reserve-mib")
    sub.add_parser("admission").add_argument("--host-reserve-mib")
    sub.add_parser("inspect").add_argument("--slot", required=True)
    remove = sub.add_parser("remove")
    remove.add_argument("--slot", required=True)
    remove.add_argument("--expected-revision", required=True)
    args = parser.parse_args(argv)
    try:
        runtime = SlotRuntime(args.deploy_dir, args.main_project)
        reserve = parse_host_reserve_mib(getattr(args, "host_reserve_mib", None))
        if args.command == "render":
            runtime.render(validate_slot(args.slot), validate_revision(args.revision))
            out = {"action": "rendered", "project": slot_project(args.slot),
                   "port": slot_port(args.slot), "network": runtime.network}
        elif args.command == "admission":
            out = asdict(runtime.admission(reserve))
        elif args.command == "preflight":
            out = runtime.preflight(args.slot, args.revision, reserve)
        elif args.command == "start":
            out = runtime.start(args.slot, args.revision, reserve)
        elif args.command == "verify":
            out = runtime.verify(validate_slot(args.slot), args.revision)
        elif args.command == "inspect":
            out = runtime.inspect(validate_slot(args.slot))
        else:
            out = runtime.remove(validate_slot(args.slot), args.expected_revision)
    except SlotError as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}))
        return exc.exit_code
    print(json.dumps(out, sort_keys=True))
    if args.command == "admission" and not out["allowed"]:
        return EXIT_REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(main())
