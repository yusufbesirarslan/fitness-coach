"""WEB-UX4-PR3 — what Chromium actually paints for the Coach destination.

The contract suite reads source. This one loads ``/coach`` in a real browser
and measures geometry, tab order, computed type, computed colour and network
volume, because F-04's defect is not a missing class name — it is that a
primary destination *renders* as a 360x500 box in an otherwise empty canvas.

Everything here is hermetic: HTTP is served by the authenticated Flask test
client through a Playwright route handler, and every non-localhost request
(fonts, CDN, analytics) is aborted. No AI provider is contacted — the Coach
presentation is provider-independent by construction, which is the point.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from app.extensions import db
from app.models import User

VIEWPORTS = (320, 390, 768, 1024, 1366)
DESKTOP = {"width": 1366, "height": 900}
# Chromium reports fractional layout widths; one device pixel of rounding is
# not a horizontal scrollbar. This is the repository's established tolerance.
OVERFLOW_TOLERANCE = 1


# ── harness ────────────────────────────────────────────────────────────────

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
                     if k.lower() in {"content-type", "origin", "x-csrftoken", "accept"}})
        route.fulfill(status=response.status_code, body=response.get_data(),
                      headers={k: v for k, v in response.headers.items()
                               if k.lower() not in {"content-length", "set-cookie"}})
    return route_request


class _Session:
    """A Chromium page plus the request log the page generated."""

    def __init__(self, page, requests):
        self.page = page
        self.requests = requests

    def visit(self, path="/coach", settle=True):
        self.requests.clear()
        self.page.goto("http://localhost" + path, wait_until="domcontentloaded")
        if settle:
            # The conversation renders from a fetch; wait for the composer to
            # be laid out rather than sleeping a guessed interval.
            try:
                self.page.wait_for_function(
                    "() => { const i = document.getElementById('cw-input');"
                    " return i && i.getBoundingClientRect().height > 0; }",
                    timeout=5000)
            except Exception:
                pass
            self.page.wait_for_timeout(250)
        return self

    def js(self, expression, arg=None):
        return self.page.evaluate(expression, arg)


@pytest.fixture
def coach(client):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(bypass_csp=True, viewport=dict(DESKTOP))
        context.route("**/*", _make_router(client))
        page = context.new_page()
        requests: list[str] = []
        page.on("request", lambda r: requests.append(urlsplit(r.url).path))
        yield _Session(page, requests)
        browser.close()


@pytest.fixture
def reduced_motion_coach(client):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(bypass_csp=True, viewport=dict(DESKTOP),
                                      reduced_motion="reduce")
        context.route("**/*", _make_router(client))
        page = context.new_page()
        yield _Session(page, [])
        browser.close()


def _ready(app, user_id, language="tr", v2=True):
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = v2
    with app.app_context():
        user = db.session.get(User, user_id)
        user.profile_complete = True
        user.language = language
        db.session.commit()


def _seed_conversation(app, user_id, turns=6):
    """Real server-side history, so the hydrated state is not a mock.

    ``/coach/history`` reads ``memory_manager``; seeding through it means the
    hydrated view under test is the one production renders.
    """
    from app.services import memory_manager
    with app.app_context():
        conversation = memory_manager.get_or_create_active_conversation(user_id)
        for i in range(turns):
            memory_manager.record_turn(
                conversation,
                f"Bacak gunumde squat yerine ne yapabilirim? ({i})",
                "Squat yerine goblet squat veya leg press kullanabilirsin. "
                "Ayni kas grubunu calistirir, dizine binen yuku azaltir ve "
                "tekniği ogrenmesi daha kolaydir. Haftalik hacmini koruyarak "
                f"3 set x 10 tekrar ile basla. (yanit {i})")
        db.session.commit()


_GEOMETRY_JS = r"""
() => {
  const win = document.getElementById('cw-window');
  const root = document.getElementById('cw-root');
  const input = document.getElementById('cw-input');
  const send = document.getElementById('cw-send');
  const h1 = document.querySelector('h1');
  const box = el => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {x: r.x, y: r.y, w: r.width, h: r.height,
            top: r.top, bottom: r.bottom, left: r.left, right: r.right};
  };
  const pos = el => el ? getComputedStyle(el).position : null;
  return {
    vw: window.innerWidth, vh: window.innerHeight,
    docW: document.documentElement.scrollWidth,
    docH: document.documentElement.scrollHeight,
    window: box(win), root: box(root), input: box(input),
    send: box(send), h1: box(h1),
    winPosition: pos(win), rootPosition: pos(root),
    rootZ: root ? getComputedStyle(root).zIndex : null,
    counts: {
      root: document.querySelectorAll('#cw-root').length,
      window: document.querySelectorAll('#cw-window').length,
      input: document.querySelectorAll('#cw-input').length,
      send: document.querySelectorAll('#cw-send').length,
      msgs: document.querySelectorAll('#cw-msgs').length,
      fab: document.querySelectorAll('#cw-fab').length,
    },
  };
}
"""


def _geometry(coach):
    return coach.js(_GEOMETRY_JS)


# ══════════════════════════════════════════════════════════════════════════
# F-04 / F-30 — the page IS the Coach
# ══════════════════════════════════════════════════════════════════════════

def test_conversation_is_page_content_not_a_floating_window(app, auth_user, coach):
    """The single sharpest F-04 assertion: the conversation must be in flow.

    ``position: fixed`` on the canonical Coach destination is the
    support-widget composition this PR exists to end.
    """
    _ready(app, auth_user.id)
    coach.visit()
    geo = _geometry(coach)
    assert geo["window"], "no Coach conversation surface rendered"
    assert geo["winPosition"] != "fixed", \
        "the Coach conversation is still a fixed floating window"
    assert geo["rootPosition"] != "fixed", \
        "the Coach root is still fixed-positioned on its own destination"


def test_desktop_composition_is_not_a_corner_chat_box(app, auth_user, coach):
    """At 1366x900 discovery measured ~87 % of the viewport unused.

    Measured against four independent properties so no single generous
    threshold can carry the test: the workspace must be wide, tall, occupy a
    real share of the canvas, and not be the 360x500 support box.
    """
    _ready(app, auth_user.id)
    _seed_conversation(app, auth_user.id)
    coach.visit()
    geo = _geometry(coach)
    win, vw, vh = geo["window"], geo["vw"], geo["vh"]
    share = (win["w"] * win["h"]) / float(vw * vh)
    failures = []
    if win["w"] < 0.45 * vw:
        failures.append("width %.0fpx is %.0f%% of the %dpx viewport"
                        % (win["w"], 100 * win["w"] / vw, vw))
    if win["h"] < 0.70 * vh:
        failures.append("height %.0fpx is %.0f%% of the %dpx viewport"
                        % (win["h"], 100 * win["h"] / vh, vh))
    if share < 0.35:
        failures.append("occupies %.0f%% of the canvas" % (100 * share))
    if abs(win["w"] - 360) < 2 and abs(win["h"] - 500) < 2:
        failures.append("is exactly the 360x500 support window")
    assert not failures, (
        "the Coach workspace still reads as a corner chat box at %dx%d: %s"
        % (vw, vh, "; ".join(failures)))


def test_the_page_heading_is_never_covered_by_the_conversation(app, auth_user, coach):
    """At 390 px the floating window covered the page's own ``h1``."""
    _ready(app, auth_user.id)
    for width in VIEWPORTS:
        coach.page.set_viewport_size({"width": width, "height": 844})
        coach.visit()
        geo = _geometry(coach)
        h1, win = geo["h1"], geo["window"]
        assert h1 and h1["h"] > 0, f"no page heading at {width}px"
        overlaps = not (win["bottom"] <= h1["top"] or win["top"] >= h1["bottom"]
                        or win["right"] <= h1["left"] or win["left"] >= h1["right"])
        assert not overlaps, (
            "at %dpx the Coach conversation (%s) overlaps the page heading (%s)"
            % (width, win, h1))


def test_composer_is_reachable_and_not_under_the_bottom_navigation(
        app, auth_user, coach):
    """§10 / §43 — the bottom bar must not cover the field the user types in."""
    _ready(app, auth_user.id)
    _seed_conversation(app, auth_user.id)
    for width in (320, 390, 768):
        coach.page.set_viewport_size({"width": width, "height": 844})
        coach.visit()
        obstructed = coach.js(r"""
() => {
  const input = document.getElementById('cw-input');
  if (!input) return 'no composer';
  const r = input.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return 'composer has no box';
  // What the browser says is actually on top at the composer's midpoint.
  const hit = document.elementFromPoint(r.left + r.width / 2,
                                        r.top + r.height / 2);
  if (!hit) return 'composer midpoint is off-screen';
  if (hit === input || input.contains(hit)) return null;
  return 'covered by ' + hit.tagName.toLowerCase()
         + (hit.id ? '#' + hit.id : '')
         + (hit.className ? '.' + String(hit.className).split(/\s+/)[0] : '');
}""")
        assert obstructed is None, f"at {width}px the composer is {obstructed}"


# ══════════════════════════════════════════════════════════════════════════
# F-05 / §16 — keyboard order and the composer's name
# ══════════════════════════════════════════════════════════════════════════

_WALK_JS = r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const visible = r.width > 0 && r.height > 0
                  && cs.visibility !== 'hidden' && cs.opacity !== '0';
  const name = el.getAttribute('aria-label')
            || (el.labels && el.labels.length ? el.labels[0].textContent.trim() : '')
            || (el.textContent || '').trim();
  return {id: el.id, tag: el.tagName.toLowerCase(), visible: visible,
          name: name.slice(0, 40),
          inCoach: !!el.closest('#cw-root')};
}
"""


def _tab_walk(coach, limit=40):
    coach.js("() => { if (document.activeElement) document.activeElement.blur(); }")
    stops, seen = [], set()
    for _ in range(limit):
        coach.page.keyboard.press("Tab")
        record = coach.js(_WALK_JS)
        if record is None:
            continue
        key = (record["id"], record["tag"], record["name"])
        if key in seen:
            break
        seen.add(key)
        stops.append(record)
    return stops


def test_the_composer_is_reached_before_the_send_control(app, auth_user, coach):
    """Discovery's exact defect: a real ``Tab`` walk met ``#cw-send`` first
    and reached ``#cw-input`` at stop 12."""
    _ready(app, auth_user.id)
    _seed_conversation(app, auth_user.id)
    coach.visit()
    stops = _tab_walk(coach)
    order = [s["id"] or (s["tag"] + ":" + s["name"]) for s in stops]
    assert "cw-input" in order, f"the composer is never reached: {order}"
    assert "cw-send" in order, f"the send control is never reached: {order}"
    assert order.index("cw-input") < order.index("cw-send"), (
        "the user meets Send before the field they must type in: %s" % order)


def test_no_invisible_control_is_focusable_on_the_destination(app, auth_user, coach):
    """§15 / §33 — no invisible launcher, no focusable closed-window controls."""
    _ready(app, auth_user.id)
    coach.visit()
    invisible = [s for s in _tab_walk(coach) if not s["visible"]]
    assert not invisible, f"invisible controls in the tab order: {invisible}"


def test_the_destination_carries_no_floating_launcher(app, auth_user, coach):
    """The page IS Coach; a FAB to open it is duplicate navigation covering
    page content."""
    _ready(app, auth_user.id)
    coach.visit()
    assert _geometry(coach)["counts"]["fab"] == 0, \
        "a floating launcher is still injected on the Coach destination"


def test_the_composer_has_an_accessible_name(app, auth_user, coach):
    """A placeholder is not a naming contract — it disappears on input."""
    _ready(app, auth_user.id)
    coach.visit()
    name = coach.js(r"""
() => {
  const el = document.getElementById('cw-input');
  if (!el) return null;
  const aria = el.getAttribute('aria-label');
  const labelled = el.getAttribute('aria-labelledby');
  const label = el.labels && el.labels.length ? el.labels[0].textContent.trim() : '';
  return (aria || label || (labelled ? 'labelledby' : '') || '').trim();
}""")
    assert name, "#cw-input has no accessible name"
    assert name != "coach.composer_label", "the composer name is a raw i18n key"


def test_shift_tab_walks_back_out_of_the_composer(app, auth_user, coach):
    """§33 — Shift+Tab must behave, and the page must not trap focus."""
    _ready(app, auth_user.id)
    coach.visit()
    coach.js("() => document.getElementById('cw-send').focus()")
    coach.page.keyboard.press("Shift+Tab")
    assert coach.js("() => document.activeElement && document.activeElement.id") \
        is not None
    # Walking backwards out of the workspace must reach the global navigation.
    escaped = False
    for _ in range(25):
        coach.page.keyboard.press("Shift+Tab")
        if coach.js("() => !document.activeElement"
                    " || !document.activeElement.closest('#cw-root')"):
            escaped = True
            break
    assert escaped, "focus is trapped inside the Coach workspace in normal page mode"


# ══════════════════════════════════════════════════════════════════════════
# §34 / §49 — exactly one Coach instance, exactly one hydration
# ══════════════════════════════════════════════════════════════════════════

def test_exactly_one_interactive_coach_tree(app, auth_user, coach):
    _ready(app, auth_user.id)
    coach.visit()
    counts = _geometry(coach)["counts"]
    for part in ("root", "window", "input", "send", "msgs"):
        assert counts[part] == 1, f"{counts[part]} x #cw-{part} on the page"


def test_one_history_hydration_per_page_load(app, auth_user, coach):
    """§49 — the destination must not increase the Coach request budget."""
    _ready(app, auth_user.id)
    _seed_conversation(app, auth_user.id)
    coach.visit()
    coach.page.wait_for_timeout(400)
    hits = [p for p in coach.requests if p == "/coach/history"]
    assert len(hits) == 1, f"{len(hits)} /coach/history requests: {coach.requests}"


def test_re_evaluating_the_widget_is_an_idempotent_no_op(app, auth_user, coach):
    """The module-level guard, proven at runtime rather than read in source."""
    _ready(app, auth_user.id)
    coach.visit()
    before = _geometry(coach)["counts"]
    coach.page.add_script_tag(url="/static/coach_widget.js")
    coach.page.wait_for_timeout(300)
    after = _geometry(coach)["counts"]
    assert before == after, f"a second evaluation changed the tree: {before} -> {after}"
    hits = [p for p in coach.requests if p == "/coach/history"]
    assert len(hits) <= 1, f"a second evaluation re-hydrated history: {hits}"


def test_the_destination_adds_no_new_server_read(app, auth_user, coach):
    """§41 — no Today/Plan/Progress fetch to make the page look richer."""
    _ready(app, auth_user.id)
    coach.visit()
    coach.page.wait_for_timeout(400)
    forbidden = [p for p in coach.requests
                 if p.startswith(("/training/", "/api/v1", "/progress",
                                  "/nutrition/", "/dashboard"))]
    assert not forbidden, f"the Coach page fetched domain data: {forbidden}"


# ══════════════════════════════════════════════════════════════════════════
# F-27 — measured typography
# ══════════════════════════════════════════════════════════════════════════

def test_coach_answers_render_at_a_readable_size_and_weight(app, auth_user, coach):
    """13.5 px at weight 300 for the longest content in the product."""
    _ready(app, auth_user.id)
    _seed_conversation(app, auth_user.id)
    coach.visit()
    coach.page.wait_for_selector(".cw-bot .cw-bubble", timeout=5000)
    measured = coach.js(r"""
() => {
  const el = document.querySelector('.cw-bot .cw-bubble');
  const cs = getComputedStyle(el);
  return {size: parseFloat(cs.fontSize), weight: parseInt(cs.fontWeight, 10),
          lineHeight: cs.lineHeight, family: cs.fontFamily};
}""")
    assert measured["size"] >= 14, f"Coach answers render at {measured['size']}px"
    assert measured["weight"] >= 400, \
        f"Coach answers render at weight {measured['weight']}"
    assert "Bebas" not in measured["family"], \
        f"long Coach answers render in the display face: {measured['family']}"


def test_composer_does_not_trigger_mobile_focus_zoom(app, auth_user, coach):
    """A sub-16 px input zooms the viewport on focus on iOS."""
    _ready(app, auth_user.id)
    coach.page.set_viewport_size({"width": 390, "height": 844})
    coach.visit()
    size = coach.js("() => parseFloat(getComputedStyle("
                    "document.getElementById('cw-input')).fontSize)")
    assert size >= 16, f"the composer renders at {size}px at 390px wide"


# ══════════════════════════════════════════════════════════════════════════
# F-28 — measured palette
# ══════════════════════════════════════════════════════════════════════════

def test_no_retired_lime_is_painted_anywhere_in_coach(app, auth_user, coach):
    """``#99CC00`` = rgb(153, 204, 0); ``#D6FF1A`` = rgb(214, 255, 26)."""
    _ready(app, auth_user.id)
    _seed_conversation(app, auth_user.id)
    coach.visit()
    lime = coach.js(r"""
() => {
  const banned = [[153, 204, 0], [214, 255, 26]];
  const near = txt => banned.some(([r, g, b]) =>
    (txt || '').includes(`${r}, ${g}, ${b}`) || (txt || '').includes(`${r},${g},${b}`));
  const out = [];
  for (const el of document.querySelectorAll('#cw-root, #cw-root *')) {
    const cs = getComputedStyle(el);
    for (const prop of ['color', 'backgroundColor', 'backgroundImage',
                        'borderColor', 'boxShadow', 'stroke', 'fill']) {
      if (near(cs[prop])) out.push((el.id || el.className) + ' ' + prop + ': ' + cs[prop]);
    }
  }
  return out;
}""")
    assert not lime, f"retired lime is still painted: {lime}"


def test_the_ai_avatar_reads_as_axisai(app, auth_user, coach):
    """F-28's visible symptom: a blue-to-lime gradient on a blue product."""
    _ready(app, auth_user.id)
    coach.visit()
    avatar = coach.js(r"""
() => {
  const el = document.getElementById('cw-avatar');
  if (!el) return null;
  const cs = getComputedStyle(el);
  return {backgroundImage: cs.backgroundImage, boxShadow: cs.boxShadow};
}""")
    if avatar is not None:
        assert "gradient" not in avatar["backgroundImage"], avatar


# ══════════════════════════════════════════════════════════════════════════
# F-26 — measured layering
# ══════════════════════════════════════════════════════════════════════════

def test_coach_does_not_outrank_the_toast_layer(app, auth_user, coach):
    """A confirmation raised while Coach is open must not render behind it."""
    _ready(app, auth_user.id)
    coach.visit()
    layers = coach.js(r"""
() => {
  const read = name => getComputedStyle(document.documentElement)
                        .getPropertyValue(name).trim();
  const z = el => { const v = getComputedStyle(el).zIndex;
                    return v === 'auto' ? null : parseInt(v, 10); };
  const root = document.getElementById('cw-root');
  const notify = document.getElementById('cw-notify');
  return {toastToken: parseInt(read('--z-toast'), 10),
          fabToken: parseInt(read('--z-fab'), 10),
          root: root ? z(root) : null,
          notify: notify ? z(notify) : null};
}""")
    assert layers["toastToken"] == 400, layers
    if layers["root"] is not None:
        assert layers["root"] <= layers["toastToken"], \
            f"the Coach window outranks the toast layer: {layers}"
    if layers["notify"] is not None:
        assert layers["notify"] <= layers["toastToken"], layers


def test_the_floating_widget_layers_on_the_scale_on_other_hosts(
        app, auth_user, coach):
    """Nutrition keeps the floating widget — but on the documented scale."""
    _ready(app, auth_user.id)
    coach.visit("/nutrition")
    layers = coach.js(r"""
() => {
  const root = document.getElementById('cw-root');
  if (!root) return null;
  const cs = getComputedStyle(root);
  const toast = parseInt(getComputedStyle(document.documentElement)
                  .getPropertyValue('--z-toast'), 10);
  return {position: cs.position, z: parseInt(cs.zIndex, 10), toast: toast};
}""")
    assert layers, "the Coach widget no longer mounts on Nutrition"
    assert layers["position"] == "fixed", \
        "Nutrition lost the floating widget it depends on"
    assert layers["z"] <= layers["toast"], \
        f"the floating widget still outranks toast: {layers}"


# ══════════════════════════════════════════════════════════════════════════
# F-33 — reduced motion
# ══════════════════════════════════════════════════════════════════════════

def test_coach_honours_prefers_reduced_motion(app, auth_user, reduced_motion_coach):
    """coach_widget.css was the most animated surface with no coverage.

    The animated states only exist mid-stream, so measuring the resting page
    would pass against the unguarded baseline and prove nothing. The probe
    materialises the real markup the widget renders while busy — the bouncing
    dots and the live streaming caret — and measures those.
    """
    _ready(app, auth_user.id)
    reduced_motion_coach.visit()
    moving = reduced_motion_coach.js(r"""
() => {
  // Exactly what CW._render()/_setLoading() paint while a reply is in flight.
  const msgs = document.getElementById('cw-msgs');
  const typing = document.createElement('div');
  typing.className = 'cw-row cw-bot';
  typing.innerHTML = '<div class="cw-bubble cw-typing">'
                   + '<span></span><span></span><span></span></div>';
  const streaming = document.createElement('div');
  streaming.className = 'cw-row cw-bot';
  streaming.innerHTML = '<div class="cw-bubble cw-md cw-typing-live"'
                      + ' id="cw-stream"></div>';
  msgs.appendChild(typing);
  msgs.appendChild(streaming);
  const out = [];
  for (const el of document.querySelectorAll('#cw-root, #cw-root *')) {
    const cs = getComputedStyle(el);
    if (cs.animationName && cs.animationName !== 'none') {
      out.push((el.id || el.className) + ' animation: ' + cs.animationName);
    }
    const after = getComputedStyle(el, '::after').animationName;
    if (after && after !== 'none') {
      out.push((el.id || el.className) + ' ::after: ' + after);
    }
  }
  typing.remove();
  streaming.remove();
  return out;
}""")
    assert not moving, f"decorative motion survives prefers-reduced-motion: {moving}"


def test_the_reduced_motion_probe_is_not_vacuous(app, auth_user, coach):
    """Guards the test above: without the reduce preference the same probe
    MUST measure running animations, or it is asserting on an empty set."""
    _ready(app, auth_user.id)
    coach.visit()
    moving = coach.js(r"""
() => {
  const msgs = document.getElementById('cw-msgs');
  const typing = document.createElement('div');
  typing.className = 'cw-row cw-bot';
  typing.innerHTML = '<div class="cw-bubble cw-typing">'
                   + '<span></span><span></span><span></span></div>';
  msgs.appendChild(typing);
  const names = [...document.querySelectorAll('#cw-root *')]
    .map(el => getComputedStyle(el).animationName)
    .filter(n => n && n !== 'none');
  typing.remove();
  return names;
}""")
    assert moving, "the reduced-motion probe measures nothing even with motion on"


def test_reduced_motion_keeps_the_loading_indicator_visible(
        app, auth_user, reduced_motion_coach):
    """§46 — do not disable functional loading feedback, only its motion."""
    _ready(app, auth_user.id)
    reduced_motion_coach.visit()
    visible = reduced_motion_coach.js(r"""
() => {
  const msgs = document.getElementById('cw-msgs');
  const probe = document.createElement('div');
  probe.className = 'cw-bubble cw-typing';
  probe.innerHTML = '<span></span><span></span><span></span>';
  msgs.appendChild(probe);
  const dot = probe.querySelector('span');
  const cs = getComputedStyle(dot);
  const r = dot.getBoundingClientRect();
  const out = {opacity: parseFloat(cs.opacity), w: r.width, h: r.height,
               display: cs.display};
  probe.remove();
  return out;
}""")
    assert visible["w"] > 0 and visible["h"] > 0, visible
    assert visible["opacity"] > 0.15, \
        f"the loading indicator is invisible under reduced motion: {visible}"


# ══════════════════════════════════════════════════════════════════════════
# §47 — responsive regression, both locales
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("width", VIEWPORTS)
def test_no_horizontal_overflow_at_any_matrix_cell(
        app, auth_user, coach, width, language):
    _ready(app, auth_user.id, language=language)
    _seed_conversation(app, auth_user.id)
    coach.page.set_viewport_size({"width": width, "height": 844})
    coach.visit()
    geo = _geometry(coach)
    assert geo["docW"] <= geo["vw"] + OVERFLOW_TOLERANCE, (
        "%s at %dpx: document scrollWidth %s exceeds viewport %s"
        % (language, width, geo["docW"], geo["vw"]))


@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("width", VIEWPORTS)
def test_the_conversation_stays_inside_the_viewport(
        app, auth_user, coach, width, language):
    """No viewport-edge collision, at either locale."""
    _ready(app, auth_user.id, language=language)
    coach.page.set_viewport_size({"width": width, "height": 844})
    coach.visit()
    geo = _geometry(coach)
    win = geo["window"]
    assert win["left"] >= -OVERFLOW_TOLERANCE, (language, width, win)
    assert win["right"] <= geo["vw"] + OVERFLOW_TOLERANCE, (language, width, win)


@pytest.mark.parametrize("language", ["tr", "en"])
def test_no_raw_locale_key_is_painted_on_the_destination(
        app, auth_user, coach, language):
    """A missing key renders as the key itself — visible, and untranslated."""
    _ready(app, auth_user.id, language=language)
    coach.visit()
    leaked = coach.js(r"""
() => [...document.querySelectorAll('#cw-root *, main *')]
  .filter(el => el.children.length === 0)
  .map(el => (el.textContent || '').trim())
  .filter(t => /^(coach|nav|nutrition)\.[a-z0-9_.]+$/.test(t))""")
    assert not leaked, f"raw i18n keys painted in {language}: {leaked}"


# ══════════════════════════════════════════════════════════════════════════
# §48 — the flag-OFF rollback still works
# ══════════════════════════════════════════════════════════════════════════

def test_flag_off_still_serves_a_working_legacy_coach(app, auth_user, coach):
    """Rollback must stay meaningful: the legacy page keeps its own
    presentation and its own floating widget."""
    _ready(app, auth_user.id, v2=False)
    coach.visit()
    legacy = coach.js(r"""
() => {
  const root = document.getElementById('cw-root');
  const input = document.getElementById('cw-input');
  return {hasRoot: !!root, hasInput: !!input,
          rootPosition: root ? getComputedStyle(root).position : null,
          hasDestination: document.body.hasAttribute('data-coach-destination'),
          hasMount: !!document.querySelector('[data-coach-mount]'),
          title: !!document.querySelector('.coach-page-title')};
}""")
    assert legacy["hasRoot"] and legacy["hasInput"], legacy
    assert legacy["rootPosition"] == "fixed", \
        "the legacy rollback page no longer shows the floating widget"
    assert not legacy["hasDestination"] and not legacy["hasMount"], legacy
    assert legacy["title"], legacy


# ══════════════════════════════════════════════════════════════════════════
# §36 — the menu scanner Nutrition depends on still works structurally
# ══════════════════════════════════════════════════════════════════════════

def test_the_menu_scanner_still_opens_from_the_widget(app, auth_user, coach):
    """F-39's dependency, exercised — no camera permission required."""
    _ready(app, auth_user.id)
    coach.visit()
    state = coach.js(r"""
() => {
  if (!window.CW || typeof window.CW.startScan !== 'function') return 'no CW.startScan';
  window.CW.toggleQrMenu();
  const menu = document.getElementById('cw-qr-menu');
  const open = menu && menu.classList.contains('cw-open');
  window.CW.hideQrMenu();
  const urlFlow = typeof window.CW.promptUrl === 'function'
               && typeof window.CW.submitUrl === 'function';
  return {menuOpens: !!open, urlFlow: urlFlow,
          scanClose: typeof window.CW.stopScan === 'function'};
}""")
    assert state != "no CW.startScan", state
    assert state["menuOpens"], f"the QR menu no longer opens: {state}"
    assert state["urlFlow"] and state["scanClose"], state


def test_nutrition_keeps_its_live_widget_dependency(app, auth_user, coach):
    """F-39 disposition evidence, measured in the browser."""
    _ready(app, auth_user.id)
    coach.visit("/nutrition")
    state = coach.js(r"""
() => ({
  cw: !!(window.CW && typeof window.CW.startScan === 'function'),
  affordance: !!document.querySelector('[data-action="logMenuScan"]'),
})""")
    assert state["cw"], "Nutrition lost window.CW.startScan"
    assert state["affordance"], "Nutrition lost its menu-scan affordance"




# ══════════════════════════════════════════════════════════════════════════
# §43 — the viewport lock has a short-viewport escape hatch
#
# The destination pins the page to the viewport so the composer stays under
# the thumb. That is only right while the viewport can hold the destination.
# At 320px of height the fixed cost leaves the conversation ~32px and the
# composer paints BELOW the panel that is meant to contain it — measured on
# this branch before the hatch existed. Nothing is stranded; the page just
# stops being a conversation.
# ══════════════════════════════════════════════════════════════════════════

_SHORT_JS = r"""
() => {
  const box = s => { const e = document.querySelector(s); if (!e) return null;
    const r = e.getBoundingClientRect();
    return {top: Math.round(r.top), bottom: Math.round(r.bottom),
            h: Math.round(r.height)}; };
  return {
    vh: window.innerHeight,
    docH: document.documentElement.scrollHeight,
    overflowY: getComputedStyle(document.body).overflowY,
    panel: box('#cw-window'), msgs: box('#cw-msgs'),
    irow: box('#cw-irow'), input: box('#cw-input'),
    title: box('.coach-dest-title'),
  };
}"""


@pytest.mark.parametrize("width,height", [(320, 420), (568, 320), (768, 450)])
def test_a_short_viewport_still_renders_a_usable_conversation(
        app, auth_user, coach, width, height):
    _ready(app, auth_user.id)
    _seed_conversation(app, auth_user.id)
    coach.page.set_viewport_size({"width": width, "height": height})
    coach.visit()
    st = coach.js(_SHORT_JS)

    assert st["title"] and st["title"]["h"] > 0, f"no heading at {width}x{height}: {st}"
    assert st["input"] and st["input"]["h"] > 0, f"no composer at {width}x{height}: {st}"

    # 1. The composer stays INSIDE the conversation panel. Without the hatch
    #    #cw-irow overflowed #cw-window by tens of pixels at these heights.
    assert st["irow"]["bottom"] <= st["panel"]["bottom"] + 2, (
        "at %dx%d the composer paints %spx below its own panel: %s"
        % (width, height, st["irow"]["bottom"] - st["panel"]["bottom"], st))

    # 2. The conversation keeps a height worth reading rather than collapsing
    #    to a couple of lines of a message list.
    assert st["msgs"]["h"] >= 160, (
        "at %dx%d the conversation collapsed to %spx: %s"
        % (width, height, st["msgs"]["h"], st))

    # 3. Whatever no longer fits is reachable by scrolling the page.
    if st["docH"] > st["vh"]:
        assert st["overflowY"] != "hidden", (
            "at %dx%d the page overflows by %spx and is locked: %s"
            % (width, height, st["docH"] - st["vh"], st))


def test_the_short_viewport_hatch_does_not_unlock_a_normal_phone(
        app, auth_user, coach):
    """Non-vacuity: the hatch is conditional, not a silent removal of the lock.

    At a phone's real height the page is still exactly the viewport. If the
    lock were simply gone, the test above would prove nothing.
    """
    _ready(app, auth_user.id)
    _seed_conversation(app, auth_user.id)
    coach.page.set_viewport_size({"width": 390, "height": 844})
    coach.visit()
    st = coach.js(_SHORT_JS)
    assert st["overflowY"] == "hidden", f"the viewport lock is gone at 390x844: {st}"
    assert st["docH"] <= st["vh"] + 2, (
        "the destination scrolls the page at 390x844 — the conversation, not "
        "the page, owns the overflow: %s" % st)


# ═════════════════════════════════════════════════════════════════════════
# §16 — a destination is not a dialog
#
# A floating Coach IS a dialog: it opens over the page, it has a close
# control, it is what the launcher launches. A destination is the page's own
# content, and announcing it as a dialog tells a screen-reader user they are
# inside something they can leave. Both directions are asserted, because
# removing the role everywhere would change assistive-technology behaviour on
# every other page — one tree, one attribute.
# ═════════════════════════════════════════════════════════════════════════

_ROLE_JS = r"""
() => {
  const w = document.getElementById('cw-window');
  const m = document.getElementById('cw-msgs');
  if (!w) return 'no window';
  return {
    role: w.getAttribute('role'),
    ariaLabel: w.getAttribute('aria-label'),
    msgsRole: m && m.getAttribute('role'),
    msgsNamed: !!(m && m.getAttribute('aria-label')),
    h1: (document.querySelector('h1') || {}).textContent || null,
  };
}"""


def test_the_destination_conversation_is_not_announced_as_a_dialog(
        app, auth_user, coach):
    _ready(app, auth_user.id)
    coach.visit()
    st = coach.js(_ROLE_JS)
    assert st != "no window", st
    assert st["role"] is None, (
        "the destination still announces itself as role=%r — a page a "
        "screen-reader user is told they can leave: %s" % (st["role"], st))
    # The conversation keeps its own semantics, and the page supplies the name
    # the suppressed widget header used to carry.
    assert st["msgsRole"] == "log" and st["msgsNamed"], st
    assert st["h1"], "the destination has no heading to stand in for the role"


def test_a_floating_coach_still_announces_itself_as_a_dialog(
        app, auth_user, coach):
    """Non-vacuity: the role was removed for the destination, not deleted.

    Nutrition loads the same widget as a floating panel. If the role were gone
    there too, the test above would be asserting a global removal it does not
    justify.
    """
    _ready(app, auth_user.id)
    coach.visit("/nutrition")
    st = coach.js(_ROLE_JS)
    assert st != "no window", st
    assert st["role"] == "dialog", (
        "the floating Coach lost its dialog role on a non-destination page: %s"
        % st)
    assert st["ariaLabel"], f"the floating Coach dialog has no name: {st}"
