"""AXISAI-LAUNCH-FREE-BE-01: the finite LAUNCH_FREE campaign scope.

Ledger tests pass explicit instants. Execution tests use a synthetic campaign
anchored at today's Istanbul midnight, so the real clock is inside a window.
Consent/provider decision points are faked exactly as in the existing
execution suites; no provider is called and nothing is activated.
"""
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from types import MappingProxyType

import pytest

from ai_allowance_support import enable_ledger, enable_subscriptions, premium_row
from app.extensions import db
from app.models import AiAllowanceBucket, AiStarterGrant, AiUsageReservation, AppleSubscription, User
from app.services import ai_consent, ai_launch, subscriptions
from app.services.ai_allowances import execution as E, launch_policy, policy as P, service as L
from app.services.ai_allowances.errors import AllowanceError
from app.services.ai_allowances.policy import (
    AllowancePolicy, CampaignPolicy, PolicyError, ScopePolicy, SUPPORTED_FEATURES)
from app.services.apple_subscriptions import SubscriptionError
from app.timeutil import APP_TZ

CAMPAIGN = launch_policy.LAUNCH_FREE_CAMPAIGN
LF1, LF2, LF3, END = (CAMPAIGN.boundary(n) for n in range(4))
US = timedelta(microseconds=1)
EXPECTED = {"coach": 30, "training_generation": 2, "nutrition_generation": 2,
            "pump_check": 20, "qr_menu": 5}


def campaign_policy(**changes):
    """The approved shape under synthetic versions (never the shipping values)."""
    return replace(launch_policy.LAUNCH_V1_POLICY, version="synthetic-lf-base",
                   launch_free=replace(CAMPAIGN, version="synthetic-lf", **changes))


def today_policy():
    start = datetime.combine(datetime.now(APP_TZ).date(), time(0), tzinfo=APP_TZ)
    return campaign_policy(starts_at=start)


@pytest.fixture
def apple_off(app, make_user):
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=False)
    enable_ledger(app, campaign_policy())
    return make_user("launch-free-owner").id


def reserve(owner, feature, key, now):
    return L.reserve(owner, feature, key, now=now)


def bucket(owner, feature, scope="LAUNCH_FREE", key=None):
    db.session.expire_all()
    query = AiAllowanceBucket.query.filter_by(user_id=owner, feature=feature, scope=scope)
    if key is not None:
        query = query.filter_by(period_key=key)
    return query.one_or_none()


# ── Policy and time boundaries ─────────────────────────────────────────────
def test_exact_source_policy_and_unchanged_existing_scopes():
    policy = launch_policy.LAUNCH_V1_POLICY
    assert dict(policy.launch_free.allowances.allowances) == EXPECTED
    assert list(policy.launch_free.allowances.allowances) == list(SUPPORTED_FEATURES)
    assert policy.launch_free.windows == 3 and policy.launch_free.allowances.period == "CAMPAIGN_WINDOW"
    assert policy.launch_free.version == "launch-free-v1" != policy.version
    assert policy.version == "launch-v1-usable-result"
    assert list(policy.starter.allowances.values()) == [10, 1, 1, 1, 1]
    assert list(policy.premium.allowances.values()) == [60, 2, 2, 16, 2]
    assert policy.starter.period == "NEVER" and policy.premium.period == "MONTHLY"
    assert policy.bucket_version("STARTER") == policy.bucket_version("PREMIUM") == policy.version
    assert policy.bucket_version("LAUNCH_FREE") == "launch-free-v1"
    assert policy.scope("LAUNCH_FREE") is policy.launch_free.allowances
    assert policy.scope("UNKNOWN") is None
    with pytest.raises(TypeError):
        policy.launch_free.allowances.allowances["coach"] = 10_000


def test_window_boundaries_are_istanbul_midnights():
    expected = [datetime(2026, 11, 25), datetime(2026, 12, 25), datetime(2027, 1, 24), datetime(2027, 2, 23)]
    for n, local in enumerate(expected):
        boundary = CAMPAIGN.boundary(n)
        assert boundary.tzinfo is not None and boundary.utcoffset() == timedelta(0)
        assert boundary.astimezone(APP_TZ).replace(tzinfo=None) == local
        assert boundary == local.replace(tzinfo=timezone.utc) - timedelta(hours=3)
    assert CAMPAIGN.ends_at == END


@pytest.mark.parametrize("instant,key,reset,resets_at", [
    (LF1 - US, None, None, None),
    (LF1, "LF1", "CAMPAIGN_WINDOW", LF2),
    (LF2 - US, "LF1", "CAMPAIGN_WINDOW", LF2),
    (LF2, "LF2", "CAMPAIGN_WINDOW", LF3),
    (LF3 - US, "LF2", "CAMPAIGN_WINDOW", LF3),
    (LF3, "LF3", "CAMPAIGN_END", None),
    (END - US, "LF3", "CAMPAIGN_END", None),
    (END, None, None, None),
    (END + timedelta(days=400), None, None, None),
])
def test_period_key_and_reset_at_every_boundary(instant, key, reset, resets_at):
    assert P.campaign_period_key(CAMPAIGN, instant) == key
    assert P.campaign_reset(CAMPAIGN, instant) == (reset, resets_at)
    # Any offset denotes the same instant; the window never follows client time.
    assert P.campaign_period_key(CAMPAIGN, instant.astimezone(timezone(timedelta(hours=-10)))) == key


def test_local_midnight_not_utc_midnight_opens_the_campaign():
    assert P.campaign_period_key(CAMPAIGN, datetime(2026, 11, 24, 23, 0, tzinfo=timezone.utc)) == "LF1"
    assert P.campaign_period_key(CAMPAIGN, datetime(2026, 11, 24, 20, 59, 59, tzinfo=timezone.utc)) is None
    with pytest.raises(PolicyError):
        P.campaign_period_key(CAMPAIGN, datetime(2026, 11, 26))  # naive: refused, not guessed
    assert P.campaign_period_key(None, LF1) is None


def _units(**overrides):
    return {**EXPECTED, **overrides}


@pytest.mark.parametrize("build", [
    lambda: CampaignPolicy("lf", datetime(2026, 11, 25), 3, ScopePolicy("CAMPAIGN_WINDOW", _units(), 0)),
    lambda: CampaignPolicy("lf", datetime(2026, 11, 25, 1, tzinfo=APP_TZ), 3,
                           ScopePolicy("CAMPAIGN_WINDOW", _units(), 0)),
    lambda: CampaignPolicy("lf", datetime(2026, 11, 25, tzinfo=timezone.utc), 3,
                           ScopePolicy("CAMPAIGN_WINDOW", _units(), 0)),
    lambda: CampaignPolicy("lf", LF1, 0, ScopePolicy("CAMPAIGN_WINDOW", _units(), 0)),
    lambda: CampaignPolicy("lf", LF1, 13, ScopePolicy("CAMPAIGN_WINDOW", _units(), 0)),
    lambda: CampaignPolicy("lf", LF1, True, ScopePolicy("CAMPAIGN_WINDOW", _units(), 0)),
    lambda: CampaignPolicy("lf", LF1, 3, ScopePolicy("MONTHLY", _units(), 0)),
    lambda: CampaignPolicy("lf", LF1, 3, {"coach": 30}),
    lambda: CampaignPolicy("bad version!", LF1, 3, ScopePolicy("CAMPAIGN_WINDOW", _units(), 0)),
    lambda: ScopePolicy("CAMPAIGN_WINDOW", _units(coach=0), 0),
    lambda: ScopePolicy("CAMPAIGN_WINDOW", _units(coach=-1), 0),
    lambda: ScopePolicy("CAMPAIGN_WINDOW", _units(coach=30.0), 0),
    lambda: ScopePolicy("CAMPAIGN_WINDOW", _units(coach=None), 0),
    lambda: ScopePolicy("CAMPAIGN_WINDOW", _units(coach=10_001), 0),
    lambda: ScopePolicy("CAMPAIGN_WINDOW", {**_units(), "unlimited": 1}, 0),
    lambda: replace(launch_policy.LAUNCH_V1_POLICY, launch_free={"coach": 30}),
    lambda: replace(launch_policy.LAUNCH_V1_POLICY,
                    launch_free=replace(CAMPAIGN, version=launch_policy.LAUNCH_V1_POLICY.version)),
    lambda: replace(launch_policy.LAUNCH_V1_POLICY,
                    premium=ScopePolicy("CAMPAIGN_WINDOW", _units(), 0)),
    lambda: replace(launch_policy.LAUNCH_V1_POLICY,
                    starter=ScopePolicy("CAMPAIGN_WINDOW", _units(), 0)),
])
def test_invalid_campaign_configuration_fails_closed(build):
    with pytest.raises(PolicyError):
        build()


def test_installer_refuses_an_incomplete_campaign(monkeypatch):
    partial = replace(CAMPAIGN, allowances=ScopePolicy("CAMPAIGN_WINDOW", {"coach": 30}, 0))
    monkeypatch.setattr(launch_policy, "APPROVED_POLICY",
                        replace(launch_policy.LAUNCH_V1_POLICY, launch_free=partial))
    with pytest.raises(PolicyError):
        launch_policy.installed_policy()
    monkeypatch.setattr(launch_policy, "APPROVED_POLICY", launch_policy.LAUNCH_V1_POLICY)
    assert launch_policy.installed_policy().launch_free is CAMPAIGN


def test_no_environment_or_request_channel_selects_campaign_values(app, monkeypatch):
    monkeypatch.setenv("LAUNCH_FREE_STARTS_AT", "2020-01-01")
    monkeypatch.setenv("AI_ALLOWANCE_POLICY", '{"launch_free": "unlimited"}')
    monkeypatch.setattr(launch_policy, "APPROVED_POLICY", launch_policy.LAUNCH_V1_POLICY)
    assert launch_policy.installed_policy().launch_free.starts_at == LF1
    import ast
    for path in (launch_policy.__file__, P.__file__, L.__file__):
        tree = ast.parse(open(path, encoding="utf-8").read())
        names = ({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
                 | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)})
        assert not names & {"environ", "getenv", "request", "headers", "args", "json"}, path


# ── Entitlement safety ─────────────────────────────────────────────────────
@pytest.mark.parametrize("feature", SUPPORTED_FEATURES)
def test_apple_off_active_campaign_grants_launch_free(apple_off, feature):
    hold = reserve(apple_off, feature, f"lf1-first-{feature}", LF1)
    assert hold.scope == "LAUNCH_FREE" and hold.may_execute
    row = bucket(apple_off, feature)
    assert (row.period_key, row.granted, row.used, row.reserved) == ("LF1", EXPECTED[feature], 0, 1)
    assert row.policy_version == "synthetic-lf"
    assert AiStarterGrant.query.count() == 0
    assert AiAllowanceBucket.query.filter(AiAllowanceBucket.scope != "LAUNCH_FREE").count() == 0


@pytest.mark.parametrize("instant", [LF1 - US, END, END + timedelta(days=30)])
def test_apple_off_outside_campaign_is_starter(apple_off, instant):
    hold = reserve(apple_off, "coach", "outside-campaign-key", instant)
    assert hold.scope == "STARTER"
    assert AiStarterGrant.query.filter_by(user_id=apple_off).count() == 1
    assert bucket(apple_off, "coach") is None
    assert bucket(apple_off, "coach", "STARTER").granted == 10


def test_campaign_end_resumes_existing_once_per_account_starter(apple_off):
    for n in range(10):
        held = reserve(apple_off, "coach", f"starter-before-{n:02d}", LF1 - US)
        L.finalize(apple_off, held.reference, "success", now=LF1 - US)
    with pytest.raises(AllowanceError):
        reserve(apple_off, "coach", "starter-before-11", LF1 - US)
    # The campaign is a separate finite scope, then Starter is NOT re-granted.
    assert reserve(apple_off, "coach", "inside-window-key", LF1).scope == "LAUNCH_FREE"
    with pytest.raises(AllowanceError) as error:
        reserve(apple_off, "coach", "after-campaign-key", END)
    assert (error.value.code, error.value.details["scope"]) == ("AI_ALLOWANCE_EXHAUSTED", "STARTER")
    assert error.value.details["reset"] == "NEVER" and error.value.details["resets_at"] is None
    assert AiStarterGrant.query.count() == 1


def test_valid_premium_wins_over_the_campaign(app, make_user, monkeypatch):
    enable_ledger(app, campaign_policy())
    enable_subscriptions(app, monkeypatch)
    owner = make_user("premium-in-campaign").id
    premium_row(owner)
    now = datetime.now(timezone.utc)
    assert L.reserve(owner, "coach", "premium-campaign-key", now=now).scope == "PREMIUM"
    # Inside the campaign window as well (explicit instant, same live entitlement).
    assert L.reserve(owner, "qr_menu", "premium-campaign-qr", now=LF2).scope == "PREMIUM"
    assert AiAllowanceBucket.query.filter_by(scope="LAUNCH_FREE").count() == 0


@pytest.mark.parametrize("damage", ["expired", "revoked", "status"])
def test_expired_revoked_or_inactive_entitlement_never_grants_premium(app, make_user, monkeypatch, damage):
    enable_ledger(app, campaign_policy())
    enable_subscriptions(app, monkeypatch)
    owner = make_user(f"lapsed-{damage}").id
    row = premium_row(owner, expires_in=timedelta(days=-1) if damage == "expired" else timedelta(days=20),
                      status=2 if damage == "status" else 1)
    if damage == "revoked":
        row.revoked_at = datetime.now(timezone.utc)
        db.session.commit()
    assert L.reserve(owner, "coach", "lapsed-campaign-key", now=LF1).scope == "LAUNCH_FREE"
    assert AiAllowanceBucket.query.filter_by(scope="PREMIUM").count() == 0


@pytest.mark.parametrize("code", ["SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", "SUBSCRIPTION_INVALID_STATE"])
def test_verification_failure_is_not_a_free_access_bypass(apple_off, monkeypatch, code):
    def broken(_):
        raise SubscriptionError(code, 503)
    monkeypatch.setattr(subscriptions, "entitlement", broken)
    with pytest.raises(AllowanceError) as error:
        reserve(apple_off, "coach", "verification-broken", LF1)
    assert error.value.code == "AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE"
    with pytest.raises(AllowanceError):
        L.reserve_dispatch_within(apple_off, "coach", "verification-broken-2", "a" * 64, 8, now=LF1)
    db.session.rollback()
    assert AiAllowanceBucket.query.count() == AiUsageReservation.query.count() == 0


def test_legacy_is_premium_cannot_bypass_finite_campaign(app, make_user):
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=False)
    enable_ledger(app, campaign_policy())
    owner = make_user("legacy-premium-free", is_premium=True).id
    for n in range(5):
        held = reserve(owner, "qr_menu", f"legacy-qr-{n}", LF1)
        L.finalize(owner, held.reference, "success", now=LF1)
    with pytest.raises(AllowanceError) as error:
        reserve(owner, "qr_menu", "legacy-qr-6", LF1)
    assert error.value.code == "AI_ALLOWANCE_EXHAUSTED"
    assert db.session.get(User, owner).is_premium is True  # untouched, and irrelevant


def test_launch_free_fabricates_no_subscription_evidence(apple_off):
    held = reserve(apple_off, "coach", "no-fake-evidence", LF1)
    L.finalize(apple_off, held.reference, "success", now=LF1)
    view = L.allowance_view(apple_off, now=LF1)
    assert view["access"] == "LAUNCH_FREE"
    assert view["subscription"] == {"state": "DISABLED", "entitlement": None}
    assert AppleSubscription.query.count() == 0
    assert db.session.get(User, apple_off).is_premium in (False, None)


def test_null_creation_timestamp_gets_no_campaign_units(apple_off):
    db.session.get(User, apple_off).created_at = None
    db.session.commit()
    with pytest.raises(AllowanceError) as error:
        reserve(apple_off, "coach", "null-created-at", LF1)
    assert (error.value.code, error.value.details["scope"]) == ("AI_FEATURE_NOT_IN_PLAN", "STARTER")
    assert AiAllowanceBucket.query.count() == 0
    assert L.allowance_view(apple_off, now=LF1)["access"] == "NONE"


def test_null_timestamp_with_existing_starter_grant_reads_and_reserves_the_same(apple_off):
    from app.models import AiStarterGrant as Grant
    db.session.add(Grant(user_id=apple_off, policy_version="synthetic-lf-base", granted_at=LF1 - US))
    db.session.add(AiAllowanceBucket(user_id=apple_off, scope="STARTER", feature="coach", period_key="NEVER",
                                     policy_version="synthetic-lf-base", granted=10, used=0, reserved=0,
                                     billed_releases=0, created_at=LF1 - US, updated_at=LF1 - US))
    db.session.get(User, apple_off).created_at = None
    db.session.commit()
    view = L.allowance_view(apple_off, now=LF2)
    assert view["access"] == "STARTER" and view["features"][0]["remaining"] == 10
    assert reserve(apple_off, "coach", "null-created-at-starter", LF2).scope == "STARTER"
    assert AiAllowanceBucket.query.filter_by(scope="LAUNCH_FREE").count() == 0


def test_campaign_key_prefix_separates_campaigns(apple_off, app):
    held = reserve(apple_off, "qr_menu", "first-campaign-key", LF1)
    L.finalize(apple_off, held.reference, "success", now=LF1)
    app.config["AI_ALLOWANCE_POLICY"] = replace(launch_policy.LAUNCH_V1_POLICY, version="synthetic-lf-base",
        launch_free=replace(CAMPAIGN, version="synthetic-lf2", key_prefix="LFB"))
    assert reserve(apple_off, "qr_menu", "second-campaign-key", LF1).scope == "LAUNCH_FREE"
    keys = sorted(b.period_key for b in AiAllowanceBucket.query.filter_by(scope="LAUNCH_FREE"))
    assert keys == ["LF1", "LFB1"]
    assert bucket(apple_off, "qr_menu", key="LF1").used == 1  # first campaign untouched
    for bad in ("lf", "L", "LF1", "TOOLONGXX", ""):
        with pytest.raises(PolicyError):
            replace(CAMPAIGN, key_prefix=bad)


def test_retention_purges_campaign_buckets_like_premium(apple_off):
    held = reserve(apple_off, "coach", "retention-key-0001", LF1)
    L.finalize(apple_off, held.reference, "success", now=LF1)
    late = reserve(apple_off, "coach", "retention-key-0002", LF3)
    L.finalize(apple_off, late.reference, "success", now=LF3)
    later = END + L.TERMINAL_RETENTION + timedelta(days=1)
    db.session.expire_all()
    for row in AiAllowanceBucket.query.filter_by(period_key="LF3"):
        row.updated_at = later - timedelta(days=10)  # touched inside retention: kept
    db.session.commit()
    settled, periods = L.purge_retained(now=later)
    assert (settled, periods) == (2, 1)
    assert sorted(b.period_key for b in AiAllowanceBucket.query.all()) == ["LF3"]


# ── Ledger correctness ────────────────────────────────────────────────────
@pytest.mark.parametrize("feature", SUPPORTED_FEATURES)
@pytest.mark.parametrize("instant,reset,resets_at", [
    (LF1, "CAMPAIGN_WINDOW", "2026-12-24T21:00:00Z"),
    (LF2 + timedelta(days=3), "CAMPAIGN_WINDOW", "2027-01-23T21:00:00Z"),
    (LF3, "CAMPAIGN_END", None),
])
def test_each_feature_exhausts_with_server_reset(apple_off, feature, instant, reset, resets_at):
    for n in range(EXPECTED[feature]):
        held = reserve(apple_off, feature, f"exhaust-{feature}-{n:03d}", instant)
        assert L.finalize(apple_off, held.reference, "success", now=instant).consumed
    with pytest.raises(AllowanceError) as error:
        reserve(apple_off, feature, f"exhaust-{feature}-over", instant)
    assert error.value.code == "AI_ALLOWANCE_EXHAUSTED"
    assert error.value.details == {"feature": feature, "scope": "LAUNCH_FREE",
                                   "reset": reset, "resets_at": resets_at}
    row = bucket(apple_off, feature)
    assert (row.used, row.reserved) == (EXPECTED[feature], 0)
    # Exhaustion never falls through to Starter or another scope.
    assert AiStarterGrant.query.count() == 0
    assert AiAllowanceBucket.query.count() == 1


def test_each_window_is_a_fresh_full_bucket_and_no_fourth(apple_off):
    for instant, key in [(LF1, "LF1"), (LF2, "LF2"), (LF3, "LF3")]:
        held = reserve(apple_off, "pump_check", f"window-{key}", instant)
        L.finalize(apple_off, held.reference, "success", now=instant)
        row = bucket(apple_off, "pump_check", key=key)
        assert (row.granted, row.used) == (20, 1)
    assert reserve(apple_off, "pump_check", "window-after-end", END).scope == "STARTER"
    keys = {b.period_key for b in AiAllowanceBucket.query.filter_by(scope="LAUNCH_FREE")}
    assert keys == {"LF1", "LF2", "LF3"}


def test_window_boundary_never_resets_early_and_never_double_grants(apple_off):
    for n in range(2):
        held = reserve(apple_off, "training_generation", f"early-key-{n}", LF2 - US)
        L.finalize(apple_off, held.reference, "success", now=LF2 - US)
    with pytest.raises(AllowanceError):
        reserve(apple_off, "training_generation", "early-key-2", LF2 - US)
    held = reserve(apple_off, "training_generation", "boundary-new", LF2)
    assert bucket(apple_off, "training_generation", key="LF2").reserved == 1
    assert AiAllowanceBucket.query.filter_by(feature="training_generation").count() == 2
    assert held.scope == "LAUNCH_FREE"


def test_idempotent_replay_and_conflicting_reuse(apple_off):
    first = reserve(apple_off, "coach", "replay-key-0001", LF1)
    L.finalize(apple_off, first.reference, "success", now=LF1)
    replay = reserve(apple_off, "coach", "replay-key-0001", LF2)  # across a boundary too
    assert replay.replayed and not replay.may_execute and replay.reference == first.reference
    assert replay.scope == "LAUNCH_FREE"
    assert bucket(apple_off, "coach", key="LF1").used == 1
    assert bucket(apple_off, "coach", key="LF2") is None
    with pytest.raises(AllowanceError) as error:
        reserve(apple_off, "qr_menu", "replay-key-0001", LF1)
    assert error.value.code == "AI_ALLOWANCE_IDEMPOTENCY_CONFLICT"
    pending = reserve(apple_off, "coach", "replay-key-0002", LF1)
    with pytest.raises(AllowanceError) as error:
        reserve(apple_off, "coach", "replay-key-0002", LF1)
    assert error.value.code == "AI_ALLOWANCE_IN_PROGRESS" and pending.may_execute


@pytest.mark.parametrize("outcome,billing,released", [
    ("invalid_output", "CONFIRMED_BILLED", True),        # proven unusable: refunded
    ("provider_timeout", "UNKNOWN", False),              # ambiguous: held, not freed
    ("refused_before_provider", "CONFIRMED_NOT_BILLED", True),
])
def test_usable_result_settlement_applies_to_campaign_buckets(apple_off, outcome, billing, released):
    held = L.reserve_dispatch_within(apple_off, "nutrition_generation", "settle-key-0001", "b" * 64, 2, now=LF1)
    db.session.commit()
    result = L.finalize(apple_off, held.reference, outcome, billing_evidence=billing,
                        result_known=outcome != "provider_timeout", now=LF1)
    row = bucket(apple_off, "nutrition_generation")
    if released:
        assert result.state == "RELEASED" and (row.used, row.reserved) == (0, 0)
    else:
        assert result.state == "RESERVED" and (row.used, row.reserved) == (0, 1)
    # A released key can never authorize a second dispatch.
    with pytest.raises(AllowanceError) as error:
        L.reserve_dispatch_within(apple_off, "nutrition_generation", "settle-key-0001", "b" * 64, 2, now=LF1)
    assert error.value.code in ("AI_ALLOWANCE_IN_PROGRESS", "AI_ALLOWANCE_RESERVATION_CONFLICT")


def test_reservation_spanning_a_window_boundary_settles_its_original_bucket(apple_off):
    late = reserve(apple_off, "coach", "spanning-success", LF2 - timedelta(seconds=5))
    refund = L.reserve_dispatch_within(apple_off, "coach", "spanning-refund", "c" * 64, 8,
                                       now=LF2 - timedelta(seconds=5))
    db.session.commit()
    L.finalize(apple_off, late.reference, "success", now=LF2 + timedelta(seconds=5))
    L.finalize(apple_off, refund.reference, "invalid_output", billing_evidence="CONFIRMED_BILLED",
               result_known=True, now=LF2 + timedelta(seconds=5))
    lf1 = bucket(apple_off, "coach", key="LF1")
    assert (lf1.used, lf1.reserved) == (1, 0)
    assert bucket(apple_off, "coach", key="LF2") is None


def test_abandoned_hold_across_boundary_expires_consumed_in_its_window(apple_off):
    reserve(apple_off, "coach", "crash-mid-flight", LF3 - timedelta(minutes=1))
    assert L.expire_abandoned(now=LF3 + L.RESERVATION_TTL) == 1
    lf2 = bucket(apple_off, "coach", key="LF2")
    assert (lf2.used, lf2.reserved) == (1, 0)
    assert AiUsageReservation.query.one().state == "EXPIRED"
    assert bucket(apple_off, "coach", key="LF3") is None


def test_existing_starter_and_premium_rows_are_preserved(app, make_user, monkeypatch):
    enable_ledger(app, campaign_policy())
    enable_subscriptions(app, monkeypatch)
    owner = make_user("historic-rows").id
    starter = L.reserve(owner, "coach", "historic-starter", now=LF1 - timedelta(days=2))
    L.finalize(owner, starter.reference, "success", now=LF1 - timedelta(days=2))
    premium_row(owner)
    paid = L.reserve(owner, "coach", "historic-premium", now=datetime.now(timezone.utc))
    L.finalize(owner, paid.reference, "success")
    db.session.query(AppleSubscription).delete()
    db.session.commit()
    snapshot = {(b.scope, b.period_key): (b.granted, b.used, b.reserved, b.policy_version)
                for b in AiAllowanceBucket.query.all()}
    assert {scope for scope, _ in snapshot} == {"STARTER", "PREMIUM"}
    for n in range(3):
        held = L.reserve(owner, "coach", f"campaign-after-history-{n}", now=LF1)
        L.finalize(owner, held.reference, "success", now=LF1)
    db.session.expire_all()
    after = {(b.scope, b.period_key): (b.granted, b.used, b.reserved, b.policy_version)
             for b in AiAllowanceBucket.query.filter(AiAllowanceBucket.scope != "LAUNCH_FREE")}
    assert after == snapshot
    assert bucket(owner, "coach").used == 3


def test_allowance_view_reports_campaign_scope_and_final_window(apple_off):
    held = reserve(apple_off, "coach", "view-key", LF1)
    view = L.allowance_view(apple_off, now=LF1)
    assert view["contract_version"] == 1 and view["access"] == "LAUNCH_FREE"
    assert view["starter"]["state"] == "ELIGIBLE"
    coach = view["features"][0]
    assert coach == {"feature": "coach", "scope": "LAUNCH_FREE", "state": "AVAILABLE", "limit": 30,
                     "used": 0, "reserved": 1, "remaining": 29, "reset": "CAMPAIGN_WINDOW",
                     "resets_at": "2026-12-24T21:00:00Z"}
    assert [f["limit"] for f in view["features"]] == [30, 2, 2, 20, 5]
    final = L.allowance_view(apple_off, now=LF3)["features"][0]
    assert (final["limit"], final["used"], final["reset"], final["resets_at"]) == (30, 0, "CAMPAIGN_END", None)
    after = L.allowance_view(apple_off, now=END)
    assert after["access"] == "STARTER" and after["features"][0]["scope"] == "STARTER"
    before = L.allowance_view(apple_off, now=LF1 - US)
    assert before["access"] == "STARTER"
    assert held.reference not in str(view)


def test_training_expiry_compensation_recognizes_only_the_installed_window(app, apple_off):
    from app.services.ai_allowances import training_compensation as C
    row = AiAllowanceBucket(user_id=apple_off, scope="LAUNCH_FREE", feature="training_generation",
                            period_key="LF1", policy_version="synthetic-lf", granted=2, used=1,
                            reserved=0, billed_releases=0, created_at=LF1, updated_at=LF1)
    with app.app_context():
        assert C._campaign_period(row, LF1 + timedelta(hours=1))
        assert not C._campaign_period(row, LF2)
        row.policy_version = "launch-v1-usable-result"
        assert not C._campaign_period(row, LF1 + timedelta(hours=1))
        app.config["AI_ALLOWANCE_POLICY"] = replace(campaign_policy(), launch_free=None)
        row.policy_version = "synthetic-lf"
        assert not C._campaign_period(row, LF1 + timedelta(hours=1))


# ── Execution gate (Apple decoupling) ──────────────────────────────────────
@pytest.fixture
def execution_ready(app, make_user, monkeypatch):
    owner = make_user("launch-free-execution").id
    app.config.update(AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True, AI_ALLOWANCE_LEDGER_ENABLED=True,
                      AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True, APPLE_SUBSCRIPTIONS_ENABLED=False,
                      AI_ALLOWANCE_POLICY=today_policy())
    monkeypatch.setattr(ai_consent, "begin_operation", lambda uid, f, *route:
                        ai_consent.OperationAuthority(uid, f, 1, "synthetic"))
    monkeypatch.setattr(ai_consent, "authorize_operation", lambda authority: authority)
    monkeypatch.setattr(ai_consent, "fence", lambda *args: None)
    return owner


@pytest.mark.parametrize("feature", SUPPORTED_FEATURES)
def test_apple_off_campaign_meters_every_native_family(execution_ready, feature):
    for n in range(EXPECTED[feature]):
        with E.logical_action(execution_ready, feature, {"n": n}, key=f"native-{feature}-{n:03d}") as action:
            E.check_provider(execution_ready, next(iter(E.PHYSICAL_FEATURES[feature])))
            action.success()
    row = bucket(execution_ready, feature)
    assert (row.scope, row.period_key, row.used, row.reserved) == ("LAUNCH_FREE", "LF1", EXPECTED[feature], 0)
    with pytest.raises(E.ExecutionRefused) as refused:
        a = E.prepare(execution_ready, feature, {"n": "over"}, key=f"native-{feature}-over")
        a.start()
    assert refused.value.code == "AI_ALLOWANCE_EXHAUSTED"
    assert refused.value.details["scope"] == "LAUNCH_FREE"
    assert refused.value.details["reset"] == "CAMPAIGN_WINDOW"


def test_native_training_strict_entitlement_accepts_apple_off_campaign(execution_ready):
    hold = L.reserve_within(execution_ready, "training_generation", "native-training-strict",
                            strict_entitlement=True)
    assert hold.scope == "LAUNCH_FREE" and hold.may_execute
    db.session.rollback()


@pytest.mark.parametrize("flag", ["AI_ALLOWANCE_LEDGER_ENABLED", "AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED"])
def test_apple_off_never_replaces_ledger_or_enforcement(app, execution_ready, flag):
    E.require_ready()
    app.config[flag] = False
    with pytest.raises(E.ExecutionRefused) as refused:
        E.require_ready()
    assert refused.value.code == "AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE"


@pytest.mark.parametrize("policy", [
    lambda: replace(today_policy(), launch_free=None),
    lambda: "not-a-policy",
    lambda: None,
])
def test_apple_off_without_finite_campaign_refuses(app, execution_ready, policy):
    app.config["AI_ALLOWANCE_POLICY"] = policy()
    with pytest.raises(E.ExecutionRefused):
        E.require_ready()
    with pytest.raises(E.ExecutionRefused):
        E.prepare(execution_ready, "coach", {"x": 1}, key="no-campaign-key")
    assert AiUsageReservation.query.count() == 0


def test_dispatch_with_apple_off_needs_the_campaign_policy(app, execution_ready):
    app.config["AI_ALLOWANCE_POLICY"] = replace(today_policy(), launch_free=None)
    for call in (lambda: L.reserve_dispatch_within(execution_ready, "coach", "apple-off-dispatch", "d" * 64, 8),
                 lambda: L.reserve_within(execution_ready, "coach", "apple-off-native", strict_entitlement=True)):
        with pytest.raises(AllowanceError) as error:
            call()
        assert error.value.code == "AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE"
        db.session.rollback()
    assert AiAllowanceBucket.query.count() == 0


def test_apple_on_gate_is_unchanged(app, execution_ready):
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True,
                      AI_ALLOWANCE_POLICY=replace(today_policy(), launch_free=None))
    E.require_ready()  # the historical Apple-on configuration still qualifies


def test_d1_cohort_and_runtime_gates_still_refuse_with_apple_off(app, execution_ready, make_user, monkeypatch):
    from app.services import ai_spend_guard
    monkeypatch.setattr(launch_policy, "APPROVED_POLICY", app.config["AI_ALLOWANCE_POLICY"])
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: object())
    # Explicitly retire the approved source pin: Apple off cannot bypass it.
    monkeypatch.setattr(ai_launch, "APPROVED_SERVICE_COHORT_VERSION", None)
    assert ai_launch.APPROVED_SERVICE_COHORT_VERSION is None
    with pytest.raises(ai_consent.ConsentDenied):
        ai_launch.require_runtime()
    monkeypatch.setattr(ai_launch, "APPROVED_SERVICE_COHORT_VERSION", ai_launch.account_eligibility.VERSION)
    ai_launch.require_runtime()
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: None)
    with pytest.raises(ai_consent.ConsentDenied):
        ai_launch.require_runtime()
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: object())
    # The signed adult TR record is still required at allocation.
    user = db.session.get(User, execution_ready)
    with pytest.raises(ai_consent.ConsentDenied):
        L.reserve(user.id, "coach", "unsigned-account-key")
    db.session.rollback()
    assert AiAllowanceBucket.query.count() == 0


def test_premium_purchase_remains_unavailable_with_apple_off(app, execution_ready):
    # Launch Free never opens a purchase path: the Apple gate answers first.
    with pytest.raises(SubscriptionError) as error:
        subscriptions.purchase_context(execution_ready)
    assert error.value.code == "SUBSCRIPTIONS_DISABLED"


# ── Refusal envelope ───────────────────────────────────────────────────────
@pytest.mark.parametrize("reset,resets_at", [("CAMPAIGN_WINDOW", "2026-12-24T21:00:00Z"),
                                             ("CAMPAIGN_END", None)])
def test_refusal_envelope_carries_only_customer_metadata(app, reset, resets_at):
    from flask import current_app
    with app.test_request_context():
        error = E.ExecutionRefused("AI_ALLOWANCE_EXHAUSTED", feature="coach", scope="LAUNCH_FREE",
                                   reset=reset, resets_at=resets_at, reservation_id="private",
                                   owner=7, campaign_version="internal")
        response = current_app.handle_user_exception(error)
    body = response.get_json()["error"]
    assert response.status_code == 402 and body["code"] == "AI_ALLOWANCE_EXHAUSTED"
    assert body["retryable"] is False
    assert body["details"] == {"feature": "coach", "scope": "LAUNCH_FREE",
                               "reset": reset, "resets_at": resets_at}
    assert response.headers["Cache-Control"] == "no-store"


# ── Migration d8f1a3c5e7b9 (SQLite; PostgreSQL in the _pg module) ─────────
def _migration(name):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "migrations/versions" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _narrow_ledger(conn):
    import sqlalchemy as sa
    conn.execute(sa.text('CREATE TABLE "user" (id INTEGER PRIMARY KEY)'))
    conn.execute(sa.text('INSERT INTO "user" VALUES (1)'))
    _run(conn, _migration("a9c1d2e3f4b5_ai_allowance_ledger.py").upgrade)
    for scope, key in (("STARTER", "NEVER"), ("PREMIUM", "2026-11")):
        conn.execute(sa.text(
            "INSERT INTO ai_allowance_bucket (user_id, scope, feature, period_key, policy_version, "
            "granted, used, reserved, billed_releases, created_at, updated_at) VALUES "
            "(1, :s, 'coach', :k, 'launch-v1-usable-result', 10, 3, 1, 0, '2026-11-01', '2026-11-01')"),
            {"s": scope, "k": key})


def _insert(conn, scope, key="LF1", used=0, granted=30):
    import sqlalchemy as sa
    conn.execute(sa.text(
        "INSERT INTO ai_allowance_bucket (user_id, scope, feature, period_key, policy_version, "
        "granted, used, reserved, billed_releases, created_at, updated_at) VALUES "
        "(1, :s, 'coach', :k, 'launch-free-v1', :g, :u, 0, 0, '2026-11-25', '2026-11-25')"),
        {"s": scope, "k": key, "u": used, "g": granted})


def test_migration_descends_from_head_and_is_expand_only():
    m = _migration("d8f1a3c5e7b9_ai_allowance_launch_free_scope.py")
    assert (m.revision, m.down_revision, m.expand_contract) == ("d8f1a3c5e7b9", "c6f8b0d2e4a1", "expand")


def test_migration_widens_scope_and_preserves_existing_rows(tmp_path):
    import sqlalchemy as sa
    m = _migration("d8f1a3c5e7b9_ai_allowance_launch_free_scope.py")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'expand.db'}")
    with engine.begin() as conn:
        _narrow_ledger(conn)
        before = conn.execute(sa.text("SELECT * FROM ai_allowance_bucket ORDER BY id")).all()
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                _insert(conn, "LAUNCH_FREE")
        _run(conn, m.upgrade)
        _run(conn, m.upgrade)  # reentrant: already widened
        assert conn.execute(sa.text("SELECT * FROM ai_allowance_bucket ORDER BY id")).all() == before
        _insert(conn, "LAUNCH_FREE")
        for bad in (lambda: _insert(conn, "UNLIMITED", "LF2"),
                    lambda: _insert(conn, "LAUNCH_FREE", "LF2", used=31)):
            with pytest.raises(sa.exc.IntegrityError):
                with conn.begin_nested():
                    bad()
        with pytest.raises(RuntimeError):
            _run(conn, m.downgrade)  # a campaign bucket exists
        conn.execute(sa.text("DELETE FROM ai_allowance_bucket WHERE scope = 'LAUNCH_FREE'"))
        _run(conn, m.downgrade)
        assert conn.execute(sa.text("SELECT * FROM ai_allowance_bucket ORDER BY id")).all() == before
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                _insert(conn, "LAUNCH_FREE")


def _fk_engine(path):
    import sqlalchemy as sa
    engine = sa.create_engine(f"sqlite:///{path}")

    @sa.event.listens_for(engine, "connect")
    def _fk(dbapi, _record):  # as app/extensions.py does for every SQLite connection
        dbapi.execute("PRAGMA foreign_keys=ON")
    return engine


def _populated_narrow_ledger(conn):
    import sqlalchemy as sa
    from datetime import datetime
    from app.db_init import replay_shipped_predicates
    from app.models import AiUsageReservation
    _model_ledger(conn)
    replay_shipped_predicates(conn, {"ai_allowance_bucket"})
    _insert(conn, "STARTER", "NEVER", granted=10)
    values = {"public_id": "r" * 22, "user_id": 1, "bucket_id": 1, "feature": "coach",
              "key_digest": "d" * 64, "state": "RESERVED", "provider_billed": False,
              "created_at": datetime(2026, 11, 1), "expires_at": datetime(2026, 11, 1)}
    conn.execute(AiUsageReservation.__table__.insert().values(**values))


def _counts(conn):
    import sqlalchemy as sa
    return tuple(conn.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar()
                 for t in ("ai_allowance_bucket", "ai_usage_reservation"))


def test_sqlite_rebuild_never_cascades_into_reservations(tmp_path):
    # SQLite batch mode drops and recreates the table; with foreign keys ON
    # (app/extensions.py) that DROP is an implicit DELETE cascading into
    # ai_usage_reservation. Upgrade and downgrade keep every row.
    import sqlalchemy as sa
    m = _migration("d8f1a3c5e7b9_ai_allowance_launch_free_scope.py")
    engine = _fk_engine(tmp_path / "fk.db")
    with engine.begin() as conn:
        _populated_narrow_ledger(conn)
    for step in (m.upgrade, m.downgrade, m.upgrade):
        with engine.connect() as conn:
            _run(conn, step)
            conn.commit()
        with engine.connect() as conn:
            assert _counts(conn) == (1, 1)
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    with engine.begin() as conn:
        _insert(conn, "LAUNCH_FREE")


def test_sqlite_rebuild_refuses_when_foreign_keys_cannot_be_disabled(tmp_path):
    import sqlalchemy as sa
    m = _migration("d8f1a3c5e7b9_ai_allowance_launch_free_scope.py")
    engine = _fk_engine(tmp_path / "fk.db")
    with engine.begin() as conn:
        _populated_narrow_ledger(conn)  # DML opened the transaction: PRAGMA is now a no-op
        with pytest.raises(RuntimeError, match="cascade-delete"):
            _run(conn, m.upgrade)
        assert _counts(conn) == (1, 1)


def _model_ledger(conn):
    import sqlalchemy as sa
    from app.models import AiAllowanceBucket as Bucket, AiStarterGrant, AiUsageReservation
    conn.execute(sa.text('CREATE TABLE "user" (id INTEGER PRIMARY KEY)'))
    conn.execute(sa.text('INSERT INTO "user" VALUES (1)'))
    for model in (AiStarterGrant, Bucket, AiUsageReservation):
        model.__table__.create(conn)


def test_shipped_ledger_revision_is_byte_identical_to_production():
    import hashlib, subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    rel = "migrations/versions/a9c1d2e3f4b5_ai_allowance_ledger.py"
    shipped = subprocess.run(["git", "show", "d13c9fa1871b375a62dde3590eda547833dbb8e6:" + rel],
                             cwd=root, capture_output=True)
    if shipped.returncode != 0:
        pytest.skip("audit baseline commit not available in this checkout")
    assert hashlib.sha256((root / rel).read_bytes()).digest() == hashlib.sha256(shipped.stdout).digest()


def test_fresh_boot_replays_shipped_predicate_then_widens(tmp_path):
    import sqlalchemy as sa
    from app.db_init import replay_shipped_predicates
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'models.db'}")
    with engine.begin() as conn:
        _model_ledger(conn)
        # Shipped a9c1 is immutable and pins the original predicate exactly.
        with pytest.raises(RuntimeError):
            with conn.begin_nested():
                _run(conn, _migration("a9c1d2e3f4b5_ai_allowance_ledger.py").upgrade)
        replay_shipped_predicates(conn, {"ai_allowance_bucket"})
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                _insert(conn, "LAUNCH_FREE")
        _run(conn, _migration("a9c1d2e3f4b5_ai_allowance_ledger.py").upgrade)
        _run(conn, _migration("d8f1a3c5e7b9_ai_allowance_launch_free_scope.py").upgrade)
        _insert(conn, "LAUNCH_FREE")
        for bad in (lambda: _insert(conn, "UNLIMITED", "LF2"),
                    lambda: _insert(conn, "LAUNCH_FREE", "LF2", used=31)):
            with pytest.raises(sa.exc.IntegrityError):
                with conn.begin_nested():
                    bad()


def test_replay_never_touches_a_table_this_boot_did_not_create(tmp_path):
    import sqlalchemy as sa
    from app.db_init import replay_shipped_predicates
    m = _migration("d8f1a3c5e7b9_ai_allowance_launch_free_scope.py")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    with engine.begin() as conn:
        _narrow_ledger(conn)
        _run(conn, lambda: m._replace(conn, "scope IN ('STARTER', 'PREMIUM') OR 1=1"))
        before = sa.inspect(conn).get_check_constraints("ai_allowance_bucket")
        replay_shipped_predicates(conn, set())   # pre-existing: never "repaired"
        assert sa.inspect(conn).get_check_constraints("ai_allowance_bucket") == before
        with pytest.raises(RuntimeError):
            _run(conn, _migration("a9c1d2e3f4b5_ai_allowance_ledger.py").upgrade)


def test_interrupted_fresh_boot_recovers_only_empty_unstamped_tables(tmp_path):
    # A fresh boot that died after create_all and before its stamp leaves the
    # widened table behind with no alembic_version; the next boot replays it.
    import sqlalchemy as sa
    from app.db_init import replay_shipped_predicates, unstamped_empty_tables
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'interrupted.db'}")
    with engine.begin() as conn:
        _model_ledger(conn)
        assert unstamped_empty_tables(conn) == {"ai_allowance_bucket"}
        replay_shipped_predicates(conn, unstamped_empty_tables(conn))
        _run(conn, _migration("a9c1d2e3f4b5_ai_allowance_ledger.py").upgrade)
        _insert(conn, "STARTER", "NEVER")
        assert unstamped_empty_tables(conn) == set()   # rows: never rewritten


def test_fresh_boot_path_records_absence_before_create_all():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "app/db_init.py").read_text(encoding="utf-8")
    absent, create_all = source.index("_absent = {"), source.index("db.create_all()")
    replay, stamp = source.index("replay_shipped_predicates(conn, _absent | unstamped_empty_tables(conn))"), source.index(
        "stamp(revision=_FRESH_SCHEMA_STAMP)")
    assert absent < create_all < replay < stamp
    from app.db_init import SHIPPED_CHECK_PREDICATES
    assert SHIPPED_CHECK_PREDICATES == (("ai_allowance_bucket", "ck_ai_allowance_bucket_scope",
                                         "scope IN ('STARTER', 'PREMIUM')", "d8f1a3c5e7b9"),)


def test_unfamiliar_scope_predicate_fails_closed(tmp_path):
    import sqlalchemy as sa
    m = _migration("d8f1a3c5e7b9_ai_allowance_launch_free_scope.py")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'unfamiliar.db'}")
    with engine.begin() as conn:
        _narrow_ledger(conn)
        _run(conn, lambda: m._replace(conn, "scope IN ('STARTER', 'PREMIUM') OR 1=1"))
        with pytest.raises(RuntimeError):
            _run(conn, m.upgrade)
        with pytest.raises(RuntimeError):
            _run(conn, _migration("a9c1d2e3f4b5_ai_allowance_ledger.py").upgrade)


# ── Native transport gate with Apple off ───────────────────────────────────
from test_d1_launch_integration import approved_cohort, contracts  # noqa: E402,F401  (fixtures)
from test_mobile_coach_api import bearer  # noqa: E402,F401  (fixture)


@pytest.mark.parametrize("endpoint", list(ai_launch.NATIVE_ENDPOINTS))
def test_native_views_pass_the_launch_gate_with_apple_off(client, app, make_user, bearer, contracts,
                                                         approved_cohort, monkeypatch, endpoint):
    from contextlib import contextmanager
    from unittest.mock import Mock
    from flask import jsonify
    from app.services import ai_spend_guard
    from subscription_support import adult_account
    policy = today_policy()
    monkeypatch.setattr(launch_policy, "APPROVED_POLICY", policy)
    user = adult_account(make_user("launch-free-native"))
    db.session.commit()
    app.config.update(AI_ALLOWANCE_POLICY=policy, AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
                      AI_ALLOWANCE_LEDGER_ENABLED=True, AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
                      APPLE_SUBSCRIPTIONS_ENABLED=False)
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: object())

    @contextmanager
    def transaction(owner):
        assert owner == user.id
        yield db.session.connection()
    monkeypatch.setattr(ai_consent, "_transaction", transaction)
    monkeypatch.setattr(ai_consent, "_state", lambda *a: contracts)
    sink = Mock(return_value=jsonify(qualified_view=True))
    monkeypatch.setitem(app.view_functions, endpoint, sink)
    rule = next(r for r in app.url_map.iter_rules() if r.endpoint == endpoint)
    assert client.post(str(rule), headers=bearer(user), json={}).status_code == 200
    # Missing ledger enforcement refuses even with the campaign installed.
    app.config["AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED"] = False
    refused = client.post(str(rule), headers=bearer(user), json={})
    assert refused.status_code == 503
    assert refused.json["error"]["code"] == "AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE"
    app.config["AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED"] = True
    # The fixture's ambient app context shares ``g`` across test requests;
    # production requests each get their own. Do not carry the refusal over.
    from flask import g
    g.pop("allowance_refusal", None)
    # Signed account eligibility is still the authority.
    user.user_metadata = {}
    db.session.commit()
    response = client.post(str(rule), headers=bearer(user), json={})
    assert response.status_code == 403
    assert response.json["error"]["code"] == "AI_SERVICE_ELIGIBILITY_REQUIRED"
    assert sink.call_count == 1


def test_scope_widening_is_a_pinned_reviewed_expand_migration():
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    try:
        import migration_expand_contract as mec
    finally:
        sys.path.pop(0)
    path = root / "migrations/versions/d8f1a3c5e7b9_ai_allowance_launch_free_scope.py"
    assert mec.REVIEWED_DDL["d8f1a3c5e7b9"] == mec.normalized_digest(path.read_bytes())
    result = mec.scan()
    assert result["classes"]["d8f1a3c5e7b9"] == mec.EXPAND
    assert result["violations"] == {}
    assert "d8f1a3c5e7b9" not in mec.contract_revisions()
