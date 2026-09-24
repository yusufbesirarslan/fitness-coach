"""WEB-UX4-PR8 - cross-surface consistency gate (source / server level).

UX4 PR2-PR7 each proved their own surface. This module turns the parts of
those contracts that are genuinely CROSS-surface into standing assertions, so
the converged product cannot drift one surface at a time:

  * token / control integrity outside the stylesheets PR2 already scans
    (scripts and templates), and the z-index scale across every UX4 stylesheet
  * every catalogue key a UX4 surface references exists in BOTH locales
  * server query budgets per surface and state, measured on d6d8675
  * F-40 - the 404 / 500 documents, the only runtime change in PR8

What it deliberately does NOT restate (already standing, cited so a reader
knows where the guard lives):

  * every ``var(--*)`` in static CSS + templates resolves ...... PR2 contract
  * ``--focus-ring`` never an ``outline`` value in static CSS ... PR2 contract
  * canonical button font-family / states / disabled rank ...... PR2 contract
  * ``set(en.json) == set(tr.json)`` + placeholders ............ test_i18n.py
  * /nutrition issues zero Supplement queries (8 / 0) ........... PR6 contract

The browser half of the gate is ``test_ux4_pr8_browser.py``.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import event

from ux4_gate_support import ready, seed_state

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"


def _strip_css_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _strip_js_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(^|[^:\\'\"])//[^\n]*", r"\1", text)


def _strip_jinja_comments(text):
    return re.sub(r"\{#.*?#\}", "", text, flags=re.S)


# ── token / control integrity ───────────────────────────────────────────────

# Every stylesheet a UX4-covered surface (or the auth entry) loads - measured
# from the request topology of each surface on d6d8675.
UX4_STYLESHEETS = ("tokens", "components", "theme", "nav", "auth", "today", "plan",
                   "coach_widget", "nutrition", "manage_stack", "progress",
                   "profile", "notifications")

# Raw (off-scale) z-index values that already existed on d6d8675, per file.
# They predate the --z-* scale and are local stacking inside a component
# (a reward overlay, the widget's own layers, the meal sheet). The gate lets
# them stay; it refuses any NEW raw value on a UX4 stylesheet.
Z_INDEX_BASELINE = {
    "today": Counter({"1000": 1}),
    "coach_widget": Counter({"20": 2, "30": 1, "auto": 1}),
    "nutrition": Counter({"2": 1, "100": 1}),
}


def _raw_z_indexes(name):
    css = _strip_css_comments((STATIC / f"{name}.css").read_text(encoding="utf-8"))
    return Counter(m.group(1).strip()
                   for m in re.finditer(r"z-index\s*:([^;}]+)", css)
                   if not m.group(1).strip().startswith("var(--z-"))


@pytest.mark.parametrize("name", UX4_STYLESHEETS)
def test_ux4_stylesheet_adds_no_off_scale_z_index(name):
    """A new raw z-index on a UX4 surface is how layer wars start again.

    PR2 pinned components.css and nav.css to the --z-* scale; PR3 pinned Coach.
    This extends the same rule to every stylesheet a UX4 surface loads,
    grandfathering only what was already there.
    """
    allowed = Z_INDEX_BASELINE.get(name, Counter())
    found = _raw_z_indexes(name)
    new = found - allowed
    assert not new, (
        "static/%s.css introduces off-scale z-index value(s) %s; use a --z-* "
        "token (allowed baseline for this file: %s)"
        % (name, dict(new), dict(allowed) or "none"))


def _defined_custom_properties():
    defined = set()
    for path in sorted(STATIC.glob("*.css")) + sorted(TEMPLATES.glob("*.html")):
        defined.update(re.findall(r"(--[A-Za-z0-9_-]+)\s*:",
                                  path.read_text(encoding="utf-8")))
    return defined


@pytest.mark.parametrize("path", sorted(p.name for p in STATIC.glob("*.js")))
def test_script_references_only_defined_custom_properties(path):
    """PR2 resolves every ``var()`` in CSS and templates. Scripts write styles
    too (``el.style.x = 'var(--y)'``), and an undefined property there erases
    the declaration exactly as silently - the scan PR2 did not cover."""
    source = _strip_js_comments((STATIC / path).read_text(encoding="utf-8"))
    refs = set(re.findall(r"var\(\s*(--[A-Za-z0-9_-]+)", source))
    missing = sorted(refs - _defined_custom_properties())
    assert not missing, "static/%s references undefined custom properties %s" % (
        path, missing)


def test_focus_ring_is_never_an_outline_value_in_templates_or_scripts():
    """PR2 forbids ``outline: var(--focus-ring)`` in stylesheets; the same
    computed-value failure (outline resolves to ``none``) happens in an inline
    <style> block or a script-written style."""
    offenders = []
    for path in sorted(TEMPLATES.glob("*.html")) + sorted(STATIC.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"outline(?:-style|-color)?\s*[:=]\s*[^;\n]*var\(\s*--focus-ring", text):
            offenders.append(path.name)
    assert not offenders, "--focus-ring used as an outline value in %s" % offenders


# ── i18n ────────────────────────────────────────────────────────────────────

# The documents and modules that render the UX4-covered surfaces (and the
# shared shell they sit in). Community pages are out of UX4 scope.
UX4_TEMPLATES = ("today", "plan", "coach_v2", "nutrition", "manage_stack",
                 "progress", "_progress_header", "_progress_current_state",
                 "_progress_trends", "_progress_axis_insight",
                 "_progress_physique", "_progress_recent_checkins",
                 "_progress_checkin_sheet", "edit_profile", "notifications", "_head", "_nav",
                 "_actionbar", "404", "500", "login", "register",
                 "forgot_password")
UX4_SCRIPTS = ("today", "training_plan_management", "plan_training_manage",
               "plan_workout", "workout_execution", "workout_state_client",
               "weekly_program", "workout_draft", "coach_widget", "nutrition",
               "progress", "progress_presentation", "progress_insights",
               "progress_physique", "profile", "actions", "i18n", "modal", "auth")

_T_CALL = re.compile(r"""\b(?:t|__t)\(\s*['"]([a-z0-9_]+(?:\.[a-z0-9_]+)+)['"]""")


def _catalogue(code):
    with open(ROOT / "locales" / f"{code}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _referenced_keys(kind, name):
    if kind == "template":
        text = _strip_jinja_comments((TEMPLATES / f"{name}.html").read_text(encoding="utf-8"))
    else:
        text = _strip_js_comments((STATIC / f"{name}.js").read_text(encoding="utf-8"))
    return set(_T_CALL.findall(text))


@pytest.mark.parametrize("kind,name",
                         [("template", n) for n in UX4_TEMPLATES]
                         + [("script", n) for n in UX4_SCRIPTS])
def test_every_key_a_ux4_surface_references_exists_in_both_locales(kind, name):
    """A missing key paints itself: server ``t()`` and client ``t()`` both fall
    back to the raw key (EN additionally falls back to TR first, so a key
    missing only from en.json paints TURKISH on an English screen).

    Parity alone (test_i18n) cannot catch a key that is referenced but missing
    from BOTH catalogues; this can.
    """
    keys = _referenced_keys(kind, name)
    en, tr = _catalogue("en"), _catalogue("tr")
    missing_en = sorted(k for k in keys if k not in en)
    missing_tr = sorted(k for k in keys if k not in tr)
    assert not missing_en and not missing_tr, (
        "%s %s references keys missing from en.json %s / tr.json %s"
        % (kind, name, missing_en, missing_tr))


def test_the_reference_scan_is_not_vacuous():
    """If the regex stopped matching, every file above would pass on zero keys."""
    total = sum(len(_referenced_keys("template", n)) for n in UX4_TEMPLATES) + \
        sum(len(_referenced_keys("script", n)) for n in UX4_SCRIPTS)
    assert total >= 400, "only %d catalogue references found - scan is broken" % total
    assert "error.back_home" in _referenced_keys("template", "404")


# ── query budgets ───────────────────────────────────────────────────────────

def _selects(client, path):
    from app.extensions import db
    statements = []

    def record(_conn, _cur, statement, _params, _ctx, _many):
        text = " ".join(statement.lower().split())
        if text.startswith("select"):
            statements.append(text)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        response = client.get(path)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    assert response.status_code == 200, (path, response.status_code)
    return statements


# (surface, state, path, SELECT budget, Supplement SELECTs) - measured on
# d6d8675 with the ux4_gate_support seeds, on a WARM request: the first request
# after login additionally loads the session user (+3 SELECTs), which is login
# cost, not page cost. SQLite (the CI pytest engine).
QUERY_BUDGETS = [
    ("today", "no_plan", "/", 8, 0),
    ("today", "scheduled", "/", 8, 0),
    ("today", "rest_day", "/", 8, 0),
    ("today", "in_progress", "/", 12, 0),
    ("plan", "no_plan", "/training", 11, 1),
    ("plan", "scheduled_start", "/training", 11, 1),
    ("plan", "active_rest_day", "/training", 11, 1),
    ("plan", "active_resume", "/training", 15, 1),
    ("coach", "populated", "/coach", 4, 0),
    ("coach", "populated", "/coach/history", 6, 0),
    ("progress", "insufficient", "/progress-page", 5, 0),
    ("progress", "populated", "/progress-page", 4, 0),
    ("progress", "insufficient", "/api/progress/summary", 7, 0),
    ("progress", "populated", "/api/progress/summary", 6, 0),
    ("account", "goal_selected", "/edit-profile", 6, 1),
    ("notifications", "populated", "/notifications", 4, 0),
    ("notifications", "populated", "/notifications/data?before_id=0&limit=20", 6, 0),
]


@pytest.mark.parametrize("surface,state,path,budget,supplement", QUERY_BUDGETS,
                         ids=["%s-%s-%s" % (s, st, p.split("?")[0].strip("/").replace("/", "_") or "root")
                              for s, st, p, _b, _q in QUERY_BUDGETS])
def test_server_query_budget_does_not_widen(app, client, auth_user, monkeypatch,
                                            surface, state, path, budget, supplement):
    """A page gaining a SELECT is the first step of an N+1 nobody sees in
    review. The budget is a ceiling: getting cheaper passes, and whoever widens
    it has to change this number on purpose."""
    ready(app, auth_user.id, "en")
    seed_state(app, client, monkeypatch, auth_user.id, surface, state)
    _selects(client, path)                      # warm: pays the login cost
    statements = _selects(client, path)
    assert len(statements) <= budget, (
        "surface=%s state=%s %s issues %d SELECTs, budget %d:\n  %s"
        % (surface, state, path, len(statements), budget, "\n  ".join(statements)))
    assert sum("from supplement" in s for s in statements) == supplement, (
        "surface=%s state=%s %s Supplement SELECT count changed" % (surface, state, path))


@pytest.mark.parametrize("state,sessions,budget", [
    ("scheduled_start", False, 7),
    ("active_rest_day", False, 7),
    ("active_resume", True, 11),
])
def test_canonical_plan_fact_gathering_budget(app, client, auth_user, monkeypatch,
                                              state, sessions, budget):
    """``gather_plan_facts`` is the one server fact read behind Plan. Its
    envelope is state-dependent only through the workout-session flag; any
    other widening is accidental."""
    from app.extensions import db
    from app.services.plan_facts import gather_plan_facts

    ready(app, auth_user.id, "en")
    seed_state(app, client, monkeypatch, auth_user.id, "plan", state)
    assert app.config.get("FITX_WORKOUT_SESSIONS_ENABLED", False) is sessions
    statements = []

    def record(_conn, _cur, statement, _params, _ctx, _many):
        if " ".join(statement.lower().split()).startswith("select"):
            statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        with app.test_request_context():
            gather_plan_facts(auth_user.id, sessions_enabled=sessions)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    assert len(statements) <= budget, (
        "gather_plan_facts(%s) issued %d SELECTs, budget %d"
        % (state, len(statements), budget))


# ── F-40 - the error documents ──────────────────────────────────────────────

ERROR_PAGES = ("404", "500")


def _source(code):
    return (TEMPLATES / f"{code}.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("code", ERROR_PAGES)
def test_error_numeral_sits_on_the_product_display_scale(code):
    """Discovery F-40: the numeral rendered at 120px, more than twice the
    product's largest display step (--text-display-lg tops out at 52px)."""
    css = _strip_css_comments(_strip_jinja_comments(_source(code)))
    h1 =re.search(r"(?m)^\s*h1\s*\{([^}]*)\}", css)
    assert h1, "%s.html no longer styles its numeral" % code
    size = re.search(r"font-size\s*:\s*([^;]+);", h1.group(1)).group(1).strip()
    assert "120px" not in css
    assert size == "clamp(38px, 6vw, 52px)", (
        "%s.html numeral must use the --text-display-lg values, got %r" % (code, size))
    assert "Bebas Neue" in h1.group(1), "the numeral lost the display face"


@pytest.mark.parametrize("code", ERROR_PAGES)
def test_error_document_adds_no_asset_and_keeps_its_nonce(code):
    """No new dependency: the only external request stays the existing Inter +
    Bebas Neue stylesheet, and the only inline block stays nonce'd."""
    html = _strip_jinja_comments(_source(code))
    assert re.findall(r"https?://[^\s\"')]+", html) == [
        "https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Inter:wght@300;400&display=swap"]
    assert "<script" not in html
    assert re.findall(r"<style[^>]*>", html) == ['<style nonce="{{ csp_nonce }}">']
    assert not re.search(r"\sstyle=", html), "inline style attributes bypass the nonce"
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html


def test_the_two_error_documents_differ_only_in_their_code_and_copy():
    normalise = lambda s, code: s.replace(code, "CODE")
    assert normalise(_source("404"), "404") == normalise(_source("500"), "500")


@pytest.mark.parametrize("language,copy", [
    ("en", ("Page Not Found", "The page you&#39;re looking for couldn&#39;t be found.", "Back to home")),
    ("tr", ("Sayfa Bulunamadı", "Aradığın sayfa bulunamadı.", "Ana sayfaya dön")),
])
def test_404_keeps_status_copy_nonce_and_one_route_home(app, client, make_user, login,
                                                          language, copy):
    make_user("pr8err", language=language, profile_complete=True)
    login("pr8err")
    response = client.get("/pr8-this-route-does-not-exist")
    assert response.status_code == 404
    html = response.get_data(as_text=True)
    title, body, home = copy
    assert "<title>%s</title>" % title in html
    assert body in html
    assert re.findall(r'<a href="([^"]*)">([^<]*)</a>', html) == [("/", home)]
    assert '<html lang="%s">' % language in html
    nonce = re.search(r'<style nonce="([^"]+)">', html).group(1)
    assert "'nonce-%s'" % nonce in response.headers.get("Content-Security-Policy", "")


def _break_a_view(app, monkeypatch, path="/login"):
    endpoint = app.url_map.bind("localhost").match(path)[0]

    def boom(*_a, **_kw):
        raise RuntimeError("pr8 induced failure")

    app.config["PROPAGATE_EXCEPTIONS"] = False
    monkeypatch.setitem(app.view_functions, endpoint, boom)


def test_500_keeps_status_copy_nonce_and_one_route_home(app, client, monkeypatch):
    _break_a_view(app, monkeypatch)
    response = client.get("/login")
    assert response.status_code == 500
    html = response.get_data(as_text=True)
    assert "<h1>500</h1>" in html
    assert "<title>Sunucu Hatası</title>" in html          # anonymous default is TR
    assert re.findall(r'<a href="([^"]*)">([^<]*)</a>', html) == [("/", "Ana sayfaya dön")]
    nonce = re.search(r'<style nonce="([^"]+)">', html).group(1)
    assert "'nonce-%s'" % nonce in response.headers.get("Content-Security-Policy", "")
