# Meydan okuma uçları (Sprint 5 PR3). Tümü @require_auth; sorgular
# current_user.id'ye scope'lu. Kanonik değerler (code/metric/type) İngilizce
# slug; görünen title/description t_or ile çevrilir.
from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from app.auth_middleware import require_auth
from app.extensions import db
from app.i18n import t, t_or
from app.models import Challenge, UserChallengeProgress
from app.services import challenges as ch_service
from app.services.badges import BADGE_CATALOG, badges_for

bp = Blueprint("challenges", __name__)


@bp.route("/challenges")
@require_auth
def challenges_page():
    return render_template("challenges.html",
                           username=current_user.username,
                           profile_picture=current_user.avatar_src)


@bp.route("/challenges/data")
@require_auth
def challenges_data():
    week_key = ch_service.current_challenge_week()
    rows = {r.challenge_id: r for r in UserChallengeProgress.query.filter_by(
        user_id=current_user.id, period_key=week_key).all()}
    out = []
    for c in Challenge.query.filter_by(is_active=True).order_by(
            Challenge.challenge_type.asc(), Challenge.id.asc()).all():
        r = rows.get(c.id)
        meta = BADGE_CATALOG.get(c.badge_code) if c.badge_code else None
        out.append({
            "id": c.id, "code": c.code,
            "title": t_or("challenge.%s.title" % c.code, c.title),
            "description": t_or("challenge.%s.desc" % c.code, c.description or ""),
            "category": c.category, "type": c.challenge_type, "metric": c.metric,
            "target": c.target_value, "xpReward": c.xp_reward,
            "badgeCode": c.badge_code, "badgeIcon": meta["icon"] if meta else None,
            "progress": r.progress if r else 0,
            "completed": bool(r and r.completed_at),
            "joined": bool(r and r.opted_in) if c.challenge_type == "featured" else True,
        })
    return jsonify({
        "weekKey": week_key,
        "periodEndsAt": ch_service.period_end_utc().isoformat() + "Z",
        "challenges": out,
        "badges": badges_for(current_user.id),
    })


@bp.route("/challenges/<int:cid>/join", methods=["POST"])
@require_auth
def challenge_join(cid):
    c = Challenge.query.filter_by(id=cid, is_active=True).first_or_404()
    if c.challenge_type != "featured":
        return jsonify({"error": t("challenge.not_joinable")}), 400
    row = ch_service.join_featured(current_user.id, cid)
    if row is None:
        return jsonify({"error": t("challenge.not_joinable")}), 400
    db.session.commit()
    return jsonify({"ok": True, "joined": True})


@bp.route("/challenges/<int:cid>/leaderboard")
@require_auth
def challenge_leaderboard(cid):
    Challenge.query.filter_by(id=cid).first_or_404()
    scope = "friends" if request.args.get("scope") == "friends" else "global"
    week_key = ch_service.current_challenge_week()
    board = ch_service.challenge_board(cid, week_key, scope, current_user.id)
    board["scope"] = scope
    board["weekKey"] = week_key
    return jsonify(board)
