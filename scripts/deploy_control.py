"""Fail-closed validation primitives for the production deploy controller."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

if __package__:
    from .deploy_contract import (
        CONTROLLER_REQUIRED_SECONDS,
        SSM_EXECUTION_TIMEOUT_SECONDS,
        SSM_HEARTBEAT_FUTURE_SKEW_SECONDS,
        SSM_HEARTBEAT_MAX_AGE_SECONDS,
        host_timeout_environment,
    )
else:
    from deploy_contract import (
        CONTROLLER_REQUIRED_SECONDS,
        SSM_EXECUTION_TIMEOUT_SECONDS,
        SSM_HEARTBEAT_FUTURE_SKEW_SECONDS,
        SSM_HEARTBEAT_MAX_AGE_SECONDS,
        host_timeout_environment,
    )


SHA_RE = re.compile(r"[0-9a-f]{40}")
INSTANCE_RE = re.compile(r"i-[0-9a-f]{8}(?:[0-9a-f]{9})?")
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
DEPLOY_DIR_RE = re.compile(r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+")
AWS_ERROR_CODE_RE = re.compile(
    r"An error occurred \(([A-Za-z][A-Za-z0-9]*)\) when calling"
)

DELIVERY_TIMEOUT_SECONDS = 60
EXECUTION_TIMEOUT_SECONDS = SSM_EXECUTION_TIMEOUT_SECONDS
AWS_EXPIRY_SECONDS = DELIVERY_TIMEOUT_SECONDS + EXECUTION_TIMEOUT_SECONDS
POLL_HORIZON_SECONDS = 2100
POLL_INTERVAL_SECONDS = 10
INVOCATION_CALL_TIMEOUT_SECONDS = 30
AWS_CLI_CALL_TIMEOUT_SECONDS = 60
GIT_CALL_TIMEOUT_SECONDS = 60
CONTROLLER_BUDGET_SECONDS = 46 * 60
if CONTROLLER_BUDGET_SECONDS < CONTROLLER_REQUIRED_SECONDS:
    raise RuntimeError("controller timeout budget is below the deploy contract")
AUTHORIZATION_CALL_TIMEOUT_SECONDS = AWS_CLI_CALL_TIMEOUT_SECONDS
AUTHORITY_CLEANUP_RESERVE_SECONDS = AWS_CLI_CALL_TIMEOUT_SECONDS
SEND_AND_MONITOR_RESERVE_SECONDS = (
    AWS_CLI_CALL_TIMEOUT_SECONDS
    + POLL_HORIZON_SECONDS
    + INVOCATION_CALL_TIMEOUT_SECONDS
    + AUTHORIZATION_CALL_TIMEOUT_SECONDS
    + AUTHORITY_CLEANUP_RESERVE_SECONDS
)
ROOT_RUNTIME_DIR = "/run/lock/axisai-production"
ROOT_OUTER_LOCK_PATH = f"{ROOT_RUNTIME_DIR}/production.lock"
# Transaction scratch state lives in the root-owned runtime directory, never
# in the production checkout.  Root provisions it after the mutation gate
# opens and removes it after the child terminates.
MONOTONIC_STATE_PATH = f"{ROOT_RUNTIME_DIR}/monotonic-clock"
# Fixed basename of the privileged helper object inside its private
# root-owned directory.
HELPER_NAME = "production_deploy.sh"
AUTHORITY_PARAMETER_PREFIX = "/axisai/production-deploy-authority/"
AUTHORITY_WAIT_ATTEMPTS = 14
AUTHORITY_GATE_WORST_CASE_SECONDS = AUTHORITY_WAIT_ATTEMPTS * 5
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
# The helper inherits the already-held outer lock: 4 + 7 + 1560 + 2 + 7.
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

# The privileged helper is an OBJECT, not a pathname the deploy user can
# replace.  Root materializes it inside a private root-owned directory, digest-
# verifies the exact bytes through the same descriptor it wrote, and leaves it
# root-owned mode 0505: readable and executable by the deploy user, writable by
# nobody.  Because the parent directory is root-owned and not group/other
# writable, the deploy user cannot unlink or recreate the entry, so the pathname
# handed across the privilege drop cannot be swapped between validation and
# `execve`.
#
# The directory lives under /tmp and NOT under ROOT_RUNTIME_DIR: systemd mounts
# /run/lock `noexec`, so a helper materialized there could be validated
# perfectly and still fail `execve` with EACCES on the production host.
HELPER_PARENT_DIR = "/tmp"
HELPER_DIRECTORY_PREFIX = "axisai-deploy-helper."
HELPER_MATERIALIZATION_SOURCE = (
    "HELPER_NAME = " + repr(HELPER_NAME)
    + "\nHELPER_MODE = 0o505"
    + "\nHELPER_PARENT_DIR = " + repr(HELPER_PARENT_DIR)
    + "\nHELPER_DIRECTORY_PREFIX = " + repr(HELPER_DIRECTORY_PREFIX)
    + "\n\n" + r'''def _write_all(os_module, fd, payload):
    written = 0
    while written < len(payload):
        chunk = os_module.write(fd, payload[written:])
        if chunk <= 0:
            raise OSError("short helper write")
        written += chunk


def _read_all(os_module, fd):
    os_module.lseek(fd, 0, 0)
    chunks = []
    while True:
        chunk = os_module.read(fd, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def create_verified_helper(directory_fd, helper_bytes, expected_sha256,
                           os_module, stat_module, hashlib_module):
    """Create the root-owned, unreplaceable helper object inside directory_fd."""
    fd = os_module.open(
        HELPER_NAME,
        os_module.O_RDWR | os_module.O_CREAT | os_module.O_EXCL |
        os_module.O_NOFOLLOW | os_module.O_CLOEXEC,
        0o500,
        dir_fd=directory_fd,
    )
    try:
        _write_all(os_module, fd, helper_bytes)
        os_module.fsync(fd)
        # After this point no write bit exists for any user, so the digest below
        # describes bytes that can no longer change.
        os_module.fchmod(fd, HELPER_MODE)
        digest = hashlib_module.sha256(_read_all(os_module, fd)).hexdigest()
        if digest != expected_sha256:
            raise OSError("helper digest mismatch")
        status = os_module.fstat(fd)
        path_status = os_module.stat(
            HELPER_NAME, dir_fd=directory_fd, follow_symlinks=False
        )
        if (status.st_uid != 0 or not stat_module.S_ISREG(status.st_mode) or
                status.st_nlink != 1 or path_status.st_uid != 0 or
                not stat_module.S_ISREG(path_status.st_mode) or
                path_status.st_nlink != 1):
            raise OSError("unsafe helper object")
        if (stat_module.S_IMODE(status.st_mode) != HELPER_MODE or
                stat_module.S_IMODE(path_status.st_mode) != HELPER_MODE or
                (status.st_dev, status.st_ino) !=
                (path_status.st_dev, path_status.st_ino)):
            raise OSError("helper identity changed")
        return fd, HELPER_NAME
    except BaseException:
        os_module.close(fd)
        raise


def main():
    import base64
    import hashlib
    import os
    import stat
    import sys
    import tempfile
    if len(sys.argv) != 3 or sys.argv[1] != "materialize-helper":
        sys.stderr.write("helper materialization arguments are invalid\n")
        return 70
    encoded_helper = ENCODED_HELPER
    expected_sha256 = sys.argv[2]
    directory = None
    directory_fd = None
    helper_fd = None
    try:
        helper_bytes = base64.b64decode(encoded_helper, validate=True)
        # mkdtemp is root-owned mode 0700 at creation and unpredictably named,
        # so no unprivileged process can pre-create or race the directory.
        directory = tempfile.mkdtemp(
            prefix=HELPER_DIRECTORY_PREFIX, dir=HELPER_PARENT_DIR
        )
        directory_fd = os.open(
            directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        directory_status = os.fstat(directory_fd)
        if (not stat.S_ISDIR(directory_status.st_mode) or
                directory_status.st_uid != 0 or
                stat.S_IMODE(directory_status.st_mode) != 0o700):
            raise OSError("unsafe helper directory")
        helper_fd, name = create_verified_helper(
            directory_fd, helper_bytes, expected_sha256, os, stat, hashlib
        )
        # Only now widen the directory to traversable.  Root still owns it and
        # no other user may write it, so the verified entry cannot be unlinked.
        os.fchmod(directory_fd, 0o755)
        widened = os.fstat(directory_fd)
        if (widened.st_uid != 0 or
                stat.S_IMODE(widened.st_mode) != 0o755 or
                (widened.st_dev, widened.st_ino) !=
                (directory_status.st_dev, directory_status.st_ino)):
            raise OSError("helper directory identity changed")
        sys.stdout.write(directory + "\n")
        return 0
    except (OSError, ValueError, UnicodeError):
        # The caller never learns the path on this branch, so its EXIT trap
        # cannot remove the directory; clean up the partial object here.
        if directory is not None:
            try:
                os.unlink(os.path.join(directory, HELPER_NAME))
            except OSError:
                pass
            try:
                os.rmdir(directory)
            except OSError:
                pass
        sys.stderr.write("privileged helper object could not be materialized\n")
        return 70
    finally:
        for descriptor in (helper_fd, directory_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
'''
)


PRIVILEGE_DROP_SOURCE = (
    "HOST_TIMEOUT_ENVIRONMENT = " + repr(host_timeout_environment()) + "\n"
    + "MONOTONIC_STATE_PATH = " + repr(MONOTONIC_STATE_PATH)
    + "\n\n" + r'''def _emit(stderr, message):
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
                "AXISAI_MONOTONIC_STATE": MONOTONIC_STATE_PATH,
                **HOST_TIMEOUT_ENVIRONMENT,
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
)

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
        if (
            not DEPLOY_DIR_RE.fullmatch(deploy_dir)
            or any(part in {".", ".."} for part in deploy_dir.split("/"))
        ):
            raise ConfigError("DEPLOY_DIR must be one canonical shell-safe absolute path")
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
    run_options = {
        "capture_output": True,
        "check": False,
        "text": True,
        "timeout": timeout,
    }
    if args[:2] == ["ssm", "send-command"]:
        run_options["env"] = {
            **os.environ,
            "AWS_MAX_ATTEMPTS": "1",
            "AWS_RETRY_MODE": "standard",
        }
    try:
        completed = run(command, **run_options)
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


def _require_live_clock(utc_now: UtcClock) -> UtcClock:
    """Reject a pre-sampled clock before any AWS work.

    A bare ``datetime`` is a clock frozen at some earlier instant. Accepting one
    would decide heartbeat freshness from before the boundary that is about to
    act, which is exactly the retired early-clock contract.

    This check is deliberately a shape check and nothing more. ``lambda: <an
    instant sampled an hour ago>`` and a live clock are indistinguishable at run
    time: both are zero-argument callables, and two genuine ``datetime.now``
    reads can legitimately return the same value, so no advance-checking
    heuristic can separate them without failing real deploys. ``utc_now`` is
    therefore a trusted injection seam, on the same footing as monkeypatching
    :func:`require_fresh_ssm_target` itself. What is guarded instead is every
    route by which this repository can supply one: the entrypoint's parameters
    are whitelisted, the send-time clock is bound exactly once and its
    construction is pinned, and this module is allowed exactly one
    zero-argument callable -- the live default.
    """
    if not callable(utc_now):
        raise ConfigError(
            "UTC clock must be callable so freshness is sampled at the boundary"
        )
    return utc_now


def _sample_utc(utc_now: UtcClock) -> datetime:
    """Sample the live clock at the caller's own authority boundary."""
    now = _require_live_clock(utc_now)()
    if not isinstance(now, datetime):
        raise PreflightError("preflight clock must return a datetime")
    return now


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
    if age < -timedelta(seconds=SSM_HEARTBEAT_FUTURE_SKEW_SECONDS):
        raise PreflightError("SSM target heartbeat is too far in the future")
    if age > timedelta(seconds=SSM_HEARTBEAT_MAX_AGE_SECONDS):
        raise PreflightError("SSM target heartbeat is stale")
    return ManagedInstance(instance_id=instance_id, last_ping=last_ping)


def preflight(
    config: DeployConfig,
    aws: AwsJsonRunner,
    utc_now: UtcClock,
) -> ManagedInstance:
    """Verify the configured instance is the sole running and fresh SSM target."""
    _require_live_clock(utc_now)
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
    return validate_managed_instance(
        ssm, config.instance_id, _sample_utc(utc_now)
    )


def require_fresh_ssm_target(
    config: DeployConfig,
    aws: AwsJsonRunner,
    utc_now: UtcClock,
) -> ManagedInstance:
    """Recheck the sole SSM target immediately before command submission."""
    _require_live_clock(utc_now)
    ssm = aws([
        "ssm", "describe-instance-information", "--filters",
        f"Key=InstanceIds,Values={config.instance_id}", "--region", config.region,
    ])
    return validate_managed_instance(
        ssm, config.instance_id, _sample_utc(utc_now)
    )


def render_bootstrap(
    encoded_script: str,
    deploy_sha: str,
    encoded_dir: str,
    deploy_user: str,
    encoded_url: str,
    encoded_authority_parameter: str,
    region: str,
) -> str:
    """Render a fixed root bootstrap from validated and base64-encoded values."""
    helper_sha256 = hashlib.sha256(
        base64.b64decode(encoded_script, validate=True)
    ).hexdigest()
    # The exact helper payload travels inside the materialization source, so the
    # bootstrap never writes it through a shell redirection into a path an
    # unprivileged user could have replaced.
    helper_materialization_source = (
        "ENCODED_HELPER = " + repr(encoded_script) + "\n"
        + HELPER_MATERIALIZATION_SOURCE
    )
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

# Authority proof.  This runs behind the outer lock and ahead of every
# mutation: a command the controller never authorized must not be able to
# change one byte of production state before it is rejected.
authority_parameter="$(printf '%s' '{encoded_authority_parameter}' | base64 --decode)"
authority_value=''
authority_ready=0
for authority_attempt in $(seq 1 {AUTHORITY_WAIT_ATTEMPTS}); do
  authority_value="$(timeout --signal=TERM --kill-after=1s 4s \
    aws ssm get-parameter --region '{region}' --name "$authority_parameter" \
    --query Parameter.Value --output text --no-cli-pager 2>/dev/null || true)"
  if python3 - "$authority_value" '{deploy_sha}' <<'AXISAI_AUTHORITY_VALUE_PY'
import re
import sys
value, deploy_sha = sys.argv[1:]
pattern = re.compile(
    re.escape(deploy_sha) +
    r":[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}"
)
raise SystemExit(0 if pattern.fullmatch(value) else 1)
AXISAI_AUTHORITY_VALUE_PY
  then
    authority_ready=1
    break
  fi
  sleep 1
done
if [ "$authority_ready" != 1 ]; then
  echo 'deployment delivery was not authorized by the controller' >&2
  exit 75
fi

# Staleness proof.  `ls-remote` reads the remote advertisement only: it writes
# no ref and no object, which is exactly why it -- and never a ref-writing
# fetch -- is the pre-mutation proof.  It runs as the configured deploy user
# so the root bootstrap never borrows root's credentials for a network read.
remote_main=''
if ! remote_main="$(root_external runuser -u '{deploy_user}' -- \
  git -C "$deploy_dir" ls-remote --exit-code origin refs/heads/main)"; then
  echo 'deployment candidate could not be proven current at host mutation gate' >&2
  exit 75
fi
if [ "$remote_main" != "{deploy_sha}$(printf '\t')refs/heads/main" ]; then
  echo 'deployment candidate is stale at host mutation gate' >&2
  exit 75
fi

# ---- mutation gate: nothing above this line changes production state. ----

root_external python3 - '{deploy_user}' "$deploy_dir" <<'AXISAI_ENV_GUARD_PY'
import os
import pwd
import stat
import sys

deploy_user, deploy_dir = sys.argv[1:]
account = pwd.getpwnam(deploy_user)
directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
deploy_dir_fd = os.open(deploy_dir, directory_flags)
env_fd = None
try:
    directory_status = os.fstat(deploy_dir_fd)
    if (not stat.S_ISDIR(directory_status.st_mode) or
            directory_status.st_uid not in {{0, account.pw_uid}} or
            stat.S_IMODE(directory_status.st_mode) & 0o022):
        raise OSError("unsafe deployment directory")
    env_fd = os.open(
        ".env",
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        dir_fd=deploy_dir_fd,
    )
    env_status = os.fstat(env_fd)
    env_path_status = os.stat(
        ".env", dir_fd=deploy_dir_fd, follow_symlinks=False
    )
    if (not stat.S_ISREG(env_status.st_mode) or
            not stat.S_ISREG(env_path_status.st_mode) or
            env_status.st_uid not in {{0, account.pw_uid}} or
            env_path_status.st_uid not in {{0, account.pw_uid}} or
            env_status.st_nlink != 1 or env_path_status.st_nlink != 1 or
            (env_status.st_dev, env_status.st_ino) !=
            (env_path_status.st_dev, env_path_status.st_ino)):
        raise OSError("unsafe deployment .env file")
    os.fchmod(env_fd, 0o600)
    if stat.S_IMODE(os.fstat(env_fd).st_mode) != 0o600:
        raise OSError("deployment .env permissions remain unsafe")
finally:
    if env_fd is not None:
        os.close(env_fd)
    os.close(deploy_dir_fd)
print('.env permissions: 600')
AXISAI_ENV_GUARD_PY

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

root_external install -o '{deploy_user}' -m 0600 /dev/null \
  '{MONOTONIC_STATE_PATH}'
# Root materializes the helper as a digest-verified, root-owned, unwritable
# object inside a private root-owned directory.  It is never chowned to the
# deploy user and never lands directly in a world-writable directory, so the
# pathname handed across the privilege drop cannot be swapped for other bytes
# between validation and `execve`.
helper_dir="$(root_external python3 - 'materialize-helper' '{helper_sha256}' \
  <<'AXISAI_HELPER_MATERIALIZATION_PY'
{helper_materialization_source}AXISAI_HELPER_MATERIALIZATION_PY
)"
cleanup() {{
  root_external rm -r -f -- "$helper_dir" || true
  root_external rm -f -- '{MONOTONIC_STATE_PATH}' || true
}}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
case "$helper_dir" in
  /*/*) ;;
  *) echo 'privileged helper directory is unusable' >&2; exit 70 ;;
esac
script_path="$helper_dir/{HELPER_NAME}"
python3 - '{deploy_user}' "$script_path" '{deploy_sha}' "$deploy_dir" \
  "$public_health_url" '{OUTER_LOCK_CAPABILITY_FD}' <<'AXISAI_PRIVILEGE_DROP_PY'
{PRIVILEGE_DROP_SOURCE}AXISAI_PRIVILEGE_DROP_PY
"""
    encoded_bootstrap = base64.b64encode(inner_bootstrap.encode("utf-8")).decode("ascii")
    # The authority gate moved into the root-lock child and took the outer
    # script's `set -eu` with it.  The outer script is one command with no
    # parameter expansion, so the options are inert today -- but they were the
    # stated contract for whatever RunShellScript executes, and a second line
    # added later must not inherit a shell that keeps going after a failure.
    return (
        "set -eu\n"
        f"python3 - {shlex.quote(encoded_bootstrap)} "
        "<<'AXISAI_ROOT_LOCK_PY'\n"
        f"{ROOT_LOCK_WRAPPER_SOURCE}"
        "AXISAI_ROOT_LOCK_PY\n"
    )


def _authority_parameter(authority_token: str) -> str:
    try:
        parsed = UUID(authority_token)
    except ValueError as error:
        raise ConfigError("deployment authority token must be a canonical UUID") from error
    if str(parsed) != authority_token:
        raise ConfigError("deployment authority token must be a canonical UUID")
    return f"{AUTHORITY_PARAMETER_PREFIX}{authority_token}"


def build_remote_command(
    config: DeployConfig,
    script: bytes,
    authority_token: str | None = None,
) -> str:
    """Encode immutable deploy inputs for the fixed remote bootstrap."""
    encoded_script = base64.b64encode(script).decode("ascii")
    encoded_dir = base64.b64encode(config.deploy_dir.encode()).decode("ascii")
    encoded_url = base64.b64encode((config.public_health_url or "").encode()).decode("ascii")
    token = str(uuid4()) if authority_token is None else authority_token
    encoded_authority_parameter = base64.b64encode(
        _authority_parameter(token).encode("ascii")
    ).decode("ascii")
    return render_bootstrap(
        encoded_script,
        config.deploy_sha,
        encoded_dir,
        config.deploy_user,
        encoded_url,
        encoded_authority_parameter,
        config.region,
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


def send_command(
    config: DeployConfig,
    script: bytes,
    aws: AwsJsonRunner,
    authority_token: str | None = None,
    *,
    utc_now: UtcClock,
) -> str:
    """Deliver the encoded host script with separate delivery and execution bounds."""
    _require_live_clock(utc_now)
    token = str(uuid4()) if authority_token is None else authority_token
    remote_command = build_remote_command(config, script, token)
    if len(remote_command) > LOCAL_RUN_SHELL_COMMAND_MAX_CHARS:
        raise ConfigError(
            "generated RunShellScript command exceeds the local 65536-character "
            "growth guard"
        )
    parameters = {
        "commands": [remote_command],
        "executionTimeout": [str(EXECUTION_TIMEOUT_SECONDS)],
    }
    send_args = [
        "ssm", "send-command",
        "--region", config.region,
        "--instance-ids", config.instance_id,
        "--document-name", "AWS-RunShellScript",
        "--comment", f"axisai-deploy:{token}",
        "--timeout-seconds", str(DELIVERY_TIMEOUT_SECONDS),
        "--parameters", json.dumps(parameters, separators=(",", ":")),
    ]
    require_fresh_ssm_target(config, aws, utc_now)
    response = aws(send_args)
    return require_command_id(response)


def authorize_command(
    config: DeployConfig,
    authority_token: str,
    command_id: str,
    aws: AwsJsonRunner,
) -> None:
    value = f"{config.deploy_sha}:{command_id}"
    aws([
        "ssm", "put-parameter",
        "--region", config.region,
        "--name", _authority_parameter(authority_token),
        "--type", "String",
        "--value", value,
        "--overwrite",
    ])


def delete_authority_parameter(
    config: DeployConfig,
    authority_token: str,
    aws: AwsJsonRunner,
) -> None:
    aws([
        "ssm", "delete-parameter",
        "--region", config.region,
        "--name", _authority_parameter(authority_token),
    ])


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
    on_in_progress: Callable[[], None] | None = None,
    outer_deadline: float | None = None,
) -> InvocationResult:
    """Poll within a hard horizon using an AWS runner with bounded calls.

    The injected runner must return or raise ``AwsCliError`` within
    ``INVOCATION_CALL_TIMEOUT_SECONDS`` so a hung process cannot bypass the
    controller's wall-clock horizon.
    """
    deadline = monotonic() + POLL_HORIZON_SECONDS
    if outer_deadline is not None:
        deadline = min(deadline, outer_deadline)
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
            if on_in_progress is not None:
                on_in_progress()
        if comparison_status == "Success":
            if on_in_progress is not None and not execution_reported:
                raise InvocationProtocolError(
                    "command succeeded before delivery authorization"
                )
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
    utc_now: UtcClock,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    log: Callable[[str], None],
    *,
    run_git: GitRunner = _run_git,
) -> InvocationResult:
    """Run the single validated production deployment lifecycle in order."""
    _require_live_clock(utc_now)
    controller_deadline = monotonic() + CONTROLLER_BUDGET_SECONDS
    config = DeployConfig.from_environ(environ)
    validate_candidate(repo_path, config.deploy_sha, run_git=run_git)
    preflight(config, aws, utc_now)
    script = _load_host_script(repo_path)
    if controller_deadline - monotonic() < SEND_AND_MONITOR_RESERVE_SECONDS:
        raise ConfigError(
            "insufficient controller time for SendCommand and terminal monitoring reserve"
        )
    authority_token = str(uuid4())
    command_id = send_command(
        config, script, aws, authority_token, utc_now=utc_now,
    )
    log(f"SSM command created: {command_id}")
    try:
        authorize_command(config, authority_token, command_id, aws)
    except AwsCliError:
        log("SSM delivery authorization response ambiguous; monitoring command ID")
    else:
        log("SSM command delivery authorized")
    try:
        result = wait_for_invocation(
            config, command_id, aws, monotonic, sleep, log,
            outer_deadline=(
                controller_deadline - AUTHORITY_CLEANUP_RESERVE_SECONDS
            ),
        )
    except InvocationFailed as error:
        _emit_invocation_output(error.result, log)
        raise
    finally:
        try:
            delete_authority_parameter(config, authority_token, aws)
        except AwsCliError:
            log("SSM delivery authority cleanup failed")
    _emit_invocation_output(result, log)
    return result


def main(
    *,
    environ: Mapping[str, str] | None = None,
    repo_path: Path | None = None,
    aws: AwsJsonRunner | None = None,
    utc_now: UtcClock | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    log: Callable[[str], None] = print,
    run_git: GitRunner = _run_git,
) -> int:
    """CLI entrypoint returning non-zero for every typed deployment failure."""
    try:
        # Deliberately no `now: datetime` override. Wrapping a pre-sampled
        # instant in `lambda: now` produces a callable that passes every
        # downstream liveness guard forever, so an arbitrarily stale heartbeat
        # would clear the send boundary. The only override is a live clock.
        send_time_clock: UtcClock = (
            utc_now
            if utc_now is not None
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
