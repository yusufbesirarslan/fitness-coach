#!/usr/bin/python3 -I
"""One-time nginx route bootstrap for R6-02B. Written in R6-02A; NOT run there.

    sudo /usr/bin/python3 -I scripts/axisai_nginx_bootstrap.py check
    sudo /usr/bin/python3 -I scripts/axisai_nginx_bootstrap.py apply

Run from the deploy checkout at the exact merged revision (the runbook in
deploy/nginx/README.md verifies that first). The control-plane artifacts it
installs are the repository bytes next to this file, never a copy from a
workstation, a pasted heredoc or the stale R6-01 Stage A files.

``check`` is read-only. ``apply`` turns the Certbot-managed live site from

    location / { proxy_pass http://127.0.0.1:5000; ... }

into

    upstream axisai_web { include /etc/nginx/axisai/active-web-upstream.conf; }
    location / { proxy_pass http://axisai_web; ... }

with the include holding ``server 127.0.0.1:5000;`` (the same backend), so the
change is behaviour-preserving. It changes exactly one argument and inserts
one block; every other byte of the site is preserved, which is verified before
anything is written. Topology is classified by the SAME classifier the switch
helper uses (loaded from the sibling file that is also the installed helper):

    legacy-direct -> eligible; migrated -> no rewrite (artifact drift fails
    closed); partial / unknown -> refuse.

Every failure after the site is rewritten restores the exact backed-up bytes,
validates them and reloads once. Exit codes: 0 ok, 10 live config invalid,
11 candidate nginx -t failed (restored), 12 reload failed (restored),
13 restore failed (MANUAL ACTION), 20 unexpected topology, 21 post-reload
verification failed (restored), 22 Certbot parser gate failed, 64 usage,
74 I/O, 75 lock busy, 77 not root, 78 untrusted/drift.
"""
import difflib
import hashlib
import importlib.util
import os
import shutil
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    "axisai_switch_web_slot", os.path.join(_HERE, "axisai_switch_web_slot.py"))
sw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sw)

PROG = "axisai-nginx-bootstrap"
REPO_DIR = os.path.dirname(_HERE)
SITES_ENABLED = "/etc/nginx/sites-enabled"
NGINX_ROOT = "/etc/nginx"
BACKUP_DIR = "/root/axisai-r6-02b"
PUBLIC_HEALTH_URL = "https://fitx-chatbot.duckdns.org/health"
HEALTH_SAMPLES = 5
HEALTH_INTERVAL_SECONDS = 1.0

# (repository path, installed path, mode)
ARTIFACTS = (
    ("scripts/axisai_switch_web_slot.py", "/usr/local/sbin/axisai-switch-web-slot", 0o755),
    ("deploy/nginx/web-slots.conf", sw.MAPPING_PATH, 0o644),
    ("deploy/nginx/active-web-upstream.conf", sw.INCLUDE_PATH, 0o644),
)

EXIT_OK = 0
EXIT_CURRENT_INVALID = 10
EXIT_CANDIDATE_INVALID = 11
EXIT_RELOAD_FAILED = 12
EXIT_RESTORE_FAILED = 13
EXIT_UNEXPECTED_TOPOLOGY = 20
EXIT_VERIFY_FAILED = 21
EXIT_CERTBOT_GATE = 22
EXIT_USAGE = 64
EXIT_IO = 74
EXIT_LOCK_BUSY = 75
EXIT_NOT_ROOT = 77
EXIT_UNTRUSTED = 78


@dataclass(frozen=True)
class BootConfig:
    sw: "sw.Config" = field(default_factory=sw.Config)
    repo_dir: str = REPO_DIR
    artifacts: Tuple[Tuple[str, str, int], ...] = ARTIFACTS
    sites_enabled: str = SITES_ENABLED
    nginx_root: str = NGINX_ROOT
    backup_dir: str = BACKUP_DIR
    health_url: str = PUBLIC_HEALTH_URL


def upstream_block(include_path: str) -> bytes:
    return ("# AxisAI web route (R6-02): the single active backend lives in the root-owned\n"
            "# include; change it only with /usr/local/sbin/axisai-switch-web-slot.\n"
            f"upstream {sw.UPSTREAM_NAME} {{\n"
            f"    include {include_path};\n"
            "}\n\n").encode("ascii")


class BootError(Exception):
    def __init__(self, code: int, result: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.result = result


def transform(site: bytes, include_path: str, legacy_port: int) -> bytes:
    """legacy-direct -> migrated by editing exactly one argument and inserting
    one block. Refuses anything that is not legacy-direct, and proves the
    result is migrated and differs from the input only in those lines."""
    topology = sw.classify_site(site, include_path, legacy_port)
    if topology.kind != sw.TOPOLOGY_LEGACY:
        raise BootError(EXIT_UNEXPECTED_TOPOLOGY, "unexpected-topology",
                        f"site topology is {topology.kind}: {topology.detail}")
    arg = topology.legacy_proxy_arg
    offset = topology.insert_offset
    assert arg is not None and offset is not None and offset <= arg.start
    block = upstream_block(include_path)
    new = (site[:offset] + block + site[offset:arg.start]
           + f"http://{sw.UPSTREAM_NAME}".encode("ascii") + site[arg.end:])
    if sw.classify_site(new, include_path, legacy_port).kind != sw.TOPOLOGY_MIGRATED:
        raise BootError(EXIT_UNEXPECTED_TOPOLOGY, "unexpected-topology", "transformed site is not migrated")
    line_start = site.rfind(b"\n", 0, arg.start) + 1
    line_end = site.find(b"\n", arg.end)
    legacy_line = site[line_start:len(site) if line_end < 0 else line_end]
    verify_minimal_change(site, new, include_path, legacy_line, site[arg.start:arg.end])
    return new


def verify_minimal_change(old: bytes, new: bytes, include_path: str,
                          legacy_line: bytes, legacy_arg: bytes) -> None:
    """The only removed line is the legacy proxy_pass line; the only added
    lines are the upstream block and that same line naming the upstream."""
    a = old.split(b"\n")
    b = new.split(b"\n")
    removed: List[bytes] = []
    added: List[bytes] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += a[i1:i2]
        if tag in ("replace", "insert"):
            added += b[j1:j2]
    if removed != [legacy_line]:
        raise BootError(EXIT_UNEXPECTED_TOPOLOGY, "unexpected-topology", "transformation removed unexpected lines")
    new_proxy = legacy_line.replace(legacy_arg, f"http://{sw.UPSTREAM_NAME}".encode(), 1)
    expected_added = sorted(upstream_block(include_path).split(b"\n")[:-1] + [new_proxy])
    if sorted(added) != expected_added:
        raise BootError(EXIT_UNEXPECTED_TOPOLOGY, "unexpected-topology", "transformation added unexpected lines")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _probe(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # fixed URL constant
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (OSError, ValueError):
        return 0


def _vhost_signature(nginx_root: str) -> List[Tuple]:
    spec = importlib.util.find_spec("certbot_nginx._internal.parser")
    if spec is None:
        raise BootError(EXIT_CERTBOT_GATE, "certbot-gate-failed", "certbot_nginx is not importable")
    from certbot_nginx._internal import parser  # noqa: WPS433
    nginx_parser = parser.NginxParser(nginx_root)
    return sorted((tuple(sorted(v.names)), bool(v.ssl), tuple(sorted(str(a) for a in v.addrs)),
                   os.path.basename(v.filep)) for v in nginx_parser.get_vhosts())


def certbot_gate(candidate: bytes, bcfg: BootConfig) -> None:
    """Certbot renewals re-parse this site with certbot's own nginx parser.
    Prove it sees exactly the same virtual hosts (names, TLS, listen
    addresses) before and after the transformation, in a private copy."""
    work = tempfile.mkdtemp(prefix="certbot-gate.", dir=bcfg.backup_dir)
    try:
        signatures = []
        for label, site_bytes in (("before", None), ("after", candidate)):
            root = os.path.join(work, label)
            shutil.copytree(bcfg.nginx_root, root, symlinks=False)
            site_rel = os.path.relpath(bcfg.sw.site_path, bcfg.nginx_root)
            targets = [os.path.join(root, site_rel)]
            enabled_rel = os.path.relpath(bcfg.sites_enabled, bcfg.nginx_root)
            for name in os.listdir(bcfg.sites_enabled):
                if os.path.realpath(os.path.join(bcfg.sites_enabled, name)) == os.path.realpath(bcfg.sw.site_path):
                    targets.append(os.path.join(root, enabled_rel, name))
            for dirpath, _dirs, files in os.walk(root):
                for fname in files:
                    path = os.path.join(dirpath, fname)
                    try:
                        text = open(path, "rb").read()
                    except OSError:
                        continue
                    prefix = bcfg.nginx_root.rstrip("/").encode() + b"/"
                    if prefix in text:
                        with open(path, "wb") as fh:
                            fh.write(text.replace(prefix, root.encode() + b"/"))
            if site_bytes is not None:
                for target in targets:
                    with open(target, "wb") as fh:
                        fh.write(site_bytes.replace(bcfg.nginx_root.rstrip("/").encode() + b"/",
                                                    root.encode() + b"/"))
            signatures.append(_vhost_signature(root))
        if not signatures[0] or signatures[0] != signatures[1]:
            raise BootError(EXIT_CERTBOT_GATE, "certbot-gate-failed",
                            "certbot's nginx parser sees different virtual hosts after the change")
    finally:
        shutil.rmtree(work, ignore_errors=True)


@dataclass
class BootDeps:
    run: Callable[[Sequence[str]], Tuple[int, str]] = sw._run
    probe: Callable[[str], int] = _probe
    gate: Callable[[bytes, BootConfig], None] = certbot_gate
    lock: Callable[[str, "sw.Config"], object] = sw._flock
    sleep: Callable[[float], None] = time.sleep
    stamp: Callable[[], str] = lambda: time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _write(path: str, data: bytes, mode: int) -> None:
    sw.atomic_write(path, data, mode)
    if os.geteuid() == 0:
        os.chown(path, 0, 0)


def _ensure_dir(path: str, bcfg: BootConfig, mode: int = 0o755) -> None:
    if not os.path.lexists(path):
        os.mkdir(path, mode)
        os.chmod(path, mode)
        if os.geteuid() == 0:
            os.chown(path, 0, 0)
    sw.check_directory_chain(path, bcfg.sw)


def repo_artifacts(bcfg: BootConfig) -> Dict[str, bytes]:
    out = {}
    for rel, dest, _mode in bcfg.artifacts:
        with open(os.path.join(bcfg.repo_dir, rel), "rb") as fh:
            out[dest] = fh.read()
    return out


def artifact_status(bcfg: BootConfig, wanted: Dict[str, bytes]) -> Dict[str, str]:
    status = {}
    for _rel, dest, mode in bcfg.artifacts:
        try:
            current = sw.read_trusted(dest, bcfg.sw, 1024 * 1024)
        except sw.ControlError:
            status[dest] = "absent" if not os.path.lexists(dest) else "untrusted"
            continue
        st = os.stat(dest, follow_symlinks=False)
        if current != wanted[dest]:
            status[dest] = "differs"
        elif stat.S_IMODE(st.st_mode) != mode:
            status[dest] = "mode-differs"
        else:
            status[dest] = "match"
    return status


def _enabled_links(bcfg: BootConfig) -> List[str]:
    site = os.path.realpath(bcfg.sw.site_path)
    return [n for n in sorted(os.listdir(bcfg.sites_enabled))
            if os.path.realpath(os.path.join(bcfg.sites_enabled, n)) == site]


class Bootstrap:
    def __init__(self, bcfg: BootConfig, deps: BootDeps):
        self.bcfg, self.deps = bcfg, deps
        self.fields: Dict[str, str] = {}
        self.notes: List[str] = []
        self.ops = sw._Ops(bcfg.sw, sw.Deps(run=deps.run))

    def _inspect(self):
        cfg = self.bcfg.sw
        wanted = repo_artifacts(self.bcfg)
        mapping = sw.parse_mapping(wanted[cfg.mapping_path])
        if wanted[cfg.include_path] != sw.render_include(mapping["legacy"]):
            raise BootError(EXIT_UNTRUSTED, "repo-artifact-invalid", "repository include is not the legacy backend")
        site = sw.read_trusted(cfg.site_path, cfg, sw.MAX_SITE_BYTES)
        links = _enabled_links(self.bcfg)
        if len(links) != 1:
            raise BootError(EXIT_UNEXPECTED_TOPOLOGY, "unexpected-topology",
                            "the site must be enabled exactly once in sites-enabled")
        topology = sw.classify_site(site, cfg.include_path, mapping["legacy"])
        status = artifact_status(self.bcfg, wanted)
        self.fields.update(topology=topology.kind, site_sha256=_sha(site))
        for (_rel, dest, _mode) in self.bcfg.artifacts:
            self.fields[os.path.basename(dest)] = status[dest]
        return wanted, mapping, site, topology, status

    def check(self) -> int:
        _wanted, _mapping, _site, topology, _status = self._inspect()
        if topology.kind == sw.TOPOLOGY_MIGRATED:
            route = sw.read_route_state(self.bcfg.sw)
            self.fields["state"] = route.state
        if topology.kind not in (sw.TOPOLOGY_LEGACY, sw.TOPOLOGY_MIGRATED):
            self.notes.append(topology.detail)
            self.fields["result"] = "unexpected-topology"
            return EXIT_UNEXPECTED_TOPOLOGY
        self.fields["result"] = "ok"
        return EXIT_OK

    def apply(self) -> int:
        cfg = self.bcfg.sw
        wanted, mapping, site, topology, status = self._inspect()
        if topology.kind == sw.TOPOLOGY_MIGRATED:
            drift = [d for d, s in status.items() if s != "match" and d != cfg.include_path]
            if drift:
                self.fields["result"] = "already-migrated-drift"
                self.notes.append("migrated site but installed artifacts differ from the repository; no change made")
                return EXIT_UNTRUSTED
            self.fields["state"] = sw.read_route_state(cfg).state
            self.fields["result"] = "already-migrated"
            return EXIT_OK
        if topology.kind != sw.TOPOLOGY_LEGACY:
            self.notes.append(topology.detail)
            self.fields["result"] = "unexpected-topology"
            return EXIT_UNEXPECTED_TOPOLOGY

        candidate = transform(site, cfg.include_path, mapping["legacy"])
        self.fields["candidate_sha256"] = _sha(candidate)
        if not self.ops.test():
            self.fields["result"] = "current-nginx-invalid"
            return EXIT_CURRENT_INVALID

        _ensure_dir(self.bcfg.backup_dir, self.bcfg, 0o700)
        self.deps.gate(candidate, self.bcfg)

        backup = os.path.join(self.bcfg.backup_dir, f"fitx.pre-r6-02b.{self.deps.stamp()}")
        _write(backup, site, 0o600)
        _write(backup + ".sha256", f"{_sha(site)}  {cfg.site_path}\n".encode(), 0o600)
        if sw.read_trusted(backup, cfg, sw.MAX_SITE_BYTES) != site:
            raise BootError(EXIT_IO, "io-error", "backup read-back mismatch")
        self.fields["backup"] = backup

        # Install the exact repository artifacts. The include is not referenced
        # by the live site yet, so replacing it cannot change traffic.
        for _rel, dest, mode in self.bcfg.artifacts:
            _ensure_dir(os.path.dirname(dest), self.bcfg)
            if status[dest] != "match":
                _write(dest, wanted[dest], mode)
        if artifact_status(self.bcfg, wanted) != {d: "match" for _r, d, _m in self.bcfg.artifacts}:
            raise BootError(EXIT_IO, "io-error", "installed artifacts do not match the repository")

        site_mode = stat.S_IMODE(os.stat(cfg.site_path).st_mode)
        _write(cfg.site_path, candidate, site_mode)

        def restore(result: str, code: int, reload: bool = True) -> int:
            self.fields["result"] = result
            try:
                _write(cfg.site_path, site, site_mode)
            except OSError as exc:
                self.notes.append(f"restore site: {exc.strerror or exc}")
                self.fields["restored"] = "no"
                return self._manual(backup)
            self.fields["restored"] = "yes"
            if not self.ops.test():
                self.fields["restored_config_valid"] = "no"
                return self._manual(backup)
            self.fields["restored_config_valid"] = "yes"
            if not reload:
                # nginx never loaded the candidate: it already runs the
                # restored configuration.
                return code
            if not self.ops.reload():
                self.fields["known_good_reload"] = "no"
                return self._manual(backup)
            self.fields["known_good_reload"] = "yes"
            return code

        if not self.ops.test():
            return restore("candidate-nginx-invalid", EXIT_CANDIDATE_INVALID, reload=False)
        if not self.ops.reload():
            return restore("reload-failed", EXIT_RELOAD_FAILED)

        try:
            now = sw.read_trusted(cfg.site_path, cfg, sw.MAX_SITE_BYTES)
            ok = (now == candidate
                  and sw.classify_site(now, cfg.include_path, mapping["legacy"]).kind == sw.TOPOLOGY_MIGRATED
                  and sw.read_route_state(cfg).state == "legacy")
        except sw.ControlError as exc:
            self.notes.append(str(exc))
            ok = False
        if not ok:
            return restore("verify-failed", EXIT_VERIFY_FAILED)
        self.deps.sleep(HEALTH_INTERVAL_SECONDS)
        codes = []
        for _ in range(HEALTH_SAMPLES):
            codes.append(self.deps.probe(self.bcfg.health_url))
            self.deps.sleep(HEALTH_INTERVAL_SECONDS)
        self.fields["health"] = ",".join(str(c) for c in codes)
        if any(c != 200 for c in codes):
            return restore("verify-failed", EXIT_VERIFY_FAILED)
        self.fields.update(state="legacy", site_sha256_after=_sha(candidate), result="migrated")
        return EXIT_OK

    def _manual(self, backup: str) -> int:
        self.notes.append(
            f"MANUAL ACTION: restore {backup} to {self.bcfg.sw.site_path} (sha256 in {backup}.sha256), "
            f"then `nginx -t` and `systemctl reload nginx`")
        return EXIT_RESTORE_FAILED


def run(argv: Sequence[str], bcfg: Optional[BootConfig] = None,
        deps: Optional[BootDeps] = None) -> Tuple[int, Dict[str, str], List[str]]:
    bcfg = bcfg or BootConfig()
    deps = deps or BootDeps()
    boot = Bootstrap(bcfg, deps)
    lock = None
    op = "invalid"
    try:
        if list(argv) not in (["check"], ["apply"]):
            raise BootError(EXIT_USAGE, "usage", f"usage: {PROG} check|apply")
        op = argv[0]
        boot.fields["op"] = op
        if os.geteuid() != bcfg.sw.trusted_uid:
            raise BootError(EXIT_NOT_ROOT, "not-root", "must run as root")
        lock = deps.lock(bcfg.sw.lock_path, bcfg.sw)
        code = boot.check() if op == "check" else boot.apply()
    except BootError as exc:
        code = exc.code
        boot.fields["result"] = exc.result
        boot.notes.append(str(exc))
    except sw.ControlError as exc:
        code = EXIT_LOCK_BUSY if exc.code == sw.EXIT_LOCK_BUSY else EXIT_UNTRUSTED
        boot.fields["result"] = exc.result
        boot.notes.append(str(exc))
    except OSError as exc:
        code = EXIT_IO
        boot.fields["result"] = "io-error"
        boot.notes.append(f"{type(exc).__name__}: {exc.strerror or exc}")
    finally:
        if isinstance(lock, int):
            os.close(lock)
    fields = {"op": op}
    fields.update(boot.fields)
    return code, fields, boot.notes


def main(argv: Sequence[str]) -> int:
    os.umask(0o022)
    code, fields, notes = run(argv)
    sys.stdout.write(" ".join(f"{k}={v}" for k, v in fields.items()) + "\n")
    for note in notes:
        sys.stderr.write(f"{PROG}: {note}\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
