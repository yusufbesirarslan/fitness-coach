"""Frozen value objects + bounded enums for the Progress summary read model.

Plain data holders (no logic, no DB, no Flask) in the style of
``app/services/training_progression/models.py``. The analysis layer computes over
them; the orchestrator composes them into the one ``ProgressSummary`` the Progress
page consumes.

Every user-visible state in this module is an explicit constant rather than a
free-form string, so an unknown value is a test failure instead of a silently
rendered typo (``docs/PROGRESS_SUMMARY.md``).
"""
from dataclasses import dataclass
from datetime import date

# ── Trajectory (V1) ────────────────────────────────────────────────────────
# Exactly three states. There is deliberately no ``off_track``: the product has
# one validated longitudinal authority (training_progression) and it does not
# model "the user is failing", only "here is the signal to surface next".
TRAJECTORY_BUILDING_BASELINE = "building_baseline"
TRAJECTORY_ON_TRACK = "on_track"
TRAJECTORY_NEEDS_ATTENTION = "needs_attention"

TRAJECTORY_STATES = (
    TRAJECTORY_BUILDING_BASELINE,
    TRAJECTORY_ON_TRACK,
    TRAJECTORY_NEEDS_ATTENTION,
)

# ── Performance (presentation projection of next_signal) ───────────────────
PERFORMANCE_BUILDING_BASELINE = "building_baseline"
PERFORMANCE_PROGRESSING = "progressing"
PERFORMANCE_STEADY = "steady"
PERFORMANCE_BUILDING_CONSISTENCY = "building_consistency"
PERFORMANCE_PLATEAU = "plateau"
PERFORMANCE_DELOAD = "deload"

PERFORMANCE_STATES = (
    PERFORMANCE_BUILDING_BASELINE,
    PERFORMANCE_PROGRESSING,
    PERFORMANCE_STEADY,
    PERFORMANCE_BUILDING_CONSISTENCY,
    PERFORMANCE_PLATEAU,
    PERFORMANCE_DELOAD,
)

# ── Body ───────────────────────────────────────────────────────────────────
# A availability ladder, NOT a judgement. Nothing here says a weight is good.
BODY_AVAILABLE = "available"          # current weight + a two-observation delta
BODY_PARTIAL = "partial"              # current weight known, delta not derivable
BODY_INSUFFICIENT_DATA = "insufficient_data"  # no canonical current weight at all

BODY_STATUSES = (BODY_AVAILABLE, BODY_PARTIAL, BODY_INSUFFICIENT_DATA)

# ── Consistency ────────────────────────────────────────────────────────────
# Echoed verbatim from ``ProgressionReport.load_consistency`` — this layer does
# not define its own consistency vocabulary.
CONSISTENCY_CONSISTENT = "consistent"
CONSISTENCY_INCONSISTENT = "inconsistent"
CONSISTENCY_INSUFFICIENT_DATA = "insufficient_data"

CONSISTENCY_STATES = (
    CONSISTENCY_CONSISTENT,
    CONSISTENCY_INCONSISTENT,
    CONSISTENCY_INSUFFICIENT_DATA,
)

# The one server-owned analysis window. Not a query parameter: a trajectory that
# changes because the client asked for a different window is not a trajectory.
SUMMARY_WEEKS = 4

# Bumped only on a breaking change to the wire contract.
CONTRACT_VERSION = 1


class UnknownProgressionSignal(ValueError):
    """A ``next_signal`` value this layer has no explicit mapping for.

    Raised — never mapped to a default — so an unrecognised canonical value fails
    closed. Mapping it to ``on_track`` would invent success; mapping it to
    ``building_baseline`` would report a contract drift (a system fault) as
    "not enough of the user's data yet", which is the exact confusion
    ``docs/PROGRESS_SUMMARY.md`` forbids. The route turns it into the generic
    JSON failure response instead.
    """


@dataclass(frozen=True)
class ProgressWindow:
    """The analysis window every signal in a summary was computed over.

    ``start``/``end`` are inclusive Istanbul calendar days; the geometry is the
    training-history foundation's trailing windows, not a second definition.
    """
    weeks: int
    start: date
    end: date
    timezone: str


@dataclass(frozen=True)
class Trajectory:
    """The single canonical answer to "how am I doing?".

    ``reason`` is the canonical ``next_signal`` verbatim — the state is derived
    from it, so exposing it keeps the classification auditable end to end.
    """
    state: str
    reason: str


@dataclass(frozen=True)
class BodySummary:
    """Truthful projection of the user's body facts. Context, never a verdict.

    Every optional field is ``None`` when unknown and never ``0.0``: a missing
    target weight is not a target of zero, and an underivable delta is not
    "no change". ``distance_to_target_kg`` is an absolute distance and carries
    no good/bad classification in V1.
    """
    status: str
    current_weight_kg: float | None = None
    weight_delta_kg: float | None = None
    target_weight_kg: float | None = None
    distance_to_target_kg: float | None = None


@dataclass(frozen=True)
class PerformanceSummary:
    """Bounded projection of ``ProgressionReport`` — no independent training math."""
    state: str
    volume_trend: str
    strength_trend: str
    next_signal: str


@dataclass(frozen=True)
class ConsistencySummary:
    """Canonical training consistency plus the transparent counts behind it.

    ``active_weeks``/``analyzed_weeks``/``sessions`` exist so the UI can explain
    the state ("3 of last 4 weeks active") without inventing a percentage; there
    is no canonical denominator for an adherence rate, so none is published.
    """
    state: str
    active_weeks: int
    analyzed_weeks: int
    sessions: int


@dataclass(frozen=True)
class ProgressSummary:
    """The canonical Progress read model for one user over one window."""
    window: ProgressWindow
    trajectory: Trajectory
    body: BodySummary
    performance: PerformanceSummary
    consistency: ConsistencySummary


@dataclass(frozen=True)
class BodyFacts:
    """Raw, already-scoped body observations handed from queries to analysis.

    Deliberately dumb: the query layer decides *which* rows qualify, this object
    carries them, and ``analysis.summarize_body`` decides what they mean. Keeping
    the two apart is what lets the whole body ladder be unit-tested without a DB.

    ``recent_qualifying_weights`` is newest-first and contains at most two
    entries — the latest two *full* weekly check-ins (see ``queries``).
    """
    current_weight_kg: float | None = None
    target_weight_kg: float | None = None
    recent_qualifying_weights: tuple[float, ...] = ()
