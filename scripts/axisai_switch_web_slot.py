#!/usr/bin/python3 -I
"""Root-owned nginx web-route control plane (R6-02A).

Installed as ``/usr/local/sbin/axisai-switch-web-slot`` (root:root 0755) by
``scripts/axisai_nginx_bootstrap.py``. Exactly two invocations exist:

    axisai-switch-web-slot status
    axisai-switch-web-slot switch legacy|blue|green

The caller names a symbolic ROUTE STATE and nothing else. ``legacy`` is the
main-project web (``127.0.0.1:5000``); ``blue``/``green`` are the R6-01B slot
runtimes. The backend is resolved through the root-owned mapping
``/etc/axisai/web-slots.conf``. Paths and commands are module constants: no
flag, environment variable or argument can redirect them.

Rollback is not a separate operation. A deploy transaction reads ``status``
before it switches and, if post-switch verification fails, switches back to
the state it read (including ``legacy`` on the first cutover). Every route
change therefore goes through the same validated path.

``switch`` sequence (fail closed at every step):
  lock -> trusted mapping -> trusted include -> live site references the
  include -> ``nginx -t`` on the CURRENT config -> pending marker ->
  atomic include rename -> ``nginx -t`` on the candidate -> graceful reload
  -> canonical read-back -> clear the pending marker.
  On a candidate ``nginx -t`` failure the previous include is restored and
  nothing is reloaded. On a reload failure the previous include is restored,
  validated and reloaded once.

The pending marker lives in /run: it exists only while a switch is between
"include rewritten" and "nginx provably loaded the on-disk include". If a
switch dies inside that window (SIGKILL, timeout), ``status`` reports
``unknown`` until a later switch succeeds. A reboot clears /run, and nginx
then starts from the on-disk include, so the marker never outlives the
ambiguity it records.

Security note: the deploy user keeps passwordless sudo, so this command is
defense-in-depth and interface narrowing, not a hard privilege boundary.

Machine-readable contract: exactly one ``key=value`` line on stdout per
invocation (no user input is echoed), human detail on stderr, and the exit
code below.
"""
import os
import re
import secrets
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

PROG = "axisai-switch-web-slot"

STATES = ("legacy", "blue", "green")
MAPPING_PATH = "/etc/axisai/web-slots.conf"
INCLUDE_PATH = "/etc/nginx/axisai/active-web-upstream.conf"
SITE_PATH = "/etc/nginx/sites-available/fitx"
LOCK_PATH = "/run/axisai-web-route.lock"
PENDING_PATH = "/run/axisai-web-route.pending"
UPSTREAM_NAME = "axisai_web"
NGINX_TEST = ("/usr/sbin/nginx", "-t", "-q")
NGINX_RELOAD = ("/usr/bin/systemctl", "reload", "nginx")
COMMAND_TIMEOUT_SECONDS = 30
FIXED_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}

EXIT_OK = 0
EXIT_CURRENT_INVALID = 10      # live nginx config invalid before any change
EXIT_CANDIDATE_INVALID = 11    # candidate rejected, previous restored, no reload
EXIT_RELOAD_FAILED = 12        # reload refused, previous restored + reloaded
EXIT_CONVERGENCE_FAILED = 13   # restore / known-good reload failed: MANUAL ACTION
EXIT_STATE_UNKNOWN = 14        # include/site/pending marker do not prove a state
EXIT_USAGE = 64
EXIT_IO = 74                   # write failed before anything was reloaded
EXIT_LOCK_BUSY = 75
EXIT_NOT_ROOT = 77
EXIT_UNTRUSTED = 78            # mapping/include/site/lock fails the trust contract

MAX_MAPPING_BYTES = 4096
MAX_INCLUDE_BYTES = 64
MAX_SITE_BYTES = 256 * 1024

LOOPBACK = "127.0.0.1"
_MAPPING_LINE = re.compile(r"(legacy|blue|green) 127\.0\.0\.1:([1-9][0-9]{3,4})")
_INCLUDE = re.compile(rb"server 127\.0\.0\.1:([1-9][0-9]{3,4});\n")


class ControlError(Exception):
    def __init__(self, code: int, result: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.result = result


@dataclass(frozen=True)
class Config:
    """Production constants. Tests construct other instances in-process; the
    command line can only ever use the defaults."""
    mapping_path: str = MAPPING_PATH
    include_path: str = INCLUDE_PATH
    site_path: str = SITE_PATH
    lock_path: str = LOCK_PATH
    pending_path: str = PENDING_PATH
    nginx_test: Tuple[str, ...] = NGINX_TEST
    nginx_reload: Tuple[str, ...] = NGINX_RELOAD
    trusted_uid: int = 0
    trust_root: str = "/"


# --------------------------------------------------------------------------
# Trusted file access
# --------------------------------------------------------------------------

def _untrusted(detail: str) -> ControlError:
    return ControlError(EXIT_UNTRUSTED, "untrusted-config", detail)


def check_directory_chain(directory: str, cfg: Config) -> None:
    """Every directory from ``cfg.trust_root`` down to ``directory`` must be a
    real directory (not a symlink) owned by the trusted uid and not group- or
    world-writable, so nobody else can swap a path component."""
    root = os.path.abspath(cfg.trust_root)
    target = os.path.abspath(directory)
    if target != root and not target.startswith(root.rstrip("/") + "/"):
        raise _untrusted(f"{directory} is outside the trusted tree")
    rel = os.path.relpath(target, root)
    parts = [] if rel == "." else rel.split("/")
    current = root
    for part in [""] + parts:
        current = os.path.join(current, part) if part else current
        st = os.lstat(current)
        if (not stat.S_ISDIR(st.st_mode) or st.st_uid != cfg.trusted_uid
                or st.st_mode & 0o022):
            raise _untrusted(f"untrusted directory: {current}")


def read_trusted(path: str, cfg: Config, limit: int) -> bytes:
    """A regular, single-link, non-symlink file owned by the trusted uid that
    neither group nor others can write, in a trusted directory chain."""
    check_directory_chain(os.path.dirname(path), cfg)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError as exc:
        raise _untrusted(f"cannot open {path}: {exc.strerror or exc}")
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != cfg.trusted_uid
                or st.st_mode & 0o022 or st.st_nlink != 1):
            raise _untrusted(f"untrusted file: {path}")
        if st.st_size > limit:
            raise _untrusted(f"file too large: {path}")
        chunks = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise _untrusted(f"file too large: {path}")
    return data


def atomic_write(path: str, data: bytes, mode: int = 0o644) -> None:
    """Same-directory exclusive temp file + fsync + rename + directory fsync.
    Readers only ever see the complete old or the complete new file; the temp
    file never survives a failure."""
    directory = os.path.dirname(path)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        try:
            os.fchmod(fd, mode)  # independent of the caller's umask
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    fsync_directory(directory)


def fsync_directory(directory: str) -> None:
    dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


# --------------------------------------------------------------------------
# Canonical parsers: mapping, include, route state (the ONLY ones)
# --------------------------------------------------------------------------

def parse_mapping(data: bytes) -> Dict[str, int]:
    """``<state> 127.0.0.1:<port>`` lines plus ``#`` comment / empty lines.

    All three states exactly once, loopback only, ports 1024-65535, pairwise
    distinct. No whitespace variants, no CR, nothing else."""
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise _untrusted("mapping is not ASCII")
    if text and not text.endswith("\n"):
        raise _untrusted("mapping must end with a newline")
    mapping: Dict[str, int] = {}
    for line in text.split("\n")[:-1]:
        if line == "" or line.startswith("#"):
            continue
        match = _MAPPING_LINE.fullmatch(line)
        if not match:
            raise _untrusted("invalid mapping line")
        state, port = match.group(1), int(match.group(2))
        if state in mapping:
            raise _untrusted(f"duplicate mapping for {state}")
        if not 1024 <= port <= 65535:
            raise _untrusted(f"port out of range for {state}")
        mapping[state] = port
    if set(mapping) != set(STATES):
        raise _untrusted("mapping must define legacy, blue and green")
    if len(set(mapping.values())) != len(STATES):
        raise _untrusted("route states must use distinct ports")
    return mapping


def render_include(port: int) -> bytes:
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("port out of range")
    return f"server {LOOPBACK}:{port};\n".encode("ascii")


def parse_include(data: bytes, mapping: Dict[str, int]) -> str:
    """Exactly one canonical ``server 127.0.0.1:<port>;`` line whose port is a
    mapped state. Anything else is an unknown route state."""
    match = _INCLUDE.fullmatch(data)
    if not match:
        raise ControlError(EXIT_STATE_UNKNOWN, "state-unknown", "active include is not canonical")
    port = int(match.group(1))
    for state, mapped in mapping.items():
        if mapped == port:
            return state
    raise ControlError(EXIT_STATE_UNKNOWN, "state-unknown", "active include names an unmapped backend")


def load_mapping(cfg: Config) -> Dict[str, int]:
    return parse_mapping(read_trusted(cfg.mapping_path, cfg, MAX_MAPPING_BYTES))


def pending_exists(cfg: Config) -> bool:
    try:
        os.lstat(cfg.pending_path)
    except FileNotFoundError:
        return False
    return True


def require_routed_site(cfg: Config, mapping: Dict[str, int]) -> None:
    """The include only governs traffic once the live site proxies to the
    named upstream that includes it (R6-02B bootstrap). Before that, or after
    any manual drift, there is no provable route state."""
    site = read_trusted(cfg.site_path, cfg, MAX_SITE_BYTES)
    topology = classify_site(site, cfg.include_path, mapping["legacy"])
    if topology.kind != TOPOLOGY_MIGRATED:
        raise ControlError(EXIT_STATE_UNKNOWN, "state-unknown",
                           f"live site topology is {topology.kind}, not {TOPOLOGY_MIGRATED}")


@dataclass(frozen=True)
class RouteState:
    state: str            # legacy | blue | green
    port: int
    disk_state: str


def read_route_state(cfg: Config, mapping: Optional[Dict[str, int]] = None) -> RouteState:
    """Canonical active-route discovery. Raises ControlError for anything that
    does not prove exactly one of legacy / blue / green."""
    mapping = mapping if mapping is not None else load_mapping(cfg)
    require_routed_site(cfg, mapping)
    disk_state = parse_include(read_trusted(cfg.include_path, cfg, MAX_INCLUDE_BYTES), mapping)
    if pending_exists(cfg):
        raise ControlError(EXIT_STATE_UNKNOWN, "state-unknown",
                           "a previous switch was interrupted; running nginx may differ from the include")
    return RouteState(state=disk_state, port=mapping[disk_state], disk_state=disk_state)


# --------------------------------------------------------------------------
# nginx site topology classifier (shared with the R6-02B bootstrap)
# --------------------------------------------------------------------------

TOPOLOGY_LEGACY = "legacy-direct"
TOPOLOGY_MIGRATED = "migrated"
TOPOLOGY_PARTIAL = "partial"
TOPOLOGY_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Token:
    value: str
    start: int   # byte offsets into the UTF-8 source
    end: int
    quoted: bool


@dataclass
class Directive:
    name: Token
    args: List[Token]
    block: Optional[List["Directive"]]


class NginxSyntaxError(ValueError):
    pass


def tokenize(data: bytes) -> List[Token]:
    """nginx lexical rules sufficient for site files: ``#`` comments outside
    quotes, single/double quoted strings with backslash escapes, and the
    ``{`` ``}`` ``;`` punctuation."""
    tokens: List[Token] = []
    i, n = 0, len(data)
    ws = b" \t\r\n"
    while i < n:
        c = data[i:i + 1]
        if c in (b" ", b"\t", b"\r", b"\n"):
            i += 1
        elif c == b"#":
            nl = data.find(b"\n", i)
            i = n if nl < 0 else nl + 1
        elif c in (b"{", b"}", b";"):
            tokens.append(Token(c.decode(), i, i + 1, False))
            i += 1
        elif c in (b'"', b"'"):
            j = i + 1
            buf = bytearray()
            while j < n and data[j:j + 1] != c:
                if data[j:j + 1] == b"\\" and j + 1 < n:
                    j += 1
                buf += data[j:j + 1]
                j += 1
            if j >= n:
                raise NginxSyntaxError("unterminated quoted string")
            tokens.append(Token(buf.decode("utf-8"), i, j + 1, True))
            i = j + 1
        else:
            j = i
            while j < n and data[j:j + 1] not in (b"{", b"}", b";") and data[j] not in ws:
                if data[j:j + 1] == b"\\" and j + 1 < n:
                    j += 1
                j += 1
            tokens.append(Token(data[i:j].decode("utf-8"), i, j, False))
            i = j
    return tokens


def parse_nginx(data: bytes) -> List[Directive]:
    tokens = tokenize(data)
    pos = 0

    def block(depth: int) -> List[Directive]:
        nonlocal pos
        out: List[Directive] = []
        while pos < len(tokens):
            tok = tokens[pos]
            if not tok.quoted and tok.value == "}":
                if depth == 0:
                    raise NginxSyntaxError("unbalanced }")
                pos += 1
                return out
            if not tok.quoted and tok.value in ("{", ";"):
                raise NginxSyntaxError("directive without a name")
            name = tok
            pos += 1
            args: List[Token] = []
            while pos < len(tokens) and (tokens[pos].quoted or tokens[pos].value not in ("{", ";", "}")):
                args.append(tokens[pos])
                pos += 1
            if pos >= len(tokens):
                raise NginxSyntaxError("unterminated directive")
            end = tokens[pos]
            pos += 1
            if end.value == ";":
                out.append(Directive(name, args, None))
            elif end.value == "{":
                out.append(Directive(name, args, block(depth + 1)))
            else:
                raise NginxSyntaxError("unexpected }")
        if depth != 0:
            raise NginxSyntaxError("unbalanced {")
        return out

    return block(0)


def _walk(directives: List[Directive], parents: Tuple[Directive, ...] = ()):
    for d in directives:
        yield d, parents
        if d.block is not None:
            yield from _walk(d.block, parents + (d,))


@dataclass(frozen=True)
class Topology:
    kind: str
    detail: str
    legacy_proxy_arg: Optional[Token] = None
    insert_offset: Optional[int] = None


def _is_root_location(parents: Tuple[Directive, ...]) -> bool:
    return (len(parents) == 2 and parents[0].name.value == "server"
            and parents[0].args == [] and parents[1].name.value == "location"
            and [a.value for a in parents[1].args] == ["/"])


def classify_site(data: bytes, include_path: str, legacy_port: int) -> Topology:
    """Classify an nginx site file (http-context contents).

    legacy-direct: exactly one ``proxy_pass http://127.0.0.1:<legacy>;`` in a
        ``server { location / { } }``, nothing else names the legacy backend,
        and no trace of the named upstream / include exists.
    migrated: exactly one top-level ``upstream axisai_web { include <path>; }``,
        every reference to the upstream is ``proxy_pass http://axisai_web;``
        in a ``server { location / { } }`` (at least one), the include path
        appears nowhere else and the legacy backend appears nowhere.
    partial: some trace of the migration exists but not the full shape.
    unknown: anything else (including unparsable text).
    """
    legacy_backend = f"{LOOPBACK}:{legacy_port}"
    try:
        tree = parse_nginx(data)
    except (NginxSyntaxError, UnicodeDecodeError) as exc:
        return Topology(TOPOLOGY_UNKNOWN, f"unparsable: {exc}")
    nodes = list(_walk(tree))

    upstream_blocks = [d for d, p in nodes if d.name.value == "upstream"
                       and [a.value for a in d.args] == [UPSTREAM_NAME]]
    named_refs = [(d, p) for d, p in nodes
                  if any(UPSTREAM_NAME in a.value for a in d.args)
                  and not any(d is u for u in upstream_blocks)]
    include_refs = [(d, p) for d, p in nodes if any(include_path in a.value for a in d.args)]
    legacy_refs = [(d, p) for d, p in nodes if any(legacy_backend in a.value for a in d.args)]
    traces = bool(upstream_blocks or named_refs or include_refs)
    servers = [d for d in tree if d.name.value == "server" and d.block is not None]

    if not traces:
        if (len(legacy_refs) == 1 and servers):
            d, parents = legacy_refs[0]
            if (d.name.value == "proxy_pass" and len(d.args) == 1 and not d.args[0].quoted
                    and d.args[0].value == f"http://{legacy_backend}" and _is_root_location(parents)):
                line_start = data.rfind(b"\n", 0, servers[0].name.start) + 1
                return Topology(TOPOLOGY_LEGACY, "direct legacy proxy_pass",
                                legacy_proxy_arg=d.args[0], insert_offset=line_start)
            return Topology(TOPOLOGY_UNKNOWN, "legacy backend referenced outside the expected proxy_pass")
        if not legacy_refs:
            return Topology(TOPOLOGY_UNKNOWN, "no app backend reference")
        return Topology(TOPOLOGY_UNKNOWN, "legacy backend referenced more than once")

    def fail(detail: str) -> Topology:
        return Topology(TOPOLOGY_PARTIAL, detail)

    if len(upstream_blocks) != 1:
        return fail("expected exactly one upstream axisai_web")
    up = upstream_blocks[0]
    if not any(up is d for d in tree):
        return fail("upstream axisai_web is not at http level")
    body = up.block or []
    if (len(body) != 1 or body[0].name.value != "include" or body[0].block is not None
            or [a.value for a in body[0].args] != [include_path]):
        return fail("upstream axisai_web must contain exactly the active include")
    if len(include_refs) != 1:
        return fail("active include referenced outside the upstream")
    if legacy_refs:
        return fail("legacy backend still referenced directly")
    if not named_refs:
        return fail("upstream axisai_web is not referenced by proxy_pass")
    for d, parents in named_refs:
        if not (d.name.value == "proxy_pass" and len(d.args) == 1 and not d.args[0].quoted
                and d.args[0].value == f"http://{UPSTREAM_NAME}" and _is_root_location(parents)):
            return fail("axisai_web referenced outside proxy_pass in location /")
    for d, parents in nodes:
        if d.name.value == "location" and [a.value for a in d.args] == ["/"]:
            passes = [c for c in (d.block or []) if c.name.value == "proxy_pass"]
            if len(passes) != 1 or [a.value for a in passes[0].args] != [f"http://{UPSTREAM_NAME}"]:
                return fail("a location / does not proxy to axisai_web")
    return Topology(TOPOLOGY_MIGRATED, "named upstream via active include")


# --------------------------------------------------------------------------
# Privileged operations
# --------------------------------------------------------------------------

def _run(argv: Sequence[str]) -> Tuple[int, str]:
    """Fixed argv, no shell, fixed minimal environment, bounded time."""
    proc = subprocess.run(
        list(argv), shell=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=dict(FIXED_ENV), timeout=COMMAND_TIMEOUT_SECONDS, check=False,
    )
    return proc.returncode, proc.stderr.decode("utf-8", "replace")


def _flock(path: str, cfg: Config):
    import fcntl
    check_directory_chain(os.path.dirname(path), cfg)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600)
    except OSError as exc:
        raise _untrusted(f"cannot open lock {path}: {exc.strerror or exc}")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != cfg.trusted_uid or st.st_mode & 0o022:
            raise _untrusted("untrusted lock file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ControlError(EXIT_LOCK_BUSY, "lock-busy", "another route operation is in progress")
    except BaseException:
        os.close(fd)
        raise
    return fd


@dataclass
class Deps:
    run: Callable[[Sequence[str]], Tuple[int, str]] = _run
    write: Callable[[str, bytes], None] = atomic_write
    lock: Callable[[str, Config], object] = _flock
    out: Callable[[str], None] = None   # type: ignore[assignment]
    err: Callable[[str], None] = None   # type: ignore[assignment]


@dataclass
class Outcome:
    code: int
    fields: Dict[str, str]
    notes: List[str] = field(default_factory=list)

    def line(self) -> str:
        return " ".join(f"{k}={v}" for k, v in self.fields.items())


def _last_line(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = lines[-1] if lines else ""
    return "".join(ch for ch in tail if " " <= ch <= "~")[:300]


class _Ops:
    def __init__(self, cfg: Config, deps: Deps):
        self.cfg, self.deps = cfg, deps
        self.notes: List[str] = []

    def ok(self, argv: Sequence[str], what: str) -> bool:
        try:
            rc, err = self.deps.run(argv)
        except subprocess.TimeoutExpired:
            self.notes.append(f"{what}: timed out after {COMMAND_TIMEOUT_SECONDS}s")
            return False
        except (OSError, subprocess.SubprocessError) as exc:
            self.notes.append(f"{what}: {type(exc).__name__}")
            return False
        if rc != 0:
            self.notes.append(f"{what}: exit {rc} {_last_line(err)}".rstrip())
        return rc == 0

    def test(self) -> bool:
        return self.ok(self.cfg.nginx_test, "nginx -t")

    def reload(self) -> bool:
        return self.ok(self.cfg.nginx_reload, "reload")

    def write_include(self, data: bytes) -> None:
        self.deps.write(self.cfg.include_path, data)

    def mark_pending(self, from_state: str, to_state: str) -> None:
        self.deps.write(self.cfg.pending_path, f"from={from_state} to={to_state}\n".encode("ascii"))

    def clear_pending(self) -> bool:
        try:
            os.unlink(self.cfg.pending_path)
            fsync_directory(os.path.dirname(self.cfg.pending_path))
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.notes.append(f"clear pending marker: {exc.strerror or exc}")
            return False
        return True


def _manual(from_state: str) -> str:
    target = from_state if from_state in STATES else "<verified state>"
    return (f"MANUAL ACTION: inspect `nginx -t` and the nginx error log, then run "
            f"`{PROG} switch {target}`; `{PROG} status` stays unknown until a switch succeeds")


def do_status(cfg: Config) -> Outcome:
    route = read_route_state(cfg)
    return Outcome(EXIT_OK, {"op": "status", "state": route.state,
                             "backend": f"{LOOPBACK}:{route.port}", "result": "ok"})


def do_switch(target: str, cfg: Config, deps: Deps, ops: _Ops) -> Outcome:
    mapping = load_mapping(cfg)
    require_routed_site(cfg, mapping)
    disk_state = parse_include(read_trusted(cfg.include_path, cfg, MAX_INCLUDE_BYTES), mapping)
    pending_before = pending_exists(cfg)
    from_state = "unknown" if pending_before else disk_state
    port = mapping[target]
    fields = {"op": "switch", "from": from_state, "to": target,
              "backend": f"{LOOPBACK}:{port}"}

    def outcome(code: int, result: str, **extra: str) -> Outcome:
        return Outcome(code, dict(fields, result=result, **extra))

    # The CURRENT config must validate before anything changes, so the bytes
    # we would restore are a genuinely known-good configuration.
    if not ops.test():
        return outcome(EXIT_CURRENT_INVALID, "current-nginx-invalid", changed="no")

    if target == disk_state:
        if not pending_before:
            return outcome(EXIT_OK, "already-active", changed="no")
        # An interrupted switch left the include at the target but nginx may
        # still run older config: converge by reloading the validated include.
        if not ops.reload():
            return outcome(EXIT_CONVERGENCE_FAILED, "convergence-failed", changed="no")
        if parse_include(read_trusted(cfg.include_path, cfg, MAX_INCLUDE_BYTES), mapping) != target:
            return outcome(EXIT_CONVERGENCE_FAILED, "convergence-failed", changed="unknown")
        if not ops.clear_pending():
            return outcome(EXIT_CONVERGENCE_FAILED, "convergence-failed", changed="no", pending="kept")
        return outcome(EXIT_OK, "converged", changed="no")

    previous = render_include(mapping[disk_state])
    candidate = render_include(port)
    try:
        ops.mark_pending(disk_state, target)
    except OSError as exc:
        ops.notes.append(f"pending marker: {exc.strerror or exc}")
        return outcome(EXIT_IO, "io-error", changed="no")

    def restore() -> bool:
        try:
            ops.write_include(previous)
            return True
        except OSError as exc:
            ops.notes.append(f"restore include: {exc.strerror or exc}")
            return False

    try:
        ops.write_include(candidate)
    except OSError as exc:
        ops.notes.append(f"write include: {exc.strerror or exc}")
        # Usually the rename never happened (e.g. ENOSPC on the temp file) and
        # the include is untouched. atomic_write can also fail after its rename
        # (directory fsync): then put the previous bytes back before reporting.
        try:
            on_disk = read_trusted(cfg.include_path, cfg, MAX_INCLUDE_BYTES)
        except ControlError:
            on_disk = None
        if on_disk != previous and not restore():
            return outcome(EXIT_CONVERGENCE_FAILED, "convergence-failed", restored="no", reload="not-attempted")
        if not pending_before:
            ops.clear_pending()
        return outcome(EXIT_IO, "io-error", changed="no", restored="yes", reload="not-attempted")

    if not ops.test():
        if not restore():
            return outcome(EXIT_CONVERGENCE_FAILED, "convergence-failed", restored="no", reload="not-attempted")
        restored_valid = ops.test()
        if restored_valid and not pending_before:
            ops.clear_pending()
        code = EXIT_CANDIDATE_INVALID if restored_valid else EXIT_CONVERGENCE_FAILED
        return outcome(code, "candidate-nginx-invalid" if restored_valid else "convergence-failed",
                       restored="yes", restored_config_valid="yes" if restored_valid else "no",
                       reload="not-attempted")

    if not ops.reload():
        # A refused reload leaves the running master on its old config (or its
        # state is unknown): put the include back to the known-good backend
        # and make one bounded attempt to load exactly that.
        if not restore():
            return outcome(EXIT_CONVERGENCE_FAILED, "convergence-failed", restored="no",
                           known_good_reload="not-attempted")
        restored_valid = ops.test()
        reloaded = restored_valid and ops.reload()
        if reloaded and ops.clear_pending():
            return outcome(EXIT_RELOAD_FAILED, "reload-failed", restored="yes",
                           restored_config_valid="yes", known_good_reload="yes")
        return outcome(EXIT_CONVERGENCE_FAILED, "convergence-failed", restored="yes",
                       restored_config_valid="yes" if restored_valid else "no",
                       known_good_reload="yes" if reloaded else "no")

    try:
        now = parse_include(read_trusted(cfg.include_path, cfg, MAX_INCLUDE_BYTES), mapping)
    except ControlError:
        now = "unknown"
    if now != target:
        return outcome(EXIT_CONVERGENCE_FAILED, "convergence-failed", readback=now)
    if not ops.clear_pending():
        return outcome(EXIT_CONVERGENCE_FAILED, "convergence-failed", pending="kept")
    return outcome(EXIT_OK, "switched", changed="yes")


def parse_argv(argv: Sequence[str]) -> Tuple[str, Optional[str]]:
    """``status`` or ``switch <state>``; exact strings only."""
    argv = list(argv)
    if argv == ["status"]:
        return "status", None
    if len(argv) == 2 and argv[0] == "switch" and argv[1] in STATES:
        return "switch", argv[1]
    raise ControlError(EXIT_USAGE, "usage", f"usage: {PROG} status | {PROG} switch legacy|blue|green")


def run(argv: Sequence[str], cfg: Optional[Config] = None, deps: Optional[Deps] = None) -> Outcome:
    cfg = cfg or Config()
    deps = deps or Deps()
    ops = _Ops(cfg, deps)
    op = "invalid"
    lock = None
    try:
        # Argument validation happens before ANY file, lock or process access.
        op, target = parse_argv(argv)
        if os.geteuid() != cfg.trusted_uid:
            raise ControlError(EXIT_NOT_ROOT, "not-root", "must run as root")
        lock = deps.lock(cfg.lock_path, cfg)
        if op == "status":
            outcome = do_status(cfg)
        else:
            outcome = do_switch(target, cfg, deps, ops)
    except ControlError as exc:
        outcome = Outcome(exc.code, {"op": op, "result": exc.result})
        ops.notes.append(str(exc))
    except OSError as exc:
        outcome = Outcome(EXIT_IO, {"op": op, "result": "io-error"})
        ops.notes.append(f"{type(exc).__name__}: {exc.strerror or exc}")
    finally:
        if isinstance(lock, int):
            os.close(lock)
    if outcome.code == EXIT_CONVERGENCE_FAILED:
        ops.notes.append(_manual(outcome.fields.get("from", "unknown")))
    outcome.notes = ops.notes
    return outcome


def main(argv: Sequence[str], cfg: Optional[Config] = None, deps: Optional[Deps] = None) -> int:
    os.umask(0o022)
    deps = deps or Deps()
    outcome = run(argv, cfg, deps)
    out = deps.out or (lambda s: sys.stdout.write(s + "\n"))
    err = deps.err or (lambda s: sys.stderr.write(s + "\n"))
    out(outcome.line())
    for note in outcome.notes:
        err(f"{PROG}: {note}")
    return outcome.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
