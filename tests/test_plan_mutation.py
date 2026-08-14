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
    PlanNotFound,
    PlanNotMutable,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
    apply_plan_mutation,
)


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
        from app.services.training_generation import response_validator

        assert validation.MAX_SETS == 100
        assert validation.MIN_SETS == 1
        # Same clamp the generator applies, exercised through the generator's own
        # helper so the two can never silently drift apart.
        assert response_validator._bounded_int(
            validation.MAX_SETS + 1, 1, validation.MIN_SETS,
            validation.MAX_SETS) == validation.MAX_SETS


# ── Ownership ────────────────────────────────────────────────────────────────

class TestOwnership:

    def test_owner_can_mutate_own_plan(self, app, make_user, seed_plan):
        user = make_user("owner")
        seed_plan(user.id)

        result = apply_plan_mutation(user.id, RemoveExerciseCommand(
            day="Pazartesi", exercise="Shoulder Press"))

        assert result.changed is True
        assert _names(_stored(user.id), "Pazartesi") == ["Bench Press"]

    def test_another_users_plan_is_never_touched(self, app, make_user, seed_plan):
        victim = make_user("victim")
        actor = make_user("actor")
        victim_text = seed_plan(victim.id)
        seed_plan(actor.id)

        apply_plan_mutation(actor.id, RemoveExerciseCommand(
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

        apply_plan_mutation(user.id, RemoveExerciseCommand(
            day="Pazartesi", exercise="Bench Press"))

        assert _names(_stored(user.id), "Pazartesi") == ["Shoulder Press"]
        assert db.session.get(TrainingPlan, superseded.id).plan_data == (
            superseded_text)

    def test_user_without_a_plan_is_rejected(self, app, make_user):
        user = make_user("planless")

        with pytest.raises(PlanNotFound):
            apply_plan_mutation(user.id, RemoveExerciseCommand(
                day="Pazartesi", exercise="Bench Press"))

    def test_unparseable_plan_is_rejected_without_repair(
            self, app, make_user, seed_plan):
        user = make_user("brokenplan")
        seed_plan(user.id, "not json at all")

        with pytest.raises(PlanNotMutable):
            apply_plan_mutation(user.id, RemoveExerciseCommand(
                day="Pazartesi", exercise="Bench Press"))
        assert _stored_text(user.id) == "not json at all"


# ── Replace exercise ─────────────────────────────────────────────────────────

class TestReplaceExercise:

    def test_replaces_exactly_the_named_exercise(self, app, make_user, seed_plan):
        user = make_user("replacer")
        seed_plan(user.id)

        apply_plan_mutation(user.id, ReplaceExerciseCommand(
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

        apply_plan_mutation(user.id, ReplaceExerciseCommand(
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

        apply_plan_mutation(user.id, ReplaceExerciseCommand(
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

        apply_plan_mutation(user.id, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Chest Press"))

        assert _day(_stored(user.id), "Pazartesi")["egzersizler"][1] == before

    def test_plan_level_fields_are_unchanged(self, app, make_user, seed_plan):
        user = make_user("planfields")
        seed_plan(user.id)

        apply_plan_mutation(user.id, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Chest Press"))

        assert _stored(user.id)["haftalik_ozet"] == {
            "toplam_antrenman_gun": 3, "yogunluk_skoru": 7}

    def test_missing_exercise_is_rejected(self, app, make_user, seed_plan):
        user = make_user("missingex")
        text = seed_plan(user.id)

        with pytest.raises(ExerciseNotFound):
            apply_plan_mutation(user.id, ReplaceExerciseCommand(
                day="Pazartesi", exercise="Deadlift", replacement="Rack Pull"))
        assert _stored_text(user.id) == text

    def test_unknown_day_is_rejected(self, app, make_user, seed_plan):
        user = make_user("unknownday")
        text = seed_plan(user.id)

        with pytest.raises(DayNotFound):
            apply_plan_mutation(user.id, ReplaceExerciseCommand(
                day="Funday", exercise="Bench Press", replacement="Push Up"))
        assert _stored_text(user.id) == text

    def test_duplicate_name_in_the_day_is_ambiguous(self, app, make_user, seed_plan):
        document = _seven_day_program()
        _day(document, "Pazartesi")["egzersizler"].append(
            {"isim": "Bench Press", "set": 2, "tekrar": "15", "dinlenme": "45 sn"})
        user = make_user("ambiguous")
        text = seed_plan(user.id, document)

        with pytest.raises(AmbiguousExerciseTarget):
            apply_plan_mutation(user.id, ReplaceExerciseCommand(
                day="Pazartesi", exercise="Bench Press",
                replacement="Machine Chest Press"))
        assert _stored_text(user.id) == text

    def test_replacement_name_is_validated(self, app, make_user, seed_plan):
        user = make_user("badreplacement")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            apply_plan_mutation(user.id, ReplaceExerciseCommand(
                day="Pazartesi", exercise="Bench Press", replacement="   "))
        assert _stored_text(user.id) == text


# ── Add exercise ─────────────────────────────────────────────────────────────

class TestAddExercise:

    def test_appends_to_the_target_day(self, app, make_user, seed_plan):
        user = make_user("adder")
        seed_plan(user.id)

        apply_plan_mutation(user.id, AddExerciseCommand(
            day="Çarşamba", exercise="Lat Pulldown", sets=3, reps="10-12"))

        assert _names(_stored(user.id), "Çarşamba") == [
            "Barbell Row", "Lat Pulldown"]

    def test_added_exercise_carries_the_explicit_prescription(
            self, app, make_user, seed_plan):
        user = make_user("adderx")
        seed_plan(user.id)

        apply_plan_mutation(user.id, AddExerciseCommand(
            day="Çarşamba", exercise="Lat Pulldown", sets=3, reps="10-12"))

        added = _day(_stored(user.id), "Çarşamba")["egzersizler"][-1]
        assert added["isim"] == "Lat Pulldown"
        assert added["set"] == 3
        assert added["tekrar"] == "10-12"

    def test_unrelated_days_unchanged_on_add(self, app, make_user, seed_plan):
        user = make_user("addisolation")
        seed_plan(user.id)
        before = _other_days(_stored(user.id), "Çarşamba")

        apply_plan_mutation(user.id, AddExerciseCommand(
            day="Çarşamba", exercise="Lat Pulldown", sets=3, reps="10-12"))

        assert _other_days(_stored(user.id), "Çarşamba") == before

    def test_prescription_is_required_and_never_invented(
            self, app, make_user, seed_plan):
        user = make_user("noinvent")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            apply_plan_mutation(user.id, AddExerciseCommand(
                day="Çarşamba", exercise="Lat Pulldown", sets=None, reps=None))
        assert _stored_text(user.id) == text

    def test_malformed_exercise_name_is_rejected(self, app, make_user, seed_plan):
        user = make_user("badname")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            apply_plan_mutation(user.id, AddExerciseCommand(
                day="Çarşamba", exercise="", sets=3, reps="10"))
        assert _stored_text(user.id) == text

    def test_adding_to_a_rest_day_is_rejected(self, app, make_user, seed_plan):
        """A rest day carrying exercises is not a structurally valid plan."""
        user = make_user("restadd")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            apply_plan_mutation(user.id, AddExerciseCommand(
                day="Salı", exercise="Lat Pulldown", sets=3, reps="10"))
        assert _stored_text(user.id) == text


# ── Remove exercise ──────────────────────────────────────────────────────────

class TestRemoveExercise:

    def test_removes_exactly_the_named_exercise(self, app, make_user, seed_plan):
        user = make_user("remover")
        seed_plan(user.id)

        apply_plan_mutation(user.id, RemoveExerciseCommand(
            day="Pazartesi", exercise="Bench Press"))

        assert _names(_stored(user.id), "Pazartesi") == ["Shoulder Press"]

    def test_missing_exercise_is_rejected(self, app, make_user, seed_plan):
        user = make_user("removemissing")
        text = seed_plan(user.id)

        with pytest.raises(ExerciseNotFound):
            apply_plan_mutation(user.id, RemoveExerciseCommand(
                day="Pazartesi", exercise="Deadlift"))
        assert _stored_text(user.id) == text

    def test_removing_the_last_exercise_of_a_training_day_is_rejected(
            self, app, make_user, seed_plan):
        """Canonical validation: a training day must keep at least one exercise."""
        user = make_user("lastexercise")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            apply_plan_mutation(user.id, RemoveExerciseCommand(
                day="Çarşamba", exercise="Barbell Row"))
        assert _stored_text(user.id) == text

    def test_unrelated_days_unchanged_on_remove(self, app, make_user, seed_plan):
        user = make_user("removeisolation")
        seed_plan(user.id)
        before = _other_days(_stored(user.id), "Pazartesi")

        apply_plan_mutation(user.id, RemoveExerciseCommand(
            day="Pazartesi", exercise="Bench Press"))

        assert _other_days(_stored(user.id), "Pazartesi") == before


# ── Prescription update ──────────────────────────────────────────────────────

class TestUpdatePrescription:

    def test_sets_update_changes_only_sets(self, app, make_user, seed_plan):
        user = make_user("setsonly")
        seed_plan(user.id)
        before = _day(_stored(user.id), "Pazartesi")["egzersizler"][0]

        apply_plan_mutation(user.id, UpdateExercisePrescriptionCommand(
            day="Pazartesi", exercise="Bench Press", sets=5))

        after = _day(_stored(user.id), "Pazartesi")["egzersizler"][0]
        assert after["set"] == 5
        assert {k: v for k, v in after.items() if k != "set"} == {
            k: v for k, v in before.items() if k != "set"}

    def test_reps_update_changes_only_reps(self, app, make_user, seed_plan):
        user = make_user("repsonly")
        seed_plan(user.id)
        before = _day(_stored(user.id), "Pazartesi")["egzersizler"][0]

        apply_plan_mutation(user.id, UpdateExercisePrescriptionCommand(
            day="Pazartesi", exercise="Bench Press", reps="5-8"))

        after = _day(_stored(user.id), "Pazartesi")["egzersizler"][0]
        assert after["tekrar"] == "5-8"
        assert {k: v for k, v in after.items() if k != "tekrar"} == {
            k: v for k, v in before.items() if k != "tekrar"}

    def test_sets_below_lower_bound_rejected(self, app, make_user, seed_plan):
        user = make_user("lowbound")
        text = seed_plan(user.id)

        with pytest.raises(InvalidPrescription):
            apply_plan_mutation(user.id, UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", sets=0))
        assert _stored_text(user.id) == text

    def test_sets_above_upper_bound_rejected(self, app, make_user, seed_plan):
        user = make_user("highbound")
        text = seed_plan(user.id)

        with pytest.raises(InvalidPrescription):
            apply_plan_mutation(user.id, UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", sets=101))
        assert _stored_text(user.id) == text

    def test_malformed_reps_rejected(self, app, make_user, seed_plan):
        user = make_user("badreps")
        text = seed_plan(user.id)

        with pytest.raises(InvalidPrescription):
            apply_plan_mutation(user.id, UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", reps="x" * 41))
        assert _stored_text(user.id) == text

    def test_empty_command_is_rejected(self, app, make_user, seed_plan):
        user = make_user("emptycmd")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            apply_plan_mutation(user.id, UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press"))
        assert _stored_text(user.id) == text

    def test_no_op_does_not_rewrite_the_plan(self, app, make_user, seed_plan):
        """Requested state already true → deterministic no-op, no churn."""
        user = make_user("noop")
        text = seed_plan(user.id)

        result = apply_plan_mutation(user.id, UpdateExercisePrescriptionCommand(
            day="Pazartesi", exercise="Bench Press", sets=3))

        assert result.changed is False
        assert _stored_text(user.id) == text


# ── Move training day ────────────────────────────────────────────────────────

class TestMoveTrainingDay:

    def test_moving_swaps_day_content_and_keeps_weekday_labels(
            self, app, make_user, seed_plan):
        user = make_user("mover")
        seed_plan(user.id)

        apply_plan_mutation(user.id, MoveTrainingDayCommand(
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

        apply_plan_mutation(user.id, MoveTrainingDayCommand(
            day="Pazartesi", target_day="Salı"))

        log = WorkoutLog.query.filter_by(user_id=user.id).one()
        assert (log.exercise_name, log.sets, log.reps, log.volume) == (
            "Bench Press", 3, 10, 2400)

    def test_moving_a_day_onto_itself_is_a_no_op(self, app, make_user, seed_plan):
        user = make_user("selfmove")
        text = seed_plan(user.id)

        result = apply_plan_mutation(user.id, MoveTrainingDayCommand(
            day="Pazartesi", target_day="Pazartesi"))

        assert result.changed is False
        assert _stored_text(user.id) == text

    def test_unknown_target_day_is_rejected(self, app, make_user, seed_plan):
        user = make_user("badtarget")
        text = seed_plan(user.id)

        with pytest.raises(DayNotFound):
            apply_plan_mutation(user.id, MoveTrainingDayCommand(
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

        apply_plan_mutation(user.id, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Chest Press"))

        log = WorkoutLog.query.filter_by(user_id=user.id).one()
        assert log.exercise_name == "Bench Press"
        assert log.volume == 2400

    def test_plan_mutation_creates_no_history_rows(self, app, make_user, seed_plan):
        from app.models import PumpCheck, WorkoutLog

        user = make_user("nohistorycreate")
        seed_plan(user.id)

        apply_plan_mutation(user.id, UpdateExercisePrescriptionCommand(
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
            apply_plan_mutation(user.id, UpdateExercisePrescriptionCommand(
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
            apply_plan_mutation(user.id, ReplaceExerciseCommand(
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
            apply_plan_mutation(user.id, {"day": "Pazartesi", "sets": 3})
        assert _stored_text(user.id) == text


# ── Structural validity of the result ────────────────────────────────────────

class TestResultingPlanStaysValid:

    def test_mutated_plan_is_still_readable_by_the_canonical_read_layer(
            self, app, make_user, seed_plan):
        from app.services.plan_facts import gather_plan_facts

        user = make_user("stillreadable")
        seed_plan(user.id)

        apply_plan_mutation(user.id, ReplaceExerciseCommand(
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

        apply_plan_mutation(user.id, AddExerciseCommand(
            day="Cuma", exercise="Leg Press", sets=3, reps="12"))

        program = _stored(user.id)["program"]
        assert len(program) == 7
        assert len({d["gun"] for d in program}) == 7
