"""Independent modalities inside the existing seven calendar-day records.

A mixed day keeps tip=antrenman and appends timed cardio prescriptions to its
exercise list. The native v1 reader can display and execute both unchanged.
Identity/equipment authority still belongs to exercise_resolution.
"""
import re

from app.services.exercise_catalog import CARDIO_MOVEMENT, load_exercise_catalog
from app.services.training_generation.output_errors import SemanticInvalidError

_MINUTES = re.compile(r"([1-9][0-9]*) dk")


def cardio_exercises(day):
    """Known cardio IDs only; unknown IDs remain the identity gate's concern."""
    catalog = load_exercise_catalog()
    return [exercise for exercise in day["egzersizler"]
            if (entry := catalog.by_id.get(exercise.get("exercise_id")))
            and entry.movement == CARDIO_MOVEMENT]


def overlap_minutes(day):
    """Exact timed prescriptions let us validate each modality's duration."""
    minutes = 0
    for exercise in cardio_exercises(day):
        match = _MINUTES.fullmatch(exercise["tekrar"])
        if exercise["set"] != 1 or not match:
            raise SemanticInvalidError("aynı gün kardiyo tek set ve dakika içermeli")
        minutes += int(match[1])
    return minutes
