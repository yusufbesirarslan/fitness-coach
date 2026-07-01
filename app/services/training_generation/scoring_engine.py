from app.services.training_generation.models import UserTrainingFeatures


def _movement_score(features: UserTrainingFeatures) -> int:
    required = ("squat", "hinge", "horizontal_push", "horizontal_pull", "core")
    if not features.movement_competency:
        return 0
    total = sum(max(0, min(3, int(features.movement_competency.get(k, 0)))) for k in required)
    return round((total / (len(required) * 3)) * 30)


def compute_score_breakdown(features: UserTrainingFeatures) -> dict[str, int]:
    months = max(0, features.training_history_months or 0)
    frequency = max(0, features.weekly_frequency or 0)
    experience = min(25, round(months / 36 * 25))
    freq_score = min(20, round(frequency / 5 * 20))
    movement = _movement_score(features)
    strength = min(15, round(max(0, features.strength_proxy) / 1.2 * 15))
    consistency = min(10, round(max(0, min(1, features.consistency_score)) * 10))
    return {
        "experience": experience,
        "frequency": freq_score,
        "movement": movement,
        "strength": strength,
        "consistency": consistency,
    }


def compute_fitness_score(features: UserTrainingFeatures) -> int:
    return sum(compute_score_breakdown(features).values())


def suggested_level_from_score(score: int) -> int:
    if score >= 75:
        return 3
    if score >= 45:
        return 2
    return 1

