from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts import deploy_contract as contract
from scripts.deploy_contract import HOST_PHASE_SECONDS, host_timeout_environment


ROOT = Path(__file__).resolve().parents[1]
HOST_SCRIPT = ROOT / "scripts" / "production_deploy.sh"
OUTER_LOCK_PATH = "/run/lock/axisai-production/production.lock"


def _bash_path(path: Path) -> str:
    resolved = path.resolve().as_posix()
    if len(resolved) >= 3 and resolved[1:3] == ":/":
        return f"/{resolved[0].lower()}{resolved[2:]}"
    return resolved


def _run(command: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )
    return result.stdout.strip()


def _readline_with_timeout(stream, timeout: int = 300) -> str:
    lines: queue.Queue[str] = queue.Queue(maxsize=1)
    threading.Thread(target=lambda: lines.put(stream.readline()), daemon=True).start()
    try:
        return lines.get(timeout=timeout)
    except queue.Empty as error:
        raise AssertionError(f"subprocess produced no line within {timeout} seconds") from error


def _trace_command_count(trace: str, command: str) -> int:
    return trace.splitlines().count(command)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


@pytest.fixture(scope="session")
def bash_executable() -> str:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.exists():
        return str(git_bash)
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")
    return bash


@pytest.fixture
def tmp_path() -> Path:
    temp_root = (ROOT / ".pytest-basetemp" / "production-deploy"
                 if os.name == "nt" else Path(tempfile.gettempdir()) / "axisai-r6-production-deploy-tests")
    temp_root.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="case-", dir=temp_root))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@dataclass
class HostFixture:
    deploy_dir: Path
    fake_bin: Path
    trace: Path
    prev_commit: str
    candidate_commit: str
    monotonic_state: Path
    environment: dict[str, str]

    def trace_text(self) -> str:
        return self.trace.read_text(encoding="utf-8")

    def command(self, bash_executable: str, deploy_sha: str | None = None) -> list[str]:
        return [
            bash_executable,
            _bash_path(HOST_SCRIPT),
            deploy_sha or self.candidate_commit,
            _bash_path(self.deploy_dir),
        ]

    def run(
        self,
        bash_executable: str,
        deploy_sha: str | None = None,
        public_health_url: str = "https://fitness.example/health",
        **environment: str,
    ) -> subprocess.CompletedProcess[str]:
        command = self.command(bash_executable, deploy_sha)
        command.append(public_health_url)
        inherited_lock = {
            "AXISAI_OUTER_LOCK_FD": "7",
            "FAKE_OUTER_LOCK_HELD": "1",
            "FAKE_OUTER_CAPABILITY_LOCKED": "1",
        }
        # Root reprovisions the clock state for every invocation.
        self.monotonic_state.write_text("", encoding="utf-8")
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            env={
                **self.environment,
                **host_timeout_environment(),
                **inherited_lock,
                **environment,
            },
            timeout=600,
        )

    def run_with_workflow_outer_lock(
        self,
        bash_executable: str,
        *,
        held: bool = True,
        marker: str = "7",
        capability_locked: bool = True,
        **environment: str,
    ) -> subprocess.CompletedProcess[str]:
        return self.run(
            bash_executable,
            AXISAI_OUTER_LOCK_FD=marker,
            FAKE_OUTER_LOCK_HELD="1" if held else "0",
            FAKE_OUTER_CAPABILITY_LOCKED="1" if capability_locked else "0",
            **environment,
        )


# Test-only engine probe: shell boundary assertions, never production orchestration.
ENGINE_PROBE = r"""import argparse,json,os,sys,subprocess
from pathlib import Path
def native(v):
    return v[1]+":"+v[2:] if os.name=="nt" and len(v)>3 and v[0]=="/" and v[2]=="/" else v
parser=argparse.ArgumentParser()
parser.add_argument("command",choices=["run"])
for name in ("deploy-sha","previous-commit","deploy-dir","context","work-dir","public-health-url","transaction-epoch"):
    parser.add_argument("--"+name,required=True)
a=parser.parse_args()
context,deploy,work=(Path(native(v)) for v in (a.context,a.deploy_dir,a.work_dir))
assert sys.flags.isolated and sys.dont_write_bytecode
assert context.parent==deploy and work.parent==deploy and context!=work
assert Path(__file__).resolve().parent.parent==context.resolve()
assert (context/"release.txt").read_text().strip()=="candidate"
assert not (context/"untracked-host.txt").exists() and not (context/".env").exists()
assert subprocess.check_output([os.environ["REAL_GIT"],"-C",str(deploy),"rev-parse","HEAD"],text=True).strip()==a.previous_commit
assert a.deploy_sha==os.environ["FAKE_CANDIDATE_SHA"]
with Path(native(os.environ["TRACE_FILE"])).open("a") as f:
    f.write("ENGINE_PROOF="+json.dumps(vars(a),sort_keys=True)+"\n")
sys.exit(int(os.environ.get("FAKE_ENGINE_EXIT","0")))
"""

@pytest.fixture
def host_fixture(tmp_path: Path):
    def make(
        *,
        flock_exit: int = 0,
        timeout_hang_phase: str = "",
        timeout_hang_command: str = "",
        clock_readings: tuple[int, ...] = (),
        initial_clock_mode: str = "",
    ) -> HostFixture:
        origin = tmp_path / "origin.git"
        source = tmp_path / "source"
        deploy_dir = tmp_path / "production"
        fake_bin = tmp_path / "fake-bin"
        trace = tmp_path / "trace.log"
        docker_state = tmp_path / "docker-revision"
        container_root = tmp_path / "container-app"
        candidate_health_count = tmp_path / "candidate-health-count"
        rollback_health_count = tmp_path / "rollback-health-count"
        public_health_count = tmp_path / "public-health-count"
        fake_clock = tmp_path / "fake-clock"
        fake_clock_count = tmp_path / "fake-clock-count"
        fake_clock_readings = tmp_path / "fake-clock-readings"
        fake_clock_latch = tmp_path / "fake-clock-latch"
        monotonic_state = tmp_path / "runtime-monotonic-clock"
        monotonic_state.write_text("", encoding="utf-8")
        fake_bin.mkdir()
        trace.write_text("", encoding="utf-8")
        fake_clock.write_text("1000", encoding="utf-8")
        fake_clock_count.write_text("0", encoding="utf-8")
        fake_clock_readings.write_text(
            "\n".join(str(reading) for reading in clock_readings),
            encoding="utf-8",
        )

        _run(["git", "init", "--bare", str(origin)])
        _run(["git", "init", "-b", "main", str(source)])
        _run(["git", "-C", str(source), "config", "user.email", "deploy-test@example.com"])
        _run(["git", "-C", str(source), "config", "user.name", "Deploy Test"])
        (source / "release.txt").write_text("previous\n", encoding="utf-8")
        _run(["git", "-C", str(source), "add", "."])
        _run(["git", "-C", str(source), "commit", "-m", "previous"])
        prev_commit = _run(["git", "-C", str(source), "rev-parse", "HEAD"])
        _run(["git", "-C", str(source), "remote", "add", "origin", str(origin)])
        _run(["git", "-C", str(source), "push", "-u", "origin", "main"])
        _run(["git", "clone", "--branch", "main", str(origin), str(deploy_dir)])
        engine = source / "scripts/r6_deploy_transaction.py"
        engine.parent.mkdir(exist_ok=True)
        engine.write_text(ENGINE_PROBE, encoding="utf-8", newline="\n")
        (source / "release.txt").write_text("candidate\n", encoding="utf-8")
        _run(["git", "-C", str(source), "add", "release.txt", "scripts/r6_deploy_transaction.py"])
        _run(["git", "-C", str(source), "commit", "-m", "candidate"])
        candidate_commit = _run(["git", "-C", str(source), "rev-parse", "HEAD"])
        _run(["git", "-C", str(source), "push", "origin", "main"])

        real_git = shutil.which("git")
        assert real_git is not None
        _write_executable(
            fake_bin / "git",
            r"""#!/usr/bin/env bash
printf 'git' >> "$TRACE_FILE"
printf ' %s' "$@" >> "$TRACE_FILE"
printf '\n' >> "$TRACE_FILE"
exec "$REAL_GIT" "$@"
""",
        )
        _write_executable(
            fake_bin / "flock",
            r"""#!/usr/bin/env bash
printf 'flock' >> "$TRACE_FILE"
printf ' %s' "$@" >> "$TRACE_FILE"
printf '\n' >> "$TRACE_FILE"
if [[ " $* " == *" $EXPECTED_OUTER_LOCK_PATH "* && " $* " == *' -n '* ]]; then
  if [[ "${FAKE_OUTER_LOCK_HELD:-0}" == 1 ]]; then exit 73; fi
  exit 0
fi
if [[ " $* " == ' -n -E 73 7 ' ]]; then
  if [[ "${FAKE_OUTER_CAPABILITY_LOCKED:-0}" == 1 ]]; then exit 0; fi
  exit 73
fi
if [[ " $* " == *" $EXPECTED_OUTER_LOCK_PATH "* ]]; then
  if [[ "${FLOCK_EXIT:-0}" != 0 ]]; then exit 73; fi
  lock_state_created=0
  if [[ -n "${FLOCK_STATE_DIR:-}" ]]; then
    if ! mkdir "$FLOCK_STATE_DIR" 2>/dev/null; then
      exit 73
    fi
    lock_state_created=1
  fi
  if [[ -n "${FLOCK_READY_FILE:-}" ]]; then
    : > "$FLOCK_READY_FILE"
    printf 'FAKE_LOCK_READY\n'
  fi
  while [[ -n "${FLOCK_HOLD_FILE:-}" && -e "$FLOCK_HOLD_FILE" ]]; do
    /usr/bin/sleep 0.05
  done
  shift 6
  FAKE_OUTER_LOCK_HELD=1 "$@"
  child_status="$?"
  if [[ "$lock_state_created" == 1 ]]; then rmdir "$FLOCK_STATE_DIR"; fi
  exit "$child_status"
fi
if [[ " $* " == ' -n -E 73 9 ' ]]; then
  if [[ "${FAKE_INNER_LOCK_CONTENDED:-0}" == 1 ]]; then exit 73; fi
  exit 0
fi
exit 64
""",
        )
        _write_executable(
            fake_bin / "stat",
            r"""#!/usr/bin/env bash
printf 'stat' >> "$TRACE_FILE"
printf ' %s' "$@" >> "$TRACE_FILE"
printf '\n' >> "$TRACE_FILE"
last=''
for argument in "$@"; do last="$argument"; done
case "$last" in
  /run/lock/axisai-production) printf '%s\n' "$FAKE_OUTER_DIR_METADATA" ;;
  /run/lock/axisai-production/production.lock) printf '%s\n' "$FAKE_OUTER_FILE_METADATA" ;;
  /proc/self/fd/7)
    if [[ " $* " != *' -L '* ]]; then
      printf '%s\n' '11:77:0:symbolic link:777:1'
      exit 0
    fi
    printf '%s\n' "$FAKE_OUTER_FD_METADATA"
    ;;
  "$AXISAI_MONOTONIC_STATE")
    printf '%s\n' "${FAKE_MONOTONIC_STATE_METADATA:-$EUID:600:1:regular empty file}"
    ;;
  *) exit 65 ;;
esac
""",
        )
        _write_executable(
            fake_bin / "python3",
            r"""#!/usr/bin/env bash
printf 'direct-outer-lock -w60\n' >> "$TRACE_FILE"
if [[ "${FLOCK_EXIT:-0}" != 0 ]]; then
  echo 'outer deployment lock unavailable after 60 seconds' >&2
  exit 73
fi
lock_state_created=0
if [[ -n "${FLOCK_STATE_DIR:-}" ]]; then
  if ! mkdir "$FLOCK_STATE_DIR" 2>/dev/null; then
    echo 'outer deployment lock unavailable after 60 seconds' >&2
    exit 73
  fi
  lock_state_created=1
fi
if [[ -n "${FLOCK_READY_FILE:-}" ]]; then
  : > "$FLOCK_READY_FILE"
  printf 'FAKE_LOCK_READY\n'
fi
while [[ -n "${FLOCK_HOLD_FILE:-}" && -e "$FLOCK_HOLD_FILE" ]]; do
  /usr/bin/sleep 0.05
done
test "$1" = -
shift
helper="$1"
shift
direct_source="$(cat)"
if [[ "$direct_source" != *'os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC'* ||
      "$direct_source" == *'os.O_RDWR'* ]]; then
  echo 'direct outer lock must be opened read-only' >&2
  exit 74
fi
AXISAI_OUTER_LOCK_FD=7 FAKE_OUTER_LOCK_HELD=1 \
  FAKE_OUTER_CAPABILITY_LOCKED=1 "$helper" "$@"
status="$?"
if [[ "$lock_state_created" == 1 ]]; then rmdir "$FLOCK_STATE_DIR"; fi
exit "$status"
""",
        )
        _write_executable(
            fake_bin / "docker",
            "#!/usr/bin/env bash\necho forbidden-host-command >> \"$TRACE_FILE\"\nexit 97\n",
        )
        _write_executable(
            fake_bin / "curl",
            "#!/usr/bin/env bash\necho forbidden-host-command >> \"$TRACE_FILE\"\nexit 97\n",
        )
        _write_executable(
            fake_bin / "timeout",
            r"""#!/usr/bin/env bash
printf 'timeout phase=%s' "${CURRENT_PHASE:-none}" >> "$TRACE_FILE"
printf ' %s' "$@" >> "$TRACE_FILE"
printf '\n' >> "$TRACE_FILE"
while [[ "${1:-}" == --* ]]; do shift; done
duration="${1%s}"
shift
command_name="$1"
shift
operation="$command_name"
if [[ "$command_name" == docker ]]; then
  if [[ " $* " == *' build '* ]]; then operation='docker:build'; fi
  if [[ " $* " == *' up -d '* ]]; then operation='docker:up'; fi
  if [[ " $* " == *' ps '* ]]; then operation='docker:ps'; fi
  if [[ " $* " == *' logs '* ]]; then operation='docker:logs'; fi
  if [[ " $* " == *' image prune '* ]]; then operation='docker:prune'; fi
elif [[ "$command_name" == git ]]; then
  operation="git:${1:-unknown}"
elif [[ "$command_name" == python3 && "${1:-}" == -I ]]; then
  operation=transaction_engine
elif [[ "$command_name" == python3 && "${1:-}" == -c && "${2:-}" == *monotonic_ns* ]]; then
  operation=clock
fi
if [[ "$operation" == clock ]]; then
  clock_count="$(cat "$FAKE_CLOCK_COUNT_FILE")"
  clock_count=$((clock_count + 1))
  printf '%s' "$clock_count" > "$FAKE_CLOCK_COUNT_FILE"
  clock_mode=''
  if [[ "$clock_count" == 1 && -n "$FAKE_INITIAL_CLOCK_MODE" ]]; then
    clock_mode="$FAKE_INITIAL_CLOCK_MODE"
  fi
  if [[ "$clock_mode" == fail ]]; then
    printf 'CLOCK_FAILURE phase=%s count=%s\n' "${CURRENT_PHASE:-none}" "$clock_count" >> "$TRACE_FILE"
    exit 45
  elif [[ "$clock_mode" == hang ]]; then
    printf 'TIMEOUT_HANG phase=%s operation=clock\n' "${CURRENT_PHASE:-none}" >> "$TRACE_FILE"
    exit 124
  fi
  mapfile -t scripted_readings < "$FAKE_CLOCK_READINGS_FILE"
  if ((${#scripted_readings[@]} > 0)); then
    reading_index=$((clock_count - 1))
    if ((reading_index >= ${#scripted_readings[@]})); then
      reading_index=$((${#scripted_readings[@]} - 1))
    fi
    printf '%s\n' "${scripted_readings[$reading_index]}"
  else
    cat "$FAKE_CLOCK_FILE"
  fi
  exit 0
fi
if [[ "${CURRENT_PHASE:-}" == "$FAKE_TIMEOUT_HANG_PHASE" && "$operation" == "$FAKE_TIMEOUT_HANG_COMMAND" ]]; then
  now="$(cat "$FAKE_CLOCK_FILE")"
  printf '%s' "$((now + duration))" > "$FAKE_CLOCK_FILE"
  printf 'TIMEOUT_HANG phase=%s operation=%s\n' "$CURRENT_PHASE" "$operation" >> "$TRACE_FILE"
  exit 124
fi
case "$command_name" in
  docker) source "$FAKE_BIN/docker" "$@" ;;
  git) source "$FAKE_BIN/git" "$@" ;;
  python)
    if [[ "${FORBID_PLAIN_PYTHON:-0}" == 1 ]]; then
      printf 'PLAIN_PYTHON_REQUIRED\n' >> "$TRACE_FILE"
      exit 127
    fi
    "$REAL_PYTHON" "$@"
    ;;
  python3) "$REAL_PYTHON" "$@" ;;
  *) "$command_name" "$@" ;;
esac
""",
        )
        # Git for Windows resolves its bundled curl ahead of an extensionless
        # PATH shim. BASH_ENV keeps the test hermetic by delegating that command
        # name to the executable fake; Linux still uses the same fake bytes.
        _write_executable(
            fake_bin / "bash-env",
            (
                'curl() { "$FAKE_BIN/curl" "$@"; }\n'
                'git() { "$FAKE_BIN/git" "$@"; }\n'
                'sleep() { "$FAKE_BIN/sleep" "$@"; }\n'
                'stat() { "$FAKE_BIN/stat" "$@"; }\n'
                'timeout() { "$FAKE_BIN/timeout" "$@"; }\n'
            ),
        )

        environment = os.environ.copy()
        environment.update(
            {
                'PATH': f"{_bash_path(fake_bin)}:{environment.get('PATH', '')}",
                'FAKE_BIN': _bash_path(fake_bin),
                'BASH_ENV': _bash_path(fake_bin / "bash-env"),
                'TRACE_FILE': _bash_path(trace),
                'REAL_GIT': Path(real_git).resolve().as_posix(),
                'REAL_PYTHON': Path(sys.executable).resolve().as_posix(),
                'FLOCK_EXIT': str(flock_exit),
                'EXPECTED_OUTER_LOCK_PATH': OUTER_LOCK_PATH,
                'AXISAI_MONOTONIC_STATE': _bash_path(monotonic_state),
                'FAKE_OUTER_DIR_METADATA': "0:directory:755",
                'FAKE_OUTER_FILE_METADATA': "11:22:0:regular empty file:644:1",
                'FAKE_OUTER_FD_METADATA': "11:22:0:regular empty file:644:1",
                'FAKE_CANDIDATE_SHA': candidate_commit,
                'FAKE_CLOCK_FILE': _bash_path(fake_clock),
                'FAKE_CLOCK_COUNT_FILE': _bash_path(fake_clock_count),
                'FAKE_CLOCK_READINGS_FILE': _bash_path(fake_clock_readings),
                'FAKE_INITIAL_CLOCK_MODE': initial_clock_mode,
                'FAKE_TIMEOUT_HANG_PHASE': timeout_hang_phase,
                'FAKE_TIMEOUT_HANG_COMMAND': timeout_hang_command,
            }
        )
        return HostFixture(
            deploy_dir,
            fake_bin,
            trace,
            prev_commit,
            candidate_commit,
            monotonic_state,
            environment,
        )

    return make


def test_host_script_is_valid_bash(bash_executable):
    result = subprocess.run(
        [bash_executable, "-n", _bash_path(HOST_SCRIPT)],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_host_helper_succeeds_when_plain_python_is_unavailable(
        bash_executable, host_fixture):
    fixture = host_fixture()

    result = fixture.run_with_workflow_outer_lock(
        bash_executable,
        public_health_url="https://fitness.example/health",
        FORBID_PLAIN_PYTHON="1",
    )

    assert result.returncode == 0, result.stderr
    assert "PLAIN_PYTHON_REQUIRED" not in fixture.trace.read_text(encoding="utf-8")


def test_workflow_outer_lock_capability_proves_held_lock(
        bash_executable, host_fixture):
    fixture = host_fixture()

    result = fixture.run_with_workflow_outer_lock(bash_executable)

    assert result.returncode == 0, result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.splitlines()[:5] == [
        "stat -c %u:%F:%a -- /run/lock/axisai-production",
        f"stat -c %d:%i:%u:%F:%a:%h -- {OUTER_LOCK_PATH}",
        "stat -L -c %d:%i:%u:%F:%a:%h -- /proc/self/fd/7",
        f"flock -n -E 73 {OUTER_LOCK_PATH} true",
        "flock -n -E 73 7",
    ]
    assert "flock -w" not in trace


def test_direct_invocation_without_inherited_capability_cannot_mutate(
        bash_executable, host_fixture):
    fixture = host_fixture()

    result = fixture.run(bash_executable, AXISAI_OUTER_LOCK_FD="")

    assert result.returncode == 73
    assert "outer deployment lock is unavailable or unsafe" in result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    assert "git " not in trace
    assert "docker " not in trace
    assert "direct-outer-lock" not in trace
    assert not (fixture.deploy_dir / ".axisai-production-deploy.lock").exists()


def test_outer_lock_marker_without_held_lock_fails_before_mutation(
        bash_executable, host_fixture):
    fixture = host_fixture()

    result = fixture.run_with_workflow_outer_lock(bash_executable, held=False)

    assert result.returncode == 73
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.splitlines()[:4] == [
        "stat -c %u:%F:%a -- /run/lock/axisai-production",
        f"stat -c %d:%i:%u:%F:%a:%h -- {OUTER_LOCK_PATH}",
        "stat -L -c %d:%i:%u:%F:%a:%h -- /proc/self/fd/7",
        f"flock -n -E 73 {OUTER_LOCK_PATH} true",
    ]
    assert "git " not in trace
    assert "docker " not in trace


def test_deploy_directory_lock_path_is_never_opened_or_created(
        bash_executable, host_fixture):
    fixture = host_fixture()

    result = fixture.run_with_workflow_outer_lock(bash_executable)

    assert result.returncode == 0, result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    assert "flock -n -E 73 9" not in trace
    assert ".axisai-production-deploy.lock" not in trace
    assert not (fixture.deploy_dir / ".axisai-production-deploy.lock").exists()


def test_unrelated_holder_with_unlocked_exact_inode_fd_is_rejected(
        bash_executable, host_fixture):
    fixture = host_fixture()
    result = fixture.run_with_workflow_outer_lock(
        bash_executable, held=True, capability_locked=False,
    )
    assert result.returncode == 73
    assert "outer deployment lock capability is not held" in result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    assert f"flock -n -E 73 {OUTER_LOCK_PATH} true" in trace
    assert "flock -n -E 73 7" in trace
    assert "git " not in trace and "docker " not in trace


@pytest.mark.linux_lock
def test_real_flock_unrelated_holder_cannot_forge_inherited_ofd(
        bash_executable, host_fixture, request):
    import stat

    assert request.config.getoption("--run-authoritative-linux-lock-tests")
    assert os.name == "posix" and os.geteuid() == 0 and shutil.which("flock")
    fixture = host_fixture()
    lock_dir = Path(OUTER_LOCK_PATH).parent
    lock_path = Path(OUTER_LOCK_PATH)
    created_dir = False
    created_file = False
    holder = None
    caller_fd = None
    try:
        if not lock_dir.exists():
            lock_dir.mkdir(mode=0o755)
            created_dir = True
        directory_status = lock_dir.lstat()
        if (not stat.S_ISDIR(directory_status.st_mode) or
                directory_status.st_uid != 0 or
                stat.S_IMODE(directory_status.st_mode) != 0o755):
            raise AssertionError("fixed root lock directory violates production policy")
        flags = os.O_RDWR | os.O_NOFOLLOW
        if not lock_path.exists():
            created_file = True
            provision_fd = os.open(lock_path, flags | os.O_CREAT | os.O_EXCL, 0o644)
            os.close(provision_fd)
        lock_status = lock_path.lstat()
        if (not stat.S_ISREG(lock_status.st_mode) or lock_status.st_uid != 0 or
                stat.S_IMODE(lock_status.st_mode) != 0o644 or lock_status.st_nlink != 1):
            raise AssertionError("fixed root lock file violates production policy")
        holder = subprocess.Popen(
            [sys.executable, "-c", (
                "import fcntl,os,sys,time; "
                "f=os.open(sys.argv[1],os.O_RDWR|os.O_NOFOLLOW); "
                "fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); time.sleep(30)"
            ), OUTER_LOCK_PATH],
            stdout=subprocess.PIPE, text=True,
        )
        assert _readline_with_timeout(holder.stdout, timeout=10).strip() == "ready"
        caller_fd = os.open(lock_path, flags)  # Exact inode, deliberately unlocked OFD.
        environment = fixture.environment.copy()
        environment.update(host_timeout_environment())
        environment.pop("BASH_ENV", None)
        environment["PATH"] = os.environ.get("PATH", "")
        environment["AXISAI_OUTER_LOCK_FD"] = "7"
        result = subprocess.run(
            fixture.command(bash_executable, "not-a-sha") + ["https://fitness.example/health"],
            text=True, capture_output=True, check=False, env=environment, timeout=30,
            close_fds=False,
            preexec_fn=lambda: os.dup2(caller_fd, 7, inheritable=True),
        )
        assert result.returncode == 73
        assert "outer deployment lock capability is not held" in result.stderr
        assert "unavailable or unsafe" not in result.stderr
        assert not (fixture.deploy_dir / ".axisai-production-deploy.lock").exists()
        assert fixture.trace.read_text(encoding="utf-8") == ""
    finally:
        if caller_fd is not None:
            os.close(caller_fd)
        if holder is not None:
            holder.terminate()
            holder.wait(timeout=10)
        if created_file:
            lock_path.unlink(missing_ok=True)
        if created_dir:
            lock_dir.rmdir()


@pytest.mark.linux_lock
def test_real_locked_inherited_ofd_reaches_helper_validation(
        bash_executable, host_fixture, request):
    import fcntl
    import stat

    assert request.config.getoption("--run-authoritative-linux-lock-tests")
    assert os.name == "posix" and os.geteuid() == 0 and shutil.which("flock")
    fixture = host_fixture()
    lock_dir = Path(OUTER_LOCK_PATH).parent
    lock_path = Path(OUTER_LOCK_PATH)
    created_dir = False
    created_file = False
    inherited_fd = None
    try:
        if not lock_dir.exists():
            lock_dir.mkdir(mode=0o755)
            created_dir = True
        directory_status = lock_dir.lstat()
        if (not stat.S_ISDIR(directory_status.st_mode) or directory_status.st_uid != 0 or
                stat.S_IMODE(directory_status.st_mode) != 0o755):
            raise AssertionError("fixed root lock directory violates production policy")
        if not lock_path.exists():
            created_file = True
            created_fd = os.open(
                lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL, 0o644,
            )
            os.close(created_fd)
        lock_status = lock_path.lstat()
        if (not stat.S_ISREG(lock_status.st_mode) or lock_status.st_uid != 0 or
                stat.S_IMODE(lock_status.st_mode) != 0o644 or lock_status.st_nlink != 1):
            raise AssertionError("fixed root lock file violates production policy")
        inherited_fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
        fcntl.flock(inherited_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        environment = fixture.environment.copy()
        environment.update(host_timeout_environment())
        environment.pop("BASH_ENV", None)
        environment["PATH"] = os.environ.get("PATH", "")
        environment["AXISAI_OUTER_LOCK_FD"] = "7"
        result = subprocess.run(
            fixture.command(bash_executable, "not-a-sha") + ["https://fitness.example/health"],
            text=True, capture_output=True, check=False, env=environment, timeout=30,
            close_fds=False,
            preexec_fn=lambda: os.dup2(inherited_fd, 7, inheritable=True),
        )
        assert result.returncode == 64, result.stderr
        assert "DEPLOY_SHA must be lowercase 40-hex" in result.stderr
        assert not (fixture.deploy_dir / ".axisai-production-deploy.lock").exists()
    finally:
        if inherited_fd is not None:
            os.close(inherited_fd)
        if created_file:
            lock_path.unlink(missing_ok=True)
        if created_dir:
            lock_dir.rmdir()


@pytest.mark.linux_lock
def test_real_outer_lock_release_allows_independent_reacquisition(request):
    import fcntl
    import stat

    assert request.config.getoption("--run-authoritative-linux-lock-tests")
    assert os.name == "posix" and os.geteuid() == 0 and shutil.which("flock")
    lock_dir = Path(OUTER_LOCK_PATH).parent
    lock_path = Path(OUTER_LOCK_PATH)
    created_dir = False
    created_file = False
    holder_fd = None
    try:
        if not lock_dir.exists():
            lock_dir.mkdir(mode=0o755)
            created_dir = True
        directory_status = lock_dir.lstat()
        if (not stat.S_ISDIR(directory_status.st_mode) or directory_status.st_uid != 0 or
                stat.S_IMODE(directory_status.st_mode) != 0o755):
            raise AssertionError("fixed root lock directory violates production policy")
        if not lock_path.exists():
            created_file = True
            created_fd = os.open(
                lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL, 0o644,
            )
            os.close(created_fd)
        holder_fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        contended = subprocess.run(
            ["flock", "-n", "-E", "73", OUTER_LOCK_PATH, "true"], check=False,
        )
        assert contended.returncode == 73
        os.close(holder_fd)
        holder_fd = None
        released = subprocess.run(
            ["flock", "-n", "-E", "73", OUTER_LOCK_PATH, "true"], check=False,
        )
        assert released.returncode == 0
    finally:
        if holder_fd is not None:
            os.close(holder_fd)
        if created_file:
            lock_path.unlink(missing_ok=True)
        if created_dir:
            lock_dir.rmdir()


def test_arbitrary_outer_lock_marker_is_rejected_before_lock_or_mutation(
        bash_executable, host_fixture):
    fixture = host_fixture()

    result = fixture.run_with_workflow_outer_lock(
        bash_executable, marker="8",
    )

    assert result.returncode == 73
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace == ""
    assert "git " not in trace
    assert "docker " not in trace


@pytest.mark.parametrize(
    ("metadata_name", "metadata"),
    [
        ("FAKE_OUTER_DIR_METADATA", "1000:directory:755"),
        ("FAKE_OUTER_DIR_METADATA", "0:directory:775"),
        ("FAKE_OUTER_FILE_METADATA", "11:22:0:symbolic link:777:1"),
        ("FAKE_OUTER_FILE_METADATA", "11:22:1000:regular empty file:644:1"),
        ("FAKE_OUTER_FILE_METADATA", "11:22:0:regular empty file:666:1"),
        ("FAKE_OUTER_FILE_METADATA", "11:22:0:regular empty file:644:2"),
        ("FAKE_OUTER_FD_METADATA", "11:99:0:regular empty file:644:1"),
    ],
)
def test_unsafe_outer_lock_prerequisite_fails_before_probe_or_mutation(
        bash_executable, host_fixture, metadata_name, metadata):
    fixture = host_fixture()

    result = fixture.run_with_workflow_outer_lock(
        bash_executable, **{metadata_name: metadata},
    )

    assert result.returncode == 73
    trace = fixture.trace.read_text(encoding="utf-8")
    assert f"flock -n -E 73 {OUTER_LOCK_PATH}" not in trace
    assert "git " not in trace
    assert "docker " not in trace


def test_invalid_sha_fails_after_lock_without_git_or_docker(bash_executable, host_fixture):
    fixture = host_fixture()
    result = fixture.run(bash_executable, deploy_sha="not-a-sha")

    assert result.returncode == 64
    assert "DEPLOY_SHA must be lowercase 40-hex" in result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    assert "direct-outer-lock" not in trace
    assert "flock -n -E 73 7" in trace
    assert "git " not in trace
    assert "docker " not in trace


def test_host_transaction_budget_preserves_ssm_margin_and_rollback_reserve(bash_executable, host_fixture):
    f=host_fixture()
    result=f.run(bash_executable)
    assert result.returncode==0, result.stderr
    line=next(x for x in result.stderr.splitlines() if x.startswith("host transaction budget:"))
    v={k:int(x) for k,x in re.findall(r"(\w+)=(\d+)",line)}
    assert v["execution"]==contract.SSM_EXECUTION_TIMEOUT_SECONDS
    assert v["worst_case"]==contract.HOST_WORST_CASE_SECONDS
    assert v["execution"]-v["worst_case"]==v["margin"]==contract.SSM_EXECUTION_MARGIN_SECONDS>=220
    assert v["failure_tail"]==contract.HOST_FAILURE_TAIL_SECONDS
    assert v["success_tail"]==contract.HOST_SUCCESS_TAIL_SECONDS
    assert v["transaction"]==v["forward"]+max(v["failure_tail"],v["success_tail"])
    assert v["post_lock"]==v["preflight"]+v["transaction"]
    assert v["clock"]+v["clock_state"]==HOST_PHASE_SECONDS["clock_setup"]
    assert v["cleanup"]==HOST_PHASE_SECONDS["cleanup"]
    grants=re.findall(r"timeout phase=(preflight|transaction) --signal=TERM --kill-after=2s (\d+)s ",f.trace_text())
    assert grants and {phase for phase,_ in grants}=={"preflight","transaction"}
    assert all(int(grant)<=(v["post_lock"] if phase=="transaction" else v[phase]) for phase,grant in grants)
    assert "monotonic_ns" in f.trace_text() and " date +%s" not in f.trace_text()


def test_host_transaction_budget_rejects_noncanonical_execution_timeout(
        bash_executable, host_fixture):
    fixture = host_fixture()

    result = fixture.run(
        bash_executable, SSM_EXECUTION_TIMEOUT_SECONDS="1700",
    )

    assert result.returncode == 70
    assert "invalid host transaction budget" in result.stderr


@pytest.mark.parametrize("clock_mode", ["fail", "hang"])
def test_initial_monotonic_clock_failure_is_closed_before_mutation(
    bash_executable, host_fixture, clock_mode
):
    fixture = host_fixture(initial_clock_mode=clock_mode)
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    assert "monotonic clock unavailable before deployment mutation" in result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    assert "git " not in trace
    assert "docker " not in trace


def test_decreasing_monotonic_reading_fails_closed_without_mutation(
    bash_executable, host_fixture
):
    fixture = host_fixture(clock_readings=(1000, 1001, 999))
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    assert "monotonic clock moved backward" in result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    assert "git reset --hard" not in trace
    assert "docker " not in trace


def test_preflight_hang_times_out_before_candidate_mutation(bash_executable, host_fixture):
    fixture = host_fixture(
        timeout_hang_phase="preflight",
        timeout_hang_command="git:fetch",
    )
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert "TIMEOUT_HANG phase=preflight operation=git:fetch" in trace
    assert "timeout phase=preflight --signal=TERM --kill-after=2s 68s git fetch" in trace
    assert "git reset --hard" not in trace
    assert "docker " not in trace


def test_success_delegates_exact_archive_without_changing_the_checkout(bash_executable, host_fixture):
    import json
    f=host_fixture()
    (f.deploy_dir/"untracked-host.txt").write_text("host-only")
    (f.deploy_dir/".env").write_text("HOST_SECRET=local")
    result=f.run(bash_executable)
    assert result.returncode==0, result.stderr
    trace=f.trace_text()
    proofs=[line for line in trace.splitlines() if line.startswith("ENGINE_PROOF=")]
    assert len(proofs)==1
    proof=json.loads(proofs[0].split("=",1)[1])
    assert proof["deploy_sha"]==f.candidate_commit
    assert proof["previous_commit"]==f.prev_commit
    assert proof["public_health_url"]=="https://fitness.example/health"
    assert proof["transaction_epoch"]=="1000"
    assert f"git archive --format=tar {f.candidate_commit}" in trace
    assert "git fetch origin main --prune" in trace
    assert "git reset" not in trace and "docker " not in trace
    assert _run(["git","-C",str(f.deploy_dir),"rev-parse","HEAD"])==f.prev_commit
    assert (f.deploy_dir/"untracked-host.txt").read_text()=="host-only"
    assert (f.deploy_dir/".env").read_text()=="HOST_SECRET=local"
    assert not list(f.deploy_dir.glob(".axisai-build-*"))
    assert not list(f.deploy_dir.glob(".axisai-r6-work.*"))
    assert f"deployment verified at {f.candidate_commit}" in result.stderr
    cleanup_timeout=HOST_PHASE_SECONDS["cleanup"]-2
    assert f"{cleanup_timeout}s rm -r -- " in trace


def test_clock_state_mutation_never_touches_the_production_checkout(
    bash_executable, host_fixture
):
    fixture = host_fixture()
    result = fixture.run(bash_executable)

    assert result.returncode == 0, result.stderr
    assert list(fixture.deploy_dir.glob(".axisai-monotonic-clock*")) == []
    trace = fixture.trace_text()
    assert ".axisai-monotonic-clock" not in trace
    assert fixture.monotonic_state.read_text(encoding="utf-8").strip() != ""


@pytest.mark.parametrize(
    ("environment", "metadata"),
    [
        ({"AXISAI_MONOTONIC_STATE": ""}, None),
        ({"AXISAI_MONOTONIC_STATE": "relative/clock"}, None),
        ({}, "0:600:1:regular empty file"),
        ({}, "$EUID:644:1:regular empty file"),
        ({}, "$EUID:600:2:regular empty file"),
        ({}, "$EUID:600:1:symbolic link"),
    ],
)
def test_unusable_runtime_clock_state_fails_closed_before_mutation(
    bash_executable, host_fixture, environment, metadata
):
    fixture = host_fixture()
    overrides = dict(environment)
    if metadata is not None:
        overrides["FAKE_MONOTONIC_STATE_METADATA"] = metadata

    result = fixture.run(bash_executable, **overrides)

    assert result.returncode == 70
    assert "monotonic clock state unavailable" in result.stderr
    trace = fixture.trace_text()
    assert "git " not in trace
    assert "docker " not in trace


def test_stale_candidate_fails_before_checkout_or_docker(bash_executable, host_fixture):
    fixture = host_fixture()
    result = fixture.run(bash_executable, deploy_sha=fixture.prev_commit)

    assert result.returncode != 0
    assert "stale" in result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    assert "git reset --hard" not in trace
    assert "docker " not in trace
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.prev_commit


def test_divergent_production_head_rejects_candidate_before_mutation(
    bash_executable, host_fixture
):
    fixture = host_fixture()
    _run(["git", "-C", str(fixture.deploy_dir), "config", "user.email", "host@example.com"])
    _run(["git", "-C", str(fixture.deploy_dir), "config", "user.name", "Host Test"])
    (fixture.deploy_dir / "host-only.txt").write_text("newer host\n", encoding="utf-8")
    _run(["git", "-C", str(fixture.deploy_dir), "add", "host-only.txt"])
    _run(["git", "-C", str(fixture.deploy_dir), "commit", "-m", "host-only"])
    host_commit = _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"])

    result = fixture.run(bash_executable)

    assert result.returncode != 0
    assert "older than or divergent" in result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    assert "git reset --hard" not in trace
    assert "docker " not in trace
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == host_commit


# --- baked revision identity contract -------------------------------------
#
# The host proves which build is actually running by reading /app/BUILD_REVISION
# inside the container.  Images built before that contract existed have no such
# file, so an authorized legacy rollback -- and only that -- may accept its
# absence.  Everything else must still prove exact revision identity.
#
# These cases all force the rollback path with a candidate whose baked revision
# is wrong, which fails the candidate at the identity check itself.

FORCE_ROLLBACK_BAKED_REVISION = "c" * 40


# --- authoritative Linux privileged-helper object identity (finding 6) ------
#
# The portable tests in tests/test_deploy_control.py prove the materializer's
# logic against injected fakes. Only a real root Linux kernel can prove the
# property that actually matters: after root materializes the helper, the
# unprivileged deploy user cannot replace the object at that pathname, and the
# bytes reached by `execve` are the bytes root verified.

HELPER_PROBE = b"#!/bin/sh\necho validated\n"


def _materializer_script(tmp_path: Path, payload: bytes) -> Path:
    import base64

    import scripts.deploy_control as deploy_control

    source = (
        "ENCODED_HELPER = "
        + repr(base64.b64encode(payload).decode("ascii"))
        + "\n"
        + deploy_control.HELPER_MATERIALIZATION_SOURCE
    )
    script = tmp_path / "materialize_helper.py"
    script.write_text(source, encoding="utf-8")
    return script


@pytest.mark.linux_helper_identity
def test_real_deploy_user_cannot_replace_the_verified_helper_object(
        tmp_path, request):
    import hashlib
    import pwd
    import stat as stat_module

    import scripts.deploy_control as deploy_control

    assert request.config.getoption("--run-authoritative-linux-lock-tests")
    assert os.name == "posix" and os.geteuid() == 0

    script = _materializer_script(tmp_path, HELPER_PROBE)
    # Exactly the argv the root bootstrap passes.
    completed = subprocess.run(
        [sys.executable, str(script), "materialize-helper",
         hashlib.sha256(HELPER_PROBE).hexdigest()],
        text=True, capture_output=True, check=False, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    directory = Path(completed.stdout.strip())
    helper = directory / deploy_control.HELPER_NAME
    # "nobody" stands in for the unprivileged deploy user; every runner has it.
    account = pwd.getpwnam("nobody")
    try:
        directory_status = directory.lstat()
        helper_status = helper.lstat()
        assert directory_status.st_uid == 0
        assert stat_module.S_IMODE(directory_status.st_mode) == 0o755
        assert helper_status.st_uid == 0 and helper_status.st_nlink == 1
        assert stat_module.S_ISREG(helper_status.st_mode)
        assert stat_module.S_IMODE(helper_status.st_mode) == 0o505
        assert stat_module.S_IMODE(helper_status.st_mode) & 0o222 == 0
        assert helper.read_bytes() == HELPER_PROBE

        # The kernel-level property: the deploy user cannot unlink or recreate
        # the entry, because its parent directory is root-owned and not
        # writable by others. Replacement between validation and execve is
        # therefore not expressible.
        attack = subprocess.run(
            [sys.executable, "-c", (
                "import os, sys\n"
                "target = sys.argv[1]\n"
                "try:\n"
                "    os.unlink(target)\n"
                "    print('unlinked')\n"
                "except OSError as error:\n"
                "    print('refused:%d' % error.errno)\n"
                "try:\n"
                "    open(target, 'wb').write(b'attacker')\n"
                "    print('rewrote')\n"
                "except OSError as error:\n"
                "    print('write-refused:%d' % error.errno)\n"
            ), str(helper)],
            text=True, capture_output=True, check=False, timeout=30,
            user=account.pw_uid, group=account.pw_gid,
        )
        assert attack.stdout.split() == [
            "refused:%d" % 13, "write-refused:%d" % 13,
        ], attack.stdout + attack.stderr
        assert helper.read_bytes() == HELPER_PROBE

        # The verified object executes under the exact identity the production
        # privilege drop builds: initgroups() replaces root's supplementary
        # groups with the deploy user's, so the root-owned helper is reached
        # through its "other" r-x bits.
        executed = subprocess.run(
            [str(helper)], text=True, capture_output=True, check=False,
            timeout=30, user=account.pw_uid, group=account.pw_gid,
            extra_groups=[account.pw_gid],
        )
        assert executed.returncode == 0, executed.stderr
        assert executed.stdout.strip() == "validated"

        # The other half of that contract, asserted rather than assumed: mode
        # 0505 grants the GROUP class nothing, and the helper's group is root.
        # A drop that forgot initgroups() would keep gid 0 in the child's group
        # list, match the group class, and be denied. The helper is therefore
        # fail-closed against an incomplete privilege drop, and this is why
        # PRIVILEGE_DROP_SOURCE calls initgroups() before setgid/setuid.
        with pytest.raises(PermissionError):
            subprocess.run(
                [str(helper)], text=True, capture_output=True, check=False,
                timeout=30, user=account.pw_uid, group=account.pw_gid,
                extra_groups=[0],
            )
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.mark.linux_helper_identity
def test_real_materializer_rejects_a_digest_that_does_not_match(tmp_path, request):
    assert request.config.getoption("--run-authoritative-linux-lock-tests")
    assert os.name == "posix" and os.geteuid() == 0

    script = _materializer_script(tmp_path, HELPER_PROBE)
    completed = subprocess.run(
        [sys.executable, str(script), "materialize-helper", "0" * 64],
        text=True, capture_output=True, check=False, timeout=30,
    )

    assert completed.returncode == 70
    assert "could not be materialized" in completed.stderr
    assert completed.stdout.strip() == ""
    # A rejected materialization leaves nothing behind for a later deploy.
    assert not list(Path("/tmp").glob("axisai-deploy-helper.*"))

@pytest.mark.parametrize("status", [1,64,70,71,72,124])
def test_engine_failure_is_propagated_once_without_shell_rollback(bash_executable,host_fixture,status):
    f=host_fixture()
    result=f.run(bash_executable,FAKE_ENGINE_EXIT=str(status))
    assert result.returncode==status
    trace=f.trace_text()
    assert trace.count("ENGINE_PROOF=")==1
    assert "git reset" not in trace and "docker " not in trace
    assert "deployment verified" not in result.stderr
    assert not list(f.deploy_dir.glob(".axisai-build-*"))
    assert not list(f.deploy_dir.glob(".axisai-r6-work.*"))
    assert _run(["git","-C",str(f.deploy_dir),"rev-parse","HEAD"])==f.prev_commit

def test_transaction_process_has_a_bounded_outer_timeout(bash_executable,host_fixture):
    f=host_fixture(timeout_hang_phase="transaction",timeout_hang_command="transaction_engine")
    result=f.run(bash_executable)
    assert result.returncode==124
    trace=f.trace_text()
    assert trace.count("TIMEOUT_HANG phase=transaction operation=transaction_engine")==1
    bound=HOST_PHASE_SECONDS["git_preparation"]+HOST_PHASE_SECONDS["release_forward"]+max(contract.HOST_FAILURE_TAIL_SECONDS,contract.HOST_SUCCESS_TAIL_SECONDS)
    assert f"--kill-after=2s {bound - 2}s python3 -I -B " in trace
    assert "ENGINE_PROOF=" not in trace and "git reset" not in trace
    assert "deployment verified" not in result.stderr
    assert not list(f.deploy_dir.glob(".axisai-build-*"))

@pytest.mark.parametrize("url", ["","http://fitness.example/health",
    "https://user:password@fitness.example/health",
    pytest.param("https://fitness.example/health\n", marks=pytest.mark.skipif(
        os.name == "nt", reason="Git Bash strips terminal LF in Windows argv; verified on Linux"))])
def test_public_origin_is_required_and_validated_before_git_or_engine(bash_executable,host_fixture,url):
    f=host_fixture()
    result=f.run(bash_executable,public_health_url=url)
    assert result.returncode!=0
    assert "git " not in f.trace_text() and "ENGINE_PROOF=" not in f.trace_text()
    assert "docker " not in f.trace_text()

def test_cleanup_total_ceiling_fits_the_reserved_phase(bash_executable,host_fixture):
    f=host_fixture()
    result=f.run(bash_executable)
    assert result.returncode==0, result.stderr
    grants=re.findall(r"--kill-after=(\d+)s (\d+)s rm -[fr]+ -- ",f.trace_text())
    assert grants
    assert sum(int(grace)+int(limit) for grace,limit in grants)<=HOST_PHASE_SECONDS["cleanup"]
