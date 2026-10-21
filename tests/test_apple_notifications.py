"""MON-02-BE-02 App Store Server Notifications V2 intake.

Official SignedDataVerifier over a synthetic chain (see apple_notification_support):
authentic synthetic signatures, not Apple PKI and not live interoperability.
"""
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from appstoreserverlibrary.signed_data_verifier import VerificationException, VerificationStatus

from app.extensions import db
from app.models import (AppleAccountAssociation, AppleNotificationReceipt, AppleSubscription,
                        AppleSubscriptionReconciliation, User)
from app.services import apple_reconciliation as r, subscriptions as s
from app.services.ai_gate import BlockingConcurrencyLimit
from app.services.apple_subscriptions import SubscriptionError
from apple_notification_support import Signer, ms

PATH = "/api/v1/subscription/apple-notifications"


@pytest.fixture(scope="module")
def signer():
    return Signer()


# ── Adapter: authenticity and identity ──────────────────────────────────────

def test_valid_notification_names_subscription(signer):
    token = str(uuid4())
    signed, payload = signer.notification(token)
    note = signer.adapter().notification(signed)
    assert (note.disposition, note.original_id, note.account_token) == ("ENQUEUED", "100", token)
    assert (note.uuid, note.type, note.environment) == (payload["notificationUUID"], "DID_RENEW", "Sandbox")


def _invalid_cases(signer):
    stranger = ec.generate_private_key(ec.SECP256R1())
    token = str(uuid4())
    yield "wrong signing key", signer.sign(signer.notification(token)[1], key=stranger)
    yield "no x5c chain", signer.sign(signer.notification(token)[1], headers={})
    yield "wrong bundle", signer.notification(token, data={"bundleId": "other.bundle"})[0]
    yield "production evidence on sandbox", signer.notification(
        token, environment="Production", transaction=signer.transaction(token))[0]
    yield "inner transaction forged", signer.notification(
        token, transaction=signer.transaction(token, key=stranger))[0]
    yield "inner transaction other environment", signer.notification(
        token, transaction=signer.transaction(token, environment="Production"))[0]
    yield "inner transaction other bundle", signer.notification(
        token, transaction=signer.transaction(token, bundleId="other.bundle"))[0]
    yield "renewal names another subscription", signer.notification(
        token, renewal=signer.renewal(originalTransactionId="999"))[0]
    yield "renewal other environment", signer.notification(token, renewal=signer.renewal("Production"))[0]
    yield "sandbox app id mismatch", signer.notification(token, data={"appAppleId": 999})[0]
    yield "wrong version", signer.notification(token, version="1.0")[0]
    yield "uuid not uuid", signer.notification(token, notificationUUID="not-a-uuid")[0]
    yield "uuid missing", signer.notification(token, notificationUUID=None)[0]
    yield "signed in the future", signer.notification(
        token, signedDate=ms(datetime.now(timezone.utc) + timedelta(minutes=10)))[0]
    yield "type not closed", signer.notification(token, kind="did_renew; drop")[0]
    yield "non-digit original id", signer.notification(
        token, transaction=signer.transaction(token, originalTransactionId="10a"))[0]
    yield "no app identity at all", signer.notification(token, kind="TEST", data=False)[0]
    yield "garbage", "not.a.jws"


def test_unauthenticated_or_cross_identity_rejected(signer):
    adapter = signer.adapter()
    for label, signed in _invalid_cases(signer):
        with pytest.raises(SubscriptionError) as error:
            adapter.notification(signed)
        assert error.value.code == "SUBSCRIPTION_INVALID_EVIDENCE", label


def test_production_requires_matching_app_id(signer):
    token = str(uuid4())
    production = signer.adapter("Production")
    def signed(**data):
        return signer.notification(token, environment="Production", data=data,
                                   transaction=signer.transaction(token, "Production"),
                                   renewal=signer.renewal("Production"))[0]
    assert production.notification(signed(appAppleId=123)).disposition == "ENQUEUED"
    for data in ({"appAppleId": 999}, {}):
        with pytest.raises(SubscriptionError, match="INVALID_EVIDENCE"):
            production.notification(signed(**data))


@pytest.mark.parametrize("build,disposition", [
    (lambda s, t: s.notification(t, kind="RENEWAL_EXTENSION", data=False, subtype="SUMMARY", summary=dict(
        bundleId="offline.bundle", environment="Sandbox", requestIdentifier=str(uuid4())))[0],
     "IGNORED_NO_TRANSACTION"),
    (lambda s, t: s.notification(t, kind="TEST", data={"signedTransactionInfo": None,
                                                       "signedRenewalInfo": None})[0], "IGNORED_NO_TRANSACTION"),
    (lambda s, t: s.notification(t, transaction=s.transaction(t, productId="other.product"))[0], "IGNORED_PRODUCT"),
    (lambda s, t: s.notification(t, transaction=s.transaction(t, subscriptionGroupIdentifier="g2"))[0],
     "IGNORED_PRODUCT"),
    (lambda s, t: s.notification(t, kind="ONE_TIME_CHARGE", transaction=s.transaction(
        t, type="Consumable"))[0], "IGNORED_PRODUCT"),
    (lambda s, t: s.notification(t, transaction=s.transaction(t, inAppOwnershipType="FAMILY_SHARED"))[0],
     "IGNORED_OWNERSHIP"),
])
def test_authentic_but_irrelevant_is_ignored_not_rejected(signer, build, disposition):
    note = signer.adapter().notification(build(signer, str(uuid4())))
    assert note.disposition == disposition and note.original_id is None


def test_missing_or_legacy_token_is_only_a_lookup_hint(signer):
    for token in (None, "legacy-token"):
        signed = signer.notification(transaction=signer.transaction(token))[0]
        note = signer.adapter().notification(signed)
        assert (note.disposition, note.original_id, note.account_token) == ("ENQUEUED", "100", None)


def test_delayed_retry_delivery_is_still_a_valid_hint(signer):
    # Apple retries for days; age is irrelevant because access needs a fresh lookup.
    signed = signer.notification(str(uuid4()), signedDate=ms(datetime.now(timezone.utc) - timedelta(days=3)))[0]
    assert signer.adapter().notification(signed).disposition == "ENQUEUED"


def test_ocsp_outage_is_retryable(signer):
    adapter = signer.adapter()
    def unavailable(_):
        raise VerificationException(VerificationStatus.RETRYABLE_VERIFICATION_FAILURE)
    adapter.verifier.verify_and_decode_notification = unavailable
    with pytest.raises(SubscriptionError) as error:
        adapter.notification(signer.notification(str(uuid4()))[0])
    assert (error.value.code, error.value.status) == ("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503)


# ── HTTP intake ─────────────────────────────────────────────────────────────

@pytest.fixture
def intake(app, monkeypatch, signer):
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_NOTIFICATIONS_ENABLED=True)
    monkeypatch.setattr(r, "AppleAdapter", lambda _: signer.adapter())
    return signer


def _rows(app):
    with app.app_context():
        return (AppleNotificationReceipt.query.count(), AppleSubscriptionReconciliation.query.all())


@pytest.mark.parametrize("flags", [(False, False), (False, True), (True, False)])
def test_flag_off_is_absent_before_any_work(app, client, monkeypatch, signer, flags):
    app.config.update(APPLE_NOTIFICATIONS_ENABLED=flags[0], APPLE_SUBSCRIPTIONS_ENABLED=flags[1])
    def forbidden(*_):
        raise AssertionError("intake must stay inactive")
    monkeypatch.setattr(r, "AppleAdapter", forbidden)
    monkeypatch.setattr(r, "accept_notification", forbidden)
    response = client.post(PATH, json={"signedPayload": signer.notification(str(uuid4()))[0]})
    assert response.status_code == 404
    assert _rows(app)[0] == 0


def test_accepts_once_and_coalesces(app, client, intake):
    token = str(uuid4())
    signed = intake.notification(token)[0]
    for _ in range(3):  # duplicate delivery
        response = client.post(PATH, json={"signedPayload": signed})
        assert response.status_code == 200 and response.json == {}
        assert response.headers["Cache-Control"] == "no-store"
    receipts, requests_ = _rows(app)
    assert receipts == 1 and len(requests_) == 1
    assert (requests_[0].generation, requests_[0].account_token, requests_[0].attempts) == (1, token, 0)
    # A distinct notification for the same subscription coalesces into the same request.
    assert client.post(PATH, json={"signedPayload": intake.notification(token, kind="DID_FAIL_TO_RENEW")[0]}
                       ).status_code == 200
    receipts, requests_ = _rows(app)
    assert receipts == 2 and len(requests_) == 1 and requests_[0].generation == 2


def test_notification_alone_never_grants_access(app, client, intake, monkeypatch):
    monkeypatch.setattr(s, "AppleAdapter", lambda _: intake.adapter())
    with app.app_context():
        user = User(username="notif-only", email="notif-only@example.invalid")
        db.session.add(user); db.session.commit()
        token = str(uuid4())
        db.session.add(AppleAccountAssociation(user_id=user.id, token=token)); db.session.commit()
        uid = user.id
    assert client.post(PATH, json={"signedPayload": intake.notification(token, kind="SUBSCRIBED")[0]}
                       ).status_code == 200
    with app.app_context():
        assert AppleSubscription.query.count() == 0
        assert s.entitlement(uid)["effective_premium"] is False


def test_ignored_notification_is_acknowledged_without_request(app, client, intake):
    signed = intake.notification(kind="TEST", data={"signedTransactionInfo": None, "signedRenewalInfo": None})[0]
    response = client.post(PATH, json={"signedPayload": signed})
    assert response.status_code == 200
    with app.app_context():
        assert AppleNotificationReceipt.query.one().disposition == "IGNORED_NO_TRANSACTION"
        assert AppleSubscriptionReconciliation.query.count() == 0


@pytest.mark.parametrize("body", [None, [], {}, {"signedPayload": 1}, {"signedPayload": ""},
                                 {"signedPayload": "ä.b.c"}, {"payload": "a.b.c"}])
def test_malformed_body_rejected(app, client, intake, body):
    response = client.post(PATH, json=body)
    assert response.status_code == 400
    assert response.json["error"]["code"] == "SUBSCRIPTION_INVALID_REQUEST"
    assert _rows(app)[0] == 0


def test_oversized_body_rejected(app, client, intake):
    response = client.post(PATH, data='{"signedPayload":"' + "a" * 70000 + '"}', content_type="application/json")
    assert response.status_code == 413
    assert _rows(app)[0] == 0


def test_forged_notification_rejected_without_rows(app, client, intake):
    forged = intake.sign(intake.notification(str(uuid4()))[1], key=ec.generate_private_key(ec.SECP256R1()))
    response = client.post(PATH, json={"signedPayload": forged})
    assert response.status_code == 422
    assert response.json["error"]["code"] == "SUBSCRIPTION_INVALID_EVIDENCE"
    assert _rows(app) == (0, [])


def test_capacity_saturation_is_retryable_without_rows(app, client, intake, monkeypatch):
    @contextmanager
    def saturated():
        raise BlockingConcurrencyLimit("full")
        yield
    monkeypatch.setattr(r, "blocking_concurrency_slot", saturated)
    response = client.post(PATH, json={"signedPayload": intake.notification(str(uuid4()))[0]})
    assert response.status_code == 503 and response.headers["Retry-After"] == "30"
    assert response.json["error"]["code"] == "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"
    assert _rows(app) == (0, [])


def test_storage_failure_is_retryable(app, client, intake, monkeypatch):
    # Rollback durability is proved on PostgreSQL (SQLite commits on savepoint release).
    import sqlalchemy as sa
    def fail():
        raise sa.exc.OperationalError("commit", {}, Exception("synthetic"))
    with app.app_context():
        monkeypatch.setattr(db.session, "commit", fail)
        response = client.post(PATH, json={"signedPayload": intake.notification(str(uuid4()))[0]})
        monkeypatch.undo()
    assert response.status_code == 503
    assert response.json["error"]["code"] == "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"


def test_no_payload_token_or_identifier_logged(app, client, intake, caplog):
    token = str(uuid4())
    signed, payload = intake.notification(token)
    with caplog.at_level(logging.DEBUG):
        assert client.post(PATH, json={"signedPayload": signed}).status_code == 200
        client.post(PATH, json={"signedPayload": signed[:-4] + "AAAA"})
    text = caplog.text
    for secret in (signed[:40], token, payload["notificationUUID"], "\"100\"", "originalTransactionId"):
        assert secret not in text
    assert "apple_reconciliation event=notification outcome=ENQUEUED" in text


def test_disabled_configuration_closes_intake(app, client, monkeypatch, signer):
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_NOTIFICATIONS_ENABLED=True)
    def unconfigured(_):
        raise SubscriptionError("SUBSCRIPTION_NOT_CONFIGURED", 503)
    monkeypatch.setattr(r, "AppleAdapter", unconfigured)
    response = client.post(PATH, json={"signedPayload": signer.notification(str(uuid4()))[0]})
    assert response.status_code == 503 and response.json["error"]["code"] == "SUBSCRIPTION_NOT_CONFIGURED"
    assert _rows(app) == (0, [])


def test_bearer_routes_unchanged_by_intake(client):
    assert client.get("/api/v1/subscription/entitlement").status_code == 401


def test_notification_rate_limit_is_dedicated_per_ip_and_route(app, client, monkeypatch):
    """Unrelated traffic from the same IP never consumes Apple's budget, and the
    600/h default does not apply; the dedicated per-IP limit still does."""
    from app import config
    from app.extensions import limiter
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_NOTIFICATIONS_ENABLED=True)
    limiter.enabled = True
    limiter.reset()
    try:
        monkeypatch.setattr(config, "APPLE_NOTIFICATION_RATELIMIT", "1000 per minute")
        for _ in range(20):
            client.get("/api/v1/subscription/entitlement")  # other routes, same IP
        codes = {client.post(PATH, json={}).status_code for _ in range(601)}
        assert codes == {400}  # past the 600/h default: never 429
        monkeypatch.setattr(config, "APPLE_NOTIFICATION_RATELIMIT", "5 per minute")
        assert {client.post(PATH, json={}).status_code for _ in range(5)} == {400}
        limited = client.post(PATH, json={})
        assert limited.status_code == 429
        assert limited.json["error"]["code"] == "SUBSCRIPTION_RATE_LIMITED"
        other_ip = client.post(PATH, json={}, environ_base={"REMOTE_ADDR": "198.51.100.7"})
        assert other_ip.status_code == 400
    finally:
        limiter.reset()
        limiter.enabled = False
