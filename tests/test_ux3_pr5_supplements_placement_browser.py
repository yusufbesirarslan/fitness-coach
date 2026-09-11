"""Real-browser checks for UX-3 PR5 Plan → Nutrition → Supplements placement.

Hermetic: the page is the real rendered template running the real dispatcher,
and every request is served by the authenticated Flask test client. No external
provider is contacted, and every mutation here goes through the canonical
Supplement routes — the point of PR5 is that no other surface can.
"""
from playwright.sync_api import expect

import pytest

from app.extensions import db
from app.models import Supplement, User
from test_training_execution_boundary import training_page  # noqa: F401

VIEWPORTS = (320, 390, 768, 1024, 1366)


def _ready(user_id, language="en"):
    user = db.session.get(User, user_id)
    user.profile_complete = True
    user.language = language
    db.session.commit()


def _seed_supplement(user_id, **fields):
    fields.setdefault("product_name", "Whey Protein")
    fields.setdefault("brand", "Optimum")
    fields.setdefault("category", "Protein")
    fields.setdefault("status", "Active")
    row = Supplement(user_id=user_id, **fields)
    db.session.add(row)
    db.session.commit()
    return row


def _no_horizontal_overflow(page):
    return page.evaluate(
        "document.documentElement.scrollWidth"
        " <= document.documentElement.clientWidth + 1"
    )


def _arm_reload_probe(page):
    """Stamp the current document so a self-reload can be detected.

    `manage_stack.html` reloads ITSELF a few hundred ms after a successful
    add/status write (`setTimeout(() => location.reload(), ...)`), i.e. AFTER
    the response this suite waits on has already arrived.
    """
    page.evaluate("window.__pr5_document = true")


def _await_cabinet_reload(page):
    """Block until the cabinet's own delayed reload has landed.

    `page.wait_for_url("**/supplements")` CANNOT serve as this barrier: a reload
    does not change the URL, so it matches the current document and returns
    immediately. The scheduled reload then fires while the test is navigating
    somewhere else and aborts that navigation -- `net::ERR_ABORTED`, on a
    request the server answered 200. Waiting for the stamp to disappear waits
    for the new document instead of guessing at a delay.
    """
    page.wait_for_function("() => window.__pr5_document === undefined")


def test_plan_to_nutrition_to_supplements_journey_keeps_plan_active(
    app, auth_user, training_page,
):
    """A. /training → Nutrition placement → Supplements child → /supplements."""
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    with app.app_context():
        _ready(auth_user.id)
        _seed_supplement(auth_user.id)
        _seed_supplement(auth_user.id, product_name="Creatine",
                         category="Creatine")

    page, traffic, _, _ = training_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    page.goto("http://localhost/training")

    # Supplements is rendered INSIDE the Nutrition placement, not beside it.
    nutrition = page.locator('[data-plan-domain="nutrition"]')
    supplements = nutrition.locator('[data-plan-domain="supplements"]')
    expect(supplements).to_have_count(1)
    expect(supplements).to_contain_text("2 supplements saved.")
    # Bounded facts only — no cabinet detail, no edit control on Plan.
    expect(supplements.locator("button")).to_have_count(0)
    expect(supplements).not_to_contain_text("Optimum")

    traffic.clear()
    supplements.locator('a[href="/supplements"]').click()

    expect(page.locator(".stack-parent-context")).to_be_visible()
    context = page.locator(".stack-parent-context")
    expect(context).to_contain_text("Plan")
    expect(context).to_contain_text("Nutrition")
    expect(context).to_contain_text("Supplements")
    expect(context.locator('a[href="/training"]')).to_have_count(1)
    expect(context.locator('a[href="/nutrition"]')).to_have_count(1)

    # Global ownership is still Plan — header tab and mobile bar agree.
    expect(page.locator('[data-nav-id="plan"][aria-current="page"]')).to_have_count(2)
    expect(page.locator('[data-nav-id="supplements"]')).to_have_count(0)
    expect(page.locator("h1")).to_have_count(1)

    # Opening the cabinet reads the cabinet once; it starts no other domain.
    assert [path for path, _, _ in traffic if path.startswith("/supplement")] == [
        "/supplements"]

    # ...and back up the hierarchy through the new parent context.
    context.locator('a[href="/nutrition"]').click()
    expect(page.locator(".nutrition-parent-context")).to_be_visible()
    page.locator('.nutrition-parent-context a[href="/training"]').click()
    expect(page.locator('[data-plan-domain="training"]')).to_have_count(1)

    assert errors == []


def test_nutrition_child_entry_reaches_the_cabinet_without_a_sixth_tab(
    app, auth_user, training_page,
):
    """B. /nutrition → Supplements child entry → /supplements."""
    with app.app_context():
        _ready(auth_user.id)

    page, _, _, _ = training_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    page.goto("http://localhost/nutrition")

    # The five daily workflows are unchanged and Supplements is not one of them.
    expect(page.locator('[role="tab"]')).to_have_count(5)
    expect(page.locator('[role="tabpanel"]')).to_have_count(5)
    expect(page.locator('[data-tab-name="supplements"]')).to_have_count(0)
    child = page.locator(".nutrition-child-domain")
    expect(child).to_be_visible()
    expect(child.locator(".tab-btn")).to_have_count(0)

    # The child entry survives switching local tabs — it belongs to the domain,
    # not to one workflow.
    for name in ("diary", "plan", "history", "water", "today"):
        tab = page.locator(f'[data-tab-name="{name}"]')
        tab.focus()
        page.keyboard.press("Enter")
        expect(tab).to_have_attribute("aria-selected", "true")
        expect(child).to_be_visible()

    child.locator('a[href="/supplements"]').click()
    expect(page.locator(".stack-parent-context")).to_be_visible()
    expect(page.locator('[data-nav-id="plan"][aria-current="page"]')).to_have_count(2)

    assert errors == []


def test_profile_links_out_and_never_edits_the_cabinet_itself(
    app, auth_user, training_page,
):
    """C. /edit-profile → contextual link → /supplements (the one editor)."""
    with app.app_context():
        _ready(auth_user.id)
        _seed_supplement(auth_user.id, product_name="Magnesium",
                         category="Vitamin/Health")

    page, traffic, _, _ = training_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    page.goto("http://localhost/edit-profile")

    # A projection: the stack is visible, with no control that can change it.
    expect(page.locator(".pf-stack")).to_contain_text("Magnesium")
    expect(page.locator(".pf-stack-context")).to_contain_text(
        "You manage it in Plan")
    expect(page.locator(".pf-stack button")).to_have_count(0)
    expect(page.locator("#add-btn")).to_have_count(0)
    expect(page.locator('[data-action="deleteSupplement"]')).to_have_count(0)
    link = page.locator('.pf-link[href="/supplements"]')
    expect(link).to_have_count(1)
    expect(link).to_contain_text("Open supplement cabinet")

    traffic.clear()
    link.click()
    expect(page.locator(".stack-parent-context")).to_be_visible()
    expect(page.locator("#add-btn")).to_have_count(1)
    assert not any(
        path.startswith("/supplement/") for path, _, _ in traffic)

    assert errors == []


def test_zero_then_add_then_status_then_delete_all_run_through_the_cabinet(
    app, auth_user, training_page,
):
    """D/E/F. The empty state, and every mutation, on the canonical surface."""
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    with app.app_context():
        _ready(auth_user.id)

    page, traffic, _, _ = training_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # `deleteSupplement` guards with window.confirm; Playwright dismisses
    # dialogs by default, which would silently CANCEL the delete under test.
    page.on("dialog", lambda dialog: dialog.accept())

    # D. Zero supplements — Plan says empty, the cabinet offers creation.
    page.goto("http://localhost/training")
    expect(page.locator('[data-plan-domain="supplements"]')).to_contain_text(
        "No supplements saved yet.")

    page.goto("http://localhost/supplements")
    expect(page.locator(".empty-state")).to_be_visible()

    page.locator("#f-name").fill("Creatine Monohydrate")
    page.locator("#f-brand").fill("Bulk")
    _arm_reload_probe(page)
    with page.expect_response(
        lambda r: r.url.endswith("/supplement/add") and r.request.method == "POST"
    ) as added:
        page.locator("#add-btn").click()
    assert added.value.ok

    _await_cabinet_reload(page)
    expect(page.locator(".supp-card")).to_have_count(1)
    expect(page.locator(".supp-card")).to_contain_text("Creatine Monohydrate")

    with app.app_context():
        row = Supplement.query.filter_by(user_id=auth_user.id).one()
        assert row.status == "Active"
        supplement_id = row.id

    # E. Status change — through /supplement/edit, from the cabinet only.
    _arm_reload_probe(page)
    with page.expect_response(
        lambda r: r.url.endswith(f"/supplement/edit/{supplement_id}")
        and r.request.method == "POST"
    ) as edited:
        page.locator(
            f'[data-action="quickStatus"][data-args=\'[{supplement_id},"Low Stock"]\']'
        ).click()
    assert edited.value.ok
    _await_cabinet_reload(page)
    with app.app_context():
        assert db.session.get(Supplement, supplement_id).status == "Low Stock"

    # Plan reflects the same cabinet without gaining a control of its own.
    page.goto("http://localhost/training")
    expect(page.locator('[data-plan-domain="supplements"]')).to_contain_text(
        "1 supplements saved.")

    # F. Delete — through /supplement/delete, from the cabinet only.
    page.goto("http://localhost/supplements")
    with page.expect_response(
        lambda r: r.url.endswith(f"/supplement/delete/{supplement_id}")
        and r.request.method == "POST"
    ) as deleted:
        page.locator(
            f'[data-action="deleteSupplement"][data-args=\'[{supplement_id}]\']'
        ).click()
    assert deleted.value.ok
    expect(page.locator(f"#supp-{supplement_id}")).to_have_count(0)
    with app.app_context():
        assert db.session.get(Supplement, supplement_id) is None

    # Every write in this journey originated at the canonical cabinet.
    # Filter by PATH, not by request body: `deleteSupplement` POSTs with no
    # body at all, so a body-based filter would silently drop the delete and
    # let this assertion pass while proving nothing about it.
    writes = [path for path, _, _ in traffic if path.startswith("/supplement/")]
    assert writes == [
        "/supplement/add",
        f"/supplement/edit/{supplement_id}",
        f"/supplement/delete/{supplement_id}",
    ], writes

    assert errors == []


def test_visibility_toggle_is_owned_by_the_cabinet_and_persists(
    app, auth_user, training_page,
):
    """G. Public/private is edited where the cabinet is, and nowhere else."""
    with app.app_context():
        _ready(auth_user.id)

    page, _, _, _ = training_page
    page.goto("http://localhost/supplements")

    toggle = page.locator("#f-public")
    expect(toggle).to_have_class("toggle on")
    toggle.click()
    expect(toggle).to_have_class("toggle")

    page.locator("#f-name").fill("Private ZMA")
    page.locator("#f-brand").fill("House")
    _arm_reload_probe(page)
    with page.expect_response(
        lambda r: r.url.endswith("/supplement/add") and r.request.method == "POST"
    ) as added:
        page.locator("#add-btn").click()
    assert added.value.ok
    _await_cabinet_reload(page)

    with app.app_context():
        row = Supplement.query.filter_by(user_id=auth_user.id).one()
        assert row.is_public is False

    # Profile shows the owner their own stack (it always has) and still offers
    # no way to change that visibility.
    page.goto("http://localhost/edit-profile")
    expect(page.locator(".pf-stack")).to_contain_text("Private ZMA")
    expect(page.locator(".pf-stack input")).to_have_count(0)
    expect(page.locator(".pf-stack .toggle")).to_have_count(0)


@pytest.mark.parametrize("language", ["en", "tr"])
def test_hierarchy_is_readable_at_every_viewport_in_both_languages(
    app, auth_user, training_page, language,
):
    """H. EN/TR × 320/390/768/1024/1366 on the three placement surfaces."""
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    with app.app_context():
        _ready(auth_user.id, language)
        _seed_supplement(auth_user.id)

    page, _, _, _ = training_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    for width in VIEWPORTS:
        page.set_viewport_size({"width": width, "height": 900})

        page.goto("http://localhost/training")
        expect(page.locator('[data-plan-domain="supplements"]')).to_be_visible()
        assert _no_horizontal_overflow(page), ("plan", language, width)

        page.goto("http://localhost/nutrition")
        expect(page.locator(".nutrition-child-domain")).to_be_visible()
        expect(page.locator('[role="tab"]')).to_have_count(5)
        assert _no_horizontal_overflow(page), ("nutrition", language, width)

        page.goto("http://localhost/supplements")
        context = page.locator(".stack-parent-context")
        expect(context).to_be_visible()
        # The orientation line must stay a line, not a hero block.
        assert context.bounding_box()["height"] <= 120, (language, width)
        expect(page.locator("#add-btn")).to_be_visible()
        assert _no_horizontal_overflow(page), ("supplements", language, width)

        page.goto("http://localhost/edit-profile")
        expect(page.locator('.pf-link[href="/supplements"]')).to_be_visible()
        assert _no_horizontal_overflow(page), ("profile", language, width)

    assert errors == []


def test_cabinet_controls_are_keyboard_reachable_and_show_focus(
    app, auth_user, training_page,
):
    """Bounded a11y check on the surfaces PR5 actually touches."""
    with app.app_context():
        _ready(auth_user.id)
        row = _seed_supplement(auth_user.id)
        supplement_id = row.id

    page, _, _, _ = training_page
    page.goto("http://localhost/supplements")

    # Parent context links are real links: focusable, with a visible outline.
    for href in ("/training", "/nutrition"):
        link = page.locator(f'.stack-parent-context a[href="{href}"]')
        link.focus()
        assert page.evaluate(
            "sel => document.activeElement === document.querySelector(sel)",
            f'.stack-parent-context a[href="{href}"]',
        )
        assert page.evaluate(
            "sel => getComputedStyle(document.querySelector(sel), ':focus-visible')"
            ".outlineStyle !== 'none'",
            f'.stack-parent-context a[href="{href}"]',
        )

    # Category and status chips are <button>s, so Enter operates them.
    creatine = page.locator('[data-action="fxPickCat"][data-cat="Creatine"]')
    creatine.focus()
    page.keyboard.press("Enter")
    expect(creatine).to_have_class("cat-chip active")

    low = page.locator('[data-action="fxPickStatus"][data-status="Low Stock"]')
    low.focus()
    page.keyboard.press("Enter")
    expect(low).to_have_class("cat-chip active")

    # Status badges are not colour-only — each carries its own label text.
    badge = page.locator(f"#supp-{supplement_id} .status-badge")
    expect(badge).to_have_text("Active")

    # Every form control has a programmatic label.
    for field in ("f-name", "f-brand", "f-price", "f-review"):
        assert page.evaluate(
            "id => { const el = document.getElementById(id);"
            " const l = el.closest('div')?.querySelector('label');"
            " return Boolean(l && l.textContent.trim()); }",
            field,
        ), field
