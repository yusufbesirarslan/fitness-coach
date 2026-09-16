"""AxisAI UIUX Sprint 1 PR3 — Coach Page V2 (flag-gated) tests.

Covers the route swap (OFF → legacy coach.html thin host, ON → hardened
coach_v2.html), @require_auth on both paths, and the guarantee that V2 REUSES the
exact existing floating widget (no second Coach implementation). The Coach flag is

WEB-UX4-PR3 turned the V2 page into a DESTINATION: the widget no longer floats
over an empty hero, it mounts into the page's content column. The invariant this
file guards is unchanged and now matters more — still ONE Coach implementation,
one composer, one stream, one history hydration. The launcher, its inline opener
and its two copy keys are gone; the assertions below track that.
toggled with app.config["UIUX_COACH_PAGE_V2_ENABLED"] (coach_page() reads it at
request time).

Lifecycle idempotency (answer.txt §4) has a runtime half and a source half:

* The runtime cases — script evaluated twice, init() twice, host present-but-
  uninitialized, route-after-floating, floating-after-route, close→reopen,
  resize/history transitions all leaving ONE root / composer / submit / stream —
  require a real DOM and are exercised in the WSL/Chromium matrix (scripts/
  frontend_audit, Task #8), not here.
* The source half — that the invariant is actually ENCODED (a single module-level
  init guard placed before any injection/fetch/CW definition; adopt-existing-host;
  one bootstrap; one history fetch) and that route-mode behaviour lives in the
  template, never in the shared widget (answer.txt §5) — is asserted structurally
  below, since that is what makes the runtime guarantee hold.
"""
import os
import re

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def _seed_login(client, make_user, login, username="coachuser"):
    make_user(username, profile_complete=True)
    login(username)
    return client


# ══════════════════════════════════════════════════════════════════════════
# A. ROUTE FLAG BEHAVIOUR — OFF preserves legacy thin host, ON renders V2
# ══════════════════════════════════════════════════════════════════════════

def test_flag_default_off_renders_legacy_coach(client, make_user, login):
    _seed_login(client, make_user, login)
    html = client.get("/coach").get_data(as_text=True)
    assert "data-coach-v2" not in html            # V2 marker absent
    assert "data-coach-destination" not in html   # destination mode absent
    assert 'class="coach-page-title"' in html      # legacy thin host still rendered


def test_flag_on_renders_coach_v2(app, client, make_user, login):
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/coach").get_data(as_text=True)
    assert "data-coach-v2" in html
    # PR3: the page no longer opens a floating widget — it IS the Coach.
    assert "data-coach-destination" in html     # destination presentation mode
    assert "data-coach-mount" in html           # and it declares where Coach lives


def test_missing_flag_fails_safe_to_legacy(app, client, make_user, login):
    app.config.pop("UIUX_COACH_PAGE_V2_ENABLED", None)
    _seed_login(client, make_user, login)
    html = client.get("/coach").get_data(as_text=True)
    assert "data-coach-v2" not in html          # safe default: legacy


def test_exactly_one_coach_tree_per_flag_state(app, client, make_user, login):
    _seed_login(client, make_user, login)
    off = client.get("/coach").get_data(as_text=True)
    assert "data-coach-v2" not in off

    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = True
    on = client.get("/coach").get_data(as_text=True)
    assert "data-coach-v2" in on
    # Never both trees at once: the V2 doc has the marker, the legacy doc does not.
    assert on != off


# ══════════════════════════════════════════════════════════════════════════
# B. @require_auth PRESERVED ON BOTH PATHS
# ══════════════════════════════════════════════════════════════════════════

def test_coach_requires_auth_when_off(app, client):
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = False
    resp = client.get("/coach")                 # no login
    assert resp.status_code in (301, 302, 401, 403)
    assert "data-coach-v2" not in resp.get_data(as_text=True)


def test_coach_requires_auth_when_on(app, client):
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = True
    resp = client.get("/coach")                 # no login
    assert resp.status_code in (301, 302, 401, 403)
    # The protected V2 page is NOT served to an anonymous request.
    assert "data-coach-v2" not in resp.get_data(as_text=True)


# ══════════════════════════════════════════════════════════════════════════
# C. REUSES THE EXACT EXISTING WIDGET — no second Coach implementation
# ══════════════════════════════════════════════════════════════════════════

def test_v2_loads_the_existing_widget_and_actions(app, client, make_user, login):
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/coach").get_data(as_text=True)
    assert "/static/coach_widget.js" in html
    assert "/static/actions.js" in html


def test_v2_references_the_widget_exactly_once(app, client, make_user, login):
    # One widget include → one self-injected host → one interactive instance.
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/coach").get_data(as_text=True)
    assert html.count("/static/coach_widget.js") == 1


def test_v2_template_does_not_reimplement_the_widget():
    # The V2 template must not hand-render a second composer / chat host; the
    # widget self-injects #cw-root, #cw-input, #cw-window. A duplicate here would
    # be a second accessibility-exposed Coach instance.
    tpl = _read("templates", "coach_v2.html")
    for marker in ('id="cw-root"', 'id="cw-input"', 'id="cw-window"', 'id="cw-fab"'):
        assert marker not in tpl, marker


# ══════════════════════════════════════════════════════════════════════════
# D. LIFECYCLE IDEMPOTENCY — source-encoded invariant (answer.txt §4)
# ══════════════════════════════════════════════════════════════════════════

def test_widget_has_module_level_init_guard_before_any_side_effect():
    src = _read("static", "coach_widget.js")
    # The guard exists and is a real early return.
    assert "window.__cwWidgetInit" in src
    guard = src.index("if (window.__cwWidgetInit) return;")
    # It runs BEFORE the host is injected, before CW is defined, and before any
    # /coach/history fetch — so a second evaluation is a clean no-op.
    assert guard < src.index("var CW = window.CW")
    assert guard < src.index("document.body.appendChild")
    assert guard < src.index("fetch('/coach/history'")


def test_widget_adopts_existing_host_instead_of_injecting_twice():
    src = _read("static", "coach_widget.js")
    # A server-rendered #cw-root is adopted, never duplicated.
    assert "if (!document.getElementById('cw-root'))" in src


def test_widget_bootstraps_exactly_once():
    src = _read("static", "coach_widget.js")
    # Single owned instance, single history hydration → no duplicate bootstrap.
    assert src.count("var CW = window.CW") == 1
    assert src.count("fetch('/coach/history'") == 1


# ══════════════════════════════════════════════════════════════════════════
# E. ROUTE-MODE BEHAVIOUR LIVES IN THE TEMPLATE, NOT THE SHARED WIDGET (§5)
# ══════════════════════════════════════════════════════════════════════════

def test_shared_widget_has_no_route_mode_behaviour():
    # Page-shell knowledge must NOT be in the shared widget, or it would fire on
    # the floating widget on every other page. PR3 kept this: the widget reads
    # two neutral presentation hooks the HOST page sets, and knows no route.
    src = _read("static", "coach_widget.js")
    assert "axCoachOpen" not in src
    assert "data-coach-v2" not in src
    # The widget knows two Coach ENDPOINTS and no Coach ROUTE: it never reads
    # the URL, so it can never decide for itself that a page is the destination.
    assert "window.location" not in src
    assert "location.pathname" not in src


def test_destination_mode_is_declared_by_the_page_not_the_widget():
    # PR3 replaced the inline "open the widget" script with two declarative
    # hooks. The page says WHERE Coach mounts and THAT it is the destination;
    # the widget decides what that means. No inline script is needed at all,
    # which is why the CSP-nonced <script> block is gone rather than unnonced.
    tpl = _read("templates", "coach_v2.html")
    assert "data-coach-destination" in tpl
    assert "data-coach-mount" in tpl
    assert "window.axCoachOpen" not in tpl
    assert "<script>" not in tpl                 # no inline script survives

    src = _read("static", "coach_widget.js")
    assert "data-coach-destination" in src
    assert "data-coach-mount" in src


def test_destination_mode_does_not_poll_for_the_widget():
    # The old opener polled until CW existed. The widget tag now sits after the
    # mount point, so the mount is in the DOM before the widget initialises.
    tpl = _read("templates", "coach_v2.html")
    assert "setInterval" not in tpl
    assert "setTimeout" not in tpl
    assert tpl.index("data-coach-mount") < tpl.index("/static/coach_widget.js")


# ══════════════════════════════════════════════════════════════════════════
# F. TEMPLATE SAFETY + COPY
# ══════════════════════════════════════════════════════════════════════════

def test_v2_has_exactly_one_h1(app, client, make_user, login):
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/coach").get_data(as_text=True)
    assert len(re.findall(r"<h1\b", html)) == 1


def test_v2_sets_coach_nav_active():
    tpl = _read("templates", "coach_v2.html")
    assert "set nav_active = 'coach'" in tpl or "set nav_active='coach'" in tpl


def test_v2_no_raw_localization_keys_leak(app, client, make_user, login):
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = True
    _seed_login(client, make_user, login)
    html = client.get("/coach").get_data(as_text=True)
    assert ">coach.v2.title<" not in html
    assert ">coach.v2.context<" not in html
    assert ">coach.v2.view_plan<" not in html


def test_v2_uses_no_inline_event_handlers():
    # PR3 removed the only data-action on this page (the launcher). The rule it
    # existed to satisfy — delegation, never inline handlers — still holds.
    tpl = _read("templates", "coach_v2.html")
    assert "onclick=" not in tpl
    assert "onload=" not in tpl


def test_coach_v2_copy_is_axisai_only():
    # No "FitX" in the new product-facing Coach copy.
    from app.i18n import _CATALOG
    for loc in ("tr", "en"):
        for key, val in _CATALOG[loc].items():
            if key.startswith("coach.v2."):
                assert "fitx" not in val.lower(), f"{loc}:{key}"


def test_coach_v2_copy_makes_no_plan_authority_claim():
    # Honest surface: Coach does not claim to control/own the Plan.
    from app.i18n import _CATALOG
    banned = ("controls the plan", "planı yönet", "planını yönet")
    for loc in ("tr", "en"):
        for key, val in _CATALOG[loc].items():
            if key.startswith("coach.v2."):
                low = val.lower()
                for b in banned:
                    assert b not in low, f"{loc}:{key}"


# ══════════════════════════════════════════════════════════════════════════
# G. i18n KEYS PRESENT (parity is enforced in full by test_i18n.py)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("key", [
    "coach.v2.title", "coach.v2.context", "coach.v2.view_plan",
])
def test_coach_v2_keys_exist_in_both_locales(key):
    from app.i18n import _CATALOG
    assert key in _CATALOG["tr"]
    assert key in _CATALOG["en"]


@pytest.mark.parametrize("retired",
                         ["coach.v2.open", "coach.v2.hint", "coach.v2.intro"])
def test_retired_launcher_copy_is_gone_from_both_locales(retired):
    # These strings only ever labelled the "Open Coach" button, its hint, and
    # the hero paragraph that explained "the chat opens on this page".
    # The button is gone; leaving the copy behind invites the button back.
    from app.i18n import _CATALOG
    assert retired not in _CATALOG["tr"]
    assert retired not in _CATALOG["en"]
