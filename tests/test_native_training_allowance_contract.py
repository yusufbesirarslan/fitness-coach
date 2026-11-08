"""BE-1/MOB-01 native HTTP contract; real command/ledger, no provider calls."""
from dataclasses import replace
from datetime import timedelta

import pytest

from ai_allowance_support import enable_subscriptions, fixture_policy, premium_row
from app.extensions import db
from app.models import AiUsageReservation
from app.services import ai_allowances
from app.services.ai_allowances import ScopePolicy, launch_policy
from app.services.mobile_training_generation import allowance as seam
from tests.test_mobile_training_allowance import (  # noqa: F401 - fixtures
    _ledger, _post, as_mobile, metered, mobile_user, provider,
)

CODES = ("AI_ALLOWANCE_EXHAUSTED", "AI_FEATURE_NOT_IN_PLAN")
FEATURE = "training_generation"


def _assert_refused(response, code, details):
    assert response.status_code == 402
    assert response.json["error"] == {
        "code": code,
        "message": "The Training plan request could not be completed.",
        "retryable": False,
        "request_id": response.json["error"]["request_id"],
        "details": details,
    }
    assert "Retry-After" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"


def _assert_no_dispatch(user_id, provider, holds):
    state = _ledger(user_id)
    assert provider.calls == 0 and state.operations == [] and state.bindings == []
    assert state.plans == 0
    assert AiUsageReservation.query.filter_by(user_id=user_id).count() == holds


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("window", range(3), ids=("LF1", "LF2", "LF3"))
@pytest.mark.parametrize("code", CODES)
def test_launch_free_native_http_preserves_real_ledger_metadata(
        app, client, mobile_user, as_mobile, metered, provider, monkeypatch, window, code):
    campaign = replace(launch_policy.LAUNCH_FREE_CAMPAIGN, version="test-native-lf")
    if code == "AI_FEATURE_NOT_IN_PLAN":
        units = dict(campaign.allowances.allowances)
        del units[FEATURE]
        campaign = replace(campaign, allowances=ScopePolicy("CAMPAIGN_WINDOW", units, 0))
    app.config.update(
        APPLE_SUBSCRIPTIONS_ENABLED=False,
        AI_ALLOWANCE_POLICY=replace(launch_policy.LAUNCH_V1_POLICY,
                                   version="test-native-base", launch_free=campaign))
    instant = campaign.boundary(window) + timedelta(hours=1)
    holds = 0
    details = {"feature": FEATURE, "scope": "LAUNCH_FREE"}
    if code == "AI_ALLOWANCE_EXHAUSTED":
        holds = campaign.allowances.allowances[FEATURE]
        for n in range(holds):
            ai_allowances.reserve(mobile_user.id, FEATURE, f"exhaust-window-{n}", now=instant)
        details["resets_at"] = (
            campaign.boundary(window + 1).isoformat().replace("+00:00", "Z")
            if window < 2 else None)
    reserve = ai_allowances.reserve_within

    def at_window(*args, **kwargs):
        return reserve(*args, **{**kwargs, "now": instant})

    monkeypatch.setattr(ai_allowances, "reserve_within", at_window)
    # Real HTTP route -> command -> claim/dispatch -> ledger -> typed exception.
    # A refusal abandons the pre-dispatch claim; the same key remains usable.
    for _ in range(2):
        response = _post(client, as_mobile, mobile_user, "native-lf-refusal-key")
        _assert_refused(response, code, details)
        _assert_no_dispatch(mobile_user.id, provider, holds)


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("scope", ("STARTER", "PREMIUM"))
@pytest.mark.parametrize("code", CODES)
def test_existing_scopes_keep_native_http_status_and_ledger_behavior(
        app, client, mobile_user, as_mobile, metered, provider, monkeypatch, scope, code):
    if scope == "PREMIUM":
        enable_subscriptions(app, monkeypatch)
        premium_row(mobile_user.id)
        units = {FEATURE: 1} if code == "AI_ALLOWANCE_EXHAUSTED" else {"coach": 1}
        policy = replace(fixture_policy(), premium=ScopePolicy("MONTHLY", units, 0))
    else:
        policy = fixture_policy(starter=code == "AI_ALLOWANCE_EXHAUSTED")
    app.config["AI_ALLOWANCE_POLICY"] = policy
    holds = int(code == "AI_ALLOWANCE_EXHAUSTED")
    details = {"feature": FEATURE, "scope": scope}
    if holds:
        held = ai_allowances.reserve(mobile_user.id, FEATURE, "existing-scope-exhaust")
        assert held.scope == scope
        # Compare to the authority's actual refusal, never a client reset guess.
        with pytest.raises(ai_allowances.AllowanceError) as caught:
            ai_allowances.reserve(mobile_user.id, FEATURE, "existing-scope-probe")
        details["resets_at"] = caught.value.details["resets_at"]
        assert (details["resets_at"] is None) == (scope == "STARTER")
    for _ in range(2):
        response = _post(client, as_mobile, mobile_user, "native-existing-refusal-key")
        _assert_refused(response, code, details)
        _assert_no_dispatch(mobile_user.id, provider, holds)


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("reset", [
    {}, {"resets_at": None}, {"resets_at": "not-a-date"},
    {"resets_at": "2026-12-24T21:00:00"},
    {"resets_at": {"internal": "private-reset-source"}},
    {"resets_at": 1798146000}, {"resets_at": False},
])
def test_native_http_missing_malformed_reset_never_synthesizes_or_leaks(
        client, mobile_user, as_mobile, metered, provider, monkeypatch, code, reset):
    details = {"feature": FEATURE, "scope": "LAUNCH_FREE", **reset,
               "reset": "CAMPAIGN_END", "reservation_id": "private-reservation",
               "request_digest": "private-digest", "provider": {"cost": 123}}

    def refused(*args, **kwargs):
        raise ai_allowances.AllowanceError(code, **details)

    monkeypatch.setattr(ai_allowances, "reserve_within", refused)
    expected = {"feature": FEATURE, "scope": "LAUNCH_FREE"}
    if "resets_at" in reset and (reset["resets_at"] is None
                                or isinstance(reset["resets_at"], str)):
        expected.update(reset)  # generic serializer's existing string/null rule
    response = _post(client, as_mobile, mobile_user, "native-malformed-refusal-key")
    _assert_refused(response, code, expected)
    assert "private-" not in response.get_data(as_text=True)
    _assert_no_dispatch(mobile_user.id, provider, 0)


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("code,status,retryable", [
    ("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE", 503, True),
    ("AI_ALLOWANCE_POLICY_UNCONFIGURED", 503, False),
    ("AI_ALLOWANCE_IDEMPOTENCY_CONFLICT", 409, False),
])
def test_other_native_refusals_keep_envelope_and_retry_headers(
        client, mobile_user, as_mobile, metered, provider, monkeypatch, code, status, retryable):
    def refused(*args, **kwargs):
        raise ai_allowances.AllowanceError(code, feature=FEATURE, scope="LAUNCH_FREE",
                                          resets_at="private-not-for-this-code")

    monkeypatch.setattr(ai_allowances, "reserve_within", refused)
    response = _post(client, as_mobile, mobile_user, "native-other-refusal-key")
    assert response.status_code == status
    assert response.json["error"]["code"] == code
    assert response.json["error"]["retryable"] is retryable
    assert "details" not in response.json["error"]
    assert response.headers.get("Retry-After") == ("30" if retryable else None)
    _assert_no_dispatch(mobile_user.id, provider, 0)


@pytest.mark.parametrize("code", CODES)
def test_exception_filters_non_string_feature_and_scope(code):
    error = seam._refused(code, details={"feature": [FEATURE], "scope": {"private": True},
                                        "resets_at": None, "internal": "private"})
    assert error.details == {"resets_at": None}
    assert (error.http_status, error.retryable) == (402, False)
