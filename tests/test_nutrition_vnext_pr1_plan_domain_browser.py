"""Browser proof for the Plan sibling-domain overview."""

import pytest
from playwright.sync_api import expect

from app.extensions import db
from app.models import User
from test_training_execution_boundary import training_page  # noqa: F401


@pytest.mark.parametrize("width,height", [(320, 844), (320, 640), (390, 844), (430, 844)])
@pytest.mark.parametrize("language", ["en", "tr"])
def test_first_run_setup_precedes_peer_domain_overview_in_initial_viewport(
    app, auth_user, training_page, width, height, language,
):
    """A new user sees the guidance and one usable creation action first."""
    with app.app_context():
        db.session.get(User, auth_user.id).language = language
        db.session.commit()

    page, _, _, _ = training_page
    page.set_viewport_size({"width": width, "height": height})
    page.goto("http://localhost/training")
    page.evaluate("document.fonts ? document.fonts.ready : null")

    outcome = page.locator(".plan-create-outcome")
    action = page.locator("[data-plan-manage-generate]")
    overview = page.locator(".plan-domain-overview")
    expect(outcome).to_be_visible()
    expect(action).to_have_count(1)
    assert page.evaluate("""() => {
      const outcome = document.querySelector('.plan-create-outcome');
      const action = document.querySelector('[data-plan-manage-generate]');
      const overview = document.querySelector('.plan-domain-overview');
      return !!(outcome.compareDocumentPosition(overview) & Node.DOCUMENT_POSITION_FOLLOWING)
        && !!(action.compareDocumentPosition(overview) & Node.DOCUMENT_POSITION_FOLLOWING);
    }""")
    usable = page.evaluate("""() => innerHeight -
      document.querySelector('.action-bar').getBoundingClientRect().height""")
    explanation = outcome.bounding_box()
    button = action.bounding_box()
    assert explanation and button
    assert explanation["y"] + explanation["height"] <= usable + 1
    assert button["y"] >= 0 and button["y"] + button["height"] <= usable + 1
    assert button["height"] >= 44

    training = overview.locator('[data-plan-domain="training"]')
    nutrition = overview.locator('[data-plan-domain="nutrition"]')
    assert training.locator("h2").count() == 1
    assert nutrition.locator("h2").count() == 1
    assert nutrition.locator('[data-plan-domain="supplements"] h3').count() == 1


def test_first_run_guards_reject_order_spacing_and_domain_mutations(auth_user, training_page):
    page, _, _, _ = training_page
    page.set_viewport_size({"width": 320, "height": 640})
    page.goto("http://localhost/training")

    def contract():
        return page.evaluate("""() => {
          const outcome = document.querySelector('.plan-create-outcome');
          const action = document.querySelector('[data-plan-manage-generate]');
          const overview = document.querySelector('.plan-domain-overview');
          const training = overview.querySelector('[data-plan-domain="training"]');
          const nutrition = overview.querySelector('[data-plan-domain="nutrition"]');
          const bar = document.querySelector('.action-bar');
          const usable = innerHeight - bar.getBoundingClientRect().height;
          return {
            order: !!(outcome.compareDocumentPosition(overview) & Node.DOCUMENT_POSITION_FOLLOWING)
              && !!(action.compareDocumentPosition(overview) & Node.DOCUMENT_POSITION_FOLLOWING),
            viewport: outcome.getBoundingClientRect().bottom <= usable + 1
              && action.getBoundingClientRect().bottom <= usable + 1,
            siblings: training.parentElement === overview && nutrition.parentElement === overview,
          };
        }""")

    assert all(contract().values())
    page.evaluate("""() => {
      const overview = document.querySelector('.plan-domain-overview');
      overview.parentElement.insertBefore(overview, document.querySelector('#plan-training-detail'));
    }""")
    assert contract()["order"] is False

    page.reload()
    page.evaluate("document.querySelector('#plan-training-detail').style.marginTop = '900px'")
    assert contract()["viewport"] is False

    page.reload()
    page.evaluate("""() => document.querySelector('[data-plan-domain="training"]')
      .append(document.querySelector('[data-plan-domain="nutrition"]'))""")
    assert contract()["siblings"] is False

    from scripts.frontend_audit.plan_coach_pr3_matrix import PLAN_MEASURE_JS

    page.reload()
    measurement = page.evaluate(PLAN_MEASURE_JS)
    assert measurement["training_state_text_present"] is True
    assert measurement["create_form_present"] == 1
    assert measurement["retry_present"] == 0
    page.evaluate("""document.querySelector('[data-plan-domain="training"] .plan-domain-summary').remove()""")
    assert page.evaluate(PLAN_MEASURE_JS)["training_state_text_present"] is False


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
    assert min(training_box["y"], nutrition_box["y"]) >= (
        detail_box["y"] + detail_box["height"] - 1
    )
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
