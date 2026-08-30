import ast
import base64
import hashlib
import inspect
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

import scripts.deploy_control as deploy_control
from scripts.deploy_contract import (
    CONTROLLER_REQUIRED_SECONDS,
    SSM_HEARTBEAT_MAX_AGE_SECONDS,
    HOST_PHASE_SECONDS,
    HOST_WORST_CASE_SECONDS,
    SSM_EXECUTION_MARGIN_SECONDS,
    SSM_EXECUTION_TIMEOUT_SECONDS,
    host_timeout_environment,
)
from scripts.deploy_control import (
    AWS_CLI_CALL_TIMEOUT_SECONDS,
    AWS_EXPIRY_SECONDS,
    AwsCliError,
    ConfigError,
    CONTROLLER_BUDGET_SECONDS,
    DELIVERY_TIMEOUT_SECONDS,
    DeployConfig,
    EXECUTION_TIMEOUT_SECONDS,
    GIT_CALL_TIMEOUT_SECONDS,
    CandidateSuperseded,
    GitCliError,
    INVOCATION_CALL_TIMEOUT_SECONDS,
    InvocationFailed,
    InvocationPollingTimeout,
    InvocationProtocolError,
    POLL_HORIZON_SECONDS,
    SEND_AND_MONITOR_RESERVE_SECONDS,
    PreflightError,
    build_remote_command,
    main,
    preflight,
    require_fresh_ssm_target,
    read_invocation,
    run_aws_json,
    run_deploy,
    send_command,
    validate_candidate,
    wait_for_invocation,
)


VALID_ENV = {
    "DEPLOY_SHA": "a" * 40,
    "AWS_REGION": "eu-central-1",
    "EC2_INSTANCE_ID": "i-0c6f5352fc214e68d",
    "DEPLOY_USER": "deploy",
    "DEPLOY_DIR": "/srv/axisai",
    "PUBLIC_HEALTH_URL": "https://fitness.example/health",
}

NOW = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
AUTHORITY_TOKEN = "11111111-1111-1111-1111-111111111111"
COMMAND_ID = "22222222-2222-2222-2222-222222222222"
HOST_SCRIPT = b"#!/bin/sh\nexit 0\n"


def managed_instance_response(last_ping: datetime, *, ping_status: str = "Online"):
    return {"InstanceInformationList": [{
        "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
        "PingStatus": ping_status,
        "LastPingDateTime": last_ping.isoformat(),
    }]}


def fresh_then_stale_ssm_runner(*, age_seconds: int):
    describe_count = 0
    sent = False

    def aws(args):
        nonlocal describe_count, sent
        if args[:2] == ["ec2", "describe-instances"]:
            return {"Reservations": [{"Instances": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "State": {"Name": "running"},
            }]}]}
        if args[:2] == ["ssm", "describe-instance-information"]:
            describe_count += 1
            age = 0 if describe_count == 1 else age_seconds
            return managed_instance_response(NOW - timedelta(seconds=age))
        if args[:2] == ["ssm", "send-command"]:
            sent = True
            return {"Command": {"CommandId": COMMAND_ID}}
        raise AssertionError(args)

    aws.sent = lambda: sent
    return aws


class FakeClock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake_clock():
    return FakeClock()


@pytest.fixture
def workspace_tmp_dir():
    path = Path(tempfile.mkdtemp(prefix="deploy-control-test-", dir=Path.cwd()))
    try:
        yield path
    finally:
        shutil.rmtree(path)


def invocation_response(status, *, response_code=-1, stdout="", stderr=""):
    return {
        "CommandId": "11111111-1111-1111-1111-111111111111",
        "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
        "Status": status.replace(" ", ""),
        "StatusDetails": status,
        "ResponseCode": response_code,
        "StandardOutputContent": stdout,
        "StandardErrorContent": stderr,
    }


def invocation_sequence(*statuses):
    responses = iter(invocation_response(status) for status in statuses)

    def aws(args):
        return next(responses)

    return aws


def fake_git_returning(sha):
    calls = []

    def run_git(args):
        calls.append(args)
        if args[-2:] == ["rev-parse", "refs/remotes/origin/main"]:
            return sha
        return ""

    run_git.calls = calls
    return run_git


def fake_git_with_history(origin_main, merge_base):
    """A git fake that can also answer where the candidate sits in history."""
    calls = []

    def run_git(args):
        calls.append(args)
        if args[-2:] == ["rev-parse", "refs/remotes/origin/main"]:
            return origin_main
        if len(args) > 3 and args[-3] == "merge-base":
            return merge_base
        return ""

    run_git.calls = calls
    return run_git


def test_config_accepts_one_explicit_production_target():
    config = DeployConfig.from_environ(VALID_ENV)

    assert config.deploy_sha == "a" * 40
    assert config.instance_id == "i-0c6f5352fc214e68d"
    assert config.public_health_url == "https://fitness.example/health"


@pytest.mark.parametrize("sha", ["", "main", "A" * 40, "a" * 39, "a" * 41, "a" * 39 + ";", "a" * 39 + "\n"])
def test_config_rejects_noncanonical_sha(sha):
    with pytest.raises(ConfigError):
        DeployConfig.from_environ({**VALID_ENV, "DEPLOY_SHA": sha})


@pytest.mark.parametrize("instance_id", ["", "i-0c6f5352fc214e68d,i-12345678", "i-1234567g", "i-0c6f5352fc214e68d "])
def test_config_rejects_empty_multiple_or_malformed_instance_ids(instance_id):
    with pytest.raises(ConfigError):
        DeployConfig.from_environ({**VALID_ENV, "EC2_INSTANCE_ID": instance_id})


@pytest.mark.parametrize("region", ["us-east-1", "eu-central-1\n"])
def test_config_rejects_nonproduction_region(region):
    with pytest.raises(ConfigError):
        DeployConfig.from_environ({**VALID_ENV, "AWS_REGION": region})


@pytest.mark.parametrize("user", ["deploy;whoami", "deploy user", "deploy\n"])
def test_config_rejects_unsafe_deploy_user(user):
    with pytest.raises(ConfigError):
        DeployConfig.from_environ({**VALID_ENV, "DEPLOY_USER": user})


@pytest.mark.parametrize("deploy_dir", [
    "srv/axisai",
    "",
    "/srv/axisai\nother",
    "/srv/axis ai",
    "/srv/axis'ai",
    "/srv/axisai/../other",
    "/srv//axisai",
    "/srv/axisai/.",
    "/srv/axisai/",
])
def test_config_rejects_noncanonical_or_shell_unsafe_deploy_dir(deploy_dir):
    with pytest.raises(ConfigError):
        DeployConfig.from_environ({**VALID_ENV, "DEPLOY_DIR": deploy_dir})


@pytest.mark.parametrize("url", [
    "http://fitness.example/health",
    "https://",
    "https://deploy@fitness.example/health",
    "https://:secret@fitness.example/health",
    "https://fitne\nss.example/health",
    "https://fitne\tss.example/health",
    " https://fitness.example/health",
    "https://fitness.example/health ",
])
def test_config_rejects_non_https_or_credentialed_health_url(url):
    with pytest.raises(ConfigError):
        DeployConfig.from_environ({**VALID_ENV, "PUBLIC_HEALTH_URL": url})


def test_config_allows_an_empty_optional_health_url():
    config = DeployConfig.from_environ({**VALID_ENV, "PUBLIC_HEALTH_URL": ""})

    assert config.public_health_url is None


def test_validate_candidate_requires_origin_main_to_equal_deploy_sha():
    with pytest.raises(ConfigError, match="stale"):
        validate_candidate(Path("/candidate"), "a" * 40, run_git=fake_git_returning("b" * 40))


def test_validate_candidate_skips_a_candidate_main_has_moved_past():
    """main moved forward while the deploy waited for approval: skip, do not fail."""
    deploy_sha = "a" * 40
    run_git = fake_git_with_history("b" * 40, merge_base=deploy_sha)

    with pytest.raises(CandidateSuperseded, match="superseded"):
        validate_candidate(Path("/candidate"), deploy_sha, run_git=run_git)


def test_validate_candidate_rejects_a_candidate_that_left_origin_main_history():
    """A candidate off main's history is a rewrite, not a supersession: fail closed."""
    run_git = fake_git_with_history("b" * 40, merge_base="c" * 40)

    with pytest.raises(ConfigError, match="stale"):
        validate_candidate(Path("/candidate"), "a" * 40, run_git=run_git)


def test_validate_candidate_asks_git_where_the_candidate_sits_in_main_history():
    deploy_sha = "a" * 40
    origin_main = "b" * 40
    run_git = fake_git_with_history(origin_main, merge_base=deploy_sha)

    with pytest.raises(CandidateSuperseded):
        validate_candidate(Path("/candidate"), deploy_sha, run_git=run_git)

    assert run_git.calls[3][-3:] == ["merge-base", deploy_sha, origin_main]


def test_a_superseded_candidate_is_not_a_config_error():
    """`main` must not fold supersession into the exit-1 failure family."""
    assert not issubclass(CandidateSuperseded, ConfigError)


def test_validate_candidate_fetches_and_checks_the_current_origin_main():
    run_git = fake_git_returning("a" * 40)

    validate_candidate(Path("/candidate"), "a" * 40, run_git=run_git)

    assert run_git.calls[0][-4:] == ["fetch", "origin", "main", "--prune"]
    assert run_git.calls[1][-3:] == ["cat-file", "-e", ("a" * 40) + "^{commit}"]
    assert run_git.calls[2][-2:] == ["rev-parse", "refs/remotes/origin/main"]


def test_git_runner_has_explicit_timeout_and_typed_diagnostic():
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    with pytest.raises(GitCliError, match="git fetch timed out after 60 seconds"):
        deploy_control._run_git(
            ["git", "-C", "/candidate", "fetch", "origin", "main", "--prune"],
            run=run,
        )

    assert calls[0][1]["timeout"] == GIT_CALL_TIMEOUT_SECONDS == 60


@pytest.fixture
def aws_fixture():
    def make_aws(*, state, ping, heartbeat):
        calls = []
        responses = iter([
            {"Reservations": [{"Instances": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "State": {"Name": state},
            }]}]},
            {"InstanceInformationList": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "PingStatus": ping,
                "LastPingDateTime": heartbeat,
            }]},
        ])

        def aws(args):
            calls.append(args)
            return next(responses)

        return aws, calls

    return make_aws


def test_preflight_accepts_running_online_fresh_single_target():
    now = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
    calls = []
    responses = iter([
        {"Reservations": [{"Instances": [{
            "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
            "State": {"Name": "running"},
        }]}]},
        {"InstanceInformationList": [{
            "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
            "PingStatus": "Online",
            "LastPingDateTime": "2026-08-22T17:58:00+00:00",
        }]},
    ])

    def aws(args):
        calls.append(args)
        return next(responses)

    managed = preflight(DeployConfig.from_environ(VALID_ENV), aws, lambda: now)

    assert managed.instance_id == VALID_ENV["EC2_INSTANCE_ID"]
    assert calls[0][:2] == ["ec2", "describe-instances"]
    assert calls[1][:2] == ["ssm", "describe-instance-information"]


@pytest.mark.parametrize("state,ping,heartbeat", [
    ("stopped", "Online", "2026-08-22T17:58:00+00:00"),
    ("running", "Offline", "2026-08-22T17:58:00+00:00"),
    ("running", "Online", "2026-08-22T17:50:00+00:00"),
])
def test_preflight_rejects_unusable_target_before_send(state, ping, heartbeat, aws_fixture):
    aws, calls = aws_fixture(state=state, ping=ping, heartbeat=heartbeat)

    with pytest.raises(PreflightError):
        preflight(
            DeployConfig.from_environ(VALID_ENV),
            aws,
            lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
        )

    assert not any(call[:2] == ["ssm", "send-command"] for call in calls)


@pytest.mark.parametrize("entries", [[], [
    {"InstanceId": VALID_ENV["EC2_INSTANCE_ID"], "PingStatus": "Online", "LastPingDateTime": "2026-08-22T17:58:00+00:00"},
    {"InstanceId": "i-12345678", "PingStatus": "Online", "LastPingDateTime": "2026-08-22T17:58:00+00:00"},
]])
def test_preflight_rejects_ambiguous_ssm_results(entries):
    responses = iter([
        {"Reservations": [{"Instances": [{"InstanceId": VALID_ENV["EC2_INSTANCE_ID"], "State": {"Name": "running"}}]}]},
        {"InstanceInformationList": entries},
    ])

    with pytest.raises(PreflightError, match="exactly one SSM"):
        preflight(
            DeployConfig.from_environ(VALID_ENV),
            lambda args: next(responses),
            lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
        )


def test_preflight_rejects_ambiguous_ec2_results():
    aws = lambda args: {"Reservations": [{"Instances": []}]}

    with pytest.raises(PreflightError, match="exactly one EC2"):
        preflight(DeployConfig.from_environ(VALID_ENV), aws, lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc))


def test_preflight_propagates_aws_cli_errors():
    def aws(args):
        raise AwsCliError("aws cli failed")

    with pytest.raises(AwsCliError, match="aws cli failed"):
        preflight(DeployConfig.from_environ(VALID_ENV), aws, lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc))


def test_preflight_rejects_future_heartbeat_beyond_one_minute():
    responses = iter([
        {"Reservations": [{"Instances": [{"InstanceId": VALID_ENV["EC2_INSTANCE_ID"], "State": {"Name": "running"}}]}]},
        {"InstanceInformationList": [{
            "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
            "PingStatus": "Online",
            "LastPingDateTime": "2026-08-22T18:01:01+00:00",
        }]},
    ])

    with pytest.raises(PreflightError, match="future"):
        preflight(
            DeployConfig.from_environ(VALID_ENV),
            lambda args: next(responses),
            lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
        )


def test_preflight_samples_utc_immediately_after_ssm_describe_response():
    events = []

    def aws(args):
        if args[:2] == ["ec2", "describe-instances"]:
            events.append("ec2-response")
            return {"Reservations": [{"Instances": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "State": {"Name": "running"},
            }]}]}
        events.append("ssm-response")
        return {"InstanceInformationList": [{
            "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
            "PingStatus": "Online",
            "LastPingDateTime": "2026-08-22T17:58:00+00:00",
        }]}

    def utc_now():
        events.append("utc-now")
        return datetime(2026, 8, 22, 18, 7, tzinfo=timezone.utc)

    with pytest.raises(PreflightError, match="stale"):
        preflight(DeployConfig.from_environ(VALID_ENV), aws, utc_now)

    assert events == ["ec2-response", "ssm-response", "utc-now"]


@pytest.mark.parametrize("state", [None, [], "running", {"Name": ["running"]}])
def test_preflight_rejects_malformed_ec2_state_with_typed_error(state):
    def aws(args):
        return {"Reservations": [{"Instances": [{
            "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
            "State": state,
        }]}]}

    with pytest.raises(PreflightError, match="state is malformed"):
        preflight(
            DeployConfig.from_environ(VALID_ENV),
            aws,
            lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
        )


def test_send_payload_separates_delivery_and_execution_timeout():
    calls = []

    def aws(args):
        calls.append(args)
        if args[:2] == ["ssm", "describe-instance-information"]:
            return managed_instance_response(NOW)
        return {"Command": {"CommandId": "11111111-1111-1111-1111-111111111111"}}

    command_id = send_command(
        DeployConfig.from_environ(VALID_ENV), b"echo safe", aws,
        utc_now=lambda: NOW,
    )

    assert command_id == "11111111-1111-1111-1111-111111111111"
    assert [call[:2] for call in calls] == [
        ["ssm", "describe-instance-information"],
        ["ssm", "send-command"],
    ]
    args = calls[1]
    assert args[args.index("--timeout-seconds") + 1] == str(DELIVERY_TIMEOUT_SECONDS) == "60"
    parameters = json.loads(args[args.index("--parameters") + 1])
    assert parameters["executionTimeout"] == [str(EXECUTION_TIMEOUT_SECONDS)] == ["1800"]
    assert args[:2] == ["ssm", "send-command"]
    assert args[args.index("--instance-ids") + 1] == VALID_ENV["EC2_INSTANCE_ID"]


def _foreign_managed_instance(last_ping):
    """One online, perfectly fresh record -- belonging to a different host."""
    return {"InstanceInformationList": [{
        "InstanceId": "i-0000000000decafbad",
        "PingStatus": "Online",
        "LastPingDateTime": last_ping.isoformat(),
    }]}


def _running_ec2_response():
    return {"Reservations": [{"Instances": [{
        "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
        "State": {"Name": "running"},
    }]}]}


def test_send_boundary_describe_asks_only_about_the_configured_deploy_target():
    # A freshness proof is worthless if it is a proof about some other record.
    # The request itself must name this instance, in this region -- every other
    # fixture stubs `aws` as a lambda that ignores its arguments, which leaves
    # the request structurally invisible to the suite.
    config = DeployConfig.from_environ(VALID_ENV)
    calls = []

    def aws(args):
        calls.append(args)
        return managed_instance_response(NOW)

    require_fresh_ssm_target(config, aws, lambda: NOW)

    assert len(calls) == 1
    args = calls[0]
    assert args[:2] == ["ssm", "describe-instance-information"]
    assert args[args.index("--filters") + 1] == (
        f"Key=InstanceIds,Values={VALID_ENV['EC2_INSTANCE_ID']}"
    )
    assert args[args.index("--region") + 1] == VALID_ENV["AWS_REGION"]


def test_preflight_describe_asks_only_about_the_configured_deploy_target():
    config = DeployConfig.from_environ(VALID_ENV)
    calls = []

    def aws(args):
        calls.append(args)
        if args[:2] == ["ec2", "describe-instances"]:
            return _running_ec2_response()
        return managed_instance_response(NOW)

    preflight(config, aws, lambda: NOW)

    describes = [
        args for args in calls
        if args[:2] == ["ssm", "describe-instance-information"]
    ]
    assert len(describes) == 1
    assert describes[0][describes[0].index("--filters") + 1] == (
        f"Key=InstanceIds,Values={VALID_ENV['EC2_INSTANCE_ID']}"
    )
    assert describes[0][describes[0].index("--region") + 1] == (
        VALID_ENV["AWS_REGION"]
    )


def test_send_boundary_rejects_a_fresh_heartbeat_from_another_instance():
    # Exactly one entry, online, zero seconds old -- every check except identity
    # passes. Dropping the identity comparison would turn the proof into "some
    # SSM record somewhere pinged recently".
    config = DeployConfig.from_environ(VALID_ENV)

    with pytest.raises(PreflightError, match="does not match configured"):
        require_fresh_ssm_target(
            config, lambda args: _foreign_managed_instance(NOW), lambda: NOW,
        )


def test_preflight_rejects_a_fresh_heartbeat_from_another_instance():
    config = DeployConfig.from_environ(VALID_ENV)

    def aws(args):
        if args[:2] == ["ec2", "describe-instances"]:
            return _running_ec2_response()
        return _foreign_managed_instance(NOW)

    with pytest.raises(PreflightError, match="does not match configured"):
        preflight(config, aws, lambda: NOW)


def test_final_ssm_target_check_refuses_a_pre_sampled_clock():
    # The send-boundary function is exported and callable on its own; its own
    # guard must hold rather than leaning on send_command's redundant one.
    config = DeployConfig.from_environ(VALID_ENV)
    calls = []

    def aws(args):
        calls.append(args)
        raise AssertionError("the clock is checked before any AWS call")

    with pytest.raises(ConfigError, match="callable"):
        require_fresh_ssm_target(config, aws, NOW)

    assert calls == []


def test_final_ssm_target_check_refuses_a_clock_without_a_timezone():
    # `datetime.now()` instead of `datetime.now(timezone.utc)` would otherwise
    # make the age subtraction raise TypeError, which main does not handle and
    # which would surface as an untyped traceback instead of a typed failure.
    config = DeployConfig.from_environ(VALID_ENV)

    with pytest.raises(PreflightError, match="timezone"):
        require_fresh_ssm_target(
            config,
            lambda args: managed_instance_response(NOW),
            lambda: NOW.replace(tzinfo=None),
        )


def test_send_describes_then_sends_and_accepts_the_ceiling_exactly():
    # The previous fixture drew from a two-element clock while asserting that
    # exactly one describe happens, so its second element was unreachable and
    # the "61 seconds" it advertised was never exercised. What this test does
    # pin is the boundary case: a heartbeat exactly at the ceiling still sends.
    config = DeployConfig.from_environ(VALID_ENV)
    calls = []

    def aws(args):
        calls.append(args)
        if args[:2] == ["ssm", "describe-instance-information"]:
            return managed_instance_response(
                NOW - timedelta(seconds=SSM_HEARTBEAT_MAX_AGE_SECONDS)
            )
        if args[:2] == ["ssm", "send-command"]:
            return {"Command": {"CommandId": COMMAND_ID}}
        raise AssertionError(args)

    assert send_command(
        config, HOST_SCRIPT, aws, AUTHORITY_TOKEN, utc_now=lambda: NOW,
    ) == COMMAND_ID
    assert [call[1] for call in calls] == [
        "describe-instance-information", "send-command",
    ]


def test_send_boundary_propagates_a_failed_describe_instead_of_sending():
    # Preflight has had this test since the beginning; the send boundary did
    # not, even though `run_deploy` deliberately swallows `AwsCliError` in two
    # other places. Wrapping the recheck in the same `except AwsCliError: pass`
    # would submit a command with no freshness proof at all, and every test
    # still passed.
    config = DeployConfig.from_environ(VALID_ENV)
    sent = []

    def aws(args):
        if args[:2] == ["ssm", "describe-instance-information"]:
            raise AwsCliError("ThrottlingException")
        sent.append(args)
        raise AssertionError("SendCommand reached after a failed freshness proof")

    with pytest.raises(AwsCliError, match="ThrottlingException"):
        send_command(
            config, HOST_SCRIPT, aws, AUTHORITY_TOKEN, utc_now=lambda: NOW,
        )

    assert sent == []


def test_final_ssm_target_check_propagates_a_failed_describe():
    config = DeployConfig.from_environ(VALID_ENV)

    def aws(args):
        raise AwsCliError("aws cli failed")

    with pytest.raises(AwsCliError, match="aws cli failed"):
        require_fresh_ssm_target(config, aws, lambda: NOW)


def test_send_performs_no_forbidden_work_after_final_freshness(monkeypatch):
    config = DeployConfig.from_environ(VALID_ENV)
    final_freshness_seen = False
    events = []

    original_build_remote_command = deploy_control.build_remote_command
    original_json_dumps = deploy_control.json.dumps
    original_read_bytes = Path.read_bytes

    def reject_after_freshness(name, operation):
        def guarded(*args, **kwargs):
            assert not final_freshness_seen, (
                f"forbidden {name} after final SSM freshness response"
            )
            events.append(name)
            return operation(*args, **kwargs)
        return guarded

    monkeypatch.setattr(
        deploy_control,
        "build_remote_command",
        reject_after_freshness("remote-command construction", original_build_remote_command),
    )
    monkeypatch.setattr(
        deploy_control.json,
        "dumps",
        reject_after_freshness("JSON construction", original_json_dumps),
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        reject_after_freshness("file read", original_read_bytes),
    )
    monkeypatch.setattr(
        deploy_control.subprocess,
        "run",
        reject_after_freshness("subprocess work", deploy_control.subprocess.run),
    )
    # A retry backoff, a budget re-check, or any other wait placed between the
    # freshness response and SendCommand spends heartbeat age the proof already
    # accounted for. Four monkeypatched operations did not see any of them.
    monkeypatch.setattr(
        deploy_control.time,
        "sleep",
        reject_after_freshness("sleep", deploy_control.time.sleep),
    )
    monkeypatch.setattr(
        deploy_control.time,
        "monotonic",
        reject_after_freshness("budget re-check", deploy_control.time.monotonic),
    )

    def aws(args):
        nonlocal final_freshness_seen
        if args[:2] == ["ssm", "describe-instance-information"]:
            final_freshness_seen = True
            events.append("freshness response")
            return managed_instance_response(NOW)
        if args[:2] == ["ssm", "send-command"]:
            events.append("send-command")
            return {"Command": {"CommandId": COMMAND_ID}}
        raise AssertionError(args)

    assert send_command(
        config, HOST_SCRIPT, aws, AUTHORITY_TOKEN, utc_now=lambda: NOW,
    ) == COMMAND_ID
    # The invariant is an ordering, not a script. What the controller does
    # *before* proving freshness is its own business and may be reordered or
    # added to; what it does after is nothing at all.
    boundary = events.index("freshness response")
    assert events[boundary + 1:] == ["send-command"]

    # And the payload really is built beforehand -- otherwise the guard would
    # hold vacuously on a controller that had stopped building one.
    assert "remote-command construction" in events[:boundary]


def test_direct_script_entrypoint_imports_contract_without_package_path():
    completed = subprocess.run(
        [sys.executable, "scripts/deploy_control.py"],
        cwd=Path(__file__).resolve().parents[1],
        env={},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "ModuleNotFoundError" not in completed.stderr
    assert "DEPLOY_SHA must be lowercase 40-hex" in completed.stdout


def test_send_samples_the_utc_clock_after_its_own_freshness_response():
    # The send boundary must read the clock from the same instant as its own
    # describe response -- not from any earlier point in the lifecycle.
    config = DeployConfig.from_environ(VALID_ENV)
    events = []

    def clock():
        events.append("utc-now")
        return NOW

    def aws(args):
        if args[:2] == ["ssm", "describe-instance-information"]:
            events.append("ssm-response")
            return managed_instance_response(NOW)
        if args[:2] == ["ssm", "send-command"]:
            events.append("send-command")
            return {"Command": {"CommandId": COMMAND_ID}}
        raise AssertionError(args)

    assert send_command(
        config, HOST_SCRIPT, aws, AUTHORITY_TOKEN, utc_now=clock,
    ) == COMMAND_ID
    assert events == ["ssm-response", "utc-now", "send-command"]


def test_send_boundary_refuses_a_pre_sampled_clock():
    # A datetime is a clock frozen at some earlier instant. Accepting one would
    # silently restore the retired early-clock contract, so the send authority
    # boundary must reject it before any AWS call.
    config = DeployConfig.from_environ(VALID_ENV)
    calls = []

    def aws(args):
        calls.append(args)
        raise AssertionError("no AWS call may precede the clock contract check")

    with pytest.raises(ConfigError, match="callable"):
        send_command(config, HOST_SCRIPT, aws, AUTHORITY_TOKEN, utc_now=NOW)
    assert calls == []


def test_preflight_refuses_a_pre_sampled_clock():
    config = DeployConfig.from_environ(VALID_ENV)

    def aws(args):
        raise AssertionError("no AWS call may precede the clock contract check")

    with pytest.raises(ConfigError, match="callable"):
        preflight(config, aws, NOW)


def test_run_deploy_refuses_a_pre_sampled_clock(workspace_tmp_dir, fake_clock):
    # Kills the "sample once in run_deploy and hand the value down" refactor.
    def aws(args):
        raise AssertionError("no AWS call may precede the clock contract check")

    with pytest.raises(ConfigError, match="callable"):
        run_deploy(
            VALID_ENV, workspace_tmp_dir, aws, NOW,
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
            run_git=lambda *args, **kwargs: None,
        )


class _AdvancingDatetime(datetime):
    """A `datetime` whose `now()` strictly advances on every read."""

    _reads = 0

    @classmethod
    def now(cls, tz=None):
        cls._reads += 1
        return NOW.astimezone(tz or timezone.utc) + timedelta(seconds=cls._reads)


def test_main_default_send_time_clock_is_live_not_frozen_at_startup(monkeypatch):
    # Kills freezing main's default clock -- both by dropping the `lambda:` and
    # by capturing a single sample in a default argument.
    monkeypatch.setattr(deploy_control, "datetime", _AdvancingDatetime)
    captured = {}

    def fake_run_deploy(environ, repo_path, aws, clock, *args, **kwargs):
        captured["clock"] = clock

    monkeypatch.setattr(deploy_control, "run_deploy", fake_run_deploy)

    assert main(
        environ=VALID_ENV, repo_path=Path("checked-out-repository"),
    ) == 0

    clock = captured["clock"]
    assert callable(clock)
    first = clock()
    assert isinstance(first, datetime) and first.tzinfo is not None
    # A clock frozen at start-up returns the same instant forever, and `x >= x`
    # would hide it, so the stub advances on every read and the check is strict.
    assert clock() > first


def test_main_refuses_a_pre_sampled_clock_override():
    # A bare timestamp handed to the entrypoint used to be wrapped in
    # `lambda: now`, producing a callable that satisfies every downstream
    # `callable()` guard forever. An hour-stale heartbeat then passed the send
    # boundary. The entrypoint must reject it, before any AWS work.
    calls = []
    messages = []

    def aws(args):
        calls.append(args)
        raise AssertionError("no AWS work may begin behind a frozen clock")

    assert main(
        environ=VALID_ENV,
        repo_path=Path("checked-out-repository"),
        aws=aws,
        utc_now=NOW,
        log=messages.append,
        run_git=fake_git_returning(VALID_ENV["DEPLOY_SHA"]),
    ) == 1

    assert calls == []
    assert messages == ["deployment failed: UTC clock must be callable so "
                        "freshness is sampled at the boundary"]


# The entrypoint's injection seams, whitelisted. Adding one is a deliberate act
# that has to be made here, next to the reason the list exists.
CONTROLLER_SOURCE = Path(deploy_control.__file__)


def _controller_function(name):
    module = ast.parse(CONTROLLER_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(module):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"{name} is not defined in the controller")


APPROVED_MAIN_PARAMETERS = [
    "environ", "repo_path", "aws", "utc_now", "monotonic", "sleep", "log",
    "run_git",
]


def test_main_exposes_no_parameter_that_can_freeze_the_lifecycle_clock():
    # Round 4 asserted that no parameter annotation mentions `datetime`, which a
    # reviewer defeated in one line with `at: object | None = None` and
    # `lambda: at` -- the retired laundering path under a new spelling. Names and
    # annotations are the wrong thing to inspect. Whitelist the seams instead.
    # The invariant is which seams exist, not the order they are declared
    # in: they are all keyword-only, so reordering them changes no call site.
    assert sorted(inspect.signature(main).parameters) == sorted(
        APPROVED_MAIN_PARAMETERS
    )


def test_the_send_time_clock_is_built_from_nothing_but_a_live_source():
    # ... and pin what the clock is actually assembled from, so a whitelisted
    # parameter cannot be re-purposed into a frozen instant either.
    entrypoint = _controller_function("main")

    # Every binding, not only the annotated one: a later `send_time_clock =
    # _pinned_clock` under an `if` rebinds the seam after the pinned
    # construction and leaves that construction perfectly intact.
    assignments = [
        node for node in ast.walk(entrypoint)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "send_time_clock"
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
    ]
    assert len(assignments) == 1
    assert isinstance(assignments[0], ast.AnnAssign)

    referenced = {
        node.id
        for node in ast.walk(assignments[0].value)
        if isinstance(node, ast.Name)
    }
    assert referenced == {"utc_now", "datetime", "timezone"}


def _callable_with_no_arguments(arguments, *, bound_self=False):
    """True when the callable can be invoked as `f()`.

    Not the same question as "does it declare parameters". A defaulted
    parameter is still a zero-argument call, and `def _pinned(instant=_FROZEN):
    return instant` is a frozen clock that the earlier, stricter test could not
    see. `*args`/`**kwargs` bind to nothing and never make `f()` illegal, so
    they are not required arguments either.
    """
    positional = arguments.posonlyargs + arguments.args
    if bound_self and positional:
        positional = positional[1:]  # the instance supplies it
    if len(positional) > len(arguments.defaults):
        return False
    # `kw_defaults` is parallel to `kwonlyargs`, with None where there is no
    # default -- those are the keyword arguments the caller must supply.
    return all(default is not None for default in arguments.kw_defaults)


def test_the_zero_argument_census_counts_calls_not_declarations():
    # Positive control. The census assertion below is an exact-list match, so
    # a helper that answered "no" to everything would satisfy it silently.
    module = ast.parse(
        "def none(): pass\n"
        "def defaulted(instant=FROZEN): return instant\n"
        "def starred(*args, **kwargs): pass\n"
        "def required(instant): return instant\n"
        "def kwonly(*, instant): return instant\n"
        "def kwonly_defaulted(*, instant=FROZEN): return instant\n"
        "class C:\n"
        "    def __call__(self): return FROZEN\n"
        "    def method(self, instant): return instant\n"
    )
    callable_with_none = {
        node.name
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef)
        and _callable_with_no_arguments(
            node.args, bound_self=node.name in ("__call__", "method")
        )
    }
    assert callable_with_none == {
        "none", "defaulted", "starred", "kwonly_defaulted", "__call__",
    }


LATCHING_CLOCK_WRAPPERS = ("partial", "lru_cache", "cache")


def _latching_clock_callee_names(module):
    """Local names that wrap a callable into a latched clock.

    Round 11 resolved `from functools import lru_cache as _memo` and the
    literal `functools.lru_cache`. `import functools as _ft` was still
    invisible: `ast.unparse` of the decorator is `_ft.lru_cache`.
    """
    names = {f"functools.{name}" for name in LATCHING_CLOCK_WRAPPERS} | set(
        LATCHING_CLOCK_WRAPPERS
    )
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom) and node.module == "functools":
            names |= {
                alias.asname or alias.name
                for alias in node.names if alias.name in LATCHING_CLOCK_WRAPPERS
            }
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] != "functools":
                    continue
                local = alias.asname or "functools"
                names |= {
                    f"{local}.{name}" for name in LATCHING_CLOCK_WRAPPERS
                }
                parts = alias.name.split(".")
                if (
                    len(parts) > 1
                    and parts[-1] in LATCHING_CLOCK_WRAPPERS
                    and alias.asname
                ):
                    names.add(alias.asname)
    return names


def _latching_wrapper_expressions(module):
    """Latching wrappers used in this module, as they appear in the tree.

    Round 12 scored only `ast.Call` (`@_ft.lru_cache(maxsize=1)`, `@latch()`).
    `@latch` and `@_ft.cache` are Name / Attribute, not Call.
    """
    names = _latching_clock_callee_names(module)
    found = []
    for node in ast.walk(module):
        if isinstance(node, ast.Call) and ast.unparse(node.func) in names:
            found.append(ast.unparse(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    continue
                if ast.unparse(decorator) in names:
                    found.append(ast.unparse(decorator))
    return found


def test_the_frozen_clock_census_resolves_functools_aliases():
    module = ast.parse(
        "import functools as _ft\n"
        "@_ft.lru_cache(maxsize=1)\n"
        "def _latched(tz):\n"
        "    return tz\n"
        "from functools import cache as _memo\n"
        "@_memo()\n"
        "def also(tz):\n"
        "    return tz\n"
    )
    names = _latching_clock_callee_names(module)
    decorator = module.body[1].decorator_list[0]
    assert ast.unparse(decorator.func) in names
    assert "_memo" in names

    bare = ast.parse(
        "from functools import cache as latch\n"
        "@latch\n"
        "def _latched(tz):\n"
        "    return tz\n"
        "import functools as _ft\n"
        "@_ft.cache\n"
        "def also(tz):\n"
        "    return tz\n"
    )
    found = _latching_wrapper_expressions(bare)
    assert "latch" in found
    assert "_ft.cache" in found


def test_the_controller_injects_no_frozen_clock_anywhere():
    # A zero-argument callable in this module is a clock by construction.
    # Exactly one is legitimate: the live default. Any other is a sample taken
    # at some earlier instant and dressed up as a clock -- which is the one
    # thing `_require_live_clock` provably cannot detect at run time, because a
    # frozen clock and a live one have identical shape.
    #
    # `def` as well as `lambda`: a nested zero-argument function is the same
    # object with a different keyword in front of it, and censusing only
    # lambdas made `def _pinned(): return datetime.fromisoformat(...)` free.
    # `__call__` and `functools.partial` for the same reason -- both produce an
    # object that answers to `f()` without any `def` in the module declaring a
    # zero-argument signature.
    module = ast.parse(CONTROLLER_SOURCE.read_text(encoding="utf-8"))

    # Names bound to `functools.partial`, however it was imported. The check
    # used to be the literal substring "partial" in the unparsed callee, and
    # `from functools import partial as _bind` walked straight through it.
    # `lru_cache` and `cache` latch lazily on the first call, which is a frozen
    # clock that arrives one read later than `partial`'s.
    clock_shaped = []
    for node in ast.walk(module):
        if isinstance(node, ast.Lambda):
            if _callable_with_no_arguments(node.args):
                clock_shaped.append(ast.unparse(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _callable_with_no_arguments(
                node.args, bound_self=node.name in ("__call__", "__new__")
            ):
                # Scored with their class below, where the callable really is.
                if node.name not in ("__call__", "__new__"):
                    clock_shaped.append(node.name)
    clock_shaped.extend(_latching_wrapper_expressions(module))

    # A `ClassDef` is neither a `Lambda`, a `FunctionDef` nor a `Call`, so the
    # census could not see one at all -- and `class _LatchedClock` with a
    # `__new__` that samples once and returns that instant forever is a clock
    # by any definition. Only the two shapes that can actually BE a clock are
    # scored: `__call__` (the instance is the clock) and `__new__` (the class
    # is). A dataclass or an exception defines neither, and `ConfigError()`
    # takes no arguments but returns an exception -- flagging it would report a
    # frozen clock where the census had found an error type.
    for definition in ast.walk(module):
        if not isinstance(definition, ast.ClassDef):
            continue
        for child in definition.body:
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name in ("__call__", "__new__")
                and _callable_with_no_arguments(child.args, bound_self=True)
            ):
                clock_shaped.append(f"{definition.name}.{child.name}")

    # `main` is the entrypoint, not a clock: its parameters are all defaulted so
    # `python -m` can call it, and which parameters it may have is pinned
    # separately by the signature whitelist. Everything else in this module that
    # answers to `f()` is a clock, and exactly one of those is legitimate.
    assert sorted(clock_shaped) == [
        "lambda: datetime.now(timezone.utc)",
        "main",
    ]


# Every exported boundary that takes the send-time clock. A default here is a
# clock nobody passes and nobody reviews.
CLOCK_CARRYING_BOUNDARIES = (
    "preflight", "require_fresh_ssm_target", "send_command", "run_deploy",
)


def _declared_default(arguments, name):
    """The default expression for `name`, or None if it is required."""
    positional = arguments.posonlyargs + arguments.args
    first_defaulted = len(positional) - len(arguments.defaults)
    for index, parameter in enumerate(positional):
        if parameter.arg == name:
            if index < first_defaulted:
                return None
            return arguments.defaults[index - first_defaulted]
    for index, parameter in enumerate(arguments.kwonlyargs):
        if parameter.arg == name:
            return arguments.kw_defaults[index]
    return None


def test_no_boundary_supplies_its_own_clock():
    # The census next door counts zero-argument callables in this module. It
    # does not stop one being installed as the DEFAULT of a boundary the census
    # has no opinion about -- which is how a latched clock reached
    # `require_fresh_ssm_target`, the function whose own test says it "is
    # exported and callable on its own". The clock is always the caller's.
    module = ast.parse(CONTROLLER_SOURCE.read_text(encoding="utf-8"))

    definitions = {
        node.name: node for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in CLOCK_CARRYING_BOUNDARIES:
        function = definitions[name]
        parameters = [
            parameter.arg for parameter in
            function.args.posonlyargs + function.args.args
            + function.args.kwonlyargs
        ]
        assert "utc_now" in parameters, name
        assert _declared_default(function.args, "utc_now") is None, name


def test_the_default_reader_finds_defaults_in_both_positions():
    # Positive control: the assertion above is negative in both directions.
    module = ast.parse(
        "def positional(a, b=1, utc_now=FROZEN): pass\n"
        "def required(a, utc_now): pass\n"
        "def kwonly(a, *, utc_now=FROZEN): pass\n"
        "def kwonly_required(a, *, utc_now): pass\n"
    )
    defaults = {
        node.name: _declared_default(node.args, "utc_now")
        for node in module.body
    }
    assert defaults["required"] is None
    assert defaults["kwonly_required"] is None
    assert ast.unparse(defaults["positional"]) == "FROZEN"
    assert ast.unparse(defaults["kwonly"]) == "FROZEN"


def test_send_rejects_heartbeat_that_became_stale_after_preflight():
    config = DeployConfig.from_environ(VALID_ENV)
    aws = fresh_then_stale_ssm_runner(age_seconds=361)

    preflight(config, aws, lambda: NOW)
    with pytest.raises(PreflightError, match="stale"):
        send_command(config, HOST_SCRIPT, aws, AUTHORITY_TOKEN, utc_now=lambda: NOW)

    assert not aws.sent()


def test_canonical_host_budget_preserves_literal_220_second_margin():
    assert list(HOST_PHASE_SECONDS.values()) == [
        10, 60, 80, 10, 70, 620, 160, 30, 440, 80, 20,
    ]
    assert HOST_WORST_CASE_SECONDS == 1580
    assert SSM_EXECUTION_MARGIN_SECONDS == 220
    assert SSM_EXECUTION_TIMEOUT_SECONDS - HOST_WORST_CASE_SECONDS == SSM_EXECUTION_MARGIN_SECONDS
    assert CONTROLLER_REQUIRED_SECONDS == 2490 < 46 * 60


@pytest.mark.parametrize("ping_status", ["ConnectionLost", "Inactive"])
def test_final_ssm_target_check_rejects_non_online_status(ping_status):
    with pytest.raises(PreflightError, match="not online"):
        require_fresh_ssm_target(
            DeployConfig.from_environ(VALID_ENV),
            lambda args: managed_instance_response(NOW, ping_status=ping_status),
            lambda: NOW,
        )


@pytest.mark.parametrize("age_seconds,expected_error", [
    (360, None),
    (361, "stale"),
    (-61, "future"),
])
def test_final_ssm_target_check_enforces_heartbeat_age_boundaries(
        age_seconds, expected_error):
    config = DeployConfig.from_environ(VALID_ENV)
    aws = lambda args: managed_instance_response(NOW - timedelta(seconds=age_seconds))

    if expected_error is None:
        assert require_fresh_ssm_target(config, aws, lambda: NOW).instance_id == config.instance_id
    else:
        with pytest.raises(PreflightError, match=expected_error):
            require_fresh_ssm_target(config, aws, lambda: NOW)


@pytest.mark.parametrize("future_seconds, expected", [(60, None), (61, "future")])
def test_final_ssm_target_check_enforces_future_skew_boundaries(
        future_seconds, expected):
    config = DeployConfig.from_environ(VALID_ENV)

    def aws(args):
        return managed_instance_response(NOW + timedelta(seconds=future_seconds))

    if expected is None:
        assert require_fresh_ssm_target(
            config, aws, lambda: NOW
        ).instance_id == config.instance_id
    else:
        with pytest.raises(PreflightError, match=expected):
            require_fresh_ssm_target(config, aws, lambda: NOW)


@pytest.mark.parametrize("heartbeat", ["not-a-date", "2026-08-22T18:00:00"])
def test_final_ssm_target_check_rejects_malformed_heartbeat(heartbeat):
    with pytest.raises(PreflightError, match="heartbeat"):
        require_fresh_ssm_target(
            DeployConfig.from_environ(VALID_ENV),
            lambda args: {"InstanceInformationList": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "PingStatus": "Online",
                "LastPingDateTime": heartbeat,
            }]},
            lambda: NOW,
        )


PLUS_FIVE = timezone(timedelta(hours=5))


@pytest.mark.parametrize("age_seconds,expected_error", [
    (0, None),
    (360, None),
    (361, "stale"),
    (18000, "stale"),
])
def test_the_boundary_judges_an_offset_heartbeat_by_its_instant(
        age_seconds, expected_error):
    # SSM reports whatever offset the agent's host is in. Reinterpreting that
    # stamp as UTC -- `heartbeat.replace(tzinfo=timezone.utc)` -- keeps the
    # digits and moves the instant, so at +05:00 a five-hour-old heartbeat
    # reads as brand new. Note the 18000-second case: expressed in +05:00 its
    # wall clock is character-for-character the sampled clock's own.
    config = DeployConfig.from_environ(VALID_ENV)
    heartbeat = (NOW - timedelta(seconds=age_seconds)).astimezone(PLUS_FIVE)
    aws = lambda args: managed_instance_response(heartbeat)

    if expected_error is None:
        instance = require_fresh_ssm_target(config, aws, lambda: NOW)
        assert instance.last_ping == NOW - timedelta(seconds=age_seconds)
    else:
        with pytest.raises(PreflightError, match=expected_error):
            require_fresh_ssm_target(config, aws, lambda: NOW)


@pytest.mark.parametrize("age_seconds,expected_error", [
    (360, None),
    (361, "stale"),
])
def test_the_boundary_judges_an_offset_clock_by_its_instant(
        age_seconds, expected_error):
    # And symmetrically for the sampled clock. `datetime.now(timezone.utc)` is
    # the live default, but nothing in the signature demands UTC, and a clock
    # returning the same instant in another offset must reach the same verdict.
    config = DeployConfig.from_environ(VALID_ENV)
    aws = lambda args: managed_instance_response(
        NOW - timedelta(seconds=age_seconds)
    )
    clock = lambda: NOW.astimezone(timezone(timedelta(hours=-3)))

    if expected_error is None:
        assert require_fresh_ssm_target(
            config, aws, clock
        ).instance_id == config.instance_id
    else:
        with pytest.raises(PreflightError, match=expected_error):
            require_fresh_ssm_target(config, aws, clock)


def test_aws_expiry_is_derived_from_delivery_and_execution_timeouts():
    assert AWS_EXPIRY_SECONDS == (
        DELIVERY_TIMEOUT_SECONDS + EXECUTION_TIMEOUT_SECONDS
    ) == 1860
    assert AWS_EXPIRY_SECONDS < POLL_HORIZON_SECONDS


def test_generated_command_stays_below_local_growth_guard():
    command = build_remote_command(
        DeployConfig.from_environ(VALID_ENV), b"#!/bin/sh\nexit 0\n",
    )
    assert len(command) < deploy_control.LOCAL_RUN_SHELL_COMMAND_MAX_CHARS


def test_root_bootstrap_and_workflow_helper_fit_strictly_inside_ssm_budget():
    assert deploy_control.ROOT_EXTERNAL_CALL_COUNT == 16
    command = build_remote_command(
        DeployConfig.from_environ(VALID_ENV), b"exit 0\n",
    )
    assert (
        "timeout --signal=TERM --kill-after=1s 4s" in command
        or "timeout --signal=TERM --kill-after=1s 4s"
        in _decode_inner_bootstrap(command)
    )
    assert (
        deploy_control.AUTHORITY_GATE_WORST_CASE_SECONDS
        + deploy_control.ROOT_BOOTSTRAP_WORST_CASE_SECONDS
        + deploy_control.WORKFLOW_HELPER_WORST_CASE_SECONDS
        < EXECUTION_TIMEOUT_SECONDS
    )


def test_send_command_rejects_oversized_local_payload_before_aws():
    calls = []
    oversized = b"x" * deploy_control.LOCAL_RUN_SHELL_COMMAND_MAX_CHARS
    with pytest.raises(ConfigError, match="local .*growth guard"):
        send_command(
            DeployConfig.from_environ(VALID_ENV), oversized,
            lambda args: calls.append(args),
            utc_now=lambda: NOW,
        )
    assert calls == []


def test_remote_command_encodes_script_and_positional_values():
    script = b"#!/bin/sh\nprintf '%s\\n' \"$1\""
    deploy_dir = "/srv/axisai"
    public_url = "https://fitness.example/health?probe='$(touch%20nope)"
    config = DeployConfig.from_environ({
        **VALID_ENV,
        "DEPLOY_DIR": deploy_dir,
        "PUBLIC_HEALTH_URL": public_url,
    })

    command = build_remote_command(config, script)
    command_lines = command.splitlines()
    root_wrapper_line = next(
        line for line in command_lines if line.startswith("python3 - ")
    )
    arguments = shlex.split(root_wrapper_line.split(" <<", 1)[0])
    assert arguments[:2] == ["python3", "-"]
    wrapper_start = command_lines.index(root_wrapper_line) + 1
    assert "\n".join(command_lines[wrapper_start:-1]) + "\n" == deploy_control.ROOT_LOCK_WRAPPER_SOURCE
    assert command_lines[-1] == "AXISAI_ROOT_LOCK_PY"
    inner_bootstrap = base64.b64decode(arguments[2], validate=True).decode("utf-8")

    assert script.decode() not in command
    assert deploy_dir not in command
    assert public_url not in command
    assert base64.b64encode(script).decode("ascii") in inner_bootstrap
    assert base64.b64encode(deploy_dir.encode()).decode("ascii") in inner_bootstrap
    assert base64.b64encode(public_url.encode()).decode("ascii") in inner_bootstrap


def _write_shell_stub(directory, name, body):
    path = directory / name
    path.write_text(f"#!/bin/sh\nset -eu\n{body}", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _read_null_arguments(path):
    payload = path.read_bytes()
    assert payload.endswith(b"\0")
    return payload[:-1].decode("utf-8").split("\0")


def _locate_git_bash():
    """Locate the Git-for-Windows bash binary across install layouts.

    `git` resolves to `<root>/cmd/git.exe`, `<root>/bin/git.exe`, or
    `<root>/mingw64/bin/git.exe` depending on the installer and PATH, but
    `bash.exe` only ever lives in `<root>/bin`.  This is intentionally
    Windows-only: the harness stubs are `bash.exe` shims.
    """
    candidates = []
    git = shutil.which("git")
    if git is not None:
        parents = Path(git).resolve().parents
        candidates.extend(parent / "bin" / "bash.exe" for parent in parents[:3])
    shell = shutil.which("bash")
    if shell is not None and shell.lower().endswith(".exe"):
        candidates.append(Path(shell))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _git_bash_path(path):
    windows_path = path.resolve().as_posix()
    drive, remainder = windows_path.split(":", 1)
    return f"/{drive.lower()}{remainder}"


class FakeRootLockOs:
    O_RDONLY = 1
    O_RDWR = 2
    O_CREAT = 4
    O_DIRECTORY = 8
    O_NOFOLLOW = 16
    O_CLOEXEC = 32
    O_EXCL = 64

    def __init__(
        self,
        *,
        directory_uid=0,
        file_uid=0,
        existing_directory=True,
        symlink_directory=False,
        symlink_lock=False,
        existing_file=False,
        file_permissions=0o600,
    ):
        self.directory_uid = directory_uid
        self.file_uid = file_uid
        self.existing_directory = existing_directory
        self.symlink_directory = symlink_directory
        self.symlink_lock = symlink_lock
        self.existing_file = existing_file
        self.directory_mode = stat.S_IFDIR | 0o755
        self.file_mode = stat.S_IFREG | file_permissions
        self.open_calls = []
        self.mkdir_calls = []
        self.closed = []
        self.dup2_calls = []
        self.inheritable_calls = []
        self.environ = {"PATH": "/unsafe/caller/path"}

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        self.open_calls.append((path, flags, mode, dir_fd))
        if path == "/run/lock":
            return 10
        if path == "axisai-production":
            return 11
        if path == "production.lock":
            if self.symlink_lock:
                raise OSError("refusing symbolic link")
            if flags & self.O_EXCL and self.existing_file:
                raise FileExistsError(path)
            return 12
        raise AssertionError(path)

    def mkdir(self, path, mode=0o777, *, dir_fd=None):
        self.mkdir_calls.append((path, mode, dir_fd))
        if self.existing_directory:
            raise FileExistsError(path)
        self.existing_directory = True
        self.directory_mode = stat.S_IFDIR | 0o700

    def fstat(self, fd):
        if fd in (10, 11):
            uid = 0 if fd == 10 else self.directory_uid
            return SimpleNamespace(
                st_uid=uid,
                st_mode=self.directory_mode,
                st_nlink=2,
                st_dev=1,
                st_ino=fd,
            )
        if fd == 12:
            return SimpleNamespace(
                st_uid=self.file_uid,
                st_mode=self.file_mode,
                st_nlink=1,
                st_dev=1,
                st_ino=12,
            )
        raise AssertionError(fd)

    def stat(self, path, *, dir_fd=None, follow_symlinks=True):
        assert follow_symlinks is False
        if path == "axisai-production":
            assert dir_fd == 10
            mode = (
                stat.S_IFLNK | 0o777
                if self.symlink_directory
                else self.directory_mode
            )
            return SimpleNamespace(
                st_uid=self.directory_uid,
                st_mode=mode,
                st_nlink=2,
                st_dev=1,
                st_ino=11,
            )
        assert path == "production.lock"
        assert dir_fd == 11
        if self.symlink_lock:
            mode = stat.S_IFLNK | 0o777
        else:
            mode = self.file_mode
        return SimpleNamespace(
            st_uid=self.file_uid,
            st_mode=mode,
            st_nlink=1,
            st_dev=1,
            st_ino=12,
        )

    def fchmod(self, fd, mode):
        if fd == 11:
            self.directory_mode = stat.S_IFDIR | mode
        elif fd == 12:
            self.file_mode = stat.S_IFREG | mode
        else:
            raise AssertionError(fd)

    def close(self, fd):
        self.closed.append(fd)

    def dup2(self, source, destination, inheritable=False):
        self.dup2_calls.append((source, destination, inheritable))

    def set_inheritable(self, fd, inheritable):
        self.inheritable_calls.append((fd, inheritable))


class FakeRootLockFcntl:
    LOCK_EX = 1
    LOCK_NB = 2

    def __init__(self, *, contended=False):
        self.contended = contended
        self.calls = []

    def flock(self, fd, operation):
        self.calls.append((fd, operation))
        if self.contended:
            raise BlockingIOError


def _load_root_lock_runner():
    namespace = {"__name__": "root_lock_test"}
    exec(deploy_control.ROOT_LOCK_WRAPPER_SOURCE, namespace)
    return namespace["run"]


def test_root_lock_wrapper_provisions_root_owned_nonwritable_directory_and_readable_file():
    fake_os = FakeRootLockOs(existing_directory=False)
    fake_fcntl = FakeRootLockFcntl()
    child_calls = []
    fake_subprocess = SimpleNamespace(
        run=lambda args, **kwargs: child_calls.append((args, kwargs))
        or SimpleNamespace(returncode=23),
    )
    fake_time = SimpleNamespace(monotonic=lambda: 0, sleep=lambda _delay: None)

    status = _load_root_lock_runner()(
        base64.b64encode(b"printf safe").decode("ascii"),
        fake_os,
        stat,
        fake_fcntl,
        fake_subprocess,
        fake_time,
        [],
    )

    assert status == 23
    assert fake_os.directory_mode & 0o022 == 0
    assert fake_os.file_mode & 0o777 == 0o644
    assert fake_os.mkdir_calls == [("axisai-production", 0o755, 10)]
    assert fake_os.open_calls == [
        ("/run/lock", 1 | 8 | 16 | 32, 0o777, None),
        ("axisai-production", 1 | 8 | 16 | 32, 0o777, 10),
        ("production.lock", 2 | 4 | 16 | 32 | 64, 0o644, 11),
    ]
    assert child_calls == [([
        "/bin/sh", "-c", "printf safe",
    ], {
        "check": False,
        "pass_fds": (deploy_control.OUTER_LOCK_CAPABILITY_FD,),
        "env": {
            "PATH": deploy_control.HARDENED_EXECUTION_PATH,
            "AXISAI_ROOT_LOCK_FD": str(deploy_control.OUTER_LOCK_CAPABILITY_FD),
        },
    })]
    assert fake_os.dup2_calls == [(12, deploy_control.OUTER_LOCK_CAPABILITY_FD, True)]
    assert fake_fcntl.calls == [(12, 1 | 2)]
    assert fake_os.closed == [deploy_control.OUTER_LOCK_CAPABILITY_FD, 12, 11, 10]


def _load_privilege_drop_runner():
    namespace = {"__name__": "privilege_drop_test"}
    exec(deploy_control.PRIVILEGE_DROP_SOURCE, namespace)
    return namespace["run"]


def test_privilege_drop_validates_user_drops_groups_then_execs_exact_helper():
    events = []
    user = SimpleNamespace(pw_name="deploy", pw_uid=1000, pw_gid=1001)

    class FakeOs:
        environ = {"SECRET": "must-not-leak", "PATH": "/caller/path"}

        def initgroups(self, name, gid): events.append(("initgroups", name, gid))
        def setgid(self, gid): events.append(("setgid", gid))
        def setuid(self, uid): events.append(("setuid", uid))
        def getuid(self): return 1000
        def geteuid(self): return 1000
        def getgid(self): return 1001
        def getegid(self): return 1001
        def set_inheritable(self, fd, value): events.append(("inheritable", fd, value))
        def execve(self, path, argv, env): events.append(("execve", path, argv, env))

    status = _load_privilege_drop_runner()(
        "deploy", "/tmp/helper", "a" * 40, "/srv/axisai",
        "https://example.test/health", "7", FakeOs(),
        SimpleNamespace(getpwnam=lambda name: user),
        SimpleNamespace(alarm=lambda seconds: events.append(("alarm", seconds))), [],
    )

    assert status == 70  # A real execve never returns.
    assert events == [
        ("initgroups", "deploy", 1001),
        ("setgid", 1001),
        ("setuid", 1000),
        ("inheritable", 7, True),
        ("alarm", 0),
        ("execve", "/tmp/helper", [
            "/tmp/helper", "a" * 40, "/srv/axisai", "https://example.test/health",
            ], {
                "PATH": deploy_control.HARDENED_EXECUTION_PATH,
                "AXISAI_OUTER_LOCK_FD": "7",
                "AXISAI_MONOTONIC_STATE": deploy_control.MONOTONIC_STATE_PATH,
                **host_timeout_environment(),
            }),
    ]


@pytest.mark.parametrize("uid", [0, -1])
def test_privilege_drop_rejects_root_or_invalid_user_before_mutation(uid):
    events = []
    fake_os = SimpleNamespace(initgroups=lambda *args: events.append(args))
    errors = []
    status = _load_privilege_drop_runner()(
        "deploy", "/tmp/helper", "a" * 40, "/srv/axisai", "", "7",
        fake_os,
        SimpleNamespace(getpwnam=lambda name: SimpleNamespace(
            pw_name=name, pw_uid=uid, pw_gid=1000,
        )),
        SimpleNamespace(alarm=lambda seconds: events.append(("alarm", seconds))),
        errors,
    )
    assert status == 70
    assert events == []
    assert errors == ["configured deploy user is invalid"]


@pytest.mark.parametrize(
    ("os_kwargs", "expected_error"),
    [
        ({"symlink_lock": True}, "outer deployment lock is unavailable or unsafe"),
        ({"symlink_directory": True}, "outer deployment lock is unavailable or unsafe"),
        ({"directory_uid": 1000}, "outer deployment lock is unavailable or unsafe"),
        ({"file_uid": 1000}, "outer deployment lock is unavailable or unsafe"),
        (
            {"existing_file": True, "file_permissions": 0o666},
            "outer deployment lock is unavailable or unsafe",
        ),
    ],
)
def test_root_lock_wrapper_rejects_symlink_or_nonroot_ownership_before_child(
        os_kwargs, expected_error):
    fake_os = FakeRootLockOs(**os_kwargs)
    child_calls = []
    errors = []

    status = _load_root_lock_runner()(
        base64.b64encode(b"exit 0").decode("ascii"),
        fake_os,
        stat,
        FakeRootLockFcntl(),
        SimpleNamespace(
            run=lambda *args, **kwargs: child_calls.append(args)
            or SimpleNamespace(returncode=0),
        ),
        SimpleNamespace(monotonic=lambda: 0, sleep=lambda _delay: None),
        errors,
    )

    assert status == 73
    assert child_calls == []
    assert errors == [expected_error]


def test_root_lock_wrapper_contention_exits_73_before_child():
    child_calls = []
    errors = []
    readings = iter((100, 159, 160))
    sleeps = []
    fake_fcntl = FakeRootLockFcntl(contended=True)

    status = _load_root_lock_runner()(
        base64.b64encode(b"exit 0").decode("ascii"),
        FakeRootLockOs(),
        stat,
        fake_fcntl,
        SimpleNamespace(run=lambda *args, **kwargs: child_calls.append(args)),
        SimpleNamespace(monotonic=lambda: next(readings), sleep=sleeps.append),
        errors,
    )

    assert status == 73
    assert child_calls == []
    assert fake_fcntl.calls == [(12, 1 | 2), (12, 1 | 2)]
    assert sleeps == [0.25]
    assert errors == ["outer deployment lock unavailable after 60 seconds"]


@dataclass
class BootstrapHarness:
    bash: Path
    root: Path
    stubs: Path
    deploy_dir: Path
    remote_script: Path
    events: Path
    mutations: Path
    env_file: Path
    env_chmod_marker: Path
    nginx_site: Path
    nginx_state: Path
    git_refs: Path
    docker_trace: Path
    revision_marker: Path
    monotonic_state: Path
    config: DeployConfig
    script: bytes

    def trace_text(self) -> str:
        return self.mutations.read_text(encoding="utf-8")

    def mutation_trace(self) -> list[str]:
        return self.trace_text().splitlines()

    def event_trace(self) -> list[str]:
        return self.events.read_text(encoding="utf-8").splitlines()

    @staticmethod
    def _file_state(path: Path):
        try:
            status = path.stat()
        except FileNotFoundError:
            return None
        return (path.read_bytes(), stat.S_IMODE(status.st_mode), status.st_ino)

    def production_snapshot(self) -> dict:
        """Byte/mode/inode state of every production object the host owns."""
        return {
            "env": self._file_state(self.env_file),
            "env_chmod_marker": self.env_chmod_marker.exists(),
            "nginx_site": self._file_state(self.nginx_site),
            "nginx_service": self._file_state(self.nginx_state),
            "git_refs": self._file_state(self.git_refs),
            "docker": self._file_state(self.docker_trace),
            "revision_marker": self._file_state(self.revision_marker),
            "helper_script": self._file_state(self.remote_script),
            "monotonic_state": self._file_state(self.monotonic_state),
        }

    def run(
        self,
        *,
        outer_lock_mode="success",
        env_exists=True,
        env_permissions="600",
        env_chmod_exit=0,
        nginx_exit=0,
        systemctl_active_exit=0,
        systemctl_action_exit=0,
        fatsecret_listener="LISTEN 0 128 127.0.0.1:3000 0.0.0.0:*",
        remote_exit_code=0,
        remote_main=None,
        remote_proof_exit=0,
        authority_value=None,
    ):
        self.events.write_text("", encoding="utf-8")
        self.mutations.write_text("", encoding="utf-8")
        self.env_chmod_marker.unlink(missing_ok=True)
        if env_exists:
            if not self.env_file.exists():
                self.env_file.write_text("test-only-placeholder\n", encoding="utf-8")
        else:
            self.env_file.unlink(missing_ok=True)
        bash_root = _git_bash_path(self.root)
        bash_stubs = _git_bash_path(self.stubs)
        environment = {
            **os.environ,
            "HARNESS_DIR": bash_root,
            "DEPLOY_SHA": self.config.deploy_sha,
            "PATH": f"{bash_stubs}:/usr/bin:/bin",
            "OUTER_LOCK_MODE": outer_lock_mode,
            "ENV_FILE": _git_bash_path(self.env_file),
            "ENV_PERMISSIONS": env_permissions,
            "ENV_CHMOD_EXIT": str(env_chmod_exit),
            "ENV_CHMOD_MARKER": _git_bash_path(self.env_chmod_marker),
            "NGINX_EXIT": str(nginx_exit),
            "SYSTEMCTL_ACTIVE_EXIT": str(systemctl_active_exit),
            "SYSTEMCTL_ACTION_EXIT": str(systemctl_action_exit),
            "FATSECRET_LISTENER": fatsecret_listener,
            "REMOTE_EXIT_CODE": str(remote_exit_code),
            "MUTATION_LOG": _git_bash_path(self.mutations),
            "REMOTE_MAIN": remote_main or self.config.deploy_sha,
            "REMOTE_PROOF_EXIT": str(remote_proof_exit),
            "AUTHORITY_VALUE": (
                authority_value
                if authority_value is not None
                else f"{self.config.deploy_sha}:{AUTHORITY_TOKEN}"
            ),
            "NGINX_STATE": _git_bash_path(self.nginx_state),
            "GIT_REFS": _git_bash_path(self.git_refs),
            "DOCKER_TRACE": _git_bash_path(self.docker_trace),
            "HARNESS_MONOTONIC_STATE": _git_bash_path(self.monotonic_state),
        }
        # Git for Windows truncates a process command line at 8 KiB in BOTH
        # directions: `bash -c <script>` silently loses the tail of a longer
        # script, and bash silently truncates a longer argv when it execs a
        # child.  The rendered bootstrap is larger than that, so this harness
        # (a) hands bash a script file, which is also how the SSM agent
        # delivers the command on the real host, and (b) shadows the outer
        # `python3` with a shell function that runs the very same stub body
        # without an exec.  Neither step alters one byte of the rendered
        # command, and the bootstrap's own `python3` calls still resolve the
        # PATH stub: the function is deliberately not exported, so the
        # root-lock child looks it up exactly as the real host would.
        command_file = self.root / "remote-command.sh"
        command_file.write_text(
            f"export PATH={shlex.quote(f'{bash_stubs}:/usr/bin:/bin')}\n"
            f"python3() {{ ( . {shlex.quote(f'{bash_stubs}/python3')} ); }}\n"
            + build_remote_command(self.config, self.script),
            encoding="utf-8",
            newline="\n",
        )
        return subprocess.run(
            [
                str(self.bash), "--noprofile", "--norc",
                _git_bash_path(command_file),
            ],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )


@pytest.fixture
def bootstrap_harness(workspace_tmp_dir):
    git_bash = _locate_git_bash()
    if git_bash is None:
        pytest.skip("Git Bash is unavailable")

    harness = workspace_tmp_dir / "bootstrap-harness"
    stubs = harness / "bin"
    stubs.mkdir(parents=True)
    deploy_dir_path = harness / "deploy-safe"
    deploy_dir_path.mkdir()
    env_file = deploy_dir_path / ".env"
    env_chmod_marker = harness / "env-chmod-complete"
    events = harness / "events"
    events.write_text("", encoding="utf-8")
    mutations = harness / "mutations"
    mutations.write_text("", encoding="utf-8")
    env_file.write_text("test-only-placeholder\n", encoding="utf-8")
    nginx_site = harness / "nginx-site"
    nginx_site.write_text("server { listen 80; }\n", encoding="utf-8")
    nginx_state = harness / "nginx-service-state"
    nginx_state.write_text("", encoding="utf-8")
    git_refs = harness / "git-refs"
    git_refs.write_text("", encoding="utf-8")
    docker_trace = harness / "docker-trace"
    docker_trace.write_text("", encoding="utf-8")
    revision_marker = deploy_dir_path / "BUILD_REVISION"
    revision_marker.write_text("b" * 40 + "\n", encoding="utf-8")
    monotonic_state = harness / "runtime-monotonic-clock"
    monotonic_state.write_text("", encoding="utf-8")
    remote_script = harness / "helper-dir" / "production_deploy.sh"
    bash_harness = _git_bash_path(harness)

    _write_shell_stub(stubs, "python3", """
test "$1" = '-'
if [ "$#" = 2 ]; then
  printf '%s\n' outer-lock >> "$HARNESS_DIR/events"
  printf '%s\\0' "$@" > "$HARNESS_DIR/python3-args"
  case "$OUTER_LOCK_MODE" in
    success) ;;
    contended|unsafe|symlink) exit 73 ;;
    *) exit 64 ;;
  esac
  shift
  cat > "$HARNESS_DIR/root-lock-source"
  # Piped, not `sh -c "$command"`: the decoded bootstrap is larger than the
  # 8 KiB command line Git for Windows will carry, and an argv would be
  # truncated in silence.
  printf '%s' "$1" | base64 --decode | AXISAI_ROOT_LOCK_FD=7 /bin/sh
elif [ "$#" = 3 ] && [ "$2" = materialize-helper ]; then
  printf '%s\\n' materialize-helper >> "$HARNESS_DIR/events"
  printf '%s\\0' "$@" > "$HARNESS_DIR/helper-args"
  cat > "$HARNESS_DIR/helper-materialization-source"
  # Emulate root materialization: a private directory holding the exact
  # verified bytes, which the harness later asserts root removed.
  mkdir -p "$HARNESS_DIR/helper-dir"
  sed -n "s/^ENCODED_HELPER = '\\\\(.*\\\\)'$/\\\\1/p" \\
    "$HARNESS_DIR/helper-materialization-source" | base64 --decode \\
    > "$HARNESS_DIR/helper-dir/production_deploy.sh"
  printf '%s\\n' "$HARNESS_DIR/helper-dir"
elif [ "$#" = 3 ] && [ "$3" = "$DEPLOY_SHA" ]; then
  printf '%s\n' authority >> "$HARNESS_DIR/events"
  cat > "$HARNESS_DIR/authority-value-source"
  case "$2" in
    "$DEPLOY_SHA":????????-????-????-????-????????????) exit 0 ;;
    *) exit 1 ;;
  esac
elif [ "$#" = 3 ]; then
  printf '%s\n' env-guard >> "$HARNESS_DIR/events"
  printf '%s\n' 'fchmod:.env' >> "$MUTATION_LOG"
  cat > "$HARNESS_DIR/env-guard-source"
  if [ ! -f "$ENV_FILE" ]; then
    echo 'deployment .env file is missing' >&2
    exit 1
  fi
  if [ "$ENV_PERMISSIONS" != 600 ]; then
    if [ "$ENV_CHMOD_EXIT" != 0 ]; then exit "$ENV_CHMOD_EXIT"; fi
    : > "$ENV_CHMOD_MARKER"
  fi
else
  printf '%s\n' privilege-drop >> "$HARNESS_DIR/events"
  printf '%s\n' 'privilege-drop' >> "$MUTATION_LOG"
  shift
  user="$1"; script_path="$2"; sha="$3"; deploy_dir="$4"; url="$5"; fd="$6"
  test "$user" = deploy
  test "$fd" = 7
  cat > "$HARNESS_DIR/privilege-drop-source"
  AXISAI_OUTER_LOCK_FD=7 sh "$script_path" "$sha" "$deploy_dir" "$url"
fi
""")

    _write_shell_stub(stubs, "aws", """
case "$*" in
  *'ssm get-parameter'*)
    printf '%s\n' "$AUTHORITY_VALUE"
    ;;
  *) exit 64 ;;
esac
""")

    _write_shell_stub(stubs, "runuser", """
printf '%s\n' runuser >> "$HARNESS_DIR/events"
printf '%s\\0' "$@" > "$HARNESS_DIR/runuser-args"
test "$1" = '-u'
test "$3" = '--'
shift 3
exec "$@"
""")

    _write_shell_stub(stubs, "git", """
printf '%s\n' "git:$*" >> "$HARNESS_DIR/git-calls"
case "$*" in
  *' ls-remote '*)
    if [ "$REMOTE_PROOF_EXIT" != 0 ]; then exit "$REMOTE_PROOF_EXIT"; fi
    printf '%s\\t%s\n' "$REMOTE_MAIN" refs/heads/main
    ;;
  *' fetch '*)
    printf '%s\n' "git:fetch" >> "$MUTATION_LOG"
    printf '%s\n' "$REMOTE_MAIN" >> "$GIT_REFS"
    ;;
  *) exit 64 ;;
esac
""")

    _write_shell_stub(stubs, "docker", """
printf '%s\n' "docker:$*" >> "$MUTATION_LOG"
printf '%s\n' "docker:$*" >> "$DOCKER_TRACE"
""")

    _write_shell_stub(stubs, "install", """
printf '%s\n' install >> "$HARNESS_DIR/events"
printf '%s\\0' "$@" > "$HARNESS_DIR/install-args"
printf '%s\n' "install:$*" >> "$MUTATION_LOG"
: > "$HARNESS_MONOTONIC_STATE"
""")

    _write_shell_stub(stubs, "mktemp", """
printf '%s\n' mktemp >> "$HARNESS_DIR/events"
printf '%s\n' "mktemp:$*" >> "$MUTATION_LOG"
printf '%s\\0' "$@" > "$HARNESS_DIR/mktemp-args"
printf '%s\n' "$HARNESS_DIR/decoded-script"
""")
    _write_shell_stub(stubs, "chmod", """
printf '%s\n' chmod >> "$HARNESS_DIR/events"
printf '%s\n' "chmod:$*" >> "$MUTATION_LOG"
printf '%s\\0' "$@" > "$HARNESS_DIR/chmod-args"
last=''
for argument in "$@"; do last="$argument"; done
if [ "$last" = "$ENV_FILE" ]; then
  if [ "$ENV_CHMOD_EXIT" != 0 ]; then exit "$ENV_CHMOD_EXIT"; fi
  : > "$ENV_CHMOD_MARKER"
fi
""")
    _write_shell_stub(stubs, "id", """
printf '%s\n' id >> "$HARNESS_DIR/events"
printf '%s\\0' "$@" > "$HARNESS_DIR/id-args"
""")
    _write_shell_stub(stubs, "chown", """
printf '%s\n' chown >> "$HARNESS_DIR/events"
printf '%s\n' "chown:$*" >> "$MUTATION_LOG"
printf '%s\\0' "$@" > "$HARNESS_DIR/chown-args"
""")
    _write_shell_stub(stubs, "nginx", """
printf '%s\n' nginx >> "$HARNESS_DIR/events"
test "$1" = '-t'
exit "$NGINX_EXIT"
""")
    _write_shell_stub(stubs, "systemctl", """
printf '%s\n' systemctl >> "$HARNESS_DIR/events"
case "$1" in
  is-active) exit "$SYSTEMCTL_ACTIVE_EXIT" ;;
  reload)
    printf '%s\n' "systemctl:reload" >> "$MUTATION_LOG"
    printf '%s\n' reloaded >> "$NGINX_STATE"
    exit "$SYSTEMCTL_ACTION_EXIT"
    ;;
  enable)
    printf '%s\n' "systemctl:enable" >> "$MUTATION_LOG"
    printf '%s\n' enabled >> "$NGINX_STATE"
    exit "$SYSTEMCTL_ACTION_EXIT"
    ;;
  *) exit 64 ;;
esac
""")
    _write_shell_stub(stubs, "ss", """
printf '%s\n' ss >> "$HARNESS_DIR/events"
case " $* " in
  *' -ltnp '*) printf '%s\n' 'LISTEN 0 128 0.0.0.0:443 0.0.0.0:*' ;;
  *) printf '%s\n' "$FATSECRET_LISTENER" ;;
esac
""")
    _write_shell_stub(stubs, "stat", """
printf '%s\n' stat >> "$HARNESS_DIR/events"
if [ -e "$ENV_CHMOD_MARKER" ]; then
  printf '%s\n' 600
else
  printf '%s\n' "$ENV_PERMISSIONS"
fi
""")
    _write_shell_stub(stubs, "sudo", """
printf '%s\n' sudo >> "$HARNESS_DIR/events"
printf '%s\\0' "$@" > "$HARNESS_DIR/sudo-args"
test "$1" = '-u'
shift
user="$1"
shift
test "$1" = '--'
shift
if [ "$1" = 'env' ]; then
  shift
  export "$1"
  shift
fi
script_path="$1"
shift
printf '%s' "$user" > "$HARNESS_DIR/sudo-user"
sh "$script_path" "$@"
""")

    deploy_dir = _git_bash_path(deploy_dir_path)
    public_url = "https://fitness.example/health?probe='$(touch%20nope)"
    config = DeployConfig.from_environ({
        **VALID_ENV,
        "DEPLOY_DIR": deploy_dir,
        "PUBLIC_HEALTH_URL": public_url,
    })
    script = b"""#!/bin/sh
printf '%s\\0' "$@" > "$HARNESS_DIR/script-args"
printf '%s' "${AXISAI_OUTER_LOCK_FD:-}" > "$HARNESS_DIR/script-lock-fd"
exit "$REMOTE_EXIT_CODE"
"""
    return BootstrapHarness(
        bash=git_bash,
        root=harness,
        stubs=stubs,
        deploy_dir=deploy_dir_path,
        remote_script=remote_script,
        events=events,
        mutations=mutations,
        env_file=env_file,
        env_chmod_marker=env_chmod_marker,
        nginx_site=nginx_site,
        nginx_state=nginx_state,
        git_refs=git_refs,
        docker_trace=docker_trace,
        revision_marker=revision_marker,
        monotonic_state=monotonic_state,
        config=config,
        script=script,
    )


@pytest.mark.parametrize("remote_exit_code", [0, 23])
def test_bootstrap_holds_lock_through_safeguards_and_helper_cleanup(
        bootstrap_harness, remote_exit_code):
    completed = bootstrap_harness.run(remote_exit_code=remote_exit_code)
    harness = bootstrap_harness.root
    deploy_dir = _git_bash_path(bootstrap_harness.deploy_dir)
    public_url = bootstrap_harness.config.public_health_url

    assert completed.returncode == remote_exit_code, completed.stderr
    assert (harness / "events").read_text(encoding="utf-8").splitlines() == [
        "outer-lock", "authority", "runuser",
        "env-guard", "nginx", "systemctl", "systemctl", "ss", "ss",
        "install", "materialize-helper", "privilege-drop",
    ]
    # The helper is materialized as a root-owned object, never chmod-ed or
    # chown-ed into the deploy user's reach: those stubs must not fire at all.
    assert not (harness / "mktemp-args").exists()
    assert not (harness / "chmod-args").exists()
    assert not (harness / "chown-args").exists()
    assert _read_null_arguments(harness / "helper-args") == [
        "-", "materialize-helper",
        hashlib.sha256(bootstrap_harness.script).hexdigest(),
    ]
    assert _read_null_arguments(harness / "script-args") == [
        VALID_ENV["DEPLOY_SHA"], deploy_dir, public_url,
    ]
    assert (harness / "script-lock-fd").read_text(encoding="utf-8") == "7"
    assert "sudo" not in (harness / "privilege-drop-source").read_text(encoding="utf-8")
    assert not bootstrap_harness.remote_script.exists()


def test_bootstrap_contention_exits_73_before_any_host_side_effect(
        bootstrap_harness):
    completed = bootstrap_harness.run(outer_lock_mode="contended")

    assert completed.returncode == 73
    assert bootstrap_harness.events.read_text(encoding="utf-8").splitlines() == [
        "outer-lock",
    ]
    assert not bootstrap_harness.remote_script.exists()


@pytest.mark.parametrize("outer_lock_mode", ["unsafe", "symlink"])
def test_bootstrap_rejects_unsafe_outer_lock_before_any_host_side_effect(
        bootstrap_harness, outer_lock_mode):
    completed = bootstrap_harness.run(outer_lock_mode=outer_lock_mode)

    assert completed.returncode == 73
    assert bootstrap_harness.events.read_text(encoding="utf-8").splitlines() == [
        "outer-lock",
    ]
    assert not bootstrap_harness.remote_script.exists()


def test_bootstrap_missing_env_fails_before_nginx_or_helper(bootstrap_harness):
    completed = bootstrap_harness.run(env_exists=False)
    events = bootstrap_harness.events.read_text(encoding="utf-8").splitlines()

    assert completed.returncode != 0
    assert "deployment .env file is missing" in completed.stderr
    assert events == ["outer-lock", "authority", "runuser", "env-guard"]


def test_bootstrap_repairs_and_rechecks_env_permissions_before_helper(
        bootstrap_harness):
    completed = bootstrap_harness.run(env_permissions="644")
    events = bootstrap_harness.events.read_text(encoding="utf-8").splitlines()

    assert completed.returncode == 0, completed.stderr
    assert events[:4] == ["outer-lock", "authority", "runuser", "env-guard"]
    assert bootstrap_harness.env_chmod_marker.exists()
    assert "privilege-drop" in events


def test_bootstrap_unrepairable_env_permissions_fail_before_helper(
        bootstrap_harness):
    completed = bootstrap_harness.run(env_permissions="644", env_chmod_exit=5)
    events = bootstrap_harness.events.read_text(encoding="utf-8").splitlines()

    assert completed.returncode != 0
    assert "privilege-drop" not in events
    assert "nginx" not in events


def test_bootstrap_nginx_failure_stops_before_systemctl_and_helper(bootstrap_harness):
    completed = bootstrap_harness.run(nginx_exit=1)
    events = bootstrap_harness.events.read_text(encoding="utf-8").splitlines()

    assert completed.returncode != 0
    assert "nginx configuration test failed" in completed.stderr
    assert "systemctl" not in events
    assert "privilege-drop" not in events


def _decode_inner_bootstrap(command):
    """Return the base64-decoded root-lock child of a rendered bootstrap."""
    root_wrapper_line = next(
        line for line in command.splitlines() if line.startswith("python3 - ")
    )
    arguments = shlex.split(root_wrapper_line.split(" <<", 1)[0])
    return base64.b64decode(arguments[2], validate=True).decode("utf-8")


MUTATION_MARKERS = (
    "AXISAI_ENV_GUARD_PY",
    "os.fchmod",
    "nginx -t",
    "systemctl",
    "AXISAI_HELPER_MATERIALIZATION_PY",
    "AXISAI_PRIVILEGE_DROP_PY",
)


def test_stale_post_lock_command_preserves_all_production_state(bootstrap_harness):
    before = bootstrap_harness.production_snapshot()
    result = bootstrap_harness.run(remote_main="f" * 40)
    after = bootstrap_harness.production_snapshot()

    assert result.returncode == 75, result.stderr
    assert "stale at host mutation gate" in result.stderr
    assert after == before
    assert bootstrap_harness.mutation_trace() == []


def test_stale_command_never_chmods_env_or_reloads_nginx(bootstrap_harness):
    result = bootstrap_harness.run(remote_main="f" * 40)

    assert result.returncode == 75
    assert "fchmod:.env" not in bootstrap_harness.trace_text()
    assert "systemctl" not in bootstrap_harness.trace_text()


def test_stale_command_never_writes_local_refs_before_the_remote_proof(
        bootstrap_harness):
    """A local `git fetch` writes refs/objects, so it is mutation, not proof."""
    result = bootstrap_harness.run(remote_main="f" * 40)

    assert result.returncode == 75
    assert "git:fetch" not in bootstrap_harness.trace_text()
    assert bootstrap_harness.git_refs.read_bytes() == b""
    assert bootstrap_harness.event_trace() == ["outer-lock", "authority", "runuser"]


def test_root_bootstrap_proves_remote_main_before_any_mutation():
    inner_bootstrap = _decode_inner_bootstrap(
        build_remote_command(DeployConfig.from_environ(VALID_ENV), b"exit 0\n"),
    )

    proof = inner_bootstrap.index("ls-remote --exit-code origin refs/heads/main")
    authority = inner_bootstrap.index("aws ssm get-parameter")
    capability = inner_bootstrap.index("AXISAI_ROOT_LOCK_FD")
    assert capability < authority < proof
    for marker in MUTATION_MARKERS:
        assert proof < inner_bootstrap.index(marker), marker
    # The read-only proof must not be "optimised" into a ref-writing fetch.
    assert "git fetch" not in inner_bootstrap
    assert "runuser -u" in inner_bootstrap


def test_authority_gate_runs_inside_the_root_lock_child():
    command = build_remote_command(
        DeployConfig.from_environ(VALID_ENV), b"exit 0\n",
    )

    lines = command.split("\n")
    # The outer script keeps its explicit shell error handling and is otherwise
    # nothing but the root-lock wrapper invocation.
    assert lines[0] == "set -eu"
    assert lines[1].startswith("python3 - ")
    assert "aws ssm get-parameter" not in lines[0]
    assert "aws ssm get-parameter" not in lines[1]
    assert "aws ssm get-parameter" in _decode_inner_bootstrap(command)


def test_unauthorized_command_is_rejected_inside_the_lock_before_mutation(
        bootstrap_harness):
    before = bootstrap_harness.production_snapshot()
    result = bootstrap_harness.run(authority_value="not-authorized")

    assert result.returncode == 75
    assert "not authorized by the controller" in result.stderr
    assert bootstrap_harness.mutation_trace() == []
    assert bootstrap_harness.production_snapshot() == before
    # The gate must be proven behind the outer lock, not ahead of it.
    assert bootstrap_harness.event_trace()[0] == "outer-lock"
    assert "runuser" not in bootstrap_harness.event_trace()


def test_unprovable_remote_main_fails_closed_as_stale_without_mutation(
        bootstrap_harness):
    result = bootstrap_harness.run(remote_proof_exit=2)

    assert result.returncode == 75
    assert "could not be proven current" in result.stderr
    assert bootstrap_harness.mutation_trace() == []


def test_current_candidate_passes_the_gate_and_reaches_the_helper(
        bootstrap_harness):
    result = bootstrap_harness.run()

    assert result.returncode == 0, result.stderr
    assert bootstrap_harness.event_trace()[:3] == [
        "outer-lock", "authority", "runuser",
    ]
    assert "privilege-drop" in bootstrap_harness.event_trace()
    assert _read_null_arguments(bootstrap_harness.root / "runuser-args") == [
        "-u", VALID_ENV["DEPLOY_USER"], "--", "git", "-C",
        _git_bash_path(bootstrap_harness.deploy_dir),
        "ls-remote", "--exit-code", "origin", "refs/heads/main",
    ]


def test_monotonic_clock_state_lives_in_the_verified_root_runtime_directory():
    inner_bootstrap = _decode_inner_bootstrap(
        build_remote_command(DeployConfig.from_environ(VALID_ENV), b"exit 0\n"),
    )

    assert deploy_control.MONOTONIC_STATE_PATH == (
        deploy_control.ROOT_RUNTIME_DIR + "/monotonic-clock"
    )
    assert deploy_control.ROOT_RUNTIME_DIR == str(
        PurePosixPath(deploy_control.ROOT_OUTER_LOCK_PATH).parent
    )
    assert deploy_control.MONOTONIC_STATE_PATH in inner_bootstrap
    assert '"AXISAI_MONOTONIC_STATE": MONOTONIC_STATE_PATH' in (
        deploy_control.PRIVILEGE_DROP_SOURCE
    )
    # The clock state is provisioned only after the mutation gate opens.
    assert inner_bootstrap.index(
        "ls-remote --exit-code origin refs/heads/main"
    ) < inner_bootstrap.index(deploy_control.MONOTONIC_STATE_PATH)


def test_bootstrap_warns_for_port_30000_without_treating_it_as_fatsecret(
        bootstrap_harness):
    completed = bootstrap_harness.run(
        fatsecret_listener="LISTEN 0 128 127.0.0.1:30000 0.0.0.0:*",
    )
    events = bootstrap_harness.events.read_text(encoding="utf-8").splitlines()

    assert completed.returncode == 0, completed.stderr
    assert "WARNING: fatsecret proxy is not listening on 127.0.0.1:3000" in completed.stdout
    assert "privilege-drop" in events


@pytest.mark.parametrize("response", [
    None,
    [],
    {},
    {"Command": {}},
    {"Command": {"CommandId": None}},
    {"Command": {"CommandId": "command-id"}},
])
def test_send_command_requires_exactly_one_uuid_command_id(response):
    def aws(args):
        if args[:2] == ["ssm", "describe-instance-information"]:
            return managed_instance_response(NOW)
        return response

    with pytest.raises(InvocationProtocolError):
        send_command(
            DeployConfig.from_environ(VALID_ENV), b"echo safe",
            aws, utc_now=lambda: NOW,
        )


def test_read_invocation_builds_argument_array_and_preserves_aws_fields():
    calls = []

    def aws(args):
        calls.append(args)
        return invocation_response(
            "Success", response_code=0, stdout="deploy complete\n", stderr="warning\n",
        )

    result = read_invocation(DeployConfig.from_environ(VALID_ENV), "command-id", aws)

    assert result.status_details == "Success"
    assert result.response_code == 0
    assert result.stdout == "deploy complete\n"
    assert result.stderr == "warning\n"
    assert calls == [[
        "ssm", "get-command-invocation", "--region", VALID_ENV["AWS_REGION"],
        "--command-id", "command-id", "--instance-id", VALID_ENV["EC2_INSTANCE_ID"],
    ]]


@pytest.mark.parametrize("field,value", [
    ("StatusDetails", None),
    ("ResponseCode", "0"),
    ("ResponseCode", True),
    ("StandardOutputContent", None),
    ("StandardErrorContent", []),
])
def test_read_invocation_rejects_wrong_required_field_types(field, value):
    response = invocation_response("Success", response_code=0)
    response[field] = value

    with pytest.raises(InvocationProtocolError, match=field):
        read_invocation(DeployConfig.from_environ(VALID_ENV), "command-id", lambda args: response)


def test_in_progress_is_reported_without_claiming_host_process_start(fake_clock):
    aws = invocation_sequence("Pending", "Delayed", "InProgress", "Success")
    messages = []

    result = wait_for_invocation(
        DeployConfig.from_environ(VALID_ENV), "command-id", aws,
        fake_clock.monotonic, fake_clock.sleep, messages.append,
    )

    assert result.status_details == "Success"
    assert sum("SSM reports command InProgress" in message for message in messages) == 1
    assert "host execution started" not in messages
    assert messages.index("SSM reports command InProgress") > messages.index("SSM status: Delayed")


def test_spaced_status_is_normalized_only_for_comparison(fake_clock):
    messages = []

    result = wait_for_invocation(
        DeployConfig.from_environ(VALID_ENV), "command-id",
        invocation_sequence("In Progress", "Success"),
        fake_clock.monotonic, fake_clock.sleep, messages.append,
    )

    assert result.status_details == "Success"
    assert messages[:2] == [
        "SSM status: In Progress",
        "SSM reports command InProgress",
    ]


@pytest.mark.parametrize("status", [
    "Failed",
    "DeliveryTimedOut",
    "ExecutionTimedOut",
    "Undeliverable",
    "Cancelled",
    "Terminated",
    "Delivery Timed Out",
    "Execution Timed Out",
])
def test_terminal_failure_states_fail_closed_with_full_result(status, fake_clock):
    response = invocation_response(status, response_code=1, stderr="deploy failed")

    with pytest.raises(InvocationFailed) as captured:
        wait_for_invocation(
            DeployConfig.from_environ(VALID_ENV), "command-id", lambda args: response,
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
        )

    assert captured.value.result.status_details == status
    assert captured.value.result.response_code == 1
    assert captured.value.result.stderr == "deploy failed"


def test_unknown_status_is_a_visible_protocol_failure(fake_clock):
    messages = []

    with pytest.raises(InvocationProtocolError, match="Mystery"):
        wait_for_invocation(
            DeployConfig.from_environ(VALID_ENV), "command-id",
            invocation_sequence("Mystery"),
            fake_clock.monotonic, fake_clock.sleep, messages.append,
        )

    assert messages == ["SSM status: Mystery"]


def test_aws_runner_failure_is_not_converted_to_pending(fake_clock):
    messages = []

    def aws(args):
        raise AwsCliError("get-command-invocation failed")

    with pytest.raises(AwsCliError, match="get-command-invocation failed"):
        wait_for_invocation(
            DeployConfig.from_environ(VALID_ENV), "command-id", aws,
            fake_clock.monotonic, fake_clock.sleep, messages.append,
        )

    assert messages == []


def test_first_poll_not_visible_recovers_within_existing_horizon(fake_clock):
    responses = iter([
        AwsCliError(
            "aws ssm get-command-invocation failed with exit code 254",
            code="InvocationDoesNotExist",
        ),
        invocation_response("Success", response_code=0),
    ])

    def aws(args):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    messages = []
    result = wait_for_invocation(
        DeployConfig.from_environ(VALID_ENV), "command-id", aws,
        fake_clock.monotonic, fake_clock.sleep, messages.append,
    )

    assert result.status_details == "Success"
    assert fake_clock.sleeps == [10]
    assert messages == [
        "SSM invocation not visible yet",
        "SSM status: Success",
    ]


def test_invocation_not_visible_is_bounded_by_polling_horizon(fake_clock):
    calls = 0

    def aws(args):
        nonlocal calls
        calls += 1
        raise AwsCliError(
            "aws ssm get-command-invocation failed with exit code 254",
            code="InvocationDoesNotExist",
        )

    with pytest.raises(InvocationPollingTimeout, match="command-id"):
        wait_for_invocation(
            DeployConfig.from_environ(VALID_ENV), "command-id", aws,
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
        )

    assert fake_clock.now == POLL_HORIZON_SECONDS
    assert calls == POLL_HORIZON_SECONDS // 10


def test_other_aws_error_codes_are_not_retried(fake_clock):
    calls = 0

    def aws(args):
        nonlocal calls
        calls += 1
        raise AwsCliError(
            "aws ssm get-command-invocation failed with exit code 254",
            code="AccessDeniedException",
        )

    with pytest.raises(AwsCliError) as captured:
        wait_for_invocation(
            DeployConfig.from_environ(VALID_ENV), "command-id", aws,
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
        )

    assert captured.value.code == "AccessDeniedException"
    assert calls == 1
    assert fake_clock.sleeps == []


def test_invocation_runner_timeout_propagates_closed(fake_clock):
    messages = []

    def aws(args):
        fake_clock.now += INVOCATION_CALL_TIMEOUT_SECONDS
        raise AwsCliError(
            f"aws cli timed out after {INVOCATION_CALL_TIMEOUT_SECONDS} seconds",
        )

    with pytest.raises(AwsCliError, match="timed out after 30 seconds"):
        wait_for_invocation(
            DeployConfig.from_environ(VALID_ENV), "command-id", aws,
            fake_clock.monotonic, fake_clock.sleep, messages.append,
        )

    assert fake_clock.now == 30
    assert messages == []


def test_pending_is_polled_through_the_complete_horizon(fake_clock):
    calls = 0

    def aws(args):
        nonlocal calls
        calls += 1
        return invocation_response("Pending")

    with pytest.raises(InvocationPollingTimeout, match="command-id"):
        wait_for_invocation(
            DeployConfig.from_environ(VALID_ENV), "command-id", aws,
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
        )

    assert fake_clock.now == POLL_HORIZON_SECONDS == 2100
    assert calls == POLL_HORIZON_SECONDS // 10


def test_success_returned_after_polling_deadline_is_rejected(fake_clock):
    def aws(args):
        fake_clock.now += POLL_HORIZON_SECONDS + 1
        return invocation_response("Success", response_code=0)

    with pytest.raises(InvocationPollingTimeout, match="command-id"):
        wait_for_invocation(
            DeployConfig.from_environ(VALID_ENV), "command-id", aws,
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
        )


def test_final_poll_sleep_is_clipped_to_remaining_horizon(fake_clock):
    def aws(args):
        fake_clock.now += POLL_HORIZON_SECONDS - 4
        return invocation_response("Pending")

    with pytest.raises(InvocationPollingTimeout, match="command-id"):
        wait_for_invocation(
            DeployConfig.from_environ(VALID_ENV), "command-id", aws,
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
        )

    assert fake_clock.sleeps == [4]
    assert fake_clock.now == POLL_HORIZON_SECONDS


class FakeAwsCompletedProcess:
    def __init__(self, *, returncode=0, stdout="{}", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_aws_json_runner_decodes_one_json_object_with_bounded_cli_arguments():
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return FakeAwsCompletedProcess(stdout='{"Reservations": []}')

    result = run_aws_json(["ec2", "describe-instances"], run=run)

    assert result == {"Reservations": []}
    assert calls == [([
        "aws", "ec2", "describe-instances", "--output", "json", "--no-cli-pager",
    ], {
        "capture_output": True,
        "check": False,
        "text": True,
        "timeout": AWS_CLI_CALL_TIMEOUT_SECONDS,
    })]
    assert 0 < AWS_CLI_CALL_TIMEOUT_SECONDS < POLL_HORIZON_SECONDS


def test_every_invocation_status_cli_call_has_the_exact_30_second_timeout():
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return FakeAwsCompletedProcess(stdout=json.dumps(invocation_response("Pending")))

    run_aws_json([
        "ssm", "get-command-invocation", "--command-id", "command-id",
        "--instance-id", VALID_ENV["EC2_INSTANCE_ID"],
    ], run=run)

    assert calls[0][1]["timeout"] == INVOCATION_CALL_TIMEOUT_SECONDS == 30


@pytest.mark.parametrize("args", [
    ["ec2", "describe-instances"],
    ["ssm", "describe-instance-information"],
    ["ssm", "send-command"],
])
def test_every_non_polling_aws_cli_call_is_bounded(args):
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs)
        return FakeAwsCompletedProcess()

    run_aws_json(args, run=run)

    assert calls[0]["timeout"] == AWS_CLI_CALL_TIMEOUT_SECONDS


def test_send_command_disables_opaque_aws_cli_retries():
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs)
        return FakeAwsCompletedProcess(stdout='{"Command": {}}')

    run_aws_json(["ssm", "send-command"], run=run)

    assert calls[0]["env"]["AWS_MAX_ATTEMPTS"] == "1"
    assert calls[0]["env"]["AWS_RETRY_MODE"] == "standard"


@pytest.mark.parametrize("completed,error_pattern", [
    (FakeAwsCompletedProcess(returncode=7, stderr="denied"), "failed with exit code 7"),
    (FakeAwsCompletedProcess(stdout="not-json"), "invalid JSON"),
    (FakeAwsCompletedProcess(stdout="[]"), "JSON object"),
])
def test_aws_json_runner_fails_closed_on_cli_and_json_errors(completed, error_pattern):
    with pytest.raises(AwsCliError, match=error_pattern):
        run_aws_json(["ec2", "describe-instances"], run=lambda args, **kwargs: completed)


def test_aws_json_runner_exposes_only_structured_sanitized_error_code():
    completed = FakeAwsCompletedProcess(
        returncode=254,
        stderr=(
            "An error occurred (InvocationDoesNotExist) when calling the "
            "GetCommandInvocation operation: secret-instance-detail"
        ),
    )

    with pytest.raises(AwsCliError) as captured:
        run_aws_json(
            ["ssm", "get-command-invocation"],
            run=lambda args, **kwargs: completed,
        )

    assert captured.value.code == "InvocationDoesNotExist"
    assert "secret-instance-detail" not in str(captured.value)


def test_aws_json_runner_converts_process_timeout_to_typed_failure():
    def run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    with pytest.raises(AwsCliError, match="timed out after 30 seconds"):
        run_aws_json(["ssm", "get-command-invocation"], run=run)


def test_aws_json_runner_converts_process_start_failure_to_typed_failure():
    def run(args, **kwargs):
        raise FileNotFoundError("aws")

    with pytest.raises(AwsCliError, match="could not start"):
        run_aws_json(["ec2", "describe-instances"], run=run)


def _write_integration_host_script(repo_path, content=b"#!/bin/sh\necho exact helper\n"):
    scripts = repo_path / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    host_script = scripts / "production_deploy.sh"
    host_script.write_bytes(content)
    return content


def test_run_deploy_orders_validation_preflight_exact_script_send_and_poll(
        workspace_tmp_dir, fake_clock):
    exact_script = _write_integration_host_script(
        workspace_tmp_dir,
        b"#!/bin/sh\nprintf 'exact checked-out helper\\n'\n",
    )
    events = []
    sent_parameters = []
    invocation_states = iter(["Pending", "In Progress", "Success"])
    now = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)

    def run_git(args):
        operation = args[3]
        events.append(f"git:{operation}")
        if operation == "rev-parse":
            return VALID_ENV["DEPLOY_SHA"]
        return ""

    def aws(args):
        operation = ":".join(args[:2])
        events.append(f"aws:{operation}")
        if args[:2] == ["ec2", "describe-instances"]:
            return {"Reservations": [{"Instances": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "State": {"Name": "running"},
            }]}]}
        if args[:2] == ["ssm", "describe-instance-information"]:
            return {"InstanceInformationList": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "PingStatus": "Online",
                "LastPingDateTime": "2026-08-22T17:58:00+00:00",
            }]}
        if args[:2] == ["ssm", "send-command"]:
            sent_parameters.append(json.loads(args[args.index("--parameters") + 1]))
            return {"Command": {
                "CommandId": "11111111-1111-1111-1111-111111111111",
            }}
        if args[:2] == ["ssm", "get-command-invocation"]:
            state = next(invocation_states)
            return invocation_response(
                state, response_code=0 if state == "Success" else -1,
                stdout="exact deploy complete\n", stderr="bounded warning\n",
            )
        if args[:2] == ["ssm", "put-parameter"]:
            value = args[args.index("--value") + 1]
            assert value == (
                f"{VALID_ENV['DEPLOY_SHA']}:"
                "11111111-1111-1111-1111-111111111111"
            )
            return {"Version": 1, "Tier": "Standard"}
        if args[:2] == ["ssm", "delete-parameter"]:
            return {}
        raise AssertionError(f"unexpected AWS call: {args}")

    messages = []
    result = run_deploy(
        VALID_ENV, workspace_tmp_dir, aws, lambda: now,
        fake_clock.monotonic, fake_clock.sleep, messages.append,
        run_git=run_git,
    )

    assert result.status_details == "Success"
    assert messages[0] == (
        "SSM command created: 11111111-1111-1111-1111-111111111111"
    )
    assert events == [
        "git:fetch", "git:cat-file", "git:rev-parse",
        "aws:ec2:describe-instances",
        "aws:ssm:describe-instance-information",
        "aws:ssm:describe-instance-information",
        "aws:ssm:send-command",
        "aws:ssm:put-parameter",
        "aws:ssm:get-command-invocation",
        "aws:ssm:get-command-invocation",
        "aws:ssm:get-command-invocation",
        "aws:ssm:delete-parameter",
    ]
    remote_command = sent_parameters[0]["commands"][0]
    remote_lines = remote_command.splitlines()
    root_wrapper_line = next(line for line in remote_lines if line.startswith("python3 - "))
    remote_arguments = shlex.split(root_wrapper_line.split(" <<", 1)[0])
    assert remote_arguments[:2] == ["python3", "-"]
    inner_bootstrap = base64.b64decode(
        remote_arguments[2], validate=True,
    ).decode("utf-8")
    assert base64.b64encode(exact_script).decode("ascii") in inner_bootstrap
    assert "sudo" not in inner_bootstrap
    assert deploy_control.PRIVILEGE_DROP_SOURCE in inner_bootstrap
    # The authority gate proves itself inside the root-lock child, so it can
    # never run ahead of the outer lock -- and never ahead of the mutation gate.
    assert "aws ssm get-parameter" not in remote_command
    assert "aws ssm get-parameter" in inner_bootstrap
    assert inner_bootstrap.index("aws ssm get-parameter") < inner_bootstrap.index(
        "AXISAI_ENV_GUARD_PY"
    )
    assert f"'{VALID_ENV['DEPLOY_USER']}' \"$script_path\"" in inner_bootstrap
    assert "\"$public_health_url\" '7'" in inner_bootstrap
    assert (
        f"python3 - '{VALID_ENV['DEPLOY_USER']}' \"$script_path\" "
        f"'{VALID_ENV['DEPLOY_SHA']}' \"$deploy_dir\""
    ) in inner_bootstrap
    assert messages[-9:] == [
        "SSM command delivery authorized",
        "SSM status: Pending",
        "SSM status: In Progress",
        "SSM reports command InProgress",
        "SSM status: Success",
        "SSM stdout:",
        "exact deploy complete",
        "SSM stderr:",
        "bounded warning",
    ]


def test_run_deploy_rejects_stale_candidate_before_any_aws_call(
        workspace_tmp_dir, fake_clock):
    _write_integration_host_script(workspace_tmp_dir)
    aws_calls = []

    with pytest.raises(ConfigError, match="stale"):
        run_deploy(
            VALID_ENV, workspace_tmp_dir, lambda args: aws_calls.append(args),
            lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
            run_git=fake_git_returning("b" * 40),
        )

    assert aws_calls == []


def test_ambiguous_send_command_failure_never_creates_delivery_authority(
        monkeypatch, workspace_tmp_dir, fake_clock):
    _write_integration_host_script(workspace_tmp_dir)
    monkeypatch.setattr(deploy_control, "validate_candidate", lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy_control, "preflight", lambda *args, **kwargs: None)
    calls = []

    def aws(args):
        calls.append(args[:2])
        if args[:2] == ["ssm", "describe-instance-information"]:
            return managed_instance_response(NOW)
        if args[:2] == ["ssm", "send-command"]:
            raise AwsCliError("SendCommand response was lost")
        raise AssertionError(f"unexpected AWS call after ambiguous send: {args}")

    with pytest.raises(AwsCliError, match="response was lost"):
        run_deploy(
            VALID_ENV, workspace_tmp_dir, aws,
            lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
        )

    assert calls == [
        ["ssm", "describe-instance-information"],
        ["ssm", "send-command"],
    ]


def test_ambiguous_authorization_response_keeps_monitoring_known_command(
        monkeypatch, workspace_tmp_dir, fake_clock):
    _write_integration_host_script(workspace_tmp_dir)
    monkeypatch.setattr(deploy_control, "validate_candidate", lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy_control, "preflight", lambda *args, **kwargs: None)
    calls = []
    invocation_states = iter(["In Progress", "Failed"])

    def aws(args):
        calls.append(args[:2])
        if args[:2] == ["ssm", "describe-instance-information"]:
            return managed_instance_response(NOW)
        if args[:2] == ["ssm", "send-command"]:
            return {"Command": {
                "CommandId": "11111111-1111-1111-1111-111111111111",
            }}
        if args[:2] == ["ssm", "get-command-invocation"]:
            state = next(invocation_states)
            return invocation_response(state, response_code=-1 if state != "Failed" else 1)
        if args[:2] == ["ssm", "put-parameter"]:
            raise AwsCliError("PutParameter response was lost")
        if args[:2] == ["ssm", "delete-parameter"]:
            return {}
        raise AssertionError(f"unexpected AWS call: {args}")

    messages = []
    with pytest.raises(InvocationFailed):
        run_deploy(
            VALID_ENV, workspace_tmp_dir, aws,
            lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
            fake_clock.monotonic, fake_clock.sleep, messages.append,
        )

    assert calls == [
        ["ssm", "describe-instance-information"],
        ["ssm", "send-command"],
        ["ssm", "put-parameter"],
        ["ssm", "get-command-invocation"],
        ["ssm", "get-command-invocation"],
        ["ssm", "delete-parameter"],
    ]
    assert "SSM delivery authorization response ambiguous; monitoring command ID" in messages


def test_controller_refuses_to_send_without_full_terminal_monitoring_reserve(
        workspace_tmp_dir, fake_clock):
    _write_integration_host_script(workspace_tmp_dir)
    calls = []

    def aws(args):
        calls.append(args[:2])
        if args[:2] == ["ec2", "describe-instances"]:
            return {"Reservations": [{"Instances": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "State": {"Name": "running"},
            }]}]}
        if args[:2] == ["ssm", "describe-instance-information"]:
            fake_clock.now = (
                CONTROLLER_BUDGET_SECONDS - SEND_AND_MONITOR_RESERVE_SECONDS + 1
            )
            return {"InstanceInformationList": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "PingStatus": "Online",
                "LastPingDateTime": "2026-08-22T17:58:00+00:00",
            }]}
        raise AssertionError("SendCommand must not run without its full reserve")

    with pytest.raises(ConfigError, match="terminal monitoring reserve"):
        run_deploy(
            VALID_ENV, workspace_tmp_dir, aws,
            lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
            run_git=fake_git_returning(VALID_ENV["DEPLOY_SHA"]),
        )

    assert ["ssm", "send-command"] not in calls
    assert CONTROLLER_BUDGET_SECONDS == 46 * 60
    assert SEND_AND_MONITOR_RESERVE_SECONDS > AWS_EXPIRY_SECONDS


def test_run_deploy_rejects_failed_preflight_before_loading_or_sending_script(
        workspace_tmp_dir, fake_clock):
    aws_calls = []

    def aws(args):
        aws_calls.append(args)
        if args[:2] == ["ec2", "describe-instances"]:
            return {"Reservations": [{"Instances": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "State": {"Name": "stopped"},
            }]}]}
        raise AssertionError("preflight failure must stop later AWS calls")

    with pytest.raises(PreflightError, match="not running"):
        run_deploy(
            VALID_ENV, workspace_tmp_dir, aws,
            lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
            run_git=fake_git_returning(VALID_ENV["DEPLOY_SHA"]),
        )

    assert [call[:2] for call in aws_calls] == [["ec2", "describe-instances"]]


def test_run_deploy_refuses_a_heartbeat_that_ages_out_during_the_lifecycle(
        workspace_tmp_dir, fake_clock):
    # The operational invariant behind the send boundary: time the controller
    # itself spends between preflight and send counts against heartbeat age.
    # The agent stops pinging, so `LastPingDateTime` is constant while the wall
    # clock advances past the ceiling. A clock frozen anywhere in the lifecycle
    # would report a young heartbeat here and let the send through.
    _write_integration_host_script(workspace_tmp_dir)
    last_ping = NOW
    readings = iter([
        NOW + timedelta(seconds=10),                                # preflight
        NOW + timedelta(seconds=SSM_HEARTBEAT_MAX_AGE_SECONDS + 40),  # send
    ])
    aws_calls = []

    def clock():
        return next(readings)

    def aws(args):
        aws_calls.append(args[:2])
        if args[:2] == ["ec2", "describe-instances"]:
            return {"Reservations": [{"Instances": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "State": {"Name": "running"},
            }]}]}
        if args[:2] == ["ssm", "describe-instance-information"]:
            return managed_instance_response(last_ping)
        raise AssertionError(f"stale heartbeat must not reach {args[:2]}")

    with pytest.raises(PreflightError, match="stale"):
        run_deploy(
            VALID_ENV, workspace_tmp_dir, aws, clock,
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
            run_git=fake_git_returning(VALID_ENV["DEPLOY_SHA"]),
        )

    assert ["ssm", "send-command"] not in aws_calls


def test_run_deploy_reads_the_clock_only_after_each_describe_response(
        workspace_tmp_dir, fake_clock):
    # Age arithmetic is only honest if every clock read follows the describe
    # response it judges. A read taken anywhere else -- including one taken in
    # run_deploy and passed down behind a lambda, which no age assertion can
    # distinguish -- shows up here as an out-of-place "utc-now".
    _write_integration_host_script(workspace_tmp_dir)
    events = []

    def clock():
        events.append("utc-now")
        return NOW

    def aws(args):
        if args[:2] == ["ec2", "describe-instances"]:
            events.append("ec2-response")
            return {"Reservations": [{"Instances": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "State": {"Name": "running"},
            }]}]}
        if args[:2] == ["ssm", "describe-instance-information"]:
            events.append("ssm-response")
            return managed_instance_response(NOW)
        if args[:2] == ["ssm", "send-command"]:
            events.append("send-command")
            raise AwsCliError("stop after the send boundary")
        raise AssertionError(args[:2])

    with pytest.raises(AwsCliError, match="stop after the send boundary"):
        run_deploy(
            VALID_ENV, workspace_tmp_dir, aws, clock,
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
            run_git=fake_git_returning(VALID_ENV["DEPLOY_SHA"]),
        )

    assert events == [
        "ec2-response", "ssm-response", "utc-now",
        "ssm-response", "utc-now", "send-command",
    ]


def test_run_deploy_sends_when_the_heartbeat_stays_inside_the_ceiling(
        workspace_tmp_dir, fake_clock):
    # The mirror of the test above: the same advancing clock must still allow a
    # send while the heartbeat is genuinely inside the ceiling, so the refusal
    # above is attributable to age and not to the advancing clock itself.
    _write_integration_host_script(workspace_tmp_dir)
    last_ping = NOW
    readings = iter([
        NOW + timedelta(seconds=10),
        NOW + timedelta(seconds=SSM_HEARTBEAT_MAX_AGE_SECONDS),
    ])
    sent = []

    def aws(args):
        if args[:2] == ["ec2", "describe-instances"]:
            return {"Reservations": [{"Instances": [{
                "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
                "State": {"Name": "running"},
            }]}]}
        if args[:2] == ["ssm", "describe-instance-information"]:
            return managed_instance_response(last_ping)
        if args[:2] == ["ssm", "send-command"]:
            sent.append(args)
            raise AwsCliError("stop after the send boundary")
        raise AssertionError(args[:2])

    with pytest.raises(AwsCliError, match="stop after the send boundary"):
        run_deploy(
            VALID_ENV, workspace_tmp_dir, aws, lambda: next(readings),
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
            run_git=fake_git_returning(VALID_ENV["DEPLOY_SHA"]),
        )

    assert len(sent) == 1


def test_run_deploy_requires_exact_host_script_after_preflight_before_send(
        workspace_tmp_dir, fake_clock):
    aws_calls = []
    responses = iter([
        {"Reservations": [{"Instances": [{
            "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
            "State": {"Name": "running"},
        }]}]},
        {"InstanceInformationList": [{
            "InstanceId": VALID_ENV["EC2_INSTANCE_ID"],
            "PingStatus": "Online",
            "LastPingDateTime": "2026-08-22T17:58:00+00:00",
        }]},
    ])

    def aws(args):
        aws_calls.append(args)
        return next(responses)

    with pytest.raises(ConfigError, match="production_deploy.sh"):
        run_deploy(
            VALID_ENV, workspace_tmp_dir, aws,
            lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
            run_git=fake_git_returning(VALID_ENV["DEPLOY_SHA"]),
        )

    assert [call[:2] for call in aws_calls] == [
        ["ec2", "describe-instances"],
        ["ssm", "describe-instance-information"],
    ]


def test_main_returns_nonzero_when_bounded_aws_call_times_out(
        workspace_tmp_dir, fake_clock):
    _write_integration_host_script(workspace_tmp_dir)
    messages = []

    def aws(args):
        raise AwsCliError("aws ec2 describe-instances timed out after 60 seconds")

    exit_code = main(
        environ=VALID_ENV,
        repo_path=workspace_tmp_dir,
        aws=aws,
        utc_now=lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
        monotonic=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        log=messages.append,
        run_git=fake_git_returning(VALID_ENV["DEPLOY_SHA"]),
    )

    assert exit_code != 0
    assert messages == [
        "deployment failed: aws ec2 describe-instances timed out after 60 seconds",
    ]


def test_main_exits_zero_without_deploying_a_superseded_candidate(
        workspace_tmp_dir, fake_clock):
    _write_integration_host_script(workspace_tmp_dir)
    aws_calls = []
    messages = []

    exit_code = main(
        environ=VALID_ENV,
        repo_path=workspace_tmp_dir,
        aws=lambda args: aws_calls.append(args),
        utc_now=lambda: datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
        monotonic=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        log=messages.append,
        run_git=fake_git_with_history(
            "b" * 40, merge_base=VALID_ENV["DEPLOY_SHA"],
        ),
    )

    assert exit_code == 0
    assert aws_calls == []
    assert any("superseded" in message for message in messages)


def test_main_defaults_to_the_concrete_bounded_aws_json_runner(monkeypatch):
    captured = {}

    def fake_run_deploy(environ, repo_path, aws, *args, **kwargs):
        captured["aws"] = aws

    monkeypatch.setattr(deploy_control, "run_deploy", fake_run_deploy)

    exit_code = main(environ=VALID_ENV, repo_path=Path("checked-out-repository"))

    assert exit_code == 0
    assert captured["aws"] is deploy_control.run_aws_json


def test_main_passes_injected_utc_clock_without_sampling_it_early(monkeypatch):
    captured = {}

    def utc_now():
        raise AssertionError("run_deploy owns the send-time clock sample")

    def fake_run_deploy(environ, repo_path, aws, clock, *args, **kwargs):
        captured["clock"] = clock

    monkeypatch.setattr(deploy_control, "run_deploy", fake_run_deploy)

    exit_code = main(
        environ=VALID_ENV,
        repo_path=Path("checked-out-repository"),
        utc_now=utc_now,
    )

    assert exit_code == 0
    assert captured["clock"] is utc_now


# --- privileged helper object identity (deploy hardening PR1, finding 6) ----
#
# The bootstrap used to `mktemp` the helper in world-writable /tmp, `chmod 0700`
# it, and `chown` it to the deploy user, then `execve` that PATHNAME after
# dropping privilege. Between the chown and the execve the deploy user owned
# both the file and a sticky-directory entry it could unlink, so it could
# substitute arbitrary bytes and have them executed holding the outer lock
# capability and the whole transaction environment. The helper must instead be
# a root-owned, non-writable object inside a root-owned private directory that
# the deploy user can traverse and execute but never replace.


class FakeHelperOs:
    O_RDONLY = 1
    O_RDWR = 2
    O_CREAT = 4
    O_EXCL = 8
    O_NOFOLLOW = 16
    O_CLOEXEC = 32
    O_DIRECTORY = 64

    def __init__(self, *, symlink=False, exists=False, short_write=False,
                 uid=0, nlink=1, corrupt=None, swap_path_inode=False,
                 refuse_fchmod=False):
        self.symlink = symlink
        self.exists = exists
        self.short_write = short_write
        self.uid = uid
        self.nlink = nlink
        self.corrupt = corrupt
        self.swap_path_inode = swap_path_inode
        self.refuse_fchmod = refuse_fchmod
        self.open_calls = []
        self.chown_calls = []
        self.fchmod_calls = []
        self.closed = []
        self.fsync_calls = []
        self.mode = 0o500
        self.content = b""
        self.offset = 0

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        self.open_calls.append((path, flags, mode, dir_fd))
        if self.symlink:
            raise OSError("refusing symbolic link")
        if flags & self.O_EXCL and self.exists:
            raise FileExistsError(path)
        return 21

    def write(self, fd, payload):
        assert fd == 21
        if self.short_write:
            self.short_write = False
            self.content += payload[:1]
            return 1
        self.content += payload
        return len(payload)

    def lseek(self, fd, position, whence):
        assert fd == 21
        self.offset = position
        return position

    def read(self, fd, size):
        assert fd == 21
        source = self.content if self.corrupt is None else self.corrupt
        chunk = source[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def fsync(self, fd):
        self.fsync_calls.append(fd)

    def fchmod(self, fd, mode):
        self.fchmod_calls.append((fd, mode))
        if not self.refuse_fchmod:
            self.mode = mode

    def chown(self, *args, **kwargs):  # pragma: no cover - must never be called
        self.chown_calls.append((args, kwargs))

    def fstat(self, fd):
        assert fd == 21
        return SimpleNamespace(
            st_uid=self.uid, st_mode=stat.S_IFREG | self.mode,
            st_nlink=self.nlink, st_dev=1, st_ino=21,
        )

    def stat(self, path, *, dir_fd=None, follow_symlinks=True):
        assert follow_symlinks is False
        return SimpleNamespace(
            st_uid=self.uid, st_mode=stat.S_IFREG | self.mode,
            st_nlink=self.nlink, st_dev=1,
            st_ino=99 if self.swap_path_inode else 21,
        )

    def close(self, fd):
        self.closed.append(fd)


def _load_helper_materializer():
    namespace = {"__name__": "helper_materializer_test"}
    exec(deploy_control.HELPER_MATERIALIZATION_SOURCE, namespace)
    return namespace


HELPER_BYTES = b"#!/usr/bin/env bash\necho validated\n"
HELPER_SHA256 = hashlib.sha256(HELPER_BYTES).hexdigest()


def test_verified_helper_is_root_owned_unwritable_and_never_chowned():
    fake_os = FakeHelperOs()
    namespace = _load_helper_materializer()

    fd, name = namespace["create_verified_helper"](
        9, HELPER_BYTES, HELPER_SHA256, fake_os, stat, hashlib,
    )

    assert (fd, name) == (21, namespace["HELPER_NAME"])
    # Created exclusively, without following a symlink, inside the given dir.
    path, flags, mode, dir_fd = fake_os.open_calls[0]
    assert path == namespace["HELPER_NAME"] and dir_fd == 9 and mode == 0o500
    for required in (fake_os.O_CREAT, fake_os.O_EXCL, fake_os.O_NOFOLLOW,
                     fake_os.O_CLOEXEC, fake_os.O_RDWR):
        assert flags & required
    assert fake_os.content == HELPER_BYTES
    assert fake_os.fsync_calls == [21]
    # 0505: root r-x, other r-x. No write bit for anyone, no group access.
    assert fake_os.mode == 0o505
    assert fake_os.mode & 0o222 == 0
    assert fake_os.chown_calls == []


@pytest.mark.parametrize("kwargs, expected", [
    ({"corrupt": b"#!/bin/sh\necho attacker\n"}, "digest"),
    ({"uid": 1000}, "unsafe helper object"),
    ({"nlink": 2}, "unsafe helper object"),
    ({"swap_path_inode": True}, "helper identity changed"),
    ({"refuse_fchmod": True}, "helper identity changed"),
])
def test_verified_helper_rejects_unsafe_objects(kwargs, expected):
    fake_os = FakeHelperOs(**kwargs)
    namespace = _load_helper_materializer()

    with pytest.raises(OSError, match=expected):
        namespace["create_verified_helper"](
            9, HELPER_BYTES, HELPER_SHA256, fake_os, stat, hashlib,
        )
    assert fake_os.closed == [21]


def test_verified_helper_completes_a_short_write_before_hashing():
    fake_os = FakeHelperOs(short_write=True)
    namespace = _load_helper_materializer()

    namespace["create_verified_helper"](
        9, HELPER_BYTES, HELPER_SHA256, fake_os, stat, hashlib,
    )

    assert fake_os.content == HELPER_BYTES


@pytest.mark.parametrize("kwargs", [{"symlink": True}, {"exists": True}])
def test_verified_helper_refuses_a_preexisting_or_symlinked_path(kwargs):
    fake_os = FakeHelperOs(**kwargs)
    namespace = _load_helper_materializer()

    with pytest.raises(OSError):
        namespace["create_verified_helper"](
            9, HELPER_BYTES, HELPER_SHA256, fake_os, stat, hashlib,
        )
    assert fake_os.closed == []


def test_bootstrap_no_longer_hands_the_helper_object_to_the_deploy_user():
    inner_bootstrap = _decode_inner_bootstrap(
        build_remote_command(DeployConfig.from_environ(VALID_ENV), HELPER_BYTES)
    )

    # The exact race primitives are gone: no world-writable mktemp target, no
    # chmod/chown of the executed object to the unprivileged deploy user.
    assert "mktemp /tmp/fitx-deploy" not in inner_bootstrap
    assert "chmod 0700" not in inner_bootstrap
    assert f"chown -- '{VALID_ENV['DEPLOY_USER']}'" not in inner_bootstrap
    # The exact bytes the controller loaded are pinned by digest.
    assert HELPER_SHA256 in inner_bootstrap
    assert deploy_control.HELPER_MATERIALIZATION_SOURCE in inner_bootstrap
    # Root still owns the object and removes its private directory afterwards.
    assert "rm -r -f -- \"$helper_dir\"" in inner_bootstrap
# --- canonical hardened execution PATH -------------------------------------
#
# Both privileged deploy boundaries hand their child a *complete* environment,
# so PATH is deployment contract surface, not an inherited convenience.  The
# retired value below omitted the sbin directories.  Production probe evidence
# from the deploy host: `runuser` and `nginx` live in /usr/sbin and resolved
# nowhere, so the pre-mutation staleness proof and the nginx validation gate
# both failed closed -- one gate per deploy attempt, because each gate only
# becomes reachable after the previous one passes.  Enumerating every external
# command the bootstrap and the helper invoke is the point of these tests: the
# defect was systemic, not `aws`-specific.

RETIRED_HARDENED_PATH = "/usr/local/bin:/usr/bin:/bin"

# name -> the absolute directory the executable occupies on the Ubuntu deploy
# host.  Keyed by directory rather than by full path so the table states the
# only fact PATH resolution actually depends on.
REQUIRED_DEPLOY_EXECUTABLES = {
    "aws": "/usr/local/bin",
    "runuser": "/usr/sbin",
    "nginx": "/usr/sbin",
    "ss": "/usr/bin",
    "git": "/usr/bin",
    "docker": "/usr/bin",
    "timeout": "/usr/bin",
    "base64": "/usr/bin",
    "python3": "/usr/bin",
    "systemctl": "/usr/bin",
    "install": "/usr/bin",
    "grep": "/usr/bin",
    "rm": "/usr/bin",
    "seq": "/usr/bin",
    "sleep": "/usr/bin",
    "curl": "/usr/bin",
    "mktemp": "/usr/bin",
    "tar": "/usr/bin",
}

# The subset the three fail-closed production attempts actually tripped on.
SBIN_ONLY_DEPLOY_EXECUTABLES = ("runuser", "nginx")


def _mirror_deploy_host(root):
    """Materialize every required executable at its real absolute directory."""
    for name, directory in REQUIRED_DEPLOY_EXECUTABLES.items():
        target = root / directory.lstrip("/")
        target.mkdir(parents=True, exist_ok=True)
        binary = target / name
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)


def _resolve_under(root, search_path, name):
    """Resolve `name` the way execvp does: first PATH entry that holds it.

    `shutil.which` is not used deliberately -- on Windows it consults PATHEXT
    and would report a false negative for extension-less POSIX binaries, which
    would make this contract silently untested off Linux.
    """
    for entry in search_path.split(":"):
        candidate = root / entry.lstrip("/") / name
        if candidate.is_file():
            return "/" + candidate.relative_to(root).as_posix()
    return None


def test_canonical_hardened_path_is_the_standard_root_search_order():
    entries = deploy_control.HARDENED_EXECUTION_PATH.split(":")

    assert entries == [
        "/usr/local/sbin", "/usr/local/bin",
        "/usr/sbin", "/usr/bin",
        "/sbin", "/bin",
    ]
    # Every entry is absolute: a relative element is a privilege-escalation
    # primitive in a root child, and this PATH is handed to one.
    assert all(entry.startswith("/") for entry in entries)
    assert len(set(entries)) == len(entries)
    # The retired PATH is a strict subset in the same relative order, so this
    # change only ever widens resolution -- it cannot shadow a binary that
    # already resolved.
    retired = RETIRED_HARDENED_PATH.split(":")
    assert [entry for entry in entries if entry in retired] == retired


def test_required_deploy_executables_resolve_under_the_canonical_path(tmp_path):
    _mirror_deploy_host(tmp_path)

    resolved = {
        name: _resolve_under(
            tmp_path, deploy_control.HARDENED_EXECUTION_PATH, name
        )
        for name in REQUIRED_DEPLOY_EXECUTABLES
    }

    assert resolved == {
        name: f"{directory}/{name}"
        for name, directory in REQUIRED_DEPLOY_EXECUTABLES.items()
    }


def test_retired_hardened_path_could_not_resolve_the_sbin_executables(tmp_path):
    # Without this the test above passes vacuously against any PATH at all.
    # This pins the defect: exactly the sbin binaries were unresolvable, which
    # is why `aws` alone was never the whole story.
    _mirror_deploy_host(tmp_path)

    unresolved = sorted(
        name for name in REQUIRED_DEPLOY_EXECUTABLES
        if _resolve_under(tmp_path, RETIRED_HARDENED_PATH, name) is None
    )

    assert unresolved == sorted(SBIN_ONLY_DEPLOY_EXECUTABLES)


def test_both_privileged_boundaries_receive_one_canonical_path():
    # The outer root-lock child and the privilege-dropped helper are separate
    # source objects; a second hard-coded literal in either one would reopen
    # the defect while every env-dict assertion above still passed.
    boundaries = {
        "root-lock child": deploy_control.ROOT_LOCK_WRAPPER_SOURCE,
        "privilege drop": deploy_control.PRIVILEGE_DROP_SOURCE,
    }

    for label, source in boundaries.items():
        namespace = {"__name__": f"path_contract_{label}"}
        exec(source, namespace)
        assert namespace["HARDENED_PATH"] == (
            deploy_control.HARDENED_EXECUTION_PATH
        ), label
        assert RETIRED_HARDENED_PATH not in source, label
        # The PATH the child receives is the injected name, never a literal.
        assert '"PATH": HARDENED_PATH,' in source, label


def test_every_external_command_the_deploy_invokes_is_a_known_executable():
    # The systemic lesson from three fail-closed attempts: a command that does
    # not resolve is invisible until the gate before it passes.  Enumerate the
    # whole set instead of discovering it one deploy at a time.
    inner_bootstrap = _decode_inner_bootstrap(
        build_remote_command(DeployConfig.from_environ(VALID_ENV), HOST_SCRIPT)
    )
    helper_source = (
        Path(__file__).resolve().parents[1]
        / "scripts" / "production_deploy.sh"
    ).read_text(encoding="utf-8")

    root_commands = set(
        re.findall(r"root_external\s+([A-Za-z0-9_.-]+)", inner_bootstrap)
    )
    helper_commands = set(
        re.findall(r"run_external\s+([A-Za-z0-9_.-]+)", helper_source)
    )

    assert root_commands, "root bootstrap dispatches no external command"
    assert helper_commands, "host helper dispatches no external command"
    assert root_commands <= set(REQUIRED_DEPLOY_EXECUTABLES)
    assert helper_commands <= set(REQUIRED_DEPLOY_EXECUTABLES)
    # `root_external` is itself `timeout`, and these three run outside it:
    # the authority read, the authority-parameter decode, and the retry loop.
    assert "timeout --signal=TERM --kill-after=1s 4s" in inner_bootstrap
    assert "aws ssm get-parameter" in inner_bootstrap
    assert "base64 --decode" in inner_bootstrap
    assert f"seq 1 {deploy_control.AUTHORITY_WAIT_ATTEMPTS}" in inner_bootstrap
    # git runs through runuser, so it resolves against this same PATH.
    assert "runuser -u" in inner_bootstrap and "git -C" in inner_bootstrap


def test_the_gates_that_failed_in_production_now_resolve(tmp_path):
    _mirror_deploy_host(tmp_path)
    inner_bootstrap = _decode_inner_bootstrap(
        build_remote_command(DeployConfig.from_environ(VALID_ENV), HOST_SCRIPT)
    )

    # Staleness proof (exit 75) and the nginx gate (exit 1) are the two the
    # narrow PATH broke; `aws` is the one the earlier attempt surfaced.
    for name in ("runuser", "nginx", "aws", "timeout"):
        assert name in inner_bootstrap
        assert _resolve_under(
            tmp_path, deploy_control.HARDENED_EXECUTION_PATH, name
        ) is not None, name

    for name in SBIN_ONLY_DEPLOY_EXECUTABLES:
        assert _resolve_under(tmp_path, RETIRED_HARDENED_PATH, name) is None
