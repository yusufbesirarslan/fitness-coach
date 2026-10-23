"""LP21 D1 operation fencing: pure contract pieces (no database).

Real lock/transaction behaviour is qualified only on PostgreSQL in
``tests/test_lp21_d1_operation_fencing_pg.py``.
"""
import json
from types import MappingProxyType

import pytest

from app.services import ai_consent, ai_recovery
from app.services.ai_consent import ConsentDenied, OperationAuthority


def _authority(owner=7, feature="training_plan", generation=3, version="synthetic-v"):
    return OperationAuthority(owner, feature, generation, version)


def test_shipping_policies_stay_empty_and_bindings_unchanged():
    assert isinstance(ai_consent.POLICIES, MappingProxyType) and dict(ai_consent.POLICIES) == {}
    assert dict(ai_consent.BINDINGS) == {
        "training_plan": ("training.plan", ("fitness_preferences", "body_metrics", "health_constraints")),
        "nutrition_plan": ("nutrition.plan", ("nutrition_targets", "food_preferences", "personal_free_text")),
    }


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(owner=8),
    lambda c: c.update(owner=True),
    lambda c: c.update(feature="coach"),
    lambda c: c.update(generation=0),
    lambda c: c.update(generation=True),
    lambda c: c.update(generation="3"),
    lambda c: c.update(version=""),
    lambda c: c.update(extra=1),
    lambda c: c.pop("version"),
])
def test_durable_authority_is_stale_unless_exact_and_owned(mutate):
    context = _authority().to_context()
    mutate(context)
    with pytest.raises(ConsentDenied) as refused:
        OperationAuthority.from_context(7, context)
    assert refused.value.code == "AI_CONSENT_CHANGED"


def test_durable_authority_round_trips_for_its_owner_only():
    authority = _authority()
    assert OperationAuthority.from_context(7, authority.to_context()) == authority
    for value in (None, [], "x"):
        with pytest.raises(ConsentDenied):
            OperationAuthority.from_context(7, value)


def test_candidate_envelope_keeps_exact_document_bytes():
    text = '{"b": 1,  "a": "ç"}'  # non-canonical spacing/order is preserved
    stored = ai_consent.bind_candidate(_authority(), text)
    authority, document = ai_consent.unbind_candidate(7, stored)
    assert authority == _authority() and document == text
    with pytest.raises(ValueError):
        ai_consent.bind_candidate(_authority(), "[1]")
    with pytest.raises(ConsentDenied):
        ai_consent.bind_candidate(None, text)


@pytest.mark.parametrize("stored", [
    None, "", "not json", json.dumps({"program": []}),  # legacy staged plan text
    json.dumps({"domain": "other/v1", "authority": _authority().to_context(), "document": "{}"}),
    json.dumps({"domain": "axisai/d1-operation-candidate/v1",
                "authority": _authority().to_context(), "document": {}}),
])
def test_legacy_foreign_or_corrupt_candidates_are_stale(stored):
    with pytest.raises(ConsentDenied) as refused:
        ai_consent.unbind_candidate(7, stored)
    assert refused.value.code == "AI_CONSENT_CHANGED"


def test_another_owners_candidate_is_stale():
    stored = ai_consent.bind_candidate(_authority(owner=7), "{}")
    with pytest.raises(ConsentDenied):
        ai_consent.unbind_candidate(8, stored)


def test_fence_refuses_provenance_less_and_foreign_writes_before_any_read(app):
    with app.app_context():
        with pytest.raises(ConsentDenied) as refused:
            ai_consent.fence(None, 7)
        assert refused.value.code == "AI_CONSENT_PATH_UNAVAILABLE"
        with pytest.raises(ConsentDenied) as foreign:
            ai_consent.fence(_authority(owner=7), 8)
        assert foreign.value.code == "AI_CONSENT_REQUIRED"
        with pytest.raises(ConsentDenied):
            ai_consent.fence(_authority(owner=7), None)
        with pytest.raises(ConsentDenied) as sqlite:  # authority is PostgreSQL-only
            ai_consent.fence(_authority(owner=7), 7)
        assert (sqlite.value.code, sqlite.value.status) == ("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)


def test_fence_without_application_context_is_unavailable():
    with pytest.raises(ConsentDenied) as refused:
        ai_consent.fence(_authority(), 7)
    assert refused.value.status == 503


def test_admissions_inside_an_operation_use_its_generation_only(monkeypatch):
    monkeypatch.setattr(ai_consent, "generation", lambda owner: 99)
    assert ai_consent.admission_generation(7, "training_plan") == 99
    with ai_consent.operation_scope(_authority()):
        assert ai_consent.current_operation() == _authority()
        assert ai_consent.admission_generation(7, "training_plan") == 3
        with pytest.raises(ConsentDenied):
            ai_consent.admission_generation(8, "training_plan")
        with pytest.raises(ConsentDenied) as scope:
            ai_consent.admission_generation(7, "nutrition_plan")
        assert scope.value.code == "AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE"
    assert ai_consent.current_operation() is None
    with pytest.raises(ConsentDenied):
        with ai_consent.operation_scope({"owner": 7}):
            pass


class _Redis:
    def __init__(self):
        self.store, self.calls = {}, []

    def setex(self, key, ttl, value):
        self.calls.append("setex")
        self.store[key] = value

    def get(self, key):
        self.calls.append("get")
        return self.store.get(key)


def test_last_good_cache_is_never_read_or_written_inside_an_operation(monkeypatch):
    redis = _Redis()
    monkeypatch.setattr(ai_recovery, "_get_redis", lambda: redis)
    ai_recovery.remember_last_good("k", "outside")
    assert ai_recovery.recall_last_good("k") == "outside"
    with ai_consent.operation_scope(_authority()):
        ai_recovery.remember_last_good("k", "inside")
        assert ai_recovery.recall_last_good("k") is None
    assert redis.store == {"k": "outside"} and redis.calls == ["setex", "get"]


def test_browser_plan_save_aliases_are_closed():
    from app.blueprints import ai_consent as routes
    assert {"training.save_training_plan", "nutrition.save_nutrition_plan"} <= routes._CLOSED_ENDPOINTS


def test_active_fences_are_no_ops_outside_an_operation(monkeypatch):
    calls = []
    monkeypatch.setattr(ai_consent, "fence", lambda *a: calls.append(("fence", a)))
    monkeypatch.setattr(ai_consent, "authorize_operation", lambda *a: calls.append(("auth", a)))
    assert ai_consent.fence_active(7) is None and ai_consent.authorize_active() is None
    assert calls == []
    with ai_consent.operation_scope(_authority()):
        assert ai_consent.fence_active(7) == _authority()
        assert ai_consent.authorize_active() == _authority()
    assert calls == [("fence", (_authority(), 7)), ("auth", (_authority(),))]


def test_pipeline_record_never_swallows_a_fence_refusal(app, monkeypatch):
    from types import SimpleNamespace
    from app.services import ai_pipeline, memory_manager

    def refuse(*a, **k):
        raise ConsentDenied("AI_CONSENT_CHANGED")
    with app.app_context():
        monkeypatch.setattr(memory_manager, "record_turn", refuse)
        with pytest.raises(ConsentDenied):
            ai_pipeline._record(SimpleNamespace(id=1), "q", "a")
        monkeypatch.setattr(memory_manager, "record_turn",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
        assert ai_pipeline._record(SimpleNamespace(id=1), "q", "a") is None  # unchanged


@pytest.mark.parametrize("operation", [False, True])
def test_unpersisted_coach_reply_is_publication_fenced(app, monkeypatch, operation):
    from app.services import ai_coach, ai_pipeline
    monkeypatch.setattr(ai_pipeline, "_memory_stage", lambda user_id: (None, [], None))
    monkeypatch.setattr(ai_pipeline, "_context_stage", lambda *a, **k: "")
    monkeypatch.setattr(ai_coach, "_run_coach_conversation", lambda *a, **k: "synthetic reply")
    monkeypatch.setattr(ai_consent, "authorize_operation",
                        lambda authority: (_ for _ in ()).throw(ConsentDenied("AI_CONSENT_CHANGED")))
    with app.test_request_context():
        if not operation:
            assert ai_pipeline.generate_answer(7, "question")["answer"]
            return
        with ai_consent.operation_scope(_authority()), pytest.raises(ConsentDenied):
            ai_pipeline.generate_answer(7, "question")


def test_coach_plan_tool_propagates_a_fence_refusal(app, monkeypatch):
    from app.services.coach_plan_tools import executor, grounding
    monkeypatch.setattr(executor, "plan_mutation_tools_enabled", lambda: True)
    monkeypatch.setattr(executor, "get_pending", lambda user_id: None)
    monkeypatch.setattr(grounding, "supersede_stale_clarification", lambda *a: None)
    name = sorted(executor.PLAN_MUTATION_TOOL_NAMES)[0]
    with app.test_request_context():
        monkeypatch.setattr(executor, "_execute_mutation",
                            lambda *a: (_ for _ in ()).throw(ConsentDenied("AI_CONSENT_CHANGED")))
        with pytest.raises(ConsentDenied):
            executor.execute_plan_tool(7, name, {})
        monkeypatch.setattr(executor, "_execute_mutation",
                            lambda *a: (_ for _ in ()).throw(RuntimeError("other")))
        assert executor.execute_plan_tool(7, name, {})["status"] == "error"  # unchanged
