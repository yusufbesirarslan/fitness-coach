from types import SimpleNamespace as NS

import pytest
from app.extensions import db, limiter
from app.models import User, AppleSubscription
from app.services import subscriptions as s, mobile_auth
from app.services.apple_subscriptions import SubscriptionError
from subscription_support import evidence
from subscription_support import adapter

PATH = "/api/v1/subscription/"


@pytest.fixture
def principal(app, monkeypatch):
    with app.app_context():
        user = User(username="subscription-api", email="subscription-api@example.invalid", is_premium=True)
        db.session.add(user); db.session.commit(); uid = user.id
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda _: NS(
        user=db.session.get(User, uid), family=None, claims={}))
    return uid


@pytest.fixture
def enabled(app, monkeypatch):
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_SUBSCRIPTION_ENVIRONMENT="Sandbox")
    monkeypatch.setattr(s, "AppleAdapter", lambda _: NS(
        environment="Sandbox", verify=lambda transaction_id, token: evidence(token)))


@pytest.mark.parametrize("suffix,method", [("purchase-context", "get"), ("entitlement", "get"), ("verify", "post")])
def test_authentication_required(client, suffix, method):
    response = getattr(client, method)(PATH + suffix)
    assert response.status_code == 401


@pytest.mark.parametrize("suffix,method", [("purchase-context", "get"), ("entitlement", "get"), ("verify", "post")])
def test_flag_off_does_not_call_apple(app, client, principal, monkeypatch, suffix, method):
    def forbidden(_):
        raise AssertionError("Apple must remain inactive")
    monkeypatch.setattr(s, "AppleAdapter", forbidden)
    response = getattr(client, method)(PATH + suffix, headers={"Authorization": "Bearer fixture"})
    assert response.status_code == 503
    assert response.json["error"]["code"] == "SUBSCRIPTIONS_DISABLED"
    with app.app_context():
        assert db.session.get(User, principal).is_premium is True
        assert AppleSubscription.query.count() == 0


def test_purchase_context_stable_and_verify_idempotent(app, client, principal, enabled):
    headers = {"Authorization": "Bearer fixture"}
    first = client.get(PATH + "purchase-context", headers=headers)
    assert first.status_code == 200
    assert first.json == client.get(PATH + "purchase-context", headers=headers).json
    assert set(first.json) == {"contract_version", "app_account_token", "environment", "evidence_type"}
    for _ in range(2):
        result = client.post(PATH + "verify", json={"transaction_id": "101"}, headers=headers)
        assert result.status_code == 200
        assert result.json["entitlement"]["effective_premium"]
    lookup = client.get(PATH + "entitlement", headers=headers)
    assert set(lookup.json["entitlement"]) == {"tier", "effective_premium", "status", "expires_at", "verification_pending"}
    assert lookup.headers["Cache-Control"] == "no-store"
    with app.app_context():
        assert AppleSubscription.query.count() == 1
        assert db.session.get(User, principal).is_premium is True


@pytest.mark.parametrize("body", [None, [], {}, {"transaction_id": 101}, {"transaction_id": True},
                                 {"transaction_id": ""}, {"transaction_id": "1" * 65},
                                 {"transaction_id": "١"}, {"transaction_id": "101", "is_premium": True}])
def test_strict_request(client, principal, enabled, body):
    response = client.post(PATH + "verify", json=body, headers={"Authorization": "Bearer fixture"})
    assert response.status_code == 400
    assert response.json["error"]["code"] == "SUBSCRIPTION_INVALID_REQUEST"


def test_payload_bound(client, principal, enabled):
    response = client.post(PATH + "verify", data='{"transaction_id":"' + "1" * 2000 + '"}',
                           content_type="application/json", headers={"Authorization": "Bearer fixture"})
    assert response.status_code == 413


def test_outage_preserves_bounded_verified_state(app, client, principal, enabled, monkeypatch):
    headers = {"Authorization": "Bearer fixture"}
    client.get(PATH + "purchase-context", headers=headers)
    client.post(PATH + "verify", json={"transaction_id": "101"}, headers=headers)
    def fail(*_):
        raise SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503)
    monkeypatch.setattr(s, "AppleAdapter", lambda _: NS(environment="Sandbox", verify=fail))
    response = client.post(PATH + "verify", json={"transaction_id": "101"}, headers=headers)
    assert response.status_code == 503 and response.json["error"]["retryable"]
    assert client.get(PATH + "entitlement", headers=headers).json["entitlement"]["effective_premium"]


def test_rate_limit(client, app, principal, enabled):
    limiter.enabled = True
    try:
        for _ in range(6):
            response = client.post(PATH + "verify", json={"transaction_id": "101"},
                                   headers={"Authorization": "Bearer fixture"})
        assert response.status_code == 429
        assert response.json["error"]["code"] == "SUBSCRIPTION_RATE_LIMITED"
    finally:
        limiter.enabled = False


def test_other_account_context_and_replay_are_isolated(app, client, principal, enabled, monkeypatch):
    headers = {"Authorization": "Bearer fixture"}
    original = client.get(PATH + "purchase-context", headers=headers).json["app_account_token"]
    a, *_ = adapter(original)
    monkeypatch.setattr(s, "AppleAdapter", lambda _: a)
    assert client.post(PATH + "verify", json={"transaction_id": "101"}, headers=headers).status_code == 200
    with app.app_context():
        other = User(username="other-api", email="other-api@example.invalid")
        db.session.add(other); db.session.commit(); uid = other.id
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda _: NS(
        user=db.session.get(User, uid), family=None, claims={}))
    other_token = client.get(PATH + "purchase-context", headers=headers).json["app_account_token"]
    assert other_token != original
    conflict = client.post(PATH + "verify", json={"transaction_id": "101"}, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json["error"]["code"] == "SUBSCRIPTION_OWNERSHIP_CONFLICT"
    assert "subscription-api" not in str(conflict.json)
    assert client.get(PATH + "entitlement", headers=headers).json["entitlement"]["effective_premium"] is False
