"""MON-02-BE-03A ledger semantics (offline, SQLite). PG races: test_ai_allowances_pg.py."""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from ai_allowance_support import (
    BALANCED_STARTER_TEST_FIXTURE, LEGACY_CREATED_AT, PREMIUM_TEST_FIXTURE, enable_ledger,
    enable_subscriptions, fixture_policy, premium_row,
)
from app.extensions import db
from app.models import AiAllowanceBucket, AiStarterGrant, AiUsageReservation, AppleSubscription, User
from app.services import ai_allowances as A
from app.services import premium
from app.services.ai_allowances import policy as P
from app.services.ai_allowances import service as S

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


def code(excinfo):
    return excinfo.value.code


def counts():
    return tuple(db.session.query(m).count() for m in (AiStarterGrant, AiAllowanceBucket, AiUsageReservation))


def bucket(uid, feature, scope="STARTER"):
    return db.session.execute(sa.select(AiAllowanceBucket).where(
        AiAllowanceBucket.user_id == uid, AiAllowanceBucket.feature == feature,
        AiAllowanceBucket.scope == scope).execution_options(populate_existing=True)).scalar_one()


@pytest.fixture
def owner(make_user):
    return make_user("ledger-owner").id


@pytest.fixture
def other(make_user):
    return make_user("ledger-other").id


# ── Flag OFF / unconfigured ───────────────────────────────────────────────
def test_flag_off_refuses_every_entry_point_and_writes_nothing(app, owner):
    app.config["AI_ALLOWANCE_POLICY"] = fixture_policy()
    assert app.config["AI_ALLOWANCE_LEDGER_ENABLED"] is False
    calls = [lambda: A.reserve(owner, "coach", "key-00000001"),
             lambda: A.finalize(owner, "0" * 32, "success"),
             lambda: A.grant_starter(owner), lambda: A.allowance_view(owner),
             lambda: A.expire_abandoned(), lambda: A.purge_retained()]
    for call in calls:
        with pytest.raises(A.AllowanceError) as error:
            call()
        assert code(error) == "AI_ALLOWANCE_DISABLED"
    assert counts() == (0, 0, 0)


@pytest.mark.parametrize("ledger_on", [False, True])
def test_legacy_weekly_quota_is_identical_in_both_flag_states(app, make_user, ledger_on):
    app.config["AI_ALLOWANCE_LEDGER_ENABLED"] = ledger_on
    app.config["AI_ALLOWANCE_POLICY"] = fixture_policy() if ledger_on else None
    user = make_user(f"legacy-{ledger_on}")
    assert premium.reserve_ai_quota(user, "chat", 2) is True
    assert premium.reserve_ai_quota(user, "chat", 2) is True
    assert premium.reserve_ai_quota(user, "chat", 2) is False
    premium.refund_ai_quota(user, "chat")
    db.session.refresh(user)
    assert user.user_metadata["ai_plan_quota"]["chat"] == 1
    assert premium.remaining_ai_chats(user) == premium.FREE_WEEKLY_AI_CHATS - 1
    assert counts() == (0, 0, 0)


def test_unconfigured_policy_is_explicit_never_unlimited_or_zero(app, owner):
    enable_ledger(app, policy=None)
    app.config["AI_ALLOWANCE_POLICY"] = None
    with pytest.raises(A.AllowanceError) as error:
        A.reserve(owner, "coach", "key-00000001")
    assert code(error) == "AI_ALLOWANCE_POLICY_UNCONFIGURED"
    view = A.allowance_view(owner)
    assert view["policy_state"] == "UNCONFIGURED" and view["access"] == "UNCONFIGURED"
    assert view["starter"]["state"] == "UNCONFIGURED"
    for item in view["features"]:
        assert item["state"] == "NOT_CONFIGURED"
        assert [item[k] for k in ("limit", "used", "reserved", "remaining", "resets_at")] == [None] * 5
    assert counts() == (0, 0, 0)


def test_a_non_policy_object_is_refused(app, owner):
    enable_ledger(app)
    app.config["AI_ALLOWANCE_POLICY"] = {"starter": {"coach": 10}}
    with pytest.raises(A.AllowanceError) as error:
        A.allowance_view(owner)
    assert code(error) == "AI_ALLOWANCE_POLICY_UNCONFIGURED"


@pytest.mark.parametrize("build", [
    lambda: A.ScopePolicy("WEEKLY", {"coach": 1}, 0) and A.AllowancePolicy(
        "v", starter=A.ScopePolicy("WEEKLY", {"coach": 1}, 0),
        starter_eligible_from=datetime(2026, 1, 1, tzinfo=timezone.utc)),
    lambda: A.ScopePolicy("NEVER", {"pump_comparison": 1}, 0),
    lambda: A.ScopePolicy("NEVER", {"coach": True}, 0),
    lambda: A.ScopePolicy("NEVER", {"coach": 0}, 0),
    lambda: A.ScopePolicy("NEVER", {}, 0),
    lambda: A.ScopePolicy("YEARLY", {"coach": 1}, 0),
    lambda: A.AllowancePolicy("v", premium=A.ScopePolicy("NEVER", {"coach": 1}, 0)),
    lambda: A.AllowancePolicy("v", starter=A.ScopePolicy("NEVER", {"coach": 1}, 0),
                              starter_eligible_from=datetime(2026, 1, 1)),
    lambda: A.AllowancePolicy("bad version!"),
])
def test_malformed_policy_is_refused(build):
    with pytest.raises(P.PolicyError):
        build()


# ── Feature taxonomy ──────────────────────────────────────────────────────
def test_taxonomy_maps_only_decided_families():
    assert A.SUPPORTED_FEATURES == ("coach", "training_generation", "nutrition_generation",
                                    "pump_check", "qr_menu")
    assert A.canonical_feature("web_coach") == "coach"
    assert A.canonical_feature("web_nutrition_generation") == "nutrition_generation"
    for name in ("training_replacement", "pump_comparison", "legacy_web_ai",
                 "workout_photo_proof", "coach_summary", "unknown", None):
        with pytest.raises(P.UnsupportedFeature):
            A.canonical_feature(name)
    assert A.EXTENSIONS["workout_photo_proof"].disposition == "EXEMPT"


def test_unsupported_feature_and_bad_keys_are_refused_before_any_write(app, owner):
    enable_ledger(app)
    with pytest.raises(A.AllowanceError) as error:
        A.reserve(owner, "pump_comparison", "key-00000001")
    assert code(error) == "AI_ALLOWANCE_FEATURE_UNSUPPORTED"
    for key in (None, "short", "x" * 129, "has space-1", "ğğğğğğğğ", 12345678):
        with pytest.raises(A.AllowanceError) as error:
            A.reserve(owner, "coach", key)
        assert code(error) == "AI_ALLOWANCE_INVALID_REQUEST"
    assert counts() == (0, 0, 0)


def test_family_alias_consumes_the_family_unit(app, owner):
    enable_ledger(app)
    held = A.reserve(owner, "web_coach", "web-key-0001", now=NOW)
    assert held.feature == "coach" and held.may_execute
    assert bucket(owner, "coach").reserved == 1


# ── Exactly-once Starter grant ───────────────────────────────────────────
def test_new_account_is_granted_exactly_once_with_snapshotted_quantities(app, owner):
    enable_ledger(app)
    assert A.grant_starter(owner, now=NOW) == "GRANTED"
    assert A.grant_starter(owner, now=NOW) == "ALREADY_GRANTED"
    assert counts() == (1, 5, 0)
    assert {b.feature: b.granted for b in AiAllowanceBucket.query} == BALANCED_STARTER_TEST_FIXTURE
    grant = db.session.get(AiStarterGrant, owner)
    assert grant.policy_version == "test-balanced-v1"
    # A later policy never rewrites what was granted.
    app.config["AI_ALLOWANCE_POLICY"] = fixture_policy(version="v2", starter_units={"coach": 99})
    assert A.grant_starter(owner, now=NOW) == "ALREADY_GRANTED"
    assert bucket(owner, "coach").granted == 10
    assert A.allowance_view(owner, now=NOW)["features"][0]["limit"] == 10


def test_existing_accounts_are_never_granted_automatically(app, make_user):
    enable_ledger(app)
    legacy = make_user("legacy-account", created_at=LEGACY_CREATED_AT).id
    unknown = make_user("unknown-created").id
    db.session.get(User, unknown).created_at = None
    db.session.commit()
    for uid in (legacy, unknown):
        assert A.grant_starter(uid, now=NOW) == "NOT_ELIGIBLE"
        with pytest.raises(A.AllowanceError) as error:
            A.reserve(uid, "coach", f"key-{uid:08d}", now=NOW)
        assert code(error) == "AI_FEATURE_NOT_IN_PLAN"
        view = A.allowance_view(uid, now=NOW)
        assert view["access"] == "NONE" and view["starter"]["state"] == "NOT_ELIGIBLE"
        assert {f["state"] for f in view["features"]} == {"NOT_IN_PLAN"}
    assert counts() == (0, 0, 0)


def test_missing_account_and_rollback_on_database_failure(app, owner, monkeypatch):
    enable_ledger(app)
    with pytest.raises(A.AllowanceError) as error:
        A.grant_starter(10_000)
    assert code(error) == "AI_ALLOWANCE_ACCOUNT_UNAVAILABLE"

    def broken_bucket(*args, **kwargs):
        raise sa.exc.OperationalError("INSERT", {}, Exception("disk full"))
    monkeypatch.setattr(S, "_new_bucket", broken_bucket)
    with pytest.raises(A.AllowanceError) as error:
        A.grant_starter(owner, now=NOW)
    assert code(error) == "AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE"
    assert counts() == (0, 0, 0)


def test_grant_without_starter_scope_is_not_in_plan(app, owner):
    enable_ledger(app, policy=fixture_policy(starter=False, premium=True))
    with pytest.raises(A.AllowanceError) as error:
        A.grant_starter(owner)
    assert code(error) == "AI_FEATURE_NOT_IN_PLAN"


# ── Reservation lifecycle ────────────────────────────────────────────────
def test_reserve_commit_and_idempotent_replay_never_double_spend(app, owner):
    enable_ledger(app)
    held = A.reserve(owner, "coach", "turn-000001", now=NOW)
    assert held.may_execute and held.scope == "STARTER" and held.state == "RESERVED"
    assert counts() == (1, 5, 1)  # lazy grant inside the first reservation
    with pytest.raises(A.AllowanceError) as error:
        A.reserve(owner, "coach", "turn-000001", now=NOW)
    assert code(error) == "AI_ALLOWANCE_IN_PROGRESS" and error.value.retryable
    done = A.finalize(owner, held.reference, "success", now=NOW)
    assert (done.state, done.consumed, done.replayed) == ("COMMITTED", True, False)
    again = A.finalize(owner, held.reference, "success", now=NOW)
    assert (again.state, again.replayed) == ("COMMITTED", True)
    replay = A.reserve(owner, "coach", "turn-000001", now=NOW)
    assert replay.replayed and replay.state == "COMMITTED" and not replay.may_execute
    with pytest.raises(A.AllowanceError) as error:
        A.finalize(owner, held.reference, "provider_error", now=NOW)
    assert code(error) == "AI_ALLOWANCE_RESERVATION_CONFLICT"
    b = bucket(owner, "coach")
    assert (b.used, b.reserved) == (1, 0)
    assert counts() == (1, 5, 1)


def test_key_reuse_for_another_feature_conflicts(app, owner):
    enable_ledger(app)
    A.reserve(owner, "coach", "shared-key-1", now=NOW)
    with pytest.raises(A.AllowanceError) as error:
        A.reserve(owner, "qr_menu", "shared-key-1", now=NOW)
    assert code(error) == "AI_ALLOWANCE_IDEMPOTENCY_CONFLICT"


def test_starter_is_lifetime_and_exhausts_without_a_reset(app, owner):
    enable_ledger(app)
    for i in range(10):
        held = A.reserve(owner, "coach", f"coach-{i:06d}", now=NOW)
        A.finalize(owner, held.reference, "success", now=NOW)
    for later in (NOW, NOW + timedelta(days=8), NOW + timedelta(days=40), NOW + timedelta(days=400)):
        with pytest.raises(A.AllowanceError) as error:
            A.reserve(owner, "coach", f"late-{later.toordinal()}", now=later)
        assert code(error) == "AI_ALLOWANCE_EXHAUSTED"
        assert error.value.details == {"feature": "coach", "scope": "STARTER",
                                       "reset": "NEVER", "resets_at": None}
    item = A.allowance_view(owner, now=NOW)["features"][0]
    assert (item["state"], item["remaining"], item["used"], item["reset"], item["resets_at"]) == (
        "EXHAUSTED", 0, 10, "NEVER", None)


def test_qualifying_failures_release_and_billed_failures_are_bounded(app, owner):
    enable_ledger(app, policy=fixture_policy(max_billed=2))
    results = []
    for i, (outcome, billed) in enumerate([
            ("refused_before_provider", False), ("cancelled_before_delivery", False),
            ("provider_error", True), ("provider_timeout", True), ("invalid_output", True)]):
        held = A.reserve(owner, "coach", f"fail-{i:06d}", now=NOW)
        results.append(A.finalize(owner, held.reference, outcome, provider_billed=billed, now=NOW))
    assert [(r.state, r.consumed) for r in results] == [
        ("RELEASED", False), ("RELEASED", False), ("RELEASED", False),
        ("RELEASED", False), ("COMMITTED", True)]
    b = bucket(owner, "coach")
    assert (b.used, b.reserved, b.billed_releases) == (1, 0, 2)
    rows = AiUsageReservation.query.order_by(AiUsageReservation.id).all()
    assert [r.provider_billed for r in rows] == [False, False, True, True, True]
    assert [r.outcome for r in rows][-1] == "invalid_output"
    with pytest.raises(A.AllowanceError) as error:
        A.finalize(owner, rows[0].public_id, "refused_before_provider", provider_billed=True, now=NOW)
    assert code(error) == "AI_ALLOWANCE_INVALID_REQUEST"
    released = A.reserve(owner, "coach", "fail-000000", now=NOW)
    assert released.replayed and released.state == "RELEASED" and not released.may_execute


@pytest.mark.parametrize("bad", [
    dict(reference="0" * 31, outcome="success"), dict(reference="Z" * 32, outcome="success"),
    dict(reference="0" * 32, outcome="partial"), dict(reference="0" * 32, outcome="success",
                                                      provider_billed=1)])
def test_finalize_rejects_malformed_input(app, owner, bad):
    enable_ledger(app)
    with pytest.raises(A.AllowanceError) as error:
        A.finalize(owner, **bad)
    assert code(error) == "AI_ALLOWANCE_INVALID_REQUEST"


def test_abandoned_holds_expire_as_consumed_and_never_mint_free_retries(app, owner):
    enable_ledger(app)
    held = A.reserve(owner, "pump_check", "pump-000001", now=NOW)
    later = NOW + A.RESERVATION_TTL + timedelta(seconds=1)
    replay = A.reserve(owner, "pump_check", "pump-000001", now=later)
    assert (replay.state, replay.replayed, replay.may_execute) == ("EXPIRED", True, False)
    with pytest.raises(A.AllowanceError) as error:
        A.reserve(owner, "pump_check", "pump-000002", now=later)
    assert code(error) == "AI_ALLOWANCE_EXHAUSTED"
    late = A.finalize(owner, held.reference, "provider_error", provider_billed=True, now=later)
    assert (late.state, late.consumed, late.replayed) == ("EXPIRED", True, True)
    b = bucket(owner, "pump_check")
    assert (b.used, b.reserved) == (1, 0)


def test_sweep_settles_abandoned_holds_across_owners(app, owner, other):
    enable_ledger(app)
    A.reserve(owner, "coach", "sweep-00001", now=NOW)
    A.reserve(other, "coach", "sweep-00001", now=NOW)
    fresh = A.reserve(owner, "qr_menu", "sweep-00002", now=NOW + A.RESERVATION_TTL - timedelta(seconds=1))
    assert A.expire_abandoned(now=NOW + A.RESERVATION_TTL + timedelta(seconds=1)) == 2
    states = {(r.user_id, r.feature): r.state for r in AiUsageReservation.query}
    assert states == {(owner, "coach"): "EXPIRED", (other, "coach"): "EXPIRED",
                      (owner, "qr_menu"): "RESERVED"}
    assert fresh.may_execute
    assert bucket(owner, "coach").used == 1 and bucket(other, "coach").used == 1


def test_lazy_expiry_on_the_next_reservation(app, owner):
    enable_ledger(app)
    A.reserve(owner, "coach", "lazy-000001", now=NOW)
    A.reserve(owner, "coach", "lazy-000002", now=NOW + timedelta(hours=1))
    first = AiUsageReservation.query.order_by(AiUsageReservation.id).first()
    assert first.state == "EXPIRED"
    b = bucket(owner, "coach")
    assert (b.used, b.reserved) == (1, 1)


# ── Cross-account isolation ──────────────────────────────────────────────
def test_cross_account_reference_reuse_is_refused_and_keys_are_owner_scoped(app, owner, other):
    enable_ledger(app)
    mine = A.reserve(owner, "coach", "same-key-01", now=NOW)
    theirs = A.reserve(other, "coach", "same-key-01", now=NOW)
    assert mine.reference != theirs.reference and theirs.may_execute
    with pytest.raises(A.AllowanceError) as error:
        A.finalize(other, mine.reference, "success", now=NOW)
    assert code(error) == "AI_ALLOWANCE_RESERVATION_NOT_FOUND"
    assert db.session.execute(sa.select(AiUsageReservation.state).where(
        AiUsageReservation.public_id == mine.reference)).scalar_one() == "RESERVED"
    A.finalize(owner, mine.reference, "success", now=NOW)
    assert A.allowance_view(other, now=NOW)["features"][0]["used"] == 0
    assert A.allowance_view(owner, now=NOW)["features"][0]["used"] == 1
    reservation = db.session.execute(sa.select(AiUsageReservation).where(
        AiUsageReservation.public_id == mine.reference)).scalar_one()
    assert reservation.key_digest != "same-key-01" and len(reservation.key_digest) == 64


# ── Premium via the #436 entitlement ─────────────────────────────────────
def test_premium_uses_finite_premium_allowance_and_never_unlimited(app, owner, monkeypatch):
    enable_subscriptions(app, monkeypatch)
    enable_ledger(app, policy=fixture_policy(premium=True))
    premium_row(owner)
    now = datetime.now(timezone.utc)
    held = A.reserve(owner, "coach", "prem-000001", now=now)
    assert held.scope == "PREMIUM"
    A.finalize(owner, held.reference, "success", now=now)
    view = A.allowance_view(owner, now=now)
    assert view["access"] == "PREMIUM"
    assert view["subscription"]["state"] == "AVAILABLE"
    assert view["subscription"]["entitlement"]["effective_premium"] is True
    coach = view["features"][0]
    assert (coach["scope"], coach["limit"], coach["used"], coach["reset"]) == ("PREMIUM", 3, 1, "WEEKLY")
    assert coach["resets_at"] == S._iso(P.resets_at("WEEKLY", now))
    assert {f["feature"]: f["state"] for f in view["features"]}["pump_check"] == "NOT_IN_PLAN"
    with pytest.raises(A.AllowanceError) as error:
        A.reserve(owner, "pump_check", "prem-000002", now=now)
    assert code(error) == "AI_FEATURE_NOT_IN_PLAN" and error.value.details["scope"] == "PREMIUM"
    for i in range(2):
        A.finalize(owner, A.reserve(owner, "coach", f"prem-1{i:05d}", now=now).reference, "success", now=now)
    with pytest.raises(A.AllowanceError) as error:
        A.reserve(owner, "coach", "prem-000099", now=now)
    assert code(error) == "AI_ALLOWANCE_EXHAUSTED" and error.value.details["reset"] == "WEEKLY"
    assert error.value.details["resets_at"] is not None
    # Starter is untouched by Premium consumption.
    assert db.session.get(AiStarterGrant, owner) is None


def test_premium_without_premium_policy_is_not_in_plan(app, owner, monkeypatch):
    enable_subscriptions(app, monkeypatch)
    enable_ledger(app, policy=fixture_policy(premium=False))
    premium_row(owner)
    with pytest.raises(A.AllowanceError) as error:
        A.reserve(owner, "coach", "prem-000001")
    assert code(error) == "AI_FEATURE_NOT_IN_PLAN"
    view = A.allowance_view(owner)
    assert view["access"] == "PREMIUM"
    assert {(f["scope"], f["state"], f["limit"]) for f in view["features"]} == {("PREMIUM", "NOT_IN_PLAN", None)}


@pytest.mark.parametrize("row", [
    dict(expires_in=timedelta(seconds=-1)),                          # expired
    dict(verified_ago=timedelta(hours=25)),                          # verification stale
    dict(status=5),                                                  # revoked
])
def test_premium_expiry_and_staleness_propagate_to_starter(app, owner, monkeypatch, row):
    enable_subscriptions(app, monkeypatch)
    enable_ledger(app, policy=fixture_policy(premium=True))
    premium_row(owner, **row)
    held = A.reserve(owner, "coach", "after-expiry")
    assert held.scope == "STARTER"
    assert A.allowance_view(owner)["access"] == "STARTER"


def test_legacy_is_premium_column_is_never_an_entitlement(app, make_user, monkeypatch):
    enable_subscriptions(app, monkeypatch)
    enable_ledger(app, policy=fixture_policy(premium=True))
    uid = make_user("legacy-premium", is_premium=True).id
    assert A.reserve(uid, "coach", "legacy-prem1").scope == "STARTER"
    assert db.session.get(User, uid).is_premium is True


def test_subscription_disabled_is_decided_and_other_failures_fail_closed(app, owner, monkeypatch):
    enable_ledger(app, policy=fixture_policy(premium=True))
    view = A.allowance_view(owner)
    assert view["subscription"] == {"state": "DISABLED", "entitlement": None}
    assert view["access"] == "STARTER"

    def unavailable(_):
        raise S.SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503)
    monkeypatch.setattr(S.subscriptions, "entitlement", unavailable)
    for call in (lambda: A.allowance_view(owner), lambda: A.reserve(owner, "coach", "closed-0001")):
        with pytest.raises(A.AllowanceError) as error:
            call()
        assert code(error) == "AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE" and error.value.retryable
    assert counts() == (0, 0, 0)


# ── Read view ────────────────────────────────────────────────────────────
def test_view_is_read_only_and_reports_eligible_balances(app, owner):
    enable_ledger(app)
    view = A.allowance_view(owner, now=NOW)
    assert counts() == (0, 0, 0)
    assert view["access"] == "STARTER" and view["starter"] == {"state": "ELIGIBLE", "granted_at": None}
    assert [(f["feature"], f["limit"], f["used"], f["remaining"], f["reset"], f["resets_at"])
            for f in view["features"]] == [
        (name, units, 0, units, "NEVER", None) for name, units in BALANCED_STARTER_TEST_FIXTURE.items()]
    held = A.reserve(owner, "qr_menu", "menu-000001", now=NOW)
    view = A.allowance_view(owner, now=NOW)
    assert view["starter"] == {"state": "GRANTED", "granted_at": "2026-10-14T09:00:00Z"}
    menu = {f["feature"]: f for f in view["features"]}["qr_menu"]
    assert (menu["state"], menu["reserved"], menu["remaining"]) == ("EXHAUSTED", 1, 0)
    A.finalize(owner, held.reference, "provider_timeout", provider_billed=True, now=NOW)
    menu = {f["feature"]: f for f in A.allowance_view(owner, now=NOW)["features"]}["qr_menu"]
    assert (menu["state"], menu["reserved"], menu["remaining"]) == ("AVAILABLE", 0, 1)


# ── Periods ──────────────────────────────────────────────────────────────
def test_period_keys_follow_the_istanbul_calendar():
    sunday_late = datetime(2026, 10, 18, 20, 59, tzinfo=timezone.utc)   # 23:59 Istanbul
    monday = datetime(2026, 10, 18, 21, 0, tzinfo=timezone.utc)         # 00:00 Istanbul
    assert P.period_key("WEEKLY", sunday_late) == "2026-W42"
    assert P.period_key("WEEKLY", monday) == "2026-W43"
    assert P.resets_at("WEEKLY", sunday_late) == monday
    assert P.period_key("MONTHLY", datetime(2026, 12, 31, 21, 0, tzinfo=timezone.utc)) == "2027-01"
    assert P.resets_at("MONTHLY", datetime(2026, 12, 10, tzinfo=timezone.utc)) == datetime(
        2026, 12, 31, 21, 0, tzinfo=timezone.utc)
    assert P.period_key("NEVER", monday) == "NEVER" and P.resets_at("NEVER", monday) is None


# ── Deletion and retention ───────────────────────────────────────────────
def test_account_deletion_removes_every_ledger_row_of_that_owner_only(app, owner, other):
    from app.cli import _purge_user
    enable_ledger(app)
    for uid in (owner, other):
        A.finalize(uid, A.reserve(uid, "coach", "del-0000001", now=NOW).reference, "success", now=NOW)
        A.reserve(uid, "qr_menu", "del-0000002", now=NOW)
    _purge_user(db.session.get(User, owner))
    db.session.commit()
    for model in (AiStarterGrant, AiAllowanceBucket, AiUsageReservation):
        assert model.query.filter_by(user_id=owner).count() == 0
        assert model.query.filter_by(user_id=other).count() > 0
    with pytest.raises(A.AllowanceError) as error:
        A.reserve(owner, "coach", "del-0000003", now=NOW)
    assert code(error) == "AI_ALLOWANCE_ACCOUNT_UNAVAILABLE"


def test_retention_purges_settled_history_and_past_premium_periods_only(app, owner, monkeypatch):
    enable_subscriptions(app, monkeypatch)
    enable_ledger(app, policy=fixture_policy(premium=True))
    premium_row(owner)
    old = datetime.now(timezone.utc) - timedelta(days=120)
    A.finalize(owner, A.reserve(owner, "coach", "old-prem-001", now=old).reference, "success", now=old)
    db.session.query(AppleSubscription).delete()  # Premium lapses
    db.session.commit()
    A.finalize(owner, A.reserve(owner, "coach", "old-strt-001", now=old).reference, "success", now=old)
    open_hold = A.reserve(owner, "qr_menu", "open-hold-01")
    settled, periods = A.purge_retained()
    assert (settled, periods) == (2, 1)
    assert {b.scope for b in AiAllowanceBucket.query} == {"STARTER"}
    assert [r.public_id for r in AiUsageReservation.query] == [open_hold.reference]
    assert bucket(owner, "coach").used == 1  # lifetime Starter balance retained



def test_success_preserves_provider_billing_evidence_on_replay(app, owner):
    enable_ledger(app)
    held = A.reserve(owner, "coach", "billed-success", now=NOW)
    A.finalize(owner, held.reference, "success", provider_billed=True, now=NOW)
    A.finalize(owner, held.reference, "success", provider_billed=True, now=NOW)
    row = AiUsageReservation.query.filter_by(public_id=held.reference).one()
    assert row.provider_billed is True
    assert (bucket(owner, "coach").used, bucket(owner, "coach").reserved) == (1, 0)
