"""Use disposable PostgreSQL, real D1/allowance fences, and synthetic SDKs only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.extensions import db
from app.models import AiUsageReservation, User, WeeklyCheckIn
from app.services import (ai, ai_consent, ai_consent_disclosures, ai_launch,
                          ai_recovery, ai_spend_guard, health_consent)
from app.services.ai_allowances import execution, launch_policy
from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES
from app.services.weekly_review import operations, provider
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
    monkeypatch.setattr(provider, "APPROVED_ALLOWANCE_FAMILY", "coach")
    monkeypatch.setitem(execution.PHYSICAL_FEATURES, "coach",
                        execution.PHYSICAL_FEATURES["coach"] | {provider.FEATURE})
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
    assert _get(pg_app, check_in_id, version="2").status_code == 400
    assert _get(pg_app, check_in_id, user=USERS[1]).status_code == 404
    assert _get(pg_app, check_in_id).json == {
        "contract_version": 1, "check_in_id": check_in_id,
        "status": "unavailable", "evaluation": None}
    with pg_app.app_context():
        assert WeeklyCheckIn.query.count() == 3
        assert AiUsageReservation.query.count() == 0


def test_concurrent_submissions_workers_and_result_reads(pg_app, authorized):
    sdk, fallback, _ = authorized
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: _post(pg_app), range(4)))
    assert sorted(result.status_code for result in responses) == [200, 200, 200, 201]
    snapshots = [result.json for result in responses]
    assert all(snapshot == snapshots[0] for snapshot in snapshots)
    check_in_id = snapshots[0]["check_in_id"]
    with pg_app.app_context():
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
        assert WeeklyCheckIn.query.count() == 1
        hold = AiUsageReservation.query.one()
        assert hold.physical_attempts == 1
        assert hold.outcome == "success"
        assert hold.feature == "coach"
        assert len(WeeklyCheckIn.query.one().review_evidence_digest) == 64


@pytest.mark.parametrize("condition", ["revoke", "redis", "spend", "rollback", "ineligible"])
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
        else:
            pg_app.config[provider.FLAG] = False
    _drain(pg_app)
    assert _get(pg_app, saved.json["check_in_id"]).json["status"] == "unavailable"
    sdk.assert_not_called()
    fallback.assert_not_called()
    with pg_app.app_context():
        assert all(hold.physical_attempts == 0 for hold in AiUsageReservation.query.all())


@pytest.mark.parametrize("condition", ["malformed", "refusal", "timeout", "write_failure"])
def test_provider_failure_never_reopens_dispatch(pg_app, authorized, monkeypatch, condition):
    sdk, fallback, _ = authorized
    if condition == "malformed":
        sdk.return_value.content[0].text = '{"next_week_actions": []}'
    elif condition == "refusal":
        sdk.return_value.stop_reason = "refusal"
    elif condition == "timeout":
        sdk.side_effect = TimeoutError("synthetic timeout")
    else:
        original = ai_consent.fence

        def reject_result(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("synthetic write failure")
        monkeypatch.setattr(ai_consent, "fence", reject_result)
    saved = _post(pg_app)
    _drain(pg_app)
    attempts = sdk.call_count
    assert 1 <= attempts <= 8
    assert _post(pg_app).status_code == 200
    _drain(pg_app)
    assert _get(pg_app, saved.json["check_in_id"]).json["status"] == "unavailable"
    assert sdk.call_count == attempts
    fallback.assert_not_called()
    with pg_app.app_context():
        assert WeeklyCheckIn.query.count() == 1
        assert AiUsageReservation.query.one().physical_attempts == attempts


def test_pre_dispatch_crash_recovers_and_ambiguous_dispatch_does_not(pg_app, authorized, monkeypatch):
    sdk, _, _ = authorized
    first = _post(pg_app)
    second = _post(pg_app, "weekly-review-b")
    with pg_app.app_context():
        first_claim = operations._claim()
        second_claim = operations._claim()
        assert first_claim[1] != second_claim[1]
        assert operations._begin_dispatch(second_claim[0], second_claim[1], second_claim[3])
        future = operations._now() + operations.CLAIM_WINDOW + timedelta(seconds=1)
        monkeypatch.setattr(operations, "_now", lambda: future)
    _drain(pg_app)
    sdk.assert_called_once()
    assert _get(pg_app, first.json["check_in_id"]).json["status"] == "completed"
    assert _get(pg_app, second.json["check_in_id"]).json["status"] == "unavailable"


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
        hold = AiUsageReservation.query.one()
        assert hold.canonical_committed is True
        assert hold.outcome == "success"
        assert hold.physical_attempts == 1
    assert _get(pg_app, saved.json["check_in_id"]).json["status"] == "completed"
    assert _post(pg_app).json == saved.json
    sdk.assert_called_once()


def test_allowance_exhaustion_is_terminal_for_this_saved_id(pg_app, authorized):
    sdk, _, _ = authorized
    for suffix in ("a", "b", "c"):
        result = _post(pg_app, f"weekly-review-{suffix}")
        assert result.status_code == 201
        _drain(pg_app)
        assert _get(pg_app, result.json["check_in_id"]).json["status"] == (
            "unavailable" if suffix == "c" else "completed")
    assert sdk.call_count == 2
    with pg_app.app_context():
        assert WeeklyCheckIn.query.count() == 3
        assert sum(hold.physical_attempts for hold in AiUsageReservation.query.all()) == 2
