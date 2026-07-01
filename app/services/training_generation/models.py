from dataclasses import dataclass, field
from typing import Any


LEVELS = ("Beginner", "Intermediate", "Advanced")
LEVEL_VALUE = {"beginner": 1, "intermediate": 2, "advanced": 3}
VALUE_LEVEL = {1: "Beginner", 2: "Intermediate", 3: "Advanced"}


@dataclass(frozen=True)
class TrainingPreferences:
    gun_sayisi: int = 3
    ekipman: str = "spor_salonu"
    odak: str = "tum_vucut"
    sure: int = 45
    kardiyo_tipi: str = "yok"
    kardiyo_gun: int = 0
    kardiyo_sure: int = 20
    kardiyo_yogunluk: str = "orta"
    antrenman_tarzi: str = "genel"
    odak_hedef: str = "genel"
    injuries: str = ""


@dataclass(frozen=True)
class PerformanceHistory:
    weekly_training_sessions: list[int] = field(default_factory=list)
    volume_trend: list[float] = field(default_factory=list)
    adherence_score: float = 0.0
    fatigue_trend: float = 3.0
    sleep_quality: float = 3.0
    stable_score_weeks: int = 0
    dropout_risk: bool = False


@dataclass(frozen=True)
class UserTrainingFeatures:
    age: int | None
    weight: float | None
    height: float | None
    goal: str
    self_reported_level: str
    current_activity: str
    training_history_months: int
    weekly_frequency: int
    movement_competency: dict[str, int]
    strength_proxy: float
    consistency_score: float
    injuries: str
    performance_history: PerformanceHistory = field(default_factory=PerformanceHistory)


@dataclass(frozen=True)
class ClassificationResult:
    level: str
    confidence: float
    score: int
    score_breakdown: dict[str, int]
    constraints_applied: list[str]
    risk_flags: list[str]
    reasoning_summary: str


@dataclass(frozen=True)
class ProgramContext:
    classified_level: str
    weekly_training_days: int
    recovery_capacity_factor: float
    volume_guideline: str
    intensity_guideline: str
    progression_guideline: str
    deload_guideline: str
    movement_coverage: list[str]
    style_directive: str
    risk_flags: list[str]
    constraints_applied: list[str]
    debug: dict[str, Any] = field(default_factory=dict)

