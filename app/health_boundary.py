"""Fail-closed request boundary BEFORE bodies, hooks, views and D1 admission.

The small ordinary-data allowlist is intentional. New authenticated product
routes require health consent unless separately reviewed into this list.
"""
from flask import current_app, g, jsonify, request
from flask_login import current_user

from app.services import health_consent as consent

MARKER = "axisai.first_party_health_scope"
SAFE_ENDPOINTS = frozenset({
    "mobile_api.me", "mobile_api.put_account_language", "mobile_api.delete_account",
    "mobile_api.account_eligibility", "mobile_api.mobile_consent",
    "mobile_api.health_consent", "health_consent.web_consent",
    "account_eligibility.web_existing", "account_eligibility.web_preview",
    "mobile_api.subscription_purchase_context", "mobile_api.subscription_verify",
    "mobile_api.subscription_entitlement", "mobile_api.subscription_apple_notifications",
    "mobile_api.ai_allowances_read", "mobile_api.ai_action_receipt",
    "ai_consent.web_consent", "health", "pages.referral_data",
})


def needs_health():
    endpoint = request.endpoint or ""
    if endpoint == "mobile_api.today_guidance":
        from app.blueprints.mobile_today_guidance import _guidance_enabled
        if not _guidance_enabled():
            return False
    if endpoint in {"mobile_api.exercise_history_capabilities", "mobile_api.read_exercise_history"}:
        from app.blueprints.mobile_exercise_history import _ready
        if not _ready():
            return False
    return bool(endpoint and endpoint not in SAFE_ENDPOINTS
                and not endpoint.startswith(("auth.", "static", "account_eligibility."))
                and endpoint not in {"mobile_api.login", "mobile_api.refresh", "mobile_api.logout",
                    "mobile_api.register", "mobile_api.eligibility_preview", "mobile_api.verify",
                    "mobile_api.verify_resend", "mobile_api.password_forgot", "mobile_api.password_reset"})


def failure(error):
    response = jsonify(error={"code": error.code,
        "message": "Review first-party health consent before using health features.",
        "retryable": error.status == 503,
        "request_id": getattr(g, "request_id", None)})
    response.status_code = error.status
    response.headers["Cache-Control"] = "no-store"
    return response


def enforce_request(user):
    if not needs_health() or MARKER in request.environ:
        return
    scope = consent.processing(user.id)
    scope.__enter__()
    request.environ[MARKER] = scope


def permit_account_projection(owner):
    """Account controls remain usable; only a live held grant exposes health."""
    if MARKER in request.environ:
        return True
    scope = consent.processing(owner)
    try:
        scope.__enter__()
    except consent.HealthConsentDenied:
        return False
    request.environ[MARKER] = scope
    return True


def install(app):
    app.register_error_handler(consent.HealthConsentDenied, failure)

    @app.before_request
    def before_health_processing():
        if not needs_health():
            return
        # Reuse the real authentication paths, not an id from a body or cookie
        # on the Bearer-only API. Decorators remain in place as defence in depth.
        if request.blueprint == "mobile_api":
            from app.mobile_auth_middleware import require_mobile_auth
            return require_mobile_auth(lambda: None)()
        if current_user.is_authenticated:
            from app.auth_middleware import require_auth
            return require_auth(lambda: None)()

    @app.after_request
    def no_health_cache(response):
        if MARKER in request.environ:
            response.cache_control.no_store = True
        return response


class HealthProcessingBoundary:
    """Keep the processing/withdrawal lock through WSGI iterable consumption.

    This orders server processing/publication, not remote device delivery.
    Connection/iteration errors always release the transaction lock.
    """
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        context = consent.fresh_request_context()
        try:
            iterable = context.run(self.app, environ, start_response)
        except BaseException:
            scope = environ.pop(MARKER, None)
            if scope is not None:
                context.run(scope.__exit__, None, None, None)
            raise
        scope = environ.pop(MARKER, None)
        if scope is None:
            return iterable

        closed = False

        def release():
            nonlocal closed
            if not closed:
                closed = True
                try:
                    if hasattr(iterable, "close"):
                        iterable.close()
                finally:
                    scope.__exit__(None, None, None)

        try:
            iterator = context.run(iter, iterable)
        except BaseException:
            context.run(release)
            raise

        class ContextIterator:
            def __iter__(self):
                return self

            def __next__(self):
                if closed:
                    raise StopIteration
                try:
                    return context.run(next, iterator)
                except StopIteration:
                    # Preserve existing response.call_on_close timing. The WSGI
                    # server closes after it has consumed/published the body.
                    raise
                except BaseException:
                    self.close()
                    raise

            def close(self):
                finalizer = getattr(self, "_finalizer", None)
                if finalizer is None:
                    context.run(release)
                else:
                    finalizer()

        result = ContextIterator()
        # A registry-held finalizer keeps the suspended body/authority alive
        # until cleanup can run in this Context. A __del__ alone cannot order
        # their generator finalizers during cyclic GC.
        import weakref
        result._finalizer = weakref.finalize(result, context.run, release)
        return result
