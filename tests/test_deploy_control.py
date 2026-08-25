import base64
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


def test_send_rechecks_online_heartbeat_at_actual_send_boundary():
    config = DeployConfig.from_environ(VALID_ENV)
    clock = iter((NOW, NOW + timedelta(seconds=61)))
    calls = []

    def aws(args):
        calls.append(args)
        if args[:2] == ["ssm", "describe-instance-information"]:
            sampled = next(clock)
            return managed_instance_response(sampled - timedelta(seconds=360))
        if args[:2] == ["ssm", "send-command"]:
            return {"Command": {"CommandId": COMMAND_ID}}
        raise AssertionError(args)

    assert send_command(
        config, HOST_SCRIPT, aws, AUTHORITY_TOKEN, utc_now=lambda: NOW,
    ) == COMMAND_ID
    assert [call[1] for call in calls] == [
        "describe-instance-information", "send-command",
    ]


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
    assert events == [
        "remote-command construction", "JSON construction", "freshness response",
        "send-command",
    ]


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


def test_main_normalizes_a_frozen_now_override_into_a_callable(monkeypatch):
    captured = {}

    def fake_run_deploy(environ, repo_path, aws, clock, *args, **kwargs):
        captured["clock"] = clock

    monkeypatch.setattr(deploy_control, "run_deploy", fake_run_deploy)

    assert main(
        environ=VALID_ENV,
        repo_path=Path("checked-out-repository"),
        now=NOW,
    ) == 0

    assert callable(captured["clock"])
    assert captured["clock"]() == NOW


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
    assert "timeout --signal=TERM --kill-after=1s 4s" in build_remote_command(
        DeployConfig.from_environ(VALID_ENV), b"exit 0\n",
    ) or "timeout --signal=TERM --kill-after=1s 4s" in base64.b64decode(
        shlex.split(build_remote_command(
            DeployConfig.from_environ(VALID_ENV), b"exit 0\n",
        ).splitlines()[0].split(" <<", 1)[0])[2], validate=True,
    ).decode("utf-8")
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
            "PATH": "/usr/local/bin:/usr/bin:/bin",
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
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "AXISAI_OUTER_LOCK_FD": "7",
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
    env_file: Path
    env_chmod_marker: Path
    config: DeployConfig
    script: bytes

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
    ):
        self.events.write_text("", encoding="utf-8")
        self.env_chmod_marker.unlink(missing_ok=True)
        if env_exists:
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
        }
        return subprocess.run(
            [
                str(self.bash), "--noprofile", "--norc", "-c",
                f"export PATH={shlex.quote(f'{bash_stubs}:/usr/bin:/bin')}\n"
                + build_remote_command(self.config, self.script),
            ],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )


@pytest.fixture
def bootstrap_harness(workspace_tmp_dir):
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git Bash is unavailable")
    git_bash = Path(git).parent.parent / "bin" / "bash.exe"
    if not git_bash.is_file():
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
    remote_script = harness / "decoded-script"
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
  printf '%s' "$1" | base64 --decode | AXISAI_ROOT_LOCK_FD=7 /bin/sh
elif [ "$#" = 3 ] && [ "${2#"$DEPLOY_SHA:"}" != "$2" ]; then
  cat > "$HARNESS_DIR/authority-value-source"
  exit 0
elif [ "$#" = 3 ]; then
  printf '%s\n' env-guard >> "$HARNESS_DIR/events"
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
    printf '%s\n' "$DEPLOY_SHA:11111111-1111-1111-1111-111111111111"
    ;;
  *) exit 64 ;;
esac
""")

    _write_shell_stub(stubs, "mktemp", """
printf '%s\n' mktemp >> "$HARNESS_DIR/events"
printf '%s\\0' "$@" > "$HARNESS_DIR/mktemp-args"
printf '%s\n' "$HARNESS_DIR/decoded-script"
""")
    _write_shell_stub(stubs, "chmod", """
printf '%s\n' chmod >> "$HARNESS_DIR/events"
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
  reload) exit "$SYSTEMCTL_ACTION_EXIT" ;;
  enable) exit "$SYSTEMCTL_ACTION_EXIT" ;;
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
        env_file=env_file,
        env_chmod_marker=env_chmod_marker,
        config=config,
        script=script,
    )


@pytest.mark.parametrize("remote_exit_code", [0, 23])
def test_bootstrap_holds_lock_through_safeguards_and_helper_cleanup(
        bootstrap_harness, remote_exit_code):
    completed = bootstrap_harness.run(remote_exit_code=remote_exit_code)
    harness = bootstrap_harness.root
    bash_remote_script = f"{_git_bash_path(harness)}/decoded-script"
    deploy_dir = _git_bash_path(bootstrap_harness.deploy_dir)
    public_url = bootstrap_harness.config.public_health_url

    assert completed.returncode == remote_exit_code, completed.stderr
    assert (harness / "events").read_text(encoding="utf-8").splitlines() == [
        "outer-lock", "env-guard", "nginx", "systemctl", "systemctl", "ss", "ss",
        "mktemp", "chmod", "chown", "privilege-drop",
    ]
    assert _read_null_arguments(harness / "mktemp-args") == ["/tmp/fitx-deploy.XXXXXX"]
    assert _read_null_arguments(harness / "chmod-args") == ["0700", bash_remote_script]
    assert _read_null_arguments(harness / "chown-args") == [
        "--", VALID_ENV["DEPLOY_USER"], bash_remote_script,
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
    assert events == ["outer-lock", "env-guard"]


def test_bootstrap_repairs_and_rechecks_env_permissions_before_helper(
        bootstrap_harness):
    completed = bootstrap_harness.run(env_permissions="644")
    events = bootstrap_harness.events.read_text(encoding="utf-8").splitlines()

    assert completed.returncode == 0, completed.stderr
    assert events[:2] == ["outer-lock", "env-guard"]
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
    assert "aws ssm get-parameter" in remote_command
    assert remote_command.index("aws ssm get-parameter") < remote_command.index(root_wrapper_line)
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
        now=datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
        monotonic=fake_clock.monotonic,
        sleep=fake_clock.sleep,
        log=messages.append,
        run_git=fake_git_returning(VALID_ENV["DEPLOY_SHA"]),
    )

    assert exit_code != 0
    assert messages == [
        "deployment failed: aws ec2 describe-instances timed out after 60 seconds",
    ]


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
