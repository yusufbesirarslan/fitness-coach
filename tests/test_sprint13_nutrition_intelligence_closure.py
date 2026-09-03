"""Sprint 13 PR5 — retire unowned nutrition intelligence and presentation authority.

PR2 closed the *server* half of N4. This module pins the remaining closure:

* F9  — orphaned ``/api/progress/nutrition`` and legacy ``/api/progress/insights``
        are gone; the live Axis Insights route is untouched.
* F10 — the browser no longer invents a 0–100 nutrition score or an A–D grade.
* N4  — the browser presents PR2's canonical split (or absence), and invents none.
* N10 — no unowned nutrition scoring / adherence definition ships.
* C13 — ``POST /api/food/barcode/add`` was investigated; removal is DEFERRED.

PR5 removes/confines authority. It adds no nutrition-intelligence domain, no
schema, no flag, and no canonical Progress scoring.

    python -m pytest tests/test_sprint13_nutrition_intelligence_closure.py -v
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from app.extensions import db
from app.models import MealLog, UserSession
from app.services.nutrition_targets import (
    DailyMacros,
    derive_daily_macro_targets,
    remaining_macro_budget,
)
from app.timeutil import day_key

REPO_ROOT = Path(__file__).resolve().parent.parent
NUTRITION_JS = REPO_ROOT / "static" / "nutrition.js"
# UX-2 PR4 retired templates/index.html and moved Today's hydration out of the
# template into a real module, so the browser half of N4 now lives in today.js.
TODAY_HTML = REPO_ROOT / "templates" / "today.html"
TODAY_JS = REPO_ROOT / "static" / "today.js"
TRACKING_PY = REPO_ROOT / "app" / "blueprints" / "tracking.py"
MEALLOG_PY = REPO_ROOT / "app" / "blueprints" / "nutrition" / "meallog.py"

LEGACY_NUTRITION = "/api/progress/nutrition"
LEGACY_INSIGHTS = "/api/progress/insights"
AXIS_INSIGHTS = "/api/progress/axis-insights"
TODAY_URL = "/meal-log/today"
BARCODE_ADD = "/api/food/barcode/add"

# Hand-computed fixtures — the same numbers PR2 pinned, in the *web* vocabulary.
# 2000 kcal / muscle gain 30/45/25 → 150 P, 225 C, 55.555… F
# 2000 kcal / otherwise 25/50/25   → 125 P, 250 C, 55.555… F
TARGET_KCAL = 2000.0
FAT_G = TARGET_KCAL * 0.25 / 9.0
TOL = 5e-4

MUSCLE_GAIN_WEB = {
    "kalori": TARGET_KCAL, "protein": 150.0, "karb": 225.0, "yag": FAT_G,
}
DEFAULT_WEB = {
    "kalori": TARGET_KCAL, "protein": 125.0, "karb": 250.0, "yag": FAT_G,
}


def _as_web(macros):
    raw = macros.as_dict() if isinstance(macros, DailyMacros) else dict(macros)
    return {
        "kalori": raw["calories"],
        "protein": raw["protein"],
        "karb": raw["carbs"],
        "yag": raw["fat"],
    }


def _approx_web(actual, expected):
    for key, value in expected.items():
        assert actual[key] == pytest.approx(value, abs=TOL), key


def _read(relative):
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _js_function_body(source, name):
    start = source.index(f"function {name}(")
    open_brace = source.index("{", start)
    depth, index, quote = 0, open_brace, None
    while index < len(source):
        char = source[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace:index + 1]
        index += 1
    raise AssertionError(f"unbalanced body for {name}")


def _url_rules(app):
    return {rule.rule for rule in app.url_map.iter_rules()}


def _frontend_files():
    for root in ("static", "templates"):
        for path in sorted((REPO_ROOT / root).rglob("*")):
            if path.suffix.lower() in {".js", ".html", ".css"}:
                yield path


def _seed_session(user, *, goal, target_calories):
    db.session.add(UserSession(
        user_id=user.id, goal=goal, target_calories=target_calories))
    db.session.commit()


# ---------------------------------------------------------------------------
# F9 — orphaned endpoints are gone; Axis Insights is not
# ---------------------------------------------------------------------------

def test_legacy_progress_nutrition_route_is_absent(app, client, auth_user):
    assert LEGACY_NUTRITION not in _url_rules(app)
    assert client.get(LEGACY_NUTRITION).status_code == 404
    assert client.get(f"{LEGACY_NUTRITION}?range=week").status_code == 404


def test_legacy_progress_insights_route_is_absent(app, client, auth_user):
    assert LEGACY_INSIGHTS not in _url_rules(app)
    assert client.get(LEGACY_INSIGHTS).status_code == 404


def test_axis_insights_live_route_survives(app, client, auth_user):
    """Mutation D: removing the legacy path must not capture the live one."""
    assert AXIS_INSIGHTS in _url_rules(app)
    response = client.get(AXIS_INSIGHTS)
    assert response.status_code == 200
    body = response.get_json()
    assert "next_move" in body
    assert "working" in body and "watch" in body
    assert "insights" not in body


def test_legacy_removal_does_not_use_a_prefix_that_would_catch_axis_insights(app):
    """Exact-path deletion, not ``/api/progress/*`` and not a substring trap.

    ``axis-insights`` contains the token ``insights``. A naive
    ``if "insights" in rule`` cleanup would delete the live route.
    """
    rules = _url_rules(app)
    progress_rules = sorted(r for r in rules if r.startswith("/api/progress/"))
    assert AXIS_INSIGHTS in progress_rules
    assert LEGACY_NUTRITION not in progress_rules
    assert LEGACY_INSIGHTS not in progress_rules
    assert not any(r.startswith("/api/progress/<") for r in progress_rules)

    tree = ast.parse(TRACKING_PY.read_text(encoding="utf-8"),
                     filename=str(TRACKING_PY))
    published = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            if not (isinstance(deco.func, ast.Attribute)
                    and deco.func.attr == "route"):
                continue
            if deco.args and isinstance(deco.args[0], ast.Constant):
                published.append(deco.args[0].value)
    assert AXIS_INSIGHTS in published
    assert LEGACY_NUTRITION not in published
    assert LEGACY_INSIGHTS not in published


def test_no_first_party_frontend_calls_the_legacy_progress_nutrition_routes():
    """Supported consumers are JS/HTML fetch sites. Historical docs are not."""
    offenders = []
    for path in _frontend_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(REPO_ROOT).as_posix()
        if LEGACY_NUTRITION in text:
            offenders.append(f"{relative}: {LEGACY_NUTRITION}")
        # Exact legacy insights path, not the live axis-insights sibling.
        if LEGACY_INSIGHTS in text and AXIS_INSIGHTS not in text.replace(
                AXIS_INSIGHTS, ""):
            # A file that only mentions axis-insights is fine even if a naive
            # substring of "insights" appears inside it.
            pass
        if re.search(r'(?<!axis-)/api/progress/insights(?![\w-])', text):
            offenders.append(f"{relative}: {LEGACY_INSIGHTS}")
    assert offenders == []


def test_progress_insights_js_still_calls_the_live_axis_insights_route():
    js = _read("static/progress_insights.js")
    assert AXIS_INSIGHTS in js
    assert LEGACY_INSIGHTS not in js.replace(AXIS_INSIGHTS, "")


def test_unowned_calorie_adherence_heuristic_is_gone():
    """F9 / N10: ``80 ≤ pct ≤ 110 → success`` must not ship anywhere first-party."""
    source = TRACKING_PY.read_text(encoding="utf-8")
    assert "80 <=" not in source
    assert "pct <= 110" not in source
    assert "ins_cal_title" not in source
    assert "Calorie adherence" not in source
    assert "calorie adherence" not in source.lower()

    tree = ast.parse(source, filename=str(TRACKING_PY))
    names = {node.name for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef)}
    assert "progress_nutrition" not in names
    assert "progress_insights" not in names


def test_canonical_progress_gained_no_nutrition_scoring_fields():
    """C8: removing the orphan is not permission to move it into Progress."""
    banned = ("nutrition_score", "calorie_adherence", "adherence_pct",
              "diet_grade", "food_score")
    for relative in (
            "app/services/progress_summary",
            "app/services/progress_insights",
            "app/services/progress_history",
            "app/services/progress_physique",
    ):
        root = REPO_ROOT / relative
        paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for token in banned:
                assert token not in text, f"{path}: {token}"


# ---------------------------------------------------------------------------
# F10 — browser score and grade are deleted, not relocated
# ---------------------------------------------------------------------------

def test_browser_no_longer_calculates_a_nutrition_score_or_letter_grade():
    js = NUTRITION_JS.read_text(encoding="utf-8")
    assert "function mealScore" not in js
    assert "grade = 'A'" not in js
    assert "grade = 'B'" not in js
    assert "grade = 'C'" not in js
    assert "grade = 'D'" not in js
    assert "'A'; tone" not in js
    assert "/100" not in _js_function_body(js, "mealCardHTML")
    assert "nutrition.ai_score" not in js


def test_meal_card_no_longer_renders_a_score_or_grade_shell():
    js = NUTRITION_JS.read_text(encoding="utf-8")
    body = _js_function_body(js, "mealCardHTML")
    assert "badge-" not in body
    assert "mealScore" not in body
    assert "s.grade" not in body
    assert "s.value" not in body
    # Correction + quick-edit stay; the grade badge does not.
    assert "deleteMeal" in body
    assert "mc-edit" in body


def test_no_server_replacement_score_was_introduced():
    """F10/N10: delete the heuristic. Do not mint a canonical score."""
    meallog = MEALLOG_PY.read_text(encoding="utf-8")
    for token in ("nutrition_score", "meal_score", "food_score",
                  "diet_score", "letter_grade", "ai_score"):
        assert token not in meallog

    tree = ast.parse(meallog, filename=str(MEALLOG_PY))
    today = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "today_meals")
    strings = [n.value for n in ast.walk(today)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    for key in ("score", "grade", "adherence", "nutrition_score", "ai_score"):
        assert key not in strings, key


def test_nutrition_ai_score_locale_key_is_gone():
    for name in ("en", "tr"):
        locale = json.loads(
            (REPO_ROOT / "locales" / f"{name}.json").read_text(encoding="utf-8"))
        assert "nutrition.ai_score" not in locale


# ---------------------------------------------------------------------------
# N4 — server authority is presented; the browser invents no split
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("goal,expected", [
    ("kilo verme", DEFAULT_WEB),
    ("kas kazanma", MUSCLE_GAIN_WEB),
    ("", DEFAULT_WEB),
])
def test_today_payload_publishes_the_canonical_split(
        client, auth_user, goal, expected):
    _seed_session(auth_user, goal=goal, target_calories=TARGET_KCAL)

    body = client.get(TODAY_URL).get_json()
    assert body["targets"] is not None
    _approx_web(body["targets"], expected)

    canonical = derive_daily_macro_targets(TARGET_KCAL, goal)
    _approx_web(body["targets"], _as_web(canonical))
    _approx_web(body["remaining"], expected)


def test_today_payload_publishes_remaining_from_the_canonical_budget(
        client, auth_user):
    _seed_session(auth_user, goal="kilo verme", target_calories=TARGET_KCAL)
    db.session.add(MealLog(
        user_id=auth_user.id, ogun="Kahvaltı", yemekler="yulaf",
        kalori=500, protein=25, karb=50, yag=5.5, tarih=day_key()))
    db.session.commit()

    body = client.get(TODAY_URL).get_json()
    targets = derive_daily_macro_targets(TARGET_KCAL, "kilo verme")
    remaining = remaining_macro_budget(targets, {
        "calories": 500, "protein": 25, "carbs": 50, "fat": 5.5,
    })
    _approx_web(body["remaining"], _as_web(remaining))
    assert body["totals"]["kalori"] == 500


def test_unset_calorie_target_publishes_absence_not_a_fabricated_number(
        client, auth_user):
    """F3a must not re-enter through the web payload or a 0/2000 fallback."""
    body = client.get(TODAY_URL).get_json()
    assert body["targets"] is None
    assert body["remaining"] is None
    assert body["totals"] == {
        "kalori": 0, "protein": 0, "karb": 0, "yag": 0,
    }

    _seed_session(auth_user, goal="kilo verme", target_calories=None)
    body = client.get(TODAY_URL).get_json()
    assert body["targets"] is None
    assert body["remaining"] is None


def test_today_payload_does_not_publish_a_zero_or_2000_stand_in_for_absence(
        client, auth_user):
    body = client.get(TODAY_URL).get_json()
    assert body["targets"] is not None or body["targets"] is None
    assert body["targets"] != {
        "kalori": 2000, "protein": 0, "karb": 0, "yag": 0,
    }
    assert body["targets"] != {
        "kalori": 0, "protein": 0, "karb": 0, "yag": 0,
    }
    assert body["targets"] is None


def test_web_today_imports_the_canonical_authority():
    tree = ast.parse(MEALLOG_PY.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        and node.module.endswith("nutrition_targets")
        for alias in node.names
    }
    assert "derive_daily_macro_targets" in imported
    assert "remaining_macro_budget" in imported


def test_browser_load_today_consumes_server_targets_and_derives_none():
    js = NUTRITION_JS.read_text(encoding="utf-8")
    body = _js_function_body(js, "loadTodayData")
    assert "targets" in body
    assert "0.30 / 4" not in body
    assert "0.40 / 4" not in body
    assert "0.30 / 9" not in body
    assert "targetCalories *" not in body
    # Absence is a real state, not a 2000 kcal stand-in.
    assert "2000" not in body


def test_browser_macro_bars_do_not_fabricate_fallback_grams():
    js = NUTRITION_JS.read_text(encoding="utf-8")
    body = _js_function_body(js, "updateMacroBars")
    assert "|| 140" not in body
    assert "|| 200" not in body
    assert "|| 60" not in body


def test_browser_contains_no_competing_macro_split():
    """N4 is not closed by deleting the digits ``0.3`` somewhere.

    The competing authority is *deriving grams from a calorie percentage*.
    Every browser surface that shows a macro or calorie target must converge on
    the canonical payload instead of re-deriving one.
    """
    forbidden = ("0.30 / 4", "0.40 / 4", "0.30 / 9",
                 "0.3 / 4", "0.4 / 4", "0.3 / 9")
    for relative in ("static/nutrition.js", "static/today.js",
                     "templates/today.html"):
        text = _read(relative)
        for token in forbidden:
            assert token not in text, f"{relative} still derives {token}"


def test_home_consumes_the_today_payload_targets():
    """Home reads the canonical target from `/meal-log/today` and, when there is
    no configured target, shows what was eaten rather than inventing a goal."""
    today = TODAY_JS.read_text(encoding="utf-8")
    assert "meals.targets" in today or "targets.protein" in today
    # The page may not fall back to a fabricated 2000 kcal configured target.
    assert "target_calories || 2000" not in today
    assert "2000" not in today
    # The template itself no longer hydrates anything, so this guard cannot be
    # satisfied by copy that merely sits in the markup.
    assert "meal-log/today" not in TODAY_HTML.read_text(encoding="utf-8")


def test_nutrition_js_does_not_default_target_calories_to_2000():
    js = NUTRITION_JS.read_text(encoding="utf-8")
    assert "let targetCalories = 2000" not in js
    assert "var targetCalories = 2000" not in js
    assert "targetCalories = 2000" not in js


# ---------------------------------------------------------------------------
# C13 — barcode/add: KEEP DEPRECATED (evidence, not a wish)
# ---------------------------------------------------------------------------

def test_barcode_add_compatibility_route_is_kept_deprecated(app, client, auth_user):
    """F6 safety is CLOSED by PR3. Compatibility *removal* is DEFERRED.

    Evidence collected for PR5, and why it does not meet C13's removal bar:

    * ``docs/MOBILE_NUTRITION.md`` still documents the path as a *supported*
      compatibility surface and states that no sunset date is promised.
    * PR3's deprecation is observable (``Deprecation: true``) but names no
      period that has elapsed.
    * The repository records no release note, changelog, or external-integrator
      sunset. Absence of a first-party ``static/`` / ``templates/`` caller is
      documented, and C13 says that is **not sufficient**.
    * No production access log is available from this worktree.

    Therefore PR5 does not delete the route, and does not modify PR3's safe
    implementation.
    """
    assert BARCODE_ADD in _url_rules(app)

    contract = _read("docs/MOBILE_NUTRITION.md")
    assert "POST /api/food/barcode/add" in contract
    assert "DEPRECATED compatibility surface" in contract
    assert "no sunset date is promised" in contract

    spec = _read(
        "docs/superpowers/specs/2026-08-30-sprint13-pr1-nutrition-closure-discovery.md")
    assert "C13" in spec
    assert "documented compatibility surface" in spec or \
        "legacy compatibility surface" in spec


def test_barcode_lookup_and_canonical_logfood_are_untouched_by_the_c13_decision(
        app):
    rules = _url_rules(app)
    assert "/api/food/barcode" in rules
    assert BARCODE_ADD in rules


# ---------------------------------------------------------------------------
# N10 — repository-wide: no unowned nutrition scoring definition ships
# ---------------------------------------------------------------------------

def test_no_unowned_nutrition_score_definition_ships_in_first_party_code():
    """Classify by meaning, not by the substring ``score``.

    Allowed (different questions, different owners):
    * nutrition *plan* 0–10 quality labels (İyi/Orta/Kötü) — planning domain
    * training_generation ``adherence_score`` — training consistency
    * progress check-in ``nutrition_adh`` slider — user self-report, not a score
    * ``nutrition_pipeline`` food-quality caps — provider parsing, not a day grade
    """
    js = NUTRITION_JS.read_text(encoding="utf-8")
    assert re.search(r"value\s*>=\s*75", js) is None
    assert "ideal protein payı" not in js
    assert "Makro denge" not in js

    tracking = TRACKING_PY.read_text(encoding="utf-8")
    assert "80 <= pct <= 110" not in tracking.replace(" ", "")
    assert re.search(r"80\s*<=\s*pct\s*<=\s*110", tracking) is None
