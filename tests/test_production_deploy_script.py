from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
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
        health_code: int = 200,
        health_status: str = "ok",
        health_revision: str = "",
        public_health_code: int = 200,
        rollback_health_has_revision: bool = True,
    ) -> HostFixture:
        origin = tmp_path / "origin.git"
        source = tmp_path / "source"
        deploy_dir = tmp_path / "production"
        fake_bin = tmp_path / "fake-bin"
        trace = tmp_path / "trace.log"
        docker_state = tmp_path / "docker-revision"
        docker_exec_count = tmp_path / "docker-exec-count"
        fake_bin.mkdir()
        trace.write_text("", encoding="utf-8")

        _run(["git", "init", "--bare", str(origin)])
        _run(["git", "init", "-b", "main", str(source)])
        _run(["git", "-C", str(source), "config", "user.email", "deploy-test@example.com"])
        _run(["git", "-C", str(source), "config", "user.name", "Deploy Test"])
        (source / "release.txt").write_text("previous\n", encoding="utf-8")
        _run(["git", "-C", str(source), "add", "release.txt"])
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
  fi
  while [[ -n "${FLOCK_HOLD_FILE:-}" && -e "$FLOCK_HOLD_FILE" ]]; do
    sleep 0.05
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
if [[ " $* " == *' build '* || " $* " == *' up '* ]]; then
  printf '%s' "$revision" > "$DOCKER_STATE_FILE"
  printf 'APP_REVISION=%s\n' "$revision" >> "$TRACE_FILE"
fi
if [[ " $* " == *' exec -T web printenv APP_REVISION '* ]]; then
  count=0
  [[ -f "$DOCKER_EXEC_COUNT_FILE" ]] && count="$(cat "$DOCKER_EXEC_COUNT_FILE")"
  count=$((count + 1))
  printf '%s' "$count" > "$DOCKER_EXEC_COUNT_FILE"
  if [[ "$count" -eq 1 && -n "${FAKE_CANDIDATE_CONTAINER_REVISION:-}" ]]; then
    printf '%s\n' "$FAKE_CANDIDATE_CONTAINER_REVISION"
  else
    cat "$DOCKER_STATE_FILE"
    printf '\n'
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
if [[ "$url" == https://* ]]; then
  code="$FAKE_PUBLIC_HEALTH_CODE"
elif [[ "$revision" == "$FAKE_CANDIDATE_SHA" ]]; then
  code="$FAKE_CANDIDATE_HEALTH_CODE"
  status="$FAKE_CANDIDATE_HEALTH_STATUS"
  [[ -n "$FAKE_CANDIDATE_HEALTH_REVISION" ]] && revision="$FAKE_CANDIDATE_HEALTH_REVISION"
else
  include_revision="$FAKE_ROLLBACK_HEALTH_HAS_REVISION"
fi
if [[ -n "$output" ]]; then
  if [[ "$include_revision" == 1 ]]; then
    printf '{"status":"%s","revision":"%s"}\n' "$status" "$revision" > "$output"
  else
    printf '{"status":"%s"}\n' "$status" > "$output"
  fi
fi
printf '%s' "$code"
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
                "DOCKER_EXEC_COUNT_FILE": _bash_path(docker_exec_count),
                "FAKE_CANDIDATE_CONTAINER_REVISION": container_revision,
                "FAKE_CANDIDATE_SHA": candidate_commit,
                "FAKE_CANDIDATE_HEALTH_CODE": str(health_code),
                "FAKE_CANDIDATE_HEALTH_STATUS": health_status,
                "FAKE_CANDIDATE_HEALTH_REVISION": health_revision,
                "FAKE_PUBLIC_HEALTH_CODE": str(public_health_code),
                "FAKE_ROLLBACK_HEALTH_HAS_REVISION": "1" if rollback_health_has_revision else "0",
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


def test_public_health_runs_after_internal_gate_and_failure_rolls_back(
    bash_executable, host_fixture
):
    fixture = host_fixture()
    result = fixture.run(
        bash_executable,
        public_health_url="https://fitness.example/health",
        FAKE_PUBLIC_HEALTH_CODE="503",
    )

    assert result.returncode != 0
    trace = fixture.trace.read_text(encoding="utf-8")
    assert trace.index("http://127.0.0.1:5000/health?deep=1") < trace.index(
        "https://fitness.example/health"
    )
    assert f"git reset --hard {fixture.prev_commit}" in trace


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
    deadline = time.monotonic() + 5
    while not ready_file.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready_file.exists(), "first deployment did not enter the fake lock"

    second = fixture.run(
        bash_executable,
        TRACE_FILE=_bash_path(second_trace),
        **shared,
    )
    hold_file.unlink()
    first_stdout, first_stderr = first.communicate(timeout=10)

    assert first.returncode == 0, (first_stdout, first_stderr)
    assert second.returncode == 73
    assert second_trace.read_text(encoding="utf-8") == "flock -w 60 9\n"
