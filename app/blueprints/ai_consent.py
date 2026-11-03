"""Session + bearer API, using existing web CSRF and mobile authentication."""
from flask import Blueprint, g, jsonify, request
from flask_login import current_user
from app.auth_middleware import require_auth
from app.mobile_auth_middleware import require_mobile_auth
from app.services import ai_consent as consent
from app.observability import current_request_id

bp = Blueprint('ai_consent', __name__)



def consent_error_response(error):
    # Preserve the established native error envelope for older clients.
    response = jsonify(error={
        'code': error.code,
        'message': 'AI processing is unavailable. Review sharing permissions before continuing.',
        'retryable': error.status == 503,
        'request_id': current_request_id(),
    })
    response.status_code = error.status
    response.headers['Cache-Control'] = 'no-store'
    return response

def _respond(owner):
    try:
        result = consent.status(owner) if request.method == 'GET' else consent.mutate(owner, request.get_json(silent=True))
        response = jsonify(result)
    except consent.ConsentDenied as error:
        response = consent_error_response(error)
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.route('/api/ai-consent', methods=['GET', 'POST'])
@require_auth
def web_consent():
    return _respond(current_user.id)


@require_mobile_auth
def mobile_consent():
    return _respond(g.mobile_user.id)


# Deny BEFORE upload, quota, enqueue or model context building. Manual/history
# endpoints are deliberately absent. Mixed first-party actions gate only their
# AI branch at the provider boundary below.
# LP21: the browser plan-save aliases persist the closed browser generators'
# output from a CLIENT-held document. No server-held AI provenance can travel
# with it, so a withdrawn operation's result could become canonical there;
# the shared writers also refuse them (no ``OperationAuthority``).
_CLOSED_ENDPOINTS = frozenset({
    'coach.chat', 'coach.ask_coach', 'coach.ask_coach_stream',
    'training.training_plan_generate',
    'menu.analyze_menu', 'menu.proxy_scan_menu',
    'mobile_api.send_coach_message', 'mobile_api.create_training_plan',
    'mobile_api.create_replacement_proposal', 'mobile_api.nutrition_plan_generate_native',
    'mobile_api.nutrition_menu_analyze', 'mobile_api.create_pump_check',
    'mobile_api.create_pump_check_comparison',
    'nutrition.nutrition_plan_generate', 'nutrition.review_meals',
    'training.save_training_plan', 'nutrition.save_nutrition_plan',
})


def require_legacy_ai(code="AI_CONSENT_DISCLOSURE_UNAVAILABLE"):
    """Fresh unqualified AI work is unavailable; first-party replays may run."""
    raise consent.ConsentDenied(code)


def install(app):
    app.register_blueprint(bp)

    app.register_error_handler(consent.ConsentDenied, consent_error_response)

    @app.before_request
    def closed_legacy_ai():
        if request.method in {'POST', 'PUT'} and request.endpoint in _CLOSED_ENDPOINTS:
            # Enforce the existing transport size ceiling without consuming
            # the body or building any AI context.
            from flask import current_app
            from werkzeug.exceptions import RequestEntityTooLarge
            ceiling = current_app.config.get('MAX_CONTENT_LENGTH')
            if ceiling is not None and request.content_length is not None and request.content_length > ceiling:
                raise RequestEntityTooLarge()
            def deny():
                from app.services.ai_launch import NATIVE_ENDPOINTS, admit_endpoint
                feature = NATIVE_ENDPOINTS.get(request.endpoint)
                if feature is not None:
                    try:
                        admit_endpoint(g.mobile_user.id, feature)
                    except consent.ConsentDenied as error:
                        return consent_error_response(error)
                    return None
                return consent_error_response(consent.ConsentDenied('AI_CONSENT_DISCLOSURE_UNAVAILABLE'))
            if request.endpoint.startswith('mobile_api.'):
                return require_mobile_auth(deny)()
            from app.auth_middleware import require_auth
            return require_auth(deny)()
