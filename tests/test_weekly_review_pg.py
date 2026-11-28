"""Use disposable PostgreSQL, real D1/allowance fences, and synthetic SDKs only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.extensions import db
from app.models import AiUsageReservation, User, UserSession, WeeklyCheckIn, WeeklyReviewAdmission
from app.services import (ai, ai_consent, ai_consent_disclosures, ai_launch,
                          ai_recovery, ai_spend_guard, health_consent)
from app.services.ai_allowances import execution, launch_policy
from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES
from app.services.weekly_review import allowance, operations, provider
from ai_allowance_support import enable_subscriptions, premium_row
from consent_projection_support import publish_synthetic_texts
from d1_screened_support import screen_adult_tr
from test_ai_spend_guard import FakeRedis
from test_lp16b_native_checkin_pg import BODY, PATH, USERS, pg_app  # noqa: F401

pytestmark = pytest.mark.pg_concurrency
V2 = PATH + "?contract_version=2"
VERSION = "synthetic-weekly-review"


def _post(app, key="weekly-review-a", *, user=USERS[0], path=V2, **extra):
    body = dict(BODY, nutrition_adherence="not_applicable") if path == V2 else BODY
    with app.test_client() as client:
        response = client.post(path, json=dict(body, **extra), headers={
            "Authorization": f"Bearer {user}", "Idempotency-Key": key})
        response.get_data()
        response.close()
        return response


def _get(app, check_in_id, user=USERS[0], version="1"):
    with app.test_client() as client:
        response = client.get(f"{PATH}/{check_in_id}/evaluation?contract_version={version}",
                          headers={"Authorization": f"Bearer {user}"})
        response.get_data()
        response.close()
        return response


def _owner():
    return User.query.filter_by(username=USERS[0]).one()


def _consent(owner, action="affirmative_grant"):
    policy = ai_consent.POLICIES[VERSION]
    generation = ai_consent.status(owner)["generation"]
    return ai_consent.mutate(owner, {
        "action": action, "purpose": policy.purpose, "version": policy.version,
        "locale": "en", "text_digest": dict(policy.texts)["en"],
        "contract_digest": ai_consent.contract_digest(policy),
        "expected_generation": generation, "request_id": f"synthetic-review-{generation}",
    })


@pytest.fixture
def authorized(pg_app, monkeypatch):
    from app.services import account_eligibility

    sdk = Mock()
    sdk.return_value = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps({
            "language": "tr", "summary": ["sleep_quality", "fatigue"],
            "changes": [], "next_week_actions": ["bedtime"],
        }))])
    fallback = Mock(side_effect=RuntimeError("synthetic fallback refusal"))
    monkeypatch.setattr(ai, "bedrock_client", SimpleNamespace(messages=SimpleNamespace(create=sdk)))
    monkeypatch.setattr(ai, "openai_client", SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback))))
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_recovery, "_sleep", lambda *_: None)
    redis = FakeRedis()
    monkeypatch.setattr(ai_spend_guard, "_get_redis", lambda: redis)
    monkeypatch.setattr(ai_spend_guard, "ENABLED", True)
    monkeypatch.setattr(ai_spend_guard, "WEEKLY_REVIEW_DAILY_ATTEMPT_LIMIT", 20)
    monkeypatch.setattr(allowance, "APPROVED_CAMPAIGN", allowance.Campaign(date(2026, 11, 25), 3))
    from app.services.mobile_weekly_checkin import submission
    clock = lambda: datetime(2026, 11, 25, 10)
    monkeypatch.setattr(operations, "_now", clock)
    monkeypatch.setattr(submission, "server_checked_in_at", clock)
    categories = ("body_metrics", "recovery_ratings", "fitness_checkin")
    monkeypatch.setattr(ai_consent, "BINDINGS", {
        **ai_consent.BINDINGS, provider.FEATURE: (provider.PURPOSE, categories)})
    policy = ai_consent.Disclosure(VERSION, provider.PURPOSE, categories,
                                   (ai.heavy_primary_route(),), ())
    monkeypatch.setattr(ai_consent, "POLICIES",
                        publish_synthetic_texts(monkeypatch, {VERSION: policy}))
    monkeypatch.setattr(ai_consent_disclosures, "LAUNCH_VERSIONS",
                        {provider.PURPOSE: VERSION})
    resolved = AllowancePolicy("synthetic-weekly-review", premium=ScopePolicy(
        "WEEKLY", {family: 2 for family in SUPPORTED_FEATURES}, 2))
    monkeypatch.setattr(launch_policy, "installed_policy", lambda: resolved)
    monkeypatch.setattr(ai_launch, "APPROVED_SERVICE_COHORT_VERSION", account_eligibility.VERSION)
    enable_subscriptions(pg_app, monkeypatch)
    pg_app.config.update({provider.FLAG: True, execution.FLAG: True,
                          "AI_ALLOWANCE_LEDGER_ENABLED": True,
                          "AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED": True,
                          "AI_ALLOWANCE_POLICY": resolved})
    with pg_app.app_context():
        owner = _owner()
        owner.language = "tr"
        owner_id = owner.id
        db.session.commit()
        screen_adult_tr(owner_id)
        premium_row(owner_id)
        _consent(owner_id)
    return sdk, fallback, redis


def _drain(app):
    with app.app_context():
        operations.drain()


def test_flag_and_legal_hold_preserve_v1_v2_success(pg_app):
    with pg_app.app_context():
        assert not provider.enabled()
    off = _post(pg_app, "weekly-review-off")
    assert off.status_code == 201
    assert _get(pg_app, off.json["check_in_id"]).status_code == 404
    pg_app.config[provider.FLAG] = True
    legacy = _post(pg_app, "weekly-review-v1", path=PATH)
    assert legacy.status_code == 201 and "check_in_id" not in legacy.json
    assert _post(pg_app, "weekly-review-failed", fatigue=99).status_code == 422
    saved = _post(pg_app)
    check_in_id = saved.json["check_in_id"]
    assert _get(pg_app, check_in_id, version="3").status_code == 400
    assert _get(pg_app, check_in_id, version="2").json["allowance"] == {
        "state": "none", "remaining": None, "window_end": None}
    assert _get(pg_app, check_in_id, user=USERS[1]).status_code == 404
    assert _get(pg_app, check_in_id).json == {
        "contract_version": 1, "check_in_id": check_in_id,
        "status": "unavailable", "evaluation": None}
    with pg_app.app_context():
        assert WeeklyCheckIn.query.count() == 3
        assert AiUsageReservation.query.count() == 0


def test_concurrent_submissions_workers_and_result_reads(pg_app, authorized):
    from app.services.calculations import calculate_bmr, calculate_tdee
    from app.services.goal_pace import round_kcal

    sdk, fallback, _ = authorized
    with pg_app.app_context():
        owner = _owner()
        owner.goal_pace = "lose_slower"
        age = owner.age
        db.session.commit()
    expected_kcal = round_kcal(calculate_tdee(calculate_bmr(78, 180, age, "male"), "active") - 220)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: _post(pg_app), range(4)))
    assert sorted(result.status_code for result in responses) == [200, 200, 200, 201]
    snapshots = [result.json for result in responses]
    assert all(snapshot == snapshots[0] for snapshot in snapshots)
    check_in_id = snapshots[0]["check_in_id"]
    with pg_app.app_context():
        owner = _owner()
        session = UserSession.query.filter_by(user_id=owner.id).one()
        assert (owner.weight, owner.goal_pace, session.weight, session.target_calories) == (
            78.0, "lose_slower", 78.0, expected_kcal)
        row = db.session.get(WeeklyCheckIn, check_in_id)
        # Legacy can change today's canonical weight; replay remains immutable.
        row.weight = 77.4
        _owner().language = "en"
        db.session.commit()
    assert _get(pg_app, check_in_id).json["status"] == "pending"
    sdk.assert_not_called()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: _drain(pg_app), range(4)))
    sdk.assert_called_once()
    fallback.assert_not_called()
    sent = json.loads(sdk.call_args.kwargs["messages"][0]["content"])
    assert sent["evidence"]["recorded"]["weight_kg"] == 77.4
    assert sent["evidence"]["changes"] == {}
    assert sent["language"] == "tr"
    completed = _get(pg_app, check_in_id).json
    assert completed["status"] == "completed"
    assert completed["evaluation"]["language"] == "tr"
    assert _post(pg_app).json == snapshots[0]
    _drain(pg_app)
    assert _get(pg_app, check_in_id).json == completed
    assert _get(pg_app, check_in_id, user=USERS[1]).status_code == 404
    sdk.assert_called_once()
    with pg_app.app_context():
        owner = _owner()
        session = UserSession.query.filter_by(user_id=owner.id).one()
        assert (owner.weight, owner.goal_pace, session.weight, session.target_calories) == (
            78.0, "lose_slower", 78.0, expected_kcal)
        assert WeeklyCheckIn.query.count() == 1
        hold = WeeklyReviewAdmission.query.one()
        assert hold.physical_attempt is True
        assert hold.state == allowance.CHARGED
        assert AiUsageReservation.query.count() == 0
        assert "coach_weekly_review" not in execution.PHYSICAL_FEATURES["coach"]
        assert len(WeeklyCheckIn.query.one().review_evidence_digest) == 64


@pytest.mark.parametrize("condition", [
    "revoke", "redis", "spend", "rollback", "ineligible", "purpose", "allowance", "physical_budget",
])
def test_current_refusal_has_no_physical_attempt(pg_app, authorized, monkeypatch, condition):
    sdk, fallback, redis = authorized
    saved = _post(pg_app)
    with pg_app.app_context():
        owner = _owner()
        if condition == "revoke":
            _consent(owner.id, "revoke")
            _consent(owner.id)
        elif condition == "ineligible":
            owner.age = 17
            db.session.commit()
        elif condition == "redis":
            redis.fail = True
        elif condition == "spend":
            monkeypatch.setattr(ai_spend_guard, "_redis_admit",
                                lambda *_: "global")
        elif condition == "purpose":
            monkeypatch.delitem(ai_consent.BINDINGS, provider.FEATURE)
        elif condition == "allowance":
            monkeypatch.setattr(allowance, "APPROVED_CAMPAIGN", None)
        elif condition == "physical_budget":
            monkeypatch.setattr(ai_spend_guard, "WEEKLY_REVIEW_DAILY_ATTEMPT_LIMIT", None)
        else:
            pg_app.config[provider.FLAG] = False
    _drain(pg_app)
    assert _get(pg_app, saved.json["check_in_id"]).json["status"] == "unavailable"
    sdk.assert_not_called()
    fallback.assert_not_called()
    with pg_app.app_context():
        assert all(not hold.physical_attempt for hold in WeeklyReviewAdmission.query.all())
        assert AiUsageReservation.query.count() == 0


@pytest.mark.parametrize("condition", ["malformed", "refusal", "timeout", "write_failure", "rejected"])
def test_provider_failure_never_reopens_dispatch(pg_app, authorized, monkeypatch, condition):
    sdk, fallback, _ = authorized
    if condition == "malformed":
        sdk.return_value.content[0].text = '{"next_week_actions": []}'
    elif condition == "refusal":
        sdk.return_value.stop_reason = "refusal"
    elif condition == "timeout":
        sdk.side_effect = TimeoutError("synthetic timeout")
    elif condition == "rejected":
        class SyntheticRejection(Exception):
            status_code = 429
        sdk.side_effect = SyntheticRejection()
    else:
        def reject_result(*args, **kwargs):
            raise RuntimeError("synthetic write failure")
        monkeypatch.setattr(operations, "_publish", reject_result)
    saved = _post(pg_app)
    _drain(pg_app)
    attempts = sdk.call_count
    assert attempts == 1
    check_in_id = saved.json["check_in_id"]
    if condition in {"timeout", "write_failure"}:
        assert _get(pg_app, check_in_id, version="2").json["allowance"]["state"] == "reserved"
        assert _get(pg_app, check_in_id, version="2").json["status"] == "processing"
        future = operations._now() + operations.CLAIM_WINDOW
        monkeypatch.setattr(operations, "_now", lambda: future)
        # Settlement runs while the dispatch switch is OFF.
        pg_app.config[provider.FLAG] = False
    assert _post(pg_app).status_code == 200
    _drain(pg_app)
    assert _get(pg_app, saved.json["check_in_id"]).json["status"] == "unavailable"
    assert sdk.call_count == attempts
    fallback.assert_not_called()
    with pg_app.app_context():
        assert WeeklyCheckIn.query.count() == 1
        hold = WeeklyReviewAdmission.query.one()
        assert hold.physical_attempt is True and hold.state == allowance.RESTORED
        assert AiUsageReservation.query.count() == 0
    assert _get(pg_app, check_in_id, version="2").json["allowance"]["state"] == "restored"


def test_pre_dispatch_crash_recovers_and_ambiguous_dispatch_does_not(pg_app, authorized, monkeypatch):
    sdk, _, _ = authorized
    first = _post(pg_app)
    second = _post(pg_app, "weekly-review-b")
    with pg_app.app_context():
        first_claim = operations._claim()
        second_claim = operations._claim()
        assert first_claim[1] != second_claim[1]
        authority = provider.authorize(second_claim[0], second_claim[2])
        assert operations._begin_dispatch(second_claim[0], second_claim[1], second_claim[3], authority)
        future = operations._now() + operations.CLAIM_WINDOW + timedelta(seconds=1)
        monkeypatch.setattr(operations, "_now", lambda: future)
    _drain(pg_app)
    sdk.assert_not_called()
    assert _get(pg_app, first.json["check_in_id"]).json["status"] == "unavailable"
    assert _get(pg_app, second.json["check_in_id"]).json["status"] == "unavailable"
    with pg_app.app_context():
        assert WeeklyReviewAdmission.query.one().state == allowance.RESTORED
        assert operations._evaluate(*second_claim) == "interrupted"


def test_erasure_removes_review_and_late_claim_cannot_recreate_it(pg_app, authorized):
    sdk, _, _ = authorized
    completed = _post(pg_app)
    _drain(pg_app)
    pending = _post(pg_app, "weekly-review-b")
    with pg_app.app_context():
        claim = operations._claim()
        from app.services import health_disclosure
        owner = _owner().id
        health_consent.mutate(owner, {
            "action": "withdraw", "purpose": health_disclosure.PURPOSE,
            "version": health_disclosure.VERSION, "locale": "en",
            "text_digest": health_disclosure.DIGESTS["en"], "expected_generation": 1,
            "request_id": "synthetic-pd6-erasure",
        })
        from app.services import health_erasure
        assert health_erasure.run(owner) == "completed"
        assert WeeklyCheckIn.query.filter_by(user_id=owner).count() == 0
        db.session.rollback()
        assert operations._evaluate(*claim) == "authorization"
        assert operations.read(owner, completed.json["check_in_id"]) is None
        assert operations.read(owner, pending.json["check_in_id"]) is None
    sdk.assert_called_once()


def test_account_purge_removes_review_and_allowance_data(pg_app, authorized):
    from app.cli import _purge_user

    sdk, _, _ = authorized
    completed = _post(pg_app)
    _drain(pg_app)
    _post(pg_app, "pd6-account-purge-pending")
    with pg_app.app_context():
        claim = operations._claim()
        owner = User.query.filter_by(id=_owner().id).with_for_update().one()
        owner_id = owner.id
        assert WeeklyReviewAdmission.query.filter_by(user_id=owner_id).count() == 1
        assert _purge_user(owner)["weekly_check_in"] == 2
        db.session.commit()
        assert WeeklyCheckIn.query.filter_by(user_id=owner_id).count() == 0
        assert WeeklyReviewAdmission.query.filter_by(user_id=owner_id).count() == 0
        db.session.rollback()
        assert operations._evaluate(*claim) == "authorization"
        assert operations.read(owner_id, completed.json["check_in_id"]) is None
    sdk.assert_called_once()


def test_result_commit_ack_loss_preserves_accounting_and_replay(pg_app, authorized, monkeypatch):
    sdk, _, _ = authorized
    saved = _post(pg_app)
    with pg_app.app_context():
        session = db.session()
        commit = session.commit

        def lose_ack():
            sent = sdk.called
            commit()
            # The first ORM commit after the SDK reply is result publication.
            if sent and not lost:
                lost.append(True)
                raise RuntimeError("synthetic lost publication commit acknowledgement")

        lost = []
        monkeypatch.setattr(session, "commit", lose_ack)
        operations.drain()
        assert lost == [True]
        hold = WeeklyReviewAdmission.query.one()
        assert hold.state == allowance.CHARGED
        assert hold.physical_attempt is True
    assert _get(pg_app, saved.json["check_in_id"]).json["status"] == "completed"
    assert _post(pg_app).json == saved.json
    sdk.assert_called_once()


def test_distinct_same_week_saves_and_workers_share_one_slot(pg_app, authorized):
    sdk, fallback, _ = authorized
    with ThreadPoolExecutor(max_workers=3) as pool:
        saved = list(pool.map(lambda k: _post(pg_app, k), ["pd6-review-a", "pd6-review-b", "pd6-review-c"]))
        assert all(item.status_code == 201 for item in saved)
        list(pool.map(lambda _: _drain(pg_app), range(3)))
    states = sorted(_get(pg_app, item.json["check_in_id"], version="2").json["status"]
                    for item in saved)
    assert states == ["completed", "unavailable", "unavailable"]
    sdk.assert_called_once()
    fallback.assert_not_called()
    with pg_app.app_context():
        assert WeeklyReviewAdmission.query.count() == 1
        assert AiUsageReservation.query.count() == 0


def test_finite_window_and_original_window_release(pg_app, authorized, monkeypatch):
    from app.services.mobile_weekly_checkin import submission
    sdk, _, _ = authorized
    with pg_app.app_context():
        owner = _owner().id
        window = date(2026, 11, 25)
        for index in range(5):
            hold, failure = allowance.reserve(owner, date(2026, 11, 23) + timedelta(weeks=index), window)
            assert failure is None
            hold.state = allowance.CHARGED
            db.session.flush()
        assert allowance.reserve(owner, date(2026, 12, 28), window)[1] == "allowance_exhausted"
        db.session.rollback()
    saved_time = datetime(2026, 12, 24, 20, 58)
    monkeypatch.setattr(submission, "server_checked_in_at", lambda: saved_time)
    monkeypatch.setattr(operations, "_now", lambda: saved_time)
    sdk.side_effect = TimeoutError("synthetic timeout")
    saved = _post(pg_app, "pd6-before-window-end")
    assert saved.status_code == 201
    _drain(pg_app)
    future = saved_time + operations.CLAIM_WINDOW
    monkeypatch.setattr(operations, "_now", lambda: future)
    _drain(pg_app)
    balance = _get(pg_app, saved.json["check_in_id"], version="2").json["allowance"]
    assert balance == {"state": "restored", "remaining": 5, "window_end": "2027-01-24"}
    with pg_app.app_context():
        assert WeeklyReviewAdmission.query.one().window_start == window


def test_late_sunday_never_admitted_on_monday(pg_app, authorized, monkeypatch):
    from app.services.mobile_weekly_checkin import submission

    sdk, _, _ = authorized
    sunday = datetime(2026, 11, 29, 20, 59, 50)
    monday = sunday + timedelta(seconds=10)
    monkeypatch.setattr(submission, "server_checked_in_at", lambda: sunday)
    monkeypatch.setattr(operations, "_now", lambda: sunday)
    saved = _post(pg_app, "pd6-late-sunday")
    assert saved.status_code == 201
    assert operations._fresh_week(sunday, sunday) == date(2026, 11, 23)
    monkeypatch.setattr(operations, "_now", lambda: monday)
    _drain(pg_app)
    assert _get(pg_app, saved.json["check_in_id"]).json["status"] == "unavailable"
    sdk.assert_not_called()
    monkeypatch.setattr(submission, "server_checked_in_at", lambda: monday)
    result = _post(pg_app, "pd6-monday")
    _drain(pg_app)
    assert _get(pg_app, result.json["check_in_id"]).json["status"] == "completed"
    sdk.assert_called_once()
