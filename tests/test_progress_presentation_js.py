"""Progress V2 PR1 — the Progress presentation model (static/progress_presentation.js).

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
E. performance — the model performs no I/O, and the page still fetches the
   summary exactly once.

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


def _summary(signal="progressing", *, consistency="consistent",
             volume_trend="up", body=None, weeks=4, active=4, sessions=12):
    """A /api/progress/summary payload shaped exactly like the server's."""
    return {
        "contract_version": 1,
        "window": {"weeks": weeks, "start": "2026-08-28", "end": "2026-09-24",
                   "timezone": "Europe/Istanbul"},
        "trajectory": {"state": TRAJECTORY_BY_SIGNAL[signal], "reason": signal},
        "body": body if body is not None else {
            "status": "available", "current_weight_kg": 78.4,
            "weight_delta_kg": -0.6, "target_weight_kg": 75.0,
            "distance_to_target_kg": 3.4},
        "performance": {"state": PERFORMANCE_FOR_SIGNAL[signal],
                        "volume_trend": volume_trend, "strength_trend": "flat",
                        "next_signal": signal},
        "consistency": {"state": consistency, "active_weeks": active,
                        "analyzed_weeks": weeks, "sessions": sessions},
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


def _views(payloads):
    return _run("%s.map(function (d) { return P.buildSummaryView(d); })"
                % json.dumps(payloads))


def _tables():
    return _run(
        "{TRAJECTORY: P.TRAJECTORY, CURRENT_STATE_SUMMARY: P.CURRENT_STATE_SUMMARY,"
        " TRAINING_STATE: P.TRAINING_STATE,"
        " TRAINING_VOLUME_AVAILABILITY: P.TRAINING_VOLUME_AVAILABILITY,"
        " CONSISTENCY_STATE: P.CONSISTENCY_STATE,"
        " CONSISTENCY_AVAILABILITY: P.CONSISTENCY_AVAILABILITY,"
        " VOLUME_TREND: P.VOLUME_TREND, TREND_INLINE: P.TREND_INLINE}")


def _descriptors(view):
    """Every copy descriptor the page would render for one view, in order."""
    cs = view["current_state"]
    out = [cs["headline"], cs["summary"], cs["evidence"]]
    for metric in view["metrics"].values():
        out += [metric["value"], metric["detail"]]
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
    assert set(t["VOLUME_TREND"]) == set(t["TREND_INLINE"]) == {"up", "flat", "down"}


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
    assert w == {
        "metric": "weight", "status": "available", "unit": "kg",
        "current": 78.4, "delta": -0.6, "comparison_period": "previous_checkin",
        "distance_to_target": 3.4,
        "value": {"key": "progress.metric_weight_value", "params": {"value": "78.4"}},
        "detail": {"key": "progress.body_sub_delta", "params": {"delta": "-0.6"}},
    }
    assert _render(w["value"], "en") == "78.4 kg"


@requires_node
def test_weight_sparse_and_missing_states_are_safe():
    one_checkin = {"status": "partial", "current_weight_kg": 80.0,
                   "weight_delta_kg": None, "target_weight_kg": None,
                   "distance_to_target_kg": None}
    with_target = dict(one_checkin, target_weight_kg=75.0, distance_to_target_kg=5.0)
    no_weight = {"status": "insufficient_data", "current_weight_kg": None,
                 "weight_delta_kg": None, "target_weight_kg": None,
                 "distance_to_target_kg": None}
    flat = dict(one_checkin, status="available", weight_delta_kg=0.0)
    views = _views([_summary(body=b) for b in
                    (one_checkin, with_target, no_weight, flat)])
    w = [v["metrics"]["weight"] for v in views]

    assert w[0]["status"] == "partial" and w[0]["delta"] is None
    assert w[0]["comparison_period"] is None
    assert w[0]["detail"]["key"] == "progress.body_sub_partial"
    assert w[1]["detail"] == {"key": "progress.body_sub_target",
                              "params": {"distance": "5.0"}}
    # No weight is not zero weight.
    assert w[2]["status"] == "insufficient_data"
    assert w[2]["current"] is None and w[2]["value"] is None
    assert w[2]["detail"]["key"] == "progress.body_sub_none"
    assert w[3]["detail"]["key"] == "progress.body_sub_flat"


@requires_node
def test_training_volume_metric_contract():
    views = _views([_summary("progressing", volume_trend="up"),
                    _summary("plateau", volume_trend="flat"),
                    _summary("deload", volume_trend="down")])
    vols = [v["metrics"]["training_volume"] for v in views]
    assert vols[0] == {
        "metric": "training_volume", "status": "available", "trend": "up",
        "comparison_period": {"weeks": 4},
        "value": {"key": "progress.metric_volume_up", "params": None},
        "detail": {"key": "progress.metric_volume_period", "params": {"weeks": 4}},
    }
    assert [v["value"]["key"] for v in vols] == [
        "progress.metric_volume_up", "progress.metric_volume_flat",
        "progress.metric_volume_down"]


@requires_node
def test_training_volume_without_history_is_insufficient_not_steady():
    """The canonical trend is 'flat' when there is nothing to compare; a new
    user must read 'not enough data', never 'Steady'."""
    vol = _views([_summary("insufficient_data", volume_trend="flat",
                           consistency="insufficient_data", active=0,
                           sessions=0)])[0]["metrics"]["training_volume"]
    assert vol["status"] == "insufficient_data"
    assert vol["trend"] is None and vol["value"] is None
    assert vol["detail"]["key"] == "progress.metric_volume_insufficient"


@requires_node
def test_consistency_metric_contract():
    c = _views([_summary("build_consistency", consistency="inconsistent",
                         active=2, sessions=5)])[0]["metrics"]["consistency"]
    assert c == {
        "metric": "consistency", "status": "available", "state": "inconsistent",
        "active_weeks": 2, "total_weeks": 4, "session_count": 5,
        "comparison_period": {"weeks": 4},
        "value": {"key": "progress.cons_state_inconsistent", "params": None},
        "detail": {"key": "progress.metric_consistency_detail",
                   "params": {"active": 2, "total": 4, "n": 5}},
    }
    assert _render(c["detail"], "en") == "2 of the last 4 weeks active · 5 sessions"


@requires_node
def test_empty_user_consistency_keeps_real_zeroes():
    """Zero sessions in four weeks is a measured fact, not missing data."""
    c = _views([_summary("insufficient_data", consistency="insufficient_data",
                         active=0, sessions=0)])[0]["metrics"]["consistency"]
    assert c["status"] == "insufficient_data"
    assert c["active_weeks"] == 0 and c["session_count"] == 0
    assert c["detail"]["params"] == {"active": 0, "total": 4, "n": 0}


@requires_node
def test_missing_counts_are_dropped_not_faked():
    d = _summary("progressing")
    d["consistency"] = {"state": "consistent"}
    d["window"] = {}
    view = _views([d])[0]
    c = view["metrics"]["consistency"]
    assert c["active_weeks"] is None and c["session_count"] is None
    assert c["detail"] is None and c["comparison_period"] is None
    assert view["current_state"]["evidence"] is None
    assert view["metrics"]["training_volume"]["detail"] is None


# ── C. deduplication ────────────────────────────────────────────────────────

@requires_node
@pytest.mark.parametrize("signal", sorted(TRAJECTORY_BY_SIGNAL))
def test_one_summary_never_renders_the_same_sentence_twice(signal):
    consistency = {"insufficient_data": "insufficient_data",
                   "build_consistency": "inconsistent"}.get(signal, "consistent")
    view = _views([_summary(signal, consistency=consistency)])[0]
    descriptors = _descriptors(view)
    keys = [d["key"] for d in descriptors]
    assert len(keys) == len(set(keys)), keys
    for locale in LOCALES:
        rendered = [_render(d, locale) for d in descriptors]
        assert len(rendered) == len(set(rendered)), (locale, rendered)


@requires_node
@pytest.mark.parametrize("signal", sorted(TRAJECTORY_BY_SIGNAL))
def test_consistency_state_has_exactly_one_rendering(signal):
    """Before V2 PR1 `build_consistency` put the consistency state on screen
    five times (headline meta, lede, Performance card, Consistency card, Axis
    WATCH). The state now renders once, on its own Trends card."""
    consistency = {"insufficient_data": "insufficient_data",
                   "build_consistency": "inconsistent"}.get(signal, "consistent")
    view = _views([_summary(signal, consistency=consistency)])[0]
    keys = [d["key"] for d in _descriptors(view)]
    cons_keys = [k for k in keys if k.startswith("progress.cons_state_")]
    assert cons_keys == ["progress.cons_state_%s" % consistency]
    assert view["metrics"]["consistency"]["value"]["key"] == cons_keys[0]
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
            for desc in (cs["headline"], cs["summary"], cs["evidence"]):
                assert _render(desc, locale) not in axis


@requires_node
def test_current_state_is_trajectory_level_not_signal_level():
    """Three signals share needs_attention; Current State says the same thing
    for all of them — which one it is, is Axis Insight's job."""
    views = _views([_summary(s) for s in ("build_consistency", "plateau", "deload")])
    states = {json.dumps(v["current_state"], sort_keys=True) for v in views}
    assert len(states) == 1
