"""Pump Check Gallery PR2 — premium-UI browser verification matrix.

Reuses the Sprint-0 hermetic audit harness (``create_audit_app`` configures the
hermetic environment before any ``app.*`` import) to run the EXACT PR2 matrix:
every required gallery state x every required viewport x both locales, plus a
loading sub-matrix and a reduced-motion sub-matrix.

States:

  populated      a full multi-row gallery of real photos
  unavailable    rows the server resolved to no media (imageUrl null)
  broken_media   rows whose media URL 404s, so the client swaps the fallback in
  empty          no Pump Checks at all
  modal          populated, first card opened into the detail view
  loading        /data stalled, so the placeholder grid is what gets measured

Media is hermetic: ``s3_helper.generate_presigned_url`` is replaced with a
same-origin ``/static/audit-pump/<n>.jpg`` path (the deployed CSP's
``img-src 'self'`` already allows it, so the browser really issues the request)
and Playwright fulfils that route with Pillow-encoded JPEGs in three upload
shapes — portrait, square and landscape — so the card frame is measured against
the aspect ratios a phone camera actually produces. ``broken_media`` simply
leaves the route unregistered so the server answers 404.

Run (WSL, Sprint-0 venv + browsers):
  PLAYWRIGHT_BROWSERS_PATH=<cache> \
    python -m scripts.frontend_audit.pump_check_pr2_matrix \
      --output docs/frontend-readiness/pump-check-pr2
"""
from __future__ import annotations

import argparse
import io
import json
from datetime import datetime, timedelta
from pathlib import Path

from .app import ROOT, create_audit_app
from .runner import AuditServer, browser_clock_script
from .seed import seed_all


VIEWPORTS = {
    "320": {"width": 320, "height": 780},
    "390": {"width": 390, "height": 844},
    "768": {"width": 768, "height": 1024},
    "1280": {"width": 1280, "height": 800},
    "1440": {"width": 1440, "height": 900},
}

LOCALES = ("en", "tr")

AUDIT_DAY = datetime(2026, 7, 20, 12)

STATE_SCENARIO = {
    "populated": "completed-workout",
    "unavailable": "progress-history",
    "broken_media": "coach-history",
    "empty": "social-empty",
    "modal": "active-workout",
    "loading": "wearable-connected",
}

# Nine cards is deliberately more than one row at every viewport in the matrix
# (2 columns at 320/390, 3 at 768, 4 at 1280/1440), so "multiple rows" is
# actually exercised rather than assumed.
POPULATED_ROWS = 9
UNAVAILABLE_ROWS = 6
BROKEN_ROWS = 6

# Upload shapes a phone camera actually produces. The card frame must be
# identical for all three; only the crop may differ.
MEDIA_SHAPES = ((1080, 1440), (1200, 1200), (1600, 1200))

AUDIT_MEDIA_PREFIX = "/static/audit-pump/"

# Long enough to wrap at 320px in both locales — narrow-width metadata clipping
# is one of the things this matrix exists to catch.
DESCRIPTIONS = (
    "Chest and triceps, felt strong today",
    "Göğüs ve arka kol, bugün form çok iyiydi",
    "Leg day",
    "Pull day — back and biceps supersets",
    "Omuz ve karın çalışması tamamlandı",
    "Full body",
    "Push day, new bench PR",
    "Bacak günü, ağırlıklar arttı",
    "Cardio and core finisher",
)


SETTLED_JS = r"""
() => {
  const grid = document.getElementById('gallery-grid');
  if (!grid) return false;
  const state = grid.getAttribute('data-state');
  if (!state || state === 'loading') return false;
  return [...grid.querySelectorAll('img')].every((img) => img.complete);
}
"""

LOADING_JS = r"""
() => {
  const grid = document.getElementById('gallery-grid');
  // The placeholders, not just the attribute: `data-state="loading"` is the
  // server-rendered initial value, so waiting on it alone would settle before
  // the page had run any script at all.
  return !!(grid && grid.getAttribute('data-state') === 'loading'
            && grid.querySelector('.gallery-skeleton'));
}
"""

# A never-resolving `/pump-check-gallery/data` fetch, installed in the page
# rather than as a Playwright route. The obvious way to stall a request is a
# route handler that waits, but that handler holds Playwright's dispatcher and
# raises TargetClosedError if it is still in flight when the context closes,
# which takes the rest of the run down with it. csrf.js wraps `window.fetch`
# after this init script runs, so its wrapper simply calls this stub.
STALL_GALLERY_DATA_JS = r"""
(() => {
  const original = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.indexOf('/pump-check-gallery/data') !== -1) {
      return new Promise(() => {});
    }
    return original.apply(window, arguments);
  };
})();
"""

MEASURE_JS = r"""
() => {
  const de = document.documentElement;
  const q = (s) => [...document.querySelectorAll(s)];
  const text = (el) => (el && el.textContent ? el.textContent.trim() : '');
  const round = (n) => Math.round(n * 10) / 10;
  const rect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {w: round(r.width), h: round(r.height)};
  };

  const grid = document.getElementById('gallery-grid');
  const gridCS = grid ? getComputedStyle(grid) : null;
  const columns = gridCS
    ? gridCS.gridTemplateColumns.split(' ').filter((v) => v && v !== 'none').length
    : 0;

  const cards = q('.gallery-item');
  const first = cards[0] || null;
  const firstMedia = first
    ? first.querySelector('.gallery-media, img, .gallery-media-fallback')
    : null;
  const frameSizes = [...new Set(q('.gallery-media').map((el) => {
    const r = el.getBoundingClientRect();
    return Math.round(r.width) + 'x' + Math.round(r.height);
  }))];

  const imgs = q('.gallery-item img');
  const brokenImages = imgs.filter(
    (img) => img.complete && img.naturalWidth === 0).length;

  const h1 = document.querySelector('.page-hdr h1');
  const h1CS = h1 ? getComputedStyle(h1) : null;
  const h1Rect = h1 ? h1.getBoundingClientRect() : null;
  const h1Lines = (h1CS && h1Rect && parseFloat(h1CS.lineHeight))
    ? Math.round(h1Rect.height / parseFloat(h1CS.lineHeight)) : null;
  const sub = document.querySelector('.page-hdr p');
  const subCS = sub ? getComputedStyle(sub) : null;

  const meta = document.querySelector('.gallery-date');
  const metaCS = meta ? getComputedStyle(meta) : null;
  const desc = document.querySelector('.gallery-desc');
  const descCS = desc ? getComputedStyle(desc) : null;

  const modal = document.getElementById('gallery-modal');
  const detail = document.getElementById('gallery-detail');
  const detailMedia = detail
    ? detail.querySelector('img, .gallery-detail-fallback') : null;
  const detailTitle = document.getElementById('gallery-modal-title');
  const detailTitleCS = detailTitle ? getComputedStyle(detailTitle) : null;

  const empty = document.querySelector('.empty-state');
  const emptyCta = empty ? empty.querySelector('a, button') : null;

  const truncates = (el) => {
    const cs = getComputedStyle(el);
    return cs.textOverflow === 'ellipsis'
        || (cs.webkitLineClamp && cs.webkitLineClamp !== 'none');
  };
  const clipped = q('.gallery-item *')
    .filter((el) => !truncates(el))
    .filter((el) => el.scrollWidth > el.clientWidth + 1
                 || el.scrollHeight > el.clientHeight + 1)
    .map((el) => el.className || el.tagName).slice(0, 10);
  const descClamp = desc ? getComputedStyle(desc).webkitLineClamp : null;

  const bodyText = document.body ? document.body.innerText : '';

  return {
    doc_scroll_width: de.scrollWidth,
    doc_client_width: de.clientWidth,
    doc_horizontal_overflow: de.scrollWidth > de.clientWidth + 1,

    grid_present: !!grid,
    grid_state: grid ? grid.getAttribute('data-state') : null,
    grid_columns: columns,
    grid_gap: gridCS ? gridCS.columnGap : null,
    grid_width: grid ? round(grid.getBoundingClientRect().width) : null,

    card_count: cards.length,
    card_tag: first ? first.tagName : null,
    card_rect: rect(first),
    media_rect: rect(firstMedia),
    media_aspect: (() => {
      const r = rect(firstMedia);
      return (r && r.h) ? round(r.w / r.h) : null;
    })(),
    media_frame_sizes: frameSizes,
    media_object_fit: (() => {
      const img = document.querySelector('.gallery-item img');
      return img ? getComputedStyle(img).objectFit : null;
    })(),

    img_count: imgs.length,
    broken_images: brokenImages,
    lazy_images: imgs.filter((i) => i.getAttribute('loading') === 'lazy').length,
    async_images: imgs.filter((i) => i.getAttribute('decoding') === 'async').length,
    fallback_count: q('.gallery-media-fallback').length,
    fallback_role: (() => {
      const el = document.querySelector('.gallery-media-fallback');
      return el ? el.getAttribute('role') : null;
    })(),
    fallback_label: (() => {
      const el = document.querySelector('.gallery-media-fallback');
      return el ? el.getAttribute('aria-label') : null;
    })(),
    fallback_rect: rect(document.querySelector('.gallery-media-fallback')),
    skeleton_count: q('.gallery-skeleton').length,
    skeleton_rect: rect(document.querySelector('.gallery-skeleton')),

    h1_text: text(h1),
    h1_font_family: h1CS ? h1CS.fontFamily : null,
    h1_font_size: h1CS ? round(parseFloat(h1CS.fontSize)) : null,
    h1_font_weight: h1CS ? h1CS.fontWeight : null,
    h1_letter_spacing: h1CS ? h1CS.letterSpacing : null,
    h1_line_height: h1CS ? round(parseFloat(h1CS.lineHeight)) : null,
    h1_transform: h1CS ? h1CS.textTransform : null,
    h1_lines: h1Lines,
    h1_rect: rect(h1),
    sub_text: text(sub),
    sub_font_size: subCS ? round(parseFloat(subCS.fontSize)) : null,
    sub_color: subCS ? subCS.color : null,
    meta_font_size: metaCS ? round(parseFloat(metaCS.fontSize)) : null,
    meta_color: metaCS ? metaCS.color : null,
    desc_font_size: descCS ? round(parseFloat(descCS.fontSize)) : null,

    modal_open: !!(modal && modal.classList.contains('show')),
    detail_rect: rect(detail),
    detail_media_rect: rect(detailMedia),
    detail_media_share: (() => {
      const d = rect(detail); const m = rect(detailMedia);
      return (d && m && d.h) ? round(m.h / d.h) : null;
    })(),
    detail_title_font_family: detailTitleCS ? detailTitleCS.fontFamily : null,
    detail_title_letter_spacing: detailTitleCS ? detailTitleCS.letterSpacing : null,
    detail_title_tag: detailTitle ? detailTitle.tagName : null,
    detail_overflow: detail ? detail.scrollHeight > detail.clientHeight + 1 : null,
    detail_in_viewport: (() => {
      if (!detail) return null;
      const r = detail.getBoundingClientRect();
      return r.top >= -1 && r.bottom <= de.clientHeight + 1;
    })(),
    modal_close_label: (() => {
      const el = document.querySelector('[data-close-gallery]');
      return el ? (el.getAttribute('aria-label') || text(el)) : null;
    })(),

    empty_present: !!empty,
    empty_title: text(document.querySelector('.empty-title')),
    empty_desc: text(document.querySelector('.empty-desc')),
    empty_cta_href: emptyCta ? emptyCta.getAttribute('href') : null,
    empty_cta_text: text(emptyCta),

    clipped_children: clipped,
    desc_line_clamp: descClamp,
    raw_key_leak: [...new Set(
      bodyText.match(/\b(?:gallery|feed|common|training)\.[a-z_]+/g) || [])].slice(0, 10),
    html_lang: de.getAttribute('lang'),
  };
}
"""

FOCUS_JS = r"""
() => {
  const card = document.querySelector('.gallery-item');
  if (!card) return {present: false};
  card.focus();
  const cs = getComputedStyle(card);
  const r = card.getBoundingClientRect();
  return {
    present: true,
    tag: card.tagName,
    focusable: document.activeElement === card,
    outline: cs.outlineStyle + ' ' + cs.outlineWidth,
    box_shadow: cs.boxShadow,
    touch_w: Math.round(r.width),
    touch_h: Math.round(r.height),
  };
}
"""

MODAL_FOCUS_JS = r"""
() => {
  const detail = document.getElementById('gallery-detail');
  const active = document.activeElement;
  return {
    focus_inside_detail: !!(detail && active && detail.contains(active)),
    active_tag: active ? active.tagName : null,
    active_label: active ? (active.getAttribute('aria-label')
                            || (active.textContent || '').trim()) : null,
    body_overflow: getComputedStyle(document.body).overflow,
  };
}
"""

REDUCED_MOTION_JS = r"""
() => {
  const names = (el) => {
    const cs = getComputedStyle(el);
    return {animation: cs.animationName, transition: cs.transitionDuration};
  };
  const skeleton = document.querySelector('.gallery-skeleton-media');
  const card = document.querySelector('.gallery-media img');
  return {
    reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
    skeleton: skeleton ? names(skeleton) : null,
    card: card ? names(card) : null,
  };
}
"""


# The intended responsive contract, measured rather than assumed. Photo
# visibility is the objective, so the minimum card width is asserted too — a
# grid that fits more columns by shrinking the photo is a regression here.
EXPECTED_COLUMNS = {"320": 2, "390": 2, "768": 3, "1280": 4, "1440": 4}
MIN_CARD_WIDTH = {"320": 125, "390": 160, "768": 210, "1280": 270, "1440": 270}

# 4:5 portrait frame (docs/PUMP_CHECK_GALLERY_UI.md). The tolerance covers
# sub-pixel grid-track rounding, not a different ratio.
TARGET_MEDIA_ASPECT = 0.8
MEDIA_ASPECT_TOLERANCE = 0.03

# Cells with no card to focus; the focus assertions do not apply to them.
_NO_CARD_STATES = ("empty", "loading")
_SKIPPED_FOCUS = {
    "present": True, "focusable": True, "outline": "solid 2px",
    "box_shadow": "none", "touch_w": 0, "touch_h": 0,
}


def _jpeg(width: int, height: int, seed: int) -> bytes:
    from PIL import Image

    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        row = (y * 255 // height + seed * 47) % 256
        for x in range(width):
            pixels[x, y] = ((x * 255 // width + seed * 23) % 256, row, (seed * 91) % 256)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


def _media_bodies() -> dict:
    return {
        f"{index}.jpg": _jpeg(width, height, index)
        for index, (width, height) in enumerate(MEDIA_SHAPES)
    }


def seed_states(app) -> dict:
    from app.extensions import db
    from app.models import PumpCheck, User

    counts = {}
    with app.app_context():
        for state, scenario in STATE_SCENARIO.items():
            user = User.query.filter_by(username=f"audit-{scenario}").first()
            assert user is not None, scenario
            PumpCheck.query.filter_by(user_id=user.id).delete()
            db.session.flush()
            if state == "empty":
                counts[state] = 0
                continue
            if state == "unavailable":
                total, keyed = UNAVAILABLE_ROWS, False
            elif state == "broken_media":
                total, keyed = BROKEN_ROWS, True
            else:
                total, keyed = POPULATED_ROWS, True
            for index in range(total):
                db.session.add(PumpCheck(
                    user_id=user.id,
                    image_key=(
                        "pump-checks/%s/2026/07/%s.jpg"
                        % (user.id, index % len(MEDIA_SHAPES)) if keyed else None),
                    location_type="Spor Salonu" if index % 2 else "Ev",
                    description=DESCRIPTIONS[index % len(DESCRIPTIONS)],
                    workout_score=7.0 + (index % 4) * 0.5,
                    visibility=("feed", "friends", "private")[index % 3],
                    shared_friend_ids=[],
                    reposts_count=index % 3,
                    valid=True,
                    # date_key stays NULL: the legacy (user_id, date_key) unique
                    # constraint is the one-per-day workout-completion guard, and
                    # these are gallery fixtures, not completions.
                    date_key=None,
                    created_at=AUDIT_DAY - timedelta(days=index * 3),
                ))
            counts[state] = total
        db.session.commit()
    return counts


def _set_user_language(app, scenario, locale):
    from app.extensions import db
    from app.models import User

    with app.app_context():
        user = User.query.filter_by(username=f"audit-{scenario}").first()
        user.language = locale
        db.session.commit()


def _cells(only=None):
    cells = []
    for state in ("populated", "unavailable", "broken_media", "empty", "modal"):
        for viewport in VIEWPORTS:
            for locale in LOCALES:
                cells.append({
                    "state": state, "viewport": viewport, "locale": locale,
                    "scenario": STATE_SCENARIO[state],
                    "shot": viewport in ("390", "1280"),
                })
    for viewport in ("390", "1280"):
        for locale in LOCALES:
            cells.append({
                "state": "loading", "viewport": viewport, "locale": locale,
                "scenario": STATE_SCENARIO["loading"], "shot": True,
            })
    if only:
        wanted = set(only)
        cells = [
            cell for cell in cells
            if cell["state"] in wanted
            or ("%s:%s" % (cell["state"], cell["viewport"])) in wanted
            or ("%s:%s:%s" % (cell["state"], cell["viewport"], cell["locale"])) in wanted
        ]
    return cells


def _cell_id(cell):
    return "pump-gallery__%s__%s__%s" % (cell["state"], cell["viewport"], cell["locale"])


def _has_visible_focus(focus):
    if not focus.get("focusable"):
        return False
    outline = focus.get("outline") or ""
    shadow = focus.get("box_shadow") or ""
    if outline and not outline.startswith("none") and "0px" not in outline.split():
        return True
    return shadow not in ("", "none")


def _evaluate_typography(m, viewport, reasons):
    """The highest-priority PR2 correction: no condensed poster treatment."""
    if "Bebas" in (m["h1_font_family"] or ""):
        reasons.append(
            "page title still uses the condensed display face (%s)" % m["h1_font_family"])
    if m["h1_transform"] == "uppercase":
        reasons.append("page title is CSS-uppercased")
    if (m["h1_text"] or "").isupper():
        reasons.append("page title copy is all caps (%r)" % m["h1_text"])
    if m["h1_font_size"] is not None and not 26 <= m["h1_font_size"] <= 40:
        reasons.append("page title font-size=%s (want 26-40)" % m["h1_font_size"])
    if m["h1_font_weight"] and not 600 <= int(m["h1_font_weight"]) <= 750:
        reasons.append("page title weight=%s (want 600-750)" % m["h1_font_weight"])
    spacing = m["h1_letter_spacing"] or "normal"
    if spacing != "normal" and float(spacing.replace("px", "")) > 0:
        reasons.append("page title uses positive tracking (%s)" % spacing)
    if m["h1_lines"] and m["h1_lines"] > (2 if viewport == "320" else 1):
        reasons.append(
            "page title wraps to %s lines at %spx" % (m["h1_lines"], viewport))
    if not m["sub_text"]:
        reasons.append("supporting sentence missing")
    if m["sub_font_size"] and not 14 <= m["sub_font_size"] <= 16:
        reasons.append("supporting copy font-size=%s (want 14-16)" % m["sub_font_size"])
    if m["meta_font_size"] and not 12 <= m["meta_font_size"] <= 14:
        reasons.append("metadata font-size=%s (want 12-14)" % m["meta_font_size"])


def _evaluate(cell, m, focus, modal_focus=None):
    reasons = []
    viewport, state = cell["viewport"], cell["state"]

    if m["doc_horizontal_overflow"]:
        reasons.append("document horizontal overflow (%s > %s)"
                       % (m["doc_scroll_width"], m["doc_client_width"]))
    if m["raw_key_leak"]:
        reasons.append("raw localization keys leaked: %s" % m["raw_key_leak"])
    if m["html_lang"] != cell["locale"]:
        reasons.append("html lang=%s (want %s)" % (m["html_lang"], cell["locale"]))
    _evaluate_typography(m, viewport, reasons)

    if state == "empty":
        if m["card_count"]:
            reasons.append("empty state rendered %s cards" % m["card_count"])
        if not m["empty_present"]:
            reasons.append("empty state missing")
        if not m["empty_title"]:
            reasons.append("empty state has no title")
        if not m["empty_desc"]:
            reasons.append("empty state has no explanation")
        if m["empty_cta_href"] != "/training":
            reasons.append("empty state CTA href=%s (want /training)" % m["empty_cta_href"])
        if not m["empty_cta_text"]:
            reasons.append("empty state CTA has no label")
        return ("pass" if not reasons else "fail"), reasons

    if state == "loading":
        if m["grid_state"] != "loading":
            reasons.append("grid state=%s (want loading)" % m["grid_state"])
        if m["card_count"]:
            reasons.append("loading state rendered %s real cards" % m["card_count"])
        want_skeletons = EXPECTED_COLUMNS[viewport] * 2
        if m["skeleton_count"] != want_skeletons:
            reasons.append("skeleton_count=%s (want %s: two reserved rows)"
                           % (m["skeleton_count"], want_skeletons))
        skeleton = m["skeleton_rect"]
        if not skeleton or not skeleton["h"]:
            reasons.append("loading placeholder reserves no height")
        elif abs(skeleton["w"] / skeleton["h"] - TARGET_MEDIA_ASPECT) > 0.35:
            reasons.append(
                "loading placeholder geometry %s does not reserve the media frame"
                % skeleton)
        return ("pass" if not reasons else "fail"), reasons

    want_cards = {
        "populated": POPULATED_ROWS, "modal": POPULATED_ROWS,
        "unavailable": UNAVAILABLE_ROWS, "broken_media": BROKEN_ROWS,
    }[state]
    if m["card_count"] != want_cards:
        reasons.append("card_count=%s (want %s)" % (m["card_count"], want_cards))

    want_columns = EXPECTED_COLUMNS[viewport]
    if m["grid_columns"] != want_columns:
        reasons.append("grid columns=%s (want %s at %spx)"
                       % (m["grid_columns"], want_columns, viewport))
    card = m["card_rect"] or {}
    if card.get("w") and card["w"] < MIN_CARD_WIDTH[viewport]:
        reasons.append("card width=%s below the %spx premium floor at %spx"
                       % (card["w"], MIN_CARD_WIDTH[viewport], viewport))

    aspect = m["media_aspect"]
    if aspect is None:
        reasons.append("media frame has no measurable geometry")
    elif abs(aspect - TARGET_MEDIA_ASPECT) > MEDIA_ASPECT_TOLERANCE:
        reasons.append("media aspect=%s (want %s)" % (aspect, TARGET_MEDIA_ASPECT))
    if len(m["media_frame_sizes"]) > 1:
        reasons.append("media frames are not one size: %s" % m["media_frame_sizes"])

    if m["broken_images"]:
        reasons.append("%s native broken-image icons" % m["broken_images"])
    if m["clipped_children"]:
        reasons.append("clipped card content: %s" % m["clipped_children"])

    if state in ("unavailable", "broken_media"):
        if m["fallback_count"] != want_cards:
            reasons.append("fallback_count=%s (want %s)"
                           % (m["fallback_count"], want_cards))
        if m["fallback_role"] != "img":
            reasons.append("fallback role=%s (want img)" % m["fallback_role"])
        if not m["fallback_label"]:
            reasons.append("fallback has no accessible name")
        fallback, media = m["fallback_rect"], m["media_rect"]
        if fallback and media and abs(fallback["h"] - media["h"]) > 1:
            reasons.append("fallback geometry %s differs from the media frame %s"
                           % (fallback, media))
    else:
        if m["img_count"] != want_cards:
            reasons.append("img_count=%s (want %s)" % (m["img_count"], want_cards))
        if m["lazy_images"] != m["img_count"]:
            reasons.append('loading="lazy" lost on some gallery photos')
        if m["async_images"] != m["img_count"]:
            reasons.append('decoding="async" lost on some gallery photos')
        if m["media_object_fit"] != "cover":
            reasons.append("photo object-fit=%s (want cover)" % m["media_object_fit"])

    if m["card_tag"] != "BUTTON":
        reasons.append("card is a %s (want BUTTON)" % m["card_tag"])
    if not focus.get("present"):
        reasons.append("no focusable card")
    elif not focus.get("focusable"):
        reasons.append("card is not keyboard focusable")
    elif not _has_visible_focus(focus):
        reasons.append("card focus is not visible (%s)" % focus)
    if focus.get("touch_h") and focus["touch_h"] < 44:
        reasons.append("card touch target %sx%s under 44px"
                       % (focus["touch_w"], focus["touch_h"]))

    if state == "modal":
        if not m["modal_open"]:
            reasons.append("detail view did not open")
        if "Bebas" in (m["detail_title_font_family"] or ""):
            reasons.append("detail heading still uses the condensed display face")
        letter = m["detail_title_letter_spacing"] or "normal"
        if letter != "normal" and float(letter.replace("px", "")) > 0.5:
            reasons.append("detail heading uses poster tracking (%s)" % letter)
        if m["detail_title_tag"] != "H2":
            reasons.append("detail heading is %s (want H2)" % m["detail_title_tag"])
        if not m["detail_in_viewport"]:
            reasons.append("detail panel does not fit the viewport")
        # `overflow: hidden` on the panel means content taller than it is silently
        # cut off rather than scrolled — the photo and the action row with it.
        if m["detail_overflow"]:
            reasons.append("detail panel clips its own content (scrollHeight > clientHeight)")
        share = m["detail_media_share"]
        if share is None:
            reasons.append("detail media has no measurable geometry")
        elif share > 1.0:
            reasons.append(
                "detail media is taller than the panel (share=%s): it is being clipped"
                % share)
        elif viewport in ("1280", "1440") and share < 0.5:
            reasons.append("detail media occupies only %s of the panel on desktop" % share)
        if not m["modal_close_label"]:
            reasons.append("detail close control has no accessible name")
        if modal_focus:
            if not modal_focus.get("focus_inside_detail"):
                reasons.append("focus stayed outside the open detail (%s)"
                               % modal_focus.get("active_tag"))
            if modal_focus.get("body_overflow") != "hidden":
                reasons.append("background scroll not locked while the detail is open")

    return ("pass" if not reasons else "fail"), reasons


_THIRD_PARTY_CONSOLE_HOSTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "www.google.com/g/collect",
    "clarity.ms",
)


def _is_third_party_console_noise(message: str) -> bool:
    return any(host in message for host in _THIRD_PARTY_CONSOLE_HOSTS)


def _make_media_route(media):
    def _serve(route):
        body = media.get(route.request.url.rsplit("/", 1)[-1])
        if body is None:
            route.fulfill(status=404, body="")
        else:
            route.fulfill(status=200, content_type="image/jpeg", body=body)
    return _serve


def run(output_dir: Path, only=None) -> dict:
    from playwright.sync_api import TimeoutError as PWTimeoutError
    from playwright.sync_api import sync_playwright

    import s3_helper

    output_dir = Path(output_dir)
    shots_dir = output_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    db_path = ROOT / "artifacts" / "ui-audit" / "pump_check_pr2_matrix.db"
    app = create_audit_app(db_path)
    seed_summary = seed_all(app)
    seed_summary["gallery_rows"] = seed_states(app)

    # Hermetic media: a same-origin path the deployed CSP's `img-src 'self'`
    # already allows, so the browser really requests it and Playwright can fulfil
    # it with bytes. No S3 call, no network, no signing behaviour touched.
    def _audit_presigned_url(key, expires_in=3600, expected_user_id=None):
        return AUDIT_MEDIA_PREFIX + str(key).rsplit("/", 1)[-1]

    s3_helper.generate_presigned_url = _audit_presigned_url
    media = _media_bodies()

    cells = _cells(only)
    results = []
    with AuditServer(app) as server, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        clocks = app.extensions["frontend_audit"]["scenario_clocks"]
        try:
            for cell in cells:
                results.append(
                    _run_cell(browser, server, app, clocks, media, cell, shots_dir,
                              PWTimeoutError))
            results.extend(_run_reduced_motion(
                browser, server, app, clocks, media, shots_dir, PWTimeoutError))
        finally:
            browser.close()

    passed = sum(1 for r in results if r["verdict"] == "pass")
    failed = [r["id"] for r in results if r["verdict"] == "fail"]
    blocked = [r["id"] for r in results if r["verdict"] == "blocked"]
    manifest = {
        "schema_version": "1.0.0",
        "pr": "pump-check-gallery-premium-ui-pr2",
        "engine": "chromium",
        "hermetic": True,
        "seed_summary": seed_summary,
        "viewports": VIEWPORTS,
        "locales": list(LOCALES),
        "expected_columns": EXPECTED_COLUMNS,
        "min_card_width": MIN_CARD_WIDTH,
        "target_media_aspect": TARGET_MEDIA_ASPECT,
        "totals": {
            "cells": len(results), "passed": passed,
            "failed": len(failed), "blocked": len(blocked),
        },
        "failed_ids": failed,
        "blocked_ids": blocked,
        "cells": results,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "validation-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _run_cell(browser, server, app, clocks, media, cell, shots_dir, PWTimeoutError):
    _set_user_language(app, cell["scenario"], cell["locale"])
    dims = VIEWPORTS[cell["viewport"]]
    context = browser.new_context(viewport=dict(dims))
    context.add_init_script(
        browser_clock_script(clocks[cell["scenario"]]["fixed_current_datetime"]))
    if cell["state"] == "loading":
        context.add_init_script(STALL_GALLERY_DATA_JS)
    page = context.new_page()
    cid = _cell_id(cell)
    try:
        # broken_media deliberately leaves the media route unregistered, so the
        # audit server answers 404 and the client fallback path really runs.
        if cell["state"] != "broken_media":
            page.route("**" + AUDIT_MEDIA_PREFIX + "*", _make_media_route(media))

        console_errors, page_errors = [], []
        server_errors, failed_requests = [], []
        page.on("console", lambda msg: console_errors.append(msg.text)
                if msg.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on("response", lambda r: server_errors.append(
            {"url": r.url, "status": r.status})
            if (r.status >= 500 and r.url.startswith(server.base_url)) else None)
        page.on("requestfailed", lambda req: failed_requests.append(
            {"url": req.url, "error": str(req.failure)})
            if req.url.startswith(server.base_url) else None)

        page.goto("%s/__audit__/login/%s" % (server.base_url, cell["scenario"]),
                  wait_until="domcontentloaded", timeout=30000)
        resp = page.goto("%s/pump-check-gallery" % server.base_url,
                         wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_function(
                LOADING_JS if cell["state"] == "loading" else SETTLED_JS,
                timeout=15000)
        except PWTimeoutError:
            pass

        modal_focus = None
        if cell["state"] == "modal":
            page.evaluate("() => document.querySelector('.gallery-item')?.focus()")
            page.keyboard.press("Enter")
            try:
                page.wait_for_function(
                    "() => document.getElementById('gallery-modal')"
                    "?.classList.contains('show')", timeout=5000)
                page.wait_for_function(
                    "() => [...document.querySelectorAll('#gallery-detail img')]"
                    ".every((i) => i.complete)", timeout=8000)
            except PWTimeoutError:
                pass
            modal_focus = page.evaluate(MODAL_FOCUS_JS)

        m = page.evaluate(MEASURE_JS)
        if cell["state"] in _NO_CARD_STATES:
            focus = dict(_SKIPPED_FOCUS)
        elif cell["state"] == "modal":
            # The card behind an open detail is inert; its focus contract is
            # already covered by the populated cells at the same viewport.
            focus = dict(_SKIPPED_FOCUS)
        else:
            focus = page.evaluate(FOCUS_JS)

        status_code = resp.status if resp else None
        verdict, reasons = _evaluate(cell, m, focus, modal_focus)

        if status_code is not None and status_code >= 400:
            verdict = "fail"
            reasons.append("document status %s" % status_code)
        if server_errors:
            verdict = "fail"
            reasons.append("unexpected 5xx: " + ", ".join(
                "%s %s" % (e["status"], e["url"].rsplit("/", 1)[-1])
                for e in server_errors))
        hard = [f for f in failed_requests
                if "ERR_ABORTED" not in (f.get("error") or "")]
        if hard:
            verdict = "fail"
            reasons.append("failed requests: " + ", ".join(
                f["url"].rsplit("/", 1)[-1] for f in hard))
        own_console = [c for c in console_errors
                       if not _is_third_party_console_noise(c)]
        if cell["state"] == "broken_media":
            # The media 404s are what this cell exists to produce. Chromium's
            # subresource-failure message carries no URL, so match its shape.
            own_console = [
                c for c in own_console
                if not ("Failed to load resource" in c and "404" in c)]
        if own_console or page_errors:
            verdict = "fail"
            reasons.append("console/page errors present: %s" % (own_console or page_errors))

        shot_rel = None
        if cell["shot"]:
            page.screenshot(path=str(shots_dir / (cid + ".png")),
                            full_page=cell["state"] != "modal")
            shot_rel = "screenshots/%s.png" % cid

        return {
            "id": cid, "state": cell["state"], "viewport": cell["viewport"],
            "viewport_px": dims, "locale": cell["locale"],
            "scenario": cell["scenario"], "status_code": status_code,
            "verdict": verdict, "reasons": reasons,
            "console_errors": console_errors, "own_console_errors": own_console,
            "page_errors": page_errors, "server_errors": server_errors,
            "failed_requests": failed_requests, "focus": focus,
            "modal_focus": modal_focus, "measurements": m, "screenshot": shot_rel,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "id": cid, "state": cell["state"], "viewport": cell["viewport"],
            "locale": cell["locale"], "verdict": "blocked",
            "reasons": ["%s: %s" % (type(exc).__name__, exc)],
        }
    finally:
        context.close()


def _seconds(value):
    """A CSS duration in seconds. An unparseable token counts as motion rather
    than silently passing the reduced-motion check."""
    token = (value or "").strip()
    try:
        if token.endswith("ms"):
            return float(token[:-2]) / 1000
        return float(token.rstrip("s"))
    except ValueError:
        return float("inf")


def _run_reduced_motion(browser, server, app, clocks, media, shots_dir, PWTimeoutError):
    """One populated and one loading cell under prefers-reduced-motion."""
    results = []
    for state in ("populated", "loading"):
        scenario = STATE_SCENARIO[state]
        _set_user_language(app, scenario, "en")
        cid = "pump-gallery__reduced-motion-%s__390__en" % state
        context = browser.new_context(
            viewport=dict(VIEWPORTS["390"]), reduced_motion="reduce")
        context.add_init_script(
            browser_clock_script(clocks[scenario]["fixed_current_datetime"]))
        if state == "loading":
            context.add_init_script(STALL_GALLERY_DATA_JS)
        page = context.new_page()
        try:
            page.route("**" + AUDIT_MEDIA_PREFIX + "*", _make_media_route(media))
            page.goto("%s/__audit__/login/%s" % (server.base_url, scenario),
                      wait_until="domcontentloaded", timeout=30000)
            page.goto("%s/pump-check-gallery" % server.base_url,
                      wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_function(
                    LOADING_JS if state == "loading" else SETTLED_JS, timeout=12000)
            except PWTimeoutError:
                pass
            motion = page.evaluate(REDUCED_MOTION_JS)
            reasons = []
            if not motion["reduced"]:
                reasons.append("reduced-motion emulation did not apply")
            target = motion["skeleton"] if state == "loading" else motion["card"]
            if target is None:
                reasons.append("nothing to measure for %s under reduced motion" % state)
            else:
                if target["animation"] not in ("none", ""):
                    reasons.append("%s still runs animation %s under reduced motion"
                                   % (state, target["animation"]))
                # The populated card's only motion is a transition, so an
                # animation-only assertion there would be vacuous.
                if any(_seconds(d) > 0
                       for d in (target["transition"] or "0s").split(",")):
                    reasons.append("%s keeps transition %s under reduced motion"
                                   % (state, target["transition"]))
            page.screenshot(path=str(shots_dir / (cid + ".png")), full_page=True)
            results.append({
                "id": cid, "state": "reduced-motion-%s" % state, "viewport": "390",
                "locale": "en", "verdict": "pass" if not reasons else "fail",
                "reasons": reasons, "measurements": motion,
                "screenshot": "screenshots/%s.png" % cid,
            })
        except Exception as exc:  # noqa: BLE001
            results.append({
                "id": cid, "state": "reduced-motion-%s" % state, "viewport": "390",
                "locale": "en", "verdict": "blocked",
                "reasons": ["%s: %s" % (type(exc).__name__, exc)]})
        finally:
            context.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--only", action="append", default=None,
        help="Restrict to state, state:viewport or state:viewport:locale")
    args = parser.parse_args()
    manifest = run(args.output, only=args.only)
    print(json.dumps({
        "totals": manifest["totals"],
        "failed_ids": manifest["failed_ids"],
        "blocked_ids": manifest["blocked_ids"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
