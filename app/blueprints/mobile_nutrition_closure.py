"""NUTR-PR7 — the native Nutrition closure routes, on the existing ``mobile_api``.

Thin Bearer transports over the canonical authorities (see
``app/services/nutrition_native``). On this blueprint, not a second one, so the
``/api/v1`` prefix, ``Cache-Control: no-store``, the ADR 0001 envelope, the 413/
429 handlers, the ``MOBILE_AUTH_ENABLED`` gate and the exact approved-route
allow-list (tests/test_mobile_auth_feature_gate.py) all keep covering them.

The owner is ``g.mobile_user`` and nothing else: no route reads a user id,
e-mail, subject, plan id or database id from the request. Every failure is a
closed, typed error; an unexpected fault is caught HERE and answered as the
capability's own retryable 503 — never the blueprint catch-all's
``AUTH_TEMPORARILY_UNAVAILABLE``, which would make a client drop a good session.
Logs carry an event name, an exception TYPE and the request id only.

Contract: docs/NUTRITION_VNEXT_PR7.md
"""
from flask import current_app, g, jsonify, request
from flask_limiter.errors import RateLimitExceeded

from app.blueprints.mobile_api import bp, mobile_error
from app.config import AI_RATELIMIT, BEDROCK_RATELIMIT
from app.extensions import db, limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import ai, meal_idempotency
from app.services.ai_gate import mobile_ai_concurrency_gate
from app.services.nutrition_day_view import (
    build_nutrition_day_view,
    nutrition_day_view_payload,
)
from app.services.nutrition_native import (
    errors,
    history,
    hydration,
    plan,
    preconditions,
    supplements,
)


def _secret():
    return current_app.config["SECRET_KEY"]


def _render(error):
    return mobile_error(error.code, error.message, error.status, error.retryable)


def _failure(event, error, unavailable):
    try:
        db.session.rollback()
    except Exception:
        pass
    current_app.logger.error(
        "mobile_nutrition event=%s error_type=%s request_id=%s",
        event, type(error).__name__, current_request_id())
    return _render(unavailable)


def _run(event, unavailable, operation):
    try:
        return operation()
    except errors.NativeNutritionError as error:
        try:
            db.session.rollback()
        except Exception:
            pass
        return _render(error)
    except Exception as error:
        return _failure(event, error, unavailable)


def _if_match():
    try:
        return preconditions.parse_if_match(request.headers.get("If-Match"))
    except preconditions.MissingPrecondition:
        raise errors.PreconditionRequired from None
    except preconditions.InvalidPrecondition:
        raise errors.InvalidPrecondition from None


def _json_body(error_class):
    data = request.get_json(silent=True)
    if data is None:
        raise error_class
    return data


def _no_body_or_empty_object(error_class):
    raw = request.get_data(cache=True)
    if not raw:
        return
    data = request.get_json(silent=True)
    if data != {}:
        raise error_class


def _owner_key():
    return str(g.mobile_user.id)


# ── A. day view ────────────────────────────────────────────────────────────


@bp.get("/nutrition/day-view")
@require_mobile_auth
def nutrition_day_view_native():
    """The PR6 day view, verbatim: same builder, same payload function."""
    return _run(
        "day_view_failed", errors.DayViewUnavailable,
        lambda: jsonify(nutrition_day_view_payload(
            build_nutrition_day_view(g.mobile_user.id))))


# ── B–D. nutrition plan ────────────────────────────────────────────────────


@bp.get("/nutrition/plan")
@require_mobile_auth
def nutrition_plan_read():
    return _run("plan_read_failed", errors.PlanUnavailable,
                lambda: jsonify(plan.read_plan(g.mobile_user.id, _secret())))


def _generation_chat(**kwargs):
    # Resolved at call time: the shared heavy path, exactly as the web route.
    return ai._heavy_chat(**kwargs)


@bp.post("/nutrition/plan/generate")
@require_mobile_auth
@mobile_ai_concurrency_gate(
    "NUTRITION_PLAN_GENERATION_BUSY",
    "Plan generation is busy. Try again shortly.")
def nutrition_plan_generate_native():
    def operation():
        command = plan.parse_generation_request(
            _json_body(errors.InvalidGenerationRequest))
        try:
            with limiter.limit(AI_RATELIMIT, key_func=_owner_key):
                with limiter.limit(BEDROCK_RATELIMIT, key_func=_owner_key):
                    pass
        except RateLimitExceeded as error:
            return mobile_error(
                errors.GenerationRateLimited.code,
                errors.GenerationRateLimited.message, 429, True,
                retry_after=error.limit.limit.get_expiry())
        return jsonify(plan.generate_proposals(
            g.mobile_user, _secret(), command, _generation_chat,
            current_app.config.get("AI_PLAN_QUOTA_ENABLED", True),
            logger=current_app.logger))
    return _run("plan_generation_failed", errors.GenerationFailed, operation)


@bp.put("/nutrition/plan")
@require_mobile_auth
def nutrition_plan_save_native():
    def operation():
        try:
            precondition = preconditions.parse_create_or_match(
                request.headers.get("If-Match"),
                request.headers.get("If-None-Match"))
        except preconditions.MissingPrecondition:
            raise errors.PreconditionRequired from None
        except preconditions.InvalidPrecondition:
            raise errors.InvalidPrecondition from None
        body = _json_body(errors.InvalidPlan)
        return jsonify(plan.save_plan(
            g.mobile_user.id, _secret(), precondition, body))
    return _run("plan_save_failed", errors.PlanUnavailable, operation)


# ── L. planned meal → consumed food ────────────────────────────────────────


@bp.post("/nutrition/plan/meals/<planned_meal_id>/log")
@require_mobile_auth
def nutrition_planned_meal_log(planned_meal_id):
    def operation():
        revision = _if_match()
        key = meal_idempotency.read_idempotency_key()
        if key is None:
            raise errors.IdempotencyKeyRequired
        _no_body_or_empty_object(errors.InvalidPlannedMealCommand)
        meal, created = plan.log_planned_meal(
            g.mobile_user.id, _secret(), revision, planned_meal_id, key)
        response = jsonify({"meal": meal})
        response.status_code = 201 if created else 200
        return response
    return _run("planned_meal_log_failed", errors.NativeNutritionError, operation)


# ── E–F. hydration ─────────────────────────────────────────────────────────


@bp.get("/nutrition/hydration")
@require_mobile_auth
def nutrition_hydration_read():
    return _run("hydration_read_failed", errors.HydrationUnavailable,
                lambda: jsonify(hydration.read_today(g.mobile_user.id, _secret())))


@bp.put("/nutrition/hydration")
@require_mobile_auth
def nutrition_hydration_set():
    def operation():
        revision = _if_match()
        amount = hydration.parse_command(_json_body(errors.InvalidHydration))
        return jsonify(hydration.set_today(
            g.mobile_user.id, _secret(), revision, amount))
    return _run("hydration_write_failed", errors.HydrationUnavailable, operation)


# ── G. history ─────────────────────────────────────────────────────────────


@bp.get("/nutrition/history")
@require_mobile_auth
def nutrition_history_page():
    def operation():
        limit = history.parse_limit(request.args.get("limit"))
        return jsonify(history.read_page(
            g.mobile_user.id, _secret(), request.args.get("cursor"), limit))
    return _run("history_read_failed", errors.HistoryUnavailable, operation)


# ── H–K. supplements ───────────────────────────────────────────────────────


@bp.get("/nutrition/supplements")
@require_mobile_auth
def nutrition_supplements_list():
    return _run("supplements_read_failed", errors.SupplementsUnavailable,
                lambda: jsonify(supplements.read_cabinet(
                    g.mobile_user.id, _secret())))


@bp.post("/nutrition/supplements")
@require_mobile_auth
def nutrition_supplements_create():
    def operation():
        revision = _if_match()
        body = _json_body(errors.InvalidSupplement)
        response = jsonify(supplements.create(
            g.mobile_user.id, _secret(), revision, body))
        response.status_code = 201
        return response
    return _run("supplement_create_failed", errors.SupplementsUnavailable,
                operation)


@bp.patch("/nutrition/supplements/<supplement_token>")
@require_mobile_auth
def nutrition_supplements_update(supplement_token):
    def operation():
        revision = _if_match()
        body = _json_body(errors.InvalidSupplement)
        return jsonify(supplements.update(
            g.mobile_user.id, _secret(), supplement_token, revision, body))
    return _run("supplement_update_failed", errors.SupplementsUnavailable,
                operation)


@bp.delete("/nutrition/supplements/<supplement_token>")
@require_mobile_auth
def nutrition_supplements_delete(supplement_token):
    def operation():
        revision = _if_match()
        if request.get_data(cache=True):
            raise errors.InvalidSupplement
        supplements.delete(g.mobile_user.id, _secret(), supplement_token, revision)
        return "", 204
    return _run("supplement_delete_failed", errors.SupplementsUnavailable,
                operation)
