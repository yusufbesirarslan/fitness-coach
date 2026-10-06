"""TI-03 fact layer: eligibility, exposure, frequency, volume, V1/V2, intervals.

Hand-calculated expectations over canonical stored columns. Pure: no database.
"""
from dataclasses import replace
from datetime import date, datetime

import pytest

from app.services.training_intelligence import facts
from app.services.training_intelligence.models import (
    AVAILABILITY_INCONSISTENT,
    AVAILABILITY_OBSERVED,
    AVAILABILITY_UNAVAILABLE,
    MAX_RECENT_SESSIONS,
    SetObservation,
)
from tests.ti03_support import (
    ANCHOR, BENCH, SQUAT, context_entry, day, session, stored, straight, utc_noon,
)


def obs(index, completed=True, reps=None, weight=None, rir=None, gap=None, method="completion_gap"):
    return SetObservation(index, completed, reps, weight, actual_rir=rir,
                          interval_seconds=gap, interval_method=method if gap is not None else None)


# ── Session eligibility / provenance ────────────────────────────────────────
def test_valid_completed_checkpoint_is_observed_with_canonical_order():
    parsed = session("s1", ANCHOR, [(SQUAT, straight(60.0, 8, 8)), (BENCH, straight(40.0, 10))])
    assert parsed.availability == AVAILABILITY_OBSERVED
    assert [e.exercise_id for e in parsed.exercises] == [SQUAT, BENCH]
    assert [s.index for s in parsed.exercises[0].sets] == [0, 1]
    assert parsed.plan_lineage == "ti03-lineage-a" and parsed.plan_version == 3


@pytest.mark.parametrize("raw", [None, "", "not-json", "[]", '{"exercises": []}',
                                 '{"current_exercise_index":0,"elapsed_seconds":1,"exercises":'
                                 '[{"exercise_id":"ex_barbell_back_squat","sets":[{"index":0,'
                                 '"completed":1,"reps":5,"weight_kg":10}]}]}'])
def test_missing_or_corrupt_checkpoint_is_unavailable_not_guessed(raw):
    row = replace(stored("s1", ANCHOR, [(SQUAT, straight(60.0, 8))]), checkpoint_data=raw)
    parsed = facts.parse_session(row)
    assert parsed.availability == AVAILABILITY_UNAVAILABLE
    assert parsed.exercises == ()


@pytest.mark.parametrize("bad", ["reps_bool", "weight_nan", "negative_reps", "duplicate_index",
                                 "extra_key", "too_many_sets"])
def test_out_of_contract_stored_sets_make_the_whole_session_unavailable(bad):
    sets = [{"index": 0, "completed": True, "reps": 8, "weight_kg": 60.0}]
    if bad == "reps_bool": sets[0]["reps"] = True
    if bad == "weight_nan": sets[0]["weight_kg"] = float("nan")
    if bad == "negative_reps": sets[0]["reps"] = -1
    if bad == "duplicate_index": sets.append(dict(sets[0]))
    if bad == "extra_key": sets[0]["rir"] = 2
    if bad == "too_many_sets": sets = [{"index": i % 20, "completed": True, "reps": 1, "weight_kg": 1.0}
                                       for i in range(21)]
    import json
    raw = json.dumps({"current_exercise_index": 0, "elapsed_seconds": 1,
                      "exercises": [{"exercise_id": SQUAT, "sets": sets}]})
    row = replace(stored("s1", ANCHOR, [(SQUAT, straight(60.0, 8))]), checkpoint_data=raw)
    assert facts.parse_session(row).availability == AVAILABILITY_UNAVAILABLE


def test_cross_date_completion_is_excluded_not_redated():
    # Completed at 21:30 UTC = 00:30 Istanbul on the NEXT day.
    late = replace(stored("s1", ANCHOR, [(SQUAT, straight(60.0, 8))]),
                   completed_at=datetime(2026, 10, 1, 21, 30))
    missing = replace(stored("s2", ANCHOR, [(SQUAT, straight(60.0, 8))]), completed_at=None)
    assert facts.parse_session(late).availability == AVAILABILITY_INCONSISTENT
    assert facts.parse_session(missing).availability == AVAILABILITY_INCONSISTENT
    # 20:59 UTC is 23:59 Istanbul on the same day: consistent.
    edge = replace(stored("s3", ANCHOR, [(SQUAT, straight(60.0, 8))]),
                   completed_at=datetime(2026, 10, 1, 20, 59))
    assert facts.parse_session(edge).observed


# ── Weekly exposure ─────────────────────────────────────────────────────────
def test_weekly_exposure_counts_completed_set_identities_only():
    week = (date(2026, 9, 21), date(2026, 9, 27))
    sessions = [
        session("a", date(2026, 9, 22), [(SQUAT, [(0, True, 8, 60.0), (1, False, 8, 60.0),
                                                 (2, True, None, None)])]),
        session("b", date(2026, 9, 24), [(SQUAT, straight(60.0, 5, 5)), (BENCH, straight(40.0, 9))]),
    ]
    fact = facts.weekly_set_exposure(sessions, SQUAT, week)
    assert fact.completed_sets == 4      # 2 (incl. null reps/load) + 2; uncompleted excluded
    assert (fact.coverage.eligible_sessions, fact.coverage.observed_sessions) == (2, 2)
    assert facts.weekly_set_exposure(sessions, BENCH, week).completed_sets == 1


def test_istanbul_monday_to_sunday_boundaries():
    assert facts.week_start(date(2026, 10, 4)) == date(2026, 9, 28)   # Sunday
    assert facts.week_start(date(2026, 9, 28)) == date(2026, 9, 28)   # Monday
    prior, recent = facts.exposure_windows(ANCHOR)
    assert recent == (date(2026, 9, 21), date(2026, 9, 27))
    assert prior == (date(2026, 9, 14), date(2026, 9, 20))
    sunday = session("sun", date(2026, 9, 27), [(SQUAT, straight(60.0, 8))])
    monday = session("mon", date(2026, 9, 28), [(SQUAT, straight(60.0, 8))])
    assert facts.weekly_set_exposure([sunday, monday], SQUAT, recent).completed_sets == 1


def test_istanbul_day_not_utc_day_decides_the_window():
    # 2026-09-27 21:30 UTC is already Monday 2026-09-28 00:30 in Istanbul; the
    # row's workout_date is the Istanbul start day, so it belongs to the NEXT week.
    row = stored("x", date(2026, 9, 28), [(SQUAT, straight(60.0, 8))],
                 completed_at=datetime(2026, 9, 27, 21, 30))
    parsed = facts.parse_session(row)
    assert parsed.observed
    recent = (date(2026, 9, 21), date(2026, 9, 27))
    assert facts.weekly_set_exposure([parsed], SQUAT, recent).completed_sets == 0


def test_same_day_sessions_both_contribute_sets_but_one_training_day():
    week = (date(2026, 9, 21), date(2026, 9, 27))
    first = session("a", date(2026, 9, 23), [(SQUAT, straight(60.0, 8, 8))], minute=0)
    second = session("b", date(2026, 9, 23), [(SQUAT, straight(60.0, 6))], minute=30)
    assert facts.weekly_set_exposure([first, second], SQUAT, week).completed_sets == 3
    assert facts.exercise_frequency([first, second], SQUAT, week).days == 1
    assert facts.training_frequency([first, second], week).days == 1


def test_unavailable_sessions_reduce_coverage_never_count_as_zero():
    week = (date(2026, 9, 21), date(2026, 9, 27))
    good = session("a", date(2026, 9, 22), [(SQUAT, straight(60.0, 8))])
    corrupt = facts.parse_session(replace(
        stored("b", date(2026, 9, 24), [(SQUAT, straight(60.0, 8))]), checkpoint_data="{"))
    fact = facts.weekly_set_exposure([good, corrupt], SQUAT, week)
    assert fact.completed_sets == 1
    assert fact.coverage.eligible_sessions == 2 and fact.coverage.observed_sessions == 1
    assert not fact.coverage.complete


# ── Frequency ───────────────────────────────────────────────────────────────
def test_exercise_and_training_frequency_are_distinct_dates():
    week = (date(2026, 9, 21), date(2026, 9, 27))
    sessions = [
        session("a", date(2026, 9, 21), [(SQUAT, straight(60.0, 8))]),
        session("b", date(2026, 9, 23), [(BENCH, straight(40.0, 8))]),
        session("c", date(2026, 9, 25), [(SQUAT, [(0, False, 8, 60.0)]), (BENCH, straight(40.0, 8))]),
    ]
    assert facts.exercise_frequency(sessions, SQUAT, week).days == 1   # c has no completed squat
    assert facts.exercise_frequency(sessions, BENCH, week).days == 2
    assert facts.training_frequency(sessions, week).days == 3


def test_training_frequency_counts_completion_even_without_usable_sets():
    week = (date(2026, 9, 21), date(2026, 9, 27))
    corrupt = facts.parse_session(replace(
        stored("b", date(2026, 9, 24), [(SQUAT, straight(60.0, 8))]), checkpoint_data=None))
    inconsistent = facts.parse_session(replace(
        stored("c", date(2026, 9, 25), [(SQUAT, straight(60.0, 8))]), completed_at=None))
    fact = facts.training_frequency([corrupt, inconsistent], week)
    assert fact.days == 1                 # completion is canonical; re-dating is not
    assert (fact.coverage.eligible_sessions, fact.coverage.observed_sessions) == (2, 0)


# ── Volume ──────────────────────────────────────────────────────────────────
def test_rep_volume_excludes_null_and_preserves_zero():
    sets = [obs(0, reps=8), obs(1, reps=0), obs(2, reps=None), obs(3, completed=False, reps=50)]
    fact = facts.rep_volume(sets)
    assert (fact.total, fact.observed_count, fact.eligible_count) == (8, 2, 3)
    nothing = facts.rep_volume([obs(0, reps=None)])
    assert (nothing.total, nothing.observed_count, nothing.eligible_count) == (None, 0, 1)
    zero = facts.rep_volume([obs(0, reps=0)])
    assert zero.total == 0 and zero.observed_count == 1


def test_load_volume_pairs_reps_and_weight_only():
    sets = [obs(0, reps=8, weight=62.5), obs(1, reps=5, weight=0.0), obs(2, reps=None, weight=80.0),
            obs(3, reps=10, weight=None), obs(4, reps=0, weight=100.0), obs(5, completed=False, reps=9, weight=9.9)]
    fact = facts.load_volume(sets)
    # 8*62.5 + 5*0 + 0*100 = 500.0 kg·reps
    assert (fact.total, fact.observed_count, fact.eligible_count) == (500.0, 3, 5)
    assert facts.load_volume([obs(0, reps=None, weight=None)]).total is None


def test_load_volume_is_exact_in_tenths():
    sets = [obs(i, reps=3, weight=0.1) for i in range(10)]
    assert facts.load_volume(sets).total == 3.0          # float summation would drift
    assert facts.load_volume(list(reversed(sets))).total == 3.0


# ── V1 / V2 ─────────────────────────────────────────────────────────────────
def test_v1_session_keeps_base_facts_and_reports_unknown_context():
    parsed = session("v1", ANCHOR, [(SQUAT, straight(60.0, 8, 7))], v1=True)
    squat = parsed.exercise(SQUAT)
    assert facts.rep_volume(squat.sets).total == 15
    rir = facts.rir_fact(squat.sets)
    assert (rir.samples, rir.eligible, rir.distribution) == (0, 2, ())
    interval = facts.logging_interval_fact(squat.sets)
    assert (interval.samples, interval.eligible, interval.median_seconds) == (0, 1, None)
    assert all(s.tempo_adherence is None for s in squat.sets)


def test_v2_rir_zero_is_distinct_from_null_and_4_plus_stays_a_bucket():
    parsed = session("v2", ANCHOR, [(SQUAT, straight(60.0, 8, 8, 8))], context=[
        context_entry(SQUAT, 0, rir="0"), context_entry(SQUAT, 1, rir="4_plus")])
    rir = facts.rir_fact(parsed.exercise(SQUAT).sets)
    assert (rir.samples, rir.eligible) == (2, 3)
    assert rir.distribution == (("0", 1), ("4_plus", 1))


def test_unbound_context_is_absent_not_reattached_by_index():
    row = stored("v2", ANCHOR, [(SQUAT, straight(60.0, 8, 8))],
                 context=[context_entry(SQUAT, 0, rir="2")])
    stale = replace(row, checkpoint_revision=row.checkpoint_revision + 1)
    assert facts.parse_session(stale).exercise(SQUAT).sets[0].actual_rir is None
    assert facts.parse_session(row).exercise(SQUAT).sets[0].actual_rir == "2"


def test_completion_gap_is_preserved_as_logging_interval_evidence_only():
    parsed = session("v2", ANCHOR, [(SQUAT, straight(60.0, 8, 8, 8))], context=[
        context_entry(SQUAT, 1, gap=95), context_entry(SQUAT, 2, gap=70)])
    sets = parsed.exercise(SQUAT).sets
    assert [(s.interval_seconds, s.interval_method) for s in sets] == [
        (None, None), (95, "completion_gap"), (70, "completion_gap")]
    fact = facts.logging_interval_fact(sets)
    assert (fact.method, fact.samples, fact.eligible, fact.median_seconds) == ("completion_gap", 2, 2, 70)


def test_unsupported_interval_methods_are_never_combined():
    sets = [obs(0, reps=8), obs(1, reps=8, gap=90), obs(2, reps=8, gap=30, method="set_boundary"),
            obs(3, reps=8, gap=50)]
    fact = facts.logging_interval_fact(sets)
    assert (fact.samples, fact.median_seconds) == (2, 50)
    boundary = facts.logging_interval_fact(sets, method="set_boundary")
    assert (boundary.samples, boundary.median_seconds) == (1, 30)


# ── Recent performance ──────────────────────────────────────────────────────
def test_recent_performance_is_bounded_ordered_and_horizon_limited():
    current = session("cur", ANCHOR, [(SQUAT, straight(60.0, 8))])
    history = [session(f"h{n:02d}", day(-n), [(SQUAT, straight(60.0, 8))]) for n in range(1, 13)]
    history.append(session("old", day(-57), [(SQUAT, straight(60.0, 8))]))
    history.append(session("edge", day(-56), [(BENCH, straight(40.0, 8))]))
    later = session("later", ANCHOR, [(SQUAT, straight(60.0, 8))], minute=30)
    recent = facts.recent_performance(history + [later], SQUAT, current)
    assert len(recent.occurrences) == MAX_RECENT_SESSIONS
    assert [o.session.ref for o in recent.occurrences] == [f"h{n:02d}" for n in range(1, 9)]


def test_recent_performance_breaks_completed_at_ties_by_opaque_reference():
    current = session("zz", ANCHOR, [(SQUAT, straight(60.0, 8))], minute=59)
    tied = [session(ref, day(-7), [(SQUAT, straight(60.0, 8))]) for ref in ("bbb", "aaa", "ccc")]
    recent = facts.recent_performance(tied, SQUAT, current)
    assert [o.session.ref for o in recent.occurrences] == ["ccc", "bbb", "aaa"]


def test_recent_performance_reports_excluded_rows_in_its_horizon():
    current = session("cur", ANCHOR, [(SQUAT, straight(60.0, 8))])
    corrupt = facts.parse_session(replace(stored("bad", day(-7), [(SQUAT, straight(60.0, 8))]),
                                          checkpoint_data="{"))
    assert facts.recent_performance([corrupt], SQUAT, current).excluded_sessions == 1


def test_completed_at_is_the_ordering_authority_not_workout_date():
    assert utc_noon(ANCHOR, 5) > utc_noon(ANCHOR, 4)
    a = session("a", ANCHOR, [(SQUAT, straight(60.0, 8))], minute=5)
    b = session("b", ANCHOR, [(SQUAT, straight(60.0, 8))], minute=4)
    assert b.order_key < a.order_key
