"""WEB-UX4-PR2 - what a real browser computes for the repaired foundations.

The contract suite (``test_ux4_pr2_foundation_contract.py``) reads source. This
one presses ``Tab`` in Chromium and measures what a user would actually see,
because every defect UX4 discovery found was invisible to source review:

  * ``outline: var(--focus-ring, ...)`` parses fine and computes to ``none``
  * ``--space-7`` reads like a spacing step and silently erases the declaration
  * ``.btn-ghost`` has no ``font-family``, so the ELEMENT picks the typeface

A control does NOT pass here because ``document.activeElement`` moved. It
passes when the browser paints an indicator that (a) differs from the control's
unfocused painting and (b) clears 3:1 against the backdrop actually behind it.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from app.extensions import db
from app.models import MealLog, User
from app.timeutil import app_today

# Today / Plan / Coach / Progress are the primary IA; Nutrition, Supplements,
# Account and Notifications are the other surfaces discovery audited.
SURFACES = {
    "today": "/",
    "plan": "/training",
    "coach": "/coach",
    "progress": "/progress-page",
    "nutrition": "/nutrition",
    "supplements": "/supplements",
    "account": "/edit-profile",
    "notifications": "/notifications",
}
VIEWPORTS = (320, 390, 768, 1024, 1366)


_FOCUS_JS = r"""
(() => {
  const parse = c => {
    const m = /rgba?\(([^)]+)\)/.exec(c || '');
    if (!m) return null;
    const p = m[1].split(',').map(s => parseFloat(s));
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
  };
  const lin = v => { v /= 255; return v <= 0.04045 ? v / 12.92
                                                   : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = c => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  const over = (fg, bg) => ({r: fg.r * fg.a + bg.r * (1 - fg.a),
                             g: fg.g * fg.a + bg.g * (1 - fg.a),
                             b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1});
  const ratio = (x, y) => {
    const a = lum(x), b = lum(y);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  };
  // What the ring is seen against. An outline (and a non-inset box-shadow
  // ring) is painted OUTSIDE the border box, so the element's own fill is not
  // the backdrop - a primary-filled .btn-volt would otherwise measure its blue
  // ring against its own blue face and score 1.00:1.
  const backdrop = el => {
    for (let n = el.parentElement; n && n !== document.documentElement; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a === 1) return c;
    }
    const c = parse(getComputedStyle(document.body).backgroundColor);
    return (c && c.a === 1) ? c : {r: 18, g: 18, b: 18, a: 1};
  };
  window.__pr2 = {
    describe(el) {
      const cs = getComputedStyle(el);
      return {outline: cs.outlineStyle + ' ' + cs.outlineWidth + ' ' + cs.outlineColor,
              boxShadow: cs.boxShadow, borderColor: cs.borderColor};
    },
    // Strongest ring the element paints, expressed as contrast vs its backdrop.
    indicator(el) {
      const cs = getComputedStyle(el);
      const bg = backdrop(el);
      let best = 0, how = null;
      const w = parseFloat(cs.outlineWidth) || 0;
      if (cs.outlineStyle !== 'none' && w > 0) {
        const c = parse(cs.outlineColor);
        if (c && c.a > 0) { best = ratio(over(c, bg), bg); how = 'outline'; }
      }
      if (cs.boxShadow && cs.boxShadow !== 'none') {
        const found = cs.boxShadow.match(/rgba?\([^)]+\)/g) || [];
        for (const raw of found) {
          const c = parse(raw);
          if (!c || c.a === 0) continue;
          const r = ratio(over(c, bg), bg);
          if (r > best) { best = r; how = 'box-shadow'; }
        }
      }
      return {contrast: best, how, outlineWidth: w, outlineStyle: cs.outlineStyle};
    },
    label(el) {
      const id = el.id ? '#' + el.id : '';
      const cls = String(el.className || '').split(/\s+/).filter(Boolean)
        .slice(0, 3).map(c => '.' + c).join('');
      return el.tagName.toLowerCase() + id + cls
             + ' "' + (el.textContent || '').trim().slice(0, 24) + '"';
    },
    isVisible(el) {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden'
             && cs.opacity !== '0';
    },
  };
})();
"""


def _make_router(client):
    def route_request(route):
        request = route.request
        url = urlsplit(request.url)
        if url.netloc != "localhost":
            route.abort()
            return
        response = client.open(
            url.path + ("?" + url.query if url.query else ""),
            method=request.method, data=request.post_data,
            headers={k: v for k, v in request.headers.items()
                     if k.lower() in {"content-type", "origin", "x-csrftoken"}})
        route.fulfill(status=response.status_code, body=response.get_data(),
                      headers={k: v for k, v in response.headers.items()
                               if k.lower() not in {"content-length", "set-cookie"}})
    return route_request


@pytest.fixture
def surface(client):
    """A Chromium page whose HTTP is served by the authenticated Flask client.

    Nothing leaves the process: every non-localhost request (Google Fonts,
    analytics) is aborted, so the run is hermetic. The typeface measured is the
    DECLARED stack, which is exactly what the Arial-leak defect is about.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(bypass_csp=True,
                                      viewport={"width": 390, "height": 844})
        context.route("**/*", _make_router(client))
        page = context.new_page()
        page.add_init_script(_FOCUS_JS)
        yield page
        browser.close()


def _ready(user_id, language="en"):
    user = db.session.get(User, user_id)
    user.profile_complete = True
    user.language = language
    db.session.commit()


# A focus ring that fades in over 150ms is still a valid indicator, so the
# walk must let the transition finish before measuring - otherwise it reads an
# animation frame (e.g. rgb(7, 16, 29) partway from black to primary) and
# reports a defect that does not exist.
_SETTLE_MS = 200


def _visit(page, path):
    page.goto("http://localhost" + path, wait_until="domcontentloaded")
    # /coach opens its widget from a setInterval that polls for up to ~2s, so a
    # fixed sleep races it. Wait for the widget to settle, bounded.
    try:
        page.wait_for_function(
            "() => { const w = document.getElementById('cw-window');"
            " return !w || (!w.inert && !w.closest('[inert]')); }", timeout=3000)
    except Exception:
        pass
    page.wait_for_timeout(_SETTLE_MS)


def _keyboard_walk(page, limit=40):
    """Tab through the document, one record per real, visible stop."""
    page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); }")
    stops, seen = [], set()
    for _ in range(limit):
        page.keyboard.press("Tab")
        page.wait_for_timeout(_SETTLE_MS)   # let the focus transition finish
        record = page.evaluate(r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  if (!window.__pr2.isVisible(el)) return {skipped: true};
  window.__pr2.current = el;
  const key = window.__pr2.label(el) + '|'
            + Math.round(el.getBoundingClientRect().top);
  const focused = window.__pr2.describe(el);
  const indicator = window.__pr2.indicator(el);
  el.blur();
  // Some surfaces keep a control focused (the Coach widget re-focuses its own
  // composer on open), so the element cannot be observed at rest at all.
  return {key: key, label: window.__pr2.label(el),
          selfFocusing: document.activeElement === el,
          focused: focused, indicator: indicator};
}""")
        if record is None or record.get("skipped"):
            continue
        # Read the resting painting only AFTER the focus transition has run
        # back out. Reading it synchronously after blur() catches the ring
        # mid-fade and makes every transitioned indicator look unchanged.
        page.wait_for_timeout(_SETTLE_MS)
        record["resting"] = page.evaluate(
            "() => { const el = window.__pr2.current;"
            " const r = window.__pr2.describe(el); el.focus(); return r; }")
        if record["key"] in seen:
            break
        seen.add(record["key"])
        stops.append(record)
    return stops


def _assert_focus_is_perceptible(stops, where):
    assert stops, "no keyboard stops found on %s" % where
    invisible = []
    for stop in stops:
        indicator = stop["indicator"]
        # The "does focusing change the painting" guard exists to stop a
        # permanent decoration passing as a focus indicator. It is only
        # meaningful for a control that can actually be observed at rest.
        changed = stop["selfFocusing"] or stop["resting"] != stop["focused"]
        if indicator["contrast"] < 3.0 or not changed:
            invisible.append(
                "%s -> %.2f:1 via %s (painting changed on focus: %s; outline %s)"
                % (stop["label"], indicator["contrast"], indicator["how"],
                   changed, stop["focused"]["outline"]))
    assert not invisible, (
        "%d of %d keyboard stops on %s compute an effectively invisible focus "
        "state:\n  %s" % (len(invisible), len(stops), where,
                          "\n  ".join(invisible)))


# --- F-02 / F-03 - focus is visible on every stop -----------------------

@pytest.mark.parametrize("name", sorted(SURFACES))
def test_every_keyboard_stop_shows_a_visible_focus_indicator(
        app, auth_user, surface, name):
    """The hard exit criterion: zero audited controls compute an effectively
    invisible ``:focus-visible`` state.

    A stop fails if its indicator is below 3:1 against the backdrop behind it,
    or if focusing changes nothing about how it is painted - neither of which
    ``document.activeElement`` moving can satisfy.
    """
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    with app.app_context():
        _ready(auth_user.id)
    _visit(surface, SURFACES[name])
    _assert_focus_is_perceptible(_keyboard_walk(surface), name)


def test_login_keyboard_stops_show_a_visible_focus_indicator(surface):
    """The acquisition surface is audited too - it is the product's first screen."""
    _visit(surface, "/login")
    _assert_focus_is_perceptible(_keyboard_walk(surface), "/login")


def _log_meal(user_id):
    """`.mc-edit` / `.mc-del` only exist once a meal is logged.

    Without this the two controls discovery singled out - "delete a logged
    meal" above all - would silently skip, and a skipped test protects nothing.
    """
    db.session.add(MealLog(user_id=user_id, ogun="Breakfast",
                           yemekler="Oats, 2 eggs", kalori=520.0, protein=32.0,
                           karb=54.0, yag=18.0, tarih=app_today().isoformat()))
    db.session.commit()


@pytest.mark.parametrize("selector", [
    ".mc-edit", ".mc-del", ".qab", ".log-fab", ".slot-empty",
])
def test_known_focus_erasing_controls_are_repaired(
        app, auth_user, surface, selector):
    """The controls F-03 measured at ``outline-style: none`` under a real
    ``Tab`` - including "delete a logged meal", which a keyboard user could
    land on with no indication at all."""
    with app.app_context():
        _ready(auth_user.id)
        _log_meal(auth_user.id)
    _visit(surface, "/nutrition")
    surface.wait_for_selector(selector, timeout=5000)
    surface.locator(selector).first.focus()
    measured = surface.evaluate(
        "sel => window.__pr2.indicator(document.querySelector(sel))", selector)
    assert measured["contrast"] >= 3.0, (
        "%s focus indicator measures %.2f:1, below the 3:1 bar (%s)"
        % (selector, measured["contrast"], measured))


def test_focus_does_not_move_the_page(app, auth_user, surface):
    """An indicator that reflows the layout is its own defect."""
    with app.app_context():
        _ready(auth_user.id)
    _visit(surface, "/training")
    before = surface.evaluate("() => document.documentElement.scrollHeight")
    for _ in range(12):
        surface.keyboard.press("Tab")
    after = surface.evaluate("() => document.documentElement.scrollHeight")
    assert before == after, (
        "keyboard focus changed document height %s -> %s" % (before, after))


# --- F-10 - no browser-default typography -------------------------------

@pytest.mark.parametrize("name", sorted(SURFACES))
def test_no_control_computes_to_the_browser_default_typeface(
        app, auth_user, surface, name):
    """``.btn-ghost`` computed Arial as a <button> and Inter as an <a>; 41
    Arial elements were attributed across four surfaces.

    Chromium's UA sheet sets ``font: 400 13.333px Arial`` on form controls, so
    a control that declares nothing computes exactly ``Arial``.
    """
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    with app.app_context():
        _ready(auth_user.id)
    _visit(surface, SURFACES[name])
    leaked = surface.evaluate(r"""
() => [...document.querySelectorAll('button, input, select, textarea, [role=button]')]
  .filter(el => window.__pr2.isVisible(el))
  .filter(el => /^(Arial|Times New Roman|-webkit-standard)$/
                  .test(getComputedStyle(el).fontFamily.replace(/["']/g, '')))
  .map(el => window.__pr2.label(el) + ' -> ' + getComputedStyle(el).fontFamily)
  .slice(0, 25)""")
    assert not leaked, (
        "%s renders %d control(s) in the browser default face:\n  %s"
        % (name, len(leaked), "\n  ".join(leaked)))


def test_btn_ghost_computes_one_typeface_for_button_and_anchor(
        app, auth_user, surface):
    """The same class must not render in two faces depending on its element."""
    with app.app_context():
        _ready(auth_user.id)
    _visit(surface, "/training")
    faces = surface.evaluate(r"""
() => {
  const mk = tag => {
    const el = document.createElement(tag);
    el.className = 'btn-ghost';
    el.textContent = 'x';
    if (tag === 'a') el.href = '#';
    document.body.appendChild(el);
    const f = getComputedStyle(el).fontFamily;
    el.remove();
    return f;
  };
  return {button: mk('button'), anchor: mk('a')};
}""")
    assert faces["button"] == faces["anchor"], (
        ".btn-ghost renders as %(button)s on <button> and %(anchor)s on <a>"
        % faces)
    assert "Arial" not in faces["button"], faces


# --- F-10 / F-21 / F-36 - one button family -----------------------------

@pytest.mark.parametrize("selector,expect_face", [
    (".btn-volt", "Bebas Neue"),
    (".btn-ghost", "Inter"),
    (".btn-danger", "Inter"),
])
def test_canonical_buttons_render_as_one_control_family(
        app, auth_user, surface, selector, expect_face):
    """Measured baseline: ghost 32-35px vs volt 45px, radius 8 vs 12, Arial vs
    Bebas. The three must compute one height and one radius."""
    with app.app_context():
        _ready(auth_user.id)
    _visit(surface, "/training")
    # `opacity` is transitioned, so it must be read AFTER the transition has
    # run - reading it synchronously after setting `disabled` returns the
    # starting value (1) and makes a working rule look broken.
    measured = surface.evaluate(r"""
async sel => {
  const el = document.createElement('button');
  el.className = sel.slice(1);
  el.textContent = 'Action';
  document.body.appendChild(el);
  const cs = getComputedStyle(el);
  const out = {font: cs.fontFamily, height: el.getBoundingClientRect().height,
               radius: cs.borderTopLeftRadius, cursor: cs.cursor,
               enabledOpacity: cs.opacity};
  el.disabled = true;
  await new Promise(r => setTimeout(r, 400));
  const dis = getComputedStyle(el);
  out.disabledOpacity = dis.opacity;
  out.disabledCursor = dis.cursor;
  el.remove();
  return out;
}""", selector)
    assert expect_face in measured["font"], (
        "%s renders in %s, expected %s" % (selector, measured["font"], expect_face))
    # One family means one height, not merely "each clears the floor": the
    # baseline defect was ghost at 32-35px beside volt at 45px.
    assert measured["height"] == 44, (
        "%s computes %.1fpx tall; the family shares a 44px control height"
        % (selector, measured["height"]))
    assert measured["radius"] == "12px", (
        "%s radius is %s, not the canonical 12px" % (selector, measured["radius"]))
    assert measured["cursor"] == "pointer", measured
    assert float(measured["enabledOpacity"]) == 1.0, (
        "%s is already dimmed while enabled (%s)" % (selector, measured))
    assert float(measured["disabledOpacity"]) < 1.0, (
        "%s does not dim when disabled (%s)" % (selector, measured))
    assert measured["disabledCursor"] == "not-allowed", (
        "%s keeps a clickable cursor while disabled (%s)" % (selector, measured))


def test_btn_ghost_as_a_link_is_not_underlined_and_stays_focusable(
        app, auth_user, surface):
    """Links using ``.btn-ghost`` must not regress into underlined text or
    lose keyboard focus behaviour."""
    with app.app_context():
        _ready(auth_user.id)
    _visit(surface, "/training")
    result = surface.evaluate(r"""
() => {
  const a = document.createElement('a');
  a.className = 'btn-ghost'; a.href = '#x'; a.textContent = 'Ask the coach';
  document.body.appendChild(a);
  a.focus();
  const cs = getComputedStyle(a);
  const out = {decoration: cs.textDecorationLine,
               focused: document.activeElement === a,
               indicator: window.__pr2.indicator(a),
               height: a.getBoundingClientRect().height};
  a.remove();
  return out;
}""")
    assert result["decoration"] == "none", result
    assert result["focused"] is True, result
    assert result["indicator"]["contrast"] >= 3.0, result
    assert result["height"] >= 44, result


def test_btn_danger_does_not_read_as_the_primary_call_to_action(
        app, auth_user, surface):
    """F-36: destroy must not sit at the same visual rank as confirm."""
    with app.app_context():
        _ready(auth_user.id)
    _visit(surface, "/training")
    measured = surface.evaluate(r"""
() => {
  const mk = cls => {
    const el = document.createElement('button');
    el.className = cls; el.textContent = 'Go';
    document.body.appendChild(el);
    const cs = getComputedStyle(el);
    const out = {bg: cs.backgroundColor, color: cs.color,
                 borderWidth: cs.borderTopWidth, borderColor: cs.borderTopColor};
    el.remove();
    return out;
  };
  return {volt: mk('btn-volt'), danger: mk('btn-danger')};
}""")
    volt, danger = measured["volt"], measured["danger"]
    assert danger["bg"] in ("rgba(0, 0, 0, 0)", "transparent"), (
        "`.btn-danger` still paints a solid fill like the primary CTA: %s" % danger)
    assert volt["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), (
        "the primary CTA lost its solid fill: %s" % volt)
    assert float(danger["borderWidth"].rstrip("px")) > 0, danger


# --- F-01 - auth card padding -------------------------------------------

@pytest.mark.parametrize("width", VIEWPORTS)
def test_auth_card_has_real_padding_at_every_viewport(surface, width):
    """``--space-7`` is undefined, so ``.auth-card``'s padding declaration was
    dropped and the sign-in card's content sat flush against its own border at
    768, 1024 and 1366 - the first screen of the product on desktop."""
    surface.set_viewport_size({"width": width, "height": 900})
    _visit(surface, "/login")
    padding = surface.evaluate(r"""
() => {
  const el = document.querySelector('.auth-card');
  if (!el) return null;
  const cs = getComputedStyle(el);
  return {top: parseFloat(cs.paddingTop), right: parseFloat(cs.paddingRight),
          bottom: parseFloat(cs.paddingBottom), left: parseFloat(cs.paddingLeft)};
}""")
    assert padding, ".auth-card not rendered at %dpx" % width
    assert min(padding.values()) >= 20, (
        ".auth-card inset collapsed to %s at %dpx" % (padding, width))


@pytest.mark.parametrize("path", ["/login", "/register", "/forgot-password"])
def test_the_whole_acquisition_path_shares_the_repaired_card(surface, path):
    """The same primitive serves every acquisition screen."""
    surface.set_viewport_size({"width": 1366, "height": 900})
    response = surface.goto("http://localhost" + path,
                            wait_until="domcontentloaded")
    if response is None or response.status >= 400:
        pytest.skip("%s is not reachable anonymously" % path)
    padding = surface.evaluate(
        "() => { const e = document.querySelector('.auth-card');"
        " return e ? parseFloat(getComputedStyle(e).paddingTop) : null; }")
    if padding is None:
        pytest.skip("%s does not use .auth-card" % path)
    assert padding > 0, "%s .auth-card padding is %s" % (path, padding)


# --- F-11 / F-38 - the shared Modal primitive ---------------------------

_FIXTURE = r"""
() => {
  document.body.insertAdjacentHTML('beforeend',
    '<button id="pr2-opener" class="btn-ghost">Open</button>'
  + '<a id="pr2-background-link" href="#bg">background link</a>'
  + '<div class="modal-backdrop" id="pr2-modal" role="dialog" aria-modal="true">'
  + '<div class="modal"><div class="modal-header">'
  + '<h2 class="modal-title">Fixture</h2>'
  + '<button class="modal-close" id="pr2-close">x</button></div>'
  + '<div class="modal-body"><input id="pr2-input"><a href="#a" id="pr2-link">link</a></div>'
  + '<div class="modal-footer"><button class="btn-volt" id="pr2-confirm">Confirm</button>'
  + '</div></div></div>');
}
"""


@pytest.fixture
def modal_fixture(surface):
    """The canonical Modal contract only - not one of the 21 product dialogs.

    PR2 hardens the primitive; each journey PR migrates its own dialogs.
    """
    _visit(surface, "/login")
    surface.evaluate(_FIXTURE)
    return surface


def test_modal_primitive_is_available_from_the_shared_shell(modal_fixture):
    assert modal_fixture.evaluate(
        "() => !!window.AxisModal && typeof window.AxisModal.open === 'function'"
        " && typeof window.AxisModal.close === 'function'"), (
        "the shared shell exposes no behaviour for the canonical Modal primitive")


def test_modal_moves_focus_into_the_dialog_on_open(modal_fixture):
    result = modal_fixture.evaluate(r"""
() => {
  document.getElementById('pr2-opener').focus();
  window.AxisModal.open(document.getElementById('pr2-modal'));
  const modal = document.getElementById('pr2-modal');
  return {inside: modal.contains(document.activeElement),
          active: window.__pr2.label(document.activeElement)};
}""")
    assert result["inside"], "focus stayed outside the dialog: %s" % result


def test_tab_from_the_last_control_wraps_to_the_first(modal_fixture):
    modal_fixture.evaluate(
        "() => window.AxisModal.open(document.getElementById('pr2-modal'))")
    modal_fixture.evaluate("() => document.getElementById('pr2-confirm').focus()")
    modal_fixture.keyboard.press("Tab")
    assert modal_fixture.evaluate("() => document.activeElement.id") == "pr2-close", (
        "Tab from the last control did not wrap to the first")


def test_shift_tab_from_the_first_control_wraps_to_the_last(modal_fixture):
    modal_fixture.evaluate(
        "() => window.AxisModal.open(document.getElementById('pr2-modal'))")
    modal_fixture.evaluate("() => document.getElementById('pr2-close').focus()")
    modal_fixture.keyboard.press("Shift+Tab")
    assert modal_fixture.evaluate("() => document.activeElement.id") == "pr2-confirm", (
        "Shift+Tab from the first control did not wrap to the last")


def test_keyboard_cannot_escape_into_the_isolated_background(modal_fixture):
    """21 dialogs carried ``aria-modal="true"``; exactly one trapped focus."""
    modal_fixture.evaluate(
        "() => window.AxisModal.open(document.getElementById('pr2-modal'))")
    escaped = []
    for _ in range(10):
        modal_fixture.keyboard.press("Tab")
        where = modal_fixture.evaluate(
            "() => ({inside: document.getElementById('pr2-modal')"
            ".contains(document.activeElement),"
            " label: window.__pr2.label(document.activeElement)})")
        if not where["inside"]:
            escaped.append(where["label"])
    assert not escaped, "focus escaped the dialog to: %s" % escaped


def test_background_is_not_interactable_while_the_modal_is_open(modal_fixture):
    state = modal_fixture.evaluate(r"""
() => {
  window.AxisModal.open(document.getElementById('pr2-modal'));
  const link = document.getElementById('pr2-background-link');
  link.focus();
  return {inert: !!link.closest('[inert]') || link.inert === true,
          focusable: document.activeElement === link};
}""")
    assert state["inert"], "background content was not isolated"
    assert not state["focusable"], "background link still took focus"


def test_isolation_reaches_the_whole_page_when_the_backdrop_is_nested(
        modal_fixture):
    """A consumer may place the backdrop inside a wrapper rather than at top
    level. Marking only its immediate siblings would leave the rest of the
    page interactive - a half-isolated dialog, invisible in review."""
    state = modal_fixture.evaluate(r"""
() => {
  const wrap = document.createElement('div');
  document.body.appendChild(wrap);
  const modal = document.getElementById('pr2-modal');
  wrap.appendChild(modal);
  window.AxisModal.open(modal);
  const link = document.getElementById('pr2-background-link');
  link.focus();
  const out = {inert: !!link.closest('[inert]') || link.inert === true,
               focusable: document.activeElement === link,
               dialogReachable: !modal.closest('[inert]')};
  window.AxisModal.close(modal);
  document.body.appendChild(modal);
  wrap.remove();
  out.releasedAfterClose = !(link.closest('[inert]') || link.inert === true);
  return out;
}""")
    assert state["inert"], "a nested backdrop left the page interactive"
    assert not state["focusable"], state
    assert state["dialogReachable"], "the dialog isolated itself"
    assert state["releasedAfterClose"], "isolation was not released on close"


def test_escape_closes_a_dismissible_dialog(modal_fixture):
    modal_fixture.evaluate(
        "() => window.AxisModal.open(document.getElementById('pr2-modal'),"
        " {dismissible: true})")
    modal_fixture.keyboard.press("Escape")
    assert not modal_fixture.evaluate(
        "() => document.getElementById('pr2-modal').classList.contains('open')"), (
        "Escape did not close a dismissible dialog")


def test_escape_does_not_close_a_destructive_confirmation(modal_fixture):
    """The primitive must not assume every modal is dismissible - a delete
    confirmation must not vanish on a stray key."""
    modal_fixture.evaluate(
        "() => window.AxisModal.open(document.getElementById('pr2-modal'),"
        " {dismissible: false})")
    modal_fixture.keyboard.press("Escape")
    assert modal_fixture.evaluate(
        "() => document.getElementById('pr2-modal').classList.contains('open')"), (
        "Escape closed a dialog that declared itself non-dismissible")


def test_closing_restores_focus_to_the_opener(modal_fixture):
    restored = modal_fixture.evaluate(r"""
() => {
  const opener = document.getElementById('pr2-opener');
  opener.focus();
  window.AxisModal.open(document.getElementById('pr2-modal'));
  window.AxisModal.close(document.getElementById('pr2-modal'));
  return document.activeElement === opener;
}""")
    assert restored, "focus was not returned to the opener"


def test_closing_restores_the_background_and_leaves_no_stale_state(modal_fixture):
    """No stale ``inert``, no stale key handler, no trap after teardown."""
    state = modal_fixture.evaluate(r"""
() => {
  const modal = document.getElementById('pr2-modal');
  window.AxisModal.open(modal);
  window.AxisModal.close(modal);
  const link = document.getElementById('pr2-background-link');
  link.focus();
  return {inert: !!link.closest('[inert]') || link.inert === true,
          focusable: document.activeElement === link,
          open: modal.classList.contains('open')};
}""")
    assert not state["inert"], "background stayed inert after close"
    assert state["focusable"], "background did not become interactable again"
    assert not state["open"], state


def test_escape_after_close_does_not_reach_a_stale_handler(modal_fixture):
    """A handler left bound after teardown would swallow the application's
    own keyboard shortcuts."""
    seen = modal_fixture.evaluate(r"""
() => {
  const modal = document.getElementById('pr2-modal');
  window.AxisModal.open(modal);
  window.AxisModal.close(modal);
  let reached = 0;
  const probe = e => { if (e.key === 'Escape') reached += 1; };
  document.addEventListener('keydown', probe);
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  document.removeEventListener('keydown', probe);
  return {reached: reached, open: modal.classList.contains('open')};
}""")
    assert seen["reached"] == 1, (
        "a stale handler intercepted Escape after teardown: %s" % seen)
    assert not seen["open"], seen


def test_a_removed_opener_does_not_strand_focus(modal_fixture):
    """Restoring to a removed element would lose focus entirely."""
    landed = modal_fixture.evaluate(r"""
() => {
  const opener = document.getElementById('pr2-opener');
  opener.focus();
  const modal = document.getElementById('pr2-modal');
  window.AxisModal.open(modal);
  opener.remove();
  window.AxisModal.close(modal);
  return {tag: document.activeElement ? document.activeElement.tagName : null,
          connected: !!document.activeElement && document.activeElement.isConnected};
}""")
    assert landed["connected"], "focus was stranded on a detached node: %s" % landed


def test_nested_dialogs_unwind_in_order(modal_fixture):
    """Nested-safe cleanup: closing the inner dialog must not release the
    outer one's isolation."""
    state = modal_fixture.evaluate(r"""
() => {
  const outer = document.getElementById('pr2-modal');
  document.body.insertAdjacentHTML('beforeend',
    '<div class="modal-backdrop" id="pr2-inner" role="dialog" aria-modal="true">'
    + '<div class="modal"><button id="pr2-inner-btn">inner</button></div></div>');
  const inner = document.getElementById('pr2-inner');
  window.AxisModal.open(outer);
  window.AxisModal.open(inner);
  window.AxisModal.close(inner);
  const link = document.getElementById('pr2-background-link');
  return {outerOpen: outer.classList.contains('open'),
          backgroundStillIsolated: !!link.closest('[inert]') || link.inert === true,
          focusInOuter: outer.contains(document.activeElement)};
}""")
    assert state["outerOpen"], state
    assert state["backgroundStillIsolated"], (
        "closing the inner dialog released the outer dialog's isolation")
    assert state["focusInOuter"], state


# --- F-33 - reduced motion ----------------------------------------------

def test_shared_primitives_do_not_animate_under_reduced_motion(client):
    """Under ``prefers-reduced-motion: reduce`` the shared overlays must not
    move - while functional feedback (colour, opacity) survives."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(bypass_csp=True, reduced_motion="reduce",
                                      viewport={"width": 390, "height": 844})
        context.route("**/*", _make_router(client))
        page = context.new_page()
        page.goto("http://localhost/login", wait_until="domcontentloaded")
        measured = page.evaluate(r"""
() => {
  const out = {};
  for (const cls of ['modal-backdrop', 'modal', 'sheet-backdrop', 'sheet', 'toast']) {
    const el = document.createElement('div');
    el.className = cls + (cls.indexOf('backdrop') >= 0 ? ' open' : '');
    document.body.appendChild(el);
    const cs = getComputedStyle(el);
    out[cls] = {animation: cs.animationName, duration: cs.animationDuration};
    el.remove();
  }
  const btn = document.createElement('button');
  btn.className = 'btn-volt';
  document.body.appendChild(btn);
  out.buttonTransition = getComputedStyle(btn).transitionProperty;
  btn.remove();
  return out;
}""")
        browser.close()

    moving = {k: v for k, v in measured.items()
              if isinstance(v, dict) and v["animation"] != "none"
              and v["duration"] not in ("0s", "0ms")}
    assert not moving, "shared primitives still animate under reduce: %s" % moving
    assert measured["buttonTransition"] != "none", (
        "reduced motion removed all button feedback, not just movement")


# --- responsive ----------------------------------------------------------

@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("width", VIEWPORTS)
def test_no_page_level_horizontal_overflow(
        app, auth_user, surface, width, language):
    """Zero page overflow at 320-1366 in TR and EN, protected by assertion.

    A region with its own ``overflow-x: auto`` scrolls INSIDE itself and is not
    page overflow; this measures the document element only.
    """
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    with app.app_context():
        _ready(auth_user.id, language)
    surface.set_viewport_size({"width": width, "height": 900})
    overflowing = []
    for name, path in sorted(SURFACES.items()):
        _visit(surface, path)
        measured = surface.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth,"
            " client: document.documentElement.clientWidth})")
        if measured["scroll"] > measured["client"] + 1:
            overflowing.append("%s %s" % (name, measured))
    assert not overflowing, (
        "%s at %dpx overflows horizontally: %s"
        % (language.upper(), width, overflowing))
