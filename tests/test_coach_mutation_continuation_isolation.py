"""One clarification record belongs to one mutation request, and completing it
never loses what the user already said.

These are the four production failures, driven through the real executor,
the real grounding boundary and the real confirmation copy:

A. "add Walking Lunges with 4 sets" → "15" wrote ``3x15``: the parser refused
   the call for a missing ``reps`` before grounding could store the 4, so the
   next turn had nothing but the model's memory to run on.
B. a failed add left an older replace armed, and "Monday" executed the replace.
C. a replace was refused for a prescription it does not need.
D. "change X to 4x10" was refused for a day the server can resolve itself.

Every persistence assertion is on the plan row, the mutation journal, the
version counter and the confirmation/log tables — never on a mock.
"""
import pytest

from app.extensions import db
from app.models import (
    PlanMutationRecord,
    TrainingPlan,
    TrainingPlanConfirmationProposal,
    WorkoutLog,
)
from app.observability import assign_request_id
from app.services import ai_coach, coach_confirmation, coach_plan_tools
from app.services.coach_plan_tools import clarifications, grounding, parser
from app.services.coach_plan_tools.schemas import (
    ADD_EXERCISE_TOOL,
    MOVE_DAY_TOOL,
    REMOVE_EXERCISE_TOOL,
    REPLACE_EXERCISE_TOOL,
    UPDATE_PRESCRIPTION_TOOL,
)
from tests.test_coach_plan_tools import (  # noqa: F401
    ADD, MOVE, PRESCRIBE, REMOVE, REPLACE, call, seed_plan, tools_on, turn,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

def _two_arm_days():
    """Legacy plan: the same exercise on two days, plus one unique leg slot.

    Legacy on purpose. A canonical document addresses slots by
    ``exercise_id``, which only exists on entries the save boundary wrote;
    these tests are about which REQUEST a continuation belongs to, and a
    fixture that cannot be targeted at all would hide that.
    """
    return {
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman", "odak": "Arms",
             "egzersizler": [{"isim": "Barbell Curl", "set": 3,
                              "tekrar": "10"}]},
            {"gun": "Salı", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Çarşamba", "tip": "antrenman", "odak": "Legs",
             "egzersizler": [{"isim": "Bodyweight Squat", "set": 3,
                              "tekrar": "12"}]},
            {"gun": "Perşembe", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Cuma", "tip": "antrenman", "odak": "Arms",
             "egzersizler": [{"isim": "Barbell Curl", "set": 4,
                              "tekrar": "12"}]},
            {"gun": "Cumartesi", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Pazar", "tip": "dinlenme", "egzersizler": []},
        ],
    }


@pytest.fixture
def arms_user(app, make_user):
    user = make_user("continuationarms")
    seed_plan(user.id, _two_arm_days())
    return user


def _turn(app, user_id, message, calls=(), language="en"):
    """One HTTP turn: begin, run the model's tool calls, settle the reply."""
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn(message, history=[], user_id=user_id)
        pending = coach_confirmation.resolve_pending_turn(user_id, language)
        if not calls:
            return [], pending
        results = [call(user_id, name, arguments) for name, arguments in calls]
        reply = coach_confirmation.reply_after_tools(
            user_id, language, results)
        return results, reply


def _plan(user_id):
    db.session.expire_all()
    return TrainingPlan.query.filter_by(
        user_id=user_id).order_by(TrainingPlan.id.desc()).first()


def _day(user_id, day):
    import json
    document = json.loads(_plan(user_id).plan_data)
    for entry in document["program"]:
        if entry["gun"] == day:
            return entry.get("egzersizler")
    return None


def _names(user_id, day):
    return [e.get("isim") for e in (_day(user_id, day) or ())]


def _rx(user_id, day, name):
    for entry in _day(user_id, day) or ():
        if entry.get("isim") == name:
            return (entry.get("set"), entry.get("tekrar"))
    return None


def _snapshot(user_id):
    plan = _plan(user_id)
    return (
        plan.plan_data,
        plan.mutation_version,
        PlanMutationRecord.query.filter_by(user_id=user_id).count(),
        TrainingPlanConfirmationProposal.query.filter_by(
            user_id=user_id).count(),
        WorkoutLog.query.filter_by(user_id=user_id).count(),
    )


def _assert_unchanged(user_id, before):
    assert _snapshot(user_id) == before


def _journal(user_id):
    return PlanMutationRecord.query.filter_by(
        user_id=user_id).order_by(PlanMutationRecord.id).all()


# ── A. A grounded field survives the clarification ──────────────────────────

def test_a_partial_prescription_is_kept_across_the_clarification(
        app, arms_user, tools_on):
    """"4 sets" then "15" is 4x15. The production bug wrote 3x15."""
    before = _snapshot(arms_user.id)

    results, reply = _turn(
        app, arms_user.id,
        "Add Walking Lunges with 4 sets to my Wednesday workout",
        [(ADD, {"day": "Çarşamba", "exercise": "Walking Lunges", "sets": 4})])

    assert results[0]["status"] == "needs_user_input"
    assert results[0]["reason"] == "missing_reps"
    assert "reps" in reply.lower()
    _assert_unchanged(arms_user.id, before)

    _, reply = _turn(app, arms_user.id, "15")

    assert _rx(arms_user.id, "Çarşamba", "Walking Lunge") == (4, "15")
    assert "Walking Lunge" in reply
    assert _plan(arms_user.id).mutation_version == before[1] + 1
    assert [r.operation_kind for r in _journal(arms_user.id)] == ["mutation"]
    assert _names(arms_user.id, "Pazartesi") == ["Barbell Curl"]
    assert _names(arms_user.id, "Cuma") == ["Barbell Curl"]
    assert WorkoutLog.query.filter_by(user_id=arms_user.id).count() == 0


def test_b_the_reverse_partial_is_kept_too(app, arms_user, tools_on):
    """Reps supplied, sets asked for. Symmetry is the point: the rule is
    "already grounded", not "the sets field"."""
    before = _snapshot(arms_user.id)

    results, _ = _turn(
        app, arms_user.id,
        "Add Walking Lunges for 12 reps to my Wednesday workout",
        [(ADD, {"day": "Çarşamba", "exercise": "Walking Lunges",
                "reps": "12"})])

    assert results[0]["reason"] == "missing_sets"
    _assert_unchanged(arms_user.id, before)

    _turn(app, arms_user.id, "4")

    assert _rx(arms_user.id, "Çarşamba", "Walking Lunge") == (4, "12")
    assert len(_journal(arms_user.id)) == 1


def test_b2_a_half_answered_request_keeps_both_answers(app, arms_user,
                                                      tools_on):
    """Two questions, two answers, and the first answer survives the second.

    "4 sets" is grounded, then the day is asked and answered, and only then
    the reps. The sets must still be 4 when the write finally happens — this
    is the merge across MORE than one clarification hop.
    """
    uid = arms_user.id
    before = _snapshot(uid)

    results, _ = _turn(
        app, uid, "Add Walking Lunges with 4 sets to my arm workout",
        [(ADD, {"day": "", "exercise": "Walking Lunges", "sets": 4})])

    assert results[0]["reason"] == "ambiguous_workout"
    _assert_unchanged(uid, before)

    _, reply = _turn(app, uid, "Monday")

    assert "reps" in reply.lower()
    _assert_unchanged(uid, before)
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn("", history=[], user_id=uid)
        record = clarifications.load(uid)
    assert (record["day"], record["sets"], record["reps"]) == (
        "Pazartesi", 4, None)

    _turn(app, uid, "12")

    assert _rx(uid, "Pazartesi", "Walking Lunge") == (4, "12")
    assert _names(uid, "Cuma") == ["Barbell Curl"]
    assert len(_journal(uid)) == 1


# ── C. A stale operation can never execute ──────────────────────────────────

def test_c_a_failed_add_does_not_leave_the_older_replace_executable(
        app, arms_user, tools_on):
    """The incident, verbatim.

    One turn, two tool calls: a replace that needs a day, then the add the
    user actually asked for. The add's own trouble must not hand the replace
    to the next "Monday" — which is what produced "Barbell Curl has been
    replaced with Dumbbell Biceps Curl on Monday" for a user who had asked to
    ADD an exercise.
    """
    uid = arms_user.id
    before = _snapshot(uid)

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn(
            "Add Dumbbell Biceps Curl 3x10 to my arm workout",
            history=[], user_id=uid)
        stale = call(uid, REPLACE, {
            "day": "Pazartesi", "exercise": "Barbell Curl",
            "replacement": "Dumbbell Curl"})
        assert stale["status"] == "needs_user_input"
        fresh = call(uid, ADD, {
            "day": "", "exercise": "Dumbbell Biceps Curl", "sets": 3,
            "reps": "10"})
        assert fresh["status"] == "needs_user_input"
        record = clarifications.load(uid)

    # The pending record is the request the user is actually in.
    assert record["operation"] == "add_exercise"
    assert record["exercise"] == "Dumbbell Biceps Curl"
    assert record["replacement"] == ""
    _assert_unchanged(uid, before)

    _, reply = _turn(app, uid, "Monday")

    journal = _journal(uid)
    assert len(journal) == 1
    performed = journal[0].command_type
    assert performed == "add_exercise"
    assert performed != "replace_exercise", "the stale operation executed"
    assert _names(uid, "Pazartesi") == ["Barbell Curl", "Dumbbell Biceps Curl"]
    assert _rx(uid, "Pazartesi", "Barbell Curl") == (3, "10")
    assert "replaced" not in reply.lower()


def test_c2_an_incompatible_new_request_supersedes_before_it_can_fail(
        app, arms_user, tools_on):
    """Supersession happens at the REQUEST boundary, so it also happens when
    the new request is refused outright by the parser."""
    uid = arms_user.id

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn("Swap my curls", history=[], user_id=uid)
        call(uid, REPLACE, {"day": "Pazartesi", "exercise": "Barbell Curl",
                            "replacement": "Dumbbell Curl"})
        assert clarifications.load(uid)["operation"] == "replace_exercise"
        refused = call(uid, MOVE, {"day": "Cuma"})
        assert refused["error"] == "INVALID_ARGUMENTS"
        assert clarifications.load(uid) is None

    before = _snapshot(uid)
    _turn(app, uid, "Monday")
    _assert_unchanged(uid, before)


def test_c3_the_same_request_is_never_superseded_by_its_own_continuation(
        app, arms_user, tools_on):
    """The server re-issues the command it is completing. If supersession
    could not tell that apart from a new intention, every continuation would
    clear the record it is completing and nothing would ever apply."""
    uid = arms_user.id
    _turn(app, uid,
          "Add Walking Lunges with 4 sets to my Wednesday workout",
          [(ADD, {"day": "Çarşamba", "exercise": "Walking Lunges",
                  "sets": 4})])

    _turn(app, uid, "15")

    assert _rx(uid, "Çarşamba", "Walking Lunge") == (4, "15")


# ── D. Ambiguous add ────────────────────────────────────────────────────────

def test_d_an_ambiguous_add_asks_and_then_lands_on_the_named_day(
        app, arms_user, tools_on):
    uid = arms_user.id
    before = _snapshot(uid)

    results, reply = _turn(
        app, uid, "Add Walking Lunges 3x10 to my arm workout",
        [(ADD, {"day": "", "exercise": "Walking Lunges", "sets": 3,
                "reps": "10"})])

    assert results[0]["reason"] == "ambiguous_workout"
    assert "Monday" in reply and "Friday" in reply
    _assert_unchanged(uid, before)

    _turn(app, uid, "Friday")

    assert _names(uid, "Cuma") == ["Barbell Curl", "Walking Lunge"]
    assert _names(uid, "Pazartesi") == ["Barbell Curl"]
    assert len(_journal(uid)) == 1


# ── E/F. Replace ────────────────────────────────────────────────────────────

def test_e_replace_inherits_the_prescription_it_was_not_given(
        app, arms_user, tools_on):
    """Replace needs source + replacement + day. Sets and reps are optional
    and inherit — asking for them was the regression."""
    uid = arms_user.id

    results, reply = _turn(
        app, uid, "Replace Barbell Curl with Dumbbell Curl in my Friday workout",
        [(REPLACE, {"day": "Cuma", "exercise": "Barbell Curl",
                    "replacement": "Dumbbell Curl"})])

    assert results[0]["status"] == "applied"
    assert _names(uid, "Cuma") == ["Dumbbell Biceps Curl"]
    assert _rx(uid, "Cuma", "Dumbbell Biceps Curl") == (4, "12")
    assert "Friday" in reply
    assert _names(uid, "Pazartesi") == ["Barbell Curl"]


def test_e2_replace_survives_a_provider_that_blanks_the_optional_fields(
        app, arms_user, tools_on):
    """Providers emit "" for "I have nothing for this". Refusing that is how
    a replace ended up asking for sets and reps."""
    uid = arms_user.id

    results, _ = _turn(
        app, uid, "Replace Barbell Curl with Dumbbell Curl in my Friday workout",
        [(REPLACE, {"day": "Cuma", "exercise": "Barbell Curl",
                    "replacement": "Dumbbell Curl", "sets": "", "reps": ""})])

    assert results[0]["status"] == "applied"
    assert _rx(uid, "Cuma", "Dumbbell Biceps Curl") == (4, "12")


def test_f_replace_with_a_fuzzy_destination_asks_then_applies(
        app, arms_user, tools_on):
    uid = arms_user.id
    before = _snapshot(uid)

    results, reply = _turn(
        app, uid,
        "Replace Barbell Curl with Dumbbell Bicep Curl on Friday",
        [(REPLACE, {"day": "Cuma", "exercise": "Barbell Curl",
                    "replacement": "Dumbbell Bicep Curl"})])

    assert results[0]["reason"] == "exercise_suggest"
    assert "Dumbbell Biceps Curl" in reply
    _assert_unchanged(uid, before)

    _turn(app, uid, "yes")

    assert _names(uid, "Cuma") == ["Dumbbell Biceps Curl"]
    assert _rx(uid, "Cuma", "Dumbbell Biceps Curl") == (4, "12")
    assert len(_journal(uid)) == 1


# ── G/H. Update prescription ────────────────────────────────────────────────

def test_g_update_resolves_a_unique_slot_without_being_told_the_day(
        app, arms_user, tools_on):
    """"Change Bodyweight Squat to 4x10" names one slot in the whole plan."""
    uid = arms_user.id

    results, reply = _turn(
        app, uid, "Change Bodyweight Squat to 4x10",
        [(PRESCRIBE, {"day": "", "exercise": "Bodyweight Squat", "sets": 4,
                      "reps": "10"})])

    assert results[0]["status"] == "applied"
    assert _rx(uid, "Çarşamba", "Bodyweight Squat") == (4, "10")
    assert reply and "Monday" not in reply and "Friday" not in reply
    assert _rx(uid, "Pazartesi", "Barbell Curl") == (3, "10")
    assert _rx(uid, "Cuma", "Barbell Curl") == (4, "12")


def test_h_an_ambiguous_update_asks_only_for_the_day(app, arms_user, tools_on):
    """The prescription the user gave is preserved across the question — the
    server must not come back asking for 4x10 again."""
    uid = arms_user.id
    before = _snapshot(uid)

    results, reply = _turn(
        app, uid, "Change Barbell Curl to 5x8",
        [(PRESCRIBE, {"day": "", "exercise": "Barbell Curl", "sets": 5,
                      "reps": "8"})])

    assert results[0]["reason"] == "ambiguous_workout"
    assert "Monday" in reply and "Friday" in reply
    _assert_unchanged(uid, before)

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn("", history=[], user_id=uid)
        record = clarifications.load(uid)
    assert (record["sets"], record["reps"]) == (5, "8")
    assert record["exercise"] == "Barbell Curl"
    assert record["operation"] == "update_exercise_prescription"

    _turn(app, uid, "Monday")

    assert _rx(uid, "Pazartesi", "Barbell Curl") == (5, "8")
    assert _rx(uid, "Cuma", "Barbell Curl") == (4, "12")
    assert len(_journal(uid)) == 1


# ── I. Supersession ─────────────────────────────────────────────────────────

def test_i_a_new_request_supersedes_the_pending_one_across_turns(
        app, arms_user, tools_on):
    uid = arms_user.id

    _turn(app, uid, "Change Barbell Curl to 5x8",
          [(PRESCRIBE, {"day": "", "exercise": "Barbell Curl", "sets": 5,
                        "reps": "8"})])
    _turn(app, uid, "Add Walking Lunges 3x10 to my leg workout",
          [(ADD, {"day": "", "exercise": "Walking Lunges", "sets": 3,
                  "reps": "10"})])

    journal = _journal(uid)
    assert [r.command_type for r in journal] == ["add_exercise"]
    assert _rx(uid, "Pazartesi", "Barbell Curl") == (3, "10")
    assert _rx(uid, "Cuma", "Barbell Curl") == (4, "12")
    assert _names(uid, "Çarşamba") == ["Bodyweight Squat", "Walking Lunge"]


# ── J. Unknown exercise ─────────────────────────────────────────────────────

def test_j_an_unknown_exercise_stores_nothing_to_continue_from(
        app, arms_user, tools_on):
    uid = arms_user.id
    before = _snapshot(uid)

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn(
            "Add blabla 3x10 to my Friday workout", history=[], user_id=uid)
        result = call(uid, ADD, {"day": "Cuma", "exercise": "blabla",
                                 "sets": 3, "reps": "10"})
        assert result["reason"] == "exercise_unknown"
        assert clarifications.load(uid) is None

    _turn(app, uid, "yes")
    _turn(app, uid, "Friday")
    _assert_unchanged(uid, before)


# ── Request identity ────────────────────────────────────────────────────────

def test_request_identity_ignores_the_fields_a_continuation_may_fill():
    """Day, sets and reps are what a clarification EXISTS to settle, so a
    request that has not learned them yet is the same request as the one that
    has. Operation and the two names are not."""
    base = grounding.request_id("add_exercise", "Walking Lunge")
    assert base == grounding.request_id("add_exercise", "  walking  lunge  ")
    assert base != grounding.request_id("replace_exercise", "Walking Lunge")
    assert base != grounding.request_id("add_exercise", "Barbell Curl")
    assert (grounding.request_id("replace_exercise", "A", "B")
            != grounding.request_id("replace_exercise", "A", "C"))


def test_a_suggestion_is_the_same_request_as_the_typo_it_corrects():
    record = {"operation": "add_exercise", "exercise": "Walkin Lunges",
              "suggestion": "Walking Lunge"}
    assert grounding.request_matches_record(
        record, "add_exercise", "Walking Lunge")
    assert grounding.request_matches_record(
        record, "add_exercise", "Walkin Lunges")
    assert not grounding.request_matches_record(
        record, "add_exercise", "Barbell Curl")
    assert not grounding.request_matches_record(
        record, "replace_exercise", "Walking Lunge")


def test_a_consumed_record_from_another_request_is_not_executed():
    """``load`` then ``consume`` is two reads of a shared store. What the
    arguments were planned from and what was actually taken must agree."""
    taken = {"operation": "replace_exercise", "exercise": "Barbell Curl",
             "replacement": "Dumbbell Curl", "suggestion": ""}
    assert not grounding.continuation_matches_record(
        taken, ADD, {"day": "Pazartesi", "exercise": "Walking Lunge",
                     "sets": 3, "reps": "10"})
    assert grounding.continuation_matches_record(
        taken, REPLACE, {"day": "Pazartesi", "exercise": "Barbell Curl",
                         "replacement": "Dumbbell Curl"})


def test_every_stored_clarification_carries_its_request(app, arms_user,
                                                        tools_on):
    uid = arms_user.id
    _turn(app, uid, "Add Walking Lunges 3x10 to my arm workout",
          [(ADD, {"day": "", "exercise": "Walking Lunges", "sets": 3,
                  "reps": "10"})])
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn("", history=[], user_id=uid)
        record = clarifications.load(uid)
    assert record["request_id"] == grounding.request_id(
        "add_exercise", "Walking Lunge")


def test_the_record_writer_never_drops_an_already_grounded_field(
        app, arms_user, tools_on):
    """The merge is structural, not a habit of the call sites.

    ``_needs_input`` is the ONLY place a clarification is written, and it is
    monotonic on its own: a caller that asks a second question without
    re-supplying what the first one already grounded still stores the 4. That
    property is what makes "a later clarification may fill missing fields but
    never discards grounded ones" true for every call site, including ones
    written after this change.
    """
    from app.services.plan_mutation import AddExerciseCommand

    uid = arms_user.id
    _turn(app, uid, "Add Walking Lunges with 4 sets to my arm workout",
          [(ADD, {"day": "", "exercise": "Walking Lunges", "sets": 4})])

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn("", history=[], user_id=uid)
        assert clarifications.load(uid)["sets"] == 4
        # No ``user_rx``: the caller supplies nothing about the prescription.
        grounding._needs_input(
            uid, "missing_prescription",
            AddExerciseCommand(day="Pazartesi", exercise="Walking Lunge"))
        after = clarifications.load(uid)
    assert (after["sets"], after["exercise"]) == (4, "Walking Lunge")


# ── The parser boundary ─────────────────────────────────────────────────────

def test_groundable_fields_are_declared_and_bounded():
    """Only fields the server can actually resolve, and only on the tools
    that have a continuation path."""
    for name, (required, optional) in parser.TOOL_ARGUMENTS.items():
        assert name in parser.GROUNDABLE, name
        assert parser.GROUNDABLE[name] <= set(required) | set(optional), name
    assert parser.GROUNDABLE[REMOVE_EXERCISE_TOOL] == frozenset()
    assert parser.GROUNDABLE[MOVE_DAY_TOOL] == frozenset()
    assert "exercise" not in parser.GROUNDABLE[ADD_EXERCISE_TOOL]
    assert "replacement" not in parser.GROUNDABLE[REPLACE_EXERCISE_TOOL]
    for name in parser.GROUNDABLE:
        assert "target_day" not in parser.GROUNDABLE[name]
    assert set(parser.GROUNDABLE) == set(parser.TOOL_ARGUMENTS)
    # Every groundable operation must have somewhere to be continued.
    for name, fields in parser.GROUNDABLE.items():
        if fields:
            assert name in set(grounding._OPERATION_TOOLS.values()), name


@pytest.mark.parametrize("name,arguments", [
    (ADD_EXERCISE_TOOL, {"day": "", "exercise": "X", "sets": 3, "reps": "8"}),
    (ADD_EXERCISE_TOOL, {"day": "Cuma", "exercise": "X"}),
    (ADD_EXERCISE_TOOL, {"day": "Cuma", "exercise": "X", "sets": None,
                         "reps": ""}),
    (REPLACE_EXERCISE_TOOL, {"day": "", "exercise": "X", "replacement": "Y"}),
    (REPLACE_EXERCISE_TOOL, {"day": "Cuma", "exercise": "X",
                             "replacement": "Y", "sets": "", "reps": ""}),
    (UPDATE_PRESCRIPTION_TOOL, {"day": "", "exercise": "X", "sets": 4}),
])
def test_a_groundable_gap_is_not_a_refusal(name, arguments):
    fields = parser.parse_tool_arguments(name, arguments)
    for field, value in arguments.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            assert field not in fields
    parser.build_command(name, arguments)


@pytest.mark.parametrize("name,arguments,expected", [
    # A present but wrong-typed value is a model defect, not a gap.
    (ADD_EXERCISE_TOOL, {"day": "Cuma", "exercise": "X", "sets": "3",
                         "reps": "8"}, "tam sayı"),
    (ADD_EXERCISE_TOOL, {"day": "Cuma", "exercise": "X", "sets": True,
                         "reps": "8"}, "tam sayı"),
    # A target nobody named is still refused.
    (ADD_EXERCISE_TOOL, {"day": "Cuma", "exercise": "   ", "sets": 3,
                         "reps": "8"}, "boş"),
    (REPLACE_EXERCISE_TOOL, {"day": "Cuma", "exercise": "X",
                             "replacement": "  "}, "boş"),
    (REPLACE_EXERCISE_TOOL, {"day": "Cuma", "exercise": "X"}, "eksik"),
    # Remove and move ground nothing.
    (REMOVE_EXERCISE_TOOL, {"day": "", "exercise": "X"}, "boş"),
    (MOVE_DAY_TOOL, {"day": "", "target_day": "Cuma"}, "boş"),
    (MOVE_DAY_TOOL, {"day": "Cuma", "target_day": ""}, "boş"),
])
def test_what_the_boundary_still_refuses(name, arguments, expected):
    with pytest.raises(parser.ToolArgumentError) as exc:
        parser.build_command(name, arguments)
    assert expected in str(exc.value)


def test_an_ungroundable_day_is_asked_for_not_guessed(app, arms_user,
                                                      tools_on):
    """No weekday, no nickname, no unique slot: the server asks rather than
    letting the domain answer "day is required"."""
    uid = arms_user.id
    before = _snapshot(uid)

    results, _ = _turn(
        app, uid, "Add Walking Lunges 3x10",
        [(ADD, {"day": "", "exercise": "Walking Lunges", "sets": 3,
                "reps": "10"})])

    assert results[0]["status"] == "needs_user_input"
    assert results[0]["reason"] == "workout_not_found"
    _assert_unchanged(uid, before)


# ── Phase 5: consumption and replay ─────────────────────────────────────────

def test_a_continuation_is_consumed_once(app, arms_user, tools_on):
    uid = arms_user.id
    _turn(app, uid, "Add Walking Lunges 3x10 to my arm workout",
          [(ADD, {"day": "", "exercise": "Walking Lunges", "sets": 3,
                  "reps": "10"})])

    _turn(app, uid, "Friday")
    after_first = _snapshot(uid)
    _turn(app, uid, "Friday")

    assert _snapshot(uid) == after_first
    assert len(_journal(uid)) == 1
    assert _names(uid, "Cuma").count("Walking Lunge") == 1


def test_continuation_fails_closed_when_the_shared_store_is_unreadable(
        app, arms_user, tools_on, monkeypatch):
    """Redis is the production authority. Unreadable means no continuation —
    never a fall back to whatever this worker happens to remember."""
    uid = arms_user.id
    _turn(app, uid, "Add Walking Lunges 3x10 to my arm workout",
          [(ADD, {"day": "", "exercise": "Walking Lunges", "sets": 3,
                  "reps": "10"})])
    before = _snapshot(uid)

    def _unavailable(*_args, **_kwargs):
        raise clarifications.ClarificationAuthorityUnavailable

    monkeypatch.setattr(clarifications, "load", _unavailable)
    monkeypatch.setattr(clarifications, "consume", _unavailable)

    _, reply = _turn(app, uid, "Friday")

    _assert_unchanged(uid, before)
    assert reply
    assert "added" not in reply.lower()


def test_supersession_fails_closed_when_the_store_is_unreadable(
        app, arms_user, tools_on, monkeypatch):
    """An unreadable store must not become a silent "nothing pending" that
    lets the mutation through unsupervised — the continuation path already
    refuses to execute from it, so nothing is armed either way."""
    def _unavailable(*_args, **_kwargs):
        raise clarifications.ClarificationAuthorityUnavailable

    monkeypatch.setattr(clarifications, "load", _unavailable)
    grounding.supersede_stale_clarification(
        arms_user.id, ADD, {"exercise": "Walking Lunges"})


def test_the_flag_still_closes_every_door(app, arms_user, tools_on,
                                          monkeypatch):
    uid = arms_user.id
    _turn(app, uid, "Add Walking Lunges 3x10 to my arm workout",
          [(ADD, {"day": "", "exercise": "Walking Lunges", "sets": 3,
                  "reps": "10"})])
    before = _snapshot(uid)

    app.config[coach_plan_tools.FLAG_KEY] = False
    try:
        _turn(app, uid, "Friday")
    finally:
        app.config[coach_plan_tools.FLAG_KEY] = True

    _assert_unchanged(uid, before)
