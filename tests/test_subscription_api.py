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
        environment="Sandbox", environments=("Sandbox",), verify=lambda transaction_id, token: evidence(token)))


@pytest.mark.parametrize("suffix,method", [("purchase-context", "get"), ("entitlement", "get"), ("verify", "post")])
def test_authentication_required(client, suffix, method):
    response = getattr(client, method)(PATH + suffix)
    assert response.status_code == 401


@pytest.mark.parametrize("suffix,method", [("purchase-context", "get"), ("entitlement", "get"), ("verify", "post")])
def test_flag_off_does_not_call_apple(app, client, principal, monkeypatch, suffix, method):
    def forbidden(_):
        raise AssertionError("Apple must remain inactive")
    monkeypatch.setattr(s, "AppleAdapter", forbidden)
    monkeypatch.setattr(s, "blocking_concurrency_slot", forbidden)
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
    monkeypatch.setattr(s, "AppleAdapter", lambda _: NS(environment="Sandbox", environments=("Sandbox",), verify=fail))
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



def test_shared_saturation_and_recovery(app, client, principal, enabled, monkeypatch):
    from contextlib import ExitStack
    from app.services import ai_gate
    headers = {"Authorization": "Bearer fixture"}
    token = client.get(PATH + "purchase-context", headers=headers).json["app_account_token"]
    a, *_ = adapter(token)
    calls = []
    for obj, name in [(a.client, "get_transaction_info"), (a.client, "get_all_subscription_statuses")]:
        original = getattr(obj, name)
        def track(*args, _fn=original):
            calls.append("provider")
            return _fn(*args)
        monkeypatch.setattr(obj, name, track)
    monkeypatch.setattr(s, "AppleAdapter", lambda _: a)
    baseline = ai_gate.capacity_snapshot()
    with ExitStack() as stack:
        for _ in range(ai_gate.AI_MAX_CONCURRENCY):
            stack.enter_context(ai_gate.blocking_concurrency_slot(wait_seconds=0))
        result = client.post(PATH + "verify", json={"transaction_id": "101"}, headers=headers)
        assert result.status_code == 503
        assert result.json["error"]["code"] == "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"
        assert result.json["error"]["retryable"] is True
        assert result.headers["Retry-After"] == "30"
        assert set(result.json["error"]) == {"code", "message", "retryable", "request_id"}
        assert calls == []
        with app.app_context():
            assert AppleSubscription.query.count() == 0
            assert db.session.get(User, principal).is_premium is True
    assert client.post(PATH + "verify", json={"transaction_id": "101"}, headers=headers).status_code == 200
    assert calls == ["provider", "provider"]
    assert ai_gate.capacity_snapshot() == baseline


@pytest.mark.parametrize("failure", [None, "timeout", "invalid", "provider", "verification", "unexpected"])
def test_complete_apple_sequence_permit_and_transaction_boundaries(
        app, client, principal, enabled, monkeypatch, failure):
    import requests
    from app.services import ai_gate
    from appstoreserverlibrary.api_client import APIException
    from appstoreserverlibrary.signed_data_verifier import VerificationException, VerificationStatus
    headers = {"Authorization": "Bearer fixture"}
    token = client.get(PATH + "purchase-context", headers=headers).json["app_account_token"]
    a, *_ = adapter(token)
    monkeypatch.setattr(s, "AppleAdapter", lambda _: a)
    baseline = ai_gate.capacity_snapshot()
    calls = []
    errors = {
        "timeout": requests.Timeout("private provider detail"),
        "invalid": SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE"),
        "provider": APIException(500),
        "verification": VerificationException(VerificationStatus.VERIFICATION_FAILURE),
        "unexpected": RuntimeError("synthetic unexpected"),
    }
    for obj, name in [(a.client, "get_transaction_info"),
                      (a.verifier, "verify_and_decode_signed_transaction"),
                      (a.client, "get_all_subscription_statuses"),
                      (a.verifier, "verify_and_decode_renewal_info")]:
        original = getattr(obj, name)
        def guarded(*args, _fn=original, _name=name):
            assert ai_gate.capacity_snapshot()["ai_active"] == baseline["ai_active"] + 1
            assert not db.session().in_transaction(), "Apple I/O inside DB transaction"
            calls.append(_name)
            if failure and _name == "verify_and_decode_renewal_info":
                raise errors[failure]
            return _fn(*args)
        monkeypatch.setattr(obj, name, guarded)
    from contextlib import contextmanager
    original_gate = s.blocking_concurrency_slot
    acquisitions = []
    @contextmanager
    def gate():
        acquisitions.append("acquire")
        with original_gate():
            yield
    monkeypatch.setattr(s, "blocking_concurrency_slot", gate)
    original_persist = s.persist_verified
    def persist(*args):
        assert ai_gate.capacity_snapshot() == baseline, "permit held across persistence"
        return original_persist(*args)
    monkeypatch.setattr(s, "persist_verified", persist)
    result = client.post(PATH + "verify", json={"transaction_id": "101"}, headers=headers)
    assert result.status_code == (200 if failure is None else 503 if failure in ("timeout", "provider", "unexpected") else 422)
    assert "private provider detail" not in result.get_data(as_text=True)
    assert "synthetic unexpected" not in result.get_data(as_text=True)
    assert calls == ["get_transaction_info", "verify_and_decode_signed_transaction",
                     "get_all_subscription_statuses", "verify_and_decode_signed_transaction",
                     "verify_and_decode_renewal_info"]
    assert ai_gate.capacity_snapshot() == baseline
    assert acquisitions == ["acquire"]
    # Prove the actual semaphore, as well as its counter, recovered completely.
    held = 0
    try:
        while ai_gate._ai_slots.acquire(blocking=False):
            held += 1
        assert held == ai_gate.AI_MAX_CONCURRENCY
    finally:
        for _ in range(held):
            ai_gate._ai_slots.release()
    with app.app_context():
        assert AppleSubscription.query.count() == (1 if failure is None else 0)
        assert db.session.get(User, principal).is_premium is True
