"""Deterministic diagnostic tokens derived from facts (TI-00 §14).

Diagnostics describe logged evidence; they never name a cause. Performance,
effort (RIR), tempo and exposure are compared on separate axes, and
completion-gap logging intervals are descriptive evidence only: no rest
diagnosis is reachable from them.
"""
from __future__ import annotations

from collections import Counter

from .comparability import PAIRED, pair_exercise
from .facts import exercise_frequency, exposure_windows, lower_median, recent_performance, weekly_set_exposure
from .models import (
    FREQUENCY_INCREASE_MIN_EXTRA_DAYS,
    HISTORY_UNAVAILABLE,
    INCOMPLETE_COVERAGE,
    KIND_EFFORT_DECREASED,
    KIND_EFFORT_INCREASED,
    KIND_EFFORT_MIXED,
    KIND_EFFORT_STABLE,
    KIND_EXECUTION_QUALITY,
    KIND_FREQUENCY,
    KIND_PERFORMANCE_DECLINED,
    KIND_PERFORMANCE_IMPROVED,
    KIND_PERFORMANCE_STABLE,
    KIND_PRIORITY,
    KIND_VOLUME_INCREASE,
    LOGGING_INTERVAL_METHOD,
    METRIC_FREQUENCY,
    METRIC_INTERVAL,
    METRIC_ORDER,
    METRIC_REPS,
    METRIC_RIR,
    METRIC_SET_COUNT,
    METRIC_TEMPO,
    METRIC_WEIGHT,
    MIN_INTERVAL_PAIRS,
    MIN_RIR_PAIRS,
    MIN_TEMPO_PAIRS,
    MISSING_RIR,
    MISSING_TEMPO,
    MIXED_PERFORMANCE,
    REST_METHOD_UNSUPPORTED,
    RIR_ORDER,
    STATE_INSUFFICIENT,
    STATE_NOT_COMPARABLE,
    VOLUME_INCREASE_MIN_EXTRA_SETS,
    VOLUME_INCREASE_MIN_RATIO,
    Evidence,
    ExerciseAssessment,
)

IMPROVED, DECLINED, STABLE, MIXED = "improved", "declined", "stable", "mixed"
INCREASED, DECREASED = "increased", "decreased"

_PERFORMANCE_KIND = {
    IMPROVED: KIND_PERFORMANCE_IMPROVED,
    DECLINED: KIND_PERFORMANCE_DECLINED,
    STABLE: KIND_PERFORMANCE_STABLE,
}
_EFFORT_KIND = {
    INCREASED: KIND_EFFORT_INCREASED,
    DECREASED: KIND_EFFORT_DECREASED,
    STABLE: KIND_EFFORT_STABLE,
    MIXED: KIND_EFFORT_MIXED,
}


def _direction(deltas) -> str:
    """All non-decreasing with >=1 increase, the converse, all equal, or mixed."""
    up = any(delta > 0 for delta in deltas)
    down = any(delta < 0 for delta in deltas)
    if up and not down:
        return IMPROVED
    if down and not up:
        return DECLINED
    return STABLE if not up else MIXED


# ── Axes ────────────────────────────────────────────────────────────────────
def compare_performance(pairs) -> str:
    """Directly observed load/reps at identical set indices; no formula.

    * equal load at every paired index: compare summed reps;
    * equal reps at every paired index: compare loads per index;
    * otherwise both dimensions per index must agree in direction.

    Any load-for-reps tradeoff is ``mixed`` (not comparable). There is no
    estimated 1RM and no strength score.
    """
    loads_equal = all(p.current.weight_kg == p.previous.weight_kg for p in pairs)
    reps_equal = all(p.current.reps == p.previous.reps for p in pairs)
    if loads_equal and reps_equal:
        return STABLE
    if loads_equal:
        delta = sum(p.current.reps for p in pairs) - sum(p.previous.reps for p in pairs)
        return IMPROVED if delta > 0 else DECLINED if delta < 0 else MIXED
    if reps_equal:
        return _direction([p.current.weight_kg - p.previous.weight_kg for p in pairs])
    deltas = []
    for p in pairs:
        deltas.extend((p.current.weight_kg - p.previous.weight_kg, p.current.reps - p.previous.reps))
    return _direction(deltas)


def compare_effort(pairs):
    """Ordinal RIR at identical indices where BOTH sessions recorded RIR.

    Lower RIR means higher self-reported effort. Missing RIR is never imputed;
    ``4_plus`` only equals ``4_plus``. Returns ``(direction | None, usable)``.
    """
    usable = tuple(p for p in pairs
                   if p.previous.actual_rir is not None and p.current.actual_rir is not None)
    if len(usable) < MIN_RIR_PAIRS:
        return None, usable
    # effort rises when reserve falls: negate the ordinal RIR delta
    deltas = [RIR_ORDER.index(p.previous.actual_rir) - RIR_ORDER.index(p.current.actual_rir)
              for p in usable]
    direction = _direction(deltas)
    return {IMPROVED: INCREASED, DECLINED: DECREASED}.get(direction, direction), usable


def compare_tempo(pairs):
    """Changed categorical adherence distribution at identical indices.

    Tokens are categories, never scores. Returns ``(changed | None, usable)``.
    """
    usable = tuple(p for p in pairs
                   if p.previous.tempo_adherence is not None and p.current.tempo_adherence is not None)
    if len(usable) < MIN_TEMPO_PAIRS:
        return None, usable
    changed = (Counter(p.previous.tempo_adherence for p in usable)
               != Counter(p.current.tempo_adherence for p in usable))
    return changed, usable


def _has_interval(observation) -> bool:
    return (observation.interval_seconds is not None
            and observation.interval_method == LOGGING_INTERVAL_METHOD)


def compare_logging_intervals(pairs):
    """Descriptive lower-median completion-gap intervals; never a diagnosis."""
    usable = tuple(p for p in pairs if _has_interval(p.previous) and _has_interval(p.current))
    if len(usable) < MIN_INTERVAL_PAIRS:
        return None
    return (lower_median(p.previous.interval_seconds for p in usable),
            lower_median(p.current.interval_seconds for p in usable), len(usable))


def _representative(pairs, value):
    """The highest paired index where the value differs, else the highest one.

    Always an actually observed pair; never an average or a synthetic value.
    """
    differing = [p for p in pairs if value(p.previous) != value(p.current)]
    chosen = (differing or list(pairs))[-1]
    return value(chosen.previous), value(chosen.current)


def exposure_context(history, current, exercise_id, *, truncated=False):
    """Recorded exposure in the two complete weeks before the session's week.

    Returns ``(kinds, evidence, missing)``. Zero baseline yields no context (a
    ratio to zero is undefined); incomplete coverage yields none either and is
    reported. This explains changed RECORDED exposure; it proves neither more
    training nor fatigue.
    """
    prior_window, recent_window = exposure_windows(current.workout_date)
    prior = weekly_set_exposure(history, exercise_id, prior_window)
    recent = weekly_set_exposure(history, exercise_id, recent_window)
    if truncated or not (prior.coverage.complete and recent.coverage.complete):
        return (), (), {INCOMPLETE_COVERAGE}
    if not (prior.completed_sets or recent.completed_sets):
        return (), (), set()
    prior_days = exercise_frequency(history, exercise_id, prior_window).days
    recent_days = exercise_frequency(history, exercise_id, recent_window).days
    kinds = []
    extra = recent.completed_sets - prior.completed_sets
    numerator, denominator = VOLUME_INCREASE_MIN_RATIO
    if (prior.completed_sets > 0 and extra >= VOLUME_INCREASE_MIN_EXTRA_SETS
            and extra * denominator >= prior.completed_sets * numerator):
        kinds.append(KIND_VOLUME_INCREASE)
    if prior_days > 0 and recent_days - prior_days >= FREQUENCY_INCREASE_MIN_EXTRA_DAYS:
        kinds.append(KIND_FREQUENCY)
    evidence = (
        Evidence(METRIC_SET_COUNT, prior.completed_sets, recent.completed_sets, 0, None),
        Evidence(METRIC_FREQUENCY, prior_days, recent_days, 0, None),
    )
    return tuple(kinds), evidence, set()


# ── One exercise ────────────────────────────────────────────────────────────
def assess_exercise(current, history, exercise_id, position, *, truncated=False):
    """Every axis for one canonical exercise of the current session."""
    recent = recent_performance(history, exercise_id, current)
    pairing = pair_exercise(current, recent, exercise_id)
    kinds, evidence, missing, policy = [], [], set(), {}
    comparison = pairing.comparison
    previous_ref = pairing.previous.ref if pairing.previous is not None else None

    if pairing.comparison == PAIRED:
        pairs = pairing.pairs
        count = len(pairs)
        performance = compare_performance(pairs)
        policy["performance"] = performance
        if performance == MIXED:
            comparison = STATE_NOT_COMPARABLE
            missing.add(MIXED_PERFORMANCE)
        else:
            kinds.append(_PERFORMANCE_KIND[performance])
        evidence.append(Evidence(METRIC_REPS, sum(p.previous.reps for p in pairs),
                                 sum(p.current.reps for p in pairs), count, previous_ref))
        previous_load, current_load = _representative(pairs, lambda o: o.weight_kg)
        evidence.append(Evidence(METRIC_WEIGHT, previous_load, current_load, count, previous_ref))

        effort, rir_pairs = compare_effort(pairs)
        policy["effort"] = effort
        policy["current_rir"] = tuple(p.current.actual_rir for p in rir_pairs)
        if effort is None:
            missing.add(MISSING_RIR)
        else:
            kinds.append(_EFFORT_KIND[effort])
            previous_rir, current_rir = _representative(rir_pairs, lambda o: o.actual_rir)
            evidence.append(Evidence(METRIC_RIR, previous_rir, current_rir, len(rir_pairs), previous_ref))

        tempo_changed, tempo_pairs = compare_tempo(pairs)
        policy["tempo_changed"] = tempo_changed
        policy["current_tempo"] = tuple(p.current.tempo_adherence for p in tempo_pairs)
        if tempo_changed is None:
            missing.add(MISSING_TEMPO)
        else:
            if tempo_changed:
                kinds.append(KIND_EXECUTION_QUALITY)
            previous_tempo, current_tempo = _representative(tempo_pairs, lambda o: o.tempo_adherence)
            evidence.append(Evidence(METRIC_TEMPO, previous_tempo, current_tempo,
                                     len(tempo_pairs), previous_ref))

        intervals = compare_logging_intervals(pairs)
        if intervals is not None:
            evidence.append(Evidence(METRIC_INTERVAL, intervals[0], intervals[1],
                                     intervals[2], previous_ref))
        if any(_has_interval(p.previous) or _has_interval(p.current) for p in pairs):
            # Interval evidence exists, but its method cannot support any rest
            # conclusion: say so instead of leaving rest silently unevaluated.
            missing.add(REST_METHOD_UNSUPPORTED)
        entry = current.prescription[exercise_id]
        policy["target_tempo"] = entry["target_tempo"]
        policy["target_rir"] = entry["target_rir"]
    else:
        missing.add(pairing.reason)

    exposure_kinds, exposure_evidence, exposure_missing = exposure_context(
        history, current, exercise_id, truncated=truncated)
    kinds.extend(exposure_kinds)
    evidence.extend(exposure_evidence)
    missing |= exposure_missing
    policy["exposure_increase"] = bool(exposure_kinds)
    if recent.excluded_sessions:
        missing.add(HISTORY_UNAVAILABLE)
    if truncated:
        missing.add(INCOMPLETE_COVERAGE)

    kinds.sort(key=KIND_PRIORITY.index)
    evidence.sort(key=lambda item: METRIC_ORDER.index(item.metric))
    if comparison not in (PAIRED, STATE_NOT_COMPARABLE):
        comparison = STATE_INSUFFICIENT
    return ExerciseAssessment(
        exercise_id=exercise_id, position=position, comparison=comparison,
        kinds=tuple(kinds), evidence=tuple(evidence), missing=frozenset(missing),
        previous_ref=previous_ref, policy=policy,
    )
