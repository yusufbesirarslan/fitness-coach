"""R6-01 / P2: root-owned nginx web-slot switch primitive.

The privileged commands (nginx -t, systemctl reload) are injected; file I/O,
trust checks and the atomic rename run for real against a temporary tree that
is owned by the test user (trusted_uid = current uid).

    python -m pytest tests/test_axisai_switch_web_slot.py -v
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "geteuid"),
    reason="the switch helper is a Linux-only root command",
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import axisai_switch_web_slot as sw  # noqa: E402

SCRIPT = Path("scripts/axisai_switch_web_slot.py")
LEGACY = b"server 127.0.0.1:5000;\n"
MAPPING = b"# comment\n\nblue 127.0.0.1:5001\ngreen 127.0.0.1:5002\n"


class Recorder:
    """Fake privileged runner: records argv, returns scripted exit codes."""

    def __init__(self, results=None):
        self.calls = []
        self.results = dict(results or {})

    def __call__(self, argv):
        argv = tuple(argv)
        self.calls.append(argv)
        outcome = self.results.get(argv, 0)
        if isinstance(outcome, list):
            outcome = outcome.pop(0) if outcome else 0
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def tree(tmp_path):
    etc = tmp_path / "etc-axisai"
    inc = tmp_path / "nginx-axisai"
    for d in (etc, inc):
        d.mkdir()
        d.chmod(0o755)
    (etc / "web-slots.conf").write_bytes(MAPPING)
    (inc / "active-web-upstream.conf").write_bytes(LEGACY)
    for f in (etc / "web-slots.conf", inc / "active-web-upstream.conf"):
        f.chmod(0o644)
    cfg = sw.Config(
        mapping_path=str(etc / "web-slots.conf"),
        include_path=str(inc / "active-web-upstream.conf"),
        lock_path=str(tmp_path / "lock"),
        trusted_uid=os.geteuid(),
    )
    return cfg


def _invoke(cfg, argv, runner=None):
    runner = runner or Recorder()
    out = []
    locks = []
    deps = sw.Deps(run=runner, emit=out.append, lock=lambda p: locks.append(p))
    rc = sw.main(argv, cfg, deps)
    return rc, runner, out, locks


def _include(cfg):
    return Path(cfg.include_path).read_bytes()


# --- accepted input ----------------------------------------------------------

@pytest.mark.parametrize("slot,port", [("blue", 5001), ("green", 5002)])
def test_valid_slot_switches_through_validate_then_reload(tree, slot, port):
    rc, runner, out, locks = _invoke(tree, [slot])
    assert rc == 0
    assert _include(tree) == f"server 127.0.0.1:{port};\n".encode()
    assert Path(tree.include_path + ".prev").read_bytes() == LEGACY
    assert runner.calls == [sw.NGINX_TEST, sw.NGINX_RELOAD]
    assert out == [f"axisai-switch-web-slot: slot={slot} backend=127.0.0.1:{port} result=switched"]
    assert locks == [tree.lock_path]
    assert oct(Path(tree.include_path).stat().st_mode & 0o777) == "0o644"


def test_already_active_slot_validates_but_never_rewrites_or_reloads(tree):
    Path(tree.include_path).write_bytes(b"server 127.0.0.1:5002;\n")
    before = Path(tree.include_path).stat()
    rc, runner, out, _ = _invoke(tree, ["green"])
    assert rc == 0
    assert runner.calls == [sw.NGINX_TEST]
    assert Path(tree.include_path).stat().st_ino == before.st_ino
    assert not Path(tree.include_path + ".prev").exists()
    assert out[0].endswith("result=already-active")


# --- rejected input: nothing is touched --------------------------------------

@pytest.mark.parametrize("argv", [
    [], [""], ["5001"], ["localhost:5001"], ["127.0.0.1:5001"], ["/tmp/foo"],
    ["BLUE"], ["Green"], ["green;id"], ["green$(id)"], ["`id`"], ["../blue"],
    ["blue/"], ["blue "], [" blue"], ["blue\n"], ["blue\x00"], ["blue", "extra_argument"],
    ["blue", "green"], ["--help"], ["-h"], ["legacy"], ["red"],
])
def test_invalid_arguments_are_rejected_without_touching_nginx_state(tree, argv, capsys):
    before = Path(tree.include_path).stat()
    rc, runner, out, locks = _invoke(tree, argv)
    assert rc == sw.EXIT_USAGE
    assert runner.calls == [] and out == [] and locks == []
    after = Path(tree.include_path).stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert _include(tree) == LEGACY
    assert sorted(os.listdir(Path(tree.include_path).parent)) == ["active-web-upstream.conf"]
    assert "usage: axisai-switch-web-slot blue|green" in capsys.readouterr().err


# --- trusted mapping ---------------------------------------------------------

@pytest.mark.parametrize("mapping", [
    b"blue 127.0.0.1:5001\n",                                   # green missing
    b"blue 127.0.0.1:5001\nblue 127.0.0.1:5003\ngreen 127.0.0.1:5002\n",  # duplicate
    b"blue 127.0.0.1:5001\ngreen 127.0.0.1:5001\n",             # same port
    b"blue 0.0.0.0:5001\ngreen 127.0.0.1:5002\n",               # not loopback
    b"blue localhost:5001\ngreen 127.0.0.1:5002\n",             # hostname
    b"blue 10.0.0.5:5001\ngreen 127.0.0.1:5002\n",              # remote host
    b"blue 127.0.0.1:80\ngreen 127.0.0.1:5002\n",               # privileged port
    b"blue 127.0.0.1:70000\ngreen 127.0.0.1:5002\n",            # out of range
    b"blue 127.0.0.1:5001\ngreen 127.0.0.1:5002\nred 127.0.0.1:5003\n",  # unknown slot
    b"blue 127.0.0.1:5001;\ngreen 127.0.0.1:5002\n",            # nginx syntax smuggling
    b"blue 127.0.0.1:5001 backup\ngreen 127.0.0.1:5002\n",      # extra token
    b"blue=127.0.0.1:5001\ngreen=127.0.0.1:5002\n",             # shell-style
    b"blue 127.0.0.1:5001\ngreen 127.0.0.1:5002\n\xc4\xb1\n",   # non-ASCII
    b"",
])
def test_invalid_mapping_fails_closed_before_any_change(tree, mapping):
    Path(tree.mapping_path).write_bytes(mapping)
    rc, runner, _, _ = _invoke(tree, ["blue"])
    assert rc == sw.EXIT_CONFIG
    assert runner.calls == []
    assert _include(tree) == LEGACY
    assert not Path(tree.include_path + ".prev").exists()


@pytest.mark.parametrize("target", ["mapping", "include"])
@pytest.mark.parametrize("weaken", ["group_write", "world_write", "dir_world_write", "symlink"])
def test_untrusted_mapping_or_include_is_refused(tree, target, weaken):
    path = Path(tree.mapping_path if target == "mapping" else tree.include_path)
    if weaken == "group_write":
        path.chmod(0o664)
    elif weaken == "world_write":
        path.chmod(0o646)
    elif weaken == "dir_world_write":
        path.parent.chmod(0o777)
    elif weaken == "symlink":
        real = path.with_name("real")
        path.rename(real)
        path.symlink_to(real)
    rc, runner, _, _ = _invoke(tree, ["green"])
    assert rc == sw.EXIT_CONFIG
    assert runner.calls == []
    assert not Path(tree.include_path + ".prev").exists()


@pytest.mark.parametrize("target", ["mapping", "include"])
def test_files_not_owned_by_root_are_refused(tree, target):
    path = tree.mapping_path if target == "mapping" else tree.include_path
    with pytest.raises(sw.SwitchError) as exc:
        sw._read_trusted(path, os.geteuid() + 1, 4096)
    assert exc.value.code == sw.EXIT_CONFIG


def test_mapping_parser_accepts_the_shipped_mapping():
    shipped = Path("deploy/nginx/web-slots.conf").read_bytes()
    assert sw.parse_mapping(shipped) == {"blue": 5001, "green": 5002}


# --- atomic replacement ------------------------------------------------------

def test_include_is_replaced_by_same_directory_rename_never_rewritten_in_place(tree, monkeypatch):
    renames = []
    real_replace = os.replace

    def spy(src, dst):
        renames.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(sw.os, "replace", spy)
    inode_before = Path(tree.include_path).stat().st_ino
    rc, _, _, _ = _invoke(tree, ["blue"])
    assert rc == 0
    targets = [dst for _, dst in renames]
    assert targets == [tree.include_path + ".prev", tree.include_path]
    for src, dst in renames:
        assert os.path.dirname(src) == os.path.dirname(dst)
    assert Path(tree.include_path).stat().st_ino != inode_before
    leftovers = [n for n in os.listdir(Path(tree.include_path).parent) if n.endswith(".tmp")]
    assert leftovers == []


def test_failed_rename_leaves_previous_include_and_no_temp_file(tree, monkeypatch):
    real_replace = os.replace

    def failing(src, dst):
        if dst == tree.include_path:
            raise OSError(28, "No space left on device")
        return real_replace(src, dst)

    monkeypatch.setattr(sw.os, "replace", failing)
    rc, runner, _, _ = _invoke(tree, ["blue"])
    assert rc == sw.EXIT_CONFIG
    assert runner.calls == []
    assert _include(tree) == LEGACY
    assert [n for n in os.listdir(Path(tree.include_path).parent) if n.endswith(".tmp")] == []


# --- fail-closed validation / reload -----------------------------------------

def test_nginx_test_failure_restores_previous_include_and_never_reloads(tree, capsys):
    runner = Recorder({sw.NGINX_TEST: [1, 0]})
    rc, runner, out, _ = _invoke(tree, ["blue"], runner)
    assert rc == sw.EXIT_NGINX_TEST_FAILED
    assert _include(tree) == LEGACY
    assert sw.NGINX_RELOAD not in runner.calls
    assert runner.calls == [sw.NGINX_TEST, sw.NGINX_TEST]
    assert out == []
    err = capsys.readouterr().err
    assert "result=nginx-test-failed" in err and "restored_config_valid=yes" in err


def test_nginx_test_timeout_is_a_failure(tree):
    runner = Recorder({sw.NGINX_TEST: [subprocess.TimeoutExpired("nginx", 60), 0]})
    rc, runner, _, _ = _invoke(tree, ["green"], runner)
    assert rc == sw.EXIT_NGINX_TEST_FAILED
    assert _include(tree) == LEGACY
    assert sw.NGINX_RELOAD not in runner.calls


def test_reload_failure_restores_known_good_and_reconverges_running_nginx(tree, capsys):
    runner = Recorder({sw.NGINX_RELOAD: [1, 0]})
    rc, runner, out, _ = _invoke(tree, ["blue"], runner)
    assert rc == sw.EXIT_RELOAD_FAILED
    assert _include(tree) == LEGACY
    assert runner.calls == [sw.NGINX_TEST, sw.NGINX_RELOAD, sw.NGINX_TEST, sw.NGINX_RELOAD]
    assert out == []
    err = capsys.readouterr().err
    assert "result=reload-failed" in err and "known_good_reload=yes" in err


def test_reload_failure_with_unreloadable_nginx_still_leaves_known_good_on_disk(tree, capsys):
    runner = Recorder({sw.NGINX_RELOAD: [1, 1]})
    rc, _, _, _ = _invoke(tree, ["blue"], runner)
    assert rc == sw.EXIT_RELOAD_FAILED
    assert _include(tree) == LEGACY
    assert "known_good_reload=NO" in capsys.readouterr().err


def test_failed_restore_is_reported_as_manual_action(tree, monkeypatch, capsys):
    real_write = sw._atomic_write
    writes = []

    def write(path, data):
        writes.append(path)
        if len(writes) == 3:  # .prev, candidate, then the restore
            raise OSError(5, "Input/output error")
        real_write(path, data)

    runner = Recorder({sw.NGINX_TEST: [1]})
    deps = sw.Deps(run=runner, write=write, emit=lambda m: None, lock=lambda p: None)
    rc = sw.main(["blue"], tree, deps)
    assert rc == sw.EXIT_CONFIG
    err = capsys.readouterr().err
    assert "MANUAL ACTION" in err and ".prev" in err
    assert Path(tree.include_path + ".prev").read_bytes() == LEGACY


# --- privileged command surface ----------------------------------------------

def test_privileged_commands_are_fixed_argv_without_shell(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(sw.subprocess, "run", fake_run)
    assert sw._run(sw.NGINX_TEST) == 0
    assert seen["argv"] == ["/usr/sbin/nginx", "-t", "-q"]
    assert seen["shell"] is False
    assert seen["env"] == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
    assert sw.NGINX_RELOAD == ("/usr/bin/systemctl", "reload", "nginx")


def test_source_has_no_dynamic_execution_or_environment_overrides():
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("eval(", "exec(", "shell=True", "os.system", "os.popen",
                      "os.environ", "getenv", "argparse"):
        assert forbidden not in source, forbidden
    assert source.startswith("#!/usr/bin/python3 -I\n")


def test_production_paths_are_fixed_constants():
    cfg = sw.Config()
    assert cfg.mapping_path == "/etc/axisai/web-slots.conf"
    assert cfg.include_path == "/etc/nginx/axisai/active-web-upstream.conf"
    assert cfg.trusted_uid == 0


def test_non_root_cli_rejects_bad_arguments_first_then_refuses_to_run(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("asserts the non-root refusal")
    bad = subprocess.run([sys.executable, "-I", str(SCRIPT), "5001"],
                         capture_output=True, text=True, timeout=30)
    assert bad.returncode == sw.EXIT_USAGE and "usage:" in bad.stderr
    good = subprocess.run([sys.executable, "-I", str(SCRIPT), "blue"],
                          capture_output=True, text=True, timeout=30)
    assert good.returncode == sw.EXIT_USAGE and "must run as root" in good.stderr
