"""TI-03 rule constants, closed vocabularies and frozen value objects.

Every threshold, bound and token TI-03 uses lives in this module. Facts,
comparability, diagnostics, policy and projection import from here; no other
module in the package may define a numeric rule or a public token.

Sources: TI-00 §10 (facts and bounds), §14 (comparator v1 and thresholds), §15
(one-lever policy), §16 as amended by TI-03 (projection, primary selection).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping, Optional

from app.services.workout_session.checkpoint import (
    MAX_EXERCISES,
    MAX_SETS_PER_EXERCISE,
    MAX_WEIGHT_KG,
)

CONTRACT_VERSION = 1
RULESET_VERSION = "ti_rules_v1"

# ── Bounds (TI-00 §10) ──────────────────────────────────────────────────────
HISTORY_DAYS = 56                 # previous session workout_date >= anchor - 56
MAX_RECENT_SESSIONS = 8           # per exercise, newest first
MAX_SCOPE_EXERCISES = MAX_EXERCISES        # 32, the checkpoint bound
MAX_SCOPE_SETS = MAX_SETS_PER_EXERCISE     # 20, the checkpoint bound
# One query row per completed session in the 57 queried Istanbul days. Same-day
# completion is exactly-once since LP-13, so 57 rows is the realistic maximum;
# the headroom absorbs legacy same-day rows. Reaching the limit is reported as
# incomplete coverage, never silently treated as complete history.
MAX_HISTORY_ROWS = 64

# ── Comparator thresholds (TI-00 §14) ───────────────────────────────────────
MIN_PERFORMANCE_PAIRS = 2
MIN_RIR_PAIRS = 2
MIN_TEMPO_PAIRS = 2
MIN_INTERVAL_PAIRS = 2
EXPOSURE_WEEKS = 2                       # two complete Mon–Sun Istanbul weeks
VOLUME_INCREASE_MIN_EXTRA_SETS = 2
VOLUME_INCREASE_MIN_RATIO = (1, 5)       # extra >= 20% of the nonzero prior count
FREQUENCY_INCREASE_MIN_EXTRA_DAYS = 1

# Catalog measurement conventions under which a logged kilogram value has no
# defined meaning (added load vs assistance vs bodyweight), or the activity has
# no rep/load measurement at all. Such exercises yield no eligible pairs.
UNSUPPORTED_MOVEMENTS = frozenset({"cardio", "mobility"})
UNKNOWN_LOAD_EQUIPMENT = frozenset({"bodyweight", "pull_up_bar", "resistance_band"})

# Only between-set logging intervals measured by this method are read. It is
# client-observed completion-tap evidence, never physiological rest.
LOGGING_INTERVAL_METHOD = "completion_gap"
MAX_LOGGING_INTERVAL_SECONDS = 3600

# ── Closed vocabularies ─────────────────────────────────────────────────────
STATE_AVAILABLE = "available"
STATE_INSUFFICIENT = "insufficient_data"
STATE_NOT_COMPARABLE = "not_comparable"
STATES = (STATE_AVAILABLE, STATE_INSUFFICIENT, STATE_NOT_COMPARABLE)

KIND_PERFORMANCE_DECLINED = "performance_declined"
KIND_PERFORMANCE_IMPROVED = "performance_improved"
KIND_PERFORMANCE_STABLE = "performance_stable"
KIND_EXECUTION_QUALITY = "execution_quality_context_changed"
KIND_EFFORT_INCREASED = "effort_increased"
KIND_EFFORT_DECREASED = "effort_decreased"
KIND_EFFORT_STABLE = "effort_stable"
KIND_EFFORT_MIXED = "effort_mixed"
KIND_VOLUME_INCREASE = "volume_increase_context"
KIND_FREQUENCY = "exposure_frequency_context"
KIND_INSUFFICIENT = "insufficient_comparable_history"
# Reserved by TI-00 §8/§14: requires a future precise rest method. No code path
# may emit it; a test pins that it is unreachable.
RESERVED_KINDS = frozenset({"short_rest_confound"})

# Frozen cross-exercise primary selection (TI-00 §16 as amended by TI-03).
# Rank 0 is the highest priority.
KIND_PRIORITY = (
    KIND_PERFORMANCE_DECLINED,
    KIND_PERFORMANCE_IMPROVED,
    KIND_EXECUTION_QUALITY,
    KIND_EFFORT_INCREASED,
    KIND_EFFORT_DECREASED,
    KIND_VOLUME_INCREASE,
    KIND_FREQUENCY,
    KIND_PERFORMANCE_STABLE,
    KIND_EFFORT_STABLE,
    KIND_EFFORT_MIXED,
)
KINDS = KIND_PRIORITY + (KIND_INSUFFICIENT,)

TITLE_PREFIX = "training_insight."
TITLE_NOT_COMPARABLE = TITLE_PREFIX + "not_comparable"

# TI-00 §14/§16: exactly these twelve, in this order. No other missing token.
MISSING_PRESCRIPTION = "missing_prescription"
MISSING_EXECUTION = "missing_execution"
MISSING_RIR = "missing_rir"
MISSING_TEMPO = "missing_tempo"
REST_METHOD_UNSUPPORTED = "rest_method_unsupported"
INSUFFICIENT_PAIRS = "insufficient_pairs"
PLAN_CHANGED = "plan_changed"
MIXED_PERFORMANCE = "mixed_performance"
INCOMPLETE_COVERAGE = "incomplete_coverage"
SESSION_NOT_COMPLETED = "session_not_completed"
HISTORY_UNAVAILABLE = "history_unavailable"
NO_COMPLETED_SETS = "no_completed_sets"
MISSING_CODES = (
    MISSING_PRESCRIPTION, MISSING_EXECUTION, MISSING_RIR, MISSING_TEMPO,
    REST_METHOD_UNSUPPORTED, INSUFFICIENT_PAIRS, PLAN_CHANGED,
    MIXED_PERFORMANCE, INCOMPLETE_COVERAGE, SESSION_NOT_COMPLETED,
    HISTORY_UNAVAILABLE, NO_COMPLETED_SETS,
)

# Evidence metric -> value-type unit tag, in the fixed projection order.
METRIC_REPS = "reps"
METRIC_WEIGHT = "weight_kg"
METRIC_RIR = "rir"
METRIC_TEMPO = "tempo"
METRIC_INTERVAL = "logging_interval_seconds"
METRIC_SET_COUNT = "set_count"
METRIC_FREQUENCY = "frequency_days"
METRIC_UNITS = (
    (METRIC_REPS, "int"),
    (METRIC_WEIGHT, "number"),
    (METRIC_RIR, "token"),
    (METRIC_TEMPO, "token"),
    (METRIC_INTERVAL, "int"),
    (METRIC_SET_COUNT, "int"),
    (METRIC_FREQUENCY, "int"),
)
METRIC_ORDER = tuple(metric for metric, _ in METRIC_UNITS)
# TI-00 §16 value bounds per numeric metric.
METRIC_MAXIMUM = {
    METRIC_REPS: 20_000,
    METRIC_WEIGHT: MAX_WEIGHT_KG,
    METRIC_INTERVAL: MAX_LOGGING_INTERVAL_SECONDS,
    METRIC_SET_COUNT: 12_800,
    METRIC_FREQUENCY: HISTORY_DAYS,
}
MAX_EVIDENCE = 8
MAX_PAIRED_SETS = MAX_SETS_PER_EXERCISE

# Ordinal RIR buckets. ``4_plus`` is an open top bucket: equal to itself as a
# bucket, never the number 4, never averaged.
RIR_ORDER = ("0", "1", "2", "3", "4_plus")
TEMPO_TOKENS = ("as_prescribed", "faster", "slower", "lost_control")

# TI-00 §15 one-lever policy vocabulary. Rest is reserved and unreachable.
LEVER_TEMPO = ("tempo", "follow_prescribed_tempo")
LEVER_EFFORT = ("effort", "aim_prescribed_effort")
LEVER_HOLD = ("current_approach", "hold_current_approach")
RESERVED_LEVERS = frozenset({"rest"})
OBSERVE_NEXT = "next_comparable_session"


# ── Value objects ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StoredSession:
    """Raw persisted columns of one owned session. No ORM identity, no user id.

    Attribute names deliberately match ``WorkoutSession`` so the canonical
    TI-01A ``project_context`` binding verifier reads it unchanged.
    """

    ref: str
    status: str
    workout_date: str
    completed_at: Optional[datetime]
    weekday_slot: Optional[str]
    checkpoint_revision: int
    checkpoint_data: Optional[str]
    execution_context_data: Optional[str]
    prescription_data: Optional[str]


AVAILABILITY_OBSERVED = "observed"
# Missing, unparseable or out-of-contract checkpoint: no execution evidence.
AVAILABILITY_UNAVAILABLE = "unavailable"
# completed_at absent or on another Istanbul day than workout_date: excluded,
# never re-dated.
AVAILABILITY_INCONSISTENT = "inconsistent_date"


@dataclass(frozen=True)
class SetObservation:
    index: int
    completed: bool
    reps: Optional[int]
    weight_kg: Optional[float]
    actual_rir: Optional[str] = None
    tempo_adherence: Optional[str] = None
    interval_seconds: Optional[int] = None
    interval_method: Optional[str] = None


@dataclass(frozen=True)
class ExerciseExecution:
    exercise_id: str
    sets: tuple  # SetObservation, ascending index

    @property
    def completed(self) -> tuple:
        return tuple(item for item in self.sets if item.completed)


@dataclass(frozen=True)
class SessionExecution:
    """One completed session's immutable execution evidence, or why it has none."""

    ref: str
    workout_date: Optional[date]
    completed_at: Optional[datetime]
    weekday_slot: Optional[str]
    checkpoint_revision: int
    availability: str
    exercises: tuple = ()  # ExerciseExecution, canonical workout order
    # Immutable start snapshot (TI-01A): plan identity for both transports and
    # the per-exercise structured targets. ``None`` when no valid snapshot.
    plan_lineage: Optional[str] = None
    plan_version: Optional[int] = None
    prescription: Optional[Mapping[str, dict]] = None

    @property
    def observed(self) -> bool:
        return self.availability == AVAILABILITY_OBSERVED

    @property
    def order_key(self) -> tuple:
        """Historical order: completed_at, then the opaque reference for ties."""
        return (self.completed_at or datetime.min, self.ref)

    def exercise(self, exercise_id: str) -> Optional[ExerciseExecution]:
        for entry in self.exercises:
            if entry.exercise_id == exercise_id:
                return entry
        return None


@dataclass(frozen=True)
class Coverage:
    eligible_sessions: int
    observed_sessions: int

    @property
    def complete(self) -> bool:
        return self.observed_sessions == self.eligible_sessions


@dataclass(frozen=True)
class ExposureFact:
    """Completed canonical set identities of one exercise in one window."""

    exercise_id: str
    window: tuple  # (monday, sunday)
    completed_sets: int
    coverage: Coverage


@dataclass(frozen=True)
class FrequencyFact:
    window: tuple
    days: int
    coverage: Coverage


@dataclass(frozen=True)
class VolumeFact:
    """``total`` sums observed values; ``None`` when nothing was observed."""

    total: Optional[float]
    observed_count: int
    eligible_count: int


@dataclass(frozen=True)
class RirFact:
    samples: int
    eligible: int
    distribution: tuple  # (bucket, count) in RIR_ORDER, zero counts omitted


@dataclass(frozen=True)
class IntervalFact:
    method: str
    samples: int
    eligible: int
    median_seconds: Optional[int]  # lower median; descriptive only


@dataclass(frozen=True)
class Occurrence:
    session: SessionExecution
    exercise: ExerciseExecution


@dataclass(frozen=True)
class RecentPerformance:
    exercise_id: str
    occurrences: tuple   # Occurrence, newest first, at most MAX_RECENT_SESSIONS
    excluded_sessions: int  # corrupt / inconsistent rows in the horizon


@dataclass(frozen=True)
class Evidence:
    metric: str
    previous: object
    current: object
    paired_sets: int
    previous_session_ref: Optional[str]


@dataclass(frozen=True)
class Pair:
    index: int
    previous: SetObservation
    current: SetObservation


@dataclass(frozen=True)
class ExerciseAssessment:
    exercise_id: str
    position: int
    comparison: str  # "paired" | STATE_INSUFFICIENT | STATE_NOT_COMPARABLE
    kinds: tuple     # available kinds, in KIND_PRIORITY order
    evidence: tuple  # Evidence, in METRIC_ORDER
    missing: frozenset
    previous_ref: Optional[str] = None
    policy: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Selection:
    state: str
    kind: Optional[str]
    exercise_id: Optional[str]
    evidence: tuple
    missing: tuple
    recommended_action: Optional[Mapping[str, str]]
