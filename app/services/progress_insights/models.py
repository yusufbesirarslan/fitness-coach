"""Frozen value objects + bounded vocabulary for Axis Insights (Progress PR3).

Plain data holders (no logic, no DB, no Flask) in the style of
``app/services/progress_summary/models.py``. The analysis layer selects over
them; the orchestrator composes them into the one ``ProgressInsights`` the
AXIS INSIGHTS section consumes.

Every machine value this package can publish is an explicit constant in this
module — there is no second copy in the blueprint, the client or the tests, so a
typo is an import error rather than a silently rendered string
(``docs/PROGRESS_INSIGHTS.md``).

Nothing here is a *decision*. Trajectory belongs to ``progress_summary``,
progression signals to ``training_progression``, and the next training
adjustment to ``training_planning``. This layer only names which canonical
signal earned which of the three slots.
"""
from dataclasses import dataclass

from app.services.progress_summary import ProgressWindow

# ── Slot availability ──────────────────────────────────────────────────────
# Three states, and the distinction between the last two is the whole point:
# "we looked and there is genuinely nothing to say" is a legitimate product
# state, "there is not enough of your history to look at" is a different one,
# and neither of them is "the service broke" (that is an HTTP failure — see
# the route). Collapsing any two of the three would hide one of them.
SLOT_AVAILABLE = "available"
SLOT_EMPTY = "empty"
SLOT_INSUFFICIENT_DATA = "insufficient_data"

SLOT_STATUSES = (SLOT_AVAILABLE, SLOT_EMPTY, SLOT_INSUFFICIENT_DATA)

# ── Domains ────────────────────────────────────────────────────────────────
# One domain in V1, deliberately. Body, nutrition, recovery and hydration have
# no validated Progress authority to select from, so an insight cannot honestly
# be attributed to them yet (``docs/PROGRESS_INSIGHTS.md`` §"What is excluded").
DOMAIN_TRAINING = "training"

DOMAINS = (DOMAIN_TRAINING,)

# ── WHAT'S WORKING ─────────────────────────────────────────────────────────
# Positive claims, each owned by exactly one canonical signal. There is no
# "everything is on track" code: a single positive sub-signal is not a verdict
# on the whole trajectory, so each code describes only the evidence it carries.
WORKING_TRAINING_PROGRESSING = "training_progressing"
WORKING_TRAINING_STEADY = "training_steady"
WORKING_TRAINING_CONSISTENT = "training_consistent"

WORKING_CODES = (
    WORKING_TRAINING_PROGRESSING,
    WORKING_TRAINING_STEADY,
    WORKING_TRAINING_CONSISTENT,
)

# ── WATCH THIS ─────────────────────────────────────────────────────────────
# One code per canonical ``AdaptivePlan`` reason code that deserves attention.
# Names are kept identical to the planner's own vocabulary so a reader can trace
# a rendered card straight back to ``derive_reason_codes`` without a glossary.
WATCH_BUILD_CONSISTENCY = "build_consistency"
WATCH_DELOAD_DUE = "deload_due"
WATCH_PLATEAU_DETECTED = "plateau_detected"
WATCH_VOLUME_TREND_DOWN = "volume_trend_down"
WATCH_STRENGTH_TREND_DOWN = "strength_trend_down"

WATCH_CODES = (
    WATCH_BUILD_CONSISTENCY,
    WATCH_DELOAD_DUE,
    WATCH_PLATEAU_DETECTED,
    WATCH_VOLUME_TREND_DOWN,
    WATCH_STRENGTH_TREND_DOWN,
)

# ── NEXT MOVE ──────────────────────────────────────────────────────────────
# One code per canonical ``AdaptivePlan.week_focus``. The mapping is 1:1 and
# exhaustive; these are presentation names for a decision the planner already
# made, not a second decision.
NEXT_BUILD_BASELINE = "build_baseline"
NEXT_PRIORITIZE_CONSISTENCY = "prioritize_consistency"
NEXT_DELOAD = "deload"
NEXT_MAINTAIN_AND_CONSOLIDATE = "maintain_and_consolidate"
NEXT_PROGRESS_TRAINING = "progress_training"
NEXT_MAINTAIN_CURRENT_TRAINING = "maintain_current_training"

NEXT_MOVE_CODES = (
    NEXT_BUILD_BASELINE,
    NEXT_PRIORITIZE_CONSISTENCY,
    NEXT_DELOAD,
    NEXT_MAINTAIN_AND_CONSOLIDATE,
    NEXT_PROGRESS_TRAINING,
    NEXT_MAINTAIN_CURRENT_TRAINING,
)

# ── AXIS INSIGHT (Progress V2 PR3) ─────────────────────────────────────────
# The unified surface: ONE interpretation, the canonical facts it rests on, and
# ONE action. Keyed 1:1 on ``AdaptivePlan.week_focus`` (the same decision NEXT
# MOVE projects), refined only where the planner's own reason codes record a
# nuance (``steady`` + a recorded down-trend). No new decision is made here —
# the code names which canonical explanation applies, the client maps it to copy.
INSIGHT_BASELINE = "baseline"
INSIGHT_CONSISTENCY_GAPS = "consistency_gaps"
INSIGHT_DELOAD_DUE = "deload_due"
INSIGHT_STALLED = "stalled"
INSIGHT_READY_TO_PROGRESS = "ready_to_progress"
INSIGHT_HOLDING_STEADY = "holding_steady"
INSIGHT_STEADY_WITH_DIP = "steady_with_dip"

INSIGHT_CODES = (
    INSIGHT_BASELINE,
    INSIGHT_CONSISTENCY_GAPS,
    INSIGHT_DELOAD_DUE,
    INSIGHT_STALLED,
    INSIGHT_READY_TO_PROGRESS,
    INSIGHT_HOLDING_STEADY,
    INSIGHT_STEADY_WITH_DIP,
)

# Evidence: observable facts, each one a canonical value or the canonical rule
# constant that made a signal fire. Params are counts the upstream layers
# already computed — never a percentage, never a derived score.
EVIDENCE_SESSIONS_ACROSS_WEEKS = "sessions_across_weeks"  # {sessions, active, total}
EVIDENCE_TRAINED_WEEKS = "trained_weeks"                  # {active, total}
EVIDENCE_UNBROKEN_BLOCK = "unbroken_block"                # {weeks}
EVIDENCE_VOLUME_FLAT_RUN = "volume_flat_run"              # {weeks}
EVIDENCE_VOLUME_RISING = "volume_rising"
EVIDENCE_VOLUME_HOLDING = "volume_holding"
EVIDENCE_VOLUME_FALLING = "volume_falling"
EVIDENCE_STRENGTH_RISING = "strength_rising"
EVIDENCE_STRENGTH_FALLING = "strength_falling"

EVIDENCE_CODES = (
    EVIDENCE_SESSIONS_ACROSS_WEEKS,
    EVIDENCE_TRAINED_WEEKS,
    EVIDENCE_UNBROKEN_BLOCK,
    EVIDENCE_VOLUME_FLAT_RUN,
    EVIDENCE_VOLUME_RISING,
    EVIDENCE_VOLUME_HOLDING,
    EVIDENCE_VOLUME_FALLING,
    EVIDENCE_STRENGTH_RISING,
    EVIDENCE_STRENGTH_FALLING,
)

# Bumped only on a breaking change to the wire contract. Independent of
# ``progress_summary``'s version: they are separate published surfaces and one
# may evolve without the other.
CONTRACT_VERSION = 1


class UnknownCanonicalVocabulary(ValueError):
    """An upstream canonical value this layer has no explicit mapping for.

    Raised — never defaulted — so a contract change upstream fails closed. The
    two harmful defaults are both available and both wrong: mapping an unknown
    ``week_focus`` to ``steady`` would publish "keep doing what you are doing"
    as advice nobody decided, and silently skipping an unknown attention reason
    would render an all-clear WATCH THIS while a real canonical concern exists.
    The route turns this into the generic JSON failure instead, because a
    contract drift is a system fault and not a statement about the user.
    """


@dataclass(frozen=True)
class NextMoveAction:
    """The canonical adjustment behind NEXT MOVE, projected verbatim.

    Every field is copied from ``AdaptivePlan`` — nothing here is computed.
    ``volume_delta_pct`` is the planner's signed fraction (``0.05``, ``-0.40``,
    or ``0.0`` when volume holds); the magnitude is the planner's decision and
    this layer has no authority to size it.
    """
    week_focus: str
    volume_action: str
    intensity_action: str
    volume_delta_pct: float


@dataclass(frozen=True)
class InsightSlot:
    """One of the three slots. At most one primary insight, never a list.

    ``code``/``domain`` are ``None`` unless ``status == available``: an empty
    slot has nothing to name. ``evidence`` carries only the canonical facts the
    rendered claim rests on (bounded, no ORM rows, no ids); ``action`` is set
    only by NEXT MOVE, whose evidence *is* the canonical adjustment.
    """
    status: str
    code: str | None = None
    domain: str | None = None
    evidence: dict | None = None
    action: NextMoveAction | None = None


@dataclass(frozen=True)
class InsightEvidence:
    """One observable fact behind the Axis Insight: a code + its bounded counts."""
    code: str
    params: dict | None = None


@dataclass(frozen=True)
class AxisInsight:
    """The unified Axis Insight (Progress V2 PR3).

    ``code`` names the interpretation, ``evidence`` carries at most two
    canonical facts it rests on (possibly none — a
    sparse user gets no invented fact), and ``action`` is the ONE recommended
    move: the same canonical projection NEXT MOVE carries, so the two can never
    disagree. ``status`` is ``available`` or ``insufficient_data``; a failure
    is an HTTP error, never an insight.
    """
    status: str
    code: str
    evidence: tuple = ()
    action_code: str | None = None
    action: NextMoveAction | None = None


@dataclass(frozen=True)
class ProgressInsights:
    """The canonical Axis Insights read model for one user over one window.

    Exactly three slots — no ranked list, no carousel, no fourth surface. The
    window is the same object ``progress_summary`` publishes, built from the
    same call, so the two responses can never claim different analysis periods.
    """
    window: ProgressWindow
    working: InsightSlot
    watch: InsightSlot
    next_move: InsightSlot
    # Additive (V2 PR3): the one coherent insight the Progress page renders.
    # ``None`` only for callers that build the three slots alone.
    insight: AxisInsight | None = None


__all__ = [
    "SLOT_AVAILABLE",
    "SLOT_EMPTY",
    "SLOT_INSUFFICIENT_DATA",
    "SLOT_STATUSES",
    "DOMAIN_TRAINING",
    "DOMAINS",
    "WORKING_TRAINING_PROGRESSING",
    "WORKING_TRAINING_STEADY",
    "WORKING_TRAINING_CONSISTENT",
    "WORKING_CODES",
    "WATCH_BUILD_CONSISTENCY",
    "WATCH_DELOAD_DUE",
    "WATCH_PLATEAU_DETECTED",
    "WATCH_VOLUME_TREND_DOWN",
    "WATCH_STRENGTH_TREND_DOWN",
    "WATCH_CODES",
    "NEXT_BUILD_BASELINE",
    "NEXT_PRIORITIZE_CONSISTENCY",
    "NEXT_DELOAD",
    "NEXT_MAINTAIN_AND_CONSOLIDATE",
    "NEXT_PROGRESS_TRAINING",
    "NEXT_MAINTAIN_CURRENT_TRAINING",
    "NEXT_MOVE_CODES",
    "INSIGHT_BASELINE",
    "INSIGHT_CONSISTENCY_GAPS",
    "INSIGHT_DELOAD_DUE",
    "INSIGHT_STALLED",
    "INSIGHT_READY_TO_PROGRESS",
    "INSIGHT_HOLDING_STEADY",
    "INSIGHT_STEADY_WITH_DIP",
    "INSIGHT_CODES",
    "EVIDENCE_SESSIONS_ACROSS_WEEKS",
    "EVIDENCE_TRAINED_WEEKS",
    "EVIDENCE_UNBROKEN_BLOCK",
    "EVIDENCE_VOLUME_FLAT_RUN",
    "EVIDENCE_VOLUME_RISING",
    "EVIDENCE_VOLUME_HOLDING",
    "EVIDENCE_VOLUME_FALLING",
    "EVIDENCE_STRENGTH_RISING",
    "EVIDENCE_STRENGTH_FALLING",
    "EVIDENCE_CODES",
    "CONTRACT_VERSION",
    "UnknownCanonicalVocabulary",
    "InsightEvidence",
    "AxisInsight",
    "NextMoveAction",
    "InsightSlot",
    "ProgressInsights",
]
