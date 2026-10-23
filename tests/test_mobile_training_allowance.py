"""MON-02-BE-03B-01B: native initial Training generation x AI allowance ledger.

Synthetic test-only policies (tests/ai_allowance_support.py), synthetic
providers, the real route. PostgreSQL races and crash boundaries live in
tests/test_mobile_training_allowance_pg.py.
"""
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from ai_allowance_support import enable_ledger, enable_subscriptions, fixture_policy, premium_row
from app.extensions import db
from app.models import (
    AiAllowanceBucket, AiUsageReservation, TrainingPlan, TrainingPlanGenerationAllowance,
    TrainingPlanGenerationOperation, User,
)
from app.services import ai_allowances, ai_provider_call, premium
from app.services.ai_allowances import AllowancePolicy, ScopePolicy
from app.services.ai_gate import BlockingConcurrencyLimit
from app.services.mobile_training_generation import allowance as seam
from app.services.mobile_training_generation import service as generation_service
from app.services.mobile_training_generation import store
from app.services.training_generation.output_errors import (
    GenerationUnavailableError, SchemaInvalidError,
)
from tests.test_mobile_training_generation_api import (  # noqa: F401 - fixtures
    CANONICAL, POST_PATH, _document, _provider_document, as_mobile, mobile_user,
)

ENFORCE = "AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED"


@pytest.fixture
def metered(app):
    enable_ledger(app)  # Starter: one training_generation unit, billed cap 2
    app.config[ENFORCE] = True
    app.config["AI_PLAN_QUOTA_ENABLED"] = True  # proves the legacy quota is bypassed
    return app


@pytest.fixture
def provider(monkeypatch):
    """Synthetic candidate; ``mode`` steers one outcome per call."""
    state = SimpleNamespace(calls=0, mode="ok")

    def candidate(user, last_session, preferences, chat_fn, **kwargs):
        state.calls += 1
        # A synthetic PHYSICAL attempt: through the pre-SDK evidence door, as
        # every real call is (ai_provider_call.Admission.create).
        ai_provider_call._before_physical_attempt()
        if state.mode == "unavailable":
            raise GenerationUnavailableError("provider endpoint detail")
        if state.mode == "invalid":
            raise SchemaInvalidError("schema detail")
        return SimpleNamespace(document=_document(), overall_score=8.5)

    monkeypatch.setattr(generation_service, "generate_training_plan_candidate", candidate)
    monkeypatch.setattr("app.blueprints.mobile_training._heavy_chat", lambda **kwargs: "unused")
    return state


def _post(client, as_mobile, user, key):
    return client.post(POST_PATH, json=CANONICAL, headers=as_mobile(user, key))


def _ledger(user_id):
    db.session.expire_all()
    bucket = AiAllowanceBucket.query.filter_by(
        user_id=user_id, feature="training_generation").one_or_none()
    return SimpleNamespace(
        bucket=None if bucket is None else (bucket.granted, bucket.used, bucket.reserved,
                                            bucket.billed_releases),
        holds=[(r.state, r.outcome, r.billing_evidence, r.provider_billed)
               for r in AiUsageReservation.query.filter_by(user_id=user_id)
               .order_by(AiUsageReservation.id)],
        bindings=[(b.state, b.reservation_ref) for b in TrainingPlanGenerationAllowance.query
                  .filter_by(user_id=user_id).order_by(TrainingPlanGenerationAllowance.operation_id)],
        operations=[(o.status, o.provider_dispatch_possible, o.allowance_metered)
                    for o in TrainingPlanGenerationOperation.query.filter_by(user_id=user_id)
                    .order_by(TrainingPlanGenerationOperation.id)],
        plans=TrainingPlan.query.filter_by(user_id=user_id).count(),
    )


def _legacy_charges(user_id):
    db.session.expire_all()
    meta = db.session.get(User, user_id).user_metadata or {}
    return int((meta.get("ai_plan_quota") or {}).get("training", 0))


# ── Authorization ──────────────────────────────────────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer")
def test_fresh_success_consumes_exactly_one_unit_and_replay_consumes_none(
        client, mobile_user, as_mobile, metered, provider):
    created = _post(client, as_mobile, mobile_user, "allowance-key-success")
    assert created.status_code == 201
    state = _ledger(mobile_user.id)
    assert state.bucket == (1, 1, 0, 0)
    assert state.holds == [("COMMITTED", "success", "UNKNOWN", None)]
    assert [b[0] for b in state.bindings] == ["COMMITTED"]
    assert state.operations == [("SUCCEEDED", True, True)]
    assert state.plans == 1 and provider.calls == 1
    assert _legacy_charges(mobile_user.id) == 0  # ledger, not the weekly quota

    for _ in range(3):
        replay = _post(client, as_mobile, mobile_user, "allowance-key-success")
        assert replay.status_code == 201 and replay.json == created.json
        assert replay.headers["Idempotency-Replayed"] == "true"
    assert _ledger(mobile_user.id).bucket == (1, 1, 0, 0)
    assert len(_ledger(mobile_user.id).holds) == 1 and provider.calls == 1


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("configure,code,status,retryable", [
    (lambda app: app.config.update(AI_ALLOWANCE_POLICY=None),
     "AI_ALLOWANCE_POLICY_UNCONFIGURED", 503, False),
    (lambda app: app.config.update(AI_ALLOWANCE_LEDGER_ENABLED=False),
     "AI_ALLOWANCE_DISABLED", 503, False),
    (lambda app: app.config.update(AI_ALLOWANCE_POLICY=fixture_policy(starter=False, premium=True)),
     "AI_FEATURE_NOT_IN_PLAN", 402, False),
])
def test_refusal_before_dispatch_holds_nothing_and_blocks_nothing(
        app, client, mobile_user, as_mobile, metered, provider, configure, code, status, retryable):
    configure(app)
    response = _post(client, as_mobile, mobile_user, "allowance-key-refused")
    assert response.status_code == status
    assert response.json["error"] == {**response.json["error"], "code": code, "retryable": retryable}
    assert "Retry-After" not in response.headers
    state = _ledger(mobile_user.id)
    assert provider.calls == 0 and state.holds == [] and state.bindings == []
    assert state.operations == [] and state.plans == 0  # nothing left active


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_exhausted_allowance_refuses_before_inference_and_new_key_is_not_blocked(
        client, mobile_user, as_mobile, metered, provider, app):
    app.config["AI_ALLOWANCE_POLICY"] = fixture_policy(max_billed=0)
    provider.mode = "invalid"
    failed = _post(client, as_mobile, mobile_user, "allowance-key-exhaust-1")
    assert failed.status_code == 422  # invalid provider output, unit consumed (cap 0)
    assert _ledger(mobile_user.id).bucket == (1, 1, 0, 0)

    provider.mode = "ok"
    refused = _post(client, as_mobile, mobile_user, "allowance-key-exhaust-2")
    assert refused.status_code == 402
    assert refused.json["error"]["code"] == "AI_ALLOWANCE_EXHAUSTED"
    assert refused.json["error"]["retryable"] is False
    assert provider.calls == 1  # no inference for the exhausted attempt
    state = _ledger(mobile_user.id)
    assert [o[0] for o in state.operations] == ["FAILED"]  # the refused claim is gone
    assert len(state.holds) == 1


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_temporary_subscription_failure_never_grants_premium(
        client, mobile_user, as_mobile, metered, provider, monkeypatch, app):
    enable_subscriptions(app, monkeypatch)

    def broken(user_id):
        raise ai_allowances.service.SubscriptionError("SUBSCRIPTION_TEMPORARILY_UNAVAILABLE", 503)
    monkeypatch.setattr(ai_allowances.service.subscriptions, "entitlement", broken)
    response = _post(client, as_mobile, mobile_user, "allowance-key-subscription")
    assert response.status_code == 503
    assert response.json["error"]["code"] == "AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE"
    assert response.json["error"]["retryable"] is True
    assert response.headers["Retry-After"] == "30"
    assert provider.calls == 0 and _ledger(mobile_user.id).operations == []


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_premium_is_server_resolved_and_never_falls_back_to_starter(
        client, mobile_user, as_mobile, metered, provider, monkeypatch, app):
    enable_subscriptions(app, monkeypatch)
    premium_row(mobile_user.id)
    # The test-only Premium fixture has no training_generation: NOT_IN_PLAN,
    # never an unlimited answer and never the Starter unit.
    app.config["AI_ALLOWANCE_POLICY"] = fixture_policy(premium=True)
    refused = _post(client, as_mobile, mobile_user, "allowance-key-premium-1")
    assert refused.status_code == 402
    assert refused.json["error"]["code"] == "AI_FEATURE_NOT_IN_PLAN"
    assert provider.calls == 0

    app.config["AI_ALLOWANCE_POLICY"] = AllowancePolicy(
        version="test-premium-training",
        premium=ScopePolicy("MONTHLY", {"training_generation": 2}, 0))
    created = _post(client, as_mobile, mobile_user, "allowance-key-premium-2")
    assert created.status_code == 201
    db.session.expire_all()
    [bucket] = AiAllowanceBucket.query.filter_by(user_id=mobile_user.id).all()
    assert (bucket.scope, bucket.used, bucket.reserved) == ("PREMIUM", 1, 0)


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_legacy_premium_flag_and_client_fields_grant_nothing(
        client, mobile_user, as_mobile, metered, provider, app):
    app.config["AI_ALLOWANCE_POLICY"] = fixture_policy(max_billed=0)
    db.session.get(User, mobile_user.id).is_premium = True  # legacy bypass flag
    db.session.commit()
    provider.mode = "invalid"
    _post(client, as_mobile, mobile_user, "allowance-key-legacy-1")
    provider.mode = "ok"
    headers = {**as_mobile(mobile_user, "allowance-key-legacy-2"),
               "X-AI-Tier": "PREMIUM", "X-Allowance-Remaining": "99"}
    refused = client.post(POST_PATH, json=CANONICAL, headers=headers)
    assert refused.status_code == 402
    assert refused.json["error"]["code"] == "AI_ALLOWANCE_EXHAUSTED"
    tampered = client.post(POST_PATH, json={**CANONICAL, "tier": "PREMIUM"},
                           headers=as_mobile(mobile_user, "allowance-key-legacy-3"))
    assert tampered.status_code == 422  # closed request contract
    assert provider.calls == 1


# ── Settlement ─────────────────────────────────────────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("mode,outcome,http", [
    ("invalid", "invalid_output", 422),
    ("unavailable", "provider_error", 503),
])
@pytest.mark.parametrize("cap,settled,bucket", [
    (2, "RELEASED", (1, 0, 0, 1)),
    (0, "COMMITTED", (1, 1, 0, 0)),
])
def test_post_dispatch_failure_is_unknown_billing_settled_by_the_cap(
        app, client, mobile_user, as_mobile, metered, provider, mode, outcome, http,
        cap, settled, bucket):
    app.config["AI_ALLOWANCE_POLICY"] = fixture_policy(max_billed=cap)
    provider.mode = mode
    response = _post(client, as_mobile, mobile_user, "allowance-key-failure")
    assert response.status_code == http
    assert response.json["error"]["retryable"] is False
    state = _ledger(mobile_user.id)
    assert state.holds == [(settled, outcome, "UNKNOWN", None)]
    assert [b[0] for b in state.bindings] == [settled]
    assert state.bucket == bucket and state.plans == 0
    again = _post(client, as_mobile, mobile_user, "allowance-key-failure")  # replay
    assert again.status_code == http and _ledger(mobile_user.id).bucket == bucket
    assert provider.calls == 1


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_internal_repair_turn_is_one_logical_action(
        client, mobile_user, as_mobile, metered, monkeypatch):
    completions = []

    def complete(**kwargs):
        completions.append(1)
        return "not json" if len(completions) == 1 else json.dumps(
            _provider_document("ex_barbell_back_squat"))
    monkeypatch.setattr("app.blueprints.mobile_training._heavy_chat", complete)
    response = _post(client, as_mobile, mobile_user, "allowance-key-repair")
    assert response.status_code == 201
    assert len(completions) == 2  # initial + bounded format repair
    state = _ledger(mobile_user.id)
    assert state.bucket == (1, 1, 0, 0) and len(state.holds) == 1


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_capacity_refusal_never_creates_a_hold(
        client, mobile_user, as_mobile, metered, provider, monkeypatch):
    @contextmanager
    def reject():
        raise BlockingConcurrencyLimit("busy")
        yield
    monkeypatch.setattr("app.blueprints.mobile_training.blocking_concurrency_slot", reject)
    response = _post(client, as_mobile, mobile_user, "allowance-key-capacity")
    assert response.status_code == 503
    assert response.json["error"]["code"] == "TRAINING_PLAN_GENERATION_BUSY"
    state = _ledger(mobile_user.id)
    assert state.holds == [] and state.operations == [] and provider.calls == 0
    assert state.bucket in (None, (1, 0, 0, 0))


# ── Recovery ───────────────────────────────────────────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer")
def test_lost_success_settlement_is_finished_by_replay_exactly_once(
        client, mobile_user, as_mobile, metered, provider, monkeypatch, caplog):
    real = ai_allowances.finalize
    failures = []

    def flaky(*args, **kwargs):
        if not failures:
            failures.append(1)
            raise ai_allowances.AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
        return real(*args, **kwargs)
    monkeypatch.setattr(ai_allowances, "finalize", flaky)
    key = "allowance-key-settle-retry"
    with caplog.at_level(logging.WARNING):
        created = _post(client, as_mobile, mobile_user, key)
    assert created.status_code == 201  # the plan is the success, not the ledger
    state = _ledger(mobile_user.id)
    assert state.operations == [("SUCCEEDED", True, True)]
    assert [b[0] for b in state.bindings] == ["RESERVED"] and state.bucket == (1, 0, 1, 0)
    [line] = [r.getMessage() for r in caplog.records if "training_allowance" in r.getMessage()]
    assert line == "training_allowance event=settlement_deferred code=AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE"
    assert key not in line and state.bindings[0][1] not in line

    replay = _post(client, as_mobile, mobile_user, key)
    assert replay.status_code == 201 and replay.json == created.json
    state = _ledger(mobile_user.id)
    assert [b[0] for b in state.bindings] == ["COMMITTED"] and state.bucket == (1, 1, 0, 0)
    _post(client, as_mobile, mobile_user, key)
    assert _ledger(mobile_user.id).bucket == (1, 1, 0, 0) and provider.calls == 1


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_success_settled_after_hold_expiry_is_one_expired_unit_not_a_new_charge(
        client, mobile_user, as_mobile, metered, provider, monkeypatch):
    real_settle = seam.settle
    monkeypatch.setattr(seam, "settle", lambda operation_id: None)  # settlement lost
    key = "allowance-key-expired-success"
    assert _post(client, as_mobile, mobile_user, key).status_code == 201
    monkeypatch.setattr(seam, "settle", real_settle)
    db.session.expire_all()
    hold = AiUsageReservation.query.filter_by(user_id=mobile_user.id).one()
    hold.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.session.commit()
    assert _post(client, as_mobile, mobile_user, key).status_code == 201
    state = _ledger(mobile_user.id)
    assert state.holds == [("EXPIRED", "expired", "UNKNOWN", None)]
    assert [b[0] for b in state.bindings] == ["EXPIRED"]
    assert state.bucket == (1, 1, 0, 0) and state.plans == 1


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_generated_candidate_save_failure_keeps_the_hold_for_save_only_recovery(
        client, mobile_user, as_mobile, metered, provider, monkeypatch):
    real_insert = store._insert_plan
    monkeypatch.setattr(store, "_insert_plan", lambda **kwargs: (_ for _ in ()).throw(
        RuntimeError("disk")))
    key = "allowance-key-generated"
    failed = _post(client, as_mobile, mobile_user, key)
    assert failed.status_code == 503
    assert failed.json["error"]["code"] == "TRAINING_PLAN_PERSISTENCE_UNAVAILABLE"
    state = _ledger(mobile_user.id)
    assert state.operations == [("GENERATED", True, True)]
    assert [b[0] for b in state.bindings] == ["RESERVED"] and state.bucket == (1, 0, 1, 0)

    monkeypatch.setattr(store, "_insert_plan", real_insert)
    saved = _post(client, as_mobile, mobile_user, key)
    assert saved.status_code == 201 and provider.calls == 1  # save-only, no inference
    state = _ledger(mobile_user.id)
    assert state.holds == [("COMMITTED", "success", "UNKNOWN", None)]
    assert state.bucket == (1, 1, 0, 0) and state.plans == 1


class _Crash(BaseException):
    """Escapes application handlers, like a worker dying mid-inference."""


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.usefixtures("synthetic_operation_authority")
def test_abandoned_dispatch_retires_as_one_expired_unit_and_late_output_is_fenced(
        app, client, mobile_user, as_mobile, metered, provider, monkeypatch):
    app.config["AI_ALLOWANCE_POLICY"] = fixture_policy(
        starter_units={"training_generation": 2, "coach": 1})
    real = generation_service.generate_training_plan_candidate
    monkeypatch.setattr(generation_service, "generate_training_plan_candidate",
                        lambda *a, **k: (_ for _ in ()).throw(_Crash()))
    user_id = mobile_user.id
    with pytest.raises(_Crash):
        _post(client, as_mobile, mobile_user, "allowance-key-abandoned")
    db.session.remove()
    mobile_user = db.session.get(User, user_id)
    state = _ledger(mobile_user.id)
    assert state.operations == [("IN_PROGRESS", True, True)]
    assert [b[0] for b in state.bindings] == ["RESERVED"] and state.bucket == (2, 0, 1, 0)
    # Same key: never redispatched, never a second hold.
    monkeypatch.setattr(generation_service, "generate_training_plan_candidate", real)
    busy = _post(client, as_mobile, mobile_user, "allowance-key-abandoned")
    assert busy.status_code == 409 and len(_ledger(mobile_user.id).holds) == 1

    # Age the dispatch past the retirement interval; marker and hold share one
    # instant, so the hold is already past its TTL when retirement runs.
    operation = TrainingPlanGenerationOperation.query.one()
    aged = datetime.now(timezone.utc) - timedelta(minutes=16)
    operation.updated_at = aged.replace(tzinfo=None)
    AiUsageReservation.query.one().expires_at = aged + ai_allowances.RESERVATION_TTL
    db.session.commit()
    operation_id = operation.id

    fresh = _post(client, as_mobile, mobile_user, "allowance-key-after-retire")
    assert fresh.status_code == 201
    state = _ledger(mobile_user.id)
    assert [o[0] for o in state.operations] == ["FAILED", "SUCCEEDED"]
    assert [h[0] for h in state.holds] == ["EXPIRED", "COMMITTED"]
    assert [b[0] for b in state.bindings] == ["EXPIRED", "COMMITTED"]
    assert state.bucket == (2, 2, 0, 0) and state.plans == 1  # one expired + one success
    with pytest.raises(store.GenerationPersistenceUnavailable):
        store.stage_candidate(operation_id, json.dumps(_document()), 8.0)  # late worker
    assert _ledger(mobile_user.id).bucket == (2, 2, 0, 0)


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_hold_window_is_stamped_at_the_dispatch_instant_and_ends_at_retirement(
        client, mobile_user, as_mobile, metered, provider):
    assert store.ABANDON_AFTER >= ai_allowances.RESERVATION_TTL  # retirement never precedes expiry
    assert _post(client, as_mobile, mobile_user, "allowance-key-instant").status_code == 201
    db.session.expire_all()
    operation = TrainingPlanGenerationOperation.query.one()
    hold = AiUsageReservation.query.one()
    binding = TrainingPlanGenerationAllowance.query.one()
    dispatched = binding.created_at  # stamped with the marker in the dispatch commit
    assert hold.created_at.replace(tzinfo=None) == dispatched
    assert hold.expires_at.replace(tzinfo=None) - dispatched == ai_allowances.RESERVATION_TTL
    assert operation.provider_dispatch_possible is True


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("drift", ["abandon_after_shorter", "reservation_ttl_longer", "hold_after_marker"])
def test_retirement_before_hold_expiry_refuses_before_dispatch(
        client, mobile_user, as_mobile, metered, provider, monkeypatch, drift):
    """R2: correct retirement accounting needs ABANDON_AFTER >= RESERVATION_TTL from one instant."""
    if drift == "abandon_after_shorter":
        monkeypatch.setattr(store, "ABANDON_AFTER", ai_allowances.RESERVATION_TTL - timedelta(seconds=1))
    elif drift == "reservation_ttl_longer":
        monkeypatch.setattr(ai_allowances.service, "RESERVATION_TTL",
                            store.ABANDON_AFTER + timedelta(seconds=1))
    else:  # R1 mutant M-C: the hold window opens 1 s after the marker instant
        real = ai_allowances.reserve_within
        monkeypatch.setattr(ai_allowances, "reserve_within", lambda user_id, feature, key, now: real(
            user_id, feature, key, now=now + timedelta(seconds=1)))
    refused = _post(client, as_mobile, mobile_user, "allowance-key-ttl")
    assert refused.status_code == 503
    assert refused.json["error"]["code"] == "AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE"
    assert refused.json["error"]["retryable"] is True
    state = _ledger(mobile_user.id)
    assert provider.calls == 0 and state.holds == [] and state.bindings == []
    assert state.operations == [] and state.plans == 0
    assert state.bucket in (None, (1, 0, 0, 0))  # no unit moved (SQLite may keep the lazy grant)
    assert _legacy_charges(mobile_user.id) == 0


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("claimed_metered", [True, False])
def test_flag_flip_between_claim_and_dispatch_never_dispatches_under_another_mode(
        app, client, mobile_user, as_mobile, metered, provider, monkeypatch, claimed_metered):
    monkeypatch.setattr(premium, "FREE_WEEKLY_AI_PLANS", 2)
    app.config[ENFORCE] = claimed_metered
    real = store.authorize_dispatch

    def flip_then_authorize(operation_id):
        app.config[ENFORCE] = not claimed_metered
        return real(operation_id)
    monkeypatch.setattr(store, "authorize_dispatch", flip_then_authorize)
    refused = _post(client, as_mobile, mobile_user, "allowance-key-flip")
    assert refused.status_code == 503
    assert refused.json["error"]["code"] == "AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE"
    assert refused.json["error"]["retryable"] is True
    state = _ledger(mobile_user.id)
    assert provider.calls == 0 and state.operations == [] and state.holds == []
    assert _legacy_charges(mobile_user.id) == 0  # a legacy claim was refunded

    monkeypatch.setattr(store, "authorize_dispatch", real)
    retried = _post(client, as_mobile, mobile_user, "allowance-key-flip")
    assert retried.status_code == 201  # same key, fresh claim under the current mode
    state = _ledger(mobile_user.id)
    now_metered = not claimed_metered
    assert state.operations == [("SUCCEEDED", True, True if now_metered else None)]
    assert len(state.holds) == (1 if now_metered else 0)
    assert _legacy_charges(mobile_user.id) == (0 if now_metered else 1)


# ── OFF parity ─────────────────────────────────────────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("ledger_on", [True, False])
def test_enforcement_off_is_the_legacy_command_with_no_ledger_activity(
        app, client, mobile_user, as_mobile, provider, monkeypatch, ledger_on):
    if ledger_on:
        enable_ledger(app)
    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    assert app.config.get(ENFORCE) is not True  # registry default
    calls = []
    for name in ("reserve", "reserve_within", "finalize", "grant_starter"):
        monkeypatch.setattr(ai_allowances, name, lambda *a, _n=name, **k: calls.append(_n))
    created = _post(client, as_mobile, mobile_user, "allowance-key-off")
    assert created.status_code == 201
    replay = _post(client, as_mobile, mobile_user, "allowance-key-off")
    assert replay.headers["Idempotency-Replayed"] == "true"
    state = _ledger(mobile_user.id)
    assert calls == [] and state.holds == [] and state.bindings == [] and state.bucket is None
    assert state.operations == [("SUCCEEDED", True, None)]
    assert _legacy_charges(mobile_user.id) == 1


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_enforcement_off_legacy_quota_refusal_is_unchanged(
        client, mobile_user, as_mobile, provider, app):
    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    enable_ledger(app)
    user = db.session.get(User, mobile_user.id)
    user.user_metadata = {"ai_plan_quota": {"week": premium._quota_from_meta({})[2],
                                            "training": premium.FREE_WEEKLY_AI_PLANS}}
    db.session.commit()
    refused = _post(client, as_mobile, mobile_user, "allowance-key-off-quota")
    assert refused.status_code == 402
    assert refused.json["error"]["code"] == "PREMIUM_REQUIRED"
    assert provider.calls == 0 and _ledger(mobile_user.id).bucket is None


# ── Isolation and constraints ──────────────────────────────────────────────
@pytest.mark.usefixtures("synthetic_ai_layer")
def test_owners_sharing_a_key_have_independent_operations_and_holds(
        client, mobile_user, as_mobile, metered, provider, make_user, monkeypatch):
    other = make_user("training-allowance-other", profile_complete=True)
    real = ai_allowances.finalize
    monkeypatch.setattr(ai_allowances, "finalize", lambda *a, **k: (_ for _ in ()).throw(
        ai_allowances.AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")))
    assert _post(client, as_mobile, mobile_user, "shared-allowance-key").status_code == 201
    monkeypatch.setattr(ai_allowances, "finalize", real)
    assert _post(client, as_mobile, other, "shared-allowance-key").status_code == 201
    mine, theirs = _ledger(mobile_user.id), _ledger(other.id)
    assert [b[0] for b in mine.bindings] == ["RESERVED"]  # B's command never settles A
    assert [b[0] for b in theirs.bindings] == ["COMMITTED"]
    assert mine.bindings[0][1] != theirs.bindings[0][1]
    assert mine.bucket == (1, 0, 1, 0) and theirs.bucket == (1, 1, 0, 0)


def test_one_hold_binds_at_most_one_operation(app, mobile_user, metered):
    first = TrainingPlanGenerationOperation(
        user_id=mobile_user.id, idempotency_key="constraint-key-1", request_fingerprint="f" * 64,
        status="SUCCEEDED", provider_dispatch_possible=True, allowance_metered=True)
    second = TrainingPlanGenerationOperation(
        user_id=mobile_user.id, idempotency_key="constraint-key-2", request_fingerprint="f" * 64,
        status="FAILED", provider_dispatch_possible=True, allowance_metered=True)
    db.session.add_all([first, second])
    db.session.flush()
    now = datetime.utcnow()
    for operation in (first, second):
        db.session.add(TrainingPlanGenerationAllowance(
            operation_id=operation.id, user_id=mobile_user.id, reservation_ref="a" * 32,
            state="RESERVED", created_at=now, updated_at=now))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_architecture_only_the_seam_reaches_the_ledger():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app/services/mobile_training_generation"
    importers = sorted(
        path.name for path in root.glob("*.py")
        if any(isinstance(node, ast.ImportFrom) and node.module and (
            node.module.startswith("app.services.ai_allowances")
            or (node.module == "app.services" and any(a.name == "ai_allowances" for a in node.names)))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))))
    assert importers == ["allowance.py"]
    source = (root / "allowance.py").read_text(encoding="utf-8")
    assert "is_premium" not in source and "idempotency_key" not in source
    assert "logger" in source and "%s" in source  # one fixed, code-only line


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_browser_plan_written_before_save_leaves_the_hold_to_ledger_expiry(
        client, mobile_user, as_mobile, metered, provider, monkeypatch):
    """Founder gate: a never-saved GENERATED candidate is not settled here; the
    ledger's own expiry consumes it. Nothing is guessed, nothing is doubled."""
    real_insert = store._insert_plan
    monkeypatch.setattr(store, "_insert_plan", lambda **kwargs: (_ for _ in ()).throw(
        RuntimeError("disk")))
    key = "allowance-key-browser-race"
    assert _post(client, as_mobile, mobile_user, key).status_code == 503
    monkeypatch.setattr(store, "_insert_plan", real_insert)
    db.session.add(TrainingPlan(user_id=mobile_user.id, plan_data=json.dumps(_document())))
    db.session.commit()  # the browser's create won the owner lock first

    refused = _post(client, as_mobile, mobile_user, key)
    assert refused.status_code == 409
    assert refused.json["error"]["code"] == "TRAINING_PLAN_REPLACEMENT_REFUSED"
    state = _ledger(mobile_user.id)
    assert state.operations == [("GENERATED", True, True)] and state.plans == 1
    assert state.holds == [("RESERVED", None, "UNKNOWN", None)] and state.bucket == (1, 0, 1, 0)

    later = datetime.now(timezone.utc) + ai_allowances.RESERVATION_TTL + timedelta(seconds=1)
    assert ai_allowances.expire_abandoned(now=later) == 1
    state = _ledger(mobile_user.id)
    assert state.holds == [("EXPIRED", "expired", "UNKNOWN", None)]
    assert state.bucket == (1, 1, 0, 0) and provider.calls == 1


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_unrepairable_provider_text_is_invalid_output_in_one_hold(
        client, mobile_user, as_mobile, metered, monkeypatch):
    completions = []
    def physical(**kwargs):  # through the pre-SDK evidence door, like a real call
        ai_provider_call._before_physical_attempt()
        completions.append(1)
        return "not json"
    monkeypatch.setattr("app.blueprints.mobile_training._heavy_chat", physical)
    response = _post(client, as_mobile, mobile_user, "allowance-key-parse")
    assert response.json["error"]["code"] == "TRAINING_PLAN_GENERATION_PARSE_FAILED"
    assert len(completions) == 2  # initial + repair, one logical action
    state = _ledger(mobile_user.id)
    assert state.holds == [("RELEASED", "invalid_output", "UNKNOWN", None)]
    assert state.bucket == (1, 0, 0, 1)  # cap 2: one possibly-billed release


@pytest.mark.parametrize("enforced", [False, True])
def test_empty_d1_registry_blocks_route_before_allowance_or_provider(
        client, mobile_user, as_mobile, app, monkeypatch, enforced):
    """Real D1 default deny; deliberately does not request synthetic_ai_layer."""
    from unittest.mock import Mock
    from app.services import ai_consent
    enable_ledger(app)
    app.config[ENFORCE] = enforced
    enable_subscriptions(app, monkeypatch)
    premium_row(mobile_user.id)
    provider = Mock()
    monkeypatch.setattr(generation_service, "generate_training_plan_candidate", provider)
    assert dict(ai_consent.POLICIES) == {}
    response = _post(client, as_mobile, mobile_user, "d1-default-deny-native")
    assert response.status_code == 403
    assert response.json["error"]["code"] == "AI_CONSENT_DISCLOSURE_UNAVAILABLE"
    provider.assert_not_called()
    state = _ledger(mobile_user.id)
    assert state.bucket is None
    assert state.holds == state.bindings == state.operations == []
    assert _legacy_charges(mobile_user.id) == 0
