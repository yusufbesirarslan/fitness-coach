from app.services.training_generation.contradiction_engine import detect_contradiction
from app.services.training_generation.models import LEVEL_VALUE, VALUE_LEVEL, ClassificationResult, UserTrainingFeatures
from app.services.training_generation.rule_engine import apply_hard_gates
from app.services.training_generation.scoring_engine import compute_fitness_score, compute_score_breakdown, suggested_level_from_score


def classify_user(features: UserTrainingFeatures) -> ClassificationResult:
    score_breakdown = compute_score_breakdown(features)
    score = compute_fitness_score(features)
    suggested = suggested_level_from_score(score)
    reported = LEVEL_VALUE.get((features.self_reported_level or "beginner").lower(), 1)
    hard_cap, constraints, risks = apply_hard_gates(features)
    contradiction = detect_contradiction(features)

    observed_cap = int(contradiction["observed_ceiling"])
    final_numeric = min(suggested, reported, hard_cap, observed_cap)

    # Hysteresis: upgrades require sustained evidence; safety downgrades are immediate.
    if final_numeric > reported and features.performance_history.stable_score_weeks < 3:
        final_numeric = reported
        constraints.append("upgrade_requires_stability_window")

    if contradiction["contradiction"]:
        risks.append("self_report_capacity_mismatch")
        constraints.append("observed_capacity_cap")

    data_quality = 1.0
    if not features.movement_competency:
        data_quality -= 0.25
    if not features.performance_history.weekly_training_sessions:
        data_quality -= 0.1
    confidence = 0.9 * data_quality
    confidence -= float(contradiction["contradiction_score"]) * 0.25
    if "active_injury_or_limitation" in risks:
        confidence -= 0.15
    confidence = round(max(0.25, min(0.95, confidence)), 2)

    constraints = list(dict.fromkeys(constraints))
    risks = list(dict.fromkeys(risks))
    level = VALUE_LEVEL[max(1, min(3, final_numeric))]
    return ClassificationResult(
        level=level,
        confidence=confidence,
        score=score,
        score_breakdown=score_breakdown,
        constraints_applied=constraints,
        risk_flags=risks,
        reasoning_summary=(
            f"score={score}, suggested={VALUE_LEVEL[suggested]}, "
            f"reported={VALUE_LEVEL.get(reported, 'Beginner')}, cap={VALUE_LEVEL[hard_cap]}"
        ),
    )

