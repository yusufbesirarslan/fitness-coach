"""Progress Redesign PR4 — PHYSIQUE PROGRESS exact browser matrix.

Reuses the Sprint-0 hermetic audit harness (``create_audit_app`` + ``AuditServer``
+ fixed browser clock + Chromium) to run the EXACT PR4 matrix: every required
Physique Progress semantic state x every required viewport, plus the endpoint-
failure state that no state-driven seed can produce. Hermetic:
``create_audit_app`` calls ``configure_audit_environment`` before any ``app.*``
import, so no production or external dependency is contacted.

The nine states, and what each one exists to prove:

  empty                 no Pump Checks at all
  legacy_only           captured_at IS NULL rows stay gallery-only
  single_check          one canonical check, no comparison
  history_only          multiple canonical checks, no saved comparison
  comparable            persisted comparable comparison
  limited               persisted limited comparison
  not_comparable        persisted not_comparable comparison
  stale_comparison      saved comparison older than the latest check
  endpoint_failure      PHYSIQUE PROGRESS alone unavailable; other four stay

Seeds are computed against the harness's FIXED clock (2026-07-20 Istanbul).
Each cell asserts the state the SERVICE reports, so a seed that drifts fails
loudly instead of silently auditing the wrong state.

Run (WSL, Sprint-0 venv + browsers):
  PLAYWRIGHT_BROWSERS_PATH=<cache> \\
    python -m scripts.frontend_audit.progress_pr4_matrix \\
      --output docs/frontend-readiness/progress-pr4
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .app import ROOT, create_audit_app
from .runner import AuditServer, browser_clock_script
from .seed import seed_all


VIEWPORTS = {
    "390": {"width": 390, "height": 844},
    "768": {"width": 768, "height": 1024},
    "1280": {"width": 1280, "height": 800},
    "1440": {"width": 1440, "height": 900},
}

AUDIT_DAY = datetime(2026, 7, 20, 6, 30)  # 09:30 Istanbul as naive UTC

STATE_SCENARIO = {
    "empty": "social-empty",
    "legacy_only": "active-rest-day",
    "single_check": "coach-history",
    "history_only": "wearable-connected",
    "comparable": "active-workout",
    "limited": "wearable-disconnected",
    "not_comparable": "progress-history",
    "stale_comparison": "completed-workout",
}
FAILURE_SCENARIO = "social-empty"

LONG_SUMMARY = (
    "Lighting and camera height match closely enough to treat the two frames "
    "as a cautious visual comparison, although the long unspaced token "
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "
    "must stay inside the PHYSIQUE PROGRESS column without expanding the page."
)

# 1x1 PNG. Harness-only fixture so image load/layout is measurable without S3.
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
    b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

SETTLED_JS = r"""
() => {
  const physique = document.getElementById('physique-body');
  const physiqueReady = !!(physique && (
    physique.getAttribute('data-state') ||
    physique.getAttribute('data-status') === 'unavailable'
  ));
  const your = document.getElementById('ps-state');
  const history = document.getElementById('history-list');
  const working = document.getElementById('ax-working');
  return !!(
    physiqueReady &&
    your && your.textContent && your.textContent.trim() &&
    history && history.textContent && history.textContent.trim() &&
    working && working.getAttribute('data-status')
  );
}
"""

MEASURE_JS = r"""
() => {
  const de = document.documentElement;
  const q = (s) => [...document.querySelectorAll(s)];
  const text = (el) => (el && el.textContent ? el.textContent.trim() : '');
  const section = document.querySelector('[aria-labelledby="pp-h"]');
  const body = document.getElementById('physique-body');
  const chips = q('.pp-chip').map((el) => {
    const cs = getComputedStyle(el);
    return {
      region: el.dataset.region || null,
      label: text(el),
      checked: el.getAttribute('aria-checked') === 'true',
      tabindex: el.getAttribute('tabindex'),
      role: el.getAttribute('role'),
      overflow: el.scrollWidth > el.clientWidth + 1,
      visible: cs.display !== 'none' && cs.visibility !== 'hidden'
               && el.getClientRects().length > 0,
    };
  });
  const images = q('#physique-body img').map((img) => {
    const cs = getComputedStyle(img);
    return {
      src: img.getAttribute('src') || '',
      alt: img.getAttribute('alt') || '',
      complete: img.complete,
      natural_width: img.naturalWidth,
      loaded: !!(img.complete && img.naturalWidth > 0),
      width: img.clientWidth,
      height: img.clientHeight,
      overflow: img.scrollWidth > img.clientWidth + 1,
      display: cs.display,
    };
  });
  const pair = body ? body.querySelector('.pp-pair') : null;
  const pairCs = pair ? getComputedStyle(pair) : null;
  const reliability = body ? body.querySelector('.pp-reliability') : null;
  const longOverflow = q('#physique-body p, #physique-body li, #physique-body h4')
    .filter((el) => el.scrollWidth > el.clientWidth + 1)
    .map((el) => el.className || el.tagName)
    .slice(0, 10);
  const bodyText = document.body ? document.body.innerText : '';
  return {
    doc_scroll_width: de.scrollWidth,
    doc_client_width: de.clientWidth,
    doc_horizontal_overflow: de.scrollWidth > de.clientWidth + 1,
    section_present: !!section,
    section_heading_tag: section && section.querySelector('#pp-h')
      ? section.querySelector('#pp-h').tagName : null,
    section_overflow: section ? section.scrollWidth > section.clientWidth + 1 : false,
    physique_state: body ? body.getAttribute('data-state') : null,
    physique_region: body ? body.getAttribute('data-region') : null,
    physique_text: text(body),
    physique_text_len: text(body).length,
    comparability: reliability ? reliability.getAttribute('data-comparability') : null,
    reliability_text: text(reliability),
    stale_note: !!(body && /latest saved comparison|son kaydedilen karşılaştırma/i.test(text(body))),
    legacy_note: !!(body && /older Pump Checks|eski Pump Check/i.test(text(body))),
    unavailable: !!(body && (
      body.getAttribute('data-status') === 'unavailable' ||
      /couldn't be loaded|yüklenemedi/i.test(text(body))
    )),
    compare_present: !!(body && body.querySelector('.pp-compare')),
    pair_present: !!pair,
    pair_columns: pairCs ? pairCs.gridTemplateColumns : null,
    pair_figures: pair ? pair.querySelectorAll('figure').length : 0,
    images: images,
    image_count: images.length,
    images_loaded: images.filter((img) => img.loaded).length,
    chips: chips,
    chip_count: chips.length,
    radiogroup: !!document.querySelector('.pp-regions[role="radiogroup"]'),
    long_text_overflow: longOverflow,
    your_progress_state: text(document.getElementById('ps-state')),
    what_changed_cards: q('.wc-card').length,
    axis_working_status: document.getElementById('ax-working')
      ? document.getElementById('ax-working').getAttribute('data-status') : null,
    axis_watch_status: document.getElementById('ax-watch')
      ? document.getElementById('ax-watch').getAttribute('data-status') : null,
    axis_next_status: document.getElementById('ax-next')
      ? document.getElementById('ax-next').getAttribute('data-status') : null,
    history_filled: text(document.getElementById('history-list')).length > 0,
    raw_key_leak: [...new Set(bodyText.match(/\bprogress\.[a-z_]+/g) || [])].slice(0, 10),
    horizontal_scrollers: q('[aria-labelledby="pp-h"] *')
      .filter((el) => el.scrollWidth > el.clientWidth + 1)
      .map((el) => el.className || el.tagName).slice(0, 10),
    html_lang: de.getAttribute('lang'),
  };
}
"""

FOCUS_JS = r"""
() => {
  const selected = document.querySelector('.pp-chip[aria-checked="true"]')
    || document.querySelector('.pp-chip');
  if (!selected) return {present: false, focusable: false, outline: null};
  selected.focus();
  const cs = getComputedStyle(selected);
  return {
    present: true,
    focusable: document.activeElement === selected,
    outline: cs.outlineStyle + ' ' + cs.outlineWidth,
    box_shadow: cs.boxShadow,
    selected: selected.dataset.region || null,
  };
}
"""


def _token(prefix, n):
    return (f"{prefix}{n:02d}" + "x" * 24)[:24]


def _analysis():
    return {
        "summary": "Visible definition can be assessed in this image.",
        "observations": ["Shoulder outline is visible."],
        "strengths": ["Consistent framing."],
        "focus_areas": ["Keep lighting even."],
        "limitations": ["One image cannot establish progress."],
        "next_check_guidance": "Repeat this framing next time.",
        "quality": "sufficient",
    }


def _comparison_analysis(comparability):
    return {
        "summary": LONG_SUMMARY,
        "observed_changes": [
            "Upper chest outline looks a little fuller in the later frame.",
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        ],
        "stable_areas": ["Waist line looks similar."],
        "focus_areas": ["Keep the same camera height."],
        "limitations": [
            "Lighting still differs slightly between the two frames.",
        ],
        "comparability_reasons": [
            "Both frames show the same pose and crop."
            if comparability != "not_comparable"
            else "The later frame is cropped much tighter than the first.",
        ],
        "next_check_guidance": "Match the first photo's lighting and distance.",
    }


def _add_check(db, PumpCheck, user, *, token, captured_at, region, source_version):
    row = PumpCheck(
        user_id=user.id,
        public_id=token,
        captured_at=captured_at,
        body_region=region,
        location_type="gym",
        visibility="private",
        valid=True,
        analysis_status="completed",
        analysis=_analysis(),
        analysis_version=source_version,
        image_key=f"pump-checks/{user.id}/2026/07/{token[:8]}.jpg",
    )
    db.session.add(row)
    db.session.flush()
    return row


def _add_legacy(db, PumpCheck, user):
    db.session.add(PumpCheck(
        user_id=user.id,
        captured_at=None,
        body_region=None,
        visibility="private",
        valid=True,
        image_key=f"pump-checks/{user.id}/2025/01/legacy.jpg",
        created_at=datetime(2025, 1, 1, 12, 0, 0),
    ))


def _add_comparison(
        db, PumpCheckComparison, user, baseline, current, *,
        comparability, public_id, analysis_version):
    db.session.add(PumpCheckComparison(
        user_id=user.id,
        baseline_pump_check_id=baseline.id,
        current_pump_check_id=current.id,
        public_id=public_id,
        status="completed",
        comparability=comparability,
        analysis=_comparison_analysis(comparability),
        analysis_version=analysis_version,
        created_at=datetime(2026, 7, 15, 10, 0, 0),
    ))


def seed_states(app) -> dict:
    """Write the per-state Pump Check / comparison rows onto each scenario."""
    from app.extensions import db
    from app.models import PumpCheck, PumpCheckComparison, User
    from app.services.mobile_pump_check_comparisons.analysis import ANALYSIS_VERSION
    from app.services.mobile_pump_checks.analysis import (
        ANALYSIS_VERSION as SOURCE_ANALYSIS_VERSION,
    )

    counts = {}
    with app.app_context():
        for state, scenario in STATE_SCENARIO.items():
            user = User.query.filter_by(username=f"audit-{scenario}").first()
            assert user is not None, scenario
            PumpCheckComparison.query.filter_by(user_id=user.id).delete()
            PumpCheck.query.filter_by(user_id=user.id).delete()
            db.session.flush()
            prefix = state[:2]
            if state == "empty":
                counts[state] = 0
                continue
            if state == "legacy_only":
                _add_legacy(db, PumpCheck, user)
                counts[state] = 1
                continue
            if state == "single_check":
                _add_check(
                    db, PumpCheck, user, token=_token(prefix, 1),
                    captured_at=datetime(2026, 7, 10, 8), region="upper_body",
                    source_version=SOURCE_ANALYSIS_VERSION)
                counts[state] = 1
                continue
            first = _add_check(
                db, PumpCheck, user, token=_token(prefix, 1),
                captured_at=datetime(2026, 7, 1, 8), region="upper_body",
                source_version=SOURCE_ANALYSIS_VERSION)
            second = _add_check(
                db, PumpCheck, user, token=_token(prefix, 2),
                captured_at=datetime(2026, 7, 12, 8), region="upper_body",
                source_version=SOURCE_ANALYSIS_VERSION)
            # A second area so region chips exist wherever history exists.
            _add_check(
                db, PumpCheck, user, token=_token(prefix, 9),
                captured_at=datetime(2026, 7, 8, 8), region="arms",
                source_version=SOURCE_ANALYSIS_VERSION)
            if state == "history_only":
                counts[state] = 3
                continue
            if state == "stale_comparison":
                _add_check(
                    db, PumpCheck, user, token=_token(prefix, 3),
                    captured_at=datetime(2026, 7, 18, 8), region="upper_body",
                    source_version=SOURCE_ANALYSIS_VERSION)
                _add_comparison(
                    db, PumpCheckComparison, user, first, second,
                    comparability="comparable", public_id=_token("st", 1),
                    analysis_version=ANALYSIS_VERSION)
                counts[state] = 4
                continue
            _add_comparison(
                db, PumpCheckComparison, user, first, second,
                comparability=state, public_id=_token(prefix, 8),
                analysis_version=ANALYSIS_VERSION)
            counts[state] = 3
        db.session.commit()
    return counts


def verify_states(app) -> dict:
    from app.models import User
    from app.services.progress_physique import build_progress_physique

    observed = {}
    with app.app_context():
        for state, scenario in STATE_SCENARIO.items():
            user = User.query.filter_by(username=f"audit-{scenario}").first()
            physique = build_progress_physique(user.id)
            observed[state] = {
                "scenario": scenario,
                "state": physique.state,
                "legacy": physique.legacy_gallery_available,
                "comparability": (
                    physique.comparison.comparability
                    if physique.comparison is not None else None),
                "current_is_latest": (
                    physique.comparison.current_is_latest_check
                    if physique.comparison is not None else None),
                "region_count": len(physique.regions),
                "check_count": len(physique.recent_checks),
            }
    return observed


EXPECTED = {
    "empty": {
        "state": "empty", "legacy": False, "comparability": None,
        "current_is_latest": None,
    },
    "legacy_only": {
        "state": "empty", "legacy": True, "comparability": None,
        "current_is_latest": None,
    },
    "single_check": {
        "state": "single_check", "legacy": False, "comparability": None,
        "current_is_latest": None,
    },
    "history_only": {
        "state": "history_only", "legacy": False, "comparability": None,
        "current_is_latest": None,
    },
    "comparable": {
        "state": "comparison_available", "legacy": False,
        "comparability": "comparable", "current_is_latest": True,
    },
    "limited": {
        "state": "comparison_available", "legacy": False,
        "comparability": "limited", "current_is_latest": True,
    },
    "not_comparable": {
        "state": "comparison_available", "legacy": False,
        "comparability": "not_comparable", "current_is_latest": True,
    },
    "stale_comparison": {
        "state": "comparison_available", "legacy": False,
        "comparability": "comparable", "current_is_latest": False,
    },
}


def _cells(only=None):
    cells = []
    for state in STATE_SCENARIO:
        for viewport in VIEWPORTS:
            cells.append({
                "state": state, "viewport": viewport,
                "scenario": STATE_SCENARIO[state], "fail": False,
                "shot": viewport in ("390", "1440"),
            })
    for viewport in VIEWPORTS:
        cells.append({
            "state": "endpoint_failure", "viewport": viewport,
            "scenario": FAILURE_SCENARIO, "fail": True,
            "shot": viewport in ("390", "1440"),
        })
    if only:
        wanted = set(only)
        cells = [
            cell for cell in cells
            if f"{cell['state']}:{cell['viewport']}" in wanted
            or cell["state"] in wanted
        ]
    return cells


def _cell_id(cell):
    return f"physique-progress__{cell['state']}__{cell['viewport']}"


def _has_visible_focus(focus):
    if not focus.get("focusable"):
        return False
    outline = focus.get("outline") or ""
    shadow = focus.get("box_shadow") or ""
    if outline and not outline.startswith("none") and "0px" not in outline.split():
        return True
    return shadow not in ("", "none")


def _evaluate(cell, m, focus, keyboard=None):
    reasons = []

    if m["doc_horizontal_overflow"]:
        reasons.append(
            f"document horizontal overflow ({m['doc_scroll_width']}"
            f" > {m['doc_client_width']})")
    if m["section_overflow"]:
        reasons.append("PHYSIQUE PROGRESS section horizontal overflow")
    if m["horizontal_scrollers"]:
        reasons.append(f"horizontal scroller inside the section: {m['horizontal_scrollers']}")
    if m["long_text_overflow"]:
        reasons.append(f"long text overflow: {m['long_text_overflow']}")
    if m["section_heading_tag"] != "H2":
        reasons.append(f"section heading is {m['section_heading_tag']} (want H2)")
    if m["raw_key_leak"]:
        reasons.append(f"raw localization keys leaked: {m['raw_key_leak']}")

    # Other four Progress sections stay independently loaded.
    if not m["your_progress_state"]:
        reasons.append("YOUR PROGRESS did not render")
    if m["what_changed_cards"] != 3:
        reasons.append(f"WHAT CHANGED cards={m['what_changed_cards']} (want 3)")
    if not m["axis_working_status"]:
        reasons.append("AXIS INSIGHTS did not settle")
    if not m["history_filled"]:
        reasons.append("PROGRESS HISTORY did not render")

    if cell["fail"]:
        if not m["unavailable"]:
            reasons.append("failure cell did not render the unavailable state")
        if m["compare_present"]:
            reasons.append("failure cell rendered comparison content")
        if m["physique_state"] == "comparison_available":
            reasons.append("failure cell published comparison_available")
        return ("pass" if not reasons else "fail"), reasons

    expected = EXPECTED[cell["state"]]
    if m["physique_state"] != expected["state"]:
        reasons.append(
            f"physique state={m['physique_state']} (want {expected['state']})")
    if expected["comparability"] is None:
        if m["compare_present"]:
            reasons.append("comparison chrome rendered without a comparison")
        if m["comparability"]:
            reasons.append(f"comparability={m['comparability']} (want none)")
    else:
        if not m["compare_present"]:
            reasons.append("comparison chrome missing")
        if m["comparability"] != expected["comparability"]:
            reasons.append(
                f"comparability={m['comparability']} (want {expected['comparability']})")
        if not m["reliability_text"]:
            reasons.append("comparability is colour-only (no reliability text)")
        if m["pair_figures"] != 2:
            reasons.append(f"pair figures={m['pair_figures']} (want 2)")
        if m["image_count"] != 2:
            reasons.append(f"image count={m['image_count']} (want 2)")
        if m["images_loaded"] != 2:
            reasons.append(f"images loaded={m['images_loaded']} (want 2)")
        for img in m["images"]:
            if not (img["alt"] or "").strip():
                reasons.append("image missing meaningful alt text")
            if "Pump Check" not in img["alt"] and "pump check" not in img["alt"].lower():
                reasons.append(f"weak image alt: {img['alt']!r}")

    if cell["state"] == "legacy_only" and not m["legacy_note"]:
        reasons.append("legacy-only cell missing the gallery note")
    if cell["state"] == "stale_comparison" and not m["stale_note"]:
        reasons.append("stale comparison missing the latest-saved note")
    if cell["state"] == "empty" and m["legacy_note"]:
        reasons.append("empty cell showed a legacy note")

    wants_chips = cell["state"] in {
        "history_only", "comparable", "limited", "not_comparable",
        "stale_comparison",
    }
    if wants_chips:
        if not m["radiogroup"] or m["chip_count"] < 2:
            reasons.append(f"region chips missing (count={m['chip_count']})")
        if not focus.get("focusable"):
            reasons.append("region chip is not keyboard focusable")
        if not _has_visible_focus(focus):
            reasons.append(f"region chip focus is not visible ({focus})")
        if keyboard:
            if not keyboard.get("moved"):
                reasons.append("ArrowRight did not change the selected region")
            if keyboard.get("errors"):
                reasons.append(f"region keyboard errors: {keyboard['errors']}")
    elif m["chip_count"] != 0:
        reasons.append(f"unexpected region chips ({m['chip_count']})")

    return ("pass" if not reasons else "fail"), reasons


_THIRD_PARTY_CONSOLE_HOSTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "www.google.com/g/collect",
    "clarity.ms",
)


def _is_third_party_console_noise(message: str) -> bool:
    return any(host in message for host in _THIRD_PARTY_CONSOLE_HOSTS)


def _install_image_fixture(app, image_url: str):
    from flask import Response

    from app.services import progress_physique as pkg

    @app.get("/__audit__/physique-image")
    def _audit_physique_image():
        return Response(_PNG, mimetype="image/png")

    def _signed(_user_id, image_key):
        if not image_key:
            return None
        return image_url

    pkg.sign_owned_image = _signed
    return lambda: setattr(pkg, "sign_owned_image", pkg.queries.sign_owned_image)


def run(output_dir: Path, only=None) -> dict:
    from playwright.sync_api import TimeoutError as PWTimeoutError
    from playwright.sync_api import sync_playwright

    from app.blueprints import tracking

    output_dir = Path(output_dir)
    shots_dir = output_dir / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    db_path = ROOT / "artifacts" / "ui-audit" / "progress_pr4_matrix.db"
    app = create_audit_app(db_path)
    seed_summary = seed_all(app)
    seed_summary["physique_rows"] = seed_states(app)
    observed_states = verify_states(app)

    drift = {
        state: observed_states[state]
        for state, want in EXPECTED.items()
        if (
            observed_states[state]["state"] != want["state"]
            or observed_states[state]["legacy"] != want["legacy"]
            or observed_states[state]["comparability"] != want["comparability"]
            or observed_states[state]["current_is_latest"] != want["current_is_latest"]
        )
    }

    clocks = app.extensions["frontend_audit"]["scenario_clocks"]
    real_builder = tracking.build_progress_physique

    def _broken_builder(*_a, **_kw):
        raise RuntimeError("audit-injected Physique Progress failure")

    cells = _cells(only)
    results = []
    with AuditServer(app) as server, sync_playwright() as pw:
        restore_sign = _install_image_fixture(
            app, f"{server.base_url}/__audit__/physique-image")
        browser = pw.chromium.launch(headless=True)
        try:
            for cell in cells:
                tracking.build_progress_physique = (
                    _broken_builder if cell["fail"] else real_builder)

                dims = VIEWPORTS[cell["viewport"]]
                context = browser.new_context(viewport=dict(dims))
                context.add_init_script(
                    browser_clock_script(
                        clocks[cell["scenario"]]["fixed_current_datetime"]))
                page = context.new_page()
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

                cid = _cell_id(cell)
                try:
                    page.goto(f"{server.base_url}/__audit__/login/{cell['scenario']}",
                              wait_until="domcontentloaded", timeout=30000)
                    try:
                        resp = page.goto(f"{server.base_url}/progress-page",
                                         wait_until="domcontentloaded", timeout=30000)
                    except PWTimeoutError:
                        resp = page.goto(f"{server.base_url}/progress-page",
                                         wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_function(SETTLED_JS, timeout=12000)
                    except PWTimeoutError:
                        pass
                    # Wait for comparison images to finish (or fail) before measuring.
                    try:
                        page.wait_for_function(
                            "() => [...document.querySelectorAll('#physique-body img')]"
                            ".every((img) => img.complete)",
                            timeout=5000)
                    except PWTimeoutError:
                        pass
                    m = page.evaluate(MEASURE_JS)
                    focus = page.evaluate(FOCUS_JS)
                    keyboard = None
                    if (
                        not cell["fail"]
                        and cell["state"] in {
                            "history_only", "comparable", "limited",
                            "not_comparable", "stale_comparison",
                        }
                    ):
                        keyboard = {"moved": False, "errors": []}
                        before = m.get("physique_region")
                        page.evaluate(
                            "() => { const el = document.querySelector("
                            "'.pp-chip[aria-checked=\"true\"]'); if (el) el.focus(); }")
                        page.keyboard.press("ArrowRight")
                        try:
                            page.wait_for_function(
                                """(prev) => {
                                  const el = document.getElementById('physique-body');
                                  return !!(el && el.getAttribute('data-state')
                                    && el.getAttribute('data-region')
                                    && el.getAttribute('data-region') !== prev);
                                }""",
                                arg=before, timeout=8000)
                        except PWTimeoutError:
                            keyboard["errors"].append("region change did not settle")
                        after = page.evaluate(
                            "() => document.getElementById('physique-body')"
                            "?.getAttribute('data-region') || null")
                        keyboard["from"] = before
                        keyboard["to"] = after
                        keyboard["moved"] = bool(after and after != before)
                        # Restore the default region so the screenshot matches the seed.
                        # Do not overwrite the original measurements used for the verdict.
                        if before:
                            page.evaluate(
                                """(region) => {
                                  const chip = [...document.querySelectorAll('.pp-chip')]
                                    .find((el) => el.dataset.region === region);
                                  if (chip) chip.click();
                                }""",
                                before)
                            try:
                                page.wait_for_function(
                                    """(want) => {
                                      const el = document.getElementById('physique-body');
                                      return !!(el && el.getAttribute('data-state')
                                        && el.getAttribute('data-region') === want);
                                    }""",
                                    arg=before, timeout=8000)
                            except PWTimeoutError:
                                keyboard["errors"].append("region restore did not settle")

                    status_code = resp.status if resp else None
                    verdict, reasons = _evaluate(cell, m, focus, keyboard)

                    if status_code is not None and status_code >= 500:
                        verdict = "fail"
                        reasons.append(f"document status {status_code}")
                    unexpected = [
                        e for e in server_errors
                        if not (cell["fail"] and e["url"].endswith("/api/progress/physique"))
                    ]
                    if unexpected:
                        verdict = "fail"
                        reasons.append("unexpected 5xx: " + ", ".join(
                            f"{e['status']} {e['url'].rsplit('/', 1)[-1]}" for e in unexpected))
                    hard = [f for f in failed_requests
                            if "ERR_ABORTED" not in (f.get("error") or "")]
                    if hard:
                        verdict = "fail"
                        reasons.append("failed requests: " + ", ".join(
                            f["url"].rsplit("/", 1)[-1] for f in hard))
                    own_console = [c for c in console_errors
                                   if not _is_third_party_console_noise(c)]
                    if cell["fail"]:
                        own_console = [c for c in own_console if "500" not in c]
                    if own_console or page_errors:
                        verdict = "fail"
                        reasons.append("console/page errors present")

                    shot_rel = None
                    if cell["shot"]:
                        shot_path = shots_dir / f"{cid}.png"
                        page.screenshot(path=str(shot_path), full_page=True)
                        shot_rel = f"screenshots/{cid}.png"

                    results.append({
                        "id": cid, "state": cell["state"], "viewport": cell["viewport"],
                        "viewport_px": dims, "scenario": cell["scenario"],
                        "injected_failure": cell["fail"], "status_code": status_code,
                        "verdict": verdict, "reasons": reasons,
                        "console_errors": console_errors,
                        "own_console_errors": own_console,
                        "page_errors": page_errors,
                        "server_errors": server_errors, "failed_requests": failed_requests,
                        "focus": focus, "keyboard": keyboard,
                        "measurements": m, "screenshot": shot_rel,
                    })
                except Exception as exc:  # noqa: BLE001
                    results.append({
                        "id": cid, "state": cell["state"], "viewport": cell["viewport"],
                        "verdict": "blocked", "reasons": [f"{type(exc).__name__}: {exc}"],
                    })
                finally:
                    context.close()
        finally:
            tracking.build_progress_physique = real_builder
            restore_sign()
            browser.close()

    passed = sum(1 for r in results if r["verdict"] == "pass")
    failed = [r["id"] for r in results if r["verdict"] == "fail"]
    blocked = [r["id"] for r in results if r["verdict"] == "blocked"]
    manifest = {
        "schema_version": "1.0.0",
        "pr": "progress-redesign-pr4-physique-progress",
        "engine": "chromium",
        "hermetic": True,
        "audit_day": "2026-07-20",
        "seed_summary": seed_summary,
        "observed_states": observed_states,
        "seed_drift": drift,
        "viewports": VIEWPORTS,
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--only", action="append", default=None,
        help="Restrict to state or state:viewport (repeatable)")
    args = parser.parse_args()
    manifest = run(args.output, only=args.only)
    print(json.dumps({
        "totals": manifest["totals"],
        "seed_drift": manifest["seed_drift"],
        "failed_ids": manifest["failed_ids"],
        "blocked_ids": manifest["blocked_ids"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
