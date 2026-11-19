"""Adaptive Coach C1 — targeted Nutrition Plan edits (domain only).

F-3 policy under test: whole-meal re-estimation by the injected server
estimator, explicit two-phase prepare/apply, estimated-value labelling and
provenance, atomic meal + daily-total update from canonical meals, version
history, undo, idempotency, and ZERO writes on a failed or ambiguous estimate.
Assertions are on persisted state.
"""
import json

import pytest

from app.extensions import db
from app.models import NutritionPlan, NutritionPlanMutationRecord
from app.services import ai_consent, health_consent
from app.services import nutrition_plan_mutation as npm
from app.services.nutrition_native.plan import project_plan_row
from app.services.nutrition_plan_schema import (
    NutritionPlanInvalid,
    validate_nutrition_plan_for_save,
)

PLAN = {
    "isim": "Plan A",
    "kahvalti": {"yemekler": ["Yumurta - 3 adet", "Tam buğday ekmeği - 2 dilim"],
                 "kalori": 420, "protein": 28, "karb": 35, "yag": 18},
    "ogle": {"yemekler": ["Tavuk göğsü - 150 g", "Pilav - 1 porsiyon"],
             "kalori": 600, "protein": 50, "karb": 60, "yag": 12},
    "toplam_kalori": 1020, "toplam_protein": 78, "toplam_karb": 95, "toplam_yag": 30,
}
# Energy-consistent per-item estimates (4/4/9).
TABLE = {
    "tam buğday ekmeği - 2 dilim": {"calories": 160, "protein": 8, "carbs": 30, "fat": 1},
    "yumurta - 3 adet": {"calories": 210, "protein": 18, "carbs": 2, "fat": 15},
    "dana eti - 150 g": {"calories": 300, "protein": 39, "carbs": 0, "fat": 16},
    "avokado - yarım": {"calories": 120, "protein": 1.5, "carbs": 6, "fat": 10.5},
}
REPLACE = npm.ReplaceMealItemCommand("kahvalti", "Yumurta - 3 adet", "Dana eti - 150 g")


def estimator(calls=None, table=TABLE):
    def run(names):
        if calls is not None:
            calls.append(list(names))
        return {n: table[n.casefold()] for n in names if n.casefold() in table}
    return run


@pytest.fixture
def fences(monkeypatch):
    calls = []

    def fence(authority, owner, feature=None):
        calls.append((authority, owner, feature))
        if authority is None:
            raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")

    monkeypatch.setattr(ai_consent, "fence", fence)
    monkeypatch.setattr(ai_consent, "fence_active", lambda owner: None)
    return calls


@pytest.fixture
def owner(app, make_user):
    user = make_user("nutri")
    db.session.add(NutritionPlan(user_id=user.id, plan_data=json.dumps(PLAN, ensure_ascii=False),
                                 score=8.0))
    db.session.commit()
    return user


def auth_for(user_id):
    return ai_consent.OperationAuthority(user_id, "nutrition_plan", 3, "v")


@pytest.fixture
def authority(owner):
    return auth_for(owner.id)


def ctx(key="edit-key-0001"):
    return npm.NutritionEditContext(idempotency_key=key, actor=npm.ACTOR_AI_COACH)


def row(user):
    db.session.expire_all()
    return NutritionPlan.query.filter_by(user_id=user.id).one()


def journal(user):
    return NutritionPlanMutationRecord.query.filter_by(user_id=user.id).count()


def test_prepare_reestimates_the_whole_meal_and_writes_nothing(owner):
    calls = []
    before = row(owner).plan_data

    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(calls), authority=auth_for(owner.id))

    # The WHOLE edited meal was estimated, not just the new item.
    assert sorted(calls[0]) == ["Dana eti - 150 g", "Tam buğday ekmeği - 2 dilim"]
    meal = prepared.document["kahvalti"]
    assert meal["yemekler"] == ["Dana eti - 150 g", "Tam buğday ekmeği - 2 dilim"]
    assert (meal["kalori"], meal["protein"], meal["karb"], meal["yag"]) == (460, 47, 30, 17)
    assert meal["tahmini"] is True
    assert "tahmini" not in prepared.document["ogle"]  # untouched meal unchanged
    assert (prepared.document["toplam_kalori"], prepared.document["toplam_protein"],
            prepared.document["toplam_karb"], prepared.document["toplam_yag"]) == (1060, 97, 90, 29)
    assert prepared.provenance["method"] == "whole_meal_reestimation"
    assert prepared.provenance["source"] == npm.ESTIMATOR_ID
    assert row(owner).plan_data == before and row(owner).mutation_version == 0
    assert journal(owner) == 0


def test_apply_writes_document_version_and_journal_once(owner, fences, authority, app):
    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))

    result = npm.apply_prepared_edit(owner.id, prepared, ctx(), authority=authority)

    assert result.outcome == npm.OUTCOME_APPLIED and result.plan_version == 1
    saved = row(owner)
    assert json.loads(saved.plan_data) == prepared.document
    assert saved.mutation_version == 1 and saved.lineage_id
    record = NutritionPlanMutationRecord.query.filter_by(user_id=owner.id).one()
    assert json.loads(record.provenance)["meal"] == "kahvalti"
    assert json.loads(record.before_snapshot) == PLAN
    assert fences == [(authority, owner.id, "nutrition_plan")]
    # The saved-plan read labels the estimated meal; the other meal is not.
    projected = project_plan_row(saved, app.config["SECRET_KEY"])
    flags = {m["slot"]: m["estimated"] for m in projected["meals"]}
    assert flags == {"kahvalti": True, "ogle": False}

    again = npm.apply_prepared_edit(owner.id, prepared, ctx(), authority=authority)
    assert again.replayed and again.plan_version == 1
    assert journal(owner) == 1 and row(owner).mutation_version == 1


def test_one_key_never_stands_for_two_different_estimates(owner, fences, authority):
    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))
    npm.apply_prepared_edit(owner.id, prepared, ctx(), authority=authority)
    other = npm.prepare_meal_edit(
        owner.id, npm.AddMealItemCommand("ogle", "Avokado - yarım"),
        estimator(table={**TABLE, "tavuk göğsü - 150 g": {"calories": 250, "protein": 46, "carbs": 0, "fat": 7},
                         "pilav - 1 porsiyon": {"calories": 350, "protein": 6, "carbs": 75, "fat": 3}}), authority=auth_for(owner.id))
    with pytest.raises(npm.NutritionIdempotencyConflict):
        npm.apply_prepared_edit(owner.id, other, ctx(), authority=authority)
    assert journal(owner) == 1


def test_a_stale_prepared_edit_writes_nothing(owner, fences, authority):
    first = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))
    second = npm.prepare_meal_edit(
        owner.id, npm.RemoveMealItemCommand("kahvalti", "Tam buğday ekmeği - 2 dilim"),
        estimator(), authority=auth_for(owner.id))
    npm.apply_prepared_edit(owner.id, first, ctx("edit-key-0001"), authority=authority)
    after_first = row(owner).plan_data

    with pytest.raises(npm.NutritionPlanStateConflict):
        npm.apply_prepared_edit(owner.id, second, ctx("edit-key-0002"), authority=authority)
    assert row(owner).plan_data == after_first and journal(owner) == 1


def test_a_refused_authority_writes_nothing(owner, fences):
    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))
    with pytest.raises(ai_consent.ConsentDenied):
        npm.apply_prepared_edit(owner.id, prepared, ctx(), authority=None)
    assert row(owner).mutation_version == 0 and journal(owner) == 0


@pytest.mark.parametrize("table", [
    {},  # estimator resolved nothing
    {"dana eti - 150 g": TABLE["dana eti - 150 g"]},  # one item missing
    {**TABLE, "dana eti - 150 g": {"calories": 900, "protein": 5, "carbs": 5, "fat": 5}},  # inconsistent
    {**TABLE, "dana eti - 150 g": {"calories": 300, "protein": "39", "carbs": 0, "fat": 16}},  # not a number
    {**TABLE, "dana eti - 150 g": {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}},  # no energy
])
def test_a_failed_or_ambiguous_estimate_refuses(owner, table):
    with pytest.raises(npm.EstimateUnavailable):
        npm.prepare_meal_edit(owner.id, REPLACE, estimator(table=table), authority=auth_for(owner.id))
    assert row(owner).mutation_version == 0 and journal(owner) == 0


def test_an_estimator_fault_refuses(owner):
    def boom(names):
        raise RuntimeError("provider down")
    with pytest.raises(npm.EstimateUnavailable):
        npm.prepare_meal_edit(owner.id, REPLACE, boom, authority=auth_for(owner.id))


def test_totals_are_never_a_partial_sum(owner):
    plan = row(owner)
    document = json.loads(plan.plan_data)
    del document["ogle"]["kalori"]
    plan.plan_data = json.dumps(document, ensure_ascii=False)
    db.session.commit()
    with pytest.raises(npm.EstimateUnavailable):
        npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))


@pytest.mark.parametrize("command, error", [
    (npm.ReplaceMealItemCommand("kahvalti", "Muz", "Elma"), npm.MealItemNotFound),
    (npm.ReplaceMealItemCommand("aksam", "Yumurta - 3 adet", "Elma"), npm.MealNotFound),
    (npm.AddMealItemCommand("kahvalti", "  yumurta -  3 ADET "), npm.MealItemAlreadyPresent),
    (npm.ReplaceMealItemCommand("brunch", "a", "b"), npm.InvalidNutritionEdit),
    (npm.AddMealItemCommand("kahvalti", "bad\nitem"), npm.InvalidNutritionEdit),
])
def test_targets_are_exact_and_refusals_write_nothing(owner, command, error):
    with pytest.raises(error):
        npm.prepare_meal_edit(owner.id, command, estimator(), authority=auth_for(owner.id))
    assert journal(owner) == 0


def test_two_matching_items_are_ambiguous(owner):
    plan = row(owner)
    document = json.loads(plan.plan_data)
    document["kahvalti"]["yemekler"] = ["Yumurta - 3 adet", "yumurta - 3 adet"]
    plan.plan_data = json.dumps(document, ensure_ascii=False)
    db.session.commit()
    with pytest.raises(npm.AmbiguousMealItem):
        npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))


def test_the_last_item_of_a_meal_is_not_removed(owner):
    plan = row(owner)
    document = json.loads(plan.plan_data)
    document["kahvalti"]["yemekler"] = ["Yumurta - 3 adet"]
    plan.plan_data = json.dumps(document, ensure_ascii=False)
    db.session.commit()
    with pytest.raises(npm.InvalidNutritionEdit):
        npm.prepare_meal_edit(
            owner.id, npm.RemoveMealItemCommand("kahvalti", "Yumurta - 3 adet"), estimator(), authority=auth_for(owner.id))


def test_a_same_item_replace_is_a_journaled_no_op(owner, fences, authority):
    calls = []
    same = npm.ReplaceMealItemCommand("kahvalti", "Yumurta - 3 adet", "yumurta - 3 adet")
    prepared = npm.prepare_meal_edit(owner.id, same, estimator(calls), authority=auth_for(owner.id))
    assert calls == [] and prepared.changed is False  # nothing to estimate
    result = npm.apply_prepared_edit(owner.id, prepared, ctx(), authority=authority)
    assert result.outcome == npm.OUTCOME_NO_OP and row(owner).mutation_version == 0
    assert journal(owner) == 1


def test_undo_restores_exact_bytes_as_a_new_version(owner, fences, authority):
    original = row(owner).plan_data
    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))
    npm.apply_prepared_edit(owner.id, prepared, ctx("edit-key-0001"), authority=authority)

    undone = npm.undo_last_nutrition_edit(owner.id, ctx("undo-key-0001"))

    assert undone.plan_version == 2
    assert row(owner).plan_data == original and row(owner).mutation_version == 2
    replay = npm.undo_last_nutrition_edit(owner.id, ctx("undo-key-0001"))
    assert replay.replayed and row(owner).mutation_version == 2
    with pytest.raises(npm.NutritionUndoUnavailable):
        npm.undo_last_nutrition_edit(owner.id, ctx("undo-key-0002"))
    assert journal(owner) == 2


def test_undo_never_crosses_a_replacement(owner, fences, authority):
    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))
    npm.apply_prepared_edit(owner.id, prepared, ctx(), authority=authority)
    # Whole-plan replacement = new row = new lineage.
    NutritionPlan.query.filter_by(user_id=owner.id).delete()
    db.session.add(NutritionPlan(user_id=owner.id, plan_data=json.dumps(PLAN), score=7.0))
    db.session.commit()
    with pytest.raises(npm.NutritionUndoUnavailable):
        npm.undo_last_nutrition_edit(owner.id, ctx("undo-key-0001"))


def test_undo_refuses_when_the_plan_moved_outside_this_boundary(owner, fences, authority):
    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))
    npm.apply_prepared_edit(owner.id, prepared, ctx(), authority=authority)
    plan = row(owner)
    plan.plan_data = plan.plan_data.replace("Plan A", "Plan B")
    db.session.commit()
    with pytest.raises(npm.NutritionUndoConflict):
        npm.undo_last_nutrition_edit(owner.id, ctx("undo-key-0001"))


def test_a_row_without_lineage_gets_one_on_its_first_edit(owner, fences, authority):
    plan = row(owner)
    plan.lineage_id = None  # written by the previous revision / pre-C1
    db.session.commit()
    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))
    assert prepared.lineage_id is None
    npm.apply_prepared_edit(owner.id, prepared, ctx(), authority=authority)
    assert row(owner).lineage_id
    assert NutritionPlanMutationRecord.query.one().plan_lineage_id == row(owner).lineage_id


def test_a_prepared_edit_round_trips_through_its_payload(owner, fences, authority):
    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))
    stored = json.loads(json.dumps(prepared.to_payload()))
    restored = npm.PreparedMealEdit.from_payload(stored)
    assert restored == prepared
    with pytest.raises(npm.InvalidNutritionEdit):
        npm.PreparedMealEdit.from_payload({"v": 2})


def test_the_estimated_marker_is_closed():
    plan = json.loads(json.dumps(PLAN))
    plan["kahvalti"]["tahmini"] = True
    assert validate_nutrition_plan_for_save(plan)["kahvalti"]["tahmini"] is True
    for bad in (False, "true", 1):
        plan["kahvalti"]["tahmini"] = bad
        with pytest.raises(NutritionPlanInvalid):
            validate_nutrition_plan_for_save(plan)


def test_another_users_plan_is_unreachable(owner, make_user):
    stranger = make_user("stranger")
    with pytest.raises(npm.NutritionPlanNotFound):
        npm.prepare_meal_edit(stranger.id, REPLACE, estimator(), authority=auth_for(stranger.id))


# ── production estimator wiring (review P2) ────────────────────────────────

def test_the_server_estimator_runs_under_the_nutrition_plan_operation(
        app, owner, authority, monkeypatch):
    """The real estimate chain calls the model as ``nutrition_plan`` — the
    purpose apply's fence requires — so the provider gate admits it inside the
    owner's Nutrition Plan operation instead of failing closed."""
    from app.services import ai_nutrition
    seen = []

    def fake_heavy_chat(*, messages, system_prompt, temperature, max_tokens, feature):
        ai_consent.provider_binding(owner.id, feature)  # the real gate decision
        seen.append(feature)
        return json.dumps({name: {"calories": TABLE[name.casefold()]["calories"],
                                  "protein": TABLE[name.casefold()]["protein"],
                                  "carbs": TABLE[name.casefold()]["carbs"],
                                  "fat": TABLE[name.casefold()]["fat"]}
                           for name in ("Dana eti - 150 g", "Tam buğday ekmeği - 2 dilim")},
                          ensure_ascii=False)

    monkeypatch.setattr(ai_nutrition, "_heavy_chat", fake_heavy_chat)
    with app.test_request_context(), health_consent.processing(owner.id), \
            ai_consent.operation_scope(authority):
        prepared = npm.prepare_meal_edit(owner.id, REPLACE, npm.server_estimator, authority=auth_for(owner.id))
    assert seen == ["nutrition_plan"]
    assert prepared.document["kahvalti"]["kalori"] == 460


def test_a_consent_refusal_is_typed_not_an_estimate_failure(
        app, owner, authority, monkeypatch):
    from app.services import ai_nutrition

    def refused(**kwargs):
        raise ai_consent.ConsentDenied("AI_CONSENT_REQUIRED")

    monkeypatch.setattr(ai_nutrition, "_heavy_chat", refused)
    with app.test_request_context(), health_consent.processing(owner.id), \
            ai_consent.operation_scope(authority):
        with pytest.raises(ai_consent.ConsentDenied):
            npm.prepare_meal_edit(owner.id, REPLACE, npm.server_estimator, authority=auth_for(owner.id))
    assert row(owner).mutation_version == 0 and journal(owner) == 0


def test_the_legacy_nutrition_estimator_still_swallows_failures(monkeypatch, app):
    """Existing callers (menu analysis, diary) keep their {} contract."""
    from app.services import ai_nutrition

    def refused(**kwargs):
        raise ai_consent.ConsentDenied("AI_CONSENT_REQUIRED")

    monkeypatch.setattr(ai_nutrition, "_heavy_chat", refused)
    with app.test_request_context("/"):
        assert ai_nutrition._estimate_macros_llm_batch(["Elma"]) == {}


def test_prepare_never_discards_a_callers_pending_work(owner):
    db.session.add(NutritionPlan(user_id=owner.id, plan_data="{}", score=1.0))
    with pytest.raises(npm.InvalidNutritionEdit):
        npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))
    db.session.rollback()


def test_the_edit_service_is_the_only_targeted_nutrition_plan_writer():
    """Single owner of a loaded NutritionPlan row's ``plan_data`` and
    ``mutation_version`` (whole replacement constructs a new row instead)."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app"
    owner = root / "services" / "nutrition_plan_mutation" / "service.py"
    writers = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if "NutritionPlan" not in path.read_text(encoding="utf-8"):
            continue
        for node in ast.walk(tree):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign))
                       else [])
            for target in targets:
                if (isinstance(target, ast.Attribute)
                        and target.attr in ("plan_data", "mutation_version", "lineage_id")
                        and path != owner and "plan_mutation/service.py" not in path.as_posix()):
                    writers.append(f"{path.relative_to(root)}:{node.lineno}")
    assert writers == []


# ── independent review (ee101668) regressions ──────────────────────────────


def test_an_edit_is_bound_to_the_operation_it_was_estimated_under(owner, fences):
    """Review P1: revoke -> regrant (new generation) never revives an estimate."""
    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=auth_for(owner.id))
    newer = ai_consent.OperationAuthority(owner.id, "nutrition_plan", 5, "v")
    for other in (newer, ai_consent.OperationAuthority(owner.id, "coach", 3, "v"), None):
        with pytest.raises(ai_consent.ConsentDenied):
            npm.apply_prepared_edit(owner.id, prepared, ctx(), authority=other)
    assert fences == [] and row(owner).mutation_version == 0 and journal(owner) == 0

    # A payload whose bound operation was swapped is refused the same way.
    payload = prepared.to_payload()
    payload["authority"] = newer.to_context()
    with pytest.raises(ai_consent.ConsentDenied):
        npm.apply_prepared_edit(owner.id, npm.PreparedMealEdit.from_payload(payload), ctx(),
                                authority=auth_for(owner.id))
    assert journal(owner) == 0


def test_prepare_refuses_an_authority_for_another_account_or_purpose(owner, make_user):
    stranger = make_user("stranger-auth")
    for bad in (auth_for(stranger.id),
                ai_consent.OperationAuthority(owner.id, "coach", 3, "v"), None):
        with pytest.raises(ai_consent.ConsentDenied):
            npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=bad)


def _tampered(prepared, mutate):
    payload = json.loads(json.dumps(prepared.to_payload()))
    mutate(payload)
    return npm.PreparedMealEdit.from_payload(payload)


@pytest.mark.parametrize("mutate", [
    lambda p: p["document"]["ogle"].__setitem__("kalori", 5),           # another meal
    lambda p: p["document"]["kahvalti"].pop("tahmini"),                 # label removed
    lambda p: p["document"].__setitem__("toplam_kalori", 99999),        # totals
    lambda p: p["document"]["kahvalti"].__setitem__("kalori", 900),     # not the estimate
    lambda p: p["document"].__setitem__("isim", "Plan X"),              # outside the meal
    lambda p: p["provenance"]["items"].pop(),                           # items not covered
    lambda p: p.__setitem__("provenance", None),
    lambda p: p.__setitem__("changed", "no"),
    lambda p: p.__setitem__("command_type", "replace_everything"),
], ids=["other-meal", "label", "totals", "meal-values", "name", "items", "no-provenance",
        "changed-type", "command-type"])
def test_a_tampered_stored_edit_is_refused_at_the_write(owner, fences, authority, mutate):
    """Review P2: the write re-checks F-3 against the bound base, not just the schema."""
    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=authority)
    before = row(owner).plan_data
    with pytest.raises(npm.InvalidNutritionEdit):
        npm.apply_prepared_edit(owner.id, _tampered(prepared, mutate), ctx(),
                                authority=authority)
    assert row(owner).plan_data == before and journal(owner) == 0


def test_a_reordered_stored_document_still_replays(owner, fences, authority):
    """Review P3: the replay identity is key-order independent."""
    prepared = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=authority)
    first = npm.apply_prepared_edit(owner.id, prepared, ctx(), authority=authority)
    reordered = _tampered(prepared, lambda p: p.__setitem__(
        "document", dict(reversed(list(p["document"].items())))))
    again = npm.apply_prepared_edit(owner.id, reordered, ctx(), authority=authority)
    assert again.replayed and again.mutation_id == first.mutation_id
    assert journal(owner) == 1


def test_a_no_op_on_a_lineage_less_row_does_not_stale_other_edits(owner, fences, authority):
    """Review P3: assigning a first lineage is not a plan change."""
    plan = row(owner)
    plan.lineage_id = None
    db.session.commit()
    same = npm.ReplaceMealItemCommand("kahvalti", "Yumurta - 3 adet", "Yumurta - 3 adet")
    no_op = npm.prepare_meal_edit(owner.id, same, estimator(), authority=authority)
    real = npm.prepare_meal_edit(owner.id, REPLACE, estimator(), authority=authority)
    npm.apply_prepared_edit(owner.id, no_op, ctx("edit-key-0001"), authority=authority)
    result = npm.apply_prepared_edit(owner.id, real, ctx("edit-key-0002"), authority=authority)
    assert result.outcome == npm.OUTCOME_APPLIED and row(owner).mutation_version == 1


@pytest.mark.parametrize("reply", [
    {"calories": 160, "protein": 8, "carbs": 30},                 # fat missing
    {"calories": 160, "protein": 8, "carbs": "N/A", "fat": 1},    # unparseable
    {"calories": 160, "protein": 8, "carbs": "30 g", "fat": 1},   # unit text
    {"calories": 160, "protein": True, "carbs": 30, "fat": 1},    # boolean
])
def test_a_dropped_or_unparseable_macro_is_no_estimate(app, owner, authority, monkeypatch, reply):
    """Review P2: the plan estimator never turns a missing macro into 0."""
    from app.services import ai_nutrition

    def fake_heavy_chat(**kwargs):
        return json.dumps({"Dana eti - 150 g": TABLE["dana eti - 150 g"],
                           "Tam buğday ekmeği - 2 dilim": reply}, ensure_ascii=False)

    monkeypatch.setattr(ai_nutrition, "_heavy_chat", fake_heavy_chat)
    with app.test_request_context(), health_consent.processing(owner.id), \
            ai_consent.operation_scope(authority):
        with pytest.raises(npm.EstimateUnavailable):
            npm.prepare_meal_edit(owner.id, REPLACE, npm.server_estimator, authority=authority)
    assert row(owner).mutation_version == 0 and journal(owner) == 0


def test_the_legacy_estimator_keeps_its_lenient_parse(app, monkeypatch):
    from app.services import ai_nutrition
    monkeypatch.setattr(ai_nutrition, "_heavy_chat", lambda **kwargs: json.dumps(
        {"Elma": {"calories": "95 kcal", "protein": 0.5, "carbs": 25}}))
    with app.test_request_context("/"):
        assert ai_nutrition._estimate_macros_llm_batch(["Elma"]) == {
            "Elma": {"calories": 95.0, "protein": 0.5, "carbs": 25.0, "fat": 0.0}}
