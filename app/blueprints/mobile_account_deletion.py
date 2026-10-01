"""Native account deletion (LP-11).

    DELETE /api/v1/account    204  (no body)

Attached to the existing `mobile_api` blueprint, so the `/api/v1` surface, the
`no-store` policy, the ADR 0001 error envelope, the 429/413 handlers, the CSRF
exemption, the `MOBILE_AUTH_ENABLED` gate and the approved-route allow-list
(tests/test_mobile_auth_feature_gate.py) stay single-sourced.

Transport only. The lifecycle — provider identity, storage, local purge, their
order and every failure mode — lives in `app/services/account_deletion.py`.

Owner: the verified Bearer principal and nothing else. The request carries NO
authority: a body or a query string is refused, not ignored, so a client can
never believe it selected an account, an identity or an object to delete.

Success is `204` with no body, the established mobile deletion answer
(`POST /auth/logout`, `DELETE /nutrition/logs/<token>`). Failures:

    401 AUTH_SESSION_EXPIRED          nothing deleted; sign in again, then retry
    503 ACCOUNT_DELETION_UNAVAILABLE  nothing deleted; account intact; retry
    503 ACCOUNT_DELETION_INCOMPLETE   identity deleted, local cleanup not
                                      finished; retry with the same session
    400 ACCOUNT_DELETION_INVALID_REQUEST  a body or query string was sent

None of them is answered by the blueprint's auth-flavoured catch-all, and none
carries provider text, keys, table names or ids.
"""
from flask import current_app, g, request

from app.blueprints.mobile_api import bp, mobile_error
from app.extensions import db
from app.mobile_auth_middleware import _safe_message, require_mobile_auth
from app.observability import current_request_id
from app.services import account_deletion


@bp.delete("/account")
@require_mobile_auth
def delete_account():
    if request.args or request.get_data(cache=True):
        return mobile_error(
            "ACCOUNT_DELETION_INVALID_REQUEST", "Invalid request.", 400,
            False)
    try:
        account_deletion.delete_account(
            g.mobile_user, g.mobile_session, g.mobile_claims)
    except account_deletion.ProviderSessionRejected:
        return mobile_error(
            "AUTH_SESSION_EXPIRED", _safe_message("AUTH_SESSION_EXPIRED"),
            401, False)
    except account_deletion.DeletionUnavailable as failure:
        return mobile_error(
            "ACCOUNT_DELETION_UNAVAILABLE",
            "Account deletion is temporarily unavailable.", 503, True,
            retry_after=failure.retry_after)
    except account_deletion.DeletionIncomplete:
        return _incomplete()
    except Exception as error:
        # The service types every failure by stage; reaching here means it did
        # not. The identity step may have run, so "incomplete" is the only
        # answer that cannot be false — never "nothing happened".
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.error(
            "account_deletion event=unclassified_failure error_type=%s "
            "request_id=%s", type(error).__name__, current_request_id())
        return _incomplete()
    return "", 204


def _incomplete():
    return mobile_error(
        "ACCOUNT_DELETION_INCOMPLETE",
        "Account deletion did not finish. Try again.", 503, True)
