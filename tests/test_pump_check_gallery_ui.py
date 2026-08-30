"""Pump Check Gallery premium-UI contracts (PR2).

Structural guarantees only — the decisions that made the gallery a progress-photo
surface instead of a thumbnail grid. Deliberately NOT asserted: exact px values,
colours, shadow strings and rendered layout; those are design decisions that
should be free to move, and the browser matrix
(``scripts/frontend_audit/pump_check_pr2_matrix.py``) is what measures geometry.

Rationale for each contract: docs/PUMP_CHECK_GALLERY_UI.md.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GALLERY_CSS = ROOT / "static" / "pump_check_gallery.css"
GALLERY_HTML = ROOT / "templates" / "pump_check_gallery.html"


def _read(path):
    return path.read_text(encoding="utf-8")


def _rules_only(css):
    """CSS with comments stripped — a rationale comment naming the value it
    replaced must not satisfy (or fail) a rule assertion."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _rule(css, selector):
    """Body of the first rule whose selector matches exactly."""
    match = re.search(
        r"(?:^|\}|\*/)\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M)
    assert match, f"missing rule for {selector}"
    return match.group(1)


def _locale(name):
    return json.loads(_read(ROOT / "locales" / f"{name}.json"))


# ── Typography ────────────────────────────────────────────────────────────
# The highest-priority PR2 correction: the condensed all-caps poster title is
# the right role for PROGRESS / NUTRITION and the wrong one for a page about the
# user's body.


def test_gallery_title_takes_a_neutral_headline_role():
    rule = _rule(_read(GALLERY_CSS), ".gallery-hdr h1")
    assert "var(--font-sans)" in rule
    assert "var(--font-display)" not in rule
    assert "var(--text-display-sm)" in rule
    assert "var(--leading-headline)" in rule
    assert "var(--tracking-tight)" in rule


def test_gallery_never_reaches_for_the_condensed_display_face():
    """Including the detail heading, which used to carry it as an inline style."""
    assert "--font-display" not in _rules_only(_read(GALLERY_CSS))
    assert "font-family" not in _read(GALLERY_HTML)


def test_gallery_title_copy_is_not_shouted_in_either_locale():
    for name in ("en", "tr"):
        catalog = _locale(name)
        for key in ("gallery.h1_a", "gallery.h1_b"):
            value = catalog[key]
            assert not value.isupper(), f"{name} {key} is all caps: {value!r}"


def test_card_metadata_puts_the_date_above_the_description():
    css = _read(GALLERY_CSS)
    date = _rule(css, ".gallery-date")
    desc = _rule(css, ".gallery-desc")
    # The date is the primary metadata; before PR2 the hierarchy was inverted.
    assert "var(--text-md)" in date and "var(--color-text-1)" in date
    assert "var(--text-sm)" in desc and "var(--color-text-3)" in desc


def test_shared_headline_tokens_exist_and_are_additive():
    tokens = _read(ROOT / "static" / "tokens.css")
    assert "--leading-headline:" in tokens
    assert "--tracking-tight:" in tokens
    # Additive means no other surface consumes them yet, so neither can regress
    # an existing consumer.
    consumers = [
        path.name for path in (ROOT / "static").glob("*.css")
        if path.name not in ("tokens.css", "pump_check_gallery.css")
        and ("--leading-headline" in _read(path) or "--tracking-tight" in _read(path))
    ]
    assert consumers == [], consumers


# ── Media frame ───────────────────────────────────────────────────────────


def test_media_frame_is_a_portrait_progress_photo_not_a_square_thumbnail():
    css = _read(GALLERY_CSS)
    frame = _rule(css, ".gallery-media")
    assert "aspect-ratio: 4 / 5" in frame
    assert "1/1" not in css and "1 / 1" not in css
    assert "object-fit: cover" in _rule(css, ".gallery-media img")


def test_every_media_state_shares_the_one_frame():
    """Photo, placeholder and unavailable all reserve the same geometry, so the
    grid cannot reflow when bytes land or fail to."""
    css = _read(GALLERY_CSS)
    assert "position: relative" in _rule(css, ".gallery-media")
    for selector in (".gallery-media-fallback", ".gallery-detail-fallback"):
        rule = _rule(css, selector)
        assert "position: absolute" in rule and "inset: 0" in rule
    assert "aspect-ratio: 4 / 5" in _rule(css, ".gallery-skeleton-media")


def test_cards_wrap_their_photo_in_the_canonical_media_frame():
    html = _read(GALLERY_HTML)
    assert 'class="gallery-media"' in html
    assert "data-gallery-photo" in html


def test_gallery_photos_keep_their_deferred_loading_hints():
    html = _read(GALLERY_HTML)
    assert 'loading="lazy"' in html
    assert 'decoding="async"' in html


# ── Responsive grid ───────────────────────────────────────────────────────


def test_grid_is_a_deliberate_min_card_width_system():
    css = _read(GALLERY_CSS)
    grid = _rule(css, ".gallery-grid")
    assert "repeat(auto-fill, minmax(var(--gallery-card-min), 1fr))" in grid
    # One knob per tier rather than three grid-template rewrites.
    assert css.count("--gallery-card-min:") == 3


def test_gallery_wrapper_no_longer_caps_itself_below_the_page_container():
    """The 880px cap left a third of a 1280px viewport unused while the photos
    were 168px wide."""
    assert "880px" not in _rules_only(_read(GALLERY_CSS))


# ── States ────────────────────────────────────────────────────────────────


def test_grid_publishes_its_state():
    html = _read(GALLERY_HTML)
    assigns = re.findall(r"setAttribute\('data-state',([^)]*)\)", html)
    assert assigns, "the grid never publishes a state"
    published = " ".join(assigns)
    for state in ("loading", "populated", "empty", "unavailable"):
        assert f"'{state}'" in published, state


def test_loading_state_reserves_the_card_geometry():
    html = _read(GALLERY_HTML)
    assert "gallery-skeleton" in html
    # Two rows at the grid's real column count, not a guessed number.
    assert "gridTemplateColumns" in html
    assert "columns * 2" in html


def test_read_failure_is_not_presented_as_an_empty_gallery():
    """Same rule the Progress read models follow: absence of data and absence of
    the service are different answers."""
    html = _read(GALLERY_HTML)
    assert "gallery.unavailable" in html
    assert "gallery.retry" in html
    assert "data-retry-gallery" in html
    # The catch must not fall through to the empty branch.
    catch = html[html.index("} catch (err) {"):html.index("galleryItems = data.items")]
    assert "errorHTML()" in catch
    assert "emptyHTML()" not in catch


def test_empty_state_explains_itself_and_points_at_an_existing_route():
    html = _read(GALLERY_HTML)
    assert "gallery.empty_title" in html
    assert "gallery.empty" in html
    assert "gallery.empty_cta" in html
    assert 'href="/training"' in html


def test_unavailable_photo_keeps_controlled_semantics():
    """PR #244's contract: no native broken-image UI, accessible name intact."""
    html = _read(GALLERY_HTML)
    assert "onerror=" not in html
    assert "addEventListener('error'" in html
    assert 'role="img"' in html
    assert "gallery.photo_unavailable" in html


def test_gallery_state_blocks_span_the_grid():
    assert "grid-column: 1 / -1" in _rule(_read(GALLERY_CSS), ".gallery-state")


# ── Interaction and accessibility ─────────────────────────────────────────


def test_cards_stay_real_buttons_with_a_visible_focus_ring():
    html, css = _read(GALLERY_HTML), _read(GALLERY_CSS)
    assert 'type="button" class="gallery-item"' in html
    focus = _rule(css, ".gallery-item:focus-visible")
    assert "outline:" in focus
    # components.css rounds focused buttons to --radius-sm; a 16px card must not
    # change shape when the keyboard reaches it.
    assert "border-radius: var(--radius-lg)" in focus


def test_detail_manages_focus_and_can_be_closed_accessibly():
    html = _read(GALLERY_HTML)
    assert "data-close-gallery" in html
    assert "aria-label=" in html
    assert "close.focus()" in html
    assert "lastFocused" in html
    assert "e.key === 'Escape'" in html
    assert "e.key !== 'Tab'" in html


def test_detail_heading_is_an_h2_with_the_page_headline_role():
    html, css = _read(GALLERY_HTML), _read(GALLERY_CSS)
    assert '<h2 id="gallery-modal-title" class="gallery-detail-title">' in html
    title = _rule(css, ".gallery-detail-title")
    assert "var(--font-sans)" in title
    assert "var(--tracking-tight)" in title


def test_detail_gives_the_photo_the_wider_column_on_desktop():
    css = _read(GALLERY_CSS)
    assert "grid-template-columns: minmax(0, 1.3fr) minmax(320px, 0.7fr)" in css
    # A definite height plus a shrinkable row: with `min-height` the row sized to
    # the facts column and the panel clipped the photo and the buttons.
    assert "height: min(72vh, 660px)" in css
    assert "grid-template-rows: minmax(0, 1fr)" in css


def test_modal_sits_on_the_canonical_overlay_layer():
    """A raw 1000 put the dialog above the toast layer."""
    modal = _rule(_read(GALLERY_CSS), ".gallery-modal")
    assert "z-index: var(--z-overlay)" in modal


def test_page_motion_respects_reduced_motion():
    css = _read(GALLERY_CSS)
    block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".gallery-skeleton-media" in block
    assert "animation: none" in block
    assert "transform: none" in block


# ── Design-system discipline ──────────────────────────────────────────────


def test_gallery_css_uses_tokens_not_raw_values():
    css = _read(GALLERY_CSS)
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    hexes = re.findall(r"#[0-9A-Fa-f]{3,8}\b", body)
    assert hexes == [], hexes


def test_gallery_classes_stay_scoped_to_this_page():
    """Nothing in this file can reach another surface."""
    leaked = []
    for path in list((ROOT / "templates").glob("*.html")) + \
            list((ROOT / "static").glob("*.css")) + \
            list((ROOT / "static").glob("*.js")):
        if path.name.startswith("pump_check_gallery"):
            continue
        if re.search(r"\bgallery-(item|media|grid|wrap|detail|meta|date|desc"
                     r"|label|value|actions|modal|skeleton|hdr|state)\b",
                     _read(path)):
            leaked.append(path.name)
    assert leaked == [], leaked


# ── i18n ──────────────────────────────────────────────────────────────────


def test_gallery_copy_is_mirrored_and_non_empty_in_both_locales():
    en, tr = _locale("en"), _locale("tr")
    keys = {key for key in en if key.startswith("gallery.")}
    assert keys == {key for key in tr if key.startswith("gallery.")}
    for key in ("gallery.empty_title", "gallery.empty_cta",
                "gallery.unavailable", "gallery.retry"):
        assert key in keys
        assert en[key].strip() and tr[key].strip()


def test_gallery_page_hardcodes_no_english(client, auth_user):
    html = client.get("/pump-check-gallery").get_data(as_text=True)
    for literal in ("No Pump Checks", "Go to Training", "Try again",
                    "Photo unavailable"):
        assert literal not in html, literal


# ── Media contract (PR #244) is untouched ─────────────────────────────────


def test_gallery_page_does_not_reopen_the_media_contract():
    html = _read(GALLERY_HTML)
    for forbidden in ("amazonaws", "X-Amz", "image_key", "s3_helper", "bucket"):
        assert forbidden not in html, forbidden
