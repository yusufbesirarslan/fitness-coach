"""WEB-UX4-PR2 - foundation contract guards (source / computed-value level).

These protect the repairs UX4 discovery proved necessary
(docs/superpowers/specs/2026-09-14-web-ux4-premium-experience-discovery.md):

  F-01  every ``var(--*)`` reference resolves to a defined custom property
  F-02  ``--focus-ring`` is perceptible (>= 3:1) on every app surface
  F-03  ``--focus-ring`` is never used as an ``outline`` value
  F-10  the canonical buttons declare a deterministic ``font-family``
  F-12  ``.sec-label`` is the canonical micro-label
  F-18  ``--color-text-4`` is not used for the text discovery measured
  F-21  the canonical buttons carry focus / disabled / loading / pressed rules
  F-26  the shared foundation layer stays on the ``--z-*`` scale
  F-29  the shared library obeys its own radius / spacing / tracking scale
  F-32  DM Sans is not downloaded and is not in the fallback stack
  F-33  the shared primitives honour ``prefers-reduced-motion``
  F-36  destructive rank is not "primary button with a different hue"
  F-37  selection does not speak with the primary call-to-action fill

They are deliberately source-level: the rendered consequences are proven
separately by ``test_ux4_pr2_foundations_browser.py``, which measures what a
real browser computes under a real ``Tab`` press.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"

BASELINE_REV = "7c07439b228aee9a31f65ca4e1090799cd3badd3"

_AA_NON_TEXT = 3.0
_AA_NORMAL = 4.5
_SURFACES = ("--color-bg", "--color-surface-1", "--color-surface-2",
             "--color-surface-3")

_BUTTONS = (".btn-volt", ".btn-ghost", ".btn-danger")


# -- helpers --------------------------------------------------------------

def _css(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _all_css() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(STATIC.glob("*.css"))}


def _block(css: str, header: str) -> str:
    """Return the declaration body of the first rule whose header matches."""
    start = css.index(header)
    open_at = css.index("{", start)
    depth = 0
    for i, ch in enumerate(css[open_at:], open_at):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return css[open_at + 1:i]
    raise AssertionError("unclosed block for %r" % header)


def _decl(block: str, prop: str) -> str | None:
    m = re.search(r"(?<![-\w])%s\s*:\s*([^;]+)" % re.escape(prop), block)
    return m.group(1).strip() if m else None


def _token_map(block: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block, re.I)}


def _theme(name: str) -> dict[str, str]:
    """Resolved token map for a theme.

    ``[data-theme="light"]`` only OVERRIDES; everything it does not redefine
    (``--focus-ring`` among them) still comes from ``:root``. Reading the light
    block alone would miss exactly the tokens this suite is about.
    """
    css = _css("tokens.css")
    tokens = _token_map(_block(css, ":root {"))
    if name == "light":
        tokens.update(_token_map(_block(css, '[data-theme="light"] {')))
    return tokens


def _resolve_hex(tokens: dict[str, str], name: str) -> str:
    value = tokens[name]
    seen: set[str] = set()
    while value.startswith("var("):
        inner = value[4:value.index(")")].split(",")[0].strip()
        assert inner not in seen, "cycle resolving %s" % name
        seen.add(inner)
        value = tokens[inner]
    m = re.fullmatch(r"#([0-9A-Fa-f]{6})", value.strip())
    assert m, "%s resolved to non-hex %r" % (name, value)
    return "#" + m.group(1).upper()


def _lin(channel: float) -> float:
    channel /= 255.0
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    raw = hex_color.lstrip("#")
    r, g, b = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast(fg: str, bg: str) -> float:
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _composite(fg: str, bg: str, alpha: float) -> str:
    raw_fg, raw_bg = fg.lstrip("#"), bg.lstrip("#")
    return "#" + "".join(
        "%02X" % round(int(raw_fg[i:i + 2], 16) * alpha
                       + int(raw_bg[i:i + 2], 16) * (1 - alpha))
        for i in (0, 2, 4))


def _reduced_motion_body(css: str) -> str:
    m = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{", css)
    assert m, "components.css has no reduced-motion block"
    body_start = css.index("{", m.start())
    depth = 0
    for i, ch in enumerate(css[body_start:], body_start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return css[body_start + 1:i]
    raise AssertionError("unclosed reduced-motion block")


# -- F-01 - every var(--*) reference resolves -----------------------------

def test_every_custom_property_reference_resolves():
    """A ``var(--x)`` whose property is never defined silently erases the
    declaration. ``--space-7`` did exactly that to ``.auth-card``'s padding,
    which measured 0px at 768 / 1024 / 1366.

    Fails again the moment any undefined custom property is referenced.
    """
    defined: set[str] = set()
    sources: dict[str, str] = {}
    for path in sorted(STATIC.glob("*.css")) + sorted(TEMPLATES.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        sources[path.name] = text
        defined.update(m.group(1)
                       for m in re.finditer(r"(--[A-Za-z0-9_-]+)\s*:", text))

    unresolved: dict[str, set[str]] = {}
    for name, text in sources.items():
        for m in re.finditer(r"var\(\s*(--[A-Za-z0-9_-]+)", text):
            if m.group(1) not in defined:
                unresolved.setdefault(m.group(1), set()).add(name)

    assert not unresolved, "undefined custom properties referenced: " + "; ".join(
        "%s in %s" % (k, sorted(v)) for k, v in sorted(unresolved.items()))


def test_space_7_is_retargeted_and_never_minted():
    """The repair retargets the three references onto an existing step.

    Discovery's section T is explicit: a UX4-PR2 that adds a spacing token is
    out of scope by the document's own definition.
    """
    for name, css in _all_css().items():
        assert "--space-7" not in css, "%s still mentions --space-7" % name


def test_auth_card_padding_comes_from_the_spacing_scale():
    block = _block(_strip_comments(_css("auth.css")), ".auth-card {")
    padding = _decl(block, "padding")
    assert padding == "var(--space-6)", (
        ".auth-card padding must be an existing spacing step, got %r" % padding)


# -- F-02 - the focus token is perceptible --------------------------------

@pytest.mark.parametrize("theme", ["dark", "light"])
def test_focus_ring_meets_non_text_contrast_on_every_surface(theme):
    """WCAG 2.2 SC 1.4.11 requires 3:1 for a focus indicator.

    Baseline measured 1.07-1.09:1 - the ring existed but could not be seen.
    This fails again at any alpha that drops a surface below 3:1, in either
    theme, because the light theme redefines ``--color-primary``.
    """
    tokens = _theme(theme)
    value = tokens["--focus-ring"]
    primary = _resolve_hex(tokens, "--color-primary")
    alpha = re.search(
        r"rgba\(\s*var\(--color-primary-rgb\)\s*,\s*([0-9.]+)\s*\)", value)
    if not alpha:
        assert "var(--color-primary)" in value, (
            "--focus-ring must be expressed through the primary palette, "
            "got %r" % value)

    for surface in _SURFACES:
        bg = _resolve_hex(tokens, surface)
        painted = (_composite(primary, bg, float(alpha.group(1)))
                   if alpha else primary)
        ratio = _contrast(painted, bg)
        assert ratio >= _AA_NON_TEXT, (
            "%s --focus-ring %r paints %s on %s %s at %.2f:1 (needs %s:1)"
            % (theme, value, painted, surface, bg, ratio, _AA_NON_TEXT))


def test_focus_ring_stays_a_box_shadow_value():
    """The token's syntactic role is a ``box-shadow``; nine call sites use it
    that way. Turning it into an ``outline`` shorthand would invert the
    contract and erase focus at exactly those nine sites - the same
    computed-value failure F-03 describes, mirrored.
    """
    value = _theme("dark")["--focus-ring"]
    assert re.match(r"^0\s+0\s+0\s+\d+px\s", value), (
        "--focus-ring must stay a box-shadow ring value, got %r" % value)


def test_focus_ring_is_at_least_two_pixels_thick():
    value = _theme("dark")["--focus-ring"]
    spread = int(re.match(r"^0\s+0\s+0\s+(\d+)px", value).group(1))
    assert spread >= 2, "focus ring spread %dpx is below 2px" % spread


# -- F-03 - the token is never an outline value ---------------------------

def test_focus_ring_is_never_used_as_an_outline_value():
    """``outline: var(--focus-ring, ...)`` is invalid at computed-value time.

    ``var()`` defeats the parse-time check, so ``outline`` resolves to its
    initial ``none`` - and does NOT fall back to the previous cascade winner.
    Five controls, including "delete a logged meal", lost focus this way.
    """
    offenders = []
    for name, raw in _all_css().items():
        css = _strip_comments(raw)
        for match in re.finditer(r"outline\s*:\s*[^;{}]*var\(\s*--focus-ring",
                                 css):
            offenders.append("%s:%d" % (name, css[:match.start()].count("\n") + 1))
    assert not offenders, (
        "--focus-ring used as an outline value at " + ", ".join(offenders))


@pytest.mark.parametrize("name,selector", [
    ("nutrition.css", ".qab:focus-visible"),
    ("progress.css", ".overload-chip:focus-visible"),
    ("progress.css", 'input[type="range"]:focus-visible'),
])
def test_repaired_focus_rules_still_paint_a_canonical_ring(name, selector):
    """Guards against "fixing" F-03 by deleting the rules, which would drop
    these controls onto the browser default ring (or nothing at all, where a
    page rule suppresses the outline)."""
    css = _strip_comments(_css(name))
    assert selector in css, "%s no longer styles %s" % (name, selector)
    block = _block(css, selector)
    outline = _decl(block, "outline")
    assert outline == "2px solid var(--color-primary)", (
        "%s %s must paint the canonical ring, got %r" % (name, selector, outline))


# -- F-10 / F-21 / F-36 - the canonical button family ---------------------

@pytest.mark.parametrize("selector", _BUTTONS)
def test_canonical_button_declares_a_deterministic_font_family(selector):
    """``.btn-ghost`` computed Arial as a <button> and Inter as an <a>.

    A class that renders in two typefaces depending on its element is the
    clearest "cheap" signal in the product. Every canonical button must name
    its face, and it must be an EXISTING design-system face.
    """
    block = _block(_strip_comments(_css("components.css")), selector + " {")
    value = _decl(block, "font-family")
    assert value in ("var(--font-display)", "var(--font-body)"), (
        "%s must name an existing type token, got %r" % (selector, value))


@pytest.mark.parametrize("selector", _BUTTONS)
def test_canonical_button_shares_one_control_geometry(selector):
    """Measured baseline: ghost 32-35px vs volt 45px, radius 8 vs 12.

    The three must read as one family: one minimum height (the repo's existing
    44px touch standard, used at 26 sites) and one radius rule, with padding
    from the spacing scale.
    """
    block = _block(_strip_comments(_css("components.css")), selector + " {")
    assert re.search(r"min-height\s*:\s*44px", block), (
        "%s must declare the 44px minimum control height" % selector)
    assert _decl(block, "border-radius") == "var(--radius-md)", (
        "%s must use var(--radius-md), got %r"
        % (selector, _decl(block, "border-radius")))
    padding = _decl(block, "padding")
    assert padding and "var(--space-" in padding, (
        "%s padding must come from the spacing scale, got %r"
        % (selector, padding))


@pytest.mark.parametrize("selector", _BUTTONS)
@pytest.mark.parametrize("state", [":hover", ":active", ":focus-visible",
                                   ":disabled", ".loading"])
def test_canonical_button_covers_every_interaction_state(selector, state):
    """No canonical button had a ``:focus-visible`` or ``:disabled`` rule, so
    in-flight and unavailable states were undesigned across the product."""
    css = _strip_comments(_css("components.css"))
    assert re.search(re.escape(selector + state) + r"(?![-\w])", css), (
        "components.css has no %s%s rule" % (selector, state))


def test_canonical_button_focus_is_the_shared_ring_not_the_browser_default():
    css = _strip_comments(_css("components.css"))
    block = _block(css, ".btn-volt:focus-visible")
    assert _decl(block, "outline") == "2px solid var(--color-primary)", (
        "the canonical buttons must carry the shared focus language")


def test_btn_danger_ranks_apart_from_the_primary_call_to_action():
    """F-36: destroy must not sit at the same visual rank as confirm.

    Identical geometry AND an identical solid fill made "delete" read as a
    primary call to action one Tab away from a toggle.
    """
    css = _strip_comments(_css("components.css"))
    volt = _block(css, ".btn-volt {")
    danger = _block(css, ".btn-danger {")
    assert _decl(danger, "background") != "var(--color-danger)", (
        "`.btn-danger` is still a solid fill, i.e. `.btn-volt` with the hue "
        "swapped")
    assert _decl(volt, "background") == "var(--color-primary)", (
        "the primary CTA must keep the solid primary fill that outranks it")
    border = _decl(danger, "border")
    assert border and border != "none" and "--color-danger" in border, (
        "`.btn-danger` must carry its own border treatment to rank apart from "
        "the solid primary CTA, got %r" % border)


def test_btn_danger_label_is_readable_at_rest_and_on_hover():
    """The destructive rank has to stay legible where it actually sits.

    This is why the outlined treatment won: the SHIPPED solid fill put
    ``--white`` on ``--color-danger`` at 3.27:1, and a ``--color-danger-soft``
    tint drops the label to 4.07:1 on ``--color-surface-3``. Painting the label
    straight onto the page surface measures 4.69-5.73:1 at rest, and the hover
    affordance is an inset ring rather than a fill, so hover cannot lower it.
    """
    tokens = _theme("dark")
    danger = _resolve_hex(tokens, "--color-danger")
    for surface in _SURFACES:
        bg = _resolve_hex(tokens, surface)
        ratio = _contrast(danger, bg)
        assert ratio >= _AA_NORMAL, (
            "dark --color-danger %s on %s %s is %.2f:1"
            % (danger, surface, bg, ratio))

    block = _block(_strip_comments(_css("components.css")), ".btn-danger:hover")
    assert _decl(block, "background") is None, (
        "the destructive hover must not tint the label's own backdrop; that "
        "drops it to 4.07:1 on --color-surface-3")


def test_btn_danger_never_paints_white_on_the_danger_fill():
    """The shipped primitive measured 3.27:1 for its own label.

    It had zero consumers, so nothing in the product surfaced the defect - but
    PR6 is about to adopt it.
    """
    tokens = _theme("dark")
    bad = _contrast(_resolve_hex({"--white": "#FFFFFF"}, "--white"),
                    _resolve_hex(tokens, "--color-danger"))
    assert bad < _AA_NORMAL, "premise check: white on danger should be the "
    block = _block(_strip_comments(_css("components.css")), ".btn-danger {")
    assert _decl(block, "color") != "var(--white)", (
        "`.btn-danger` still paints --white on the danger fill (%.2f:1)" % bad)


def test_btn_danger_indicator_stays_perceptible_in_the_prepared_light_theme():
    """The light theme is prepared but unshipped (every page emits
    ``data-theme="dark"``), and PR2 may not change the palette. Its danger
    border must still clear the 3:1 non-text bar."""
    tokens = _theme("light")
    danger = _resolve_hex(tokens, "--color-danger")
    for surface in _SURFACES:
        bg = _resolve_hex(tokens, surface)
        ratio = _contrast(danger, bg)
        assert ratio >= _AA_NON_TEXT, (
            "light --color-danger %s on %s is %.2f:1" % (danger, surface, ratio))


def test_bare_controls_inherit_the_product_typeface():
    """The root cause of 41 Arial elements: <button> defaults to the UA face
    unless a font-family is declared.

    One shared normalisation in the library repairs ``.cat-chip``, ``.star``,
    ``.supp-act-btn``, ``.add-btn``, ``.auth-btn``, ``.setup-btn``,
    ``.log-fab`` and ``#cw-close`` without touching any page stylesheet.
    """
    css = _strip_comments(_css("components.css"))
    m = re.search(r"(?m)^\s*button\s*,[^{]*\{([^}]*)\}", css)
    assert m, "components.css declares no shared control reset"
    body = m.group(1)
    assert _decl(body, "font-family") == "inherit", (
        "the shared control reset must inherit the product typeface, got %r"
        % _decl(body, "font-family"))
    for control in ("input", "select", "textarea"):
        assert re.search(r"(?<![-\w])%s\s*[,{]" % control, m.group(0)), (
            "the shared control reset must also cover <%s>" % control)


# -- F-18 - --color-text-4 is not readable text ---------------------------

@pytest.mark.parametrize("name,selector", [
    ("coach_widget.css", ".cw-ts"),
    ("nutrition.css", ".mc-time"),
    ("nav.css", ".hub-section-label"),
])
def test_measured_text_no_longer_uses_the_hairline_colour(name, selector):
    """Measured 1.97:1, 1.97:1 and 2.22:1 as actual readable text.

    ``--color-text-4`` is a hairline / decoration value; ``--color-text-3``
    measures 5.22:1 and is the correct muted TEXT step. No palette change.
    """
    block = _block(_strip_comments(_css(name)), selector)
    assert "--color-text-4" not in block, (
        "%s %s still paints text with --color-text-4" % (name, selector))
    assert "--color-text-3" in block, (
        "%s %s must use the muted text step --color-text-3" % (name, selector))


def test_text_3_stays_readable_where_text_4_was_replaced():
    """Proves the replacement step is actually the right one.

    Dark is the shipped theme, so it carries the AA bar. The prepared light
    theme measures 4.44:1 on ``--color-surface-3`` at HEAD already; PR2 may not
    change the palette, so light is held to the same bar it shipped with and
    only guarded against regression.
    """
    dark = _theme("dark")
    dark_text3 = _resolve_hex(dark, "--color-text-3")
    for surface in _SURFACES:
        bg = _resolve_hex(dark, surface)
        ratio = _contrast(dark_text3, bg)
        assert ratio >= _AA_NORMAL, (
            "dark --color-text-3 on %s is %.2f:1" % (surface, ratio))

    light = _theme("light")
    light_text3 = _resolve_hex(light, "--color-text-3")
    worst = min(_contrast(light_text3, _resolve_hex(light, s)) for s in _SURFACES)
    assert worst >= 4.4, (
        "light --color-text-3 regressed below its shipped 4.44:1 (now %.2f:1)"
        % worst)


def test_decorative_uses_of_text_4_are_left_alone():
    """The palette is unchanged and the hairline role is legitimate - only
    the three measured TEXT usages move."""
    assert "--color-text-4" in _css("tokens.css")
    assert "--color-text-4" in _decl(
        _block(_strip_comments(_css("components.css")), ".empty-icon svg"),
        "stroke")


# -- F-26 - the shared foundation stays on the z scale --------------------

def test_shared_foundation_layers_use_the_z_token_scale():
    """PR3 must be able to place Coach below toast without minting a value."""
    for name in ("components.css", "nav.css"):
        css = _strip_comments(_css(name))
        raw = [m.group(1) for m in re.finditer(r"z-index\s*:([^;]+);", css)
               if not m.group(1).strip().startswith("var(--z-")]
        assert not raw, "%s bypasses the --z-* scale: %s" % (name, raw)


def test_z_scale_tops_out_at_toast_and_leaves_a_slot_below_it():
    tokens = _token_map(_block(_css("tokens.css"), ":root {"))
    scale = {k: int(v) for k, v in tokens.items()
             if k.startswith("--z-") and v.strip().isdigit()}
    assert scale, "no --z-* scale found"
    assert max(scale.values()) == scale["--z-toast"], (
        "toast must remain the top layer, got %s" % scale)
    assert scale["--z-overlay"] < scale["--z-toast"], (
        "there must be a documented overlay layer below toast")


# -- F-29 / F-12 - the library obeys its own scale ------------------------

@pytest.mark.parametrize("selector,prop", [
    (".tab-btn {", "border-radius"),
    (".tab-btn {", "gap"),
    (".chip {", "gap"),
    (".cat-label {", "letter-spacing"),
])
def test_shared_library_values_come_from_the_canonical_scale(selector, prop):
    """The library shipped 9px radii, 7px gaps and 0.14em tracking, so pages
    that correctly adopted it still inherited off-scale values - which is why
    page-by-page correction could not converge."""
    block = _block(_strip_comments(_css("components.css")), selector)
    value = _decl(block, prop)
    assert value and value.startswith("var(--"), (
        "%s %s is off-scale: %r" % (selector, prop, value))


def test_cat_label_shares_the_sec_label_tracking():
    """F-12: ``.sec-label`` is the canonical micro-label and ``.cat-label``
    is its smaller sibling, not a sixth variant."""
    css = _strip_comments(_css("components.css"))
    sec = _decl(_block(css, ".sec-label {"), "letter-spacing")
    cat = _decl(_block(css, ".cat-label {"), "letter-spacing")
    assert sec == cat == "var(--tracking-label)", (
        "sec-label %r / cat-label %r must share one tracking token"
        % (sec, cat))


def test_no_new_design_tokens_were_minted():
    """Discovery's hard contract: PR2 adds no token to any scale."""
    base = subprocess.run(
        ["git", "show", "%s:static/tokens.css" % BASELINE_REV],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    if base.returncode != 0:
        pytest.skip("baseline revision unavailable in this checkout")
    before = set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", base.stdout))
    after = set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", _css("tokens.css")))
    assert not (after - before), "new tokens minted: %s" % sorted(after - before)


# -- F-32 - DM Sans is gone -----------------------------------------------

def _template_markup(path: Path) -> str:
    """Template text with Jinja comments stripped.

    The repair itself is documented in a ``{# ... #}`` comment naming DM Sans,
    which is prose, not a network request.
    """
    return re.sub(r"\{#.*?#\}", "", path.read_text(encoding="utf-8"), flags=re.S)


def test_no_template_downloads_dm_sans():
    """A full family (5 weights + an italic) was fetched to render 4 elements.

    ``_head.html`` is not the only place that asks: ``404.html`` and
    ``500.html`` are standalone documents that do NOT include it and carried
    their own DM Sans request, so the product still had a download path.
    """
    offenders = [p.name for p in sorted(TEMPLATES.glob("*.html"))
                 if "DM+Sans" in _template_markup(p)
                 or "DM Sans" in _template_markup(p)]
    assert not offenders, "templates still requesting DM Sans: %s" % offenders


def test_only_the_axisai_families_are_requested_anywhere():
    families: set[str] = set()
    for path in sorted(TEMPLATES.glob("*.html")):
        families.update(re.findall(r"family=([A-Za-z+]+)",
                                   _template_markup(path)))
    assert families == {"Bebas+Neue", "Inter"}, (
        "unexpected font families requested: %s" % sorted(families))


def test_inter_keeps_the_weights_the_type_scale_uses():
    head = _template_markup(TEMPLATES / "_head.html")
    m = re.search(r"family=Inter:wght@([0-9;]+)", head)
    assert m, "_head.html no longer requests Inter with explicit weights"
    weights = {int(w) for w in m.group(1).split(";")}
    assert {300, 400, 500, 600, 700, 800} <= weights, (
        "Inter lost weights the type scale depends on: %s" % sorted(weights))


def test_dm_sans_is_not_a_fallback_either():
    """Leaving it in the stack would prefer a locally installed DM Sans over
    the system UI face - for a family the product no longer ships."""
    stack = _decl(_block(_css("tokens.css"), ":root {"), "--font-sans")
    assert stack and "DM Sans" not in stack, (
        "--font-sans still falls back to DM Sans: %r" % stack)


# -- F-33 - shared motion -------------------------------------------------

def test_shared_library_declares_no_blanket_transition():
    """``transition: all`` animates properties nobody chose, layout-affecting
    ones included. The library owned 8 of the repo's 31."""
    css = _strip_comments(_css("components.css"))
    assert not re.search(r"transition\s*:\s*all(?![-\w])", css), (
        "components.css still declares `transition: all`")


@pytest.mark.parametrize("selector", [
    ".modal", ".sheet", ".modal-backdrop", ".sheet-backdrop", ".toast",
    ".tab-panel.active",
])
def test_shared_primitives_are_covered_by_reduced_motion(selector):
    """The canonical overlays animate on open; under ``reduce`` they must not."""
    body = _reduced_motion_body(_strip_comments(_css("components.css")))
    assert re.search(re.escape(selector) + r"(?![-\w])", body), (
        "%s is not covered by the reduced-motion block" % selector)


def test_reduced_motion_keeps_functional_feedback():
    """``reduce`` must not become "disable all feedback" - colour and opacity
    still have to tell the user the control responded."""
    body = _reduced_motion_body(_strip_comments(_css("components.css")))
    assert not re.search(r"(?m)^\s*\*\s*[,{]", body), (
        "a blanket `*` rule under reduce removes functional feedback too")
    assert "scroll-behavior: auto !important" in body


# -- F-37 - selection is not action ---------------------------------------

@pytest.mark.parametrize("name,selector", [
    ("components.css", ".chip.selected"),
    ("nav.css", ".hub-lang-opt.on"),
    ("auth.css", ".lang-opt.on"),
])
def test_selection_does_not_use_the_call_to_action_fill(name, selector):
    """A selected filter outranking the screen's CTA inverts the hierarchy.

    Selection speaks in soft-primary + border + primary text (the language
    ``.chip.selected`` already used correctly); a solid primary fill means
    "act", and only the primary CTA may say that.
    """
    block = _block(_strip_comments(_css(name)), selector)
    value = _decl(block, "background")
    assert value, "%s %s declares no background" % (name, selector)
    assert value != "var(--color-primary)", (
        "%s %s is painted as a primary CTA (%s)" % (name, selector, value))
    assert "soft" in value or "rgba(var(--color-primary-rgb)" in value, (
        "%s %s must use the soft-primary selection language, got %r"
        % (name, selector, value))
    assert _decl(block, "color") == "var(--color-primary)", (
        "%s %s must keep primary as its TEXT colour, not its fill"
        % (name, selector))


# -- F-31 - one navigation identity ---------------------------------------

@pytest.mark.parametrize("prop", ["font-weight", "letter-spacing",
                                  "text-transform"])
def test_both_navigations_speak_with_one_label_voice(prop):
    """The same four destinations were rendered 12px/600/0.04em sentence case
    above 1024px and 10px/700/0.08em UPPERCASE below it - crossing one
    breakpoint changed the product's navigation identity.

    Size still differs (a stacked icon tab is not an inline header link); the
    voice must not.
    """
    css = _strip_comments(_css("nav.css"))
    header = _decl(_block(css, ".hn-link {"), prop)
    tab = _decl(_block(css, ".ab-tab {"), prop)
    assert header == tab, (
        ".hn-link %s=%r vs .ab-tab %s=%r" % (prop, header, prop, tab))
