import s3_helper

from app.extensions import db
from app.models import Friendship, PumpCheckLike
from app.timeutil import display_dt

_SHARING_STATUS_KEYS = {
    "feed": "pump_check.sharing.feed",
    "friends": "pump_check.sharing.friends",
    "private": "pump_check.sharing.private",
}


def get_friend_ids(user_id):
    rows = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(Friendship.sender_id == user_id, Friendship.receiver_id == user_id),
    ).all()
    ids = set()
    for row in rows:
        ids.add(row.receiver_id if row.sender_id == user_id else row.sender_id)
    return ids


def can_view_pump_check(user_id, check):
    if check.user_id == user_id:
        return True
    visibility = check.visibility or "private"
    if visibility == "feed":
        return user_id in get_friend_ids(check.user_id)
    if visibility == "friends":
        return user_id in set(check.shared_friend_ids or [])
    return False


def pump_check_image_url(check, viewer_id, expires_in=3600):
    if not check.image_key or not can_view_pump_check(viewer_id, check):
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


def serialize_pump_check_card(check, viewer_id, include_viewer_state=True):
    user = check.user
    liked = False
    if include_viewer_state:
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
        "imageUrl": pump_check_image_url(check, viewer_id),
        "workoutScore": None,
        "environment": check.location_type or "",
        "description": check.description or "",
        "visibility": check.visibility or "private",
        "sharingStatus": sharing_status(check),
        "sharedFriendIds": check.shared_friend_ids or [],
        "likesCount": check.likes_count or 0,
        "commentsCount": check.comments_count or 0,
        "likedByMe": liked,
    }
