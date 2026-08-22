from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.deploy_control import (
    AwsCliError,
    ConfigError,
    DeployConfig,
    PreflightError,
    preflight,
    validate_candidate,
)


VALID_ENV = {
    "DEPLOY_SHA": "a" * 40,
    "AWS_REGION": "eu-central-1",
    "EC2_INSTANCE_ID": "i-0c6f5352fc214e68d",
    "DEPLOY_USER": "deploy",
    "DEPLOY_DIR": "/srv/axisai",
    "PUBLIC_HEALTH_URL": "https://fitness.example/health",
}


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


@pytest.mark.parametrize("url", ["http://fitness.example/health", "https://", "https://deploy@fitness.example/health", "https://:secret@fitness.example/health"])
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
