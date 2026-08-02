"""Feature flag lifecycle behaviour: defaults, explicit values, malformed input.

Every assertion here is about the CURRENT production contract. PR2 activates
nothing: the default of all eight flags is OFF and these tests fail loudly if a
future change flips one by accident.
"""
import pytest

from app import feature_flags
from app.feature_flags import (
    FeatureFlagConfigurationError, parse_bool_flag, resolve_rollout_flags,
)
from app.services.mobile_credentials import CredentialConfigurationError


# Values that today's `os.getenv(X, "0") == "1"` idiom reads as a SILENT OFF.
# "1 " is the dangerous one in the other direction: strip-then-accept would turn
# it into a silent ON, activating a capability on a host where it is off.
MALFORMED_VALUES = [
    "true", "TRUE", "True", "yes", "Y", "on", "enabled",
    "1 ", " 1", "0 ", "01", "2", "-1", "1.0", "false", "off", "null",
]

REGISTRY_PARSED_KEYS = tuple(
    flag.key for flag in feature_flags.ROLLOUT_FLAGS
    if flag.parsed_by == feature_flags.PARSED_BY_REGISTRY)


def _create_app():
    from app import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


def _clear_flag_env(monkeypatch):
    for key in feature_flags.FEATURE_FLAG_KEYS:
        monkeypatch.delenv(key, raising=False)


# ── parse_bool_flag ────────────────────────────────────────────────────────
@pytest.mark.parametrize("default", [True, False])
def test_unset_value_falls_back_to_the_default(default):
    assert parse_bool_flag("X", None, default=default) is default


@pytest.mark.parametrize("raw", ["", "   ", "\t", "\n"])
@pytest.mark.parametrize("default", [True, False])
def test_empty_value_falls_back_to_the_default(raw, default):
    """`KEY=` is a normal way to write "not set" in a .env file.

    It means OFF today; keeping it permissive is what makes the stricter parser
    backward-compatible for every value currently accepted.
    """
    assert parse_bool_flag("X", raw, default=default) is default


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_value_is_rejected_when_the_caller_disallows_it(raw):
    with pytest.raises(FeatureFlagConfigurationError):
        parse_bool_flag("X", raw, default=False, allow_empty=False)


@pytest.mark.parametrize("default", [True, False])
def test_explicit_values_override_the_default_in_both_directions(default):
    assert parse_bool_flag("X", "1", default=default) is True
    assert parse_bool_flag("X", "0", default=default) is False


@pytest.mark.parametrize("raw", MALFORMED_VALUES)
def test_malformed_values_are_rejected_rather_than_guessed(raw):
    with pytest.raises(FeatureFlagConfigurationError) as excinfo:
        parse_bool_flag("SOME_FLAG", raw, default=False)
    assert "SOME_FLAG" in str(excinfo.value)


def test_a_trailing_space_is_not_silently_stripped_into_an_activation():
    """The single most important parser assertion.

    Accepting "1 " would ENABLE a capability on a host where it is currently
    disabled — a rollout nobody decided to run.
    """
    with pytest.raises(FeatureFlagConfigurationError):
        parse_bool_flag("SOME_FLAG", "1 ", default=False)


# ── resolve_rollout_flags ──────────────────────────────────────────────────
def test_every_registry_flag_defaults_to_off():
    resolved = resolve_rollout_flags({})
    assert set(resolved) == set(REGISTRY_PARSED_KEYS)
    assert not any(resolved.values())


def test_registry_does_not_write_the_mobile_auth_key():
    """MOBILE_AUTH_ENABLED keeps its own stricter validator.

    A second parse path here is exactly the duplicate registry this PR avoids —
    and it would also skip the credential-lifetime and keyring validation.
    """
    assert "MOBILE_AUTH_ENABLED" not in resolve_rollout_flags(
        {"MOBILE_AUTH_ENABLED": "1"})


@pytest.mark.parametrize("key", REGISTRY_PARSED_KEYS)
def test_each_flag_turns_on_and_off_independently(key):
    on = resolve_rollout_flags({key: "1"})
    assert on[key] is True
    assert not any(v for k, v in on.items() if k != key), \
        "enabling one flag must not enable another"
    assert resolve_rollout_flags({key: "0"})[key] is False


@pytest.mark.parametrize("key", REGISTRY_PARSED_KEYS)
def test_rollback_to_off_restores_the_default_state_exactly(key):
    assert resolve_rollout_flags({key: "0"}) == resolve_rollout_flags({})


def test_every_malformed_key_is_reported_in_one_error():
    """An operator with two bad values fixes both in one pass.

    Reporting only the first would hand them a second failed deploy.
    """
    with pytest.raises(FeatureFlagConfigurationError) as excinfo:
        resolve_rollout_flags({
            "UIUX_NAV_V2_ENABLED": "true",
            "UIUX_PLAN_V2_ENABLED": "1 ",
            "WEEKLY_PROGRAM_UI_ENABLED": "1",
        })
    message = str(excinfo.value)
    assert "UIUX_NAV_V2_ENABLED" in message
    assert "UIUX_PLAN_V2_ENABLED" in message
    assert "WEEKLY_PROGRAM_UI_ENABLED" not in message, \
        "a valid key must not be named in the error"


# ── Boot integration ───────────────────────────────────────────────────────
def test_app_boots_with_every_rollout_flag_off(monkeypatch):
    from app.config import feature_flag_state

    _clear_flag_env(monkeypatch)
    flask_app = _create_app()
    state = feature_flag_state(flask_app)
    assert set(state) == set(feature_flags.FEATURE_FLAG_KEYS)
    assert not any(state.values())


@pytest.mark.parametrize("key", REGISTRY_PARSED_KEYS)
def test_boot_fails_fast_on_a_malformed_flag(monkeypatch, key):
    _clear_flag_env(monkeypatch)
    monkeypatch.setenv(key, "true")
    with pytest.raises(FeatureFlagConfigurationError):
        _create_app()


def test_boot_rejects_a_malformed_mobile_auth_value(monkeypatch):
    """Unchanged from before PR2: same exception type, same message."""
    monkeypatch.setenv("MOBILE_AUTH_ENABLED", "true")
    with pytest.raises(CredentialConfigurationError) as excinfo:
        _create_app()
    assert str(excinfo.value) == "invalid MOBILE_AUTH_ENABLED"


def test_boot_applies_an_explicitly_enabled_flag(monkeypatch):
    _clear_flag_env(monkeypatch)
    monkeypatch.setenv("WEEKLY_PROGRAM_UI_ENABLED", "1")
    flask_app = _create_app()
    assert flask_app.config["WEEKLY_PROGRAM_UI_ENABLED"] is True
    assert flask_app.config["UIUX_PLAN_V2_ENABLED"] is False


def test_boot_rollback_to_off_matches_a_never_enabled_boot(monkeypatch):
    from app.config import feature_flag_state

    _clear_flag_env(monkeypatch)
    baseline = feature_flag_state(_create_app())
    monkeypatch.setenv("WEEKLY_PROGRAM_UI_ENABLED", "0")
    assert feature_flag_state(_create_app()) == baseline


def test_flag_state_exposes_names_and_booleans_only(monkeypatch):
    """The /health?deep=1 payload must never widen into a config dump."""
    from app.config import feature_flag_state

    _clear_flag_env(monkeypatch)
    state = feature_flag_state(_create_app())
    assert all(isinstance(k, str) for k in state)
    assert all(v is True or v is False for v in state.values())
