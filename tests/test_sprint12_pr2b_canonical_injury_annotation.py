"""Sprint 12 PR2B — canonical injury annotation ordering.

Injury warnings are warn-only and persist on ``not``. They must be keyed off
canonical exercise identity after catalog resolution, not off the provider
spelling that happened to resolve to that identity.

Alias pair used throughout (real catalog entries, not fixtures):

    "Squat" / "Barbell Squat" → ex_barbell_back_squat / "Barbell Back Squat"

PR B (server-validated catalog identity): the provider no longer spells an
exercise at all — it returns the chosen ``exercise_id``. Two alias spellings
of one lift therefore reach generation as the SAME ID, so alias-equivalence
of warnings is now structural. The alias tests are kept as regression pins
that the warning and the persisted ``not`` key off catalog identity; the
alias pair is still exercised directly at the save boundary, which keeps
name-only client plans.
"""
import ast
import json
from pathlib import Path

import pytest

from app.blueprints import training as training_bp
from app.models import TrainingPlan
from app.services.exercise_catalog import ExerciseContext, resolve_exercise, load_exercise_catalog
from app.services.training_generation.output_errors import (
    GenerationExerciseIdentityInvalidError,
    GenerationExerciseUnresolvedError,
)
from app.services.training_generation.plan_schema import EXERCISE_ID_KEY, EXERCISE_KEYS, NOTE_MAX
from app.services.training_generation.response_validator import (
    annotate_injuries,
    validate_generated_plan,
)
from app.services.training_generation.service import (
    validate_plan_for_save,
)
from app.services.training_generation import exercise_resolution
from app.services.today_facts import get_active_plan
from app.services.training_generation import service as training_service
from tests.test_sprint11_training_generation_output import (
    _generate,
    _prefs,
    _provider_exercise,
    _provider_week,
    _session,
    _stored_document,
)


ALIAS_A = "Squat"
ALIAS_B = "Barbell Squat"
CANONICAL_ID = "ex_barbell_back_squat"
CANONICAL_NAME = "Barbell Back Squat"
MENISCUS = "menisküs"
WRIST = "bilek sakatlığı"
WARNING_PREFIX = "⚠️ SAKATLIK RİSKİ"


def _alias_week(alias):
    """Provider week for the lift ``alias`` names: by its catalog ID (PR B)."""
    return _provider_week(exercises=[
        _provider_exercise(resolve_exercise(name=alias).exercise_id),
        _provider_exercise("ex_barbell_row"),
        _provider_exercise("ex_push_up"),
    ])


def _generate_alias(monkeypatch, provider_name, injuries, calls=None):
    def fake(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return json.dumps(_alias_week(provider_name))

    return _generate(
        monkeypatch, fake, preferences=_prefs(injuries=injuries),
    )


def _squat_entry(payload):
    for day in payload["program"]:
        for ex in day["egzersizler"]:
            if ex.get("exercise_id") == CANONICAL_ID or ex.get("isim") == CANONICAL_NAME:
                return ex
    raise AssertionError("canonical squat exercise missing from plan")


def test_catalog_still_resolves_squat_aliases_to_the_same_exercise():
    """The previously observed alias pair remains real on origin/main."""
    catalog = load_exercise_catalog()
    a = resolve_exercise(name=ALIAS_A, catalog=catalog)
    b = resolve_exercise(name=ALIAS_B, catalog=catalog)
    assert a.exercise_id == b.exercise_id == CANONICAL_ID
    assert a.canonical_name == b.canonical_name == CANONICAL_NAME


def test_alias_equivalent_exercises_receive_equivalent_injury_warnings(monkeypatch):
    """Same injury input + same canonical exercise → same warning, regardless of spelling."""
    payloads = [
        _generate_alias(monkeypatch, ALIAS_A, MENISCUS),
        _generate_alias(monkeypatch, ALIAS_B, MENISCUS),
    ]
    notes = []
    warning_payloads = []
    for payload in payloads:
        ex = _squat_entry(payload)
        assert ex["exercise_id"] == CANONICAL_ID
        assert ex["isim"] == CANONICAL_NAME
        assert ex["not"].startswith(WARNING_PREFIX)
        assert ex["not"].count(WARNING_PREFIX) == 1
        assert "set" in ex and ex["set"] == 3
        notes.append(ex["not"])
        squat_warnings = [
            w for w in payload["injury_warnings"] if w["egzersiz"] == CANONICAL_NAME
        ]
        assert squat_warnings
        warning_payloads.append(squat_warnings)

    assert notes[0] == notes[1]
    assert warning_payloads[0] == warning_payloads[1]


def test_alias_equivalent_exercises_remain_warning_free_when_injury_is_irrelevant(
        monkeypatch):
    """Canonicalization must not become 'always warn'."""
    for alias in (ALIAS_A, ALIAS_B):
        payload = _generate_alias(monkeypatch, alias, WRIST)
        ex = _squat_entry(payload)
        assert ex["exercise_id"] == CANONICAL_ID
        assert ex["isim"] == CANONICAL_NAME
        assert WARNING_PREFIX not in (ex["not"] or "")
        assert payload["injury_warnings"] == []


def test_existing_deadlift_warning_survives_canonicalization(monkeypatch):
    """Known warn-producing case remains warn-only after identity resolution."""
    def fake(**kwargs):
        return json.dumps(_provider_week(exercises=[
            _provider_exercise("ex_barbell_deadlift"),
            _provider_exercise("ex_leg_press"),
            _provider_exercise("ex_barbell_row"),
        ]))

    payload = _generate(
        monkeypatch, fake, preferences=_prefs(injuries="bel fıtığı"),
    )
    deadlift = payload["program"][0]["egzersizler"][0]
    safe = payload["program"][0]["egzersizler"][1]

    assert deadlift["exercise_id"] == "ex_barbell_deadlift"
    assert deadlift["isim"] == "Barbell Deadlift"
    assert deadlift["not"].startswith(WARNING_PREFIX)
    assert deadlift["set"] == 3
    assert deadlift["tekrar"] == "8-12"
    assert "Leg Press" in safe["isim"] or safe["isim"] == "Machine Leg Press"
    assert not (safe["not"] or "").startswith(WARNING_PREFIX)

    assert payload["injury_warnings"]
    assert payload["injury_warnings"][0]["egzersiz"] == "Barbell Deadlift"
    assert payload["injury_warnings"][0]["neden"] == "deadlift"


def test_alias_equivalent_warnings_persist_through_canonical_save(monkeypatch):
    """``not`` is a persisted schema key — alias spelling cannot change it."""
    context = ExerciseContext(equipment_context="spor_salonu")
    persisted = []
    for alias in (ALIAS_A, ALIAS_B):
        payload = _generate_alias(monkeypatch, alias, MENISCUS)
        saved = validate_plan_for_save({"program": payload["program"]}, context)
        ex = _squat_entry(saved)
        persisted.append({
            "exercise_id": ex["exercise_id"],
            "isim": ex["isim"],
            "not": ex["not"],
            "set": ex["set"],
            "tekrar": ex["tekrar"],
            "dinlenme": ex["dinlenme"],
        })
    assert persisted[0] == persisted[1]
    assert persisted[0]["not"].startswith(WARNING_PREFIX)
    assert persisted[0]["not"].count(WARNING_PREFIX) == 1


def test_alias_equivalent_warnings_persist_into_stored_plan_data(
        client, auth_user, monkeypatch):
    """End-to-end: generate + save cannot store alias-dependent warning text."""
    _session(auth_user)
    stored_notes = []
    for alias in (ALIAS_A, ALIAS_B):
        def _chat(name=alias, **kwargs):
            return json.dumps(_alias_week(name))
        monkeypatch.setattr(training_bp, "_heavy_chat", _chat)
        generated = client.post("/training-plan", json={
            "gun_sayisi": 3, "sure": 45, "injuries": MENISCUS,
        })
        assert generated.status_code == 200
        body = generated.get_json()
        current = get_active_plan(auth_user.id)
        saved = client.post("/training-plan/save", json={
            "plan": body["program"],
            "score": body["overall_score"],
            "exercise_context_token": body["exercise_context_token"],
            # Loop pass 1 creates, pass 2 replaces — read the precondition
            # each time rather than assuming which one this is.
            "expected_plan": None if current is None else {
                "lineage_id": current.lineage_id,
                "mutation_version": current.mutation_version,
            },
        })
        assert saved.status_code == 200
        stored = _stored_document(auth_user.id)
        stored_notes.append(_squat_entry(stored)["not"])
    assert stored_notes[0] == stored_notes[1]
    assert stored_notes[0].startswith(WARNING_PREFIX)


def test_unknown_exercise_id_fails_closed_before_injury_annotation(monkeypatch):
    """Unknown identity must not be annotated and then persisted."""
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return json.dumps(_provider_week(
            exercises=[_provider_exercise("ex_invented_laser_row")]))

    with pytest.raises(GenerationExerciseIdentityInvalidError):
        _generate(monkeypatch, fake, preferences=_prefs(injuries=MENISCUS))
    assert len(calls) == 1


def test_unresolved_exercise_with_injuries_does_not_persist(
        client, auth_user, monkeypatch):
    _session(auth_user)
    monkeypatch.setattr(
        training_bp, "_heavy_chat",
        lambda **kwargs: json.dumps(_provider_week(
            exercises=[_provider_exercise("ex_invented_laser_row")])),
    )
    response = client.post("/training-plan", json={
        "gun_sayisi": 3, "sure": 45, "injuries": MENISCUS,
    })
    assert response.status_code == 500
    assert TrainingPlan.query.filter_by(user_id=auth_user.id).count() == 0


def test_unexpected_resolution_failure_remains_fail_closed_and_is_not_an_injury_tiebreaker(
        monkeypatch):
    """Formerly the ambiguous-NAME case. ID lookup is exact, so ambiguity is
    unreachable from generation; any unexpected catalog resolution failure on
    the ID path must still fail closed, typed, before annotation."""
    from app.services.exercise_catalog import ExerciseAmbiguous

    alias_week = _alias_week(ALIAS_A)  # resolve the alias before patching

    def fake_resolve(*, name=None, catalog=None, exercise_id=None):
        raise ExerciseAmbiguous("ambiguous")

    monkeypatch.setattr(exercise_resolution, "resolve_exercise", fake_resolve)

    def fake(**kwargs):
        return json.dumps(alias_week)

    with pytest.raises(GenerationExerciseUnresolvedError) as caught:
        _generate(monkeypatch, fake, preferences=_prefs(injuries=MENISCUS))
    assert caught.value.resolution_category == "resolution_error"


def test_injury_annotation_is_warn_only(monkeypatch):
    payload = _generate_alias(monkeypatch, ALIAS_B, MENISCUS)
    ex = _squat_entry(payload)
    assert ex["set"] == 3
    assert ex["tekrar"] == "8-12"
    assert ex["dinlenme"] == "90 sn"
    assert ex["isim"] == CANONICAL_NAME
    assert len(payload["program"][0]["egzersizler"]) == 3
    assert not any(
        "tanı" in (ex["not"] or "").lower()
        or "diagnosis" in (ex["not"] or "").lower()
        for day in payload["program"] for ex in day["egzersizler"]
    )


def test_injury_annotation_does_not_add_provider_calls(monkeypatch):
    calls = []
    _generate_alias(monkeypatch, ALIAS_A, MENISCUS, calls=calls)
    assert len(calls) == 1


def test_raw_provider_entry_cannot_reach_the_warning_matcher():
    """Architecture guard: annotation requires a canonical (ID, name) pair.

    PR B: a validated provider entry now HAS an ``exercise_id`` (raw, not yet
    checked against the catalog or the choice set) and no name, so presence
    of an ID is no longer proof of canonicalization."""
    structured, _ = validate_generated_plan(_alias_week(ALIAS_A), _prefs())
    raw = structured["program"][0]["egzersizler"][0]
    assert raw[EXERCISE_ID_KEY] == CANONICAL_ID
    assert "isim" not in raw
    original_note = raw["not"]

    with pytest.raises((TypeError, ValueError)):
        annotate_injuries(structured, MENISCUS)

    assert raw["not"] == original_note
    assert WARNING_PREFIX not in (raw["not"] or "")


def test_forged_identity_pair_cannot_reach_the_warning_matcher():
    """A real ID beside a non-catalog name — or an unknown ID beside a real
    name — is refused, and refused before ANY entry is annotated."""
    structured, _ = validate_generated_plan(_alias_week(ALIAS_A), _prefs())
    days = structured["program"]
    for day in days:
        for ex in day["egzersizler"]:
            ex["isim"] = load_exercise_catalog().by_id[ex[EXERCISE_ID_KEY]].canonical_name
    last = days[2]["egzersizler"][-1]
    for forged in ({"isim": "Magic Squat"},
                   {EXERCISE_ID_KEY: "ex_unknown_squat", "isim": CANONICAL_NAME}):
        snapshot = json.dumps(structured, sort_keys=True)
        original = dict(last)
        last.update(forged)
        with pytest.raises(TypeError):
            annotate_injuries(structured, MENISCUS)
        last.clear()
        last.update(original)
        assert json.dumps(structured, sort_keys=True) == snapshot


def test_annotate_injuries_is_not_called_from_structural_validation():
    """validate_generated_plan is shape+semantics; annotation is later."""
    source = Path(
        "app/services/training_generation/response_validator.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    target = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "validate_generated_plan"
    )
    called = [
        node.func.id for node in ast.walk(target)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "annotate_injuries" not in called


def test_generation_annotates_after_canonical_exercise_resolution():
    """Behavioral ownership guard: canonicalize, then annotate, never the reverse."""
    source = Path(training_service.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    target = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "generate_training_plan_candidate"
    )
    lines = {
        "canonicalize_generated_exercises": [],
        "annotate_injuries": [],
    }
    for node in ast.walk(target):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in lines):
            lines[node.func.id].append(node.lineno)

    assert lines["canonicalize_generated_exercises"], "canonicalization call site disappeared"
    assert lines["annotate_injuries"], "injury annotation is not in the generation pipeline"
    assert max(lines["canonicalize_generated_exercises"]) < min(lines["annotate_injuries"])

    # Canonicalization stays outside the parse/truncation repair boundary.
    canonicalize_call = "plan = canonicalize_generated_exercises(plan, exercise_choices)"
    annotate_call = "annotate_injuries("
    ozet_line = 'ozet = plan.get("haftalik_ozet", {})'
    assert source.index(canonicalize_call) < source.index(annotate_call)
    assert source.index(annotate_call) < source.index(ozet_line)

    # The single bounded repair now also covers SchemaInvalidError; the
    # invariant this guards is unchanged - neither canonicalization nor
    # annotation may run from inside the repair attempt.
    repair_block = source.split(
        "except (ParseFailedError, TruncatedError, SchemaInvalidError) as exc:"
    )[1].split("except SemanticInvalidError:")[0]
    assert "annotate_injuries" not in repair_block
    assert "canonicalize_generated_exercises" not in repair_block


def test_persisted_not_key_remains_the_warning_channel():
    assert "not" in EXERCISE_KEYS
    source = Path(
        "app/services/training_generation/response_validator.py"
    ).read_text(encoding="utf-8")
    assert 'ex["not"] = f"{warn}. {note}"' in source
    assert "NOTE_MAX" in source or str(NOTE_MAX) in source


def test_save_does_not_re_derive_injury_annotation():
    """Model A: generation owns annotation; save re-validates the canonical payload."""
    source = Path(training_service.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    target = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "validate_plan_for_save"
    )
    called = [
        node.func.id for node in ast.walk(target)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "annotate_injuries" not in called
    assert "canonicalize_plan_exercises" in called
