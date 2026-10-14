"""Mandatory deployment-specific recovery proof; no live calls at import time."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from uuid import uuid4

if __package__:
    from .deploy_contract import RECOVERY_PREFLIGHT_SECONDS
else:
    from deploy_contract import RECOVERY_PREFLIGHT_SECONDS

RECOVERY_SECONDS = RECOVERY_PREFLIGHT_SECONDS
CALL_SECONDS = 60
POLL_SECONDS = 15
DB_ID = re.compile(r"[a-z][a-z0-9-]{0,62}")
DB_ARN = re.compile(r"arn:aws:rds:eu-central-1:([0-9]{12}):db:([a-z][a-z0-9-]{0,62})")
ROLE_ARN = re.compile(r"arn:aws:iam::([0-9]{12}):role/([A-Za-z0-9+=,.@_/-]+)")


class RecoveryError(RuntimeError):
    """Sanitized failure: never include API payloads or environment values."""


def require_recovery(environ: Mapping[str, str], aws: Callable, monotonic: Callable,
                     sleep: Callable, log: Callable) -> str:
    """Create once and independently prove Available before any host authority.

    The injected AWS runner must bound each call to CALL_SECONDS. Errors and
    interruptions propagate; no ambiguous create is retried and no old backup
    can substitute for this invocation's unique recovery point.
    """
    identifier = environ.get("RDS_INSTANCE_ID", "")
    arn = environ.get("EXPECTED_RDS_INSTANCE_ARN", "")
    role = environ.get("EXPECTED_DEPLOY_ROLE_ARN", "")
    sha = environ.get("DEPLOY_SHA", "")
    db_match, role_match = DB_ARN.fullmatch(arn), ROLE_ARN.fullmatch(role)
    if (not DB_ID.fullmatch(identifier) or identifier.endswith("-") or "--" in identifier
            or db_match is None or db_match[2] != identifier
            or role_match is None or role_match[1] != db_match[1]
            or environ.get("AWS_REGION") != "eu-central-1"
            or re.fullmatch(r"[0-9a-f]{40}", sha) is None):
        raise RecoveryError("recovery configuration missing or invalid")
    account = db_match[1]
    role_name = role_match[2].rsplit("/", 1)[-1]
    if role_name in {"axisai-staging-operator", "axisai-deployer"}:
        raise RecoveryError("recovery identity is not a production OIDC role")
    deadline = monotonic() + RECOVERY_SECONDS

    def call(args):
        if deadline - monotonic() <= CALL_SECONDS:
            raise RecoveryError("recovery verification timed out")
        response = aws([*args, "--region", "eu-central-1"])
        if monotonic() >= deadline:
            raise RecoveryError("recovery verification timed out")
        if not isinstance(response, Mapping):
            raise RecoveryError("recovery response malformed")
        return response

    identity = call(["sts", "get-caller-identity"])
    expected_identity = rf"arn:aws:sts::{account}:assumed-role/{re.escape(role_name)}/[A-Za-z0-9+=,.@_-]+"
    identity_arn = identity.get("Arn")
    if (identity.get("Account") != account or not isinstance(identity_arn, str)
            or re.fullmatch(expected_identity, identity_arn) is None):
        raise RecoveryError("recovery caller identity rejected")
    instances = call(["rds", "describe-db-instances", "--db-instance-identifier", identifier]).get("DBInstances")
    if not isinstance(instances, list) or len(instances) != 1 or not isinstance(instances[0], Mapping):
        raise RecoveryError("recovery instance response malformed")
    instance = instances[0]
    resource_id = instance.get("DbiResourceId")
    if (instance.get("DBInstanceArn") != arn or instance.get("DBInstanceIdentifier") != identifier
            or instance.get("Engine") != "postgres" or instance.get("StorageEncrypted") is not True
            or instance.get("DBInstanceStatus") != "available"
            or not isinstance(resource_id, str) or re.fullmatch(r"db-[A-Z0-9]+", resource_id) is None):
        raise RecoveryError("recovery instance identity or state rejected")
    snapshot_id = f"predeploy-{sha}-{uuid4().hex}"
    snapshot_arn = f"arn:aws:rds:eu-central-1:{account}:snapshot:{snapshot_id}"

    def status(snapshot):
        if (not isinstance(snapshot, Mapping)
                or snapshot.get("DBSnapshotIdentifier") != snapshot_id
                or snapshot.get("DBInstanceIdentifier") != identifier
                or snapshot.get("DBSnapshotArn") != snapshot_arn
                or snapshot.get("DbiResourceId") != resource_id
                or snapshot.get("SnapshotType") != "manual"
                or snapshot.get("Engine") != "postgres"
                or snapshot.get("Encrypted") is not True):
            raise RecoveryError("recovery snapshot identity or response rejected")
        state = snapshot.get("Status")
        if not isinstance(state, str) or state not in {"creating", "available"}:
            raise RecoveryError("recovery snapshot unavailable or unknown")
        return state

    response = call(["rds", "create-db-snapshot", "--db-instance-identifier", identifier,
                     "--db-snapshot-identifier", snapshot_id])
    status(response.get("DBSnapshot"))
    log("Recovery snapshot request accepted; independent availability proof required")
    while True:
        snapshots = call(["rds", "describe-db-snapshots", "--db-snapshot-identifier", snapshot_id]).get("DBSnapshots")
        if not isinstance(snapshots, list) or len(snapshots) != 1:
            raise RecoveryError("recovery snapshot readback unavailable")
        if status(snapshots[0]) == "available":
            log(f"Recovery snapshot Available for candidate {sha}: {snapshot_id}")
            return snapshot_id
        log("Recovery snapshot creating; migration remains unauthorized")
        sleep(min(POLL_SECONDS, max(0, deadline - monotonic() - CALL_SECONDS)))
