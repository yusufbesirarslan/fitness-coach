"""Mobile-authenticated nutrition contract, attached to the `/api/v1` blueprint.

Registered on the existing `mobile_api` blueprint rather than a second one, so
the versioned surface, the `Cache-Control: no-store` policy, the throttling
handler and the error envelope stay single-sourced — and so the approved-route
gate in tests/test_mobile_auth_feature_gate.py keeps covering every `/api/v1`
route there is instead of quietly stopping at the auth ones.

The web nutrition routes are untouched. They are unreachable from the app for a
structural reason, not an oversight: `@require_auth` resolves a Flask-Login
cookie plus a `cognito_sid` session value and answers a browser, and a native
client has neither. Bolting `@require_mobile_auth` onto them would also have
published their ambiguities — `DD.MM` days, no entry identity, naive timestamps,
fabricated zeroes — as the mobile contract. This is an adapter over the same
canonical ledger instead.
"""
from flask import current_app, g, jsonify, request

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import db
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import mobile_nutrition
from app.services import mobile_food_discovery
from app.services.barcode import normalize_barcode
from app.services import meal_idempotency, mobile_log_food
from app.services import mobile_diary_mutation
from app.timeutil import day_key


@bp.get("/nutrition/diary/today")
@require_mobile_auth
def nutrition_diary_today():
    """The canonical diary day for the authenticated mobile user.

    The user comes from the verified Bearer credential (`g.mobile_user`) and
    from nowhere else — there is no account parameter to tamper with, so there
    is no cross-user read to defend against downstream.
    """
    try:
        payload = mobile_nutrition.build_diary_day(
            g.mobile_user.id, current_app.config["SECRET_KEY"])
    except Exception as error:
        # Fail closed with a nutrition-shaped error instead of falling through
        # to the blueprint's auth-flavoured handler: a storage fault here is not
        # an authentication outcome, and a client that read it as one would
        # discard a perfectly good session and send the user back to login.
        # The log line carries a type name and a request id — never a meal,
        # a macro, a target or an account identifier.
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.error(
            "mobile_nutrition event=diary_read_failed error_type=%s "
            "request_id=%s", type(error).__name__, current_request_id())
        return mobile_error(
            "NUTRITION_TEMPORARILY_UNAVAILABLE",
            "Nutrition data is temporarily unavailable.", 503, True)
    return jsonify(payload)


def _discovery_failure(error):
    current_app.logger.error(
        "mobile_nutrition event=food_discovery_failed error_type=%s request_id=%s",
        type(error).__name__, current_request_id())
    return mobile_error(
        "FOOD_PROVIDER_UNAVAILABLE",
        "Food discovery is temporarily unavailable.", 503, True)


@bp.get("/nutrition/foods/search")
@require_mobile_auth
def nutrition_food_search():
    query = request.args.get("q", "").strip()
    if not 2 <= len(query) <= 100:
        return mobile_error("INVALID_FOOD_QUERY", "Invalid food query.", 400, False)
    try:
        return jsonify({"foods": mobile_food_discovery.search(query)})
    except Exception as error:
        return _discovery_failure(error)


@bp.get("/nutrition/foods/fatsecret/<food_id>/servings")
@require_mobile_auth
def nutrition_food_servings(food_id):
    if not food_id or len(food_id) > 128:
        return mobile_error("INVALID_FOOD_ID", "Invalid food identifier.", 400, False)
    try:
        food = mobile_food_discovery.servings(food_id)
    except Exception as error:
        return _discovery_failure(error)
    if not food:
        return mobile_error("FOOD_NOT_FOUND", "Food was not found.", 404, False)
    return jsonify({"food": food})


@bp.get("/nutrition/foods/barcode")
@require_mobile_auth
def nutrition_food_barcode():
    code = request.args.get("code", "").strip()
    if not code.isdigit() or not normalize_barcode(code):
        return mobile_error("INVALID_BARCODE", "Invalid barcode.", 400, False)
    try:
        food = mobile_food_discovery.barcode_lookup(code)
    except Exception as error:
        return _discovery_failure(error)
    if not food:
        return mobile_error("FOOD_NOT_FOUND", "Food was not found.", 404, False)
    return jsonify({"food": food})


@bp.post("/nutrition/logs")
@require_mobile_auth
def nutrition_log_food():
    key = meal_idempotency.read_idempotency_key()
    if key is None:
        return mobile_error(
            "INVALID_IDEMPOTENCY_KEY", "A valid Idempotency-Key is required.",
            400, False)
    try:
        command = mobile_log_food.parse_command(request.get_json(silent=True))
        entry, created = mobile_log_food.log_food(g.mobile_user.id, key, command)
    except mobile_log_food.InvalidLogFoodCommand:
        return mobile_error(
            "INVALID_LOG_FOOD_COMMAND", "Invalid LogFood command.", 400, False)
    except mobile_log_food.IdempotencyConflict:
        return mobile_error(
            "IDEMPOTENCY_CONFLICT",
            "The Idempotency-Key belongs to a different command.", 409, False)
    except mobile_log_food.ProviderFoodNotFound:
        return mobile_error("FOOD_NOT_FOUND", "Food was not found.", 404, False)
    except Exception as error:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.error(
            "mobile_nutrition event=log_food_failed error_type=%s request_id=%s",
            type(error).__name__, current_request_id())
        return mobile_error(
            "NUTRITION_TEMPORARILY_UNAVAILABLE",
            "Nutrition data is temporarily unavailable.", 503, True)
    response = jsonify({
        "meal": mobile_log_food.response_meal(
            entry, current_app.config["SECRET_KEY"], g.mobile_user.id)
    })
    response.status_code = 201 if created else 200
    return response


def _mutation_precondition():
    try:
        return mobile_diary_mutation.parse_if_match(
            request.headers.get("If-Match")), None
    except mobile_diary_mutation.MissingPrecondition:
        return None, mobile_error(
            "DIARY_PRECONDITION_REQUIRED",
            "An If-Match precondition is required.", 428, False)
    except mobile_diary_mutation.InvalidPrecondition:
        return None, mobile_error(
            "INVALID_DIARY_PRECONDITION",
            "The If-Match precondition is invalid.", 400, False)


def _mutation_error(code, message, status):
    try:
        db.session.rollback()
    except Exception:
        pass
    return mobile_error(code, message, status, False)


@bp.patch("/nutrition/logs/<entry_token>")
@require_mobile_auth
def nutrition_set_log_slot(entry_token):
    revision, error_response = _mutation_precondition()
    if error_response is not None:
        return error_response
    try:
        command = mobile_diary_mutation.parse_mutation_command(
            request.get_json(silent=True))
        meal = mobile_diary_mutation.set_slot(
            g.mobile_user.id,
            day_key(),
            entry_token,
            revision,
            command,
            current_app.config["SECRET_KEY"],
        )
    except mobile_diary_mutation.InvalidDiaryMutation:
        return _mutation_error(
            "INVALID_DIARY_MUTATION", "Invalid diary mutation.", 400)
    except mobile_diary_mutation.EntryNotFound:
        return _mutation_error(
            "DIARY_ENTRY_NOT_FOUND", "Diary entry was not found.", 404)
    except mobile_diary_mutation.StaleDiaryEntry:
        return _mutation_error(
            "STALE_DIARY_ENTRY", "Diary entry has changed.", 412)
    except Exception as error:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.error(
            "mobile_nutrition event=diary_mutation_failed error_type=%s "
            "request_id=%s", type(error).__name__, current_request_id())
        return mobile_error(
            "NUTRITION_TEMPORARILY_UNAVAILABLE",
            "Nutrition data is temporarily unavailable.", 503, True)
    return jsonify({"meal": meal})


@bp.delete("/nutrition/logs/<entry_token>")
@require_mobile_auth
def nutrition_delete_log(entry_token):
    revision, error_response = _mutation_precondition()
    if error_response is not None:
        return error_response
    if request.get_data(cache=True):
        return _mutation_error(
            "INVALID_DIARY_MUTATION", "Invalid diary mutation.", 400)
    try:
        mobile_diary_mutation.delete_entry(
            g.mobile_user.id,
            day_key(),
            entry_token,
            revision,
            current_app.config["SECRET_KEY"],
        )
    except mobile_diary_mutation.EntryNotFound:
        return _mutation_error(
            "DIARY_ENTRY_NOT_FOUND", "Diary entry was not found.", 404)
    except mobile_diary_mutation.StaleDiaryEntry:
        return _mutation_error(
            "STALE_DIARY_ENTRY", "Diary entry has changed.", 412)
    except Exception as error:
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.error(
            "mobile_nutrition event=diary_delete_failed error_type=%s "
            "request_id=%s", type(error).__name__, current_request_id())
        return mobile_error(
            "NUTRITION_TEMPORARILY_UNAVAILABLE",
            "Nutrition data is temporarily unavailable.", 503, True)
    return "", 204
