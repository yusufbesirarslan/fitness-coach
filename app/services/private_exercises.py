"""Owner-scoped registration and ID resolution for manually edited plans."""

import re
import unicodedata
from uuid import uuid4

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.extensions import db
from app.models import PrivateExercise
from app.services import exercise_catalog
from app.services.exercise_catalog import (
    CARDIO_EQUIPMENT,
    CARDIO_MOVEMENT,
    EQUIPMENT_VOCABULARY,
    MOVEMENT_VOCABULARY,
    REGION_VOCABULARY,
    PRIVATE_ID_PREFIX,
    ExerciseDefinition,
    normalize_exercise_lookup,
    resolve_exercise,
    resolve_historical_exercise,
    with_owner_definitions,
)
from app.services.training_generation.plan_schema import NAME_MAX


PRIVATE_ID = re.compile(PRIVATE_ID_PREFIX + r"[0-9a-f]{32}")
PAGE_SIZE = 50


class InvalidRegistration(ValueError):
    pass


class RegistrationConflict(ValueError):
    pass


def is_private_id(value):
    return isinstance(value, str) and PRIVATE_ID.fullmatch(value) is not None


def _label_char(char):
    return unicodedata.category(char)[0] in "LMN" or char in " -'’().+/‐‑‒–—―−"


def _registration(body):
    if not isinstance(body, dict) or set(body) != {
        "display_name", "equipment", "movement", "primary_region",
    }:
        raise InvalidRegistration()
    name = body["display_name"]
    if not isinstance(name, str) or not 1 <= len(name) <= NAME_MAX:
        raise InvalidRegistration()
    if not all(_label_char(char) for char in name) or not any(char.isalnum() for char in name):
        raise InvalidRegistration()
    name = " ".join(name.split())
    key = normalize_exercise_lookup(name)
    if len(key) > NAME_MAX * 4:
        raise InvalidRegistration()
    equipment = body["equipment"]
    if (not isinstance(equipment, list) or not 1 <= len(equipment) <= len(EQUIPMENT_VOCABULARY)
            or not all(isinstance(item, str) and item in EQUIPMENT_VOCABULARY for item in equipment)
            or len(set(equipment)) != len(equipment)):
        raise InvalidRegistration()
    movement, region = body["movement"], body["primary_region"]
    if (not isinstance(movement, str) or movement not in MOVEMENT_VOCABULARY
            or not isinstance(region, str) or region not in REGION_VOCABULARY):
        raise InvalidRegistration()
    if movement == CARDIO_MOVEMENT and (
        region != "cardio" or not set(equipment) <= CARDIO_EQUIPMENT["karisik"]
    ):
        raise InvalidRegistration()
    if movement != CARDIO_MOVEMENT and region == "cardio":
        raise InvalidRegistration()
    return name, key, sorted(equipment), movement, region


def register(user_id, body):
    """Insert or replay an immutable registration. The unique key arbitrates races."""
    name, key, equipment, movement, region = _registration(body)
    insert = sqlite_insert if db.session.get_bind().dialect.name == "sqlite" else pg_insert
    inserted = db.session.execute(insert(PrivateExercise).values(
        exercise_id=PRIVATE_ID_PREFIX + uuid4().hex, user_id=user_id, name_key=key,
        display_name=name, equipment=equipment, movement=movement, primary_region=region,
    ).on_conflict_do_nothing(index_elements=["user_id", "name_key"])).rowcount == 1
    row = PrivateExercise.query.filter_by(user_id=user_id, name_key=key).one()
    if (row.equipment, row.movement, row.primary_region) != (equipment, movement, region):
        raise RegistrationConflict()
    result = project(row)
    db.session.commit()
    return result, inserted


def project(row):
    return {
        "exercise_id": row.exercise_id, "display_name": row.display_name,
        "equipment": row.equipment, "movement": row.movement,
        "primary_region": row.primary_region,
    }


def browse(user_id, query, after):
    rows = PrivateExercise.query.filter_by(user_id=user_id)
    if query:
        rows = rows.filter(PrivateExercise.name_key.contains(query, autoescape=True))
    if after is not None:
        rows = rows.filter(PrivateExercise.exercise_id > after)
    items = rows.order_by(PrivateExercise.exercise_id).limit(PAGE_SIZE + 1).all()
    cursor = items[PAGE_SIZE - 1].exercise_id if len(items) > PAGE_SIZE else None
    return [project(row) for row in items[:PAGE_SIZE]], cursor


def owner_definitions(user_id, exercise_ids, *, session=None):
    ids = {value for value in exercise_ids if is_private_id(value)}
    if user_id is None or not ids:
        return {}
    session = db.session if session is None else session
    rows = session.query(PrivateExercise).filter(
        PrivateExercise.user_id == user_id, PrivateExercise.exercise_id.in_(ids))
    return {
        row.exercise_id: ExerciseDefinition(
            row.exercise_id, row.display_name, (), frozenset(row.equipment),
            row.movement, row.primary_region, True,
        )
        for row in rows
    }


def owned_catalog(user_id, exercise_ids):
    return with_owner_definitions(
        exercise_catalog.load_exercise_catalog(), owner_definitions(user_id, exercise_ids))


def resolve_owned_exercise(user_id, exercise_id):
    return resolve_exercise(exercise_id=exercise_id, catalog=owned_catalog(user_id, (exercise_id,)))


def resolve_owned_historical_exercise(user_id, exercise_id):
    return resolve_historical_exercise(exercise_id, catalog=owned_catalog(user_id, (exercise_id,)))


def program_exercise_ids(program):
    for day in program:
        exercises = day.get("egzersizler") if isinstance(day, dict) else None
        if isinstance(exercises, list):
            for exercise in exercises:
                if isinstance(exercise, dict):
                    yield exercise.get("exercise_id")
