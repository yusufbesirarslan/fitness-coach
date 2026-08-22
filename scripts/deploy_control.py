"""Fail-closed validation primitives for the production deploy controller."""

from __future__ import annotations

import base64
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
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
deploy_dir="$(printf '%s' '{encoded_dir}' | base64 --decode)"
public_health_url="$(printf '%s' '{encoded_url}' | base64 --decode)"
sudo -u '{deploy_user}' -- "$script_path" '{deploy_sha}' "$deploy_dir" "$public_health_url"
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
    """Poll until success, a typed closed failure, or the bounded horizon."""
    deadline = monotonic() + POLL_HORIZON_SECONDS
    execution_reported = False
    while monotonic() < deadline:
        result = read_invocation(config, command_id, aws)
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
        sleep(POLL_INTERVAL_SECONDS)
    raise InvocationPollingTimeout(command_id)
