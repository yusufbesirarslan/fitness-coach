"""MON-BE-APPLE-DUAL-ENV-REVIEW-01: Production-first verification with Sandbox fallback.

Fake Apple endpoints answer like the App Store Server API: each environment
knows only its own transaction IDs and answers TransactionIdNotFoundError
(4040010) for the rest. The real AppleAdapter checks run over them; signed-data
environment authority is also proved with the official verifier over a
synthetic chain (apple_notification_support, NOT Apple PKI).
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
import requests
from appstoreserverlibrary.api_client import APIException
from appstoreserverlibrary.models.Environment import Environment
from appstoreserverlibrary.signed_data_verifier import VerificationException, VerificationStatus
from cryptography.hazmat.primitives import serialization

from app.extensions import db
from app.models import (AppleAccountAssociation, AppleNotificationReceipt, AppleSubscription,
                        AppleSubscriptionReconciliation as Request, User)
from app.services import apple_reconciliation as r, apple_subscriptions as apple, subscriptions as s
from app.services.apple_subscriptions import AppleAdapter, SubscriptionError
from apple_notification_support import APP_ID, BUNDLE, GROUP, Signer
from subscription_support import adapter as fake_adapter, evidence

NOT_FOUND = 4040010


def ms(value):
    return int(value.timestamp() * 1000)


class Endpoint:
    """One environment's App Store Server API: knows only its own transactions."""

    def __init__(self, known, response, signed="signed-tx"):
        self.known, self.response, self.signed = set(known), response, signed
        self.calls, self.fail, self.status_fail = [], None, None

    def get_transaction_info(self, transaction_id):
        self.calls.append(("info", transaction_id))
        if self.fail:
            raise self.fail
        if transaction_id not in self.known:
            raise APIException(404, NOT_FOUND, "Transaction id not found.")
        return NS(signedTransactionInfo=self.signed)

    def get_all_subscription_statuses(self, original_id):
        self.calls.append(("status", original_id))
        if self.status_fail:
            raise self.status_fail
        return self.response


class Dual:
    """Production adapter (original 100 / tx 101) with a Sandbox fallback (200 / 201)."""

    def __init__(self, production_token, sandbox_token=None, sandbox_original="200"):
        self.p, _, self.ptx, self.prenewal, self.pitem, self.presponse = fake_adapter(production_token)
        self.p.environment = "Production"
        for item in (self.ptx, self.prenewal, self.presponse):
            item.environment = Environment.PRODUCTION
        self.p.client = Endpoint({"101"}, self.presponse)
        self.s, _, self.stx, self.srenewal, self.sitem, self.sresponse = fake_adapter(
            sandbox_token or production_token)
        self.stx.originalTransactionId = self.srenewal.originalTransactionId = sandbox_original
        self.sitem.originalTransactionId = sandbox_original
        self.stx.transactionId = "201"
        self.sresponse.appAppleId = None  # Apple omits App Apple ID in Sandbox.
        self.s.client = Endpoint({"201"}, self.sresponse)
        self.p.fallback = self.s

    def sign(self):
        now = ms(datetime.now(timezone.utc))
        for item in (self.ptx, self.prenewal, self.stx, self.srenewal):
            item.signedDate = now


def tokens():
    return str(uuid4()), str(uuid4())


# ── Discovery: Production first, Sandbox only on 4040010 ───────────────────

def test_production_transaction_never_queries_sandbox():
    dual = Dual(str(uuid4()))
    result = dual.p.verify("101", dual.ptx.appAccountToken)
    assert (result.environment, result.original_id, result.transaction_id) == ("Production", "100", "101")
    assert dual.p.client.calls == [("info", "101"), ("status", "100")]
    assert dual.s.client.calls == []


def test_sandbox_transaction_found_after_exact_transaction_not_found():
    dual = Dual(str(uuid4()))
    result = dual.p.verify("201", dual.stx.appAccountToken)
    assert (result.environment, result.original_id, result.transaction_id) == ("Sandbox", "200", "201")
    # Production answered only Get Transaction Info; statuses come from Sandbox.
    assert dual.p.client.calls == [("info", "201")]
    assert dual.s.client.calls == [("info", "201"), ("status", "200")]


def test_unknown_transaction_rejected_in_both_environments():
    dual = Dual(str(uuid4()))
    with pytest.raises(SubscriptionError) as error:
        dual.p.verify("999", dual.ptx.appAccountToken)
    assert (error.value.code, error.value.status, error.value.reason) == \
        ("SUBSCRIPTION_INVALID_EVIDENCE", 422, "APPLE_NOT_FOUND")
    assert dual.p.client.calls == [("info", "999")] and dual.s.client.calls == [("info", "999")]


@pytest.mark.parametrize("failure,code", [
    (APIException(401), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (APIException(401, 4010001, "Unauthorized"), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (APIException(403), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (APIException(429, 4290000, "Rate limit exceeded."), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (APIException(500, 5000000, "Internal server error."), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (APIException(503), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (APIException(400, 4000006, "Invalid transaction id."), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (APIException(404), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (APIException(404, 4040001, "Account not found."), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (APIException(404, 4040002, "Account not found. Please try again."), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (APIException(404, 4040005, "Original transaction id not found."), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (APIException(404, "4040010", "string code"), "SUBSCRIPTION_INVALID_EVIDENCE"),
    (APIException(400, NOT_FOUND, "wrong status"), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (requests.Timeout("private"), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (requests.ConnectionError("private"), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
])
def test_production_failures_never_fall_back(failure, code):
    dual = Dual(str(uuid4()))
    dual.p.client.fail = failure
    with pytest.raises(SubscriptionError) as error:
        dual.p.verify("201", dual.stx.appAccountToken)
    assert error.value.code == code
    assert dual.s.client.calls == []


@pytest.mark.parametrize("status", [VerificationStatus.VERIFICATION_FAILURE,
                                    VerificationStatus.INVALID_CERTIFICATE,
                                    VerificationStatus.INVALID_APP_IDENTIFIER,
                                    VerificationStatus.INVALID_ENVIRONMENT,
                                    VerificationStatus.RETRYABLE_VERIFICATION_FAILURE])
def test_invalid_production_signature_never_falls_back(status):
    dual = Dual(str(uuid4()))
    def reject(_):
        raise VerificationException(status)
    dual.p.verifier.verify_and_decode_signed_transaction = reject
    with pytest.raises(SubscriptionError):
        dual.p.verify("101", dual.ptx.appAccountToken)
    assert dual.s.client.calls == []


def test_production_status_failures_never_fall_back():
    dual = Dual(str(uuid4()))
    dual.p.client.status_fail = APIException(404, NOT_FOUND, "Transaction id not found.")
    with pytest.raises(SubscriptionError, match="INVALID_EVIDENCE"):
        dual.p.verify("101", dual.ptx.appAccountToken)
    assert dual.s.client.calls == []


def test_production_ownership_conflict_never_falls_back():
    dual = Dual(str(uuid4()))
    with pytest.raises(SubscriptionError) as error:
        dual.p.verify("101", str(uuid4()))
    assert (error.value.code, error.value.status) == ("SUBSCRIPTION_OWNERSHIP_CONFLICT", 409)
    assert dual.s.client.calls == []


@pytest.mark.parametrize("failure,code", [
    (APIException(401), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (APIException(429), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (requests.Timeout(), "SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"),
    (APIException(404, 4040001), "SUBSCRIPTION_INVALID_EVIDENCE"),
])
def test_sandbox_failures_stay_closed(failure, code):
    dual = Dual(str(uuid4()))
    dual.s.client.fail = failure
    with pytest.raises(SubscriptionError) as error:
        dual.p.verify("201", dual.stx.appAccountToken)
    assert error.value.code == code


def test_single_environment_production_never_queries_sandbox():
    dual = Dual(str(uuid4()))
    dual.p.fallback = None
    assert dual.p.environments == ("Production",)
    with pytest.raises(SubscriptionError) as error:
        dual.p.verify("201", dual.stx.appAccountToken)
    assert error.value.reason == "APPLE_NOT_FOUND"
    assert dual.s.client.calls == []


def test_staging_sandbox_adapter_is_unchanged():
    a, token, *_ = fake_adapter()
    assert a.fallback is None and a.environments == ("Sandbox",)
    assert a.verify("101", token).environment == "Sandbox"
    a.client.get_transaction_info = lambda _: (_ for _ in ()).throw(APIException(404, NOT_FOUND))
    with pytest.raises(SubscriptionError) as error:
        a.verify("101", token)
    assert error.value.reason == "APPLE_NOT_FOUND"


# ── The selected environment verifies everything that follows ─────────────

@pytest.mark.parametrize("target,field,value", [
    ("stx", "environment", Environment.PRODUCTION),
    ("srenewal", "environment", Environment.PRODUCTION),
    ("sresponse", "environment", Environment.PRODUCTION),
    ("stx", "bundleId", "other.bundle"),
    ("sresponse", "bundleId", "other.bundle"),
    ("stx", "productId", "unknown.product"),
    ("srenewal", "productId", "unknown.product"),
    ("stx", "subscriptionGroupIdentifier", "other.group"),
    ("sresponse", "appAppleId", 999),
    ("stx", "transactionId", "202"),
    ("stx", "inAppOwnershipType", "FAMILY_SHARED"),
])
def test_sandbox_evidence_validated_for_its_own_environment(target, field, value):
    dual = Dual(str(uuid4()))
    setattr(getattr(dual, target), field, value)
    with pytest.raises(SubscriptionError, match="INVALID_EVIDENCE"):
        dual.p.verify("201", dual.stx.appAccountToken)


@pytest.mark.parametrize("target,field,value", [
    ("ptx", "environment", Environment.SANDBOX),
    ("prenewal", "environment", Environment.SANDBOX),
    ("presponse", "environment", Environment.SANDBOX),
    ("presponse", "appAppleId", None),
    ("presponse", "appAppleId", 999),
    ("ptx", "productId", "unknown.product"),
    ("ptx", "subscriptionGroupIdentifier", "other.group"),
])
def test_production_evidence_validated_without_fallback(target, field, value):
    dual = Dual(str(uuid4()))
    setattr(getattr(dual, target), field, value)
    with pytest.raises(SubscriptionError, match="INVALID_EVIDENCE"):
        dual.p.verify("101", dual.ptx.appAccountToken)
    assert dual.s.client.calls == []


@pytest.mark.parametrize("token", [None, "not-a-uuid", "00000000-0000-4000-8000-000000000001"])
def test_sandbox_account_token_must_match(token):
    dual = Dual(str(uuid4()))
    expected = dual.stx.appAccountToken
    dual.stx.appAccountToken = token
    with pytest.raises(SubscriptionError) as error:
        dual.p.verify("201", expected)
    assert (error.value.code, error.value.status) == ("SUBSCRIPTION_OWNERSHIP_CONFLICT", 409)


def test_status_lookup_uses_the_requested_environment():
    dual = Dual(str(uuid4()))
    token = dual.ptx.appAccountToken
    assert dual.p.status("200", token, "Sandbox").environment == "Sandbox"
    assert dual.p.status("100", token, "Production").environment == "Production"
    assert dual.p.status("100", token).environment == "Production"
    assert dual.p.client.calls == [("status", "100"), ("status", "100")]
    assert dual.s.client.calls == [("status", "200")]
    for environment in ("Xcode", "LocalTesting", "sandbox"):
        with pytest.raises(SubscriptionError, match="NOT_CONFIGURED"):
            dual.p.status("100", token, environment)
    dual.p.fallback = None
    with pytest.raises(SubscriptionError, match="NOT_CONFIGURED"):
        dual.p.status("200", token, "Sandbox")
    a, token, *_ = fake_adapter()
    with pytest.raises(SubscriptionError, match="NOT_CONFIGURED"):
        a.status("100", token, "Production")


# ── Real signatures: signed environment is authoritative ───────────────────

def _signed_dual(signer, token, production_signed=None, sandbox_signed=None):
    p, sb = signer.adapter("Production"), signer.adapter("Sandbox")
    p.fallback = sb

    def statuses(environment, original, app_id):
        tx = signer.transaction(token, environment, transactionId=str(int(original) + 1),
                                originalTransactionId=original)
        renewal = signer.renewal(environment, originalTransactionId=original)
        item = NS(originalTransactionId=original, signedTransactionInfo=tx,
                  signedRenewalInfo=renewal, status=1)
        return NS(bundleId=BUNDLE, appAppleId=app_id, environment=Environment(environment),
                  data=[NS(subscriptionGroupIdentifier=GROUP, lastTransactions=[item])])
    p.client = Endpoint({"101"}, statuses("Production", "100", APP_ID),
                        production_signed or signer.transaction(token, "Production"))
    sb.client = Endpoint({"201"}, statuses("Sandbox", "200", None),
                         sandbox_signed or signer.transaction(token, "Sandbox", transactionId="201",
                                                              originalTransactionId="200"))
    return p, sb


def test_real_signatures_verify_each_environment():
    signer, token = Signer(), str(uuid4())
    p, sb = _signed_dual(signer, token)
    assert p.verify("101", token).environment == "Production"
    assert p.verify("201", token).environment == "Sandbox"


def test_real_signature_environment_must_match_selected_endpoint():
    signer, token = Signer(), str(uuid4())
    # Production endpoint returning Sandbox-signed data: rejected, no fallback.
    p, sb = _signed_dual(signer, token, production_signed=signer.transaction(token, "Sandbox"))
    with pytest.raises(SubscriptionError, match="INVALID_EVIDENCE"):
        p.verify("101", token)
    assert sb.client.calls == []
    # Sandbox endpoint returning Production-signed data: rejected.
    p, _ = _signed_dual(signer, token, sandbox_signed=signer.transaction(
        token, "Production", transactionId="201", originalTransactionId="200"))
    with pytest.raises(SubscriptionError, match="INVALID_EVIDENCE"):
        p.verify("201", token)
    # Forged Sandbox signature: rejected.
    from cryptography.hazmat.primitives.asymmetric import ec
    p, _ = _signed_dual(signer, token, sandbox_signed=signer.transaction(
        token, "Sandbox", key=ec.generate_private_key(ec.SECP256R1()),
        transactionId="201", originalTransactionId="200"))
    with pytest.raises(SubscriptionError, match="INVALID_EVIDENCE"):
        p.verify("201", token)


# ── Construction and configuration ─────────────────────────────────────────

def _config(tmp_path, signer, **changes):
    root, key = tmp_path / "root.der", tmp_path / "key.pem"
    root.write_bytes(signer.root)
    key.write_bytes(signer.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
    config = dict(APPLE_SUBSCRIPTION_ENVIRONMENT="Production", APPLE_BUNDLE_ID=BUNDLE, APPLE_APP_ID=APP_ID,
                  APPLE_MONTHLY_PRODUCT_ID="monthly.fixture", APPLE_ANNUAL_PRODUCT_ID="annual.fixture",
                  APPLE_SUBSCRIPTION_GROUP_ID=GROUP, APPLE_ROOT_CERTIFICATE_PATHS=[str(root)],
                  APPLE_PRIVATE_KEY_PATH=str(key), APPLE_KEY_ID="fixture", APPLE_ISSUER_ID="fixture",
                  APPLE_SANDBOX_FALLBACK_ENABLED=True)
    config.update(changes)
    return config


def test_dual_construction_qualifies_each_environment(tmp_path, monkeypatch):
    signer = Signer()
    verifiers, clients = [], []
    real_verifier, real_client = apple.SignedDataVerifier, apple.BoundedAppleClient
    def verifier(*args):
        verifiers.append(args[1:])
        return real_verifier(*args)
    def client(*args):
        clients.append(args[-1])
        return real_client(*args)
    monkeypatch.setattr(apple, "SignedDataVerifier", verifier)
    monkeypatch.setattr(apple, "BoundedAppleClient", client)
    a = AppleAdapter(_config(tmp_path, signer))
    assert a.environments == ("Production", "Sandbox")
    assert a.fallback.fallback is None and a.fallback.environments == ("Sandbox",)
    # Online checks on in both; each verifier and API client bound to its environment.
    assert verifiers == [(True, Environment.PRODUCTION, BUNDLE, APP_ID),
                         (True, Environment.SANDBOX, BUNDLE, APP_ID)]
    assert clients == [Environment.PRODUCTION, Environment.SANDBOX]


@pytest.mark.parametrize("flag", [False, None, 1, "1", "true"])
def test_fallback_needs_the_parsed_flag(tmp_path, flag):
    a = AppleAdapter(_config(tmp_path, Signer(), APPLE_SANDBOX_FALLBACK_ENABLED=flag))
    assert a.fallback is None and a.environments == ("Production",)


def test_fallback_refused_outside_production(tmp_path):
    signer = Signer()
    with pytest.raises(SubscriptionError) as error:
        AppleAdapter(_config(tmp_path, signer, APPLE_SUBSCRIPTION_ENVIRONMENT="Sandbox"))
    assert (error.value.code, error.value.status) == ("SUBSCRIPTION_NOT_CONFIGURED", 503)
    staging = AppleAdapter(_config(tmp_path, signer, APPLE_SUBSCRIPTION_ENVIRONMENT="Sandbox",
                                   APPLE_SANDBOX_FALLBACK_ENABLED=False))
    assert staging.environments == ("Sandbox",)


def test_incomplete_configuration_closes_both_environments(tmp_path):
    with pytest.raises(SubscriptionError, match="NOT_CONFIGURED"):
        AppleAdapter(_config(tmp_path, Signer(), APPLE_APP_ID=None))


def test_flag_is_a_registered_default_off_rollout_flag(app):
    from app import feature_flags
    flag = next(f for f in feature_flags.ROLLOUT_FLAGS if f.key == "APPLE_SANDBOX_FALLBACK_ENABLED")
    assert flag.default is False and flag.depends_on == ("APPLE_SUBSCRIPTIONS_ENABLED",)
    assert app.config["APPLE_SANDBOX_FALLBACK_ENABLED"] is False


# ── Notifications: verified environment, never cross-environment ───────────

def _notification_dual(signer):
    p, sb = signer.adapter("Production"), signer.adapter("Sandbox")
    p.fallback = sb
    return p


def test_sandbox_notification_verified_by_sandbox_verifier():
    signer = Signer()
    signed, payload = signer.notification(str(uuid4()))
    note = _notification_dual(signer).notification(signed)
    assert (note.environment, note.disposition, note.original_id) == ("Sandbox", "ENQUEUED", "100")


def test_production_notification_stays_production():
    signer = Signer()
    signed, _ = signer.notification(str(uuid4()), environment="Production", data={"appAppleId": APP_ID})
    note = _notification_dual(signer).notification(signed)
    assert (note.environment, note.disposition) == ("Production", "ENQUEUED")


@pytest.mark.parametrize("case", ["forged", "production_wrong_app", "production_other_bundle",
                                  "production_with_sandbox_transaction", "sandbox_with_production_transaction",
                                  "sandbox_wrong_app"])
def test_cross_environment_or_forged_notifications_rejected(case):
    signer, token = Signer(), str(uuid4())
    if case == "forged":
        signed, _ = signer.notification(token)
        header, body, _sig = signed.split(".")
        other, _ = Signer().notification(token)
        signed = ".".join([header, body, other.split(".")[2]])
    elif case == "production_wrong_app":
        signed, _ = signer.notification(token, environment="Production", data={"appAppleId": 999})
    elif case == "production_other_bundle":
        signed, _ = signer.notification(token, environment="Production",
                                        data={"appAppleId": APP_ID, "bundleId": "other.bundle"})
    elif case == "production_with_sandbox_transaction":
        signed, _ = signer.notification(token, environment="Production", data={"appAppleId": APP_ID},
                                        transaction=signer.transaction(token, "Sandbox"))
    elif case == "sandbox_with_production_transaction":
        signed, _ = signer.notification(token, transaction=signer.transaction(token, "Production"))
    else:
        signed, _ = signer.notification(token, data={"appAppleId": 999})
    with pytest.raises(SubscriptionError, match="INVALID_EVIDENCE"):
        _notification_dual(signer).notification(signed)


def test_production_only_rejects_sandbox_notification():
    signer = Signer()
    signed, _ = signer.notification(str(uuid4()))
    with pytest.raises(SubscriptionError, match="INVALID_EVIDENCE"):
        signer.adapter("Production").notification(signed)


@pytest.mark.parametrize("status,code", [
    (VerificationStatus.RETRYABLE_VERIFICATION_FAILURE, "TEMPORARILY_UNAVAILABLE"),
    (VerificationStatus.VERIFICATION_FAILURE, "INVALID_EVIDENCE"),
    (VerificationStatus.INVALID_CERTIFICATE, "INVALID_EVIDENCE"),
    (VerificationStatus.INVALID_CHAIN_LENGTH, "INVALID_EVIDENCE"),
])
def test_unauthenticated_production_notification_never_falls_back(status, code):
    signer = Signer()
    a = _notification_dual(signer)
    def fail(_):
        raise VerificationException(status)
    a.verifier.verify_and_decode_notification = fail
    a.fallback.verifier.verify_and_decode_notification = lambda _: pytest.fail("fallback used")
    signed, _ = signer.notification(str(uuid4()))
    with pytest.raises(SubscriptionError, match=code):
        a.notification(signed)


def test_fallback_is_logged_with_closed_codes_only(caplog):
    import logging
    signer = Signer()
    dual = Dual(str(uuid4()))
    token = dual.stx.appAccountToken
    with caplog.at_level(logging.INFO, logger="app.apple_subscriptions"):
        dual.p.verify("101", token)
        assert caplog.messages == []
        dual.p.verify("201", token)
        signed, _ = signer.notification(str(uuid4()))
        _notification_dual(signer).notification(signed)
    assert caplog.messages == ["apple_subscription event=sandbox_fallback kind=verify",
                               "apple_subscription event=sandbox_fallback kind=notification"]
    assert token not in caplog.text and "201" not in caplog.text


# ── Persistence, entitlement and lifecycle (SQLite) ────────────────────────

@pytest.fixture
def world(app, monkeypatch):
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_SUBSCRIPTION_ENVIRONMENT="Production",
                      APPLE_SANDBOX_FALLBACK_ENABLED=True)
    with app.app_context():
        users = [User(username=f"dual{i}", email=f"dual{i}@example.invalid") for i in range(2)]
        db.session.add_all(users); db.session.commit()
        owners = [(u.id, str(uuid4())) for u in users]
        db.session.add_all([AppleAccountAssociation(user_id=uid, token=t) for uid, t in owners])
        db.session.commit()
    dual = Dual(owners[0][1])
    current = {"adapter": dual.p}
    monkeypatch.setattr(s, "AppleAdapter", lambda _: current["adapter"])
    monkeypatch.setattr(r, "AppleAdapter", lambda _: current["adapter"])
    return app, owners, dual, current


def _rows():
    return {(row.environment, row.original_transaction_id): row
            for row in AppleSubscription.query.order_by(AppleSubscription.id)}


def test_review_purchase_verifies_restores_and_is_idempotent(world):
    app, owners, dual, _ = world
    with app.app_context():
        uid = owners[0][0]
        assert s.verify_purchase(uid, "201")["effective_premium"]
        # Restore resubmits the same transaction: same row, no second identity.
        assert s.verify_purchase(uid, "201")["effective_premium"]
        rows = _rows()
        assert list(rows) == [("Sandbox", "200")]
        assert rows[("Sandbox", "200")].user_id == uid
        assert s.entitlement(uid)["tier"] == "PREMIUM"
        assert not db.session.get(User, uid).is_premium


def test_account_switch_cannot_claim_review_purchase(world):
    app, owners, dual, current = world
    with app.app_context():
        s.verify_purchase(owners[0][0], "201")
        with pytest.raises(SubscriptionError) as error:
            s.verify_purchase(owners[1][0], "201")
        assert error.value.status == 409
        # Even a trusted-adapter contradiction cannot move the Sandbox row.
        with pytest.raises(SubscriptionError, match="OWNERSHIP_CONFLICT"):
            s.persist_verified(owners[1][0], evidence(owners[1][1], original_id="200", transaction_id="201"),
                               owners[1][1])
        assert _rows()[("Sandbox", "200")].user_id == owners[0][0]
        assert s.entitlement(owners[1][0])["tier"] == "FREE"


def test_same_original_id_in_both_environments_stays_distinct(world):
    app, owners, _, _ = world
    with app.app_context():
        (a, a_token), (b, b_token) = owners
        s.persist_verified(a, evidence(a_token, environment="Production"), a_token)
        # A Sandbox record with the same numeric identity is a different Apple identity.
        s.persist_verified(b, evidence(b_token, environment="Sandbox"), b_token)
        revoked = evidence(b_token, environment="Sandbox", status=5,
                           revoked_at=datetime.now(timezone.utc), transaction_signed_at=datetime.now(timezone.utc)
                           + timedelta(seconds=1), renewal_signed_at=datetime.now(timezone.utc) + timedelta(seconds=1))
        assert s.persist_verified(b, revoked, b_token)["status"] == "REVOKED"
        rows = _rows()
        assert rows[("Production", "100")].user_id == a and rows[("Production", "100")].status == 1
        assert rows[("Sandbox", "100")].user_id == b and rows[("Sandbox", "100")].status == 5
        assert s.entitlement(a)["tier"] == "PREMIUM" and s.entitlement(b)["tier"] == "FREE"
        # A's Sandbox evidence for that identity conflicts with B's Sandbox row and
        # leaves A's Production row exactly as it was.
        before = (rows[("Production", "100")].latest_transaction_id, rows[("Production", "100")].updated_at)
        with pytest.raises(SubscriptionError, match="OWNERSHIP_CONFLICT"):
            s.persist_verified(a, evidence(a_token, environment="Sandbox", transaction_id="999"), a_token)
        production = _rows()[("Production", "100")]
        assert (production.latest_transaction_id, production.updated_at) == before


def test_entitlement_projection_and_kill_switch(world):
    app, owners, dual, current = world
    with app.app_context():
        uid, token = owners[0]
        s.verify_purchase(uid, "201")
        assert s.entitlement(uid)["status"] == "ACTIVE"
        # OFF: Production-only adapter; the Sandbox row no longer counts.
        dual.p.fallback = None
        assert s.entitlement(uid) == s.entitlement_view(None, datetime.now(timezone.utc))
        with pytest.raises(SubscriptionError) as error:
            s.verify_purchase(uid, "201")
        assert error.value.reason == "APPLE_NOT_FOUND"
        dual.p.fallback = dual.s
        assert s.entitlement(uid)["tier"] == "PREMIUM"
        assert AppleSubscription.query.count() == 1


def test_production_ranks_first_without_merging_state(world):
    app, owners, _, _ = world
    now = datetime.now(timezone.utc)
    with app.app_context():
        uid, token = owners[0]
        s.persist_verified(uid, evidence(token, environment="Sandbox", original_id="200", transaction_id="201",
                                         status=2, expires_at=now - timedelta(days=1)), token)
        s.persist_verified(uid, evidence(token, environment="Production", status=3,
                                         expires_at=now - timedelta(hours=1)), token)
        # Neither grants access: the configured environment's view is reported.
        assert s.entitlement(uid)["status"] == "BILLING_RETRY"
        # A Production refund leaves the Sandbox record's own state alone, and vice versa.
        s.persist_verified(uid, evidence(token, environment="Sandbox", original_id="200", transaction_id="202",
                                         transaction_signed_at=now + timedelta(seconds=1),
                                         renewal_signed_at=now + timedelta(seconds=1)), token)
        assert s.entitlement(uid)["status"] == "ACTIVE"
        rows = _rows()
        assert rows[("Production", "100")].status == 3 and rows[("Sandbox", "200")].status == 1


def test_sandbox_renewal_and_revocation_are_scoped(world):
    app, owners, dual, _ = world
    with app.app_context():
        uid, token = owners[0]
        # A Production purchase too: two independent identities for one account.
        s.verify_purchase(uid, "101")
        s.verify_purchase(uid, "201")
        # Renewal: Apple issues a new transaction under the same original.
        dual.stx.transactionId = "202"
        dual.s.client.known.add("202")
        dual.sign()
        assert s.verify_purchase(uid, "202")["status"] == "ACTIVE"
        assert _rows()[("Sandbox", "200")].latest_transaction_id == "202"
        # Refund of the Sandbox purchase revokes only the Sandbox record.
        dual.sitem.status = 5
        dual.stx.revocationDate = ms(datetime.now(timezone.utc))
        dual.sign()
        assert s.verify_purchase(uid, "202")["status"] == "REVOKED"
        rows = _rows()
        assert rows[("Sandbox", "200")].status == 5
        assert rows[("Production", "100")].status == 1 and rows[("Production", "100")].revoked_at is None
        assert s.entitlement(uid)["tier"] == "PREMIUM"
        # Replay of the pre-revocation Sandbox snapshot cannot restore it.
        dual.sitem.status = 1
        dual.stx.revocationDate = None
        dual.stx.signedDate = dual.srenewal.signedDate = ms(datetime.now(timezone.utc) - timedelta(seconds=60))
        with pytest.raises(SubscriptionError, match="VERIFICATION_PENDING"):
            s.verify_purchase(uid, "202")
        assert _rows()[("Sandbox", "200")].status == 5


def test_purchase_context_environment_stays_configured(world):
    app, owners, _, _ = world
    with app.app_context():
        context = s.purchase_context(owners[0][0])
        assert context["environment"] == "Production"
        assert set(context) == {"contract_version", "app_account_token", "environment", "evidence_type"}


# ── Reconciliation per environment ─────────────────────────────────────────

def _stale(original, environment):
    row = AppleSubscription.query.filter_by(environment=environment, original_transaction_id=original).one()
    row.last_verified_at = datetime.now(timezone.utc) - r.REFRESH_AFTER - timedelta(minutes=1)
    db.session.commit()


def test_reconciliation_refreshes_each_environment_with_its_own_client(world):
    app, owners, dual, _ = world
    with app.app_context():
        uid = owners[0][0]
        s.verify_purchase(uid, "101"); s.verify_purchase(uid, "201")
        _stale("100", "Production"); _stale("200", "Sandbox")
        dual.p.client.calls.clear(); dual.s.client.calls.clear()
        dual.sign()
        counts = r.run()
        assert counts["APPLIED"] == 2 and counts["processed"] == 2 and counts["enqueued"] == 2
        assert dual.p.client.calls == [("status", "100")]
        assert dual.s.client.calls == [("status", "200")]
        assert Request.query.count() == 0


def test_production_outage_does_not_block_sandbox_pass(world):
    app, owners, dual, _ = world
    with app.app_context():
        uid = owners[0][0]
        s.verify_purchase(uid, "101"); s.verify_purchase(uid, "201")
        _stale("100", "Production"); _stale("200", "Sandbox")
        dual.p.client.status_fail = requests.Timeout("private")
        dual.sign()
        counts = r.run()
        assert counts["APPLIED"] == 1 and counts["SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"] == 1
        assert Request.query.one().environment == "Production"


def test_sandbox_hint_never_changes_production_row(world):
    app, owners, dual, current = world
    with app.app_context():
        (a, a_token), (b, b_token) = owners
        s.verify_purchase(a, "101")
        production = _rows()[("Production", "100")]
        before = (production.user_id, production.latest_transaction_id, production.updated_at)
        # A Sandbox notification naming the same numeric original for B.
        other = Dual(a_token, sandbox_token=b_token, sandbox_original="100")
        dual.p.fallback = other.s
        r.request_reconciliation("Sandbox", "100", b_token, datetime.now(timezone.utc))
        db.session.commit()
        dual.p.client.calls.clear()
        assert r.run()["APPLIED"] == 1
        assert dual.p.client.calls == []
        rows = _rows()
        assert rows[("Sandbox", "100")].user_id == b
        production = rows[("Production", "100")]
        assert (production.user_id, production.latest_transaction_id, production.updated_at) == before


def test_fallback_off_leaves_sandbox_requests_untouched(world):
    app, owners, dual, _ = world
    with app.app_context():
        r.request_reconciliation("Sandbox", "200", owners[0][1], datetime.now(timezone.utc))
        db.session.commit()
        dual.p.fallback = None
        assert r.run()["processed"] == 0
        assert dual.s.client.calls == []
        assert Request.query.one().attempts == 0


def test_notification_intake_records_verified_environment(app, monkeypatch):
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_SUBSCRIPTION_ENVIRONMENT="Production",
                      APPLE_SANDBOX_FALLBACK_ENABLED=True)
    signer = Signer()
    a = _notification_dual(signer)
    monkeypatch.setattr(r, "AppleAdapter", lambda _: a)
    with app.app_context():
        sandbox, _ = signer.notification(str(uuid4()))
        production, _ = signer.notification(str(uuid4()), environment="Production", data={"appAppleId": APP_ID})
        assert r.accept_notification(sandbox) == "ENQUEUED"
        assert r.accept_notification(production) == "ENQUEUED"
        assert sorted(x.environment for x in AppleNotificationReceipt.query) == ["Production", "Sandbox"]
        assert sorted((q.environment, q.original_transaction_id) for q in Request.query) == \
            [("Production", "100"), ("Sandbox", "100")]
