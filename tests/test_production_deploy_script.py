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


ROOT = Path(__file__).resolve().parents[1]
HOST_SCRIPT = ROOT / "scripts" / "production_deploy.sh"


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
    temp_root = ROOT / ".pytest_cache" / "production-deploy"
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
    environment: dict[str, str]

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
        public_health_url: str = "",
        **environment: str,
    ) -> subprocess.CompletedProcess[str]:
        command = self.command(bash_executable, deploy_sha)
        command.append(public_health_url)
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            env={**self.environment, **environment},
            timeout=600,
        )

    def run_with_preheld_lock(
        self,
        bash_executable: str,
        *,
        acquire: bool = True,
        preheld_path: Path | None = None,
        **environment: str,
    ) -> subprocess.CompletedProcess[str]:
        wrapper = (
            'exec 9>"$1"; shift; '
            'if [[ "$WRAPPER_ACQUIRE_LOCK" == 1 ]]; then '
            'flock -w 60 9 || exit 73; fi; '
            'script_path="$1"; shift; '
            'AXISAI_DEPLOY_LOCK_FD=0 source "$script_path" "$@" <&9'
        )
        return subprocess.run(
            [
                bash_executable, "--noprofile", "--norc", "-c", wrapper,
                "preheld-wrapper",
                _bash_path(preheld_path or (
                    self.deploy_dir / ".axisai-production-deploy.lock"
                )),
                _bash_path(HOST_SCRIPT),
                self.candidate_commit,
                _bash_path(self.deploy_dir),
                "",
            ],
            text=True,
            capture_output=True,
            check=False,
            env={
                **self.environment,
                "WRAPPER_ACQUIRE_LOCK": "1" if acquire else "0",
                **environment,
            },
            timeout=600,
        )


@pytest.fixture
def host_fixture(tmp_path: Path):
    def make(
        *,
        flock_exit: int = 0,
        container_revision: str = "",
        rollback_container_revision: str = "",
        health_code: int = 200,
        health_status: str = "ok",
        health_revision: str = "",
        candidate_health_failures: int | None = None,
        candidate_curl_exit: int = 0,
        public_health_code: int = 200,
        public_health_failures: int | None = None,
        public_curl_exit: int = 0,
        rollback_health_has_revision: bool = True,
        rollback_health_failures: int = 0,
        rollback_curl_exit: int = 0,
        previous_has_hardened_marker: bool = False,
        fail_candidate_docker_command: str = "",
        fail_rollback_docker_command: str = "",
        fail_prune: bool = False,
        timeout_hang_phase: str = "",
        timeout_hang_command: str = "",
        clock_readings: tuple[int, ...] = (),
        initial_clock_mode: str = "",
        post_checkout_clock_mode: str = "",
    ) -> HostFixture:
        origin = tmp_path / "origin.git"
        source = tmp_path / "source"
        deploy_dir = tmp_path / "production"
        fake_bin = tmp_path / "fake-bin"
        trace = tmp_path / "trace.log"
        docker_state = tmp_path / "docker-revision"
        candidate_health_count = tmp_path / "candidate-health-count"
        rollback_health_count = tmp_path / "rollback-health-count"
        public_health_count = tmp_path / "public-health-count"
        fake_clock = tmp_path / "fake-clock"
        fake_clock_count = tmp_path / "fake-clock-count"
        fake_clock_readings = tmp_path / "fake-clock-readings"
        fake_clock_latch = tmp_path / "fake-clock-latch"
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
        if previous_has_hardened_marker:
            marker = source / "scripts" / "production_deploy.sh"
            marker.parent.mkdir()
            marker.write_text("# hardened revision-health contract\n", encoding="utf-8")
        _run(["git", "-C", str(source), "add", "."])
        _run(["git", "-C", str(source), "commit", "-m", "previous"])
        prev_commit = _run(["git", "-C", str(source), "rev-parse", "HEAD"])
        _run(["git", "-C", str(source), "remote", "add", "origin", str(origin)])
        _run(["git", "-C", str(source), "push", "-u", "origin", "main"])
        _run(["git", "clone", "--branch", "main", str(origin), str(deploy_dir)])
        (source / "release.txt").write_text("candidate\n", encoding="utf-8")
        _run(["git", "-C", str(source), "add", "release.txt"])
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
if [[ -n "${FLOCK_STATE_DIR:-}" ]]; then
  if ! mkdir "$FLOCK_STATE_DIR" 2>/dev/null; then
    exit 1
  fi
  if [[ -n "${FLOCK_READY_FILE:-}" ]]; then
    : > "$FLOCK_READY_FILE"
    printf 'FAKE_LOCK_READY\n'
  fi
  while [[ -n "${FLOCK_HOLD_FILE:-}" && -e "$FLOCK_HOLD_FILE" ]]; do
    /usr/bin/sleep 0.05
  done
  rmdir "$FLOCK_STATE_DIR"
fi
if [[ " $* " == ' -n 8 ' ]]; then
  if [[ "${FAKE_PREHELD_LOCK_HELD:-0}" == 1 ]]; then exit 1; fi
  exit 0
fi
exit "${FLOCK_EXIT:-0}"
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
  /proc/self/fd/0) printf '%s\n' "${FAKE_INHERITED_LOCK_ID:-stdin}" ;;
  *.axisai-production-deploy.lock) printf '%s\n' stable ;;
  *) exit 65 ;;
esac
""",
        )
        _write_executable(
            fake_bin / "docker",
            r"""#!/usr/bin/env bash
printf 'docker' >> "$TRACE_FILE"
printf ' %s' "$@" >> "$TRACE_FILE"
printf '\n' >> "$TRACE_FILE"

override=''
previous=''
for argument in "$@"; do
  if [[ "$previous" == '-f' ]]; then override="$argument"; fi
  previous="$argument"
done
revision=''
if [[ -n "$override" && -f "$override" ]]; then
  revision="$(grep -m1 'APP_REVISION:' "$override" | cut -d "'" -f 2)"
fi
operation=''
if [[ " $* " == *' build '* ]]; then operation=build; fi
if [[ " $* " == *' up -d '* ]]; then operation=up; fi
if [[ " $* " == *' ps '* ]]; then operation=ps; fi
if [[ " $* " == *' image prune '* ]]; then operation=prune; fi
if [[ "$operation" == prune && "$FAKE_FAIL_PRUNE" == 1 ]]; then exit 41; fi
if [[ -n "$FAKE_FAIL_CANDIDATE_DOCKER_COMMAND" && "$revision" == "$FAKE_CANDIDATE_SHA" && "$operation" == "$FAKE_FAIL_CANDIDATE_DOCKER_COMMAND" ]]; then
  exit 42
fi
if [[ -n "$FAKE_FAIL_ROLLBACK_DOCKER_COMMAND" && "$revision" != "$FAKE_CANDIDATE_SHA" && "$operation" == "$FAKE_FAIL_ROLLBACK_DOCKER_COMMAND" ]]; then
  exit 43
fi
if [[ "$operation" == build || "$operation" == up ]]; then
  printf '%s' "$revision" > "$DOCKER_STATE_FILE"
  printf 'APP_REVISION=%s\n' "$revision" >> "$TRACE_FILE"
fi
if [[ " $* " == *' exec -T web printenv APP_REVISION '* ]]; then
  if [[ "$revision" == "$FAKE_CANDIDATE_SHA" && -n "${FAKE_CANDIDATE_CONTAINER_REVISION:-}" ]]; then
    printf '%s\n' "$FAKE_CANDIDATE_CONTAINER_REVISION"
  elif [[ "$revision" != "$FAKE_CANDIDATE_SHA" && -n "${FAKE_ROLLBACK_CONTAINER_REVISION:-}" ]]; then
    printf '%s\n' "$FAKE_ROLLBACK_CONTAINER_REVISION"
  else
    printf '%s\n' "$revision"
  fi
fi
exit 0
""",
        )
        _write_executable(
            fake_bin / "curl",
            r"""#!/usr/bin/env bash
printf 'curl' >> "$TRACE_FILE"
printf ' %s' "$@" >> "$TRACE_FILE"
printf '\n' >> "$TRACE_FILE"
output=''
previous=''
for argument in "$@"; do
  if [[ "$previous" == '--output' ]]; then output="$argument"; fi
  previous="$argument"
done
url="${@: -1}"
revision="$(cat "$DOCKER_STATE_FILE")"
code=200
status=ok
include_revision=1
exit_code=0
counter_file=''
failures=0
if [[ "$url" == https://* ]]; then
  counter_file="$PUBLIC_HEALTH_COUNT_FILE"
  failures="$FAKE_PUBLIC_HEALTH_FAILURES"
  code="$FAKE_PUBLIC_HEALTH_CODE"
  exit_code="$FAKE_PUBLIC_CURL_EXIT"
elif [[ "$revision" == "$FAKE_CANDIDATE_SHA" ]]; then
  counter_file="$CANDIDATE_HEALTH_COUNT_FILE"
  failures="$FAKE_CANDIDATE_HEALTH_FAILURES"
  code="$FAKE_CANDIDATE_HEALTH_CODE"
  status="$FAKE_CANDIDATE_HEALTH_STATUS"
  [[ -n "$FAKE_CANDIDATE_HEALTH_REVISION" ]] && revision="$FAKE_CANDIDATE_HEALTH_REVISION"
  exit_code="$FAKE_CANDIDATE_CURL_EXIT"
else
  counter_file="$ROLLBACK_HEALTH_COUNT_FILE"
  failures="$FAKE_ROLLBACK_HEALTH_FAILURES"
  include_revision="$FAKE_ROLLBACK_HEALTH_HAS_REVISION"
  exit_code="$FAKE_ROLLBACK_CURL_EXIT"
fi
attempt=0
[[ -f "$counter_file" ]] && attempt="$(cat "$counter_file")"
attempt=$((attempt + 1))
printf '%s' "$attempt" > "$counter_file"
printf 'CURL_REVISION=%s ATTEMPT=%s\n' "$revision" "$attempt" >> "$TRACE_FILE"
if [[ "$attempt" -gt "$failures" ]]; then
  code=200
  exit_code=0
fi
if [[ -n "$output" ]]; then
  if [[ "$include_revision" == 1 ]]; then
    printf '{"status":"%s","revision":"%s"}\n' "$status" "$revision" > "$output"
  else
    printf '{"status":"%s"}\n' "$status" > "$output"
  fi
fi
printf '%s' "$code"
exit "$exit_code"
""",
        )
        _write_executable(
            fake_bin / "sleep",
            r"""#!/usr/bin/env bash
printf 'sleep' >> "$TRACE_FILE"
printf ' %s' "$@" >> "$TRACE_FILE"
printf '\n' >> "$TRACE_FILE"
exit 0
""",
        )
        _write_executable(
            fake_bin / "date",
            r"""#!/usr/bin/env bash
cat "$FAKE_CLOCK_FILE"
""",
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
elif [[ "$command_name" == python && "${1:-}" == -c && "${2:-}" == *monotonic_ns* ]]; then
  operation=clock
fi
if [[ "$operation" == clock ]]; then
  clock_count="$(cat "$FAKE_CLOCK_COUNT_FILE")"
  clock_count=$((clock_count + 1))
  printf '%s' "$clock_count" > "$FAKE_CLOCK_COUNT_FILE"
  clock_mode=''
  if [[ "$clock_count" == 1 && -n "$FAKE_INITIAL_CLOCK_MODE" ]]; then
    clock_mode="$FAKE_INITIAL_CLOCK_MODE"
  elif [[ -n "$FAKE_POST_CHECKOUT_CLOCK_MODE" ]]; then
    if [[ -f "$FAKE_CLOCK_LATCH_FILE" ]]; then
      clock_mode="$FAKE_POST_CHECKOUT_CLOCK_MODE"
    elif [[ "$("$REAL_GIT" -C "$FAKE_DEPLOY_DIR" rev-parse HEAD)" == "$FAKE_CANDIDATE_SHA" ]]; then
      : > "$FAKE_CLOCK_LATCH_FILE"
      clock_mode="$FAKE_POST_CHECKOUT_CLOCK_MODE"
    fi
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
  curl) source "$FAKE_BIN/curl" "$@" ;;
  date) source "$FAKE_BIN/date" "$@" ;;
  docker) source "$FAKE_BIN/docker" "$@" ;;
  git) source "$FAKE_BIN/git" "$@" ;;
  python) "$REAL_PYTHON" "$@" ;;
  sleep) source "$FAKE_BIN/sleep" "$@" ;;
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
                "PATH": f"{_bash_path(fake_bin)}:{environment.get('PATH', '')}",
                "FAKE_BIN": _bash_path(fake_bin),
                "BASH_ENV": _bash_path(fake_bin / "bash-env"),
                "TRACE_FILE": _bash_path(trace),
                "REAL_GIT": Path(real_git).resolve().as_posix(),
                "REAL_PYTHON": Path(sys.executable).resolve().as_posix(),
                "FLOCK_EXIT": str(flock_exit),
                "DOCKER_STATE_FILE": _bash_path(docker_state),
                "FAKE_CANDIDATE_CONTAINER_REVISION": container_revision,
                "FAKE_ROLLBACK_CONTAINER_REVISION": rollback_container_revision,
                "FAKE_CANDIDATE_SHA": candidate_commit,
                "FAKE_CANDIDATE_HEALTH_CODE": str(health_code),
                "FAKE_CANDIDATE_HEALTH_STATUS": health_status,
                "FAKE_CANDIDATE_HEALTH_REVISION": health_revision,
                "FAKE_CANDIDATE_HEALTH_FAILURES": str(
                    candidate_health_failures
                    if candidate_health_failures is not None
                    else (0 if health_code == 200 else 999)
                ),
                "FAKE_CANDIDATE_CURL_EXIT": str(candidate_curl_exit),
                "FAKE_PUBLIC_HEALTH_CODE": str(public_health_code),
                "FAKE_PUBLIC_HEALTH_FAILURES": str(
                    public_health_failures
                    if public_health_failures is not None
                    else (0 if public_health_code == 200 else 999)
                ),
                "FAKE_PUBLIC_CURL_EXIT": str(public_curl_exit),
                "FAKE_ROLLBACK_HEALTH_HAS_REVISION": "1" if rollback_health_has_revision else "0",
                "FAKE_ROLLBACK_HEALTH_FAILURES": str(rollback_health_failures),
                "FAKE_ROLLBACK_CURL_EXIT": str(rollback_curl_exit),
                "CANDIDATE_HEALTH_COUNT_FILE": _bash_path(candidate_health_count),
                "ROLLBACK_HEALTH_COUNT_FILE": _bash_path(rollback_health_count),
                "PUBLIC_HEALTH_COUNT_FILE": _bash_path(public_health_count),
                "FAKE_FAIL_CANDIDATE_DOCKER_COMMAND": fail_candidate_docker_command,
                "FAKE_FAIL_ROLLBACK_DOCKER_COMMAND": fail_rollback_docker_command,
                "FAKE_FAIL_PRUNE": "1" if fail_prune else "0",
                "FAKE_CLOCK_FILE": _bash_path(fake_clock),
                "FAKE_CLOCK_COUNT_FILE": _bash_path(fake_clock_count),
                "FAKE_CLOCK_READINGS_FILE": _bash_path(fake_clock_readings),
                "FAKE_CLOCK_LATCH_FILE": _bash_path(fake_clock_latch),
                "FAKE_INITIAL_CLOCK_MODE": initial_clock_mode,
                "FAKE_POST_CHECKOUT_CLOCK_MODE": post_checkout_clock_mode,
                "FAKE_DEPLOY_DIR": _bash_path(deploy_dir),
                "FAKE_TIMEOUT_HANG_PHASE": timeout_hang_phase,
                "FAKE_TIMEOUT_HANG_COMMAND": timeout_hang_command,
            }
        )
        return HostFixture(
            deploy_dir,
            fake_bin,
            trace,
            prev_commit,
            candidate_commit,
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


def test_lock_contention_fails_before_git_or_docker(bash_executable, host_fixture):
    fixture = host_fixture(flock_exit=1)
    result = fixture.run(bash_executable)

    assert result.returncode == 73
    assert "deployment lock unavailable" in result.stderr
    assert fixture.trace.read_text(encoding="utf-8") == "flock -w 60 9\n"


def test_inherited_lock_reuses_verified_stdin_fd_without_reacquire_gap(
        bash_executable, host_fixture):
    fixture = host_fixture()

    result = fixture.run_with_preheld_lock(
        bash_executable,
        FAKE_INHERITED_LOCK_ID="stable",
        FAKE_PREHELD_LOCK_HELD="1",
    )

    assert result.returncode == 0, result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.splitlines()[:5] == [
        "flock -w 60 9",
        "stat -Lc %d:%i -- /proc/self/fd/0",
        f"stat -Lc %d:%i -- {_bash_path(fixture.deploy_dir)}/.axisai-production-deploy.lock",
        "flock -n 8",
        "flock -n 0",
    ]
    assert trace.count("flock -w 60 9\n") == 1


def test_inherited_lock_marker_with_wrong_inode_fails_before_git_or_docker(
        bash_executable, host_fixture, tmp_path):
    fixture = host_fixture()
    wrong_lock = tmp_path / "wrong-lock"

    result = fixture.run_with_preheld_lock(
        bash_executable,
        preheld_path=wrong_lock,
        FAKE_INHERITED_LOCK_ID="wrong",
    )

    assert result.returncode == 73
    trace = fixture.trace.read_text(encoding="utf-8")
    assert "git " not in trace
    assert "docker " not in trace
    assert "flock -n 0" not in trace


def test_inherited_correct_inode_without_preheld_lock_fails_before_mutation(
        bash_executable, host_fixture):
    fixture = host_fixture()

    result = fixture.run_with_preheld_lock(
        bash_executable,
        acquire=False,
        FAKE_INHERITED_LOCK_ID="stable",
        FAKE_PREHELD_LOCK_HELD="0",
    )

    assert result.returncode == 73
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.splitlines()[:3] == [
        "stat -Lc %d:%i -- /proc/self/fd/0",
        f"stat -Lc %d:%i -- {_bash_path(fixture.deploy_dir)}/.axisai-production-deploy.lock",
        "flock -n 8",
    ]
    assert "flock -n 0" not in trace
    assert "git " not in trace
    assert "docker " not in trace


def test_inherited_lock_marker_alone_cannot_bypass_normal_locking(
        bash_executable, host_fixture):
    fixture = host_fixture()

    result = fixture.run(
        bash_executable,
        AXISAI_DEPLOY_LOCK_FD="0",
        FAKE_INHERITED_LOCK_ID="stdin",
    )

    assert result.returncode == 73
    trace = fixture.trace.read_text(encoding="utf-8")
    assert "git " not in trace
    assert "docker " not in trace
    assert "flock -n 0" not in trace


def test_invalid_sha_fails_after_lock_without_git_or_docker(bash_executable, host_fixture):
    fixture = host_fixture()
    result = fixture.run(bash_executable, deploy_sha="not-a-sha")

    assert result.returncode == 64
    assert "DEPLOY_SHA must be lowercase 40-hex" in result.stderr
    assert fixture.trace.read_text(encoding="utf-8") == "flock -w 60 9\n"


def test_host_transaction_budget_preserves_ssm_margin_and_rollback_reserve(
    bash_executable, host_fixture
):
    fixture = host_fixture()
    result = fixture.run(bash_executable)

    assert result.returncode == 0, result.stderr
    budget = re.search(
        r"host transaction budget: limit=(\d+) worst_case=(\d+) lock=(\d+) clock=(\d+) "
        r"clock_state=(\d+) rollback_reset=(\d+) "
        r"preflight=(\d+) candidate=(\d+) diagnostics=(\d+) rollback=(\d+) "
        r"post_lock=(\d+) timeout_grace=(\d+) cleanup=(\d+) ssm_margin=(\d+)",
        result.stderr,
    )
    assert budget is not None
    (
        limit,
        worst_case,
        lock,
        clock,
        clock_state,
        rollback_reset,
        preflight,
        candidate,
        diagnostics,
        rollback,
        post_lock,
        grace,
        cleanup,
        margin,
    ) = map(int, budget.groups())
    assert limit + margin == 1800
    assert preflight + candidate + diagnostics + rollback == post_lock
    assert lock + clock + clock_state + post_lock + grace + cleanup == worst_case < limit
    assert rollback >= rollback_reset + (30 * 5) + (29 * 5)
    assert candidate >= (30 * 5) + (29 * 5) + (12 * 5) + (11 * 5)
    trace = fixture.trace.read_text(encoding="utf-8")
    assert "timeout phase=preflight" in trace
    assert "timeout phase=candidate" in trace
    assert "monotonic_ns" in trace
    assert " date +%s" not in trace
    phase_limits = {"preflight": preflight, "candidate": candidate}
    phase_grants = re.findall(
        r"timeout phase=(preflight|candidate) --signal=TERM --kill-after=2s (\d+)s ",
        trace,
    )
    assert phase_grants
    assert all(int(grant) <= phase_limits[phase] for phase, grant in phase_grants)


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


@pytest.mark.parametrize("clock_mode", ["fail", "hang"])
def test_post_checkout_clock_failure_still_attempts_one_bounded_exact_reset(
    bash_executable, host_fixture, clock_mode
):
    fixture = host_fixture(post_checkout_clock_mode=clock_mode)
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1
    assert (
        f"timeout phase=rollback --signal=TERM --kill-after=2s 5s "
        f"git reset --hard {fixture.prev_commit}"
    ) in trace
    assert "rollback failed verification" in result.stderr
    assert "rollback verified" not in result.stderr
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.prev_commit


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
    assert "timeout phase=preflight --signal=TERM --kill-after=2s 90s git fetch" in trace
    assert "git reset --hard" not in trace
    assert "docker " not in trace


def test_candidate_deadline_hang_transitions_to_exactly_one_rollback(
    bash_executable, host_fixture
):
    fixture = host_fixture(
        timeout_hang_phase="candidate",
        timeout_hang_command="docker:build",
    )
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.count("TIMEOUT_HANG phase=candidate operation=docker:build") == 1
    assert "timeout phase=candidate --signal=TERM --kill-after=2s 820s docker" in trace
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1
    assert "rollback verified" in result.stderr


def test_diagnostic_hang_cannot_consume_rollback_reserve(bash_executable, host_fixture):
    fixture = host_fixture(
        fail_candidate_docker_command="build",
        timeout_hang_phase="diagnostics",
        timeout_hang_command="docker:ps",
    )
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.count("TIMEOUT_HANG phase=diagnostics operation=docker:ps") == 1
    assert "timeout phase=diagnostics --signal=TERM --kill-after=2s 60s docker" in trace
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1
    assert "rollback verified" in result.stderr


def test_rollback_operation_hang_is_bounded_and_reported(bash_executable, host_fixture):
    fixture = host_fixture(
        container_revision="b" * 40,
        timeout_hang_phase="rollback",
        timeout_hang_command="docker:build",
    )
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.count("TIMEOUT_HANG phase=rollback operation=docker:build") == 1
    assert "timeout phase=rollback --signal=TERM --kill-after=2s 620s docker" in trace
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1
    assert "rollback failed verification" in result.stderr
    assert "rollback verified" not in result.stderr
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.prev_commit


def test_success_deploys_exact_candidate_and_verifies_revision(bash_executable, host_fixture):
    fixture = host_fixture()
    result = fixture.run(bash_executable)

    assert result.returncode == 0, result.stderr
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.candidate_commit
    trace = fixture.trace.read_text(encoding="utf-8")
    assert f"git fetch origin main --prune" in trace
    assert f"git reset --hard {fixture.candidate_commit}" in trace
    assert f"APP_REVISION={fixture.candidate_commit}" in trace
    assert "exec -T web printenv APP_REVISION" in trace
    assert "http://127.0.0.1:5000/health?deep=1" in trace
    compose_lines = [line for line in trace.splitlines() if line.startswith("docker compose ")]
    expected_prefix = f"docker compose -f {_bash_path(fixture.deploy_dir / 'docker-compose.yml')} -f "
    assert compose_lines
    assert all(line.startswith(expected_prefix) for line in compose_lines)
    trace_lines = trace.splitlines()
    prune_index = trace_lines.index("docker image prune -f")
    cleanup_indices = [
        index for index, line in enumerate(trace_lines) if " 5s rm -f -- " in line
    ]
    assert cleanup_indices
    assert prune_index < cleanup_indices[-1]


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


def test_wrong_running_revision_rolls_back_despite_health_200(bash_executable, host_fixture):
    fixture = host_fixture(container_revision="b" * 40, health_code=200)
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.prev_commit
    trace = fixture.trace.read_text(encoding="utf-8")
    assert f"git reset --hard {fixture.prev_commit}" in trace
    assert f"APP_REVISION={fixture.prev_commit}" in trace
    assert "rollback verified" in result.stderr


def test_wrong_deep_health_revision_rolls_back(bash_executable, host_fixture):
    fixture = host_fixture(health_revision="c" * 40)
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.prev_commit
    assert "deep health revision mismatch" in result.stderr


def test_candidate_health_retries_bounded_calls_until_delayed_success(
    bash_executable, host_fixture
):
    fixture = host_fixture(
        health_code=503,
        candidate_health_failures=2,
        candidate_curl_exit=28,
    )
    result = fixture.run(bash_executable)

    assert result.returncode == 0, result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    candidate_attempts = [
        line for line in trace.splitlines()
        if line.startswith(f"CURL_REVISION={fixture.candidate_commit}")
    ]
    assert len(candidate_attempts) == 3
    curl_lines = [line for line in trace.splitlines() if line.startswith("curl ")]
    assert curl_lines
    assert all("--connect-timeout 2 --max-time 5" in line for line in curl_lines)
    assert f"git reset --hard {fixture.prev_commit}" not in trace


def test_hung_candidate_health_exhausts_bound_then_rolls_back_once(
    bash_executable, host_fixture
):
    fixture = host_fixture(
        health_code=503,
        candidate_health_failures=999,
        candidate_curl_exit=28,
    )
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.count(f"CURL_REVISION={fixture.candidate_commit}") == 30
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1
    assert "rollback verified" in result.stderr
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.prev_commit


def test_rollback_health_retries_bounded_calls_until_delayed_success(
    bash_executable, host_fixture
):
    fixture = host_fixture(
        container_revision="b" * 40,
        rollback_health_failures=2,
        rollback_curl_exit=28,
    )
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.count(f"CURL_REVISION={fixture.prev_commit}") == 3
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1
    assert "rollback verified" in result.stderr


def test_hung_rollback_health_exhausts_bound_and_reports_failure(
    bash_executable, host_fixture
):
    fixture = host_fixture(
        container_revision="b" * 40,
        rollback_health_failures=999,
        rollback_curl_exit=28,
    )
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.count(f"CURL_REVISION={fixture.prev_commit}") == 30
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1
    assert "rollback failed verification" in result.stderr
    assert "rollback verified" not in result.stderr
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.prev_commit


def test_public_health_retries_bounded_calls_until_delayed_success(
    bash_executable, host_fixture
):
    fixture = host_fixture(
        public_health_code=503,
        public_health_failures=2,
        public_curl_exit=28,
    )
    result = fixture.run(
        bash_executable,
        public_health_url="https://fitness.example/health",
    )

    assert result.returncode == 0, result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    public_calls = [
        line for line in trace.splitlines()
        if line.startswith("curl ") and "https://fitness.example/health" in line
    ]
    assert len(public_calls) == 3
    assert all("--connect-timeout 2 --max-time 5" in line for line in public_calls)
    assert f"git reset --hard {fixture.prev_commit}" not in trace


def test_old_rollback_without_health_revision_uses_compatibility_proof(
    bash_executable, host_fixture
):
    fixture = host_fixture(
        container_revision="b" * 40,
        rollback_health_has_revision=False,
    )
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    assert "rollback compatibility proof accepted" in result.stderr
    assert "rollback verified" in result.stderr
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.prev_commit


def test_hardened_rollback_cannot_use_missing_revision_compatibility(
    bash_executable, host_fixture
):
    fixture = host_fixture(
        container_revision="b" * 40,
        rollback_health_has_revision=False,
        previous_has_hardened_marker=True,
    )
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    assert "rollback compatibility proof accepted" not in result.stderr
    assert "deep health revision is missing" in result.stderr
    assert "rollback failed verification" in result.stderr
    trace = fixture.trace.read_text(encoding="utf-8")
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1


@pytest.mark.parametrize("operation", ["build", "up", "ps"])
def test_candidate_compose_failure_rolls_back_exactly_once(
    bash_executable, host_fixture, operation
):
    fixture = host_fixture(fail_candidate_docker_command=operation)
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1
    assert "rollback verified" in result.stderr
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.prev_commit


def test_prune_failure_rolls_back_exactly_once(bash_executable, host_fixture):
    fixture = host_fixture(fail_prune=True)
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert _trace_command_count(trace, "docker image prune -f") == 1
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1
    assert "rollback verified" in result.stderr


def test_failed_rollback_revision_is_reported_without_second_attempt(
    bash_executable, host_fixture
):
    fixture = host_fixture(
        container_revision="b" * 40,
        rollback_container_revision="d" * 40,
    )
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1
    assert "running web container revision mismatch" in result.stderr
    assert "rollback failed verification" in result.stderr
    assert "rollback verified" not in result.stderr
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.prev_commit


def test_public_health_runs_after_internal_gate_and_failure_rolls_back(
    bash_executable, host_fixture
):
    fixture = host_fixture(public_health_code=503)
    result = fixture.run(
        bash_executable,
        public_health_url="https://fitness.example/health",
    )

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    curl_calls = [line for line in trace.splitlines() if line.startswith("curl ")]
    internal_call_index = next(
        index for index, line in enumerate(curl_calls)
        if "http://127.0.0.1:5000/health?deep=1" in line
    )
    public_call_index = next(
        index for index, line in enumerate(curl_calls)
        if "https://fitness.example/health" in line
    )
    assert internal_call_index < public_call_index
    public_calls = [
        line for line in curl_calls
        if line.startswith("curl ") and "https://fitness.example/health" in line
    ]
    assert len(public_calls) == 12
    assert all("--connect-timeout 2 --max-time 5" in line for line in public_calls)
    assert _trace_command_count(trace, f"git reset --hard {fixture.prev_commit}") == 1


def test_rapid_candidate_second_invocation_cannot_mutate_while_lock_is_held(
    bash_executable, host_fixture, tmp_path
):
    fixture = host_fixture()
    hold_file = tmp_path / "hold-lock"
    ready_file = tmp_path / "lock-ready"
    state_dir = tmp_path / "fake-lock-state"
    first_trace = tmp_path / "first-trace.log"
    second_trace = tmp_path / "second-trace.log"
    hold_file.write_text("hold", encoding="utf-8")
    first_trace.write_text("", encoding="utf-8")
    second_trace.write_text("", encoding="utf-8")
    shared = {
        "FLOCK_HOLD_FILE": _bash_path(hold_file),
        "FLOCK_READY_FILE": _bash_path(ready_file),
        "FLOCK_STATE_DIR": _bash_path(state_dir),
    }
    first_environment = {
        **fixture.environment,
        **shared,
        "TRACE_FILE": _bash_path(first_trace),
    }
    first = subprocess.Popen(
        fixture.command(bash_executable),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=first_environment,
    )
    try:
        assert first.stdout is not None
        assert _readline_with_timeout(first.stdout).strip() == "FAKE_LOCK_READY"
        assert ready_file.exists()

        second = fixture.run(
            bash_executable,
            TRACE_FILE=_bash_path(second_trace),
            **shared,
        )
    finally:
        hold_file.unlink(missing_ok=True)
        try:
            first_stdout, first_stderr = first.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            first.kill()
            first.communicate()
            raise AssertionError("first deployment did not finish within 600 seconds")

    assert first.returncode == 0, (first_stdout, first_stderr)
    assert second.returncode == 73
    assert second_trace.read_text(encoding="utf-8") == "flock -w 60 9\n"
