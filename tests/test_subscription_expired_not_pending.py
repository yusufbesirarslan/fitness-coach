"""2026-11-02 Sandbox defect: a lapsed subscription must not stay pending.

An EXPIRED/REVOKED row verified more than 24 h ago was answered
``verification_pending=true``; the app (correctly fail-closed) then hid every
offer and the ledger refused Starter AI. Freshness now gates only rows that
could grant or regain Premium; Premium itself is decided exactly as before.
"""
from datetime import datetime, timedelta, timezone
from itertools import product
from types import SimpleNamespace as NS

import pytest

from ai_allowance_support import enable_subscriptions, premium_row
from app.extensions import db
from app.models import AiAllowanceBucket, AiUsageReservation, AppleSubscription
from app.services import ai_consent, mobile_auth, subscriptions as s
from app.services.ai_allowances import service as L
from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
STALE, FRESH = timedelta(hours=25), timedelta(minutes=5)
AUTH = {"Authorization": "Bearer fixture"}


def _row(status, *, expires, verified_ago, revoked=False, grace=None):
    return NS(status=status, expires_at=NOW + expires, revoked_at=NOW - timedelta(days=1) if revoked else None,
              grace_expires_at=None if grace is None else NOW + grace,
              last_verified_at=NOW - verified_ago, auto_renew=False)


def _previous_view(row, now):
    """The f438459 rule, kept verbatim as the oracle for unchanged Premium."""
    status = {1: "ACTIVE", 2: "EXPIRED", 3: "BILLING_RETRY", 4: "GRACE_PERIOD", 5: "REVOKED"}[row.status]
    expiry = s.utc(row.grace_expires_at) if status == "GRACE_PERIOD" else s.utc(row.expires_at)
    if row.revoked_at:
        status = "REVOKED"
    pending = (s.utc(row.last_verified_at) > now
               or now - s.utc(row.last_verified_at) >= s.MAX_VERIFICATION_AGE)
    effective = (not pending and status in ("ACTIVE", "GRACE_PERIOD")
                 and expiry is not None and expiry > now)
    if status in ("ACTIVE", "GRACE_PERIOD") and (expiry is None or expiry <= now):
        status = "EXPIRED"
    if pending and status not in ("REVOKED", "EXPIRED"):
        status = "VERIFICATION_PENDING"
    return {"tier": "PREMIUM" if effective else "FREE", "effective_premium": effective,
            "status": status, "verification_pending": pending}


@pytest.mark.parametrize("row,status,pending,premium", [
    # 1-2 stored EXPIRED: never pending, old or recent.
    (_row(2, expires=-timedelta(days=6), verified_ago=timedelta(days=7)), "EXPIRED", False, False),
    (_row(2, expires=-timedelta(days=6), verified_ago=FRESH), "EXPIRED", False, False),
    # 3 REVOKED, by status or by revocation date: never pending, never Premium.
    (_row(5, expires=timedelta(days=20), verified_ago=STALE), "REVOKED", False, False),
    (_row(1, expires=timedelta(days=20), verified_ago=STALE, revoked=True), "REVOKED", False, False),
    (_row(1, expires=timedelta(days=20), verified_ago=FRESH, revoked=True), "REVOKED", False, False),
    # 4-5 ACTIVE within its term keeps the freshness gate.
    (_row(1, expires=timedelta(days=20), verified_ago=STALE), "VERIFICATION_PENDING", True, False),
    (_row(1, expires=timedelta(days=20), verified_ago=s.MAX_VERIFICATION_AGE), "VERIFICATION_PENDING", True, False),
    (_row(1, expires=timedelta(days=20), verified_ago=-timedelta(minutes=1)), "VERIFICATION_PENDING", True, False),
    (_row(1, expires=timedelta(days=20), verified_ago=FRESH), "ACTIVE", False, True),
    # 6 GRACE_PERIOD within grace is gated like ACTIVE; BILLING_RETRY unchanged.
    (_row(4, expires=-timedelta(days=1), grace=timedelta(days=2), verified_ago=STALE), "VERIFICATION_PENDING", True, False),
    (_row(4, expires=-timedelta(days=1), grace=timedelta(days=2), verified_ago=FRESH), "GRACE_PERIOD", False, True),
    (_row(3, expires=-timedelta(days=1), verified_ago=STALE), "VERIFICATION_PENDING", True, False),
    (_row(3, expires=-timedelta(days=1), verified_ago=FRESH), "BILLING_RETRY", False, False),
    # 7 effective expiry passed (the Nov 2 shape: ACTIVE stored, term over).
    (_row(1, expires=-timedelta(days=6), verified_ago=timedelta(days=7)), "EXPIRED", False, False),
    (_row(1, expires=-timedelta(seconds=1), verified_ago=FRESH), "EXPIRED", False, False),
    (_row(1, expires=timedelta(0), verified_ago=STALE), "EXPIRED", False, False),
    (_row(4, expires=-timedelta(days=3), grace=-timedelta(days=1), verified_ago=STALE), "EXPIRED", False, False),
    (_row(4, expires=-timedelta(days=3), verified_ago=STALE), "EXPIRED", False, False),
])
def test_lapsed_rows_are_not_pending_and_live_rows_stay_gated(row, status, pending, premium):
    view = s.entitlement_view(row, NOW)
    assert (view["status"], view["verification_pending"], view["effective_premium"]) == (status, pending, premium)
    assert view["tier"] == ("PREMIUM" if premium else "FREE")


def test_only_terminal_pending_changes_against_the_previous_rule():
    """Exhaustive over status x term x grace x revocation x age: Premium,
    tier and status are byte-identical to f438459; the ONE difference is a
    pending flag cleared on a row that answers EXPIRED or REVOKED."""
    ages = (-timedelta(minutes=1), timedelta(0), FRESH, s.MAX_VERIFICATION_AGE - timedelta(seconds=1),
            s.MAX_VERIFICATION_AGE, STALE, timedelta(days=30))
    terms = (-timedelta(days=30), -timedelta(seconds=1), timedelta(0), timedelta(seconds=1), timedelta(days=30))
    graces = (None, -timedelta(hours=1), timedelta(0), timedelta(hours=1))
    changed = 0
    for status, expires, grace, revoked, age in product((1, 2, 3, 4, 5), terms, graces, (False, True), ages):
        row = _row(status, expires=expires, grace=grace, revoked=revoked, verified_ago=age)
        new, old = s.entitlement_view(row, NOW), _previous_view(row, NOW)
        for key in ("tier", "effective_premium", "status"):
            assert new[key] == old[key], (status, expires, grace, revoked, age, key)
        if new["verification_pending"] != old["verification_pending"]:
            changed += 1
            assert old["verification_pending"] and not new["verification_pending"]
            assert new["status"] in ("EXPIRED", "REVOKED") and not new["effective_premium"]
    assert changed  # non-vacuous: the defect existed in this space


@pytest.fixture
def account(app, make_user, monkeypatch):
    user, other = make_user("expired-owner"), make_user("expired-other")
    enable_subscriptions(app, monkeypatch)
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda _: NS(
        user=db.session.get(type(user), user.id), family=None, claims={}))
    return user.id, other.id


def _entitlement(client):
    response = client.get("/api/v1/subscription/entitlement", headers=AUTH)
    assert response.status_code == 200
    return response.json["entitlement"]


def _age(row, *, expires_in, verified_ago, status=None):
    now = datetime.now(timezone.utc)
    row.expires_at, row.last_verified_at = now + expires_in, now - verified_ago
    if status is not None:
        row.status = status
    db.session.commit()


def test_november_2_expired_account_is_free_and_can_resubscribe(app, client, account):
    owner, _ = account
    with app.app_context():
        # Last verified while ACTIVE on 2026-10-26, term ended 2026-10-27.
        _age(premium_row(owner), expires_in=-timedelta(days=6), verified_ago=timedelta(days=7))
    body = _entitlement(client)
    assert body == {"tier": "FREE", "effective_premium": False, "status": "EXPIRED",
                    "expires_at": body["expires_at"], "verification_pending": False}
    # The offers the app showed again only after Restore are reachable directly.
    context = client.get("/api/v1/subscription/purchase-context", headers=AUTH)
    assert context.status_code == 200 and context.json["app_account_token"]
    with app.app_context():  # read-only: the stored evidence is not rewritten
        row = AppleSubscription.query.filter_by(user_id=owner).one()
        assert row.status == 1 and datetime.now(timezone.utc) - s.utc(row.last_verified_at) > timedelta(days=6)


def test_stale_live_row_still_pending_and_never_premium(app, client, account):
    owner, _ = account
    with app.app_context():
        _age(premium_row(owner), expires_in=timedelta(days=20), verified_ago=timedelta(hours=25))
    body = _entitlement(client)
    assert (body["status"], body["verification_pending"], body["effective_premium"], body["tier"]) == (
        "VERIFICATION_PENDING", True, False, "FREE")


@pytest.mark.parametrize("newer,expected", [
    ((timedelta(days=20), timedelta(minutes=1), 1), ("ACTIVE", False, True)),
    ((timedelta(days=20), timedelta(hours=25), 1), ("VERIFICATION_PENDING", True, False)),
    ((-timedelta(days=1), timedelta(days=2), 2), ("EXPIRED", False, False)),
])
def test_old_expired_row_never_dominates_a_newer_state(app, client, account, newer, expected):
    owner, _ = account
    with app.app_context():
        _age(premium_row(owner, original_id="800"), expires_in=-timedelta(days=200),
             verified_ago=timedelta(days=201), status=2)
        expires_in, verified_ago, status = newer
        _age(premium_row(owner, original_id="801"), expires_in=expires_in, verified_ago=verified_ago, status=status)
    body = _entitlement(client)
    assert (body["status"], body["verification_pending"], body["effective_premium"]) == expected


def test_no_rows_is_free_starter(client, account):
    assert _entitlement(client) == {"tier": "FREE", "effective_premium": False, "status": "FREE",
                                    "expires_at": None, "verification_pending": False}


def test_another_accounts_rows_never_reach_this_account(app, client, account):
    owner, other = account
    with app.app_context():
        premium_row(other, original_id="700")  # fresh Premium, someone else's
        _age(premium_row(owner, original_id="701"), expires_in=-timedelta(days=6),
             verified_ago=timedelta(days=7))
    body = _entitlement(client)
    assert (body["status"], body["effective_premium"], body["verification_pending"]) == ("EXPIRED", False, False)
    with app.app_context():
        assert s.entitlement(other)["effective_premium"] is True


@pytest.fixture
def ledger(app, monkeypatch):
    app.config.update(AI_ALLOWANCE_LEDGER_ENABLED=True, AI_ALLOWANCE_POLICY=AllowancePolicy(
        "test-expired", starter=ScopePolicy("NEVER", {f: 2 for f in SUPPORTED_FEATURES}, 1),
        starter_eligible_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        premium=ScopePolicy("WEEKLY", {f: 2 for f in SUPPORTED_FEATURES}, 1)))
    monkeypatch.setattr(ai_consent, "fence", lambda *args: None)


def test_lapsed_subscriber_keeps_free_starter_dispatch(app, account, ledger):
    owner, _ = account
    with app.app_context():
        _age(premium_row(owner), expires_in=-timedelta(days=6), verified_ago=timedelta(days=7))
        L.reserve_dispatch_within(owner, "coach", "expired-starter-key", "a" * 64, 30)
        db.session.commit()
        hold = AiUsageReservation.query.filter_by(user_id=owner).one()
        assert db.session.get(AiAllowanceBucket, hold.bucket_id).scope == "STARTER"
