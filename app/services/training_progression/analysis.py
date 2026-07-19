"""Pure, deterministic progression calculations (Sprint 6 PR2).

No DB, no Flask, no clock — every function is a total function of its inputs, so it
unit-tests without fixtures (mirrors ``training_history/analysis.py`` and
``tests/test_calculations.py``). All windowing / marker semantics are decided by the
training-history foundation upstream; these helpers only interpret its value
objects into progression signals.

Trend direction reuses the foundation's ±5% dead-band (``TREND_BAND``) and 7-day
window size (``WEEK_DAYS``) so volume and strength are judged on one scale and the
band lives in a single place. Every threshold below is explicit and documented in
``docs/TRAINING_PROGRESSION.md``.
"""
from datetime import timedelta
from collections.abc import Iterable

from app.services.training_history import WeeklyVolume, WorkoutEntry, estimated_1rm, weekly_windows
from app.services.training_history.analysis import WEEK_DAYS, TREND_BAND

from .models import WeeklyStrength

# Consecutive active weeks that must be flat (both volume and non-rising strength)
# before we call it a plateau. Fewer active weeks → not enough evidence.
MIN_PLATEAU_WEEKS = 3
# Consecutive active weeks of sustained, non-dropping load with no rest week that
# signal a planned deload is due.
MIN_DELOAD_WEEKS = 4
# Windows (out of the most recent four) the user must have trained in to be
# "consistent" enough to support progressive overload.
CONSISTENCY_MIN_ACTIVE_WEEKS = 3
# Below this many weeks with any training we cannot judge consistency/trend at all.
MIN_DATA_WEEKS = 2


def series_trend(values: Iterable[float]) -> str:
    """Direction of a numeric series: ``"up" | "flat" | "down"``.

    Compares the earliest to the latest *active* (``> 0``) value with the same ±5%
    dead-band the foundation's ``volume_trend`` uses, so strength and volume trends
    are computed identically. Fewer than two active values → ``"flat"`` (neutral).
    """
    active = [v for v in values if v > 0]
    if len(active) < 2:
        return "flat"
    first, last = active[0], active[-1]
    if last > first * (1 + TREND_BAND):
        return "up"
    if last < first * (1 - TREND_BAND):
        return "down"
    return "flat"


def _flat_run(values: list[float]) -> bool:
    """True when every value sits within +``TREND_BAND`` of the run's minimum.

    A stricter "genuine plateau" check than endpoint comparison: the whole run must
    be compressed inside the band (no week spikes above it). Needs ≥2 positive
    values.
    """
    if len(values) < 2:
        return False
    lo, hi = min(values), max(values)
    return lo > 0 and hi <= lo * (1 + TREND_BAND)


def weekly_best_estimated_1rm(
    entries: Iterable[WorkoutEntry], end_day, weeks: int
) -> list[WeeklyStrength]:
    """Per-week peak estimated 1RM over the same 7-day windows the foundation uses.

    For each window takes ``max(estimated_1rm(weight, reps))`` across the window's
    real exercise entries (markers excluded). Empty / rest / bodyweight-only windows
    yield ``best_estimated_1rm == 0.0``. Reuses the foundation's ``weekly_windows``
    and ``estimated_1rm`` — no new windowing or 1RM math here.
    """
    entries = list(entries)
    out: list[WeeklyStrength] = []
    for week_start in weekly_windows(end_day, weeks):
        week_end = week_start + timedelta(days=WEEK_DAYS - 1)
        in_week = [
            e for e in entries
            if not e.is_marker and week_start <= e.performed_on <= week_end
        ]
        best = max((estimated_1rm(e.weight_kg, e.reps) for e in in_week), default=0.0)
        out.append(
            WeeklyStrength(
                week_start=week_start,
                best_estimated_1rm=best,
                entry_count=len(in_week),
            )
        )
    return out


def is_progressing(volume_trend: str, strength_trend: str) -> bool:
    """The user is progressing when either volume or estimated strength trends up."""
    return volume_trend == "up" or strength_trend == "up"


def detect_plateau(
    weekly_volume: list[WeeklyVolume], weekly_strength: list[WeeklyStrength]
) -> bool:
    """True when recent training has stalled.

    Volume is the primary signal: the last ``MIN_PLATEAU_WEEKS`` *active* volume
    weeks must be a flat run (all within the band). Estimated strength is a
    secondary check — if there is enough strength data and it is still trending
    ``"up"``, the user is progressing via intensity, so it is **not** a plateau.
    Fewer than ``MIN_PLATEAU_WEEKS`` active volume weeks → ``False`` (neutral).
    """
    active_v = [w.total_volume for w in weekly_volume if w.total_volume > 0]
    if len(active_v) < MIN_PLATEAU_WEEKS:
        return False
    if not _flat_run(active_v[-MIN_PLATEAU_WEEKS:]):
        return False

    active_s = [s.best_estimated_1rm for s in weekly_strength if s.best_estimated_1rm > 0]
    if len(active_s) >= MIN_PLATEAU_WEEKS and series_trend(active_s[-MIN_PLATEAU_WEEKS:]) == "up":
        return False
    return True


def detect_deload_due(
    weekly_volume: list[WeeklyVolume], weekly_strength: list[WeeklyStrength]
) -> bool:
    """True when a long unbroken training block has now stalled — deload due.

    Deload readiness really depends on fatigue/recovery, which this foundation does
    not carry, so we stay conservative and only fire on the strongest inference
    available from volume-only history: a sustained accumulation block that has
    *stopped progressing*. Concretely, the most recent ``MIN_DELOAD_WEEKS`` windows
    are all active (no rest week — a rest week is itself a deload) **and** training
    is currently plateauing (see ``detect_plateau``). A healthy, still-rising block
    is **not** flagged; without a plateau we return the neutral ``False`` rather than
    guessing at fatigue.
    """
    vols = [w.total_volume for w in weekly_volume]
    if len(vols) < MIN_DELOAD_WEEKS:
        return False
    if any(v <= 0 for v in vols[-MIN_DELOAD_WEEKS:]):  # a rest week already deloaded
        return False
    return detect_plateau(weekly_volume, weekly_strength)


def assess_consistency(weekly_volume: list[WeeklyVolume]) -> str:
    """How consistent recent training is: enough to support progressive overload?

    ``"insufficient_data"`` when fewer than ``MIN_DATA_WEEKS`` windows have any
    trained day at all; otherwise ``"consistent"`` when the user trained in
    ≥ ``CONSISTENCY_MIN_ACTIVE_WEEKS`` of the last four windows, else
    ``"inconsistent"``. A trained window is one with ``session_count > 0`` (marker
    days count — a session happened).
    """
    active_total = sum(1 for w in weekly_volume if w.session_count > 0)
    if active_total < MIN_DATA_WEEKS:
        return "insufficient_data"
    recent = weekly_volume[-4:]
    active_recent = sum(1 for w in recent if w.session_count > 0)
    return "consistent" if active_recent >= CONSISTENCY_MIN_ACTIVE_WEEKS else "inconsistent"


def derive_next_signal(
    *,
    has_data: bool,
    load_consistency: str,
    deload_due: bool,
    is_plateau: bool,
    is_progressing: bool,
) -> str:
    """The single progression signal the coach should surface next.

    Deterministic precedence (highest first), so exactly one signal wins:
    ``insufficient_data`` → ``build_consistency`` → ``deload`` → ``plateau`` →
    ``progressing`` → ``keep_pushing``. Rationale: you cannot coach without data;
    build a consistent base before worrying about overload; rest before chasing a
    stalled lift; a plateau outranks steady work; progress is surfaced before the
    "holding steady" default.
    """
    if not has_data or load_consistency == "insufficient_data":
        return "insufficient_data"
    if load_consistency == "inconsistent":
        return "build_consistency"
    if deload_due:
        return "deload"
    if is_plateau:
        return "plateau"
    if is_progressing:
        return "progressing"
    return "keep_pushing"
