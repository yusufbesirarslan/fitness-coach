from app.services import injury_constraints
from app.services.training_generation.models import PerformanceHistory, TrainingPreferences, UserTrainingFeatures
from app.services.training_generation.time_series_model import build_performance_history


def _to_int(value, default, low=None, high=None):
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = default
    if low is not None:
        out = max(low, out)
    if high is not None:
        out = min(high, out)
    return out


def parse_preferences(data: dict, stored_injuries: str = "") -> TrainingPreferences:
    prefs = TrainingPreferences(
        gun_sayisi=_to_int(data.get("gun_sayisi", 3), 3, 1, 6),
        ekipman=data.get("ekipman", "spor_salonu"),
        odak=data.get("odak", "tum_vucut"),
        sure=_to_int(data.get("sure", 45), 45, 15, 120),
        kardiyo_tipi=data.get("kardiyo_tipi", "yok"),
        kardiyo_gun=_to_int(data.get("kardiyo_gun", 0), 0, 0, 6),
        kardiyo_sure=_to_int(data.get("kardiyo_sure", 20), 20, 5, 90),
        kardiyo_yogunluk=data.get("kardiyo_yogunluk", "orta"),
        antrenman_tarzi=data.get("antrenman_tarzi", "genel"),
        odak_hedef=data.get("odak_hedef", "genel"),
        injuries=(data.get("injuries") or stored_injuries or "").strip(),
    )
    if prefs.kardiyo_tipi == "yok":
        return TrainingPreferences(**{**prefs.__dict__, "kardiyo_gun": 0})
    return prefs


def infer_movement_competency(last_session, preferences: TrainingPreferences) -> dict[str, int]:
    level = ((last_session.fitness_level if last_session else None) or "beginner").lower()
    # B17: advanced, intermediate ile aynı (2) idi → hareket alt-skoru advanced'ı
    # ayırt edemiyordu. Skorlama zaten min(3, ...) ile kısıyor; advanced en üst (3).
    base = {"beginner": 1, "intermediate": 2, "advanced": 3}.get(level, 1)
    if injury_constraints.has_constraints(preferences.injuries):
        base = max(0, base - 1)
    return {
        "squat": base,
        "hinge": base,
        "horizontal_push": base,
        "vertical_push": max(0, base - 1),
        "horizontal_pull": base,
        "vertical_pull": max(0, base - 1),
        "core": base,
    }


def build_features(user, last_session, preferences: TrainingPreferences, history: PerformanceHistory | None = None) -> UserTrainingFeatures:
    history = history or build_performance_history(user.id)
    frequency = preferences.gun_sayisi
    if history.weekly_training_sessions:
        observed = round(sum(history.weekly_training_sessions) / len(history.weekly_training_sessions))
        frequency = max(1, min(6, max(observed, preferences.gun_sayisi)))
    months_by_level = {"beginner": 3, "intermediate": 18, "advanced": 48}
    self_level = (getattr(last_session, "fitness_level", None) or getattr(user, "fitness_level", None) or "beginner").lower()
    return UserTrainingFeatures(
        age=getattr(last_session, "age", None) or getattr(user, "age", None),
        weight=getattr(user, "weight", None) or getattr(last_session, "weight", None),
        height=getattr(last_session, "height", None) or getattr(user, "height", None),
        goal=getattr(last_session, "goal", None) or getattr(user, "goal", "") or "",
        self_reported_level=self_level,
        current_activity=getattr(last_session, "current_activity", None) or getattr(user, "current_activity", "") or "",
        training_history_months=months_by_level.get(self_level, 3),
        weekly_frequency=frequency,
        movement_competency=infer_movement_competency(last_session, preferences),
        strength_proxy={"beginner": 0.45, "intermediate": 0.85, "advanced": 1.05}.get(self_level, 0.45),
        consistency_score=history.adherence_score,
        injuries=preferences.injuries,
        performance_history=history,
    )

