#!/usr/bin/python3 -I
"""Root-owned nginx web-slot switch primitive (R6-01 / P2).

Installed as ``/usr/local/sbin/axisai-switch-web-slot`` (root:root 0755).

    axisai-switch-web-slot blue|green

The caller supplies a slot NAME and nothing else. The backend is resolved
through the root-owned mapping ``/etc/axisai/web-slots.conf``; the only file
this command writes is the active upstream include
``/etc/nginx/axisai/active-web-upstream.conf`` (plus its ``.prev``
last-known-good copy). Paths and commands are fixed constants: no flag,
environment variable or argument can redirect them.

Sequence (fail closed): validate argv -> parse+validate mapping -> render
include -> save last-known-good -> atomic rename -> ``nginx -t`` (restore and
exit non-zero on failure) -> ``systemctl reload nginx`` (restore known-good
and exit non-zero on failure) -> one audit line.

Security note: while the deploy user keeps passwordless sudo this command is
defense-in-depth (it narrows what the deploy transaction itself can do), not
a hard privilege boundary.
"""
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Dict, Sequence

PROG = "axisai-switch-web-slot"

SLOTS = ("blue", "green")
MAPPING_PATH = "/etc/axisai/web-slots.conf"
INCLUDE_PATH = "/etc/nginx/axisai/active-web-upstream.conf"
LOCK_PATH = "/run/axisai-switch-web-slot.lock"
NGINX_TEST = ("/usr/sbin/nginx", "-t", "-q")
NGINX_RELOAD = ("/usr/bin/systemctl", "reload", "nginx")
COMMAND_TIMEOUT_SECONDS = 60

EXIT_OK = 0
EXIT_USAGE = 64
EXIT_CONFIG = 78
EXIT_NGINX_TEST_FAILED = 2
EXIT_RELOAD_FAILED = 3

_MAPPING_LINE = re.compile(r"(blue|green) 127\.0\.0\.1:([0-9]{4,5})")
_INCLUDE_LINE = "server 127.0.0.1:{port};\n"
_MAX_MAPPING_BYTES = 4096
_MAX_INCLUDE_BYTES = 4096


class SwitchError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Config:
    mapping_path: str = MAPPING_PATH
    include_path: str = INCLUDE_PATH
    lock_path: str = LOCK_PATH
    trusted_uid: int = 0


def parse_slot(argv: Sequence[str]) -> str:
    """Exactly one argument, exactly ``blue`` or ``green`` -- nothing else."""
    if len(argv) != 1 or argv[0] not in SLOTS:
        raise SwitchError(EXIT_USAGE, f"usage: {PROG} blue|green")
    return argv[0]


def _read_trusted(path: str, trusted_uid: int, limit: int) -> bytes:
    """Read a regular, non-symlink file owned by ``trusted_uid`` that neither
    group nor others can write, inside a directory with the same properties."""
    parent = os.path.dirname(path)
    parent_st = os.stat(parent, follow_symlinks=False)
    if (not stat.S_ISDIR(parent_st.st_mode) or parent_st.st_uid != trusted_uid
            or parent_st.st_mode & 0o022):
        raise SwitchError(EXIT_CONFIG, f"untrusted directory: {parent}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != trusted_uid or st.st_mode & 0o022
                or st.st_nlink != 1):
            raise SwitchError(EXIT_CONFIG, f"untrusted file: {path}")
        data = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise SwitchError(EXIT_CONFIG, f"file too large: {path}")
    return data


def parse_mapping(data: bytes) -> Dict[str, int]:
    """``<slot> 127.0.0.1:<port>`` lines; comments/blank lines ignored.

    Both slots exactly once, loopback only, unprivileged distinct ports."""
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise SwitchError(EXIT_CONFIG, "mapping is not ASCII")
    mapping: Dict[str, int] = {}
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _MAPPING_LINE.fullmatch(line)
        if not match:
            raise SwitchError(EXIT_CONFIG, "invalid mapping line")
        slot, port = match.group(1), int(match.group(2))
        if slot in mapping:
            raise SwitchError(EXIT_CONFIG, f"duplicate mapping for {slot}")
        if not 1024 <= port <= 65535:
            raise SwitchError(EXIT_CONFIG, f"port out of range for {slot}")
        mapping[slot] = port
    if set(mapping) != set(SLOTS):
        raise SwitchError(EXIT_CONFIG, "mapping must define blue and green")
    if mapping["blue"] == mapping["green"]:
        raise SwitchError(EXIT_CONFIG, "blue and green must use distinct ports")
    return mapping


def render_include(port: int) -> bytes:
    return _INCLUDE_LINE.format(port=port).encode("ascii")


def _atomic_write(path: str, data: bytes) -> None:
    """Same-directory temp file + fsync + rename + directory fsync. Readers
    (nginx -t / reload) only ever see the complete old or complete new file."""
    directory = os.path.dirname(path)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
    try:
        os.fchmod(fd, 0o644)  # independent of the caller's umask
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        os.unlink(tmp)
        raise
    os.close(fd)
    try:
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _run(argv: Sequence[str]) -> int:
    # Fixed argv, no shell, minimal fixed environment.
    return subprocess.run(
        list(argv), shell=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        timeout=COMMAND_TIMEOUT_SECONDS, check=False,
    ).returncode


@dataclass
class Deps:
    run: Callable[[Sequence[str]], int] = _run
    write: Callable[[str, bytes], None] = _atomic_write
    emit: Callable[[str], None] = print
    lock: Callable[[str], object] = None  # type: ignore[assignment]


def _flock(path: str):
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise SwitchError(EXIT_CONFIG, "another switch is in progress")
    return fd


def _run_ok(deps: Deps, argv: Sequence[str]) -> bool:
    try:
        return deps.run(argv) == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _restore(deps: Deps, cfg: Config, previous: bytes, context: str) -> None:
    try:
        deps.write(cfg.include_path, previous)
    except OSError as exc:
        raise SwitchError(
            EXIT_CONFIG,
            f"{context} AND restoring the previous include failed ({exc.strerror or exc}); "
            f"MANUAL ACTION: install {cfg.include_path}.prev as {cfg.include_path}, nginx -t, reload",
        )


def switch(slot: str, cfg: Config, deps: Deps) -> int:
    mapping = parse_mapping(_read_trusted(cfg.mapping_path, cfg.trusted_uid, _MAX_MAPPING_BYTES))
    port = mapping[slot]
    backend = f"127.0.0.1:{port}"
    candidate = render_include(port)
    # Last-known-good = what nginx validated last. Read it before changing
    # anything; it must itself be trusted (root-owned include directory).
    previous = _read_trusted(cfg.include_path, cfg.trusted_uid, _MAX_INCLUDE_BYTES)

    if previous == candidate:
        if not _run_ok(deps, NGINX_TEST):
            raise SwitchError(EXIT_NGINX_TEST_FAILED, f"slot={slot} backend={backend} result=nginx-test-failed (no change made)")
        deps.emit(f"{PROG}: slot={slot} backend={backend} result=already-active")
        return EXIT_OK

    deps.write(cfg.include_path + ".prev", previous)
    deps.write(cfg.include_path, candidate)

    if not _run_ok(deps, NGINX_TEST):
        _restore(deps, cfg, previous, f"slot={slot} nginx -t failed")
        restored_ok = _run_ok(deps, NGINX_TEST)
        raise SwitchError(
            EXIT_NGINX_TEST_FAILED,
            f"slot={slot} backend={backend} result=nginx-test-failed "
            f"restored=previous restored_config_valid={'yes' if restored_ok else 'NO'} reload=not-attempted",
        )

    if not _run_ok(deps, NGINX_RELOAD):
        # The running master keeps its loaded config when a reload is refused,
        # so put the on-disk include back to the known-good backend and make the
        # running state converge on it again.
        _restore(deps, cfg, previous, f"slot={slot} reload failed")
        restored_ok = _run_ok(deps, NGINX_TEST)
        reloaded_ok = restored_ok and _run_ok(deps, NGINX_RELOAD)
        raise SwitchError(
            EXIT_RELOAD_FAILED,
            f"slot={slot} backend={backend} result=reload-failed restored=previous "
            f"restored_config_valid={'yes' if restored_ok else 'NO'} "
            f"known_good_reload={'yes' if reloaded_ok else 'NO'}",
        )

    deps.emit(f"{PROG}: slot={slot} backend={backend} result=switched")
    return EXIT_OK


def main(argv: Sequence[str], cfg: Config = Config(), deps: Deps = None) -> int:
    deps = deps or Deps()
    try:
        # Argument validation happens before ANY file or lock access, so a
        # rejected invocation cannot touch nginx state.
        slot = parse_slot(argv)
        if os.geteuid() != cfg.trusted_uid:
            raise SwitchError(EXIT_USAGE, "must run as root")
        lock = (deps.lock or _flock)(cfg.lock_path)
        try:
            return switch(slot, cfg, deps)
        finally:
            if isinstance(lock, int):
                os.close(lock)
    except SwitchError as exc:
        sys.stderr.write(f"{PROG}: ERROR: {exc}\n")
        return exc.code
    except OSError as exc:
        sys.stderr.write(f"{PROG}: ERROR: {type(exc).__name__}: {exc.strerror or exc}\n")
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
