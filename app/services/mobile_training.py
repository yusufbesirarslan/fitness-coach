"""Canonical read-only projections for the native Training surface."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from datetime import datetime, timezone

from app.services.exercise_catalog import resolve_exercise
from app.services.today_facts import get_active_plan
from app.services.training_generation.capability import (
    SUPPORTED_EQUIPMENT_BY_STYLE,
    STATUS_CONFLICTING,
    STATUS_UNSUPPORTED,
)
from app.services.training_generation.preference_contract import (
    BODY_FOCUS_VALUES,
    CARDIO_DAYS,
    CARDIO_DURATIONS,
    CARDIO_INTENSITIES,
    CARDIO_TYPES,
    DAY_COUNTS,
    DURATIONS,
    EQUIPMENT_VALUES,
    FOCUS_VALUES,
    STYLE_RULE_KEYS,
)
from app.services.training_generation.plan_schema import SCORE_MAX, SCORE_MIN
from app.services.training_generation.response_validator import (
    WEEKDAYS,
    validate_plan_structure,
)
from app.services.workout_state import WorkoutStateReadError, resolve_workout_state
from app.services.workout_state.models import SCHEDULE_SCHEDULED
from app.services.workout_state.snapshot import coherent_read_snapshot
from app.timeutil import app_today


CONTRACT_VERSION = 1
MAX_EXERCISES_PER_DAY = 32
_PLAN_DATA_MAX = 256_000
_REST_SECONDS_MAX = 86_400
_REF_BYTES = 18
_REF_INFO = b"axisai/mobile-training/workout-ref/v1"
WORKOUT_REF_RE = re.compile(r"^[A-Za-z0-9_-]{24}$")
_REST_RE = re.compile(r"^(0|[1-9][0-9]*) (sn|dk)$")
_KIND = {"antrenman": "training", "kardiyo": "cardio", "dinlenme": "rest"}


class PlanUnprojectable(ValueError):
    """A persisted current plan exists but cannot satisfy the native contract."""


class TrainingReadUnavailable(RuntimeError):
    """A canonical persistence authority could not be read."""


class WorkoutNotFound(LookupError):
    """A reference is malformed or cannot name a published workout."""


class WorkoutStale(LookupError):
    """A well-formed reference does not match the owner's current revision."""


def preference_contract() -> dict:
    """Return deterministic rendering metadata from the canonical vocabularies."""
    return {
        "contract_version": CONTRACT_VERSION,
        "fields": {
            "gun_sayisi": _integer_field(3, DAY_COUNTS),
            "ekipman": _token_field("spor_salonu", EQUIPMENT_VALUES),
            "odak": _token_field("tum_vucut", BODY_FOCUS_VALUES),
            "sure": _integer_field(45, DURATIONS),
            "kardiyo_tipi": _token_field("yok", CARDIO_TYPES),
            "kardiyo_gun": _integer_field(0, CARDIO_DAYS),
            "kardiyo_sure": _integer_field(20, CARDIO_DURATIONS),
            "kardiyo_yogunluk": _token_field("orta", CARDIO_INTENSITIES),
            "antrenman_tarzi": _token_field("genel", STYLE_RULE_KEYS),
            "odak_hedef": _token_field("genel", FOCUS_VALUES),
            "injuries": {"type": "string", "default": ""},
        },
        "capability_constraints": [
            {
                "status": STATUS_UNSUPPORTED,
                "reason": "CROSSFIT_SCHEMA_UNSUPPORTED",
                "when": {"antrenman_tarzi": _styles_for_rule("crossfit")},
            },
            {
                "status": STATUS_UNSUPPORTED,
                "reason": "POWERLIFTING_REQUIRES_GYM_EQUIPMENT",
                "when": {
                    "antrenman_tarzi": _styles_for_rule("powerlifting"),
                    "ekipman": sorted(
                        EQUIPMENT_VALUES
                        - SUPPORTED_EQUIPMENT_BY_STYLE["powerlifting"]
                    ),
                },
            },
            {
                "status": STATUS_CONFLICTING,
                "reason": "CARDIO_DAYS_WITHOUT_TYPE",
                "when": {"kardiyo_tipi": ["yok"], "kardiyo_gun": [1, 2, 3, 4, 5, 6]},
            },
            {
                "status": STATUS_CONFLICTING,
                "reason": "WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS",
                "when": {"rule": "gun_sayisi + effective_kardiyo_gun > 7"},
            },
        ],
    }


def build_current_plan(user_id: int, secret, *, sessions_enabled: bool = False) -> dict:
    """Project the authenticated owner's current plan inside one read snapshot."""
    try:
        with coherent_read_snapshot():
            plan = get_active_plan(user_id)
            if plan is None:
                return {"plan": None}
            day = app_today()
            projected = _project_plan(plan, user_id, secret)
            snapshot = resolve_workout_state(
                user_id,
                today=day,
                plan=plan,
                sessions_enabled=sessions_enabled,
                strict_reads=True,
            )
            current_ref = None
            if snapshot.schedule_state == SCHEDULE_SCHEDULED:
                candidate = projected["days"][day.weekday()]
                current_ref = candidate["workout_ref"]
            projected["current_workout_ref"] = current_ref
            return {"plan": projected}
    except PlanUnprojectable:
        raise
    except WorkoutStateReadError as error:
        raise TrainingReadUnavailable("canonical workout state unavailable") from error
    except Exception as error:  # query/snapshot failures are not empty product state
        raise TrainingReadUnavailable("canonical Training read unavailable") from error


def build_workout(user_id: int, secret, reference: str) -> dict:
    """Resolve one opaque reference against only the owner's current plan."""
    if not isinstance(reference, str) or not WORKOUT_REF_RE.fullmatch(reference):
        raise WorkoutNotFound("workout reference is not usable")
    try:
        with coherent_read_snapshot():
            plan = get_active_plan(user_id)
            if plan is None:
                raise WorkoutStale("workout reference is no longer current")
            projected = _project_plan(plan, user_id, secret)
            matched = None
            for day in projected["days"]:
                candidate = day["workout_ref"]
                if candidate is not None and hmac.compare_digest(candidate, reference):
                    matched = day
            if matched is None:
                raise WorkoutStale("workout reference is no longer current")
            return {
                "workout": {
                    "plan_lineage": projected["plan_lineage"],
                    "mutation_version": projected["mutation_version"],
                    "workout_ref": matched["workout_ref"],
                    "slot": matched["slot"],
                    "weekday": matched["weekday"],
                    "kind": matched["kind"],
                    "focus": matched["focus"],
                    "duration_minutes": matched["duration_minutes"],
                    "estimated_calories": matched["estimated_calories"],
                    "exercises": matched["exercises"],
                }
            }
    except (WorkoutStale, PlanUnprojectable):
        raise
    except Exception as error:
        raise TrainingReadUnavailable("canonical Training read unavailable") from error


def workout_ref(secret, user_id: int, lineage: str, mutation_version: int, slot: int) -> str:
    """Mint a deterministic opaque reference bound to owner, revision, and slot."""
    key_material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    subkey = hmac.new(key_material, _REF_INFO, hashlib.sha256).digest()
    message = (
        f"{int(user_id)}\x00{lineage}\x00{int(mutation_version)}\x00{int(slot)}"
    ).encode("utf-8")
    digest = hmac.new(subkey, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:_REF_BYTES]).decode("ascii")


def _integer_field(default, choices):
    return {"type": "integer", "default": default, "choices": sorted(choices)}


def _token_field(default, choices):
    return {"type": "token", "default": default, "choices": sorted(choices)}


def _styles_for_rule(rule):
    return sorted(token for token, target in STYLE_RULE_KEYS.items() if target == rule)


def _project_plan(plan, user_id, secret) -> dict:
    try:
        lineage = _lineage(plan.lineage_id)
        version = _version(plan.mutation_version)
        created_at = _utc_iso(plan.created_at)
        score = _score(plan.score)
        raw = plan.plan_data
        if not isinstance(raw, str) or len(raw) > _PLAN_DATA_MAX:
            raise ValueError("invalid plan document")
        document = json.loads(raw)
        validated = validate_plan_structure(document, allow_exercise_id=True)
        by_weekday = {day["gun"]: day for day in validated["program"]}
        days = [
            _project_day(
                by_weekday[weekday], slot, user_id, secret, lineage, version
            )
            for slot, weekday in enumerate(WEEKDAYS)
        ]
        return {
            "plan_lineage": lineage,
            "mutation_version": version,
            "created_at": created_at,
            "score": score,
            "current_workout_ref": None,
            "days": days,
        }
    except PlanUnprojectable:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        raise PlanUnprojectable("persisted plan is not projectable") from error


def _project_day(day, slot, user_id, secret, lineage, version):
    if len(day["egzersizler"]) > MAX_EXERCISES_PER_DAY:
        raise PlanUnprojectable("day exceeds the native exercise bound")
    kind = _KIND[day["tip"]]
    reference = None
    if kind != "rest":
        reference = workout_ref(secret, user_id, lineage, version, slot)
    return {
        "slot": slot,
        "weekday": day["gun"],
        "kind": kind,
        "focus": day["odak"],
        "duration_minutes": day["sure_dk"],
        "estimated_calories": day["tahmini_kalori"],
        "workout_ref": reference,
        "exercises": [_project_exercise(item) for item in day["egzersizler"]],
    }


def _project_exercise(item):
    exercise_id = item.get("exercise_id")
    if not isinstance(exercise_id, str):
        raise PlanUnprojectable("canonical exercise identity is missing")
    definition = resolve_exercise(exercise_id=exercise_id)
    return {
        "exercise_id": definition.exercise_id,
        "display_name": definition.canonical_name,
        "sets": item["set"],
        "reps": item["tekrar"],
        "rest": _rest(item["dinlenme"]),
        "notes": item["not"],
    }


def _rest(display):
    seconds = None
    if display == "0":
        seconds = 0
    else:
        match = _REST_RE.fullmatch(display)
        if match:
            value = int(match.group(1))
            candidate = value if match.group(2) == "sn" else value * 60
            if candidate <= _REST_SECONDS_MAX:
                seconds = candidate
    return {"display_text": display, "seconds": seconds}


def _lineage(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or any(ord(char) < 33 or ord(char) > 126 for char in value)
    ):
        raise PlanUnprojectable("invalid plan lineage")
    return value


def _version(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PlanUnprojectable("invalid mutation version")
    return value


def _score(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanUnprojectable("invalid score")
    numeric = float(value)
    if not math.isfinite(numeric) or not SCORE_MIN <= numeric <= SCORE_MAX:
        raise PlanUnprojectable("invalid score")
    return numeric


def _utc_iso(value):
    if not isinstance(value, datetime):
        raise PlanUnprojectable("invalid creation time")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")
