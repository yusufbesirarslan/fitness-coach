"""Design system regression guards (Phase 1).

tokens.css + components.css tüm sayfalara _head.html üzerinden girer; bu
testler o kabloyu ve token sözleşmesini korur (bkz. docs/design-system.md).
"""
import re


def test_head_serves_design_system_assets(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "/static/tokens.css" in html
    assert "/static/components.css" in html
    assert "family=Inter" in html


def test_tokens_css_defines_canonical_and_legacy_tokens(client):
    resp = client.get("/static/tokens.css")
    assert resp.status_code == 200
    css = resp.get_data(as_text=True)
    for token in (
        "--color-primary:",
        "--space-2:",
        "--radius-md:",
        "--accent:",
        "--font-sans:",
        '[data-theme="light"]',
    ):
        assert token in css, f"tokens.css missing {token}"
    # --volt* were legacy aliases for --color-primary*; retired app-wide
    # (Phase 5 Final QA Task 2) — they must no longer be defined.
    assert "--volt:" not in css


def test_components_css_defines_spec_components(client):
    resp = client.get("/static/components.css")
    assert resp.status_code == 200
    css = resp.get_data(as_text=True)
    for sel in (
        ".btn-volt", ".fc-input", ".card", ".modal", ".sheet",
        ".badge", ".chip", ".avatar", ".ring-svg", ".pbar-track",
        ".tab-btn", ".quick-add-btn", ".empty-state", ".skeleton",
        ".stat-card", ".sec-label",
    ):
        assert sel in css, f"components.css missing {sel}"


# ── Contrast contract (PR3) ──────────────────────────────────────────────
# Metadata labels (--color-text-3) sit on cards (--color-surface-2) across
# Home / Training / Progress. WCAG AA for normal text is 4.5:1; the previous
# #808080 / #1E1E1E pairing measured 4.22:1. Fix the token, not selectors.

_AA_NORMAL = 4.5
_HEX = r"#([0-9A-Fa-f]{6})"


def _decl_block(css: str, header: str) -> str:
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
    raise AssertionError(f"unclosed block for {header}")


def _token_map(block: str) -> dict[str, str]:
    found = {}
    for match in re.finditer(
        r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block, re.I
    ):
        found[match.group(1)] = match.group(2).strip()
    return found


def _resolve(tokens: dict[str, str], name: str) -> str:
    value = tokens[name]
    seen = set()
    while value.startswith("var("):
        inner = value[4:value.index(")")].split(",")[0].strip()
        assert inner not in seen, f"cycle resolving {name}"
        seen.add(inner)
        value = tokens[inner]
    match = re.fullmatch(_HEX, value, re.I)
    assert match, f"{name} resolved to non-hex {value!r}"
    return "#" + match.group(1).upper()


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
    lighter, darker = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _theme_tokens(client, header: str) -> dict[str, str]:
    css = client.get("/static/tokens.css").get_data(as_text=True)
    return _token_map(_decl_block(css, header))


def test_muted_text_meets_aa_on_semantic_surfaces(client):
    """--color-text-3 must be readable as normal text on the surfaces it labels.

    Dark is the shipped theme; light is the prepared counterpart. Hierarchy is
    preserved by requiring secondary text to stay more contrasting than muted
    text on the same card surface.
    """
    dark = _theme_tokens(client, ":root {")
    light = _theme_tokens(client, '[data-theme="light"] {')

    dark_text3 = _resolve(dark, "--color-text-3")
    dark_text2 = _resolve(dark, "--color-text-2")
    for surface in ("--color-bg", "--color-surface-1", "--color-surface-2",
                    "--color-surface-3"):
        bg = _resolve(dark, surface)
        ratio = _contrast(dark_text3, bg)
        assert ratio >= _AA_NORMAL, (
            f"dark --color-text-3 {dark_text3} on {surface} {bg} is {ratio:.2f}:1"
        )
    assert _contrast(dark_text2, _resolve(dark, "--color-surface-2")) > _contrast(
        dark_text3, _resolve(dark, "--color-surface-2")
    ), "muted text must stay below secondary text on cards"

    light_text3 = _resolve(light, "--color-text-3")
    light_text2 = _resolve(light, "--color-text-2")
    # Light --color-surface-3 (#E5E4DF) cannot take a distinct muted token to
    # 4.5:1 without collapsing into --color-text-2. Unshipped theme; consumers
    # such as .lso-sub on .log-sheet-opt still sit on that raised fill. The
    # labels that drove this debt sit on surface-2 cards, the page bg, and
    # surface-1.
    for surface in ("--color-bg", "--color-surface-1", "--color-surface-2"):
        bg = _resolve(light, surface)
        ratio = _contrast(light_text3, bg)
        assert ratio >= _AA_NORMAL, (
            f"light --color-text-3 {light_text3} on {surface} {bg} is {ratio:.2f}:1"
        )
    assert _contrast(light_text2, _resolve(light, "--color-surface-2")) > _contrast(
        light_text3, _resolve(light, "--color-surface-2")
    ), "light muted text must stay below secondary text on cards"
