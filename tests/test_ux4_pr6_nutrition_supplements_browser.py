"""WEB-UX4-PR6 — real-browser contract for Nutrition + Supplements.

Hermetic: the real rendered templates and scripts, every request served by the
authenticated Flask test client, no external provider. Every supplement write
here goes through the canonical `/supplement/{add,edit,delete}` routes.

Geometry is measured from actual browser boxes, keyboard behaviour from real
key presses, and network behaviour from the requests the page actually makes.
"""
import json
import re

import pytest
from playwright.sync_api import expect

from app.extensions import db
from app.models import MealLog, Supplement, User, UserSession
from app.timeutil import app_today
from test_training_execution_boundary import training_page  # noqa: F401

WIDTHS = (320, 390, 768, 1024, 1366)
# Review P2-1: the in-between phone widths where a row can run out of room.
PHONE_EDGES = (360, 375, 384, 414)
RATING_FIELDS = ("rating_effect", "rating_taste", "rating_digestion", "rating_price")

# Global chrome (header/action bar/Coach widget root) is owned by the shell, not
# by these two pages; it is measured by the shell's own suites.
CONTROL_GEOMETRY = r"""
() => {
  const chrome = [...document.querySelectorAll('header, .global-header, .action-bar, #cw-root')];
  const inChrome = el => chrome.some(c => c.contains(el));
  const shown = el => el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})
      && el.getBoundingClientRect().width > 0;
  const out = [];
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab]';
  for (const el of document.querySelectorAll(sel)) {
    if (inChrome(el)) continue;
    let box = el;
    if (el.matches('input[type=radio], input[type=checkbox]') && getComputedStyle(el).opacity === '0') {
      box = el.closest('label') || el;
    }
    if (!shown(box)) continue;
    const r = box.getBoundingClientRect();
    out.push({d: el.outerHTML.slice(0, 80), w: r.width, h: r.height});
  }
  return out;
}
"""

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")


def _ready(user_id, language="en"):
    user = db.session.get(User, user_id)
    user.profile_complete = True
    user.language = language
    db.session.commit()


def _seed_supplements(user_id, count=3):
    specs = [
        dict(product_name="Gold Standard Whey", brand="Optimum Nutrition", category="Protein",
             status="Active", rating_effect=4, rating_taste=5, review_text="Mixes cleanly."),
        dict(product_name="Creatine Monohydrate", brand="Bulk", category="Creatine", status="Low Stock"),
        dict(product_name="Magnesium Bisglycinate with a deliberately long product name",
             brand="A rather long brand name for wrapping", category="Vitamin/Health", status="Finished"),
    ]
    rows = []
    for spec in specs[:count]:
        row = Supplement(user_id=user_id, **spec)
        db.session.add(row)
        rows.append(row)
    db.session.commit()
    return [row.id for row in rows]


def _seed_meals(user_id):
    for slot, food, kcal in (("Kahvaltı", "Yulaf", 525), ("Öğle", "Tavuk pilav", 710)):
        db.session.add(MealLog(user_id=user_id, ogun=slot, yemekler=food, kalori=kcal,
                               protein=30, karb=60, yag=12, tarih=app_today().isoformat()))
    db.session.commit()


def _no_overflow(page):
    return page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def _small_controls(page):
    return [c for c in page.evaluate(CONTROL_GEOMETRY) if c["w"] < 43.5 or c["h"] < 43.5]


def _tab_walk(page, limit=120):
    """Real Tab presses from the top of the document until focus wraps."""
    page.evaluate(
        "() => { const s = document.createElement('span'); s.id = '__pr6_start';"
        " s.tabIndex = -1; document.body.prepend(s); s.focus(); }")
    stops = []
    for _ in range(limit):
        page.keyboard.press("Tab")
        info = page.evaluate("""() => {
          const a = document.activeElement;
          if (a === document.body) return null;
          const seen = a.hasAttribute('data-pr6-walk');
          a.setAttribute('data-pr6-walk', '1');
          const face = a.matches('input[type=radio], input[type=checkbox]') ? a.nextElementSibling : a;
          const cs = getComputedStyle(face);
          return {seen, id: a.id, name: a.getAttribute('name'), tag: a.tagName,
                  inAdd: Boolean(a.closest('#add-card')) && a.tagName !== 'SUMMARY',
                  summary: a.tagName === 'SUMMARY',
                  rating: a.closest('[data-rating-field]')?.dataset.ratingField || null,
                  tabIndex: a.tabIndex,
                  indicator: cs.outlineStyle !== 'none' || (cs.boxShadow && cs.boxShadow !== 'none')};
        }""")
        if info is None or info["seen"]:
            break
        stops.append(info)
    page.evaluate("() => { document.getElementById('__pr6_start')?.remove();"
                  " document.querySelectorAll('[data-pr6-walk]').forEach(e => e.removeAttribute('data-pr6-walk')); }")
    return stops


# ─────────────────────────────────────────────────────────────────────────────
# Supplements
# ─────────────────────────────────────────────────────────────────────────────

def test_cabinet_is_first_and_add_is_a_deliberate_zero_request_disclosure(
    app, auth_user, training_page,
):
    with app.app_context():
        _ready(auth_user.id)
        _seed_supplements(auth_user.id, 2)
    page, traffic, _, _ = training_page
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("http://localhost/supplements")

    cabinet = page.locator("#supp-list")
    disclosure = page.locator("#add-card")
    assert cabinet.bounding_box()["y"] < disclosure.bounding_box()["y"]
    expect(page.locator("#f-name")).to_be_hidden()

    # Collapsed: the only Add stop is the summary; no hidden field takes focus.
    stops = _tab_walk(page)
    assert not [s for s in stops if s["inAdd"]], stops
    assert sum(1 for s in stops if s["summary"]) == 1
    assert all(s["tabIndex"] <= 0 for s in stops)
    assert all(s["indicator"] for s in stops), [s for s in stops if not s["indicator"]]

    summary = page.locator("#add-toggle")
    traffic.clear()
    summary.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#f-name")).to_be_visible()
    page.wait_for_timeout(150)
    assert traffic == []

    stops = _tab_walk(page)
    in_add = [s for s in stops if s["inAdd"]]
    order = [s["id"] or s["name"] for s in in_add]
    assert order == ["f-name", "f-brand", "category", "status", *RATING_FIELDS,
                     "f-price", "f-public", "f-review", "add-btn"], order
    # ~1 Tab stop per rating dimension, not 5.
    ratings = [s["rating"] for s in stops if s["rating"]]
    assert ratings == list(RATING_FIELDS), ratings
    assert all(s["indicator"] for s in in_add), [s for s in in_add if not s["indicator"]]

    # Local choices send nothing, and neither does closing.
    traffic.clear()
    page.locator('label.stack-choice:has(input[value="Creatine"])').click()
    page.locator('label.stack-choice:has(input[value="Finished"])').click()
    page.locator('[data-rating-field="rating_taste"] label:has(input[value="3"])').click()
    page.locator('label.stack-switch').click()
    page.wait_for_timeout(150)
    assert traffic == []
    summary.click()
    expect(page.locator("#f-name")).to_be_hidden()
    page.wait_for_timeout(150)
    assert traffic == []

    with app.app_context():
        assert Supplement.query.filter_by(user_id=auth_user.id).count() == 2
    assert page.locator('[data-action="addSupplement"]').count() == 1
    assert errors == []


def test_rating_groups_use_native_arrow_keys_and_expose_the_value(
    app, auth_user, training_page,
):
    with app.app_context():
        _ready(auth_user.id)
    page, traffic, _, _ = training_page
    page.goto("http://localhost/supplements")
    page.locator("#add-toggle").click()

    effect = page.locator('input[name="rating_effect"]')
    expect(effect).to_have_count(6)
    effect.first.focus()
    expect(page.get_by_role("radio", name=re.compile("Not rated")).first).to_be_checked()
    traffic.clear()
    for expected in ("1", "2", "3"):
        page.keyboard.press("ArrowRight")
        expect(page.locator('input[name="rating_effect"]:checked')).to_have_value(expected)
    page.keyboard.press("ArrowLeft")
    expect(page.locator('input[name="rating_effect"]:checked')).to_have_value("2")
    assert traffic == []
    focused = page.evaluate("""() => {
      const a = document.activeElement;
      const face = getComputedStyle(a.nextElementSibling);
      return {name: a.name, value: a.value, checked: a.checked,
              outline: face.outlineStyle, inset: face.boxShadow.includes('inset')};
    }""")
    assert focused == {"name": "rating_effect", "value": "2", "checked": True,
                       "outline": "solid", "inset": True}, focused
    # The group has an accessible name and each option a spoken value.
    group = page.locator('[data-rating-field="rating_effect"]')
    expect(group.get_by_role("radio", name="2")).to_be_checked()
    expect(page.get_by_role("group", name="Effect")).to_have_count(1)
    # Tab leaves the whole dimension in one press.
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.name") == "rating_taste"


def test_add_status_delete_run_through_the_canonical_endpoints_with_the_same_payload(
    app, auth_user, training_page,
):
    with app.app_context():
        _ready(auth_user.id)
    page, traffic, _, _ = training_page
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    page.goto("http://localhost/supplements")
    expect(page.locator(".stack-empty")).to_be_visible()

    page.locator("#add-toggle").click()
    page.locator("#f-name").fill("Creatine Monohydrate")
    page.locator("#f-brand").fill("Bulk")
    page.locator('input[name="category"][value="Creatine"]').focus()
    page.keyboard.press("Space")
    page.locator('label.stack-choice:has(input[value="Low Stock"])').click()
    page.locator('input[name="rating_effect"]').first.focus()
    for _ in range(4):
        page.keyboard.press("ArrowRight")
    page.locator("#f-price").fill("750")
    page.locator("#f-public").focus()
    page.keyboard.press("Space")
    page.locator("#f-review").fill("Dissolves well.")

    page.evaluate("window.__pr6_document = true")
    traffic.clear()
    with page.expect_response(lambda r: r.url.endswith("/supplement/add")) as added:
        page.locator("#add-btn").click()
    assert added.value.ok
    page.wait_for_function("() => window.__pr6_document === undefined")

    posts = [(p, body) for p, body, _ in traffic if p.startswith("/supplement/")]
    assert [p for p, _ in posts] == ["/supplement/add"]
    assert json.loads(posts[0][1]) == {
        "product_name": "Creatine Monohydrate", "brand": "Bulk",
        "category": "Creatine", "status": "Low Stock",
        "rating_effect": 4, "rating_taste": None, "rating_digestion": None, "rating_price": None,
        "review_text": "Dissolves well.", "price_paid": "750", "is_public": False,
    }
    with app.app_context():
        row = Supplement.query.filter_by(user_id=auth_user.id).one()
        assert (row.category, row.status, row.rating_effect, row.rating_taste, row.is_public) == (
            "Creatine", "Low Stock", 4, None, False)
        sid = row.id

    card = page.locator(f"#supp-{sid}")
    expect(card).to_contain_text("4/5")
    expect(card.locator('[data-action="quickStatus"][aria-pressed="true"]')).to_have_count(1)
    page.evaluate("window.__pr6_document = true")
    traffic.clear()
    with page.expect_response(lambda r: r.url.endswith(f"/supplement/edit/{sid}")) as edited:
        card.locator('[data-action="quickStatus"]').nth(2).click()
    assert edited.value.ok
    page.wait_for_function("() => window.__pr6_document === undefined")
    edits = [(p, body) for p, body, _ in traffic if p.startswith("/supplement/")]
    assert edits == [(f"/supplement/edit/{sid}", '{"status":"Finished"}')]

    card = page.locator(f"#supp-{sid}")
    delete = card.locator('[data-action="deleteSupplement"]')
    styles = page.evaluate("""(sid) => {
      const card = document.getElementById('supp-' + sid);
      const del = card.querySelector('[data-action="deleteSupplement"]');
      const st = card.querySelector('[data-action="quickStatus"]');
      const pick = el => ({color: getComputedStyle(el).color, border: getComputedStyle(el).borderTopColor});
      const probe = document.createElement('span'); probe.style.color = 'var(--color-danger)';
      document.body.appendChild(probe); const danger = getComputedStyle(probe).color; probe.remove();
      return {del: pick(del), status: pick(st), danger};
    }""", sid)
    assert styles["del"]["color"] == styles["danger"] == styles["del"]["border"]
    assert styles["status"]["color"] != styles["danger"]
    traffic.clear()
    with page.expect_response(lambda r: r.url.endswith(f"/supplement/delete/{sid}")) as deleted:
        delete.click()
    assert deleted.value.ok
    assert dialogs == ["Delete this supplement?"]
    assert [p for p, _, _ in traffic if p.startswith("/supplement/")] == [f"/supplement/delete/{sid}"]
    with app.app_context():
        assert db.session.get(Supplement, sid) is None
    assert errors == []


@pytest.mark.parametrize("language", ["en", "tr"])
def test_supplements_geometry_emoji_and_overflow_across_the_matrix(
    app, auth_user, training_page, language,
):
    with app.app_context():
        _ready(auth_user.id, language)
        _seed_supplements(auth_user.id, 3)
    page, _, _, _ = training_page
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto("http://localhost/supplements")
        headings = page.evaluate("[...document.querySelectorAll('h1,h2')].map(h => h.tagName)")
        assert headings.count("H1") == 1 and "H2" in headings
        assert _small_controls(page) == [], (language, width, "collapsed")
        page.locator("#add-toggle").click()
        expect(page.locator("#add-btn")).to_be_visible()
        assert _small_controls(page) == [], (language, width, "expanded")
        text = page.locator("main").inner_text()
        assert EMOJI.findall(text) == [], (language, width)
        assert not re.search("[★☆]", text), (language, width)
        assert _no_overflow(page), (language, width)
        clipped = page.evaluate("""() => [...document.querySelectorAll(
            '.supp-status-btn, .btn-danger, .stack-choice-face, .stack-rating-face, .stack-add-summary')]
            .filter(el => el.checkVisibility() && el.scrollWidth > el.clientWidth + 1)
            .map(el => el.textContent.trim())""")
        assert clipped == [], (language, width, clipped)
    for width in PHONE_EDGES:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto("http://localhost/supplements")
        page.locator("#add-toggle").click()
        page.locator('[data-rating-field="rating_effect"] label:has(input[value=""])').click()
        assert _no_overflow(page), (language, width)
        assert _small_controls(page) == [], (language, width)
    assert errors == []


# ─────────────────────────────────────────────────────────────────────────────
# Nutrition
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("language", ["en", "tr"])
def test_nutrition_tabs_are_all_discoverable_touchable_and_keyboard_reachable(
    app, auth_user, training_page, language,
):
    with app.app_context():
        _ready(auth_user.id, language)
        db.session.add(UserSession(user_id=auth_user.id, target_calories=2100))
        _seed_meals(auth_user.id)
    page, traffic, _, _ = training_page
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto("http://localhost/nutrition")
        page.wait_for_selector(".meal-card")
        bar = page.evaluate("""() => {
          const bar = document.querySelector('.tab-bar'); const b = bar.getBoundingClientRect();
          const tabs = [...bar.querySelectorAll('[role=tab]')].map(t => {
            const r = t.getBoundingClientRect();
            return {h: r.height, left: r.left, right: r.right,
                    clipped: t.scrollWidth > t.clientWidth + 1,
                    visible: r.left >= b.left - 0.5 && r.right <= b.right + 0.5};
          });
          const cs = getComputedStyle(bar);
          return {tabs, scrollable: bar.scrollWidth > bar.clientWidth + 1,
                  mask: cs.maskImage !== 'none' || (cs.webkitMaskImage || 'none') !== 'none'};
        }""")
        assert len(bar["tabs"]) == 5
        # 43.5 matches `_small_controls` / the measure harness. Linux Chromium
        # reports CSS min-height: 44px as 43.99998 via getBoundingClientRect.
        assert all(t["h"] >= 43.5 for t in bar["tabs"]), (language, width, bar)
        assert not any(t["clipped"] for t in bar["tabs"]), (language, width, bar)
        if bar["scrollable"]:
            # Off-screen workflows must announce themselves on arrival.
            assert bar["mask"], (language, width, bar)
        else:
            assert all(t["visible"] for t in bar["tabs"]), (language, width, bar)
        for a, b in zip(bar["tabs"], bar["tabs"][1:]):
            assert a["right"] <= b["left"] + 0.5, ("tab collision", language, width)
        assert _no_overflow(page), (language, width)
        assert _small_controls(page) == [], (language, width)

    page.set_viewport_size({"width": 320, "height": 800})
    page.goto("http://localhost/nutrition")
    page.wait_for_selector(".meal-card")
    stops = _tab_walk(page)
    tab_ids = [s["id"] for s in stops if s["id"].startswith("nutrition-tab-")]
    assert tab_ids == ["nutrition-tab-today", "nutrition-tab-diary", "nutrition-tab-plan",
                       "nutrition-tab-history", "nutrition-tab-water"]
    assert all(s["indicator"] for s in stops)
    assert all(s["tabIndex"] <= 0 for s in stops)
    water = page.locator("#nutrition-tab-water")
    water.focus()
    page.keyboard.press("Enter")
    expect(water).to_have_attribute("aria-selected", "true")
    expect(page.locator("#panel-water")).to_be_visible()
    expect(page.locator("#panel-water h2")).to_be_visible()
    traffic.clear()
    page.set_viewport_size({"width": 700, "height": 800})
    page.mouse.wheel(0, 300)
    page.wait_for_timeout(200)
    assert [p for p, _, _ in traffic if not p.startswith("/static/")] == []
    assert errors == []


def test_nutrition_target_present_renders_the_canonical_value_unchanged(
    app, auth_user, training_page,
):
    with app.app_context():
        _ready(auth_user.id)
        db.session.add(UserSession(user_id=auth_user.id, target_calories=2100))
        _seed_meals(auth_user.id)
    page, _, _, _ = training_page
    with page.expect_response(lambda r: r.url.endswith("/meal-log/today")):
        page.goto("http://localhost/nutrition")
    hero = page.locator(".nut-hero")
    expect(hero).to_have_attribute("data-target-state", "known")
    expect(page.locator("#ring-target")).to_have_text("2100")
    expect(page.locator("#ring-pct")).to_have_text("59%")   # Math.round(1235 / 2100 * 100)
    expect(page.locator("#ring-pct")).to_be_visible()
    expect(page.locator("#ring-eaten")).to_have_text("1235")
    expect(page.locator(".nut-target-known")).to_be_visible()
    expect(page.locator(".nut-target-absent")).to_be_hidden()
    expect(page.locator("#macro-protein")).to_have_text("60")


@pytest.mark.parametrize("language", ["en", "tr"])
def test_nutrition_absent_target_is_intentional_and_never_numeric(
    app, auth_user, training_page, language,
):
    """No target from `nutrition_targets` — even with everything a fallback
    could be synthesized from sitting right there (profile, BMR, TDEE, goal)."""
    with app.app_context():
        _ready(auth_user.id, language)
        user = db.session.get(User, auth_user.id)
        user.weight, user.height, user.age, user.gender, user.goal = 80, 180, 30, "male", "kas kazanma"
        db.session.add(UserSession(user_id=auth_user.id, weight=80, height=180, age=30,
                                   goal="kas kazanma", bmr=1800, tdee=2500, target_calories=None))
        db.session.commit()
        _seed_meals(auth_user.id)
    page, _, _, _ = training_page
    with page.expect_response(lambda r: r.url.endswith("/meal-log/today")):
        page.goto("http://localhost/nutrition")
    hero = page.locator(".nut-hero")
    expect(hero).to_have_attribute("data-target-state", "absent")
    copy = {"en": "Target not set yet", "tr": "Hedef henüz belirlenmedi"}[language]
    expect(page.locator(".nut-target-absent")).to_be_visible()
    expect(page.locator(".nut-target-known")).to_be_hidden()
    # innerText, not textContent: what is rendered and exposed, not what is hidden.
    assert page.locator(".nut-hero-target").inner_text().strip() == copy
    expect(page.locator(".nut-hero-target")).to_match_aria_snapshot(f"- text: {copy}")
    expect(page.locator("#ring-pct")).to_be_hidden()
    visible_target = page.locator(".nut-hero-target").inner_text() + page.locator(".ring-label").inner_text()
    numbers = re.findall(r"\d+", visible_target)
    assert numbers == ["1235"], numbers          # what was eaten — never a target
    for invented in ("1800", "2500", "2000", "%"):
        assert invented not in visible_target
    assert page.locator(".pbar-fill").evaluate_all("els => els.map(e => e.style.width)") == ["0%"] * 3
