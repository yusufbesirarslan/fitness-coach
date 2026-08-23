import base64
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.deploy_control as deploy_control
from scripts.deploy_control import (
    AWS_CLI_CALL_TIMEOUT_SECONDS,
    AwsCliError,
    ConfigError,
    DELIVERY_TIMEOUT_SECONDS,
    DeployConfig,
    EXECUTION_TIMEOUT_SECONDS,
    INVOCATION_CALL_TIMEOUT_SECONDS,
    InvocationFailed,
    InvocationPollingTimeout,
    InvocationProtocolError,
    POLL_HORIZON_SECONDS,
    PreflightError,
    build_remote_command,
    main,
    preflight,
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
    command_lines = command.splitlines()
    arguments = shlex.split(command_lines[0].split(" <<", 1)[0])
    assert arguments[:2] == ["python3", "-"]
    assert "\n".join(command_lines[1:-1]) + "\n" == (
        deploy_control.ROOT_LOCK_WRAPPER_SOURCE
    )
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
    assert child_calls == [(["/bin/sh", "-c", "printf safe"], {"check": False})]
    assert fake_fcntl.calls == [(12, 1 | 2)]
    assert fake_os.closed == [12, 11, 10]


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

    harness = workspace_tmp_dir / "bootstrap harness"
    stubs = harness / "bin"
    stubs.mkdir(parents=True)
    deploy_dir_path = harness / "deploy '$(touch nope)"
    deploy_dir_path.mkdir()
    env_file = deploy_dir_path / ".env"
    env_chmod_marker = harness / "env-chmod-complete"
    events = harness / "events"
    events.write_text("", encoding="utf-8")
    remote_script = harness / "decoded-script"
    bash_harness = _git_bash_path(harness)

    _write_shell_stub(stubs, "python3", """
printf '%s\n' outer-lock >> "$HARNESS_DIR/events"
printf '%s\\0' "$@" > "$HARNESS_DIR/python3-args"
case "$OUTER_LOCK_MODE" in
  success) ;;
  contended|unsafe|symlink) exit 73 ;;
  *) exit 64 ;;
esac
test "$1" = '-'
shift
cat > "$HARNESS_DIR/root-lock-source"
printf '%s' "$1" | base64 --decode | /bin/sh
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
        "outer-lock", "stat", "nginx", "systemctl", "systemctl", "ss", "ss",
        "mktemp", "chmod", "id", "chown", "sudo",
    ]
    assert _read_null_arguments(harness / "mktemp-args") == ["/tmp/fitx-deploy.XXXXXX"]
    assert _read_null_arguments(harness / "chmod-args") == ["0700", bash_remote_script]
    assert _read_null_arguments(harness / "id-args") == ["-u", VALID_ENV["DEPLOY_USER"]]
    assert _read_null_arguments(harness / "chown-args") == [
        "--", VALID_ENV["DEPLOY_USER"], bash_remote_script,
    ]
    assert (harness / "sudo-user").read_text(encoding="utf-8") == VALID_ENV["DEPLOY_USER"]
    assert _read_null_arguments(harness / "sudo-args") == [
        "-u", VALID_ENV["DEPLOY_USER"], "--", "env",
        "AXISAI_OUTER_LOCK_PATH=/run/lock/axisai-production/production.lock",
        bash_remote_script,
        VALID_ENV["DEPLOY_SHA"], deploy_dir, public_url,
    ]
    assert _read_null_arguments(harness / "script-args") == [
        VALID_ENV["DEPLOY_SHA"], deploy_dir, public_url,
    ]
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
    assert events == ["outer-lock"]


def test_bootstrap_repairs_and_rechecks_env_permissions_before_helper(
        bootstrap_harness):
    completed = bootstrap_harness.run(env_permissions="644")
    events = bootstrap_harness.events.read_text(encoding="utf-8").splitlines()

    assert completed.returncode == 0, completed.stderr
    assert events[:4] == ["outer-lock", "stat", "chmod", "stat"]
    assert "sudo" in events


def test_bootstrap_unrepairable_env_permissions_fail_before_helper(
        bootstrap_harness):
    completed = bootstrap_harness.run(env_permissions="644", env_chmod_exit=5)
    events = bootstrap_harness.events.read_text(encoding="utf-8").splitlines()

    assert completed.returncode != 0
    assert "sudo" not in events
    assert "nginx" not in events


def test_bootstrap_nginx_failure_stops_before_systemctl_and_helper(bootstrap_harness):
    completed = bootstrap_harness.run(nginx_exit=1)
    events = bootstrap_harness.events.read_text(encoding="utf-8").splitlines()

    assert completed.returncode != 0
    assert "nginx configuration test failed" in completed.stderr
    assert "systemctl" not in events
    assert "sudo" not in events


def test_bootstrap_warns_for_port_30000_without_treating_it_as_fatsecret(
        bootstrap_harness):
    completed = bootstrap_harness.run(
        fatsecret_listener="LISTEN 0 128 127.0.0.1:30000 0.0.0.0:*",
    )
    events = bootstrap_harness.events.read_text(encoding="utf-8").splitlines()

    assert completed.returncode == 0, completed.stderr
    assert "WARNING: fatsecret proxy is not listening on 127.0.0.1:3000" in completed.stdout
    assert "sudo" in events


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


@pytest.mark.parametrize("completed,error_pattern", [
    (FakeAwsCompletedProcess(returncode=7, stderr="denied"), "failed with exit code 7"),
    (FakeAwsCompletedProcess(stdout="not-json"), "invalid JSON"),
    (FakeAwsCompletedProcess(stdout="[]"), "JSON object"),
])
def test_aws_json_runner_fails_closed_on_cli_and_json_errors(completed, error_pattern):
    with pytest.raises(AwsCliError, match=error_pattern):
        run_aws_json(["ec2", "describe-instances"], run=lambda args, **kwargs: completed)


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
            return invocation_response(
                "Success", response_code=0,
                stdout="exact deploy complete\n", stderr="bounded warning\n",
            )
        raise AssertionError(f"unexpected AWS call: {args}")

    messages = []
    result = run_deploy(
        VALID_ENV, workspace_tmp_dir, aws, now,
        fake_clock.monotonic, fake_clock.sleep, messages.append,
        run_git=run_git,
    )

    assert result.status_details == "Success"
    assert events == [
        "git:fetch", "git:cat-file", "git:rev-parse",
        "aws:ec2:describe-instances",
        "aws:ssm:describe-instance-information",
        "aws:ssm:send-command",
        "aws:ssm:get-command-invocation",
    ]
    remote_command = sent_parameters[0]["commands"][0]
    remote_lines = remote_command.splitlines()
    remote_arguments = shlex.split(remote_lines[0].split(" <<", 1)[0])
    assert remote_arguments[:2] == ["python3", "-"]
    inner_bootstrap = base64.b64decode(
        remote_arguments[2], validate=True,
    ).decode("utf-8")
    assert base64.b64encode(exact_script).decode("ascii") in inner_bootstrap
    assert (
        f"sudo -u '{VALID_ENV['DEPLOY_USER']}' -- env "
        "AXISAI_OUTER_LOCK_PATH=/run/lock/axisai-production/production.lock"
    ) in inner_bootstrap
    assert (
        "\"$script_path\" "
        f"'{VALID_ENV['DEPLOY_SHA']}' \"$deploy_dir\" \"$public_health_url\""
    ) in inner_bootstrap
    assert messages[-5:] == [
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
            datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
            run_git=fake_git_returning("b" * 40),
        )

    assert aws_calls == []


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
            datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
            fake_clock.monotonic, fake_clock.sleep, lambda message: None,
            run_git=fake_git_returning(VALID_ENV["DEPLOY_SHA"]),
        )

    assert [call[:2] for call in aws_calls] == [["ec2", "describe-instances"]]


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
            datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc),
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
