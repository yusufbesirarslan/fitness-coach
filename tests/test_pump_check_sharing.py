from datetime import datetime, timedelta

from app.extensions import db
from app.models import Friendship, PumpCheck, PumpCheckComment, PumpCheckLike
from app.services.pump_checks import (
    can_view_pump_check,
    get_friend_ids,
    serialize_pump_check_card,
)


def _friend(a, b):
    db.session.add(Friendship(sender_id=a.id, receiver_id=b.id, status="accepted"))
    db.session.commit()


def test_pump_check_defaults_are_private_safe(auth_user):
    check = PumpCheck(user_id=auth_user.id, image_key="pump-checks/1/2026/07/x.jpg", valid=True)
    db.session.add(check)
    db.session.commit()

    assert check.visibility == "private"
    assert check.shared_friend_ids == []
    assert check.likes_count == 0
    assert check.comments_count == 0


def test_get_friend_ids_returns_accepted_friend_ids(make_user):
    user = make_user("owner")
    friend = make_user("friend")
    pending = make_user("pending")
    db.session.add(Friendship(sender_id=user.id, receiver_id=friend.id, status="accepted"))
    db.session.add(Friendship(sender_id=pending.id, receiver_id=user.id, status="pending"))
    db.session.commit()

    assert get_friend_ids(user.id) == {friend.id}


def test_can_view_pump_check_enforces_visibility(make_user):
    owner = make_user("owner")
    friend = make_user("friend")
    selected = make_user("selected")
    stranger = make_user("stranger")
    _friend(owner, friend)
    _friend(owner, selected)

    feed = PumpCheck(user_id=owner.id, visibility="feed", valid=True)
    friends = PumpCheck(user_id=owner.id, visibility="friends", shared_friend_ids=[selected.id], valid=True)
    private = PumpCheck(user_id=owner.id, visibility="private", valid=True)
    db.session.add_all([feed, friends, private])
    db.session.commit()

    assert can_view_pump_check(owner.id, feed) is True
    assert can_view_pump_check(friend.id, feed) is True
    assert can_view_pump_check(stranger.id, feed) is False
    assert can_view_pump_check(selected.id, friends) is True
    assert can_view_pump_check(friend.id, friends) is False
    assert can_view_pump_check(owner.id, private) is True
    assert can_view_pump_check(friend.id, private) is False


def test_serialize_pump_check_card_exposes_requested_fields(make_user):
    owner = make_user("owner", full_name="Owner Person")
    check = PumpCheck(
        user_id=owner.id,
        image_key=None,
        location_type="Gym",
        description="Upper body session.",
        visibility="feed",
        likes_count=2,
        comments_count=3,
        created_at=datetime.utcnow() - timedelta(hours=2),
        valid=True,
    )
    db.session.add(check)
    db.session.flush()
    db.session.add(PumpCheckLike(pump_check_id=check.id, user_id=owner.id))
    db.session.add(PumpCheckComment(pump_check_id=check.id, user_id=owner.id, body="Nice"))
    db.session.commit()

    data = serialize_pump_check_card(check, owner.id)

    assert data["id"] == check.id
    assert data["username"] == "owner"
    assert data["imageUrl"] is None
    assert data["environment"] == "Gym"
    assert data["description"] == "Upper body session."
    assert data["likesCount"] == 2
    assert data["commentsCount"] == 3
    assert data["visibility"] == "feed"
