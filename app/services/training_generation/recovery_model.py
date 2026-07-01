from app.services.training_generation.models import UserTrainingFeatures


def recovery_capacity_factor(features: UserTrainingFeatures) -> float:
    factor = 1.0
    age = features.age or 30
    hist = features.performance_history

    if age >= 50:
        factor -= 0.15
    elif age >= 40:
        factor -= 0.08

    if features.weekly_frequency >= 6:
        factor -= 0.12
    elif features.weekly_frequency <= 2:
        factor -= 0.05

    if hist.fatigue_trend >= 4:
        factor -= 0.18
    elif hist.fatigue_trend <= 2:
        factor += 0.05

    if hist.sleep_quality and hist.sleep_quality <= 2:
        factor -= 0.15
    elif hist.sleep_quality and hist.sleep_quality >= 4:
        factor += 0.05

    if hist.dropout_risk:
        factor -= 0.1

    return round(max(0.5, min(1.2, factor)), 2)

