"""GET /api/v1/ai/allowances (MON-02-BE-03A): flag gate, auth, contract shape."""
from types import SimpleNamespace as NS

import pytest

from ai_allowance_support import enable_ledger, enable_subscriptions, fixture_policy, premium_row
from app.extensions import db
from app.models import AiAllowanceBucket, AiStarterGrant, AiUsageReservation, User
from app.services import ai_allowances, mobile_auth

PATH = "/api/v1/ai/allowances"
AUTH = {"Authorization": "Bearer fixture"}
FEATURE_KEYS = {"feature", "scope", "state", "limit", "used", "reserved", "remaining", "reset", "resets_at"}


@pytest.fixture
def principal(app, make_user, monkeypatch):
    ids = {"current": make_user("allowance-api").id}
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda _: NS(
        user=db.session.get(User, ids["current"]), family=None, claims={}))
    return ids


def rows():
    return tuple(m.query.count() for m in (AiStarterGrant, AiAllowanceBucket, AiUsageReservation))


def test_flag_off_answers_404_before_auth_and_never_runs_the_ledger(app, client, principal, monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("ledger must not run while OFF")
    monkeypatch.setattr(ai_allowances, "allowance_view", forbidden)
    app.config["AI_ALLOWANCE_POLICY"] = fixture_policy()
    for headers in ({}, AUTH):
        response = client.get(PATH, headers=headers)
        assert response.status_code == 404
        assert "error" not in (response.get_json(silent=True) or {})


def test_authentication_is_required_when_on(app, client):
    enable_ledger(app)
    response = client.get(PATH)
    assert response.status_code == 401


def test_contract_shape_for_an_eligible_account_without_writes(app, client, principal):
    enable_ledger(app)
    response = client.get(PATH + "?tier=PREMIUM&user_id=1&period=MONTHLY", headers=AUTH)
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    body = response.get_json()
    assert set(body) == {"contract_version", "policy_state", "access", "subscription", "starter", "features"}
    assert body["contract_version"] == 1 and body["policy_state"] == "CONFIGURED"
    assert body["access"] == "STARTER"
    assert body["subscription"] == {"state": "DISABLED", "entitlement": None}
    assert body["starter"] == {"state": "ELIGIBLE", "granted_at": None}
    assert [f["feature"] for f in body["features"]] == list(ai_allowances.SUPPORTED_FEATURES)
    assert all(set(f) == FEATURE_KEYS for f in body["features"])
    assert body["features"][0] == {"feature": "coach", "scope": "STARTER", "state": "AVAILABLE", "limit": 10,
                                   "used": 0, "reserved": 0, "remaining": 10, "reset": "NEVER",
                                   "resets_at": None}
    assert rows() == (0, 0, 0)


def test_unconfigured_policy_is_distinguishable_from_zero(app, client, principal):
    enable_ledger(app)
    app.config["AI_ALLOWANCE_POLICY"] = None
    body = client.get(PATH, headers=AUTH).get_json()
    assert body["policy_state"] == "UNCONFIGURED" and body["access"] == "UNCONFIGURED"
    assert {f["state"] for f in body["features"]} == {"NOT_CONFIGURED"}
    assert {f["remaining"] for f in body["features"]} == {None}


def test_granted_balance_is_server_state_across_sessions_and_devices(app, client, principal):
    enable_ledger(app)
    uid = principal["current"]
    held = ai_allowances.reserve(uid, "coach", "device-a-key-1")
    ai_allowances.finalize(uid, held.reference, "success")
    # "Logout/login", "reinstall" and "another device" are new credentials for
    # the same account; the ledger is keyed by the owner, never by the client.
    second = app.test_client()
    first_body = client.get(PATH, headers={"Authorization": "Bearer device-a"}).get_json()
    second_body = second.get(PATH, headers={"Authorization": "Bearer device-b"}).get_json()
    assert first_body == second_body
    assert first_body["starter"]["state"] == "GRANTED"
    assert first_body["features"][0]["used"] == 1 and first_body["features"][0]["remaining"] == 9


def test_premium_status_comes_from_the_subscription_foundation(app, client, principal, monkeypatch):
    enable_subscriptions(app, monkeypatch)
    enable_ledger(app, policy=fixture_policy(premium=True))
    premium_row(principal["current"])
    body = client.get(PATH, headers=AUTH).get_json()
    assert body["access"] == "PREMIUM"
    assert body["subscription"]["state"] == "AVAILABLE"
    assert set(body["subscription"]["entitlement"]) == {
        "tier", "effective_premium", "status", "expires_at", "verification_pending"}
    coach = body["features"][0]
    assert (coach["scope"], coach["limit"], coach["reset"]) == ("PREMIUM", 3, "WEEKLY")
    assert coach["resets_at"].endswith("Z")


def test_other_accounts_never_leak_and_no_identifier_is_exposed(app, client, principal, make_user):
    enable_ledger(app)
    other = make_user("allowance-api-other").id
    held = ai_allowances.reserve(other, "qr_menu", "other-key-001")
    body = client.get(PATH, headers=AUTH).get_json()
    menu = {f["feature"]: f for f in body["features"]}["qr_menu"]
    assert (menu["used"], menu["reserved"]) == (0, 0)
    text = str(body)
    assert held.reference not in text and "user_id" not in text and "policy_version" not in text


@pytest.mark.parametrize("error_code,status,retryable", [
    ("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE", 503, True),
    ("AI_ALLOWANCE_ACCOUNT_UNAVAILABLE", 409, False),
    ("AI_ALLOWANCE_POLICY_UNCONFIGURED", 503, False),
    ("AI_ALLOWANCE_DISABLED", 503, False),
])
def test_failures_are_typed_and_never_auth_shaped(app, client, principal, monkeypatch,
                                                  error_code, status, retryable):
    enable_ledger(app)

    def fail(_):
        raise ai_allowances.AllowanceError(error_code)
    monkeypatch.setattr(ai_allowances, "allowance_view", fail)
    response = client.get(PATH, headers=AUTH)
    assert response.status_code == status
    error = response.get_json()["error"]
    assert error["code"] == error_code and error["retryable"] is retryable
    assert ("Retry-After" in response.headers) is retryable
