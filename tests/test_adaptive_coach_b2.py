"""Adaptive Coach Foundation B2 — Confirm/Cancel without a Coach turn.

The native button resolves ONE named pending training-plan proposal through
the same confirm/cancel code a typed "evet" uses, under the D1 authority that
proposed it, with no model call and no chat allowance. Assertions are about
persisted state: the plan document, the journal, the proposal row and the
chat quota counter.
"""
import json

import pytest

from app.extensions import db
from app.models import PlanMutationRecord, TrainingPlan, TrainingPlanConfirmationProposal
from app.observability import assign_request_id
from app.services import ai_coach, ai_consent, coach_plan_tools
from tests.test_coach_plan_tool_characterization import _program
from tests.test_mobile_coach_api import (  # noqa: F401 -- shared native fixtures
    _chat_used, alice, bearer, bob, mobile, no_real_provider,
)

PATH = "/api/v1/coach/plan-confirmation"
REMOVE = "remove_training_plan_exercise"
REMOVE_ARGS = {"day": "Pazartesi", "exercise": "Shoulder Press"}


@pytest.fixture
def tools_on(app):
    previous = app.config.get(coach_plan_tools.FLAG_KEY, False)
    app.config[coach_plan_tools.FLAG_KEY] = True
    yield
    app.config[coach_plan_tools.FLAG_KEY] = previous


@pytest.fixture
def fences(monkeypatch):
    """Record every D1 write fence; the PG suites own the real lock/state
    semantics (test_lp21_d1_operation_fencing_pg). ``deny`` makes it refuse."""
    calls, state = [], {"deny": None}

    def fence(authority, owner, feature=None):
        calls.append((authority, owner))
        if state["deny"]:
            raise ai_consent.ConsentDenied(state["deny"])

    monkeypatch.setattr(ai_consent, "fence", fence)
    return calls, state


def _seed(user):
    db.session.add(TrainingPlan(
        user_id=user.id, score=8,
        plan_data=json.dumps(_program(), ensure_ascii=False)))
    db.session.commit()


def _stage(app, user, authority):
    """A Coach turn that stages a confirmation-required remove."""
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn()
        if authority is None:
            staged = coach_plan_tools.execute_plan_tool(user.id, REMOVE, REMOVE_ARGS)
        else:
            with ai_consent.operation_scope(authority):
                staged = coach_plan_tools.execute_plan_tool(
                    user.id, REMOVE, REMOVE_ARGS)
    assert staged["status"] == "confirmation_required"
    return _proposal(user).public_id


def _proposal(user):
    db.session.expire_all()
    return (TrainingPlanConfirmationProposal.query.filter_by(user_id=user.id)
            .order_by(TrainingPlanConfirmationProposal.id.desc()).first())


def _monday(user):
    db.session.expire_all()
    plan = TrainingPlan.query.filter_by(user_id=user.id).one()
    day = next(d for d in json.loads(plan.plan_data)["program"]
               if d["gun"] == "Pazartesi")
    return [e["isim"] for e in day["egzersizler"]]


def _journal(user):
    return PlanMutationRecord.query.filter_by(user_id=user.id).count()


def _answer(client, headers, proposal_id, decision="confirm", **body):
    return client.post(PATH, headers=headers, json=body or {
        "proposal_id": proposal_id, "decision": decision})


@pytest.fixture
def authority(alice):
    return ai_consent.OperationAuthority(alice.id, "coach", 7, "synthetic-v")


def test_confirm_applies_once_without_a_model_or_allowance(
        app, mobile, alice, bearer, tools_on, fences, authority):
    _seed(alice)
    proposal_id = _stage(app, alice, authority)
    headers = bearer(alice)
    before = _monday(alice)
    assert "Shoulder Press" in before
    calls, _ = fences
    calls.clear()

    response = _answer(mobile, headers, proposal_id)

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["outcome"] == "applied"
    assert "Shoulder Press" in body["message"]
    assert body["plan_effects"] == {"training_plan": {
        "changed": True, "pending_confirmation": False, "proposal_id": None}}
    assert _monday(alice) == [n for n in before if n != "Shoulder Press"]
    assert _journal(alice) == 1
    assert _proposal(alice).status == "applied"
    # The write ran under the authority that PROPOSED it, not a fresh one.
    assert calls and all(a == authority and o == alice.id for a, o in calls)
    assert _chat_used(alice.id) == 0  # no_real_provider: no model either

    # A retry (lost response, double tap) replays; nothing applies twice.
    again = _answer(mobile, headers, proposal_id)
    assert again.status_code == 200
    assert again.get_json()["outcome"] == "applied"
    assert _journal(alice) == 1
    assert _chat_used(alice.id) == 0


def test_cancel_leaves_the_plan_and_a_later_confirm_is_stale(
        app, mobile, alice, bearer, tools_on, fences, authority):
    _seed(alice)
    proposal_id = _stage(app, alice, authority)
    headers = bearer(alice)
    before = _monday(alice)

    response = _answer(mobile, headers, proposal_id, "cancel")
    assert response.status_code == 200
    assert response.get_json()["outcome"] == "cancelled"
    assert _proposal(alice).status == "cancelled"

    late = _answer(mobile, headers, proposal_id, "confirm")
    assert late.status_code == 409
    assert late.get_json()["error"]["code"] == "COACH_PLAN_CONFIRMATION_STALE"
    assert _monday(alice) == before and _journal(alice) == 0


def test_a_repeated_cancel_replays_and_a_cancel_after_apply_is_stale(
        app, mobile, alice, bearer, tools_on, fences, authority):
    _seed(alice)
    headers = bearer(alice)
    cancelled = _stage(app, alice, authority)
    for _ in range(2):  # a lost response retried: same truthful answer
        again = _answer(mobile, headers, cancelled, "cancel")
        assert again.status_code == 200
        assert again.get_json()["outcome"] == "cancelled"

    applied = _stage(app, alice, authority)
    assert _answer(mobile, headers, applied, "confirm").status_code == 200
    late = _answer(mobile, headers, applied, "cancel")
    # Never "cancelled, plan unchanged" for a change that was applied.
    assert late.status_code == 200
    assert late.get_json()["outcome"] == "applied"
    assert _journal(alice) == 1


def test_another_users_proposal_id_resolves_nothing(
        app, mobile, alice, bob, bearer, tools_on, fences):
    _seed(alice)
    _seed(bob)
    bobs = _stage(app, bob, ai_consent.OperationAuthority(bob.id, "coach", 3, "v"))
    headers = bearer(alice)

    for decision in ("confirm", "cancel"):
        response = _answer(mobile, headers, bobs, decision)
        assert response.status_code == 409, decision
    assert _proposal(bob).status == "pending"
    assert "Shoulder Press" in _monday(bob)


def test_a_superseded_id_never_resolves_the_current_proposal(
        app, mobile, alice, bearer, tools_on, fences, authority):
    _seed(alice)
    first = _stage(app, alice, authority)
    assert _answer(mobile, bearer(alice), first, "cancel").status_code == 200
    current = _stage(app, alice, authority)
    assert current != first

    response = _answer(mobile, bearer(alice), first, "confirm")
    assert response.status_code == 409
    # Re-cancelling the old card replays ITS cancel; the current one stays.
    again = _answer(mobile, bearer(alice), first, "cancel")
    assert again.get_json()["outcome"] == "cancelled"
    assert _proposal(alice).public_id == current
    assert _proposal(alice).status == "pending"
    assert "Shoulder Press" in _monday(alice)


def test_a_withdrawn_permission_refuses_the_write(
        app, mobile, alice, bearer, tools_on, fences, authority):
    _seed(alice)
    proposal_id = _stage(app, alice, authority)
    _, state = fences
    state["deny"] = "AI_CONSENT_REQUIRED"

    response = _answer(mobile, bearer(alice), proposal_id)

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "AI_CONSENT_REQUIRED"
    assert "Shoulder Press" in _monday(alice) and _journal(alice) == 0
    # A definitive refusal retires the proposal instead of blocking new ones.
    assert _proposal(alice).status == "stale"


def test_a_proposal_without_proposing_authority_is_never_applied(
        app, mobile, alice, bearer, tools_on, fences):
    """No stored D1 authority (staged outside an operation) -> no write. The
    button never substitutes a fresh or missing authorization."""
    _seed(alice)
    proposal_id = _stage(app, alice, None)

    response = _answer(mobile, bearer(alice), proposal_id)

    assert response.status_code == 403
    assert "Shoulder Press" in _monday(alice) and _journal(alice) == 0


def test_flag_off_is_unavailable_and_touches_nothing(
        app, mobile, alice, bearer, fences, authority):
    _seed(alice)
    app.config[coach_plan_tools.FLAG_KEY] = True
    try:
        proposal_id = _stage(app, alice, authority)
    finally:
        app.config[coach_plan_tools.FLAG_KEY] = False

    response = _answer(mobile, bearer(alice), proposal_id)

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "COACH_PLAN_CONFIRMATION_UNAVAILABLE"
    assert _proposal(alice).status == "pending"
    assert "Shoulder Press" in _monday(alice)


@pytest.mark.parametrize("body", [
    {"proposal_id": "x"},
    {"proposal_id": "x", "decision": "yes"},
    {"proposal_id": "x", "decision": "confirm", "user_id": 1},
    {"proposal_id": 7, "decision": "confirm"},
    {"proposal_id": "x" * 65, "decision": "confirm"},
    {"proposal_id": "", "decision": "cancel"},
])
def test_invalid_bodies_are_typed_400s(app, mobile, alice, bearer, tools_on, body):
    response = mobile.post(PATH, headers=bearer(alice), json=body)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "COACH_INVALID_REQUEST"


def test_unauthenticated_is_rejected(app, mobile, alice, tools_on):
    response = mobile.post(PATH, json={"proposal_id": "x", "decision": "confirm"})
    assert response.status_code == 401


def test_a_pending_turn_publishes_the_proposal_id_to_its_owner(
        app, alice, tools_on, fences, authority):
    _seed(alice)
    proposal_id = _stage(app, alice, authority)
    with app.test_request_context("/ask"):
        effects = coach_plan_tools.turn_plan_effects(alice.id)
    assert effects == {"changed": False, "pending_confirmation": True,
                       "proposal_id": proposal_id}


# ── G4: the mutation surface no longer depends on AI_ADAPTIVE_PLAN_CONTEXT ──

def test_mutation_tools_run_on_the_base_prompt(app, tools_on):
    from app.feature_flags import ROLLOUT_FLAGS
    from app.prompts.system import (
        COACH_SYSTEM_PROMPT, PLAN_MUTATION_POLICY, build_coach_system,
    )

    flag = next(f for f in ROLLOUT_FLAGS
                if f.key == "AI_COACH_PLAN_MUTATION_TOOLS_ENABLED")
    assert "AI_ADAPTIVE_PLAN_CONTEXT" not in flag.depends_on
    assert app.config.get("AI_ADAPTIVE_PLAN_CONTEXT", False) is False
    prompt = build_coach_system("en", adaptive_plan_context=False,
                                plan_mutation_tools=True)
    assert prompt.startswith(COACH_SYSTEM_PROMPT)
    assert PLAN_MUTATION_POLICY in prompt
    with app.test_request_context("/ask"):
        names = [t["function"]["name"] for t in ai_coach._openai_tools_for_call()]
    assert coach_plan_tools.READ_PLAN_TOOL not in names
    assert REMOVE in names
