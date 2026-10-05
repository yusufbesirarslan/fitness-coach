"""TI-00 persistent-note transport; native bearer owner only."""
from functools import wraps

import sqlalchemy as sa
from flask import current_app, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import db
from app.mobile_auth_middleware import require_mobile_auth
from app.services import exercise_notes as notes


def _ready(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not (current_app.config.get("FITX_TRAINING_EXECUTION_CONTEXT_ENABLED")
                and current_app.config.get("FITX_WORKOUT_SESSIONS_ENABLED")):
            return mobile_error("TRAINING_SESSION_NOT_FOUND", "Not found.", 404, False)
        try:
            inspector = sa.inspect(db.engine)
            if not (inspector.has_table("exercise_note")
                    and inspector.has_table("workout_session")):
                return mobile_error("TRAINING_SESSION_NOT_FOUND", "Not found.", 404, False)
            columns = {c["name"] for c in inspector.get_columns("exercise_note")}
            unique = inspector.get_unique_constraints("exercise_note")
            foreign = inspector.get_foreign_keys("exercise_note")
            checks = {c["name"] for c in inspector.get_check_constraints("exercise_note")}
            if (columns != {"id", "user_id", "exercise_id", "text", "revision",
                            "created_at", "updated_at"}
                    or not any(c["column_names"] == ["user_id", "exercise_id"] for c in unique)
                    or not any(c["constrained_columns"] == ["user_id"]
                               and c["referred_table"] == "user"
                               and c.get("options", {}).get("ondelete") == "CASCADE" for c in foreign)
                    or not {"ck_exercise_note_revision", "ck_exercise_note_text_length"} <= checks):
                return mobile_error("TRAINING_SESSION_NOT_FOUND", "Not found.", 404, False)
        except SQLAlchemyError:
            db.session.rollback()
            return _failure(notes.NoteUnavailable())
        return view(*args, **kwargs)
    return wrapper


def _failure(error):
    response = mobile_error(error.code, "The exercise note request could not be completed.",
                            error.status, error.status == 503,
                            retry_after=15 if error.status == 503 else None)
    response.headers["Session-Resolution"] = error.resolution
    return response


@bp.route("/training/exercises/<exercise_id>/note", methods=["GET", "PUT"])
@require_mobile_auth
@_ready
def exercise_note(exercise_id):
    try:
        if request.method == "GET":
            payload = notes.get_exercise_note(g.mobile_user.id, exercise_id)
        else:
            payload = notes.set_exercise_note(
                g.mobile_user.id, exercise_id, request.get_json(silent=True),
                request.headers.get("If-Match"))
        return jsonify(payload)
    except notes.NoteError as error:
        return _failure(error)
