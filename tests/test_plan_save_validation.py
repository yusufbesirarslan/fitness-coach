"""F2/F3 — the write-side contract of the two plan-save routes.

F3: ``score`` came straight off the wire into a ``db.Float`` column on BOTH
``POST /nutrition-plan/save`` and ``POST /training-plan/save``. A non-numeric
value survived until the flush and surfaced as an unhandled ``DataError`` —
an HTML 500 to a caller that only ever parses JSON.

F2: ``POST /nutrition-plan/save`` accepted arbitrary client JSON with no
schema, ``GET /nutrition-plan/active`` returned it verbatim, and the nutrition
frontend interpolated several of those fields into ``innerHTML``. The CSP
blocks script execution, so this was never active XSS — but stored HTML/CSS
injection and the UI-redress it buys were live.

    python -m pytest tests/test_plan_save_validation.py -q
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import NutritionPlan, TrainingPlan
from tests.test_training_routes import (  # noqa: F401 - fixture import
    _seven_day_program,
    plan_save_token,
)


# ── canonical payloads, as the real clients emit them ────────────────────────

def _meal(items=("Yumurta - 3 adet",), kalori=420):
    return {"yemekler": list(items), "kalori": kalori,
            "protein": 28, "karb": 35, "yag": 18}


def valid_nutrition_plan():
    """Exactly the shape ``selectPlan()`` forwards: one element of ``planlar``."""
    return {
        "isim": "Plan A",
        "kahvalti": _meal(),
        "ogle": _meal(("Tavuk göğsü - 150g", "Pirinç - 100g"), 380),
        "aksam": _meal(("Kırmızı et - 120g",), 450),
        "ara_ogun": _meal(("Yoğurt - 200g",), 227),
        "toplam_kalori": 1477,
        "toplam_protein": 136,
        "toplam_karb": 123,
        "toplam_yag": 44,
    }


def _save_nutrition(client, plan=None, score=8.0):
    body = {"plan": valid_nutrition_plan() if plan is None else plan}
    if score != "__omit__":
        body["score"] = score
    return client.post("/nutrition-plan/save", json=body)


def _save_training(client, token, score=7.0, expected=None):
    return client.post("/training-plan/save", json={
        "plan": _seven_day_program(),
        "score": score,
        "exercise_context_token": token,
        "expected_plan": expected,
    })


class flush_spy:
    """Record every INSERT/UPDATE the database really runs against a table.

    The F3 claim is "an invalid score never reaches DB flush". Watching the
    statements is what makes that evidence rather than an assertion about the
    order of a few lines of route code.
    """

    def __init__(self, table):
        self.table = table.upper()

    def __enter__(self):
        self.statements = []
        self._engine = db.engine
        event.listen(self._engine, "before_cursor_execute", self._record)
        return self.statements

    def __exit__(self, *exc):
        event.remove(self._engine, "before_cursor_execute", self._record)
        return False

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        text = statement.lstrip().upper()
        if text.startswith(("INSERT", "UPDATE")) and self.table in text:
            self.statements.append(statement)


# ── F3: score validation ─────────────────────────────────────────────────────

INVALID_SCORES = [
    pytest.param("abc", id="non-numeric-string"),
    pytest.param("8.0", id="numeric-string"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinity"),
    pytest.param(float("-inf"), id="negative-infinity"),
    pytest.param(-1, id="below-range"),
    pytest.param(10.1, id="above-range"),
    pytest.param(True, id="boolean"),
    pytest.param([8], id="list"),
    pytest.param({"v": 8}, id="object"),
]


class TestNutritionScore:

    @pytest.mark.parametrize("score", INVALID_SCORES)
    def test_invalid_score_is_json_400_and_never_reaches_flush(
            self, client, auth_user, score):
        with flush_spy("nutrition_plan") as writes:
            response = _save_nutrition(client, score=score)
        assert response.status_code == 400
        assert response.is_json, response.get_data(as_text=True)[:200]
        assert response.get_json()["code"] == "plan_score_invalid"
        assert writes == []
        assert NutritionPlan.query.filter_by(user_id=auth_user.id).count() == 0

    def test_an_invalid_score_does_not_destroy_the_existing_plan(
            self, client, auth_user):
        assert _save_nutrition(client, score=8.0).status_code == 200
        assert _save_nutrition(client, score="abc").status_code == 400
        rows = NutritionPlan.query.filter_by(user_id=auth_user.id).all()
        assert len(rows) == 1 and rows[0].score == 8.0

    @pytest.mark.parametrize("score,stored", [
        (8.0, 8.0), (0, 0.0), (10, 10.0), (7, 7.0), (6.5, 6.5),
    ])
    def test_valid_scores_are_unchanged(self, client, auth_user, score, stored):
        assert _save_nutrition(client, score=score).status_code == 200
        assert NutritionPlan.query.filter_by(user_id=auth_user.id).one().score == stored

    def test_an_omitted_score_still_persists_null(self, client, auth_user):
        assert _save_nutrition(client, score="__omit__").status_code == 200
        assert NutritionPlan.query.filter_by(user_id=auth_user.id).one().score is None

    def test_an_explicit_null_score_still_persists_null(self, client, auth_user):
        assert _save_nutrition(client, score=None).status_code == 200
        assert NutritionPlan.query.filter_by(user_id=auth_user.id).one().score is None


class TestTrainingScore:

    @pytest.mark.parametrize("score", INVALID_SCORES)
    def test_invalid_score_is_json_400_and_never_reaches_flush(
            self, client, auth_user, plan_save_token, score):
        token = plan_save_token(auth_user.id)
        with flush_spy("training_plan") as writes:
            response = _save_training(client, token, score=score)
        assert response.status_code == 400
        assert response.is_json, response.get_data(as_text=True)[:200]
        assert response.get_json()["code"] == "plan_score_invalid"
        assert writes == []
        assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0

    def test_the_two_routes_answer_an_invalid_score_identically(
            self, client, auth_user, plan_save_token):
        nutrition = _save_nutrition(client, score="abc")
        training = _save_training(client, plan_save_token(auth_user.id), score="abc")
        assert nutrition.status_code == training.status_code == 400
        assert nutrition.get_json() == training.get_json()

    def test_valid_score_behaviour_is_unchanged(
            self, client, auth_user, plan_save_token):
        token = plan_save_token(auth_user.id)
        assert _save_training(client, token, score=7.0).status_code == 200
        assert TrainingPlan.query.filter_by(user_id=auth_user.id).one().score == 7.0

    def test_an_invalid_score_does_not_destroy_the_existing_plan(
            self, client, auth_user, plan_save_token, plan_expectation):
        token = plan_save_token(auth_user.id)
        assert _save_training(client, token, score=7.0).status_code == 200
        existing = TrainingPlan.query.filter_by(user_id=auth_user.id).one()
        response = _save_training(client, token, score=float("inf"),
                                  expected=plan_expectation(auth_user.id))
        assert response.status_code == 400
        rows = TrainingPlan.query.filter_by(user_id=auth_user.id).all()
        assert len(rows) == 1 and rows[0].id == existing.id


# ── F2: the persisted nutrition-plan document ────────────────────────────────

def _persisted(user_id):
    return json.loads(
        NutritionPlan.query.filter_by(user_id=user_id).one().plan_data)


def _rejects(client, plan):
    response = client.post("/nutrition-plan/save", json={"plan": plan, "score": 8.0})
    assert response.status_code == 400, response.get_data(as_text=True)[:300]
    assert response.is_json
    body = response.get_json()
    assert body["code"] == "nutrition_plan_invalid"
    assert set(body) == {"error", "code", "retryable"}
    return body


class TestNutritionPlanAccepted:
    """What the application really emits must keep working, byte for byte."""

    def test_the_document_the_frontend_emits_round_trips_unchanged(
            self, client, auth_user):
        plan = valid_nutrition_plan()
        assert _save_nutrition(client, plan).status_code == 200
        body = client.get("/nutrition-plan/active").get_json()
        assert body["exists"] is True
        assert body["plan"] == plan
        assert body["score"] == 8.0

    def test_integer_macros_stay_integers(self, client, auth_user):
        """1477 must not come back as 1477.0 — the UI prints it raw."""
        assert _save_nutrition(client).status_code == 200
        stored = _persisted(auth_user.id)
        assert stored["toplam_kalori"] == 1477
        assert isinstance(stored["toplam_kalori"], int)
        assert isinstance(stored["kahvalti"]["kalori"], int)

    def test_a_partial_plan_with_one_meal_is_accepted(self, client, auth_user):
        plan = {"ogle": _meal()}
        assert _save_nutrition(client, plan).status_code == 200
        assert _persisted(auth_user.id) == plan

    def test_a_meal_without_macros_is_accepted(self, client, auth_user):
        plan = {"ogle": {"yemekler": ["Tavuk - 150g"]}}
        assert _save_nutrition(client, plan).status_code == 200

    def test_saving_still_replaces_the_previous_plan(self, client, auth_user):
        first = {"ogle": _meal(("A - 1",))}
        second = {"ogle": _meal(("B - 2",))}
        assert _save_nutrition(client, first).status_code == 200
        assert _save_nutrition(client, second).status_code == 200
        rows = NutritionPlan.query.filter_by(user_id=auth_user.id).all()
        assert len(rows) == 1
        assert json.loads(rows[0].plan_data) == second

    def test_numeric_strings_are_coerced_to_numbers_not_stored_as_text(
            self, client, auth_user):
        """The generator is an LLM; "420" is a benign shape it really emits.

        Coercing is what makes it safe: the persisted value is a number, so the
        unescaped macro slots in the UI can only ever render digits.
        """
        plan = {"ogle": {"yemekler": ["Tavuk - 150g"], "kalori": "420",
                         "protein": "31.5"}}
        assert _save_nutrition(client, plan).status_code == 200
        stored = _persisted(auth_user.id)["ogle"]
        assert stored["kalori"] == 420 and isinstance(stored["kalori"], int)
        assert stored["protein"] == 31.5 and isinstance(stored["protein"], float)


class TestNutritionPlanRejected:

    @pytest.mark.parametrize("plan", [
        pytest.param([{"ogle": {}}], id="list"),
        pytest.param("plan", id="string"),
        pytest.param(42, id="number"),
        pytest.param(True, id="boolean"),
    ])
    def test_a_document_that_is_not_an_object_is_refused(self, client, auth_user, plan):
        _rejects(client, plan)

    def test_an_unknown_top_level_key_is_refused(self, client, auth_user):
        plan = valid_nutrition_plan()
        plan["notlar"] = "<style>body{display:none}</style>"
        _rejects(client, plan)

    def test_a_document_with_no_meal_at_all_is_refused(self, client, auth_user):
        _rejects(client, {"isim": "Plan A", "toplam_kalori": 1477})

    def test_an_empty_meal_object_is_refused(self, client, auth_user):
        _rejects(client, {"ogle": {}})

    def test_a_meal_that_is_not_an_object_is_refused(self, client, auth_user):
        _rejects(client, {"ogle": ["Tavuk - 150g"]})

    def test_an_unknown_key_inside_a_meal_is_refused(self, client, auth_user):
        _rejects(client, {"ogle": {"yemekler": ["A"], "onclick": "x"}})

    @pytest.mark.parametrize("yemekler", [
        pytest.param("Tek string yemek", id="string-not-list"),
        pytest.param({"0": "A"}, id="object-not-list"),
        pytest.param([{"isim": "A"}], id="object-item"),
        pytest.param([None], id="null-item"),
        pytest.param([12], id="numeric-item"),
        pytest.param([""], id="empty-item"),
    ])
    def test_a_malformed_food_list_is_refused(self, client, auth_user, yemekler):
        _rejects(client, {"ogle": {"yemekler": yemekler, "kalori": 380}})

    @pytest.mark.parametrize("value", [
        pytest.param("400 kcal", id="trailing-text"),
        pytest.param("", id="empty-string"),
        pytest.param("1e9", id="exponent"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(-1, id="negative"),
        pytest.param(100001, id="above-bound"),
        pytest.param(None, id="null"),
        pytest.param(True, id="boolean"),
        pytest.param([380], id="list"),
    ])
    def test_a_malformed_macro_is_refused(self, client, auth_user, value):
        _rejects(client, {"ogle": {"yemekler": ["A - 1"], "kalori": value}})
        _rejects(client, {"ogle": _meal(), "toplam_kalori": value})

    @pytest.mark.parametrize("isim", [
        pytest.param(123, id="not-a-string"),
        pytest.param("P" * 121, id="too-long"),
        pytest.param("Plan\x00A", id="nul-byte"),
        pytest.param("Plan\x1bA", id="escape-byte"),
    ])
    def test_a_malformed_name_is_refused(self, client, auth_user, isim):
        _rejects(client, {"isim": isim, "ogle": _meal()})

    def test_an_oversized_food_name_is_refused(self, client, auth_user):
        _rejects(client, {"ogle": {"yemekler": ["y" * 201]}})

    def test_too_many_food_items_are_refused(self, client, auth_user):
        _rejects(client, {"ogle": {"yemekler": ["y%d" % n for n in range(31)]}})

    def test_an_oversized_document_is_refused(self, client, auth_user):
        """Every field inside its own bound, the document still far too large."""
        plan = {key: {"yemekler": ["y" * 200] * 30} for key in
                ("kahvalti", "ogle", "aksam", "ara_ogun")}
        _rejects(client, plan)

    def test_nothing_is_persisted_or_destroyed_by_a_refused_document(
            self, client, auth_user):
        assert _save_nutrition(client).status_code == 200
        with flush_spy("nutrition_plan") as writes:
            _rejects(client, {"ogle": {"yemekler": "x"}})
        assert writes == []
        assert _persisted(auth_user.id) == valid_nutrition_plan()


class TestStoredMarkupInjection:
    """F2's actual payoff, stated as behaviour rather than as a schema detail.

    The CSP already stops script execution. What it does not stop is markup
    and a `style` attribute (`style-src-attr 'unsafe-inline'` is still on, and
    has to be), so a persisted `<div style="position:fixed;inset:0">` is a real
    UI-redress primitive. Two independent controls close it: the macro slots —
    the fields the page interpolates WITHOUT escaping — can only hold numbers,
    and the text slots are escaped on render (proved in
    tests/js/nutrition_plan_render.test.js and by the render contract gate).
    """

    MARKUP = '<div style="position:fixed;inset:0;z-index:9999">owned</div>'

    @pytest.mark.parametrize("field", ["kalori", "protein", "karb", "yag"])
    def test_markup_cannot_be_persisted_in_a_meal_macro(
            self, client, auth_user, field):
        _rejects(client, {"ogle": {"yemekler": ["A - 1"], field: self.MARKUP}})

    @pytest.mark.parametrize("field", [
        "toplam_kalori", "toplam_protein", "toplam_karb", "toplam_yag",
    ])
    def test_markup_cannot_be_persisted_in_a_total(self, client, auth_user, field):
        _rejects(client, {"ogle": _meal(), field: self.MARKUP})

    def test_markup_cannot_be_smuggled_through_an_extra_field(
            self, client, auth_user):
        _rejects(client, {"ogle": _meal(), "style": self.MARKUP})
        _rejects(client, {"ogle": {"yemekler": ["A"], "style": self.MARKUP}})

    def test_markup_in_a_food_name_survives_as_text_and_only_as_text(
            self, client, auth_user):
        """A name is free text, so the schema must NOT pretend to sanitise it.

        It is stored verbatim — escaping is the render-side control, and
        claiming a second one here would be false confidence. What this proves
        is that it stays confined to the two string slots the UI escapes.
        """
        plan = {"isim": self.MARKUP, "ogle": {"yemekler": [self.MARKUP]}}
        assert _save_nutrition(client, plan).status_code == 200
        assert _persisted(auth_user.id) == plan


class TestLegacyRowsStillReadable:
    """The schema guards the WRITE side only.

    Rows written before this boundary existed are still in the database, so the
    read paths must stay exactly as tolerant as they were. A validator that
    retroactively broke `/nutrition-plan/active` would be a worse bug than the
    one it fixes, and would need a migration this change deliberately avoids.
    """

    def _seed(self, user_id, document):
        db.session.add(NutritionPlan(
            user_id=user_id, plan_data=json.dumps(document), score=8.0))
        db.session.commit()

    def test_a_legacy_document_the_schema_would_reject_still_loads(
            self, client, auth_user):
        legacy = {"v": 1, "ogle": {"yemekler": "Tek string", "kalori": "400 kcal"}}
        self._seed(auth_user.id, legacy)
        body = client.get("/nutrition-plan/active").get_json()
        assert body["exists"] is True and body["plan"] == legacy

    def test_a_legacy_document_still_quick_adds_without_a_500(
            self, client, auth_user):
        self._seed(auth_user.id, {"ogle": {"yemekler": "Tek string yemek",
                                           "kalori": "400 kcal", "karb": 30}})
        response = client.post("/api/quick-add-meal", json={"meal_key": "ogle"})
        assert response.status_code == 200
        assert response.get_json()["nutrients"]["kalori"] == 0


# ── F2: the render contract, enforced from CI ────────────────────────────────

NUTRITION_JS = Path(__file__).resolve().parents[1] / "static" / "nutrition.js"

# The three functions that interpolate a stored or generated plan into innerHTML.
PLAN_RENDERERS = (
    "function renderPlans(data)",
    "function renderActivePlanDetail(plan, score, createdAt)",
    "async function loadQuickAddSection()",
)

# Identifiers that carry server/provider data on this page. An interpolation
# naming one of these has to go through esc() (free text) or fmtNum() (numeric).
DATA_IDENTIFIERS = re.compile(r"\b(plan|ml|data|score|createdAt|d|y)\b")

# Bare locals that are provably not plan data: loop indices, the fixed meal
# descriptor array, the fixed colour map, and fragments already assembled from
# interpolations this same gate checks.
SAFE_LOCALS = frozenset({
    "i", "color", "m.label", "m.icon", "m.key", "mealsHtml", "items", "sub",
})


def _read_nutrition_js():
    return NUTRITION_JS.read_text(encoding="utf-8").replace("\r\n", "\n")


def _function_body(source, signature):
    """The balanced { ... } block of one function declaration."""
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening:index + 1]
    raise AssertionError("unbalanced body for %s" % signature)


def _interpolations(text):
    """Every `${ ... }` in a block, brace-balanced so nested ones survive."""
    found = []
    for match in re.finditer(r"\$\{", text):
        depth, index = 1, match.end()
        while index < len(text):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        found.append(text[match.end():index].strip())
    return found


def _strip_guarded(expression):
    """Remove every esc(...) / fmtNum(...) call, arguments and all."""
    out, index = "", 0
    while index < len(expression):
        for guard in ("esc(", "fmtNum("):
            if expression.startswith(guard, index):
                depth, scan = 1, index + len(guard)
                while scan < len(expression) and depth:
                    if expression[scan] == "(":
                        depth += 1
                    elif expression[scan] == ")":
                        depth -= 1
                    scan += 1
                index = scan
                break
        else:
            out += expression[index]
            index += 1
    return out


class TestNutritionRenderContract:
    """A CI-visible gate on the property the node suite proves behaviourally.

    `pytest -q` is the only thing CI runs on every PR, and tests/js/ is not part
    of it. Without this, the frontend half of F2 would be guarded only by a
    suite nothing enforces — so the rule is restated here as structure: every
    interpolation into a plan template is enumerated, and each one either
    carries no plan data or passes through esc()/fmtNum().
    """

    def test_fmtnum_exists_and_only_lets_numbers_through(self):
        source = _read_nutrition_js()
        assert "function fmtNum(" in source
        body = _function_body(source, "function fmtNum(")
        assert "Number.isFinite" in body
        assert re.search(r"/\^-\?\\d\+", body), "no strict decimal guard"

    @pytest.mark.parametrize("signature", PLAN_RENDERERS)
    def test_every_plan_interpolation_is_escaped_or_numeric(self, signature):
        body = _function_body(_read_nutrition_js(), signature)
        expressions = _interpolations(body)
        assert expressions, "no interpolations found — did the function move?"
        for expression in expressions:
            if "`" in expression:
                # A composite that builds its own template; the interpolations
                # inside it are scanned as their own entries.
                continue
            if expression in SAFE_LOCALS:
                continue
            residue = _strip_guarded(expression)
            assert not DATA_IDENTIFIERS.search(residue), (
                "%s interpolates plan data unguarded: %s" % (signature, expression))

    @pytest.mark.parametrize("signature", PLAN_RENDERERS)
    def test_the_set_of_interpolations_is_pinned(self, signature):
        """An exact set, so a NEW slot cannot arrive unreviewed.

        The rule above passes anything that merely avoids the known data names;
        this makes adding a slot a deliberate edit to a reviewed list.
        """
        body = _function_body(_read_nutrition_js(), signature)
        assert set(_interpolations(body)) == EXPECTED_INTERPOLATIONS[signature]

    def test_the_action_argument_attribute_escapes_ampersands_too(self):
        """The old `.replace(/"/g,'&quot;')` left `&` and `<` alone.

        `<` rode into the attribute verbatim and a stored `&quot;` corrupted the
        JSON actions.js parses back out. esc() covers both and round-trips
        through the HTML parser.
        """
        source = _read_nutrition_js()
        assert "esc(JSON.stringify([i, plan, data.overall_score]))" in source
        # Scoped to this slot on purpose: the food-autocomplete attributes use
        # their own escaper, and that one already replaces `&` FIRST, so it is
        # correct and out of F2's scope.
        assert "data.overall_score]).replace(" not in source


EXPECTED_INTERPOLATIONS = {
    "function renderPlans(data)": frozenset({
        "(ml.yemekler || []).map(y => `<li>${esc(y)}</li>`).join('')",
        "__t('nutrition.carb_short')",
        "__t('nutrition.macro_fat')",
        "__t('nutrition.macro_protein')",
        "__t('nutrition.plan_word')",
        "__t('nutrition.score_desc')",
        "__t('nutrition.select_plan')",
        "color",
        "esc(JSON.stringify([i, plan, data.overall_score]))",
        "esc(plan.isim ?? 'Plan ' + (i+1))",
        "esc(scoreLabel(data.score_label))",
        "esc(y)",
        "fmtNum(data.overall_score)",
        "fmtNum(ml.kalori)",
        "fmtNum(plan.toplam_kalori)",
        "fmtNum(plan.toplam_karb)",
        "fmtNum(plan.toplam_protein)",
        "fmtNum(plan.toplam_yag)",
        "i",
        "m.label",
        "mealsHtml",
    }),
    "function renderActivePlanDetail(plan, score, createdAt)": frozenset({
        "__t('nutrition.carb_short')",
        "__t('nutrition.macro_fat')",
        "__t('nutrition.macro_protein')",
        "__t('nutrition.new_plan')",
        "__t('nutrition.score_text')",
        "esc(createdAt)",
        "esc(plan.isim || __t('nutrition.active_plan_name'))",
        "esc(y)",
        "fmtNum(ml.kalori)",
        "fmtNum(plan.toplam_kalori)",
        "fmtNum(plan.toplam_karb)",
        "fmtNum(plan.toplam_protein)",
        "fmtNum(plan.toplam_yag)",
        "fmtNum(score)",
        "items",
        "m.icon",
        "m.label",
        "mealsHtml",
    }),
    "async function loadQuickAddSection()": frozenset({
        "__t('nutrition.no_active_plan')",
        "__t('nutrition.unit_carb')",
        "__t('nutrition.unit_protein')",
        "esc(d.plan.isim || 'Aktif Plan')",
        "fmtNum(ml.kalori)",
        "fmtNum(ml.karb)",
        "fmtNum(ml.protein)",
        "m.icon",
        "m.key",
        "m.label",
        "sub",
    }),
}


def test_the_behavioural_render_suite_passes():
    """Run tests/js/nutrition_plan_render.test.js, the behavioural proof.

    The gate above reads the source; this runs it. Skipped only where node is
    genuinely absent — the structural gate still holds there, so the property is
    never left ungated.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the structural gate above still runs")
    suite = Path(__file__).resolve().parents[1] / "tests" / "js" / "nutrition_plan_render.test.js"
    result = subprocess.run(
        [node, "--test", str(suite)],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
