"""Private owner × catalog exercise authority (TI-00 §9).

No dependency on plans, sessions, execution context, diagnostics or providers.
Clear retains a revisioned tombstone; SQL CAS arbitrates competing writers.
"""
import unicodedata
from datetime import timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import ExerciseNote
from app.services.exercise_catalog import ExerciseResolutionError, resolve_exercise
from app.timeutil import app_now

MAX_REVISION = 999999999


class NoteError(ValueError):
    code = "TRAINING_NOTE_INVALID"
    status = 400
    resolution = "terminal"


class NoteConflict(NoteError):
    code = "TRAINING_NOTE_REVISION_CONFLICT"
    status = 409
    resolution = "reread"


class NoteExhausted(NoteError):
    code = "TRAINING_NOTE_REVISION_EXHAUSTED"
    status = 409


class NoteUnavailable(NoteError):
    code = "TRAINING_NOTE_UNAVAILABLE"
    status = 503
    resolution = "retry"


def _identity(exercise_id):
    try:
        resolve_exercise(exercise_id=exercise_id)
    except ExerciseResolutionError:
        raise NoteError() from None


def parse_text(body):
    if not isinstance(body, dict) or set(body) != {"text"}:
        raise NoteError()
    value = body["text"]
    if value is None:
        return None
    if not isinstance(value, str):
        raise NoteError()
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise NoteError() from None
    # Bounds apply before normalization: oversized input never becomes valid
    # merely because trimming would discard it.
    if len(value) > 500 or size > 2000:
        raise NoteError()
    value = value.replace("\r\n", "\n")
    if any(unicodedata.category(char) == "Cc" and char not in "\n\t"
           for char in value):
        raise NoteError()
    return value.strip() or None


def parse_revision(raw):
    if not isinstance(raw, str):
        raise NoteError()
    value = raw.strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if not value.isascii() or not value.isdecimal() or len(value) > 9:
        raise NoteError()
    return int(value)


def _query(user_id, exercise_id):
    return ExerciseNote.query.filter_by(user_id=user_id, exercise_id=exercise_id)


def _project(exercise_id, row):
    return {"note": {
        "exercise_id": exercise_id,
        "text": row.text if row else None,
        "revision": row.revision if row else 0,
        "updated_at": (row.updated_at.replace(tzinfo=timezone.utc)
                       .isoformat().replace("+00:00", "Z")) if row else None,
    }}


def get_exercise_note(user_id, exercise_id):
    _identity(exercise_id)
    try:
        return _project(exercise_id, _query(user_id, exercise_id).first())
    except SQLAlchemyError:
        db.session.rollback()
        raise NoteUnavailable() from None


def set_exercise_note(user_id, exercise_id, body, revision):
    _identity(exercise_id)
    text = parse_text(body)
    expected = parse_revision(revision)
    now = app_now().astimezone(timezone.utc).replace(tzinfo=None)
    try:
        dialect = db.session.get_bind().dialect.name
        insert = {"postgresql": pg_insert, "sqlite": sqlite_insert}.get(dialect)
        if insert is None:
            raise NoteUnavailable()
        # The unique pair is the arbiter even on simultaneous first creation.
        # Insert and subsequent CAS form one transaction, including clear.
        inserted = db.session.execute(insert(ExerciseNote).values(
            user_id=user_id, exercise_id=exercise_id, text=None, revision=0,
            created_at=now, updated_at=now,
        ).on_conflict_do_nothing(index_elements=["user_id", "exercise_id"])).rowcount
        row = _query(user_id, exercise_id).populate_existing().one()
        if not inserted and row.text == text and expected in (row.revision, row.revision - 1):
            payload = _project(exercise_id, row)
            db.session.commit()
            return payload
        if row.revision != expected:
            raise NoteConflict()
        if expected == MAX_REVISION:
            raise NoteExhausted()
        changed = db.session.execute(sa.update(ExerciseNote).where(
            ExerciseNote.user_id == user_id,
            ExerciseNote.exercise_id == exercise_id,
            ExerciseNote.revision == expected,
        ).values(text=text, revision=expected + 1, updated_at=now),
            execution_options={"synchronize_session": False}).rowcount
        if changed != 1:
            db.session.rollback()
            row = _query(user_id, exercise_id).populate_existing().first()
            if row and row.text == text and expected == row.revision - 1:
                return _project(exercise_id, row)
            raise NoteConflict()
        row = _query(user_id, exercise_id).populate_existing().one()
        payload = _project(exercise_id, row)
        db.session.commit()
        return payload
    except NoteError:
        db.session.rollback()
        raise
    except SQLAlchemyError:
        db.session.rollback()
        raise NoteUnavailable() from None
