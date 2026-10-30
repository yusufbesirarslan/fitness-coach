"""Server purchase authority, including paid-but-ineligible recovery."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from app.extensions import db
from app.models import User, AppleAccountAssociation, AppleSubscription
from app.services import account_eligibility as policy, mobile_auth, subscriptions as s
from app.services.apple_subscriptions import SubscriptionError
from subscription_support import adult_account, evidence, premium_benefits_ready

PATH = "/api/v1/subscription/"
HEADERS = {"Authorization": "Bearer fixture"}


@pytest.fixture
def owner(app, make_user, monkeypatch):
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_SUBSCRIPTION_ENVIRONMENT="Production",
                      APPLE_SANDBOX_FALLBACK_ENABLED=True, ACCOUNT_ELIGIBILITY_ENABLED=False)
    premium_benefits_ready(monkeypatch)
    user = adult_account(make_user("purchase-adult"))
    db.session.commit()
    uid = user.id
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda _: NS(
        user=db.session.get(User, uid), family=None, claims={"age": 99, "eligibility": "screen_passed"}))
    monkeypatch.setattr(s, "AppleAdapter", lambda _: NS(environment="Production",
        environments=("Production", "Sandbox"), verify=lambda _, token: evidence(token, environment="Production")))
    return uid


def invalidate(uid, case):
    user = db.session.get(User, uid)
    record = policy._record(user)
    if case == "missing":
        user.user_metadata = {}
    elif case == "unsigned":
        user.user_metadata = {policy.METADATA_KEY: {"status": "screen_passed", "age_band": "18_plus", "country": "TR"}}
    elif case == "tampered":
        user.user_metadata = {policy.METADATA_KEY: user.user_metadata[policy.METADATA_KEY] + "x"}
    elif case == "other_subject":
        user.cognito_sub = "other-subject"
    elif case == "no_subject":
        user.cognito_sub = None
    elif case == "contradictory":
        user.age = 17
    else:
        if case == "teen":
            record.update(age_band="13_17", country="GB")
            user.age = 30  # Editable profile age cannot promote a signed teen.
        elif case == "tr_teen":
            record.update(age_band="13_17", country="TR")
        elif case == "country":
            record["country"] = "US"
        elif case == "future":
            record["checked_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        elif case == "naive":
            record["checked_at"] = datetime.now().isoformat()
        elif case == "invalid_time":
            record["checked_at"] = "not-a-date"
        elif case in {"review_required", "ineligible", "qualification_required"}:
            record["status"] = case  # Unknown signed schemas must fail closed too.
        else:
            raise AssertionError(case)
        user.user_metadata = {policy.METADATA_KEY: policy._signer(account=True).dumps(
            {"subject": user.cognito_sub, "record": record})}
    db.session.commit()


CASES = ["missing", "unsigned", "tampered", "other_subject", "no_subject", "contradictory",
         "teen", "tr_teen", "country", "future", "naive", "invalid_time",
         "review_required", "ineligible", "qualification_required"]


@pytest.mark.parametrize("case", CASES)
def test_context_denies_without_creating_association(client, owner, case):
    invalidate(owner, case)
    result = client.get(PATH + "purchase-context?age=25&country=TR&eligibility=screen_passed", headers=HEADERS)
    assert result.status_code == 403
    assert result.json["error"]["code"] == "SUBSCRIPTION_ELIGIBILITY_REQUIRED"
    assert result.json["error"]["retryable"] is False
    assert AppleAccountAssociation.query.count() == 0
    assert AppleSubscription.query.count() == 0


@pytest.mark.parametrize("environment", ["Production", "Sandbox"])
@pytest.mark.parametrize("case", CASES)
def test_paid_purchase_retained_without_grant_then_restore(client, app, owner, monkeypatch, environment, case):
    token = client.get(PATH + "purchase-context", headers=HEADERS).json["app_account_token"]
    monkeypatch.setattr(s, "AppleAdapter", lambda _: NS(environment="Production",
        environments=("Production", "Sandbox"), verify=lambda _, t: evidence(t, environment=environment)))
    invalidate(owner, case)
    for _ in range(2):
        result = client.post(PATH + "verify", json={"transaction_id": "101"}, headers=HEADERS)
        assert result.status_code == 200
        view = result.json["entitlement"]
        assert view["effective_premium"] is False and view["tier"] == "FREE"
        assert view["status"] == "ELIGIBILITY_REVIEW_REQUIRED"
        assert view["purchase_recovery_required"] is True
        assert view["resolution"] == "CONTACT_SUPPORT_OR_APPLE_REFUND"
    row = AppleSubscription.query.one()
    assert (row.user_id, row.environment, row.latest_transaction_id, row.status) == (owner, environment, "101", 1)
    assert db.session.get(AppleAccountAssociation, owner).token == token
    assert not db.session.get(User, owner).is_premium
    assert client.get(PATH + "entitlement", headers=HEADERS).json["entitlement"]["effective_premium"] is False
    # Simulate controlled canonical review, never a local/client status override.
    adult_account(db.session.get(User, owner))
    db.session.commit()
    assert client.post(PATH + "verify", json={"transaction_id": "101"}, headers=HEADERS).json["entitlement"]["effective_premium"]
    assert AppleSubscription.query.count() == 1


def test_revalidate_after_apple_io_and_before_existing_grant(client, owner, monkeypatch):
    client.get(PATH + "purchase-context", headers=HEADERS)
    def verify(_, token):
        assert not db.session().in_transaction()
        invalidate(owner, "contradictory")
        return evidence(token, environment="Production")
    monkeypatch.setattr(s, "AppleAdapter", lambda _: NS(environment="Production", verify=verify))
    result = client.post(PATH + "verify", json={"transaction_id": "101"}, headers=HEADERS)
    assert result.json["entitlement"]["effective_premium"] is False
    assert AppleSubscription.query.count() == 1


def test_existing_grant_lapses_without_new_verification(client, owner):
    client.get(PATH + "purchase-context", headers=HEADERS)
    assert client.post(PATH + "verify", json={"transaction_id": "101"}, headers=HEADERS).json["entitlement"]["effective_premium"]
    invalidate(owner, "missing")
    assert client.get(PATH + "entitlement", headers=HEADERS).json["entitlement"]["effective_premium"] is False
    assert AppleSubscription.query.one().status == 1


def test_recovery_does_not_accept_invalid_apple_evidence(client, owner, monkeypatch):
    client.get(PATH + "purchase-context", headers=HEADERS)
    invalidate(owner, "missing")
    def reject(*_):
        raise SubscriptionError("SUBSCRIPTION_INVALID_EVIDENCE")
    monkeypatch.setattr(s, "AppleAdapter", lambda _: NS(verify=reject))
    assert client.post(PATH + "verify", json={"transaction_id": "101"}, headers=HEADERS).status_code == 422
    assert AppleSubscription.query.count() == 0


@pytest.mark.parametrize("environment,fallback,screen,bypass", [
    ("Sandbox", False, False, True), ("Sandbox", False, True, False),
    ("Sandbox", True, False, False), ("Production", False, False, False),
    ("Production", True, False, False), ("", False, False, False)])
def test_legacy_boundary_has_no_production_bypass(app, owner, environment, fallback, screen, bypass):
    invalidate(owner, "missing")
    app.config.update(APPLE_SUBSCRIPTION_ENVIRONMENT=environment,
                      APPLE_SANDBOX_FALLBACK_ENABLED=fallback, ACCOUNT_ELIGIBILITY_ENABLED=screen)
    user = db.session.get(User, owner)
    now = datetime.now(timezone.utc)
    assert s._purchase_eligible(user, now, "Sandbox") is bypass
    assert s._purchase_eligible(user, now, "Production") is False


def test_purpose_binding(owner):
    user = db.session.get(User, owner)
    now = datetime.now(timezone.utc)
    assert policy.purchase_eligible(user, now)
    user.user_metadata = {policy.METADATA_KEY: policy._signer().dumps({"age": 25, "country": "TR"})}
    assert not policy.purchase_eligible(user, now)


NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
EAST, WEST = timezone(timedelta(hours=3)), timezone(timedelta(hours=-5))


@pytest.mark.parametrize("checked_at,eligible", [
    (NOW.isoformat(), True),
    ((NOW - timedelta(microseconds=1)).isoformat(), True),
    ((NOW + timedelta(microseconds=1)).isoformat(), False),
    (NOW.astimezone(EAST).isoformat(), True),
    (NOW.astimezone(WEST).isoformat(), True),
    ((NOW + timedelta(microseconds=1)).astimezone(EAST).isoformat(), False),
    ((NOW + timedelta(microseconds=1)).astimezone(WEST).isoformat(), False),
    ("2026-10-26T12:00:00Z", True),
    ((NOW - timedelta(days=3 * 365)).isoformat(), True),
    ((NOW + timedelta(days=1)).isoformat(), False),
    (NOW.replace(tzinfo=None).isoformat(), False),
    ("2026-10-26", False),
    ("not-a-date", False),
    ("", False),
])
def test_checked_at_is_an_aware_instant_not_after_now(owner, checked_at, eligible):
    # Frozen clock: same instant in any offset is equal; a future record fails closed.
    user = adult_account(db.session.get(User, owner), checked_at=checked_at)
    assert policy.purchase_eligible(user, NOW) is eligible
    user.cognito_sub = "other-subject"
    assert policy.purchase_eligible(user, NOW) is False


def test_future_dated_record_cannot_open_grant_or_restore(client, owner):
    client.get(PATH + "purchase-context", headers=HEADERS)
    assert client.post(PATH + "verify", json={"transaction_id": "101"}, headers=HEADERS).json["entitlement"]["effective_premium"]
    invalidate(owner, "future")
    assert client.get(PATH + "purchase-context", headers=HEADERS).status_code == 403
    assert client.get(PATH + "entitlement", headers=HEADERS).json["entitlement"]["status"] == "ELIGIBILITY_REVIEW_REQUIRED"
    restored = client.post(PATH + "verify", json={"transaction_id": "101"}, headers=HEADERS).json["entitlement"]
    assert restored["effective_premium"] is False and restored["purchase_recovery_required"] is True
    assert AppleSubscription.query.one().status == 1


def test_record_age_alone_never_lapses_a_renewing_subscriber(client, owner):
    # No existing-account re-declaration exists, so a valid record has no purchase expiry.
    adult_account(db.session.get(User, owner),
                  checked_at=(datetime.now(timezone.utc) - timedelta(days=3 * 365)).isoformat())
    db.session.commit()
    client.get(PATH + "purchase-context", headers=HEADERS)
    assert client.post(PATH + "verify", json={"transaction_id": "101"}, headers=HEADERS).json["entitlement"]["effective_premium"]
    assert client.get(PATH + "entitlement", headers=HEADERS).json["entitlement"]["effective_premium"]


@pytest.mark.parametrize("extra", [{"age": 25}, {"eligibility": "screen_passed"}, {"country": "TR"}])
def test_verify_rejects_client_eligibility_fields(client, owner, extra):
    assert client.post(PATH + "verify", json={"transaction_id": "101", **extra}, headers=HEADERS).status_code == 400


@pytest.mark.parametrize("case", ["missing", "country", "teen"])
def test_views_without_premium_keep_the_v1_contract(client, owner, monkeypatch, case):
    # Shipped clients decode status from a closed v1 set; only a withheld grant changes it.
    invalidate(owner, case)
    v1 = {"tier": "FREE", "effective_premium": False, "status": "FREE",
          "expires_at": None, "verification_pending": False}
    assert client.get(PATH + "entitlement", headers=HEADERS).json["entitlement"] == v1
    adult_account(db.session.get(User, owner))
    db.session.commit()
    client.get(PATH + "purchase-context", headers=HEADERS)
    monkeypatch.setattr(s, "AppleAdapter", lambda _: NS(environment="Production",
        environments=("Production", "Sandbox"), verify=lambda _, t: evidence(
            t, environment="Production", expires_at=datetime.now(timezone.utc) - timedelta(days=1))))
    invalidate(owner, case)
    view = client.post(PATH + "verify", json={"transaction_id": "101"}, headers=HEADERS).json["entitlement"]
    assert set(view) == set(v1) and view["status"] == "EXPIRED" and not view["effective_premium"]
