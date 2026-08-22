from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
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
    )
    return result.stdout.strip()


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
        fake_bin.mkdir()
        trace.write_text("", encoding="utf-8")

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
exit "${FLOCK_EXIT:-0}"
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
        # Git for Windows resolves its bundled curl ahead of an extensionless
        # PATH shim. BASH_ENV keeps the test hermetic by delegating that command
        # name to the executable fake; Linux still uses the same fake bytes.
        _write_executable(
            fake_bin / "bash-env",
            (
                'curl() { "$FAKE_BIN/curl" "$@"; }\n'
                'git() { "$FAKE_BIN/git" "$@"; }\n'
                'sleep() { "$FAKE_BIN/sleep" "$@"; }\n'
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
    )
    assert result.returncode == 0, result.stderr


def test_lock_contention_fails_before_git_or_docker(bash_executable, host_fixture):
    fixture = host_fixture(flock_exit=1)
    result = fixture.run(bash_executable)

    assert result.returncode == 73
    assert "deployment lock unavailable" in result.stderr
    assert fixture.trace.read_text(encoding="utf-8") == "flock -w 60 9\n"


def test_invalid_sha_fails_after_lock_without_git_or_docker(bash_executable, host_fixture):
    fixture = host_fixture()
    result = fixture.run(bash_executable, deploy_sha="not-a-sha")

    assert result.returncode == 64
    assert "DEPLOY_SHA must be lowercase 40-hex" in result.stderr
    assert fixture.trace.read_text(encoding="utf-8") == "flock -w 60 9\n"


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
    assert trace.rstrip().endswith("docker image prune -f")


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
    assert trace.count(f"git reset --hard {fixture.prev_commit}") == 1
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
    assert trace.count(f"git reset --hard {fixture.prev_commit}") == 1
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
    assert trace.count(f"git reset --hard {fixture.prev_commit}") == 1
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
    assert trace.count(f"git reset --hard {fixture.prev_commit}") == 1


@pytest.mark.parametrize("operation", ["build", "up", "ps"])
def test_candidate_compose_failure_rolls_back_exactly_once(
    bash_executable, host_fixture, operation
):
    fixture = host_fixture(fail_candidate_docker_command=operation)
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.count(f"git reset --hard {fixture.prev_commit}") == 1
    assert "rollback verified" in result.stderr
    assert _run(["git", "-C", str(fixture.deploy_dir), "rev-parse", "HEAD"]) == fixture.prev_commit


def test_prune_failure_rolls_back_exactly_once(bash_executable, host_fixture):
    fixture = host_fixture(fail_prune=True)
    result = fixture.run(bash_executable)

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.count("docker image prune -f") == 1
    assert trace.count(f"git reset --hard {fixture.prev_commit}") == 1
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
    assert trace.count(f"git reset --hard {fixture.prev_commit}") == 1
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
    assert trace.index("http://127.0.0.1:5000/health?deep=1") < trace.index(
        "https://fitness.example/health"
    )
    public_calls = [
        line for line in trace.splitlines()
        if line.startswith("curl ") and "https://fitness.example/health" in line
    ]
    assert len(public_calls) == 12
    assert all("--connect-timeout 2 --max-time 5" in line for line in public_calls)
    assert trace.count(f"git reset --hard {fixture.prev_commit}") == 1


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
    assert first.stdout is not None
    assert first.stdout.readline().strip() == "FAKE_LOCK_READY"
    assert ready_file.exists()

    second = fixture.run(
        bash_executable,
        TRACE_FILE=_bash_path(second_trace),
        **shared,
    )
    hold_file.unlink()
    first_stdout, first_stderr = first.communicate()

    assert first.returncode == 0, (first_stdout, first_stderr)
    assert second.returncode == 73
    assert second_trace.read_text(encoding="utf-8") == "flock -w 60 9\n"
