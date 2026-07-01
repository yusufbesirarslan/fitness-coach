from app.services import injury_constraints
from app.services.training_generation.models import UserTrainingFeatures


def apply_hard_gates(features: UserTrainingFeatures) -> tuple[int, list[str], list[str]]:
    """Return max allowed numeric level plus constraints and risk flags."""
    max_level = 3
    constraints: list[str] = []
    risks: list[str] = []
    movement = features.movement_competency or {}

    if not movement:
        max_level = min(max_level, 2)
        constraints.append("advanced_requires_observed_competency")
        risks.append("missing_movement_data")
    else:
        if movement.get("squat", 0) <= 0:
            max_level = min(max_level, 1)
            constraints.append("squat_pattern_gate")
        if movement.get("horizontal_push", 0) <= 0:
            max_level = min(max_level, 1)
            constraints.append("push_pattern_gate")
        if movement.get("hinge", 0) <= 0:
            max_level = min(max_level, 1)
            constraints.append("hinge_pattern_gate")
        if movement.get("core", 0) <= 0:
            max_level = min(max_level, 1)
            constraints.append("core_stability_gate")

    if (features.current_activity or "").lower() == "sedentary" and features.weekly_frequency <= 2:
        max_level = min(max_level, 1)
        constraints.append("sedentary_low_frequency_gate")
        risks.append("low_activity_base")

    if injury_constraints.has_constraints(features.injuries):
        max_level = max(1, max_level - 1)
        constraints.append("injury_downgrade")
        risks.append("active_injury_or_limitation")

    return max_level, constraints, risks

