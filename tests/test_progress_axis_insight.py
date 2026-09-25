"""Progress V2 PR3 — the unified Axis Insight surface.

A. Semantic contract (server): one interpretation keyed on the planner's
   canonical ``week_focus``; at most two evidence facts, every one grounded in
   a canonical count / trend / rule constant; exactly one action, identical to
   NEXT MOVE's projection; a sparse user gets no invented fact.
B. View model (node): ``buildAxisInsightView`` maps the REAL server payload
   (generated through ``progress_insights_payload``) to copy descriptors and
   fails closed on anything it cannot name.
C. Deduplication: no rendered Axis sentence equals a Current State or Trends
   sentence, for any canonical signal, in either locale.
D. Coach handoff: ``/coach?review=progress-insight`` pre-fills a DRAFT that
   quotes the same two sentences Progress rendered, re-derived server-side;
   no user data in the URL, no auto-send, no model call, fail-soft.
E. Performance: no new Progress request, script or dependency.

    python -m pytest tests/test_progress_axis_insight.py -v
"""
import ast
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from app.i18n import _CATALOG
from app.services.progress_insights import (
    EVIDENCE_CODES,
    INSIGHT_BY_WEEK_FOCUS,
    INSIGHT_CODES,
    NEXT_MOVE_CODES,
    ProgressInsights,
    UnknownCanonicalVocabulary,
    build_progress_insights,
    progress_insights_payload,
    select_insight,
    select_next_move,
    select_watch,
    select_working,
)
from app.services.progress_summary import (
    TRAJECTORY_BY_SIGNAL,
    build_window,
    summarize_consistency,
    summarize_performance,
)
from app.services.training_history.models import WeeklyVolume
from app.services.training_planning import derive_adaptive_plan
from app.services.training_progression import ProgressionReport
from app.services.training_progression.analysis import (
    MIN_DELOAD_WEEKS,
    MIN_PLATEAU_WEEKS,
)

ROOT = Path(__file__).resolve().parents[1]
PRESENTATION = (ROOT / "static" / "progress_presentation.js").read_text(encoding="utf-8")
LOCALES = ("en", "tr")
END_DAY = date(2026, 9, 24)
RULES = {"deload_weeks": MIN_DELOAD_WEEKS, "plateau_weeks": MIN_PLATEAU_WEEKS}

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

# Consistency is not a free variable: each canonical signal implies one.
SIGNAL_CONTEXT = {
    "insufficient_data": "insufficient_data",
    "build_consistency": "inconsistent",
    "deload": "consistent",
    "plateau": "consistent",
    "progressing": "consistent",
    "keep_pushing": "consistent",
}

# Representative real-shaped cases, including the refinements (A–E of the
# brief: strong signal, needs-attention, on-track, sparse, no standout).
CASES = {
    "baseline": dict(signal="insufficient_data", sessions=(1, 0, 0, 0)),
    "consistency_gaps": dict(signal="build_consistency", sessions=(2, 0, 3, 0)),
    "deload_due": dict(signal="deload", is_plateau=True, deload_due=True),
    "stalled": dict(signal="plateau", is_plateau=True),
    "ready_to_progress": dict(signal="progressing", volume="up"),
    "ready_both": dict(signal="progressing", volume="up", strength="up"),
    "holding_steady": dict(signal="keep_pushing"),
    "steady_with_dip": dict(signal="keep_pushing", volume="down"),
    "steady_strength_dip": dict(signal="keep_pushing", strength="down"),
}


def _report(signal, *, volume="flat", strength="flat", sessions=(3, 3, 3, 3),
            **flags):
    weekly = [WeeklyVolume(week_start=date(2026, 8, 28), session_count=n,
                           total_volume=1000.0 * n) for n in sessions]
    return ProgressionReport(
        weeks=4, has_data=signal != "insufficient_data", next_signal=signal,
        load_consistency=SIGNAL_CONTEXT[signal], volume_trend=volume,
        strength_trend=strength, weekly_volume=weekly, **flags)


def _parts(case):
    spec = dict(CASES[case])
    report = _report(spec.pop("signal"), **spec)
    return (derive_adaptive_plan(report), summarize_performance(report),
            summarize_consistency(report))


def _insight(case):
    plan, performance, consistency = _parts(case)
    return select_insight(plan, performance, consistency, **RULES)


def _payload(case):
    """The exact wire payload the server would publish for ``case``."""
    plan, performance, consistency = _parts(case)
    return progress_insights_payload(ProgressInsights(
        window=build_window(END_DAY, 4),
        working=select_working(performance, consistency),
        watch=select_watch(plan, consistency),
        next_move=select_next_move(plan),
        insight=select_insight(plan, performance, consistency, **RULES),
    ))


def _render(desc, locale):
    text = _CATALOG[locale][desc["key"]]
    for name, value in (desc.get("params") or {}).items():
        text = text.replace("{%s}" % name, str(value))
    assert "{" not in text, text          # every placeholder was filled
    return text


# ── A. semantic contract ─────────────────────────────────────────────────────

def test_interpretation_covers_every_canonical_week_focus():
    from app.services.training_planning.analysis import _FOCUS_BY_SIGNAL

    assert set(INSIGHT_BY_WEEK_FOCUS) == set(_FOCUS_BY_SIGNAL.values())
    assert set(INSIGHT_BY_WEEK_FOCUS.values()) <= set(INSIGHT_CODES)


@pytest.mark.parametrize("case,code", [
    ("baseline", "baseline"),
    ("consistency_gaps", "consistency_gaps"),
    ("deload_due", "deload_due"),
    ("stalled", "stalled"),
    ("ready_to_progress", "ready_to_progress"),
    ("holding_steady", "holding_steady"),
    ("steady_with_dip", "steady_with_dip"),
    ("steady_strength_dip", "steady_with_dip"),
])
def test_each_canonical_decision_has_its_interpretation(case, code):
    assert _insight(case).code == code


@pytest.mark.parametrize("case", sorted(CASES))
def test_one_action_identical_to_the_canonical_next_move(case):
    plan, _, _ = _parts(case)
    insight = _insight(case)
    next_move = select_next_move(plan)
    assert insight.action_code == next_move.code
    assert insight.action == next_move.action
    assert insight.action_code in NEXT_MOVE_CODES


@pytest.mark.parametrize("case", sorted(CASES))
def test_evidence_is_bounded_and_grounded(case):
    insight = _insight(case)
    _, performance, consistency = _parts(case)
    assert len(insight.evidence) <= 2
    codes = [e.code for e in insight.evidence]
    assert len(codes) == len(set(codes))
    for fact in insight.evidence:
        assert fact.code in EVIDENCE_CODES
        for value in (fact.params or {}).values():
            assert isinstance(value, int) and not isinstance(value, bool)
        params = fact.params or {}
        if "active" in params:
            assert params["active"] == consistency.active_weeks
            assert params["total"] == consistency.analyzed_weeks
        if "sessions" in params:
            assert params["sessions"] == consistency.sessions
        # A trend fact exists only when the canonical trend says so.
        if fact.code.startswith("volume_") and fact.code != "volume_flat_run":
            expected = {"volume_rising": "up", "volume_holding": "flat",
                        "volume_falling": "down"}[fact.code]
            assert performance.volume_trend == expected
        if fact.code.startswith("strength_"):
            expected = {"strength_rising": "up",
                        "strength_falling": "down"}[fact.code]
            assert performance.strength_trend == expected


def test_sparse_user_gets_no_invented_fact_and_a_data_collection_action():
    insight = _insight("baseline")
    assert insight.status == "insufficient_data"
    assert insight.evidence == ()
    assert insight.action_code == "build_baseline"


def test_deload_and_plateau_evidence_quote_the_canonical_rule_constants():
    deload = {e.code: e.params for e in _insight("deload_due").evidence}
    assert deload == {"unbroken_block": {"weeks": MIN_DELOAD_WEEKS},
                      "volume_flat_run": {"weeks": MIN_PLATEAU_WEEKS}}
    stalled = [e.code for e in _insight("stalled").evidence]
    assert stalled == ["volume_flat_run", "trained_weeks"]


def test_progression_evidence_names_what_is_actually_rising():
    assert [e.code for e in _insight("ready_both").evidence] == [
        "strength_rising", "volume_rising"]
    assert [e.code for e in _insight("ready_to_progress").evidence] == [
        "volume_rising", "trained_weeks"]
    assert [e.code for e in _insight("steady_strength_dip").evidence] == [
        "trained_weeks", "strength_falling"]


def test_unknown_upstream_vocabulary_fails_closed():
    from dataclasses import replace

    plan, performance, consistency = _parts("holding_steady")
    with pytest.raises(UnknownCanonicalVocabulary):
        select_insight(replace(plan, week_focus="mystery"), performance,
                       consistency, **RULES)
    with pytest.raises(UnknownCanonicalVocabulary):
        select_insight(replace(plan, reason_codes=("steady_state", "new_code")),
                       performance, consistency, **RULES)


def test_payload_shape_is_additive_and_bounded():
    for case in CASES:
        d = _payload(case)
        assert d["contract_version"] == 1
        ins = d["insight"]
        assert set(ins) == {"status", "code", "evidence", "action"}
        assert ins["code"] in INSIGHT_CODES
        assert isinstance(ins["evidence"], list) and len(ins["evidence"]) <= 2
        for fact in ins["evidence"]:
            assert set(fact) == {"code", "params"}
        assert set(ins["action"]) == {"code", "week_focus", "volume_action",
                                      "intensity_action", "volume_delta_pct"}
        assert ins["action"]["code"] == d["next_move"]["code"]
        assert ins["action"]["volume_delta_pct"] == \
            d["next_move"]["action"]["volume_delta_pct"]


def test_endpoint_publishes_the_insight_from_the_same_single_read(
        app, client, make_user, login, monkeypatch):
    """No second history read: the insight rides the one progression report."""
    from app.services import progress_insights as pkg

    calls = []
    real = pkg.build_progression_report
    monkeypatch.setattr(pkg, "build_progression_report",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    make_user("axinsread", profile_complete=True)
    login("axinsread")
    d = client.get("/api/progress/axis-insights").get_json()
    assert len(calls) == 1
    assert d["insight"]["code"] == "baseline"
    assert d["insight"]["evidence"] == []
    assert d["insight"]["action"]["code"] == "build_baseline"


# ── B. view model under node ────────────────────────────────────────────────

def _node(expression_js):
    script = ("globalThis.window = {};\n" + PRESENTATION
              + "\nvar P = window.FitXProgressPresentation;\n"
              + "process.stdout.write(JSON.stringify(" + expression_js + "));\n")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                         timeout=30, check=True)
    return json.loads(out.stdout)


def _views(payloads):
    return _node("%s.map(function (d) { return P.buildAxisInsightView(d); })"
                 % json.dumps(payloads))


@requires_node
def test_view_model_maps_every_real_payload():
    cases = sorted(CASES)
    views = _views([_payload(c) for c in cases])
    for case, view in zip(cases, views):
        payload = _payload(case)["insight"]
        assert view["code"] == payload["code"]
        assert view["interpretation"]["key"] == \
            "progress.axis_insight_%s" % payload["code"]
        assert view["meaning"]["key"] == \
            "progress.axis_insight_%s_why" % payload["code"]
        assert view["action"]["text"]["key"] == \
            "progress.axis_action_%s" % payload["action"]["code"]
        assert [e["key"] for e in view["evidence"]] == [
            "progress.axis_evidence_%s" % f["code"] for f in payload["evidence"]]
        assert view["status"] == payload["status"]
        for locale in LOCALES:
            for desc in [view["interpretation"], view["meaning"],
                         view["action"]["text"], *view["evidence"]]:
                _render(desc, locale)


@requires_node
def test_view_model_carries_the_planner_delta_only_when_it_is_not_a_hold():
    views = _views([_payload("ready_to_progress"), _payload("deload_due"),
                    _payload("holding_steady")])
    assert [v["action"]["volume_delta"] for v in views] == [0.05, -0.4, None]


@requires_node
@pytest.mark.parametrize("payload", [
    None, {}, [], "x", {"insight": None}, {"insight": {}},
    {"insight": {"status": "available", "code": "mystery",
                 "evidence": [], "action": {"code": "deload"}}},
    {"insight": {"status": "available", "code": "stalled",
                 "evidence": [], "action": {"code": "mystery"}}},
    {"working": {"status": "available", "code": "training_steady"}},
])
def test_unreadable_payload_is_unavailable_never_advice(payload):
    view = _views([payload])[0]
    assert view["status"] == "unavailable"
    assert view["interpretation"]["key"] == "progress.axis_unavailable"
    assert view["action"] is None and view["evidence"] == []
    assert view["meaning"] is None


@requires_node
def test_view_model_drops_unknown_or_incomplete_evidence_and_caps_it():
    payload = _payload("stalled")
    payload["insight"]["evidence"] = [
        {"code": "mystery", "params": None},
        {"code": "trained_weeks", "params": {"active": 3}},        # incomplete
        {"code": "volume_holding", "params": None},
        {"code": "strength_rising", "params": None},
        {"code": "volume_rising", "params": None},
    ]
    view = _views([payload])[0]
    assert [e["key"] for e in view["evidence"]] == [
        "progress.axis_evidence_volume_holding",
        "progress.axis_evidence_strength_rising"]


@requires_node
def test_client_tables_are_exhaustive_over_the_server_vocabulary():
    tables = _node("{i: P.AXIS_INSIGHT, m: P.AXIS_MEANING, e: P.AXIS_EVIDENCE,"
                   " a: P.AXIS_ACTION, max: P.AXIS_MAX_EVIDENCE}")
    assert set(tables["i"]) == set(INSIGHT_CODES) == set(tables["m"])
    assert set(tables["e"]) == set(EVIDENCE_CODES)
    assert set(tables["a"]) == set(NEXT_MOVE_CODES)
    assert tables["max"] == 2


# ── C. deduplication with Current State + Trends ────────────────────────────

PERFORMANCE_FOR_SIGNAL = {
    "insufficient_data": "building_baseline", "progressing": "progressing",
    "keep_pushing": "steady", "build_consistency": "building_consistency",
    "plateau": "plateau", "deload": "deload",
}


def _summary_payload(case):
    spec = dict(CASES[case])
    report = _report(spec.pop("signal"), **spec)
    performance = summarize_performance(report)
    consistency = summarize_consistency(report)
    return {
        "contract_version": 1,
        "window": {"weeks": 4, "start": "2026-08-28", "end": "2026-09-24",
                   "timezone": "Europe/Istanbul"},
        "trajectory": {"state": TRAJECTORY_BY_SIGNAL[report.next_signal],
                       "reason": report.next_signal},
        "body": {"status": "available", "current_weight_kg": 78.4,
                 "weight_delta_kg": -0.6, "target_weight_kg": 75.0,
                 "distance_to_target_kg": 3.4,
                 "weight_series": [{"day": "2026-09-03", "weight_kg": 79.4},
                                   {"day": "2026-09-10", "weight_kg": 79.0},
                                   {"day": "2026-09-17", "weight_kg": 78.4}]},
        "performance": {"state": performance.state,
                        "volume_trend": performance.volume_trend,
                        "strength_trend": performance.strength_trend,
                        "next_signal": report.next_signal},
        "consistency": {"state": consistency.state,
                        "active_weeks": consistency.active_weeks,
                        "analyzed_weeks": consistency.analyzed_weeks,
                        "sessions": consistency.sessions},
        "weekly": [{"start": "2026-08-28", "sessions": w.session_count,
                    "active": w.session_count > 0,
                    "volume_kg": w.total_volume} for w in report.weekly_volume],
    }


def _page_descriptors(summary_view):
    cs = summary_view["current_state"]
    current = [cs["window"], cs["headline"], cs["summary"], *cs["evidence"],
               cs["next_action"]]
    trends = []
    for metric in summary_view["metrics"].values():
        trends += [metric["value"], metric["unit_label"],
                   (metric["change"] or {}).get("text"), metric["note"],
                   *metric["meta"], (metric["viz"] or {}).get("label")]
        trends += [c["label"] for c in (metric["viz"] or {}).get("cells", [])]
    return [d for d in current if d], [d for d in trends if d]


@requires_node
@pytest.mark.parametrize("case", sorted(CASES))
def test_no_axis_sentence_repeats_current_state_or_trends(case):
    summary, axis = _node(
        "[P.buildSummaryView(%s, {locale: 'en'}), P.buildAxisInsightView(%s)]"
        % (json.dumps(_summary_payload(case)), json.dumps(_payload(case))))
    current, trends = _page_descriptors(summary)
    axis_descs = [axis["interpretation"], axis["meaning"], axis["action"]["text"],
                  *axis["evidence"]]
    for locale in LOCALES:
        a = [_render(d, locale) for d in axis_descs]
        assert len(a) == len(set(a)), (case, locale)
        assert not set(a) & {_render(d, locale) for d in current}, (case, locale)
        assert not set(a) & {_render(d, locale) for d in trends}, (case, locale)


def test_axis_copy_never_equals_any_current_state_or_trends_template():
    """Stronger than per-case: no Axis template string equals any template the
    two PR2 sections own, whatever the params."""
    prefixes = ("progress.state_", "progress.traj_", "progress.metric_",
                "progress.cons_state_", "progress.body_sub_")
    for locale in LOCALES:
        owned = {v for k, v in _CATALOG[locale].items() if k.startswith(prefixes)}
        axis = {v for k, v in _CATALOG[locale].items()
                if k.startswith(("progress.axis_insight_",
                                 "progress.axis_evidence_",
                                 "progress.axis_action_"))}
        assert axis and not axis & owned


def test_current_state_handoff_points_at_the_new_surface():
    en = _CATALOG["en"]["progress.state_next_needs_attention"]
    tr = _CATALOG["tr"]["progress.state_next_needs_attention"]
    assert "Axis Insight" in en and "Insights" not in en
    assert "Axis içgörüsü" in tr


def test_interpretations_add_meaning_beyond_evidence():
    """Every interpretation is a reading AND why it matters (two distinct
    sentences), and neither restates an evidence template."""
    for locale in LOCALES:
        evidence = {v for k, v in _CATALOG[locale].items()
                    if k.startswith("progress.axis_evidence_")}
        for code in INSIGHT_CODES:
            lead = _CATALOG[locale]["progress.axis_insight_%s" % code]
            why = _CATALOG[locale]["progress.axis_insight_%s_why" % code]
            assert lead.endswith(".") and why.endswith("."), (locale, code)
            assert lead != why
            assert lead not in evidence and why not in evidence


# ── D. Coach handoff ────────────────────────────────────────────────────────

_DRAFT = re.compile(r"input\.value = (\".*?\");")


def _coach(client, path):
    r = client.get(path)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    m = _DRAFT.search(html)
    return html, (json.loads(m.group(1)) if m else None)


@pytest.mark.parametrize("v2", [False, True])
def test_review_link_prefills_the_insight_as_a_draft(app, client, make_user, login, v2):
    app.config["UIUX_COACH_PAGE_V2_ENABLED"] = v2
    make_user("axhand%d" % v2, profile_complete=True)
    login("axhand%d" % v2)
    _, draft = _coach(client, "/coach?review=progress-insight")
    assert draft
    locale = "tr"
    assert _CATALOG[locale]["progress.axis_insight_baseline"] in draft
    assert _CATALOG[locale]["progress.axis_insight_baseline_why"] in draft
    assert _CATALOG[locale]["progress.axis_action_build_baseline"] in draft


def test_draft_follows_the_display_locale(app, client, make_user, login):
    make_user("axhanden", profile_complete=True, language="en")
    login("axhanden")
    html, draft = _coach(client, "/coach?review=progress-insight")
    assert 'lang="en"' in html
    assert draft.startswith("I'm reviewing my Axis Insight")
    assert _CATALOG["en"]["progress.axis_insight_baseline"] in draft
    assert _CATALOG["en"]["progress.axis_action_build_baseline"] in draft


@pytest.mark.parametrize("path", ["/coach", "/coach?review=other",
                                  "/coach?review=", "/coach?insight=deload"])
def test_no_or_unknown_review_renders_coach_unchanged(app, client, make_user, login, path):
    make_user("axhandno", profile_complete=True)
    login("axhandno")
    html, draft = _coach(client, path)
    assert draft is None
    assert "cw-input');\n    if (input" not in html


def test_handoff_is_fail_soft(app, client, make_user, login, monkeypatch):
    import app.services.progress_insights as pkg

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(pkg, "build_progress_insights", boom)
    make_user("axhandfail", profile_complete=True)
    login("axhandfail")
    _, draft = _coach(client, "/coach?review=progress-insight")
    assert draft is None


def test_handoff_keeps_auth(app, client):
    r = client.get("/coach?review=progress-insight")
    assert r.status_code in (301, 302, 401)


def test_handoff_draft_is_never_sent_and_calls_no_model(
        app, client, make_user, login, monkeypatch):
    """Rendering the draft costs no model call and no write."""
    from app.services import ai_pipeline

    for name in dir(ai_pipeline):
        if name.startswith(("answer", "stream", "run")) and callable(
                getattr(ai_pipeline, name)):
            monkeypatch.setattr(ai_pipeline, name, lambda *a, **k: pytest.fail(name))
    make_user("axhandai", profile_complete=True)
    login("axhandai")
    _coach(client, "/coach?review=progress-insight")

    partial = (ROOT / "templates" / "_coach_handoff.html").read_text(encoding="utf-8")
    code = re.sub(r"\{#.*?#\}", "", partial, flags=re.S)
    for banned in ("send", "fetch", "submit", "click", "Storage", "setInterval"):
        assert banned not in code, banned
    assert "!input.value" in code                       # never overwrites a draft


def test_handoff_module_imports_no_ai_and_quotes_the_page_tables():
    source = (ROOT / "app" / "coach_handoff.py").read_text(encoding="utf-8")
    mods = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    assert mods == {"flask", "app.i18n", "app.services.progress_insights"}

    from app.coach_handoff import ACTION_KEYS, INSIGHT_KEYS
    assert set(INSIGHT_KEYS) == set(INSIGHT_CODES)
    assert set(ACTION_KEYS) == set(NEXT_MOVE_CODES)
    for code, key in {**INSIGHT_KEYS, **ACTION_KEYS}.items():
        assert f"{code}: '{key}'" in PRESENTATION, code
    for locale in LOCALES:
        assert "{insight}" in _CATALOG[locale]["coach.handoff_progress_insight"]
        assert "{action}" in _CATALOG[locale]["coach.handoff_progress_insight"]


def test_handoff_message_for_every_insight_uses_real_copy(app):
    from app.coach_handoff import coach_handoff_message
    import app.services.progress_insights as pkg

    with app.test_request_context("/coach"):
        for case in CASES:
            payload_insight = _insight(case)
            original = pkg.build_progress_insights
            pkg.build_progress_insights = lambda _uid, _i=payload_insight: \
                type("R", (), {"insight": _i})()
            try:
                msg = coach_handoff_message("progress-insight", 1)
            finally:
                pkg.build_progress_insights = original
            assert msg and "{" not in msg
            assert "progress." not in msg and "_" not in msg


# ── E. performance ──────────────────────────────────────────────────────────

def test_progress_page_request_budget_is_unchanged(app, client, make_user, login):
    make_user("axperf", profile_complete=True)
    login("axperf")
    html = client.get("/progress-page").get_data(as_text=True)
    scripts = re.findall(r'<script[^>]+src="(/static/[^"?]+)', html)
    assert scripts.count("/static/progress_insights.js") == 1
    assert "/static/coach_widget.js" not in scripts      # no second Coach
    for src in re.findall(r'<script[^>]+src="([^"]+)"', html):
        assert src.startswith("/static/") or "googletagmanager" in src, src

    insights = (ROOT / "static" / "progress_insights.js").read_text(encoding="utf-8")
    assert insights.count("fetch(") == 1


def test_no_new_llm_path_in_the_insight_package():
    """Deterministic before and after PR3: nothing in the package or the
    handoff imports a provider, the AI pipeline or a prompt module."""
    paths = list((ROOT / "app" / "services" / "progress_insights").glob("*.py"))
    paths.append(ROOT / "app" / "coach_handoff.py")
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for name in names:
                for banned in ("openai", "anthropic", "boto3", "app.prompts",
                               "app.services.ai", "app.services.prompt_builder",
                               "app.services.context_builder"):
                    assert not name.startswith(banned), (path.name, name)


def test_build_progress_insights_publishes_the_insight(app, make_user):
    user = make_user("axbuild", profile_complete=True)
    insights = build_progress_insights(user.id, end_day=END_DAY)
    assert insights.insight.code == "baseline"
    assert insights.insight.action_code == insights.next_move.code
