"""Inventory completeness and drift detection for rollout flags.

The failure this file exists to prevent: someone adds a rollout flag in a hurry,
ships it default-OFF, and it is never documented, never reviewed and never
removed — so a year later nobody can say what it does, who owns it, or whether
it is safe to enable. Every assertion below is a gate a new flag must pass.
"""
import ast
import re
from pathlib import Path

import pytest

from app import feature_flags
from app.feature_flags import (
    DECISIONS, LIFECYCLE_STATES, OPERATIONAL_BOOLEAN_KEYS, ROLLOUT_FLAGS,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "app" / "config.py"
ENV_EXAMPLE = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
FLAGS_DOC_PATH = REPO_ROOT / "docs" / "FEATURE_FLAGS.md"
ROLLOUT_DOC_PATH = REPO_ROOT / "docs" / "ROLLOUT.md"

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _boolean_env_constants(path):
    """Module-level `NAME = os.getenv("KEY", "...") == "1"` assignments.

    This is the idiom every pre-PR2 rollout flag used, so it is exactly where
    an undocumented new one would appear.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (isinstance(value, ast.Compare)
                and len(value.ops) == 1
                and isinstance(value.ops[0], ast.Eq)):
            continue
        call = value.left
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in {"getenv", "get"}
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)):
            continue
        found.add(call.args[0].value)
    return found


# ── Record completeness ────────────────────────────────────────────────────
@pytest.mark.parametrize("flag", ROLLOUT_FLAGS, ids=lambda f: f.key)
def test_every_flag_carries_a_complete_lifecycle_record(flag):
    assert flag.key and flag.key.isupper()
    assert len(flag.capability) > 40, "capability must say what ON actually does"
    assert flag.owner
    assert isinstance(flag.default, bool)
    assert isinstance(flag.depends_on, tuple)
    assert flag.observability
    assert flag.prerequisites, "a flag with no prerequisite is an unreviewed flag"
    assert flag.success_signals, "no success signal means no way to call it done"
    assert flag.abort_signals, "no abort signal means no way to call it broken"
    assert flag.rollback
    assert flag.lifecycle in LIFECYCLE_STATES
    assert _ISO_DATE.match(flag.review_by), "review date must be an ISO date"
    assert flag.decision in DECISIONS


def test_nav_v2_flag_rollback_is_not_an_env_flip():
    """UX-1 PR2 made the four-destination shell production chrome.

    Operators must not be told that `UIUX_NAV_V2_ENABLED=0` restores the
    legacy five-tab shell — that env flip is a no-op after this PR.
    """
    flag = next(f for f in feature_flags.ROLLOUT_FLAGS
                if f.key == "UIUX_NAV_V2_ENABLED")
    assert "revert" in flag.rollback.lower()
    assert "does not restore" in flag.rollback
    assert "set UIUX_NAV_V2_ENABLED=0" not in flag.rollback


def test_no_flag_is_registered_with_a_default_of_on():
    """PR2 must not activate a feature.

    Every production default was OFF before this change and stays OFF; flipping
    one is a runbook action, not a code change.
    """
    enabled = [flag.key for flag in ROLLOUT_FLAGS if flag.default]
    assert enabled == [], f"registry would activate: {enabled}"


def test_registry_keys_are_unique():
    keys = [flag.key for flag in ROLLOUT_FLAGS]
    assert len(keys) == len(set(keys))


def test_declared_dependencies_resolve_to_something_real():
    known = set(feature_flags.FEATURE_FLAG_KEYS)
    for flag in ROLLOUT_FLAGS:
        for dependency in flag.depends_on:
            assert dependency in known or dependency.startswith("MOBILE_AUTH_"), \
                f"{flag.key} depends on unknown {dependency}"


def test_rollout_and_operational_categories_do_not_overlap():
    """A key is either a rollout flag or permanent configuration, never both."""
    assert not (set(feature_flags.FEATURE_FLAG_KEYS)
                & set(OPERATIONAL_BOOLEAN_KEYS))


def test_pr1_inventory_is_derived_from_the_registry():
    """One list, not two — the whole point of building on PR1's inventory."""
    from app.config import FEATURE_FLAG_KEYS as config_keys

    assert config_keys is feature_flags.FEATURE_FLAG_KEYS
    assert config_keys == tuple(flag.key for flag in ROLLOUT_FLAGS)


# ── Drift detection ────────────────────────────────────────────────────────
def test_no_unclassified_boolean_env_constant_in_config():
    """The drift gate.

    A new `X = os.getenv("X", "0") == "1"` line in app/config.py must be either
    registered as a rollout flag (with a full lifecycle record) or declared as
    permanent operational configuration. It cannot be neither.
    """
    discovered = _boolean_env_constants(CONFIG_PATH)
    classified = set(feature_flags.FEATURE_FLAG_KEYS) | set(
        OPERATIONAL_BOOLEAN_KEYS)
    unclassified = sorted(discovered - classified)
    assert unclassified == [], (
        "Unclassified boolean setting(s) in app/config.py: "
        f"{unclassified}. Add a FeatureFlag record to app/feature_flags.py if "
        "this gates a product rollout, or add it to OPERATIONAL_BOOLEAN_KEYS "
        "if it is permanent configuration or a kill switch.")


def test_rollout_flags_are_not_read_as_module_constants_in_config():
    """Rollout flags must flow through the strict parser.

    A flag re-added the old way would silently accept `true` as OFF again.
    """
    discovered = _boolean_env_constants(CONFIG_PATH)
    leaked = sorted(discovered & set(feature_flags.FEATURE_FLAG_KEYS))
    assert leaked == [], (
        f"{leaked} bypass resolve_rollout_flags(); they would read 'true' as "
        "a silent OFF again.")


def test_operational_keys_are_classified_as_a_known_category():
    assert set(OPERATIONAL_BOOLEAN_KEYS.values()) <= set(
        feature_flags.NON_ROLLOUT_CATEGORIES)


# ── Repository-wide drift detection ────────────────────────────────────────
# The gates above only look at app/config.py, which is where every rollout flag
# has historically been declared. That is not enough: nothing stops a future
# change from reading a flag with os.getenv() inside a blueprint or a service and
# bypassing the registry entirely. The three gates below scan the whole
# application package.
SCANNED_PATHS = tuple(sorted((REPO_ROOT / "app").rglob("*.py"))) + (
    REPO_ROOT / "starter.py",
)

# Where each rollout key is ALLOWED to be read from the environment, derived
# from the registry's own `parsed_by` field rather than a hand-kept exception
# list — so a record cannot claim one owner while the code has another.
_PARSED_BY_PATHS = {
    feature_flags.PARSED_BY_REGISTRY: "app/feature_flags.py",
    feature_flags.PARSED_BY_MOBILE_CREDENTIALS:
        "app/services/mobile_credentials.py",
}


def _env_key(node):
    """The literal env key this expression reads, or None.

    Covers `os.getenv("K")`, `os.environ.get("K")`, `environ.get("K")` and
    `os.environ["K"]` — every form used in this repository.
    """
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and node.args:
            arg = node.args[0]
            if not (isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)):
                return None
            if func.attr == "getenv":
                return arg.value
            if func.attr == "get":
                target = func.value
                if ((isinstance(target, ast.Attribute)
                     and target.attr == "environ")
                        or (isinstance(target, ast.Name)
                            and target.id == "environ")):
                    return arg.value
        return None
    if isinstance(node, ast.Subscript):
        target = node.value
        is_environ = ((isinstance(target, ast.Attribute)
                       and target.attr == "environ")
                      or (isinstance(target, ast.Name)
                          and target.id == "environ"))
        index = node.slice
        if (is_environ and isinstance(index, ast.Constant)
                and isinstance(index.value, str)):
            return index.value
    return None


def _scan_package():
    """(env reads, boolean-style comparisons) per repo-relative path."""
    reads, comparisons = {}, {}
    for path in SCANNED_PATHS:
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            key = _env_key(node)
            if key:
                reads.setdefault(key, set()).add(rel)
            # `os.getenv(K, "0") == "1"` and its inverted twin
            # `os.environ.get(K, "1") != "0"` — the same rollout idiom.
            if (isinstance(node, ast.Compare) and len(node.ops) == 1
                    and isinstance(node.ops[0], (ast.Eq, ast.NotEq))):
                key = _env_key(node.left)
                if key:
                    comparisons.setdefault(key, set()).add(rel)
    return reads, comparisons


PACKAGE_ENV_READS, PACKAGE_BOOLEAN_COMPARISONS = _scan_package()


@pytest.mark.parametrize("key", feature_flags.FEATURE_FLAG_KEYS)
def test_no_rollout_key_is_read_from_the_environment_outside_its_owner(key):
    """A rollout flag may only be read where its record says it is read.

    Reading `os.getenv("UIUX_NAV_V2_ENABLED")` inside a blueprint would restore
    every failure mode PR2 removed at once: the value would skip the strict
    parser (so `true` becomes a silent OFF again), skip `app.config` (so
    /health?deep=1 and the [FLAGS] boot line would no longer describe what the
    process is actually doing), and skip the registry (so the flag would have no
    owner, no review date and no rollback procedure).

    The allowance is derived from the flag's own `parsed_by` field — the record
    and the code cannot disagree about who owns the read.
    """
    owner_path = _PARSED_BY_PATHS[feature_flags.FLAGS_BY_KEY[key].parsed_by]
    offenders = sorted(PACKAGE_ENV_READS.get(key, set()) - {owner_path})
    assert offenders == [], (
        f"{key} is read directly from the environment in {offenders}. Read it "
        f"from current_app.config['{key}'] instead; the registry resolves it "
        "once at boot through the strict parser.")


def test_no_unclassified_boolean_env_setting_anywhere_in_the_package():
    """Every boolean env switch in the package must be classified.

    The narrow version of this gate only watched app/config.py. A rollout flag
    introduced in a service or a blueprint would have sailed straight past it.
    """
    classified = set(feature_flags.FEATURE_FLAG_KEYS) | set(
        OPERATIONAL_BOOLEAN_KEYS)
    unclassified = {key: sorted(paths)
                    for key, paths in PACKAGE_BOOLEAN_COMPARISONS.items()
                    if key not in classified}
    assert unclassified == {}, (
        f"Unclassified boolean env setting(s): {unclassified}. Add a FeatureFlag "
        "record to app/feature_flags.py if this gates a product rollout, or add "
        "it to OPERATIONAL_BOOLEAN_KEYS with the category that fits (operational "
        "/ kill_switch / environment / escape_hatch) if it is not a rollout.")


def test_classified_non_rollout_keys_are_permitted_anywhere():
    """The gate above must not become a ban on ordinary configuration.

    Operational settings, kill switches, environment identity and escape hatches
    are read with exactly the same idiom and are deliberately allowed. This
    asserts the currently-classified keys really are exempt, so a future tighten
    cannot quietly outlaw them.
    """
    classified_in_use = {key for key in PACKAGE_BOOLEAN_COMPARISONS
                         if key in OPERATIONAL_BOOLEAN_KEYS}
    assert classified_in_use, "expected classified non-rollout keys in the package"
    assert not (classified_in_use & set(feature_flags.FEATURE_FLAG_KEYS))


@pytest.mark.parametrize("key", feature_flags.FEATURE_FLAG_KEYS)
def test_every_flag_is_documented_in_env_example(key):
    """Two flags were missing before PR2 (UIUX_NAV_V2, FITX_WORKOUT_SESSIONS).

    An operator copying .env.example could not discover they existed.
    """
    assert f"# {key}=0" in ENV_EXAMPLE.splitlines(), \
        f"{key} has no commented `# {key}=0` line in .env.example"


@pytest.mark.parametrize("key", feature_flags.FEATURE_FLAG_KEYS)
def test_no_flag_is_shipped_pre_enabled_in_env_example(key):
    """No `KEY=1` settings line, commented or not.

    Line-scoped on purpose: `.env.example` legitimately mentions
    `MOBILE_AUTH_ENABLED=1` inside prose describing the enablement procedure.
    What must never appear is a line a copy-paste would turn into an activation.
    """
    pattern = re.compile(rf"^\s*#?\s*(?:export\s+)?{re.escape(key)}=1\s*$")
    offending = [line for line in ENV_EXAMPLE.splitlines()
                 if pattern.match(line)]
    assert offending == [], f"copying .env.example would enable {key}"


@pytest.mark.parametrize("key", feature_flags.FEATURE_FLAG_KEYS)
def test_every_flag_appears_in_the_flag_documentation(key):
    assert key in FLAGS_DOC_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize("key", feature_flags.FEATURE_FLAG_KEYS)
def test_every_flag_appears_in_the_rollout_runbook(key):
    assert key in ROLLOUT_DOC_PATH.read_text(encoding="utf-8")


# ── Cross-repository entries ───────────────────────────────────────────────
def test_cross_repository_flags_are_not_backend_rollout_flags():
    """AXISAI_NATIVE_AUTH_ENABLED is a Dart compile-time constant.

    The backend cannot read it, flip it or roll it back. Registering it as a
    backend flag would advertise control this process does not have.
    """
    backend_keys = set(feature_flags.FEATURE_FLAG_KEYS)
    for entry in feature_flags.CROSS_REPOSITORY_FLAGS:
        assert entry.key not in backend_keys
        assert entry.repository and entry.declared_in and entry.mechanism
        assert _ISO_DATE.match(entry.review_by)
        assert entry.decision in DECISIONS


def test_native_auth_flag_is_documented_as_blocked_on_mobile_auth():
    entry, = [e for e in feature_flags.CROSS_REPOSITORY_FLAGS
              if e.key == "AXISAI_NATIVE_AUTH_ENABLED"]
    assert "MOBILE_AUTH_ENABLED" in entry.depends_on
    assert entry.lifecycle == feature_flags.LIFECYCLE_BLOCKED
