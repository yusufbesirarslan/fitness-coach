"""WEB-UX4-PR3 — the Coach destination contract, read from source.

The companion suite (``test_ux4_pr3_coach_destination_browser.py``) opens
Chromium and measures what a user is actually given. This one asserts the
invariants that have to hold in the *source*, because they are what makes the
measured behaviour reproducible rather than accidental:

  * there is exactly ONE Coach implementation, one composer, one submit, one
    ``/coach/history`` hydration — the destination changes where the widget
    lives, never what it is;
  * the AI boundary is untouched — no route, prompt, provider, streaming-frame
    or plan-mutation change may ride along with a presentation PR;
  * the retired lime palette, the raw 9998/9999 layering and the half-pixel
    type scale cannot come back silently;
  * every user-facing and accessibility-facing string in the widget goes
    through the existing catalogue at exact TR/EN parity.

Every assertion here fails on the PR2 baseline (b9a0925) for the reason the
UX4 discovery recorded, not because it searches for a class name this PR
happens to introduce.
"""
from __future__ import annotations

import json
import os
import re

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts: str) -> str:
    with open(os.path.join(REPO, *parts), encoding="utf-8") as handle:
        return handle.read()


def _catalog(locale: str) -> dict:
    with open(os.path.join(REPO, "locales", f"{locale}.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _strip_template_comments(tpl: str) -> str:
    """Jinja comments are stripped before the browser ever sees the page, so a
    positional assertion that counts them measures the explanation rather than
    the markup. Length-preserving, so surrounding offsets stay meaningful."""
    return re.sub(r"\{#.*?#\}", lambda m: " " * len(m.group(0)), tpl, flags=re.S)


def _strip_css_comments(css: str) -> str:
    """Same rule for stylesheets: a declaration is code, a comment is prose.
    A note recording the value this PR retired must not read as that value
    coming back."""
    return re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), css, flags=re.S)


WIDGET_JS = "static/coach_widget.js"
WIDGET_CSS = "static/coach_widget.css"
V2_TPL = "templates/coach_v2.html"
LEGACY_TPL = "templates/coach.html"


# ══════════════════════════════════════════════════════════════════════════
# F-04 — the destination hosts the conversation as page content
# ══════════════════════════════════════════════════════════════════════════

def test_v2_template_declares_a_destination_mount_in_the_content_column():
    """The conversation must be composed INTO the page, not floated over it.

    The V2 shell carries an explicit mount inside ``<main>``; the widget
    injects its one tree there instead of at the foot of ``<body>``.
    """
    tpl = _strip_template_comments(_read(V2_TPL))
    assert "data-coach-mount" in tpl, "V2 declares no destination mount"
    main_at = tpl.index("<main")
    mount_at = tpl.index("data-coach-mount")
    assert main_at < mount_at < tpl.index("</main>"), \
        "the Coach mount is outside the page content column"


def test_v2_body_opts_into_destination_mode():
    tpl = _strip_template_comments(_read(V2_TPL))
    assert "data-coach-destination" in tpl, "V2 never opts into destination mode"
    body_at = tpl.index("<body")
    body_tag = tpl[body_at:tpl.index(">", body_at)]
    assert "data-coach-destination" in body_tag, \
        f"destination mode is not declared on <body>: {body_tag!r}"


def test_widget_honours_the_destination_mount_and_mode():
    """The ONE widget mounts differently; it is not a second implementation."""
    src = _read(WIDGET_JS)
    assert "data-coach-mount" in src, "widget cannot mount into a page host"
    assert "data-coach-destination" in src, "widget has no destination-mode gate"


def test_destination_mode_drops_the_fixed_support_window_geometry():
    """``position: fixed`` + 360x500 is the support-widget composition F-04 names."""
    css = _read(WIDGET_CSS)
    dest = [line for line in css.splitlines() if "data-coach-destination" in line]
    assert dest, "no destination-mode presentation exists"
    joined = "\n".join(dest)
    assert "position:static" in joined.replace(" ", "") or \
           "position:relative" in joined.replace(" ", ""), \
        "destination mode never leaves fixed positioning"


def test_v2_has_no_open_coach_cta_under_the_conversation():
    """§17 — no CTA to open something that is already on the page."""
    tpl = _read(V2_TPL)
    assert "axCoachOpen" not in tpl
    assert "coach.v2.open" not in tpl


# ══════════════════════════════════════════════════════════════════════════
# F-04 / §18 — deterministic initialization, no polling
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("template", [V2_TPL])
def test_v2_does_not_poll_to_mount_coach(template):
    tpl = _read(template)
    for banned in ("setInterval", "setTimeout", "MutationObserver"):
        assert banned not in tpl, f"{template} still polls with {banned}"


def test_widget_does_not_poll_to_mount_itself():
    src = _read(WIDGET_JS)
    assert "setInterval" not in src, "widget mounts by polling"


# ══════════════════════════════════════════════════════════════════════════
# ONE Coach implementation (§5, §34) — unchanged invariants
# ══════════════════════════════════════════════════════════════════════════

def test_widget_has_module_level_init_guard_before_any_side_effect():
    src = _read(WIDGET_JS)
    assert "window.__cwWidgetInit" in src
    guard = src.index("if (window.__cwWidgetInit) return;")
    assert guard < src.index("var CW = window.CW")
    assert guard < src.index("fetch('/coach/history'")


def test_widget_bootstraps_and_hydrates_exactly_once():
    src = _read(WIDGET_JS)
    assert src.count("var CW = window.CW") == 1
    assert src.count("fetch('/coach/history'") == 1


def test_widget_adopts_an_existing_host_instead_of_injecting_twice():
    assert "getElementById('cw-root')" in _read(WIDGET_JS)


def test_v2_template_does_not_reimplement_the_widget():
    """A mount point is not a Coach implementation — a composer would be."""
    tpl = _read(V2_TPL)
    for marker in ('id="cw-root"', 'id="cw-input"', 'id="cw-window"',
                   'id="cw-send"', 'id="cw-fab"', 'id="cw-msgs"'):
        assert marker not in tpl, marker


def test_v2_references_the_widget_exactly_once():
    assert _read(V2_TPL).count("/static/coach_widget.js") == 1


# ══════════════════════════════════════════════════════════════════════════
# F-05 / §16 — composer before send, composer has a name
# ══════════════════════════════════════════════════════════════════════════

def test_composer_precedes_send_in_the_widget_dom():
    """DOM order is the tab order. Send must never come first."""
    src = _read(WIDGET_JS)
    composer = src.index("id=\"cw-input\"")
    send = src.index("id=\"cw-send\"")
    assert composer < send, "the send control is built before the composer"


def test_composer_has_an_accessible_name_from_the_catalogue():
    src = _read(WIDGET_JS)
    row = src[src.index("id=\"cw-irow\""):src.index("id=\"cw-scan\"")]
    at = row.index("id=\"cw-input\"")
    # The composer now leads the row, so its tag can start at offset 0 — clamp
    # rather than letting a negative start wrap round to the end of the slice.
    input_decl = row[max(0, at - 120):at + 320]
    assert "aria-label" in input_decl, "#cw-input is named by placeholder alone"
    assert "coach.composer_label" in src


def test_no_positive_tabindex_anywhere_in_coach():
    """§15 — order by DOM, never by a positive tabindex."""
    for path in (WIDGET_JS, V2_TPL, LEGACY_TPL):
        src = _read(path)
        assert not re.search(r"tabindex\s*=\s*[\"']?[1-9]", src), path


# ══════════════════════════════════════════════════════════════════════════
# F-13 — no hardcoded user-facing copy in the widget
# ══════════════════════════════════════════════════════════════════════════

_ALLOWED_LITERALS = {
    # Locale-neutral: an example URL in a url-type input.
    "https://menu.example.com",
}

# A user-facing literal is a quoted run that reaches the DOM as text or as an
# accessibility name. Matching the ATTRIBUTES and the markup text runs is what
# makes this test survive a rewrite: it does not look for today's strings.
_NAME_ATTR = re.compile(r"(aria-label|title|placeholder)=[\"']([^\"'+]{2,})[\"']")


def test_no_hardcoded_accessibility_names_in_the_widget():
    src = _read(WIDGET_JS)
    leaked = [m.group(0) for m in _NAME_ATTR.finditer(src)
              if m.group(2).strip() not in _ALLOWED_LITERALS
              and not m.group(2).strip().startswith("' +")]
    assert not leaked, f"hardcoded accessibility names: {leaked}"


def test_no_hardcoded_english_product_title_in_static_sources():
    """The exact F-13 regression: the AI introduces itself in English."""
    for name in os.listdir(os.path.join(REPO, "static")):
        if not name.endswith((".js", ".css", ".html")):
            continue
        assert "AI Fitness Coach" not in _read("static", name), name


def _strip_comments(src: str) -> str:
    """Comments describe the code; they are never painted. Scanning them would
    make this test fail on its own explanation."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", " ", src)


def test_widget_user_facing_text_runs_go_through_the_catalogue():
    """Markup text nodes in the injected HTML must be t() calls.

    A run of letters sitting between ``>`` and ``<`` inside the widget's HTML
    string is painted copy; if it is a literal it cannot be translated.
    """
    src = _strip_comments(_read(WIDGET_JS))
    leaked = []
    for match in re.finditer(r">([^<>'\"]*[A-Za-zÇĞİÖŞÜçğıöşü]{3,}[^<>'\"]*)<", src):
        text = match.group(1).strip()
        if not text or text in _ALLOWED_LITERALS:
            continue
        leaked.append(text)
    assert not leaked, f"hardcoded widget copy: {leaked}"


def test_coach_catalogue_keys_are_at_exact_tr_en_parity():
    tr, en = _catalog("tr"), _catalog("en")
    tr_keys = {k for k in tr if k.startswith("coach.")}
    en_keys = {k for k in en if k.startswith("coach.")}
    assert tr_keys == en_keys, tr_keys ^ en_keys
    assert set(tr) == set(en), "catalogue parity broke outside coach.*"


def test_every_coach_key_used_by_the_widget_exists_in_both_locales():
    src = _read(WIDGET_JS)
    used = set(re.findall(r"t\(\s*'(coach\.[a-z0-9_.]+)'", src))
    assert used, "the widget resolves no catalogue keys at all"
    tr, en = _catalog("tr"), _catalog("en")
    missing = sorted(k for k in used if k not in tr or k not in en)
    assert not missing, missing


def test_new_coach_copy_is_axisai_not_fitx():
    tr, en = _catalog("tr"), _catalog("en")
    for locale, cat in (("tr", tr), ("en", en)):
        for key, value in cat.items():
            if key.startswith("coach."):
                assert "fitx" not in str(value).lower(), f"{locale}:{key}"


# ══════════════════════════════════════════════════════════════════════════
# F-26 — Coach layers on the documented z-scale, below toast
# ══════════════════════════════════════════════════════════════════════════

def test_coach_declares_no_raw_high_z_index():
    # Declarations only: the note recording what 9998 used to do is prose, and
    # a stylesheet that cannot explain its own history is a worse stylesheet.
    css = _strip_css_comments(_read(WIDGET_CSS))
    raw = [int(v) for v in re.findall(r"z-index:\s*(\d+)", css)]
    assert not [v for v in raw if v >= 1000], f"raw Coach layering: {raw}"


def test_coach_root_uses_the_fab_layer_and_notify_the_toast_layer():
    css = _read(WIDGET_CSS)
    root = css[css.index("#cw-root{"):css.index("\n", css.index("#cw-root{"))]
    assert "var(--z-fab)" in root, "#cw-root bypasses the z-index scale"
    notify = css[css.index("#cw-notify{"):css.index("\n", css.index("#cw-notify{"))]
    assert "var(--z-toast)" in notify, "#cw-notify bypasses the z-index scale"


def test_coach_adds_no_new_z_index_token():
    tokens = _read("static", "tokens.css")
    names = set(re.findall(r"--z-[a-z-]+", tokens))
    assert names == {"--z-header", "--z-drawer-backdrop", "--z-drawer",
                     "--z-fab", "--z-overlay", "--z-toast"}, names


# ══════════════════════════════════════════════════════════════════════════
# F-27 — readable Coach typography on the canonical scale
# ══════════════════════════════════════════════════════════════════════════

def test_coach_stylesheet_has_no_half_pixel_type():
    css = _read(WIDGET_CSS)
    halves = re.findall(r"font-size:\s*(\d+\.\d+)px", css)
    assert not halves, f"off-scale half-pixel type survives: {halves}"


def test_coach_body_text_uses_a_body_token_at_a_readable_weight():
    css = _read(WIDGET_CSS)
    bubble = css[css.index(".cw-bubble{"):css.index("}", css.index(".cw-bubble{"))]
    assert "var(--text-base)" in bubble, bubble
    assert "font-weight:300" not in bubble.replace(" ", ""), \
        "the longest text in the product still renders at weight 300"
    assert "var(--weight-light)" not in bubble


def test_composer_font_clears_the_ios_focus_zoom_threshold():
    """A sub-16px input zooms the viewport on focus. 16px is the threshold."""
    css = _read(WIDGET_CSS)
    decl = css[css.index("#cw-input{"):css.index("}", css.index("#cw-input{"))]
    match = re.search(r"font-size:\s*var\((--text-[a-z0-9]+)\)", decl)
    assert match, f"composer font-size is not tokenised: {decl}"
    tokens = _read("static", "tokens.css")
    size = re.search(rf"{re.escape(match.group(1))}:\s*(\d+)px", tokens)
    assert size and int(size.group(1)) >= 16, \
        f"composer renders at {size and size.group(1)}px — below the 16px threshold"


# ══════════════════════════════════════════════════════════════════════════
# F-28 — the retired lime palette is gone, and cannot return
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("dead", ["#99CC00", "#D6FF1A",
                                  "--color-chat-avatar-accent",
                                  "--color-chat-send-hover"])
def test_retired_lime_palette_is_absent_from_every_stylesheet(dead):
    for name in sorted(os.listdir(os.path.join(REPO, "static"))):
        if not name.endswith(".css"):
            continue
        assert dead.lower() not in _read("static", name).lower(), f"{dead} in {name}"


def test_coach_introduces_no_arbitrary_hex_colour():
    css = _read(WIDGET_CSS)
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not hexes, f"raw colour values in the Coach stylesheet: {hexes}"


# ══════════════════════════════════════════════════════════════════════════
# F-33 — Coach honours reduced motion
# ══════════════════════════════════════════════════════════════════════════

def test_coach_stylesheet_covers_prefers_reduced_motion():
    css = _read(WIDGET_CSS)
    assert "prefers-reduced-motion" in css, \
        "the most animated surface in the product still has no reduced-motion block"


def test_coach_reduced_motion_block_disables_its_keyframe_animations():
    css = _read(WIDGET_CSS)
    block = css[css.index("prefers-reduced-motion"):]
    declared = set(re.findall(r"@keyframes\s+([a-z0-9-]+)", css))
    assert declared, "no keyframes to govern"
    assert "animation" in block, "reduced-motion block governs no animation"


def test_coach_uses_motion_tokens_not_bare_durations():
    css = _read(WIDGET_CSS)
    bare = re.findall(r"transition:[^;}]*?(?<![\w-])(\.\d+|\d+\.?\d*)s", css)
    assert not bare, f"untokenised transition durations: {sorted(set(bare))}"


def test_coach_declares_no_transition_all():
    assert "transition:all" not in _read(WIDGET_CSS).replace(" ", "")


# ══════════════════════════════════════════════════════════════════════════
# HARD ARCHITECTURE BOUNDARY (§3, §37, §41, §50)
# ══════════════════════════════════════════════════════════════════════════

_AI_ENDPOINTS = ("/ask/stream", "/ask", "/coach/history")


def test_widget_still_talks_to_exactly_the_same_ai_surface():
    src = _read(WIDGET_JS)
    for endpoint in _AI_ENDPOINTS:
        assert f"'{endpoint}'" in src, endpoint
    assert src.count("fetch('/ask/stream'") == 1
    assert src.count("fetch('/ask'") == 1


def test_coach_page_adds_no_new_server_read():
    """§41 — presentation must not duplicate domain data fetching."""
    tpl = _read(V2_TPL)
    assert "fetch(" not in tpl
    for route in ("/training/bootstrap", "/api/v1", "/progress", "/nutrition/diary"):
        assert route not in tpl, route


def test_coach_blueprint_is_unchanged_presentation_only():
    """The route still renders one of two templates and reads no domain data."""
    src = _read("app", "blueprints", "coach.py")
    fn = src[src.index("def coach_page("):src.index("def _ai_cooldown_response(")]
    assert "coach_v2.html" in fn and "coach.html" in fn
    assert "UIUX_COACH_PAGE_V2_ENABLED" in fn
    assert "query" not in fn and "db.session" not in fn


def test_widget_never_writes_the_training_plan():
    """§37 — presentation can improve, authority cannot."""
    src = _read(WIDGET_JS)
    for banned in ("/training/plan", "plan_mutation", "coach_plan_tools",
                   "/plan/apply", "/training/apply"):
        assert banned not in src, banned


def test_widget_keeps_its_sanitisation_and_pinned_integrity():
    src = _read(WIDGET_JS)
    assert "DOMPurify.sanitize" in src
    assert src.count("integrity") >= 1 and "crossOrigin" in src
    # User-authored text is escaped, never routed through the markdown path.
    assert "m.role === 'user' ? self._esc(" in src


def test_v2_inline_script_free_or_nonced():
    tpl = _read(V2_TPL)
    for match in re.finditer(r"<script(?![^>]*\bsrc=)([^>]*)>", tpl):
        assert "csp_nonce" in match.group(1), f"un-nonced inline script: {match.group(0)}"


# ══════════════════════════════════════════════════════════════════════════
# FLAG CONTRACT (§4, §48) — one selector, unchanged default, legacy rollback
# ══════════════════════════════════════════════════════════════════════════

def test_rollout_selector_is_unchanged_and_defaults_off(app):
    assert app.config.get("UIUX_COACH_PAGE_V2_ENABLED", False) is False


def test_flag_off_renders_the_legacy_rollback_page(client, make_user, login):
    make_user("coachrollback", profile_complete=True)
    login("coachrollback")
    html = client.get("/coach").get_data(as_text=True)
    assert "data-coach-v2" not in html
    assert "data-coach-destination" not in html
    assert 'class="coach-page-title"' in html


def test_flag_on_renders_the_destination(app, client, make_user, login):
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = True
    make_user("coachdest", profile_complete=True)
    login("coachdest")
    html = client.get("/coach").get_data(as_text=True)
    assert "data-coach-v2" in html
    assert "data-coach-destination" in html
    assert "data-coach-mount" in html


def test_missing_flag_fails_safe_to_legacy(app, client, make_user, login):
    app.config.pop("UIUX_COACH_PAGE_V2_ENABLED", None)
    make_user("coachsafe", profile_complete=True)
    login("coachsafe")
    assert "data-coach-v2" not in client.get("/coach").get_data(as_text=True)


def test_coach_presentation_is_not_coupled_to_the_plan_flag(app, client, make_user, login):
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = True
    make_user("coachplanflag", profile_complete=True)
    login("coachplanflag")
    # The CSP nonce is minted per request, so it is normalised out — otherwise
    # this compares randomness, not the Plan flag.
    nonce = re.compile(r'nonce="[^"]+"')
    app.config["UIUX_PLAN_V2_ENABLED"] = False
    off = nonce.sub('nonce="N"', client.get("/coach").get_data(as_text=True))
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    on = nonce.sub('nonce="N"', client.get("/coach").get_data(as_text=True))
    assert off == on, "the Plan flag changes the Coach page"


@pytest.mark.parametrize("flag", [False, True])
def test_coach_requires_auth_in_both_flag_states(app, client, flag):
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = flag
    resp = client.get("/coach")
    assert resp.status_code in (301, 302, 401, 403)
    assert "data-coach-mount" not in resp.get_data(as_text=True)


def test_legacy_coach_page_is_untouched_as_a_rollback_target():
    """§26 — the rollback target keeps its own presentation."""
    tpl = _read(LEGACY_TPL)
    assert "data-coach-mount" not in tpl
    assert "data-coach-destination" not in tpl
    assert 'class="coach-page-title"' in tpl


# ══════════════════════════════════════════════════════════════════════════
# F-39 — Nutrition's live dependency on the ONE widget
# ══════════════════════════════════════════════════════════════════════════

def test_nutrition_still_owns_a_live_menu_scan_dependency_on_the_widget():
    """The evidence behind the F-39 disposition, asserted so it cannot rot.

    If this ever fails, the dependency is dead and the asset path can be
    removed — with regression tests, in its own PR.
    """
    assert "window.CW.startScan()" in _read("static", "nutrition.js")
    assert 'data-action="logMenuScan"' in _read("templates", "nutrition.html")
    assert "/static/coach_widget.js" in _read("templates", "nutrition.html")


def test_nutrition_does_not_opt_into_coach_destination_mode():
    """Destination presentation is scoped to /coach; every other host keeps
    the floating widget it expects (§25)."""
    tpl = _read("templates", "nutrition.html")
    assert "data-coach-destination" not in tpl
    assert "data-coach-mount" not in tpl
