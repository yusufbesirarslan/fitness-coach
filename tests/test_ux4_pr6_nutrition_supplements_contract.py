"""WEB-UX4-PR6 — Nutrition & Supplements experience convergence (contract).

Server-rendered checks against what a user actually receives. The browser half
(geometry, keyboard, network, target presentation) lives in
``test_ux4_pr6_nutrition_supplements_browser.py``.

Owned findings: F-08 cabinet first · F-15 naming · F-16 absent target ·
F-17 headings · F-20 Supplements iconography · F-34 inline typography ·
F-35 tabs · F-36 destructive rank. Authority boundaries are asserted too:
no Supplement query on /nutrition, no new query on /supplements, canonical
category/status/rating values unchanged, one editor.
"""
import html as html_lib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from sqlalchemy import event

ROOT = Path(__file__).resolve().parents[1]

CANONICAL_CATEGORIES = ["Protein", "Amino Acid", "Pre-Workout", "Vitamin/Health", "Creatine", "Other"]
CANONICAL_STATUSES = ["Active", "Low Stock", "Finished"]
RATING_FIELDS = ["rating_effect", "rating_taste", "rating_digestion", "rating_price"]

# Emoji / pictographic blocks. `★`/`☆` (U+2605/2606) sit in Misc Symbols, so the
# glyph check below is separate and explicit rather than an accident of ranges.
_PICTOGRAPHIC = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")
_STAR_GLYPHS = re.compile("[★☆]")


def _login(client, make_user, login, username, language="en"):
    user = make_user(username, profile_complete=True, language=language)
    login(username)
    return user


def _body(html):
    """Rendered page without <script> blocks: `_head.html` injects the whole
    locale catalog into every page, so a copy assertion against raw HTML is
    true whether or not the page renders the sentence."""
    return re.sub(r"(?is)<script[^>]*>.*?</script>", "", html)


def _add(user_id, *specs):
    from app.extensions import db
    from app.models import Supplement

    rows = []
    for spec in specs:
        row = Supplement(user_id=user_id, **{
            "product_name": "Whey", "brand": "Brand", "category": "Protein",
            "status": "Active", **spec})
        db.session.add(row)
        rows.append(row)
    db.session.commit()
    return rows


class _Outline(HTMLParser):
    """Headings (level, text) plus the document positions of chosen ids."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headings, self.ids, self.title = [], {}, ""
        self._open = None
        self._in_title = False
        self._pos = 0

    def handle_starttag(self, tag, attrs):
        self._pos += 1
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.setdefault(attrs["id"], (self._pos, tag, attrs))
        if re.fullmatch(r"h[1-6]", tag):
            self._open = [int(tag[1]), ""]
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if self._open and tag == f"h{self._open[0]}":
            self.headings.append((self._open[0], " ".join(self._open[1].split())))
            self._open = None
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._open:
            self._open[1] += data
        if self._in_title:
            self.title += data


def _outline(html):
    parser = _Outline()
    parser.feed(_body(html))
    return parser


def _assert_heading_structure(outline):
    levels = [level for level, _ in outline.headings]
    assert levels.count(1) == 1, outline.headings
    assert levels[0] == 1, outline.headings
    assert 2 in levels, outline.headings
    for previous, current in zip(levels, levels[1:]):
        assert current <= previous + 1, ("skipped heading level", outline.headings)
    for level, text in outline.headings:
        assert text, ("empty heading", level)


# ─────────────────────────────────────────────────────────────────────────────
# F-34 — zero inline font-size in nutrition.html
# ─────────────────────────────────────────────────────────────────────────────

INLINE_STYLE = re.compile(r"""\bstyle\s*=\s*("([^"]*)"|'([^']*)')""", re.I)


def inline_font_size_count(source):
    count = 0
    for match in INLINE_STYLE.finditer(source):
        value = match.group(2) if match.group(2) is not None else match.group(3)
        count += len(re.findall(r"(?<![-\w])font-size\s*:", value, re.I))
    return count


def test_nutrition_template_has_zero_inline_font_size():
    source = (ROOT / "templates" / "nutrition.html").read_text(encoding="utf-8")
    assert inline_font_size_count(source) == 0


def test_inline_font_size_detector_is_not_vacuous():
    assert inline_font_size_count('<p style="color:red; font-size:12px">') == 1
    assert inline_font_size_count("<p style='FONT-SIZE : 1rem'>") == 1
    assert inline_font_size_count('<p style="--x-font-size:1px">') == 0


# ─────────────────────────────────────────────────────────────────────────────
# F-15 / F-17 — naming and heading structure (TR + EN, rendered)
# ─────────────────────────────────────────────────────────────────────────────

NAMES = {
    "en": {"nutrition": "Nutrition", "plan_tab": "Nutrition Plan", "supplements": "Supplements"},
    "tr": {"nutrition": "Beslenme", "plan_tab": "Beslenme Planı", "supplements": "Takviyeler"},
}


@pytest.mark.parametrize("language", ["en", "tr"])
def test_nutrition_destination_name_and_headings(app, client, make_user, login, language):
    user = _login(client, make_user, login, f"pr6-nut-{language}", language)
    _add(user.id, {})  # a cabinet exists; Nutrition must still not read it
    html = client.get("/nutrition").get_data(as_text=True)
    outline = _outline(html)
    names = NAMES[language]

    _assert_heading_structure(outline)
    h1 = [text for level, text in outline.headings if level == 1][0]
    assert h1.casefold() == names["nutrition"].casefold()

    tab = re.search(r'id="nutrition-tab-plan"[^>]*>(.*?)</button>', html, re.S).group(1)
    tab_label = " ".join(re.sub(r"(?s)<svg.*?</svg>", "", tab).split())
    assert tab_label == names["plan_tab"]
    assert tab_label.casefold() != h1.casefold()
    assert names["nutrition"] in outline.title

    # Exactly five local workflow tabs; Supplements is not one of them.
    assert html.count('role="tab"') == 5
    assert 'data-tab-name="supplements"' not in html


@pytest.mark.parametrize("language", ["en", "tr"])
def test_supplements_destination_has_one_name(app, client, make_user, login, language):
    user = _login(client, make_user, login, f"pr6-supp-{language}", language)
    _add(user.id, {"product_name": "Creatine", "category": "Creatine"})
    html = client.get("/supplements").get_data(as_text=True)
    outline = _outline(html)
    name = NAMES[language]["supplements"]

    _assert_heading_structure(outline)
    h1 = [text for level, text in outline.headings if level == 1][0]
    crumb = re.search(r'<span aria-current="page">(.*?)</span>', _body(html)).group(1).strip()
    assert h1 == name
    assert crumb == name
    assert outline.title.split("—")[0].strip() == name
    # "Cabinet"/"Dolap" may describe the page; it may not name it.
    assert "Cabinet" not in h1 and "Dolab" not in h1
    h2 = [text for level, text in outline.headings if level == 2]
    assert len(h2) >= 2, h2


# ─────────────────────────────────────────────────────────────────────────────
# F-08 — cabinet first, Add deliberately disclosed
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("count", [0, 1, 4])
def test_cabinet_precedes_the_add_disclosure_in_the_dom(app, client, make_user, login, count):
    user = _login(client, make_user, login, f"pr6-order-{count}")
    _add(user.id, *[{"product_name": f"S{i}"} for i in range(count)])
    html = client.get("/supplements").get_data(as_text=True)
    outline = _outline(html)

    cabinet_pos = outline.ids["supp-list"][0]
    add_pos, add_tag, add_attrs = outline.ids["add-card"]
    assert cabinet_pos < add_pos
    assert outline.ids["f-name"][0] > add_pos
    assert outline.ids["add-btn"][0] > add_pos
    # Native disclosure, closed on arrival — even for an empty cabinet.
    assert add_tag == "details"
    assert "open" not in add_attrs
    assert html.count('data-action="addSupplement"') == 1
    body = _body(html)
    if count == 0:
        assert "No supplements saved yet." in body
    else:
        assert body.count('class="supp-card"') == count


# ─────────────────────────────────────────────────────────────────────────────
# F-20 — no emoji, no ★/☆
# ─────────────────────────────────────────────────────────────────────────────

def _rated_cabinet(user_id):
    return _add(
        user_id,
        *[{"product_name": f"S-{cat}", "category": cat, "status": status,
           "rating_effect": 4, "rating_taste": 5, "rating_digestion": 1, "rating_price": 3,
           "review_text": "Solid."}
          for cat, status in zip(CANONICAL_CATEGORIES + ["Unknown Legacy"],
                                 CANONICAL_STATUSES * 3)])


def emoji_in(text):
    return _PICTOGRAPHIC.findall(text)


def test_supplements_renders_no_emoji_and_no_star_glyphs(app, client, make_user, login):
    user = _login(client, make_user, login, "pr6-glyphs")
    _rated_cabinet(user.id)
    body = _body(client.get("/supplements").get_data(as_text=True))
    assert emoji_in(body) == []
    assert _STAR_GLYPHS.findall(body) == []
    # Every category (and an unknown legacy value) renders a system SVG tile.
    assert body.count('class="icon-tile icon-tile-sm icon-tile-soft supp-icon"><svg') == 7
    # Saved ratings are readable numbers, not glyph rows.
    assert body.count(">4/5<") == 7 and body.count(">1/5<") == 7


def test_emoji_detector_is_not_vacuous():
    from app.blueprints.supplements import CATEGORY_ICONS

    for icon in CATEGORY_ICONS.values():
        assert emoji_in(icon), icon
    assert _STAR_GLYPHS.findall("★☆")


# ─────────────────────────────────────────────────────────────────────────────
# Canonical values, rating radio groups, labels, visibility default
# ─────────────────────────────────────────────────────────────────────────────

def _radios(html, name):
    return re.findall(
        rf'<input type="radio" name="{re.escape(name)}" value="([^"]*)"( checked)?>', html)


def test_form_choices_carry_the_canonical_values_and_defaults(app, client, make_user, login):
    from app.blueprints.supplements import SUPPLEMENT_CATEGORIES, SUPPLEMENT_STATUSES

    assert SUPPLEMENT_CATEGORIES == CANONICAL_CATEGORIES
    assert SUPPLEMENT_STATUSES == CANONICAL_STATUSES

    _login(client, make_user, login, "pr6-values", language="tr")
    html = client.get("/supplements").get_data(as_text=True)

    categories = _radios(html, "category")
    assert [value for value, _ in categories] == CANONICAL_CATEGORIES
    assert [value for value, checked in categories if checked] == ["Protein"]
    statuses = _radios(html, "status")
    assert [value for value, _ in statuses] == CANONICAL_STATUSES
    assert [value for value, checked in statuses if checked] == ["Active"]
    for field in RATING_FIELDS:
        options = _radios(html, field)
        assert [value for value, _ in options] == ["", "1", "2", "3", "4", "5"], field
        assert [value for value, checked in options if checked] == [""], field
        assert f'data-rating-field="{field}"' in html
    assert '<input type="checkbox" role="switch" id="f-public" checked>' in html
    # TR presentation is localized; the submitted values above are not.
    assert "Antrenman Öncesi" in _body(html)


def test_every_visible_supplement_input_has_a_real_label(app, client, make_user, login):
    _login(client, make_user, login, "pr6-labels")
    html = client.get("/supplements").get_data(as_text=True)
    for field in ("f-name", "f-brand", "f-price", "f-review", "f-public"):
        assert re.search(rf'<label[^>]*for="{field}"', html), field
    for group in ("category", "status", *RATING_FIELDS):
        # Each radio group lives in a <fieldset> with a <legend> name.
        before = html.split(f'name="{group}"', 1)[0]
        assert before.rfind("<fieldset") > before.rfind("</fieldset>"), group
        assert "<legend>" in before[before.rfind("<fieldset"):], group


def test_submit_script_reads_canonical_values_and_never_sends_zero(app):
    source = (ROOT / "templates" / "manage_stack.html").read_text(encoding="utf-8")
    script = source.split("async function addSupplement()", 1)[1].split("async function quickStatus", 1)[0]
    for key in ("product_name", "brand", "category", "status", *RATING_FIELDS,
                "review_text", "price_paid", "is_public"):
        assert re.search(rf"\b{key}\s*:", script), key
    assert "checkedValue('category', 'Protein')" in script
    assert "checkedValue('status', 'Active')" in script
    assert "document.getElementById('f-public').checked" in script
    assert "return raw ? parseInt(raw, 10) : null;" in source


# ─────────────────────────────────────────────────────────────────────────────
# F-36 — destructive rank
# ─────────────────────────────────────────────────────────────────────────────

def test_delete_is_the_canonical_destructive_rank_apart_from_status(app, client, make_user, login):
    user = _login(client, make_user, login, "pr6-rank")
    row, = _add(user.id, {"product_name": "Zinc", "status": "Low Stock"})
    html = client.get("/supplements").get_data(as_text=True)
    actions = html.split('class="supp-actions"', 1)[1].split("</div>", 1)[0]

    delete = re.search(r'<button[^>]*data-action="deleteSupplement"[^>]*>', actions).group(0)
    assert 'class="btn-danger supp-delete"' in delete
    assert f"data-args='[{row.id}]'" in delete
    assert 'aria-label="Delete Zinc"' in delete
    statuses = re.findall(r'<button[^>]*data-action="quickStatus"[^>]*>', actions)
    assert len(statuses) == 3
    assert all("btn-danger" not in b and "btn-volt" not in b for b in statuses)
    pressed = [b for b in statuses if 'aria-pressed="true"' in b]
    assert len(pressed) == 1 and '"Low Stock"' in pressed[0]
    # The destructive control is not inside the status group.
    assert actions.index("</span>") < actions.index('data-action="deleteSupplement"')


# ─────────────────────────────────────────────────────────────────────────────
# Authority: query budget, endpoints, one editor, backend untouched
# ─────────────────────────────────────────────────────────────────────────────

def _selects(client, path):
    from app.extensions import db

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


# Measured on the authorized baseline 8ab9cda with this exact fixture (8 / 9),
# minus exactly one SELECT: F12 made session touch() a single atomic UPDATE of
# CognitoSession.last_used_at instead of SELECT-then-ORM-flush.
@pytest.mark.parametrize("path,total,supplement", [
    ("/nutrition", 7, 0),
    ("/supplements", 8, 1),
])
def test_page_query_budget_is_unchanged(app, client, make_user, login, path, total, supplement):
    user = _login(client, make_user, login, f"pr6-q-{path.strip('/')}")
    _rated_cabinet(user.id)
    statements = _selects(client, path)
    assert sum("from supplement" in s for s in statements) == supplement, statements
    assert len(statements) == total, statements


def test_supplement_routes_are_unchanged(app):
    rules = {r.rule: sorted(r.methods - {"HEAD", "OPTIONS"})
             for r in app.url_map.iter_rules() if "supplement" in r.rule}
    assert rules == {
        "/supplements": ["GET"],
        "/supplement/add": ["POST"],
        "/supplement/edit/<int:sid>": ["POST"],
        "/supplement/delete/<int:sid>": ["POST"],
    }


def test_nutrition_absent_target_copy_exists_and_invents_no_number():
    for language in ("en", "tr"):
        catalog = json.loads((ROOT / "locales" / f"{language}.json").read_text(encoding="utf-8"))
        copy = catalog["nutrition.target_absent"]
        assert copy.strip() and not re.search(r"\d", copy), (language, copy)
    css = (ROOT / "static" / "nutrition.css").read_text(encoding="utf-8")
    assert '.nut-hero[data-target-state="absent"] .nut-target-known { display: none; }' in css


@pytest.mark.parametrize("language", ["en", "tr"])
def test_each_nutrition_workflow_panel_opens_with_a_real_section_heading(
    app, client, make_user, login, language,
):
    """F-17 per panel, not per page: whichever workflow tab is showing, AT users
    get a section heading below the page title. The Nutrition Plan panel's
    content is rendered by the plan client and is out of scope."""
    _login(client, make_user, login, f"pr6-panels-{language}", language)
    html = _body(client.get("/nutrition").get_data(as_text=True))
    expected = {
        "panel-today": "nutrition.todays_meals",
        "panel-diary": "nutrition.diary_builder",
        "panel-history": "nutrition.weekly_cal",
        "panel-water": "nutrition.water_tracking",
    }
    catalog = json.loads((ROOT / "locales" / f"{language}.json").read_text(encoding="utf-8"))
    for panel, key in expected.items():
        section = html.split(f'id="{panel}"', 1)[1].split('class="tab-panel', 1)[0]
        first_h2 = re.search(r"<h2[^>]*>(.*?)</h2>", section, re.S)
        assert first_h2, panel
        text = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", "", first_h2.group(1))).split())
        assert text == catalog[key], (panel, text)
