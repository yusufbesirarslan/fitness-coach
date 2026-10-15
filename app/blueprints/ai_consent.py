"""Session + bearer API, using existing web CSRF and mobile authentication."""
from flask import Blueprint, g, jsonify, request
from flask_login import current_user
from app.auth_middleware import require_auth
from app.mobile_auth_middleware import require_mobile_auth
from app.services import ai_consent as consent

bp = Blueprint('ai_consent', __name__)


def _respond(owner):
    try:
        result = consent.status(owner) if request.method == 'GET' else consent.mutate(owner, request.get_json(silent=True))
        response = jsonify(result)
    except consent.ConsentDenied as error:
        response = jsonify(error={'code': error.code, 'retryable': error.status == 503})
        response.status_code = error.status
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
_CLOSED_ENDPOINTS = frozenset({
    'coach.chat', 'coach.ask_coach', 'coach.ask_coach_stream',
    'training.training_plan_generate', 'training.complete_workout',
    'menu.analyze_menu', 'menu.proxy_scan_menu',
    'mobile_api.send_coach_message', 'mobile_api.create_training_plan',
    'mobile_api.create_replacement_proposal', 'mobile_api.nutrition_plan_generate_native',
    'mobile_api.nutrition_menu_analyze', 'mobile_api.create_pump_check',
    'mobile_api.create_pump_check_comparison', 'mobile_api.complete_workout_session',
    'nutrition.nutrition_plan_generate', 'nutrition.review_meals',
})


def install(app):
    app.register_blueprint(bp)

    @app.errorhandler(consent.ConsentDenied)
    def refusal(error):
        response = jsonify(error={'code': error.code, 'retryable': error.status == 503})
        response.status_code = error.status
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.before_request
    def closed_legacy_ai():
        if request.method in {'POST', 'PUT'} and request.endpoint in _CLOSED_ENDPOINTS:
            def deny():
                return refusal(consent.ConsentDenied('AI_CONSENT_DISCLOSURE_UNAVAILABLE'))
            if request.endpoint.startswith('mobile_api.'):
                return require_mobile_auth(deny)()
            from app.auth_middleware import require_auth
            return require_auth(deny)()
