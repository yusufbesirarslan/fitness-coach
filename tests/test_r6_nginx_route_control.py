"""R6-02A: root-owned nginx route switch helper (hermetic).

The privileged commands (nginx -t, reload) are injected; trust checks, the
lock, the pending marker and the atomic rename run for real against a
temporary tree owned by the test user (trusted_uid = current uid, trust_root =
the tree). The real-nginx proof lives in tests/test_r6_nginx_integration.py.

    python -m pytest tests/test_r6_nginx_route_control.py -v
"""
import ast
import dataclasses
import errno
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "geteuid") or sys.platform == "win32",
    reason="the route helper is a Linux-only root command",
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import axisai_nginx_bootstrap as boot  # noqa: E402

sw = boot.sw   # the exact helper module the bootstrap loads and installs

SCRIPT = ROOT / "scripts" / "axisai_switch_web_slot.py"
LIVE_SITE = ROOT / "tests" / "fixtures" / "r6_nginx" / "fitx.live-2026-10-08.conf"
MAPPING = (ROOT / "deploy" / "nginx" / "web-slots.conf").read_bytes()
INCLUDES = {"legacy": b"server 127.0.0.1:5000;\n",
            "blue": b"server 127.0.0.1:5001;\n",
            "green": b"server 127.0.0.1:5002;\n"}
TEST = ("nginx", "-t")
RELOAD = ("nginx", "-s", "reload")


class Runner:
    """Fake privileged runner: records argv, returns scripted exit codes per
    command kind (a list is consumed one call at a time)."""

    def __init__(self, test=0, reload=0):
        self.calls = []
        self.script = {TEST: test, RELOAD: reload}

    def __call__(self, argv):
        argv = tuple(argv)
        self.calls.append(argv)
        outcome = self.script[argv]
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if outcome else 0
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome, "nginx: [emerg] simulated failure in /etc/nginx/x.conf:1\n"

    def kinds(self):
        return ["test" if c == TEST else "reload" for c in self.calls]


def _mkdir(path):
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o755)
    return path


def _write(path, data, mode=0o644):
    path.write_bytes(data)
    path.chmod(mode)
    return path


@pytest.fixture
def tree(tmp_path):
    tmp_path.chmod(0o755)
    etc_axisai = _mkdir(tmp_path / "etc" / "axisai")
    nginx_axisai = _mkdir(tmp_path / "etc" / "nginx" / "axisai")
    sites = _mkdir(tmp_path / "etc" / "nginx" / "sites-available")
    run = _mkdir(tmp_path / "run")
    cfg = sw.Config(
        mapping_path=str(etc_axisai / "web-slots.conf"),
        include_path=str(nginx_axisai / "active-web-upstream.conf"),
        site_path=str(sites / "fitx"),
        lock_path=str(run / "axisai-web-route.lock"),
        pending_path=str(run / "axisai-web-route.pending"),
        nginx_test=TEST, nginx_reload=RELOAD,
        trusted_uid=os.geteuid(), trust_root=str(tmp_path),
    )
    _write(Path(cfg.mapping_path), MAPPING)
    _write(Path(cfg.include_path), INCLUDES["legacy"])
    migrated = boot.transform(LIVE_SITE.read_bytes(), cfg.include_path, 5000)
    _write(Path(cfg.site_path), migrated)
    return cfg


def invoke(cfg, *argv, runner=None, write=None, lock=None):
    runner = runner or Runner()
    out, err = [], []
    deps = sw.Deps(run=runner, out=out.append, err=err.append)
    if write is not None:
        deps.write = write
    if lock is not None:
        deps.lock = lock
    code = sw.main(list(argv), cfg, deps)
    assert len(out) == 1, out
    fields = dict(kv.split("=", 1) for kv in out[0].split(" "))
    return code, fields, runner, "\n".join(err)


def include(cfg):
    return Path(cfg.include_path).read_bytes()


def snapshot(root):
    """(relative path, bytes, inode) of every file under the tree."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            out[str(path.relative_to(root))] = (path.read_bytes(), path.stat().st_ino)
    return out


def set_state(cfg, state):
    _write(Path(cfg.include_path), INCLUDES[state])


# --- 1. argument surface -------------------------------------------------------

@pytest.mark.parametrize("argv", [
    [], ["switch"], ["status", "extra"], ["switch", "blue", "extra"],
    ["switch", "BLUE"], ["switch", "Blue"], ["switch", "5001"], ["switch", "127.0.0.1:5001"],
    ["switch", "blue;id"], ["switch", "$(id)"], ["switch", " blue"], ["switch", "blue\n"],
    ["switch", "/etc/passwd"], ["switch", "restore-previous"], ["switch", "rollback"],
    ["switch", "--target=blue"], ["--config", "/tmp/x", "status"], ["STATUS"], ["switch", ""],
    ["switch", "bluEİ"], ["switch", "unknown"],
])
def test_rejects_everything_but_the_symbolic_surface_before_any_access(tree, argv):
    before = snapshot(Path(tree.trust_root))
    locks = []
    code, fields, runner, _err = invoke(tree, *argv, lock=lambda p, c: locks.append(p))
    assert code == sw.EXIT_USAGE
    assert fields == {"op": "invalid", "result": "usage"}
    assert runner.calls == [] and locks == []
    assert snapshot(Path(tree.trust_root)) == before


def test_rejected_input_is_never_echoed(tree):
    marker = "ZZ-attacker-marker-ZZ"
    out, err = [], []
    sw.main(["switch", marker], tree, sw.Deps(run=Runner(), out=out.append, err=err.append))
    assert marker not in "".join(out + err)


@pytest.mark.parametrize("argv", [["status"], ["switch", "legacy"], ["switch", "blue"], ["switch", "green"]])
def test_accepts_exactly_the_documented_operations(tree, argv):
    code, fields, _runner, _err = invoke(tree, *argv)
    assert code == sw.EXIT_OK, fields


def test_requires_root(tree):
    cfg = dataclasses.replace(tree, trusted_uid=os.geteuid() + 1)
    code, fields, runner, _err = invoke(cfg, "switch", "blue")
    assert (code, fields["result"], runner.calls) == (sw.EXIT_NOT_ROOT, "not-root", [])


# --- 2. trusted mapping ----------------------------------------------------------

BAD_MAPPINGS = {
    "non-loopback": MAPPING.replace(b"blue 127.0.0.1:5001", b"blue 10.0.0.5:5001"),
    "hostname": MAPPING.replace(b"blue 127.0.0.1:5001", b"blue localhost:5001"),
    "ipv6": MAPPING.replace(b"blue 127.0.0.1:5001", b"blue [::1]:5001"),
    "duplicate": MAPPING + b"blue 127.0.0.1:5003\n",
    "missing-legacy": MAPPING.replace(b"legacy 127.0.0.1:5000\n", b""),
    "missing-green": MAPPING.replace(b"green 127.0.0.1:5002\n", b""),
    "same-port": MAPPING.replace(b"green 127.0.0.1:5002", b"green 127.0.0.1:5001"),
    "unknown-key": MAPPING + b"purple 127.0.0.1:5004\n",
    "extra-token": MAPPING.replace(b"blue 127.0.0.1:5001", b"blue 127.0.0.1:5001 backup"),
    "nginx-semicolon": MAPPING.replace(b"blue 127.0.0.1:5001", b"blue 127.0.0.1:5001;"),
    "nginx-directive": MAPPING.replace(b"blue 127.0.0.1:5001", b"blue 127.0.0.1:5001; server 10.0.0.1:80"),
    "non-ascii": MAPPING.replace(b"# AxisAI", "# AxısAI".encode()),
    "crlf": MAPPING.replace(b"\n", b"\r\n"),
    "leading-space": MAPPING.replace(b"blue 127.0.0.1:5001", b" blue 127.0.0.1:5001"),
    "tab": MAPPING.replace(b"blue 127.0.0.1:5001", b"blue\t127.0.0.1:5001"),
    "uppercase": MAPPING.replace(b"blue 127.0.0.1:5001", b"BLUE 127.0.0.1:5001"),
    "privileged-port": MAPPING.replace(b"blue 127.0.0.1:5001", b"blue 127.0.0.1:1000"),
    "port-too-big": MAPPING.replace(b"blue 127.0.0.1:5001", b"blue 127.0.0.1:70000"),
    "leading-zero": MAPPING.replace(b"blue 127.0.0.1:5001", b"blue 127.0.0.1:05001"),
    "no-final-newline": MAPPING.rstrip(b"\n"),
    "empty": b"",
    "shell": MAPPING + b"$(touch /tmp/pwned)\n",
}


@pytest.mark.parametrize("name", sorted(BAD_MAPPINGS))
@pytest.mark.parametrize("argv", [["status"], ["switch", "blue"]])
def test_invalid_mapping_fails_closed_without_nginx_or_writes(tree, name, argv):
    _write(Path(tree.mapping_path), BAD_MAPPINGS[name])
    before = include(tree)
    code, fields, runner, _err = invoke(tree, *argv)
    assert (code, fields["result"]) == (sw.EXIT_UNTRUSTED, "untrusted-config")
    assert runner.calls == []
    assert include(tree) == before
    assert not os.path.lexists(tree.pending_path)


def test_oversize_mapping_rejected(tree):
    _write(Path(tree.mapping_path), MAPPING + b"#" * sw.MAX_MAPPING_BYTES + b"\n")
    assert invoke(tree, "status")[0] == sw.EXIT_UNTRUSTED


@pytest.mark.parametrize("attr", ["mapping_path", "include_path", "site_path"])
def test_symlinked_authority_file_rejected(tree, attr, tmp_path):
    real = Path(getattr(tree, attr))
    moved = real.with_name(real.name + ".real")
    real.rename(moved)
    real.symlink_to(moved)
    code, fields, runner, _err = invoke(tree, "switch", "blue")
    assert (code, fields["result"], runner.calls) == (sw.EXIT_UNTRUSTED, "untrusted-config", [])


@pytest.mark.parametrize("attr", ["mapping_path", "include_path", "site_path"])
@pytest.mark.parametrize("mode", [0o664, 0o646, 0o666])
def test_group_or_world_writable_authority_file_rejected(tree, attr, mode):
    os.chmod(getattr(tree, attr), mode)
    code, _fields, runner, _err = invoke(tree, "switch", "blue")
    assert (code, runner.calls) == (sw.EXIT_UNTRUSTED, [])


@pytest.mark.parametrize("attr", ["mapping_path", "include_path", "site_path", "lock_path"])
def test_writable_parent_directory_rejected(tree, attr):
    os.chmod(os.path.dirname(getattr(tree, attr)), 0o777)
    code, _fields, runner, _err = invoke(tree, "switch", "blue")
    assert (code, runner.calls) == (sw.EXIT_UNTRUSTED, [])


def test_writable_ancestor_directory_rejected(tree):
    os.chmod(Path(tree.mapping_path).parent.parent, 0o775)
    assert invoke(tree, "status")[0] == sw.EXIT_UNTRUSTED


def test_symlinked_parent_directory_rejected(tree):
    parent = Path(tree.mapping_path).parent
    moved = parent.with_name("axisai.real")
    parent.rename(moved)
    parent.symlink_to(moved, target_is_directory=True)
    assert invoke(tree, "status")[0] == sw.EXIT_UNTRUSTED


def test_hard_linked_authority_file_rejected(tree):
    os.link(tree.mapping_path, Path(tree.mapping_path).with_name("alias"))
    assert invoke(tree, "status")[0] == sw.EXIT_UNTRUSTED


def test_fifo_authority_file_rejected_without_blocking(tree):
    os.unlink(tree.mapping_path)
    os.mkfifo(tree.mapping_path, 0o644)
    assert invoke(tree, "status")[0] == sw.EXIT_UNTRUSTED


def test_wrong_owner_rejected(tree, monkeypatch):
    real_fstat = os.fstat

    def foreign(fd):
        st = real_fstat(fd)
        values = list(st)
        values[stat.ST_UID] = st.st_uid + 1
        return os.stat_result(values)

    monkeypatch.setattr(sw.os, "fstat", foreign)
    code, _fields, runner, _err = invoke(tree, "switch", "blue")
    assert (code, runner.calls) == (sw.EXIT_UNTRUSTED, [])


def test_missing_mapping_rejected(tree):
    os.unlink(tree.mapping_path)
    assert invoke(tree, "switch", "blue")[0] == sw.EXIT_UNTRUSTED


# --- 3. current-config preflight ----------------------------------------------------

@pytest.mark.parametrize("failure", [1, subprocess.TimeoutExpired("nginx", 30), FileNotFoundError("nginx")])
def test_invalid_current_config_blocks_any_mutation(tree, failure):
    before = snapshot(Path(tree.trust_root))
    code, fields, runner, _err = invoke(tree, "switch", "blue", runner=Runner(test=failure))
    assert (code, fields["result"], fields["changed"]) == (sw.EXIT_CURRENT_INVALID, "current-nginx-invalid", "no")
    assert runner.kinds() == ["test"]
    after = snapshot(Path(tree.trust_root))
    after.pop("run/axisai-web-route.lock", None)
    assert after == before


# --- 4. atomic write --------------------------------------------------------------------

def test_switch_renames_a_fsynced_same_directory_file_without_truncation(tree, monkeypatch):
    old_inode = os.stat(tree.include_path).st_ino
    old_fd = os.open(tree.include_path, os.O_RDONLY)
    synced, renames = [], []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(sw.os, "fsync", lambda fd: synced.append(os.readlink(f"/proc/self/fd/{fd}")) or real_fsync(fd))
    monkeypatch.setattr(sw.os, "replace", lambda a, b: renames.append((a, b)) or real_replace(a, b))
    try:
        code, fields, _runner, _err = invoke(tree, "switch", "blue")
        assert code == sw.EXIT_OK and fields["result"] == "switched"
        assert os.pread(old_fd, 100, 0) == INCLUDES["legacy"]   # old inode untouched
    finally:
        os.close(old_fd)
    assert os.stat(tree.include_path).st_ino != old_inode
    assert include(tree) == INCLUDES["blue"]
    include_dir = os.path.dirname(tree.include_path)
    include_renames = [r for r in renames if r[1] == tree.include_path]
    assert len(include_renames) == 1 and os.path.dirname(include_renames[0][0]) == include_dir
    assert include_dir in synced                                # directory fsync
    assert any(p.startswith(include_dir + "/.active-web-upstream.conf.") for p in synced)  # file fsync
    assert sorted(os.listdir(include_dir)) == ["active-web-upstream.conf"]
    assert stat.S_IMODE(os.stat(tree.include_path).st_mode) == 0o644


def test_failed_temp_write_leaves_no_leftover_and_no_change(tree, monkeypatch):
    real_write = os.write

    def enospc(fd, data):
        if bytes(data).startswith(b"server "):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(fd, data)

    monkeypatch.setattr(sw.os, "write", enospc)
    code, fields, runner, _err = invoke(tree, "switch", "blue")
    assert (code, fields["result"], fields["changed"]) == (sw.EXIT_IO, "io-error", "no")
    assert runner.kinds() == ["test"]
    assert include(tree) == INCLUDES["legacy"]
    assert sorted(os.listdir(os.path.dirname(tree.include_path))) == ["active-web-upstream.conf"]
    assert not os.path.lexists(tree.pending_path)


def test_rename_failure_after_write_restores_previous(tree):
    def write(path, data, mode=0o644):
        sw.atomic_write(path, data, mode)
        if path == tree.include_path and data == INCLUDES["blue"]:
            raise OSError(errno.EIO, "directory fsync failed")

    code, fields, runner, _err = invoke(tree, "switch", "blue", write=write)
    assert (code, fields["restored"]) == (sw.EXIT_IO, "yes")
    assert runner.kinds() == ["test"]
    assert include(tree) == INCLUDES["legacy"]


# --- 5. candidate nginx -t failure ----------------------------------------------------------

def test_candidate_rejection_restores_previous_and_never_reloads(tree):
    code, fields, runner, err = invoke(tree, "switch", "blue", runner=Runner(test=[0, 1, 0]))
    assert code == sw.EXIT_CANDIDATE_INVALID
    assert fields == {"op": "switch", "from": "legacy", "to": "blue", "backend": "127.0.0.1:5001",
                      "result": "candidate-nginx-invalid", "restored": "yes",
                      "restored_config_valid": "yes", "reload": "not-attempted"}
    assert runner.kinds() == ["test", "test", "test"]
    assert include(tree) == INCLUDES["legacy"]
    assert not os.path.lexists(tree.pending_path)
    assert invoke(tree, "status")[1]["state"] == "legacy"
    assert "simulated failure" in err


def test_candidate_rejection_with_invalid_restore_demands_manual_action(tree):
    code, fields, runner, err = invoke(tree, "switch", "blue", runner=Runner(test=[0, 1, 1]))
    assert (code, fields["result"], fields["restored_config_valid"]) == (
        sw.EXIT_CONVERGENCE_FAILED, "convergence-failed", "no")
    assert "reload" not in runner.kinds()
    assert include(tree) == INCLUDES["legacy"]
    assert "MANUAL ACTION" in err and "switch legacy" in err


def test_candidate_timeout_is_a_rejection(tree):
    runner = Runner(test=[0, subprocess.TimeoutExpired("nginx", 30), 0])
    code, fields, runner, err = invoke(tree, "switch", "blue", runner=runner)
    assert code == sw.EXIT_CANDIDATE_INVALID and "timed out" in err
    assert include(tree) == INCLUDES["legacy"]


def test_restore_write_failure_demands_manual_action(tree):
    calls = []

    def write(path, data, mode=0o644):
        calls.append((path, data))
        if path == tree.include_path and data == INCLUDES["legacy"]:
            raise OSError(errno.EROFS, "read-only file system")
        sw.atomic_write(path, data, mode)

    code, fields, runner, err = invoke(tree, "switch", "blue", runner=Runner(test=[0, 1]), write=write)
    assert (code, fields["restored"]) == (sw.EXIT_CONVERGENCE_FAILED, "no")
    assert "reload" not in runner.kinds()
    assert "MANUAL ACTION" in err
    assert os.path.lexists(tree.pending_path)                      # evidence kept
    assert Path(tree.pending_path).read_bytes() == b"from=legacy to=blue\n"
    assert invoke(tree, "status")[1]["result"] == "state-unknown"


# --- 6. reload failure ------------------------------------------------------------------------------

def test_reload_failure_restores_and_reconverges_once(tree):
    code, fields, runner, _err = invoke(tree, "switch", "blue", runner=Runner(reload=[1, 0]))
    assert code == sw.EXIT_RELOAD_FAILED
    assert (fields["result"], fields["restored"], fields["restored_config_valid"],
            fields["known_good_reload"]) == ("reload-failed", "yes", "yes", "yes")
    assert runner.kinds() == ["test", "test", "reload", "test", "reload"]
    assert include(tree) == INCLUDES["legacy"]
    assert not os.path.lexists(tree.pending_path)
    assert invoke(tree, "status")[1]["state"] == "legacy"


@pytest.mark.parametrize("script,expected_kinds", [
    (dict(reload=[1, 1]), ["test", "test", "reload", "test", "reload"]),
    (dict(reload=[1], test=[0, 0, 1]), ["test", "test", "reload", "test"]),
])
def test_failed_reconvergence_is_bounded_and_manual(tree, script, expected_kinds):
    code, fields, runner, err = invoke(tree, "switch", "green", runner=Runner(**script))
    assert (code, fields["result"]) == (sw.EXIT_CONVERGENCE_FAILED, "convergence-failed")
    assert runner.kinds() == expected_kinds                         # no retry loop
    assert include(tree) == INCLUDES["legacy"]
    assert "MANUAL ACTION" in err and "switch legacy" in err
    status = invoke(tree, "status")
    assert (status[0], status[1]["result"]) == (sw.EXIT_STATE_UNKNOWN, "state-unknown")


# --- 7. active-state discovery ---------------------------------------------------------------------

@pytest.mark.parametrize("state,port", [("legacy", 5000), ("blue", 5001), ("green", 5002)])
def test_status_reports_each_state(tree, state, port):
    set_state(tree, state)
    code, fields, runner, _err = invoke(tree, "status")
    assert code == sw.EXIT_OK
    assert fields == {"op": "status", "state": state, "backend": f"127.0.0.1:{port}", "result": "ok"}
    assert runner.calls == []                                          # read-only, no nginx


@pytest.mark.parametrize("data", [
    b"", b"server 127.0.0.1:5001;", b"server 127.0.0.1:5003;\n", b"server localhost:5001;\n",
    b"server 127.0.0.1:5001;\nserver 127.0.0.1:5002;\n", b"server 127.0.0.1:5001 backup;\n",
    b"server 127.0.0.1:5001;\r\n", b"# c\nserver 127.0.0.1:5001;\n", b" server 127.0.0.1:5001;\n",
    b"server 127.0.0.1:5001; keepalive 4;\n", b"server 10.0.0.1:5001;\n", b"server unix:/x;\n",
])
@pytest.mark.parametrize("argv", [["status"], ["switch", "green"]])
def test_malformed_or_unmapped_include_fails_closed(tree, data, argv):
    _write(Path(tree.include_path), data)
    code, fields, runner, _err = invoke(tree, *argv)
    assert (code, fields["result"]) == (sw.EXIT_STATE_UNKNOWN, "state-unknown")
    assert runner.calls == []
    assert include(tree) == data


def test_status_is_unknown_while_a_switch_is_pending(tree):
    _write(Path(tree.pending_path), b"from=legacy to=blue\n")
    code, fields, _runner, _err = invoke(tree, "status")
    assert (code, fields) == (sw.EXIT_STATE_UNKNOWN, {"op": "status", "result": "state-unknown"})


@pytest.mark.parametrize("site", ["legacy", "partial", "missing"])
def test_route_state_requires_the_migrated_site(tree, site):
    if site == "missing":
        os.unlink(tree.site_path)
    else:
        data = LIVE_SITE.read_bytes()
        if site == "partial":
            data = data.replace(b"upstream fatsecret_proxy",
                                f"upstream axisai_web {{ include {tree.include_path}; }}\nupstream fatsecret_proxy".encode())
        _write(Path(tree.site_path), data)
    for argv in (["status"], ["switch", "blue"]):
        code, _fields, runner, _err = invoke(tree, *argv)
        assert code in (sw.EXIT_STATE_UNKNOWN, sw.EXIT_UNTRUSTED)
        assert runner.calls == []
    assert include(tree) == INCLUDES["legacy"]


def test_lock_busy_fails_closed(tree):
    import fcntl
    fd = os.open(tree.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        for argv in (["switch", "blue"], ["status"]):
            code, fields, runner, _err = invoke(tree, *argv)
            assert (code, fields["result"], runner.calls) == (sw.EXIT_LOCK_BUSY, "lock-busy", [])
    finally:
        os.close(fd)
    assert include(tree) == INCLUDES["legacy"]


def test_symlinked_lock_rejected(tree, tmp_path):
    os.symlink(tmp_path / "elsewhere", tree.lock_path)
    code, _fields, runner, _err = invoke(tree, "switch", "blue")
    assert (code, runner.calls) == (sw.EXIT_UNTRUSTED, [])
    assert not (tmp_path / "elsewhere").exists()


# --- 8/9. reversible routing ---------------------------------------------------------------------------

def _switch(cfg, target):
    code, fields, runner, _err = invoke(cfg, "switch", target)
    assert code == sw.EXIT_OK, fields
    return fields, runner


def test_first_cutover_rolls_back_to_legacy_without_raw_config(tree):
    original = include(tree)
    assert invoke(tree, "status")[1]["state"] == "legacy"
    fields, runner = _switch(tree, "blue")
    assert (fields["from"], fields["to"], fields["backend"], fields["result"]) == (
        "legacy", "blue", "127.0.0.1:5001", "switched")
    assert runner.kinds() == ["test", "test", "reload"]
    assert invoke(tree, "status")[1]["state"] == "blue"
    # post-switch verification failed in R6-03 -> switch back to what status said
    fields, _runner = _switch(tree, "legacy")
    assert (fields["from"], fields["to"], fields["result"]) == ("blue", "legacy", "switched")
    assert invoke(tree, "status")[1]["state"] == "legacy"
    assert include(tree) == original


def test_slot_to_slot_switch_is_reversible(tree):
    _switch(tree, "blue")
    fields, _ = _switch(tree, "green")
    assert (fields["from"], fields["to"]) == ("blue", "green")
    assert invoke(tree, "status")[1]["state"] == "green"
    fields, _ = _switch(tree, "blue")
    assert (fields["from"], fields["to"]) == ("green", "blue")
    assert include(tree) == INCLUDES["blue"]


def test_switch_to_active_state_is_a_validated_noop(tree):
    inode = os.stat(tree.include_path).st_ino
    fields, runner = _switch(tree, "legacy")
    assert (fields["result"], fields["changed"]) == ("already-active", "no")
    assert runner.kinds() == ["test"]
    assert os.stat(tree.include_path).st_ino == inode


def test_interrupted_switch_is_unknown_until_a_later_switch_succeeds(tree):
    # A switch killed after the rename: include says blue, marker remains.
    set_state(tree, "blue")
    _write(Path(tree.pending_path), b"from=legacy to=blue\n")
    assert invoke(tree, "status")[1]["result"] == "state-unknown"
    fields, runner = _switch(tree, "legacy")        # rollback still possible
    assert (fields["from"], fields["result"]) == ("unknown", "switched")
    assert runner.kinds() == ["test", "test", "reload"]
    assert invoke(tree, "status")[1]["state"] == "legacy"


def test_interrupted_switch_to_the_same_state_converges_by_reload(tree):
    set_state(tree, "green")
    _write(Path(tree.pending_path), b"from=blue to=green\n")
    inode = os.stat(tree.include_path).st_ino
    fields, runner = _switch(tree, "green")
    assert (fields["result"], fields["from"]) == ("converged", "unknown")
    assert runner.kinds() == ["test", "reload"]
    assert os.stat(tree.include_path).st_ino == inode
    assert invoke(tree, "status")[1]["state"] == "green"


def test_candidate_failure_after_interruption_keeps_state_unknown(tree):
    set_state(tree, "blue")
    _write(Path(tree.pending_path), b"from=legacy to=blue\n")
    code, _fields, _runner, _err = invoke(tree, "switch", "legacy", runner=Runner(test=[0, 1, 0]))
    assert code == sw.EXIT_CANDIDATE_INVALID
    assert include(tree) == INCLUDES["blue"]
    assert invoke(tree, "status")[1]["result"] == "state-unknown"


# --- 11. no shell / dynamic execution, fixed argv + environment ------------------------------------

def test_production_constants_are_fixed():
    cfg = sw.Config()
    assert (cfg.mapping_path, cfg.include_path, cfg.site_path, cfg.lock_path, cfg.pending_path) == (
        "/etc/axisai/web-slots.conf", "/etc/nginx/axisai/active-web-upstream.conf",
        "/etc/nginx/sites-available/fitx", "/run/axisai-web-route.lock", "/run/axisai-web-route.pending")
    assert cfg.nginx_test == ("/usr/sbin/nginx", "-t", "-q")
    assert cfg.nginx_reload == ("/usr/bin/systemctl", "reload", "nginx")
    assert (cfg.trusted_uid, cfg.trust_root) == (0, "/")
    assert sw.FIXED_ENV == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
    assert sw.STATES == ("legacy", "blue", "green")


def test_runner_uses_fixed_argv_no_shell_fixed_env_and_timeout(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setenv("PATH", "/tmp/evil")
    monkeypatch.setenv("LD_PRELOAD", "/tmp/evil.so")
    monkeypatch.setattr(sw.subprocess, "run", fake_run)
    assert sw._run(sw.NGINX_RELOAD) == (0, "")
    assert seen["argv"] == ["/usr/bin/systemctl", "reload", "nginx"]
    assert seen["shell"] is False
    assert seen["env"] == sw.FIXED_ENV
    assert seen["timeout"] == sw.COMMAND_TIMEOUT_SECONDS == 30
    assert seen["stdin"] == subprocess.DEVNULL


def test_command_line_cannot_redirect_paths_or_commands(monkeypatch):
    calls = {}
    monkeypatch.setattr(sw, "run", lambda argv, cfg=None, deps=None: calls.update(cfg=cfg) or sw.Outcome(0, {"op": "x"}))
    for var in ("AXISAI_MAPPING", "NGINX", "AXISAI_INCLUDE", "PYTHONPATH"):
        monkeypatch.setenv(var, "/tmp/x")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "status"])
    sw.main(["status"], deps=sw.Deps(out=lambda s: None, err=lambda s: None))
    assert calls["cfg"] is None                   # -> Config() production defaults


def test_source_has_no_shell_or_dynamic_execution():
    for path in (SCRIPT, ROOT / "scripts" / "axisai_nginx_bootstrap.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    assert func.id not in {"eval", "exec", "compile", "__import__"}, (path, func.id)
                elif isinstance(func, ast.Attribute):
                    assert func.attr not in {"system", "popen", "Popen", "execv", "execve", "execvp",
                                             "spawnl", "check_output", "getoutput", "call"}, (path, func.attr)
                for kw in node.keywords:
                    if kw.arg == "shell":
                        assert isinstance(kw.value, ast.Constant) and kw.value.value is False
        assert "os.environ" not in source and "getenv" not in source, path
        assert source.startswith("#!/usr/bin/python3 -I\n"), path


def test_lock_is_not_shared_with_the_deploy_lock():
    deploy = (ROOT / "scripts" / "production_deploy.sh").read_text(encoding="utf-8")
    assert sw.LOCK_PATH not in deploy
    assert "axisai-switch-web-slot" not in deploy
