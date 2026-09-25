"""Progress V2 PR4 — Physique + Recent Check-ins in a real browser.

Hermetic Chromium (tests/ux4_gate_support.py): the real template + static
bundle of this commit, served by the Flask test client. The two reads this PR
owns are fulfilled with payloads produced by the SERVER's own projection
functions (``progress_physique_payload`` / ``progress_history_payload``) over
synthetic canonical read models, so each state is exactly what production
would publish; Current State, Trends and Axis Insight read the real endpoints.

Scenarios (physique × history):

  empty      × several days (more than the visible bound, has_more)
  comparison × same-day check-ins (two on one Istanbul day)
  history    × one check-in
  empty      × no check-ins
  failure    × failure

For every scenario × 320/390/768/1024/1366 × EN/TR it proves: no horizontal
overflow, neither section is left on "Loading…", the Physique section has at
most one action and it is tappable, photos are bounded thumbnails, the history
shows only the bounded recent set (no hidden rows in the DOM), the date is
quieter than the summary and never collides with the weight, same-day
check-ins are grouped and expandable, failure never reads as "empty", and the
page makes exactly its existing reads.

Set PR4_QA_SHOTS=<dir> to also save screenshots of both sections per cell.

    python -m pytest tests/test_progress_physique_history_browser.py -v
"""
import json
import os
from datetime import date, datetime, timedelta

import pytest

from ux4_gate_support import gate, ready  # noqa: F401  (fixture re-export)

from app.services.progress_history import (
    HistoryEntry,
    ProgressHistory,
    progress_history_payload,
)
from app.services.progress_physique import (
    PhysiqueCheck,
    PhysiqueComparison,
    PhysiqueComparisonAnalysis,
    PhysiqueRegion,
    ProgressPhysique,
    progress_physique_payload,
)
from app.services.progress_summary import (
    build_window,
    summarize_consistency,
    summarize_performance,
    trajectory_for_signal,
)
from app.services.training_history.models import WeeklyVolume
from app.services.training_progression import ProgressionReport

pytestmark = pytest.mark.filterwarnings("ignore")

PHYSIQUE = "/api/progress/physique"
HISTORY = "/api/progress/history"
WIDTHS = (320, 390, 768, 1024, 1366)
IMAGE = "http://localhost/static/icon-holo.png"
VISIBLE = 4   # progress_presentation.js HISTORY_VISIBLE_GROUPS


# ── payloads from the server's own projections ──────────────────────────────

def _report(signal, sessions=(3, 3, 3, 3), consistency="consistent"):
    weekly = [WeeklyVolume(week_start=date(2026, 8, 28), session_count=n,
                           total_volume=1000.0 * n) for n in sessions]
    return ProgressionReport(
        weeks=4, has_data=signal != "insufficient_data", next_signal=signal,
        load_consistency=consistency, volume_trend="flat", strength_trend="flat",
        weekly_volume=weekly)


def _entry(created_utc, weight, delta, signal="keep_pushing"):
    report = _report(signal)
    day = (created_utc + timedelta(hours=3)).date()     # Istanbul, UTC+3
    return HistoryEntry(
        checked_in_at=created_utc, analysis_day=day, window=build_window(day, 4),
        trajectory=trajectory_for_signal(signal),
        performance=summarize_performance(report),
        consistency=summarize_consistency(report),
        weight_kg=weight, weight_delta_kg=delta)


def _history(kind):
    if kind == "empty":
        return progress_history_payload(ProgressHistory(state="empty"))
    if kind == "one":
        entries = (_entry(datetime(2026, 9, 18, 9), 78.0, None, "insufficient_data"),)
        return progress_history_payload(ProgressHistory(state="available", entries=entries))
    if kind == "same_day":
        entries = (
            _entry(datetime(2026, 9, 18, 17, 40), 78.0, -0.4),
            _entry(datetime(2026, 9, 18, 6, 5), 78.4, 0.0),
            _entry(datetime(2026, 9, 11, 9), 78.4, None, "build_consistency"),
        )
        return progress_history_payload(ProgressHistory(state="available", entries=entries))
    signals = ("keep_pushing", "build_consistency", "progressing", "plateau",
               "deload", "insufficient_data")
    entries = tuple(
        _entry(datetime(2026, 9, 18, 9) - timedelta(days=7 * i), 78.0 + 0.3 * i,
               -0.3 if i < 5 else None, signal)
        for i, signal in enumerate(signals))
    return progress_history_payload(ProgressHistory(
        state="available", entries=entries, has_more=True))


def _check(pid, when):
    return PhysiqueCheck(public_id=pid, captured_at=when, body_region="upper_body",
                         analysis_status="completed", analysis_quality="good",
                         image_url=IMAGE)


def _physique(kind):
    if kind == "empty":
        return progress_physique_payload(ProgressPhysique(
            state="empty", selected_region=None, regions=(), recent_checks=(),
            comparison=None, legacy_gallery_available=False))
    a, b = datetime(2026, 8, 20, 9), datetime(2026, 9, 17, 9)
    region = PhysiqueRegion(body_region="upper_body", check_count=2,
                            latest_captured_at=b)
    checks = (_check("pc_b", b), _check("pc_a", a))
    if kind == "history":
        return progress_physique_payload(ProgressPhysique(
            state="history_only", selected_region="upper_body", regions=(region,),
            recent_checks=checks, comparison=None, legacy_gallery_available=False))
    comparison = PhysiqueComparison(
        public_id="cmp_1", comparability="limited",
        baseline_pump_check_id="pc_a", current_pump_check_id="pc_b",
        current_is_latest_check=True, baseline_captured_at=a, current_captured_at=b,
        baseline_image_url=IMAGE, current_image_url=IMAGE,
        analysis=PhysiqueComparisonAnalysis(
            summary="Shoulders look slightly fuller; lighting differs between photos.",
            observed_changes=("Slightly fuller shoulders",),
            stable_areas=("Arms",), focus_areas=("Posture",),
            limitations=("Different lighting",), comparability_reasons=(),
            next_check_guidance="Use the same spot and light next time."))
    return progress_physique_payload(ProgressPhysique(
        state="comparison_available", selected_region="upper_body", regions=(region,),
        recent_checks=checks, comparison=comparison, legacy_gallery_available=False))


SCENARIOS = {
    "empty_several": ("empty", "several"),
    "comparison_same_day": ("comparison", "same_day"),
    "history_one": ("history", "one"),
    "empty_none": ("empty", "empty"),
    "failure": (None, None),
}


PROBE = r"""
() => {
  const q = s => document.querySelector(s);
  const box = el => { const r = el.getBoundingClientRect();
                      return {top: r.top, bottom: r.bottom, left: r.left,
                              right: r.right, width: r.width, height: r.height}; };
  const pp = q('#physique-body'), hist = q('#history-list');
  const items = [...hist.querySelectorAll('.hist-item')];
  const px = el => parseFloat(getComputedStyle(el).fontSize);
  return {
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    loading: document.querySelectorAll('#progress-physique .prog-loading, #progress-recent-checkins .prog-loading').length,
    ppState: pp.getAttribute('data-state'), ppStatus: pp.getAttribute('data-status'),
    ppText: pp.textContent.trim(),
    ppLinks: [...pp.querySelectorAll('a')].map(a => ({href: a.getAttribute('href'),
                                                     text: a.textContent.trim(), box: box(a)})),
    ppImgs: [...pp.querySelectorAll('img')].map(i => ({w: i.getBoundingClientRect().width,
                                                     lazy: i.loading})),
    ppSection: box(q('#progress-physique')),
    histState: hist.getAttribute('data-state'), histStatus: hist.getAttribute('data-status'),
    histText: hist.textContent.trim(),
    hiddenInHistory: hist.querySelectorAll('[hidden]').length,
    items: items.map(li => {
      const s = li.querySelector('.hist-summary'), m = li.querySelector('.hist-metric');
      const d = li.querySelector('time.hist-date');
      return {
        day: li.getAttribute('data-day'), count: li.getAttribute('data-count'),
        summary: s.textContent.trim(), metric: m ? m.textContent.trim() : null,
        datetime: d && d.getAttribute('datetime'),
        sBox: box(s), mBox: m ? box(m) : null, dBox: d ? box(d) : null,
        sPx: px(s), dPx: d ? px(d) : null,
        dColor: d ? getComputedStyle(d).color : null,
        more: !!li.querySelector('.hist-more'),
      };
    }),
    toggle: q('.hist-toggle') ? q('.hist-toggle').textContent.trim() : null,
    histSection: box(q('#progress-recent-checkins')),
    order: [...document.querySelectorAll('[data-progress-section]')]
        .map(s => s.getAttribute('data-progress-section')),
  };
}
"""


def _overlap(a, b):
    return not (a["right"] <= b["left"] or b["right"] <= a["left"]
                or a["bottom"] <= b["top"] or b["bottom"] <= a["top"])


@pytest.mark.parametrize("locale", ["en", "tr"])
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_physique_and_recent_checkins_matrix(
        app, gate, make_user, login, scenario, locale):  # noqa: F811
    name = "pr4br_%s_%s" % (scenario[:10], locale)
    user = make_user(name)
    ready(app, user.id, language=locale)
    login(name)

    phys_kind, hist_kind = SCENARIOS[scenario]
    if scenario == "failure":
        gate.overrides[PHYSIQUE] = (500, json.dumps({"error": "x"}))
        gate.overrides[HISTORY] = (500, json.dumps({"error": "x"}))
    else:
        gate.overrides[PHYSIQUE] = (200, json.dumps(_physique(phys_kind)))
        gate.overrides[HISTORY] = (200, json.dumps(_history(hist_kind)))

    catalog = json.loads(open("locales/%s.json" % locale, encoding="utf-8").read())
    shots = os.environ.get("PR4_QA_SHOTS")

    for width in WIDTHS:
        gate.visit("/progress-page", width=width)
        f = gate.page.evaluate(PROBE)
        cell = (scenario, locale, width)

        assert not gate.errors, (cell, gate.errors)
        assert not f["overflow"], cell
        assert f["loading"] == 0, cell
        assert f["order"] == ["header", "current-state", "trends", "axis-insight",
                              "physique", "recent-checkins"], cell
        assert f["hiddenInHistory"] == 0, cell

        # ── Physique ──
        assert len(f["ppLinks"]) <= 1, (cell, f["ppLinks"])
        for link in f["ppLinks"]:
            assert link["box"]["height"] >= 44, (cell, link)
        for img in f["ppImgs"]:
            assert img["w"] <= 141 and img["lazy"] == "lazy", (cell, img)
        if scenario == "failure":
            assert f["ppStatus"] == "unavailable", cell
            assert f["ppText"] == catalog["progress.physique_unavailable"], cell
        elif phys_kind == "empty":
            assert f["ppState"] == "empty", cell
            assert f["ppLinks"][0]["href"] == "/training", cell
            assert f["ppLinks"][0]["text"] == catalog["progress.physique_empty_cta"], cell
            assert catalog["progress.physique_empty_title"] in f["ppText"], cell
            # Compact: heading + two lines + one button, never a tall card.
            assert f["ppSection"]["height"] <= 260, (cell, f["ppSection"])
        else:
            assert f["ppLinks"][0]["href"] == "/pump-check-gallery", cell
            assert len(f["ppImgs"]) == 2, cell

        # ── Recent Check-ins ──
        if scenario == "failure":
            assert f["histStatus"] == "unavailable", cell
            assert f["histText"] == catalog["progress.history_unavailable"], cell
            assert catalog["progress.history_empty_title"] not in f["histText"], cell
        elif hist_kind == "empty":
            assert f["histState"] == "empty", cell
            assert f["items"] == [], cell
            assert catalog["progress.history_empty_title"] in f["histText"], cell
        else:
            expected = {"several": VISIBLE, "same_day": 2, "one": 1}[hist_kind]
            assert len(f["items"]) == expected, (cell, len(f["items"]))
            days = [i["day"] for i in f["items"]]
            assert days == sorted(days, reverse=True), cell     # newest first
            for item in f["items"]:
                assert item["datetime"] == item["day"], cell
                assert item["dPx"] < item["sPx"], (cell, item)   # date is metadata
                assert "_" not in item["summary"] and "{" not in item["summary"], cell
                if item["mBox"]:
                    assert not _overlap(item["sBox"], item["mBox"]), (cell, item)
                    assert not _overlap(item["dBox"], item["mBox"]), (cell, item)
            if hist_kind == "several":
                assert f["toggle"] == catalog["progress.history_show_earlier"].replace(
                    "{n}", "2"), cell
            else:
                assert f["toggle"] is None, cell
            if hist_kind == "same_day":
                first = f["items"][0]
                assert first["count"] == "2" and first["more"], cell
                assert first["metric"].startswith("78.0"), cell
                assert not f["items"][1]["more"], cell

        # Exactly the page's existing reads; images excluded from the static
        # budget check (the fixture image is served from /static).
        reads = [p for p in gate.app_reads() if p.startswith("/api/")]
        assert sorted(reads) == sorted(["/api/progress/summary", PHYSIQUE, HISTORY,
                                        "/api/progress/axis-insights"]), (cell, reads)

        if shots:
            os.makedirs(shots, exist_ok=True)
            for section in ("physique", "recent-checkins"):
                gate.page.locator("#progress-%s" % section).screenshot(
                    path=os.path.join(shots, "%s_%s_%s_%d.png"
                                      % (scenario, section, locale, width)))
            if width in (390, 1366):
                gate.page.screenshot(
                    path=os.path.join(shots, "page_%s_%s_%d.png"
                                      % (scenario, locale, width)), full_page=True)


def _items(gate):
    return gate.page.evaluate(
        "() => [...document.querySelectorAll('#history-list .hist-item')]"
        ".map(li => li.getAttribute('data-day'))")


def test_disclosures_build_rows_on_demand_and_remove_them(
        app, gate, make_user, login):  # noqa: F811
    user = make_user("pr4disclose")
    ready(app, user.id, language="en")
    login("pr4disclose")
    history = _history("several")
    history["entries"].insert(1, dict(history["entries"][0],
                                      checked_in_at="2026-09-18T08:00:00+03:00"))
    gate.overrides[HISTORY] = (200, json.dumps(history))
    gate.overrides[PHYSIQUE] = (200, json.dumps(_physique("empty")))
    gate.visit("/progress-page", width=390)
    page = gate.page

    # Initial DOM holds only the visible groups — no hidden archive.
    assert len(_items(gate)) == VISIBLE
    assert page.locator("#history-list [hidden]").count() == 0
    assert page.locator(".hist-updates").count() == 0

    # Same-day: expand builds the day's check-ins, collapse removes them.
    more = page.locator(".hist-more").first
    assert more.get_attribute("aria-expanded") == "false"
    more.focus()
    page.keyboard.press("Enter")
    assert more.get_attribute("aria-expanded") == "true"
    rows = page.locator(".hist-updates li")
    assert rows.count() == 2
    assert "78.0" in rows.nth(0).inner_text()
    assert page.locator(".hist-updates time").first.get_attribute("datetime") \
        .startswith("2026-09-18T")
    more.click()
    assert page.locator(".hist-updates").count() == 0

    # Earlier groups: built on request, removed again; archive note appears.
    toggle = page.locator(".hist-toggle")
    assert toggle.get_attribute("aria-controls") == "hist-groups"
    toggle.click()
    assert len(_items(gate)) == 6
    assert page.locator(".hist-archive").count() == 1
    assert toggle.inner_text() == "Show fewer"
    toggle.click()
    assert len(_items(gate)) == VISIBLE
    assert page.locator(".hist-archive").count() == 0
    assert not gate.errors
