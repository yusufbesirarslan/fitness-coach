#!/usr/bin/env python3
"""R6-03A: the exact-SHA blue/green production release transaction.

``scripts/production_deploy.sh`` (C) proves the outer lock, the controller's
authority, the candidate's currency on ``origin/main`` and its ancestry, then
materializes ``git archive DEPLOY_SHA`` into a private context directory and
runs THIS file from that context. Nothing in here is reached by a command that
failed those proofs.

The transaction (every arrow is a gate; the old backend serves throughout):

    baseline: canonical route status (legacy|blue|green), control-plane
      parity, serving revision proven from the running container, exact
      worker baseline (immutable image ID), Redis identity
    -> migration overlap gate (static, BEFORE any database I/O)
    -> exact image axisai-web:<DEPLOY_SHA> from the git-archive context,
       baked BUILD_REVISION proven with `docker run --network none`
    -> exact previous worker image pinned as rollback material
    -> release-prepare exactly once (the only DB mutation)
    -> candidate slot: web_slot_runtime.py start (admission) + verify
    -> immediate re-verify + route re-read (must still be the previous route)
    -> route switch through the root helper only
    -> post-switch proof: route, candidate, public /health, anonymous mobile
       ingress envelope
    -> worker -> exact candidate image (worker only; Redis/web untouched)
    -> checkout -> DEPLOY_SHA
    == COMMIT POINT ==
    -> bounded drain of the old backend (kernel socket observation)
    -> retirement of the old backend (legacy container or previous slot)
    -> housekeeping (warnings only; never rolls a committed release back)

Any failure before the commit point rolls back in the order route, previous
web proof, exact previous worker, candidate removal, checkout. A backend is
only ever removed once the route is PROVEN to point elsewhere. Database
migrations are never rolled back (expand-only by the gate).

Exit codes: 0 committed and retired; 1 failed and the previous serving state
is proven; 2 failed and the previous serving state is NOT proven (manual
action, details on stderr); 3 committed but the old backend was not retired;
64 invalid invocation; 70 invalid timing contract.

Production constants (helper path, mapping path, image names) are module
constants. Tests build ``Transaction`` / ``HostOps`` in-process with fakes; the
command line can only ever use the production defaults.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path, PurePosixPath
from typing import Callable, Optional
from urllib.parse import urlsplit, urlunsplit

SCRIPTS_DIR = Path(__file__).resolve().parent


def _load_sibling(name):
    """Load a sibling module by path. The engine runs under ``python3 -I``,
    which keeps the script directory off sys.path on purpose."""
    spec = importlib.util.spec_from_file_location(
        f"_r6_sibling_{name}", SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = _load_sibling("deploy_contract")

# â”€â”€ Identity contract â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
ROUTE_STATES = ("legacy", "blue", "green")
# Deterministic candidate rule. There is no slot input anywhere.
CANDIDATE_SLOT = {"legacy": "blue", "blue": "green", "green": "blue"}
# Cross-checked against the helper's own `status` backend before anything is
# trusted; tests pin these to deploy/nginx/web-slots.conf and the slot runtime.
ROUTE_BACKENDS = {"legacy": "127.0.0.1:5000", "blue": "127.0.0.1:5001",
                  "green": "127.0.0.1:5002"}
ROUTE_HELPER = "/usr/local/sbin/axisai-switch-web-slot"
ROUTE_COMMAND = ("sudo", "-n", ROUTE_HELPER)
ROUTE_MAPPING = "/etc/axisai/web-slots.conf"
# Installed privileged route authority vs. the candidate's repository bytes.
CONTROL_PLANE_FILES = (
    (ROUTE_HELPER, "scripts/axisai_switch_web_slot.py"),
    (ROUTE_MAPPING, "deploy/nginx/web-slots.conf"),
)
IMAGE_REPO = "axisai-web"
WORKER_ROLLBACK_REPO = "axisai-worker-rollback"
MOBILE_INGRESS_PATH = "/api/v1/account/me"
MOBILE_INGRESS_CODE = "AUTH_SESSION_EXPIRED"
SLOT_RUNTIME = "scripts/web_slot_runtime.py"
RELEASE_PREPARE_SERVICE = "release-prepare"
RELEASE_PREPARE_MEMORY = "640m"
LEGACY_STOP_GRACE_SECONDS = 45     # = the slot stop grace (gunicorn 30 s + 15 s)
WORKER_POLL_SECONDS = 5
PUBLIC_PROBE_DELAY_SECONDS = 5
PUBLIC_PROBE_ATTEMPTS = 12
INGRESS_PROBE_ATTEMPTS = 6
HTTP_TIMEOUT_SECONDS = 5
BUILD_CACHE_KEEP_BYTES = 4294967296
COMMAND_KILL_GRACE_SECONDS = 2
_SHA = re.compile(r"[0-9a-f]{40}")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")

EXIT_OK = 0
EXIT_ROLLED_BACK = 1
EXIT_ROLLBACK_INCOMPLETE = 2
EXIT_COMMITTED_RESIDUE = 3
EXIT_USAGE = 64
EXIT_CONTRACT = 70

_DEEP_PROBE_CODE = (
    "import json,urllib.request\n"
    "r=urllib.request.urlopen('http://127.0.0.1:5000/health?deep=1',timeout=5)\n"
    "p=json.load(r)\n"
    "print(json.dumps({'http':r.status,'status':p.get('status'),"
    "'revision':p.get('revision')}))\n"
)


class StepFailed(Exception):
    """A gate refused. The transaction rolls back from the recorded state."""


class OpError(Exception):
    """A host operation failed or returned something that proves nothing."""


class PreparationCleanupUnproven(OpError):
    """The one-off migrator may still be running; recovery is incomplete."""


class DeadlineExhausted(StepFailed):
    pass


@dataclass(frozen=True)
class Container:
    id: str
    image_id: str
    status: str
    health: Optional[str]
    started_at: str
    app_revision: Optional[str]
    labels: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Route:
    state: str
    backend: str


# â”€â”€ Migration overlap gate (pure; static; no database) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _module_literal(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            if isinstance(node.value, ast.Constant):
                return node.value.value
            return None
    return None


def _revisions_with_digests(versions_dir, expand_contract):
    found = {}
    for path in sorted(Path(versions_dir).glob("*.py")):
        raw = path.read_bytes()
        revision = _module_literal(ast.parse(raw.decode("utf-8")), "revision")
        if not isinstance(revision, str) or not revision:
            raise OpError(f"{path.name}: revision is not a module literal")
        if revision in found:
            raise OpError(f"duplicate migration revision {revision}")
        found[revision] = (path, expand_contract.normalized_digest(raw))
    return found


def classify_migration_delta(previous_dir, candidate_dir, expand_contract):
    """Classify every migration the candidate adds over the previous revision.

    Returns ``(delta, problems)``; ``delta`` maps revision -> classification.
    The candidate must pass the repository gate as a whole. Each delta
    migration must then be overlap-safe:

    * declared ``expand`` (rules already enforced by the gate)       -> safe
    * declared ``contract``                                          -> REFUSE
    * a pre-contract historical migration the previous revision never
      ran: it carries no declaration, so it must pass the SAME expand rules
      on its own source (``downgrade`` excluded)                     -> safe/REFUSE
    * anything else (undeclared, unparsable)                         -> REFUSE

    A migration the previous revision has that the candidate lost, or whose
    bytes the candidate changed, is refused too: shipped history is immutable.
    """
    problems = []
    scan = expand_contract.scan(versions_dir=Path(candidate_dir))
    for name, found in sorted(scan["violations"].items()):
        problems += [f"{name}: {problem}" for problem in found]
    previous = _revisions_with_digests(previous_dir, expand_contract)
    candidate = _revisions_with_digests(candidate_dir, expand_contract)
    for revision in sorted(set(previous) - set(candidate)):
        problems.append(f"{revision}: removed by the candidate")
    for revision in sorted(set(previous) & set(candidate)):
        if previous[revision][1] != candidate[revision][1]:
            problems.append(f"{revision}: modified after it shipped")
    delta = {}
    for revision in sorted(set(candidate) - set(previous)):
        declared = scan["classes"].get(revision)
        if declared == expand_contract.EXPAND:
            delta[revision] = "expand"
        elif declared == expand_contract.CONTRACT:
            delta[revision] = "contract"
            problems.append(f"{revision}: contract migration cannot overlap two "
                            "serving revisions")
        elif declared == "historical":
            path = candidate[revision][0]
            visitor = expand_contract._Scope()
            visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
            if visitor.problems:
                delta[revision] = "historical-unsafe"
                problems += [f"{revision}: {p}" for p in visitor.problems]
            else:
                delta[revision] = "historical-expand"
        else:
            delta[revision] = "unclassified"
            problems.append(f"{revision}: not classified as overlap-safe")
    return delta, problems


# â”€â”€ Host operations (all side effects live here) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _bounded_run(argv, timeout, env=None, cwd=None):
    """One external command in its own process group, hard-bounded.

    On timeout the whole group gets SIGTERM, then SIGKILL after a short
    grace: a docker CLI child must not outlive the budget that started it."""
    if timeout <= 0:
        raise OpError(f"{argv[0]}: no time left")
    try:
        proc = subprocess.Popen(
            list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, cwd=cwd, start_new_session=True)
    except OSError as exc:
        raise OpError(f"{argv[0]} unavailable: {type(exc).__name__}") from exc
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        for sig, grace in ((signal.SIGTERM, COMMAND_KILL_GRACE_SECONDS),
                           (signal.SIGKILL, None)):
            try:
                os.killpg(proc.pid, sig)
            except OSError:
                pass
            try:
                proc.communicate(timeout=grace)
                break
            except subprocess.TimeoutExpired:
                continue
        raise OpError(f"{argv[0]} {argv[1] if len(argv) > 1 else ''} "
                      f"timed out after {int(timeout)}s")
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _http_get(url, timeout):
    """Anonymous GET: no credentials, no cookies, no Authorization."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "axisai-deploy-probe", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(65536)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(65536)


def _host_operation(func):
    """Sequential commands in one host operation share one monotonic cutoff."""
    @wraps(func)
    def bounded(self, *args, **kwargs):
        timeout = kwargs.get("timeout", args[-1] if args else None)
        previous = self._command_deadline
        deadline = self.clock() + timeout
        self._command_deadline = min(previous, deadline) if previous is not None else deadline
        try:
            return func(self, *args, **kwargs)
        finally:
            self._command_deadline = previous
    return bounded


class HostOps:
    """Real host operations. Every method takes a hard timeout (seconds)."""

    def __init__(self, *, deploy_dir, context_dir, work_dir, main_project, deploy_sha,
                 previous_commit, run=_bounded_run, http_get=_http_get,
                 route_command=ROUTE_COMMAND, control_plane_files=CONTROL_PLANE_FILES,
                 trusted_uid=0, python=None, environ=None):
        self.deploy_dir = Path(deploy_dir)
        self.context = Path(context_dir)
        self.work = Path(work_dir)
        self.trusted_uid = trusted_uid
        self.main = main_project
        self.deploy_sha = deploy_sha
        self.previous_commit = previous_commit
        self._run = run
        self.clock = time.monotonic
        self._command_deadline = None
        self._http_get = http_get
        self.route_command = tuple(route_command)
        self.control_plane_files = tuple(control_plane_files)
        self.python = python or sys.executable
        self.environ = dict(os.environ if environ is None else environ)
        self.network = f"{main_project}_default"

    # plumbing
    def _command(self, argv, timeout, env=None):
        if self._command_deadline is not None:
            timeout = min(timeout, self._command_deadline - self.clock())
        # subprocess timeout is followed by a bounded kill grace; reserve both.
        timeout -= COMMAND_KILL_GRACE_SECONDS
        if timeout <= 0:
            raise OpError(f"{argv[0]}: operation deadline exhausted")
        return self._run(argv, timeout, env)

    def _ok(self, argv, timeout, what):
        rc, out, err = self._command(argv, timeout, self.environ)
        if rc != 0:
            raise OpError(f"{what} failed (exit {rc}): {err.strip()[-300:]}")
        return out

    def _docker(self, timeout, *args, what=None):
        return self._ok(["docker", *args], timeout, what or f"docker {args[0]}")

    def _compose(self, timeout, compose_file, override, *args):
        return self._docker(
            timeout, "compose", "--project-directory", str(self.deploy_dir),
            "-p", self.main, "-f", str(compose_file), "-f", str(override), *args,
            what=f"docker compose {args[0]}")

    # workspace
    @_host_operation
    def prepare_workspace(self, timeout):
        """Fill the private work dir (a sibling of the archive context, never
        inside it, so nothing here can enter the image build): the previous
        revision's Compose file and migrations, plus a `.env` link (never a
        copy) so Compose resolves env_file the same from here or the checkout."""
        try:
            (self.work / ".env").symlink_to(self.deploy_dir / ".env")
            previous = self._ok(["git", "-C", str(self.deploy_dir), "show",
                                 f"{self.previous_commit}:docker-compose.yml"],
                                timeout, "git show previous docker-compose.yml")
            (self.work / "previous-compose.yml").write_text(previous, encoding="utf-8")
            archive = self.work / "previous-migrations.tar"
            self._ok(["git", "-C", str(self.deploy_dir), "archive", "--format=tar",
                      "-o", str(archive), self.previous_commit, "migrations/versions"],
                     timeout, "git archive previous migrations")
            with tarfile.open(archive) as tar:
                tar.extractall(self.work / "previous", filter="data")
        except (OSError, tarfile.TarError) as exc:
            raise OpError(f"workspace preparation failed: {exc}") from exc

    def link_context_env(self, timeout):
        """After the image is built: the slot runtime and Compose read
        `.env` relative to the context. A link, so no secret is copied."""
        link = self.context / ".env"
        try:
            if link.exists() or link.is_symlink():
                raise OpError(f"{link} already exists in the archive context")
            link.symlink_to(self.deploy_dir / ".env")
        except OSError as exc:
            raise OpError(f"context .env link failed: {exc}") from exc

    def migration_delta(self, timeout):
        expand_contract = _load_sibling("migration_expand_contract")
        try:
            return classify_migration_delta(
                self.work / "previous" / "migrations" / "versions",
                self.context / "migrations" / "versions", expand_contract)
        except (OSError, SyntaxError, ValueError, UnicodeError) as exc:
            raise OpError(f"migration delta unreadable: {exc}") from exc


    # route authority (the helper is the only reader and writer)
    def _route(self, args, timeout):
        rc, out, err = self._command([*self.route_command, *args], timeout, self.environ)
        lines = [line for line in out.splitlines() if line.strip()]
        fields = {}
        if len(lines) == 1:
            for token in lines[0].split(" "):
                key, sep, value = token.partition("=")
                if not sep or key in fields:
                    fields = {}
                    break
                fields[key] = value
        return rc, fields, err.strip()[-500:]

    def route_status(self, timeout):
        rc, fields, err = self._route(["status"], timeout)
        if rc != 0 or fields.get("op") != "status" or fields.get("result") != "ok":
            raise OpError(f"route status unproven (exit {rc}, "
                          f"result={fields.get('result')}): {err}")
        return Route(fields.get("state", ""), fields.get("backend", ""))

    def route_switch(self, target, timeout):
        rc, fields, err = self._route(["switch", target], timeout)
        if rc != 0 or fields.get("result") != "switched" or fields.get("to") != target:
            raise OpError(f"route switch to {target} failed (exit {rc}, "
                          f"result={fields.get('result')}): {err}")
        return fields

    def control_plane_mismatches(self, timeout):
        problems = []
        for installed, repo_rel in self.control_plane_files:
            try:
                status = os.lstat(installed)
                with open(installed, "rb") as handle:
                    installed_bytes = handle.read()
            except OSError as exc:
                problems.append(f"{installed}: unreadable ({exc.strerror})")
                continue
            if (not stat.S_ISREG(status.st_mode) or status.st_uid != self.trusted_uid
                    or status.st_mode & 0o022):
                problems.append(f"{installed}: not a root-owned, non-writable file")
            candidate = (self.context / repo_rel).read_bytes()
            if hashlib.sha256(installed_bytes).digest() != hashlib.sha256(candidate).digest():
                problems.append(f"{installed} differs from the candidate's {repo_rel}")
        return problems

    # git
    def checkout_head(self, timeout):
        return self._ok(["git", "-C", str(self.deploy_dir), "rev-parse", "--verify",
                         "HEAD^{commit}"], timeout, "git rev-parse").strip()

    def checkout_reset(self, revision, timeout):
        self._ok(["git", "-C", str(self.deploy_dir), "reset", "--hard", revision],
                 timeout, "git reset --hard")

    # containers
    def _inspect(self, ids, timeout):
        if not ids:
            return []
        try:
            raw = json.loads(self._docker(timeout, "inspect", *ids))
        except ValueError as exc:
            raise OpError("docker inspect returned invalid JSON") from exc
        found = []
        for item in raw:
            config = item.get("Config") or {}
            state = item.get("State") or {}
            env = dict(entry.split("=", 1) for entry in config.get("Env") or []
                       if "=" in entry)
            found.append(Container(
                id=item.get("Id", ""), image_id=item.get("Image", ""),
                status=state.get("Status", ""),
                health=(state.get("Health") or {}).get("Status"),
                started_at=state.get("StartedAt", ""),
                app_revision=env.get("APP_REVISION"),
                labels=dict(config.get("Labels") or {})))
        return found

    @_host_operation
    def _service(self, service, timeout):
        ids = self._docker(
            timeout, "ps", "-a", "--filter", f"label=com.docker.compose.project={self.main}",
            "--filter", f"label=com.docker.compose.service={service}",
            "--format", "{{.ID}}").split()
        return [c for c in self._inspect(ids, timeout)
                if c.labels.get("com.docker.compose.oneoff") != "True"]

    def main_web(self, timeout):
        return self._service("web", timeout)

    def worker(self, timeout):
        return self._service("worker", timeout)

    def redis(self, timeout):
        return self._service("redis", timeout)

    @_host_operation
    def container(self, container_id, timeout):
        rc, out, err = self._command(["docker", "container", "inspect", "--format",
                                  "{{.Id}}", container_id], timeout, self.environ)
        if rc != 0:
            if re.fullmatch(r"(?:Error: |Error response from daemon: )?No such (?:container|object): "
                            + re.escape(container_id), err.strip(), re.IGNORECASE):
                return None
            raise OpError(f"docker container inspect failed (exit {rc})")
        found = self._inspect([container_id], timeout)
        return found[0] if found else None

    def container_build_revision(self, container_id, timeout):
        return self._docker(timeout, "exec", container_id, "cat",
                            "/app/BUILD_REVISION").strip()

    def container_deep_health_revision(self, container_id, timeout):
        out = self._docker(timeout, "exec", container_id, "python3", "-c",
                           _DEEP_PROBE_CODE)
        try:
            payload = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise OpError("deep health returned no JSON") from exc
        if payload.get("http") != 200 or payload.get("status") != "ok":
            raise OpError("deep health is not ok")
        return payload.get("revision")

    # slots (web_slot_runtime.py owns every slot Docker rule)
    def _slot(self, timeout, *args):
        argv = [self.python, "-I", str(self.context / SLOT_RUNTIME),
                "--deploy-dir", str(self.context), "--main-project", self.main, *args]
        rc, out, err = self._command(argv, timeout, self.environ)
        try:
            payload = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            payload = {"error": "no-json", "detail": err.strip()[-300:]}
        return rc, payload

    def slot_start(self, slot, revision, timeout):
        rc, payload = self._slot(timeout, "start", "--slot", slot, "--revision", revision)
        if rc != 0 or payload.get("action") not in ("started", "already-running"):
            raise OpError(f"slot {slot} start refused (exit {rc}): {payload}")
        return payload

    def slot_verify(self, slot, revision, timeout):
        rc, payload = self._slot(timeout, "verify", "--slot", slot, "--revision", revision)
        if rc != 0 or payload.get("deep_health_revision") != revision:
            raise OpError(f"slot {slot} verify failed (exit {rc}): {payload}")
        return payload

    def slot_inspect(self, slot, timeout):
        rc, payload = self._slot(timeout, "inspect", "--slot", slot)
        if rc != 0 or "containers" not in payload:
            raise OpError(f"slot {slot} inspect failed (exit {rc}): {payload}")
        return payload["containers"]

    def slot_remove(self, slot, revision, timeout):
        """True when the slot no longer holds `revision`. A refusal means the
        slot holds something else, which this transaction never removes."""
        rc, payload = self._slot(timeout, "remove", "--slot", slot,
                                 "--expected-revision", revision)
        if rc == 0 and payload.get("action") in ("absent", "removed"):
            return payload["action"]
        if rc == 3:
            return "foreign"
        raise OpError(f"slot {slot} remove failed (exit {rc}): {payload}")

    # images
    def build_image(self, revision, timeout):
        self._docker(timeout, "build", "--build-arg", f"BUILD_REVISION={revision}",
                     "-t", f"{IMAGE_REPO}:{revision}", str(self.context))

    def image_id(self, reference, timeout):
        out = self._docker(timeout, "image", "inspect", "--format", "{{.Id}}",
                           reference).strip()
        if not _IMAGE_ID.fullmatch(out):
            raise OpError(f"{reference} has no immutable image ID")
        return out

    def image_baked_revision(self, reference, timeout):
        return self._docker(timeout, "run", "--rm", "--pull", "never", "--network",
                            "none", "--entrypoint", "cat", reference,
                            "/app/BUILD_REVISION").strip()

    def tag_image(self, image_id, tag, timeout):
        self._docker(timeout, "tag", image_id, tag)

    # release preparation: the one migration authority
    @_host_operation
    def release_prepare(self, revision, timeout):
        override = self.work / "release-prepare.yml"
        override.write_text(
            "services:\n"
            f"  {RELEASE_PREPARE_SERVICE}:\n"
            f"    image: '{IMAGE_REPO}:{revision}'\n"
            "    pull_policy: never\n"
            "    env_file: [.env]\n"
            "    environment:\n"
            "      FITX_SKIP_DB_INIT: '1'\n"
            f"      APP_REVISION: '{revision}'\n"
            "    entrypoint: ['flask', '--app', 'starter', 'release-prepare']\n"
            f"    mem_limit: {RELEASE_PREPARE_MEMORY}\n"
            "    logging:\n"
            "      driver: json-file\n"
            "      options: {max-size: '10m', max-file: '3'}\n",
            encoding="utf-8")
        name = f"axisai-release-prepare-{revision[:12]}"
        if timeout <= 30 + COMMAND_KILL_GRACE_SECONDS:
            raise OpError("release-prepare has no cleanup reserve")
        try:
            self._compose(timeout - 30, self.context / "docker-compose.yml", override,
                          "run", "--rm", "--no-deps", "-T", "--name", name,
                          RELEASE_PREPARE_SERVICE)
        except OpError:
            # A killed CLI can leave the one-off container running: remove it
            # so nothing keeps migrating after the transaction gave up.
            try:
                # Reserve five seconds for an absence proof after --rm.
                self._docker(25, "rm", "-f", name, what="release-prepare cleanup")
            except OpError as cleanup_error:
                try:
                    absent = self.container(name, 5) is None
                except OpError:
                    absent = False
                if not absent:
                    raise PreparationCleanupUnproven(
                        f"release-prepare cleanup unproven: {cleanup_error}") from cleanup_error
            raise

    # worker (the only main-project service this transaction recreates)
    def worker_up(self, kind, image_tag, revision, timeout):
        compose = (self.context / "docker-compose.yml" if kind == "candidate"
                   else self.work / "previous-compose.yml")
        override = self.work / f"worker-{kind}.yml"
        override.write_text(
            "services:\n"
            "  worker:\n"
            f"    image: '{image_tag}'\n"
            "    pull_policy: never\n"
            "    environment:\n"
            f"      APP_REVISION: '{revision}'\n", encoding="utf-8")
        self._compose(timeout, compose, override, "up", "-d", "--no-deps",
                      "--no-build", "--pull", "never", "worker")

    # retirement
    def established_connections(self, backend, timeout):
        rc, out, err = self._command(["ss", "-H", "-t", "-n", "state", "established",
                                  "dst", backend], timeout, self.environ)
        if rc != 0:
            raise OpError(f"ss failed (exit {rc}): {err.strip()[-200:]}")
        return len([line for line in out.splitlines() if line.strip()])

    @_host_operation
    def stop_and_remove(self, container_id, timeout):
        self._docker(timeout, "stop", "--time", str(LEGACY_STOP_GRACE_SECONDS),
                     container_id)
        self._docker(timeout, "rm", container_id)

    # observation only
    def http_get(self, url, timeout):
        return self._http_get(url, timeout)

    def diagnostics(self, timeout):
        rc, out, _ = self._command(["docker", "ps", "-a", "--format",
                                "{{.Names}}\t{{.Image}}\t{{.Status}}"],
                               timeout, self.environ)
        return out

    def housekeeping(self, keep_revisions, previous_revision, timeout_for):
        """Warnings only, each command bounded; stops when the budget is gone.
        Old axisai-web tags go; the current and previous revisions stay. Never
        `docker image prune -a`: only dangling layers and the bounded
        BuildKit cache are pruned."""
        notes = []
        commands = []
        try:
            rc, out, _ = self._command(["docker", "image", "ls", "--format",
                                    "{{.Repository}}:{{.Tag}}"], timeout_for(),
                                   self.environ)
        except OpError as exc:
            rc, out = 1, ""
            notes.append(f"image listing did not complete: {exc}")
        if rc == 0:
            for reference in out.split():
                repo, _, tag = reference.partition(":")
                if ((repo == IMAGE_REPO and tag not in keep_revisions)
                        or (repo == WORKER_ROLLBACK_REPO and tag != previous_revision)):
                    commands.append(["docker", "image", "rm", reference])
        commands += [["docker", "image", "prune", "-f"],
                     ["docker", "builder", "prune", "--force", "--keep-storage",
                      str(BUILD_CACHE_KEEP_BYTES)]]
        for argv in commands:
            try:
                rc, _, _ = self._command(argv, timeout_for(), self.environ)
            except OpError as exc:
                notes.append(f"{' '.join(argv[:4])} did not complete: {exc}")
                continue
            if rc != 0:
                notes.append(f"{' '.join(argv[:4])} exited {rc}")
        return notes


# â”€â”€ The transaction â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class Budget:
    """Monotonic phase cutoffs derived from the canonical contract."""

    def __init__(self, clock, forward_cutoff):
        self.clock = clock
        self.forward_cutoff = forward_cutoff
        self.phase_cutoff = forward_cutoff

    def deadline(self, step_seconds):
        return min(self.clock() + step_seconds, self.phase_cutoff)

    def enter(self, phase_seconds, phase_cap):
        """Start a tail phase: at most `phase_seconds` from now and never past
        forward cutoff + `phase_cap` (the contract's own algebra)."""
        self.phase_cutoff = min(self.clock() + phase_seconds,
                                self.forward_cutoff + phase_cap)


class Transaction:
    def __init__(self, ops, *, deploy_sha, previous_commit, public_health_url,
                 clock=time.monotonic, sleep=time.sleep, log=None, budget=None,
                 drain_max_seconds=contract.OLD_BACKEND_DRAIN_MAX_SECONDS,
                 drain_poll_seconds=contract.OLD_BACKEND_DRAIN_POLL_SECONDS):
        self.ops = ops
        self.deploy_sha = deploy_sha
        self.previous_commit = previous_commit
        self.public_health_url = public_health_url
        parts = urlsplit(public_health_url)
        self.ingress_url = urlunsplit((parts.scheme, parts.netloc,
                                       MOBILE_INGRESS_PATH, "", ""))
        self.clock = clock
        self.sleep = sleep
        self.log = log or (lambda message: print(f"r6-deploy: {message}",
                                                 file=sys.stderr, flush=True))
        self.budget = budget or Budget(clock, clock() + contract.HOST_PHASE_SECONDS[
            "release_forward"])
        self.drain_max_seconds = drain_max_seconds
        self.drain_poll_seconds = drain_poll_seconds
        # recorded state
        self.previous_route = None
        self.previous_backend = None
        self.target_slot = None
        self.previous_revision = None
        self.legacy_web = None
        self.previous_worker = None
        self.redis_baseline = None
        self.candidate_image_id = None
        self.worker_rollback_tag = None
        self.delta = {}
        # mutation ledger (what rollback must undo)
        self.release_prepared = False
        self.release_prepare_cleanup_unproven = False
        self.candidate_started = False
        self.route_switch_attempted = False
        self.worker_attempted = False
        self.checkout_attempted = False
        self.events = []

    # helpers
    def _event(self, name, **detail):
        self.events.append((name, detail))
        extra = " ".join(f"{k}={v}" for k, v in detail.items())
        self.log(f"{name} {extra}".rstrip())

    def _time_left(self, deadline):
        left = deadline - self.clock()
        if left <= 0:
            raise DeadlineExhausted("phase deadline exhausted")
        return left

    def _call(self, deadline, what, func, *args):
        try:
            return func(*args, self._time_left(deadline))
        except OpError as exc:
            raise StepFailed(f"{what}: {exc}") from exc

    def _step_deadline(self, step):
        return self.budget.deadline(contract.RELEASE_FORWARD_STEP_SECONDS[step])

    # â”€â”€ forward â”€â”€
    def run(self):
        try:
            self._call(self._step_deadline("baseline"), "workspace",
                       self.ops.prepare_workspace)
            self._baseline()
            self._migration_gate()
            self._build_and_prove_image()
            self._release_prepare()
            self._start_candidate()
            self._pre_switch()
            self._switch()
            self._post_switch()
            self._update_worker()
            self._checkout()
        except Exception as failure:  # noqa: BLE001 - any surprise rolls back
            self._event("release-failed", reason=f"{type(failure).__name__}: {failure}")
            try:
                return self._rollback()
            except Exception as surprise:  # noqa: BLE001
                self._event("rollback-incomplete",
                            manual=f"rollback crashed: {type(surprise).__name__}: "
                                   f"{surprise}")
                return EXIT_ROLLBACK_INCOMPLETE
        self._event("release-committed", revision=self.deploy_sha,
                    route=self.target_slot)
        try:
            return self._retire()
        except Exception as surprise:  # noqa: BLE001 - committed; never roll back
            self._event("retirement-incomplete", backend=self.previous_route,
                        reason=f"{type(surprise).__name__}: {surprise}")
            return EXIT_COMMITTED_RESIDUE

    def _baseline(self):
        deadline = self._step_deadline("baseline")
        route = self._call(deadline, "route status", self.ops.route_status)
        if route.state not in ROUTE_STATES or route.backend != ROUTE_BACKENDS[route.state]:
            raise StepFailed(f"active route is not a proven state: {route}")
        self.previous_route, self.previous_backend = route.state, route.backend
        self.target_slot = CANDIDATE_SLOT[route.state]
        self._event("route-baseline", previous=route.state, candidate=self.target_slot)

        mismatches = self._call(deadline, "control-plane parity",
                                self.ops.control_plane_mismatches)
        if mismatches:
            raise StepFailed("control-plane parity failed; the privileged route "
                             "authority needs a separately reviewed rollout: "
                             + "; ".join(mismatches))

        head = self._call(deadline, "checkout head", self.ops.checkout_head)
        if head != self.previous_commit:
            raise StepFailed("checkout HEAD moved since the host preflight")

        legacy = self._call(deadline, "legacy web", self.ops.main_web)
        if route.state == "legacy":
            running = [c for c in legacy if c.status == "running"]
            if len(running) != 1 or len(legacy) != 1:
                raise StepFailed("route is legacy but the main-project web is not "
                                 "exactly one running container")
            web = running[0]
            revision = self._call(deadline, "legacy BUILD_REVISION",
                                  self.ops.container_build_revision, web.id)
            deep = self._call(deadline, "legacy deep health",
                              self.ops.container_deep_health_revision, web.id)
            if deep != revision:
                raise StepFailed("legacy deep-health revision differs from its "
                                 "baked BUILD_REVISION")
            self.legacy_web = web
        else:
            if legacy:
                raise StepFailed(f"route is {route.state} but a main-project web "
                                 "container still exists")
            slot = self._call(deadline, "active slot", self.ops.slot_inspect,
                              route.state)
            if len(slot) != 1 or not slot[0].get("app_revision"):
                raise StepFailed(f"active slot {route.state} is not one container")
            revision = slot[0]["app_revision"]
            if not _SHA.fullmatch(revision or ""):
                raise StepFailed("active slot revision is not 40-hex")
            self._call(deadline, "active slot verify", self.ops.slot_verify,
                       route.state, revision)
        if not _SHA.fullmatch(revision or ""):
            raise StepFailed("serving revision is not a 40-hex BUILD_REVISION")
        if revision != self.previous_commit:
            raise StepFailed("serving revision differs from the checkout HEAD")
        self.previous_revision = revision
        self._event("serving-revision", revision=revision)

        workers = self._call(deadline, "worker baseline", self.ops.worker)
        if (len(workers) != 1 or workers[0].status != "running"
                or workers[0].health != "healthy"):
            raise StepFailed("worker is not exactly one healthy running container")
        worker = workers[0]
        if not _IMAGE_ID.fullmatch(worker.image_id):
            raise StepFailed("worker has no immutable image ID")
        worker_revision = self._call(deadline, "worker BUILD_REVISION",
                                     self.ops.container_build_revision, worker.id)
        if worker_revision != revision or worker.app_revision != revision:
            raise StepFailed("worker revision differs from the serving revision")
        self.previous_worker = worker
        self._event("worker-baseline", container=worker.id[:12],
                    image=worker.image_id, started=worker.started_at)

        redis = self._call(deadline, "redis baseline", self.ops.redis)
        if len(redis) != 1 or redis[0].status != "running":
            raise StepFailed("redis is not exactly one running container")
        self.redis_baseline = redis[0]

    def _migration_gate(self):
        delta, problems = self._call(self._step_deadline("migration_gate"),
                                     "migration gate", self.ops.migration_delta)
        self.delta = delta
        self._event("migration-delta", **{rev: cls for rev, cls in delta.items()})
        if problems:
            raise StepFailed("migration overlap gate refused before any database "
                             "mutation: " + "; ".join(problems))

    def _build_and_prove_image(self):
        tag = f"{IMAGE_REPO}:{self.deploy_sha}"
        self._call(self._step_deadline("image_build"), "image build",
                   self.ops.build_image, self.deploy_sha)
        deadline = self._step_deadline("image_proof")
        self.candidate_image_id = self._call(deadline, "image inspect",
                                             self.ops.image_id, tag)
        baked = self._call(deadline, "baked revision", self.ops.image_baked_revision, tag)
        if baked != self.deploy_sha:
            raise StepFailed("candidate image bakes a different BUILD_REVISION")
        # Exact rollback material for the worker, pinned by immutable ID
        # BEFORE anything destructive happens to it.
        self.worker_rollback_tag = f"{WORKER_ROLLBACK_REPO}:{self.previous_revision}"
        self._call(deadline, "worker rollback tag", self.ops.tag_image,
                   self.previous_worker.image_id, self.worker_rollback_tag)
        pinned = self._call(deadline, "worker rollback image", self.ops.image_id,
                            self.worker_rollback_tag)
        if pinned != self.previous_worker.image_id:
            raise StepFailed("worker rollback image is not the exact previous image")
        self._call(deadline, "context .env link", self.ops.link_context_env)
        self._event("image-proven", image=self.candidate_image_id)

    def _release_prepare(self):
        self.release_prepared = True
        try:
            self.ops.release_prepare(self.deploy_sha, self._time_left(
                self._step_deadline("release_prepare")))
        except PreparationCleanupUnproven:
            self.release_prepare_cleanup_unproven = True
            raise
        except OpError as exc:
            raise StepFailed(f"release-prepare: {exc}") from exc
        self._event("release-prepared")

    def _start_candidate(self):
        self.candidate_started = True
        self._call(self._step_deadline("candidate_start"), "candidate start",
                   self.ops.slot_start, self.target_slot, self.deploy_sha)
        self._verify_candidate(self._step_deadline("candidate_verify"))
        self._event("candidate-verified", slot=self.target_slot)

    def _verify_candidate(self, deadline):
        # The slot authority proves slot/revision/readiness. Bind its container
        # to the immutable image built from this candidate archive as well.
        proof = self._call(deadline, "candidate verify", self.ops.slot_verify,
                           self.target_slot, self.deploy_sha)
        identity = (proof.get("container") or {}).get("id")
        if not isinstance(identity, str) or not identity:
            raise StepFailed("candidate verification returned no container identity")
        current = self._call(deadline, "candidate immutable image",
                             self.ops.container, identity)
        if (current is None or current.status != "running"
                or current.image_id != self.candidate_image_id
                or current.app_revision != self.deploy_sha):
            raise StepFailed("candidate does not run the exact built image")

    def _pre_switch(self):
        deadline = self._step_deadline("pre_switch_verify")
        self._verify_candidate(deadline)
        route = self._call(deadline, "route re-read", self.ops.route_status)
        if route.state != self.previous_route:
            raise StepFailed(f"route drifted to {route.state} before the switch")

    def _switch(self):
        self.route_switch_attempted = True
        self._call(self._step_deadline("route_switch"), "route switch",
                   self.ops.route_switch, self.target_slot)
        self._event("route-switched", to=self.target_slot)

    def _post_switch(self):
        deadline = self._step_deadline("post_switch")
        route = self._call(deadline, "route read-back", self.ops.route_status)
        if route.state != self.target_slot:
            raise StepFailed(f"route reads {route.state} after the switch")
        self._verify_candidate(deadline)
        self._probe(deadline, PUBLIC_PROBE_ATTEMPTS, self.public_health_url,
                    self._public_ok, "public health")
        self._probe(deadline, INGRESS_PROBE_ATTEMPTS, self.ingress_url,
                    self._ingress_ok, "mobile ingress")
        self._event("post-switch-proven")

    @staticmethod
    def _public_ok(status, body):
        return status == 200

    @staticmethod
    def _ingress_ok(status, body):
        if status != 401:
            return False
        try:
            error = json.loads(body).get("error") or {}
        except (ValueError, AttributeError):
            return False
        return (error.get("code") == MOBILE_INGRESS_CODE
                and error.get("retryable") is False)

    def _probe(self, deadline, attempts, url, accept, what):
        last = None
        for attempt in range(1, attempts + 1):
            try:
                status, body = self.ops.http_get(
                    url, min(HTTP_TIMEOUT_SECONDS, self._time_left(deadline)))
                last = status
                if accept(status, body):
                    return
            except (OSError, urllib.error.URLError, ValueError) as exc:
                last = type(exc).__name__
            if attempt < attempts:
                if self._time_left(deadline) <= PUBLIC_PROBE_DELAY_SECONDS:
                    break
                self.sleep(PUBLIC_PROBE_DELAY_SECONDS)
        raise StepFailed(f"{what} not proven (last={last})")

    def _update_worker(self):
        deadline = self._step_deadline("worker_update")
        self.worker_attempted = True
        self._call(deadline, "worker update", self.ops.worker_up, "candidate",
                   f"{IMAGE_REPO}:{self.deploy_sha}", self.deploy_sha)
        self._prove_worker(deadline, self.candidate_image_id, self.deploy_sha)
        redis = self._call(deadline, "redis identity", self.ops.redis)
        if (len(redis) != 1 or redis[0].id != self.redis_baseline.id
                or redis[0].started_at != self.redis_baseline.started_at
                or redis[0].status != "running"):
            raise StepFailed("redis was recreated or restarted during the release")
        self._event("worker-updated", revision=self.deploy_sha)

    def _prove_worker(self, deadline, image_id, revision):
        while True:
            workers = self._call(deadline, "worker state", self.ops.worker)
            if len(workers) != 1:
                raise StepFailed(f"{len(workers)} worker containers, expected 1")
            worker = workers[0]
            if worker.status != "running":
                raise StepFailed(f"worker is {worker.status}")
            if worker.health == "healthy":
                break
            if worker.health not in ("starting", None) or \
                    self._time_left(deadline) <= WORKER_POLL_SECONDS:
                raise StepFailed(f"worker never became healthy ({worker.health})")
            self.sleep(WORKER_POLL_SECONDS)
        if worker.image_id != image_id:
            raise StepFailed("worker runs a different image than the exact one")
        baked = self._call(deadline, "worker BUILD_REVISION",
                           self.ops.container_build_revision, worker.id)
        if baked != revision or worker.app_revision != revision:
            raise StepFailed("worker revision does not match")

    def _checkout(self):
        deadline = self._step_deadline("checkout")
        self.checkout_attempted = True
        self._call(deadline, "checkout", self.ops.checkout_reset, self.deploy_sha)
        head = self._call(deadline, "checkout head", self.ops.checkout_head)
        if head != self.deploy_sha:
            raise StepFailed("checkout HEAD is not DEPLOY_SHA")

    # â”€â”€ rollback â”€â”€
    def _rollback(self):
        self.budget.enter(contract.HOST_PHASE_SECONDS["diagnostics"],
                          contract.HOST_PHASE_SECONDS["diagnostics"])
        try:
            snapshot = self.ops.diagnostics(self._time_left(self.budget.phase_cutoff))
            self.log("diagnostics:\n" + snapshot.rstrip())
        except (StepFailed, OpError) as exc:
            self.log(f"diagnostics skipped: {exc}")
        self.budget.enter(contract.HOST_PHASE_SECONDS["release_rollback"],
                          contract.HOST_FAILURE_TAIL_SECONDS)
        steps = contract.RELEASE_ROLLBACK_STEP_SECONDS
        manual = (["release-prepare container cleanup unproven"]
                  if self.release_prepare_cleanup_unproven else [])

        route_ok = True
        if self.previous_route is not None and (
                self.route_switch_attempted or self.candidate_started):
            route_ok = self._restore_route(self.budget.deadline(steps["route_restore"]))
            if not route_ok:
                manual.append(f"route is not proven {self.previous_route}")

        previous_ok = True
        if (self.route_switch_attempted or self.candidate_started) and route_ok:
            previous_ok = self._verify_previous(self.budget.deadline(steps["previous_verify"]))
            if not previous_ok:
                manual.append("previous web did not re-verify")

        worker_ok = True
        if self.worker_attempted:
            worker_ok = self._restore_worker(self.budget.deadline(steps["worker_restore"]))
            if not worker_ok:
                manual.append("WORKER ROLLBACK NOT PROVEN")

        if self.candidate_started:
            if route_ok and previous_ok and worker_ok:
                if not self._remove_candidate(self.budget.deadline(steps["candidate_removal"])):
                    manual.append(f"candidate slot {self.target_slot} not removed")
            else:
                manual.append(f"candidate slot {self.target_slot} kept (route/previous/"
                              "worker not proven)")

        if self.checkout_attempted:
            try:
                deadline = self.budget.deadline(steps["candidate_removal"])
                self.ops.checkout_reset(self.previous_commit, self._time_left(deadline))
                if self.ops.checkout_head(self._time_left(deadline)) != self.previous_commit:
                    raise OpError("HEAD mismatch")
                self._event("checkout-restored", revision=self.previous_commit)
            except (StepFailed, OpError) as exc:
                manual.append(f"checkout not restored to {self.previous_commit}: {exc}")

        if self.release_prepared:
            self.log("note: release-prepare ran; expand migrations stay applied "
                     "(never rolled back)")
        if manual:
            self._event("rollback-incomplete", manual="; ".join(manual))
            return EXIT_ROLLBACK_INCOMPLETE
        self._event("rollback-verified", route=self.previous_route,
                    revision=self.previous_revision)
        return EXIT_ROLLED_BACK

    def _restore_route(self, deadline):
        try:
            route = self.ops.route_status(self._time_left(deadline))
            if route.state == self.previous_route:
                return True
        except (StepFailed, OpError) as exc:
            self.log(f"route status during rollback: {exc}")
        if not self.route_switch_attempted:
            # The transaction never switched: a different route is drift that
            # this transaction must not "repair".
            return False
        try:
            self.ops.route_switch(self.previous_route, self._time_left(deadline))
            route = self.ops.route_status(self._time_left(deadline))
        except (StepFailed, OpError) as exc:
            self.log(f"route restore failed: {exc}")
            return False
        if route.state != self.previous_route:
            return False
        self._event("route-restored", to=self.previous_route)
        return True

    def _verify_previous(self, deadline):
        try:
            if self.previous_route == "legacy":
                current = self.ops.container(self.legacy_web.id, self._time_left(deadline))
                if current is None or current.status != "running":
                    return False
                if self.ops.container_build_revision(
                        current.id, self._time_left(deadline)) != self.previous_revision:
                    return False
                return self.ops.container_deep_health_revision(
                    current.id, self._time_left(deadline)) == self.previous_revision
            self.ops.slot_verify(self.previous_route, self.previous_revision,
                                 self._time_left(deadline))
            return True
        except (StepFailed, OpError) as exc:
            self.log(f"previous web verification failed: {exc}")
            return False

    def _restore_worker(self, deadline):
        try:
            pinned = self.ops.image_id(self.worker_rollback_tag, self._time_left(deadline))
            if pinned != self.previous_worker.image_id:
                self.log("worker rollback tag no longer names the exact previous image")
                return False
            self.ops.worker_up("previous", self.worker_rollback_tag,
                               self.previous_revision, self._time_left(deadline))
            self._prove_worker(deadline, self.previous_worker.image_id,
                               self.previous_revision)
        except (StepFailed, OpError) as exc:
            self.log(f"worker restore failed: {exc}")
            return False
        self._event("worker-restored", image=self.previous_worker.image_id)
        return True

    def _remove_candidate(self, deadline):
        try:
            route = self.ops.route_status(self._time_left(deadline))
            if (route.state != self.previous_route
                    or route.backend != ROUTE_BACKENDS[self.previous_route]):
                raise StepFailed("route drifted before candidate removal")
            outcome = self.ops.slot_remove(self.target_slot, self.deploy_sha,
                                           self._time_left(deadline))
        except (StepFailed, OpError) as exc:
            self.log(f"candidate removal failed: {exc}")
            return False
        self._event("candidate-removed", slot=self.target_slot, outcome=outcome)
        return True

    # â”€â”€ success tail â”€â”€
    def _retire(self):
        self.budget.enter(contract.HOST_PHASE_SECONDS["retirement"],
                          contract.HOST_SUCCESS_TAIL_SECONDS)
        steps = contract.RETIREMENT_STEP_SECONDS
        self._drain(self.budget.deadline(steps["drain"]))
        status = EXIT_OK
        deadline = self.budget.deadline(steps["backend_retirement"])
        try:
            self._retire_old_backend(deadline)
        except (StepFailed, OpError) as exc:
            self._event("retirement-incomplete", backend=self.previous_route,
                        reason=str(exc))
            status = EXIT_COMMITTED_RESIDUE
        hk_deadline = self.budget.deadline(steps["housekeeping"])

        def timeout_for():
            return min(120, hk_deadline - self.clock())

        try:
            for note in self.ops.housekeeping(
                    {self.deploy_sha, self.previous_revision},
                    self.previous_revision, timeout_for):
                self.log(f"housekeeping warning: {note}")
        except (OpError, OSError) as exc:
            self.log(f"housekeeping warning: {exc}")
        if status == EXIT_OK:
            self._event("release-complete", route=self.target_slot,
                        revision=self.deploy_sha)
        return status

    def _drain(self, deadline):
        ceiling = min(self.clock() + self.drain_max_seconds, deadline)
        started = self.clock()
        observed = None
        while True:
            try:
                observed = self.ops.established_connections(
                    self.previous_backend, max(1, min(10, ceiling - self.clock())))
            except OpError as exc:
                observed = None
                self.log(f"drain observation unavailable: {exc}")
            if observed == 0:
                self._event("drain-complete", backend=self.previous_backend,
                            seconds=int(self.clock() - started))
                return
            if self.clock() + self.drain_poll_seconds >= ceiling:
                break
            self.sleep(self.drain_poll_seconds)
        self._event("drain-deadline", backend=self.previous_backend,
                    residual=("unknown" if observed is None else observed),
                    seconds=int(self.clock() - started))

    def _retire_old_backend(self, deadline):
        route = self._call(deadline, "pre-retirement route", self.ops.route_status)
        if (route.state != self.target_slot
                or route.backend != ROUTE_BACKENDS[self.target_slot]):
            raise StepFailed("route drifted before previous backend retirement")
        if self.previous_route == "legacy":
            current = self.ops.container(self.legacy_web.id, self._time_left(deadline))
            if current is None:
                self._event("legacy-retired", container="already-absent")
                return
            revision = self.ops.container_build_revision(current.id,
                                                         self._time_left(deadline))
            if revision != self.previous_revision:
                raise StepFailed("legacy web no longer runs the previous revision")
            self.ops.stop_and_remove(current.id, self._time_left(deadline))
            self._event("legacy-retired", container=current.id[:12])
            return
        outcome = self.ops.slot_remove(self.previous_route, self.previous_revision,
                                       self._time_left(deadline))
        if outcome == "foreign":
            raise StepFailed(f"slot {self.previous_route} no longer holds the "
                             "previous revision")
        self._event("slot-retired", slot=self.previous_route, outcome=outcome)


# â”€â”€ Command line (production defaults only) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _validate_contract_environment(environ):
    expected = contract.host_timeout_environment()
    for name, value in expected.items():
        if environ.get(name) != value:
            raise ValueError(f"{name} does not match the canonical contract")


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("--deploy-sha", "--previous-commit", "--deploy-dir", "--context",
                 "--work-dir", "--public-health-url", "--transaction-epoch"):
        run.add_argument(name, required=True)
    args = parser.parse_args(argv)
    try:
        if not (_SHA.fullmatch(args.deploy_sha) and _SHA.fullmatch(args.previous_commit)):
            raise ValueError("revisions must be lowercase 40-hex")
        # The host is Linux: validate as POSIX paths whatever runs the tests.
        deploy_dir, context, work = (PurePosixPath(args.deploy_dir),
                                     PurePosixPath(args.context),
                                     PurePosixPath(args.work_dir))
        if not all(path.is_absolute() for path in (deploy_dir, context, work)):
            raise ValueError("paths must be absolute")
        if context.parent != deploy_dir or work.parent != deploy_dir or context == work:
            raise ValueError("context and work dir must be private directories of "
                             "DEPLOY_DIR")
        main_project = deploy_dir.name
        if not _PROJECT.fullmatch(main_project) or main_project.startswith("axisai-web-"):
            raise ValueError("DEPLOY_DIR basename is not a Compose project name")
        url = urlsplit(args.public_health_url)
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.password is not None):
            raise ValueError("PUBLIC_HEALTH_URL must be HTTPS without credentials")
        if not re.fullmatch(r"[0-9]{1,15}", args.transaction_epoch):
            raise ValueError("transaction epoch must be a monotonic second count")
        epoch = int(args.transaction_epoch)
    except ValueError as exc:
        print(f"r6-deploy: invalid invocation: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        _validate_contract_environment(environ)
    except ValueError as exc:
        print(f"r6-deploy: {exc}", file=sys.stderr)
        return EXIT_CONTRACT
    phases = contract.HOST_PHASE_SECONDS
    forward_cutoff = min(time.monotonic() + phases["release_forward"],
                         epoch + phases["git_preparation"] + phases["release_forward"])
    ops = HostOps(deploy_dir=Path(deploy_dir), context_dir=Path(context),
                  work_dir=Path(work),
                  main_project=main_project,
                  deploy_sha=args.deploy_sha, previous_commit=args.previous_commit,
                  python=sys.executable, environ=dict(environ))
    transaction = Transaction(ops, deploy_sha=args.deploy_sha,
                              previous_commit=args.previous_commit,
                              public_health_url=args.public_health_url,
                              budget=Budget(time.monotonic, forward_cutoff))
    return transaction.run()


if __name__ == "__main__":
    sys.exit(main())
