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
    valid = {feature_flags.CATEGORY_OPERATIONAL,
             feature_flags.CATEGORY_KILL_SWITCH}
    assert set(OPERATIONAL_BOOLEAN_KEYS.values()) <= valid


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
