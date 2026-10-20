"""MON-02-BE-03B-A1 on real PostgreSQL: zero-attempt settlement vs. physical attempts.

The REAL chain runs: native command -> canonical generator -> heavy router ->
recovery ladder -> ``ai_provider_call.admit`` -> D1 ``ai_consent.dispatch``
(real PostgreSQL fence, synthetic disclosure only) -> mocked SDK client. The
counters below are invocations of the mocked SDK entry point itself, so a call
stopped at the final consent fence is distinguishable from one that may have
reached a provider. Disposable database only (PG_TEST_DATABASE_URL).
"""
import json
import threading
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from functools import partial
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.services.ai_consent import ConsentDenied

from tests.test_mobile_training_generation_api import _provider_document
from tests.test_mobile_training_generation_pg import (  # noqa: F401
    pg_generation_app, _request,
)

pytestmark = pytest.mark.pg_concurrency


class Crash(BaseException):
    """Escapes application handlers, like abrupt worker death."""


class ServerError(Exception):
    status_code = 500  # retried inside one admission (ai_provider_call)


def _response(provider):
    text = json.dumps(_provider_document("ex_barbell_back_squat"), ensure_ascii=False)
    if provider == "bedrock":
        return SimpleNamespace(stop_reason="end_turn", usage=None,
                               content=[SimpleNamespace(type="text", text=text)])
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=text))])


@pytest.fixture
def chain(pg_generation_app):
    """Metered app + synthetic D1 disclosure + mocked SDK clients (no network)."""
    from ai_allowance_support import fixture_policy
    from app.services import ai, ai_consent, ai_provider_call, ai_recovery, ai_spend_guard
    app, owners, monkeypatch = pg_generation_app
    app.config.update(
        AI_ALLOWANCE_LEDGER_ENABLED=True,
        AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_POLICY=fixture_policy(
            max_billed=0, starter_units={"training_generation": 2, "coach": 1}))
    monkeypatch.setattr(ai_provider_call, "model_concurrency_slot", lambda *a, **k: nullcontext())
    monkeypatch.setattr(ai_spend_guard, "charge", lambda *a, **k: None)
    monkeypatch.setattr(ai_recovery, "_get_redis", lambda: None)  # no last-good cache
    state = SimpleNamespace(app=app, owners=owners, monkeypatch=monkeypatch,
                            calls={"bedrock": 0, "openai": 0}, behaviour=[], backoff=[], recovery=[])

    def sdk(provider, **kwargs):
        state.calls[provider] += 1
        step = state.behaviour.pop(0) if state.behaviour else "ok"
        if callable(step):
            step = step()
        if step == "server_error":
            raise ServerError()
        if step == "timeout":
            import httpx
            from openai import APITimeoutError
            raise APITimeoutError(request=httpx.Request("POST", "https://synthetic.invalid"))
        if step == "crash":
            raise Crash()
        return _response(provider)

    monkeypatch.setattr(ai, "openai_client", SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=partial(sdk, "openai")))))
    monkeypatch.setattr(ai, "bedrock_client", SimpleNamespace(messages=SimpleNamespace(
        create=partial(sdk, "bedrock"))))
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", False)
    monkeypatch.setattr(ai_provider_call.time, "sleep",
                        lambda _: state.backoff.pop(0)() if state.backoff else None)
    monkeypatch.setattr(ai_recovery, "_sleep",
                        lambda _: state.recovery.pop(0)() if state.recovery else None)
    state.policy = disclose(monkeypatch)
    return state


def disclose(monkeypatch, recipients=None, purpose_feature="training_plan"):
    from app.services import ai, ai_consent
    purpose, categories = ai_consent.BINDINGS[purpose_feature]
    recipients = recipients or (("bedrock", ai.BEDROCK_MODEL), ("openai", ai.OPENAI_MODEL))
    policy = ai_consent.Disclosure("synthetic-a1-" + purpose_feature, purpose, categories,
                                   tuple(recipients), (("en", "a" * 64),))
    monkeypatch.setattr(ai_consent, "POLICIES", {policy.version: policy})
    return policy


def consent(owner, policy, action, request_id):
    from app.services import ai_consent
    return ai_consent.mutate(owner, dict(
        action=action, purpose=policy.purpose, version=policy.version, locale="en",
        text_digest="a" * 64, contract_digest=ai_consent.contract_digest(policy),
        expected_generation=ai_consent.generation(owner), request_id=request_id))


def generate(user_id, key):
    from app.extensions import db
    from app.models import User
    from app.services import ai
    from app.services.mobile_training_generation import service
    return service.generate_and_persist(
        db.session.get(User, user_id), _request(), key,
        chat_fn=partial(ai._heavy_complete, feature="training_plan"),
        provider_guard=nullcontext)


def snap(user_id):
    from app.extensions import db
    from app.models import (
        AiAllowanceBucket, AiUsageReservation, TrainingPlan,
        TrainingPlanGenerationAllowance, TrainingPlanGenerationOperation)
    db.session.remove()
    bucket = AiAllowanceBucket.query.filter_by(
        user_id=user_id, feature="training_generation").one_or_none()
    return SimpleNamespace(
        operations=[(o.status, o.provider_dispatch_possible, o.provider_attempt_possible)
                    for o in TrainingPlanGenerationOperation.query.filter_by(user_id=user_id)
                    .order_by(TrainingPlanGenerationOperation.id)],
        codes=[o.error_code for o in TrainingPlanGenerationOperation.query.filter_by(user_id=user_id)
               .order_by(TrainingPlanGenerationOperation.id)],
        bindings=[b.state for b in TrainingPlanGenerationAllowance.query.filter_by(user_id=user_id)
                  .order_by(TrainingPlanGenerationAllowance.operation_id)],
        holds=[(r.state, r.outcome, r.billing_evidence, r.provider_billed) for r in
               AiUsageReservation.query.filter_by(user_id=user_id).order_by(AiUsageReservation.id)],
        bucket=None if bucket is None else (bucket.used, bucket.reserved, bucket.billed_releases),
        plans=TrainingPlan.query.filter_by(user_id=user_id).count(),
    )


def _unbilled(state, holds=1):
    assert state.holds == [("RELEASED", "refused_before_provider", "CONFIRMED_NOT_BILLED", False)] * holds
    assert state.bindings == ["RELEASED"] * holds
    assert state.bucket == (0, 0, 0)  # nothing consumed, nothing counted as billed


def _age(user_id, minutes=16):
    """Move the operation and its hold past ABANDON_AFTER / RESERVATION_TTL."""
    from app.extensions import db
    from app.models import (AiUsageReservation, TrainingPlanGenerationOperation,
                            TrainingPlanGenerationAllowance, AiAllowanceBucket, AiStarterGrant)
    from app.services import ai_allowances
    aged = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    for bucket in AiAllowanceBucket.query.filter_by(user_id=user_id):
        bucket.created_at = min(ai_allowances.service._utc(bucket.created_at), aged)
    grant = db.session.get(AiStarterGrant, user_id)
    if grant is not None:
        grant.granted_at = min(ai_allowances.service._utc(grant.granted_at), aged)
    for operation in TrainingPlanGenerationOperation.query.filter_by(user_id=user_id):
        operation.created_at = aged.replace(tzinfo=None)
        operation.updated_at = aged.replace(tzinfo=None)
    for binding in TrainingPlanGenerationAllowance.query.filter_by(user_id=user_id):
        binding.created_at = aged.replace(tzinfo=None)
    for hold in AiUsageReservation.query.filter_by(user_id=user_id):
        hold.created_at = aged
        hold.expires_at = aged + ai_allowances.RESERVATION_TTL
    db.session.commit()


# ── 1-4: definitive D1 refusal before any reservation ─────────────────────
@pytest.mark.parametrize("case", ["no_grant", "no_disclosure", "wrong_purpose",
                                  "wrong_model", "primary_recipient_missing"])
def test_preflight_refusal_holds_nothing_and_calls_no_sdk(chain, case):
    from app.services import ai, ai_consent
    owner = chain.owners[0]
    expected = "AI_CONSENT_REQUIRED"
    with chain.app.app_context():
        if case == "no_disclosure":
            chain.monkeypatch.setattr(ai_consent, "POLICIES", {})
        elif case == "wrong_purpose":
            consent(owner, disclose(chain.monkeypatch, purpose_feature="nutrition_plan"),
                    "affirmative_grant", "grant-nutrition-only")
        elif case == "wrong_model":
            consent(owner, disclose(chain.monkeypatch, recipients=(("openai", "synthetic-other"),)),
                    "affirmative_grant", "grant-wrong-model")
            expected = "AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE"
        elif case == "primary_recipient_missing":
            # Bedrock is tried first and a refusal there never falls back to OpenAI.
            chain.monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
            consent(owner, disclose(chain.monkeypatch, recipients=(("openai", ai.OPENAI_MODEL),)),
                    "affirmative_grant", "grant-openai-only")
            expected = "AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE"
        with pytest.raises(ai_consent.ConsentDenied) as refused:
            generate(owner, "a1-preflight-" + case)
        assert refused.value.code == expected
        state = snap(owner)
        assert state.operations == [] and state.holds == [] and state.bindings == []
        assert state.bucket in (None, (0, 0, 0))
        assert chain.calls == {"bedrock": 0, "openai": 0}


def test_unavailable_consent_authority_fails_closed_before_reservation(chain):
    from app.extensions import db
    from app.services import ai_consent
    owner = chain.owners[0]
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "grant-authority-busy")
        blocker = db.engine.connect()
        blocker.begin()
        # Conflicts with D1's FOR UPDATE, not with the claim's FOR KEY SHARE.
        blocker.exec_driver_sql('SELECT id FROM "user" WHERE id=%s FOR NO KEY UPDATE', (owner,))
        try:
            with pytest.raises(ai_consent.ConsentDenied) as refused:
                generate(owner, "a1-authority-unavailable")
        finally:
            blocker.rollback()
            blocker.close()
        assert (refused.value.code, refused.value.status) == ("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
        state = snap(owner)
        assert state.operations == [] and state.holds == [] and state.bucket in (None, (0, 0, 0))
        assert chain.calls == {"bedrock": 0, "openai": 0}


# ── 5/6: valid at preflight, refused at the final SDK gate ────────────────
@pytest.mark.parametrize("route", ["openai", "bedrock"])
@pytest.mark.parametrize("moment", ["after_preflight", "after_admission"])
def test_revocation_before_final_gate_releases_the_hold_unbilled(chain, route, moment):
    from app.services import ai, ai_consent, ai_provider_call
    from app.services.mobile_training_generation import allowance
    from app.services.mobile_training_generation.errors import StoredGenerationFailure
    owner = chain.owners[0]
    chain.monkeypatch.setattr(ai, "BEDROCK_ENABLED", route == "bedrock")
    revoke = lambda: consent(owner, chain.policy, "revoke", "revoke-" + moment)  # noqa: E731
    if moment == "after_preflight":
        real = allowance.preflight_consent
        chain.monkeypatch.setattr(allowance, "preflight_consent",
                                  lambda user_id: (real(user_id), revoke()))
    else:
        @contextmanager
        def slot(*args, **kwargs):  # admission captured its generation; revoke while queued
            revoke()
            yield
        chain.monkeypatch.setattr(ai_provider_call, "model_concurrency_slot", slot)
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "grant-" + moment)
        with pytest.raises(ConsentDenied) as failed:
            generate(owner, "a1-late-revoke")
        assert failed.value.code in {"AI_CONSENT_REQUIRED", "AI_CONSENT_CHANGED"}
        assert failed.value.status == 403
        state = snap(owner)
        assert state.operations == [("FAILED", True, False)]
        _unbilled(state)
        assert chain.calls == {"bedrock": 0, "openai": 0}  # no fallback after a refusal

        # Same-key replay: the stored answer and the ledger settlement replay.
        for _ in range(2):
            with pytest.raises(StoredGenerationFailure) as replay:
                generate(owner, "a1-late-revoke")
            assert replay.value.public_code == failed.value.code
            assert replay.value.http_status == 403
        _unbilled(snap(owner))
        assert ai_consent.generation(owner) == 2


# ── 7/8: a refusal after a possible physical attempt stays UNKNOWN ────────
@pytest.mark.parametrize("first", ["server_error", "timeout"])
def test_refusal_after_an_earlier_physical_attempt_keeps_unknown_and_consumes(chain, first):
    from app.services.mobile_training_generation.errors import StoredGenerationFailure
    owner = chain.owners[0]
    chain.behaviour[:] = [first]
    revoke = lambda: consent(owner, chain.policy, "revoke", "revoke-after-" + first)  # noqa: E731
    if first == "server_error":
        chain.backoff[:] = [revoke]   # between charged retries inside one admission
    else:
        chain.recovery[:] = [revoke]  # a timeout is retried by the recovery ladder
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "grant-before-" + first)
        with pytest.raises(ConsentDenied):
            generate(owner, "a1-prior-attempt-" + first)
        state = snap(owner)
        assert chain.calls == {"bedrock": 0, "openai": 1}
        assert state.operations == [("FAILED", True, True)]
        # max_billed_releases=0: a possibly billed failure consumes its unit.
        assert state.holds == [("COMMITTED", "provider_error", "UNKNOWN", None)]
        assert state.bucket == (1, 0, 0) and state.bindings == ["COMMITTED"]


# ── 9/10: crash boundaries ─────────────────────────────────────────────────
@pytest.mark.parametrize("boundary", ["before_marker", "after_marker_commit", "in_sdk"])
def test_crash_boundaries_never_produce_a_false_zero_attempt_release(chain, boundary, caplog):
    from app.services.mobile_training_generation import store
    from app.services.mobile_training_generation.errors import StoredGenerationFailure
    owner = chain.owners[0]
    real_mark = store.mark_attempt_possible
    if boundary == "before_marker":
        chain.monkeypatch.setattr(store, "mark_attempt_possible",
                                  lambda operation_id: (_ for _ in ()).throw(Crash()))
    elif boundary == "after_marker_commit":
        chain.monkeypatch.setattr(store, "mark_attempt_possible",
                                  lambda operation_id: (real_mark(operation_id), (_ for _ in ()).throw(Crash())))
    else:
        chain.behaviour[:] = ["crash"]
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "grant-crash-" + boundary)
        with pytest.raises(Crash):
            generate(owner, "a1-crash")
        attempted = boundary != "before_marker"
        state = snap(owner)
        assert state.operations == [("IN_PROGRESS", True, attempted)]
        from app.models import TrainingPlanGenerationAllowance, TrainingPlanGenerationOperation
        # The marker write never moves retirement timing (#442 / BE-03B same instant).
        assert (TrainingPlanGenerationOperation.query.one().updated_at
                == TrainingPlanGenerationAllowance.query.one().created_at)
        assert state.holds == [("RESERVED", None, "UNKNOWN", None)] and state.bucket == (0, 1, 0)
        assert chain.calls["openai"] == (1 if boundary == "in_sdk" else 0)

        chain.monkeypatch.setattr(store, "mark_attempt_possible", real_mark)
        _age(owner)
        with pytest.raises(StoredGenerationFailure):
            generate(owner, "a1-crash")  # retirement, then the stored terminal answer
        state = snap(owner)
        assert state.operations == [("FAILED", True, attempted)]
        assert state.codes == ["TRAINING_PLAN_GENERATION_OUTCOME_UNKNOWN"]
        # The hold expired with the retirement instant: consumed exactly once.
        assert state.holds[0][0] == "EXPIRED" and state.bindings == ["EXPIRED"]
        assert state.bucket == (int(attempted), 0, 0)
        assert chain.calls["openai"] == (1 if boundary == "in_sdk" else 0)  # never redispatched
        from app.models import TrainingAllowanceCompensation
        assert TrainingAllowanceCompensation.query.count() == int(not attempted)


# ── 11/12: lost settlement acknowledgement and concurrent replay ──────────
def test_lost_settlement_mark_is_finished_by_replay_without_moving_a_unit(chain):
    from app.services.mobile_training_generation import allowance
    from app.services.mobile_training_generation.errors import StoredGenerationFailure
    owner = chain.owners[0]
    real_binding = allowance._binding
    chain.monkeypatch.setattr(allowance, "preflight_consent", lambda user_id: (
        allowance.ai_consent.preflight(user_id, "training_plan", "openai", allowance.ai.OPENAI_MODEL),
        consent(owner, chain.policy, "revoke", "revoke-lost-ack")))
    chain.monkeypatch.setattr(allowance, "_binding", lambda *a, **k: (_ for _ in ()).throw(Crash()))
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "grant-lost-ack")
        with pytest.raises(Crash):
            generate(owner, "a1-lost-ack")
        state = snap(owner)
        assert state.holds == [("RELEASED", "refused_before_provider", "CONFIRMED_NOT_BILLED", False)]
        assert state.bindings == ["RESERVED"] and state.bucket == (0, 0, 0)
        chain.monkeypatch.setattr(allowance, "_binding", real_binding)
        with pytest.raises(StoredGenerationFailure):
            generate(owner, "a1-lost-ack")
        _unbilled(snap(owner))
        assert chain.calls == {"bedrock": 0, "openai": 0}


def _deferred_zero_attempt_failure(chain, owner, key):
    """A zero-attempt FAILED operation whose settlement has not happened yet."""
    from app.services import ai_allowances
    from app.services.mobile_training_generation import allowance
    from app.services.mobile_training_generation.errors import StoredGenerationFailure
    real_finalize = ai_allowances.finalize
    chain.monkeypatch.setattr(allowance, "preflight_consent", lambda user_id: (
        allowance.ai_consent.preflight(user_id, "training_plan", "openai", allowance.ai.OPENAI_MODEL),
        consent(owner, chain.policy, "revoke", "revoke-" + key)))

    def unavailable(*args, **kwargs):
        raise ai_allowances.AllowanceError("AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE")
    chain.monkeypatch.setattr(ai_allowances, "finalize", unavailable)
    consent(owner, chain.policy, "affirmative_grant", "grant-" + key)
    with pytest.raises(ConsentDenied):
        generate(owner, key)
    chain.monkeypatch.setattr(ai_allowances, "finalize", real_finalize)
    state = snap(owner)
    assert state.operations == [("FAILED", True, False)]
    assert state.holds == [("RESERVED", None, "UNKNOWN", None)] and state.bindings == ["RESERVED"]
    from app.models import TrainingPlanGenerationOperation
    return TrainingPlanGenerationOperation.query.filter_by(user_id=owner).one().id


def test_concurrent_settlement_replay_releases_once(chain):
    from app.extensions import db
    from app.services.mobile_training_generation import allowance
    owner = chain.owners[0]
    with chain.app.app_context():
        operation_id = _deferred_zero_attempt_failure(chain, owner, "a1-concurrent-settle")
    barrier, observed = threading.Barrier(3), []

    def settle():
        with chain.app.app_context():
            try:
                barrier.wait(timeout=10)
                observed.append(allowance.settle(operation_id))
            finally:
                db.session.remove()
    workers = [threading.Thread(target=settle) for _ in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
    assert observed == ["RELEASED"] * 3
    with chain.app.app_context():
        _unbilled(snap(owner))


# ── 13: revocation racing an in-flight physical attempt ───────────────────
def test_revocation_waiting_on_an_in_flight_attempt_is_ordered_after_it(chain):
    from app.extensions import db
    from app.services import ai_consent
    owner = chain.owners[0]
    revoked = []

    def revoke():
        with chain.app.app_context():
            try:
                revoked.append(consent(owner, chain.policy, "revoke", "revoke-in-flight")["generation"])
            finally:
                db.session.remove()

    def in_flight():
        # Inside the D1 fence, after the committed marker: the revoke must wait.
        thread = threading.Thread(target=revoke)
        thread.start()
        deadline = time.monotonic() + 1.5
        with db.engine.connect() as probe:
            while time.monotonic() < deadline:
                if probe.exec_driver_sql(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                        "AND wait_event_type = 'Lock'").scalar():
                    break
                time.sleep(0.02)
            else:
                pytest.fail("revocation never waited on the in-flight attempt")
        chain.in_flight_thread = thread
        return "ok"
    chain.behaviour[:] = [in_flight]
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "grant-in-flight")
        generate(owner, "a1-in-flight")
        chain.in_flight_thread.join(timeout=10)
        assert revoked == [2]
        state = snap(owner)
        assert state.operations == [("SUCCEEDED", True, True)] and state.plans == 1
        assert state.holds == [("COMMITTED", "success", "UNKNOWN", None)] and state.bucket == (1, 0, 0)
        assert chain.calls == {"bedrock": 0, "openai": 1}
        # The acknowledged revoke precedes every later attempt: without the plan,
        # a new key is refused before any hold.
        with db.engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM training_plan WHERE user_id=%s", (owner,))
        with pytest.raises(ai_consent.ConsentDenied):
            generate(owner, "a1-after-revoke")
        state = snap(owner)
        assert len(state.holds) == 1 and state.bucket == (1, 0, 0) and chain.calls["openai"] == 1


# ── 14: expiry racing a proven zero-attempt disposition ───────────────────
def test_expiry_before_settlement_corrects_proven_zero_attempt_consumption(chain, caplog):
    from app.services.mobile_training_generation import allowance
    owner = chain.owners[0]
    with chain.app.app_context():
        operation_id = _deferred_zero_attempt_failure(chain, owner, "a1-expiry-race")
        _age(owner)  # the deferred settlement now runs after the hold's TTL
        assert allowance.settle(operation_id) == "EXPIRED"
        state = snap(owner)
        assert state.holds[0][0] == "EXPIRED" and state.bindings == ["EXPIRED"]
        assert state.bucket == (0, 0, 0)  # expiry retained; separate correction
        from app.models import TrainingAllowanceCompensation
        assert TrainingAllowanceCompensation.query.count() == 1


def test_released_zero_attempt_hold_is_not_reconsumed_by_the_sweep(chain):
    from app.services import ai_allowances
    from app.services.mobile_training_generation import allowance
    owner = chain.owners[0]
    with chain.app.app_context():
        operation_id = _deferred_zero_attempt_failure(chain, owner, "a1-sweep")
        assert allowance.settle(operation_id) == "RELEASED"
        ai_allowances.expire_abandoned(now=datetime.now(timezone.utc) + timedelta(hours=1))
        _unbilled(snap(owner))


# ── 15: a distinct key after a terminal refusal ───────────────────────────
def test_distinct_key_after_refusal_is_a_new_action_with_its_own_hold(chain):
    from app.services import ai_consent
    from app.services.mobile_training_generation import allowance
    from app.services.mobile_training_generation.errors import StoredGenerationFailure
    owner = chain.owners[0]
    real = allowance.preflight_consent
    with chain.app.app_context():
        chain.monkeypatch.setattr(allowance, "preflight_consent", lambda user_id: (
            real(user_id), consent(owner, chain.policy, "revoke", "revoke-distinct")))
        consent(owner, chain.policy, "affirmative_grant", "grant-distinct-1")
        with pytest.raises(ConsentDenied):
            generate(owner, "a1-distinct-1")
        chain.monkeypatch.setattr(allowance, "preflight_consent", real)
        with pytest.raises(ai_consent.ConsentDenied):
            generate(owner, "a1-distinct-2")  # still revoked: refused before any hold
        assert len(snap(owner).holds) == 1
        consent(owner, chain.policy, "affirmative_grant", "grant-distinct-2")
        generate(owner, "a1-distinct-3")
        state = snap(owner)
        assert [op[0] for op in state.operations] == ["FAILED", "SUCCEEDED"]
        assert [h[0] for h in state.holds] == ["RELEASED", "COMMITTED"]
        assert state.bucket == (1, 0, 0) and chain.calls == {"bedrock": 0, "openai": 1}


# ── 16: account deletion during settlement ────────────────────────────────
def test_account_deletion_before_settlement_leaves_nothing_and_never_raises(chain):
    from app.extensions import db
    from app.models import (
        AiAllowanceBucket, AiUsageReservation, TrainingPlanGenerationAllowance,
        TrainingPlanGenerationOperation)
    from app.services.mobile_training_generation import allowance
    owner, other = chain.owners
    with chain.app.app_context():
        operation_id = _deferred_zero_attempt_failure(chain, owner, "a1-deleted")
        with db.engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM user_session WHERE user_id=%s", (owner,))
            conn.exec_driver_sql('DELETE FROM "user" WHERE id=%s', (owner,))
        assert allowance.settle(operation_id) is None
        allowance.settle_pending(owner)
        db.session.remove()
        for model in (TrainingPlanGenerationOperation, TrainingPlanGenerationAllowance,
                      AiUsageReservation, AiAllowanceBucket):
            assert model.query.filter_by(user_id=owner).count() == 0
        assert snap(other).holds == []


# ── 17/18: OFF parity and the success path ────────────────────────────────
def test_enforcement_off_takes_no_preflight_hold_or_attempt_marker(chain):
    from app.services import ai_consent
    from app.services.mobile_training_generation import store
    owner = chain.owners[0]
    chain.app.config["AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED"] = False
    marks, preflights = [], []
    real_mark = store.mark_attempt_possible
    chain.monkeypatch.setattr(store, "mark_attempt_possible", lambda i: (marks.append(i), real_mark(i)))
    chain.monkeypatch.setattr(ai_consent, "preflight", lambda *a: preflights.append(a))
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "grant-off")
        generate(owner, "a1-off")
        state = snap(owner)
        assert state.operations == [("SUCCEEDED", True, None)]
        assert state.holds == [] and state.bindings == [] and state.bucket is None
        assert marks == [] and preflights == [] and chain.calls["openai"] == 1


def test_success_consumes_exactly_one_unit_and_marker_precedes_the_sdk(chain):
    from app.extensions import db
    owner = chain.owners[0]
    seen = []

    def probe():
        assert not db.session().in_transaction()  # nothing spans provider I/O
        with db.engine.begin() as conn:
            seen.append(conn.exec_driver_sql(
                "SELECT provider_attempt_possible FROM training_plan_generation_operation "
                "WHERE user_id=%s", (owner,)).scalar_one())
            for sql in ("SELECT id FROM training_plan_generation_operation WHERE user_id=%s FOR UPDATE NOWAIT",
                        "SELECT operation_id FROM training_plan_generation_allowance WHERE user_id=%s FOR UPDATE NOWAIT",
                        "SELECT id FROM ai_usage_reservation WHERE user_id=%s FOR UPDATE NOWAIT",
                        "SELECT id FROM ai_allowance_bucket WHERE user_id=%s FOR UPDATE NOWAIT"):
                seen.append(len(conn.exec_driver_sql(sql, (owner,)).all()))
        with db.engine.begin() as conn, pytest.raises(sa.exc.OperationalError):
            # The D1 account fence (pre-existing contract) is what is held.
            conn.exec_driver_sql('SELECT id FROM "user" WHERE id=%s FOR UPDATE NOWAIT', (owner,))
        return "ok"
    chain.behaviour[:] = [probe]
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "grant-success")
        first = generate(owner, "a1-success")
        assert seen[0] is True  # committed and visible before the SDK call
        assert seen[1:4] == [1, 1, 1] and seen[4] >= 1  # all present, all lockable NOWAIT
        for _ in range(2):
            assert generate(owner, "a1-success").replayed is True
        state = snap(owner)
        assert state.operations == [("SUCCEEDED", True, True)] and state.plans == 1
        assert state.holds == [("COMMITTED", "success", "UNKNOWN", None)]
        assert state.bucket == (1, 0, 0) and state.bindings == ["COMMITTED"]
        assert chain.calls == {"bedrock": 0, "openai": 1} and first.replayed is False


def test_unrecordable_evidence_refuses_the_attempt_instead_of_sending_it(chain):
    """A retired/terminal operation cannot record a marker, so nothing is sent."""
    from app.extensions import db
    from app.services import ai_provider_call
    from app.services.mobile_training_generation.errors import GenerationPersistenceUnavailable
    owner = chain.owners[0]

    @contextmanager
    def slot(*args, **kwargs):  # another request retired this operation meanwhile
        with db.engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE training_plan_generation_operation SET status='FAILED', "
                "error_code='TRAINING_PLAN_GENERATION_OUTCOME_UNKNOWN' WHERE user_id=%s", (owner,))
        yield
    chain.monkeypatch.setattr(ai_provider_call, "model_concurrency_slot", slot)
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "grant-unrecordable")
        with pytest.raises(GenerationPersistenceUnavailable):
            generate(owner, "a1-unrecordable")  # the late worker is fenced, as before
        state = snap(owner)
        assert state.operations == [("FAILED", True, False)]
        assert chain.calls == {"bedrock": 0, "openai": 0}  # no SDK call, no fallback


# ── Migration e7a1c3d5f9b2 on PostgreSQL ───────────────────────────────────
from tests.test_mobile_training_allowance_pg import (  # noqa: E402,F401
    _b0d3_schema, _module, _run, migrate_engine,
)

EVIDENCE = "e7a1c3d5f9b2_training_attempt_evidence.py"


def _b1e4_schema(conn):
    _b0d3_schema(conn)
    _run(conn, _module("b1e4f5a6c7d8_training_allowance_association.py").upgrade)


def _column(conn):
    return {c["name"]: c for c in sa.inspect(conn).get_columns(
        "training_plan_generation_operation")}.get("provider_attempt_possible")


def test_evidence_migration_adds_nullable_marker_reenters_and_retains_history(migrate_engine):
    with migrate_engine.begin() as conn:
        _b1e4_schema(conn)
        conn.exec_driver_sql('INSERT INTO "user" (id) VALUES (1)')
        conn.exec_driver_sql(
            "INSERT INTO training_plan_generation_operation (user_id,idempotency_key,request_fingerprint,"
            "status,attempt_count,quota_reserved,created_at,updated_at,provider_dispatch_possible,"
            "allowance_metered) VALUES (1,'historical','f','FAILED',1,false,now(),now(),true,true)")
        migration = _module(EVIDENCE)
        assert migration.down_revision == "b1e4f5a6c7d8" and migration.expand_contract == "expand"
        _run(conn, migration.upgrade)
        _run(conn, migration.upgrade)  # create_all-first / reentrant boot
        column = _column(conn)
        assert str(column["type"]) == "BOOLEAN" and column["nullable"] is True
        assert column.get("default") is None
        # No backfill: historical operations stay untracked (UNKNOWN settlement).
        assert conn.exec_driver_sql(
            "SELECT provider_attempt_possible FROM training_plan_generation_operation").all() == [(None,)]
        _run(conn, migration.downgrade)  # nothing recorded yet: reversible
        assert _column(conn) is None
        _run(conn, migration.upgrade)
        conn.exec_driver_sql("UPDATE training_plan_generation_operation SET provider_attempt_possible=false")
        with pytest.raises(RuntimeError, match="must be retained"):
            _run(conn, migration.downgrade)
        assert _column(conn) is not None


@pytest.mark.parametrize("ddl", [
    "BOOLEAN NOT NULL DEFAULT false",
    "BOOLEAN DEFAULT false",
    "VARCHAR(8)",
    "INTEGER",
])
def test_evidence_migration_refuses_an_incompatible_existing_column(migrate_engine, ddl):
    with migrate_engine.begin() as conn:
        _b1e4_schema(conn)
        conn.exec_driver_sql(
            f"ALTER TABLE training_plan_generation_operation ADD COLUMN provider_attempt_possible {ddl}")
        with pytest.raises(RuntimeError, match="incompatible"):
            _run(conn, _module(EVIDENCE).upgrade)


# ── A2: retirement and physical SDK admission share D1's account fence ─────
def _a2_retire_in_worker(chain, owner):
    """A separate worker uses the production owner-arbitration entry order."""
    from app.extensions import db
    from app.services.mobile_training_generation import store
    from app.services.mobile_training_generation.locking import try_owner_lock
    results = []

    def retire():
        with chain.app.app_context():
            try:
                with try_owner_lock(owner) as acquired:
                    assert acquired
                    store.retire_abandoned(owner)
                results.append("retired")
            except Exception as error:
                results.append(error)
            finally:
                db.session.remove()
    worker = threading.Thread(target=retire)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "retirement waited across provider I/O"
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize("boundary", ["after_consent", "after_marker", "in_sdk"])
@pytest.mark.parametrize("sdk_outcome", ["ok", "timeout"])
def test_a2_active_d1_fence_refuses_retirement_until_sdk_exit(chain, boundary, sdk_outcome):
    from app.extensions import db
    from app.services import ai, ai_consent
    from app.services.mobile_training_generation import store
    from app.services.mobile_training_generation.errors import GenerationPersistenceUnavailable
    owner, other = chain.owners
    events = []
    original_sdk = ai.openai_client.chat.completions.create

    def blocked_retirement():
        result = _a2_retire_in_worker(chain, owner)
        assert isinstance(result, GenerationPersistenceUnavailable)
        state = snap(owner)
        assert state.operations == [("IN_PROGRESS", True, boundary != "after_consent")]
        assert snap(other).operations == []
        # The failed retirement left no operation or advisory lock behind.
        from app.services.mobile_training_generation.locking import try_owner_lock
        with try_owner_lock(owner) as acquired:
            assert acquired
        db.session.rollback()
        events.append("RETIRE_REFUSED")

    def sdk(**kwargs):
        events.append("SDK_ENTRY")
        if boundary == "in_sdk":
            blocked_retirement()
        response = original_sdk(**kwargs)
        events.append("SDK_RETURN")
        return response
    chain.monkeypatch.setattr(ai.openai_client.chat.completions, "create", sdk)
    real_marker = store.mark_attempt_possible

    def marker(op):
        _age(owner)
        if boundary == "after_consent":
            blocked_retirement()
        real_marker(op)
        events.append("MARKER_COMMITTED")
        if boundary == "after_marker":
            blocked_retirement()
    chain.monkeypatch.setattr(store, "mark_attempt_possible", marker)
    real_dispatch = ai_consent.dispatch

    @contextmanager
    def dispatch(*args, **kwargs):
        try:
            with real_dispatch(*args, **kwargs):
                yield
        finally:
            # AFTER D1 releases, BEFORE staging or another recovery attempt.
            events.append("D1_RELEASED")
            assert _a2_retire_in_worker(chain, owner) == "retired"
            events.append("RETIRE_COMMITTED")
    chain.monkeypatch.setattr(ai_consent, "dispatch", dispatch)
    if sdk_outcome == "timeout":
        chain.behaviour[:] = ["timeout"]
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "a2-fence-grant")
        with pytest.raises(GenerationPersistenceUnavailable):
            generate(owner, "a2-fenced")
        assert events.index("SDK_ENTRY") < events.index("D1_RELEASED") < events.index("RETIRE_COMMITTED")
        if boundary != "in_sdk":
            assert events.index("RETIRE_REFUSED") < events.index("SDK_ENTRY")
        state = snap(owner)
        assert state.operations == [("FAILED", True, True)] and state.plans == 0
        assert chain.calls == {"bedrock": 0, "openai": 1}
        # Same key is terminal and cannot cause another physical attempt.
        from app.services.mobile_training_generation.errors import StoredGenerationFailure
        with pytest.raises(StoredGenerationFailure):
            generate(owner, "a2-fenced")
        assert chain.calls["openai"] == 1
        assert snap(owner).bucket == (1, 0, 0)


def test_a2_retirement_before_d1_refuses_sdk_and_new_key_is_independent(chain):
    from app.services import ai_provider_call
    from app.services.mobile_training_generation import store
    from app.services.mobile_training_generation.errors import GenerationPersistenceUnavailable
    owner = chain.owners[0]

    @contextmanager
    def capacity(*args, **kwargs):
        _age(owner)
        assert _a2_retire_in_worker(chain, owner) == "retired"
        yield
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "a2-first-grant")
        with chain.monkeypatch.context() as patch:
            patch.setattr(ai_provider_call, "model_concurrency_slot", capacity)
            with pytest.raises(GenerationPersistenceUnavailable):
                generate(owner, "a2-retire-first")
        state = snap(owner)
        assert state.operations == [("FAILED", True, False)] and sum(chain.calls.values()) == 0
        generate(owner, "a2-explicit-new-key")
        state = snap(owner)
        assert state.operations == [("FAILED", True, False), ("SUCCEEDED", True, True)]
        assert state.bindings == ["EXPIRED", "COMMITTED"]
        assert state.bucket == (1, 0, 0) and state.plans == 1 and chain.calls["openai"] == 1


@pytest.mark.parametrize("boundary", ["before_marker", "after_marker", "in_sdk"])
def test_a2_worker_crash_releases_fence_and_allows_retirement(chain, boundary):
    from app.services.mobile_training_generation import store
    from app.services.mobile_training_generation.errors import StoredGenerationFailure
    owner = chain.owners[0]
    real = store.mark_attempt_possible

    def marker(op):
        _age(owner)
        if boundary == "before_marker":
            raise Crash()
        real(op)
        if boundary == "after_marker":
            raise Crash()
    chain.monkeypatch.setattr(store, "mark_attempt_possible", marker)
    if boundary == "in_sdk":
        chain.behaviour[:] = ["crash"]
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "a2-crash-grant")
        with pytest.raises(Crash):
            generate(owner, "a2-crash")
        assert _a2_retire_in_worker(chain, owner) == "retired"
        with pytest.raises(StoredGenerationFailure):
            generate(owner, "a2-crash")
        state = snap(owner)
        assert state.operations == [("FAILED", True, boundary != "before_marker")]
        assert state.bucket == (int(boundary != "before_marker"), 0, 0) and state.bindings == ["EXPIRED"]
        assert chain.calls["openai"] == int(boundary == "in_sdk")


def test_a2_retirement_operation_user_reverse_order_does_not_deadlock(chain):
    """D1 owns user and waits for operation; retirement must not wait for user."""
    from app.extensions import db
    from app.services.mobile_training_generation import store
    from app.services.mobile_training_generation.errors import GenerationPersistenceUnavailable
    owner = chain.owners[0]
    real = store.authorize_dispatch

    def crash(op):
        real(op)
        raise Crash()
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "a2-reverse-grant")
        with chain.monkeypatch.context() as patch:
            patch.setattr(store, "authorize_dispatch", crash)
            with pytest.raises(Crash):
                generate(owner, "a2-reverse")
        _age(owner)
        engine = db.engine
        user_locked = threading.Event()
        finished, failures, workers = [], [], []

        def admission():
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql("SET LOCAL lock_timeout = '2000ms'")
                    conn.exec_driver_sql('SELECT id FROM "user" WHERE id=%s FOR UPDATE', (owner,))
                    user_locked.set()
                    conn.exec_driver_sql("SELECT id FROM training_plan_generation_operation WHERE user_id=%s FOR UPDATE", (owner,))
                    finished.append(True)
            except BaseException as error:
                failures.append(error)

        def after_operation_lock(conn, cursor, statement, parameters, context, executemany):
            if ("training_plan_generation_operation" in statement
                    and "FOR UPDATE" in statement and not workers):
                worker = threading.Thread(target=admission)
                workers.append(worker)
                worker.start()
                assert user_locked.wait(5)
        sa.event.listen(engine, "after_cursor_execute", after_operation_lock)
        try:
            start = time.monotonic()
            with pytest.raises(GenerationPersistenceUnavailable):
                store.retire_abandoned(owner)
            assert time.monotonic() - start < 1.5
        finally:
            sa.event.remove(engine, "after_cursor_execute", after_operation_lock)
            for worker in workers:
                worker.join(5)
        assert workers and not failures and finished == [True]
        assert all(not worker.is_alive() for worker in workers)
        assert snap(owner).operations == [("IN_PROGRESS", True, False)]
        store.retire_abandoned(owner)
        assert snap(owner).operations == [("FAILED", True, False)]


@pytest.mark.parametrize("order", ["sweep_first", "retirement_first"])
def test_a2_zero_attempt_expiry_orders_compensate_original_consumption(chain, order):
    from app.services import ai_allowances
    from app.services.mobile_training_generation import store, allowance
    owner = chain.owners[0]
    real = store.authorize_dispatch

    def crash(op):
        real(op)
        raise Crash()
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "a2-expiry-grant")
        with chain.monkeypatch.context() as patch:
            patch.setattr(store, "authorize_dispatch", crash)
            with pytest.raises(Crash):
                generate(owner, "a2-zero-expired")
        _age(owner)
        actions = (ai_allowances.expire_abandoned, lambda: store.retire_abandoned(owner))
        for action in actions if order == "sweep_first" else reversed(actions):
            action()
        for _ in range(3):
            allowance.settle_pending(owner)
            ai_allowances.expire_abandoned()
        state = snap(owner)
        assert state.operations == [("FAILED", True, False)]
        assert state.holds == [("EXPIRED", "expired", "UNKNOWN", None)]
        assert state.bindings == ["EXPIRED"] and state.bucket == (0, 0, 0)
        assert sum(chain.calls.values()) == 0


@pytest.mark.parametrize("action", ["revoke", "delete"])
def test_a2_retirement_concurrent_with_revocation_or_deletion(chain, action):
    from app.extensions import db
    from app.services.mobile_training_generation import store
    from app.services.mobile_training_generation.errors import GenerationPersistenceUnavailable
    owner, other = chain.owners
    real = store.mark_attempt_possible
    workers, outcomes, errors = [], [], []

    def marker(op):
        _age(owner)
        real(op)
    chain.monkeypatch.setattr(store, "mark_attempt_possible", marker)

    def mutate():
        with chain.app.app_context():
            try:
                if action == "revoke":
                    consent(owner, chain.policy, "revoke", "a2-concurrent-revoke")
                else:
                    with db.engine.begin() as conn:
                        conn.exec_driver_sql("SET LOCAL lock_timeout = '2000ms'")
                        conn.exec_driver_sql("DELETE FROM user_session WHERE user_id=%s", (owner,))
                        conn.exec_driver_sql('DELETE FROM "user" WHERE id=%s', (owner,))
                outcomes.append(action)
            except BaseException as error:
                errors.append(error)
            finally:
                db.session.remove()

    def in_flight():
        worker = threading.Thread(target=mutate)
        workers.append(worker)
        worker.start()
        deadline = time.monotonic() + 1.0
        with db.engine.connect() as conn:
            while time.monotonic() < deadline:
                if conn.exec_driver_sql(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND wait_event_type='Lock'").scalar():
                    break
            else:
                pytest.fail("mutation did not queue behind D1")
        assert isinstance(_a2_retire_in_worker(chain, owner), GenerationPersistenceUnavailable)
        return "ok"
    chain.behaviour[:] = [in_flight]
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "a2-concurrent-grant")
        try:
            generate(owner, "a2-concurrent")
        except GenerationPersistenceUnavailable:
            assert action == "delete"
        finally:
            for worker in workers:
                worker.join(5)
        assert outcomes == [action] and not errors
        assert all(not worker.is_alive() for worker in workers)
        assert chain.calls == {"bedrock": 0, "openai": 1}
        assert snap(other).operations == [] and snap(other).holds == []
        if action == "delete":
            assert snap(owner).operations == snap(owner).holds == []
        else:
            from app.services import ai_consent
            assert ai_consent.status(owner)["generation"] == 2


def test_a2_retirement_fails_closed_on_account_lock_then_recovers(chain):
    from app.extensions import db
    from app.services.mobile_training_generation import store
    from app.services.mobile_training_generation.errors import GenerationPersistenceUnavailable
    owner = chain.owners[0]
    real = store.authorize_dispatch

    def crash(op):
        real(op)
        raise Crash()
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "a2-busy-grant")
        with chain.monkeypatch.context() as patch:
            patch.setattr(store, "authorize_dispatch", crash)
            with pytest.raises(Crash):
                generate(owner, "a2-busy")
        _age(owner)
        blocker = db.engine.connect()
        blocker.begin()
        try:
            blocker.exec_driver_sql('SELECT id FROM "user" WHERE id=%s FOR UPDATE', (owner,))
            start = time.monotonic()
            with pytest.raises(GenerationPersistenceUnavailable):
                store.retire_abandoned(owner)
            assert time.monotonic() - start < 1.5
            assert not db.session().in_transaction()
        finally:
            # Models the blocking worker exiting/crashing: transaction releases.
            blocker.close()
        assert _a2_retire_in_worker(chain, owner) == "retired"
        assert snap(owner).operations == [("FAILED", True, False)]
        assert sum(chain.calls.values()) == 0
