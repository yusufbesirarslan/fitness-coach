"""Adaptive Coach C2 — Coach Nutrition tools over the C1 domain.

Synthetic authorities and estimator; D1 write fences are recorded here and
exercised for real in ``test_adaptive_coach_c2_nutrition_pg.py``. Assertions
are on persisted state: plan bytes/version, C1 operations, journal, shared
proposals and the chat allowance counter.
"""
import json
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import NutritionPlan, NutritionPlanEditOperation as Operation
from app.models import NutritionPlanMutationRecord as Record
from app.models import TrainingPlan, TrainingPlanConfirmationProposal as Proposal
from app.observability import assign_request_id
from app.services import ai_coach, ai_consent, coach_confirmation, coach_plan_tools
from app.services import coach_privacy, nutrition_plan_store
from app.services.ai_spend_guard import AISpendLimitExceeded
from app.services.coach_plan_tools import nutrition
from app.services.nutrition_plan_mutation import EditError, access, service
from tests.test_coach_plan_tool_characterization import _program
from tests.test_mobile_coach_api import (  # noqa: F401 -- shared native fixtures
    _chat_used, alice, bearer, bob, mobile, no_real_provider,
)

PATH = "/api/v1/coach/plan-confirmation"
APPROVED = "synthetic-coach-edit-r3"
DOCUMENT = {"isim": "Synthetic", "kahvalti": {
    "yemekler": ["Eggs - 2 adet", "Bread - 60 g"],
    "kalori": 400, "protein": 20, "karb": 35, "yag": 15},
    "aksam": {"yemekler": ["Rice - 100 g"], "kalori": 200,
              "protein": 5, "karb": 40, "yag": 2},
    "toplam_kalori": 600, "toplam_protein": 25, "toplam_karb": 75, "toplam_yag": 17}
ESTIMATE = {"kalori": 450, "protein": 32, "karb": 38, "yag": 19}
REPLACE = {"meal": "kahvalti", "item": "Eggs - 2 adet", "replacement": "Chicken - 100 g"}


@pytest.fixture
def c2(app, alice, monkeypatch):
    """Gate open (synthetic approved disclosure), flag ON, grants present."""
    raw = json.dumps(DOCUMENT, indent=2)
    db.session.add(NutritionPlan(user_id=alice.id, plan_data=raw))
    db.session.commit()
    previous = app.config.get(coach_plan_tools.FLAG_KEY, False)
    app.config[coach_plan_tools.FLAG_KEY] = True
    monkeypatch.setattr(access, "NUTRITION_EDITS_ENABLED", True)
    monkeypatch.setattr(access, "APPROVED_COACH_EDIT_DISCLOSURES", frozenset({APPROVED}))
    state = {"calls": [], "fences": [], "published": [], "grant": True,
             "deny_fence": None, "estimate": dict(ESTIMATE)}

    def capture(owner, feature, recipient, model):
        assert feature == "nutrition_plan"
        if not state["grant"]:
            raise ai_consent.ConsentDenied("AI_CONSENT_REQUIRED")
        return ai_consent.OperationAuthority(owner, "nutrition_plan", 3, "synthetic-nutrition")

    def fence(authority, owner, feature=None):
        assert authority.owner == owner
        state["fences"].append(feature)
        if state["deny_fence"]:
            raise ai_consent.ConsentDenied(state["deny_fence"])

    def authorize(authority):
        state["published"].append(authority.feature)
        if authority.feature == "nutrition_plan" and not state["grant"]:
            raise ai_consent.ConsentDenied("AI_CONSENT_REQUIRED")

    def estimate(items):
        assert not db.session().in_transaction(), "provider I/O must not hold a transaction"
        assert ai_consent.current_operation().feature == "nutrition_plan"
        state["calls"].append(tuple(items))
        value = state["estimate"]
        if isinstance(value, Exception):
            raise value
        return dict(value)

    monkeypatch.setattr(ai_consent, "begin_independent_operation", capture)
    monkeypatch.setattr(ai_consent, "fence", fence)
    monkeypatch.setattr(ai_consent, "authorize_operation", authorize)
    monkeypatch.setattr(service.estimation, "estimate_meal", estimate)
    state.update(app=app, owner=alice.id, user=alice, raw=raw,
                 coach=ai_consent.OperationAuthority(alice.id, "coach", 3, APPROVED))
    yield state
    app.config[coach_plan_tools.FLAG_KEY] = previous


class Turn:
    """One synthetic Coach turn under the turn's own Coach D1 operation."""

    def __init__(self, rig, message="", authority=None):
        self.rig, self.message = rig, message
        self.authority = authority or rig["coach"]

    def __enter__(self):
        self.ctx = self.rig["app"].test_request_context("/ask", method="POST")
        self.ctx.push()
        assign_request_id()
        self.scope = ai_consent.operation_scope(self.authority)
        self.scope.__enter__()
        ai_coach._begin_coach_turn(self.message, user_id=self.rig["owner"])
        return self

    def __exit__(self, *exc):
        self.scope.__exit__(*exc)
        self.ctx.pop()

    def call(self, name, args=None):
        raw = ai_coach._dispatch_coach_tool(self.rig["owner"], name, json.dumps(args or {}))
        return json.loads(raw), raw

    def names(self):
        return [t["name"] for t in ai_coach._coach_tool_defs_for_call(self.rig["owner"])]


def _plan(owner):
    db.session.expire_all()
    row = nutrition_plan_store.newest_plan(owner)
    return row.plan_data, row.mutation_version


def _proposal(owner):
    db.session.expire_all()
    return Proposal.query.filter_by(user_id=owner).order_by(Proposal.id.desc()).first()


def _stage(rig, args=REPLACE, tool=nutrition.REPLACE_TOOL):
    with Turn(rig, "please change it") as turn:
        result, _ = turn.call(tool, args)
    assert result["status"] == "confirmation_required", result
    return _proposal(rig["owner"]).public_id


def _answer(client, headers, proposal_id, decision="confirm"):
    return client.post(PATH, headers=headers, json={"proposal_id": proposal_id, "decision": decision})


# ── Gates ─────────────────────────────────────────────────────────────────────

def test_production_default_publishes_and_executes_nothing(app, alice, monkeypatch):
    """Flag ON alone (the reported production state) never opens Nutrition."""
    db.session.add(NutritionPlan(user_id=alice.id, plan_data=json.dumps(DOCUMENT)))
    db.session.commit()
    assert access.NUTRITION_EDITS_ENABLED is False and not access.APPROVED_COACH_EDIT_DISCLOSURES
    app.config[coach_plan_tools.FLAG_KEY] = True
    captured = []
    monkeypatch.setattr(ai_consent, "begin_independent_operation",
                        lambda *a: captured.append(a))
    monkeypatch.setattr(ai_consent, "authorize_operation", lambda authority: None)
    try:
        rig = {"app": app, "owner": alice.id,
               "coach": ai_consent.OperationAuthority(alice.id, "coach", 1, "r2")}
        with Turn(rig) as turn:
            assert not set(turn.names()) & nutrition.TOOL_NAMES
            for name in nutrition.TOOL_NAMES:
                result, _ = turn.call(name, REPLACE if name == nutrition.REPLACE_TOOL else {})
                assert result["error"] == "CAPABILITY_DISABLED"
            effects = coach_plan_tools.turn_plan_effects(alice.id)
        assert "nutrition_plan" not in effects
        assert not captured and Operation.query.count() == 0
    finally:
        app.config[coach_plan_tools.FLAG_KEY] = False


def test_flag_off_hides_nutrition_even_with_source_gate_open(c2):
    c2["app"].config[coach_plan_tools.FLAG_KEY] = False
    with Turn(c2) as turn:
        assert not set(turn.names()) & nutrition.TOOL_NAMES
        result, _ = turn.call(nutrition.READ_TOOL)
    assert result["error"] in ("CAPABILITY_DISABLED", "INVALID_ARGUMENTS") or result["status"] == "unavailable"
    assert not c2["calls"] and "nutrition_plan" not in c2["published"]


def test_missing_nutrition_grant_keeps_ordinary_chat_and_withholds_content(c2):
    c2["grant"] = False
    with Turn(c2) as turn:
        names = turn.names()
        assert "query_fitx_metrics" in names and "get_current_training_plan" in names
        assert not set(names) & nutrition.TOOL_NAMES
        result, raw = turn.call(nutrition.READ_TOOL)
    assert result["error"] == "PERMISSION_UNAVAILABLE"
    assert "Eggs" not in raw and "meals" not in result


def test_old_coach_disclosure_is_not_sufficient(c2):
    old = ai_consent.OperationAuthority(c2["owner"], "coach", 3, "d1-closure02-coach-candidate-r2")
    with Turn(c2, authority=old) as turn:
        assert not set(turn.names()) & nutrition.TOOL_NAMES
        result, _ = turn.call(nutrition.REPLACE_TOOL, REPLACE)
    assert result["error"] == "PERMISSION_UNAVAILABLE"
    assert not c2["calls"] and Operation.query.count() == 0


def test_revoked_nutrition_permission_at_read_withholds_plan(c2, monkeypatch):
    with Turn(c2) as turn:
        assert nutrition.READ_TOOL in turn.names()
        c2["grant"] = False  # revoked after tools were published this turn
        result, raw = turn.call(nutrition.READ_TOOL)
    assert result["error"] == "PERMISSION_UNAVAILABLE" and "Eggs" not in raw


def test_independent_capture_never_switches_the_active_coach_operation(app, monkeypatch):
    coach = ai_consent.OperationAuthority(1, "coach", 3, APPROVED)
    seen = []

    def begin(owner, feature, recipient, model):
        seen.append(ai_consent.current_operation())
        return ai_consent.OperationAuthority(owner, feature, 3, "n")

    monkeypatch.setattr(ai_consent, "begin_operation", begin)
    with app.test_request_context("/ask"):
        with ai_consent.operation_scope(coach):
            got = ai_consent.begin_independent_operation(1, "nutrition_plan", "bedrock", "m")
            assert ai_consent.current_operation() == coach
    assert seen == [None] and got.feature == "nutrition_plan"


# ── Read ──────────────────────────────────────────────────────────────────────

def test_read_projects_the_authoritative_plan_without_identity(c2):
    with Turn(c2) as turn:
        result, raw = turn.call(nutrition.READ_TOOL)
        refused, _ = turn.call(nutrition.READ_TOOL, {"user_id": 2})
    assert result["status"] == "ok"
    assert [m["meal"] for m in result["meals"]] == ["kahvalti", "aksam"]
    assert result["meals"][0]["items"] == ["Eggs - 2 adet", "Bread - 60 g"]
    assert result["totals"]["toplam_kalori"] == 600
    projected = json.loads(coach_privacy.tool_result(nutrition.READ_TOOL, raw))
    assert projected["meals"][0]["items"] == ["Eggs - 2 adet", "Bread - 60 g"]
    for secret in ("lineage", "version", "proposal", "operation_id", str(c2["owner"]) + ","):
        assert secret not in json.dumps(projected)
    assert refused["error"] == "INVALID_ARGUMENTS"
    assert "nutrition_plan" in c2["published"]
    assert _plan(c2["owner"]) == (c2["raw"], 0)


def test_privacy_projection_drops_unknown_fields_and_control_text(c2):
    hostile = json.dumps({"domain": "nutrition_plan", "status": "ok", "secret": "x",
                          "meals": [{"meal": "kahvalti", "items": ["ok - 1 g", "bad\u0000"],
                                     "kalori": "999", "lineage_id": "L"}],
                          "error": "SQL: SELECT"})
    out = json.loads(coach_privacy.tool_result(nutrition.READ_TOOL, hostile))
    assert out["meals"] == [{"items": ["ok - 1 g"], "meal": "kahvalti"}]
    assert "secret" not in out and "error" not in out


# ── Proposal ──────────────────────────────────────────────────────────────────

def test_replace_prepares_a_quote_and_changes_nothing(c2):
    with Turn(c2, "swap my eggs for chicken") as turn:
        result, raw = turn.call(nutrition.REPLACE_TOOL, REPLACE)
        assert not set(turn.names()) & nutrition.WRITE_TOOL_NAMES  # pending → read only
        reply = coach_confirmation.reply_after_tools(c2["owner"], "en", [result])
        effects = coach_plan_tools.turn_plan_effects(c2["owner"], "en")
        assert coach_plan_tools.proposal_created_this_turn()
        assert not coach_plan_tools.plan_changed_this_turn()
    assert result["before"]["kalori"] == 400 and result["after"]["kalori"] == 450
    assert result["after"]["estimated"] is True
    assert result["day_totals"] == {
        "before": {"toplam_kalori": 600, "toplam_protein": 25, "toplam_karb": 75, "toplam_yag": 17},
        "after": {"toplam_kalori": 650, "toplam_protein": 37, "toplam_karb": 78, "toplam_yag": 21}}
    assert result["estimate"]["whole_meal_reestimated"] is True
    projected = json.loads(coach_privacy.tool_result(nutrition.REPLACE_TOOL, raw))
    assert projected["change"]["replacement"] == "Chicken - 100 g"
    assert "NOTHING has been saved" in projected["message"]
    assert "Confirm this change?" in reply and "re-estimated" in reply
    assert "Chicken - 100 g" in reply and "650 kcal" in reply
    assert effects["pending_confirmation"] is False and effects["proposal_id"] is None
    n = effects["nutrition_plan"]
    assert n["pending_confirmation"] is True and n["proposal_id"] == _proposal(c2["owner"]).public_id
    assert n["proposal"]["after"]["nutrition"]["energy_kcal"] == 450
    assert n["proposal"]["totals"]["after"]["energy_kcal"] == 650
    assert "estimates" in n["proposal"]["estimate"]["notice"]
    assert c2["calls"] == [("Chicken - 100 g", "Bread - 60 g")]
    assert _plan(c2["owner"]) == (c2["raw"], 0) and Record.query.count() == 0


@pytest.mark.parametrize("args,code", [
    ({"meal": "kahvalti", "item": "Egg", "replacement": "Tofu - 100 g"}, "ITEM_NOT_FOUND"),
    ({"meal": "ogle", "item": "Eggs - 2 adet", "replacement": "Tofu - 100 g"}, "MEAL_NOT_FOUND"),
    ({"meal": "kahvalti", "item": "Eggs - 2 adet", "replacement": "Tofu"}, "QUANTITY_REQUIRED"),
    ({"meal": "kahvalti", "item": "Eggs - 2 adet", "replacement": "Tofu - a bowl"}, "QUANTITY_REQUIRED"),
    ({"meal": "kahvalti", "item": "Eggs - 2 adet", "replacement": "Tofu - 0 g"}, "QUANTITY_REQUIRED"),
    ({"meal": "kahvalti", "item": "Eggs - 2 adet", "replacement": "Bread - 60 g"}, "ITEM_ALREADY_PRESENT"),
    ({"meal": "breakfast", "item": "Eggs - 2 adet", "replacement": "Tofu - 100 g"}, "INVALID_ARGUMENTS"),
    ({"meal": "kahvalti", "item": "Eggs - 2 adet"}, "INVALID_ARGUMENTS"),
    ({"meal": "kahvalti", "item": "Eggs - 2 adet", "replacement": "Tofu - 1 g", "plan_id": 1}, "INVALID_ARGUMENTS"),
    ({"meal": "kahvalti", "item": "Eggs - 2 adet", "replacement": 7}, "INVALID_ARGUMENTS"),
])
def test_ungrounded_requests_refuse_before_any_claim_or_estimate(c2, args, code):
    with Turn(c2) as turn:
        result, _ = turn.call(nutrition.REPLACE_TOOL, args)
    assert result["error"] == code
    assert not c2["calls"] and Operation.query.count() == 0 and Proposal.query.count() == 0
    assert _plan(c2["owner"]) == (c2["raw"], 0)


def test_ambiguous_duplicate_item_and_unquantified_legacy_meal_refuse(c2):
    doc = dict(DOCUMENT, aksam={"yemekler": ["Rice - 100 g", "rice - 100 g"],
                                "kalori": 200, "protein": 5, "karb": 40, "yag": 2})
    row = nutrition_plan_store.newest_plan(c2["owner"])
    row.plan_data = json.dumps(doc)
    db.session.commit()
    with Turn(c2) as turn:
        ambiguous, _ = turn.call(nutrition.REMOVE_TOOL, {"meal": "aksam", "item": "Rice - 100 g"})
    assert ambiguous["status"] == "needs_user_input" and ambiguous["error"] == "AMBIGUOUS_ITEM"
    doc = dict(DOCUMENT, aksam={"yemekler": ["Rice", "Salad - 50 g"], "kalori": 200,
                                "protein": 5, "karb": 40, "yag": 2})
    row = nutrition_plan_store.newest_plan(c2["owner"])
    row.plan_data = json.dumps(doc)
    db.session.commit()
    with Turn(c2) as turn:
        legacy, _ = turn.call(nutrition.REMOVE_TOOL, {"meal": "aksam", "item": "Salad - 50 g"})
    assert legacy["error"] == "QUANTITY_REQUIRED" and legacy["status"] == "needs_user_input"
    assert not c2["calls"] and Operation.query.count() == 0


@pytest.mark.parametrize("failure", [
    EditError("estimate_unavailable"), RuntimeError("provider down"),
    AISpendLimitExceeded("user", "heavy")])
def test_estimation_failure_writes_nothing_and_never_invents_values(c2, failure):
    c2["estimate"] = failure
    with Turn(c2) as turn:
        result, raw = turn.call(nutrition.REPLACE_TOOL, REPLACE)
        assert not coach_plan_tools.proposal_created_this_turn()
    assert result["error"] == "ESTIMATE_UNAVAILABLE"
    assert "kalori" not in raw and "after" not in result
    assert Proposal.query.count() == 0 and Record.query.count() == 0
    assert Operation.query.one().status == "failed"
    assert _plan(c2["owner"]) == (c2["raw"], 0)
    c2["estimate"] = dict(ESTIMATE)  # a failed claim never blocks a new request
    _stage(c2)


def test_incomplete_estimate_shape_is_refused(c2):
    c2["estimate"] = {"kalori": 450, "protein": 32, "karb": 38}
    with Turn(c2) as turn:
        result, _ = turn.call(nutrition.REPLACE_TOOL, REPLACE)
    assert result["error"] == "ESTIMATE_UNAVAILABLE" and Proposal.query.count() == 0


def test_same_turn_redelivery_replays_without_a_second_estimate(c2):
    with Turn(c2) as turn:
        first, _ = turn.call(nutrition.REPLACE_TOOL, REPLACE)
        second, _ = turn.call(nutrition.REPLACE_TOOL, REPLACE)
        other, _ = turn.call(nutrition.REMOVE_TOOL, {"meal": "kahvalti", "item": "Bread - 60 g"})
    assert first["status"] == second["status"] == "confirmation_required"
    assert other["error"] == "PENDING_CONFIRMATION_EXISTS"
    assert len(c2["calls"]) == 1 and Proposal.query.count() == 1


def test_noop_replacement_dispatches_nothing(c2):
    with Turn(c2) as turn:
        result, _ = turn.call(nutrition.REPLACE_TOOL, dict(REPLACE, replacement="Eggs - 2 adet"))
    assert result["status"] == "no_op" and not c2["calls"] and Proposal.query.count() == 0


def test_interrupted_estimator_claim_is_released_not_retried(c2):
    with Turn(c2) as turn:
        turn.call(nutrition.READ_TOOL)
    db.session.add(Operation(
        user_id=c2["owner"], idempotency_key="aic:n:interrupted:0001",
        command_fingerprint="f" * 64, command_payload={"edit": {}},
        plan_lineage_id="L" * 20, base_version=0, before_snapshot=c2["raw"],
        status="estimating", created_at=datetime.utcnow() - timedelta(hours=1)))
    db.session.commit()
    _stage(c2)
    statuses = sorted(op.status for op in Operation.query.all())
    assert statuses == ["cancelled", "prepared"] and len(c2["calls"]) == 1


def test_recent_in_flight_claim_blocks_instead_of_double_dispatch(c2):
    db.session.add(Operation(
        user_id=c2["owner"], idempotency_key="aic:n:inflight:0001",
        command_fingerprint="f" * 64, command_payload={"edit": {}},
        plan_lineage_id="L" * 20, base_version=0, before_snapshot=c2["raw"],
        status="estimating"))
    db.session.commit()
    with Turn(c2) as turn:
        result, _ = turn.call(nutrition.REPLACE_TOOL, REPLACE)
    assert result["error"] == "PREPARATION_IN_PROGRESS" and not c2["calls"]


# ── Deterministic Confirm/Cancel (B2 endpoint) ───────────────────────────────

def test_button_confirm_applies_once_without_model_or_allowance(c2, mobile, bearer, alice):
    proposal_id = _stage(c2)
    headers = bearer(alice)
    used = _chat_used(alice.id)
    response = _answer(mobile, headers, proposal_id)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["outcome"] == "applied" and "Chicken - 100 g" in body["message"]
    assert body["plan_effects"]["training_plan"]["changed"] is False
    assert body["plan_effects"]["nutrition_plan"]["changed"] is True
    assert body["plan_effects"]["nutrition_plan"]["pending_confirmation"] is False
    raw, version = _plan(alice.id)
    doc = json.loads(raw)
    assert version == 1 and doc["kahvalti"]["tahmini"] is True
    assert doc["kahvalti"]["yemekler"] == ["Chicken - 100 g", "Bread - 60 g"]
    assert doc["aksam"] == DOCUMENT["aksam"] and doc["toplam_kalori"] == 650
    assert _chat_used(alice.id) == used and len(c2["calls"]) == 1
    assert {"coach", "nutrition_plan"} <= set(c2["fences"])
    again = _answer(mobile, headers, proposal_id)  # repeated tap
    assert again.status_code == 200 and again.get_json()["outcome"] == "applied"
    late_cancel = _answer(mobile, headers, proposal_id, "cancel")
    assert late_cancel.status_code == 409
    assert _plan(alice.id)[1] == 1 and Record.query.count() == 1 and len(c2["calls"]) == 1


def test_button_cancel_never_applies_and_replays(c2, mobile, bearer, alice):
    proposal_id = _stage(c2)
    headers = bearer(alice)
    assert _answer(mobile, headers, proposal_id, "cancel").get_json()["outcome"] == "cancelled"
    assert _answer(mobile, headers, proposal_id, "cancel").get_json()["outcome"] == "cancelled"
    assert _answer(mobile, headers, proposal_id).status_code == 409
    assert _plan(alice.id) == (c2["raw"], 0) and Record.query.count() == 0


def test_foreign_and_tampered_ids_resolve_nothing(c2, mobile, bearer, alice, bob):
    proposal_id = _stage(c2)
    for user, pid in ((bob, proposal_id), (alice, proposal_id[:-2] + "zz"), (alice, "x" * 70)):
        response = _answer(mobile, bearer(user), pid)
        assert response.status_code in (400, 409), response.get_json()
    assert _proposal(alice.id).status == "pending" and _plan(alice.id) == (c2["raw"], 0)


def test_stale_plan_rejects_confirmation(c2, mobile, bearer, alice):
    proposal_id = _stage(c2)
    row = nutrition_plan_store.newest_plan(alice.id)
    row.plan_data = json.dumps(dict(DOCUMENT, isim="Changed elsewhere"))
    db.session.commit()
    response = _answer(mobile, bearer(alice), proposal_id)
    assert response.status_code == 409
    assert json.loads(_plan(alice.id)[0])["isim"] == "Changed elsewhere"
    assert Record.query.count() == 0 and _proposal(alice.id).status == "stale"


def test_revoked_authority_at_confirm_writes_nothing(c2, mobile, bearer, alice):
    proposal_id = _stage(c2)
    c2["deny_fence"] = "AI_CONSENT_REQUIRED"
    response = _answer(mobile, bearer(alice), proposal_id)
    assert response.status_code == 403
    assert _plan(alice.id) == (c2["raw"], 0) and Record.query.count() == 0
    assert _proposal(alice.id).status == "stale"  # a regrant can never revive it
    c2["deny_fence"] = None
    assert _answer(mobile, bearer(alice), proposal_id).status_code == 409


def test_typed_yes_confirms_before_any_model_call(c2):
    _stage(c2)
    with Turn(c2, "yes") as turn:
        reply = coach_confirmation.resolve_pending_turn(c2["owner"], "en")
        assert coach_plan_tools.plan_changed_this_turn()
        effects = coach_plan_tools.turn_plan_effects(c2["owner"])
    assert "updated" in reply and _plan(c2["owner"])[1] == 1
    assert effects["changed"] is False
    assert effects["nutrition_plan"]["changed"] is True


def test_typed_yes_after_revocation_reports_honestly(c2):
    _stage(c2)
    c2["deny_fence"] = "AI_CONSENT_REQUIRED"
    with Turn(c2, "yes"):
        reply = coach_confirmation.resolve_pending_turn(c2["owner"], "en")
    assert "no longer valid" in reply and _plan(c2["owner"]) == (c2["raw"], 0)


# ── Undo ──────────────────────────────────────────────────────────────────────

def test_undo_restores_exact_bytes_without_estimation(c2, mobile, bearer, alice):
    _answer(mobile, bearer(alice), _stage(c2))
    with Turn(c2, "undo that") as turn:
        result, _ = turn.call(nutrition.UNDO_TOOL)
        again, _ = turn.call(nutrition.UNDO_TOOL)
        reply = coach_confirmation.reply_after_tools(c2["owner"], "en", [result])
        refused, _ = turn.call(nutrition.UNDO_TOOL, {"which": "last"})
    assert result["status"] == "applied" and again["status"] == "replayed"
    assert "restored exactly" in reply and refused["error"] == "INVALID_ARGUMENTS"
    assert _plan(alice.id) == (c2["raw"], 2) and len(c2["calls"]) == 1
    with Turn(c2, "undo") as turn:
        none_left, _ = turn.call(nutrition.UNDO_TOOL)
    assert none_left["error"] == "UNDO_UNAVAILABLE"


def test_undo_contract_explicit_single_step_no_proposal_no_estimate(c2, mobile, bearer, alice):
    # Founder decision: explicit Undo applies without a second confirmation,
    # never on a yes/no answer, never over a pending quote, no estimator call.
    _answer(mobile, bearer(alice), _stage(c2))
    applied = _plan(alice.id)
    proposals = Proposal.query.filter_by(user_id=alice.id).count()
    for answer in ("yes", "no", "evet", "hayır"):
        with Turn(c2, answer) as turn:
            refused, _ = turn.call(nutrition.UNDO_TOOL)
        assert refused["error"] == "NO_PENDING_CONFIRMATION"
    assert _plan(alice.id) == applied
    _stage(c2, {"meal": "aksam", "item": "Salad - 80 g"}, nutrition.ADD_TOOL)
    with Turn(c2, "undo my last change") as turn:
        blocked, _ = turn.call(nutrition.UNDO_TOOL)
    assert blocked["error"] == "PENDING_CONFIRMATION_EXISTS" and _plan(alice.id) == applied
    _answer(mobile, bearer(alice), _proposal(alice.id).public_id, "cancel")
    calls = len(c2["calls"])
    with Turn(c2, "undo my last change") as turn:
        done, _ = turn.call(nutrition.UNDO_TOOL)
    assert done["status"] == "applied" and _plan(alice.id) == (c2["raw"], 2)
    assert len(c2["calls"]) == calls  # no estimate
    assert Proposal.query.filter_by(user_id=alice.id).count() == proposals + 1  # only the cancelled add


def test_add_and_remove_round_trip(c2, mobile, bearer, alice):
    _answer(mobile, bearer(alice), _stage(
        c2, {"meal": "aksam", "item": "Salad - 80 g"}, nutrition.ADD_TOOL))
    assert json.loads(_plan(alice.id)[0])["aksam"]["yemekler"] == ["Rice - 100 g", "Salad - 80 g"]
    _answer(mobile, bearer(alice), _stage(
        c2, {"meal": "aksam", "item": "Rice - 100 g"}, nutrition.REMOVE_TOOL))
    assert json.loads(_plan(alice.id)[0])["aksam"]["yemekler"] == ["Salad - 80 g"]
    with Turn(c2) as turn:
        last, _ = turn.call(nutrition.REMOVE_TOOL, {"meal": "aksam", "item": "Salad - 80 g"})
    assert last["error"] == "EMPTY_MEAL"


# ── Training / Nutrition isolation ───────────────────────────────────────────

def _seed_training(user):
    db.session.add(TrainingPlan(user_id=user.id, score=8,
                                plan_data=json.dumps(_program(), ensure_ascii=False)))
    db.session.commit()


def test_nutrition_pending_blocks_training_proposals_and_vice_versa(c2, alice):
    _seed_training(alice)
    _stage(c2)
    with Turn(c2) as turn:
        assert not set(turn.names()) & set(coach_plan_tools.PLAN_MUTATION_TOOL_NAMES)
        training = coach_plan_tools.execute_plan_tool(
            alice.id, "remove_training_plan_exercise",
            {"day": "Pazartesi", "exercise": "Shoulder Press"})
        effects = coach_plan_tools.turn_plan_effects(alice.id)
    assert training["error"] == "PENDING_CONFIRMATION_EXISTS"
    assert effects["proposal_id"] is None
    service.resolve_edit(alice.id, _proposal(alice.id).public_id, "cancel")

    with Turn(c2) as turn:
        staged = coach_plan_tools.execute_plan_tool(
            alice.id, "remove_training_plan_exercise",
            {"day": "Pazartesi", "exercise": "Shoulder Press"})
    assert staged["status"] == "confirmation_required"
    with Turn(c2) as turn:
        assert not set(turn.names()) & nutrition.WRITE_TOOL_NAMES
        blocked, _ = turn.call(nutrition.REPLACE_TOOL, REPLACE)
        effects = coach_plan_tools.turn_plan_effects(alice.id)
    assert blocked["error"] == "PENDING_CONFIRMATION_EXISTS" and len(c2["calls"]) == 1
    assert effects["nutrition_plan"]["pending_confirmation"] is False
    assert effects["pending_confirmation"] is True


def test_training_button_still_resolves_training_proposals(c2, alice, mobile, bearer):
    _seed_training(alice)
    with Turn(c2) as turn:
        coach_plan_tools.execute_plan_tool(
            alice.id, "remove_training_plan_exercise",
            {"day": "Pazartesi", "exercise": "Shoulder Press"})
    response = _answer(mobile, bearer(alice), _proposal(alice.id).public_id)
    assert response.status_code == 200 and response.get_json()["outcome"] == "applied"
    assert response.get_json()["plan_effects"]["training_plan"]["changed"] is True
    assert response.get_json()["plan_effects"]["nutrition_plan"]["changed"] is False


# ── Privacy erasure ──────────────────────────────────────────────────────────

def test_account_purge_erases_c2_quotes_history_and_proposals(c2, mobile, bearer, alice):
    from app.cli import _purge_user
    _answer(mobile, bearer(alice), _stage(c2))
    _stage(c2, {"meal": "aksam", "item": "Salad - 80 g"}, nutrition.ADD_TOOL)
    assert Operation.query.count() == 2 and Record.query.count() == 1
    _purge_user(db.session.get(type(alice), alice.id))
    db.session.commit()
    assert Operation.query.count() == Record.query.count() == Proposal.query.count() == 0


# ── Shape guards ─────────────────────────────────────────────────────────────

SERVER_OWNED = {"user", "user_id", "owner", "plan_id", "lineage", "lineage_id",
                "version", "proposal", "proposal_id", "key", "idempotency_key",
                "operation_id", "nutrients", "kalori", "calories", "estimate", "actor"}


def test_tool_schemas_carry_bounded_intent_only():
    defs = (nutrition.READ_TOOL_DEF,) + nutrition.EDIT_TOOL_DEFS + (nutrition.UNDO_TOOL_DEF,)
    assert {d["name"] for d in defs} == nutrition.TOOL_NAMES
    assert not nutrition.TOOL_NAMES & set(coach_plan_tools.PLAN_WRITE_TOOL_NAMES)
    for definition in defs:
        params = definition["parameters"]
        assert params["additionalProperties"] is False
        assert not set(params["properties"]) & SERVER_OWNED
        assert set(params.get("required", ())) == set(params["properties"])


def test_bridge_module_has_no_storage_query_or_logging():
    import ast
    from pathlib import Path
    path = Path("app/services/coach_plan_tools/nutrition.py")
    source = path.read_text(encoding="utf-8")
    assert ".query" not in source and "app.models" not in source
    for node in ast.walk(ast.parse(source)):
        name = getattr(node, "attr", None) or getattr(node, "id", None)
        assert name not in {"logger", "print", "current_app"}, node.lineno


# ── Review fixes (independent review of #542) ────────────────────────────────

def test_typed_confirm_copy_is_display_only_never_memory(c2):
    from app.services.coach_memory_provenance import DisplayOnlyReply
    _stage(c2)
    with Turn(c2, "yes"):
        reply = coach_confirmation.resolve_pending_turn(c2["owner"], "en")
    assert isinstance(reply, DisplayOnlyReply) and "Chicken - 100 g" in reply


@pytest.mark.parametrize("args", [
    {"meal": "kahvalti", "item": "Eggs - 2 adet", "replacement": "Tofu"},
    {"meal": "kahvalti", "item": "Egg", "replacement": "Tofu - 100 g"},
    {"meal": "ogle", "item": "Eggs - 2 adet", "replacement": "Tofu - 100 g"}])
def test_nutrition_clarifications_never_get_training_copy(c2, args):
    with Turn(c2) as turn:
        result, _ = turn.call(nutrition.REPLACE_TOOL, args)
        owned = coach_confirmation.reply_after_tools(c2["owner"], "en", [result])
    assert result["status"] == "needs_user_input" and owned is None


def test_yes_no_turn_never_starts_a_nutrition_change(c2):
    with Turn(c2, "yes") as turn:
        assert not set(turn.names()) & nutrition.WRITE_TOOL_NAMES
        result, _ = turn.call(nutrition.REPLACE_TOOL, REPLACE)
    assert result["error"] == "NO_PENDING_CONFIRMATION" and not c2["calls"]


def test_authority_capture_fault_fails_closed_and_keeps_chat(c2, monkeypatch):
    def broken(*_):
        raise RuntimeError("launch table unavailable")
    monkeypatch.setattr(ai_consent, "begin_independent_operation", broken)
    with Turn(c2) as turn:
        names = turn.names()
        result, _ = turn.call(nutrition.READ_TOOL)
    assert "query_fitx_metrics" in names and not set(names) & nutrition.TOOL_NAMES
    assert result["error"] == "PERMISSION_UNAVAILABLE"


def test_closed_gate_never_resolves_but_releases_a_stranded_quote(c2, mobile, bearer, alice, monkeypatch):
    proposal_id = _stage(c2)
    monkeypatch.setattr(access, "NUTRITION_EDITS_ENABLED", False)  # gate closed later
    headers = bearer(alice)
    confirm = _answer(mobile, headers, proposal_id)
    assert confirm.status_code == 409
    assert _proposal(alice.id).status == "pending" and _plan(alice.id) == (c2["raw"], 0)
    with Turn(c2, "How should I train today?"):
        assert coach_confirmation.resolve_pending_turn(alice.id, "en") is None  # chat not blocked
    cancel = _answer(mobile, headers, proposal_id, "cancel")
    assert cancel.status_code == 200 and cancel.get_json()["outcome"] == "cancelled"
    assert "nutrition_plan" not in cancel.get_json()["plan_effects"]
    assert _proposal(alice.id).status == "cancelled" and _plan(alice.id) == (c2["raw"], 0)
    assert Record.query.count() == 0
    _seed_training(alice)
    with Turn(c2):
        staged = coach_plan_tools.execute_plan_tool(
            alice.id, "remove_training_plan_exercise",
            {"day": "Pazartesi", "exercise": "Shoulder Press"})
    assert staged["status"] == "confirmation_required"  # shared slot freed


def test_closed_gate_typed_cancel_releases_a_stranded_quote(c2, monkeypatch):
    _stage(c2)
    monkeypatch.setattr(access, "APPROVED_COACH_EDIT_DISCLOSURES", frozenset())
    with Turn(c2, "cancel"):
        reply = coach_confirmation.resolve_pending_turn(c2["owner"], "en")
    assert "cancelled" in reply and _proposal(c2["owner"]).status == "cancelled"
    assert _plan(c2["owner"]) == (c2["raw"], 0)


def test_release_refuses_while_the_boundary_is_open(c2):
    proposal_id = _stage(c2)
    with pytest.raises(EditError, match="proposal_unavailable"):
        service.release_unavailable(c2["owner"], proposal_id)
    assert _proposal(c2["owner"]).status == "pending"


# ── Final P3 hardening: query count, cache, B2 order, logging ────────────────

PROPOSAL_TABLE = Proposal.__tablename__


def _proposal_reads(fn):
    """``fn()`` and the statements it sent that touch the shared proposal table."""
    from sqlalchemy import event
    seen = []

    def record(_conn, _cursor, statement, *_):
        if PROPOSAL_TABLE in statement:
            seen.append(statement.split(None, 1)[0].upper())

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        return fn(), seen
    finally:
        event.remove(db.engine, "before_cursor_execute", record)


def _spy(monkeypatch, target, name, log):
    real = getattr(target, name)

    def spy(*args, **kwargs):
        log.append(name)
        return real(*args, **kwargs)
    monkeypatch.setattr(target, name, spy)


def _close_gate(monkeypatch):
    monkeypatch.setattr(access, "NUTRITION_EDITS_ENABLED", False)  # production state


@pytest.mark.parametrize("message", ["How should I train today?", "no", "hayır", "cancel"])
def test_closed_gate_turn_reads_the_proposal_table_exactly_once(c2, monkeypatch, message):
    # P1 regression + P3-1/P3-3: nothing pending → one proposal read, whether
    # or not the turn is a plain no/cancel, and no stranded release attempt.
    _close_gate(monkeypatch)
    calls = []
    _spy(monkeypatch, nutrition, "release_stranded", calls)
    monkeypatch.setattr(coach_confirmation, "_complete_grounded_followup", lambda *_: None)
    with Turn(c2, message):
        reply, reads = _proposal_reads(
            lambda: coach_confirmation.resolve_pending_turn(c2["owner"], "en"))
    assert reply is None and reads == ["SELECT"] and calls == []


def test_ordinary_turn_never_looks_for_a_stranded_quote(c2, monkeypatch):
    # P3-1: the stranded lookup sits behind the cancel intent.
    _stage(c2)
    _close_gate(monkeypatch)
    calls = []
    _spy(monkeypatch, coach_confirmation, "_raw_pending", calls)
    _spy(monkeypatch, nutrition, "release_stranded", calls)
    monkeypatch.setattr(coach_confirmation, "_complete_grounded_followup", lambda *_: None)
    with Turn(c2, "How should I train today?"):
        reply, reads = _proposal_reads(
            lambda: coach_confirmation.resolve_pending_turn(c2["owner"], "en"))
    assert reply is None and reads == ["SELECT"] and calls == []
    assert _proposal(c2["owner"]).status == "pending"


def test_typed_cancel_of_a_stranded_quote_reuses_the_loaded_row(c2, monkeypatch):
    proposal_id = _stage(c2)
    _close_gate(monkeypatch)
    real_get = coach_confirmation.plan_confirmation.get_pending
    gets = []
    monkeypatch.setattr(coach_confirmation.plan_confirmation, "get_pending",
                        lambda owner: gets.append(owner) or real_get(owner))
    with Turn(c2, "cancel"):
        reply = coach_confirmation.resolve_pending_turn(c2["owner"], "en")
    assert "cancelled" in reply and gets == [c2["owner"]]  # no second pending read
    assert _proposal(c2["owner"]).public_id == proposal_id
    assert _proposal(c2["owner"]).status == "cancelled"


def test_failed_pending_lookup_invalidates_the_request_cache(c2, monkeypatch):
    # P3-2: a stale row from an earlier read in the same app context must not
    # survive a failed lookup and be released on a later cancel.
    from flask import g
    proposal_id = _stage(c2)
    _close_gate(monkeypatch)
    stale = _proposal(c2["owner"])
    released = []
    monkeypatch.setattr(nutrition, "release_stranded",
                        lambda owner, pid=None: released.append(pid) or True)

    def broken(_owner):
        raise RuntimeError("database unavailable")
    with Turn(c2, "cancel"):
        g._coach_raw_pending_cache = (c2["owner"], stale)
        monkeypatch.setattr(coach_confirmation.plan_confirmation, "get_pending", broken)
        assert coach_confirmation._plan_pending(c2["owner"]) is None
        assert "_coach_raw_pending_cache" not in g
        reply = coach_confirmation.resolve_pending_turn(c2["owner"], "en")
    assert released == [] and "cancelled" not in (reply or "")
    assert _proposal(c2["owner"]).public_id == proposal_id
    assert _proposal(c2["owner"]).status == "pending"


def test_request_cache_is_keyed_by_owner(c2, monkeypatch):
    # A row cached for another owner is never returned; it is read afresh.
    from flask import g
    reads = []
    real = coach_confirmation.plan_confirmation.get_pending
    monkeypatch.setattr(coach_confirmation.plan_confirmation, "get_pending",
                        lambda owner: reads.append(owner) or real(owner))
    foreign = object()
    with Turn(c2, "cancel"):
        g._coach_raw_pending_cache = (c2["owner"] + 10_000, foreign)
        assert coach_confirmation._raw_pending(c2["owner"]) is not foreign
        assert reads == [c2["owner"]]
        assert g._coach_raw_pending_cache[0] == c2["owner"]


def test_successful_lookup_refreshes_the_request_cache(c2, monkeypatch):
    from flask import g
    _close_gate(monkeypatch)
    with Turn(c2, "cancel"):
        g._coach_raw_pending_cache = (c2["owner"], object())
        assert coach_confirmation._plan_pending(c2["owner"]) is None
        assert g._coach_raw_pending_cache == (c2["owner"], None)


def test_b2_cancel_checks_training_before_nutrition(c2, mobile, bearer, alice, monkeypatch):
    # P3-3: a Training cancel resolves on the Training path and never reaches
    # the Nutrition release; a stranded Nutrition id is tried there only after
    # the Training path found nothing.
    _seed_training(alice)
    with Turn(c2):
        staged = coach_plan_tools.execute_plan_tool(
            alice.id, "remove_training_plan_exercise",
            {"day": "Pazartesi", "exercise": "Shoulder Press"})
    assert staged["status"] == "confirmation_required"
    training_id = _proposal(alice.id).public_id
    _close_gate(monkeypatch)
    order = []
    _spy(monkeypatch, coach_plan_tools.executor, "execute_plan_tool", order)
    _spy(monkeypatch, nutrition, "release_stranded", order)
    response = _answer(mobile, bearer(alice), training_id, "cancel")
    assert response.status_code == 200 and response.get_json()["outcome"] == "cancelled"
    assert order == ["execute_plan_tool"]
    assert _proposal(alice.id).status == "cancelled"

    monkeypatch.setattr(access, "NUTRITION_EDITS_ENABLED", True)
    proposal_id = _stage(c2)
    _close_gate(monkeypatch)
    order.clear()
    response = _answer(mobile, bearer(alice), proposal_id, "cancel")
    assert response.status_code == 200 and response.get_json()["outcome"] == "cancelled"
    assert order == ["execute_plan_tool", "release_stranded"]
    assert _proposal(alice.id).status == "cancelled" and _plan(alice.id) == (c2["raw"], 0)


def test_b2_stranded_cancel_logs_its_real_outcome_without_content(c2, mobile, bearer, alice, monkeypatch):
    # P3-4: the release logs its own closed-vocabulary outcome.
    proposal_id = _stage(c2)
    _close_gate(monkeypatch)
    lines = []
    monkeypatch.setattr(coach_plan_tools.executor, "_log",
                        lambda name, outcome: lines.append((name, outcome)))
    response = _answer(mobile, bearer(alice), proposal_id, "cancel")
    assert response.status_code == 200 and response.get_json()["outcome"] == "cancelled"
    assert lines[-1] == (nutrition.STRANDED_RELEASE_LOG, "cancelled")
    text = repr(lines)
    assert proposal_id not in text and "Eggs" not in text and "Chicken" not in text
