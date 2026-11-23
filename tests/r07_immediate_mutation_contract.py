"""Exercise accepted immediate-write mechanics independently of launch policy.

Legacy scenarios cover operation-key budgets, grounding and provider recovery
on the immediate path. The R-07 launch suites separately exercise material
proposals with the actual policy and explicit confirmation.
"""
import pytest

from app.services.coach_plan_policy import APPLY_NOW, UNDO_LAST_CHANGE, ImpactDecision
from app.services.coach_plan_tools import executor
from app.services.plan_mutation import (
    AddExerciseCommand, ReplaceExerciseCommand, UpdateExercisePrescriptionCommand,
)


@pytest.fixture
def immediate_mutation_contract(monkeypatch):
    decide = executor.decide_plan_mutation_impact

    def decide_for_immediate_path(command, facts):
        target = command is UNDO_LAST_CHANGE or isinstance(
            command, (AddExerciseCommand, ReplaceExerciseCommand, UpdateExercisePrescriptionCommand))
        if target and not facts.touches_active_session and not facts.impact_undeterminable:
            return ImpactDecision(APPLY_NOW, ())
        return decide(command, facts)

    monkeypatch.setattr(executor, "decide_plan_mutation_impact", decide_for_immediate_path)
