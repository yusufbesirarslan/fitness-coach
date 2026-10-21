"""MON-02-BE-02 lifecycle state machine and bounded reconciliation (SQLite).

The real AppleAdapter status/identity/freshness logic runs over a fake Apple
transport (subscription_support.adapter); signatures are covered elsewhere.
Concurrency and rollback durability are proved in test_apple_reconciliation_pg.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import requests
from appstoreserverlibrary.api_client import APIException

from app.extensions import db
from app.models import (AppleAccountAssociation, AppleSubscription,
                        AppleSubscriptionReconciliation as Request, User)
from app.services import apple_reconciliation as r, subscriptions as s
from app.services.apple_subscriptions import SubscriptionError
from subscription_support import adapter as fake_adapter


def ms(value):
    return int(value.timestamp() * 1000)


class Apple:
    """Mutable Apple state behind the real adapter's status() checks."""

    def __init__(self, token):
        self.a, _, self.tx, self.renewal, self.item, self.response = fake_adapter(token)
        self.calls = 0
        lookup = self.a.client.get_all_subscription_statuses
        def counted(original_id):
            self.calls += 1
            if self.fail:
                raise self.fail
            self.sign()
            return lookup(original_id)
        self.a.client.get_all_subscription_statuses = counted
        self.fail = None

    def sign(self, at=None):
        self.tx.signedDate = self.renewal.signedDate = ms(at or datetime.now(timezone.utc))

    def state(self, status=1, expires=timedelta(days=30), **changes):
        now = datetime.now(timezone.utc)
        self.item.status = status
        self.tx.expiresDate = ms(now + expires)
        for key, value in changes.items():
            target = self.renewal if key in ("autoRenewStatus", "gracePeriodExpiresDate",
                                             "isInBillingRetryPeriod", "autoRenewProductId") else self.tx
            setattr(target, key, value)


@pytest.fixture
def world(app, monkeypatch):
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_SUBSCRIPTION_ENVIRONMENT="Sandbox")
    with app.app_context():
        users = [User(username=f"recon{i}", email=f"recon{i}@example.invalid") for i in range(2)]
        db.session.add_all(users); db.session.commit()
        owners = [(u.id, str(uuid4())) for u in users]
        db.session.add_all([AppleAccountAssociation(user_id=uid, token=t) for uid, t in owners])
        db.session.commit()
    apple = Apple(owners[0][1])
    monkeypatch.setattr(r, "AppleAdapter", lambda _: apple.a)
    monkeypatch.setattr(s, "AppleAdapter", lambda _: apple.a)
    return app, owners, apple


def _request(original="100", token=None, environment="Sandbox"):
    r.request_reconciliation(environment, original, token, datetime.now(timezone.utc))
    db.session.commit()


def _owned(uid, token, apple):
    """Ownership established exactly like client verify (not counted as a worker call)."""
    view = s.persist_verified(uid, apple.a.status("100", token), token)
    apple.calls = 0
    return view


def _row():
    return AppleSubscription.query.one()


def _ent(uid):
    return s.entitlement(uid)


def test_missed_verify_recovered_from_signed_hint(world):
    app, owners, apple = world
    with app.app_context():
        _request(token=owners[0][1])
        assert r.run()["APPLIED"] == 1
        assert _row().user_id == owners[0][0]
        assert _ent(owners[0][0])["status"] == "ACTIVE"
        assert Request.query.count() == 0


@pytest.mark.parametrize("hint", [None, "00000000-0000-4000-8000-000000000009"])
def test_unmatched_target_is_terminal_and_writes_nothing(world, hint):
    app, _, apple = world
    with app.app_context():
        _request(token=hint)
        assert r.run()[r.UNMATCHED] == 1
        assert apple.calls == 0 and AppleSubscription.query.count() == 0 and Request.query.count() == 0


def test_renewal_extends_and_cancellation_keeps_access_to_expiry(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        first = _row().expires_at
        apple.state(expires=timedelta(days=60), transactionId="102")
        _request(); r.run()
        assert _row().expires_at > first and _row().latest_transaction_id == "102"
        apple.state(expires=timedelta(days=60), transactionId="102", autoRenewStatus=0)
        _request(); r.run()
        view = _ent(uid)
        assert _row().auto_renew is False and view["effective_premium"] and view["status"] == "ACTIVE"


def test_expiration_billing_retry_and_grace(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        apple.state(status=3, expires=-timedelta(hours=1), isInBillingRetryPeriod=True)
        _request(); r.run()
        assert (_ent(uid)["status"], _ent(uid)["effective_premium"]) == ("BILLING_RETRY", False)
        grace = ms(datetime.now(timezone.utc) + timedelta(days=6))
        apple.state(status=4, expires=-timedelta(hours=1), isInBillingRetryPeriod=True,
                    gracePeriodExpiresDate=grace)
        _request(); r.run()
        assert (_ent(uid)["status"], _ent(uid)["effective_premium"]) == ("GRACE_PERIOD", True)
        apple.state(status=2, expires=-timedelta(hours=1), isInBillingRetryPeriod=False,
                    gracePeriodExpiresDate=None, autoRenewStatus=0)
        _request(); r.run()
        assert (_ent(uid)["status"], _ent(uid)["effective_premium"]) == ("EXPIRED", False)


def test_grace_requires_signed_grace_evidence(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        apple.state(status=4, expires=-timedelta(hours=1), isInBillingRetryPeriod=True)
        _request()
        assert r.run()["SUBSCRIPTION_INVALID_EVIDENCE"] == 1
        assert _row().status == 1 and Request.query.count() == 0


def test_refund_revokes_and_only_controlled_repurchase_restores(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        revoked = datetime.now(timezone.utc) - timedelta(minutes=5)
        apple.state(status=5, revocationDate=ms(revoked))
        _request(); r.run()
        assert (_ent(uid)["status"], _ent(uid)["effective_premium"]) == ("REVOKED", False)
        # Same transaction no longer revoked (e.g. refund reversal): stays terminal.
        apple.state(status=1, revocationDate=None)
        _request()
        assert r.run()["REVOCATION_TERMINAL"] == 1
        assert _row().status == 5 and Request.query.count() == 0
        # A different transaction bought BEFORE the revocation is not a repurchase.
        apple.state(status=1, revocationDate=None, transactionId="103",
                    purchaseDate=ms(revoked - timedelta(minutes=1)))
        _request()
        assert r.run()["REVOCATION_TERMINAL"] == 1 and _row().status == 5
        # A new transaction bought after the revocation, same owner: controlled repurchase.
        apple.state(status=1, revocationDate=None, transactionId="104",
                    purchaseDate=ms(datetime.now(timezone.utc) - timedelta(seconds=30)))
        _request()
        assert r.run()["APPLIED"] == 1
        assert (_ent(uid)["status"], _ent(uid)["effective_premium"]) == ("ACTIVE", True)
        assert _row().revoked_at is None and _row().latest_transaction_id == "104"


def test_client_replay_cannot_clear_revocation(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        apple.state(status=5, revocationDate=ms(datetime.now(timezone.utc)))
        _request(); r.run()
        apple.state(status=1, revocationDate=None)
        with pytest.raises(SubscriptionError) as error:
            s.verify_purchase(uid, "101")
        assert error.value.code == "SUBSCRIPTION_VERIFICATION_PENDING"
        assert _row().status == 5


def test_repurchase_after_expiration(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        apple.state(status=2, expires=-timedelta(days=3), autoRenewStatus=0)
        _request(); r.run()
        assert _ent(uid)["status"] == "EXPIRED"
        apple.state(status=1, transactionId="105", purchaseDate=ms(datetime.now(timezone.utc)))
        _request(); r.run()
        assert _ent(uid)["effective_premium"] is True


def test_replacement_purchase_by_another_account_cannot_claim(world):
    app, owners, apple = world
    (a_uid, a_token), (b_uid, b_token) = owners
    with app.app_context():
        _owned(a_uid, a_token, apple)
        apple.state(status=2, expires=-timedelta(days=1))
        _request(); r.run()
        # Same Apple ID resubscribes while signed in as B: latest token is B's.
        apple.state(status=1, transactionId="106", appAccountToken=b_token,
                    purchaseDate=ms(datetime.now(timezone.utc)))
        _request(token=b_token)
        assert r.run()["SUBSCRIPTION_OWNERSHIP_CONFLICT"] == 1
        assert _row().user_id == a_uid and _row().status == 2
        assert _ent(a_uid)["effective_premium"] is False and _ent(b_uid)["status"] == "FREE"
        with pytest.raises(SubscriptionError, match="OWNERSHIP_CONFLICT"):
            s.verify_purchase(b_uid, "106")


def test_product_change_within_group_and_outside_it(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        apple.state(productId="annual.fixture", transactionId="107")
        apple.renewal.productId = apple.renewal.autoRenewProductId = "annual.fixture"
        _request(); r.run()
        assert _row().product_id == "annual.fixture" and _ent(uid)["effective_premium"]
        apple.state(subscriptionGroupIdentifier="other.group")
        _request()
        assert r.run()["SUBSCRIPTION_INVALID_EVIDENCE"] == 1
        assert _row().product_id == "annual.fixture"


def test_superseded_and_out_of_order_snapshots(world, monkeypatch):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        later = _row().transaction_signed_at
        # A lookup signed before what is stored: a newer verification already won.
        monkeypatch.setattr(apple, "sign", lambda at=None: Apple.sign(
            apple, datetime.now(timezone.utc) - timedelta(seconds=30)))
        apple.state(status=5, revocationDate=ms(datetime.now(timezone.utc)))
        _request()
        assert r.run()[r.SUPERSEDED] == 1
        assert _row().status == 1 and _row().transaction_signed_at == later and Request.query.count() == 0
        # Only one part older: contradictory ordering, retried later.
        def mixed(at=None):
            apple.tx.signedDate = ms(datetime.now(timezone.utc))
            apple.renewal.signedDate = ms(datetime.now(timezone.utc) - timedelta(seconds=30))
        monkeypatch.setattr(apple, "sign", mixed)
        _request()
        assert r.run()["OUT_OF_ORDER"] == 1
        assert Request.query.one().attempts == 1 and _row().status == 1


def test_stale_snapshot_retries_and_never_extends_access(world, monkeypatch):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        stored = _row().last_verified_at
        monkeypatch.setattr(apple, "sign", lambda at=None: Apple.sign(
            apple, datetime.now(timezone.utc) - timedelta(minutes=10)))
        apple.state(expires=timedelta(days=90))
        _request()
        assert r.run()["STALE_SNAPSHOT"] == 1
        pending = Request.query.one()
        assert pending.attempts == 1 and pending.lease_until is None
        assert s.utc(pending.next_attempt_at) > datetime.now(timezone.utc) + timedelta(seconds=50)
        assert s.utc(_row().last_verified_at) == s.utc(stored)


@pytest.mark.parametrize("failure", [requests.Timeout(), APIException(503), APIException(429)])
def test_apple_outage_backs_off_and_stops_the_pass(world, failure):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        _request(); _request(original="200")
        apple.fail = failure
        result = r.run()
        assert result["processed"] == 1 and result["SUBSCRIPTION_TEMPORARILY_UNAVAILABLE"] == 1
        assert apple.calls == 1  # circuit: the second due request is not attempted
        assert {q.original_transaction_id: q.attempts for q in Request.query} == {"100": 1, "200": 0}
        # Not due yet: an immediate rerun makes no Apple call for it.
        apple.calls = 0
        r.run()
        assert Request.query.filter_by(original_transaction_id="100").one().attempts == 1


def test_retries_are_bounded(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        _request()
        apple.fail = requests.ConnectionError()
        outcomes = []
        for _ in range(r.MAX_ATTEMPTS + 2):
            Request.query.update({Request.next_attempt_at: datetime.now(timezone.utc) - timedelta(seconds=1)})
            db.session.commit()
            outcomes.append(next(k for k in r.run() if k not in ("enqueued", "processed")))
            if not Request.query.count():
                break
        assert outcomes[-1] == r.EXHAUSTED and len(outcomes) == r.MAX_ATTEMPTS
        assert apple.calls == r.MAX_ATTEMPTS


def test_unverifiable_state_expires_into_pending_not_premium(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        row = _row()
        row.last_verified_at = datetime.now(timezone.utc) - timedelta(hours=25)
        db.session.commit()
        apple.fail = requests.Timeout()
        r.run()
        view = _ent(uid)
        assert view["verification_pending"] and not view["effective_premium"]
        assert view["status"] == "VERIFICATION_PENDING"  # unavailable, not EXPIRED


def test_interrupted_worker_resumes_after_lease(world, monkeypatch):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        _request()
        claim = r._claim("Sandbox", datetime.now(timezone.utc))  # worker dies after claiming
        assert claim[2] == 1
        assert r.run()["processed"] == 0 and apple.calls == 0  # lease still held
        Request.query.update({Request.lease_until: datetime.now(timezone.utc) - timedelta(seconds=1)})
        db.session.commit()
        assert r.run()["APPLIED"] == 1 and Request.query.count() == 0


def test_defect_during_processing_is_a_bounded_retry(world, monkeypatch):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        _request(); _request(original="200")
        def broken(*_):
            raise RuntimeError("synthetic defect")
        monkeypatch.setattr(s, "persist_verified", broken)
        result = r.run()
        assert result == {"enqueued": 0, "processed": 1, "RETRY": 1}
        pending = Request.query.filter_by(original_transaction_id="100").one()
        assert pending.lease_until is None and pending.last_outcome == "RETRY"


def test_hint_during_lookup_is_not_lost(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        _request()
        original = apple.a.client.get_all_subscription_statuses
        def notified_meanwhile(original_id):
            _request()  # a new notification commits mid-lookup
            return original(original_id)
        apple.a.client.get_all_subscription_statuses = notified_meanwhile
        assert r.run(limit=1)["APPLIED"] == 1
        pending = Request.query.one()
        assert (pending.generation, pending.attempts, pending.lease_until) == (2, 0, None)


def test_missed_notification_sweep_is_bounded(world):
    app, owners, apple = world
    uid, token = owners[0]
    now = datetime.now(timezone.utc)
    with app.app_context():
        _owned(uid, token, apple)
        base = _row()
        def clone(original, **changes):
            values = {c.name: getattr(base, c.name) for c in AppleSubscription.__table__.columns if c.name != "id"}
            values.update(original_transaction_id=original, **changes)
            db.session.add(AppleSubscription(**values))
        base.last_verified_at = now - timedelta(hours=13)
        clone("300", last_verified_at=now - timedelta(hours=14))
        clone("301", last_verified_at=now - timedelta(hours=1))                      # fresh
        clone("302", last_verified_at=now - timedelta(hours=20), status=5, revoked_at=now)
        clone("303", last_verified_at=now - timedelta(days=90), status=2,
              expires_at=now - timedelta(days=61))                                    # long lapsed
        clone("304", last_verified_at=now - timedelta(hours=13), environment="Production")
        db.session.commit()
        assert r.enqueue_stale("Sandbox", now, limit=1) == 1
        assert [q.original_transaction_id for q in Request.query] == ["300"]
        assert r.enqueue_stale("Sandbox", now, limit=10) == 1
        assert sorted(q.original_transaction_id for q in Request.query) == ["100", "300"]
        assert r.enqueue_stale("Sandbox", now, limit=10) == 0  # already pending: no duplicate


def test_sweep_refreshes_verification(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        _row().last_verified_at = datetime.now(timezone.utc) - timedelta(hours=13)
        db.session.commit()
        result = r.run()
        assert result["enqueued"] == 1 and result["APPLIED"] == 1
        assert datetime.now(timezone.utc) - s.utc(_row().last_verified_at) < timedelta(minutes=1)


def test_environments_are_isolated(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _request(token=token, environment="Production")
        assert r.run()["processed"] == 0 and apple.calls == 0
        assert Request.query.one().environment == "Production"
        _request(token=token)
        r.run()
        assert [row.environment for row in AppleSubscription.query] == ["Sandbox"]


def test_deleted_owner_is_unmatched(world):
    app, owners, apple = world
    uid, token = owners[0]
    with app.app_context():
        _owned(uid, token, apple)
        AppleSubscription.query.delete(); AppleAccountAssociation.query.filter_by(user_id=uid).delete()
        db.session.delete(db.session.get(User, uid)); db.session.commit()
        _request(token=token)
        assert r.run()[r.UNMATCHED] == 1 and apple.calls == 0


@pytest.mark.parametrize("config", [{"APPLE_SUBSCRIPTIONS_ENABLED": False}, {"APPLE_BUNDLE_ID": ""}])
def test_disabled_or_unconfigured_worker_refuses(app, monkeypatch, config):
    from app.services.apple_subscriptions import AppleAdapter
    monkeypatch.setattr(r, "AppleAdapter", AppleAdapter)
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True)
    app.config.update(config)
    with app.app_context():
        _request(token=str(uuid4()))
        with pytest.raises(r.ReconciliationDisabled):
            r.run()
        assert Request.query.one().attempts == 0


def test_cli_reports_closed_counts(world):
    app, owners, apple = world
    with app.app_context():
        _request(token=owners[0][1])
    result = app.test_cli_runner().invoke(args=["apple-subscription-reconcile", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "APPLIED=1 enqueued=0 processed=1"
    app.config["APPLE_SUBSCRIPTIONS_ENABLED"] = False
    refused = app.test_cli_runner().invoke(args=["apple-subscription-reconcile"])
    assert refused.exit_code != 0 and "SUBSCRIPTIONS_DISABLED" in refused.output
