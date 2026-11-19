"""C1 transactions on synthetic records; distributed races live in the PG suite."""
import json
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import NutritionPlan, NutritionPlanEditOperation as Operation
from app.models import NutritionPlanMutationRecord as Record, TrainingPlanConfirmationProposal as Proposal
from app.services import ai_consent, nutrition_plan_store
from app.services.plan_mutation import MutationContext
from app.services.nutrition_plan_mutation import Edit, EditError
from app.services.nutrition_plan_mutation import access, service

DOCUMENT = {"isim": "Synthetic", "kahvalti": {
    "yemekler": ["Eggs - 2 adet", "Bread - 60 g"],
    "kalori": 400, "protein": 20, "karb": 35, "yag": 15},
    "aksam": {"yemekler": ["Rice - 100 g"], "kalori": 200,
              "protein": 5, "karb": 40, "yag": 2},
    "toplam_kalori": 9999, "toplam_protein": 999, "toplam_karb": 999, "toplam_yag": 999}
EDIT = Edit("replace", "kahvalti", "Eggs - 2 adet", "Chicken - 100 g")
ESTIMATE = {"kalori": 450, "protein": 32, "karb": 38, "yag": 19}


def authorities(owner):
    return access.Authorities(ai_consent.OperationAuthority(owner, "coach", 1, "synthetic-edit"),
                              ai_consent.OperationAuthority(owner, "nutrition_plan", 1, "synthetic-nutrition"))


@pytest.fixture
def edit_rig(app, make_user, monkeypatch):
    owner = make_user("c1-service").id
    raw = json.dumps(DOCUMENT, indent=2)
    db.session.add(NutritionPlan(user_id=owner, plan_data=raw))
    db.session.commit()
    monkeypatch.setattr(access, "NUTRITION_EDITS_ENABLED", True)
    monkeypatch.setattr(access, "APPROVED_COACH_EDIT_DISCLOSURES", frozenset({"synthetic-edit"}))
    fences, calls = [], []

    def fence(authority, user, feature=None):
        assert authority.owner == user == owner
        assert authority.feature == feature
        fences.append(feature)

    def estimate(items):
        assert not db.session().in_transaction(), "provider I/O must not hold a SQL transaction"
        assert ai_consent.current_operation() == authorities(owner).nutrition
        calls.append(tuple(items))
        return dict(ESTIMATE)

    monkeypatch.setattr(ai_consent, "fence", fence)
    monkeypatch.setattr(service.estimation, "estimate_meal", estimate)
    return SimpleNamespace(owner=owner, raw=raw, calls=calls, fences=fences, authorities=authorities(owner))


def prepare(rig, key="c1-edit-0001", edit=EDIT):
    return service.prepare_edit(rig.owner, edit, MutationContext(key), authorities=rig.authorities)


def state(owner):
    row = nutrition_plan_store.newest_plan(owner)
    return row.plan_data, row.mutation_version


def test_quote_confirm_replay_and_exact_undo(edit_rig):
    r = edit_rig
    quote = prepare(r)
    assert quote.status == "prepared" and quote.requires_confirmation and quote.estimated
    assert "unchanged foods" in quote.estimate_notice
    assert state(r.owner) == (r.raw, 0)
    assert Record.query.count() == 0
    assert prepare(r).proposal_id == quote.proposal_id
    applied = service.resolve_edit(r.owner, quote.proposal_id, "confirm")
    assert applied.is_current and applied.plan_version == 1 and applied.estimated
    doc = json.loads(applied.plan_data)
    assert doc["toplam_kalori"] == 650 and doc["toplam_protein"] == 37
    assert doc["aksam"] == DOCUMENT["aksam"]
    assert doc["kahvalti"]["tahmini"] is True
    from app.services.nutrition_native.plan import project_plan_row
    db.session.remove()
    reopened = project_plan_row(nutrition_plan_store.newest_plan(r.owner), "synthetic-secret")
    assert {m["slot"]: m["estimated"] for m in reopened["meals"]} == {"kahvalti": True, "aksam": False}
    assert service.resolve_edit(r.owner, quote.proposal_id, "confirm").replayed
    assert service.resolve_edit(r.owner, quote.proposal_id, "cancel").status == "applied"
    assert prepare(r).mutation_id == applied.mutation_id
    undone = service.undo_last_change(r.owner, MutationContext("c1-undo-0001"), authorities=r.authorities)
    assert undone.plan_data == r.raw and undone.plan_version == 2
    assert service.undo_last_change(r.owner, MutationContext("c1-undo-0001"), authorities=r.authorities).replayed
    with pytest.raises(EditError, match="plan_stale"):
        service.resolve_edit(r.owner, quote.proposal_id, "confirm")
    assert len(r.calls) == 1 and Record.query.count() == 2


@pytest.mark.parametrize("kind,item,replacement", [
    ("add", "Apple - 100 g", None), ("remove", "Eggs - 2 adet", None)])
def test_add_and_remove_require_confirm(edit_rig, kind, item, replacement):
    r = edit_rig
    quote = prepare(r, edit=Edit(kind, "kahvalti", item, replacement))
    assert state(r.owner) == (r.raw, 0)
    assert quote.requires_confirmation
    result = service.resolve_edit(r.owner, quote.proposal_id, "confirm")
    foods = json.loads(result.plan_data)["kahvalti"]["yemekler"]
    assert (item in foods) == (kind == "add")
    assert len(r.calls) == 1


def test_noop_and_bad_reference_do_not_dispatch(edit_rig):
    r = edit_rig
    with pytest.raises(EditError, match="item_not_found"):
        prepare(r, edit=Edit("remove", "kahvalti", "Egg"))
    assert Operation.query.count() == 0
    result = prepare(r, edit=Edit("replace", "kahvalti", "Eggs - 2 adet", "Eggs - 2 adet"))
    assert result.status == "no_op" and not result.estimated
    assert not r.calls and not Proposal.query.count() and not Record.query.count()
    assert state(r.owner) == (r.raw, 0)


def test_cancel_replay_and_next_edit(edit_rig):
    r = edit_rig
    q = prepare(r)
    assert service.resolve_edit(r.owner, q.proposal_id, "cancel").status == "cancelled"
    assert service.resolve_edit(r.owner, q.proposal_id, "cancel").replayed
    with pytest.raises(EditError, match="proposal_unavailable"):
        service.resolve_edit(r.owner, q.proposal_id, "confirm")
    assert prepare(r, "c1-edit-0002").requires_confirmation
    assert state(r.owner) == (r.raw, 0) and Record.query.count() == 0


def test_key_conflicts_and_one_pending(edit_rig):
    r = edit_rig
    prepare(r)
    with pytest.raises(EditError, match="idempotency_conflict"):
        prepare(r, edit=Edit("remove", "kahvalti", "Eggs - 2 adet"))
    with pytest.raises(EditError, match="pending_change_exists"):
        prepare(r, "c1-edit-0002")
    assert len(r.calls) == 1


@pytest.mark.parametrize("failure", [RuntimeError("provider unavailable"), EditError("estimate_unavailable")])
def test_failed_estimator_writes_no_plan_proposal_or_journal(edit_rig, monkeypatch, failure):
    r = edit_rig
    def fail(_):
        r.calls.append("failed")
        raise failure
    monkeypatch.setattr(service.estimation, "estimate_meal", fail)
    with pytest.raises(type(failure)):
        prepare(r)
    with pytest.raises(EditError, match="estimate_unavailable"):
        prepare(r)
    assert r.calls == ["failed"]
    assert state(r.owner) == (r.raw, 0)
    assert Record.query.count() == Proposal.query.count() == 0
    assert Operation.query.one().status == "failed"


@pytest.mark.parametrize("change", ["version", "bytes", "replacement"])
def test_stale_confirmation_never_writes(edit_rig, change):
    r = edit_rig
    q = prepare(r)
    row = nutrition_plan_store.newest_plan(r.owner)
    if change == "version": row.mutation_version += 1
    elif change == "bytes": row.plan_data += " "
    else:
        db.session.delete(row)
        db.session.add(NutritionPlan(user_id=r.owner, plan_data=r.raw))
    db.session.commit()
    before = state(r.owner)
    with pytest.raises(EditError, match="plan_stale"):
        service.resolve_edit(r.owner, q.proposal_id, "confirm")
    assert state(r.owner) == before and Record.query.count() == 0
    assert Operation.query.one().status == Proposal.query.one().status == "stale"
    assert prepare(r, "c1-edit-0002").status == "prepared"


def test_atomic_rollback_if_journal_flush_fails(edit_rig, monkeypatch):
    r = edit_rig
    q = prepare(r)
    from sqlalchemy import event
    def fail(session, *_):
        if any(isinstance(row, Record) for row in session.new):
            raise RuntimeError("synthetic journal failure")
    event.listen(db.session(), "before_flush", fail)
    try:
        with pytest.raises(RuntimeError, match="journal failure"):
            service.resolve_edit(r.owner, q.proposal_id, "confirm")
    finally:
        event.remove(db.session(), "before_flush", fail)
    assert state(r.owner) == (r.raw, 0) and Record.query.count() == 0
    assert Operation.query.one().status == "prepared" and Proposal.query.one().status == "pending"
    assert service.resolve_edit(r.owner, q.proposal_id, "confirm").is_current


def test_original_authority_denial_retires_both_states(edit_rig, monkeypatch):
    r = edit_rig
    q = prepare(r)
    def deny(*_): raise ai_consent.ConsentDenied("AI_CONSENT_CHANGED")
    with monkeypatch.context() as patch:
        patch.setattr(access, "fence", deny)
        with pytest.raises(ai_consent.ConsentDenied):
            service.resolve_edit(r.owner, q.proposal_id, "confirm")
    assert Operation.query.one().status == Proposal.query.one().status == "stale"
    assert state(r.owner) == (r.raw, 0) and Record.query.count() == 0
    assert prepare(r, "c1-edit-0002").status == "prepared"


def test_transient_authority_failure_keeps_recoverable(edit_rig, monkeypatch):
    r = edit_rig
    q = prepare(r)
    def deny(*_): raise ai_consent.ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
    with monkeypatch.context() as patch:
        patch.setattr(access, "fence", deny)
        with pytest.raises(ai_consent.ConsentDenied):
            service.resolve_edit(r.owner, q.proposal_id, "confirm")
    assert Operation.query.one().status == "prepared" and Proposal.query.one().status == "pending"
    assert service.resolve_edit(r.owner, q.proposal_id, "confirm").is_current


def test_crash_claim_is_not_redispatched_and_can_be_cancelled_after_withdrawal(edit_rig, monkeypatch):
    r = edit_rig
    class Crash(BaseException): pass
    def crash(_): raise Crash()
    monkeypatch.setattr(service.estimation, "estimate_meal", crash)
    with pytest.raises(Crash): prepare(r)
    replay = prepare(r)
    assert replay.status == "estimating" and replay.replayed
    monkeypatch.setattr(access, "fence", lambda *_: (_ for _ in ()).throw(AssertionError("withdrawn authority")))
    assert service.cancel_preparation(r.owner, replay.operation_id).status == "cancelled"
    assert state(r.owner) == (r.raw, 0)


def test_foreign_owner_cannot_resolve(edit_rig, make_user):
    q = prepare(edit_rig)
    stranger = make_user("c1-foreign").id
    with pytest.raises(EditError, match="proposal_unavailable"):
        service.resolve_edit(stranger, q.proposal_id, "confirm")
    assert state(edit_rig.owner) == (edit_rig.raw, 0)


@pytest.mark.parametrize("decision", ["confirm", "cancel"])
def test_training_actions_cannot_resolve_or_expose_nutrition(edit_rig, monkeypatch, decision):
    from app.services.coach_plan_tools import executor, turn_effects
    q = prepare(edit_rig)
    monkeypatch.setattr(executor, "plan_mutation_tools_enabled", lambda: True)
    monkeypatch.setattr(turn_effects, "plan_mutation_tools_enabled", lambda: True)
    result = executor.execute_direct_confirmation(edit_rig.owner, q.proposal_id, decision)
    assert result["status"] != "applied"
    assert turn_effects.turn_plan_effects(edit_rig.owner)["proposal_id"] is None
    assert Proposal.query.one().status == "pending" and Operation.query.one().status == "prepared"
    assert service.resolve_edit(edit_rig.owner, q.proposal_id, "cancel").status == "cancelled"


def test_release_gates_closed_without_mutating_ordinary_consent(app, monkeypatch, make_user):
    monkeypatch.setenv("AI_COACH_PLAN_MUTATION_TOOLS_ENABLED", "1")
    app.config["AI_COACH_PLAN_MUTATION_TOOLS_ENABLED"] = True
    owner = make_user("c1-source-hold").id
    raw = json.dumps(DOCUMENT)
    db.session.add(NutritionPlan(user_id=owner, plan_data=raw))
    db.session.commit()
    def unexpected_estimate(_): raise AssertionError("closed gate dispatched an estimate")
    monkeypatch.setattr(service.estimation, "estimate_meal", unexpected_estimate)
    assert access.NUTRITION_EDITS_ENABLED is False
    assert not access.APPROVED_COACH_EDIT_DISCLOSURES
    with pytest.raises(EditError, match="nutrition_edit_unavailable"):
        service.prepare_edit(owner, EDIT, MutationContext("c1-gated-0001"), authorities=authorities(owner))
    with pytest.raises(EditError, match="nutrition_edit_unavailable"):
        service.resolve_edit(owner, "A" * 32, "confirm")
    with pytest.raises(EditError, match="nutrition_edit_unavailable"):
        service.undo_last_change(owner, MutationContext("c1-gated-undo"), authorities=authorities(owner))
    # Even opening the operational source switch cannot bypass legal approval.
    monkeypatch.setattr(access, "NUTRITION_EDITS_ENABLED", True)
    with pytest.raises(EditError, match="nutrition_edit_unavailable"):
        access.gate()
    assert state(owner) == (raw, 0)
    assert Operation.query.count() == Proposal.query.count() == Record.query.count() == 0


def test_c2_routes_and_tools_remain_absent_with_coach_flag_enabled(app, monkeypatch):
    from app.services import coach_plan_tools
    monkeypatch.setenv("AI_COACH_PLAN_MUTATION_TOOLS_ENABLED", "1")
    app.config["AI_COACH_PLAN_MUTATION_TOOLS_ENABLED"] = True
    nutrition_tools = {"get_current_nutrition_plan", "replace_nutrition_plan_item",
                       "add_nutrition_plan_item", "remove_nutrition_plan_item",
                       "undo_nutrition_plan_change"}
    exposed = {d["name"] for d in coach_plan_tools.PLAN_MUTATION_TOOL_DEFS}
    assert not exposed & nutrition_tools
    for rule in app.url_map.iter_rules():
        view = app.view_functions[rule.endpoint]
        assert "nutrition_plan_mutation" not in view.__module__
        assert "nutrition" not in rule.rule or not any(
            token in rule.rule for token in ("/confirmation", "/edit", "/undo", "/mutation"))


@pytest.mark.parametrize("message", ["cancel", "yes", "How should I train today?"])
def test_coach_turn_never_intercepts_nutrition_pending_as_training(edit_rig, app, monkeypatch, message):
    from app.services import ai_coach, coach_confirmation
    from app.services.coach_plan_tools import executor
    q = prepare(edit_rig)
    monkeypatch.setattr(executor, "plan_mutation_tools_enabled", lambda: True)
    with app.test_request_context("/synthetic-coach", method="POST"):
        ai_coach._begin_coach_turn(message, user_id=edit_rig.owner)
        assert coach_confirmation.resolve_pending_turn(edit_rig.owner, "en") is None
        assert executor.published_plan_tool_defs(edit_rig.owner) == ()
    assert Proposal.query.one().status == "pending" and Operation.query.one().status == "prepared"
    assert service.resolve_edit(edit_rig.owner, q.proposal_id, "cancel").status == "cancelled"


def test_nutrition_store_cannot_mutate_a_training_row_even_with_matching_binding(edit_rig):
    from app.models import TrainingPlan
    r = edit_rig
    # Deliberately give a Training row valid Nutrition bytes and every matching
    # precondition. Schema/binding alone cannot distinguish persistence domains.
    row = TrainingPlan(user_id=r.owner, plan_data=r.raw)
    db.session.add(row)
    db.session.commit()
    before = row.plan_data, row.mutation_version
    with pytest.raises(EditError, match="invalid_plan_domain"):
        nutrition_plan_store.write_targeted_locked(row, lineage_id=row.lineage_id,
            version=row.mutation_version, before=row.plan_data,
            after=json.dumps({**DOCUMENT, "isim": "Would corrupt training"}))
    assert (row.plan_data, row.mutation_version) == before
    assert row not in db.session.dirty


def test_pending_admission_exposes_only_an_owner_scoped_boolean(edit_rig, make_user):
    from app.services.plan_confirmation import has_pending
    stranger = make_user("c1-pending-foreign").id
    q = prepare(edit_rig)
    assert has_pending(edit_rig.owner) is True
    assert has_pending(stranger) is False
    assert has_pending(True) is False
    assert has_pending(None) is False
    assert service.resolve_edit(edit_rig.owner, q.proposal_id, "cancel").status == "cancelled"
    assert has_pending(edit_rig.owner) is False


@pytest.mark.parametrize("outcome", ["confirm", "cancel", "failure", "journal_failure"])
def test_old_writer_null_lineage_is_adopted_only_in_atomic_confirmation(edit_rig, monkeypatch, outcome):
    r = edit_rig
    row = nutrition_plan_store.newest_plan(r.owner)
    row.lineage_id = None
    db.session.commit()
    if outcome == "failure":
        def fail(_): raise EditError("estimate_unavailable")
        monkeypatch.setattr(service.estimation, "estimate_meal", fail)
        with pytest.raises(EditError, match="estimate_unavailable"): prepare(r)
        assert nutrition_plan_store.newest_plan(r.owner).lineage_id is None
        assert state(r.owner) == (r.raw, 0)
        assert Record.query.count() == Proposal.query.count() == 0
        return
    q = prepare(r)
    assert nutrition_plan_store.newest_plan(r.owner).lineage_id is None
    assert state(r.owner) == (r.raw, 0)
    if outcome == "journal_failure":
        from sqlalchemy import event
        def fail(session, *_):
            if any(isinstance(item, Record) for item in session.new):
                raise RuntimeError("synthetic journal failure")
        event.listen(db.session(), "before_flush", fail)
        try:
            with pytest.raises(RuntimeError, match="journal failure"):
                service.resolve_edit(r.owner, q.proposal_id, "confirm")
        finally:
            event.remove(db.session(), "before_flush", fail)
        assert nutrition_plan_store.newest_plan(r.owner).lineage_id is None
        assert state(r.owner) == (r.raw, 0) and Record.query.count() == 0
        return
    service.resolve_edit(r.owner, q.proposal_id, outcome)
    row = nutrition_plan_store.newest_plan(r.owner)
    if outcome == "cancel":
        assert row.lineage_id is None and state(r.owner) == (r.raw, 0)
    else:
        assert row.lineage_id == Operation.query.one().plan_lineage_id == Record.query.one().plan_lineage_id
        assert service.resolve_edit(r.owner, q.proposal_id, "confirm").replayed
        undone = service.undo_last_change(r.owner, MutationContext("c1-legacy-undo"), authorities=r.authorities)
        assert undone.plan_data == r.raw and undone.plan_version == 2


@pytest.mark.parametrize("field", ["plan_id", "base_lineage_id"])
def test_incomplete_server_binding_cannot_confirm(edit_rig, field):
    q = prepare(edit_rig)
    op = Operation.query.one()
    payload = dict(op.command_payload)
    payload.pop(field)
    op.command_payload = payload
    db.session.commit()
    with pytest.raises(EditError, match="plan_stale"):
        service.resolve_edit(edit_rig.owner, q.proposal_id, "confirm")
    assert state(edit_rig.owner) == (edit_rig.raw, 0) and Record.query.count() == 0


@pytest.mark.parametrize("marker", [False, 0, 1, "true", None, {}])
def test_estimate_marker_is_closed_and_typed(marker):
    from app.services.nutrition_plan_schema import validate_nutrition_plan_for_save, NutritionPlanInvalid
    doc = {**DOCUMENT, "kahvalti": {**DOCUMENT["kahvalti"], "tahmini": marker}}
    with pytest.raises(NutritionPlanInvalid): validate_nutrition_plan_for_save(doc)


def test_unrelated_estimate_label_survives_edit_and_exact_undo(edit_rig):
    r = edit_rig
    row = nutrition_plan_store.newest_plan(r.owner)
    doc = json.loads(row.plan_data)
    doc["aksam"]["tahmini"] = True
    r.raw = row.plan_data = json.dumps(doc, indent=2)
    db.session.commit()
    q = prepare(r)
    result = service.resolve_edit(r.owner, q.proposal_id, "confirm")
    assert json.loads(result.plan_data)["aksam"] == doc["aksam"]
    undo = service.undo_last_change(r.owner, MutationContext("c1-label-undo"), authorities=r.authorities)
    assert undo.plan_data == r.raw
