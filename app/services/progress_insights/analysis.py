"""Pure, deterministic Axis Insights slot selection.

No DB, no Flask, no clock — every function is a total function of its inputs, so
it unit-tests without fixtures (mirrors ``progress_summary/analysis.py`` and
``training_planning/analysis.py``).

The whole file is *selection*, not measurement. Not one threshold, count,
percentage or trend is computed here. Volume trends, plateau, deload and
consistency belong to ``training_progression``; the next weekly adjustment
belongs to ``training_planning``; the trajectory belongs to
``progress_summary``. What this layer adds is the product decision of *which
canonical signal earns which of the three slots* — and nothing else.

Every table below is exhaustive over the upstream vocabulary it consumes, and
every lookup miss raises ``UnknownCanonicalVocabulary``. That is deliberate: a
canonical value this build has never seen means the contract moved, and both
plausible fallbacks would publish something nobody decided (see the exception's
docstring).
"""
from app.services.progress_summary import (
    CONSISTENCY_CONSISTENT,
    CONSISTENCY_STATES,
    PERFORMANCE_BUILDING_BASELINE,
    PERFORMANCE_PROGRESSING,
    PERFORMANCE_STATES,
    PERFORMANCE_STEADY,
    ConsistencySummary,
    PerformanceSummary,
)
from app.services.training_planning import AdaptivePlan

from .models import (
    DOMAIN_TRAINING,
    NEXT_BUILD_BASELINE,
    NEXT_DELOAD,
    NEXT_MAINTAIN_AND_CONSOLIDATE,
    NEXT_MAINTAIN_CURRENT_TRAINING,
    NEXT_PRIORITIZE_CONSISTENCY,
    NEXT_PROGRESS_TRAINING,
    SLOT_AVAILABLE,
    SLOT_EMPTY,
    SLOT_INSUFFICIENT_DATA,
    WATCH_BUILD_CONSISTENCY,
    WATCH_DELOAD_DUE,
    WATCH_PLATEAU_DETECTED,
    WATCH_STRENGTH_TREND_DOWN,
    WATCH_VOLUME_TREND_DOWN,
    WORKING_TRAINING_CONSISTENT,
    WORKING_TRAINING_PROGRESSING,
    WORKING_TRAINING_STEADY,
    InsightSlot,
    NextMoveAction,
    UnknownCanonicalVocabulary,
)

# ── NEXT MOVE ──────────────────────────────────────────────────────────────
# ``AdaptivePlan.week_focus`` → the code the client renders. 1:1 and exhaustive
# over the planner's vocabulary; ``tests/test_progress_insights.py`` compares
# this table's keys against ``training_planning``'s own focus table, so a new
# focus breaks PR3 loudly instead of quietly becoming "steady".
NEXT_MOVE_BY_WEEK_FOCUS = {
    "insufficient_data": NEXT_BUILD_BASELINE,
    "build_consistency": NEXT_PRIORITIZE_CONSISTENCY,
    "deload": NEXT_DELOAD,
    "maintenance": NEXT_MAINTAIN_AND_CONSOLIDATE,
    "overload": NEXT_PROGRESS_TRAINING,
    "steady": NEXT_MAINTAIN_CURRENT_TRAINING,
}

# ── WATCH THIS ─────────────────────────────────────────────────────────────
# Canonical ``AdaptivePlan`` reason code → the watch code it publishes. Only
# codes that describe something worth attention appear here.
WATCH_BY_REASON_CODE = {
    "inconsistent_training": WATCH_BUILD_CONSISTENCY,
    "deload_due": WATCH_DELOAD_DUE,
    "plateau_detected": WATCH_PLATEAU_DETECTED,
    "volume_trend_down": WATCH_VOLUME_TREND_DOWN,
    "strength_trend_down": WATCH_STRENGTH_TREND_DOWN,
}

# Canonical reason codes that are real, understood, and simply not a concern.
# Listed explicitly rather than handled by an ``else`` branch: together with
# ``WATCH_BY_REASON_CODE`` this partitions the planner's entire vocabulary, so
# an unrecognised code is unambiguously new rather than merely uninteresting.
NON_ATTENTION_REASON_CODES = frozenset({
    "insufficient_history",
    "progressing",
    "steady_state",
})

# ── WHAT'S WORKING ─────────────────────────────────────────────────────────
# Canonical PERFORMANCE state → the positive code it supports, for the states
# that genuinely support one. ``plateau`` / ``deload`` / ``building_consistency``
# are absent on purpose: none of them is a positive claim, and manufacturing one
# from them is exactly what this slot must not do.
WORKING_BY_PERFORMANCE_STATE = {
    PERFORMANCE_PROGRESSING: WORKING_TRAINING_PROGRESSING,
    PERFORMANCE_STEADY: WORKING_TRAINING_STEADY,
}


def _require_known(value: str, allowed, what: str) -> str:
    """Fail closed on any canonical value outside the vocabulary we mapped."""
    if value not in allowed:
        raise UnknownCanonicalVocabulary(f"unmapped canonical {what}")
    return value


def select_working(
    performance: PerformanceSummary, consistency: ConsistencySummary
) -> InsightSlot:
    """The one positive signal worth surfacing, or an explicitly empty slot.

    Precedence, most specific first:

    1. canonical performance ``progressing`` — the strongest positive the
       product owns;
    2. canonical performance ``steady`` — holding, nothing to escalate;
    3. canonical training consistency ``consistent`` — the positive that
       survives when performance itself is *not* positive. This is the case the
       invariant in ``docs/PROGRESS_INSIGHTS.md`` exists for: a user whose
       trajectory is ``needs_attention`` because of a plateau or a due deload
       has still trained consistently, and saying so is true. The copy for this
       code describes consistency and nothing else, so it cannot read as
       "everything is fine";
    4. canonical performance ``building_baseline`` — not enough history to
       claim anything, which is ``insufficient_data`` rather than ``empty``;
    5. otherwise (``building_consistency``) — genuinely nothing positive to
       report yet. Empty, not fabricated.

    Body weight movement is deliberately not a candidate at any position: the
    repository owns no validated rate-of-loss/rate-of-gain authority, so calling
    a kilogram "working" would be an invented verdict (``docs/PROGRESS_SUMMARY.md``
    keeps body contextual for the same reason).
    """
    state = _require_known(
        performance.state, PERFORMANCE_STATES, "performance state")
    consistency_state = _require_known(
        consistency.state, CONSISTENCY_STATES, "consistency state")

    code = WORKING_BY_PERFORMANCE_STATE.get(state)
    if code is not None:
        return InsightSlot(
            status=SLOT_AVAILABLE,
            code=code,
            domain=DOMAIN_TRAINING,
            evidence={"performance_state": state},
        )

    if consistency_state == CONSISTENCY_CONSISTENT:
        return InsightSlot(
            status=SLOT_AVAILABLE,
            code=WORKING_TRAINING_CONSISTENT,
            domain=DOMAIN_TRAINING,
            evidence={
                "consistency_state": consistency_state,
                "active_weeks": consistency.active_weeks,
                "analyzed_weeks": consistency.analyzed_weeks,
            },
        )

    if state == PERFORMANCE_BUILDING_BASELINE:
        return InsightSlot(status=SLOT_INSUFFICIENT_DATA)

    return InsightSlot(status=SLOT_EMPTY)


def select_watch(plan: AdaptivePlan, consistency: ConsistencySummary) -> InsightSlot:
    """The highest-priority canonical concern, or an explicitly empty slot.

    The planner already ordered its own reasons — position 0 is always the
    primary cause and the trend nuances follow in a fixed order — so this walks
    ``reason_codes`` in that order and takes the first attention-worthy one.
    That is why a plan reading ``["deload_due", "volume_trend_down"]`` surfaces
    ``deload_due``: the priority is the planner's, not a second ladder built
    here. Re-sorting the list would be exactly the duplicated authority PR3
    forbids.

    A non-attention primary does not suppress the rest of the list. When the
    planner says ``["steady_state", "volume_trend_down"]`` it has deliberately
    chosen not to *act* on the down-trend (recommending an increase back to
    baseline on ambiguous evidence is speculative) while still recording it —
    surfacing that recorded nuance is what the secondary codes are for. It is
    the planner's own code, not an invented warning.

    ``insufficient_data`` short-circuits: with too little history there is no
    honest way to name what deserves attention, and that is a different
    statement from "nothing does".
    """
    focus = _require_known(
        plan.week_focus, NEXT_MOVE_BY_WEEK_FOCUS, "week focus")
    if focus == "insufficient_data":
        return InsightSlot(status=SLOT_INSUFFICIENT_DATA)

    for reason_code in plan.reason_codes:
        watch_code = WATCH_BY_REASON_CODE.get(reason_code)
        if watch_code is None:
            # Known-but-uninteresting codes are skipped; anything else is new
            # vocabulary and must not be silently dropped into an all-clear.
            _require_known(
                reason_code, NON_ATTENTION_REASON_CODES, "plan reason code")
            continue

        evidence = {"reason_code": reason_code, "week_focus": focus}
        if watch_code == WATCH_BUILD_CONSISTENCY:
            # The one watch item the user can act on by counting weeks, so it
            # carries the canonical counts that explain it.
            evidence["active_weeks"] = consistency.active_weeks
            evidence["analyzed_weeks"] = consistency.analyzed_weeks
        return InsightSlot(
            status=SLOT_AVAILABLE,
            code=watch_code,
            domain=DOMAIN_TRAINING,
            evidence=evidence,
        )

    return InsightSlot(status=SLOT_EMPTY)


def select_next_move(plan: AdaptivePlan) -> InsightSlot:
    """The canonical next training action, projected from ``AdaptivePlan``.

    The single mandatory rule of PR3: this comes from the planner's
    ``week_focus`` and from nothing else. Sessions, volume trend, strength
    trend, consistency counts, trajectory and body change are *evidence the
    planner already weighed* — deriving a move from them here would be a second
    planning engine that can contradict the first.

    Always ``available``. Even with no history the planner emits a real,
    canonical, deliberately neutral decision (``insufficient_data`` →
    ``build_baseline``), which is a genuine next move — "log some training so
    there is something to coach on" — rather than a filled-in blank.

    The quantified adjustment is copied verbatim. This layer never computes a
    magnitude and never decides that 5% is appropriate; it only carries the
    number the planner owns.
    """
    focus = plan.week_focus
    try:
        code = NEXT_MOVE_BY_WEEK_FOCUS[focus]
    except (KeyError, TypeError):
        raise UnknownCanonicalVocabulary("unmapped canonical week focus") from None

    return InsightSlot(
        status=SLOT_AVAILABLE,
        code=code,
        domain=DOMAIN_TRAINING,
        action=NextMoveAction(
            week_focus=focus,
            volume_action=plan.volume_action,
            intensity_action=plan.intensity_action,
            volume_delta_pct=plan.volume_delta_pct,
        ),
    )
