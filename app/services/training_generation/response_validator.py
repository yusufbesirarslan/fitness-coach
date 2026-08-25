"""Fail-closed structural validation of a generated training plan.

Parse validity is not semantic validity. This module answers: is the object
the canonical 7-day shape? Semantic checks live in semantic_validator.py.
Numeric clamps and weekday backfill are gone — malformed values are rejected.
"""
from __future__ import annotations

from app.services import injury_constraints
from app.services.training_generation.output_errors import (
    PlanValidationError,
    SchemaInvalidError,
)
from app.services.training_generation.plan_schema import (
    CALORIE_MAX,
    CALORIE_MIN,
    DAY_KEYS,
    DURATION_MAX,
    DURATION_MIN,
    EXERCISE_ID_MAX,
    EXERCISE_ID_KEY,
    EXERCISE_KEYS,
    EXERCISE_KEYS_WITH_ID,
    FOCUS_MAX,
    NAME_MAX,
    NOTE_MAX,
    OZET_KEYS,
    OZET_REQUIRED_KEYS,
    PLAN_KEYS,
    REPS_MAX,
    REST_MAX,
    SCORE_MAX,
    SCORE_MIN,
    SET_MAX,
    SET_MIN,
    VALID_TIPS,
    WEEKDAYS,
)


def _forbid_unknown(mapping: dict, allowed: frozenset, label: str) -> None:
    extra = set(mapping) - allowed
    if extra:
        raise SchemaInvalidError(f"{label} contains unknown keys")


def _require_mapping(value, label: str) -> dict:
    if not isinstance(value, dict) or isinstance(value, bool):
        raise SchemaInvalidError(f"{label} must be an object")
    return value


def _require_str(value, label: str, max_len: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SchemaInvalidError(f"{label} must be a string")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value) or "\x00" in value:
        raise SchemaInvalidError(f"{label} contains forbidden characters")
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise SchemaInvalidError(f"{label} must be non-empty")
    if len(cleaned) > max_len:
        raise SchemaInvalidError(f"{label} exceeds {max_len} characters")
    return cleaned


def _require_int(value, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaInvalidError(f"{label} must be an integer")
    if value < low or value > high:
        raise SchemaInvalidError(f"{label} is out of bounds")
    return value


def _validate_exercise(raw, *, allow_exercise_id: bool = False) -> dict:
    ex = _require_mapping(raw, "egzersiz")
    _forbid_unknown(
        ex,
        EXERCISE_KEYS_WITH_ID if allow_exercise_id else EXERCISE_KEYS,
        "egzersiz",
    )
    for key in ("isim", "set", "tekrar", "dinlenme"):
        if key not in ex:
            raise SchemaInvalidError(f"egzersiz missing {key}")
    note = ex["not"] if "not" in ex else ""
    validated = {
        "isim": _require_str(ex["isim"], "egzersiz isim", NAME_MAX),
        "set": _require_int(ex["set"], "set", SET_MIN, SET_MAX),
        "tekrar": _require_str(ex["tekrar"], "tekrar", REPS_MAX),
        "dinlenme": _require_str(ex["dinlenme"], "dinlenme", REST_MAX),
        "not": _require_str(note, "not", NOTE_MAX, allow_empty=True),
    }
    # Shape only. Whether this string is real, active and allowed by the
    # accepted equipment context is the catalog's call, made afterwards in
    # exercise_resolution — structure never confers identity. Deliberately
    # passed through unstripped, unlike every display string above: an ID is
    # round-tripped verbatim, so surrounding whitespace means it was edited,
    # and ID_PATTERN must be the gate rather than a lenient normalizer.
    if allow_exercise_id and EXERCISE_ID_KEY in ex:
        raw_id = ex[EXERCISE_ID_KEY]
        if not isinstance(raw_id, str) or not raw_id or len(raw_id) > EXERCISE_ID_MAX:
            raise SchemaInvalidError("exercise_id must be a bounded string")
        validated[EXERCISE_ID_KEY] = raw_id
    return validated


def _validate_day(raw, *, allow_exercise_id: bool = False) -> dict:
    day = _require_mapping(raw, "gün")
    _forbid_unknown(day, DAY_KEYS, "gün")
    for key in DAY_KEYS:
        if key not in day:
            raise SchemaInvalidError(f"gün missing {key}")
    gun = _require_str(day["gun"], "gun", 32)
    if gun not in WEEKDAYS:
        raise SchemaInvalidError("gun alanı kanonik Türkçe hafta günü olmalı")
    tip = _require_str(day["tip"], "tip", 32)
    if tip not in VALID_TIPS:
        raise SchemaInvalidError("tip alanı antrenman/dinlenme/kardiyo olmalı")
    exercises_raw = day["egzersizler"]
    if not isinstance(exercises_raw, list):
        raise SchemaInvalidError("egzersizler liste olmalı")
    exercises = [
        _validate_exercise(item, allow_exercise_id=allow_exercise_id)
        for item in exercises_raw
    ]
    if tip == "antrenman" and len(exercises) < 1:
        raise SchemaInvalidError("antrenman günü en az bir egzersiz içermeli")
    if tip == "kardiyo" and len(exercises) < 1:
        raise SchemaInvalidError("kardiyo günü en az bir egzersiz içermeli")
    if tip == "dinlenme" and exercises:
        raise SchemaInvalidError("dinlenme günü egzersiz içeremez")
    return {
        "gun": gun,
        "tip": tip,
        "odak": _require_str(day["odak"], "odak", FOCUS_MAX),
        "sure_dk": _require_int(day["sure_dk"], "sure_dk", DURATION_MIN, DURATION_MAX),
        "tahmini_kalori": _require_int(
            day["tahmini_kalori"], "tahmini_kalori", CALORIE_MIN, CALORIE_MAX),
        "egzersizler": exercises,
    }


def _validate_ozet(raw) -> dict:
    ozet = _require_mapping(raw, "haftalik_ozet")
    _forbid_unknown(ozet, OZET_KEYS, "haftalik_ozet")
    for key in OZET_REQUIRED_KEYS:
        if key not in ozet:
            raise SchemaInvalidError(f"haftalik_ozet missing {key}")
    scores = {
        key: _require_int(ozet[key], key, SCORE_MIN, SCORE_MAX)
        for key in OZET_REQUIRED_KEYS
    }
    result = dict(scores)
    for key in ("toplam_antrenman_gun", "toplam_tahmini_kalori"):
        if key in ozet:
            result[key] = _require_int(ozet[key], key, 0, 100_000)
    return result


def coerce_plan_document(raw) -> dict:
    """Accept the generate object or the persisted program-array save shape."""
    if isinstance(raw, list):
        return {"program": raw}
    if isinstance(raw, dict) and "program" in raw:
        return raw
    raise SchemaInvalidError("plan JSON object olmalı")


def validate_plan_structure(
    plan,
    *,
    require_ozet: bool = False,
    allow_exercise_id: bool = False,
) -> dict:
    """Validate the canonical 7-day shape of a CLIENT- or provider-authored plan.

    ``allow_exercise_id`` is off by default so provider generation keeps
    rejecting a provider-authored ``exercise_id``: providers are prompted with
    display names only, and an ID they invented is not identity. Save opts in
    — a client legitimately hands back the ID canonicalization gave it — but
    the top-level allow-list is NOT widened for either caller: ``PLAN_KEYS``
    must keep rejecting ``exercise_context`` and every other authority key, so
    a caller cannot declare its own equipment truth. The server attaches that
    block after validation, from the verified token alone.
    """
    document = coerce_plan_document(plan)
    extra_top = set(document) - PLAN_KEYS
    if extra_top:
        raise SchemaInvalidError("plan contains unknown keys")
    program_raw = document.get("program")
    if not isinstance(program_raw, list) or len(program_raw) != 7:
        raise SchemaInvalidError("program tam 7 gün içermeli")
    days = [
        _validate_day(item, allow_exercise_id=allow_exercise_id)
        for item in program_raw
    ]
    seen = [day["gun"] for day in days]
    if len(set(seen)) != 7:
        raise SchemaInvalidError("gun alanları benzersiz olmalı (tekrar eden gün)")
    if set(seen) != set(WEEKDAYS):
        raise SchemaInvalidError("program kanonik 7 hafta gününü içermeli")
    if "haftalik_ozet" in document:
        ozet = _validate_ozet(document["haftalik_ozet"])
    elif require_ozet:
        raise SchemaInvalidError("haftalik_ozet object olmalı")
    else:
        ozet = {
            "yogunluk_skoru": 7,
            "denge_skoru": 7,
            "uygunluk_skoru": 7,
        }
    training_days = sum(1 for day in days if day["tip"] == "antrenman")
    ozet["toplam_antrenman_gun"] = training_days
    ozet["toplam_tahmini_kalori"] = sum(day["tahmini_kalori"] for day in days)
    return {"program": days, "haftalik_ozet": ozet}


def annotate_injuries(plan: dict, injuries: str = "") -> list[dict]:
    """Warn-only injury overlay on an already-canonicalized plan.

    Not catalog authority, not a repair, and not a medical engine. Matching
    uses the canonical server-owned display name written by
    ``canonicalize_plan_exercises``. A raw provider spelling without
    ``exercise_id`` cannot reach the matcher.
    """
    warnings: list[dict] = []
    if not injuries:
        return warnings
    for day in plan["program"]:
        for ex in day["egzersizler"]:
            exercise_id = ex.get(EXERCISE_ID_KEY)
            if not isinstance(exercise_id, str) or not exercise_id:
                raise TypeError(
                    "injury annotation requires canonical exercise identity")
            hit = injury_constraints.find_contraindicated(ex["isim"], injuries)
            if hit:
                warn = f"⚠️ SAKATLIK RİSKİ ({hit}) - güvenli alternatifle değiştir"
                note = ex.get("not") or ""
                ex["not"] = f"{warn}. {note}".strip()[:NOTE_MAX]
                warnings.append({
                    "gun": day.get("gun"), "egzersiz": ex["isim"], "neden": hit,
                })
    return warnings


def validate_generated_plan(plan: dict, preferences, injuries: str = "") -> tuple[dict, list[dict]]:
    """Structural + semantic validation used by generate and tests.

    Injury annotation is not performed here. Warnings require canonical
    exercise identity and are applied after ``canonicalize_plan_exercises``.
    ``injuries`` is accepted for call-site compatibility and ignored.
    """
    from app.services.training_generation.semantic_validator import (
        validate_plan_semantics,
    )

    structured = validate_plan_structure(plan, require_ozet=True)
    validate_plan_semantics(structured, preferences)
    return structured, []
