"""scripts/deploy_email_lambda.py — fail-closed CustomEmailSender deploy wrapper.

Raw `sam deploy` without ResendApiKey deploys '' (every code email silently
stops) and without AlarmEmail removes the alarm subscription. These tests pin
the wrapper's guards hermetically: `sam` and `git` are fake runners, no AWS,
no SAM, no network. The secret is a fake sentinel and is asserted ABSENT from
every byte the wrapper prints. The git integrity section at the end runs the
production integrity helpers against REAL temporary git repositories (local
`git init` + commits in tmp_path; SAM stays fake).

    python -m pytest tests/test_deploy_email_lambda.py -v
"""
import ast
import importlib.util
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path("scripts/deploy_email_lambda.py")
_SPEC = importlib.util.spec_from_file_location("deploy_email_lambda", _SCRIPT)
deploy = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("deploy_email_lambda", deploy)  # dataclasses resolve the module
_SPEC.loader.exec_module(deploy)

SENTINEL = "re_FAKE_SENTINEL_never_real_0123456789"
ALARM = "ops-alarms@example.com"
SHA = "a" * 40
ROLLBACK_SHA = "b" * 40
SOURCE = Path("infra/cognito-email-sender").resolve()
REPO = _SCRIPT.resolve().parents[1]
READ_ONLY_GIT = {"rev-parse", "ls-files", "status"}


class FakeRunner:
    """Records every child process; answers git and sam deterministically.

    git: a directory under ``.../infra/cognito-email-sender`` or ``.../scripts``
    belongs to the work tree two/one levels up; the wrapper's tree is REPO.
    """

    def __init__(self, *, sha=SHA, wrapper_sha=SHA, status_before="", status_after="",
                 wrapper_status="", wrapper_tracked=True, fail=None, raise_on=None):
        self.calls = []
        self.sha = sha
        self.wrapper_sha = wrapper_sha
        self.statuses = [status_before, status_after]
        self.wrapper_status = wrapper_status
        self.wrapper_tracked = wrapper_tracked
        self.fail = fail or {}
        self.raise_on = raise_on

    @staticmethod
    def _toplevel(directory):
        if directory.parts[-2:] == ("infra", "cognito-email-sender"):
            return directory.parents[1]
        return directory.parent if directory.name == "scripts" else directory

    def _git(self, argv):
        directory, args = Path(argv[4]), argv[5:]
        ok = lambda out: subprocess.CompletedProcess(argv, 0, out, "")  # noqa: E731
        if args[:2] == ["rev-parse", "--show-toplevel"]:
            return ok(f"{self._toplevel(directory)}\n")
        if args == ["rev-parse", "HEAD"]:
            return ok((self.wrapper_sha if directory == REPO and self.wrapper_sha else self.sha) + "\n")
        path = args[-1]
        if args[0] == "ls-files":
            if path == deploy.WRAPPER_REPO_PATH:
                return ok(f"H {path}\0" if self.wrapper_tracked else "")
            return ok("".join(f"H {path}/{name}\0" for name in deploy.CANONICAL_SOURCE_FILES))
        if path == deploy.WRAPPER_REPO_PATH:
            return ok(self.wrapper_status)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return ok(status)

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if argv[0] == "git":
            return self._git(list(argv))
        step = argv[1]
        if step == self.raise_on:
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, self.fail.get(step, 0))

    def sam_steps(self):
        return [argv[1] for argv, _ in self.calls if argv[0] == "sam"]

    def sam_call(self, step):
        return next((argv, kw) for argv, kw in self.calls
                    if argv[0] == "sam" and argv[1] == step)


def _env(**over):
    env = {"RESEND_API_KEY": SENTINEL, "PATH": "/usr/bin", "SAM_DEBUG": "1",
           "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-east-1"}
    env.update(over)
    return {k: v for k, v in env.items() if v is not None}


def _argv(*extra, stack="axisai-cognito-email-sender", region="eu-central-1",
          alarm=ALARM, source=SOURCE):
    argv = ["--stack-name", stack, "--region", region, "--source-dir", str(source)]
    if alarm is not None:
        argv += ["--alarm-email", alarm]
    return argv + list(extra)


def _main(argv, *, env=None, runner=None, tty=True, capsys=None):
    runner = runner or FakeRunner()
    out = io.StringIO()
    code = deploy.main(argv, environ=_env() if env is None else env, run=runner,
                       stdin_isatty=lambda: tty, out=out)
    err = capsys.readouterr().err if capsys else ""
    return code, out.getvalue(), err, runner


# --- 1/2: ResendApiKey ------------------------------------------------------

def test_missing_resend_api_key_refuses_before_sam(capsys):
    code, out, err, runner = _main(_argv(), env=_env(RESEND_API_KEY=None), capsys=capsys)
    assert code == 2
    assert runner.calls == []
    assert "RESEND_API_KEY is not set" in err


@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_empty_or_whitespace_resend_api_key_refuses(value, capsys):
    code, _, err, runner = _main(_argv(), env=_env(RESEND_API_KEY=value), capsys=capsys)
    assert code == 2
    assert runner.calls == []
    assert "empty or whitespace-only" in err


@pytest.mark.parametrize("value", [
    "<RESEND_API_KEY>", f" {SENTINEL}", f"{SENTINEL} ", "re_a b_c_d_e", 're_"quoted"', "short",
])
def test_malformed_resend_api_key_refuses_without_echoing_it(value, capsys):
    code, out, err, runner = _main(_argv(), env=_env(RESEND_API_KEY=value), capsys=capsys)
    assert code == 2
    assert runner.calls == []
    assert value.strip() not in out + err


# --- 3/4: AlarmEmail --------------------------------------------------------

def test_missing_alarm_email_refuses_before_sam(capsys):
    code, _, err, runner = _main(_argv(alarm=None), capsys=capsys)
    assert code == 2
    assert runner.calls == []
    assert "AlarmEmail is required" in err


def test_alarm_email_may_come_from_environment():
    code, out, _, runner = _main(_argv("--dry-run", alarm=None), env=_env(ALARM_EMAIL=ALARM))
    assert code == 0
    assert "o***@example.com" in out


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_alarm_email_refuses(value, capsys):
    code, _, err, runner = _main(_argv(alarm=value), capsys=capsys)
    assert code == 2
    assert runner.calls == []
    assert "AlarmEmail is empty" in err


def test_empty_alarm_email_from_environment_refuses(capsys):
    code, _, _, runner = _main(_argv(alarm=None), env=_env(ALARM_EMAIL=""), capsys=capsys)
    assert code == 2
    assert runner.calls == []


@pytest.mark.parametrize("value", [
    "not-an-email", "a@b", "@example.com", "ops@", "ops @example.com",
    "ops@example.com,evil@example.com", "ops@example.com AlarmEmail=x",
    "<alarm-alacak-adres>", "a" * 250 + "@example.com",
])
def test_malformed_alarm_email_refuses(value, capsys):
    code, _, err, runner = _main(_argv(alarm=value), capsys=capsys)
    assert code == 2
    assert runner.calls == []
    assert "not a plain email address" in err


# --- 5/6: stack and region --------------------------------------------------

@pytest.mark.parametrize("stack", ["axisai-cognito-email-sender-staging", "other", ""])
def test_wrong_stack_refuses(stack, capsys):
    code, _, err, runner = _main(_argv(stack=stack), capsys=capsys)
    assert code == 2
    assert runner.calls == []
    assert "--stack-name must be" in err


@pytest.mark.parametrize("region", ["us-east-1", "eu-west-1", ""])
def test_wrong_region_refuses(region, capsys):
    code, _, err, runner = _main(_argv(region=region), capsys=capsys)
    assert code == 2
    assert runner.calls == []
    assert "--region must be" in err


@pytest.mark.parametrize("flag", ["--stack-name", "--region"])
def test_stack_and_region_must_be_explicit(flag):
    argv = _argv()
    index = argv.index(flag)
    del argv[index:index + 2]
    with pytest.raises(SystemExit) as exc:
        _main(argv)
    assert exc.value.code == 2


def test_region_is_pinned_on_every_sam_call_and_child_environment():
    code, _, _, runner = _main(_argv())
    assert code == 0
    validate, validate_kw = runner.sam_call("validate")
    deploy_argv, deploy_kw = runner.sam_call("deploy")
    for argv in (validate, deploy_argv):
        assert argv[argv.index("--region") + 1] == "eu-central-1"
    for _, kwargs in [c for c in runner.calls if c[0][0] == "sam"]:
        assert kwargs["env"]["AWS_REGION"] == "eu-central-1"
        assert kwargs["env"]["AWS_DEFAULT_REGION"] == "eu-central-1"
    assert deploy_argv[deploy_argv.index("--stack-name") + 1] == "axisai-cognito-email-sender"


# --- 7/8: validate and build gate the deploy --------------------------------

def test_failed_validate_stops_before_build_and_deploy():
    code, out, _, runner = _main(_argv(), runner=FakeRunner(fail={"validate": 1}))
    assert code == 3
    assert runner.sam_steps() == ["validate"]
    assert "nothing was built or deployed" in out


def test_failed_build_stops_before_deploy():
    code, out, _, runner = _main(_argv(), runner=FakeRunner(fail={"build": 1}))
    assert code == 3
    assert runner.sam_steps() == ["validate", "build"]
    assert "nothing was deployed" in out


def test_build_that_cannot_start_stops_before_deploy():
    code, _, _, runner = _main(_argv(), runner=FakeRunner(raise_on="build"))
    assert code == 3
    assert runner.sam_steps() == ["validate", "build"]


def test_build_uses_container_and_validate_uses_lint():
    _, _, _, runner = _main(_argv())
    validate, _ = runner.sam_call("validate")
    build, _ = runner.sam_call("build")
    assert validate[:3] == ["sam", "validate", "--lint"]
    assert build[:3] == ["sam", "build", "--use-container"]


# --- 9: valid inputs --------------------------------------------------------

def test_valid_inputs_produce_the_parameterized_deploy_invocation():
    code, _, _, runner = _main(_argv("--profile", "prod-operator",
                                     "--preserve-parameter", "AppBaseUrl=https://www.axisaiapp.com"))
    assert code == 0
    assert runner.sam_steps() == ["validate", "build", "deploy"]
    argv, kwargs = runner.sam_call("deploy")
    assert argv == [
        "sam", "deploy",
        "--template-file", ".aws-sam/build/template.yaml",
        "--stack-name", "axisai-cognito-email-sender",
        "--region", "eu-central-1", "--profile", "prod-operator",
        "--capabilities", "CAPABILITY_IAM",
        "--resolve-s3",
        "--s3-prefix", "axisai-cognito-email-sender",
        "--confirm-changeset",
        "--parameter-overrides",
        f"ResendApiKey={SENTINEL}",
        f"AlarmEmail={ALARM}",
        "AppBaseUrl=https://www.axisaiapp.com",
    ]
    assert kwargs["cwd"] == str(SOURCE)
    assert kwargs["env"]["AWS_PROFILE"] == "prod-operator"
    assert "shell" not in kwargs


def test_deploy_never_offers_guided_debug_or_unconfirmed_changesets():
    _, _, _, runner = _main(_argv())
    argv, _ = runner.sam_call("deploy")
    assert "--confirm-changeset" in argv
    for forbidden in ("--guided", "--debug", "--no-confirm-changeset",
                      "--no-execute-changeset", "--save-params", "--force-upload"):
        assert forbidden not in argv


def test_non_interactive_stdin_refuses_before_sam(capsys):
    code, _, err, runner = _main(_argv(), tty=False, capsys=capsys)
    assert code == 2
    assert runner.sam_steps() == []
    assert "not a terminal" in err


def test_dry_run_never_invokes_sam():
    code, out, _, runner = _main(_argv("--dry-run"), tty=False)
    assert code == 0
    assert runner.sam_steps() == []
    assert "DRY RUN" in out


def test_failed_deploy_is_reported_nonzero_with_verification_steps():
    code, out, _, _ = _main(_argv(), runner=FakeRunner(fail={"deploy": 1}))
    assert code == 4
    assert "REQUIRED post-deploy verification" in out


@pytest.mark.parametrize("item", [
    "ResendApiKey=x", "AlarmEmail=ops@example.com", "Unknown=x", "UserPoolId",
    "UserPoolId=has space", "EmailFromName=\"quoted\"",
])
def test_preserve_parameter_is_allowlisted_and_bounded(item, capsys):
    code, _, _, runner = _main(_argv("--preserve-parameter", item), capsys=capsys)
    assert code == 2
    assert runner.calls == []


def test_preserve_parameter_twice_refuses(capsys):
    code, _, _, _ = _main(_argv("--preserve-parameter", "UserPoolId=a",
                                "--preserve-parameter", "UserPoolId=b"), capsys=capsys)
    assert code == 2


# --- tracked config / template / source tree contract ------------------------

def _copy_source(tmp_path):
    target = tmp_path / "rollback" / "infra" / "cognito-email-sender"
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns(".aws-sam", "__pycache__"))
    return target


@pytest.mark.parametrize("old,new", [
    ('stack_name = "axisai-cognito-email-sender"', 'stack_name = "other-stack"'),
    ('region = "eu-central-1"', 'region = "us-east-1"'),
    ("confirm_changeset = true", "confirm_changeset = false"),
    ("image_repositories = []", 'image_repositories = []\nparameter_overrides = "ResendApiKey=x"'),
    ("[default.global.parameters]", "[prod.deploy.parameters]\nstack_name = \"x\"\n\n[default.global.parameters]"),
])
def test_samconfig_drift_refuses(tmp_path, old, new, capsys):
    source = _copy_source(tmp_path)
    config = source / "samconfig.toml"
    text = config.read_text(encoding="utf-8")
    assert old in text
    config.write_text(text.replace(old, new), encoding="utf-8")
    code, _, err, runner = _main(_argv(source=source), capsys=capsys)
    assert code == 2
    assert runner.sam_steps() == []
    assert "samconfig.toml" in err


def test_template_without_noecho_secret_refuses(tmp_path, capsys):
    source = _copy_source(tmp_path)
    template = source / "template.yaml"
    text = template.read_text(encoding="utf-8")
    template.write_text(text.replace("NoEcho: true", "NoEcho: false", 1), encoding="utf-8")
    code, _, err, runner = _main(_argv(source=source), capsys=capsys)
    assert code == 2
    assert runner.sam_steps() == []
    assert "NoEcho" in err


def test_template_parameter_drift_refuses(tmp_path, capsys):
    source = _copy_source(tmp_path)
    template = source / "template.yaml"
    text = template.read_text(encoding="utf-8")
    template.write_text(text.replace("  EmailReplyTo:", "  NewParam:\n    Type: String\n  EmailReplyTo:", 1),
                        encoding="utf-8")
    code, _, err, runner = _main(_argv(source=source), capsys=capsys)
    assert code == 2
    assert "NewParam" in err


def test_dirty_source_tree_refuses(capsys):
    code, _, err, runner = _main(_argv(), runner=FakeRunner(status_before=" M template.yaml"),
                                 capsys=capsys)
    assert code == 2
    assert runner.sam_steps() == []
    assert "uncommitted changes" in err


def test_dirty_wrapper_refuses_before_sam(capsys):
    code, out, err, runner = _main(_argv(), runner=FakeRunner(wrapper_status=" M scripts/deploy_email_lambda.py"),
                                   capsys=capsys)
    assert code == 2
    assert runner.sam_steps() == []
    assert "scripts/deploy_email_lambda.py has uncommitted changes" in err
    assert "(clean)" not in out


def test_untracked_wrapper_refuses_before_sam(capsys):
    code, _, err, runner = _main(_argv(), runner=FakeRunner(wrapper_tracked=False), capsys=capsys)
    assert code == 2
    assert runner.sam_steps() == []
    assert "not tracked by git" in err


def test_source_tree_changed_during_deploy_is_flagged():
    code, out, _, _ = _main(_argv(), runner=FakeRunner(status_after=" M samconfig.toml"))
    assert code == 5
    assert "do NOT commit it" in out


def test_tracked_samconfig_and_template_satisfy_the_contract():
    deploy.check_samconfig(SOURCE / "samconfig.toml")
    deploy.check_template(SOURCE / "template.yaml")
    assert SENTINEL not in (SOURCE / "samconfig.toml").read_text(encoding="utf-8")


# --- 10: the secret never leaves argv ---------------------------------------

@pytest.mark.parametrize("runner_factory", [
    lambda: FakeRunner(),
    lambda: FakeRunner(fail={"validate": 1}),
    lambda: FakeRunner(fail={"build": 1}),
    lambda: FakeRunner(fail={"deploy": 1}),
    lambda: FakeRunner(raise_on="deploy"),
    lambda: FakeRunner(status_after="?? leaked"),
])
def test_secret_is_absent_from_all_output(runner_factory, capsys):
    code, out, err, runner = _main(_argv(), runner=runner_factory(), capsys=capsys)
    printed = out + err + capsys.readouterr().out
    assert SENTINEL not in printed
    assert code in (0, 3, 4, 5)


def test_secret_reaches_only_the_deploy_argv_and_no_child_environment():
    _, _, _, runner = _main(_argv())
    holders = [argv for argv, _ in runner.calls if any(SENTINEL in part for part in argv)]
    assert holders == [runner.sam_call("deploy")[0]]
    for _, kwargs in runner.calls:
        env = kwargs.get("env")
        if env is not None:
            assert SENTINEL not in env.values()
            assert "RESEND_API_KEY" not in env
            assert "SAM_DEBUG" not in env
            assert env["SAM_CLI_TELEMETRY"] == "0"


def test_secret_is_absent_from_reprs_and_redacted_command():
    plan, secret = deploy.build_plan(deploy.parse_args(_argv()), _env(), FakeRunner())
    assert SENTINEL not in repr(secret) + str(secret) + f"{secret}"
    assert SENTINEL not in repr(plan)
    shown = " ".join(plan.redacted_deploy_argv())
    assert SENTINEL not in shown
    assert "ResendApiKey=<redacted>" in shown
    assert ALARM not in shown


# --- 11: operator preview ---------------------------------------------------

def test_operator_preview_names_target_alarm_and_allowed_changeset():
    _, out, _, _ = _main(_argv("--profile", "prod-operator"))
    preview = out[:out.index("--> sam deploy")]
    assert "Stack           : axisai-cognito-email-sender" in preview
    assert "Region          : eu-central-1" in preview
    assert f"Wrapper revision: {SHA} (clean)" in preview
    assert f"Source revision : {SHA} (clean)" in preview
    assert "revisions differ" not in preview
    assert "AWS identity    : --profile prod-operator" in preview
    assert "AlarmEmail      : o***@example.com" in preview
    assert ALARM not in preview
    assert "ResendApiKey    : supplied (<redacted>)" in preview
    assert "Modify  EmailSenderFunction  AWS::Lambda::Function  Replacement=False" in preview
    for resource in ("EmailKmsKey", "EmailKmsAlias", "CognitoInvokePermission",
                     "EmailSenderFunctionRole", "EmailAlarmSubscription",
                     "EmailFailureMetricFilter", "EmailFailureAlarm",
                     "EmailLambdaErrorsAlarm", "EmailLambdaThrottlesAlarm"):
        assert resource in preview
    assert "LambdaConfig" in preview


def test_preview_is_printed_after_build_and_immediately_before_deploy():
    _, out, _, _ = _main(_argv())
    assert out.index("--> sam build") < out.index("operator preview") < out.index("--> sam deploy")


def test_post_deploy_checklist_requires_lambda_stack_pool_and_sns_checks():
    _, out, _, _ = _main(_argv())
    checklist = out[out.index("REQUIRED post-deploy verification"):]
    assert "scripts/check_email_lambda.py --function-name" in checklist
    assert "describe-stacks" in checklist
    assert "UserPool.LambdaConfig" in checklist
    assert "list-subscriptions-by-topic" in checklist
    assert "get-function-configuration without --query" in checklist


# --- 12: rollback worktree --------------------------------------------------

def test_rollback_worktree_uses_its_own_tree_and_never_selects_a_revision(tmp_path):
    source = _copy_source(tmp_path)
    rollback_root = source.resolve().parents[1]
    runner = FakeRunner(sha=ROLLBACK_SHA)
    code, out, _, _ = _main(_argv(source=source), runner=runner)
    assert code == 0
    assert f"Wrapper revision: {SHA} (clean)" in out
    assert f"Source revision : {ROLLBACK_SHA} (clean)" in out
    assert "wrapper and source revisions differ" in out
    assert f"Source revision deployed: {ROLLBACK_SHA}" in out
    assert f"Wrapper revision used   : {SHA}" in out
    for argv, kwargs in runner.calls:
        if argv[0] == "sam":
            assert kwargs["cwd"] == str(source.resolve())
        else:
            assert argv[:3] == ["git", "--no-optional-locks", "--literal-pathspecs"]
            assert argv[3] == "-C"
            assert argv[4] in {str(source.resolve()), str(rollback_root),
                               str(REPO / "scripts"), str(REPO)}
            assert argv[5] in READ_ONLY_GIT
            assert "main" not in argv and "origin/main" not in argv


def test_default_source_is_the_tree_the_script_lives_in():
    assert deploy.DEFAULT_SOURCE_DIR == _SCRIPT.resolve().parents[1] / "infra" / "cognito-email-sender"


def test_wrapper_source_never_mutates_git_or_names_a_branch():
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    strings = {node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    for word in ("checkout", "pull", "fetch", "reset", "switch", "stash", "merge",
                 "rebase", "origin/main", "main", "aws"):
        assert word not in strings, word


def test_wrapper_never_writes_files():
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls & {"write_text", "write_bytes", "write", "mkdir", "unlink", "rename"}
    names = {node.func.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "open" not in names


# --- wrapper + source git integrity against REAL git ------------------------
# Local `git init` repositories in tmp_path; the production helpers run real
# read-only git. No AWS, no SAM (a recording fake answers `sam`), no network.

_GIT_ISOLATION = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                  "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                  "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
WRAPPER_IN_REPO = Path(deploy.WRAPPER_REPO_PATH)
SOURCE_IN_REPO = Path(deploy.SOURCE_REPO_PATH)


def _git_env():
    return {**os.environ, **_GIT_ISOLATION}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], env=_git_env(), check=True,
                          capture_output=True, text=True).stdout.strip()


def _real_git(argv, **kwargs):
    """subprocess.run with git isolated from the developer's global config."""
    if argv[0] == "git":
        kwargs.setdefault("env", _git_env())
    return subprocess.run(argv, **kwargs)


class GitAndFakeSam:
    """Real git, recorded fake sam: proves where refusal happens."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[0] == "git":
            return _real_git(argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0)

    def sam_steps(self):
        return [argv[1] for argv in self.calls if argv[0] == "sam"]

    def git_steps(self):
        return [argv[5] for argv in self.calls if argv[0] == "git"]


def _make_repo(root, *, commit_wrapper=True, gitignore=".aws-sam/\n__pycache__/\n"):
    root.mkdir(parents=True)
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    (root / WRAPPER_IN_REPO).parent.mkdir(parents=True)
    shutil.copy2(_SCRIPT, root / WRAPPER_IN_REPO)
    shutil.copytree(SOURCE, root / SOURCE_IN_REPO,
                    ignore=shutil.ignore_patterns(".aws-sam", "__pycache__"))
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    if not commit_wrapper:
        _git(root, "rm", "-q", "--cached", str(WRAPPER_IN_REPO))
    _git(root, "commit", "-q", "-m", "base")
    return root.resolve()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """tmp_path that git never searches above (no enclosing repository)."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.resolve()))
    return tmp_path


@pytest.fixture
def repo(sandbox):
    return _make_repo(sandbox / "repo")


def _refusal(fn, *args):
    with pytest.raises(deploy.DeployRefused) as exc:
        fn(*args, _real_git)
    return str(exc.value)


# CASE A
def test_real_git_clean_tracked_wrapper_is_accepted(repo):
    origin = deploy.wrapper_provenance(repo / WRAPPER_IN_REPO, _real_git)
    assert origin.revision == _git(repo, "rev-parse", "HEAD")
    assert (origin.root, origin.path) == (repo, deploy.WRAPPER_REPO_PATH)


# CASE B
@pytest.mark.parametrize("stage", [False, True], ids=["unstaged", "staged"])
def test_real_git_modified_wrapper_refuses(repo, stage):
    wrapper = repo / WRAPPER_IN_REPO
    wrapper.write_text(wrapper.read_text(encoding="utf-8").replace(
        'if not value.strip():', 'if False:', 1), encoding="utf-8")
    if stage:
        _git(repo, "add", str(WRAPPER_IN_REPO))
    assert "scripts/deploy_email_lambda.py has uncommitted changes" in _refusal(
        deploy.wrapper_provenance, wrapper)


# CASE C
def test_real_git_untracked_wrapper_refuses(sandbox):
    root = _make_repo(sandbox / "repo", commit_wrapper=False)
    assert "not tracked by git" in _refusal(deploy.wrapper_provenance, root / WRAPPER_IN_REPO)


def test_real_git_ignored_wrapper_copy_refuses(repo):
    copy = repo / ".aws-sam" / WRAPPER_IN_REPO
    copy.parent.mkdir(parents=True)
    shutil.copy2(repo / WRAPPER_IN_REPO, copy)
    assert _git(repo, "status", "--porcelain") == ""  # git status alone is silent
    assert "not tracked by git" in _refusal(deploy.wrapper_provenance, copy)


def test_real_git_wrapper_outside_any_work_tree_refuses(sandbox):
    loose = sandbox / "loose" / WRAPPER_IN_REPO
    loose.parent.mkdir(parents=True)
    shutil.copy2(_SCRIPT, loose)
    assert "not inside a git work tree" in _refusal(deploy.wrapper_provenance, loose)


def test_real_git_assume_unchanged_wrapper_refuses(repo):
    wrapper = repo / WRAPPER_IN_REPO
    _git(repo, "update-index", "--assume-unchanged", str(WRAPPER_IN_REPO))
    wrapper.write_text(wrapper.read_text(encoding="utf-8") + "# hidden\n", encoding="utf-8")
    assert _git(repo, "status", "--porcelain") == ""  # git status alone is fooled
    assert "assume-unchanged" in _refusal(deploy.wrapper_provenance, wrapper)


# CASE D
def test_real_git_clean_tracked_source_is_accepted(repo):
    origin = deploy.source_provenance(repo / SOURCE_IN_REPO, _real_git)
    assert origin.revision == _git(repo, "rev-parse", "HEAD")
    assert origin.path == deploy.SOURCE_REPO_PATH


@pytest.mark.parametrize("edit", ["modified", "staged", "untracked"])
def test_real_git_dirty_source_refuses(repo, edit):
    source = repo / SOURCE_IN_REPO
    if edit == "untracked":
        (source / "src" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    else:
        handler = source / "src" / "handler.py"
        handler.write_text(handler.read_text(encoding="utf-8") + "# edit\n", encoding="utf-8")
        if edit == "staged":
            _git(repo, "add", "-A")
    assert "has uncommitted changes" in _refusal(deploy.source_provenance, source)


# CASE E
def test_real_git_gitignored_source_copy_refuses(repo):
    copy = repo / ".aws-sam" / "old" / SOURCE_IN_REPO
    shutil.copytree(repo / SOURCE_IN_REPO, copy)
    assert _git(repo, "status", "--porcelain") == ""  # git status alone is silent
    assert "not tracked by git" in _refusal(deploy.source_provenance, copy)


def test_real_git_ignored_source_at_the_canonical_path_refuses(sandbox):
    root = _make_repo(sandbox / "repo", gitignore="infra/\n__pycache__/\n")
    assert _git(root, "status", "--porcelain") == ""
    assert "not tracked by git" in _refusal(deploy.source_provenance, root / SOURCE_IN_REPO)


def test_real_git_assume_unchanged_source_refuses(repo):
    template = repo / SOURCE_IN_REPO / "template.yaml"
    _git(repo, "update-index", "--assume-unchanged", str(SOURCE_IN_REPO / "template.yaml"))
    template.write_text(template.read_text(encoding="utf-8") + "# hidden\n", encoding="utf-8")
    assert "assume-unchanged" in _refusal(deploy.source_provenance, repo / SOURCE_IN_REPO)


# Wrapper-level: the whole guarded path with real git and a recording fake SAM.

def _main_real(repo, *extra, source=None, dry_run=False, capsys=None):
    runner = GitAndFakeSam()
    out = io.StringIO()
    argv = _argv(*extra, *(["--dry-run"] if dry_run else []),
                 source=source or repo / SOURCE_IN_REPO)
    code = deploy.main(argv, environ=_env(), run=runner, stdin_isatty=lambda: True,
                       out=out, wrapper=repo / WRAPPER_IN_REPO)
    err = capsys.readouterr().err if capsys else ""
    return code, out.getvalue(), err, runner


# Acceptance 1
def test_real_git_clean_wrapper_and_source_reach_sam(repo):
    code, out, _, runner = _main_real(repo)
    head = _git(repo, "rev-parse", "HEAD")
    assert code == 0
    assert runner.sam_steps() == ["validate", "build", "deploy"]
    assert f"Wrapper revision: {head} (clean)" in out
    assert f"Source revision : {head} (clean)" in out
    assert set(runner.git_steps()) <= READ_ONLY_GIT


# Acceptance 2
def test_real_git_dirty_wrapper_refuses_before_any_sam_call(repo, capsys):
    wrapper = repo / WRAPPER_IN_REPO
    wrapper.write_text(wrapper.read_text(encoding="utf-8") + "# edit\n", encoding="utf-8")
    code, out, err, runner = _main_real(repo, capsys=capsys)
    assert code == 2
    assert runner.git_steps()  # the real integrity path ran
    assert runner.sam_steps() == []
    assert "scripts/deploy_email_lambda.py has uncommitted changes" in err
    assert "(clean)" not in out


# Acceptance 3
def test_real_git_dirty_source_refuses_before_any_sam_call(repo, capsys):
    handler = repo / SOURCE_IN_REPO / "src" / "handler.py"
    handler.write_text(handler.read_text(encoding="utf-8") + "# edit\n", encoding="utf-8")
    code, _, err, runner = _main_real(repo, capsys=capsys)
    assert code == 2
    assert runner.sam_steps() == []
    assert "has uncommitted changes" in err


# Acceptance 5
def test_real_git_gitignored_source_copy_refuses_before_any_sam_call(repo, capsys):
    copy = repo / ".aws-sam" / "old" / SOURCE_IN_REPO
    shutil.copytree(repo / SOURCE_IN_REPO, copy)
    code, _, err, runner = _main_real(repo, source=copy, capsys=capsys)
    assert code == 2
    assert runner.sam_steps() == []
    assert "not tracked by git" in err


# Acceptance 6 + 7 + 8
def test_real_git_rollback_current_wrapper_with_prior_worktree_source(repo, sandbox):
    prior = _git(repo, "rev-parse", "HEAD")
    handler = repo / SOURCE_IN_REPO / "src" / "handler.py"
    handler.write_text(handler.read_text(encoding="utf-8") + "# newer\n", encoding="utf-8")
    wrapper = repo / WRAPPER_IN_REPO
    wrapper.write_text(wrapper.read_text(encoding="utf-8") + "# newer\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "newer")
    current = _git(repo, "rev-parse", "HEAD")
    worktree = sandbox / "fc-email-rollback"
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree), prior)

    code, out, _, runner = _main_real(repo, source=worktree / SOURCE_IN_REPO)

    assert current != prior
    assert code == 0
    assert runner.sam_steps() == ["validate", "build", "deploy"]
    assert f"Wrapper revision: {current} (clean)" in out
    assert f"Source revision : {prior} (clean)" in out
    assert "wrapper and source revisions differ" in out
    for argv in runner.calls:
        assert "main" not in argv and "origin/main" not in argv
        if argv[0] == "git":
            assert argv[5] in READ_ONLY_GIT
    assert _git(repo, "rev-parse", "HEAD") == current  # nothing was selected


def test_real_git_script_derives_its_own_path_and_refuses_when_edited(repo):
    """End to end through Path(__file__): the copied script checks ITSELF."""
    wrapper = repo / WRAPPER_IN_REPO
    command = [sys.executable, str(wrapper), "--dry-run", *_argv(source=repo / SOURCE_IN_REPO)]
    env = {**_git_env(), "RESEND_API_KEY": SENTINEL}
    clean = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60, cwd=repo)
    assert clean.returncode == 0, clean.stderr
    assert f"Wrapper revision: {_git(repo, 'rev-parse', 'HEAD')} (clean)" in clean.stdout

    wrapper.write_text(wrapper.read_text(encoding="utf-8") + "# edit\n", encoding="utf-8")
    dirty = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60, cwd=repo)
    assert dirty.returncode == 2
    assert "REFUSED: scripts/deploy_email_lambda.py has uncommitted changes" in dirty.stderr
    assert SENTINEL not in clean.stdout + clean.stderr + dirty.stdout + dirty.stderr
