"""Removing ONE of two duplicate exercise slots through the AI Coach.

Production incident: a Friday holding the historical duplicate

    Walking Lunge — 4x15
    Walking Lunge — 4x12

could be DETECTED but never removed. ``RemoveExerciseCommand`` carried only a
day and a name, so the domain (correctly) refused the bare target as
ambiguous, the remove tool could not express "the 4x15 one", and no
server-owned continuation existed for a remove — so every answer the user gave
("second", "4x15", "remove the first Walking Lunge") re-entered the same
refusal and nothing was ever removed.

The fix keeps the bare refusal and adds an exact, optional TARGET SELECTOR
(``match_sets`` / ``match_reps``) that narrows the identity matches the domain
already computes. The Coach asks once with the stored candidates, and the
user's answer completes the stored remove into ONE ordinary confirmation.

Every persistence assertion is on the plan row, the journal, the version
counter and the proposal table — never on a mock.
"""
import copy
import json

import pytest

from app.extensions import db
from app.models import (
    PlanMutationRecord,
    TrainingPlan,
    TrainingPlanConfirmationProposal,
    WorkoutLog,
)
from app.observability import assign_request_id
from app.services import ai_coach, coach_confirmation
from app.services.coach_plan_tools import (
    clarifications,
    parser,
    proposals,
    results,
    schemas,
)
from app.services.plan_mutation import (
    AmbiguousExerciseTarget,
    ExerciseNotFound,
    InvalidPrescription,
    RemoveExerciseCommand,
)
from app.services.plan_mutation.document import apply_command
from app.services.plan_mutation.fingerprint import semantic_fingerprint
from tests.test_coach_plan_tools import (  # noqa: F401
    REMOVE, call, seed_plan, tools_on,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

def _friday(*lunges, extra=True):
    exercises = [
        {"isim": "Walking Lunge", "set": sets, "tekrar": reps,
         "dinlenme": "60 sn", "not": note}
        for sets, reps, note in lunges
    ]
    if extra:
        exercises.append({"isim": "Bodyweight Squat", "set": 3,
                          "tekrar": "12", "custom_marker": {"keep": True}})
    return exercises


def _program(friday):
    """Legacy (name-only) plan: the production owner's plan shape."""
    return {
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman", "odak": "Üst",
             "egzersizler": [{"isim": "Push-Up", "set": 3, "tekrar": "10"}]},
            {"gun": "Salı", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Çarşamba", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Perşembe", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Cuma", "tip": "antrenman", "odak": "Bacak",
             "egzersizler": friday},
            {"gun": "Cumartesi", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Pazar", "tip": "dinlenme", "egzersizler": []},
        ],
        "haftalik_ozet": {"toplam_antrenman_gun": 2},
        "unknown_top_level": [1, 2, 3],
    }


def _incident_document():
    return _program(_friday((4, "15", "a"), (4, "12", "b")))


def _canonical_document():
    document = _incident_document()
    for day in document["program"]:
        for entry in day["egzersizler"]:
            entry["exercise_id"] = {
                "Walking Lunge": "ex_walking_lunge",
                "Bodyweight Squat": "ex_bodyweight_squat",
                "Push-Up": "ex_push_up",
            }[entry["isim"]]
    document["exercise_context"] = {
        "equipment_context": "minimal", "cardio_type": "yuruyus",
        "style": "general_fitness", "catalog_version": 1}
    return document


def _cuma(document):
    program = document["program"] if isinstance(document, dict) else document
    return next(d for d in program if d["gun"] == "Cuma")["egzersizler"]


def _remove(**selectors):
    return RemoveExerciseCommand(
        day="Cuma", exercise="Walking Lunge", **selectors)


@pytest.fixture
def dup_user(app, make_user):
    user = make_user("dupremove")
    seed_plan(user.id, _incident_document())
    return user


@pytest.fixture
def twin_user(app, make_user):
    """Two duplicates that NO supported selector can tell apart."""
    user = make_user("dupremovetwin")
    seed_plan(user.id, _program(_friday((4, "15", "a"), (4, "15", "b"))))
    return user


def _turn(app, user_id, message, calls=(), language="en"):
    """One HTTP turn, in its OWN app context (so its own ``g``), as in
    production — no request-local stash can leak into the next turn."""
    with app.app_context(), app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn(message, history=[], user_id=user_id)
        pending = coach_confirmation.resolve_pending_turn(user_id, language)
        if not calls:
            return [], pending
        payloads = [call(user_id, name, args) for name, args in calls]
        reply = coach_confirmation.reply_after_tools(
            user_id, language, payloads)
        return payloads, reply


def _plan(user_id):
    db.session.expire_all()
    return TrainingPlan.query.filter_by(
        user_id=user_id).order_by(TrainingPlan.id.desc()).first()


def _friday_rx(user_id):
    return [(e["isim"], e.get("set"), e.get("tekrar"))
            for e in _cuma(json.loads(_plan(user_id).plan_data))]


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


def _proposals(user_id):
    return TrainingPlanConfirmationProposal.query.filter_by(
        user_id=user_id).order_by(TrainingPlanConfirmationProposal.id).all()


def _journal(user_id):
    return PlanMutationRecord.query.filter_by(
        user_id=user_id).order_by(PlanMutationRecord.id).all()


def _stored(app, user_id):
    """The durable record as a NEW request would see it.

    A fresh app context, because the test ``app`` fixture keeps one app
    context (and therefore one ``g``) alive across ``test_request_context``
    blocks, and ``g`` holds the per-request stash of a consumed record.
    """
    with app.app_context(), app.test_request_context("/ask", method="POST"):
        return clarifications.load(user_id)


_BARE_REMOVE = {"day": "Cuma", "exercise": "Walking Lunge"}
_INCIDENT_ASK = "remove one of the duplicate walking lunges from my Friday workout"


# ── 1-6. Domain ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("make", [_incident_document, _canonical_document])
def test_a_bare_remove_of_a_duplicate_stays_refused(make):
    document = make()
    frozen = json.dumps(document, sort_keys=True)
    with pytest.raises(AmbiguousExerciseTarget):
        apply_command(document, _remove())
    assert json.dumps(document, sort_keys=True) == frozen


@pytest.mark.parametrize("make", [_incident_document, _canonical_document])
@pytest.mark.parametrize("sets,reps,kept_reps,kept_note", [
    (4, "15", "12", "b"),
    (4, "12", "15", "a"),
])
def test_an_exact_selector_removes_only_that_slot(
        make, sets, reps, kept_reps, kept_note):
    document = make()
    original = copy.deepcopy(document)

    mutated, changed = apply_command(
        document, _remove(match_sets=sets, match_reps=reps))

    assert changed is True
    assert document == original  # the caller's document is never touched
    friday = _cuma(mutated)
    lunges = [e for e in friday if e["isim"] == "Walking Lunge"]
    assert len(lunges) == 1
    assert (lunges[0]["set"], lunges[0]["tekrar"], lunges[0]["not"]) == (
        4, kept_reps, kept_note)
    # Everything else is byte-for-byte the parsed original.
    expected = copy.deepcopy(original)
    expected_friday = _cuma(expected)
    expected_friday[:] = [
        e for e in expected_friday
        if not (e["isim"] == "Walking Lunge" and e["tekrar"] == reps)]
    assert mutated == expected
    if make is _canonical_document:
        assert lunges[0]["exercise_id"] == "ex_walking_lunge"
        assert mutated["exercise_context"] == original["exercise_context"]


@pytest.mark.parametrize("selectors", [
    {"match_sets": 3, "match_reps": "15"},
    {"match_sets": 4, "match_reps": "10"},
    {"match_reps": "8-12"},
])
def test_a_wrong_selector_writes_nothing(selectors):
    document = _incident_document()
    frozen = json.dumps(document, sort_keys=True)
    with pytest.raises(ExerciseNotFound):
        apply_command(document, _remove(**selectors))
    assert json.dumps(document, sort_keys=True) == frozen


def test_a_partial_selector_that_still_matches_twice_is_ambiguous():
    document = _incident_document()
    frozen = json.dumps(document, sort_keys=True)
    with pytest.raises(AmbiguousExerciseTarget):
        apply_command(document, _remove(match_sets=4))
    assert json.dumps(document, sort_keys=True) == frozen


def test_a_partial_selector_that_is_unique_is_enough():
    mutated, changed = apply_command(
        _incident_document(), _remove(match_reps="12"))
    assert changed
    assert [(e["set"], e["tekrar"]) for e in _cuma(mutated)
            if e["isim"] == "Walking Lunge"] == [(4, "15")]


@pytest.mark.parametrize("selectors", [
    {"match_sets": 0}, {"match_sets": True}, {"match_sets": "4"},
    {"match_reps": ""}, {"match_reps": 15},
])
def test_an_invalid_selector_is_refused_not_ignored(selectors):
    with pytest.raises(InvalidPrescription):
        apply_command(_incident_document(), _remove(**selectors))


def test_a_selector_on_a_unique_slot_still_has_to_match():
    document = _program(_friday((4, "15", "a")))
    with pytest.raises(ExerciseNotFound):
        apply_command(document, _remove(match_sets=4, match_reps="12"))
    mutated, _ = apply_command(document, _remove(match_sets=4, match_reps="15"))
    assert [e["isim"] for e in _cuma(mutated)] == ["Bodyweight Squat"]


def test_the_selector_is_remove_only_and_changes_no_identity_rule():
    """The duplicate-ADD guard and every other operation are untouched: the
    same plan still refuses an add of a third lunge the same way."""
    from app.services.plan_mutation import AddExerciseCommand
    with pytest.raises(AmbiguousExerciseTarget):
        apply_command(_incident_document(), AddExerciseCommand(
            day="Cuma", exercise="Walking Lunge", sets=4, reps="15"))


# ── 7. Tool schema / parser ─────────────────────────────────────────────────

def _published(name):
    return next(d for d in schemas.PLAN_MUTATION_TOOL_DEFS if d["name"] == name)


def test_the_remove_tool_gains_only_two_optional_selectors():
    remove = _published(REMOVE)["parameters"]
    assert set(remove["properties"]) == {"day", "exercise", "sets", "reps"}
    assert remove["required"] == ["day", "exercise"]
    assert remove["additionalProperties"] is False
    assert parser.TOOL_ARGUMENTS[REMOVE] == (("day", "exercise"),
                                             ("sets", "reps"))
    assert parser.GROUNDABLE[REMOVE] == frozenset()


def test_the_parser_maps_selectors_onto_the_typed_command():
    command = parser.build_command(
        REMOVE, {"day": "Cuma", "exercise": "Walking Lunge", "sets": 4,
                 "reps": " 15 "})
    assert command == RemoveExerciseCommand(
        day="Cuma", exercise="Walking Lunge", match_sets=4, match_reps="15")
    bare = parser.build_command(REMOVE, dict(_BARE_REMOVE))
    assert bare == RemoveExerciseCommand(day="Cuma", exercise="Walking Lunge")
    assert (bare.match_sets, bare.match_reps) == (None, None)
    absent = parser.build_command(
        REMOVE, {**_BARE_REMOVE, "sets": None, "reps": ""})
    assert absent == bare


@pytest.mark.parametrize("arguments,expected", [
    ({**_BARE_REMOVE, "sets": "4"}, "tam sayı"),
    ({**_BARE_REMOVE, "sets": True}, "tam sayı"),
    ({**_BARE_REMOVE, "reps": 15}, "metin"),
    ({**_BARE_REMOVE, "index": 1}, "beklenmeyen"),
    ({**_BARE_REMOVE, "position": "first"}, "beklenmeyen"),
])
def test_the_parser_still_refuses_bad_selectors_and_unknown_fields(
        arguments, expected):
    with pytest.raises(parser.ToolArgumentError) as exc:
        parser.build_command(REMOVE, arguments)
    assert expected in str(exc.value)


# ── 8-9. Fingerprint / proposal round trip ──────────────────────────────────

def test_the_selector_is_part_of_the_semantic_identity():
    fifteen = semantic_fingerprint(_remove(match_sets=4, match_reps="15"))
    twelve = semantic_fingerprint(_remove(match_sets=4, match_reps="12"))
    bare = semantic_fingerprint(_remove())
    assert len({fifteen, twelve, bare}) == 3
    assert semantic_fingerprint(
        _remove(match_sets=4, match_reps=" 15 ")) == fifteen


def test_a_selector_less_remove_keeps_its_persisted_fingerprint():
    """Pinned digest of the pre-change v1 payload. A remove without selectors
    must replay/conflict exactly as it did before this change."""
    import hashlib
    legacy_payload = {
        "domain": "axisai/training-plan-mutation/v1",
        "kind": "remove_exercise",
        "day": "Cuma",
        "exercise": "walking lunge",
    }
    legacy = hashlib.sha256(json.dumps(
        legacy_payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    assert semantic_fingerprint(_remove()) == legacy
    assert semantic_fingerprint(RemoveExerciseCommand(
        day=" Cuma ", exercise=" walking LUNGE ")) == legacy


@pytest.mark.parametrize("command", [
    _remove(),
    _remove(match_sets=4, match_reps="15"),
    _remove(match_reps="12"),
])
def test_the_selector_survives_proposal_encode_decode(command):
    kind, payload, fingerprint = proposals.encode_command(command)
    assert kind == "remove_exercise"
    assert fingerprint == semantic_fingerprint(command)
    stored = json.loads(json.dumps(payload))
    assert proposals.decode_command(kind, stored) == command


def test_a_pre_change_stored_remove_payload_still_decodes():
    assert proposals.decode_command(
        "remove_exercise", {"day": "Cuma", "exercise": "Walking Lunge"}
    ) == _remove()
    kind, payload, _fp = proposals.encode_command(_remove())
    assert payload == {"day": "Cuma", "exercise": "Walking Lunge"}


def test_the_pending_summary_names_the_selected_slot():
    pending = results.confirmation_required_result(
        _remove(match_sets=4, match_reps="15"), ("REMOVE_EXERCISE",))
    assert "Walking Lunge (4x15)" in pending["summary"]
    assert pending["change"] == {
        "day": "Cuma", "exercise": "Walking Lunge",
        "match_sets": 4, "match_reps": "15"}
    bare = results.confirmation_required_result(
        _remove(), ("REMOVE_EXERCISE",))
    assert bare["change"] == {"day": "Cuma", "exercise": "Walking Lunge"}


# ── 10-14. Coach continuation (blocking /ask) ───────────────────────────────

def _ask_and_answer(app, uid, answer):
    """Turn 1 (ambiguous) then turn 2 (the user's choice)."""
    payloads, ask = _turn(app, uid, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])
    _, proposal = _turn(app, uid, answer)
    return payloads, ask, proposal


def test_the_ambiguous_remove_asks_once_with_the_real_candidates(
        app, dup_user, tools_on):
    uid = dup_user.id
    before = _snapshot(uid)

    payloads, reply = _turn(app, uid, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])

    assert payloads[0]["status"] == results.STATUS_NEEDS_INPUT
    assert payloads[0]["reason"] == results.REASON_AMBIGUOUS_EXERCISE
    assert "4x15" in reply and "4x12" in reply
    assert "Walking Lunge" in reply
    assert "third" not in reply.lower()
    assert _snapshot(uid) == before
    record = _stored(app, uid)
    assert record["operation"] == "remove_exercise"
    assert record["reason"] == results.REASON_AMBIGUOUS_EXERCISE
    assert record["day"] == "Cuma"
    assert record["exercise"] == "Walking Lunge"
    assert record["candidate_slots"] == [
        {"sets": 4, "reps": "15"}, {"sets": 4, "reps": "12"}]


@pytest.mark.parametrize("answer", [
    "4x15",
    "the 4x15 one",
    "the first one",
    "first",
    "remove the first Walking Lunge",
])
def test_the_answer_completes_the_stored_remove_into_one_proposal(
        app, dup_user, tools_on, answer):
    uid = dup_user.id
    before = _snapshot(uid)

    _payloads, _ask, proposal = _ask_and_answer(app, uid, answer)

    assert proposal and "4x15" in proposal
    rows = _proposals(uid)
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].command_type == "remove_exercise"
    assert rows[0].command_payload == {
        "day": "Cuma", "exercise": "Walking Lunge",
        "match_sets": 4, "match_reps": "15"}
    # Nothing written yet, and the clarification is consumed.
    assert _friday_rx(uid) == _friday_rx_of(before)
    assert _plan(uid).mutation_version == before[1]
    assert _stored(app, uid) is None


def _friday_rx_of(snapshot):
    return [(e["isim"], e.get("set"), e.get("tekrar"))
            for e in _cuma(json.loads(snapshot[0]))]


@pytest.mark.parametrize("answer", ["4x12", "the second one", "second"])
def test_the_other_occurrence_can_be_chosen_too(
        app, dup_user, tools_on, answer):
    uid = dup_user.id
    _ask_and_answer(app, uid, answer)
    rows = _proposals(uid)
    assert len(rows) == 1
    assert (rows[0].command_payload["match_sets"],
            rows[0].command_payload["match_reps"]) == (4, "12")


def test_confirm_removes_exactly_the_chosen_slot_once(app, dup_user, tools_on):
    uid = dup_user.id
    before = _snapshot(uid)
    _ask_and_answer(app, uid, "4x15")

    _, applied = _turn(app, uid, "yes")

    assert applied and "Walking Lunge" in applied
    assert _friday_rx(uid) == [
        ("Walking Lunge", 4, "12"), ("Bodyweight Squat", 3, "12")]
    plan = _plan(uid)
    assert plan.mutation_version == before[1] + 1
    journal = _journal(uid)
    assert [(r.command_type, r.outcome) for r in journal] == [
        ("remove_exercise", "applied")]
    rows = _proposals(uid)
    assert [r.status for r in rows] == ["applied"]
    assert _stored(app, uid) is None
    # Every unrelated field survives exactly.
    expected = _incident_document()
    _cuma(expected).pop(0)
    assert json.loads(plan.plan_data) == expected

    # Duplicate delivery / retry of the confirmation, and a repeat of the
    # user's answer, never touch the remaining lunge.
    after = _snapshot(uid)
    _turn(app, uid, "yes")
    _turn(app, uid, "4x15")
    _turn(app, uid, "yes")
    assert _snapshot(uid)[:3] == after[:3]
    assert _friday_rx(uid) == [
        ("Walking Lunge", 4, "12"), ("Bodyweight Squat", 3, "12")]
    assert not [r for r in _proposals(uid) if r.status == "pending"]


def test_a_unique_target_named_up_front_goes_straight_to_one_confirmation(
        app, dup_user, tools_on):
    uid = dup_user.id
    payloads, reply = _turn(
        app, uid, "remove the 4x15 Walking Lunge from Friday",
        [(REMOVE, _BARE_REMOVE)])

    assert payloads[0]["status"] == results.STATUS_CONFIRMATION_REQUIRED
    assert "4x15" in reply
    assert [r.command_payload for r in _proposals(uid)] == [{
        "day": "Cuma", "exercise": "Walking Lunge",
        "match_sets": 4, "match_reps": "15"}]
    assert _stored(app, uid) is None

    _turn(app, uid, "yes")
    assert _friday_rx(uid) == [
        ("Walking Lunge", 4, "12"), ("Bodyweight Squat", 3, "12")]
    assert len(_journal(uid)) == 1


def test_the_users_words_outrank_the_models_selector(app, dup_user, tools_on):
    """The model sending 4x12 cannot redirect a user who said 4x15."""
    uid = dup_user.id
    _turn(app, uid, "remove the 4x15 Walking Lunge from Friday",
          [(REMOVE, {**_BARE_REMOVE, "sets": 4, "reps": "12"})])
    assert [r.command_payload["match_reps"] for r in _proposals(uid)] == ["15"]


def test_a_model_selector_without_user_words_is_not_authority(
        app, dup_user, tools_on):
    """"remove the first lunge" typed with no stored clarification: the model
    mapping that to 4x15 from plan context is exactly the positional guess the
    server must not accept — it asks instead."""
    uid = dup_user.id
    before = _snapshot(uid)
    payloads, reply = _turn(
        app, uid, "remove one of my Friday walking lunges",
        [(REMOVE, {**_BARE_REMOVE, "sets": 4, "reps": "15"})])
    assert payloads[0]["reason"] == results.REASON_AMBIGUOUS_EXERCISE
    assert _snapshot(uid) == before


def test_an_answer_that_matches_no_candidate_asks_again_and_writes_nothing(
        app, dup_user, tools_on):
    uid = dup_user.id
    before = _snapshot(uid)
    _turn(app, uid, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])

    _, reply = _turn(app, uid, "3x10")

    assert _snapshot(uid) == before
    assert reply and "4x15" in reply and "4x12" in reply
    # Still answerable.
    _, proposal = _turn(app, uid, "4x12")
    assert proposal and "4x12" in proposal
    assert len(_proposals(uid)) == 1


def test_a_new_unrelated_request_supersedes_the_stored_remove(
        app, dup_user, tools_on):
    uid = dup_user.id
    _turn(app, uid, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])
    _turn(app, uid, "Add Bench Press to Monday")
    assert _stored(app, uid) is None
    _, reply = _turn(app, uid, "4x15")
    assert not _proposals(uid)


def test_indistinguishable_duplicates_fail_closed_once(app, twin_user, tools_on):
    uid = twin_user.id
    before = _snapshot(uid)

    payloads, _reply = _turn(app, uid, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])

    assert payloads[0]["status"] == results.STATUS_ERROR
    assert payloads[0]["error"] == results.ERROR_AMBIGUOUS_TARGET
    assert _stored(app, uid) is None
    _, again = _turn(app, uid, "4x15")
    assert _snapshot(uid) == before
    assert not _proposals(uid)


def test_the_stored_clarification_is_owner_scoped(
        app, dup_user, make_user, tools_on):
    other = make_user("dupremoveother")
    seed_plan(other.id, _incident_document())
    _turn(app, dup_user.id, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])

    _turn(app, other.id, "4x15")

    assert not _proposals(other.id)
    assert _stored(app, dup_user.id) is not None


def test_continuation_fails_closed_when_the_store_is_unreadable(
        app, dup_user, tools_on, monkeypatch):
    uid = dup_user.id
    _turn(app, uid, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])
    before = _snapshot(uid)

    def _unavailable(*_args, **_kwargs):
        raise clarifications.ClarificationAuthorityUnavailable

    monkeypatch.setattr(clarifications, "load", _unavailable)
    monkeypatch.setattr(clarifications, "consume", _unavailable)

    _, reply = _turn(app, uid, "4x15")

    assert _snapshot(uid) == before
    assert reply


# ── 15. SSE /ask/stream parity ──────────────────────────────────────────────

def _script_one_tool_call(monkeypatch, tool, arguments):
    from tests.test_ai_stream import (
        _FakeBedrock, _FakeStream, _final, _tool_use_block,
    )

    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(
        ai_coach, "_run_coach_conversation_openai",
        lambda *a, **k: pytest.fail("OpenAI fallback must not run"))
    monkeypatch.setattr(ai_coach, "bedrock_client", _FakeBedrock([
        _FakeStream([], _final(
            stop_reason="tool_use",
            content=[_tool_use_block(tool, "t1", dict(arguments))])),
    ]))


def _stream(app, user_id, question):
    from app.services import ai_stream

    with app.app_context(), app.test_request_context(
            "/ask/stream", method="POST"):
        assign_request_id()
        events = list(ai_stream.stream_coach_answer(
            user_id, question, "", [], language="en"))
    return "".join(
        e.get("text") or "" for e in events if e.get("type") == "delta")


def test_ask_stream_has_the_same_three_turn_flow(
        app, dup_user, tools_on, monkeypatch):
    from tests.test_ai_stream import _FakeBedrock

    uid = dup_user.id
    before = _snapshot(uid)
    _script_one_tool_call(monkeypatch, REMOVE, _BARE_REMOVE)

    ask = _stream(app, uid, _INCIDENT_ASK)

    assert "4x15" in ask and "4x12" in ask
    assert _snapshot(uid) == before
    assert _stored(app, uid)["operation"] == "remove_exercise"

    # Turns 2 and 3 are server-owned: the provider must never be reached.
    monkeypatch.setattr(ai_coach, "bedrock_client", _FakeBedrock([]))
    proposal = _stream(app, uid, "4x15")
    assert "4x15" in proposal
    assert len(_proposals(uid)) == 1

    applied = _stream(app, uid, "yes")
    assert "Walking Lunge" in applied
    assert _friday_rx(uid) == [
        ("Walking Lunge", 4, "12"), ("Bodyweight Squat", 3, "12")]
    assert _plan(uid).mutation_version == before[1] + 1
    assert len(_journal(uid)) == 1


# ── Redis is the production authority ───────────────────────────────────────

def test_the_remove_choice_resumes_on_another_worker_via_redis_only(
        app, dup_user, tools_on, monkeypatch):
    """Ask on worker A, answer on worker B: only the shared store carries the
    candidates, and it is consumed exactly once."""
    from tests.test_coach_plan_clarification_continuity import (
        _ClarificationRedis,
    )

    shared = _ClarificationRedis()
    monkeypatch.setattr(clarifications, "_redis", lambda: shared)
    monkeypatch.setattr(clarifications, "_MEMORY", {})
    uid = dup_user.id

    _turn(app, uid, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])
    stored = json.loads(next(iter(shared.store.values())))
    assert stored["candidate_slots"] == [
        {"sets": 4, "reps": "15"}, {"sets": 4, "reps": "12"}]

    monkeypatch.setattr(clarifications, "_MEMORY", {})  # worker B
    _, proposal = _turn(app, uid, "second")

    assert proposal and "4x12" in proposal
    assert not shared.store
    assert [r.command_payload["match_reps"] for r in _proposals(uid)] == ["12"]
    _turn(app, uid, "second")
    assert len(_proposals(uid)) == 1


def test_a_redis_outage_on_the_answer_turn_writes_nothing(
        app, dup_user, tools_on, monkeypatch):
    from tests.test_coach_plan_clarification_continuity import (
        _ClarificationRedis,
    )

    shared = _ClarificationRedis()
    monkeypatch.setattr(clarifications, "_redis", lambda: shared)
    uid = dup_user.id
    _turn(app, uid, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])
    before = _snapshot(uid)

    shared.fail = True
    _, reply = _turn(app, uid, "4x15")

    assert _snapshot(uid) == before
    assert reply
# ── Differing sets, canonical plans, model vs typed ordinal ────────────────

@pytest.fixture
def sets_user(app, make_user):
    """Duplicates that differ by SETS only (same reps)."""
    user = make_user("dupremovesets")
    seed_plan(user.id, _program(_friday((3, "12", "a"), (4, "12", "b"))))
    return user


@pytest.fixture
def canonical_user(app, make_user):
    user = make_user("dupremovecanon")
    seed_plan(user.id, _canonical_document())
    return user


@pytest.mark.parametrize("answer,removed,kept", [
    ("4x12", 4, 3),
    ("the 3 sets one", 3, 4),
    ("second", 4, 3),
])
def test_duplicates_that_differ_by_sets_resolve_to_the_named_one(
        app, sets_user, tools_on, answer, removed, kept):
    uid = sets_user.id
    _, ask = _turn(app, uid, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])
    assert "3x12" in ask and "4x12" in ask

    _turn(app, uid, answer)
    assert [r.command_payload for r in _proposals(uid)] == [{
        "day": "Cuma", "exercise": "Walking Lunge",
        "match_sets": removed, "match_reps": "12"}]

    _turn(app, uid, "yes")
    assert _friday_rx(uid) == [
        ("Walking Lunge", kept, "12"), ("Bodyweight Squat", 3, "12")]
    assert len(_journal(uid)) == 1


def test_a_canonical_plan_removes_exactly_the_chosen_slot(
        app, canonical_user, tools_on):
    uid = canonical_user.id
    before = _snapshot(uid)

    _, ask = _turn(app, uid, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])
    assert "4x15" in ask and "4x12" in ask
    _turn(app, uid, "second")
    _turn(app, uid, "yes")

    plan = _plan(uid)
    document = json.loads(plan.plan_data)
    lunges = [e for e in _cuma(document) if e["isim"] == "Walking Lunge"]
    assert [(e["set"], e["tekrar"], e["exercise_id"]) for e in lunges] == [
        (4, "15", "ex_walking_lunge")]
    assert plan.mutation_version == before[1] + 1
    assert len(_journal(uid)) == 1
    expected = _canonical_document()
    _cuma(expected).pop(1)
    assert document == expected


def test_a_typed_ordinal_outranks_a_conflicting_model_selector(
        app, dup_user, tools_on):
    """Stored question, user types "second" (= 4x12), model sends 4x15.

    Grounded at the tool itself — without the pre-provider completion — the
    stored candidate the USER picked is what reaches the domain.
    """
    uid = dup_user.id
    _turn(app, uid, _INCIDENT_ASK, [(REMOVE, _BARE_REMOVE)])
    before = _snapshot(uid)

    with app.app_context(), app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn("second", history=[], user_id=uid)
        payload = call(uid, REMOVE, {**_BARE_REMOVE, "sets": 4, "reps": "15"})

    assert payload["status"] == results.STATUS_CONFIRMATION_REQUIRED
    assert [r.command_payload for r in _proposals(uid)] == [{
        "day": "Cuma", "exercise": "Walking Lunge",
        "match_sets": 4, "match_reps": "12"}]
    assert _snapshot(uid)[:3] == before[:3]


# ── Scope: other operations' continuations are untouched ───────────────────
#
# Completing a remove answers with the staged proposal's own copy. That is a
# REMOVE rule only: an add/replace continuation that stages a proposal (it
# touches the active session) still hands the turn to the provider exactly as
# before this fix, and an update continuation still applies directly.

def _session_on(user_id, weekday):
    from app.models import WorkoutSession
    from app.services.today_facts import get_active_plan
    from app.services.workout_session.models import compute_fingerprint
    from app.timeutil import day_key

    plan = get_active_plan(user_id)
    document = json.loads(plan.plan_data)
    names = [e["isim"] for d in document["program"] if d["gun"] == weekday
             for e in d["egzersizler"]]
    db.session.add(WorkoutSession(
        user_id=user_id, status="active", workout_date=day_key(),
        weekday_slot=weekday, source="scheduled",
        planned_training_plan_id=plan.id,
        plan_fingerprint=compute_fingerprint(names)))
    db.session.commit()


@pytest.fixture
def arms_session_user(app, make_user):
    from tests.test_coach_mutation_continuation_isolation import _two_arm_days
    user = make_user("dupremovescope")
    seed_plan(user.id, _two_arm_days())
    return user


def test_an_add_continuation_that_stages_a_proposal_still_hands_back(
        app, arms_session_user, tools_on):
    from tests.test_coach_plan_tools import ADD
    uid = arms_session_user.id
    _session_on(uid, "Çarşamba")
    _turn(app, uid, "Add Walking Lunges with 4 sets to my Wednesday workout",
          [(ADD, {"day": "Çarşamba", "exercise": "Walking Lunges",
                  "sets": 4})])
    before = _plan(uid).plan_data

    _, reply = _turn(app, uid, "15")

    assert reply is None  # the provider loop runs, as before this fix
    rows = _proposals(uid)
    assert [r.command_type for r in rows] == ["add_exercise"]
    assert "ACTIVE_SESSION_IMPACT" in (rows[0].reason_codes or ())
    assert _plan(uid).plan_data == before
    assert _journal(uid) == []


def test_a_replace_continuation_that_stages_a_proposal_still_hands_back(
        app, arms_session_user, tools_on):
    from tests.test_coach_plan_tools import REPLACE
    uid = arms_session_user.id
    _session_on(uid, "Cuma")
    _turn(app, uid, "Replace Barbell Curl with Dumbbell Bicep Curl on Friday",
          [(REPLACE, {"day": "Cuma", "exercise": "Barbell Curl",
                      "replacement": "Dumbbell Bicep Curl"})])
    before = _plan(uid).plan_data

    _, reply = _turn(app, uid, "yes")

    assert reply is None
    rows = _proposals(uid)
    assert [r.command_type for r in rows] == ["replace_exercise"]
    assert "ACTIVE_SESSION_IMPACT" in (rows[0].reason_codes or ())
    assert _plan(uid).plan_data == before
    assert _journal(uid) == []


def test_an_update_continuation_on_a_session_day_still_applies_directly(
        app, arms_session_user, tools_on):
    from tests.test_coach_plan_tools import PRESCRIBE
    uid = arms_session_user.id
    _session_on(uid, "Pazartesi")
    _turn(app, uid, "Change Barbell Curl to 5x8",
          [(PRESCRIBE, {"day": "", "exercise": "Barbell Curl", "sets": 5,
                        "reps": "8"})])

    _, reply = _turn(app, uid, "Monday")

    assert reply  # applied now, server copy, as before this fix
    assert _proposals(uid) == []
    assert len(_journal(uid)) == 1
    document = json.loads(_plan(uid).plan_data)
    rx = {(d["gun"], e["isim"]): (e["set"], e["tekrar"])
          for d in document["program"] for e in d["egzersizler"]}
    assert rx[("Pazartesi", "Barbell Curl")] == (5, "8")
    assert rx[("Cuma", "Barbell Curl")] == (4, "12")
