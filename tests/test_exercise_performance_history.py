"""TD-01 PR1: canonical per-exercise historical performance service.

Selection (H-S*), corruption and availability (H-C*), exercise identity
(H-I1-I4), ownership (H-O1-O3), ordering (H-D*) and runtime read-only proof
(H-R1, H-R2) on SQLite. PostgreSQL-authoritative behaviour (tie collation,
production dialect) lives in ``test_exercise_performance_history_pg.py``.
"""
import dataclasses
import json
import math
import random
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import WorkoutSession
from app.services import exercise_catalog
from app.services.exercise_catalog import (
    CatalogConfigurationError,
    ExerciseIdentityInvalid,
    ExerciseInactive,
    ExerciseUnknown,
    resolve_historical_exercise,
)
from app.services.exercise_performance_history import (
    Coverage,
    ExerciseHistory,
    Occurrence,
    PerformedSet,
    ProjectionInvariantError,
    UnavailableSession,
    build_exercise_history,
    queries,
    selection,
)
from app.services.exercise_performance_history.models import (
    HISTORY_DAYS,
    MAX_OCCURRENCES,
    MAX_SCAN_ROWS,
    REASON_CHECKPOINT_INVALID,
    REASON_CHECKPOINT_MISSING,
    REASON_SESSION_INVALID,
    ROW_ELIGIBLE,
    ROW_EXCLUDED,
    ROW_UNAVAILABLE,
    STATES,
)
from app.services.workout_session.errors import InvalidSessionRequest
from app.timeutil import APP_TZ, audit_clock
from tests.exercise_history_support import (
    ANCHOR, ANCHOR_NOW, BENCH, PUSH_UP, ROW, SQUAT, candidate, checkpoint_json, day, persist,
    snapshot, straight, utc_noon,
)


@pytest.fixture
def owner(make_user):
    return make_user("eph-owner").id


@pytest.fixture
def stranger(make_user):
    return make_user("eph-stranger").id


def read(user_id, exercise_id=SQUAT, when=ANCHOR_NOW):
    with audit_clock(when):
        return build_exercise_history(user_id, exercise_id)


def refs(history):
    return [item.session_ref for item in history.occurrences]


def select(rows, *, exercise_id=SQUAT, scan_limit_reached=False):
    history = selection.select_history(exercise_id, ANCHOR, tuple(rows), scan_limit_reached)
    selection.check_invariants(history)
    return history


class _Statements:
    """Every SQL statement executed while the block is active."""

    def __enter__(self):
        self.statements = []
        self._listener = lambda *args: self.statements.append(args[2])
        event.listen(db.engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        event.remove(db.engine, "before_cursor_execute", self._listener)


# ── Bounds ──────────────────────────────────────────────────────────────────
def test_bounds_are_pinned_to_ti00_values_without_importing_them():
    assert (HISTORY_DAYS, MAX_SCAN_ROWS, MAX_OCCURRENCES) == (56, 64, 8)
    keys = queries.day_keys(ANCHOR)
    assert len(keys) == 57 and keys[0] == "2026-08-06" and keys[-1] == "2026-10-01"
    assert STATES == ("available", "available_with_gaps", "no_history", "undetermined")


# ── Canonical selection ─────────────────────────────────────────────────────
def test_h_s1_completed_session_with_completed_sets_is_an_occurrence(owner):
    persist(owner, candidate("s1", day(-2), [(SQUAT, straight(60.0, 8, 7)),
                                             (BENCH, straight(40.0, 10))], revision=5))
    history = read(owner)
    assert history == ExerciseHistory(
        exercise_id=SQUAT, window_start=day(-56), window_end=ANCHOR, state="available",
        occurrences=(Occurrence(
            session_ref="s1", checkpoint_revision=5, workout_date=day(-2),
            completed_at=utc_noon(day(-2)),
            sets=(PerformedSet(0, 8, 60.0), PerformedSet(1, 7, 60.0))),),
        coverage=Coverage(scanned_sessions=1, excluded_cross_date=0,
                          excluded_missing_completed_at=0, unavailable=(),
                          scan_limit_reached=False, occurrence_limit_reached=False))
    assert history.coverage.complete


@pytest.mark.parametrize("status", ["active", "abandoned"])
def test_h_s2_s3_unfinished_sessions_are_never_scanned(owner, status):
    persist(owner, candidate("live", day(-1)), status=status)
    history = read(owner)
    assert history.state == "no_history"
    assert history.occurrences == () and history.coverage.scanned_sessions == 0


def test_h_s4_uncompleted_prefilled_sets_are_not_facts(owner):
    persist(owner, candidate("pre", day(-1), [(SQUAT, [(0, False, 8, 60.0), (1, False, 8, 60.0)])]))
    history = read(owner)
    assert (history.state, history.occurrences) == ("no_history", ())
    assert history.coverage.scanned_sessions == 1 and history.coverage.complete


def test_h_s5_only_completed_indices_ascending():
    raw = snapshot([(SQUAT, [(2, True, 6, 70.0), (0, True, 8, 60.0), (1, False, 9, 65.0)])])
    history = select([candidate("mix", day(-1), checkpoint_data=json.dumps(raw))])
    assert history.occurrences[0].sets == (PerformedSet(0, 8, 60.0), PerformedSet(2, 6, 70.0))


def test_h_s6_s7_s8_null_is_unknown_and_zero_is_zero():
    history = select([candidate("vals", day(-1), [(SQUAT, [
        (0, True, None, 60.0), (1, True, 8, None), (2, True, 0, 0), (3, True, 0, 0.0)])])])
    sets = history.occurrences[0].sets
    assert sets[0].reps is None and sets[0].weight_kg == 60.0
    assert sets[1].reps == 8 and sets[1].weight_kg is None
    for item in sets[2:]:
        assert type(item.reps) is int and item.reps == 0
        assert type(item.weight_kg) is float and item.weight_kg == 0.0


def test_h_s9_bodyweight_exercise_null_weight_is_never_inferred():
    assert "bodyweight" in exercise_catalog.load_exercise_catalog().by_id[PUSH_UP].equipment
    history = select([candidate("bw", day(-1), [(PUSH_UP, [(0, True, 15, None)])])],
                     exercise_id=PUSH_UP)
    assert history.occurrences[0].sets == (PerformedSet(0, 15, None),)


def test_h_s10_context_and_prescription_columns_never_change_the_result(owner):
    from tests.ti03_support import context_entry, stored
    valid = stored("ctx", day(-1), [(SQUAT, straight(60.0, 8, 8))],
                   context=[context_entry(SQUAT, 0, rir="2", gap=None)], revision=3)
    persist(owner, candidate("ctx", day(-1), [(SQUAT, straight(60.0, 8, 8))]))
    variants = [(None, None), (valid.execution_context_data, valid.prescription_data),
                ("{not json", "[1, 2"), ('{"bound_revision": 99, "sets": []}', '{"schema_version": 7}')]
    results = []
    for context_data, prescription_data in variants:
        row = WorkoutSession.query.filter_by(public_id="ctx").one()
        row.execution_context_data = context_data
        row.prescription_data = prescription_data
        db.session.commit()
        results.append(read(owner))
    assert all(result == results[0] for result in results)
    assert repr(results[0]) == repr(results[-1])


def test_h_s11_projection_field_sets_are_pinned():
    def names(cls):
        return {field.name for field in dataclasses.fields(cls)}
    assert names(PerformedSet) == {"index", "reps", "weight_kg"}
    assert names(Occurrence) == {"session_ref", "checkpoint_revision", "workout_date",
                                 "completed_at", "sets"}
    assert names(UnavailableSession) == {"workout_date", "completed_at", "reason"}
    assert names(Coverage) == {"scanned_sessions", "excluded_cross_date",
                               "excluded_missing_completed_at", "unavailable",
                               "scan_limit_reached", "occurrence_limit_reached"}
    assert names(ExerciseHistory) == {"exercise_id", "window_start", "window_end", "state",
                                      "occurrences", "coverage"}
    every = set().union(*(names(cls) for cls in (PerformedSet, Occurrence, UnavailableSession,
                                                  Coverage, ExerciseHistory)))
    for word in ("rir", "tempo", "rest", "prescri", "target", "interval", "context"):
        assert not any(word in name for name in every), word


def test_h_s12_execution_context_flag_never_changes_the_result(app, owner):
    persist(owner, candidate("flag", day(-1)))
    app.config["FITX_TRAINING_EXECUTION_CONTEXT_ENABLED"] = False
    off = read(owner)
    app.config["FITX_TRAINING_EXECUTION_CONTEXT_ENABLED"] = True
    assert read(owner) == off


def test_h_s14_null_completed_at_is_excluded_not_a_gap(owner):
    persist(owner, candidate("nulltime", day(-1), completed_at=None))
    history = read(owner)
    assert (history.state, history.occurrences) == ("no_history", ())
    assert history.coverage.excluded_missing_completed_at == 1
    assert history.coverage.unavailable == () and history.coverage.complete


def test_h_s15_cross_date_is_excluded_never_re_dated(owner):
    # completed 00:10 Istanbul on the NEXT day (21:10 UTC on the workout date).
    late = day(-3)
    persist(owner, candidate("late", late, completed_at=datetime(late.year, late.month, late.day, 21, 10)))
    history = read(owner)
    assert (history.state, history.occurrences) == ("no_history", ())
    assert history.coverage.excluded_cross_date == 1 and history.coverage.complete


def test_h_s16_window_edges_and_future_rows(owner):
    for ref, offset in (("minus57", -57), ("minus56", -56), ("today", 0), ("future", 1)):
        persist(owner, candidate(ref, day(offset)))
    history = read(owner)
    assert refs(history) == ["today", "minus56"]
    assert history.coverage.scanned_sessions == 2
    assert (history.window_start, history.window_end) == (day(-56), ANCHOR)


def test_h_s17_window_is_the_istanbul_day_not_the_utc_day(owner):
    # 22:30 UTC on 09-30 is already 01:30 on 10-01 in Istanbul.
    when = datetime(2026, 9, 30, 22, 30, tzinfo=timezone.utc)
    persist(owner, candidate("outside", date(2026, 8, 5)))
    persist(owner, candidate("edge", date(2026, 8, 6)))
    persist(owner, candidate("today", date(2026, 10, 1),
                             completed_at=datetime(2026, 9, 30, 21, 45)))
    history = read(owner, when=when)
    assert history.window_end == date(2026, 10, 1)
    assert refs(history) == ["today", "edge"]


def test_h_s18_present_exercise_without_completed_sets_is_not_a_gap():
    history = select([candidate("zero", day(-1), [(SQUAT, [(0, False, None, None)])]),
                      candidate("other", day(-2), [(BENCH, straight(40.0, 8))])])
    assert history.state == "no_history" and history.coverage.complete


def test_h_s19_more_than_eight_occurrences_returns_the_eight_newest(owner):
    for n in range(10):
        persist(owner, candidate(f"o{n}", day(-n)))
    history = read(owner)
    assert refs(history) == [f"o{n}" for n in range(8)]
    assert history.coverage.occurrence_limit_reached
    assert history.state == "available" and history.coverage.complete


# ── Corruption and availability ─────────────────────────────────────────────
def _only(row):
    return selection.classify(row)


@pytest.mark.parametrize("raw", [
    "not json at all",                                                    # H-C1
    "[1, 2, 3]",                                                          # H-C2 array
    json.dumps({"exercises": []}),                                        # H-C2 wrong keys
    json.dumps({**snapshot([]), "extra": 1}),                             # H-C2 extra key
    checkpoint_json([(SQUAT, straight(60.0, 8)), (SQUAT, straight(60.0, 9))]),   # H-C3
    checkpoint_json([(SQUAT, [(0, True, 8, 60.0), (0, True, 9, 60.0)])]),        # H-C4
    checkpoint_json([(SQUAT, [(0, True, -1, 60.0)])]),                           # H-C5 reps
    checkpoint_json([(SQUAT, [(0, True, 8, 1001.0)])]),                          # H-C5 weight
    checkpoint_json([(SQUAT, [(0, True, 8, True)])]),                            # H-C5 bool weight
    checkpoint_json([(SQUAT, [(0, 1, 8, 60.0)])]),                               # H-C5 completed
    checkpoint_json([(SQUAT, [(0, True, 8, 60.0)])]).replace("60.0", "NaN"),     # H-C5 NaN
    checkpoint_json([(SQUAT, [(20, True, 8, 60.0)])]),                           # index bound
    "", " ",                                                                     # H-C15
], ids=lambda raw: repr(raw)[:40])
def test_h_c1_to_c5_refused_checkpoints_are_checkpoint_invalid(raw):
    for revision in (0, 3):
        verdict = _only(candidate("bad", day(-1), checkpoint_data=raw, revision=revision))
        assert (verdict.row_class, verdict.reason, verdict.workout_date) == (
            ROW_UNAVAILABLE, REASON_CHECKPOINT_INVALID, day(-1))


def test_h_c6_c7_c15_missing_is_exactly_null_at_revision_zero():
    assert _only(candidate("m", day(-1), checkpoint_data=None, revision=0)).reason == REASON_CHECKPOINT_MISSING
    assert _only(candidate("n", day(-1), checkpoint_data=None, revision=1)).reason == REASON_CHECKPOINT_INVALID
    for blank in ("", " "):
        for revision in (0, 1):
            verdict = _only(candidate("e", day(-1), checkpoint_data=blank, revision=revision))
            assert verdict.reason == REASON_CHECKPOINT_INVALID
    # A valid snapshot at revision 0 is decided by the parser alone: eligible,
    # with fact identity revision 0 (H-C7).
    history = select([candidate("r0", day(-1), revision=0)])
    assert history.state == "available"
    assert history.occurrences[0].checkpoint_revision == 0


def test_h_c8_only_corrupt_candidate_is_undetermined_never_no_history(owner):
    persist(owner, candidate("bad", day(-1), checkpoint_data="{broken"))
    history = read(owner)
    assert history.state == "undetermined"
    assert history.coverage.unavailable == (
        UnavailableSession(day(-1), utc_noon(day(-1)), REASON_CHECKPOINT_INVALID),)


def test_h_c9_valid_plus_newer_corrupt_is_available_with_gaps(owner):
    persist(owner, candidate("good", day(-5)))
    persist(owner, candidate("bad", day(-1), checkpoint_data="{broken"))
    history = read(owner)
    assert history.state == "available_with_gaps"
    assert refs(history) == ["good"]
    marker = history.coverage.unavailable[0]
    assert marker.completed_at > history.occurrences[0].completed_at


def test_h_c10_no_partial_salvage_from_a_corrupt_checkpoint():
    # The squat entry is perfectly valid; the bench entry is not. The whole
    # checkpoint is unavailable, so none of its squat sets may appear.
    raw = checkpoint_json([(SQUAT, straight(100.0, 5, 5)), (BENCH, [(0, True, -3, 40.0)])])
    history = select([candidate("half", day(-1), checkpoint_data=raw),
                      candidate("older", day(-9), [(SQUAT, straight(60.0, 8))])])
    assert refs(history) == ["older"]
    assert all(item.weight_kg != 100.0 for occ in history.occurrences for item in occ.sets)
    assert history.state == "available_with_gaps"


def test_h_c11_no_history_never_coexists_with_a_gap_property():
    generator = random.Random(20261010)
    kinds = ("match", "other", "corrupt", "missing", "cross", "nulltime", "unmatched_zero")
    for trial in range(400):
        rows = []
        for n in range(generator.randint(0, 12)):
            kind = generator.choice(kinds)
            on = day(-generator.randint(0, 56))
            ref = f"t{trial}-{n}"
            if kind == "match":
                rows.append(candidate(ref, on))
            elif kind == "other":
                rows.append(candidate(ref, on, [(BENCH, straight(40.0, 8))]))
            elif kind == "corrupt":
                rows.append(candidate(ref, on, checkpoint_data="{x"))
            elif kind == "missing":
                rows.append(candidate(ref, on, checkpoint_data=None, revision=0))
            elif kind == "cross":
                rows.append(candidate(ref, on, completed_at=datetime(on.year, on.month, on.day, 22, 0)))
            elif kind == "nulltime":
                rows.append(candidate(ref, on, completed_at=None))
            else:
                rows.append(candidate(ref, on, [(SQUAT, [(0, False, 8, 60.0)])]))
        rows.sort(key=lambda row: (row.completed_at or datetime.min, row.public_id), reverse=True)
        rows.sort(key=lambda row: row.completed_at is None)
        limit = generator.random() < 0.2
        history = select(rows, scan_limit_reached=limit)
        if history.state == "no_history":
            assert not history.coverage.unavailable and not history.coverage.scan_limit_reached
        has_gap = bool(history.coverage.unavailable) or limit
        assert history.state == {
            (True, False): "available", (True, True): "available_with_gaps",
            (False, False): "no_history", (False, True): "undetermined",
        }[(bool(history.occurrences), has_gap)]


def test_h_c12_scan_limit_without_a_match_is_undetermined(owner):
    for n in range(MAX_SCAN_ROWS):
        persist(owner, candidate(f"b{n:02d}", day(-(n % 50)), [(BENCH, straight(40.0, 8))],
                                 minute=n % 60, second=n // 60))
    history = read(owner)
    assert history.coverage.scanned_sessions == MAX_SCAN_ROWS
    assert history.coverage.scan_limit_reached and history.coverage.unavailable == ()
    assert history.state == "undetermined"


def test_h_c13_gap_older_than_the_eighth_occurrence_still_degrades_the_window(owner):
    for n in range(9):
        persist(owner, candidate(f"o{n}", day(-n)))
    persist(owner, candidate("old-bad", day(-40), checkpoint_data="{broken"))
    history = read(owner)
    assert len(history.occurrences) == 8
    assert history.coverage.occurrence_limit_reached
    assert history.state == "available_with_gaps"
    assert history.coverage.unavailable[0].workout_date == day(-40)


def test_h_c14_scan_truncation_after_eight_matches_degrades_coverage(owner):
    for n in range(MAX_SCAN_ROWS):
        exercises = [(SQUAT, straight(60.0, 8))] if n < 10 else [(BENCH, straight(40.0, 8))]
        persist(owner, candidate(f"r{n:02d}", day(-(n % 50)), exercises,
                                 minute=n % 60, second=n // 60))
    history = read(owner)
    assert len(history.occurrences) == 8 and history.coverage.scan_limit_reached
    assert history.state == "available_with_gaps"


def test_h_c16_failure_classes_stay_distinct_and_unconverted(owner, monkeypatch):
    persist(owner, candidate("good", day(-1)))

    # (a) read failure propagates unchanged
    failure = OperationalError("SELECT", {}, Exception("connection lost"))
    with monkeypatch.context() as patch:
        patch.setattr(db.session, "query", lambda *a, **k: (_ for _ in ()).throw(failure))
        with pytest.raises(OperationalError) as caught:
            read(owner)
    assert caught.value is failure

    # (b) catalog failure, raised before any history read
    def broken_catalog():
        raise CatalogConfigurationError("unable to load exercise catalog")
    with monkeypatch.context() as patch:
        patch.setattr(exercise_catalog, "load_exercise_catalog", broken_catalog)
        with _Statements() as seen, pytest.raises(CatalogConfigurationError) as caught:
            read(owner)
    assert type(caught.value) is CatalogConfigurationError and seen.statements == []

    # (c) an injected invariant violation is ProjectionInvariantError
    original = selection.select_history

    def lying(*args):
        honest = original(*args)
        return replace(honest, state="no_history")
    with monkeypatch.context() as patch:
        patch.setattr(selection, "select_history", lying)
        with pytest.raises(ProjectionInvariantError):
            read(owner)


def test_h_c16_every_source_row_class_is_counted_separately():
    rows = [
        candidate("missing", day(-1), checkpoint_data=None, revision=0),
        candidate("invalid", day(-2), checkpoint_data="{x"),
        candidate("badday", day(-3), workout_date="2026-13-45"),
        candidate("cross", day(-4), completed_at=datetime(2026, 9, 27, 22, 0)),
        candidate("nulltime", day(-5), completed_at=None),
    ]
    history = select(rows)
    coverage = history.coverage
    assert [marker.reason for marker in coverage.unavailable] == [
        REASON_CHECKPOINT_MISSING, REASON_CHECKPOINT_INVALID, REASON_SESSION_INVALID]
    assert (coverage.excluded_cross_date, coverage.excluded_missing_completed_at) == (1, 1)
    assert coverage.scanned_sessions == 5 and history.state == "undetermined"


def test_h_c17_new_account_is_no_history(owner):
    history = read(owner)
    assert history.state == "no_history"
    assert history.coverage.scanned_sessions == 0 and history.coverage.complete


def _valid_history():
    return select([candidate("b", day(-1), [(SQUAT, straight(60.0, 8, 8))]),
                   candidate("a", day(-2), [(SQUAT, straight(60.0, 8))])])


def _with_coverage(history, **changes):
    return replace(history, coverage=replace(history.coverage, **changes))


@pytest.mark.parametrize("mutate", [
    lambda h: replace(h, occurrences=tuple(reversed(h.occurrences))),
    lambda h: replace(h, occurrences=(h.occurrences[0], h.occurrences[0])),
    lambda h: replace(_with_coverage(h, unavailable=(UnavailableSession(
        day(-3), utc_noon(day(-3)), REASON_CHECKPOINT_INVALID),)), state="no_history",
        occurrences=()),
    lambda h: replace(_with_coverage(h, scan_limit_reached=True), state="no_history", occurrences=()),
    lambda h: replace(h, state="available_with_gaps"),
    lambda h: replace(h, state="maybe"),
    lambda h: replace(h, occurrences=h.occurrences * 5),
    lambda h: replace(h, occurrences=(replace(h.occurrences[0], sets=tuple(reversed(
        h.occurrences[0].sets))),) + h.occurrences[1:]),
    lambda h: replace(h, occurrences=(replace(h.occurrences[0], sets=()),) + h.occurrences[1:]),
    lambda h: replace(h, occurrences=(replace(h.occurrences[0], sets=(
        PerformedSet(0, 1001, 60.0),)),) + h.occurrences[1:]),
    lambda h: replace(h, occurrences=(replace(h.occurrences[0], sets=(
        PerformedSet(0, 8, math.nan),)),) + h.occurrences[1:]),
    lambda h: replace(h, occurrences=(replace(h.occurrences[0], sets=(
        PerformedSet(20, 8, 60.0),)),) + h.occurrences[1:]),
    lambda h: replace(h, occurrences=(replace(h.occurrences[0], sets=(
        PerformedSet(0, True, 60.0),)),) + h.occurrences[1:]),
    lambda h: replace(h, occurrences=(replace(h.occurrences[0], sets=(
        PerformedSet(0, 8, 0),)),) + h.occurrences[1:]),
    lambda h: replace(_with_coverage(h, unavailable=(UnavailableSession(
        None, utc_noon(day(-3)), REASON_CHECKPOINT_INVALID),)), state="available_with_gaps"),
    lambda h: replace(_with_coverage(h, unavailable=(UnavailableSession(
        day(-3), utc_noon(day(-3)), REASON_SESSION_INVALID),)), state="available_with_gaps"),
    lambda h: replace(_with_coverage(h, unavailable=(UnavailableSession(
        day(-3), utc_noon(day(-3)), "checkpoint_lost"),)), state="available_with_gaps"),
], ids=["order", "duplicate", "no_history_with_gap", "no_history_with_scan_limit",
        "wrong_state", "unknown_state", "too_many", "unordered_sets", "no_sets", "reps_range",
        "weight_nan", "index_range", "bool_reps", "int_weight", "dated_reason_without_date",
        "session_invalid_with_date", "unknown_reason"])
def test_h_c18_invariant_check_refuses_each_violation(mutate):
    valid = _valid_history()
    selection.check_invariants(valid)          # non-vacuity: the baseline passes
    with pytest.raises(ProjectionInvariantError):
        selection.check_invariants(mutate(valid))


def test_h_c19a_parser_refusal_is_checkpoint_invalid():
    verdict = _only(candidate("refused", day(-1), checkpoint_data=json.dumps({"x": 1})))
    assert (verdict.row_class, verdict.reason) == (ROW_UNAVAILABLE, REASON_CHECKPOINT_INVALID)


@pytest.mark.parametrize("error", [RuntimeError("bug"), KeyError("bug"), ValueError("bug")],
                         ids=["RuntimeError", "KeyError", "ValueError"])
def test_h_c19b_unexpected_parser_exceptions_propagate_unchanged(owner, monkeypatch, error):
    persist(owner, candidate("any", day(-1)))

    def exploding(_snapshot):
        raise error
    monkeypatch.setattr(selection, "parse_stored_exercises", exploding)
    with pytest.raises(type(error)) as caught:
        read(owner)
    assert caught.value is error


def test_h_c19c_pathological_json_is_recursion_error_not_a_gap(owner):
    persist(owner, candidate("deep", day(-1), checkpoint_data="[" * 200_000 + "]" * 200_000))
    with pytest.raises(RecursionError):
        read(owner)


@pytest.mark.parametrize("bad_day", ["2026-13-45", "garbage", "", None, 20261001, date(2026, 10, 1)],
                         ids=repr)
def test_h_c20_unreadable_workout_date_is_session_invalid_with_no_date(bad_day):
    stored_at = utc_noon(day(-1))
    rows = [candidate("badday", day(-1), workout_date=bad_day, completed_at=stored_at),
            candidate("invalid", day(-2), checkpoint_data="{x"),
            candidate("missing", day(-3), checkpoint_data=None, revision=0)]
    verdict = _only(rows[0])
    assert (verdict.row_class, verdict.reason, verdict.workout_date) == (
        ROW_UNAVAILABLE, REASON_SESSION_INVALID, None)
    history = select(rows)
    assert history.coverage.unavailable == (
        UnavailableSession(None, stored_at, REASON_SESSION_INVALID),
        UnavailableSession(day(-2), utc_noon(day(-2)), REASON_CHECKPOINT_INVALID),
        UnavailableSession(day(-3), utc_noon(day(-3)), REASON_CHECKPOINT_MISSING))
    assert history.state == "undetermined" and not history.coverage.complete


def test_h_c20_session_invalid_keeps_a_missing_completed_at_as_stored():
    history = select([candidate("bad", day(-1), workout_date="garbage", completed_at=None)])
    assert history.coverage.unavailable == (UnavailableSession(None, None, REASON_SESSION_INVALID),)


def test_dating_exclusion_takes_precedence_over_a_corrupt_checkpoint():
    rows = [candidate("x1", day(-1), completed_at=None, checkpoint_data="{x"),
            candidate("x2", day(-2), completed_at=datetime(2026, 9, 29, 22, 0), checkpoint_data="{x")]
    assert [_only(row).row_class for row in rows] == [ROW_EXCLUDED, ROW_EXCLUDED]
    history = select(rows)
    assert history.state == "no_history" and history.coverage.unavailable == ()


# ── Exercise identity ───────────────────────────────────────────────────────
def test_h_i1_active_exercise_is_served(owner):
    persist(owner, candidate("a", day(-1)))
    assert read(owner).state == "available"


def _catalog_with_inactive(exercise_id):
    catalog = exercise_catalog.load_exercise_catalog()
    retired = replace(catalog.by_id[exercise_id], active=False)
    by_id = dict(catalog.by_id)
    by_id[exercise_id] = retired
    exercises = tuple(retired if item.exercise_id == exercise_id else item
                      for item in catalog.exercises)
    return replace(catalog, exercises=exercises, by_id=MappingProxyType(by_id))


def test_h_i2_retired_exercise_is_served_identically(owner, monkeypatch):
    persist(owner, candidate("a", day(-1)))
    active = read(owner)
    retired_catalog = _catalog_with_inactive(SQUAT)
    with pytest.raises(ExerciseInactive):
        exercise_catalog.resolve_exercise(SQUAT, catalog=retired_catalog)

    def forbidden(*args, **kwargs):
        raise AssertionError("the active-only resolver must not be used for history")
    monkeypatch.setattr(exercise_catalog, "load_exercise_catalog", lambda: retired_catalog)
    monkeypatch.setattr(exercise_catalog, "resolve_exercise", forbidden)
    assert resolve_historical_exercise(SQUAT).active is False
    assert read(owner) == active


def test_h_i3_unknown_exercise_has_the_exact_type_and_reads_nothing(owner):
    with _Statements() as seen, pytest.raises(ExerciseUnknown) as caught:
        read(owner, "ex_never_assigned")
    assert type(caught.value) is ExerciseUnknown and seen.statements == []
    unknown_max = "ex_" + "z" * 61
    assert len(unknown_max) == 64
    with pytest.raises(ExerciseUnknown) as caught:
        resolve_historical_exercise(unknown_max)
    assert type(caught.value) is ExerciseUnknown


@pytest.mark.parametrize("bad", ["Squat", "ex-", "ex_A", "ex_" + "a" * 62, "%00", "", None, 7,
                                 "ex_barbell_back_squat\n", " ex_barbell_back_squat", "ex_"],
                         ids=repr)
def test_h_i4_malformed_exercise_is_exactly_identity_invalid(owner, bad):
    with _Statements() as seen, pytest.raises(ExerciseIdentityInvalid) as caught:
        read(owner, bad)
    assert type(caught.value) is ExerciseIdentityInvalid and seen.statements == []


def test_active_only_resolver_is_unchanged():
    assert exercise_catalog.resolve_exercise(SQUAT).exercise_id == SQUAT
    with pytest.raises(ExerciseIdentityInvalid) as caught:
        exercise_catalog.resolve_exercise("ex_never_assigned")
    assert type(caught.value) is ExerciseIdentityInvalid
    with pytest.raises(ExerciseInactive):
        exercise_catalog.resolve_exercise(SQUAT, catalog=_catalog_with_inactive(SQUAT))


# ── Ownership ───────────────────────────────────────────────────────────────
def test_h_o1_o2_owner_isolation_in_both_directions(owner, stranger):
    persist(owner, candidate("a-1", day(-1)))
    persist(owner, candidate("a-2", day(-8), [(SQUAT, straight(55.0, 10))]))
    before = read(owner)
    persist(stranger, candidate("b-1", day(-1), [(SQUAT, straight(200.0, 1))]))
    persist(stranger, candidate("b-2", day(-8)))
    assert read(owner) == before
    assert refs(before) == ["a-1", "a-2"]
    assert refs(read(stranger)) == ["b-1", "b-2"]
    assert read(stranger).occurrences[0].sets == (PerformedSet(0, 1, 200.0),)


def test_h_o3_another_owners_corrupt_rows_never_degrade_coverage(owner, stranger):
    persist(owner, candidate("a", day(-1)))
    persist(stranger, candidate("b-bad", day(-1), checkpoint_data="{broken"))
    persist(stranger, candidate("b-missing", day(-2), checkpoint_data=None, revision=0))
    assert read(owner).state == "available"
    assert read(stranger).state == "undetermined"


@pytest.mark.parametrize("bad_owner", [None, "1", True, 1.0])
def test_owner_must_be_an_explicit_integer(owner, bad_owner):
    persist(owner, candidate("a", day(-1)))
    with _Statements() as seen, pytest.raises(TypeError):
        read(bad_owner)
    assert seen.statements == []


# ── Ordering ────────────────────────────────────────────────────────────────
def test_h_d1_d2_newest_first_by_completion(owner):
    persist(owner, candidate("old", day(-20)))
    persist(owner, candidate("morning", day(-1), minute=0))
    persist(owner, candidate("evening", day(-1), minute=59))
    persist(owner, candidate("mid", day(-10)))
    history = read(owner)
    assert refs(history) == ["evening", "morning", "mid", "old"]
    assert history.occurrences[0].workout_date == history.occurrences[1].workout_date


TIE_IDS = ("Btie", "atie", "-tie", "_tie")


def test_h_d3_equal_completion_ties_break_by_public_id_byte_order(owner):
    for ref in TIE_IDS:
        persist(owner, candidate(ref, day(-3)))
    assert refs(read(owner)) == ["atie", "_tie", "Btie", "-tie"]
    assert sorted(TIE_IDS, reverse=True) == ["atie", "_tie", "Btie", "-tie"]


def test_h_d4_repeated_reads_are_identical(owner):
    for n in range(5):
        persist(owner, candidate(f"r{n}", day(-n)))
    persist(owner, candidate("bad", day(-6), checkpoint_data="{x"))
    first = read(owner)
    assert read(owner) == first and repr(read(owner)) == repr(first)


# ── Runtime read-only and source proof ──────────────────────────────────────
def _tables_in(statement):
    return set(re.findall(r'\bFROM\s+"?(\w+)', statement, re.IGNORECASE)) | set(
        re.findall(r'\bJOIN\s+"?(\w+)', statement, re.IGNORECASE))


def test_h_r1_exactly_one_select_on_workout_session_only(owner):
    persist(owner, candidate("a", day(-1)))
    persist(owner, candidate("bad", day(-2), checkpoint_data="{x"))
    with _Statements() as seen:
        read(owner)
    assert len(seen.statements) == 1
    statement = seen.statements[0]
    assert statement.lstrip().upper().startswith("SELECT")
    assert _tables_in(statement) == {"workout_session"}
    assert set(re.findall(r'"?(\w+)"?\.\w+', statement)) == {"workout_session"}
    for absent in ("workout_log", "execution_context_data", "prescription_data", "exercise_note",
                   "training_plan", "pump_check", "JOIN", "LIKE", "json"):
        assert absent.lower() not in statement.lower(), absent


def test_h_r1_capture_is_non_vacuous():
    assert _tables_in('SELECT x FROM workout_session JOIN workout_log ON 1') == {
        "workout_session", "workout_log"}


def test_h_r2_no_orm_side_effects_and_no_commit(owner):
    persist(owner, candidate("a", day(-1)))
    persist(owner, candidate("bad", day(-2), checkpoint_data=None, revision=0))
    session = db.session()
    events = []
    commit_listener = lambda *_: events.append("commit")  # noqa: E731
    flush_listener = lambda *_: events.append("flush")  # noqa: E731
    event.listen(session, "after_commit", commit_listener)
    event.listen(session, "after_flush", flush_listener)
    try:
        assert not (session.new or session.dirty or session.deleted)
        read(owner)
        read(owner, BENCH)
        assert not session.new and not session.dirty and not session.deleted
    finally:
        event.remove(session, "after_commit", commit_listener)
        event.remove(session, "after_flush", flush_listener)
    assert events == []
    db.session.expire_all()
    row = WorkoutSession.query.filter_by(public_id="a").one()
    assert (row.status, row.checkpoint_revision) == ("completed", 3)


def test_h_r2_commit_detection_is_non_vacuous(owner):
    session = db.session()
    events = []
    listener = lambda *_: events.append("commit")  # noqa: E731
    event.listen(session, "after_commit", listener)
    try:
        persist(owner, candidate("a", day(-1)))
    finally:
        event.remove(session, "after_commit", listener)
    assert events == ["commit"]
