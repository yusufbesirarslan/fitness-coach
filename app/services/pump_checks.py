import s3_helper

from app.models import PumpCheckLike, TrainingPlan
# Geriye uyumluluk: get_friend_ids artık friends servisinde yaşıyor; buradan
# import eden mevcut modüller (social, training, ...) kırılmasın diye re-export.
from app.services.friends import get_friend_ids  # noqa: F401
from app.timeutil import display_dt

_SHARING_STATUS_KEYS = {
    "feed": "pump_check.sharing.feed",
    "friends": "pump_check.sharing.friends",
    "private": "pump_check.sharing.private",
}


def can_view_pump_check(user_id, check):
    if check.user_id == user_id:
        return True
    visibility = check.visibility or "private"
    if visibility == "feed":
        return user_id in get_friend_ids(check.user_id)
    if visibility == "friends":
        # S2: paylaşım anında seçilmiş olması yetmez — arkadaşlık sonradan
        # kaldırıldıysa erişim de düşmeli (bayat yetkilendirme). Hem saklanan
        # listede OLMALI hem de HÂLÂ kabul edilmiş arkadaş olmalı.
        return (user_id in set(check.shared_friend_ids or [])
                and user_id in get_friend_ids(check.user_id))
    return False


def pump_check_image_url(check, viewer_id, expires_in=3600, visibility_preauthorized=False):
    if not check.image_key:
        return None
    if not visibility_preauthorized and not can_view_pump_check(viewer_id, check):
        return None
    return s3_helper.generate_presigned_url(
        check.image_key,
        expires_in=expires_in,
        expected_user_id=check.user_id,
    )


def sharing_status(check):
    visibility = check.visibility or "private"
    return {
        "key": _SHARING_STATUS_KEYS.get(visibility, _SHARING_STATUS_KEYS["private"]),
        "value": visibility if visibility in _SHARING_STATUS_KEYS else "private",
    }


def normalize_workout_score(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def latest_training_plan_score(user_id):
    plan = TrainingPlan.query.filter_by(user_id=user_id)\
        .order_by(TrainingPlan.created_at.desc(), TrainingPlan.id.desc())\
        .first()
    if plan is None:
        return None
    return normalize_workout_score(plan.score)


def workout_score(check):
    return normalize_workout_score(getattr(check, "workout_score", None))


def serialize_pump_check_card(
    check,
    viewer_id,
    include_viewer_state=True,
    liked_pump_check_ids=None,
    image_visibility_preauthorized=False,
    reposted_ref_ids=None,
):
    user = check.user
    liked = False
    if include_viewer_state:
        if liked_pump_check_ids is not None:
            liked = check.id in liked_pump_check_ids
        else:
            liked = PumpCheckLike.query.filter_by(
                pump_check_id=check.id,
                user_id=viewer_id,
            ).first() is not None
    return {
        "id": check.id,
        "userId": check.user_id,
        "username": user.username if user else "",
        "userAvatar": user.avatar_src if user else None,
        "timePosted": display_dt(check.created_at, "%d.%m.%Y %H:%M"),
        "createdAt": check.created_at.isoformat() if check.created_at else None,
        "imageUrl": pump_check_image_url(
            check,
            viewer_id,
            visibility_preauthorized=image_visibility_preauthorized,
        ),
        "workoutScore": workout_score(check),
        "environment": check.location_type or "",
        "description": check.description or "",
        "visibility": check.visibility or "private",
        "sharingStatus": sharing_status(check),
        "sharedFriendIds": check.shared_friend_ids or [],
        "likesCount": check.likes_count or 0,
        "commentsCount": check.comments_count or 0,
        "repostsCount": getattr(check, "reposts_count", 0) or 0,
        "likedByMe": liked,
        "repostedByMe": (reposted_ref_ids is not None and check.id in reposted_ref_ids),
    }
