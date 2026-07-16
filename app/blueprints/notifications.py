# Sosyal bildirim uçları (Sprint 5 PR1). Liste keyset (before_id) sayfalanır;
# tüm sorgular current_user.id'ye scope'ludur — başkasının bildirimi asla dönmez.
from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy.orm import joinedload

from app.auth_middleware import require_auth
from app.i18n import t
from app.models import Notification
from app.services.notifications import mark_read, serialize_notification, unread_count

bp = Blueprint("notifications", __name__)


@bp.route("/notifications")
@require_auth
def notifications_page():
    return render_template("notifications.html",
                           username=current_user.username,
                           profile_picture=current_user.avatar_src)


@bp.route("/notifications/data")
@require_auth
def notifications_data():
    try:
        limit = min(max(int(request.args.get("limit", 20) or 20), 1), 50)
    except (TypeError, ValueError):
        limit = 20
    try:
        before_id = int(request.args.get("before_id", 0) or 0)
    except (TypeError, ValueError):
        before_id = 0

    query = Notification.query.options(
        joinedload(Notification.actor),
    ).filter(Notification.user_id == current_user.id)
    if before_id > 0:
        query = query.filter(Notification.id < before_id)
    rows = query.order_by(Notification.id.desc()).limit(limit + 1).all()
    page = rows[:limit]
    has_more = len(rows) > limit
    return jsonify({
        "notifications": [serialize_notification(n) for n in page],
        "hasMore": has_more,
        "nextBeforeId": page[-1].id if has_more and page else None,
        "unreadCount": unread_count(current_user.id),
    })


@bp.route("/notifications/unread-count")
@require_auth
def notifications_unread_count():
    return jsonify({"count": unread_count(current_user.id)})


@bp.route("/notifications/read", methods=["POST"])
@require_auth
def notifications_read():
    data = request.get_json(silent=True) or {}
    mark_all = data.get("all") is True
    ids = data.get("ids")
    if not mark_all:
        if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
            return jsonify({"error": t("notif.invalid_request")}), 400
    mark_read(current_user.id, ids=None if mark_all else ids, mark_all=mark_all)
    return jsonify({"ok": True, "unreadCount": unread_count(current_user.id)})
