#!/usr/bin/env python3
"""Canonical, fail-closed deploy of the Cognito CustomEmailSender stack.

Raw ``sam deploy`` has two silent production hazards:

* without ``ResendApiKey`` the parameter falls back to its template default
  ``''`` and every verification/reset code email silently stops (the
  changeset shows an ordinary ``Modify EmailSenderFunction`` because the
  value is NoEcho);
* without ``AlarmEmail`` the ``HasAlarmEmail`` condition turns false and
  ``EmailAlarmSubscription`` is removed.

This wrapper refuses to start SAM unless both are supplied, the stack and
region are explicitly acknowledged and match the pinned values, the tracked
SAM config and template still match the reviewed contract, and two separate
git integrity checks pass: this wrapper file itself is tracked and unmodified
(an edited copy could have switched a guard off), and the Lambda source tree
is the tracked ``infra/cognito-email-sender`` of a git work tree (not a copy
under an ignored path) with no uncommitted changes. Both revisions are shown;
they may differ (current wrapper + rollback worktree). It then runs, in order
and stopping on the first failure::

    sam validate --lint
    sam build --use-container
    sam deploy (interactive changeset confirmation)

It operates on the source tree it is given (default: the tree this script
lives in) and never pulls, checks out, resets or otherwise picks a revision,
so the same guarded path deploys an approved revision or a rollback worktree
(``--source-dir``).

Secret handling: ``ResendApiKey`` is read only from the ``RESEND_API_KEY``
environment variable, is never printed, logged or written to disk, and is
removed from every child process environment. SAM accepts parameter values
only via argv or a config file; argv was chosen (nothing is persisted), so
the value is visible in the ``sam deploy`` process arguments (``ps``) to
local users while that one process runs. The argv is passed as a list, never
through a shell.

Usage (from the repository root of the revision being deployed)::

    read -rs RESEND_API_KEY && export RESEND_API_KEY
    python scripts/deploy_email_lambda.py \\
        --stack-name axisai-cognito-email-sender --region eu-central-1 \\
        --alarm-email <subscribed-alarm-address> [--profile <prod-profile>]

Exit codes: 0 = done (or --dry-run passed), 2 = refused before SAM,
3 = local validate/build failed (nothing deployed), 4 = ``sam deploy``
failed, 5 = source tree changed during the run.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import yaml

STACK_NAME = "axisai-cognito-email-sender"
REGION = "eu-central-1"
CAPABILITIES = "CAPABILITY_IAM"
SECRET_ENV = "RESEND_API_KEY"
ALARM_ENV = "ALARM_EMAIL"
WRAPPER_PATH = Path(__file__).resolve()
DEFAULT_SOURCE_DIR = WRAPPER_PATH.parents[1] / "infra" / "cognito-email-sender"
# Repository-relative locations the integrity checks pin.
WRAPPER_REPO_PATH = "scripts/deploy_email_lambda.py"
SOURCE_REPO_PATH = "infra/cognito-email-sender"
# Tracked files that prove --source-dir is the reviewed Lambda tree rather
# than a copy (e.g. under a gitignored .aws-sam/, where `git status` is silent).
CANONICAL_SOURCE_FILES = ("template.yaml", "samconfig.toml", "src/handler.py")
BUILT_TEMPLATE = ".aws-sam/build/template.yaml"
REDACTED = "<redacted>"

# The reviewed template's parameter set. A template with any other set is a
# contract change this wrapper was not written for.
TEMPLATE_PARAMETERS = frozenset({
    "ResendApiKey", "UserPoolId", "AppBaseUrl", "EmailFromName",
    "EmailFromAddress", "EmailReplyTo", "AlarmEmail",
})
# Non-secret parameters an operator may pin explicitly to preserve a live
# non-default value (otherwise SAM uses the template default).
PRESERVABLE_PARAMETERS = frozenset({
    "UserPoolId", "AppBaseUrl", "EmailFromName", "EmailFromAddress", "EmailReplyTo",
})
# The tracked samconfig.toml must hold exactly these values. Every one is also
# passed explicitly on the command line; the check catches drift such as an
# added parameter_overrides, disabled changeset confirmation or another stack.
SAMCONFIG_CONTRACT = {
    ("default", "deploy", "parameters"): {
        "stack_name": STACK_NAME,
        "resolve_s3": True,
        "s3_prefix": STACK_NAME,
        "confirm_changeset": True,
        "capabilities": CAPABILITIES,
        "image_repositories": [],
    },
    ("default", "global", "parameters"): {"region": REGION},
}

SECRET_RE = re.compile(r"[A-Za-z0-9_-]{8,256}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,63}")
PROFILE_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
PARAM_VALUE_RE = re.compile(r"[A-Za-z0-9@._:/+-]{1,256}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
# Child-environment variables that could leak the secret or enable SAM debug
# logging of the deploy context.
STRIPPED_CHILD_ENV = (SECRET_ENV, ALARM_ENV, "SAM_DEBUG")

ALLOWED_CHANGESET = "Modify  EmailSenderFunction  AWS::Lambda::Function  Replacement=False"
FORBIDDEN_CHANGESET = (
    "EmailKmsKey or EmailKmsAlias (KMS key / alias)",
    "CognitoInvokePermission (Cognito invoke permission)",
    "EmailSenderFunctionRole (function IAM role)",
    "EmailAlarmSubscription (any action - Remove means the alarm address is gone)",
    "EmailAlarmTopic, EmailFailureMetricFilter (metric filter)",
    "EmailFailureAlarm, EmailLambdaErrorsAlarm, EmailLambdaThrottlesAlarm (alarms)",
    "any Add, any Remove, Replacement=True or Replacement=Conditional",
)


class DeployRefused(Exception):
    """A precondition failed. Messages never contain the secret value."""


class _Secret:
    """Holds the API key so reprs, tracebacks and f-strings cannot show it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return REDACTED

    __str__ = __repr__


@dataclass(frozen=True)
class Provenance:
    """A tracked, clean path: its git work tree root, repo path and HEAD."""

    root: Path
    path: str
    revision: str


@dataclass(frozen=True)
class Plan:
    source_dir: Path
    wrapper: Provenance
    source: Provenance
    alarm_email: str
    preserved: tuple[tuple[str, str], ...]
    profile: str | None
    inherited_profile: str | None

    def _common(self) -> list[str]:
        common = ["--region", REGION]
        if self.profile:
            common += ["--profile", self.profile]
        return common

    def validate_argv(self) -> list[str]:
        return ["sam", "validate", "--lint", "--template-file", "template.yaml",
                *self._common()]

    def build_argv(self) -> list[str]:
        return ["sam", "build", "--use-container", "--template-file", "template.yaml"]

    def _deploy_argv(self, secret_text: str, alarm_text: str) -> list[str]:
        return [
            "sam", "deploy",
            "--template-file", BUILT_TEMPLATE,
            "--stack-name", STACK_NAME,
            *self._common(),
            "--capabilities", CAPABILITIES,
            "--resolve-s3",
            "--s3-prefix", STACK_NAME,
            "--confirm-changeset",
            "--parameter-overrides",
            f"ResendApiKey={secret_text}",
            f"AlarmEmail={alarm_text}",
            *(f"{key}={value}" for key, value in self.preserved),
        ]

    def deploy_argv(self, secret: _Secret) -> list[str]:
        return self._deploy_argv(secret.reveal(), self.alarm_email)

    def redacted_deploy_argv(self) -> list[str]:
        """Paste-safe display form: secret redacted, alarm address masked."""
        return self._deploy_argv(REDACTED, mask_email(self.alarm_email))


def read_secret(environ: Mapping[str, str]) -> _Secret:
    value = environ.get(SECRET_ENV)
    if value is None:
        raise DeployRefused(
            f"{SECRET_ENV} is not set. Supply it at execution time "
            f"(read -rs {SECRET_ENV} && export {SECRET_ENV}); an omitted "
            "ResendApiKey deploys '' and silently stops all code emails.")
    if not value.strip():
        raise DeployRefused(f"{SECRET_ENV} is empty or whitespace-only.")
    if not SECRET_RE.fullmatch(value):
        raise DeployRefused(
            f"{SECRET_ENV} has an unexpected shape (whitespace, quotes, "
            "placeholder brackets or wrong length); value not shown.")
    return _Secret(value)


def validate_alarm_email(value: str | None) -> str:
    if value is None:
        raise DeployRefused(
            f"AlarmEmail is required (--alarm-email or {ALARM_ENV}); an omitted "
            "AlarmEmail removes EmailAlarmSubscription.")
    if not value.strip():
        raise DeployRefused("AlarmEmail is empty.")
    if len(value) > 254 or not EMAIL_RE.fullmatch(value):
        raise DeployRefused("AlarmEmail is not a plain email address.")
    return value


def mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


def check_target(stack_name: str, region: str) -> None:
    if stack_name != STACK_NAME:
        raise DeployRefused(
            f"--stack-name must be {STACK_NAME!r}; got {stack_name!r}.")
    if region != REGION:
        raise DeployRefused(f"--region must be {REGION!r}; got {region!r}.")


def parse_preserved(items: Sequence[str]) -> tuple[tuple[str, str], ...]:
    seen: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or key not in PRESERVABLE_PARAMETERS:
            raise DeployRefused(
                f"--preserve-parameter accepts KEY=VALUE for "
                f"{sorted(PRESERVABLE_PARAMETERS)} only; got key {key!r}.")
        if key in seen:
            raise DeployRefused(f"--preserve-parameter {key} given twice.")
        if not PARAM_VALUE_RE.fullmatch(value):
            raise DeployRefused(f"--preserve-parameter {key} has an unsupported value.")
        seen[key] = value
    return tuple(sorted(seen.items()))


def check_samconfig(path: Path) -> None:
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise DeployRefused(f"cannot read {path.name}: {type(error).__name__}") from None
    expected_tables = {section for section, _ in SAMCONFIG_CONTRACT.items()}
    actual_tables = {
        (env, command, kind)
        for env, commands in config.items() if isinstance(commands, dict)
        for command, kinds in commands.items() if isinstance(kinds, dict)
        for kind in kinds
    }
    top_level = {key for key, value in config.items() if not isinstance(value, dict)}
    if actual_tables != expected_tables or top_level != {"version"}:
        raise DeployRefused(
            f"{path.name} sections drifted from the reviewed contract: "
            f"{sorted(actual_tables ^ expected_tables) or sorted(top_level)}")
    for (env, command, kind), expected in SAMCONFIG_CONTRACT.items():
        actual = config[env][command][kind]
        if actual != expected:
            drift = sorted(set(actual) ^ set(expected)) or sorted(
                key for key in expected if actual.get(key) != expected[key])
            raise DeployRefused(
                f"{path.name} [{env}.{command}.{kind}] drifted from the reviewed "
                f"contract (keys: {drift}).")


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that accepts CloudFormation short-form tags (!Ref, !Sub...)."""


_CfnLoader.add_multi_constructor(
    "!", lambda loader, suffix, node: None)


def check_template(path: Path) -> None:
    try:
        template = yaml.load(path.read_text(encoding="utf-8"), Loader=_CfnLoader)  # noqa: S506
    except (OSError, yaml.YAMLError) as error:
        raise DeployRefused(f"cannot read {path.name}: {type(error).__name__}") from None
    parameters = (template or {}).get("Parameters") or {}
    if set(parameters) != TEMPLATE_PARAMETERS:
        raise DeployRefused(
            f"{path.name} parameter set drifted from the reviewed contract: "
            f"{sorted(set(parameters) ^ TEMPLATE_PARAMETERS)}")
    if parameters["ResendApiKey"].get("NoEcho") is not True:
        raise DeployRefused(
            f"{path.name}: ResendApiKey must be NoEcho: true (SAM and "
            "CloudFormation would otherwise display the secret).")


def _git(directory: Path, args: list[str], run: Callable[..., Any],
         failure: str | None = None) -> str:
    # Read-only queries only; --no-optional-locks keeps `status` from
    # rewriting the index, --literal-pathspecs keeps paths from globbing.
    completed = run(
        ["git", "--no-optional-locks", "--literal-pathspecs", "-C", str(directory), *args],
        capture_output=True, text=True, check=False, timeout=60,
    )
    if completed.returncode != 0:
        raise DeployRefused(
            failure or f"git {args[0]} failed in {directory}; is it a git work tree?")
    return completed.stdout


def locate(path: Path, run: Callable[..., Any]) -> tuple[Path, str, str]:
    """The git work tree owning ``path``, the path inside it, and its HEAD."""
    path = path.resolve()
    start = path if path.is_dir() else path.parent
    root = Path(_git(start, ["rev-parse", "--show-toplevel"], run,
                     f"{path} is not inside a git work tree.").strip()).resolve()
    try:
        inside = path.relative_to(root).as_posix()
    except ValueError:
        raise DeployRefused(f"{path} is not inside its git work tree {root}.") from None
    revision = _git(root, ["rev-parse", "HEAD"], run).strip()
    if not SHA_RE.fullmatch(revision):
        raise DeployRefused("git rev-parse HEAD returned an unexpected value.")
    return root, inside, revision


def require_tracked(root: Path, path: str, required: Sequence[str],
                    run: Callable[..., Any]) -> None:
    """Every ``required`` file is in the index, and nothing under ``path``
    hides edits from ``git status`` (assume-unchanged / skip-worktree)."""
    listing = _git(root, ["ls-files", "-v", "-z", "--", path], run)
    tags = {}
    for entry in filter(None, listing.split("\0")):
        tag, _, name = entry.partition(" ")
        tags[name] = tag
    missing = [name for name in required if name not in tags]
    if missing:
        raise DeployRefused(
            f"{', '.join(missing)} not tracked by git in {root}; deploy only a "
            "tracked, committed tree (not a copy under an ignored path).")
    hidden = sorted(name for name, tag in tags.items() if tag != "H")
    if hidden:
        raise DeployRefused(
            f"{', '.join(hidden)} marked assume-unchanged/skip-worktree (or "
            "unmerged); git status cannot vouch for it.")


def changes(root: Path, path: str, run: Callable[..., Any]) -> str:
    return _git(root, ["status", "--porcelain", "--untracked-files=all", "--", path],
                run).strip()


def wrapper_provenance(wrapper: Path, run: Callable[..., Any]) -> Provenance:
    """Proves the executing wrapper file is tracked and unmodified."""
    root, path, revision = locate(wrapper, run)
    require_tracked(root, path, [path], run)
    if path != WRAPPER_REPO_PATH:
        raise DeployRefused(
            f"the deploy wrapper must run as {WRAPPER_REPO_PATH} of a git work "
            f"tree; this copy is {path}.")
    if changes(root, path, run):
        raise DeployRefused(
            f"{WRAPPER_REPO_PATH} has uncommitted changes in {root}; run only a "
            "committed wrapper (its guards are the deploy contract).")
    return Provenance(root, path, revision)


def source_provenance(source_dir: Path, run: Callable[..., Any]) -> Provenance:
    """Proves --source-dir is the tracked Lambda tree with no local changes."""
    root, path, revision = locate(source_dir, run)
    require_tracked(root, path, [f"{path}/{name}" for name in CANONICAL_SOURCE_FILES], run)
    if path != SOURCE_REPO_PATH:
        raise DeployRefused(
            f"--source-dir must be {SOURCE_REPO_PATH} of a git work tree (a "
            f"rollback worktree is fine); got {path}.")
    if changes(root, path, run):
        raise DeployRefused(
            f"{source_dir} has uncommitted changes; deploy only a committed "
            "revision (use a separate worktree for rollback).")
    return Provenance(root, path, revision)


def source_changes(source: Provenance, run: Callable[..., Any]) -> str:
    return changes(source.root, source.path, run)


def build_plan(args: argparse.Namespace, environ: Mapping[str, str],
               run: Callable[..., Any],
               wrapper: Path = WRAPPER_PATH) -> tuple[Plan, _Secret]:
    secret = read_secret(environ)
    alarm_email = validate_alarm_email(
        args.alarm_email if args.alarm_email is not None else environ.get(ALARM_ENV))
    check_target(args.stack_name, args.region)
    if args.profile is not None and not PROFILE_RE.fullmatch(args.profile):
        raise DeployRefused("--profile has an unsupported value.")
    preserved = parse_preserved(args.preserve_parameter)
    wrapper_origin = wrapper_provenance(wrapper, run)

    source_dir = Path(args.source_dir).resolve()
    if not (source_dir / "template.yaml").is_file():
        raise DeployRefused(f"{source_dir} has no template.yaml.")
    check_samconfig(source_dir / "samconfig.toml")
    check_template(source_dir / "template.yaml")
    source_origin = source_provenance(source_dir, run)

    inherited = None if args.profile else environ.get("AWS_PROFILE")
    plan = Plan(source_dir, wrapper_origin, source_origin, alarm_email, preserved,
                args.profile, inherited)
    return plan, secret


def child_env(plan: Plan, environ: Mapping[str, str]) -> dict[str, str]:
    env = {key: value for key, value in environ.items() if key not in STRIPPED_CHILD_ENV}
    env["AWS_REGION"] = REGION
    env["AWS_DEFAULT_REGION"] = REGION
    env["SAM_CLI_TELEMETRY"] = "0"
    if plan.profile:
        env["AWS_PROFILE"] = plan.profile
    return env


def render_preview(plan: Plan) -> str:
    if plan.profile:
        identity = f"--profile {plan.profile}"
    elif plan.inherited_profile:
        identity = f"AWS_PROFILE={plan.inherited_profile} (inherited)"
    else:
        identity = "default credential chain (no profile given)"
    pinned = dict(plan.preserved)
    others = [
        f"{key}={pinned[key]} (explicit)" if key in pinned else f"{key}=<template default>"
        for key in sorted(PRESERVABLE_PARAMETERS)
    ]
    lines = [
        "=== Cognito email Lambda deploy - operator preview (no secrets) ===",
        f"Stack           : {STACK_NAME}",
        f"Region          : {REGION}",
        f"Wrapper revision: {plan.wrapper.revision} (clean)",
        f"Source dir      : {plan.source_dir}",
        f"Source revision : {plan.source.revision} (clean)",
        *([] if plan.wrapper.revision == plan.source.revision else [
            "                  (wrapper and source revisions differ - expected "
            "only for a rollback worktree)"]),
        f"AWS identity    : {identity}",
        f"ResendApiKey    : supplied ({REDACTED})",
        f"AlarmEmail      : {mask_email(plan.alarm_email)}",
        "Other params    : " + ", ".join(others),
        "Deploy command  : " + " ".join(plan.redacted_deploy_argv()),
        "",
        "Pre-deploy state (CodeSha256, stack outputs, pool LambdaConfig, alarm "
        "subscription) must already be captured - see the README.",
        "SAM will show a changeset and ask 'Deploy this changeset?'.",
        "Answer y ONLY if the changeset is exactly:",
        f"  {ALLOWED_CHANGESET}",
        "Answer N if it shows ANY of:",
        *(f"  - {item}" for item in FORBIDDEN_CHANGESET),
        "The changeset cannot show the ResendApiKey value (NoEcho); it is "
        "verified only after the deploy.",
        "This stack does not own the user pool's LambdaConfig (Cognito trigger "
        "wiring); nothing here may change it.",
    ]
    return "\n".join(lines)


def render_post_deploy(plan: Plan) -> str:
    profile = f" --profile {plan.profile}" if plan.profile else ""
    return "\n".join([
        "=== REQUIRED post-deploy verification (deploy is NOT verified until all pass) ===",
        "If you answered N at the changeset prompt nothing was deployed; skip this.",
        f"Source revision deployed: {plan.source.revision}",
        f"Wrapper revision used   : {plan.wrapper.revision}",
        "1. python scripts/check_email_lambda.py --function-name <FunctionArn>",
        "   must print 'UYUMLU'; 'UYARI' (could not read) is NOT a pass.",
        f"2. aws cloudformation describe-stacks --stack-name {STACK_NAME} "
        f"--region {REGION}{profile} --query 'Stacks[0].Outputs'",
        "   FunctionArn, KmsKeyArn and AlarmTopicArn equal the pre-deploy capture.",
        "3. aws cognito-idp describe-user-pool --user-pool-id <pool> "
        f"--region {REGION}{profile} --query 'UserPool.LambdaConfig'",
        "   equals the pre-deploy capture (CustomEmailSender -> FunctionArn, KMSKeyID -> KmsKeyArn).",
        "4. aws sns list-subscriptions-by-topic --topic-arn <AlarmTopicArn> "
        f"--region {REGION}{profile}",
        "   the alarm subscription exists and is not PendingConfirmation.",
        "Never run get-function-configuration without --query: its Environment "
        "block prints RESEND_API_KEY.",
    ])


def _run_step(name: str, argv: list[str], plan: Plan, env: Mapping[str, str],
              run: Callable[..., Any], out: TextIO) -> int:
    print(f"--> {name}", file=out, flush=True)
    try:
        completed = run(argv, cwd=str(plan.source_dir), env=dict(env), check=False)
    except Exception as error:  # noqa: BLE001 - the message may embed argv
        print(f"STOP: {name} could not run ({type(error).__name__}).", file=out)
        return -1
    return completed.returncode


def execute(plan: Plan, secret: _Secret, environ: Mapping[str, str], *,
            run: Callable[..., Any], out: TextIO) -> int:
    env = child_env(plan, environ)
    if _run_step("sam validate --lint", plan.validate_argv(), plan, env, run, out) != 0:
        print("STOP: sam validate failed; nothing was built or deployed.", file=out)
        return 3
    if _run_step("sam build --use-container", plan.build_argv(), plan, env, run, out) != 0:
        print("STOP: sam build failed; nothing was deployed.", file=out)
        return 3

    print(render_preview(plan), file=out, flush=True)
    status = _run_step("sam deploy", plan.deploy_argv(secret), plan, env, run, out)
    if status != 0:
        print("STOP: sam deploy did not complete. Inspect the stack events; if "
              "anything changed, run the post-deploy verification below.", file=out)
    try:
        changed = bool(source_changes(plan.source, run))
    except DeployRefused:
        changed = True
    print(render_post_deploy(plan), file=out)
    if changed:
        print("STOP: the source tree changed during the run or could not be "
              "re-read (possible persisted parameters). Inspect it; do NOT "
              "commit it.", file=out)
        return 5
    return 0 if status == 0 else 4


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail-closed deploy of the Cognito CustomEmailSender stack. "
                    f"ResendApiKey is read from ${SECRET_ENV} only.")
    parser.add_argument("--stack-name", required=True,
                        help=f"must be {STACK_NAME} (explicit acknowledgement)")
    parser.add_argument("--region", required=True,
                        help=f"must be {REGION} (explicit acknowledgement)")
    parser.add_argument("--alarm-email", default=None,
                        help=f"currently subscribed alarm address (or ${ALARM_ENV})")
    parser.add_argument("--profile", default=None, help="AWS profile for SAM")
    parser.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR),
                        help="infra/cognito-email-sender of the revision to deploy "
                             "(e.g. a rollback worktree); default: this tree")
    parser.add_argument("--preserve-parameter", action="append", default=[],
                        metavar="KEY=VALUE",
                        help="pin a live non-default value of "
                             f"{', '.join(sorted(PRESERVABLE_PARAMETERS))}")
    parser.add_argument("--dry-run", action="store_true",
                        help="run every guard and print the preview; never invoke SAM")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *,
         environ: Mapping[str, str] | None = None,
         run: Callable[..., Any] = subprocess.run,
         stdin_isatty: Callable[[], bool] | None = None,
         out: TextIO | None = None,
         wrapper: Path = WRAPPER_PATH) -> int:
    environ = os.environ if environ is None else environ
    stdin_isatty = sys.stdin.isatty if stdin_isatty is None else stdin_isatty
    out = sys.stdout if out is None else out
    args = parse_args(argv)
    try:
        plan, secret = build_plan(args, environ, run, wrapper)
        if args.dry_run:
            print(render_preview(plan), file=out)
            print("DRY RUN: all guards passed; SAM was not invoked.", file=out)
            return 0
        if not stdin_isatty():
            raise DeployRefused(
                "stdin is not a terminal; the SAM changeset must be confirmed "
                "interactively by the operator.")
    except DeployRefused as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2
    return execute(plan, secret, environ, run=run, out=out)


if __name__ == "__main__":
    sys.exit(main())
