#!/usr/bin/env python3
"""Send an exact-SHA deploy to the AxisAI staging host over AWS SSM.

This is the staging counterpart of scripts/deploy_control.py, and it is
deliberately much smaller. It shares none of that module's production
machinery — no OIDC role assumption, no `production-deploy` concurrency group,
no environment approval, no SSM authority parameter, no automatic rollback.
Those protect real user data and a single production host from a racing
deployer; staging has neither. Reusing them here would create a second
long-term deployment authority whose rules could silently drift from
production's (docs/STAGING.md, "Why staging does not reuse the production
deploy path").

What it does keep is target identity, because that is the property whose
failure would be catastrophic rather than merely inconvenient:

  * the staging instance id must be supplied explicitly;
  * the production instance id must ALSO be supplied, so the two can be
    compared and an accidental production target refused by identity rather
    than by trusting a name;
  * the revision must be a 40-hex commit sha, never a branch name.

Usage:
    AXISAI_STAGING_INSTANCE_ID=i-... \
    AXISAI_PRODUCTION_INSTANCE_ID=i-... \
    python3 scripts/staging_control.py <40-hex sha>
"""
from __future__ import annotations

import base64
import os
import pathlib
import re
import sys
import time

import boto3

# The application logs in Turkish, so a deploy's captured output routinely
# contains non-ASCII characters. On a Windows console the default encoding is a
# legacy codepage that cannot represent them, and printing the result would
# raise UnicodeEncodeError *after* a successful deploy — reporting failure for
# work that succeeded. Force UTF-8 and degrade unrepresentable characters
# instead of losing the report.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - exotic stream
        pass

REGION = os.environ.get("AWS_REGION", "eu-central-1")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
INSTANCE_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")
# Every value interpolated into the remote command is validated, including this
# one. It is an environment variable, not a regex-checked argument, so it was
# the one interpolation that a shell metacharacter could have escaped — and the
# command runs as root on the target. An absolute path of ordinary path
# characters cannot terminate the surrounding quoting.
STAGING_ROOT_PATTERN = re.compile(r"^/[A-Za-z0-9._/-]{1,200}$")
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
POLL_SECONDS = 10
MAX_WAIT_SECONDS = 3600


class Refused(Exception):
    """A precondition failed. Nothing has been sent."""


def _require_instance(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise Refused(f"{name} is not set")
    if not INSTANCE_PATTERN.match(value):
        raise Refused(f"{name} is not an EC2 instance id: {value!r}")
    return value


def resolve_target() -> tuple[str, str]:
    staging = _require_instance("AXISAI_STAGING_INSTANCE_ID")
    production = _require_instance("AXISAI_PRODUCTION_INSTANCE_ID")
    if staging == production:
        raise Refused("staging instance id IS the production instance id")
    return staging, production


def resolve_staging_root() -> str:
    value = (os.environ.get("AXISAI_STAGING_ROOT") or "/opt/axisai-staging").strip()
    if not STAGING_ROOT_PATTERN.match(value):
        raise Refused(f"AXISAI_STAGING_ROOT is not a plain absolute path: {value!r}")
    return value.rstrip("/")


def resolve_revision(argv: list[str]) -> str:
    if len(argv) != 2:
        raise Refused("usage: staging_control.py <40-hex commit sha>")
    revision = argv[1].strip()
    if not SHA_PATTERN.match(revision):
        raise Refused(f"revision must be a 40-hex commit sha, got {revision!r}")
    return revision


def _unix_bytes(path: pathlib.Path) -> bytes:
    """Read a file that is about to be executed on Linux, with LF endings.

    This controller runs on a workstation, and on Windows `core.autocrlf=true`
    is the default that git for Windows installs — so a checked-out
    `staging_deploy.sh` has CRLF endings in the working copy even though the
    blob in the repository is LF. Shipping those bytes verbatim hands Linux
    bash a script whose every line ends in a stray carriage return; the shell
    reads the first line as `set -euo pipefail\\r` and dies on an invalid
    option name. Git Bash tolerates CR and therefore cannot reproduce it, which
    is what makes this worth normalising rather than trusting.

    The production controller does not need this: it ships its helper from a
    GitHub Actions Ubuntu checkout, where the working copy is already LF.
    """
    return path.read_bytes().replace(b"\r\n", b"\n")


def build_command(revision: str, production_id: str, staging_root: str) -> str:
    """Render the remote script.

    The helper and the compose overlay travel WITH the command rather than
    being read from the deployed checkout, so a revision that predates this
    tooling — b77a1dc, the one Sprint 14 PR5 requires — is still deployable.
    Both are base64-encoded so no quoting in their content can terminate the
    surrounding shell command.

    The staging instance id is deliberately NOT a parameter here. It constrains
    `send_command`'s `InstanceIds`, which is the only place it could mean
    anything; taking it as an argument this function then ignored would suggest
    the payload is somehow bound to a target, and it is not.
    """
    helper = _unix_bytes(REPO_ROOT / "scripts" / "staging_deploy.sh")
    overlay = _unix_bytes(REPO_ROOT / "docker-compose.staging.yml")
    encoded_helper = base64.b64encode(helper).decode("ascii")
    encoded_overlay = base64.b64encode(overlay).decode("ascii")
    return f"""set -euo pipefail
test -f {staging_root}/ENVIRONMENT
test "$(cat {staging_root}/ENVIRONMENT)" = "staging"
printf '%s' '{encoded_helper}' | base64 --decode > {staging_root}/staging_deploy.sh
printf '%s' '{encoded_overlay}' | base64 --decode > {staging_root}/docker-compose.staging.yml
chmod 0755 {staging_root}/staging_deploy.sh
chown axisai:axisai {staging_root}/staging_deploy.sh {staging_root}/docker-compose.staging.yml
# Run as the unprivileged owner of the checkout. SSM commands execute as root;
# building and running the compose project as root would leave root-owned
# artefacts in a tree the `axisai` user has to manage afterwards.
sudo -u axisai -H \
  AXISAI_STAGING_ROOT={staging_root} \
  AXISAI_PRODUCTION_INSTANCE_ID={production_id} \
  bash {staging_root}/staging_deploy.sh {revision}
"""


def main() -> int:
    try:
        revision = resolve_revision(sys.argv)
        staging_id, production_id = resolve_target()
        staging_root = resolve_staging_root()
    except Refused as refusal:
        print(f"staging-control: refused: {refusal}", file=sys.stderr)
        return 2

    ssm = boto3.client("ssm", region_name=REGION)
    print(f"staging-control: deploying {revision} to {staging_id} in {REGION}")
    sent = ssm.send_command(
        InstanceIds=[staging_id],
        DocumentName="AWS-RunShellScript",
        Comment=f"axisai staging deploy {revision[:12]}",
        TimeoutSeconds=600,
        Parameters={
            "commands": [build_command(revision, production_id, staging_root)],
            "executionTimeout": ["3600"],
        },
    )
    command_id = sent["Command"]["CommandId"]
    print(f"staging-control: command {command_id}")

    deadline = time.monotonic() + MAX_WAIT_SECONDS
    invocation = None
    while True:
        time.sleep(POLL_SECONDS)
        try:
            invocation = ssm.get_command_invocation(
                CommandId=command_id, InstanceId=staging_id)
        except ssm.exceptions.InvocationDoesNotExist:
            # Invocation records are eventually consistent. The command HAS
            # been dispatched, so dying here with a traceback would leave the
            # operator unable to tell whether a deploy is running. Keep polling
            # until the deadline, which is the same thing a slow start does.
            status = "Pending"
        else:
            status = invocation["Status"]
            if status not in ("Pending", "InProgress", "Delayed"):
                break
        if time.monotonic() > deadline:
            print("staging-control: timed out waiting for the deploy",
                  file=sys.stderr)
            return 1

    print(invocation.get("StandardOutputContent", ""))
    errors = invocation.get("StandardErrorContent", "")
    if errors:
        print(errors, file=sys.stderr)
    if status != "Success":
        print(f"staging-control: deploy {status}", file=sys.stderr)
        return 1
    print(f"staging-control: staging is running {revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
