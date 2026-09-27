"""Browser proof for the Plan sibling-domain overview."""

import pytest
from playwright.sync_api import expect

from app.extensions import db
from app.models import User
from test_training_execution_boundary import training_page  # noqa: F401


@pytest.mark.parametrize("width", [320, 390, 430, 768, 1024, 1366])
@pytest.mark.parametrize("language", ["en", "tr"])
def test_plan_overview_is_reachable_and_equal_at_supported_widths(
    app, auth_user, training_page, width, language,
):
    with app.app_context():
        db.session.get(User, auth_user.id).language = language
        db.session.commit()

    page, traffic, _, _ = training_page
    page.set_viewport_size({"width": width, "height": 900})
    traffic.clear()
    page.goto("http://localhost/training")

    overview = page.locator(".plan-domain-overview")
    training = overview.locator('[data-plan-domain="training"]')
    nutrition = overview.locator('[data-plan-domain="nutrition"]')
    detail = page.locator("#plan-training-detail")
    expect(training).to_be_visible()
    expect(nutrition).to_be_visible()
    expect(detail).to_be_visible()
    expect(nutrition.locator('[data-plan-domain="supplements"]')).to_be_visible()
    expect(training.locator('a[href="#plan-training-detail"]')).to_be_visible()
    expect(nutrition.locator('a[href="/nutrition"]')).to_be_visible()
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )

    training_box = training.bounding_box()
    nutrition_box = nutrition.bounding_box()
    detail_box = detail.bounding_box()
    assert training_box and nutrition_box and detail_box
    assert detail_box["y"] >= max(
        training_box["y"] + training_box["height"],
        nutrition_box["y"] + nutrition_box["height"],
    ) - 1
    if width >= 768:
        assert abs(training_box["width"] - nutrition_box["width"]) <= 2
        assert abs(training_box["y"] - nutrition_box["y"]) <= 2
    else:
        assert nutrition_box["y"] >= training_box["y"] + training_box["height"] - 1

    training_link = training.locator('a[href="#plan-training-detail"]')
    training_link.focus()
    assert page.evaluate(
        "document.activeElement === document.querySelector('[href=\"#plan-training-detail\"]')"
    )
    assert page.evaluate(
        "getComputedStyle(document.activeElement).outlineStyle !== 'none'"
    )
    assert not any(path in {"/meal-log/today", "/nutrition-plan/active", "/water"}
                   for path, _, _ in traffic)
