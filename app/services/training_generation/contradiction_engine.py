from app.services.training_generation.models import LEVEL_VALUE, UserTrainingFeatures


def observed_level_ceiling(features: UserTrainingFeatures) -> int:
    movement = features.movement_competency or {}
    if not movement:
        return 2
    required = ("squat", "hinge", "horizontal_push", "horizontal_pull", "core")
    minimum = min(int(movement.get(k, 0)) for k in required)
    if minimum >= 2 and features.weekly_frequency >= 4 and features.consistency_score >= 0.7:
        return 3
    if minimum >= 1 and features.weekly_frequency >= 3:
        return 2
    return 1


def detect_contradiction(features: UserTrainingFeatures) -> dict[str, float | int | bool]:
    reported = LEVEL_VALUE.get((features.self_reported_level or "beginner").lower(), 1)
    observed = observed_level_ceiling(features)
    gap = max(0, reported - observed)
    score = min(1.0, gap / 2)
    return {
        "contradiction": gap > 0,
        "contradiction_score": score,
        "observed_ceiling": observed,
    }

