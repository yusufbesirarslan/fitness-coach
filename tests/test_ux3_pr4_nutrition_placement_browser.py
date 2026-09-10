"""Real-browser checks for UX-3 PR4 Plan → Nutrition placement."""
import json

from playwright.sync_api import expect

import pytest

from app.extensions import db
from app.models import MealLog, NutritionPlan, User, UserSession
from app.timeutil import app_today
from test_training_execution_boundary import training_page  # noqa: F401


@pytest.fixture
def stub_meal_macro_provider(monkeypatch):
    """Stub ONLY the external LLM macro estimator, as this harness always has.

    `POST /meal-log` asks OpenAI for a meal's macros. Everything that makes the
    write canonical — validation, `sanitize_meal_total_macros`, idempotency,
    the `MealLog` commit and the quest funnel — stays real, so this proves the
    real web write path rather than a DOM fixture.
    """
    from app.blueprints.nutrition import meallog

    def _fake_chat(*_args, **_kwargs):
        return '{"kalori": 320, "protein": 12, "karb": 44, "yag": 9}'

    monkeypatch.setattr(meallog, "_openai_chat", _fake_chat)


def _seed(user_id):
    db.session.get(User, user_id).profile_complete = True
    db.session.add(UserSession(user_id=user_id, target_calories=2100))
    db.session.add(MealLog(
        user_id=user_id, ogun="Kahvaltı", yemekler="Yulaf", kalori=525,
        tarih=app_today().isoformat(),
    ))
    db.session.add(NutritionPlan(
        user_id=user_id, plan_data=json.dumps({"isim": "Plan A"}),
    ))
    db.session.commit()


def _seed_without_plan(user_id):
    db.session.get(User, user_id).profile_complete = True
    db.session.add(UserSession(user_id=user_id, target_calories=2100))
    db.session.commit()


def test_plan_to_nutrition_hierarchy_local_tabs_and_responsive_layout(
    app, auth_user, training_page,
):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    with app.app_context():
        _seed(auth_user.id)

    page, traffic, _, _ = training_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    for locale in ("en", "tr"):
        with app.app_context():
            db.session.get(User, auth_user.id).language = locale
            db.session.commit()
        for width in (320, 390, 768, 1024, 1366):
            page.set_viewport_size({"width": width, "height": 900})
            traffic.clear()
            page.goto("http://localhost/training")
            nutrition = page.locator('[data-plan-domain="nutrition"]')
            expect(nutrition).to_contain_text("525")
            expect(nutrition.locator('a[href="/nutrition"]')).to_have_count(1)
            assert not any(
                path in {"/meal-log/today", "/nutrition-plan/active"}
                for path, _, _ in traffic
            )
            nutrition.locator('a[href="/nutrition"]').click()

            expect(page.locator(".nutrition-parent-context")).to_be_visible()
            expect(page.locator('[data-nav-id="plan"][aria-current="page"]')).to_have_count(2)
            expect(page.locator('[role="tab"]')).to_have_count(5)
            expect(page.locator('[role="tabpanel"]')).to_have_count(5)
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            )

    for name in ("diary", "plan", "history", "water", "today"):
        tab = page.locator(f'[data-tab-name="{name}"]')
        tab.focus()
        page.keyboard.press("Enter")
        expect(tab).to_have_attribute("aria-selected", "true")
        expect(page.locator(f"#panel-{name}")).to_be_visible()

    assert errors == []


def test_no_plan_mobile_keeps_today_diary_history_water_and_creation_working(
    app, auth_user, training_page, stub_meal_macro_provider,
):
    with app.app_context():
        _seed_without_plan(auth_user.id)

    page, traffic, _, _ = training_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto("http://localhost/nutrition")

    # Log through the canonical web write flow; PR4 must not replace it.
    page.locator("#log-fab").click()
    page.locator('[data-action="logManual"]').click()
    page.locator("#meal-input").fill("PR4 canonical meal")
    with page.expect_response(
        lambda response: response.url.endswith("/meal-log")
        and response.request.method == "POST"
    ) as meal_write:
        page.locator('[data-action="logMeal"]').click()
    assert meal_write.value.ok
    expect(page.locator("#panel-today")).to_contain_text("PR4 canonical meal")

    # Diary is the daily BUILDER (CustomMeal), a deliberately separate concept
    # from the MealLog ledger — never a second view of it. Proving it survives a
    # missing NutritionPlan means proving it stays fully operable.
    diary_tab = page.locator('[data-tab-name="diary"]')
    diary_tab.focus()
    page.keyboard.press("Enter")
    expect(diary_tab).to_have_attribute("aria-selected", "true")
    expect(page.locator("#panel-diary")).to_be_visible()
    expect(page.locator("#panel-diary .diary-meal-card")).to_have_count(4)
    expect(page.locator("#panel-diary .diary-food-search")).to_have_count(4)
    expect(page.locator('#panel-diary [data-action="logDiaryMeal"]')).to_have_count(4)

    # History reads the canonical MealLog ledger, so the meal just written by the
    # real web write flow must be visible there.
    history_tab = page.locator('[data-tab-name="history"]')
    history_tab.focus()
    page.keyboard.press("Enter")
    expect(history_tab).to_have_attribute("aria-selected", "true")
    expect(page.locator("#panel-history")).to_contain_text("PR4 canonical meal")

    plan_tab = page.locator('[data-tab-name="plan"]')
    plan_tab.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#plan-form")).to_be_visible()
    expect(page.locator("#plan-btn")).to_be_visible()

    water_tab = page.locator('[data-tab-name="water"]')
    water_tab.focus()
    page.keyboard.press("Enter")
    with page.expect_response(
        lambda response: response.url.endswith("/water")
        and response.request.method == "POST"
    ) as water_write:
        page.locator("#water-btn").click()
    assert water_write.value.ok
    expect(page.locator("#water-num")).to_have_text("1")

    assert any(path == "/nutrition-plan/active" for path, _, _ in traffic)
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )
    assert errors == []
