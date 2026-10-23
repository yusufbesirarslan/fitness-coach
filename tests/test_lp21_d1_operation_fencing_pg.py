"""LP21 D1 operation-wide fencing on real PostgreSQL (R01-R10).

The REAL chain runs end to end: native command -> canonical generator -> heavy
router -> recovery ladder -> ``ai_provider_call.admit`` -> D1
``ai_consent.dispatch`` -> mocked SDK client, then the fenced stage /
publication / canonical-write transactions. Authority is a synthetic
disclosure on a disposable database only; ``POLICIES`` in source stays empty.
Nothing here stubs ``begin_operation``, ``fence`` or ``authorize_operation``.

Interleavings are forced, never slept for: a revoker parks INSIDE ``mutate``
holding its ``FOR UPDATE`` owner lock (``_parked_mutation``), a writer parks
just before its owner lock or just after its fence decision, and the test
proceeds only once PostgreSQL reports the contender waiting on that lock.

  R01 revoke (+regrant) before the first physical dispatch
  R02 revoke+regrant before a fresh recovery / fallback admission
  R03 revoke while a provider call holds the authority lock (and lock timeout)
  R04 revoke after output: before stage, between stage and commit, at recovery
  R05 revoke+regrant between retries inside one admission
  R06 delayed work: GENERATED recovery, expired replacement lease, legacy lease
  R07 revoke vs canonical write: each winner, for every fenced writer
  R08 replay of refused / superseded work after a regrant
  R09 account isolation of operation authority
  R10 partial failure: authority outage keeps work recoverable; committed work
      reconciles without a provider call
Plus: provenance-less (browser alias) writes, #456 routing fallback, and
non-vacuity controls proving the races would have succeeded without fences.
"""
import json
import threading
import time
from contextlib import contextmanager, nullcontext
from functools import partial
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app import config as app_config
from app.services.ai_consent import ConsentDenied

from tests.test_mobile_training_generation_api import _provider_document
from tests.test_mobile_training_generation_pg import (  # noqa: F401
    pg_generation_app, _request, _document,
)

pytestmark = pytest.mark.pg_concurrency

SECRET = "disposable-pg-training-generation-test"
NUTRITION_REQUEST = {"proteins": ["Tavuk Göğsü"], "carbs": ["Pirinç"],
                     "fats": ["Zeytinyağı"], "custom_foods": []}
OPTION = {"isim": "Plan A",
          "kahvalti": {"yemekler": ["Yumurta - 3 adet"], "kalori": 420,
                       "protein": 28, "karb": 35, "yag": 18},
          "ogle": {"yemekler": ["Tavuk göğsü - 150g", "Pirinç - 100g"],
                   "kalori": 380, "protein": 48, "karb": 28, "yag": 5},
          "toplam_kalori": 800, "toplam_protein": 76, "toplam_karb": 63,
          "toplam_yag": 23}
WAIT = 15


class Crash(BaseException):
    """Escapes application handlers, like abrupt worker death."""


class ServerError(Exception):
    status_code = 500  # retried inside one admission (ai_provider_call)


def _response(provider, text):
    if provider == "bedrock":
        return SimpleNamespace(stop_reason="end_turn", usage=None,
                               content=[SimpleNamespace(type="text", text=text)])
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=text))])


@pytest.fixture
def rig(pg_generation_app):
    """Metered app + synthetic disclosures + mocked SDK clients (no network)."""
    from ai_allowance_support import fixture_policy
    from app.extensions import db
    from app.models import TrainingPlan, UserSession
    from app.services import ai, ai_provider_call, ai_recovery, ai_spend_guard
    app, owners, monkeypatch = pg_generation_app
    app.config.update(
        AI_ALLOWANCE_LEDGER_ENABLED=True,
        AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_POLICY=fixture_policy(
            max_billed=0, starter_units={"training_generation": 6, "coach": 1}))
    monkeypatch.setattr(ai_provider_call, "model_concurrency_slot", lambda *a, **k: nullcontext())
    monkeypatch.setattr(ai_spend_guard, "charge", lambda *a, **k: None)
    monkeypatch.setattr(ai_recovery, "_get_redis", lambda: None)
    rig = SimpleNamespace(app=app, owners=owners, monkeypatch=monkeypatch, calls=[],
                          behaviour=[], backoff=[], recovery=[], text=None)

    def sdk(provider, **kwargs):
        rig.calls.append((provider, kwargs.get("model")))
        step = rig.behaviour.pop(0) if rig.behaviour else "ok"
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
        text = rig.text or json.dumps(_provider_document("ex_barbell_back_squat"),
                                      ensure_ascii=False)
        return _response(provider, text)

    monkeypatch.setattr(ai, "openai_client", SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=partial(sdk, "openai")))))
    monkeypatch.setattr(ai, "bedrock_client", SimpleNamespace(messages=SimpleNamespace(
        create=partial(sdk, "bedrock"))))
    monkeypatch.setattr(ai, "BEDROCK_ENABLED", False)
    monkeypatch.setattr(ai_provider_call.time, "sleep",
                        lambda _: rig.backoff.pop(0)() if rig.backoff else None)
    monkeypatch.setattr(ai_recovery, "_sleep",
                        lambda _: rig.recovery.pop(0)() if rig.recovery else None)
    rig.policies = disclose(monkeypatch)
    rig.training, rig.nutrition = rig.policies["training_plan"], rig.policies["nutrition_plan"]
    with app.app_context():
        for session in UserSession.query.filter(UserSession.user_id.in_(owners)):
            session.target_calories = 2100
        db.session.commit()

    def with_plan(owner):
        with app.app_context():
            db.session.add(TrainingPlan(user_id=owner, plan_data=json.dumps(_document()), score=8))
            db.session.commit()
    rig.with_plan = with_plan
    return rig


def disclose(monkeypatch, recipients=None):
    from app.services import ai, ai_consent
    recipients = recipients or (("bedrock", app_config.BEDROCK_MODEL), ("openai", ai.OPENAI_MODEL))
    policies = {}
    for feature, (purpose, categories) in ai_consent.BINDINGS.items():
        policies[feature] = ai_consent.Disclosure(
            "synthetic-lp21-" + feature, purpose, categories, tuple(recipients), (("en", "a" * 64),))
    monkeypatch.setattr(ai_consent, "POLICIES", {p.version: p for p in policies.values()})
    return policies


def command(policy, action, request_id, expected):
    from app.services import ai_consent
    return dict(action=action, purpose=policy.purpose, version=policy.version, locale="en",
                text_digest="a" * 64, contract_digest=ai_consent.contract_digest(policy),
                expected_generation=expected, request_id=request_id)


def consent(owner, policy, action, request_id):
    from app.services import ai_consent
    return ai_consent.mutate(owner, command(policy, action, request_id,
                                            ai_consent.generation(owner)))


def regrant(owner, policy, tag):
    """revoke -> regrant: the account is authorized again, at g+2."""
    consent(owner, policy, "revoke", "revoke-" + tag)
    consent(owner, policy, "affirmative_grant", "regrant-" + tag)


def first_plan(owner, key):
    from app.extensions import db
    from app.models import User
    from app.services import ai
    from app.services.mobile_training_generation import service
    return service.generate_and_persist(
        db.session.get(User, owner), _request(), key,
        chat_fn=partial(ai._heavy_complete, feature="training_plan"),
        provider_guard=nullcontext)


def proposal(owner, key):
    from app.extensions import db
    from app.models import User
    from app.services import ai
    from app.services.ai_spend_guard import subject_scope
    from app.services.training_plan_replacement import generation
    with subject_scope(owner):  # the native route's g.mobile_user
        return generation.generate_proposal(
            db.session.get(User, owner), _request(), key,
            chat_fn=partial(ai._heavy_complete, feature="training_plan"),
            provider_guard=nullcontext)


def confirm(owner, token, key):
    from app.services.training_plan_replacement import service
    return service.confirm_replacement(owner, token, key)


def nutrition_proposals(rig, owner):
    from app.extensions import db
    from app.models import User
    from app.services import ai
    from app.services.nutrition_native import plan
    from app.services.ai_spend_guard import subject_scope
    rig.text = json.dumps({"planlar": [OPTION]}, ensure_ascii=False)
    with subject_scope(owner):  # the native route's g.mobile_user
        return plan.generate_proposals(db.session.get(User, owner), SECRET,
                                       dict(NUTRITION_REQUEST), ai._heavy_chat, False)


def nutrition_save(owner, proposal):
    from app.services.nutrition_native import plan, preconditions
    return plan.save_plan(owner, SECRET, preconditions.parse_create_or_match(None, "*"),
                          {"plan": proposal["plan"], "proposal_token": proposal["proposal_token"]})


def first_plan_rows(owner):
    from app.extensions import db
    from app.models import TrainingPlan, TrainingPlanGenerationOperation as Op
    db.session.remove()
    return SimpleNamespace(
        ops=[(o.status, o.error_code, o.candidate_plan_data is not None)
             for o in Op.query.filter_by(user_id=owner).order_by(Op.id)],
        plans=TrainingPlan.query.filter_by(user_id=owner).count())


def holds(owner):
    from app.models import AiUsageReservation
    return [(r.state, r.billing_evidence) for r in
            AiUsageReservation.query.filter_by(user_id=owner).order_by(AiUsageReservation.id)]


def current_plan(owner):
    from app.extensions import db
    from app.models import TrainingPlan
    db.session.remove()
    row = (TrainingPlan.query.filter_by(user_id=owner)
           .order_by(TrainingPlan.created_at.desc(), TrainingPlan.id.desc()).first())
    return None if row is None else (row.lineage_id, row.mutation_version)


def nutrition_rows(owner):
    from app.extensions import db
    from app.models import NutritionPlan
    db.session.remove()
    return [json.loads(r.plan_data)["isim"] for r in NutritionPlan.query.filter_by(user_id=owner)]


# ── choreography ───────────────────────────────────────────────────────────

class Worker(threading.Thread):
    def __init__(self, app, fn, name):
        super().__init__(name=name, daemon=True)
        self.app, self.fn, self.result, self.error = app, fn, None, None

    def run(self):
        from app.extensions import db
        with self.app.app_context():
            try:
                self.result = self.fn()
            except BaseException as error:  # surfaced by the test
                self.error = error
            finally:
                db.session.remove()

    def finish(self):
        self.join(WAIT)
        assert not self.is_alive(), f"{self.name} did not finish"
        return self


def lock_waiters():
    import os
    engine = sa.create_engine(os.environ["PG_TEST_DATABASE_URL"])
    try:
        with engine.connect() as conn:
            return conn.execute(sa.text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND wait_event_type = 'Lock'")).scalar_one()
    finally:
        engine.dispose()


def await_lock_waiter():
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        if lock_waiters():
            return
        time.sleep(0.02)
    raise AssertionError("no contender ever waited on the owner lock")


@contextmanager
def _parked_mutation(monkeypatch):
    """A ``mutate`` on a thread named ``revoker`` parks holding its owner lock."""
    from app.services import ai_consent
    real = ai_consent._state
    locked, release = threading.Event(), threading.Event()

    def state(conn, owner):
        if threading.current_thread().name == "revoker" and not locked.is_set():
            locked.set()
            assert release.wait(WAIT)
        return real(conn, owner)
    monkeypatch.setattr(ai_consent, "_state", state)
    yield locked, release


def _park(monkeypatch, module, name, *, after=False):
    """Park the ``writer`` thread once, just before (or after) ``module.name``."""
    real = getattr(module, name)
    arrived, go = threading.Event(), threading.Event()

    def parked(*args, **kwargs):
        if threading.current_thread().name != "writer" or arrived.is_set():
            return real(*args, **kwargs)
        if after:
            result = real(*args, **kwargs)
            arrived.set()
            assert go.wait(WAIT)
            return result
        arrived.set()
        assert go.wait(WAIT)
        return real(*args, **kwargs)
    monkeypatch.setattr(module, name, parked)
    return arrived, go


def revoke_first(rig, owner, policy, generation, writer_fn, module, name):
    """Revoke holds the owner lock; the writer then queues behind it on its lock."""
    from app.services import ai_consent
    arrived, go = _park(rig.monkeypatch, module, name)
    with _parked_mutation(rig.monkeypatch) as (locked, release):
        writer = Worker(rig.app, writer_fn, "writer")
        writer.start()
        assert arrived.wait(WAIT)
        revoker = Worker(rig.app, lambda: ai_consent.mutate(
            owner, command(policy, "revoke", "revoke-first-" + name, generation)), "revoker")
        revoker.start()
        assert locked.wait(WAIT)
        go.set()
        await_lock_waiter()  # the writer is queued behind the uncommitted revoke
        release.set()
        revoker.finish()
        writer.finish()
    assert revoker.error is None, revoker.error
    return writer


def write_first(rig, owner, policy, generation, writer_fn):
    """The writer's fence decided under the owner lock; revoke then queues behind it."""
    from app.services import ai_consent
    arrived, go = _park(rig.monkeypatch, ai_consent, "fence", after=True)
    writer = Worker(rig.app, writer_fn, "writer")
    writer.start()
    assert arrived.wait(WAIT)
    revoker = Worker(rig.app, lambda: ai_consent.mutate(
        owner, command(policy, "revoke", "revoke-after-write", generation)), "revoker")
    revoker.start()
    await_lock_waiter()  # revoke is queued behind the writer's open transaction
    go.set()
    writer.finish()
    revoker.finish()
    assert revoker.error is None, revoker.error
    return writer


# ── R01 / R05: revoke before dispatch, revoke+regrant inside one admission ─

@pytest.mark.parametrize("route", ["openai", "bedrock"])
def test_r01_regrant_after_capture_never_reauthorizes_the_first_dispatch(rig, route):
    from app.services import ai, ai_provider_call
    owner = rig.owners[0]
    rig.monkeypatch.setattr(ai, "BEDROCK_ENABLED", route == "bedrock")

    @contextmanager
    def slot(*args, **kwargs):  # operation captured g=1; account moves to g=3
        regrant(owner, rig.training, "r01-" + route)
        yield
    rig.monkeypatch.setattr(ai_provider_call, "model_concurrency_slot", slot)
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r01")
        with pytest.raises(ConsentDenied) as refused:
            first_plan(owner, "r01-key")
        assert (refused.value.code, refused.value.status) == ("AI_CONSENT_CHANGED", 403)
        rows = first_plan_rows(owner)
        assert rows.ops == [("FAILED", "AI_CONSENT_CHANGED", False)] and rows.plans == 0
        assert rig.calls == []  # no fallback, no SDK, even though g=3 is fully granted
        assert holds(owner) == [("RELEASED", "CONFIRMED_NOT_BILLED")]


def test_r05_regrant_between_retries_of_one_admission_stops_the_retry(rig):
    owner = rig.owners[0]
    rig.behaviour[:] = ["server_error"]
    rig.backoff[:] = [lambda: regrant(owner, rig.training, "r05")]
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r05")
        with pytest.raises(ConsentDenied) as refused:
            first_plan(owner, "r05-key")
        assert refused.value.code == "AI_CONSENT_CHANGED"
        assert [c[0] for c in rig.calls] == ["openai"]
        rows = first_plan_rows(owner)
        assert rows.ops == [("FAILED", "AI_CONSENT_CHANGED", False)] and rows.plans == 0
        # An earlier physical attempt is never re-labelled as nothing sent.
        assert holds(owner) == [("COMMITTED", "UNKNOWN")]


# ── R02: a fresh admission inside the same operation keeps the ORIGINAL g ──

def test_r02_regrant_before_recovery_admission_refuses_the_new_admission(rig):
    owner = rig.owners[0]
    rig.behaviour[:] = ["timeout"]
    rig.recovery[:] = [lambda: regrant(owner, rig.training, "r02")]
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r02")
        with pytest.raises(ConsentDenied) as refused:
            first_plan(owner, "r02-key")
        assert refused.value.code == "AI_CONSENT_CHANGED"
        assert [c[0] for c in rig.calls] == ["openai"]
        rows = first_plan_rows(owner)
        assert rows.ops == [("FAILED", "AI_CONSENT_CHANGED", False)] and rows.plans == 0
        assert holds(owner) == [("COMMITTED", "UNKNOWN")]


def test_r02_control_without_operation_generation_the_regrant_would_succeed(rig):
    """Non-vacuity: an admission that re-reads the account generation (the
    pre-LP21 behaviour) lets the regranted recovery attempt commit a plan."""
    from app.services import ai_consent
    owner = rig.owners[0]
    rig.monkeypatch.setattr(ai_consent, "admission_generation",
                            lambda subject, feature: ai_consent.generation(subject))
    rig.behaviour[:] = ["timeout"]
    rig.recovery[:] = [lambda: regrant(owner, rig.training, "r02-control")]
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r02-control")
        with pytest.raises(ConsentDenied):  # caught one fence later, at staging
            first_plan(owner, "r02-control-key")
        assert len(rig.calls) == 2  # the second admission DID reach the SDK


def test_r02_bedrock_failure_then_regrant_never_reaches_the_openai_fallback(rig):
    from app.services import ai, ai_usage
    owner = rig.owners[0]
    rig.monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    # The SDK step runs INSIDE dispatch's authority lock, so the account moves
    # at the router's fallback boundary instead (between the two admissions).
    rig.behaviour[:] = [lambda: (_ for _ in ()).throw(RuntimeError("synthetic Bedrock failure"))]
    real_scope = ai_usage.fallback_scope

    def scope(*args, **kwargs):
        regrant(owner, rig.training, "r02-fallback")
        return real_scope(*args, **kwargs)
    rig.monkeypatch.setattr(ai_usage, "fallback_scope", scope)
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r02-fallback")
        with pytest.raises(ConsentDenied) as refused:
            first_plan(owner, "r02-fallback-key")
        assert refused.value.code == "AI_CONSENT_CHANGED"
        assert [c[0] for c in rig.calls] == ["bedrock"]
        assert first_plan_rows(owner).plans == 0


def test_routing_55_fallback_to_an_ungranted_model_is_refused_at_dispatch(rig):
    """#456: the claude55 heavy fallback (Haiku) is its own recipient/model;
    an operation authorized for Sonnet 5.5 never reaches Haiku through it."""
    from app.services import ai
    owner = rig.owners[0]
    sonnet, haiku = "eu.anthropic.claude-sonnet-5-5", "eu.anthropic.claude-haiku-5-5"
    for name, value in (("AI_MODEL_ROUTING", "claude55"), ("BEDROCK_ENABLED", True),
                        ("BEDROCK_SONNET55_MODEL", sonnet), ("BEDROCK_HAIKU55_MODEL", haiku)):
        rig.monkeypatch.setattr(app_config, name, value, raising=False)
    rig.monkeypatch.setattr(ai, "BEDROCK_ENABLED", True)
    policies = disclose(rig.monkeypatch, recipients=(("bedrock", sonnet),))
    rig.behaviour[:] = ["ok", lambda: (_ for _ in ()).throw(RuntimeError("synthetic"))]
    with rig.app.app_context():
        consent(owner, policies["training_plan"], "affirmative_grant", "grant-55")
        assert first_plan(owner, "r55-ok").plan is not None  # positive control
        assert rig.calls == [("bedrock", sonnet)]
        other = rig.owners[1]
        consent(other, policies["training_plan"], "affirmative_grant", "grant-55-b")
        with pytest.raises(ConsentDenied) as refused:
            first_plan(other, "r55-fallback")
        assert refused.value.code == "AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE"
        assert rig.calls == [("bedrock", sonnet), ("bedrock", sonnet)]  # Haiku never called
        assert first_plan_rows(other).plans == 0


# ── R03: revoke while a provider call holds the authority lock ─────────────

def test_r03_revoke_during_provider_io_waits_then_withholds_the_result(rig):
    from app.services import ai_consent
    from app.services.mobile_training_generation import store
    owner = rig.owners[0]
    order, box = [], {}

    def blocked_sdk():
        revoker = Worker(rig.app, lambda: ai_consent.mutate(
            owner, command(rig.training, "revoke", "revoke-r03", 1)), "revoker-io")
        revoker.start()
        await_lock_waiter()  # revoke cannot acknowledge while the call holds the lock
        assert revoker.is_alive()
        order.append("sdk-returns")
        box["revoker"] = revoker
        return "ok"
    rig.behaviour[:] = [blocked_sdk]
    real_stage = store.stage_candidate

    def stage_after_revoke(*args, **kwargs):  # revoke commits before publication
        box["revoker"].finish()
        order.append("revoke-acknowledged")
        return real_stage(*args, **kwargs)
    rig.monkeypatch.setattr(store, "stage_candidate", stage_after_revoke)
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r03")
        with pytest.raises(ConsentDenied) as refused:
            first_plan(owner, "r03-key")
        assert box["revoker"].error is None
        assert order == ["sdk-returns", "revoke-acknowledged"]
        assert refused.value.code == "AI_CONSENT_CHANGED"
        rows = first_plan_rows(owner)
        assert rows.ops == [("FAILED", "AI_CONSENT_CHANGED", False)] and rows.plans == 0
        assert len(rig.calls) == 1 and ai_consent.generation(owner) == 2


def test_r03_revoke_lock_timeout_is_unavailable_never_a_false_acknowledgement(rig):
    from app.services import ai_consent
    owner = rig.owners[0]
    box = {}

    def slow_sdk():
        revoker = Worker(rig.app, lambda: ai_consent.mutate(
            owner, command(rig.training, "revoke", "revoke-r03-timeout", 1)), "revoker-io")
        revoker.start()
        box["revoker"] = revoker.finish()  # lock_timeout (2s) elapses inside the call
        return "ok"
    rig.behaviour[:] = [slow_sdk]
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r03-timeout")
        result = first_plan(owner, "r03-timeout-key")
        error = box["revoker"].error
        assert isinstance(error, ConsentDenied)
        assert (error.code, error.status) == ("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
        # Nothing was revoked, so the still-authorized result is committed.
        assert ai_consent.generation(owner) == 1 and result.plan is not None
        assert ai_consent.status(owner)["scopes"]["training.plan"]["state"] == "granted"


# ── R04: revoke after output, before / between persistence boundaries ─────

def test_r04_revoke_before_stage_withholds_the_output(rig):
    from app.services.mobile_training_generation import store
    owner = rig.owners[0]
    real = store.stage_candidate
    rig.monkeypatch.setattr(store, "stage_candidate", lambda *a, **k: (
        consent(owner, rig.training, "revoke", "revoke-r04a"), real(*a, **k))[1])
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r04a")
        with pytest.raises(ConsentDenied):
            first_plan(owner, "r04a-key")
        rows = first_plan_rows(owner)
        assert rows.ops == [("FAILED", "AI_CONSENT_CHANGED", False)] and rows.plans == 0
        assert len(rig.calls) == 1


@pytest.mark.parametrize("regranted", [False, True])
def test_r04_revoke_between_stage_and_commit_discards_the_candidate(rig, regranted):
    from app.services.mobile_training_generation import store
    from app.services.mobile_training_generation.errors import StoredGenerationFailure
    owner = rig.owners[0]
    real = store.commit_plan

    def withdraw_then_commit(*args, **kwargs):  # GENERATED is committed here
        if regranted:
            regrant(owner, rig.training, "r04b")
        else:
            consent(owner, rig.training, "revoke", "revoke-r04b")
        return real(*args, **kwargs)
    rig.monkeypatch.setattr(store, "commit_plan", withdraw_then_commit)
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r04b")
        with pytest.raises(ConsentDenied):
            first_plan(owner, "r04b-key")
        rows = first_plan_rows(owner)
        assert rows.ops == [("FAILED", "AI_CONSENT_CHANGED", False)] and rows.plans == 0
        rig.monkeypatch.setattr(store, "commit_plan", real)
        with pytest.raises(StoredGenerationFailure) as replay:  # R08 replay stays refused
            first_plan(owner, "r04b-key")
        assert replay.value.public_code == "AI_CONSENT_CHANGED"
        assert len(rig.calls) == 1 and first_plan_rows(owner).plans == 0


# ── R06 / R10: durable recovery of GENERATED work ──────────────────────────

def test_r06_generated_recovery_after_regrant_never_commits(rig):
    from app.services.mobile_training_generation import store
    from app.services.mobile_training_generation.errors import StoredGenerationFailure
    owner = rig.owners[0]
    real = store.commit_plan
    rig.monkeypatch.setattr(store, "commit_plan",
                            lambda *a, **k: (_ for _ in ()).throw(Crash()))
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r06")
        with pytest.raises(Crash):  # process lost after GENERATED was committed
            first_plan(owner, "r06-key")
        assert first_plan_rows(owner).ops == [("GENERATED", None, True)]
        regrant(owner, rig.training, "r06")
        rig.monkeypatch.setattr(store, "commit_plan", real)
        with pytest.raises(ConsentDenied) as refused:  # same key: recovery, no SDK
            first_plan(owner, "r06-key")
        assert refused.value.code == "AI_CONSENT_CHANGED"
        rows = first_plan_rows(owner)
        assert rows.ops == [("FAILED", "AI_CONSENT_CHANGED", False)] and rows.plans == 0
        with pytest.raises(StoredGenerationFailure):
            first_plan(owner, "r06-key")
        # A NEW explicit operation at the current grant is a fresh authority.
        assert first_plan(owner, "r06-new-key").plan is not None
        assert len(rig.calls) == 2


def test_r06_stale_generated_work_never_strands_a_new_operation(rig):
    from app.services.mobile_training_generation import store
    owner = rig.owners[0]
    real = store.commit_plan
    rig.monkeypatch.setattr(store, "commit_plan",
                            lambda *a, **k: (_ for _ in ()).throw(Crash()))
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r06b")
        with pytest.raises(Crash):
            first_plan(owner, "r06b-old")
        regrant(owner, rig.training, "r06b")
        rig.monkeypatch.setattr(store, "commit_plan", real)
        assert first_plan(owner, "r06b-new").plan is not None
        rows = first_plan_rows(owner)
        assert rows.ops == [("FAILED", "AI_CONSENT_CHANGED", False), ("SUCCEEDED", None, False)]
        assert rows.plans == 1


def test_r10_authority_outage_at_commit_keeps_generated_work_recoverable(rig):
    from app.extensions import db
    from app.services.mobile_training_generation import store
    owner = rig.owners[0]
    real = store.commit_plan

    def outage(*args, **kwargs):
        # Another holder keeps D1's authority unreadable for this commit only.
        from app.services import ai_consent
        real_state = ai_consent._state
        rig.monkeypatch.setattr(ai_consent, "_state", lambda conn, o: (_ for _ in ()).throw(
            sa.exc.OperationalError("SELECT", {}, Exception("synthetic outage"))))
        try:
            return real(*args, **kwargs)
        finally:
            rig.monkeypatch.setattr(ai_consent, "_state", real_state)
    rig.monkeypatch.setattr(store, "commit_plan", outage)
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r10")
        with pytest.raises(ConsentDenied) as refused:
            first_plan(owner, "r10-key")
        assert (refused.value.code, refused.value.status) == ("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
        assert first_plan_rows(owner).ops == [("GENERATED", None, True)]
        rig.monkeypatch.setattr(store, "commit_plan", real)
        db.session.remove()
        result = first_plan(owner, "r10-key")  # same g, no new provider call
        assert result.plan is not None and len(rig.calls) == 1
        assert first_plan_rows(owner).ops == [("SUCCEEDED", None, False)]


# ── R07: revoke vs every fenced writer, each winner ────────────────────────

@pytest.mark.parametrize("boundary", ["stage", "commit"])
def test_r07_first_plan_revoke_wins_the_owner_lock(rig, boundary):
    from app.services import ai_consent
    from app.services.mobile_training_generation import store
    owner = rig.owners[0]
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r07-" + boundary)
    if boundary == "stage":
        # stage_candidate's owner lock is the fence's own FOR NO KEY UPDATE.
        module, name = ai_consent, "fence"
    else:
        module, name = store, "lock_plan_owner"
    writer = revoke_first(rig, owner, rig.training, 1,
                          lambda: first_plan(owner, "r07-" + boundary), module, name)
    assert isinstance(writer.error, ConsentDenied), writer.error
    assert writer.error.code == "AI_CONSENT_CHANGED"
    with rig.app.app_context():
        rows = first_plan_rows(owner)
        assert rows.plans == 0 and rows.ops == [("FAILED", "AI_CONSENT_CHANGED", False)]


def test_r07_first_plan_commit_wins_then_reconciles_after_revoke(rig):
    owner = rig.owners[0]
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r07-write")
    # The FIRST fence a first plan meets is stage_candidate's; park at commit's.
    from app.services.mobile_training_generation import store
    real_stage = store.stage_candidate
    staged = threading.Event()
    rig.monkeypatch.setattr(store, "stage_candidate",
                            lambda *a, **k: (real_stage(*a, **k), staged.set())[0])
    from app.services import ai_consent
    real_fence = ai_consent.fence
    arrived, go = threading.Event(), threading.Event()

    def fence(authority, owner_id):
        real_fence(authority, owner_id)
        if staged.is_set() and not arrived.is_set():
            arrived.set()
            assert go.wait(WAIT)
    rig.monkeypatch.setattr(ai_consent, "fence", fence)
    writer = Worker(rig.app, lambda: first_plan(owner, "r07-write").plan.lineage_id, "writer")
    writer.start()
    assert arrived.wait(WAIT)
    revoker = Worker(rig.app, lambda: ai_consent.mutate(
        owner, command(rig.training, "revoke", "revoke-r07-write", 1)), "revoker")
    revoker.start()
    await_lock_waiter()
    go.set()
    writer.finish()
    revoker.finish()
    assert writer.error is None and revoker.error is None, (writer.error, revoker.error)
    with rig.app.app_context():
        assert ai_consent.generation(owner) == 2
        rows = first_plan_rows(owner)
        assert rows.ops == [("SUCCEEDED", None, False)] and rows.plans == 1
        # Committed before the withdrawal: the receipt reconciles, no new call.
        replay = first_plan(owner, "r07-write")
        assert replay.replayed and replay.plan.lineage_id == writer.result
        assert len(rig.calls) == 1 and first_plan_rows(owner).plans == 1


def _proposed(rig, owner, key="r07-proposal"):
    rig.with_plan(owner)
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-" + key)
        return proposal(owner, key), current_plan(owner)


def test_r07_replacement_confirm_revoke_wins(rig):
    from app.services import plan_replacement
    owner = rig.owners[0]
    made, before = _proposed(rig, owner)
    writer = revoke_first(rig, owner, rig.training, 1,
                          lambda: confirm(owner, made.proposal_token, "confirm-key-r07a"),
                          plan_replacement, "lock_plan_owner")
    assert isinstance(writer.error, ConsentDenied) and writer.error.code == "AI_CONSENT_CHANGED"
    with rig.app.app_context():
        from app.models import TrainingPlanReplacementReceipt as Receipt
        assert current_plan(owner) == before and Receipt.query.count() == 0
        regrant(owner, rig.training, "r07a")  # a newer grant never revives it
        with pytest.raises(ConsentDenied):
            confirm(owner, made.proposal_token, "confirm-key-r07a-2")
        assert current_plan(owner) == before


def test_r07_replacement_confirm_wins_then_replays_after_revoke(rig):
    owner = rig.owners[0]
    made, before = _proposed(rig, owner)
    writer = write_first(rig, owner, rig.training, 1,
                         lambda: confirm(owner, made.proposal_token, "confirm-key-r07b"))
    assert writer.error is None and writer.result.status == "APPLIED", writer.error
    with rig.app.app_context():
        after = current_plan(owner)
        assert after != before
        replay = confirm(owner, made.proposal_token, "confirm-key-r07b")
        assert replay.replayed and replay.status == "APPLIED"
        assert current_plan(owner) == after and len(rig.calls) == 1


def test_r07_replacement_publication_revoke_wins(rig):
    from app.services.training_plan_replacement import generation
    from app.models import TrainingPlanReplacementGenerationOperation as Op
    owner = rig.owners[0]
    rig.with_plan(owner)
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r07c")
    writer = revoke_first(rig, owner, rig.training, 1, lambda: proposal(owner, "r07c-key"),
                          generation, "lock_plan_owner")
    assert isinstance(writer.error, ConsentDenied) and writer.error.code == "AI_CONSENT_CHANGED"
    with rig.app.app_context():
        from app.models import TrainingPlanReplacementProposal as Proposal
        assert Proposal.query.count() == 0
        assert [(o.status, o.error_code) for o in Op.query] == [("FAILED", "AI_CONSENT_CHANGED")]
        assert len(rig.calls) == 1


def test_r07_nutrition_save_revoke_wins(rig):
    from app.services import nutrition_plan_store
    owner = rig.owners[0]
    with rig.app.app_context():
        consent(owner, rig.nutrition, "affirmative_grant", "grant-r07n")
        made = nutrition_proposals(rig, owner)["proposals"][0]
    writer = revoke_first(rig, owner, rig.nutrition, 1, lambda: nutrition_save(owner, made),
                          nutrition_plan_store, "lock_plan_owner")
    assert isinstance(writer.error, ConsentDenied) and writer.error.code == "AI_CONSENT_CHANGED"
    with rig.app.app_context():
        assert nutrition_rows(owner) == []
        regrant(owner, rig.nutrition, "r07n")  # the old token stays stale (R08)
        with pytest.raises(ConsentDenied):
            nutrition_save(owner, made)
        assert nutrition_rows(owner) == []


def test_r07_nutrition_save_wins_then_revoke(rig):
    owner = rig.owners[0]
    with rig.app.app_context():
        consent(owner, rig.nutrition, "affirmative_grant", "grant-r07m")
        made = nutrition_proposals(rig, owner)["proposals"][0]
    writer = write_first(rig, owner, rig.nutrition, 1, lambda: nutrition_save(owner, made))
    assert writer.error is None, writer.error
    with rig.app.app_context():
        assert nutrition_rows(owner) == ["Plan A"]


def test_r07_control_without_the_write_fence_a_revoked_save_lands(rig):
    """Non-vacuity: with the fence removed, the revoke-first save commits."""
    from app.services import ai_consent, nutrition_plan_store
    owner = rig.owners[0]
    with rig.app.app_context():
        consent(owner, rig.nutrition, "affirmative_grant", "grant-r07-control")
        made = nutrition_proposals(rig, owner)["proposals"][0]
    rig.monkeypatch.setattr(ai_consent, "fence", lambda authority, owner_id: None)
    writer = revoke_first(rig, owner, rig.nutrition, 1, lambda: nutrition_save(owner, made),
                          nutrition_plan_store, "lock_plan_owner")
    assert writer.error is None
    with rig.app.app_context():
        assert nutrition_rows(owner) == ["Plan A"]


# ── Nutrition generation: publication decision ─────────────────────────────

def test_nutrition_revoke_after_output_withholds_every_proposal(rig):
    from app.services import ai_consent
    owner = rig.owners[0]
    real = ai_consent.authorize_operation
    rig.monkeypatch.setattr(ai_consent, "authorize_operation", lambda authority: (
        consent(owner, rig.nutrition, "revoke", "revoke-nutrition-pub"), real(authority))[1])
    with rig.app.app_context():
        consent(owner, rig.nutrition, "affirmative_grant", "grant-nutrition-pub")
        with pytest.raises(ConsentDenied) as refused:
            nutrition_proposals(rig, owner)
        assert refused.value.code == "AI_CONSENT_CHANGED" and len(rig.calls) == 1


def test_nutrition_regrant_before_recovery_admission_is_refused(rig):
    owner = rig.owners[0]
    rig.behaviour[:] = ["timeout"]
    rig.recovery[:] = [lambda: regrant(owner, rig.nutrition, "nutrition-r02")]
    with rig.app.app_context():
        consent(owner, rig.nutrition, "affirmative_grant", "grant-nutrition-r02")
        with pytest.raises(ConsentDenied) as refused:
            nutrition_proposals(rig, owner)
        assert refused.value.code == "AI_CONSENT_CHANGED" and len(rig.calls) == 1


def test_training_grant_never_authorizes_a_nutrition_operation_or_save(rig):
    from app.services.nutrition_native import plan
    from app.services import ai_consent
    owner = rig.owners[0]
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-training-only")
        with pytest.raises(ConsentDenied):
            nutrition_proposals(rig, owner)
        assert rig.calls == []
        forged = {"plan": plan.native_document(OPTION), "proposal_token": plan.issue_proposal_token(
            SECRET, owner, OPTION, 7.0,
            ai_consent.OperationAuthority(owner, "nutrition_plan", 1, rig.nutrition.version))}
        with pytest.raises(ConsentDenied):
            nutrition_save(owner, forged)
        assert nutrition_rows(owner) == []


# ── R06 / R08: replacement lease recovery and replay ───────────────────────

def test_r06_expired_replacement_lease_keeps_its_frozen_authority(rig):
    from app.services.training_plan_replacement import generation
    from app.models import TrainingPlanReplacementGenerationOperation as Op
    from datetime import datetime, timedelta
    owner = rig.owners[0]
    rig.with_plan(owner)
    real = generation.generate_training_plan_candidate
    rig.monkeypatch.setattr(generation, "generate_training_plan_candidate",
                            lambda *a, **k: (_ for _ in ()).throw(Crash()))
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r06-lease")
        with pytest.raises(Crash):
            proposal(owner, "r06-lease")
        row = Op.query.one()
        row.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        from app.extensions import db
        db.session.commit()
        regrant(owner, rig.training, "r06-lease")
        rig.monkeypatch.setattr(generation, "generate_training_plan_candidate", real)
        with pytest.raises(ConsentDenied) as refused:
            proposal(owner, "r06-lease")
        assert refused.value.code == "AI_CONSENT_CHANGED" and rig.calls == []
        db.session.remove()
        assert [(o.status, o.error_code) for o in Op.query] == [("FAILED", "AI_CONSENT_CHANGED")]


def test_r06_legacy_lease_without_authority_never_redispatches(rig):
    from app.extensions import db
    from app.services.training_plan_replacement import generation
    from app.models import TrainingPlanReplacementGenerationOperation as Op
    from datetime import datetime, timedelta
    owner = rig.owners[0]
    rig.with_plan(owner)
    real = generation.generate_training_plan_candidate
    rig.monkeypatch.setattr(generation, "generate_training_plan_candidate",
                            lambda *a, **k: (_ for _ in ()).throw(Crash()))
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-r06-legacy")
        with pytest.raises(Crash):
            proposal(owner, "r06-legacy")
        row = Op.query.one()
        context = json.loads(row.generation_context)
        del context["consent"]  # a lease persisted before LP21
        row.generation_context = json.dumps(context)
        row.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        rig.monkeypatch.setattr(generation, "generate_training_plan_candidate", real)
        with pytest.raises(ConsentDenied) as refused:
            proposal(owner, "r06-legacy")
        assert refused.value.code == "AI_CONSENT_CHANGED" and rig.calls == []


def test_r08_replayed_proposal_after_regrant_is_not_republished(rig):
    owner = rig.owners[0]
    made, before = _proposed(rig, owner, "r08-proposal")
    with rig.app.app_context():
        replay = proposal(owner, "r08-proposal")  # same authority: replay allowed
        assert replay.replayed and replay.proposal_token == made.proposal_token
        regrant(owner, rig.training, "r08")
        with pytest.raises(ConsentDenied) as refused:
            proposal(owner, "r08-proposal")
        assert refused.value.code == "AI_CONSENT_CHANGED"
        with pytest.raises(ConsentDenied):
            confirm(owner, made.proposal_token, "confirm-key-r08")
        assert current_plan(owner) == before and len(rig.calls) == 1


# ── R09: account isolation of operation authority ──────────────────────────

def test_r09_one_accounts_authority_never_fences_another_accounts_write(rig):
    from app.extensions import db
    from app.services import ai, ai_consent
    from app.services.nutrition_plan_store import replace_nutrition_plan, UNCONDITIONAL
    a, b = rig.owners
    with rig.app.app_context():
        consent(a, rig.nutrition, "affirmative_grant", "grant-r09-a")
        consent(b, rig.nutrition, "affirmative_grant", "grant-r09-b")
        authority_a = ai_consent.begin_operation(a, "nutrition_plan", "openai", ai.OPENAI_MODEL)
        with pytest.raises(ConsentDenied):
            replace_nutrition_plan(b, OPTION, 7.0, UNCONDITIONAL, authority=authority_a)
        assert nutrition_rows(b) == []
        with ai_consent.operation_scope(authority_a):
            with pytest.raises(ConsentDenied):
                ai_consent.admission_generation(b, "nutrition_plan")
            with pytest.raises(ConsentDenied):
                ai_consent.admission_generation(a, "training_plan")
        db.session.remove()
        with pytest.raises(ConsentDenied):
            ai_consent.begin_operation(10 ** 6, "nutrition_plan", "openai", "x")


def test_r09_other_account_cannot_confirm_or_save_a_foreign_result(rig):
    from app.services.nutrition_native import errors
    from app.services.training_plan_replacement import service
    a, b = rig.owners
    made, _ = _proposed(rig, a, "r09-proposal")
    with rig.app.app_context():
        consent(a, rig.nutrition, "affirmative_grant", "grant-r09-n")
        consent(b, rig.nutrition, "affirmative_grant", "grant-r09-nb")
        consent(b, rig.training, "affirmative_grant", "grant-r09-tb")
        with pytest.raises(service.ProposalUnavailable):
            confirm(b, made.proposal_token, "confirm-key-r09")
        foreign = nutrition_proposals(rig, a)["proposals"][0]
        with pytest.raises(errors.InvalidProposal):
            nutrition_save(b, foreign)
        assert nutrition_rows(b) == []


# ── browser aliases: no server-held provenance -> no canonical write ──────

def test_provenance_less_shared_writes_are_refused_and_change_nothing(rig):
    from app.services.nutrition_plan_store import replace_nutrition_plan, UNCONDITIONAL
    from app.services.plan_replacement import PlanExpectation, replace_training_plan
    owner = rig.owners[0]
    rig.with_plan(owner)
    with rig.app.app_context():
        consent(owner, rig.training, "affirmative_grant", "grant-browser-t")
        consent(owner, rig.nutrition, "affirmative_grant", "grant-browser-n")
        before = current_plan(owner)
        with pytest.raises(ConsentDenied) as refused:
            replace_training_plan(owner, PlanExpectation(False, *before),
                                  plan_data=json.dumps(_document()), score=8, authority=None)
        assert refused.value.code == "AI_CONSENT_PATH_UNAVAILABLE"
        with pytest.raises(ConsentDenied):
            replace_nutrition_plan(owner, OPTION, 7.0, UNCONDITIONAL, authority=None)
        assert current_plan(owner) == before and nutrition_rows(owner) == []


# ── Coach boundaries (#463 handoff): tool/domain writes, conversation turns ─
# A synthetic, test-only Coach binding: the shipping BINDINGS/POLICIES stay
# closed. Each writer below is fenced only inside an ``operation_scope``; the
# Coach integration owner enters that scope with ``begin_operation``.

COACH_CATEGORIES = ("coach_messages",)


@pytest.fixture
def coach(rig):
    from types import MappingProxyType
    from app.extensions import db
    from app.models import CoachConversation
    from app.services import ai_consent
    rig.monkeypatch.setattr(ai_consent, "BINDINGS", MappingProxyType(
        dict(ai_consent.BINDINGS, coach=("coach.chat", COACH_CATEGORIES))))
    rig.policies = disclose(rig.monkeypatch)
    rig.coach_policy = rig.policies["coach"]
    rig.training = rig.policies["training_plan"]
    owner = rig.owners[0]
    rig.with_coach_plan = lambda user: _seed_coach_plan(rig, user)
    rig.with_coach_plan(owner)
    with rig.app.app_context():
        conversation = CoachConversation(user_id=owner)
        db.session.add(conversation)
        db.session.commit()
        rig.conversation_id = conversation.id
        consent(owner, rig.coach_policy, "affirmative_grant", "grant-coach")
        rig.coach_authority = ai_consent.begin_operation(
            owner, "coach", "openai", _openai_model())
    return rig


def _seed_coach_plan(rig, owner):
    """A plan the mutation domain accepts (the LP18 mutation fixture)."""
    from app.extensions import db
    from app.models import TrainingPlan
    from test_lp18_b0 import plan_text
    with rig.app.app_context():
        db.session.add(TrainingPlan(user_id=owner, plan_data=plan_text("Old"), score=8))
        db.session.commit()


def _turn(rig, authority, text="reply"):
    from app.extensions import db
    from app.models import CoachConversation
    from app.services import ai_consent, ai_pipeline
    conversation = db.session.get(CoachConversation, rig.conversation_id)
    with ai_consent.operation_scope(authority):
        return ai_pipeline._record(conversation, "question", text)


def _mutate(authority, owner, key="coach-mutation-1"):
    from app.services import ai_consent
    from app.services.plan_mutation import apply_plan_mutation
    from app.services.plan_mutation.commands import UpdateExercisePrescriptionCommand
    from app.services.plan_mutation.context import MutationContext
    with ai_consent.operation_scope(authority):
        return apply_plan_mutation(owner, UpdateExercisePrescriptionCommand(
            "Pazartesi", "Old", sets=4), MutationContext(key))


def _messages(rig):
    from app.extensions import db
    from app.models import CoachMessage
    db.session.remove()
    return CoachMessage.query.filter_by(conversation_id=rig.conversation_id).count()


def _journal(owner):
    from app.models import PlanMutationRecord
    return PlanMutationRecord.query.filter_by(user_id=owner).count()


def test_coach_turn_persists_only_under_its_original_authority(coach):
    owner = coach.owners[0]
    with coach.app.app_context():
        assert _turn(coach, coach.coach_authority) is not None and _messages(coach) == 2
        regrant(owner, coach.coach_policy, "coach-turn")
        with pytest.raises(ConsentDenied) as refused:  # never swallowed by _record
            _turn(coach, coach.coach_authority, "late reply")
        assert refused.value.code == "AI_CONSENT_CHANGED" and _messages(coach) == 2


def test_coach_turn_outside_an_operation_is_unchanged(coach):
    from app.extensions import db
    from app.models import CoachConversation
    from app.services import ai_pipeline
    owner = coach.owners[0]
    with coach.app.app_context():
        consent(owner, coach.coach_policy, "revoke", "revoke-coach-unscoped")
        conversation = db.session.get(CoachConversation, coach.conversation_id)
        assert ai_pipeline._record(conversation, "q", "first-party text") is not None
        assert _messages(coach) == 2


def test_coach_turn_revoke_wins_the_owner_lock(coach):
    from app.services import ai_consent
    owner = coach.owners[0]
    writer = revoke_first(coach, owner, coach.coach_policy, 1,
                          lambda: _turn(coach, coach.coach_authority), ai_consent, "fence")
    assert isinstance(writer.error, ConsentDenied) and writer.error.code == "AI_CONSENT_CHANGED"
    with coach.app.app_context():
        assert _messages(coach) == 0


def test_coach_turn_written_first_then_revoked(coach):
    owner = coach.owners[0]
    writer = write_first(coach, owner, coach.coach_policy, 1,
                         lambda: _turn(coach, coach.coach_authority) is not None)
    assert writer.error is None and writer.result is True
    with coach.app.app_context():
        assert _messages(coach) == 2


def test_coach_plan_tool_write_is_fenced_and_leaves_the_plan_untouched(coach):
    owner = coach.owners[0]
    with coach.app.app_context():
        before = current_plan(owner)
        regrant(owner, coach.coach_policy, "coach-tool")
        with pytest.raises(ConsentDenied):
            _mutate(coach.coach_authority, owner)
        assert current_plan(owner) == before and _journal(owner) == 0


def test_coach_plan_tool_revoke_wins_and_write_wins(coach):
    from app.services import ai_consent
    owner = coach.owners[0]
    with coach.app.app_context():
        before = current_plan(owner)
    writer = revoke_first(coach, owner, coach.coach_policy, 1,
                          lambda: _mutate(coach.coach_authority, owner), ai_consent, "fence")
    assert isinstance(writer.error, ConsentDenied)
    with coach.app.app_context():
        assert current_plan(owner) == before and _journal(owner) == 0
        consent(owner, coach.coach_policy, "affirmative_grant", "regrant-coach-tool")
        fresh = ai_consent.begin_operation(owner, "coach", "openai",
                                           _openai_model())
    writer = write_first(coach, owner, coach.coach_policy, 3,
                         lambda: _mutate(fresh, owner, "coach-mutation-2").outcome)
    assert writer.error is None and writer.result == "applied", writer.error
    with coach.app.app_context():
        assert current_plan(owner) == (before[0], before[1] + 1) and _journal(owner) == 1


def test_coach_pending_proposal_staging_is_fenced(coach):
    from app.services import ai_consent
    from app.services.plan_confirmation import create_or_reuse_pending
    from app.models import TrainingPlanConfirmationProposal
    owner = coach.owners[0]
    with coach.app.app_context():
        lineage, version = current_plan(owner)
        regrant(owner, coach.coach_policy, "coach-pending")
        with ai_consent.operation_scope(coach.coach_authority), pytest.raises(ConsentDenied):
            create_or_reuse_pending(
                owner, lineage_id=lineage, mutation_version=version,
                snapshot_fingerprint="f" * 64, command_type="update_exercise_prescription",
                command_payload={}, command_fingerprint="c" * 64, reason_codes=(),
                summary="synthetic")
        assert TrainingPlanConfirmationProposal.query.filter_by(user_id=owner).count() == 0


def test_coach_operation_never_fences_another_accounts_writes(coach):
    from app.services import ai_consent
    a, b = coach.owners
    coach.with_coach_plan(b)
    with coach.app.app_context():
        before = current_plan(b)
        with pytest.raises(ConsentDenied):
            _mutate(coach.coach_authority, b)
        assert current_plan(b) == before
        with ai_consent.operation_scope(coach.coach_authority), pytest.raises(ConsentDenied):
            ai_consent.fence_active(b)


def _openai_model():
    from app.services import ai
    return ai.OPENAI_MODEL
