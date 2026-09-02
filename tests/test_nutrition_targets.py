"""Sprint 13 PR2 — the canonical daily macro-target authority (decision C2).

PR1 characterized the defect (`tests/test_sprint13_nutrition_closure_discovery.py`);
this module pins the correction. Three things are proven here and each is a
different kind of claim:

1. **The formula** — one arithmetic definition, stated once as a hand-computed
   fixture rather than re-spelled per consumer, so a test cannot pass merely by
   mirroring whatever the implementation happens to do.
2. **Convergence** — coach, menu and barcode derive *that* value for the same
   user on the same day. Behavioural, not textual.
3. **The architecture** — no second server derivation can come back unnoticed.
   Proven structurally (AST + imports + a purity check on the module's own
   dependencies), not by grepping for a sentence.

PR5 closed the browser half of N4: first-party surfaces now consume this
module's derivation (or present absence) and invent no competing split.

    python -m pytest tests/test_nutrition_targets.py -v
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import MealLog, UserSession
from app.services.nutrition_targets import (
    DailyMacros,
    derive_daily_macro_targets,
    macro_calorie_ratios,
    remaining_macro_budget,
)
from app.timeutil import day_key

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_MODULE = "app/services/nutrition_targets.py"

# ---------------------------------------------------------------------------
# The canonical expectation, computed by hand, once.
#
# 2000 kcal is the figure PR1's evidence is stated in, so the correction stays
# comparable to the report. Muscle gain takes 30/45/25 of calories, every other
# configured goal state takes 25/50/25, and grams are 4/4/9 kcal.
#
#   muscle gain : 2000*.30/4 = 150 P | 2000*.45/4 = 225 C | 2000*.25/9 = 55.5…
#   otherwise   : 2000*.25/4 = 125 P | 2000*.50/4 = 250 C | 2000*.25/9 = 55.5…
#
# The carbohydrate row is the whole of F2: barcode used to publish 225 for the
# second case as well.
# ---------------------------------------------------------------------------
TARGET_KCAL = 2000.0
FAT_G = 55.5556          # 2000 * 0.25 / 9, to four decimals
TOL = 5e-4               # far tighter than any rounding a consumer applies

MUSCLE_GAIN_TARGET = {
    "calories": 2000.0, "protein": 150.0, "carbs": 225.0, "fat": FAT_G,
}
NON_MUSCLE_GAIN_TARGET = {
    "calories": 2000.0, "protein": 125.0, "carbs": 250.0, "fat": FAT_G,
}

# `profile.py:135` permits exactly these three stored goal values.
STORED_GOALS_WITHOUT_MUSCLE_GAIN = ("kilo verme", "")


def _as_dict(macros):
    """Canonical macro set as a mapping, rounded well below any consumer's
    published precision so the fixtures above can be written by hand."""
    raw = macros.as_dict() if isinstance(macros, DailyMacros) else dict(macros)
    return {key: round(value, 4) for key, value in raw.items()}


def _module_ast(relative):
    return ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. The formula
# ---------------------------------------------------------------------------

def test_muscle_gain_takes_the_higher_protein_and_lower_carbohydrate_split():
    assert _as_dict(derive_daily_macro_targets(TARGET_KCAL, "kas kazanma")) == \
        MUSCLE_GAIN_TARGET


@pytest.mark.parametrize("goal", STORED_GOALS_WITHOUT_MUSCLE_GAIN)
def test_every_other_configured_goal_state_takes_the_default_split(goal):
    """Both remaining stored goal values agree — including the empty one.

    An unset goal is not an unset *target*: the user configured a calorie
    target, and the default split is a real answer for them.
    """
    assert _as_dict(derive_daily_macro_targets(TARGET_KCAL, goal)) == \
        NON_MUSCLE_GAIN_TARGET


def test_an_unrecognised_goal_string_is_not_treated_as_muscle_gain():
    """No fuzzy classification here. `barcode._goal_key` does that for
    recommendation messaging and must not become the target authority (§7)."""
    assert _as_dict(derive_daily_macro_targets(TARGET_KCAL, "bulk")) == \
        NON_MUSCLE_GAIN_TARGET
    assert _as_dict(derive_daily_macro_targets(TARGET_KCAL, "KAS KAZANMA")) == \
        NON_MUSCLE_GAIN_TARGET


def test_the_split_is_proportional_to_the_configured_target():
    half = derive_daily_macro_targets(TARGET_KCAL / 2, "kilo verme")
    assert half.protein == pytest.approx(NON_MUSCLE_GAIN_TARGET["protein"] / 2)
    assert half.carbs == pytest.approx(NON_MUSCLE_GAIN_TARGET["carbs"] / 2)


def test_the_ratios_are_a_complete_calorie_split():
    for goal in ("kas kazanma", "kilo verme", ""):
        ratios = macro_calorie_ratios(goal)
        assert ratios.protein + ratios.carbs + ratios.fat == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 2. Absence — F3a's correction, at the source
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("unset", [None, 0, 0.0, "", "not a number", -1500])
def test_an_unconfigured_target_derives_no_target_at_all(unset):
    """Absence, not 2000, not zeros, not an estimate.

    Zero and negative kilocalories are not targets anyone configured, so they
    normalise to absence exactly as the mobile goal boundary already does. The
    three call sites this module replaces all treated `0` and `None` alike;
    only barcode turned that into a number.
    """
    assert derive_daily_macro_targets(unset, "kas kazanma") is None


def test_no_fallback_calorie_number_is_reachable_from_the_authority():
    """The specific fabrication F3a names: 2000 kcal out of nothing."""
    for goal in ("kas kazanma", "kilo verme", "", None):
        assert derive_daily_macro_targets(None, goal) is None


# ---------------------------------------------------------------------------
# 3. Remaining budget
# ---------------------------------------------------------------------------

def test_remaining_is_the_target_minus_what_was_eaten():
    targets = derive_daily_macro_targets(TARGET_KCAL, "kilo verme")
    remaining = remaining_macro_budget(targets, {
        "calories": 500, "protein": 25, "carbs": 50, "fat": 5.5,
    })
    assert remaining.calories == pytest.approx(1500.0)
    assert remaining.protein == pytest.approx(100.0)
    assert remaining.carbs == pytest.approx(200.0)
    assert remaining.fat == pytest.approx(50.0556, abs=TOL)


def test_remaining_never_goes_negative():
    targets = derive_daily_macro_targets(TARGET_KCAL, "kilo verme")
    remaining = remaining_macro_budget(targets, {
        "calories": 9000, "protein": 900, "carbs": 900, "fat": 900,
    })
    assert _as_dict(remaining) == {
        "calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0,
    }


def test_an_absent_consumption_reading_spends_nothing():
    targets = derive_daily_macro_targets(TARGET_KCAL, "kilo verme")
    assert _as_dict(remaining_macro_budget(targets, None)) == \
        NON_MUSCLE_GAIN_TARGET
    assert _as_dict(remaining_macro_budget(targets, {})) == \
        NON_MUSCLE_GAIN_TARGET


def test_there_is_no_remaining_budget_without_a_configured_target():
    """`None` in, `None` out — the alternative is re-inventing F3a one layer up."""
    assert remaining_macro_budget(None, {"calories": 500}) is None
    assert remaining_macro_budget(None, None) is None


# ---------------------------------------------------------------------------
# 4. Cross-consumer parity — the same user, the same day, one answer
# ---------------------------------------------------------------------------

MENU_TEXT = "Çorbalar: Mercimek Çorbası... Ana Yemekler: Izgara Tavuk"


@pytest.fixture
def cutting_user(auth_user):
    """A configured non-muscle-gain user — the case where coach and barcode
    used to disagree by 25 g of carbohydrate."""
    db.session.add(UserSession(user_id=auth_user.id, goal="kilo verme",
                              target_calories=TARGET_KCAL))
    db.session.commit()
    return auth_user


def test_coach_menu_and_barcode_derive_one_target(
        client, cutting_user, monkeypatch):
    """Behavioural convergence, compared against the canonical fixture rather
    than against each other — three surfaces agreeing on a wrong number would
    still be a failure."""
    from app.blueprints import menu as menu_bp
    from app.services import ai_coach, barcode

    monkeypatch.setattr(menu_bp, "_extract_categorized_items",
                        lambda *a, **kw: {"Ana Yemekler": ["Izgara Tavuk"]})
    monkeypatch.setattr(menu_bp, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(
        menu_bp, "_lookup_macros_fatsecret",
        lambda items, token, cmap=None: (
            {"Izgara Tavuk": {"calories": 330.0, "protein": 62.0,
                              "carbs": 0.0, "fat": 7.0}}, {}))
    monkeypatch.setattr(menu_bp, "_estimate_macros_llm",
                        lambda items, category_map=None: {})

    # Nothing eaten, so "remaining" and "target" are the same number and the
    # coach's budget is directly comparable to the other two.
    coach = ai_coach._remaining_macros_for_user(cutting_user.id)
    barcode_targets = barcode.get_user_barcode_context(cutting_user.id)["targets"]
    menu_target = client.post(
        "/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()["target"]

    for key, expected in NON_MUSCLE_GAIN_TARGET.items():
        assert coach[key] == pytest.approx(expected, abs=TOL), key
        # Each surface keeps its own published precision; only the underlying
        # domain value has to be one value.
        assert barcode_targets[key] == pytest.approx(expected, abs=0.05), key
        assert menu_target[key] == pytest.approx(expected, abs=0.5), key


def test_the_consumers_share_the_remaining_budget_interpretation(
        client, cutting_user, monkeypatch):
    """Same day, same 500 kcal eaten, one remaining figure."""
    from app.blueprints import menu as menu_bp
    from app.services import ai_coach, barcode

    db.session.add(MealLog(
        user_id=cutting_user.id, ogun="Kahvaltı", yemekler="x",
        kalori=500, protein=25, karb=50, yag=5.5, tarih=day_key()))
    db.session.commit()

    monkeypatch.setattr(menu_bp, "_extract_categorized_items",
                        lambda *a, **kw: {"Ana Yemekler": ["Izgara Tavuk"]})
    monkeypatch.setattr(menu_bp, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(menu_bp, "_lookup_macros_fatsecret",
                        lambda items, token, cmap=None: ({}, {}))
    monkeypatch.setattr(menu_bp, "_estimate_macros_llm",
                        lambda items, category_map=None: {})

    expected = {"calories": 1500.0, "protein": 100.0, "carbs": 200.0,
                "fat": 50.0556}

    coach = ai_coach._remaining_macros_for_user(cutting_user.id)
    barcode_remaining = barcode.get_user_barcode_context(
        cutting_user.id)["remaining"]
    menu_remaining = client.post(
        "/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()["remaining"]

    for key, value in expected.items():
        assert coach[key] == pytest.approx(value, abs=TOL), key
        assert barcode_remaining[key] == pytest.approx(value, abs=0.05), key
        assert menu_remaining[key] == pytest.approx(value, abs=0.5), key


def test_the_coach_still_reports_no_budget_for_an_unconfigured_user(
        app, make_user):
    """Coach's truthful behaviour predates PR2 and survives it."""
    from app.services import ai_coach

    user = make_user("s13-pr2-no-target")
    assert ai_coach._remaining_macros_for_user(user.id) is None

    db.session.add(UserSession(user_id=user.id, goal="kilo verme",
                               target_calories=None))
    db.session.commit()
    assert ai_coach._remaining_macros_for_user(user.id) is None


def test_menu_still_answers_its_profile_data_missing_error(client, auth_user):
    """Absence is translated into the endpoint's *existing* contract, not into
    a new one and not into a fabricated target."""
    response = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT})
    assert response.status_code == 400
    assert "error" in response.get_json()


# ---------------------------------------------------------------------------
# 5. The barcode correction — F2 and F3a on the live route
# ---------------------------------------------------------------------------

def test_barcode_publishes_the_corrected_carbohydrate_target(cutting_user):
    """The intentional change: 250 g, not the 225 g barcode used to publish for
    every non-muscle-gain user."""
    from app.services import barcode

    targets = barcode.get_user_barcode_context(cutting_user.id)["targets"]
    assert targets["carbs"] == pytest.approx(250.0)
    assert targets["carbs"] != pytest.approx(225.0)


def test_barcode_agrees_with_itself_on_muscle_gain(app, make_user):
    """The one goal where barcode was already right stays right."""
    from app.services import barcode

    user = make_user("s13-pr2-bulk")
    db.session.add(UserSession(user_id=user.id, goal="kas kazanma",
                               target_calories=TARGET_KCAL))
    db.session.commit()

    targets = barcode.get_user_barcode_context(user.id)["targets"]
    assert targets["carbs"] == pytest.approx(225.0)
    assert targets["protein"] == pytest.approx(150.0)


def test_barcode_context_publishes_absence_rather_than_a_fabricated_goal(
        app, make_user):
    """F3a closed on the live lookup path.

    No `2000`, no zero-filled target that reads as "a goal of nothing", and no
    crash in the fields that hang off the target.
    """
    from app.services import barcode

    user = make_user("s13-pr2-barcode-absent")
    context = barcode.get_user_barcode_context(user.id, meal_time="post_workout")

    assert context["targets"] is None
    assert context["remaining"] is None
    assert context["daily_progress"] is None
    # Everything that does not depend on a configured target still answers.
    assert context["consumed"] == {
        "calories": 0, "protein": 0, "carbs": 0, "fat": 0,
    }
    assert context["meal_time"] == "post_workout"
    assert context["goal"] == "maintain"
    assert context["workout_completed_today"] is False


def test_a_stored_session_without_a_target_is_still_absence(app, make_user):
    from app.services import barcode

    user = make_user("s13-pr2-barcode-null-target")
    db.session.add(UserSession(user_id=user.id, goal="kas kazanma",
                               target_calories=None))
    db.session.commit()

    assert barcode.get_user_barcode_context(user.id)["targets"] is None


def test_barcode_recommendations_degrade_to_a_target_independent_answer(
        app, make_user):
    """§13: no downstream helper may assume `targets` is a mapping."""
    from app.services import barcode

    user = make_user("s13-pr2-barcode-recommend")
    context = barcode.get_user_barcode_context(user.id)
    food = barcode.normalize_food_model("5000159407236", {
        "food_id": "77777", "name": "Acme Protein Bar", "brand": "Acme",
        "servings": [{
            "serving_id": "bar-1", "serving_description": "1 bar",
            "metric_serving_amount": 60, "metric_serving_unit": "g",
            "calories": 210, "protein": 21, "carbs": 23, "fat": 6,
            "fiber": 7, "sugar": 14, "sodium": 420,
        }],
    })

    out = barcode.recommend_for_food(food, context)

    # A portion recommendation without a budget falls back to its declared
    # default rather than guessing at one.
    assert out["portion"] == {
        "servings": 1.0, "basis": "default", "label": "1 serving"}
    # The protein-completion message is a claim about a target; with no target
    # it must not be made.
    assert "Your protein target is nearly complete." not in out["messages"]
    # Target-independent analysis is unaffected.
    assert "This food is high in sugar." in out["messages"]

    payload = barcode.build_lookup_response({"source": "cache", "food": food},
                                            context)
    assert payload["daily_context"]["targets"] is None
    assert payload["analysis"]["axisai_food_score"] >= 1


def test_goal_impact_reports_absence_instead_of_a_fabricated_target(
        app, make_user):
    from app.services import barcode

    user = make_user("s13-pr2-goal-impact")
    impact = barcode.goal_impact_for_add(user.id, {
        "calories": 210.0, "protein": 21.0, "carbs": 23.0, "fat": 6.0})

    assert impact["targets"] is None
    # The add itself is not a target question and still answers fully.
    assert impact["before"] == {
        "calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    assert impact["after"]["protein"] == pytest.approx(21.0)


def test_the_barcode_goal_classifier_was_not_changed():
    """§7/§27: the display classifier is a different question and keeps its own
    fuzzy vocabulary. Only the *target* moved."""
    from app.services import barcode

    assert barcode._goal_key("kas kazanma") == "bulk"
    assert barcode._goal_key("Bulking season") == "bulk"
    assert barcode._goal_key("kilo verme") == "cut"
    assert barcode._goal_key("") == "maintain"
    assert barcode._goal_key(None) == "maintain"


# ---------------------------------------------------------------------------
# 6. F3b stays out of it
# ---------------------------------------------------------------------------

def test_the_review_route_publishes_absence_but_keeps_its_prompt_fallback(
        client, auth_user, monkeypatch):
    """F3b and F3a, kept apart — with one correction to PR1's framing.

    PR1 classified `/meal-log/review`'s `2000` as benign because it is "an
    internal fallback inside an LLM prompt that produces qualitative text" and
    "publishes no number as a configured target". The first half is true. The
    second half was not: the route's JSON response carried
    `"target": round(target)`, so a user who had configured nothing received a
    fabricated 2000 kcal *configured target* on a live payload — F3a's exact
    pattern on a second route (PR2 brief §15 authorises the correction on
    exactly this evidence).

    So PR2 splits them where the difference actually is:

    * the **published field** now answers with the canonical authority, and
      with `null` when there is no configured target;
    * the **prompt fallback** — the qualitative half PR1 correctly called
      benign — is untouched, and is deliberately NOT routed through the
      canonical authority, because promoting it would turn a coaching-text
      default into a published target.
    """
    from app.blueprints.nutrition import meallog as nutrition_meallog

    monkeypatch.setattr(nutrition_meallog, "_openai_chat",
                        lambda **kw: "Dengeli görünüyor.")
    client.post("/meal-log", json={
        "ogun": "Kahvaltı", "yemekler": "yulaf",
        "override_macros": {"kalori": 400, "protein": 20, "karb": 50, "yag": 10},
    })

    # No UserSession at all: nothing was ever configured.
    body = client.post("/meal-log/review", json={}).get_json()
    assert body["target"] is None, (
        "The review payload published a fabricated configured target again.")
    assert body["total_calories"] == 400   # measured, and unaffected
    assert body["review"] == "Dengeli görünüyor."

    # The prompt fallback stays a local default, not a canonical target.
    source = (REPO_ROOT / "app/blueprints/nutrition/meallog.py").read_text(
        encoding="utf-8")
    prompt_fallback = [
        line for line in source.splitlines()
        if "2000" in line and "last_session" in line
    ]
    assert prompt_fallback, (
        "The qualitative prompt fallback (F3b) was removed. That may well be "
        "right, but it is a separate decision from F3a — record it rather "
        "than letting the two findings merge.")


# ---------------------------------------------------------------------------
# 7. Architecture guard — one authority, provable structurally
# ---------------------------------------------------------------------------

# The formula's *shape*, not its spelling: a function that both names one of the
# canonical calorie ratios and divides by an Atwater factor is deriving a macro
# split, whatever it calls its variables.
MACRO_RATIO_LITERALS = {0.30, 0.25, 0.45, 0.50}
ATWATER_DIVISORS = {4, 9, 4.0, 9.0}

# Semantically different calculations are allowed to carry nutrition ratios
# (PR1 §C2 — a weekly protein goal is a different question from a daily target).
# The entry that matters is that this list is *empty* today: analytics consumes
# the canonical ratio instead of restating it, so nothing needs an exemption.
# A new entry here is a decision someone has to write down, not a silent pass.
SEMANTICALLY_DIFFERENT_DERIVATIONS: dict[str, str] = {}


def _server_modules():
    for root in ("app", "fitx_mcp"):
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            yield path.relative_to(REPO_ROOT).as_posix()


def _functions_deriving_a_macro_split(relative):
    tree = _module_ast(relative)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names_a_ratio = any(
            isinstance(n, ast.Constant) and isinstance(n.value, float)
            and n.value in MACRO_RATIO_LITERALS
            for n in ast.walk(node))
        divides_by_an_atwater_factor = any(
            isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
            and isinstance(n.right, ast.Constant)
            and n.right.value in ATWATER_DIVISORS
            for n in ast.walk(node))
        if names_a_ratio and divides_by_an_atwater_factor:
            found.append(f"{relative}::{node.name}:{node.lineno}")
    return found


def test_exactly_one_module_derives_a_daily_macro_split():
    """The single-authority invariant, as structure rather than as a string.

    PR1 found this shape in five functions across four modules. After PR2 the
    only place a calorie target becomes macro grams is the canonical module —
    and it does not even match this pattern, because its ratios are named
    constants. So the expected result is *no* matches anywhere.
    """
    offenders = []
    for relative in _server_modules():
        for hit in _functions_deriving_a_macro_split(relative):
            if hit.split("::")[0] in SEMANTICALLY_DIFFERENT_DERIVATIONS:
                continue
            offenders.append(hit)

    assert offenders == [], (
        "A daily macro split is being derived outside "
        f"{CANONICAL_MODULE}: {offenders}. Consume "
        "`derive_daily_macro_targets`, or — if this genuinely answers a "
        "different question — record it in "
        "SEMANTICALLY_DIFFERENT_DERIVATIONS with the reason.")


def test_every_converged_consumer_imports_the_canonical_authority():
    """The positive half of the invariant: delegation, not just absence.

    Deleting the formula and hard-coding `carbs = 250` would pass the negative
    check above. It fails here.
    """
    consumers = (
        "app/services/ai_coach.py",
        "app/blueprints/menu.py",
        "app/services/barcode.py",
        "app/services/analytics_engine.py",
        "fitx_mcp/server.py",
        "app/blueprints/nutrition/meallog.py",
    )
    for relative in consumers:
        imported = {
            alias.name
            for node in ast.walk(_module_ast(relative))
            if isinstance(node, ast.ImportFrom) and node.module
            and node.module.endswith("nutrition_targets")
            for alias in node.names
        }
        assert imported, (
            f"{relative} publishes a daily macro figure but no longer imports "
            "the canonical authority.")


def test_the_canonical_authority_stays_pure():
    """No Flask, no ORM, no transport — the property that lets a standalone
    process (`fitx_mcp`) and a request handler share one definition."""
    forbidden = ("flask", "sqlalchemy", "app.models", "app.extensions",
                 "app.blueprints", "app.timeutil", "requests", "boto3")
    imports = set()
    for node in ast.walk(_module_ast(CANONICAL_MODULE)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    for name in sorted(imports):
        assert not any(name == bad or name.startswith(bad + ".")
                       for bad in forbidden), (
            f"{CANONICAL_MODULE} imported {name}; the authority must consume "
            "values, never fetch or serialize them.")


def test_the_authority_reaches_no_database_and_no_request_state():
    """A runtime companion to the import check: the pure functions answer with
    no application context at all."""
    targets = derive_daily_macro_targets(TARGET_KCAL, "kilo verme")
    assert _as_dict(remaining_macro_budget(targets, {"calories": 500})) == {
        "calories": 1500.0, "protein": 125.0, "carbs": 250.0, "fat": FAT_G,
    }


def test_the_authority_consumes_values_not_users():
    """It takes a number and a string. Handing it a model-ish object is a type
    error, not a silent database read."""
    assert derive_daily_macro_targets(
        SimpleNamespace(target_calories=2000), "kas kazanma") is None


def test_pr5_retired_the_browser_half_of_n4():
    """The competing 30/40/30 split is gone; the browser consumes this module."""
    nutrition_js = (REPO_ROOT / "static" / "nutrition.js").read_text(
        encoding="utf-8", errors="ignore")
    assert "0.40 / 4" not in nutrition_js
    assert "0.30 / 4" not in nutrition_js
