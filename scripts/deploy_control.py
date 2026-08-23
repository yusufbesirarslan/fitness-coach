"""Fail-closed validation primitives for the production deploy controller."""

from __future__ import annotations

import base64
import json
import os
import re
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

DELIVERY_TIMEOUT_SECONDS = 60
EXECUTION_TIMEOUT_SECONDS = 1800
AWS_EXPIRY_SECONDS = 1860
POLL_HORIZON_SECONDS = 2100
POLL_INTERVAL_SECONDS = 10
INVOCATION_CALL_TIMEOUT_SECONDS = 30
AWS_CLI_CALL_TIMEOUT_SECONDS = 60

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


class PreflightError(RuntimeError):
    """Raised when the configured EC2/SSM target cannot receive a deploy."""


class AwsCliError(RuntimeError):
    """Raised by an AWS JSON runner when the AWS CLI command fails."""


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


def _run_git(args: list[str]) -> str:
    completed = subprocess.run(args, check=True, capture_output=True, text=True)
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
        raise AwsCliError(f"{operation} failed with exit code {completed.returncode}")
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


def preflight(config: DeployConfig, aws: AwsJsonRunner, now: datetime) -> ManagedInstance:
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
    if instance.get("State", {}).get("Name") != "running":
        raise PreflightError("EC2 target is not running")

    ssm = aws([
        "ssm", "describe-instance-information", "--filters",
        f"Key=InstanceIds,Values={config.instance_id}", "--region", config.region,
    ])
    return validate_managed_instance(ssm, config.instance_id, now)


def render_bootstrap(
    encoded_script: str,
    deploy_sha: str,
    encoded_dir: str,
    deploy_user: str,
    encoded_url: str,
) -> str:
    """Render a fixed root bootstrap from validated and base64-encoded values."""
    return f"""set -eu
umask 077
deploy_dir="$(printf '%s' '{encoded_dir}' | base64 --decode)"
public_health_url="$(printf '%s' '{encoded_url}' | base64 --decode)"
lock_path="$deploy_dir/.axisai-production-deploy.lock"

exec 9>"$lock_path"
if ! flock -w 60 9; then
  echo 'deployment lock unavailable after 60 seconds' >&2
  exit 73
fi

env_file="$deploy_dir/.env"
if [ ! -f "$env_file" ]; then
  echo 'deployment .env file is missing' >&2
  exit 1
fi
env_permissions="$(stat -c %a -- "$env_file")"
if [ "$env_permissions" != 600 ]; then
  echo "WARNING: correcting .env permissions from $env_permissions to 600"
  chmod 600 -- "$env_file"
  env_permissions="$(stat -c %a -- "$env_file")"
  if [ "$env_permissions" != 600 ]; then
    echo 'deployment .env permissions remain unsafe' >&2
    exit 1
  fi
else
  echo '.env permissions: 600'
fi

nginx_site=/etc/nginx/sites-available/fitx
if [ -f "$nginx_site" ] && grep -q 'add_header Content-Security-Policy' "$nginx_site"; then
  echo 'ERROR: nginx site config still contains add_header Content-Security-Policy' >&2
  exit 1
fi
if nginx -t; then
  if systemctl is-active --quiet nginx; then
    systemctl reload nginx
    echo 'nginx: configuration validated and reloaded'
  else
    systemctl enable --now nginx
    echo 'nginx: configuration validated and enabled'
  fi
else
  echo 'ERROR: nginx configuration test failed' >&2
  exit 1
fi
if listeners="$(ss -ltnp 2>&1)"; then
  printf '%s\n' "$listeners" | grep -E ':(80|443) ' >/dev/null || \
    echo 'WARNING: no listener found on port 80 or 443'
else
  echo 'WARNING: unable to inspect port 80/443 listeners'
fi

if listeners="$(ss -H -ltn 'sport = :3000' 2>&1)" && \
   printf '%s\n' "$listeners" | \
     grep -Eq '(^|[[:space:]])127[.]0[.]0[.]1:3000([[:space:]]|$)'; then
  echo 'fatsecret proxy: 127.0.0.1:3000 is listening'
else
  echo 'fatsecret proxy is not listening on 127.0.0.1:3000' >&2
  exit 1
fi

script_path="$(mktemp /tmp/fitx-deploy.XXXXXX)"
cleanup() {{
  rm -f -- "$script_path"
}}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s' '{encoded_script}' | base64 --decode > "$script_path"
chmod 0700 "$script_path"
if ! id -u '{deploy_user}' >/dev/null 2>&1; then
  echo 'configured deploy user does not exist' >&2
  exit 1
fi
chown -- '{deploy_user}' "$script_path"
sudo -u '{deploy_user}' -- env AXISAI_DEPLOY_LOCK_FD=0 \
  "$script_path" '{deploy_sha}' "$deploy_dir" "$public_health_url" <&9
"""


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
    parameters = {
        "commands": [build_remote_command(config, script)],
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
        result = read_invocation(config, command_id, aws)
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise InvocationPollingTimeout(command_id)
        log(f"SSM status: {result.status_details}")
        comparison_status = _comparison_status(result.status_details)
        if comparison_status == "InProgress" and not execution_reported:
            log("host execution started")
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
    now: datetime,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    log: Callable[[str], None],
    *,
    run_git: GitRunner = _run_git,
) -> InvocationResult:
    """Run the single validated production deployment lifecycle in order."""
    config = DeployConfig.from_environ(environ)
    validate_candidate(repo_path, config.deploy_sha, run_git=run_git)
    preflight(config, aws, now)
    script = _load_host_script(repo_path)
    command_id = send_command(config, script, aws)
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
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    log: Callable[[str], None] = print,
    run_git: GitRunner = _run_git,
) -> int:
    """CLI entrypoint returning non-zero for every typed deployment failure."""
    try:
        run_deploy(
            os.environ if environ is None else environ,
            Path.cwd() if repo_path is None else repo_path,
            run_aws_json if aws is None else aws,
            datetime.now(timezone.utc) if now is None else now,
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
