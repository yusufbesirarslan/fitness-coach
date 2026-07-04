"""Design system regression guards (Phase 1).

tokens.css + components.css tüm sayfalara _head.html üzerinden girer; bu
testler o kabloyu ve token sözleşmesini korur (bkz. docs/design-system.md).
"""


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
        "--volt:",
        "--accent:",
        "--font-sans:",
        '[data-theme="light"]',
    ):
        assert token in css, f"tokens.css missing {token}"


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
