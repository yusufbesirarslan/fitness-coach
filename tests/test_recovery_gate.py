"""Offline failure injection: real recovery gate and controller, fake AWS only."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scripts import deploy_control as controller
from scripts import recovery_gate as gate

SHA = "a" * 40
ACCOUNT = "123456789012"
ENV = {
    "DEPLOY_SHA": SHA, "AWS_REGION": "eu-central-1",
    "RDS_INSTANCE_ID": "qualified-db",
    "EXPECTED_RDS_INSTANCE_ARN": f"arn:aws:rds:eu-central-1:{ACCOUNT}:db:qualified-db",
    "EXPECTED_DEPLOY_ROLE_ARN": f"arn:aws:iam::{ACCOUNT}:role/production-oidc",
    "EC2_INSTANCE_ID": "i-0123456789abcdef0", "DEPLOY_USER": "deploy",
    "DEPLOY_DIR": "/srv/axisai", "PUBLIC_HEALTH_URL": "https://example.test/health",
    "APPLE_SUBSCRIPTIONS_ENABLED": "0",
}
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
COMMAND = "11111111-1111-1111-1111-111111111111"


class World:
    def __init__(self, states=("available",), change=None, fail=None):
        self.now = 0
        self.calls = []
        self.logs = []
        self.states = list(states)
        self.change = change or (lambda operation, response: response)
        self.fail = fail
        self.snapshot = None
        self.source = {
            "DBInstanceArn": ENV["EXPECTED_RDS_INSTANCE_ARN"],
            "DBInstanceIdentifier": "qualified-db", "Engine": "postgres",
            "StorageEncrypted": True, "DBInstanceStatus": "available",
            "DbiResourceId": "db-ABC123",
        }

    def clock(self):
        return self.now

    def sleep(self, seconds):
        assert 0 <= seconds <= gate.POLL_SECONDS
        self.now += seconds

    def aws(self, args):
        operation = args[1]
        self.calls.append(args)
        if operation == self.fail:
            raise controller.AwsCliError("sanitized failure", code="AccessDenied", kind="exit")
        if operation == "get-caller-identity":
            response = {"Account": ACCOUNT, "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/production-oidc/axisai-test"}
        elif operation == "describe-db-instances":
            response = {"DBInstances": [self.source]}
        elif operation == "create-db-snapshot":
            name = args[args.index("--db-snapshot-identifier") + 1]
            self.snapshot = {
                "DBSnapshotIdentifier": name, "DBInstanceIdentifier": "qualified-db",
                "DBSnapshotArn": f"arn:aws:rds:eu-central-1:{ACCOUNT}:snapshot:{name}",
                "DbiResourceId": "db-ABC123", "SnapshotType": "manual",
                "Engine": "postgres", "Encrypted": True, "Status": "creating",
            }
            response = {"DBSnapshot": deepcopy(self.snapshot)}
        elif operation == "describe-db-snapshots":
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            response = {"DBSnapshots": [{**self.snapshot, "Status": state}]}
        elif operation == "describe-instances":
            response = {"Reservations": [{"Instances": [{"InstanceId": ENV["EC2_INSTANCE_ID"], "State": {"Name": "running"}}]}]}
        elif operation == "describe-instance-information":
            response = {"InstanceInformationList": [{"InstanceId": ENV["EC2_INSTANCE_ID"], "PingStatus": "Online", "LastPingDateTime": NOW.isoformat()}]}
        elif operation == "send-command":
            response = {"Command": {"CommandId": COMMAND}}
        elif operation in {"put-parameter", "delete-parameter"}:
            response = {}
        elif operation == "get-command-invocation":
            response = {"StatusDetails": "Success", "ResponseCode": 0, "StandardOutputContent": "", "StandardErrorContent": ""}
        else:
            raise AssertionError(f"unmodeled operation {operation}")
        return self.change(operation, deepcopy(response))

    def run(self, env=None):
        return gate.require_recovery(ENV if env is None else env, self.aws, self.clock, self.sleep, self.logs.append)

    def operations(self):
        return [call[1] for call in self.calls]


def run_controller(world, tmp_path, monkeypatch, env=None, git=None):
    monkeypatch.setattr(controller, "_load_host_script", lambda path: b"#!/bin/sh\nexit 0\n")
    return controller.main(environ=ENV if env is None else env, repo_path=tmp_path,
                           aws=world.aws, utc_now=lambda: NOW, monotonic=world.clock,
                           sleep=world.sleep, log=world.logs.append,
                           run_git=git or (lambda args: SHA))


@pytest.mark.parametrize("states", [("available",), ("creating", "available"), ("creating", "creating", "available")])
def test_only_independent_available_readback_authorizes_ssm(states, tmp_path, monkeypatch):
    world = World(states)
    assert run_controller(world, tmp_path, monkeypatch) == 0
    assert world.snapshot["DBSnapshotIdentifier"].startswith(f"predeploy-{SHA}-")
    assert world.operations().count("create-db-snapshot") == 1
    assert "send-command" in world.operations() and "put-parameter" in world.operations()
    assert "delete-parameter" in world.operations()


@pytest.mark.parametrize("key,value", [
    ("RDS_INSTANCE_ID", None), ("RDS_INSTANCE_ID", ""), ("RDS_INSTANCE_ID", "../bad"),
    ("RDS_INSTANCE_ID", "wrong-db"), ("RDS_INSTANCE_ID", "db-"), ("RDS_INSTANCE_ID", "db--x"),
    ("EXPECTED_RDS_INSTANCE_ARN", ""), ("EXPECTED_RDS_INSTANCE_ARN", "arn:aws:rds:us-east-1:123456789012:db:qualified-db"),
    ("AWS_REGION", "us-east-1"), ("EXPECTED_DEPLOY_ROLE_ARN", ""),
    ("EXPECTED_DEPLOY_ROLE_ARN", "arn:aws:iam::999999999999:role/production-oidc"),
    ("EXPECTED_DEPLOY_ROLE_ARN", "arn:aws:iam::123456789012:role/axisai-staging-operator"),
    ("EXPECTED_DEPLOY_ROLE_ARN", "arn:aws:iam::123456789012:role/axisai-deployer"),
    ("DEPLOY_SHA", "unknown"),
])
def test_bad_config_never_creates_or_sends(key, value, tmp_path, monkeypatch):
    env = {**ENV, key: value} if value is not None else {k: v for k, v in ENV.items() if k != key}
    world = World()
    assert run_controller(world, tmp_path, monkeypatch, env) == 1
    assert "create-db-snapshot" not in world.operations()
    assert "send-command" not in world.operations()
    assert value is None or value == "" or value not in "\n".join(world.logs)


@pytest.mark.parametrize("field,value", [("Account", "999999999999"), ("Arn", "arn:aws:iam::123456789012:root"),
    ("Arn", "arn:aws:sts::123456789012:assumed-role/axisai-staging-operator/session"), ("Arn", None)])
def test_wrong_identity_stops_before_snapshot(field, value, tmp_path, monkeypatch):
    world = World(change=lambda op, result: {**result, field: value} if op == "get-caller-identity" else result)
    assert run_controller(world, tmp_path, monkeypatch) == 1
    assert "create-db-snapshot" not in world.operations()
    assert "send-command" not in world.operations()


@pytest.mark.parametrize("operation", ["get-caller-identity", "describe-db-instances", "create-db-snapshot", "describe-db-snapshots"])
def test_permission_failure_never_sends(operation, tmp_path, monkeypatch):
    world = World(fail=operation)
    assert run_controller(world, tmp_path, monkeypatch) == 1
    assert "send-command" not in world.operations()
    assert world.operations().count("create-db-snapshot") <= 1


@pytest.mark.parametrize("code", ["Throttling", "ServiceUnavailable", "AccessDenied", "DBSnapshotNotFound", None])
@pytest.mark.parametrize("operation", ["create-db-snapshot", "describe-db-snapshots"])
def test_aws_errors_fail_closed_without_retry(code, operation, tmp_path, monkeypatch):
    def fail(op, result):
        if op == operation:
            raise controller.AwsCliError("sanitized", code=code, kind="exit")
        return result
    world = World(change=fail)
    assert run_controller(world, tmp_path, monkeypatch) == 1
    assert "send-command" not in world.operations()
    assert world.operations().count(operation) == 1


@pytest.mark.parametrize("state", ["failed", "deleted", "deleting", "unknown", "", None, 0, []])
def test_unavailable_snapshot_never_sends(state, tmp_path, monkeypatch):
    world = World((state,))
    assert run_controller(world, tmp_path, monkeypatch) == 1
    assert "send-command" not in world.operations()


def test_creating_timeout_does_not_authorize_ssm(tmp_path, monkeypatch):
    world = World(("creating",))
    assert run_controller(world, tmp_path, monkeypatch) == 1
    assert world.now == gate.RECOVERY_SECONDS - gate.CALL_SECONDS
    assert "send-command" not in world.operations()
    assert "timed out" in world.logs[-1]


@pytest.mark.parametrize("operation", ["create-db-snapshot", "describe-db-snapshots"])
@pytest.mark.parametrize("field,value", [("DBSnapshotIdentifier", "old-snapshot"), ("DBInstanceIdentifier", "wrong-db"),
    ("DBSnapshotArn", "arn:aws:rds:us-east-1:123456789012:snapshot:wrong"),
    ("DbiResourceId", "db-REPLACEMENT"), ("SnapshotType", "automated"), ("Engine", "mysql"),
    ("Encrypted", False), ("Status", None)])
def test_snapshot_identity_is_bound_on_create_and_readback(operation, field, value, tmp_path, monkeypatch):
    def corrupt(op, result):
        if op == operation:
            record = result["DBSnapshot"] if op == "create-db-snapshot" else result["DBSnapshots"][0]
            record[field] = value
        return result
    world = World(change=corrupt)
    assert run_controller(world, tmp_path, monkeypatch) == 1
    assert "send-command" not in world.operations()


@pytest.mark.parametrize("field,value", [("DBInstanceArn", "wrong"), ("DBInstanceIdentifier", "wrong"),
    ("DbiResourceId", None), ("Engine", "mysql"), ("StorageEncrypted", False), ("DBInstanceStatus", "deleting")])
def test_instance_mismatch_stops_before_create(field, value, tmp_path, monkeypatch):
    world = World()
    world.source[field] = value
    assert run_controller(world, tmp_path, monkeypatch) == 1
    assert "create-db-snapshot" not in world.operations()
    assert "send-command" not in world.operations()


@pytest.mark.parametrize("operation", ["get-caller-identity", "describe-db-instances", "create-db-snapshot", "describe-db-snapshots"])
@pytest.mark.parametrize("response", [None, {}, [], {"DBSnapshots": []}, {"DBSnapshots": [None, None]}])
def test_malformed_responses_never_send(operation, response, tmp_path, monkeypatch):
    world = World(change=lambda op, result: response if op == operation else result)
    assert run_controller(world, tmp_path, monkeypatch) == 1
    assert "send-command" not in world.operations()


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_polling_interruption_cannot_authorize_host(interrupt, tmp_path, monkeypatch):
    world = World(("creating",))
    def stop(seconds):
        raise interrupt()
    world.sleep = stop
    with pytest.raises(interrupt):
        run_controller(world, tmp_path, monkeypatch)
    assert "send-command" not in world.operations()


def test_available_after_deadline_is_rejected(tmp_path, monkeypatch):
    world = World()
    def delay(op, response):
        if op == "describe-db-snapshots":
            world.now = gate.RECOVERY_SECONDS
        return response
    world.change = delay
    assert run_controller(world, tmp_path, monkeypatch) == 1
    assert "send-command" not in world.operations()


@pytest.mark.parametrize("after_recovery", [False, True])
@pytest.mark.parametrize("ancestor", [False, True])
def test_stale_or_superseded_candidate_cannot_send(after_recovery, ancestor, tmp_path, monkeypatch):
    world = World()
    def git(args):
        if "rev-parse" in args:
            return "b" * 40 if not after_recovery or world.snapshot else SHA
        if "merge-base" in args:
            return SHA if ancestor else "c" * 40
        return ""
    assert run_controller(world, tmp_path, monkeypatch, git=git) == (0 if ancestor else 1)
    assert "send-command" not in world.operations()
    assert (world.snapshot is not None) == after_recovery


def test_immediate_available_create_still_requires_readback(tmp_path, monkeypatch):
    world = World(fail="describe-db-snapshots", change=lambda op, result:
                  {"DBSnapshot": {**result["DBSnapshot"], "Status": "available"}} if op == "create-db-snapshot" else result)
    assert run_controller(world, tmp_path, monkeypatch) == 1
    assert "send-command" not in world.operations()


def test_no_credentials_or_connection_data_in_recovery_logs(tmp_path, monkeypatch):
    world = World()
    world.source.update(MasterUsername="PROTECTED", Endpoint={"Address": "PROTECTED"})
    assert run_controller(world, tmp_path, monkeypatch) == 0
    logs = "\n".join(world.logs)
    assert "PROTECTED" not in logs
    assert ENV["EXPECTED_RDS_INSTANCE_ARN"] not in logs
    assert ENV["EXPECTED_DEPLOY_ROLE_ARN"] not in logs


def test_create_cli_has_single_attempt_and_bounded_timeout():
    captured = []
    def run(args, **kwargs):
        captured.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")
    controller.run_aws_json(["rds", "create-db-snapshot"], run=run)
    assert captured[0][1]["env"]["AWS_MAX_ATTEMPTS"] == "1"
    assert captured[0][1]["timeout"] == gate.CALL_SECONDS


def test_runner_interruption_does_not_undo_authorized_host_migration(tmp_path, monkeypatch):
    world = World()
    host = {"schema_mutated": False}
    def continue_host(op, response):
        if op == "put-parameter":
            host["schema_mutated"] = True
        if op == "get-command-invocation":
            raise KeyboardInterrupt()
        return response
    world.change = continue_host
    with pytest.raises(KeyboardInterrupt):
        run_controller(world, tmp_path, monkeypatch)
    assert host["schema_mutated"] is True
    assert "delete-parameter" in world.operations()
    assert "cancel-command" not in world.operations()


def test_qualification_uses_no_real_aws_or_git_process(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline qualification attempted a real subprocess")
    monkeypatch.setattr(controller.subprocess, "run", forbidden)
    world = World()
    assert run_controller(world, tmp_path, monkeypatch) == 0
