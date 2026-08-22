import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.deploy_control import (
    AwsCliError,
    ConfigError,
    DELIVERY_TIMEOUT_SECONDS,
    DeployConfig,
    EXECUTION_TIMEOUT_SECONDS,
    InvocationFailed,
    InvocationPollingTimeout,
    InvocationProtocolError,
    POLL_HORIZON_SECONDS,
    PreflightError,
    build_remote_command,
    preflight,
    read_invocation,
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


@pytest.mark.parametrize("deploy_dir", ["srv/axisai", "", "/srv/axisai\nother"])
def test_config_rejects_nonabsolute_or_multiline_deploy_dir(deploy_dir):
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


def test_validate_candidate_fetches_and_checks_the_current_origin_main():
    run_git = fake_git_returning("a" * 40)

    validate_candidate(Path("/candidate"), "a" * 40, run_git=run_git)

    assert run_git.calls[0][-4:] == ["fetch", "origin", "main", "--prune"]
    assert run_git.calls[1][-3:] == ["cat-file", "-e", ("a" * 40) + "^{commit}"]
    assert run_git.calls[2][-2:] == ["rev-parse", "refs/remotes/origin/main"]


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

    managed = preflight(DeployConfig.from_environ(VALID_ENV), aws, now)

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
            datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
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
            datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
        )


def test_preflight_rejects_ambiguous_ec2_results():
    aws = lambda args: {"Reservations": [{"Instances": []}]}

    with pytest.raises(PreflightError, match="exactly one EC2"):
        preflight(DeployConfig.from_environ(VALID_ENV), aws, datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc))


def test_preflight_propagates_aws_cli_errors():
    def aws(args):
        raise AwsCliError("aws cli failed")

    with pytest.raises(AwsCliError, match="aws cli failed"):
        preflight(DeployConfig.from_environ(VALID_ENV), aws, datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc))


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
            datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
        )


def test_send_payload_separates_delivery_and_execution_timeout():
    calls = []

    def aws(args):
        calls.append(args)
        return {"Command": {"CommandId": "11111111-1111-1111-1111-111111111111"}}

    command_id = send_command(DeployConfig.from_environ(VALID_ENV), b"echo safe", aws)

    assert command_id == "11111111-1111-1111-1111-111111111111"
    args = calls[0]
    assert args[args.index("--timeout-seconds") + 1] == str(DELIVERY_TIMEOUT_SECONDS) == "60"
    parameters = json.loads(args[args.index("--parameters") + 1])
    assert parameters["executionTimeout"] == [str(EXECUTION_TIMEOUT_SECONDS)] == ["1800"]
    assert args[:2] == ["ssm", "send-command"]
    assert args[args.index("--instance-ids") + 1] == VALID_ENV["EC2_INSTANCE_ID"]


def test_remote_command_encodes_script_and_untrusted_positional_values():
    script = b"#!/bin/sh\nprintf '%s\\n' \"$1\""
    deploy_dir = "/srv/axis ai/'$(touch nope)"
    public_url = "https://fitness.example/health?probe='$(touch%20nope)"
    config = DeployConfig.from_environ({
        **VALID_ENV,
        "DEPLOY_DIR": deploy_dir,
        "PUBLIC_HEALTH_URL": public_url,
    })

    command = build_remote_command(config, script)

    assert script.decode() not in command
    assert deploy_dir not in command
    assert public_url not in command
    assert base64.b64encode(script).decode("ascii") in command
    assert base64.b64encode(deploy_dir.encode()).decode("ascii") in command
    assert base64.b64encode(public_url.encode()).decode("ascii") in command


@pytest.mark.parametrize("response", [
    None,
    [],
    {},
    {"Command": {}},
    {"Command": {"CommandId": None}},
    {"Command": {"CommandId": "command-id"}},
])
def test_send_command_requires_exactly_one_uuid_command_id(response):
    with pytest.raises(InvocationProtocolError):
        send_command(DeployConfig.from_environ(VALID_ENV), b"echo safe", lambda args: response)


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


def test_in_progress_is_first_execution_proof(fake_clock):
    aws = invocation_sequence("Pending", "Delayed", "InProgress", "Success")
    messages = []

    result = wait_for_invocation(
        DeployConfig.from_environ(VALID_ENV), "command-id", aws,
        fake_clock.monotonic, fake_clock.sleep, messages.append,
    )

    assert result.status_details == "Success"
    assert sum("host execution started" in message for message in messages) == 1
    assert messages.index("host execution started") > messages.index("SSM status: Delayed")


def test_spaced_status_is_normalized_only_for_comparison(fake_clock):
    messages = []

    result = wait_for_invocation(
        DeployConfig.from_environ(VALID_ENV), "command-id",
        invocation_sequence("In Progress", "Success"),
        fake_clock.monotonic, fake_clock.sleep, messages.append,
    )

    assert result.status_details == "Success"
    assert messages[:2] == ["SSM status: In Progress", "host execution started"]


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
