"""UX-3 PR4: Nutrition is a truthful child of Plan, not a second workspace."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import event

from app.plan_presenter import PlanFacts, build_plan_view
from app.timeutil import app_today


def _login_user(client, make_user, login, username="ux3pr4", language="tr"):
    user = make_user(
        username, profile_complete=True, language=language,
    )
    login(username)
    return user


def _seed_nutrition_facts(user_id, *, target=None, intake=(), has_plan=False):
    from app.extensions import db
    from app.models import MealLog, NutritionPlan, UserSession

    if target is not None:
        db.session.add(UserSession(user_id=user_id, target_calories=target))
    for index, calories in enumerate(intake):
        db.session.add(MealLog(
            user_id=user_id,
            ogun=f"Öğün {index}",
            yemekler="Kanonik kayıt",
            kalori=calories,
            protein=0,
            karb=0,
            yag=0,
            tarih=app_today().isoformat(),
        ))
    if has_plan:
        db.session.add(NutritionPlan(
            user_id=user_id,
            plan_data=json.dumps({"isim": "Aktif beslenme planı"}),
        ))
    db.session.commit()


def test_plan_facts_use_target_current_day_ledger_and_plan_presence(app, make_user):
    from app.extensions import db
    from app.models import MealLog
    from app.services.plan_facts import gather_plan_facts

    user = make_user("nutrition-facts", profile_complete=True)
    _seed_nutrition_facts(user.id, target=2200.5, intake=(100.5,), has_plan=True)
    db.session.add(MealLog(
        user_id=user.id, ogun="Dün", yemekler="Hariç", kalori=900,
        tarih=(app_today() - timedelta(days=1)).isoformat(),
    ))
    db.session.commit()

    facts = gather_plan_facts(user.id)

    assert facts.nutrition_state == "available"
    assert facts.nutrition_target_state == "available"
    # Match the canonical Nutrition UI's Math.round() display contract.
    assert facts.nutrition_target_calories == 2201
    assert facts.nutrition_intake_state == "available"
    assert facts.nutrition_consumed_calories == 101
    assert facts.nutrition_meal_count == 1
    assert facts.nutrition_plan_state == "available"
    assert facts.has_nutrition_plan is True


def test_zero_rows_is_known_zero_while_missing_target_and_plan_are_empty(app, make_user):
    from app.services.plan_facts import gather_plan_facts

    user = make_user("nutrition-empty", profile_complete=True)
    facts = gather_plan_facts(user.id)

    assert facts.nutrition_state == "empty"
    assert facts.nutrition_target_state == "empty"
    assert facts.nutrition_target_calories is None
    assert facts.nutrition_intake_state == "available"
    assert facts.nutrition_consumed_calories == 0
    assert facts.nutrition_meal_count == 0
    assert facts.nutrition_plan_state == "empty"
    assert facts.has_nutrition_plan is False


def test_nutrition_subreads_fail_independently(app, make_user, monkeypatch):
    from app.services import plan_facts as pf

    user = make_user("nutrition-isolation", profile_complete=True)
    monkeypatch.setattr(
        pf, "_read_nutrition_target", lambda _uid: ("available", 2100),
        raising=False,
    )
    monkeypatch.setattr(
        pf, "_read_today_nutrition", lambda _uid: (_ for _ in ()).throw(RuntimeError("ledger down")),
        raising=False,
    )
    monkeypatch.setattr(
        pf, "_read_nutrition_plan_presence", lambda _uid: ("available", True),
        raising=False,
    )

    facts = pf.gather_plan_facts(user.id)

    assert facts.nutrition_state == "partial"
    assert facts.nutrition_target_state == "available"
    assert facts.nutrition_target_calories == 2100
    assert facts.nutrition_intake_state == "unavailable"
    assert facts.nutrition_consumed_calories is None
    assert facts.nutrition_meal_count is None
    assert facts.nutrition_plan_state == "available"
    assert facts.has_nutrition_plan is True
    assert facts.supplements_state in {"available", "empty"}


def test_presenter_copies_nutrition_facts_without_inference():
    facts = PlanFacts(
        read_ok=True, has_active_plan=False, parse_ok=False,
        nutrition_state="available",
        nutrition_target_state="available",
        nutrition_target_calories=2040,
        nutrition_intake_state="available",
        nutrition_consumed_calories=735,
        nutrition_meal_count=3,
        nutrition_plan_state="empty",
        has_nutrition_plan=False,
    )

    view = build_plan_view(facts)

    assert view.nutrition_target_state == "available"
    assert view.nutrition_target_calories == 2040
    assert view.nutrition_intake_state == "available"
    assert view.nutrition_consumed_calories == 735
    assert view.nutrition_meal_count == 3
    assert view.nutrition_plan_state == "empty"
    assert view.has_nutrition_plan is False


def test_plan_renders_bounded_read_only_nutrition_summary(app, client, make_user, login):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _login_user(client, make_user, login, "nutrition-summary", "en")
    _seed_nutrition_facts(user.id, target=2000, intake=(625,), has_plan=True)

    html = client.get("/training").get_data(as_text=True)
    section = html.split('data-plan-domain="nutrition"', 1)[1].split(
        'data-plan-domain="supplements"', 1,
    )[0]

    assert "625" in section and "2000" in section
    assert "Nutrition plan saved" in section
    assert section.count('href="/nutrition"') == 1
    assert "/meal-log/today" not in html
    assert "/nutrition-plan/active" not in html
    assert "/static/nutrition.js" not in html


def test_nutrition_page_shows_plan_parent_and_preserves_five_local_areas(
    app, client, make_user, login,
):
    _login_user(client, make_user, login, "nutrition-placement", "en")

    html = client.get("/nutrition").get_data(as_text=True)

    assert 'class="hn-link active" aria-current="page">Plan</a>' in html
    assert 'class="nutrition-parent-context"' in html
    assert 'href="/training"' in html
    assert "Plan" in html and "Nutrition" in html
    for label in ("Today", "Diary", "Nutrition Plan", "History", "Water"):
        assert label in html
    assert html.count('role="tab"') == 5
    assert html.count('role="tabpanel"') == 5
    assert html.count('aria-controls="panel-') == 5
    assert "sidebar" not in html.lower()

    source = (__import__("pathlib").Path(__file__).resolve().parents[1]
              / "static" / "nutrition.js").read_text(encoding="utf-8")
    assert 'querySelector(\'[data-tab-name="plan"]\')' in source


def test_pr4_locale_keys_exist_in_both_catalogs():
    from app.i18n import reload_catalog

    catalog = reload_catalog()
    keys = {
        "nutrition.parent.plan",
        "nutrition.parent.current",
        "nutrition.tab_plan",
        "plan.nutrition.intake_target",
        "plan.nutrition.intake_only",
        "plan.nutrition.plan_available",
        "plan.nutrition.plan_empty",
        "plan.nutrition.intake_unavailable",
        "plan.domain_state.partial",
    }
    for locale in ("tr", "en"):
        assert keys <= catalog[locale].keys()
        assert all(catalog[locale][key] != key for key in keys)


@pytest.mark.parametrize("sessions_enabled,expected_selects", [(False, 7), (True, 9)])
def test_plan_render_has_exact_bounded_query_budget(
    app, make_user, sessions_enabled, expected_selects,
):
    from app.extensions import db
    from app.models import TrainingPlan
    from app.services.plan_facts import gather_plan_facts

    app.config["UIUX_PLAN_V2_ENABLED"] = True
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = sessions_enabled
    user = make_user(
        f"nutrition-budget-{int(sessions_enabled)}", profile_complete=True,
    )
    user_id = user.id
    db.session.add(TrainingPlan(
        user_id=user_id,
        plan_data=json.dumps([
            {"gun": "Pazartesi", "tip": "dinlenme", "egzersizler": []},
        ]),
    ))
    db.session.commit()

    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().lower().startswith("select"):
            statements.append(" ".join(statement.lower().split()))

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        facts = gather_plan_facts(user_id, sessions_enabled=sessions_enabled)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    assert facts.read_ok is True
    assert len(statements) == expected_selects, statements
    assert sum("from meal_log" in item for item in statements) == 1
    assert sum("from nutrition_plan" in item for item in statements) == 1
    assert not any("provider" in item or "history" in item for item in statements)


@pytest.mark.parametrize("raw,expected", [
    # Positive .5 boundaries follow the browser, not Python's half-to-even.
    (2200.5, 2201), (100.5, 101), (0.5, 1), (2201.5, 2202),
    # Non-boundary values round normally in both languages.
    (2200.4, 2200), (2200.6, 2201), (0.0, 0), (2000, 2000),
    # Absent or non-numeric input is never coerced into a fabricated integer.
    (None, None), ("2200.5", None), (True, None),
    (float("nan"), None), (float("inf"), None),
])
def test_display_kcal_matches_the_browser_math_round_contract(raw, expected):
    from app.services.plan_facts import _display_kcal

    assert _display_kcal(raw) == expected


def test_plan_summary_prints_the_same_integer_nutrition_today_would(
    app, client, make_user, login,
):
    """Same stored value → same displayed integer on Plan and Nutrition Today."""
    from app.services.plan_facts import gather_plan_facts

    app.config["UIUX_PLAN_V2_ENABLED"] = True
    user = _login_user(client, make_user, login, "nutrition-parity", "en")
    _seed_nutrition_facts(user.id, target=2200.5, intake=(100.5, 24.5))

    facts = gather_plan_facts(user.id)
    section = client.get("/training").get_data(as_text=True).split(
        'data-plan-domain="nutrition"', 1,
    )[1].split('data-plan-domain="supplements"', 1)[0]

    # Math.round(2200.5) === 2201; Math.round(100.5 + 24.5) === 125.
    assert facts.nutrition_target_calories == 2201
    assert facts.nutrition_consumed_calories == 125
    assert "2201" in section and "125" in section
