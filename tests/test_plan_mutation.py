"""Adaptive Coaching Sprint 1 PR1 — canonical training-plan mutation boundary.

Two kinds of test live here.

**Characterization** (``TestCanonicalPlanCharacterization``) pins the *existing*
truths this PR is built on, so a later change that quietly breaks one of them
fails here rather than in production: the active-plan selector is "newest row",
the only pre-existing write path replaces the whole plan, and ``WorkoutLog``
snapshots its own columns (which is *why* a plan mutation cannot rewrite
history).

**Behaviour** covers the new boundary. The tests assert domain semantics —
persisted state, ownership, unrelated state preserved, transaction outcome — not
private method layout or source strings (brief §20).
"""
import itertools
import json

import pytest

from app.services.plan_mutation import (
    AddExerciseCommand,
    AmbiguousExerciseTarget,
    DayNotFound,
    ExerciseNotFound,
    InvalidMutation,
    InvalidPrescription,
    MoveTrainingDayCommand,
    MutationContext,
    PlanNotFound,
    PlanNotMutable,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
    apply_plan_mutation,
)
from app.services.plan_mutation.document import apply_command


_KEYS = itertools.count()


def _mutate(user_id, command, context=None):
    """Call the boundary the way an independent request would.

    Sprint 1 PR2 made the mutation envelope mandatory, so every call now carries
    an operation key. These PR1 tests are about *plan semantics*, and each of
    them means "a separate request" — so the default is a fresh key per call.
    Reusing one key here would silently turn the second call of a two-step test
    into a replay and quietly stop testing the mutation.

    Replay behaviour itself is not tested by omission; it is tested explicitly,
    with a deliberately reused key, in tests/test_plan_mutation_history.py.
    """
    if context is None:
        context = MutationContext(idempotency_key="pr1-case-%08d" % next(_KEYS))
    return apply_plan_mutation(user_id, command, context)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _seven_day_program():
    """A canonical 7-day program in the exact shape the generator persists.

    Deliberately carries an unknown top-level key (``haftalik_ozet``) and an
    unknown per-exercise key (``tempo``) so the "unrelated state is preserved"
    assertions prove real preservation of fields this PR does not model, not
    just of the ones it happens to know about.
    """
    return {
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman", "odak": "İtiş", "sure_dk": 45,
             "tahmini_kalori": 320, "egzersizler": [
                 {"isim": "Bench Press", "set": 3, "tekrar": "8-12",
                  "dinlenme": "90 sn", "not": "kontrollü in", "tempo": "3-1-1"},
                 {"isim": "Shoulder Press", "set": 4, "tekrar": "10-12",
                  "dinlenme": "60 sn", "not": ""}]},
            {"gun": "Salı", "tip": "dinlenme", "odak": "Aktif Toparlanma",
             "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Çarşamba", "tip": "antrenman", "odak": "Çekiş", "sure_dk": 50,
             "tahmini_kalori": 340, "egzersizler": [
                 {"isim": "Barbell Row", "set": 4, "tekrar": "6-10",
                  "dinlenme": "2 dk", "not": ""}]},
            {"gun": "Perşembe", "tip": "dinlenme", "odak": "Aktif Toparlanma",
             "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Cuma", "tip": "antrenman", "odak": "Bacak", "sure_dk": 60,
             "tahmini_kalori": 400, "egzersizler": [
                 {"isim": "Squat", "set": 5, "tekrar": "5", "dinlenme": "3 dk",
                  "not": ""}]},
            {"gun": "Cumartesi", "tip": "kardiyo", "odak": "Koşu", "sure_dk": 30,
             "tahmini_kalori": 280, "egzersizler": [
                 {"isim": "Treadmill", "set": 1, "tekrar": "30 dk",
                  "dinlenme": "-", "not": ""}]},
            {"gun": "Pazar", "tip": "dinlenme", "odak": "Aktif Toparlanma",
             "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
        ],
        "haftalik_ozet": {"toplam_antrenman_gun": 3, "yogunluk_skoru": 7},
    }


@pytest.fixture
def seed_plan(app):
    """Persist a plan for a user and return the raw ``plan_data`` text."""
    from app.extensions import db
    from app.models import TrainingPlan

    def _seed(user_id, document=None):
        payload = _seven_day_program() if document is None else document
        text = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False)
        db.session.add(TrainingPlan(user_id=user_id, plan_data=text, score=8))
        db.session.commit()
        return text

    return _seed


def _stored(user_id):
    """The user's canonical active plan document, parsed."""
    from app.services.today_facts import get_active_plan
    return json.loads(get_active_plan(user_id).plan_data)


def _stored_text(user_id):
    from app.services.today_facts import get_active_plan
    return get_active_plan(user_id).plan_data


def _day(document, gun):
    return next(d for d in document["program"] if d["gun"] == gun)


def _names(document, gun):
    return [ex["isim"] for ex in _day(document, gun)["egzersizler"]]


def _other_days(document, gun):
    """Everything except the named day, for unchanged-state comparison."""
    return [d for d in document["program"] if d["gun"] != gun]


# ── Characterization of the pre-existing canonical domain ────────────────────

class TestCanonicalPlanCharacterization:

    def test_active_plan_is_the_newest_row(self, app, make_user, seed_plan):
        from app.extensions import db
        from app.models import TrainingPlan
        from app.services.today_facts import get_active_plan
        from datetime import datetime

        user = make_user("charuser")
        old = TrainingPlan(user_id=user.id, plan_data='{"program": []}',
                           created_at=datetime(2020, 1, 1))
        db.session.add(old)
        db.session.commit()
        seed_plan(user.id)

        assert get_active_plan(user.id).id != old.id
        assert _names(_stored(user.id), "Pazartesi") == [
            "Bench Press", "Shoulder Press"]

    def test_workout_history_snapshots_its_own_columns(self, app, make_user):
        """Why plan mutation cannot rewrite history: nothing in ``WorkoutLog``
        is derived from ``plan_data``."""
        from app.models import WorkoutLog

        columns = set(WorkoutLog.__table__.columns.keys())
        assert {"exercise_name", "sets", "reps", "weight_kg", "volume"} <= columns
        assert not {c for c in columns if "plan" in c}

    def test_canonical_prescription_bounds_are_reused_not_redefined(self):
        """The mutation boundary must not introduce a second bounds authority."""
        from app.services.plan_mutation import validation
        from app.services.training_generation.output_errors import SchemaInvalidError
        from app.services.training_generation.plan_schema import (
            NAME_MAX,
            REPS_MAX,
            SET_MAX,
            SET_MIN,
        )
        from app.services.training_generation.response_validator import _require_int

        assert validation.MIN_SETS == SET_MIN == 1
        assert validation.MAX_SETS == SET_MAX == 100
        assert validation.MAX_EXERCISE_NAME_CHARS == NAME_MAX
        assert validation.MAX_REPS_CHARS == REPS_MAX
        # Generate and mutation both reject; they share plan_schema bounds.
        with pytest.raises(SchemaInvalidError):
            _require_int(SET_MAX + 1, "set", SET_MIN, SET_MAX)
        with pytest.raises(InvalidPrescription):
            validation.validate_sets(SET_MAX + 1)


# ── Ownership ────────────────────────────────────────────────────────────────

class TestOwnership:

    def test_owner_can_mutate_own_plan(self, app, make_user, seed_plan):
        user = make_user("owner")
        seed_plan(user.id)

        result = _mutate(user.id, RemoveExerciseCommand(
            day="Pazartesi", exercise="Shoulder Press"))

        assert result.changed is True
        assert _names(_stored(user.id), "Pazartesi") == ["Bench Press"]

    def test_another_users_plan_is_never_touched(self, app, make_user, seed_plan):
        victim = make_user("victim")
        actor = make_user("actor")
        victim_text = seed_plan(victim.id)
        seed_plan(actor.id)

        _mutate(actor.id, RemoveExerciseCommand(
            day="Pazartesi", exercise="Bench Press"))

        assert _stored_text(victim.id) == victim_text

    def test_mutation_targets_the_row_the_canonical_selector_calls_active(
            self, app, make_user, seed_plan):
        """The boundary must never mutate a plan readers consider inactive."""
        from app.extensions import db
        from app.models import TrainingPlan
        from datetime import datetime

        user = make_user("supersededplan")
        superseded = TrainingPlan(
            user_id=user.id, created_at=datetime(2020, 1, 1),
            plan_data=json.dumps(_seven_day_program(), ensure_ascii=False))
        db.session.add(superseded)
        db.session.commit()
        superseded_text = superseded.plan_data
        seed_plan(user.id)

        _mutate(user.id, RemoveExerciseCommand(
            day="Pazartesi", exercise="Bench Press"))

        assert _names(_stored(user.id), "Pazartesi") == ["Shoulder Press"]
        assert db.session.get(TrainingPlan, superseded.id).plan_data == (
            superseded_text)

    def test_user_without_a_plan_is_rejected(self, app, make_user):
        user = make_user("planless")

        with pytest.raises(PlanNotFound):
            _mutate(user.id, RemoveExerciseCommand(
                day="Pazartesi", exercise="Bench Press"))

    def test_unparseable_plan_is_rejected_without_repair(
            self, app, make_user, seed_plan):
        user = make_user("brokenplan")
        seed_plan(user.id, "not json at all")

        with pytest.raises(PlanNotMutable):
            _mutate(user.id, RemoveExerciseCommand(
                day="Pazartesi", exercise="Bench Press"))
        assert _stored_text(user.id) == "not json at all"


# ── Replace exercise ─────────────────────────────────────────────────────────

class TestReplaceExercise:

    def test_replaces_exactly_the_named_exercise(self, app, make_user, seed_plan):
        user = make_user("replacer")
        seed_plan(user.id)

        _mutate(user.id, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Chest Press"))

        assert _names(_stored(user.id), "Pazartesi") == [
            "Machine Chest Press", "Shoulder Press"]

    def test_replacement_preserves_position_and_prescription(
            self, app, make_user, seed_plan):
        """Contract: a replacement inherits the replaced slot's prescription
        unless the command explicitly overrides it."""
        user = make_user("preserver")
        seed_plan(user.id)

        _mutate(user.id, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Chest Press"))

        replaced = _day(_stored(user.id), "Pazartesi")["egzersizler"][0]
        assert replaced["set"] == 3
        assert replaced["tekrar"] == "8-12"
        assert replaced["dinlenme"] == "90 sn"
        assert replaced["tempo"] == "3-1-1"

    def test_unrelated_days_are_byte_for_byte_unchanged(
            self, app, make_user, seed_plan):
        user = make_user("isolation")
        seed_plan(user.id)
        before = _other_days(_stored(user.id), "Pazartesi")

        _mutate(user.id, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Chest Press"))

        after = _other_days(_stored(user.id), "Pazartesi")
        assert json.dumps(after, ensure_ascii=False) == json.dumps(
            before, ensure_ascii=False)

    def test_unrelated_exercise_in_the_same_day_is_unchanged(
            self, app, make_user, seed_plan):
        user = make_user("siblingsafe")
        seed_plan(user.id)
        before = _day(_stored(user.id), "Pazartesi")["egzersizler"][1]

        _mutate(user.id, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Chest Press"))

        assert _day(_stored(user.id), "Pazartesi")["egzersizler"][1] == before

    def test_plan_level_fields_are_unchanged(self, app, make_user, seed_plan):
        user = make_user("planfields")
        seed_plan(user.id)

        _mutate(user.id, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Chest Press"))

        assert _stored(user.id)["haftalik_ozet"] == {
            "toplam_antrenman_gun": 3, "yogunluk_skoru": 7}

    def test_missing_exercise_is_rejected(self, app, make_user, seed_plan):
        user = make_user("missingex")
        text = seed_plan(user.id)

        with pytest.raises(ExerciseNotFound):
            _mutate(user.id, ReplaceExerciseCommand(
                day="Pazartesi", exercise="Deadlift", replacement="Rack Pull"))
        assert _stored_text(user.id) == text

    def test_unknown_day_is_rejected(self, app, make_user, seed_plan):
        user = make_user("unknownday")
        text = seed_plan(user.id)

        with pytest.raises(DayNotFound):
            _mutate(user.id, ReplaceExerciseCommand(
                day="Funday", exercise="Bench Press", replacement="Push Up"))
        assert _stored_text(user.id) == text

    def test_duplicate_name_in_the_day_is_ambiguous(self, app, make_user, seed_plan):
        document = _seven_day_program()
        _day(document, "Pazartesi")["egzersizler"].append(
            {"isim": "Bench Press", "set": 2, "tekrar": "15", "dinlenme": "45 sn"})
        user = make_user("ambiguous")
        text = seed_plan(user.id, document)

        with pytest.raises(AmbiguousExerciseTarget):
            _mutate(user.id, ReplaceExerciseCommand(
                day="Pazartesi", exercise="Bench Press",
                replacement="Machine Chest Press"))
        assert _stored_text(user.id) == text

    def test_replacement_name_is_validated(self, app, make_user, seed_plan):
        user = make_user("badreplacement")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            _mutate(user.id, ReplaceExerciseCommand(
                day="Pazartesi", exercise="Bench Press", replacement="   "))
        assert _stored_text(user.id) == text


# ── Add exercise ─────────────────────────────────────────────────────────────

class TestAddExercise:

    def test_appends_to_the_target_day(self, app, make_user, seed_plan):
        user = make_user("adder")
        seed_plan(user.id)

        _mutate(user.id, AddExerciseCommand(
            day="Çarşamba", exercise="Lat Pulldown", sets=3, reps="10-12"))

        assert _names(_stored(user.id), "Çarşamba") == [
            "Barbell Row", "Lat Pulldown"]

    def test_added_exercise_carries_the_explicit_prescription(
            self, app, make_user, seed_plan):
        user = make_user("adderx")
        seed_plan(user.id)

        _mutate(user.id, AddExerciseCommand(
            day="Çarşamba", exercise="Lat Pulldown", sets=3, reps="10-12"))

        added = _day(_stored(user.id), "Çarşamba")["egzersizler"][-1]
        assert added["isim"] == "Lat Pulldown"
        assert added["set"] == 3
        assert added["tekrar"] == "10-12"

    def test_unrelated_days_unchanged_on_add(self, app, make_user, seed_plan):
        user = make_user("addisolation")
        seed_plan(user.id)
        before = _other_days(_stored(user.id), "Çarşamba")

        _mutate(user.id, AddExerciseCommand(
            day="Çarşamba", exercise="Lat Pulldown", sets=3, reps="10-12"))

        assert _other_days(_stored(user.id), "Çarşamba") == before

    def test_prescription_is_required_and_never_invented(
            self, app, make_user, seed_plan):
        user = make_user("noinvent")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            _mutate(user.id, AddExerciseCommand(
                day="Çarşamba", exercise="Lat Pulldown", sets=None, reps=None))
        assert _stored_text(user.id) == text

    def test_malformed_exercise_name_is_rejected(self, app, make_user, seed_plan):
        user = make_user("badname")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            _mutate(user.id, AddExerciseCommand(
                day="Çarşamba", exercise="", sets=3, reps="10"))
        assert _stored_text(user.id) == text

    def test_adding_to_a_rest_day_is_rejected(self, app, make_user, seed_plan):
        """A rest day carrying exercises is not a structurally valid plan."""
        user = make_user("restadd")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            _mutate(user.id, AddExerciseCommand(
                day="Salı", exercise="Lat Pulldown", sets=3, reps="10"))
        assert _stored_text(user.id) == text


# ── Remove exercise ──────────────────────────────────────────────────────────

class TestRemoveExercise:

    def test_removes_exactly_the_named_exercise(self, app, make_user, seed_plan):
        user = make_user("remover")
        seed_plan(user.id)

        _mutate(user.id, RemoveExerciseCommand(
            day="Pazartesi", exercise="Bench Press"))

        assert _names(_stored(user.id), "Pazartesi") == ["Shoulder Press"]

    def test_missing_exercise_is_rejected(self, app, make_user, seed_plan):
        user = make_user("removemissing")
        text = seed_plan(user.id)

        with pytest.raises(ExerciseNotFound):
            _mutate(user.id, RemoveExerciseCommand(
                day="Pazartesi", exercise="Deadlift"))
        assert _stored_text(user.id) == text

    def test_removing_the_last_exercise_of_a_training_day_is_rejected(
            self, app, make_user, seed_plan):
        """Canonical validation: a training day must keep at least one exercise."""
        user = make_user("lastexercise")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            _mutate(user.id, RemoveExerciseCommand(
                day="Çarşamba", exercise="Barbell Row"))
        assert _stored_text(user.id) == text

    def test_unrelated_days_unchanged_on_remove(self, app, make_user, seed_plan):
        user = make_user("removeisolation")
        seed_plan(user.id)
        before = _other_days(_stored(user.id), "Pazartesi")

        _mutate(user.id, RemoveExerciseCommand(
            day="Pazartesi", exercise="Bench Press"))

        assert _other_days(_stored(user.id), "Pazartesi") == before


# ── Prescription update ──────────────────────────────────────────────────────

class TestUpdatePrescription:

    def test_sets_update_changes_only_sets(self, app, make_user, seed_plan):
        user = make_user("setsonly")
        seed_plan(user.id)
        before = _day(_stored(user.id), "Pazartesi")["egzersizler"][0]

        _mutate(user.id, UpdateExercisePrescriptionCommand(
            day="Pazartesi", exercise="Bench Press", sets=5))

        after = _day(_stored(user.id), "Pazartesi")["egzersizler"][0]
        assert after["set"] == 5
        assert {k: v for k, v in after.items() if k != "set"} == {
            k: v for k, v in before.items() if k != "set"}

    def test_reps_update_changes_only_reps(self, app, make_user, seed_plan):
        user = make_user("repsonly")
        seed_plan(user.id)
        before = _day(_stored(user.id), "Pazartesi")["egzersizler"][0]

        _mutate(user.id, UpdateExercisePrescriptionCommand(
            day="Pazartesi", exercise="Bench Press", reps="5-8"))

        after = _day(_stored(user.id), "Pazartesi")["egzersizler"][0]
        assert after["tekrar"] == "5-8"
        assert {k: v for k, v in after.items() if k != "tekrar"} == {
            k: v for k, v in before.items() if k != "tekrar"}

    def test_sets_below_lower_bound_rejected(self, app, make_user, seed_plan):
        user = make_user("lowbound")
        text = seed_plan(user.id)

        with pytest.raises(InvalidPrescription):
            _mutate(user.id, UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", sets=0))
        assert _stored_text(user.id) == text

    def test_sets_above_upper_bound_rejected(self, app, make_user, seed_plan):
        user = make_user("highbound")
        text = seed_plan(user.id)

        with pytest.raises(InvalidPrescription):
            _mutate(user.id, UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", sets=101))
        assert _stored_text(user.id) == text

    def test_malformed_reps_rejected(self, app, make_user, seed_plan):
        user = make_user("badreps")
        text = seed_plan(user.id)

        with pytest.raises(InvalidPrescription):
            _mutate(user.id, UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", reps="x" * 41))
        assert _stored_text(user.id) == text

    def test_empty_command_is_rejected(self, app, make_user, seed_plan):
        user = make_user("emptycmd")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            _mutate(user.id, UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press"))
        assert _stored_text(user.id) == text

    def test_no_op_does_not_rewrite_the_plan(self, app, make_user, seed_plan):
        """Requested state already true → deterministic no-op, no churn."""
        user = make_user("noop")
        text = seed_plan(user.id)

        result = _mutate(user.id, UpdateExercisePrescriptionCommand(
            day="Pazartesi", exercise="Bench Press", sets=3))

        assert result.changed is False
        assert _stored_text(user.id) == text


# ── Move training day ────────────────────────────────────────────────────────

class TestMoveTrainingDay:

    def test_moving_swaps_day_content_and_keeps_weekday_labels(
            self, app, make_user, seed_plan):
        user = make_user("mover")
        seed_plan(user.id)

        _mutate(user.id, MoveTrainingDayCommand(
            day="Pazartesi", target_day="Salı"))

        document = _stored(user.id)
        assert _names(document, "Salı") == ["Bench Press", "Shoulder Press"]
        assert _day(document, "Salı")["tip"] == "antrenman"
        assert _day(document, "Pazartesi")["tip"] == "dinlenme"
        assert [d["gun"] for d in document["program"]] == [
            "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma",
            "Cumartesi", "Pazar"]

    def test_moving_leaves_completed_history_untouched(
            self, app, make_user, seed_plan):
        from app.extensions import db
        from app.models import WorkoutLog

        user = make_user("historysafe")
        seed_plan(user.id)
        db.session.add(WorkoutLog(user_id=user.id, exercise_name="Bench Press",
                                  sets=3, reps=10, weight_kg=80, volume=2400))
        db.session.commit()

        _mutate(user.id, MoveTrainingDayCommand(
            day="Pazartesi", target_day="Salı"))

        log = WorkoutLog.query.filter_by(user_id=user.id).one()
        assert (log.exercise_name, log.sets, log.reps, log.volume) == (
            "Bench Press", 3, 10, 2400)

    def test_moving_a_day_onto_itself_is_a_no_op(self, app, make_user, seed_plan):
        user = make_user("selfmove")
        text = seed_plan(user.id)

        result = _mutate(user.id, MoveTrainingDayCommand(
            day="Pazartesi", target_day="Pazartesi"))

        assert result.changed is False
        assert _stored_text(user.id) == text

    def test_unknown_target_day_is_rejected(self, app, make_user, seed_plan):
        user = make_user("badtarget")
        text = seed_plan(user.id)

        with pytest.raises(DayNotFound):
            _mutate(user.id, MoveTrainingDayCommand(
                day="Pazartesi", target_day="Funday"))
        assert _stored_text(user.id) == text


# ── Historical safety ────────────────────────────────────────────────────────

class TestHistoricalSafety:

    def test_plan_mutation_leaves_workout_logs_untouched(
            self, app, make_user, seed_plan):
        from app.extensions import db
        from app.models import WorkoutLog

        user = make_user("nohistoryrewrite")
        seed_plan(user.id)
        db.session.add(WorkoutLog(user_id=user.id, exercise_name="Bench Press",
                                  sets=3, reps=10, weight_kg=80, volume=2400))
        db.session.commit()

        _mutate(user.id, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Chest Press"))

        log = WorkoutLog.query.filter_by(user_id=user.id).one()
        assert log.exercise_name == "Bench Press"
        assert log.volume == 2400

    def test_plan_mutation_creates_no_history_rows(self, app, make_user, seed_plan):
        from app.models import PumpCheck, WorkoutLog

        user = make_user("nohistorycreate")
        seed_plan(user.id)

        _mutate(user.id, UpdateExercisePrescriptionCommand(
            day="Pazartesi", exercise="Bench Press", sets=4))

        assert WorkoutLog.query.filter_by(user_id=user.id).count() == 0
        assert PumpCheck.query.filter_by(user_id=user.id).count() == 0


# ── Atomicity ────────────────────────────────────────────────────────────────

class TestAtomicity:

    def test_validation_failure_leaves_the_plan_unchanged(
            self, app, make_user, seed_plan):
        user = make_user("atomicvalidate")
        text = seed_plan(user.id)

        with pytest.raises(InvalidPrescription):
            _mutate(user.id, UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", sets=999))

        from app.extensions import db
        db.session.expire_all()
        assert _stored_text(user.id) == text

    def test_persistence_failure_leaves_no_partial_mutation(
            self, app, make_user, seed_plan, monkeypatch):
        from app.extensions import db

        user = make_user("atomicpersist")
        text = seed_plan(user.id)

        def _boom():
            raise RuntimeError("commit exploded")

        monkeypatch.setattr(db.session, "commit", _boom)

        with pytest.raises(RuntimeError):
            _mutate(user.id, ReplaceExerciseCommand(
                day="Pazartesi", exercise="Bench Press",
                replacement="Machine Chest Press"))

        monkeypatch.undo()
        db.session.rollback()
        db.session.expire_all()
        assert _stored_text(user.id) == text

    def test_a_rejected_command_type_is_refused_before_any_read(
            self, app, make_user, seed_plan):
        user = make_user("badcommand")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            _mutate(user.id, {"day": "Pazartesi", "sets": 3})
        assert _stored_text(user.id) == text


# ── Structural validity of the result ────────────────────────────────────────

class TestResultingPlanStaysValid:

    def test_mutated_plan_is_still_readable_by_the_canonical_read_layer(
            self, app, make_user, seed_plan):
        from app.services.plan_facts import gather_plan_facts

        user = make_user("stillreadable")
        seed_plan(user.id)

        _mutate(user.id, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Chest Press"))

        facts = gather_plan_facts(user.id)
        assert facts.read_ok and facts.has_active_plan and facts.parse_ok
        assert len(facts.days) == 7
        assert facts.days[0].exercises[0].name == "Machine Chest Press"

    def test_mutated_plan_keeps_seven_days_with_unique_weekdays(
            self, app, make_user, seed_plan):
        user = make_user("sevendays")
        seed_plan(user.id)

        _mutate(user.id, AddExerciseCommand(
            day="Cuma", exercise="Leg Press", sets=3, reps="12"))

        program = _stored(user.id)["program"]
        assert len(program) == 7
        assert len({d["gun"] for d in program}) == 7


# ── Sprint 11 PR4 Task 5 — canonical exercise authority in mutations ─────────
#
# The canonical documents below are exercised through ``apply_command`` — the
# pure engine — rather than through the persistence service. Identity is a
# *document* property, and the point of the pure layer is that the whole matrix
# can be proven without a database. Journal, replay and undo behaviour over
# canonical plans is proven where it lives, in
# tests/test_plan_mutation_history.py.

#: Day-one content per equipment context. A canonical plan can only ever hold
#: exercises its own context allows — that is exactly what the Task 4 save
#: boundary guarantees — so the fixture never fabricates a document the product
#: could not have persisted.
_CANONICAL_FIRST_DAY = {
    "minimal": [
        {"isim": "Dumbbell Row", "set": 3, "tekrar": "8-12",
         "dinlenme": "90 sn", "not": "kontrollü çek", "tempo": "3-1-1",
         "exercise_id": "ex_dumbbell_row"},
        {"isim": "Push-Up", "set": 3, "tekrar": "10-15", "dinlenme": "60 sn",
         "not": "", "exercise_id": "ex_push_up"},
    ],
    "ev": [
        {"isim": "Push-Up", "set": 3, "tekrar": "10-15", "dinlenme": "60 sn",
         "not": "", "exercise_id": "ex_push_up"},
        {"isim": "Inverted Row", "set": 3, "tekrar": "8-12",
         "dinlenme": "90 sn", "not": "", "exercise_id": "ex_inverted_row"},
    ],
}


def _canonical_body(equipment_context):
    """The seven canonical days, minus the context block.

    Every day after the first is bodyweight or cardio, so the same body is
    honest under both equipment contexts. ``Cumartesi`` is a real ``kardiyo``
    day, which is what makes the cardio-placement rule testable in both
    directions rather than only as a refusal.
    """
    import copy as _copy

    return {
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman", "odak": "Üst Vücut",
             "sure_dk": 45, "tahmini_kalori": 320,
             "egzersizler": _copy.deepcopy(
                 _CANONICAL_FIRST_DAY[equipment_context])},
            {"gun": "Salı", "tip": "dinlenme", "odak": "Aktif Toparlanma",
             "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Çarşamba", "tip": "antrenman", "odak": "Alt Vücut",
             "sure_dk": 40, "tahmini_kalori": 300, "egzersizler": [
                 {"isim": "Bodyweight Squat", "set": 4, "tekrar": "12-15",
                  "dinlenme": "60 sn", "not": "",
                  "exercise_id": "ex_bodyweight_squat"}]},
            {"gun": "Perşembe", "tip": "dinlenme", "odak": "Aktif Toparlanma",
             "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Cuma", "tip": "antrenman", "odak": "Merkez",
             "sure_dk": 30, "tahmini_kalori": 200, "egzersizler": [
                 {"isim": "Plank", "set": 3, "tekrar": "45 sn",
                  "dinlenme": "45 sn", "not": "", "exercise_id": "ex_plank"}]},
            {"gun": "Cumartesi", "tip": "kardiyo", "odak": "Tempolu Yürüyüş",
             "sure_dk": 30, "tahmini_kalori": 250, "egzersizler": [
                 {"isim": "Brisk Walk", "set": 1, "tekrar": "30 dk",
                  "dinlenme": "-", "not": "",
                  "exercise_id": "ex_brisk_walk"}]},
            {"gun": "Pazar", "tip": "dinlenme", "odak": "Aktif Toparlanma",
             "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
        ],
        "haftalik_ozet": {"toplam_antrenman_gun": 4, "yogunluk_skoru": 6},
    }


def canonical_document(equipment_context="minimal", cardio_type="yuruyus",
                       catalog_version=1):
    """A plan document in exactly the shape the Task 4 save boundary persists."""
    document = _canonical_body(equipment_context)
    document["exercise_context"] = {
        "equipment_context": equipment_context,
        "cardio_type": cardio_type,
        "style": "general_fitness",
        "catalog_version": catalog_version,
    }
    return document


def _document_with_raw_context(raw):
    document = _canonical_body("minimal")
    document["exercise_context"] = raw
    return document


def _legacy_document():
    """A pre-PR4 plan: names only, no identity, no context block."""
    return _seven_day_program()


def _entry(document, gun, index=0):
    return _day(document, gun)["egzersizler"][index]


def _identity_disagreements(document):
    """Entries whose stored ``exercise_id`` and ``isim`` do not agree.

    The catalog is asked, not a local table: an entry is coherent only when the
    ID it stores resolves to the exact canonical name it displays. This is the
    invariant P1-4 broke — a replace that rewrote ``isim`` and left
    ``exercise_id`` pointing at the exercise that used to be in the slot.
    """
    from app.services.exercise_catalog import resolve_exercise

    bad = []
    for day in document["program"]:
        for entry in day["egzersizler"]:
            exercise_id = entry.get("exercise_id")
            if exercise_id is None:
                bad.append((day["gun"], entry.get("isim"), None))
                continue
            resolved = resolve_exercise(exercise_id=exercise_id)
            if resolved.canonical_name != entry.get("isim"):
                bad.append((day["gun"], entry.get("isim"), exercise_id))
    return bad


class TestCanonicalExerciseAuthority:
    """A document carrying a verified ``exercise_context`` is canonical: the
    catalog, not free text, decides what an exercise is."""

    def test_replace_on_canonical_plan_preserves_slot_and_writes_identity(self):
        """Both halves of the authority claim in one command: the *target* is
        found through the stable ID its alias resolves to, and the
        *replacement* is written as catalog identity rather than as the words
        the caller happened to use."""
        document = canonical_document(equipment_context="minimal")

        mutated, changed = apply_command(
            document,
            ReplaceExerciseCommand(
                day="Pazartesi",
                exercise="One-Arm Dumbbell Row",
                replacement="Band Row",
            ),
        )

        ex = mutated["program"][0]["egzersizler"][0]
        assert changed is True
        assert ex["exercise_id"] == "ex_band_row"
        assert ex["isim"] == "Resistance Band Row"

    def test_canonical_plan_rejects_incompatible_coach_replacement(self):
        with pytest.raises(InvalidMutation):
            apply_command(
                canonical_document(equipment_context="ev"),
                ReplaceExerciseCommand(
                    day="Pazartesi",
                    exercise="Push-Up",
                    replacement="Barbell Back Squat",
                ),
            )

    def test_canonical_replace_rewrites_identity_and_name_together(self):
        """P1-4. Before this task ``_apply_replace`` wrote ``isim`` alone, so a
        canonical plan ended up displaying one exercise while storing another
        one's ID — a plan whose persisted identity is a lie."""
        document = canonical_document(equipment_context="minimal")

        mutated, _changed = apply_command(document, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Dumbbell Row", replacement="Band Row"))

        entry = _entry(mutated, "Pazartesi")
        assert entry["exercise_id"] == "ex_band_row"
        assert entry["isim"] == "Resistance Band Row"
        assert _identity_disagreements(mutated) == []

    def test_no_canonical_command_can_leave_identity_and_name_disagreeing(self):
        """P1-4 as the invariant rather than as one command's outcome: every
        identity-touching path writes ``exercise_id`` and ``isim`` together, or
        writes neither."""
        commands = [
            ReplaceExerciseCommand(day="Pazartesi", exercise="Dumbbell Row",
                                   replacement="Band Row"),
            ReplaceExerciseCommand(day="Pazartesi", exercise="Push-Up",
                                   replacement="Inverted Row", sets=4,
                                   reps="6-8"),
            AddExerciseCommand(day="Çarşamba", exercise="Goblet Squat",
                               sets=3, reps="10"),
            RemoveExerciseCommand(day="Pazartesi", exercise="Push-Up"),
            UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Dumbbell Row", sets=5),
            MoveTrainingDayCommand(day="Pazartesi", target_day="Salı"),
        ]

        for command in commands:
            mutated, changed = apply_command(canonical_document(), command)
            assert changed is True, command
            assert _identity_disagreements(mutated) == [], command

    def test_canonical_replace_preserves_position_prescription_and_unknowns(self):
        document = canonical_document(equipment_context="minimal")

        mutated, _changed = apply_command(document, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Dumbbell Row", replacement="Band Row"))

        entry = _entry(mutated, "Pazartesi")
        assert _names(mutated, "Pazartesi") == ["Resistance Band Row", "Push-Up"]
        assert entry["set"] == 3
        assert entry["tekrar"] == "8-12"
        assert entry["dinlenme"] == "90 sn"
        assert entry["not"] == "kontrollü çek"
        assert entry["tempo"] == "3-1-1"

    def test_canonical_replace_leaves_every_other_day_untouched(self):
        document = canonical_document(equipment_context="minimal")
        before = json.dumps(_other_days(document, "Pazartesi"),
                            ensure_ascii=False)

        mutated, _changed = apply_command(document, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Dumbbell Row", replacement="Band Row"))

        assert json.dumps(_other_days(mutated, "Pazartesi"),
                          ensure_ascii=False) == before
        assert mutated["exercise_context"] == document["exercise_context"]
        assert mutated["haftalik_ozet"] == document["haftalik_ozet"]

    def test_canonical_add_resolves_an_alias_to_catalog_identity(self):
        document = canonical_document(equipment_context="minimal")

        mutated, changed = apply_command(document, AddExerciseCommand(
            day="Çarşamba", exercise="Band Row", sets=3, reps="10-12"))

        added = _day(mutated, "Çarşamba")["egzersizler"][-1]
        assert changed is True
        assert added["isim"] == "Resistance Band Row"
        assert added["exercise_id"] == "ex_band_row"
        assert added["set"] == 3
        assert added["tekrar"] == "10-12"

    def test_canonical_add_rejects_an_exercise_the_catalog_does_not_know(self):
        document = canonical_document(equipment_context="minimal")

        with pytest.raises(InvalidMutation):
            apply_command(document, AddExerciseCommand(
                day="Çarşamba", exercise="Machine Chest Press", sets=3,
                reps="10"))

    def test_canonical_add_rejects_an_exercise_the_context_does_not_allow(self):
        document = canonical_document(equipment_context="ev")

        with pytest.raises(InvalidMutation):
            apply_command(document, AddExerciseCommand(
                day="Çarşamba", exercise="Goblet Squat", sets=3, reps="10"))

    def test_canonical_replacement_outside_the_catalog_is_rejected(self):
        """No fuzzy match, no near miss, no substitution: a name the catalog
        does not declare is simply not an exercise here."""
        document = canonical_document(equipment_context="minimal")

        with pytest.raises(InvalidMutation):
            apply_command(document, ReplaceExerciseCommand(
                day="Pazartesi", exercise="Dumbbell Row",
                replacement="Machine Chest Press"))

    def test_canonical_target_outside_the_catalog_is_rejected(self):
        document = canonical_document(equipment_context="minimal")

        with pytest.raises(InvalidMutation):
            apply_command(document, RemoveExerciseCommand(
                day="Pazartesi", exercise="Machine Chest Press"))

    def test_canonical_target_that_resolves_but_is_absent_is_not_found(self):
        """Resolution and presence are different questions, and the second one
        still answers ``ExerciseNotFound`` — so the Coach keeps telling the
        user "that is not in that day" rather than "that is not an exercise"."""
        document = canonical_document(equipment_context="minimal")

        with pytest.raises(ExerciseNotFound):
            apply_command(document, RemoveExerciseCommand(
                day="Pazartesi", exercise="Goblet Squat"))

    def test_canonical_target_lookup_matches_on_identity_not_on_wording(self):
        """Two entries worded differently but resolving to the same catalog
        entry are the same exercise twice. Legacy casefold matching sees two
        unrelated names and would happily edit one of them."""
        document = canonical_document(equipment_context="minimal")
        _day(document, "Pazartesi")["egzersizler"].append(
            {"isim": "One-Arm Dumbbell Row", "set": 2, "tekrar": "15",
             "dinlenme": "45 sn", "exercise_id": "ex_dumbbell_row"})

        with pytest.raises(AmbiguousExerciseTarget):
            apply_command(document, RemoveExerciseCommand(
                day="Pazartesi", exercise="Dumbbell Row"))

    def test_canonical_replace_with_an_alias_of_itself_stays_a_no_op(self):
        """``exercise_id`` joining the update set must not turn a deterministic
        no-op into a spurious write. The *original* object comes back, which is
        what lets the service skip the write entirely."""
        document = canonical_document(equipment_context="minimal")

        mutated, changed = apply_command(document, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Dumbbell Row",
            replacement="One-Arm Dumbbell Row"))

        assert changed is False
        assert mutated is document

    def test_canonical_remove_uses_identity_and_adds_none(self):
        document = canonical_document(equipment_context="minimal")

        mutated, changed = apply_command(document, RemoveExerciseCommand(
            day="Pazartesi", exercise="One-Arm Dumbbell Row"))

        assert changed is True
        assert _names(mutated, "Pazartesi") == ["Push-Up"]
        assert _entry(mutated, "Pazartesi")["exercise_id"] == "ex_push_up"

    def test_canonical_prescription_update_never_touches_identity(self):
        document = canonical_document(equipment_context="minimal")
        before = dict(_entry(document, "Pazartesi"))

        mutated, changed = apply_command(
            document,
            UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="One-Arm Dumbbell Row", sets=5))

        after = _entry(mutated, "Pazartesi")
        assert changed is True
        assert after["set"] == 5
        assert {k: v for k, v in after.items() if k != "set"} == {
            k: v for k, v in before.items() if k != "set"}

    def test_a_cardio_exercise_cannot_be_added_to_a_non_cardio_day(self):
        """Addendum §B. ``is_exercise_compatible`` gates cardio by
        ``cardio_type`` and not by equipment, so without a placement rule a
        home or minimal plan can carry a pool/treadmill movement inside a
        strength day — the equipment gate bypassed by placement alone."""
        document = canonical_document(equipment_context="minimal",
                                      cardio_type="yuruyus")

        with pytest.raises(InvalidMutation):
            apply_command(document, AddExerciseCommand(
                day="Cuma", exercise="Brisk Walk", sets=1, reps="30 dk"))

    def test_a_cardio_exercise_cannot_replace_one_on_a_non_cardio_day(self):
        document = canonical_document(equipment_context="minimal",
                                      cardio_type="yuruyus")

        with pytest.raises(InvalidMutation):
            apply_command(document, ReplaceExerciseCommand(
                day="Cuma", exercise="Plank", replacement="Brisk Walk"))

    def test_a_cardio_exercise_is_accepted_on_the_cardio_day(self):
        """The paired positive control: same exercise, same context, same
        catalog — only the day changes. Without it the two refusals above would
        also pass if cardio were simply banned outright."""
        document = canonical_document(equipment_context="minimal",
                                      cardio_type="karisik")

        mutated, changed = apply_command(document, AddExerciseCommand(
            day="Cumartesi", exercise="Jump Rope", sets=1, reps="10 dk"))

        added = _day(mutated, "Cumartesi")["egzersizler"][-1]
        assert changed is True
        assert added["exercise_id"] == "ex_jump_rope"

        with pytest.raises(InvalidMutation):
            apply_command(document, AddExerciseCommand(
                day="Cuma", exercise="Jump Rope", sets=1, reps="10 dk"))

    def test_the_placement_rule_stays_one_directional(self):
        """Forbidding a *non*-cardio exercise on a cardio day is a plan-quality
        opinion, not an authority question, and this boundary only answers
        authority questions."""
        document = canonical_document(equipment_context="minimal")

        mutated, changed = apply_command(document, AddExerciseCommand(
            day="Cumartesi", exercise="Plank", sets=3, reps="45 sn"))

        assert changed is True
        assert _day(mutated, "Cumartesi")["egzersizler"][-1][
            "exercise_id"] == "ex_plank"

    @pytest.mark.parametrize("raw", [
        None,
        "minimal",
        {},
        {"equipment_context": "minimal"},
        {"equipment_context": "uzay_istasyonu", "cardio_type": "yok",
         "style": "general_fitness", "catalog_version": 1},
        {"equipment_context": "minimal", "cardio_type": "teleport",
         "style": "general_fitness", "catalog_version": 1},
        {"equipment_context": "minimal", "cardio_type": "yok",
         "style": "olympic_alchemy", "catalog_version": 1},
        {"equipment_context": "minimal", "cardio_type": "yok",
         "style": "general_fitness", "catalog_version": "1"},
        {"equipment_context": "minimal", "cardio_type": "yok",
         "style": "general_fitness", "catalog_version": True},
        {"equipment_context": ["minimal"], "cardio_type": "yok",
         "style": "general_fitness", "catalog_version": 1},
        {"equipment_context": "minimal", "cardio_type": "yok",
         "style": "general_fitness", "catalog_version": 1, "extra": 1},
    ])
    def test_an_unusable_context_fails_closed_instead_of_degrading(self, raw):
        """A canonical plan must never quietly become a legacy one, and must
        never quietly become a DIFFERENT canonical one either.

        The command is deliberately one that would SUCCEED under both wrong
        implementations: legacy mode would write ``Band Row`` as a free-form
        name, and a mode that repaired the context into some permissive
        default would resolve it happily. Only refusing the document outright
        raises here, so "refused" cannot be confused with "the replacement was
        rejected for its own reasons".
        """
        document = _document_with_raw_context(raw)

        with pytest.raises(InvalidMutation):
            apply_command(document, ReplaceExerciseCommand(
                day="Pazartesi", exercise="Dumbbell Row",
                replacement="Band Row"))

    def test_an_unusable_context_refuses_even_a_command_that_needs_no_catalog(
            self):
        """A day move resolves nothing, so this can only fail on the context
        itself — and an empty block is the case a "fill in the defaults"
        implementation would repair into a perfectly usable one."""
        document = _document_with_raw_context({})

        with pytest.raises(InvalidMutation):
            apply_command(document, MoveTrainingDayCommand(
                day="Pazartesi", target_day="Salı"))

    def test_the_catalog_is_loaded_exactly_once_per_canonical_command(
            self, monkeypatch):
        """Zero database queries and one catalog load per mutation. A load per
        exercise would also let two differently-loaded catalogs decide identity
        inside a single command."""
        from app.services.plan_mutation import document as document_module

        loads = []
        real = document_module.load_exercise_catalog

        def _counted():
            loads.append(1)
            return real()

        monkeypatch.setattr(document_module, "load_exercise_catalog", _counted)

        apply_command(canonical_document(), ReplaceExerciseCommand(
            day="Pazartesi", exercise="Dumbbell Row", replacement="Band Row"))

        assert len(loads) == 1


class TestLegacyDocumentsAreUnchanged:
    """A name-only plan predates exercise identity and must behave exactly as
    it did before this task — including never being silently upgraded."""

    def test_legacy_replace_keeps_casefold_matching_and_free_form_names(self):
        document = _legacy_document()

        mutated, changed = apply_command(document, ReplaceExerciseCommand(
            day="Pazartesi", exercise="  bench PRESS ",
            replacement="Machine Chest Press"))

        assert changed is True
        assert _names(mutated, "Pazartesi") == [
            "Machine Chest Press", "Shoulder Press"]

    def test_legacy_replace_never_writes_an_exercise_id(self):
        document = _legacy_document()

        mutated, _changed = apply_command(document, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press", replacement="Push-Up"))

        assert "exercise_id" not in _entry(mutated, "Pazartesi")
        assert _entry(mutated, "Pazartesi")["isim"] == "Push-Up"

    def test_legacy_add_never_writes_an_exercise_id(self):
        document = _legacy_document()

        mutated, _changed = apply_command(document, AddExerciseCommand(
            day="Çarşamba", exercise="Lat Pulldown", sets=3, reps="10-12"))

        added = _day(mutated, "Çarşamba")["egzersizler"][-1]
        assert set(added) == {"isim", "set", "tekrar"}
        assert added["isim"] == "Lat Pulldown"

    def test_a_legacy_plan_may_still_hold_an_uncatalogued_exercise(self):
        """``Treadmill`` is not in the catalog and never will be. A legacy plan
        that contains it has to stay fully editable."""
        document = _legacy_document()

        mutated, changed = apply_command(
            document,
            UpdateExercisePrescriptionCommand(
                day="Cumartesi", exercise="Treadmill", reps="45 dk"))

        assert changed is True
        assert _entry(mutated, "Cumartesi")["tekrar"] == "45 dk"

    def test_a_legacy_bare_list_document_still_mutates(self):
        document = _legacy_document()["program"]

        mutated, changed = apply_command(document, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Chest Press"))

        assert changed is True
        assert isinstance(mutated, list)
        assert mutated[0]["egzersizler"][0] == {
            "isim": "Machine Chest Press", "set": 3, "tekrar": "8-12",
            "dinlenme": "90 sn", "not": "kontrollü in", "tempo": "3-1-1"}

    def test_a_legacy_document_never_consults_the_catalog(self, monkeypatch):
        """No context, no catalog: the legacy path must not even be able to
        depend on catalog availability."""
        from app.services.plan_mutation import document as document_module

        def _forbidden():
            raise AssertionError("legacy mutation loaded the exercise catalog")

        monkeypatch.setattr(
            document_module, "load_exercise_catalog", _forbidden)

        mutated, changed = apply_command(_legacy_document(), AddExerciseCommand(
            day="Çarşamba", exercise="Lat Pulldown", sets=3, reps="10-12"))

        assert changed is True
        assert _names(mutated, "Çarşamba") == ["Barbell Row", "Lat Pulldown"]
