"""Fail-closed validation primitives for the production deploy controller."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit


SHA_RE = re.compile(r"[0-9a-f]{40}")
INSTANCE_RE = re.compile(r"i-[0-9a-f]{8}(?:[0-9a-f]{9})?")
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


class ConfigError(RuntimeError):
    """Raised when deploy configuration or the checked-out candidate is unsafe."""


class PreflightError(RuntimeError):
    """Raised when the configured EC2/SSM target cannot receive a deploy."""


class AwsCliError(RuntimeError):
    """Raised by an AWS JSON runner when the AWS CLI command fails."""


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
