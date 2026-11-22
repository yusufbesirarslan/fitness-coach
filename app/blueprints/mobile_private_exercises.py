"""Additive owner-private picker resource for the native Training Editor."""

import unicodedata

from flask import g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import db
from app.mobile_auth_middleware import require_mobile_auth
from app.services import private_exercises
from app.services.exercise_catalog import normalize_exercise_lookup


@bp.get("/training/private-exercises")
@require_mobile_auth
def browse_private_exercises():
    query = request.args.get("q", "")
    cursor = request.args.get("after")
    if (set(request.args) - {"q", "after"}
            or any(len(request.args.getlist(key)) > 1 for key in ("q", "after"))
            or len(query) > 80
            or any(unicodedata.category(char)[0] == "C" for char in query)
            or (cursor is not None and not private_exercises.is_private_id(cursor))):
        return _invalid()
    try:
        items, next_cursor = private_exercises.browse(
            g.mobile_user.id, normalize_exercise_lookup(query), cursor)
    except SQLAlchemyError:
        return _unavailable()
    return jsonify(contract_version=private_exercises.CONTRACT_VERSION,
                   exercises=items, next_cursor=next_cursor)


@bp.post("/training/private-exercises")
@require_mobile_auth
def register_private_exercise():
    request.max_content_length = 4096
    try:
        item, created = private_exercises.register(
            g.mobile_user.id, request.get_json(silent=True))
    except (private_exercises.InvalidRegistration, RecursionError):
        return _invalid()
    except private_exercises.RegistrationConflict:
        db.session.rollback()
        return mobile_error("TRAINING_PRIVATE_EXERCISE_CONFLICT",
                            "This name already has different metadata.", 409, False)
    except RequestEntityTooLarge:
        return _invalid(status=413)
    except SQLAlchemyError:
        return _unavailable()
    return jsonify(contract_version=private_exercises.CONTRACT_VERSION,
                   exercise=item), 201 if created else 200


def _invalid(status=400):
    return mobile_error("TRAINING_PRIVATE_EXERCISE_INVALID",
                        "Invalid private exercise request.", status, False)


def _unavailable():
    db.session.rollback()
    return mobile_error("TRAINING_PRIVATE_EXERCISE_UNAVAILABLE",
                        "Private exercises are temporarily unavailable.", 503, True)
