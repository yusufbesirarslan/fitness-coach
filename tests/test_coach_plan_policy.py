"""Pure server-owned plan-mutation impact policy (Adaptive Coaching S1 PR4).

No Flask, no provider, no database. The decision is a function of a typed
command plus facts the caller already proved. LOW/MEDIUM/HIGH never appear.
"""
import ast
from pathlib import Path

from app.services.coach_plan_policy import (
    APPLY_NOW,
    REASON_ACTIVE_SESSION_IMPACT,
    REASON_IMPACT_UNDETERMINABLE,
    REASON_MOVE_TRAINING_DAY,
    REASON_REMOVE_EXERCISE,
    REASON_UNKNOWN_COMMAND,
    REFUSE_UNSUPPORTED,
    REQUIRE_CONFIRMATION,
    UNDO_LAST_CHANGE,
    ImpactFacts,
    decide_plan_mutation_impact,
)
from app.services.plan_mutation import (
    AddExerciseCommand,
    MoveTrainingDayCommand,
    RemoveExerciseCommand,
    ReplaceExerciseCommand,
    UpdateExercisePrescriptionCommand,
)


POLICY_ROOT = Path("app/services/coach_plan_policy")

_SAFE_FACTS = ImpactFacts(touches_active_session=False)


def _replace():
    return ReplaceExerciseCommand(
        day="Pazartesi", exercise="Bench Press", replacement="Machine Press")


def _add():
    return AddExerciseCommand(
        day="Pazartesi", exercise="Cable Fly", sets=3, reps="12")


def _remove():
    return RemoveExerciseCommand(day="Pazartesi", exercise="Bench Press")


def _prescribe():
    return UpdateExercisePrescriptionCommand(
        day="Cuma", exercise="Squat", sets=4)


def _move():
    return MoveTrainingDayCommand(day="Cuma", target_day="Cumartesi")


def test_replace_add_and_prescription_apply_now_without_session_impact():
    for command in (_replace(), _add(), _prescribe()):
        decision = decide_plan_mutation_impact(command, _SAFE_FACTS)
        assert decision.verdict == APPLY_NOW
        assert decision.reason_codes == ()


def test_remove_always_requires_confirmation():
    decision = decide_plan_mutation_impact(_remove(), _SAFE_FACTS)
    assert decision.verdict == REQUIRE_CONFIRMATION
    assert REASON_REMOVE_EXERCISE in decision.reason_codes


def test_move_day_always_requires_confirmation():
    decision = decide_plan_mutation_impact(_move(), _SAFE_FACTS)
    assert decision.verdict == REQUIRE_CONFIRMATION
    assert REASON_MOVE_TRAINING_DAY in decision.reason_codes


def test_any_typed_mutation_that_touches_an_active_session_requires_confirmation():
    facts = ImpactFacts(touches_active_session=True)
    for command in (_replace(), _add(), _prescribe(), _remove(), _move()):
        decision = decide_plan_mutation_impact(command, facts)
        assert decision.verdict == REQUIRE_CONFIRMATION
        assert REASON_ACTIVE_SESSION_IMPACT in decision.reason_codes


def test_undo_applies_now_unless_it_touches_an_active_session():
    safe = decide_plan_mutation_impact(UNDO_LAST_CHANGE, _SAFE_FACTS)
    assert safe.verdict == APPLY_NOW
    risky = decide_plan_mutation_impact(
        UNDO_LAST_CHANGE, ImpactFacts(touches_active_session=True))
    assert risky.verdict == REQUIRE_CONFIRMATION
    assert REASON_ACTIVE_SESSION_IMPACT in risky.reason_codes


def test_unknown_command_is_refused_never_downgraded_to_apply_now():
    decision = decide_plan_mutation_impact(object(), _SAFE_FACTS)
    assert decision.verdict == REFUSE_UNSUPPORTED
    assert REASON_UNKNOWN_COMMAND in decision.reason_codes


def test_undeterminable_impact_is_refused_never_downgraded_to_apply_now():
    facts = ImpactFacts(touches_active_session=False, impact_undeterminable=True)
    decision = decide_plan_mutation_impact(_replace(), facts)
    assert decision.verdict == REFUSE_UNSUPPORTED
    assert REASON_IMPACT_UNDETERMINABLE in decision.reason_codes


def test_session_impact_and_remove_stack_reason_codes_without_changing_verdict():
    decision = decide_plan_mutation_impact(
        _remove(), ImpactFacts(touches_active_session=True))
    assert decision.verdict == REQUIRE_CONFIRMATION
    assert REASON_REMOVE_EXERCISE in decision.reason_codes
    assert REASON_ACTIVE_SESSION_IMPACT in decision.reason_codes


def test_vocabulary_does_not_include_low_medium_high():
    from app.services.coach_plan_policy import decisions as module
    source = Path(module.__file__).read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in ("low", "medium", "high", "risk_score", "confidence"):
        assert forbidden not in lowered


def test_policy_package_imports_neither_flask_nor_providers():
    forbidden = (
        "flask", "openai", "anthropic", "boto3",
        "app.services.ai_coach", "app.services.ai_stream",
        "app.services.prompt_builder", "app.prompts",
        "app.extensions", "app.models", "sqlalchemy",
    )
    violations = []
    for path in sorted(POLICY_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if any(name == prefix or name.startswith(prefix + ".")
                       for prefix in forbidden):
                    violations.append(f"{path}:{node.lineno} -> {name}")
    assert not violations, violations
