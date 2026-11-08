"""Launch V1 social boundary: one server-side owner, closed for everyone.

Founder decision (Launch V1): friends, user-to-user messages and suggestions,
the feed, Pump Check sharing/engagement, social notifications and friends-only
rankings are unavailable to EVERY account, whatever its age, cohort,
subscription or eligibility record.

``SOCIAL_AVAILABLE`` is a code constant on purpose. It has no environment,
config, query, cookie or header channel, and it does not read
``ACCOUNT_ELIGIBILITY_ENABLED``, so a missing or unrelated setting cannot
reopen social features. Reopening is a reviewed code release. Nothing here
deletes or rewrites stored social rows (friendships, messages, feed items,
likes, comments, Pump Check visibility, notifications); while the constant is
False they are unreachable through the application.

Global leaderboards and global challenge rankings are NOT part of this
decision and stay unchanged.
"""
from flask import jsonify, render_template, request

from app.i18n import t

SOCIAL_AVAILABLE = False

# Every endpoint of this blueprint is a social surface: friends, search,
# requests, feed, reposts, likes, comments, moderation, DMs and suggestions.
# Denying the blueprint (not a route list) also covers any route added to it.
CLOSED_BLUEPRINTS = frozenset({"social"})

# Endpoints that are personal or global by default but have a friends-only
# mode selected by a query parameter. Only that mode is closed.
FRIENDS_SCOPED_ENDPOINTS = frozenset({
    "gamification.leaderboard_data",
    "challenges.challenge_leaderboard",
})

# Browser page navigations get the app's 404 page; everything else gets JSON.
_PAGE_ENDPOINTS = frozenset({
    "social.friends_page", "social.feed_page", "social.chat_page",
})

ERROR_CODE = "SOCIAL_FEATURE_UNAVAILABLE"

# Notification types that are not about another user's social action. The
# list is an allowlist, so an unknown or future type stays hidden.
NON_SOCIAL_NOTIFICATION_TYPES = frozenset({"challenge_complete"})

PRIVATE = "private"


def available():
    return SOCIAL_AVAILABLE is True


def closed_endpoint(endpoint, blueprint, args):
    """Whether this request targets a closed social surface."""
    if available():
        return False
    if blueprint in CLOSED_BLUEPRINTS:
        return True
    return endpoint in FRIENDS_SCOPED_ENDPOINTS and args.get("scope") == "friends"


def notification_visible(ntype):
    return available() or ntype in NON_SOCIAL_NOTIFICATION_TYPES


def effective_visibility(stored):
    """Visibility the application enforces; the stored value is never rewritten."""
    if not available():
        return PRIVATE
    return stored or PRIVATE


def _denied():
    if request.method == "GET" and request.endpoint in _PAGE_ENDPOINTS:
        response = render_template("404.html")
        return response, 404, {"Cache-Control": "no-store"}
    response = jsonify(error=t("social.unavailable"), code=ERROR_CODE)
    response.status_code = 404
    response.headers["Cache-Control"] = "no-store"
    return response


def install(app):
    """Refuse closed social requests before authentication, limits or views.

    Registered right after request id/CSP nonce, so the refusal runs before
    CSRF, the Bearer binder, eligibility, rate limits and every view. Nothing
    is read from the database.
    """
    @app.before_request
    def launch_v1_social_boundary():
        if closed_endpoint(request.endpoint or "", request.blueprint, request.args):
            return _denied()
        return None
