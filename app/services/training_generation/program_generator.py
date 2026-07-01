import json
from functools import lru_cache
from pathlib import Path

from app.services.training_generation.exercise_knowledge_base import REQUIRED_MOVEMENT_COVERAGE
from app.services.training_generation.models import ClassificationResult, ProgramContext, TrainingPreferences, UserTrainingFeatures
from app.services.training_generation.recovery_model import recovery_capacity_factor


ASSET_ROOT = Path(__file__).resolve().parents[1] / "training_assets"


STYLE_ALIASES = {
    "genel": "general_fitness",
    "general": "general_fitness",
    "general_fitness": "general_fitness",
    "fonksiyonel": "functional",
    "functional": "functional",
    "bodybuilding": "bodybuilding",
    "powerlifting": "powerlifting",
    "crossfit": "crossfit",
    "calisthenics": "calisthenics",
}


@lru_cache(maxsize=1)
def _style_rules() -> dict:
    with (ASSET_ROOT / "rules" / "style_rules.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def canonical_style(style: str) -> str:
    return STYLE_ALIASES.get((style or "genel").lower(), "general_fitness")


def load_few_shot(style: str) -> str:
    path = ASSET_ROOT / "few_shots" / f"{canonical_style(style)}.txt"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def build_program_context(
    features: UserTrainingFeatures,
    preferences: TrainingPreferences,
    classification: ClassificationResult,
) -> ProgramContext:
    style = canonical_style(preferences.antrenman_tarzi)
    rules = _style_rules().get(style, _style_rules()["general_fitness"])
    recovery = recovery_capacity_factor(features)
    level_key = classification.level.lower()
    volume = rules["volume"].get(level_key, rules["volume"]["beginner"])
    intensity = rules["intensity"].get(level_key, rules["intensity"]["beginner"])
    coverage = list(dict.fromkeys(REQUIRED_MOVEMENT_COVERAGE + rules.get("coverage", [])))
    directive = " ".join([
        rules["directive"],
        f"Movement coverage: {', '.join(coverage)}.",
        f"Fatigue rule: {rules.get('fatigue_rule', 'Keep hard days separated and scale volume by recovery capacity')}.",
    ])
    return ProgramContext(
        classified_level=classification.level,
        weekly_training_days=preferences.gun_sayisi,
        recovery_capacity_factor=recovery,
        volume_guideline=volume,
        intensity_guideline=intensity,
        progression_guideline=rules["progression"],
        deload_guideline=rules["deload"],
        movement_coverage=coverage,
        style_directive=directive,
        risk_flags=classification.risk_flags,
        constraints_applied=classification.constraints_applied,
        debug={"style": style, "rules_version": _style_rules().get("version", 1)},
    )
