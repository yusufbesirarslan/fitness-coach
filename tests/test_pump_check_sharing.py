import json
from pathlib import Path
from datetime import datetime, timedelta

from app.blueprints import social as social_bp
from app.extensions import db
from app.blueprints import training as training_bp
from app.i18n import t
from app.models import Friendship, Message, PumpCheck, PumpCheckComment, PumpCheckLike
from app.services.pump_checks import (
    can_view_pump_check,
    get_friend_ids,
    serialize_pump_check_card,
)
from tests.test_validators import _image_data_url


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
    assert data["workoutScore"] is None
    assert data["visibility"] == "feed"
    assert data["sharingStatus"] == {
        "key": "pump_check.sharing.feed",
        "value": "feed",
    }


def test_serialize_pump_check_card_includes_workout_score_when_available(client, auth_user):
    check = PumpCheck(
        user_id=auth_user.id,
        visibility="feed",
        location_type="Gym",
        description="Leg day.",
        workout_score=8.0,
        created_at=datetime.utcnow(),
        valid=True,
    )
    db.session.add(check)
    db.session.commit()

    data = serialize_pump_check_card(check, auth_user.id)

    assert data["workoutScore"] == 8.0


def test_workout_complete_persists_score_after_later_training_plan_replacement(client, auth_user, monkeypatch):
    client.post("/training-plan/save", json={"plan": {"v": 1}, "score": 8.0})
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})

    res = client.post("/workout/complete", json={"image": _image_data_url("JPEG"), "location_type": "Gym"})

    assert res.status_code == 200
    check = PumpCheck.query.filter_by(user_id=auth_user.id).one()
    assert check.workout_score == 8.0

    replace = client.post("/training-plan/save", json={"plan": {"v": 2}, "score": 5.0})

    assert replace.status_code == 200
    body = client.get("/feed/data").get_json()
    assert body["posts"][0]["workoutScore"] == 8.0


def _ready_for_workout(client, user):
    client.post("/training-plan/save", json={"plan": {"v": 1}, "score": 8.0})


def test_workout_complete_defaults_to_feed_visibility(client, auth_user, monkeypatch):
    _ready_for_workout(client, auth_user)
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})

    res = client.post("/workout/complete", json={"image": _image_data_url("JPEG"), "location_type": "Gym"})

    assert res.status_code == 200
    check = PumpCheck.query.filter_by(user_id=auth_user.id).one()
    assert check.visibility == "feed"
    assert check.shared_friend_ids == []


def test_workout_complete_rejects_friends_visibility_without_recipients(client, auth_user, monkeypatch):
    _ready_for_workout(client, auth_user)
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})

    res = client.post("/workout/complete", json={
        "image": _image_data_url("JPEG"),
        "visibility": "friends",
        "shared_friend_ids": [],
    })

    assert res.status_code == 400
    assert PumpCheck.query.count() == 0


def test_workout_complete_rejects_non_list_shared_friend_ids(client, auth_user, monkeypatch):
    _ready_for_workout(client, auth_user)
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})

    res = client.post("/workout/complete", json={
        "image": _image_data_url("JPEG"),
        "visibility": "friends",
        "shared_friend_ids": "12",
    })

    assert res.status_code == 400
    assert res.get_json()["error"] == t("pump.friend_ids_invalid")
    assert PumpCheck.query.count() == 0


def test_workout_complete_rejects_boolean_shared_friend_ids_entries(client, auth_user, monkeypatch):
    _ready_for_workout(client, auth_user)
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})
    monkeypatch.setattr(training_bp, "get_friend_ids", lambda user_id: {1})

    res = client.post("/workout/complete", json={
        "image": _image_data_url("JPEG"),
        "visibility": "friends",
        "shared_friend_ids": [True],
    })

    assert res.status_code == 400
    assert res.get_json()["error"] == t("pump.friend_ids_invalid")
    assert PumpCheck.query.count() == 0


def test_workout_complete_rejects_float_shared_friend_ids_entries(client, auth_user, monkeypatch):
    _ready_for_workout(client, auth_user)
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})
    monkeypatch.setattr(training_bp, "get_friend_ids", lambda user_id: {1})

    res = client.post("/workout/complete", json={
        "image": _image_data_url("JPEG"),
        "visibility": "friends",
        "shared_friend_ids": [1.5],
    })

    assert res.status_code == 400
    assert res.get_json()["error"] == t("pump.friend_ids_invalid")
    assert PumpCheck.query.count() == 0


def test_workout_complete_rejects_string_shared_friend_ids_entries(client, auth_user, monkeypatch):
    _ready_for_workout(client, auth_user)
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})
    monkeypatch.setattr(training_bp, "get_friend_ids", lambda user_id: {123})

    res = client.post("/workout/complete", json={
        "image": _image_data_url("JPEG"),
        "visibility": "friends",
        "shared_friend_ids": ["123"],
    })

    assert res.status_code == 400
    assert res.get_json()["error"] == t("pump.friend_ids_invalid")
    assert PumpCheck.query.count() == 0


def test_workout_complete_rejects_non_string_visibility(client, auth_user, monkeypatch):
    _ready_for_workout(client, auth_user)
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})

    res = client.post("/workout/complete", json={
        "image": _image_data_url("JPEG"),
        "visibility": 7,
    })

    assert res.status_code == 400
    assert res.get_json()["error"] == t("pump.visibility_invalid")
    assert PumpCheck.query.count() == 0


def test_workout_complete_sends_pump_check_messages_to_selected_friends(client, auth_user, make_user, monkeypatch):
    friend = make_user("friend")
    other = make_user("other")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=friend.id, status="accepted"))
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=other.id, status="accepted"))
    db.session.commit()
    _ready_for_workout(client, auth_user)
    monkeypatch.setattr(training_bp, "validate_pump_check", lambda *a: {"valid": True, "fallback": False})

    res = client.post("/workout/complete", json={
        "image": _image_data_url("JPEG"),
        "location_type": "Gym",
        "description": "Push day",
        "visibility": "friends",
        "shared_friend_ids": [friend.id],
    })

    assert res.status_code == 200
    check = PumpCheck.query.filter_by(user_id=auth_user.id).one()
    assert check.visibility == "friends"
    assert check.shared_friend_ids == [friend.id]
    msg = Message.query.filter_by(sender_id=auth_user.id, receiver_id=friend.id, message_type="pump_check").one()
    payload = json.loads(msg.body)
    assert payload["pump_check_id"] == check.id
    assert payload["environment"] == "Gym"
    assert Message.query.filter_by(receiver_id=other.id, message_type="pump_check").count() == 0


def test_friend_select_list_recent_contacts_first(client, auth_user, make_user):
    old_friend = make_user("alpha")
    recent_friend = make_user("zeta")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=old_friend.id, status="accepted"))
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=recent_friend.id, status="accepted"))
    db.session.commit()
    db.session.add(Message(sender_id=recent_friend.id, receiver_id=auth_user.id, body="hi"))
    db.session.commit()

    body = client.get("/friends/select-list").get_json()

    assert [row["id"] for row in body["friends"]] == [recent_friend.id, old_friend.id]


def test_feed_data_shows_current_user_and_friend_feed_posts(client, auth_user, make_user):
    friend = make_user("friend")
    stranger = make_user("stranger")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=friend.id, status="accepted"))
    db.session.add(PumpCheck(user_id=auth_user.id, visibility="feed", location_type="Gym", description="Mine", valid=True))
    db.session.add(PumpCheck(user_id=friend.id, visibility="feed", location_type="Home", description="Friend", valid=True))
    db.session.add(PumpCheck(user_id=stranger.id, visibility="feed", location_type="Gym", description="Nope", valid=True))
    db.session.commit()

    body = client.get("/feed/data").get_json()

    descriptions = [post["description"] for post in body["posts"]]
    assert descriptions == ["Friend", "Mine"]
    assert "Nope" not in descriptions


def test_feed_data_excludes_friends_and_private_visibility_posts(client, auth_user, make_user):
    friend = make_user("friend")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=friend.id, status="accepted"))
    db.session.add_all([
        PumpCheck(user_id=auth_user.id, visibility="feed", description="mine-feed", valid=True),
        PumpCheck(user_id=auth_user.id, visibility="friends", description="mine-friends", valid=True),
        PumpCheck(user_id=auth_user.id, visibility="private", description="mine-private", valid=True),
        PumpCheck(user_id=friend.id, visibility="feed", description="friend-feed", valid=True),
        PumpCheck(user_id=friend.id, visibility="friends", description="friend-friends", valid=True),
        PumpCheck(user_id=friend.id, visibility="private", description="friend-private", valid=True),
    ])
    db.session.commit()

    body = client.get("/feed/data").get_json()

    descriptions = [post["description"] for post in body["posts"]]
    assert descriptions == ["friend-feed", "mine-feed"]
    assert "mine-friends" not in descriptions
    assert "mine-private" not in descriptions
    assert "friend-friends" not in descriptions
    assert "friend-private" not in descriptions


def test_feed_page_renders(client, auth_user):
    assert client.get("/feed").status_code == 200


def test_feed_page_uses_shared_feed_nav_and_leaderboard_drawer(client, auth_user):
    html = client.get("/feed").get_data(as_text=True)

    assert 'href="/feed"' in html
    assert 'href="/leaderboard"' in html


def test_standalone_nav_templates_use_feed_bottom_tab_and_keep_club_in_drawer():
    root = Path(__file__).resolve().parents[1]
    template_names = [
        "friends.html",
        "index.html",
        "leaderboard.html",
        "manage_stack.html",
        "nutrition.html",
        "progress.html",
        "quests.html",
        "training.html",
    ]

    for name in template_names:
        html = (root / "templates" / name).read_text(encoding="utf-8")
        assert 'href="/feed" class="ab-tab' in html, name
        assert 'href="/leaderboard" class="ab-tab' not in html, name
        assert 'href="/feed" class="drawer-link' in html, name
        assert 'href="/leaderboard" class="drawer-link' in html, name


def test_like_create_and_delete_updates_count(client, auth_user, make_user):
    friend = make_user("friend")
    db.session.add(Friendship(sender_id=friend.id, receiver_id=auth_user.id, status="accepted"))
    check = PumpCheck(user_id=friend.id, visibility="feed", valid=True)
    db.session.add(check)
    db.session.commit()

    assert client.post(f"/pump-check/{check.id}/like").get_json()["likesCount"] == 1
    assert client.post(f"/pump-check/{check.id}/like").get_json()["likesCount"] == 1
    assert client.delete(f"/pump-check/{check.id}/like").get_json()["likesCount"] == 0


def test_like_delete_skips_decrement_when_atomic_delete_finds_no_row(client, auth_user, make_user, monkeypatch):
    friend = make_user("friend")
    db.session.add(Friendship(sender_id=friend.id, receiver_id=auth_user.id, status="accepted"))
    check = PumpCheck(user_id=friend.id, visibility="feed", likes_count=2, valid=True)
    db.session.add(check)
    db.session.flush()
    db.session.add(PumpCheckLike(pump_check_id=check.id, user_id=auth_user.id))
    db.session.commit()

    real_like = db.session.query(PumpCheckLike).filter_by(pump_check_id=check.id, user_id=auth_user.id).one()

    class _LikeQuery:
        def filter_by(self, **kw):
            assert kw == {"pump_check_id": check.id, "user_id": auth_user.id}
            return self

        def first(self):
            return real_like

        def delete(self, synchronize_session=False):
            assert synchronize_session is False
            return 0

    monkeypatch.setattr(social_bp.PumpCheckLike, "query", _LikeQuery())
    response = client.delete(f"/pump-check/{check.id}/like")

    assert response.status_code == 200
    assert response.get_json() == {"liked": False, "likesCount": 2}
    assert db.session.get(PumpCheck, check.id).likes_count == 2
    assert db.session.query(PumpCheckLike).filter_by(pump_check_id=check.id, user_id=auth_user.id).count() == 1


def test_like_duplicate_integrity_returns_stable_liked_state(client, auth_user, make_user, monkeypatch):
    friend = make_user("friend")
    db.session.add(Friendship(sender_id=friend.id, receiver_id=auth_user.id, status="accepted"))
    check = PumpCheck(user_id=friend.id, visibility="feed", likes_count=1, valid=True)
    db.session.add(check)
    db.session.flush()
    db.session.add(PumpCheckLike(pump_check_id=check.id, user_id=auth_user.id))
    db.session.commit()

    real_query = social_bp.PumpCheckLike.query
    state = {"first_check": True}

    class _RaceQuery:
        def filter_by(self, **kw):
            real = real_query.filter_by(**kw)
            if state["first_check"]:
                state["first_check"] = False

                class _Miss:
                    def first(self_inner):
                        return None

                return _Miss()
            return real

    monkeypatch.setattr(social_bp.PumpCheckLike, "query", _RaceQuery())

    response = client.post(f"/pump-check/{check.id}/like")

    assert response.status_code == 200
    assert response.get_json() == {"liked": True, "likesCount": 1}
    assert PumpCheckLike.query.filter_by(pump_check_id=check.id, user_id=auth_user.id).count() == 1
    assert db.session.get(PumpCheck, check.id).likes_count == 1


def test_like_route_avoids_python_counter_read_modify_write(client, auth_user, monkeypatch):
    state = {"count": 4, "update_values": None}

    class _LoadedCheck:
        id = 123
        user_id = auth_user.id
        visibility = "feed"

        @property
        def likes_count(self):
            raise AssertionError("route read stale likes_count")

    class _FreshCheck:
        id = 123
        user_id = auth_user.id
        visibility = "feed"

        @property
        def likes_count(self):
            return state["count"]

    loaded = _LoadedCheck()
    fresh = _FreshCheck()
    get_state = {"calls": 0}

    def fake_get(model, obj_id):
        assert obj_id == 123
        get_state["calls"] += 1
        return loaded if get_state["calls"] == 1 else fresh

    class _LikeQuery:
        def filter_by(self, **kw):
            return self

        def first(self):
            return None

    class _PumpCheckQuery:
        def filter_by(self, **kw):
            assert kw == {"id": 123}
            return self

        def update(self, values, synchronize_session=False):
            state["update_values"] = values
            state["count"] += 1
            return 1

    monkeypatch.setattr(social_bp, "can_view_pump_check", lambda viewer_id, check: True)
    monkeypatch.setattr(social_bp.PumpCheckLike, "query", _LikeQuery())
    monkeypatch.setattr(social_bp.PumpCheck, "query", _PumpCheckQuery())
    monkeypatch.setattr(social_bp.db.session, "get", fake_get)
    monkeypatch.setattr(social_bp.db.session, "add", lambda obj: None)
    monkeypatch.setattr(social_bp.db.session, "commit", lambda: None)

    response = client.post("/pump-check/123/like")

    assert response.status_code == 200
    assert response.get_json() == {"liked": True, "likesCount": 5}
    assert state["update_values"] is not None
    assert social_bp.PumpCheck.likes_count in state["update_values"]


def test_comment_requires_visibility_and_updates_count(client, auth_user, make_user):
    stranger = make_user("stranger")
    check = PumpCheck(user_id=stranger.id, visibility="feed", valid=True)
    db.session.add(check)
    db.session.commit()

    denied = client.post(f"/pump-check/{check.id}/comments", json={"body": "Nice"})
    assert denied.status_code == 403

    db.session.add(Friendship(sender_id=stranger.id, receiver_id=auth_user.id, status="accepted"))
    db.session.commit()
    ok = client.post(f"/pump-check/{check.id}/comments", json={"body": "Nice work"})
    assert ok.status_code == 200
    assert ok.get_json()["commentsCount"] == 1
    comments = client.get(f"/pump-check/{check.id}/comments").get_json()["comments"]
    assert comments[0]["body"] == "Nice work"


def test_comment_create_route_avoids_python_counter_read_modify_write(client, auth_user, monkeypatch):
    state = {"count": 4, "update_values": None}

    class _LoadedCheck:
        id = 123
        user_id = auth_user.id
        visibility = "feed"

        @property
        def comments_count(self):
            raise AssertionError("route read stale comments_count")

    class _FreshCheck:
        id = 123
        user_id = auth_user.id
        visibility = "feed"

        @property
        def comments_count(self):
            return state["count"]

    loaded = _LoadedCheck()
    fresh = _FreshCheck()
    get_state = {"calls": 0}

    def fake_get(model, obj_id):
        assert obj_id == 123
        get_state["calls"] += 1
        return loaded if get_state["calls"] == 1 else fresh

    class _PumpCheckQuery:
        def filter_by(self, **kw):
            assert kw == {"id": 123}
            return self

        def update(self, values, synchronize_session=False):
            state["update_values"] = values
            state["count"] += 1
            return 1

    def fake_add(obj):
        if isinstance(obj, PumpCheckComment):
            obj.id = 77

    monkeypatch.setattr(social_bp, "can_view_pump_check", lambda viewer_id, check: True)
    monkeypatch.setattr(social_bp.PumpCheck, "query", _PumpCheckQuery())
    monkeypatch.setattr(social_bp.db.session, "get", fake_get)
    monkeypatch.setattr(social_bp.db.session, "add", fake_add)
    monkeypatch.setattr(social_bp.db.session, "commit", lambda: None)

    response = client.post("/pump-check/123/comments", json={"body": "Nice work"})

    assert response.status_code == 200
    assert response.get_json() == {"id": 77, "commentsCount": 5}
    assert state["update_values"] is not None
    assert social_bp.PumpCheck.comments_count in state["update_values"]


def test_feed_data_exposes_workout_score_when_present(client, auth_user):
    db.session.add(PumpCheck(
        user_id=auth_user.id,
        visibility="feed",
        location_type="Gym",
        description="Scored session",
        workout_score=8.0,
        valid=True,
    ))
    db.session.commit()

    body = client.get("/feed/data").get_json()

    assert body["posts"][0]["workoutScore"] == 8.0


def test_feed_data_ignores_malformed_pagination_and_uses_defaults(client, auth_user):
    db.session.add(PumpCheck(
        user_id=auth_user.id,
        visibility="feed",
        location_type="Gym",
        description="Fallback page",
        valid=True,
    ))
    db.session.commit()

    response = client.get("/feed/data?page=abc&per_page=xyz")

    assert response.status_code == 200
    body = response.get_json()
    assert len(body["posts"]) == 1
    assert body["posts"][0]["description"] == "Fallback page"
    assert body["hasMore"] is False
    assert body["nextPage"] is None
