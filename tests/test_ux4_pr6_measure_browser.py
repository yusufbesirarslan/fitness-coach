"""WEB-UX4-PR6 measurement harness — Nutrition + Supplements.

Not a contract test: it RECORDS geometry, typography, heading, keyboard and
network facts for one commit so the same numbers can be compared on another.
It only runs when ``PR6_MEASURE_DIR`` names an output directory; CI skips it.

Hermetic like every browser suite here: the page is the real rendered template,
and every browser request is served by the authenticated Flask test client.
"""
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import MealLog, Supplement, User, UserSession
from app.timeutil import app_today
from test_training_execution_boundary import training_page  # noqa: F401

OUT = os.environ.get("PR6_MEASURE_DIR")
pytestmark = pytest.mark.skipif(not OUT, reason="set PR6_MEASURE_DIR to record")

WIDTHS = (320, 390, 768, 1024, 1366)
GLOBAL_CHROME = "header, .global-header, .action-bar, nav.action-bar, #cw-root"

PROBE = r"""
({ratingSel, cabinetSel, addSel}) => {
  const chrome = document.querySelectorAll('header, .global-header, .action-bar, #cw-root');
  const inChrome = el => [...chrome].some(c => c.contains(el));
  const shown = el => {
    if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const main = document.querySelector('main');
  const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')]
    .filter(shown).map(h => h.tagName.toLowerCase() + ':' + h.textContent.replace(/\s+/g, ' ').trim().slice(0, 48));
  const sizes = new Set(); const families = new Set(); let arial = 0; const arialEls = [];
  for (const el of main.querySelectorAll('*')) {
    if (!shown(el)) continue;
    const ownText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const isControl = el.matches('button, input, select, textarea, summary');
    if (!ownText && !isControl) continue;
    const cs = getComputedStyle(el);
    sizes.add(cs.fontSize);
    const fam = cs.fontFamily.split(',')[0].replace(/["']/g, '').trim();
    families.add(fam);
    if (fam === 'Arial') { arial++; arialEls.push(el.tagName + '.' + el.className); }
  }
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=radio], [role=switch], [role=tab], [tabindex]:not([tabindex="-1"])';
  const controls = [];
  for (const el of document.querySelectorAll(sel)) {
    if (inChrome(el)) continue;
    let box = el;
    const r0 = el.getBoundingClientRect();
    if (el.matches('input[type=radio], input[type=checkbox]') && (r0.width < 2 || r0.height < 2 || getComputedStyle(el).opacity === '0')) {
      box = el.closest('label') || document.querySelector(`label[for="${el.id}"]`) || el;
    }
    if (!shown(box)) continue;
    const r = box.getBoundingClientRect();
    controls.push({d: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : ''),
                   w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10});
  }
  const sub44 = controls.filter(c => c.w < 43.5 || c.h < 43.5);
  const text = main.innerText;
  const emoji = (text.match(/\p{Extended_Pictographic}/gu) || []).filter(c => c !== '★' && c !== '☆');
  const stars = (text.match(/[★☆]/g) || []).length;
  const out = {
    docHeight: document.documentElement.scrollHeight,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    headings, textSizes: [...sizes].sort((a, b) => parseFloat(a) - parseFloat(b)),
    families: [...families], arial, arialEls: arialEls.slice(0, 12),
    controls: controls.length, sub44: sub44.length, sub44List: sub44.slice(0, 40),
    emoji: emoji.length, emojiChars: [...new Set(emoji)], stars,
  };
  const cab = cabinetSel && document.querySelector(cabinetSel);
  const add = addSel && document.querySelector(addSel);
  if (cab && add) {
    out.cabinetY = Math.round(cab.getBoundingClientRect().top + scrollY);
    out.addY = Math.round(add.getBoundingClientRect().top + scrollY);
    out.cabinetBeforeAddInDom = Boolean(cab.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING);
  }
  const bar = document.querySelector('.tab-bar');
  if (bar && shown(bar)) {
    const b = bar.getBoundingClientRect();
    const tabs = [...bar.querySelectorAll('.tab-btn')].map(t => {
      const r = t.getBoundingClientRect();
      const vis = Math.max(0, Math.min(r.right, b.right) - Math.max(r.left, b.left));
      return {label: t.textContent.trim(), w: Math.round(r.width), h: Math.round(r.height), visibleFraction: Math.round(vis / r.width * 100) / 100};
    });
    out.tabBar = {width: Math.round(b.width), scrollWidth: bar.scrollWidth, clientWidth: bar.clientWidth,
                  scrollable: bar.scrollWidth > bar.clientWidth + 1,
                  fullyVisible: tabs.filter(t => t.visibleFraction >= 0.99).length,
                  visibleSum: Math.round(tabs.reduce((s, t) => s + t.visibleFraction, 0) * 100) / 100,
                  maskImage: getComputedStyle(bar).maskImage || getComputedStyle(bar).webkitMaskImage, tabs};
  }
  const del = document.querySelector('[data-action="deleteSupplement"]');
  const st = document.querySelector('[data-action="quickStatus"]');
  if (del && st) {
    const pick = el => { const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
      return {cls: el.className, color: cs.color, border: cs.borderTopColor, weight: cs.fontWeight, h: Math.round(r.height), w: Math.round(r.width)}; };
    out.deleteRank = {del: pick(del), status: pick(st)};
  }
  const tgt = document.getElementById('ring-target');
  if (tgt) {
    out.target = {ringTarget: tgt.textContent.trim(), ringPct: document.getElementById('ring-pct').textContent.trim(),
                  heroTarget: document.querySelector('.nut-hero-target').innerText.replace(/\s+/g, ' ').trim(),
                  ringLabel: document.querySelector('.ring-label').innerText.replace(/\s+/g, ' ').trim()};
  }
  const child = document.querySelector('.nutrition-child-domain');
  if (child && bar) {
    out.childOutsideTablist = !bar.contains(child);
    out.childAfterTablist = Boolean(bar.compareDocumentPosition(child) & Node.DOCUMENT_POSITION_FOLLOWING);
  }
  return out;
}
"""

TAB_WALK = r"""
(ratingSel) => {
  const chrome = document.querySelectorAll('header, .global-header, .action-bar, #cw-root');
  const inChrome = el => [...chrome].some(c => c.contains(el));
  const d = el => el === document.body ? 'body' : el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
      + (el.getAttribute('data-action') ? '[' + el.getAttribute('data-action') + ']' : '')
      + (el.getAttribute('name') ? '[name=' + el.getAttribute('name') + ']' : '')
      + ' ' + (el.getAttribute('aria-label') || el.textContent || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 24);
  const a = document.activeElement;
  const seen = a.hasAttribute('data-pr6-walk');
  if (a !== document.body) a.setAttribute('data-pr6-walk', '1');
  return {seen, d: d(document.activeElement), chrome: inChrome(document.activeElement),
          rating: Boolean(ratingSel && document.activeElement.closest(ratingSel)),
          positive: document.activeElement.tabIndex > 0,
          outline: getComputedStyle(document.activeElement).outlineStyle,
          shadow: getComputedStyle(document.activeElement).boxShadow};
}
"""


def _walk(page, rating_sel=None, limit=160):
    page.evaluate(
        "() => { const s = document.createElement('span'); s.id = '__pr6_start';"
        " s.tabIndex = -1; document.body.prepend(s); s.focus(); }")
    stops = []
    for _ in range(limit):
        page.keyboard.press("Tab")
        info = page.evaluate(TAB_WALK, rating_sel)
        if info["d"] == "body" or info["seen"]:
            break
        stops.append(info)
    page.evaluate("() => { document.getElementById('__pr6_start')?.remove();"
                  " document.querySelectorAll('[data-pr6-walk]').forEach(e => e.removeAttribute('data-pr6-walk')); }")
    return {
        "total": len(stops),
        "page": sum(1 for s in stops if not s["chrome"]),
        "rating": sum(1 for s in stops if s["rating"]),
        "positiveTabindex": sum(1 for s in stops if s["positive"]),
        "noIndicator": [s["d"] for s in stops
                        if s["outline"] == "none" and s["shadow"] in ("none", "")],
        "sequence": [("C " if s["chrome"] else "P ") + s["d"] for s in stops],
    }


def _write(name, data):
    path = Path(OUT)
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{name}.json").write_text(
        json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def _ready(user_id, language):
    user = db.session.get(User, user_id)
    user.profile_complete = True
    user.language = language
    db.session.commit()


def _seed_nutrition(user_id, *, target, meals):
    if target:
        db.session.add(UserSession(user_id=user_id, target_calories=2100))
    if meals:
        for slot, food, kcal in (("Kahvaltı", "Yulaf", 525), ("Öğle", "Tavuk pilav", 710)):
            db.session.add(MealLog(user_id=user_id, ogun=slot, yemekler=food,
                                   kalori=kcal, protein=30, karb=60, yag=12,
                                   tarih=app_today().isoformat()))
    db.session.commit()


def _seed_supplements(user_id, count):
    rows = [
        dict(product_name="Gold Standard Whey", brand="Optimum Nutrition", category="Protein",
             status="Active", rating_effect=4, rating_taste=5, rating_digestion=3, rating_price=4,
             review_text="Mixes cleanly, easy on the stomach after training."),
        dict(product_name="Creatine Monohydrate", brand="Bulk", category="Creatine",
             status="Low Stock", rating_effect=5),
        dict(product_name="Magnesium Bisglycinate with a very long product name for wrapping",
             brand="A brand name that is also rather long", category="Vitamin/Health",
             status="Finished"),
        dict(product_name="EAA", brand="Myprotein", category="Amino Acid", status="Active"),
        dict(product_name="Pump Pre", brand="Applied", category="Pre-Workout", status="Active"),
        dict(product_name="ZMA", brand="House", category="Other", status="Active"),
    ]
    for spec in rows[:count]:
        db.session.add(Supplement(user_id=user_id, **spec))
    db.session.commit()


def _load(page, traffic, url, wait_path=None):
    traffic.clear()
    if wait_path:
        with page.expect_response(lambda r: r.url.endswith(wait_path)):
            page.goto(url)
    else:
        page.goto(url)
    page.wait_for_load_state("networkidle")
    app_requests = [p for p, _, _ in traffic if not p.startswith("/static/")]
    return {"requests": len(traffic), "appRequests": app_requests}


@pytest.mark.parametrize("language", ["en", "tr"])
@pytest.mark.parametrize("target", [True, False], ids=["target", "notarget"])
def test_measure_nutrition(app, auth_user, training_page, language, target):
    with app.app_context():
        _ready(auth_user.id, language)
        _seed_nutrition(auth_user.id, target=target, meals=True)
    page, traffic, _, _ = training_page
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    cells = {}
    widths = WIDTHS if target else (390, 1366)
    for width in widths:
        page.set_viewport_size({"width": width, "height": 900})
        net = _load(page, traffic, "http://localhost/nutrition", "/meal-log/today")
        page.wait_for_selector(".meal-card, .mc-edit, .slot-empty")
        cell = page.evaluate(PROBE, {"ratingSel": None, "cabinetSel": None, "addSel": None})
        cell["network"] = net
        if width in (390, 1366):
            page.screenshot(path=str(Path(OUT) / f"nutrition-{language}-{'t' if target else 'nt'}-{width}.png"), full_page=True)
        if width == 390 and target:
            traffic.clear()
            page.set_viewport_size({"width": 390, "height": 700})
            page.mouse.wheel(0, 400)
            page.wait_for_timeout(150)
            cell["resizeScrollRequests"] = len(traffic)
            cell["tabWalk"] = _walk(page)
        cells[width] = cell
    _write(f"nutrition-{language}-{'target' if target else 'notarget'}", {"cells": cells, "errors": errors})


@pytest.mark.parametrize("language", ["en", "tr"])
@pytest.mark.parametrize("count", [0, 1, 6], ids=["zero", "one", "many"])
def test_measure_supplements(app, auth_user, training_page, language, count):
    with app.app_context():
        _ready(auth_user.id, language)
        _seed_supplements(auth_user.id, count)
    page, traffic, _, _ = training_page
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    cells = {}
    widths = WIDTHS if count == 6 else (390, 1366)
    selectors = {"ratingSel": ".star-group, [data-rating-field]",
                 "cabinetSel": "#supp-list", "addSel": "#add-card"}
    for width in widths:
        page.set_viewport_size({"width": width, "height": 900})
        net = _load(page, traffic, "http://localhost/supplements")
        cell = page.evaluate(PROBE, selectors)
        cell["network"] = net
        if width in (390, 1366):
            page.screenshot(path=str(Path(OUT) / f"supplements-{language}-{count}-{width}.png"), full_page=True)
        if width == 390:
            cell["tabWalk"] = _walk(page, selectors["ratingSel"])
            summary = page.locator("#add-card > summary")
            if summary.count():
                traffic.clear()
                summary.click()
                cell["openRequests"] = len(traffic)
                cell["expanded"] = page.evaluate(PROBE, selectors)
                cell["expanded"]["tabWalk"] = _walk(page, selectors["ratingSel"])
                page.screenshot(path=str(Path(OUT) / f"supplements-{language}-{count}-{width}-open.png"), full_page=True)
                traffic.clear()
                summary.click()
                cell["closeRequests"] = len(traffic)
        cells[width] = cell
    _write(f"supplements-{language}-{count}", {"cells": cells, "errors": errors})


def _selects(client, path):
    statements = []

    def record(_c, _cur, statement, _p, _ctx, _many):
        text = " ".join(statement.lower().split())
        if text.startswith("select"):
            statements.append(text)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        response = client.get(path)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    assert response.status_code == 200
    return statements


def test_measure_query_budget(app, client, auth_user):
    _ready(auth_user.id, "en")
    _seed_nutrition(auth_user.id, target=True, meals=True)
    _seed_supplements(auth_user.id, 6)
    data = {}
    for path in ("/nutrition", "/supplements", "/meal-log/today"):
        statements = _selects(client, path)
        data[path] = {"selects": len(statements),
                      "supplementSelects": sum("from supplement" in s for s in statements)}
    root = Path(__file__).resolve().parents[1]
    import re
    tpl = (root / "templates" / "nutrition.html").read_text(encoding="utf-8")
    styles = re.findall(r'style\s*=\s*"([^"]*)"', tpl)
    data["nutrition_inline"] = {
        prop: sum(len(re.findall(rf"(?<![-\w]){prop}\s*:", s)) for s in styles)
        for prop in ("font-size", "font-family", "font-weight", "letter-spacing", "line-height", "color")
    }
    _write("queries", data)
