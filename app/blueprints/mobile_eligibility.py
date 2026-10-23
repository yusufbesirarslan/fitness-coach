"""Native declaration and existing-account review, shared authority with web."""
from flask import g
from flask_limiter.util import get_remote_address

from app.blueprints.mobile_api import bp
from app.blueprints.account_eligibility import existing, failure, preview
from app.extensions import limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.services.account_eligibility import EligibilityDenied

bp.register_error_handler(EligibilityDenied, failure)


@bp.post("/auth/eligibility")
@limiter.limit("30 per hour", key_func=get_remote_address)
def eligibility_preview():
    return preview()


@bp.route("/account/eligibility", methods=["GET", "POST"])
@require_mobile_auth
def account_eligibility():
    return existing(g.mobile_user)
