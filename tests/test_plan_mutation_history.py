"""Adaptive Coaching Sprint 1 PR2 — plan versioning, journal, replay, undo.

PR1 could change a plan safely. It could not answer "did this retry already
happen?" or "put that back". These tests cover what PR2 adds, and they assert
persisted domain semantics — stored version, stored journal rows, stored plan
bytes — never private helper names or source strings.

Where a scenario has a wrong-but-plausible implementation, the test is written to
fail against that implementation specifically: an undo that decrements the
version, a replay that returns *current* state instead of the state the original
call produced, a no-op that leaves no trace and so lets a later retry mutate, an
undo that reaches into a regenerated plan's history.
"""
import json

import pytest

from app.services.plan_mutation import (
    ACTOR_AI_COACH,
    ACTOR_SYSTEM,
    ACTOR_USER,
    OUTCOME_APPLIED,
    OUTCOME_NO_OP,
    AddExerciseCommand,
    IdempotencyConflict,
    InvalidMutation,
    InvalidPrescription,
    MoveTrainingDayCommand,
    MutationContext,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UndoConflict,
    UndoUnavailable,
    UpdateExercisePrescriptionCommand,
    apply_plan_mutation,
    undo_last_change,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _program():
    """The canonical 7-day shape, carrying fields this domain does not model.

    ``haftalik_ozet`` and the per-exercise ``tempo`` exist so "the snapshot is
    the exact persisted text" is proven against a plan whose contents cannot be
    rebuilt from anything this boundary understands.
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
    from app.extensions import db
    from app.models import TrainingPlan

    def _seed(user_id, document=None):
        payload = _program() if document is None else document
        text = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False)
        db.session.add(TrainingPlan(user_id=user_id, plan_data=text, score=8))
        db.session.commit()
        return text

    return _seed


def _ctx(key, **kwargs):
    return MutationContext(idempotency_key=key, **kwargs)


def _plan(user_id):
    from app.services.today_facts import get_active_plan
    return get_active_plan(user_id)


def _text(user_id):
    return _plan(user_id).plan_data


def _version(user_id):
    return _plan(user_id).mutation_version


def _records(user_id):
    from app.models import PlanMutationRecord
    return (PlanMutationRecord.query.filter_by(user_id=user_id)
            .order_by(PlanMutationRecord.id).all())


def _names(text, gun):
    document = json.loads(text)
    day = next(d for d in document["program"] if d["gun"] == gun)
    return [ex["isim"] for ex in day["egzersizler"]]


def _swap_bench(day="Pazartesi", replacement="Machine Press"):
    return ReplaceExerciseCommand(
        day=day, exercise="Bench Press", replacement=replacement)


# ── Durable plan version ─────────────────────────────────────────────────────

class TestPlanVersion:

    def test_a_new_plan_starts_at_version_zero(self, app, make_user, seed_plan):
        """Existing and new plans alike begin with no mutation history.

        Zero is not a placeholder: it is the honest statement that this lineage
        has never been changed through the boundary. Nothing may back-fill a
        version that implies mutations the application never recorded.
        """
        user = make_user("v_zero")
        seed_plan(user.id)

        assert _version(user.id) == 0
        assert _records(user.id) == []

    def test_each_state_change_advances_the_version_by_one(
            self, app, make_user, seed_plan):
        user = make_user("v_advance")
        seed_plan(user.id)

        first = apply_plan_mutation(user.id, _swap_bench(), _ctx("v-adv-0001"))
        second = apply_plan_mutation(
            user.id, AddExerciseCommand(day="Çarşamba", exercise="Plank", sets=3,
                                        reps="30 sn"),
            _ctx("v-adv-0002"))

        assert (first.plan_version, second.plan_version) == (1, 2)
        assert _version(user.id) == 2

    def test_a_no_op_does_not_advance_the_version(self, app, make_user, seed_plan):
        user = make_user("v_noop")
        text = seed_plan(user.id)

        result = apply_plan_mutation(
            user.id,
            UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", sets=3),
            _ctx("v-noop-0001"))

        assert result.outcome == OUTCOME_NO_OP
        assert result.changed is False
        assert result.plan_version == 0
        assert _version(user.id) == 0
        assert _text(user.id) == text

    def test_a_rejected_command_does_not_advance_the_version(
            self, app, make_user, seed_plan):
        user = make_user("v_reject")
        text = seed_plan(user.id)

        with pytest.raises(InvalidPrescription):
            apply_plan_mutation(
                user.id,
                UpdateExercisePrescriptionCommand(
                    day="Pazartesi", exercise="Bench Press", sets=999),
                _ctx("v-reject-0001"))

        assert _version(user.id) == 0
        assert _text(user.id) == text
        assert _records(user.id) == []

    def test_undo_moves_the_version_forward_never_backward(
            self, app, make_user, seed_plan):
        """The single most tempting wrong implementation.

        Restoring old *content* is the point; restoring an old *version number*
        would make the version ambiguous — two different histories would report
        the same position — and any optimistic-concurrency check built on it
        later would silently pass on stale state.
        """
        user = make_user("v_undo")
        seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("v-undo-0001"))

        result = undo_last_change(user.id, _ctx("v-undo-0002"))

        assert result.plan_version == 2
        assert _version(user.id) == 2


# ── The durable journal ──────────────────────────────────────────────────────

class TestMutationJournal:

    def test_an_applied_mutation_records_the_whole_operation(
            self, app, make_user, seed_plan):
        user = make_user("j_full")
        before = seed_plan(user.id)

        result = apply_plan_mutation(
            user.id, _swap_bench(),
            _ctx("j-full-0001", actor=ACTOR_AI_COACH, reason="omuz ağrısı"))

        record, = _records(user.id)
        assert record.user_id == user.id
        assert record.plan_lineage_id == _plan(user.id).lineage_id
        assert record.operation_kind == "mutation"
        assert record.command_type == "replace_exercise"
        assert record.command_fingerprint
        assert record.actor == ACTOR_AI_COACH
        assert record.reason == "omuz ağrısı"
        assert record.outcome == OUTCOME_APPLIED
        assert (record.before_version, record.after_version) == (0, 1)
        assert record.idempotency_key == "j-full-0001"
        assert record.reverts_mutation_id is None
        assert record.created_at is not None
        # Exact persisted text on both sides — not a projection of the fields
        # this domain models.
        assert record.before_snapshot == before
        assert record.after_snapshot == _text(user.id)
        assert result.mutation_id == record.public_id

    def test_snapshots_keep_fields_the_domain_does_not_model(
            self, app, make_user, seed_plan):
        """A snapshot rebuilt from plan_facts/a DTO would pass every other test
        here and still lose these, so undo would "restore" a lossy plan."""
        user = make_user("j_unknown")
        seed_plan(user.id)

        apply_plan_mutation(user.id, _swap_bench(), _ctx("j-unknown-0001"))

        record, = _records(user.id)
        for snapshot in (record.before_snapshot, record.after_snapshot):
            document = json.loads(snapshot)
            assert document["haftalik_ozet"] == {
                "toplam_antrenman_gun": 3, "yogunluk_skoru": 7}
            bench_day = document["program"][0]["egzersizler"]
            assert any(ex.get("tempo") == "3-1-1" for ex in bench_day)

    def test_the_identity_returned_is_opaque_not_a_row_id(
            self, app, make_user, seed_plan):
        user = make_user("j_opaque")
        seed_plan(user.id)

        result = apply_plan_mutation(user.id, _swap_bench(), _ctx("j-opaque-0001"))

        record, = _records(user.id)
        assert result.mutation_id != str(record.id)
        assert len(result.mutation_id) >= 32

    def test_an_accepted_no_op_is_recorded_as_a_no_op(
            self, app, make_user, seed_plan):
        user = make_user("j_noop")
        text = seed_plan(user.id)

        apply_plan_mutation(
            user.id,
            UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", sets=3),
            _ctx("j-noop-0001"))

        record, = _records(user.id)
        assert record.outcome == OUTCOME_NO_OP
        assert (record.before_version, record.after_version) == (0, 0)
        assert record.before_snapshot == record.after_snapshot == text

    def test_undo_appends_and_never_erases(self, app, make_user, seed_plan):
        user = make_user("j_undo")
        seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("j-undo-0001"))
        original = _records(user.id)[0]
        original_snapshot = original.after_snapshot

        undo_last_change(user.id, _ctx("j-undo-0002"))

        mutation, undo = _records(user.id)
        assert mutation.id == original.id
        assert mutation.outcome == OUTCOME_APPLIED
        assert mutation.after_snapshot == original_snapshot
        assert undo.operation_kind == "undo"
        assert undo.command_type == "undo_last_change"
        assert undo.reverts_mutation_id == mutation.id
        assert (undo.before_version, undo.after_version) == (1, 2)

    def test_the_actor_vocabulary_is_bounded(self, app, make_user, seed_plan):
        user = make_user("j_actor")
        seed_plan(user.id)

        for actor in (ACTOR_USER, ACTOR_AI_COACH, ACTOR_SYSTEM):
            apply_plan_mutation(
                user.id,
                AddExerciseCommand(day="Çarşamba", exercise="Plank %s" % actor,
                                   sets=3, reps="30 sn"),
                _ctx("j-actor-%s" % actor, actor=actor))

        assert [r.actor for r in _records(user.id)] == [
            ACTOR_USER, ACTOR_AI_COACH, ACTOR_SYSTEM]

        with pytest.raises(InvalidMutation):
            apply_plan_mutation(
                user.id, _swap_bench(), _ctx("j-actor-bad", actor="root"))

    def test_an_oversized_reason_is_refused_not_truncated(
            self, app, make_user, seed_plan):
        """Silently shortening an audit note produces a different note that the
        caller is never told about."""
        user = make_user("j_reason")
        text = seed_plan(user.id)

        with pytest.raises(InvalidMutation):
            apply_plan_mutation(
                user.id, _swap_bench(), _ctx("j-reason-0001", reason="x" * 201))

        assert _text(user.id) == text
        assert _records(user.id) == []

        apply_plan_mutation(
            user.id, _swap_bench(), _ctx("j-reason-0002", reason="y" * 200))
        assert _records(user.id)[0].reason == "y" * 200


# ── Idempotent replay ────────────────────────────────────────────────────────

class TestIdempotency:

    def test_retrying_an_add_does_not_append_twice(self, app, make_user, seed_plan):
        """The concrete bug PR2 exists to close.

        ``add_exercise`` is the one command that is not naturally convergent:
        replace/update/move reach the same desired state however often they run,
        but a second add genuinely appends a second entry.
        """
        user = make_user("i_add")
        seed_plan(user.id)
        command = AddExerciseCommand(day="Çarşamba", exercise="Plank", sets=3,
                                     reps="30 sn")

        first = apply_plan_mutation(user.id, command, _ctx("i-add-0001"))
        second = apply_plan_mutation(user.id, command, _ctx("i-add-0001"))

        assert _names(_text(user.id), "Çarşamba") == ["Barbell Row", "Plank"]
        assert second.replayed is True
        assert first.replayed is False
        assert second.mutation_id == first.mutation_id
        assert second.plan_version == first.plan_version == 1
        assert _version(user.id) == 1
        assert len(_records(user.id)) == 1

    def test_a_replay_returns_the_original_result_not_current_state(
            self, app, make_user, seed_plan):
        """A retry of an old key must not report a later, unrelated change."""
        user = make_user("i_stale")
        seed_plan(user.id)
        first = apply_plan_mutation(user.id, _swap_bench(), _ctx("i-stale-0001"))
        apply_plan_mutation(
            user.id,
            AddExerciseCommand(day="Çarşamba", exercise="Plank", sets=3, reps="30 sn"),
            _ctx("i-stale-0002"))

        replayed = apply_plan_mutation(user.id, _swap_bench(), _ctx("i-stale-0001"))

        assert replayed.plan_version == first.plan_version == 1
        assert _version(user.id) == 2
        assert _names(json.dumps(replayed.plan, ensure_ascii=False), "Çarşamba") == [
            "Barbell Row"]
        # The live plan still holds the later change; only the *reported* result
        # is the original one.
        assert _names(_text(user.id), "Çarşamba") == ["Barbell Row", "Plank"]

    def test_the_same_key_for_a_different_change_is_refused(
            self, app, make_user, seed_plan):
        user = make_user("i_conflict")
        seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("i-conf-0001"))
        after_first = _text(user.id)

        with pytest.raises(IdempotencyConflict):
            apply_plan_mutation(
                user.id,
                RemoveExerciseCommand(day="Pazartesi", exercise="Shoulder Press"),
                _ctx("i-conf-0001"))

        assert _text(user.id) == after_first
        assert _version(user.id) == 1
        assert len(_records(user.id)) == 1

    def test_semantically_equal_commands_replay_rather_than_conflict(
            self, app, make_user, seed_plan):
        """Targeting is case-insensitive in the document layer, so the
        fingerprint must agree — otherwise a retry that differs only in casing
        would be read as a *different* operation and refused."""
        user = make_user("i_equal")
        seed_plan(user.id)

        first = apply_plan_mutation(
            user.id,
            RemoveExerciseCommand(day="Pazartesi", exercise="Bench Press"),
            _ctx("i-equal-0001"))
        second = apply_plan_mutation(
            user.id,
            RemoveExerciseCommand(day=" Pazartesi", exercise="  bench press "),
            _ctx("i-equal-0001"))

        assert second.replayed is True
        assert second.mutation_id == first.mutation_id

    def test_two_users_may_share_a_key_text(self, app, make_user, seed_plan):
        """Keys are scoped to one owner; a global key space would let one user's
        retry suppress a stranger's change."""
        alice, bob = make_user("i_alice"), make_user("i_bob")
        seed_plan(alice.id)
        seed_plan(bob.id)

        a = apply_plan_mutation(alice.id, _swap_bench(), _ctx("shared-key-0001"))
        b = apply_plan_mutation(bob.id, _swap_bench(), _ctx("shared-key-0001"))

        assert a.replayed is False and b.replayed is False
        assert a.mutation_id != b.mutation_id
        assert _version(alice.id) == _version(bob.id) == 1
        assert len(_records(alice.id)) == len(_records(bob.id)) == 1

    def test_a_malformed_key_is_refused_before_anything_is_read(
            self, app, make_user, seed_plan):
        user = make_user("i_badkey")
        text = seed_plan(user.id)

        for key in ("", "short", "x" * 65, "has space", "semi;colon"):
            with pytest.raises(InvalidMutation):
                apply_plan_mutation(user.id, _swap_bench(), _ctx(key))

        assert _text(user.id) == text
        assert _records(user.id) == []

    def test_a_rejected_command_does_not_consume_its_key(
            self, app, make_user, seed_plan):
        """Validation failure is not an operation. If it burned the key, the
        caller's corrected retry would be refused as a conflict — or worse,
        replay a mutation that never happened."""
        user = make_user("i_free")
        seed_plan(user.id)

        with pytest.raises(InvalidPrescription):
            apply_plan_mutation(
                user.id,
                UpdateExercisePrescriptionCommand(
                    day="Pazartesi", exercise="Bench Press", sets=999),
                _ctx("i-free-0001"))

        result = apply_plan_mutation(
            user.id,
            UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", sets=5),
            _ctx("i-free-0001"))

        assert result.replayed is False
        assert result.plan_version == 1


# ── Durable no-op replay ─────────────────────────────────────────────────────

class TestNoOpReplay:

    def test_a_no_op_key_still_cannot_mutate_later(self, app, make_user, seed_plan):
        """The scenario that makes an in-memory no-op unsafe.

        Sets are 3. A no-op "set them to 3" is accepted under key K. The user
        then really changes them to 4. A retransmitted K arrives. If the no-op
        left no durable trace, K looks fresh and quietly drags the plan back to
        3 — a mutation nobody asked for, produced by a *retry*.
        """
        user = make_user("n_replay")
        seed_plan(user.id)
        same = UpdateExercisePrescriptionCommand(
            day="Pazartesi", exercise="Bench Press", sets=3)

        first = apply_plan_mutation(user.id, same, _ctx("n-replay-0001"))
        apply_plan_mutation(
            user.id,
            UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", sets=4),
            _ctx("n-replay-0002"))
        replayed = apply_plan_mutation(user.id, same, _ctx("n-replay-0001"))

        assert replayed.replayed is True
        assert replayed.outcome == OUTCOME_NO_OP
        assert replayed.mutation_id == first.mutation_id
        sets = json.loads(_text(user.id))["program"][0]["egzersizler"][0]["set"]
        assert sets == 4
        assert _version(user.id) == 1

    def test_a_no_op_is_not_something_undo_can_reverse(
            self, app, make_user, seed_plan):
        user = make_user("n_undo")
        seed_plan(user.id)
        apply_plan_mutation(
            user.id,
            UpdateExercisePrescriptionCommand(
                day="Pazartesi", exercise="Bench Press", sets=3),
            _ctx("n-undo-0001"))

        with pytest.raises(UndoUnavailable):
            undo_last_change(user.id, _ctx("n-undo-0002"))


# ── Atomicity ────────────────────────────────────────────────────────────────

class TestAtomicity:

    def test_a_journal_failure_leaves_the_plan_untouched(
            self, app, make_user, seed_plan, monkeypatch):
        """Plan, version and history commit together or not at all.

        A plan that moved without a journal entry is unexplainable and, worse,
        un-undoable: the next undo would compare current bytes against a
        mutation that was never recorded and refuse forever.
        """
        user = make_user("a_journal")
        text = seed_plan(user.id)

        def _boom(**_kwargs):
            raise RuntimeError("journal unavailable")

        monkeypatch.setattr(
            "app.services.plan_mutation.service.build_record", _boom)

        with pytest.raises(RuntimeError):
            apply_plan_mutation(user.id, _swap_bench(), _ctx("a-journal-0001"))

        assert _text(user.id) == text
        assert _version(user.id) == 0
        assert _records(user.id) == []

    def test_an_undo_failure_leaves_the_plan_untouched(
            self, app, make_user, seed_plan, monkeypatch):
        user = make_user("a_undo")
        seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("a-undo-0001"))
        mutated = _text(user.id)

        def _boom(**_kwargs):
            raise RuntimeError("journal unavailable")

        monkeypatch.setattr(
            "app.services.plan_mutation.service.build_record", _boom)

        with pytest.raises(RuntimeError):
            undo_last_change(user.id, _ctx("a-undo-0002"))

        assert _text(user.id) == mutated
        assert _version(user.id) == 1
        assert len(_records(user.id)) == 1


# ── Undo ─────────────────────────────────────────────────────────────────────

class TestUndo:

    def test_undo_restores_the_exact_previous_bytes(self, app, make_user, seed_plan):
        user = make_user("u_exact")
        original = seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("u-exact-0001"))
        assert _text(user.id) != original

        result = undo_last_change(user.id, _ctx("u-exact-0002"))

        assert _text(user.id) == original
        assert result.plan == json.loads(original)
        assert result.outcome == OUTCOME_APPLIED

    def test_undo_reverses_an_add_without_inventing_a_remove(
            self, app, make_user, seed_plan):
        user = make_user("u_add")
        original = seed_plan(user.id)
        apply_plan_mutation(
            user.id,
            AddExerciseCommand(day="Çarşamba", exercise="Plank", sets=3, reps="30 sn"),
            _ctx("u-add-0001"))

        undo_last_change(user.id, _ctx("u-add-0002"))

        assert _text(user.id) == original
        assert _names(_text(user.id), "Çarşamba") == ["Barbell Row"]

    def test_undo_walks_back_one_change_at_a_time(self, app, make_user, seed_plan):
        user = make_user("u_multi")
        original = seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("u-multi-0001"))
        after_first = _text(user.id)
        apply_plan_mutation(
            user.id,
            AddExerciseCommand(day="Çarşamba", exercise="Plank", sets=3, reps="30 sn"),
            _ctx("u-multi-0002"))

        undo_last_change(user.id, _ctx("u-multi-0003"))
        assert _text(user.id) == after_first
        assert _version(user.id) == 3

        undo_last_change(user.id, _ctx("u-multi-0004"))
        assert _text(user.id) == original
        assert _version(user.id) == 4
        assert len(_records(user.id)) == 4

    def test_there_is_nothing_to_undo_on_an_untouched_plan(
            self, app, make_user, seed_plan):
        user = make_user("u_empty")
        text = seed_plan(user.id)

        with pytest.raises(UndoUnavailable):
            undo_last_change(user.id, _ctx("u-empty-0001"))

        assert _text(user.id) == text
        assert _version(user.id) == 0

    def test_a_mutation_is_reversed_at_most_once(self, app, make_user, seed_plan):
        """No redo, and no walking the same change back twice. After the only
        mutation is reverted, history is exhausted — the undo entry itself is
        not a candidate."""
        user = make_user("u_once")
        original = seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("u-once-0001"))
        undo_last_change(user.id, _ctx("u-once-0002"))

        with pytest.raises(UndoUnavailable):
            undo_last_change(user.id, _ctx("u-once-0003"))

        assert _text(user.id) == original
        assert _version(user.id) == 2

    def test_retrying_an_undo_replays_it_instead_of_undoing_again(
            self, app, make_user, seed_plan):
        """A lost response must not cost the user a second change."""
        user = make_user("u_retry")
        original = seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("u-retry-0001"))
        after_first = _text(user.id)
        apply_plan_mutation(
            user.id,
            AddExerciseCommand(day="Çarşamba", exercise="Plank", sets=3, reps="30 sn"),
            _ctx("u-retry-0002"))

        first = undo_last_change(user.id, _ctx("u-retry-0003"))
        again = undo_last_change(user.id, _ctx("u-retry-0003"))

        assert again.replayed is True
        assert again.mutation_id == first.mutation_id
        assert again.plan_version == first.plan_version == 3
        assert _text(user.id) == after_first
        assert _text(user.id) != original
        assert _version(user.id) == 3

    def test_an_undo_key_cannot_be_reused_for_a_mutation(
            self, app, make_user, seed_plan):
        user = make_user("u_keyreuse")
        seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("u-keyreuse-0001"))
        undo_last_change(user.id, _ctx("u-keyreuse-0002"))
        settled = _text(user.id)

        with pytest.raises(IdempotencyConflict):
            apply_plan_mutation(
                user.id,
                AddExerciseCommand(day="Çarşamba", exercise="Plank", sets=3,
                                   reps="30 sn"),
                _ctx("u-keyreuse-0002"))

        assert _text(user.id) == settled

    def test_undo_fails_closed_after_an_out_of_band_write(
            self, app, make_user, seed_plan):
        """Something wrote the plan outside this boundary. Restoring an old
        snapshot on top of that would silently destroy the other writer's work,
        so the undo refuses rather than guesses."""
        from app.extensions import db

        user = make_user("u_oob")
        seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("u-oob-0001"))

        plan = _plan(user.id)
        document = json.loads(plan.plan_data)
        document["program"][0]["odak"] = "Elle düzenlendi"
        plan.plan_data = json.dumps(document, ensure_ascii=False)
        db.session.commit()
        tampered = _text(user.id)

        with pytest.raises(UndoConflict):
            undo_last_change(user.id, _ctx("u-oob-0002"))

        assert _text(user.id) == tampered
        assert _version(user.id) == 1
        assert len(_records(user.id)) == 1

    def test_undo_never_reaches_across_a_plan_replacement(
            self, app, make_user, seed_plan):
        """The legacy save route deletes every row and inserts a new one, so a
        regenerated plan is a NEW lineage. Restoring an old plan's snapshot into
        it — however similar the day and exercise names look — would resurrect a
        program the user replaced on purpose."""
        from app.extensions import db
        from app.models import TrainingPlan

        user = make_user("u_lineage")
        seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("u-lineage-0001"))
        old_lineage = _plan(user.id).lineage_id

        # Exactly what POST /training-plan/save does.
        TrainingPlan.query.filter_by(user_id=user.id).delete()
        regenerated = json.dumps(_program(), ensure_ascii=False)
        db.session.add(TrainingPlan(user_id=user.id, plan_data=regenerated, score=9))
        db.session.commit()

        assert _plan(user.id).lineage_id != old_lineage
        with pytest.raises(UndoUnavailable):
            undo_last_change(user.id, _ctx("u-lineage-0002"))
        assert _text(user.id) == regenerated

    def test_history_outlives_the_plan_row_it_describes(
            self, app, make_user, seed_plan):
        """A hard foreign key would have cascaded this audit trail away."""
        from app.extensions import db
        from app.models import TrainingPlan

        user = make_user("u_audit")
        seed_plan(user.id)
        apply_plan_mutation(user.id, _swap_bench(), _ctx("u-audit-0001"))

        TrainingPlan.query.filter_by(user_id=user.id).delete()
        db.session.commit()

        assert len(_records(user.id)) == 1

    def test_undo_cannot_reach_another_users_history(
            self, app, make_user, seed_plan):
        alice, bob = make_user("u_alice"), make_user("u_bob")
        seed_plan(alice.id)
        seed_plan(bob.id)
        apply_plan_mutation(alice.id, _swap_bench(), _ctx("u-cross-0001"))
        alice_text = _text(alice.id)
        bob_text = _text(bob.id)

        with pytest.raises(UndoUnavailable):
            undo_last_change(bob.id, _ctx("u-cross-0002"))

        assert _text(alice.id) == alice_text
        assert _text(bob.id) == bob_text


# ── Completed history and session state stay untouched ───────────────────────

class TestHistoricalSafety:

    def test_completed_history_survives_mutation_and_undo(
            self, app, make_user, seed_plan):
        from app.extensions import db
        from app.models import PumpCheck, WorkoutLog
        from app.timeutil import day_key

        user = make_user("h_history")
        seed_plan(user.id)
        db.session.add_all([
            WorkoutLog(user_id=user.id, exercise_name="Bench Press", sets=3,
                       reps=10, weight_kg=80, volume=2400),
            PumpCheck(user_id=user.id, valid=True, date_key=day_key()),
        ])
        db.session.commit()

        apply_plan_mutation(user.id, _swap_bench(), _ctx("h-history-0001"))
        apply_plan_mutation(
            user.id,
            RemoveExerciseCommand(day="Pazartesi", exercise="Shoulder Press"),
            _ctx("h-history-0002"))
        undo_last_change(user.id, _ctx("h-history-0003"))

        log = WorkoutLog.query.filter_by(user_id=user.id).one()
        assert (log.exercise_name, log.sets, log.reps, log.volume) == (
            "Bench Press", 3, 10, 2400)
        assert PumpCheck.query.filter_by(user_id=user.id).count() == 1

    def test_an_active_session_fingerprint_is_not_refreshed(
            self, app, make_user, seed_plan):
        """PR1 decided this boundary is not a second writer of workout-session
        state; PR2 keeps that decision. Refreshing the fingerprint here would
        make a mutation silently re-bless a session whose planned workout no
        longer exists."""
        from app.extensions import db
        from app.models import WorkoutSession
        from app.timeutil import day_key

        user = make_user("h_session")
        seed_plan(user.id)
        session = WorkoutSession(
            user_id=user.id, status="active", workout_date=day_key(),
            weekday_slot="Pazartesi", source="scheduled",
            planned_training_plan_id=_plan(user.id).id,
            plan_fingerprint="v1:" + "a" * 64,
        )
        db.session.add(session)
        db.session.commit()
        before = (session.plan_fingerprint, session.status, session.version,
                  session.updated_at, session.planned_training_plan_id)

        apply_plan_mutation(user.id, _swap_bench(), _ctx("h-session-0001"))
        undo_last_change(user.id, _ctx("h-session-0002"))

        db.session.expire_all()
        session = WorkoutSession.query.filter_by(user_id=user.id).one()
        assert (session.plan_fingerprint, session.status, session.version,
                session.updated_at, session.planned_training_plan_id) == before


# ── The legacy full-plan path is not migrated onto the journal ───────────────

class TestLegacyReplaceRouteUnchanged:

    def test_saving_a_whole_plan_writes_no_journal_entry(
            self, app, make_user, seed_plan):
        """``POST /training-plan/save`` replaces the plan wholesale. It is not a
        targeted mutation, it has no operation key, and recording it as one
        would put a snapshot in the journal that undo could then restore over a
        plan the user deliberately regenerated."""
        from app.extensions import db
        from app.models import TrainingPlan

        user = make_user("l_legacy")
        seed_plan(user.id)

        TrainingPlan.query.filter_by(user_id=user.id).delete()
        db.session.add(TrainingPlan(
            user_id=user.id, plan_data=json.dumps(_program(), ensure_ascii=False),
            score=9))
        db.session.commit()

        assert _records(user.id) == []
        assert _version(user.id) == 0
        assert _plan(user.id).lineage_id


# ── Move is versioned like any other change ──────────────────────────────────

class TestMoveDay:

    def test_moving_day_content_is_journalled_and_reversible(
            self, app, make_user, seed_plan):
        user = make_user("m_move")
        original = seed_plan(user.id)

        result = apply_plan_mutation(
            user.id, MoveTrainingDayCommand(day="Pazartesi", target_day="Salı"),
            _ctx("m-move-0001"))

        assert result.plan_version == 1
        record, = _records(user.id)
        assert record.command_type == "move_training_day"

        undo_last_change(user.id, _ctx("m-move-0002"))
        assert _text(user.id) == original
