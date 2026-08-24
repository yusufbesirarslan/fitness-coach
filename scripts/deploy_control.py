"""Fail-closed validation primitives for the production deploy controller."""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID


SHA_RE = re.compile(r"[0-9a-f]{40}")
INSTANCE_RE = re.compile(r"i-[0-9a-f]{8}(?:[0-9a-f]{9})?")
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
AWS_ERROR_CODE_RE = re.compile(
    r"An error occurred \(([A-Za-z][A-Za-z0-9]*)\) when calling"
)

DELIVERY_TIMEOUT_SECONDS = 60
EXECUTION_TIMEOUT_SECONDS = 1800
AWS_EXPIRY_SECONDS = DELIVERY_TIMEOUT_SECONDS + EXECUTION_TIMEOUT_SECONDS
POLL_HORIZON_SECONDS = 2100
POLL_INTERVAL_SECONDS = 10
INVOCATION_CALL_TIMEOUT_SECONDS = 30
AWS_CLI_CALL_TIMEOUT_SECONDS = 60
GIT_CALL_TIMEOUT_SECONDS = 60
ROOT_OUTER_LOCK_PATH = "/run/lock/axisai-production/production.lock"
OUTER_LOCK_CAPABILITY_FD = 7
# AWS-RunShellScript does not publish a commands-element maximum.  This is a
# deliberately conservative local growth guard for our generated bootstrap.
LOCAL_RUN_SHELL_COMMAND_MAX_CHARS = 65_536
ROOT_OUTER_LOCK_WAIT_SECONDS = 60
ROOT_EXTERNAL_CALL_COUNT = 16
ROOT_EXTERNAL_CALL_MAX_SECONDS = 5  # 4-second timeout plus 1-second kill grace.
PRIVILEGE_DROP_MAX_SECONDS = 5
ROOT_BOOTSTRAP_WORST_CASE_SECONDS = (
    ROOT_OUTER_LOCK_WAIT_SECONDS
    + ROOT_EXTERNAL_CALL_COUNT * ROOT_EXTERNAL_CALL_MAX_SECONDS
    + PRIVILEGE_DROP_MAX_SECONDS
)
# Workflow calls inherit the already-held outer lock, so the helper's 60-second
# direct-entry wait is not part of this path: 4 + 7 + 1560 + 2 + 7.
WORKFLOW_HELPER_WORST_CASE_SECONDS = 1580

ROOT_LOCK_WRAPPER_SOURCE = r'''def _emit(stderr, message):
    if hasattr(stderr, "write"):
        stderr.write(message + "\n")
    else:
        stderr.append(message)


def run(encoded_command, os_module, stat_module, fcntl_module,
        subprocess_module, time_module, stderr):
    run_lock_fd = None
    lock_dir_fd = None
    lock_fd = None
    capability_fd = None
    try:
        command = __import__("base64").b64decode(
            encoded_command, validate=True
        ).decode("utf-8")
        directory_flags = (
            os_module.O_RDONLY | os_module.O_DIRECTORY |
            os_module.O_NOFOLLOW | os_module.O_CLOEXEC
        )
        run_lock_fd = os_module.open("/run/lock", directory_flags)
        run_lock_status = os_module.fstat(run_lock_fd)
        if (run_lock_status.st_uid != 0 or
                not stat_module.S_ISDIR(run_lock_status.st_mode)):
            raise OSError("unsafe /run/lock")

        created_directory = False
        try:
            os_module.mkdir(
                "axisai-production", 0o755, dir_fd=run_lock_fd
            )
            created_directory = True
        except FileExistsError:
            pass
        lock_dir_fd = os_module.open(
            "axisai-production", directory_flags, dir_fd=run_lock_fd
        )
        if created_directory:
            os_module.fchmod(lock_dir_fd, 0o755)
        lock_dir_status = os_module.fstat(lock_dir_fd)
        lock_dir_path_status = os_module.stat(
            "axisai-production",
            dir_fd=run_lock_fd,
            follow_symlinks=False,
        )
        if (lock_dir_status.st_uid != 0 or lock_dir_path_status.st_uid != 0 or
                not stat_module.S_ISDIR(lock_dir_status.st_mode) or
                not stat_module.S_ISDIR(lock_dir_path_status.st_mode) or
                stat_module.S_IMODE(lock_dir_status.st_mode) != 0o755 or
                stat_module.S_IMODE(lock_dir_path_status.st_mode) != 0o755 or
                (lock_dir_status.st_dev, lock_dir_status.st_ino) !=
                (lock_dir_path_status.st_dev, lock_dir_path_status.st_ino)):
            raise OSError("unsafe outer lock directory")

        common_file_flags = (
            os_module.O_RDWR | os_module.O_NOFOLLOW | os_module.O_CLOEXEC
        )
        created_file = False
        try:
            lock_fd = os_module.open(
                "production.lock",
                common_file_flags | os_module.O_CREAT | os_module.O_EXCL,
                0o644,
                dir_fd=lock_dir_fd,
            )
            created_file = True
        except FileExistsError:
            lock_fd = os_module.open(
                "production.lock", common_file_flags, dir_fd=lock_dir_fd
            )
        if created_file:
            os_module.fchmod(lock_fd, 0o644)
        lock_status = os_module.fstat(lock_fd)
        path_status = os_module.stat(
            "production.lock", dir_fd=lock_dir_fd, follow_symlinks=False
        )
        if (lock_status.st_uid != 0 or path_status.st_uid != 0 or
                not stat_module.S_ISREG(lock_status.st_mode) or
                not stat_module.S_ISREG(path_status.st_mode) or
                lock_status.st_nlink != 1 or path_status.st_nlink != 1 or
                stat_module.S_IMODE(lock_status.st_mode) != 0o644 or
                (lock_status.st_dev, lock_status.st_ino) !=
                (path_status.st_dev, path_status.st_ino)):
            raise OSError("unsafe outer lock file")

        deadline = time_module.monotonic() + 60
        while True:
            try:
                fcntl_module.flock(
                    lock_fd, fcntl_module.LOCK_EX | fcntl_module.LOCK_NB
                )
                break
            except BlockingIOError:
                now = time_module.monotonic()
                if now >= deadline:
                    _emit(
                        stderr,
                        "outer deployment lock unavailable after 60 seconds",
                    )
                    return 73
                time_module.sleep(min(0.25, deadline - now))
        os_module.dup2(lock_fd, 7, inheritable=True)
        capability_fd = 7
        completed = subprocess_module.run(
            ["/bin/sh", "-c", command],
            check=False,
            pass_fds=(7,),
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "AXISAI_ROOT_LOCK_FD": "7",
            },
        )
        return completed.returncode
    except (OSError, ValueError, UnicodeError):
        _emit(stderr, "outer deployment lock is unavailable or unsafe")
        return 73
    finally:
        for descriptor in (capability_fd, lock_fd, lock_dir_fd, run_lock_fd):
            if descriptor is not None:
                try:
                    os_module.close(descriptor)
                except OSError:
                    pass


def main():
    import fcntl
    import os
    import stat
    import subprocess
    import sys
    import time
    if len(sys.argv) != 2:
        _emit(sys.stderr, "outer deployment lock command is invalid")
        return 73
    return run(sys.argv[1], os, stat, fcntl, subprocess, time, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
'''

PRIVILEGE_DROP_SOURCE = r'''def _emit(stderr, message):
    if hasattr(stderr, "write"):
        stderr.write(message + "\n")
    else:
        stderr.append(message)


def run(user_name, helper_path, deploy_sha, deploy_dir, public_url, fd_text,
        os_module, pwd_module, signal_module, stderr):
    try:
        if fd_text != "7":
            raise ValueError("invalid capability descriptor")
        account = pwd_module.getpwnam(user_name)
        if (account.pw_name != user_name or account.pw_uid <= 0 or
                account.pw_gid <= 0):
            _emit(stderr, "configured deploy user is invalid")
            return 70
        os_module.initgroups(account.pw_name, account.pw_gid)
        os_module.setgid(account.pw_gid)
        os_module.setuid(account.pw_uid)
        if (os_module.getuid(), os_module.geteuid()) != (
                account.pw_uid, account.pw_uid
        ) or (os_module.getgid(), os_module.getegid()) != (
                account.pw_gid, account.pw_gid
        ):
            raise OSError("privilege drop identity mismatch")
        os_module.set_inheritable(7, True)
        signal_module.alarm(0)
        os_module.execve(
            helper_path,
            [helper_path, deploy_sha, deploy_dir, public_url],
            {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "AXISAI_OUTER_LOCK_FD": "7",
            },
        )
    except (KeyError, OSError, ValueError):
        _emit(stderr, "privilege drop or helper launch failed")
        return 70
    _emit(stderr, "privilege drop helper unexpectedly returned from execve")
    return 70


def main():
    import os
    import pwd
    import signal
    import sys
    if len(sys.argv) != 7:
        _emit(sys.stderr, "privilege drop arguments are invalid")
        return 70
    signal.alarm(5)
    try:
        return run(*sys.argv[1:], os, pwd, signal, sys.stderr)
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
'''

WAITING_STATES = frozenset({"Pending", "Delayed", "InProgress"})
FAILURE_STATES = frozenset({
    "Failed",
    "DeliveryTimedOut",
    "ExecutionTimedOut",
    "Undeliverable",
    "Cancelled",
    "Terminated",
})

_STATUS_COMPARISON_NAMES = {
    "In Progress": "InProgress",
    "Delivery Timed Out": "DeliveryTimedOut",
    "Execution Timed Out": "ExecutionTimedOut",
}


class ConfigError(RuntimeError):
    """Raised when deploy configuration or the checked-out candidate is unsafe."""


class GitCliError(ConfigError):
    """Raised when a bounded local Git validation command fails."""


class PreflightError(RuntimeError):
    """Raised when the configured EC2/SSM target cannot receive a deploy."""


class AwsCliError(RuntimeError):
    """Raised by an AWS JSON runner when the AWS CLI command fails."""

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code


class InvocationProtocolError(RuntimeError):
    """Raised when AWS returns an unknown state or a malformed response."""


class InvocationPollingTimeout(RuntimeError):
    """Raised when an invocation remains non-terminal beyond the polling horizon."""


# Injected runner contract: a runner used for invocation polling must return or
# raise AwsCliError within INVOCATION_CALL_TIMEOUT_SECONDS. Task 6's concrete
# subprocess runner owns that process-level timeout; this callable signature
# remains compatible with the read-only preflight and SendCommand interfaces.
AwsJsonRunner = Callable[[list[str]], dict[str, Any]]
GitRunner = Callable[[list[str]], str]
UtcClock = Callable[[], datetime]


@dataclass(frozen=True)
class DeployConfig:
    deploy_sha: str
    region: str
    instance_id: str
    deploy_user: str
    deploy_dir: str
    public_health_url: str | None

    @classmethod
    def from_environ(cls, env: Mapping[str, str]) -> "DeployConfig":
        deploy_sha = env.get("DEPLOY_SHA", "")
        region = env.get("AWS_REGION", "")
        instance_id = env.get("EC2_INSTANCE_ID", "")
        deploy_user = env.get("DEPLOY_USER", "")
        deploy_dir = env.get("DEPLOY_DIR", "")
        public_url = env.get("PUBLIC_HEALTH_URL", "") or None

        if not SHA_RE.fullmatch(deploy_sha):
            raise ConfigError("DEPLOY_SHA must be lowercase 40-hex")
        if region != "eu-central-1":
            raise ConfigError("AWS_REGION must be eu-central-1")
        if not INSTANCE_RE.fullmatch(instance_id):
            raise ConfigError("EC2_INSTANCE_ID must name one instance")
        if not USER_RE.fullmatch(deploy_user):
            raise ConfigError("DEPLOY_USER is unsafe")
        if not PurePosixPath(deploy_dir).is_absolute() or "\n" in deploy_dir or "\r" in deploy_dir:
            raise ConfigError("DEPLOY_DIR must be one absolute path")
        validate_public_health_url(public_url)
        return cls(deploy_sha, region, instance_id, deploy_user, deploy_dir, public_url)


@dataclass(frozen=True)
class ManagedInstance:
    instance_id: str
    last_ping: datetime


@dataclass(frozen=True)
class InvocationResult:
    status_details: str
    response_code: int
    stdout: str
    stderr: str


class InvocationFailed(RuntimeError):
    """Raised when SSM reports a terminal invocation failure."""

    def __init__(self, result: InvocationResult):
        super().__init__(f"SSM invocation failed: {result.status_details}")
        self.result = result


def validate_public_health_url(public_health_url: str | None) -> None:
    """Require an optional public health endpoint to be an origin-safe HTTPS URL."""
    if public_health_url is None:
        return
    if public_health_url != public_health_url.strip() or any(
        ord(character) < 32 or ord(character) == 127
        for character in public_health_url
    ):
        raise ConfigError("PUBLIC_HEALTH_URL must not contain whitespace padding or controls")

    parsed = urlsplit(public_health_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ConfigError("PUBLIC_HEALTH_URL must be HTTPS without credentials")


def _run_git(
    args: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    operation = args[3] if len(args) > 3 and args[:2] == ["git", "-C"] else "operation"
    try:
        completed = run(
            args,
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_CALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise GitCliError(
            f"git {operation} timed out after {GIT_CALL_TIMEOUT_SECONDS} seconds"
        ) from error
    except subprocess.CalledProcessError as error:
        raise GitCliError(
            f"git {operation} failed with exit code {error.returncode}"
        ) from error
    except (OSError, UnicodeError) as error:
        raise GitCliError(f"git {operation} could not start") from error
    return completed.stdout.rstrip("\r\n")


def run_aws_json(
    args: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Run one bounded AWS CLI operation and require one JSON object."""
    if len(args) < 2:
        raise AwsCliError("AWS CLI operation must include a service and command")

    timeout = (
        INVOCATION_CALL_TIMEOUT_SECONDS
        if args[:2] == ["ssm", "get-command-invocation"]
        else AWS_CLI_CALL_TIMEOUT_SECONDS
    )
    operation = f"aws {args[0]} {args[1]}"
    command = ["aws", *args, "--output", "json", "--no-cli-pager"]
    try:
        completed = run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise AwsCliError(f"{operation} timed out after {timeout} seconds") from error
    except (OSError, UnicodeError) as error:
        raise AwsCliError(f"{operation} could not start") from error

    if completed.returncode != 0:
        error_code = None
        if isinstance(completed.stderr, str):
            match = AWS_ERROR_CODE_RE.search(completed.stderr)
            if match is not None:
                error_code = match.group(1)
        raise AwsCliError(
            f"{operation} failed with exit code {completed.returncode}",
            code=error_code,
        )
    try:
        response = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise AwsCliError(f"{operation} returned invalid JSON") from error
    if not isinstance(response, dict):
        raise AwsCliError(f"{operation} must return one JSON object")
    return response


def validate_candidate(repo_path: Path, deploy_sha: str, run_git: GitRunner = _run_git) -> None:
    """Confirm the requested commit remains the current candidate on origin/main."""
    if not SHA_RE.fullmatch(deploy_sha):
        raise ConfigError("DEPLOY_SHA must be lowercase 40-hex")

    git_prefix = ["git", "-C", str(repo_path)]
    try:
        run_git([*git_prefix, "fetch", "origin", "main", "--prune"])
        run_git([*git_prefix, "cat-file", "-e", f"{deploy_sha}^{{commit}}"])
        origin_main = run_git([*git_prefix, "rev-parse", "refs/remotes/origin/main"])
    except (OSError, subprocess.SubprocessError) as error:
        raise ConfigError("unable to validate deployment candidate") from error

    if origin_main != deploy_sha:
        raise ConfigError("deployment candidate is stale: origin/main differs from DEPLOY_SHA")


def _parse_heartbeat(value: object) -> datetime:
    if not isinstance(value, str):
        raise PreflightError("SSM target heartbeat is invalid")
    try:
        heartbeat = datetime.fromisoformat(value)
    except ValueError as error:
        raise PreflightError("SSM target heartbeat is invalid") from error
    if heartbeat.tzinfo is None or heartbeat.utcoffset() is None:
        raise PreflightError("SSM target heartbeat must include a timezone")
    return heartbeat


def validate_managed_instance(
    ssm: Mapping[str, Any],
    instance_id: str,
    now: datetime,
) -> ManagedInstance:
    """Require one matching, online, recently-pinged SSM managed instance."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise PreflightError("preflight clock must include a timezone")

    entries = ssm.get("InstanceInformationList")
    if not isinstance(entries, list) or len(entries) != 1:
        raise PreflightError("expected exactly one SSM managed target")
    entry = entries[0]
    if not isinstance(entry, Mapping) or entry.get("InstanceId") != instance_id:
        raise PreflightError("SSM target does not match configured instance")
    if entry.get("PingStatus") != "Online":
        raise PreflightError("SSM target is not online")

    last_ping = _parse_heartbeat(entry.get("LastPingDateTime"))
    age = now - last_ping
    if age < -timedelta(minutes=1):
        raise PreflightError("SSM target heartbeat is too far in the future")
    if age > timedelta(minutes=5):
        raise PreflightError("SSM target heartbeat is stale")
    return ManagedInstance(instance_id=instance_id, last_ping=last_ping)


def preflight(
    config: DeployConfig,
    aws: AwsJsonRunner,
    utc_now: UtcClock | datetime,
) -> ManagedInstance:
    """Verify the configured instance is the sole running and fresh SSM target."""
    ec2 = aws([
        "ec2", "describe-instances", "--instance-ids", config.instance_id,
        "--region", config.region,
    ])
    reservations = ec2.get("Reservations")
    if not isinstance(reservations, list) or len(reservations) != 1:
        raise PreflightError("expected exactly one EC2 target")
    reservation = reservations[0]
    if not isinstance(reservation, Mapping):
        raise PreflightError("expected exactly one EC2 target")
    instances = reservation.get("Instances")
    if not isinstance(instances, list) or len(instances) != 1:
        raise PreflightError("expected exactly one EC2 target")
    instance = instances[0]
    if not isinstance(instance, Mapping) or instance.get("InstanceId") != config.instance_id:
        raise PreflightError("expected exactly one EC2 target")
    state = instance.get("State")
    if not isinstance(state, Mapping) or not isinstance(state.get("Name"), str):
        raise PreflightError("EC2 target state is malformed")
    if state["Name"] != "running":
        raise PreflightError("EC2 target is not running")

    ssm = aws([
        "ssm", "describe-instance-information", "--filters",
        f"Key=InstanceIds,Values={config.instance_id}", "--region", config.region,
    ])
    now = utc_now() if callable(utc_now) else utc_now
    if not isinstance(now, datetime):
        raise PreflightError("preflight clock must return a datetime")
    return validate_managed_instance(ssm, config.instance_id, now)


def render_bootstrap(
    encoded_script: str,
    deploy_sha: str,
    encoded_dir: str,
    deploy_user: str,
    encoded_url: str,
) -> str:
    """Render a fixed root bootstrap from validated and base64-encoded values."""
    inner_bootstrap = f"""set -eu
umask 077
root_external() {{
  timeout --signal=TERM --kill-after=1s 4s "$@"
}}
deploy_dir="$(printf '%s' '{encoded_dir}' | root_external base64 --decode)"
public_health_url="$(printf '%s' '{encoded_url}' | root_external base64 --decode)"

if [ "${{AXISAI_ROOT_LOCK_FD:-}}" != '{OUTER_LOCK_CAPABILITY_FD}' ]; then
  echo 'outer deployment lock capability is unavailable' >&2
  exit 73
fi

env_file="$deploy_dir/.env"
if [ ! -f "$env_file" ]; then
  echo 'deployment .env file is missing' >&2
  exit 1
fi
env_permissions="$(root_external stat -c %a -- "$env_file")"
if [ "$env_permissions" != 600 ]; then
  echo "WARNING: correcting .env permissions from $env_permissions to 600"
  root_external chmod 600 -- "$env_file"
  env_permissions="$(root_external stat -c %a -- "$env_file")"
  if [ "$env_permissions" != 600 ]; then
    echo 'deployment .env permissions remain unsafe' >&2
    exit 1
  fi
else
  echo '.env permissions: 600'
fi

nginx_site=/etc/nginx/sites-available/fitx
if [ -f "$nginx_site" ] && root_external grep -q 'add_header Content-Security-Policy' "$nginx_site"; then
  echo 'ERROR: nginx site config still contains add_header Content-Security-Policy' >&2
  exit 1
fi
if root_external nginx -t; then
  if root_external systemctl is-active --quiet nginx; then
    root_external systemctl reload nginx
    echo 'nginx: configuration validated and reloaded'
  else
    root_external systemctl enable --now nginx
    echo 'nginx: configuration validated and enabled'
  fi
else
  echo 'ERROR: nginx configuration test failed' >&2
  exit 1
fi
if listeners="$(root_external ss -ltnp 2>&1)"; then
  printf '%s\n' "$listeners" | grep -E ':(80|443) ' >/dev/null || \
    echo 'WARNING: no listener found on port 80 or 443'
else
  echo 'WARNING: unable to inspect port 80/443 listeners'
fi

if listeners="$(root_external ss -H -ltn 'sport = :3000' 2>&1)" && \
   printf '%s\n' "$listeners" | \
     grep -Eq '(^|[[:space:]])127[.]0[.]0[.]1:3000([[:space:]]|$)'; then
  echo 'fatsecret proxy: 127.0.0.1:3000 is listening'
else
  echo 'WARNING: fatsecret proxy is not listening on 127.0.0.1:3000'
fi

script_path="$(root_external mktemp /tmp/fitx-deploy.XXXXXX)"
cleanup() {{
  root_external rm -f -- "$script_path" || true
}}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s' '{encoded_script}' | root_external base64 --decode > "$script_path"
root_external chmod 0700 "$script_path"
root_external chown -- '{deploy_user}' "$script_path"
python3 - '{deploy_user}' "$script_path" '{deploy_sha}' "$deploy_dir" \
  "$public_health_url" '{OUTER_LOCK_CAPABILITY_FD}' <<'AXISAI_PRIVILEGE_DROP_PY'
{PRIVILEGE_DROP_SOURCE}AXISAI_PRIVILEGE_DROP_PY
"""
    encoded_bootstrap = base64.b64encode(inner_bootstrap.encode("utf-8")).decode("ascii")
    return (
        f"python3 - {shlex.quote(encoded_bootstrap)} "
        "<<'AXISAI_ROOT_LOCK_PY'\n"
        f"{ROOT_LOCK_WRAPPER_SOURCE}"
        "AXISAI_ROOT_LOCK_PY\n"
    )


def build_remote_command(config: DeployConfig, script: bytes) -> str:
    """Encode immutable deploy inputs for the fixed remote bootstrap."""
    encoded_script = base64.b64encode(script).decode("ascii")
    encoded_dir = base64.b64encode(config.deploy_dir.encode()).decode("ascii")
    encoded_url = base64.b64encode((config.public_health_url or "").encode()).decode("ascii")
    return render_bootstrap(
        encoded_script,
        config.deploy_sha,
        encoded_dir,
        config.deploy_user,
        encoded_url,
    )


def require_command_id(response: object) -> str:
    """Extract one canonical UUID command ID from a SendCommand response."""
    if not isinstance(response, Mapping):
        raise InvocationProtocolError("SendCommand response must be an object")
    command = response.get("Command")
    if not isinstance(command, Mapping):
        raise InvocationProtocolError("SendCommand response has no Command object")
    command_id = command.get("CommandId")
    if not isinstance(command_id, str):
        raise InvocationProtocolError("SendCommand response has no command UUID")
    try:
        parsed = UUID(command_id)
    except ValueError as error:
        raise InvocationProtocolError("SendCommand response has an invalid command UUID") from error
    if str(parsed) != command_id:
        raise InvocationProtocolError("SendCommand response has a noncanonical command UUID")
    return command_id


def send_command(config: DeployConfig, script: bytes, aws: AwsJsonRunner) -> str:
    """Deliver the encoded host script with separate delivery and execution bounds."""
    remote_command = build_remote_command(config, script)
    if len(remote_command) > LOCAL_RUN_SHELL_COMMAND_MAX_CHARS:
        raise ConfigError(
            "generated RunShellScript command exceeds the local 65536-character "
            "growth guard"
        )
    parameters = {
        "commands": [remote_command],
        "executionTimeout": [str(EXECUTION_TIMEOUT_SECONDS)],
    }
    response = aws([
        "ssm", "send-command",
        "--region", config.region,
        "--instance-ids", config.instance_id,
        "--document-name", "AWS-RunShellScript",
        "--timeout-seconds", str(DELIVERY_TIMEOUT_SECONDS),
        "--parameters", json.dumps(parameters, separators=(",", ":")),
    ])
    return require_command_id(response)


def read_invocation(
    config: DeployConfig,
    command_id: str,
    aws: AwsJsonRunner,
) -> InvocationResult:
    """Read and type-check the detailed SSM invocation response."""
    response = aws([
        "ssm", "get-command-invocation",
        "--region", config.region,
        "--command-id", command_id,
        "--instance-id", config.instance_id,
    ])
    if not isinstance(response, Mapping):
        raise InvocationProtocolError("get-command-invocation response must be an object")

    required_types = {
        "StatusDetails": str,
        "ResponseCode": int,
        "StandardOutputContent": str,
        "StandardErrorContent": str,
    }
    for field, expected_type in required_types.items():
        value = response.get(field)
        if type(value) is not expected_type:
            raise InvocationProtocolError(f"get-command-invocation field {field} has wrong type")

    return InvocationResult(
        status_details=response["StatusDetails"],
        response_code=response["ResponseCode"],
        stdout=response["StandardOutputContent"],
        stderr=response["StandardErrorContent"],
    )


def _comparison_status(status_details: str) -> str:
    return _STATUS_COMPARISON_NAMES.get(status_details, status_details)


def wait_for_invocation(
    config: DeployConfig,
    command_id: str,
    aws: AwsJsonRunner,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    log: Callable[[str], None],
) -> InvocationResult:
    """Poll within a hard horizon using an AWS runner with bounded calls.

    The injected runner must return or raise ``AwsCliError`` within
    ``INVOCATION_CALL_TIMEOUT_SECONDS`` so a hung process cannot bypass the
    controller's wall-clock horizon.
    """
    deadline = monotonic() + POLL_HORIZON_SECONDS
    execution_reported = False
    while True:
        if monotonic() >= deadline:
            raise InvocationPollingTimeout(command_id)
        try:
            result = read_invocation(config, command_id, aws)
        except AwsCliError as error:
            if error.code != "InvocationDoesNotExist":
                raise
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise InvocationPollingTimeout(command_id) from error
            log("SSM invocation not visible yet")
            sleep(min(POLL_INTERVAL_SECONDS, remaining))
            continue
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise InvocationPollingTimeout(command_id)
        log(f"SSM status: {result.status_details}")
        comparison_status = _comparison_status(result.status_details)
        if comparison_status == "InProgress" and not execution_reported:
            log("SSM reports command InProgress")
            execution_reported = True
        if comparison_status == "Success":
            return result
        if comparison_status in FAILURE_STATES:
            raise InvocationFailed(result)
        if comparison_status not in WAITING_STATES:
            raise InvocationProtocolError(result.status_details)
        sleep(min(POLL_INTERVAL_SECONDS, remaining))


def _load_host_script(repo_path: Path) -> bytes:
    script_path = repo_path / "scripts" / "production_deploy.sh"
    try:
        return script_path.read_bytes()
    except OSError as error:
        raise ConfigError(f"unable to load exact host helper: {script_path}") from error


def _emit_invocation_output(
    result: InvocationResult,
    log: Callable[[str], None],
) -> None:
    log("SSM stdout:")
    if result.stdout:
        log(result.stdout.rstrip("\r\n"))
    log("SSM stderr:")
    if result.stderr:
        log(result.stderr.rstrip("\r\n"))


def run_deploy(
    environ: Mapping[str, str],
    repo_path: Path,
    aws: AwsJsonRunner,
    utc_now: UtcClock | datetime,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    log: Callable[[str], None],
    *,
    run_git: GitRunner = _run_git,
) -> InvocationResult:
    """Run the single validated production deployment lifecycle in order."""
    config = DeployConfig.from_environ(environ)
    validate_candidate(repo_path, config.deploy_sha, run_git=run_git)
    preflight(config, aws, utc_now)
    script = _load_host_script(repo_path)
    command_id = send_command(config, script, aws)
    log(f"SSM command created: {command_id}")
    try:
        result = wait_for_invocation(
            config, command_id, aws, monotonic, sleep, log,
        )
    except InvocationFailed as error:
        _emit_invocation_output(error.result, log)
        raise
    _emit_invocation_output(result, log)
    return result


def main(
    *,
    environ: Mapping[str, str] | None = None,
    repo_path: Path | None = None,
    aws: AwsJsonRunner | None = None,
    now: datetime | None = None,
    utc_now: UtcClock | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    log: Callable[[str], None] = print,
    run_git: GitRunner = _run_git,
) -> int:
    """CLI entrypoint returning non-zero for every typed deployment failure."""
    try:
        if now is not None and utc_now is not None:
            raise ConfigError("provide only one UTC clock override")
        send_time_clock: UtcClock | datetime = (
            utc_now
            if utc_now is not None
            else now
            if now is not None
            else lambda: datetime.now(timezone.utc)
        )
        run_deploy(
            os.environ if environ is None else environ,
            Path.cwd() if repo_path is None else repo_path,
            run_aws_json if aws is None else aws,
            send_time_clock,
            time.monotonic if monotonic is None else monotonic,
            time.sleep if sleep is None else sleep,
            log,
            run_git=run_git,
        )
    except (
        AwsCliError,
        ConfigError,
        InvocationFailed,
        InvocationPollingTimeout,
        InvocationProtocolError,
        PreflightError,
    ) as error:
        log(f"deployment failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
