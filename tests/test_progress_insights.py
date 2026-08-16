"""Axis Insights service tests (Progress Redesign PR3).

Two halves, deliberately separated:

* the **pure** half exercises the selection tables directly against synthetic
  ``ProgressionReport`` / ``AdaptivePlan`` values — no Flask, no DB, no clock —
  because the rules under test are total functions and pinning them needs no
  fixtures (mirrors ``tests/test_progress_summary.py``);
* the **composition** half proves the orchestrator reads one user's real
  history, exactly once, without writing anything.

The exhaustiveness tests are the load-bearing ones. They compare this package's
tables against ``training_planning``'s and ``progress_summary``'s *own*
vocabularies, so a canonical value added upstream fails here loudly instead of
silently becoming plausible advice.

    python -m pytest tests/test_progress_insights.py -v
"""
import ast
import pathlib
import re
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import WorkoutLog
from app.services.progress_insights import (
    DOMAIN_TRAINING,
    NEXT_MOVE_BY_WEEK_FOCUS,
    NEXT_MOVE_CODES,
    NON_ATTENTION_REASON_CODES,
    SLOT_AVAILABLE,
    SLOT_EMPTY,
    SLOT_INSUFFICIENT_DATA,
    WATCH_BY_REASON_CODE,
    WATCH_CODES,
    WORKING_BY_PERFORMANCE_STATE,
    WORKING_CODES,
    WORKING_TRAINING_CONSISTENT,
    WORKING_TRAINING_PROGRESSING,
    WORKING_TRAINING_STEADY,
    UnknownCanonicalVocabulary,
    build_progress_insights,
    select_next_move,
    select_watch,
    select_working,
)
from app.services.progress_summary import (
    PERFORMANCE_STATES,
    SUMMARY_WEEKS,
    build_progress_summary,
    summarize_consistency,
    summarize_performance,
)
from app.services.training_planning import derive_adaptive_plan
from app.services.training_progression import ProgressionReport, build_progression_report

END_DAY = date(2026, 8, 16)

# Every canonical ``next_signal``, with the report fields the progression layer
# would necessarily have produced alongside it. Consistency is not a free
# variable: ``build_consistency`` exists *because* consistency is inconsistent,
# and every signal below it in the precedence implies a consistent user.
SIGNAL_CONTEXT = {
    "insufficient_data": "insufficient_data",
    "build_consistency": "inconsistent",
    "deload": "consistent",
    "plateau": "consistent",
    "progressing": "consistent",
    "keep_pushing": "consistent",
}


def _report(next_signal, *, volume_trend="flat", strength_trend="flat", **kw):
    """A ProgressionReport shaped like the one the canonical layer would emit."""
    return ProgressionReport(
        weeks=SUMMARY_WEEKS,
        has_data=next_signal != "insufficient_data",
        next_signal=next_signal,
        load_consistency=SIGNAL_CONTEXT[next_signal],
        volume_trend=volume_trend,
        strength_trend=strength_trend,
        **kw,
    )


def _slots(report):
    """The three slots for one report, through the real selection functions."""
    performance = summarize_performance(report)
    consistency = summarize_consistency(report)
    plan = derive_adaptive_plan(report)
    return (
        select_working(performance, consistency),
        select_watch(plan, consistency),
        select_next_move(plan),
    )


# ---------------------------------------------------------------------------
# Exhaustiveness: the tables must cover the upstream contract, not a snapshot
# ---------------------------------------------------------------------------

def test_next_move_covers_every_canonical_week_focus():
    """Rule 13 + §3.20: a new ``week_focus`` must break PR3, not default."""
    from app.services.training_planning.analysis import _FOCUS_BY_SIGNAL

    canonical = set(_FOCUS_BY_SIGNAL.values())
    assert set(NEXT_MOVE_BY_WEEK_FOCUS) == canonical
    # ...and the mapping is injective: two focuses collapsing onto one code
    # would lose a distinction the planner deliberately makes.
    assert len(set(NEXT_MOVE_BY_WEEK_FOCUS.values())) == len(NEXT_MOVE_BY_WEEK_FOCUS)
    assert set(NEXT_MOVE_BY_WEEK_FOCUS.values()) == set(NEXT_MOVE_CODES)


def test_watch_tables_partition_the_planner_reason_vocabulary():
    """Every canonical reason code is either attention-worthy or explicitly not.

    An unclassified code must be impossible: silently skipping one would render
    an all-clear WATCH THIS while a real canonical concern exists.
    """
    from app.services.training_planning.analysis import _PRIMARY_REASON

    canonical = set(_PRIMARY_REASON.values()) | {
        "volume_trend_down", "strength_trend_down"}
    classified = set(WATCH_BY_REASON_CODE) | set(NON_ATTENTION_REASON_CODES)
    assert canonical == classified
    assert not (set(WATCH_BY_REASON_CODE) & set(NON_ATTENTION_REASON_CODES))
    assert set(WATCH_BY_REASON_CODE.values()) == set(WATCH_CODES)


def test_working_table_only_references_canonical_performance_states():
    assert set(WORKING_BY_PERFORMANCE_STATE) <= set(PERFORMANCE_STATES)
    assert set(WORKING_CODES) == (
        set(WORKING_BY_PERFORMANCE_STATE.values()) | {WORKING_TRAINING_CONSISTENT})


def test_every_published_code_is_reachable():
    """No dead vocabulary: each declared code must be produced by some signal.

    ``("flat", "down")`` is in the sweep on purpose: ``strength_trend_down`` is
    only ever reachable when volume is *not* also down, because the planner
    orders volume ahead of strength and WATCH THIS takes the planner's first
    attention-worthy code.
    """
    produced = {"working": set(), "watch": set(), "next_move": set()}
    for signal in SIGNAL_CONTEXT:
        for trends in (("flat", "flat"), ("down", "down"), ("flat", "down")):
            working, watch, next_move = _slots(
                _report(signal, volume_trend=trends[0], strength_trend=trends[1]))
            for name, slot in (("working", working), ("watch", watch),
                               ("next_move", next_move)):
                if slot.code:
                    produced[name].add(slot.code)

    assert produced["working"] == set(WORKING_CODES)
    assert produced["watch"] == set(WATCH_CODES)
    assert produced["next_move"] == set(NEXT_MOVE_CODES)


# ---------------------------------------------------------------------------
# The exact decision table, one canonical signal at a time
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("signal,working,watch,next_move", [
    # insufficient history: nothing to praise, nothing to warn about, but a
    # real canonical baseline action.
    ("insufficient_data", (SLOT_INSUFFICIENT_DATA, None),
     (SLOT_INSUFFICIENT_DATA, None), "build_baseline"),
    # inconsistent: no positive signal exists, and consistency is the concern.
    ("build_consistency", (SLOT_EMPTY, None),
     (SLOT_AVAILABLE, "build_consistency"), "prioritize_consistency"),
    # deload/plateau are needs_attention, yet consistency is still genuinely
    # good — that is the invariant WHAT'S WORKING exists to honour.
    ("deload", (SLOT_AVAILABLE, WORKING_TRAINING_CONSISTENT),
     (SLOT_AVAILABLE, "deload_due"), "deload"),
    ("plateau", (SLOT_AVAILABLE, WORKING_TRAINING_CONSISTENT),
     (SLOT_AVAILABLE, "plateau_detected"), "maintain_and_consolidate"),
    # progressing/steady: nothing canonical to watch → intentionally empty.
    ("progressing", (SLOT_AVAILABLE, WORKING_TRAINING_PROGRESSING),
     (SLOT_EMPTY, None), "progress_training"),
    ("keep_pushing", (SLOT_AVAILABLE, WORKING_TRAINING_STEADY),
     (SLOT_EMPTY, None), "maintain_current_training"),
])
def test_canonical_signal_selects_the_documented_slots(
        signal, working, watch, next_move):
    w, watch_slot, move = _slots(_report(signal))

    assert (w.status, w.code) == working
    assert (watch_slot.status, watch_slot.code) == watch
    assert move.status == SLOT_AVAILABLE and move.code == next_move
    # NEXT MOVE is never empty: the planner always has a canonical decision.
    assert move.action is not None and move.action.week_focus


def test_available_slots_are_attributed_to_a_bounded_domain():
    for signal in SIGNAL_CONTEXT:
        for slot in _slots(_report(signal)):
            if slot.status == SLOT_AVAILABLE:
                assert slot.domain == DOMAIN_TRAINING
            else:
                assert slot.code is None and slot.domain is None


# ---------------------------------------------------------------------------
# WATCH THIS: canonical precedence, never a second ladder
# ---------------------------------------------------------------------------

def test_primary_plan_reason_outranks_secondary_trend_reasons():
    """§3.7's worked example: deload_due wins over volume_trend_down."""
    report = _report("deload", volume_trend="down", strength_trend="down")
    plan = derive_adaptive_plan(report)
    assert plan.reason_codes == (
        "deload_due", "volume_trend_down", "strength_trend_down")

    watch = select_watch(plan, summarize_consistency(report))
    assert watch.code == "deload_due"
    assert watch.evidence["reason_code"] == "deload_due"


def test_secondary_reason_surfaces_when_the_primary_is_not_a_concern():
    """The planner records a down-trend it deliberately chose not to act on.

    ``keep_pushing`` → ``steady_state`` is not attention-worthy, so the next
    canonical code in the planner's own order wins. Nothing is re-ranked and
    nothing is invented — ``volume_trend_down`` is the planner's own code.
    """
    report = _report("keep_pushing", volume_trend="down")
    plan = derive_adaptive_plan(report)
    assert plan.reason_codes == ("steady_state", "volume_trend_down")

    watch = select_watch(plan, summarize_consistency(report))
    assert watch.status == SLOT_AVAILABLE
    assert watch.code == "volume_trend_down"


def test_strength_down_alone_is_surfaced_in_the_planner_order():
    report = _report("progressing", strength_trend="down")
    plan = derive_adaptive_plan(report)
    watch = select_watch(plan, summarize_consistency(report))
    assert watch.code == "strength_trend_down"


def test_watch_is_empty_when_no_canonical_concern_exists():
    """Rule 6: an intentionally empty slot, not an invented one.

    Nothing about hydration, nutrition, recovery, body or streaks may appear.
    """
    report = _report("progressing")
    watch = select_watch(derive_adaptive_plan(report), summarize_consistency(report))
    assert watch.status == SLOT_EMPTY
    assert watch.code is None and watch.evidence is None


def test_watch_never_reports_a_trend_nuance_as_insufficient_data():
    """Insufficient history short-circuits before any reason-code walk."""
    report = _report("insufficient_data")
    watch = select_watch(derive_adaptive_plan(report), summarize_consistency(report))
    assert watch.status == SLOT_INSUFFICIENT_DATA
    assert watch.code is None


# ---------------------------------------------------------------------------
# Fail-closed on unknown upstream vocabulary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("unknown", ["", "steady_state", "hypertrophy_block", None, 7])
def test_unknown_week_focus_fails_closed(unknown):
    """Rule 13 + STOP K: never default an unknown focus to a recommendation."""
    plan = replace(derive_adaptive_plan(_report("progressing")), week_focus=unknown)
    with pytest.raises(UnknownCanonicalVocabulary):
        select_next_move(plan)
    with pytest.raises(UnknownCanonicalVocabulary):
        select_watch(plan, summarize_consistency(_report("progressing")))


def test_unknown_reason_code_fails_closed_rather_than_reading_all_clear():
    """A future canonical concern must not be silently skipped into an empty slot."""
    report = _report("progressing")
    plan = replace(derive_adaptive_plan(report),
                   reason_codes=("progressing", "overreaching_detected"))
    with pytest.raises(UnknownCanonicalVocabulary):
        select_watch(plan, summarize_consistency(report))


@pytest.mark.parametrize("field,value", [
    ("state", "thriving"),
    ("state", None),
])
def test_unknown_performance_state_fails_closed(field, value):
    report = _report("progressing")
    performance = replace(summarize_performance(report), **{field: value})
    with pytest.raises(UnknownCanonicalVocabulary):
        select_working(performance, summarize_consistency(report))


def test_unknown_consistency_state_fails_closed():
    report = _report("plateau")
    consistency = replace(summarize_consistency(report), state="mostly")
    with pytest.raises(UnknownCanonicalVocabulary):
        select_working(summarize_performance(report), consistency)


# ---------------------------------------------------------------------------
# Quantified NEXT MOVE: projected, never computed
# ---------------------------------------------------------------------------

def test_next_move_action_is_projected_verbatim_from_the_plan():
    for signal in SIGNAL_CONTEXT:
        report = _report(signal)
        plan = derive_adaptive_plan(report)
        action = select_next_move(plan).action
        assert action.week_focus == plan.week_focus
        assert action.volume_action == plan.volume_action
        assert action.intensity_action == plan.intensity_action
        assert action.volume_delta_pct == plan.volume_delta_pct


def test_volume_magnitude_is_the_planners_constant_not_a_local_one():
    """§3.9: the number must come from the server authority, unrounded.

    Read off ``training_planning``'s own constants rather than written out, so
    a change there flows through instead of silently disagreeing with us.
    """
    from app.services.training_planning.analysis import (
        DELOAD_VOLUME_CUT, VOLUME_INCREASE_STEP,
    )
    overload = select_next_move(derive_adaptive_plan(_report("progressing")))
    deload = select_next_move(derive_adaptive_plan(_report("deload")))
    hold = select_next_move(derive_adaptive_plan(_report("plateau")))

    assert overload.action.volume_delta_pct == VOLUME_INCREASE_STEP
    assert deload.action.volume_delta_pct == -DELOAD_VOLUME_CUT
    assert hold.action.volume_delta_pct == 0.0


# ---------------------------------------------------------------------------
# Evidence is bounded and relevant
# ---------------------------------------------------------------------------

def test_evidence_is_bounded_and_carries_no_raw_history():
    ALLOWED = {"performance_state", "consistency_state", "active_weeks",
               "analyzed_weeks", "reason_code", "week_focus"}
    for signal in SIGNAL_CONTEXT:
        for slot in _slots(_report(signal)):
            if slot.evidence is None:
                continue
            assert set(slot.evidence) <= ALLOWED, slot.evidence
            for value in slot.evidence.values():
                assert isinstance(value, (str, int)), value


def test_consistency_evidence_explains_the_claim_it_supports(make_user):
    """The two codes that talk about weeks carry the canonical counts."""
    user = make_user("axevidence")
    today = date(2026, 8, 16)
    for offset in (0, 7, 14):
        db.session.add(WorkoutLog(
            user_id=user.id, exercise_name="Squat", sets=3, reps=5, weight_kg=60,
            volume=900.0,
            created_at=datetime(today.year, today.month, today.day, 12) - timedelta(days=offset)))
    db.session.commit()

    insights = build_progress_insights(user.id, end_day=today)
    summary = build_progress_summary(user.id, end_day=today)
    for slot in (insights.working, insights.watch):
        if slot.evidence and "active_weeks" in slot.evidence:
            assert slot.evidence["active_weeks"] == summary.consistency.active_weeks
            assert slot.evidence["analyzed_weeks"] == summary.consistency.analyzed_weeks


# ---------------------------------------------------------------------------
# Composition: window, determinism, isolation, read-only
# ---------------------------------------------------------------------------

def test_window_matches_the_progress_summary_window(make_user):
    """The two surfaces must never claim different analysis periods."""
    user = make_user("axwindow")
    insights = build_progress_insights(user.id, end_day=END_DAY)
    summary = build_progress_summary(user.id, end_day=END_DAY)
    assert insights.window == summary.window
    assert insights.window.weeks == SUMMARY_WEEKS == 4


def test_insights_agree_with_the_canonical_summary(make_user):
    """PR3 consumes PR2's authority; it must not restate it differently."""
    user = make_user("axagree")
    for offset in (0, 3, 7, 10, 14, 17, 21):
        db.session.add(WorkoutLog(
            user_id=user.id, exercise_name="Bench", sets=3, reps=5, weight_kg=60,
            volume=900.0 + offset,
            created_at=datetime(2026, 8, 16, 12) - timedelta(days=offset)))
    db.session.commit()

    insights = build_progress_insights(user.id, end_day=END_DAY)
    summary = build_progress_summary(user.id, end_day=END_DAY)

    if insights.working.evidence and "performance_state" in insights.working.evidence:
        assert insights.working.evidence["performance_state"] == summary.performance.state
    # And the trajectory is never contradicted: a positive WHAT'S WORKING under
    # needs_attention may only be the consistency claim, never a global verdict.
    if summary.trajectory.state == "needs_attention" and insights.working.code:
        assert insights.working.code == WORKING_TRAINING_CONSISTENT


def test_insights_are_deterministic_for_a_fixed_end_day(make_user):
    user = make_user("axdeterministic")
    db.session.add(WorkoutLog(
        user_id=user.id, exercise_name="Row", sets=3, reps=8, weight_kg=40,
        volume=960.0, created_at=datetime(2026, 8, 12, 12)))
    db.session.commit()
    assert build_progress_insights(user.id, end_day=END_DAY) == \
        build_progress_insights(user.id, end_day=END_DAY)


def test_insights_are_scoped_to_the_requesting_user(make_user):
    """Rule 10: another user's training can never leak into these slots."""
    trained = make_user("axtrained")
    empty = make_user("axempty")
    for offset in (0, 7, 14, 21):
        db.session.add(WorkoutLog(
            user_id=trained.id, exercise_name="Squat", sets=5, reps=5, weight_kg=100,
            volume=2500.0,
            created_at=datetime(2026, 8, 16, 12) - timedelta(days=offset)))
    db.session.commit()

    assert build_progress_insights(empty.id, end_day=END_DAY).next_move.code == \
        "build_baseline"
    assert build_progress_insights(trained.id, end_day=END_DAY) != \
        build_progress_insights(empty.id, end_day=END_DAY)


def test_building_insights_performs_no_write(make_user, monkeypatch):
    """Rule 11, enforced rather than asserted by inspection."""
    user = make_user("axnowrite")
    db.session.commit()

    def _fail(*_a, **_kw):
        raise AssertionError("progress_insights must not write")

    for name in ("add", "add_all", "delete", "flush", "commit"):
        monkeypatch.setattr(db.session, name, _fail)
    build_progress_insights(user.id, end_day=END_DAY)


def test_insights_read_training_history_exactly_once(make_user, monkeypatch):
    """§3.13: one canonical progression read per request, not two.

    ``progress_summary`` and ``training_planning`` both descend from a single
    ``ProgressionReport``, so composing them through their pure entry points
    costs one read — the duplicate the brief asked us to avoid.
    """
    import app.services.progress_insights as pkg

    calls = []
    real = build_progression_report

    def _counting(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(pkg, "build_progression_report", _counting)
    user = make_user("axonecall")
    db.session.commit()

    build_progress_insights(user.id, end_day=END_DAY)
    assert len(calls) == 1, calls
    assert calls[0][1]["end_day"] == END_DAY
    assert calls[0][1]["weeks"] == SUMMARY_WEEKS


def test_one_resolved_day_flows_to_every_upstream_call(make_user, monkeypatch):
    """Rule 9 / STOP L: the two authorities cannot resolve different days."""
    import app.services.progress_insights as pkg

    seen = []
    monkeypatch.setattr(pkg, "app_today", lambda: (seen.append("clock"), END_DAY)[1])
    user = make_user("axoneday")
    db.session.commit()

    insights = build_progress_insights(user.id)
    assert len(seen) == 1              # the clock is read exactly once
    assert insights.window.end == END_DAY


# ---------------------------------------------------------------------------
# Structural guards — the rules are about what the package may contain
# ---------------------------------------------------------------------------

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "progress_insights"


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _package_modules():
    return sorted(PACKAGE.glob("*.py"))


def _executable_source(path):
    """The module's code with docstrings removed, lower-cased.

    Every module in this package *documents* the rules it obeys — "no provider
    call", "no streak", "body is not judged" — so a naive substring scan of the
    file would trip on the prose explaining the very guard being enforced. The
    docstrings are stripped from the AST first, so what is scanned is code.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and \
                isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree)).lower()


def _mentions(code, word):
    """Whole-word search: ``except`` must not read as a mention of ``xp``."""
    return re.search(rf"\b{re.escape(word)}\b", code) is not None


def test_dependency_direction_is_one_way():
    """Nothing upstream may import this package (no cycle, no inversion)."""
    root = PACKAGE.parents[2]
    for upstream in ("progress_summary", "training_planning", "training_progression",
                     "training_history"):
        for path in (root / "app" / "services" / upstream).rglob("*.py"):
            assert "progress_insights" not in path.read_text(encoding="utf-8"), path


def test_insights_never_read_raw_training_or_orm_data():
    """It selects canonical decisions; it must not fetch facts of its own."""
    banned = {"app.models", "app.extensions", "app.services.training_history",
              "sqlalchemy", "flask"}
    for path in _package_modules():
        for module in _imported_modules(path):
            assert not any(module == b or module.startswith(b + ".") for b in banned), \
                f"{path.name} imports {module}"


def test_no_provider_or_prompt_dependency_exists_anywhere():
    """§"Why no LLM in V1": the whole package must be provider-free."""
    banned = ("openai", "bedrock", "anthropic", "claude", "groq", "prompt",
              "llm", "completion", "boto3", "requests", "httpx")
    for path in _package_modules():
        code = _executable_source(path)
        for word in banned:
            assert not _mentions(code, word), f"{path.name} references {word}"


def test_analysis_layer_stays_pure():
    """No DB, no Flask, no clock — so the tables unit-test without fixtures."""
    modules = _imported_modules(PACKAGE / "analysis.py")
    for banned in ("app.timeutil", "datetime", "flask", "app.models",
                   "app.extensions", "app.services.training_progression"):
        assert banned not in modules, banned


def test_no_second_day_authority_in_the_package():
    """Rule 9: only the orchestrator resolves a day, only through app_today."""
    for path in _package_modules():
        source = path.read_text(encoding="utf-8")
        assert "date.today(" not in source, path.name
        assert "utcnow(" not in source, path.name
        assert "datetime.now(" not in source, path.name


def test_package_declares_no_threshold_constants():
    """Rule 5: no number in this package may act as a decision threshold.

    The package is allowed to *carry* the planner's magnitude, never to name one
    of its own. Structural rather than by eyeballing: any module-level numeric
    constant here would be exactly the shape of a smuggled-in heuristic.
    """
    for path in _package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                # CONTRACT_VERSION / INSIGHTS_WEEKS are contract metadata, not
                # decision inputs; INSIGHTS_WEEKS is imported, not literal.
                if target.id in {"CONTRACT_VERSION"}:
                    continue
                assert not isinstance(node.value, ast.Constant) or \
                    not isinstance(node.value.value, (int, float)), \
                    f"{path.name}: numeric constant {target.id}"


def test_no_forbidden_domain_vocabulary_leaks_into_the_service():
    """§3.10 + §4: nutrition/recovery/hydration/gamification/body stay out.

    These are not merely unused — importing one of their thresholds is the
    specific failure mode PR3 forbids, so the package must not name them at all.
    """
    for path in _package_modules():
        code = _executable_source(path)
        for banned in ("streak", "calorie", "protein", "macro", "hydration",
                       "water", "fatigue", "sleep", "recovery", "readiness",
                       "weight", "weight_delta", "bmi", "xp", "quest", "level"):
            assert not _mentions(code, banned), f"{path.name} references {banned}"
