"""Progress V2 PR1 + PR2 — the Progress presentation model (static/progress_presentation.js).

The file is the ONE place Progress turns canonical summary state into
presentation: the state → copy tables and the summary view model
(current_state + metrics.{weight, training_volume, consistency}).

Two mechanisms, the ones tests/test_weekly_program_ui_js.py established (the
repository has no JS runner and adds no dependency for one):

* **Source guards** (always run) — read the shipped asset and assert on it.
* **Behavioural execution** under ``node`` via ``subprocess``: the module is
  pure (no DOM, no fetch), so it runs with nothing but a ``window`` object.
  Skipped when ``node`` is absent; GitHub's ``ubuntu-latest`` ships Node.

What this file proves:

A. copy mapping — every server state maps to a locale key that exists in both
   catalogs; no internal identifier is ever rendered; unknown values degrade to
   the unavailable state instead of a guess.
B. metric contract — weight / training volume / consistency carry their facts,
   unit, comparison period and status, and sparse or missing data is safe.
C. deduplication — one summary never renders the same sentence twice, the
   consistency state has exactly one rendering, and Current State never
   repeats an Axis Insight sentence.
D. PR2 Current State + Trends — state → evidence (<= 2 measured facts) →
   next move; value · change · viz · note · meta per metric; sparkline / bar
   geometry only from real published series; sparse series yield a note,
   never a fake line.
E. performance — the model performs no I/O, and the page still fetches the
   summary exactly once.
F. V2 PR4 Recent Check-ins — buildHistoryView groups rows by the server's
   Istanbul analysis_day (never the browser's clock or timezone), keeps
   every same-day record, bounds the visible groups, reuses the shared
   state tables (one label per row), and never turns a failure into "empty".

    python -m pytest tests/test_progress_presentation_js.py -v
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.i18n import _CATALOG
from app.services.progress_summary import (
    CONSISTENCY_STATES,
    PERFORMANCE_STATES,
    TRAJECTORY_BY_SIGNAL,
    TRAJECTORY_STATES,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "static" / "progress_presentation.js"
SOURCE = SCRIPT_PATH.read_text(encoding="utf-8")
CONTROLLER = (ROOT / "static" / "progress.js").read_text(encoding="utf-8")

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

LOCALES = ("en", "tr")

# The performance.state the summary publishes for each canonical signal
# (app/services/progress_summary/analysis.py PERFORMANCE_BY_SIGNAL).
PERFORMANCE_FOR_SIGNAL = {
    "insufficient_data": "building_baseline",
    "progressing": "progressing",
    "keep_pushing": "steady",
    "build_consistency": "building_consistency",
    "plateau": "plateau",
    "deload": "deload",
}


def _code_only(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", source)


CODE = _code_only(SOURCE)


WEEKLY_DEFAULT = [
    {"start": "2026-08-28", "sessions": 3, "active": True, "volume_kg": 4200.0},
    {"start": "2026-09-04", "sessions": 3, "active": True, "volume_kg": 4600.0},
    {"start": "2026-09-11", "sessions": 3, "active": True, "volume_kg": 4900.0},
    {"start": "2026-09-18", "sessions": 3, "active": True, "volume_kg": 5100.0},
]
SERIES_DEFAULT = [
    {"day": "2026-09-03", "weight_kg": 79.4},
    {"day": "2026-09-10", "weight_kg": 79.0},
    {"day": "2026-09-17", "weight_kg": 78.4},
]


def _summary(signal="progressing", *, consistency="consistent",
             volume_trend="up", body=None, weeks=4, active=4, sessions=12,
             weekly=None):
    """A /api/progress/summary payload shaped exactly like the server's."""
    return {
        "contract_version": 1,
        "window": {"weeks": weeks, "start": "2026-08-28", "end": "2026-09-24",
                   "timezone": "Europe/Istanbul"},
        "trajectory": {"state": TRAJECTORY_BY_SIGNAL[signal], "reason": signal},
        "body": body if body is not None else {
            "status": "available", "current_weight_kg": 78.4,
            "weight_delta_kg": -0.6, "target_weight_kg": 75.0,
            "distance_to_target_kg": 3.4, "weight_series": SERIES_DEFAULT},
        "performance": {"state": PERFORMANCE_FOR_SIGNAL[signal],
                        "volume_trend": volume_trend, "strength_trend": "flat",
                        "next_signal": signal},
        "consistency": {"state": consistency, "active_weeks": active,
                        "analyzed_weeks": weeks, "sessions": sessions},
        "weekly": WEEKLY_DEFAULT if weekly is None else weekly,
    }


def _run(expression_js):
    """Load the module under node and print one JSON value."""
    script = (
        "globalThis.window = {};\n"
        + SOURCE
        + "\nvar P = window.FitXProgressPresentation;\n"
        + "process.stdout.write(JSON.stringify(" + expression_js + "));\n"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                         timeout=30, check=True)
    return json.loads(out.stdout)


def _run_tz(expression_js, tz):
    """Same as _run, under a given process timezone (the browser's)."""
    script = (
        "globalThis.window = {};\n"
        + SOURCE
        + "\nvar P = window.FitXProgressPresentation;\n"
        + "process.stdout.write(JSON.stringify(" + expression_js + "));\n"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                         timeout=30, check=True, env={"TZ": tz, "PATH": "/usr/bin:/bin"})
    return json.loads(out.stdout)


def _views(payloads, locale="en"):
    return _run("%s.map(function (d) { return P.buildSummaryView(d, {locale: %s}); })"
                % (json.dumps(payloads), json.dumps(locale)))


def _tables():
    return _run(
        "{TRAJECTORY: P.TRAJECTORY, CURRENT_STATE_SUMMARY: P.CURRENT_STATE_SUMMARY,"
        " TRAINING_STATE: P.TRAINING_STATE,"
        " TRAINING_VOLUME_AVAILABILITY: P.TRAINING_VOLUME_AVAILABILITY,"
        " CONSISTENCY_STATE: P.CONSISTENCY_STATE,"
        " CONSISTENCY_AVAILABILITY: P.CONSISTENCY_AVAILABILITY,"
        " VOLUME_TREND: P.VOLUME_TREND,"
        " CURRENT_STATE_NEXT: P.CURRENT_STATE_NEXT, VOLUME_CHANGE: P.VOLUME_CHANGE,"
        " STATE_FACT_VOLUME: P.STATE_FACT_VOLUME}")


def _descriptors(view):
    """Every copy descriptor the page would render for one view, in order.

    Visually-hidden text equivalents (viz labels, week cells) are included:
    a screen reader hears them, so they count as rendered sentences."""
    cs = view["current_state"]
    out = [cs["window"], cs["headline"], cs["summary"], *cs["evidence"],
           cs["next_action"]]
    for metric in view["metrics"].values():
        out += [metric["value"], metric["unit_label"],
                (metric["change"] or {}).get("text"), metric["note"],
                *metric["meta"]]
        viz = metric["viz"] or {}
        out.append(viz.get("label"))
        out += [c["label"] for c in viz.get("cells", [])]
    return [d for d in out if d]


def _render(desc, locale):
    """Python twin of window.t: the catalog string with {name} params filled."""
    text = _CATALOG[locale][desc["key"]]
    for name, value in (desc.get("params") or {}).items():
        text = text.replace("{%s}" % name, str(value))
    return text


# ── source guards (always run) ───────────────────────────────────────────────

def test_source_guards_run_against_real_code():
    assert "buildSummaryView" in CODE
    assert "FitXProgressPresentation" in CODE


def test_every_key_the_model_names_exists_in_both_catalogs():
    """The repo's i18n gate scans t('...') calls; this module names keys as
    data, so it gets its own existence check."""
    keys = set(re.findall(r"'(progress\.[a-z0-9_]+)'", CODE))
    assert len(keys) > 20       # non-vacuous: the tables are really scanned
    for key in keys:
        for locale in LOCALES:
            assert _CATALOG[locale].get(key), f"{key} missing from {locale}"


@pytest.mark.parametrize("forbidden", [
    "fetch(", "XMLHttpRequest", "document.", "localStorage", "sessionStorage",
    "setTimeout", "setInterval", "Date.now", "new Date",
])
def test_model_is_pure(forbidden):
    """No I/O, no DOM, no clock: the model cannot add a request or a query."""
    assert forbidden not in CODE


def test_no_string_surgery_turns_an_identifier_into_copy():
    """Copy comes from the tables, never from reshaping an identifier."""
    for forbidden in (".replace(", "toUpperCase", "toLowerCase", ".split("):
        assert forbidden not in CODE, forbidden
    # The controller escapes coach feedback with .replace (verbatim check-in
    # flow), so for it only the identifier-prettifying shapes are banned.
    for code in (CODE, _code_only(CONTROLLER)):
        assert not re.search(r"""replace\(\s*(/_/|['"]_['"])""", code)
        assert "toUpperCase" not in code


def test_page_fetches_the_summary_exactly_once():
    """One canonical summary request feeds Current State AND all three Trends
    cards; the refactor must not add a second read path for either."""
    code = _code_only(CONTROLLER)
    assert code.count("'/api/progress/summary'") == 1
    assert code.count("_getJSON(") == 2   # its definition + the one summary call


# ── A. copy mapping ─────────────────────────────────────────────────────────

@requires_node
def test_tables_cover_every_server_state_exactly():
    t = _tables()
    assert set(t["TRAJECTORY"]) == set(TRAJECTORY_STATES)
    assert set(t["CURRENT_STATE_SUMMARY"]) == set(TRAJECTORY_STATES)
    assert set(t["TRAINING_STATE"]) == set(PERFORMANCE_STATES)
    assert set(t["TRAINING_VOLUME_AVAILABILITY"]) == set(PERFORMANCE_STATES)
    assert set(t["CONSISTENCY_STATE"]) == set(CONSISTENCY_STATES)
    assert set(t["CONSISTENCY_AVAILABILITY"]) == set(CONSISTENCY_STATES)
    assert set(t["VOLUME_TREND"]) == {"up", "flat", "down"}
    assert set(t["VOLUME_CHANGE"]) == set(t["STATE_FACT_VOLUME"]) == {"up", "flat", "down"}
    assert set(t["CURRENT_STATE_NEXT"]) == set(TRAJECTORY_STATES)


@requires_node
def test_every_mapped_key_exists_and_reads_as_language_not_an_enum():
    """Internal identifiers stay internal: no copy is a snake_case identifier
    or an identifier shouted in capitals ("NEEDS CONSISTENCY"). Single words
    such as "Plateau" or an inline "steady" are ordinary language."""
    identifiers = (set(TRAJECTORY_STATES) | set(PERFORMANCE_STATES)
                   | set(CONSISTENCY_STATES) | set(TRAJECTORY_BY_SIGNAL))
    shouted = {i.replace("_", " ").upper() for i in identifiers}
    t = _tables()
    for name, table in t.items():
        if name.endswith("AVAILABILITY"):
            continue
        for key in table.values():
            for locale in LOCALES:
                copy = _CATALOG[locale].get(key)
                assert copy, f"{key} missing from {locale}"
                assert "_" not in copy, (locale, key, copy)
                assert copy.upper() != copy, (locale, key, copy)   # not SHOUTED
                assert copy not in shouted, (locale, key, copy)


@requires_node
@pytest.mark.parametrize("signal", sorted(TRAJECTORY_BY_SIGNAL))
def test_no_internal_identifier_reaches_rendered_copy(signal):
    view = _views([_summary(signal)])[0]
    identifiers = (set(TRAJECTORY_STATES) | set(PERFORMANCE_STATES)
                   | set(CONSISTENCY_STATES) | set(TRAJECTORY_BY_SIGNAL))
    for desc in _descriptors(view):
        assert desc["key"].startswith("progress.")
        for value in (desc.get("params") or {}).values():
            assert value not in identifiers
        for locale in LOCALES:
            rendered = _render(desc, locale)
            assert "{" not in rendered, rendered          # every param filled
            assert rendered not in identifiers
            assert "_" not in rendered


@requires_node
def test_known_states_map_to_the_intended_copy():
    views = _views([_summary(s) for s in
                    ("insufficient_data", "progressing", "build_consistency")])
    heads = [v["current_state"]["headline"]["key"] for v in views]
    assert heads == ["progress.traj_building_baseline", "progress.traj_on_track",
                     "progress.traj_needs_attention"]
    assert _CATALOG["en"]["progress.traj_building_baseline"] == "Building your baseline"
    assert _CATALOG["en"]["progress.traj_on_track"] == "On track"
    assert _CATALOG["en"]["progress.cons_state_inconsistent"] == "Less consistent"
    assert views[2]["current_state"]["state"] == "needs_attention"   # accent only


@requires_node
@pytest.mark.parametrize("payload", [
    None, {}, "nope", {"trajectory": None},
])
def test_non_summary_payload_is_unavailable_never_building_baseline(payload):
    view = _views([payload])[0]
    cs = view["current_state"]
    assert cs["status"] == "unavailable"
    assert cs["state"] is None
    assert cs["headline"]["key"] == "progress.traj_unavailable"
    assert cs["summary"]["key"] == "progress.load_error"
    for metric in view["metrics"].values():
        assert metric["status"] == "unavailable"
        assert metric["value"] is None
    assert "building_baseline" not in json.dumps(view)


@requires_node
def test_unknown_values_degrade_per_section_without_guessing():
    d = _summary("progressing")
    d["trajectory"]["state"] = "crushing_it"
    d["performance"]["state"] = "constructor"      # inherited-property probe
    d["consistency"]["state"] = "__proto__"
    view = _views([d])[0]
    assert view["current_state"]["status"] == "unavailable"
    assert view["metrics"]["training_volume"]["status"] == "unavailable"
    assert view["metrics"]["consistency"]["status"] == "unavailable"
    # Weight did not depend on any of them and still renders.
    assert view["metrics"]["weight"]["status"] == "available"

    d = _summary("progressing", volume_trend="sideways")
    vol = _views([d])[0]["metrics"]["training_volume"]
    assert vol["status"] == "unavailable" and vol["value"] is None


# ── B. metric contract ──────────────────────────────────────────────────────

@requires_node
def test_weight_metric_contract():
    w = _views([_summary()])[0]["metrics"]["weight"]
    assert w["status"] == "available" and w["unit"] == "kg"
    assert w["current"] == 78.4 and w["delta"] == -0.6
    assert w["comparison_period"] == "previous_checkin"
    assert w["distance_to_target"] == 3.4
    assert w["value"] == {"key": "progress.metric_weight_value", "params": {"value": "78.4"}}
    assert _render(w["value"], "en") == "78.4 kg"
    # Change: the server's delta, signed (typographic minus, V2 PR5), with a
    # direction taken from its sign.
    assert w["change"] == {"text": {"key": "progress.body_sub_delta",
                                    "params": {"delta": "−0.6"}},
                           "direction": "down"}
    # Three real check-ins → a sparkline over exactly those points.
    assert w["series_points"] == 3
    assert w["viz"]["kind"] == "line" and len(w["viz"]["points"]) == 3
    assert _render(w["viz"]["label"], "en") == (
        "Weight over your last 3 check-ins, from 79.4 kg to 78.4 kg.")
    assert w["note"] is None
    assert [m["key"] for m in w["meta"]] == ["progress.metric_weight_period",
                                            "progress.body_sub_target"]


@requires_node
def test_weight_sparkline_preserves_chronology_and_direction():
    """Oldest point on the left; a falling weight ends lower (larger y)."""
    w = _views([_summary()])[0]["metrics"]["weight"]
    xs = [p["x"] for p in w["viz"]["points"]]
    ys = [p["y"] for p in w["viz"]["points"]]
    assert xs == sorted(xs) and len(set(xs)) == 3
    assert ys[0] < ys[1] < ys[2]           # 79.4 → 79.0 → 78.4: descending line
    for p in w["viz"]["points"]:
        assert 0 <= p["x"] <= w["viz"]["width"] and 0 <= p["y"] <= w["viz"]["height"]


@requires_node
def test_weight_sparse_and_missing_states_are_safe():
    one_checkin = {"status": "partial", "current_weight_kg": 80.0,
                   "weight_delta_kg": None, "target_weight_kg": None,
                   "distance_to_target_kg": None,
                   "weight_series": [{"day": "2026-09-17", "weight_kg": 80.0}]}
    with_target = dict(one_checkin, target_weight_kg=75.0, distance_to_target_kg=5.0)
    no_weight = {"status": "insufficient_data", "current_weight_kg": None,
                 "weight_delta_kg": None, "target_weight_kg": None,
                 "distance_to_target_kg": None, "weight_series": []}
    two_flat = dict(one_checkin, status="available", weight_delta_kg=0.0,
                    weight_series=[{"day": "2026-09-10", "weight_kg": 80.0},
                                   {"day": "2026-09-17", "weight_kg": 80.0}])
    legacy = {k: v for k, v in one_checkin.items() if k != "weight_series"}
    views = _views([_summary(body=b) for b in
                    (one_checkin, with_target, no_weight, two_flat, legacy)])
    w = [v["metrics"]["weight"] for v in views]

    # One check-in: value, no change, no line, explicit note.
    assert w[0]["status"] == "partial" and w[0]["delta"] is None
    assert w[0]["comparison_period"] is None and w[0]["change"] is None
    assert w[0]["viz"] is None
    assert w[0]["note"]["key"] == "progress.metric_weight_no_trend"
    assert w[1]["meta"] == [{"key": "progress.body_sub_target",
                             "params": {"distance": "5.0"}}]
    # No weight is not zero weight.
    assert w[2]["status"] == "insufficient_data"
    assert w[2]["current"] is None and w[2]["value"] is None
    assert w[2]["change"] is None and w[2]["viz"] is None
    assert w[2]["note"]["key"] == "progress.body_sub_none"
    # Two points: a real "no change" delta, but NOT a drawn trend line.
    assert w[3]["change"] == {"text": {"key": "progress.body_sub_flat", "params": None},
                              "direction": "flat"}
    assert w[3]["viz"] is None
    assert w[3]["note"]["key"] == "progress.metric_weight_no_trend"
    # A pre-PR2 payload without a series degrades to the note, never a line.
    assert w[4]["viz"] is None and w[4]["value"]["params"] == {"value": "80.0"}


@requires_node
def test_training_volume_metric_contract():
    views = _views([_summary("progressing", volume_trend="up"),
                    _summary("plateau", volume_trend="flat"),
                    _summary("deload", volume_trend="down")])
    vols = [v["metrics"]["training_volume"] for v in views]
    up = vols[0]
    assert up["status"] == "available" and up["trend"] == "up"
    assert up["latest_kg"] == 5100.0                    # newest trailing week
    assert _render(up["value"], "en") == "5.1K kg"
    assert up["change"] == {"text": {"key": "progress.metric_volume_change_up",
                                     "params": {"weeks": 4}},
                            "direction": "up"}
    assert _render(up["change"]["text"], "en") == "Rising across 4 weeks"
    assert [m["key"] for m in up["meta"]] == ["progress.metric_volume_latest"]
    assert up["viz"]["kind"] == "bars" and len(up["viz"]["bars"]) == 4
    assert _render(up["viz"]["label"], "en") == (
        "Weekly training volume in kg, oldest to newest: 4.2K, 4.6K, 4.9K, 5.1K.")
    assert [v["change"]["direction"] for v in vols] == ["up", "flat", "down"]


@requires_node
def test_volume_bars_are_proportional_and_keep_zero_weeks():
    weekly = [dict(w) for w in WEEKLY_DEFAULT]
    weekly[1] = dict(weekly[1], volume_kg=0.0, sessions=0, active=False)
    vol = _views([_summary(weekly=weekly, active=3)])[0]["metrics"]["training_volume"]
    bars = vol["viz"]["bars"]
    assert [b["zero"] for b in bars] == [False, True, False, False]
    assert bars[1]["height"] == 0
    tallest = max(bars, key=lambda b: b["height"])
    assert tallest is bars[3]                           # 5100 is the max
    assert bars[0]["height"] < bars[2]["height"] < bars[3]["height"]


@requires_node
def test_volume_is_formatted_in_the_display_locale():
    tr = _views([_summary()], locale="tr")[0]["metrics"]["training_volume"]
    assert tr["value"]["params"]["value"] != "5.1K"     # Turkish compact form
    assert "5,1" in tr["value"]["params"]["value"]


@requires_node
def test_training_volume_without_history_is_insufficient_not_steady():
    """The canonical trend is 'flat' when there is nothing to compare; a new
    user must read 'not enough data', never 'Steady' — and no bars."""
    zero_weeks = [dict(w, sessions=0, active=False, volume_kg=0.0) for w in WEEKLY_DEFAULT]
    vol = _views([_summary("insufficient_data", volume_trend="flat",
                           consistency="insufficient_data", active=0,
                           sessions=0, weekly=zero_weeks)])[0]["metrics"]["training_volume"]
    assert vol["status"] == "insufficient_data"
    assert vol["trend"] is None and vol["change"] is None and vol["viz"] is None
    assert vol["note"]["key"] == "progress.metric_volume_insufficient"
    # The measured zero of the last seven days is real and may be shown.
    assert vol["latest_kg"] == 0.0


@requires_node
def test_training_volume_semantic_fallback_without_a_series():
    """Only the canonical direction is known: render it, invent no number."""
    d = _summary("progressing")
    del d["weekly"]
    vol = _views([d])[0]["metrics"]["training_volume"]
    assert vol["status"] == "available" and vol["latest_kg"] is None
    assert vol["value"] == {"key": "progress.metric_volume_up", "params": None}
    assert vol["viz"] is None and vol["change"] is None


@requires_node
def test_malformed_weekly_series_is_ignored_not_guessed():
    for bad in ([{"start": "x", "sessions": 1, "active": "yes", "volume_kg": 1.0}],
                [{"start": "x", "sessions": 1, "active": True, "volume_kg": None}],
                "nope"):
        view = _views([_summary(weekly=bad)])[0]
        assert view["metrics"]["training_volume"]["viz"] is None
        assert view["metrics"]["consistency"]["viz"] is None


@requires_node
def test_consistency_metric_contract():
    weekly = [dict(w) for w in WEEKLY_DEFAULT]
    weekly[0] = dict(weekly[0], sessions=0, active=False, volume_kg=0.0)
    weekly[2] = dict(weekly[2], sessions=0, active=False, volume_kg=0.0)
    c = _views([_summary("build_consistency", consistency="inconsistent",
                         active=2, sessions=5, weekly=weekly)])[0]["metrics"]["consistency"]
    assert c["status"] == "available" and c["state"] == "inconsistent"
    assert (c["active_weeks"], c["total_weeks"], c["session_count"]) == (2, 4, 5)
    assert c["comparison_period"] == {"weeks": 4}
    assert _render(c["value"], "en") == "2 / 4"
    assert _render(c["unit_label"], "en") == "weeks active"
    assert c["change"] == {"text": {"key": "progress.cons_state_inconsistent",
                                    "params": None}, "direction": None}
    assert [_render(m, "en") for m in c["meta"]] == ["Sessions logged in 4 weeks: 5"]
    cells = c["viz"]["cells"]
    assert [cell["active"] for cell in cells] == [False, True, False, True]
    assert [_render(cell["label"], "en") for cell in cells] == [
        "Week 1: no sessions", "Week 2: trained",
        "Week 3: no sessions", "Week 4: trained"]
    assert _render(c["viz"]["label"], "en") == "Weeks, oldest first"


@requires_node
def test_consistency_cells_never_contradict_the_counts():
    """A series that does not cover exactly the counted weeks draws nothing."""
    c = _views([_summary(weekly=WEEKLY_DEFAULT[:3])])[0]["metrics"]["consistency"]
    assert c["viz"] is None
    assert _render(c["value"], "en") == "4 / 4"


@requires_node
def test_empty_user_consistency_keeps_real_zeroes():
    """Zero sessions in four weeks is a measured fact, not missing data."""
    zero_weeks = [dict(w, sessions=0, active=False, volume_kg=0.0) for w in WEEKLY_DEFAULT]
    c = _views([_summary("insufficient_data", consistency="insufficient_data",
                         active=0, sessions=0, weekly=zero_weeks)])[0]["metrics"]["consistency"]
    assert c["status"] == "insufficient_data"
    assert c["active_weeks"] == 0 and c["session_count"] == 0
    assert c["value"]["params"] == {"active": 0, "total": 4}
    assert [cell["active"] for cell in c["viz"]["cells"]] == [False] * 4


@requires_node
def test_missing_counts_are_dropped_not_faked():
    d = _summary("progressing")
    d["consistency"] = {"state": "consistent"}
    d["window"] = {}
    view = _views([d])[0]
    c = view["metrics"]["consistency"]
    assert c["active_weeks"] is None and c["session_count"] is None
    assert c["comparison_period"] is None and c["meta"] == []
    # Without counts the value is the state label, and no cells are drawn.
    assert c["value"]["key"] == "progress.cons_state_consistent"
    assert c["unit_label"] is None and c["change"] is None and c["viz"] is None
    cs = view["current_state"]
    assert cs["window"] is None
    assert [e["key"] for e in cs["evidence"]] == ["progress.state_fact_volume_up"]
    assert view["metrics"]["training_volume"]["change"] is None


# ── D. Current State: state → evidence → action ─────────────────────────────

@requires_node
def test_current_state_follows_state_evidence_action():
    cs = _views([_summary("build_consistency", consistency="inconsistent",
                          volume_trend="flat", active=2, sessions=5)])[0]["current_state"]
    assert _render(cs["window"], "en") == "Your last 4 weeks"
    assert _render(cs["headline"], "en") == "Needs attention"
    assert [_render(e, "en") for e in cs["evidence"]] == [
        "Trained in 2 of the last 4 weeks",
        "Weekly training volume is holding steady"]
    assert cs["next_action"]["key"] == "progress.state_next_needs_attention"


@requires_node
@pytest.mark.parametrize("signal", sorted(TRAJECTORY_BY_SIGNAL))
def test_current_state_evidence_is_bounded_and_measured(signal):
    cs = _views([_summary(signal)])[0]["current_state"]
    assert len(cs["evidence"]) <= 2
    keys = [e["key"] for e in cs["evidence"]]
    assert len(keys) == len(set(keys))
    # Evidence is measured facts only — never a state label or a signal name.
    for key in keys:
        assert key.startswith("progress.state_fact_")
    assert cs["next_action"] is not None


@requires_node
def test_brand_new_user_current_state_has_no_volume_claim():
    """building_baseline: the volume direction is not evidence yet."""
    zero_weeks = [dict(w, sessions=0, active=False, volume_kg=0.0) for w in WEEKLY_DEFAULT]
    cs = _views([_summary("insufficient_data", consistency="insufficient_data",
                          volume_trend="flat", active=0, sessions=0,
                          weekly=zero_weeks)])[0]["current_state"]
    assert [e["key"] for e in cs["evidence"]] == ["progress.state_fact_weeks"]
    assert cs["next_action"]["key"] == "progress.state_next_building_baseline"


@requires_node
def test_unavailable_current_state_has_no_evidence_or_action():
    cs = _views([None])[0]["current_state"]
    assert cs["evidence"] == [] and cs["next_action"] is None and cs["window"] is None


@requires_node
def test_geometry_helpers_refuse_to_draw_what_is_not_a_series():
    out = _run("[P.sparkline([80, 79]), P.sparkline([80, null, 79]),"
               " P.bars([0, 0, 0]), P.bars([1, -1]), P.bars([]),"
               " P.sparkline([80, 80, 80]).points.map(function (p) { return p.y; })]")
    assert out[:5] == [None, None, None, None, None]
    assert len(set(out[5])) == 1          # equal real values: a level line


# ── C. deduplication ────────────────────────────────────────────────────────

@requires_node
@pytest.mark.parametrize("signal", sorted(TRAJECTORY_BY_SIGNAL))
def test_one_summary_never_renders_the_same_sentence_twice(signal):
    consistency = {"insufficient_data": "insufficient_data",
                   "build_consistency": "inconsistent"}.get(signal, "consistent")
    view = _views([_summary(signal, consistency=consistency)])[0]
    descriptors = _descriptors(view)
    # The "{value} kg" template legitimately serves weight AND volume values
    # with different numbers; compare the rendered sentences, not keys.
    for locale in LOCALES:
        rendered = [_render(d, locale) for d in descriptors]
        assert len(rendered) == len(set(rendered)), (locale, rendered)


@requires_node
@pytest.mark.parametrize("signal", sorted(TRAJECTORY_BY_SIGNAL))
def test_consistency_state_has_exactly_one_rendering(signal):
    """Before V2 PR1 `build_consistency` put the consistency state on screen
    five times (headline meta, lede, Performance card, Consistency card, Axis
    WATCH). The state label still renders once, on its own Trends card — the
    Current State evidence is the measured count, not the label."""
    consistency = {"insufficient_data": "insufficient_data",
                   "build_consistency": "inconsistent"}.get(signal, "consistent")
    view = _views([_summary(signal, consistency=consistency)])[0]
    keys = [d["key"] for d in _descriptors(view)]
    cons_keys = [k for k in keys if k.startswith("progress.cons_state_")]
    assert cons_keys == ["progress.cons_state_%s" % consistency]
    assert view["metrics"]["consistency"]["change"]["text"]["key"] == cons_keys[0]
    # No other section carries a training-state label for the same signal.
    assert not [k for k in keys if k.startswith("progress.perf_state_")]


@requires_node
def test_current_state_never_repeats_an_axis_insight_sentence():
    """Current State summarises; Axis Insight interprets. The retired
    per-signal ledes restated the Axis headline verbatim."""
    views = _views([_summary(s) for s in sorted(TRAJECTORY_BY_SIGNAL)])
    for locale in LOCALES:
        axis = {v for k, v in _CATALOG[locale].items()
                if k.startswith("progress.axis_")}
        for view in views:
            cs = view["current_state"]
            for desc in (cs["window"], cs["headline"], cs["summary"],
                         *cs["evidence"], cs["next_action"]):
                assert _render(desc, locale) not in axis


@requires_node
def test_current_state_and_trends_share_no_sentence():
    for signal in sorted(TRAJECTORY_BY_SIGNAL):
        view = _views([_summary(signal)])[0]
        cs = view["current_state"]
        top = [cs["window"], cs["headline"], cs["summary"], *cs["evidence"],
               cs["next_action"]]
        trends = _descriptors({"current_state": {
            "window": None, "headline": None, "summary": None, "evidence": [],
            "next_action": None}, "metrics": view["metrics"]})
        for locale in LOCALES:
            a = {_render(d, locale) for d in top if d}
            b = {_render(d, locale) for d in trends}
            assert not a & b, (signal, locale, a & b)


@requires_node
def test_current_state_is_trajectory_level_not_signal_level():
    """Three signals share needs_attention; given the same measured facts,
    Current State says the same thing for all of them — which one it is, is
    Axis Insight's job."""
    views = _views([_summary(s) for s in ("build_consistency", "plateau", "deload")])
    states = {json.dumps(v["current_state"], sort_keys=True) for v in views}
    assert len(states) == 1


# ── F. V2 PR4 Recent Check-ins view model ───────────────────────────────────

def _entry(day, checked_in_at, perf="steady", traj="on_track", weight=78.0, delta=None):
    return {
        "checked_in_at": checked_in_at,
        "analysis_day": day,
        "window": {"weeks": 4, "start": "2026-06-18", "end": day,
                   "timezone": "Europe/Istanbul"},
        "trajectory": {"state": traj, "reason": "keep_pushing"},
        "performance": {"state": perf, "volume_trend": "flat"},
        "consistency": {"state": "consistent", "sessions": 8,
                        "active_weeks": 4, "analyzed_weeks": 4},
        "body": {"weight_kg": weight, "weight_delta_kg": delta},
    }


def _history(entries, has_more=False, state="available"):
    return {"contract_version": 1, "state": state, "entries": entries,
            "has_more": has_more}


def _hview(payload):
    return _run("P.buildHistoryView(%s)" % json.dumps(payload))


@requires_node
def test_history_groups_same_day_records_without_dropping_any():
    payload = _history([
        _entry("2026-07-27", "2026-07-27T21:10:00+03:00", weight=78.0, delta=-0.4),
        _entry("2026-07-27", "2026-07-27T08:05:00+03:00", weight=78.4, delta=0.0),
        _entry("2026-07-24", "2026-07-24T09:00:00+03:00", perf="building_baseline",
               traj="building_baseline", weight=76.0),
    ])
    view = _hview(payload)
    assert view["status"] == "available"
    assert [g["day"] for g in view["groups"]] == ["2026-07-27", "2026-07-24"]
    same_day, single = view["groups"]
    assert same_day["count"] == 2 and len(same_day["entries"]) == 2
    assert sum(g["count"] for g in view["groups"]) == 3   # nothing lost
    assert same_day["updates"] == {"key": "progress.history_updates", "params": {"n": 2}}
    assert single["updates"] is None
    # The group leads with the NEWEST check-in, as served.
    assert same_day["weight"]["params"] == {"weight": "78.0"}
    assert same_day["delta"] == {"key": "progress.history_delta", "params": {"delta": "−0.4"}}
    assert [e["weight"]["params"]["weight"] for e in same_day["entries"]] == ["78.0", "78.4"]
    assert same_day["entries"][1]["delta"] == {"key": "progress.history_no_change",
                                              "params": None}
    assert single["delta"] is None


@requires_node
def test_history_row_has_one_state_label_from_the_shared_table():
    t = _tables()
    for perf in PERFORMANCE_STATES:
        view = _hview(_history([_entry("2026-07-27", "2026-07-27T10:00:00+03:00",
                                       perf=perf, traj="needs_attention")]))
        group = view["groups"][0]
        assert group["summary"] == {"key": t["TRAINING_STATE"][perf], "params": None}
        # One label per row: no second state descriptor on the group.
        labels = [v for k, v in group.items()
                  if isinstance(v, dict) and str(v.get("key", "")).startswith(
                      ("progress.traj_", "progress.perf_state_", "progress.cons_state_"))]
        assert labels == [group["summary"]]
    # Unknown performance → the shared trajectory label, never an identifier.
    view = _hview(_history([_entry("2026-07-27", "2026-07-27T10:00:00+03:00",
                                   perf="mystery", traj="on_track")]))
    assert view["groups"][0]["summary"]["key"] == t["TRAJECTORY"]["on_track"]
    view = _hview(_history([_entry("2026-07-27", "2026-07-27T10:00:00+03:00",
                                   perf="mystery", traj="mystery")]))
    assert view["groups"][0]["summary"] is None


@requires_node
def test_history_visible_set_is_bounded_and_the_rest_is_kept():
    days = ["2026-07-%02d" % d for d in (27, 26, 25, 24, 23, 22)]
    view = _hview(_history([_entry(d, d + "T10:00:00+03:00") for d in days],
                           has_more=True))
    bound = _run("P.HISTORY_VISIBLE_GROUPS")
    assert 3 <= bound <= 5
    assert view["visible"] == bound
    assert len(view["groups"]) == 6            # the rest stays available
    assert view["has_more"] is True
    few = _hview(_history([_entry("2026-07-27", "2026-07-27T10:00:00+03:00")]))
    assert few["visible"] == 1 and few["has_more"] is False


@requires_node
def test_history_empty_is_not_failure_and_failure_is_not_empty():
    assert _hview(_history([], state="empty"))["status"] == "empty"
    for broken in (None, {}, {"state": "available"}, {"state": "exploded"},
                   {"state": "available", "entries": "nope"}, "html"):
        view = _run("P.buildHistoryView(%s)" % json.dumps(broken))
        assert view["status"] == "unavailable", broken
        assert view["groups"] == []


@requires_node
def test_history_grouping_ignores_the_browser_timezone():
    """A check-in at 23:30 Istanbul (20:30 UTC) belongs to the Istanbul day
    the server published, whatever timezone the browser runs in."""
    payload = _history([
        _entry("2026-07-28", "2026-07-28T00:30:00+03:00"),
        _entry("2026-07-27", "2026-07-27T23:30:00+03:00"),
        _entry("2026-07-27", "2026-07-27T00:15:00+03:00"),
    ])
    expr = "P.buildHistoryView(%s)" % json.dumps(payload)
    views = [_run_tz(expr, tz) for tz in
             ("UTC", "America/Los_Angeles", "Pacific/Kiritimati", "Europe/Istanbul")]
    assert all(v == views[0] for v in views)
    assert [(g["day"], g["count"]) for g in views[0]["groups"]] == [
        ("2026-07-28", 1), ("2026-07-27", 2)]


@requires_node
def test_history_unreadable_rows_are_skipped_not_guessed():
    payload = _history([
        _entry("2026-07-27", "2026-07-27T10:00:00+03:00"),
        {"analysis_day": "27.07"},
        None,
    ])
    view = _hview(payload)
    assert [g["day"] for g in view["groups"]] == ["2026-07-27"]
