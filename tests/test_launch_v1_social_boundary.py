"""Launch V1 social boundary (app/services/launch_social.py).

Founder decision: friends, messages, suggestions, the feed, Pump Check
sharing/engagement, social notifications and friends-only rankings are closed
for EVERY account, independent of ACCOUNT_ELIGIBILITY_ENABLED. Personal Pump
Check, gamification, global rankings and non-social notifications stay.

The route matrix is built from the live URL map, so a social route added
later is covered without editing this file.
"""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.blueprints import training as training_bp
from app.extensions import db
from app.models import (
    Challenge, FeedItem, FeedItemComment, FeedItemLike, Friendship, Message,
    Notification, PumpCheck, PumpCheckComment, PumpCheckLike, TrainingPlan, User,
)
from app.services import launch_social
from app.services.notifications import notify, unread_count
from app.services.pump_checks import (
    can_view_pump_check, pump_check_image_url, serialize_pump_check_card,
)
from app.services.workout_completion import CompleteWorkoutCommand, complete_workout
from app.timeutil import app_today
from tests.test_validators import _image_data_url

SOCIAL_TABLES = ("friendship", "message", "feed_item", "feed_hide", "feed_report",
                 "pump_check", "notification", "activity")
CODE = launch_social.ERROR_CODE


@pytest.fixture(params=[False, True], ids=["eligibility_off", "eligibility_on"])
def eligibility(app, request):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = request.param
    return request.param


@pytest.fixture
def world(make_user, auth_user):
    """auth_user with an accepted friend, an old DM, old shares and engagement."""
    friend = make_user("arkadas")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=friend.id, status="accepted"))
    feed_check = PumpCheck(user_id=friend.id, visibility="feed", valid=True,
                           image_key=f"pump-checks/{friend.id}/2026/07/f.jpg")
    friends_check = PumpCheck(user_id=friend.id, visibility="friends", valid=True,
                              shared_friend_ids=[auth_user.id],
                              image_key=f"pump-checks/{friend.id}/2026/07/g.jpg")
    db.session.add_all([feed_check, friends_check])
    db.session.flush()
    db.session.add_all([
        Message(sender_id=friend.id, receiver_id=auth_user.id, body="selam"),
        PumpCheckLike(pump_check_id=feed_check.id, user_id=auth_user.id),
        PumpCheckComment(pump_check_id=feed_check.id, user_id=auth_user.id, body="harika"),
        FeedItem(user_id=friend.id, item_type="repost", ref_type="pump_check",
                 ref_id=feed_check.id),
    ])
    db.session.commit()
    return SimpleNamespace(me=auth_user, friend=friend, feed_check=feed_check,
                           friends_check=friends_check)


def _social_requests(app, world):
    """Every (method, url) the social blueprint registers, with real ids."""
    values = {"username": world.friend.username, "check_id": world.feed_check.id,
              "comment_id": 1, "item_id": 1, "request_id": 1, "msg_id": 1}
    requests = []
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("social."):
            continue
        url = rule.build({a: values[a] for a in rule.arguments}, append_unknown=False)[1]
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            requests.append((rule.endpoint, method, url))
    return requests


def _row_counts():
    return {model.__tablename__: model.query.count() for model in (
        Friendship, Message, FeedItem, FeedItemLike, FeedItemComment, PumpCheck,
        PumpCheckLike, PumpCheckComment, Notification)}


class _Statements:
    def __init__(self):
        self.sql = []

    def __call__(self, conn, cursor, statement, params, context, executemany):
        self.sql.append(statement.lower())

    def touching_social_tables(self):
        return [s for s in self.sql if any(f" {t}" in s or f'"{t}"' in s for t in SOCIAL_TABLES)]


def _assert_denied(endpoint, method, response):
    assert response.status_code == 404, (endpoint, method, response.status_code)
    assert response.headers["Cache-Control"] == "no-store"
    if method == "GET" and endpoint in {"social.friends_page", "social.feed_page",
                                        "social.chat_page"}:
        assert response.mimetype == "text/html"
    else:
        assert response.get_json()["code"] == CODE, (endpoint, method)


# ── Constant and configuration independence ─────────────────────────────────

def test_social_is_closed_by_a_code_constant_with_no_config_channel(app, monkeypatch):
    assert launch_social.SOCIAL_AVAILABLE is False
    monkeypatch.setenv("SOCIAL_AVAILABLE", "1")
    app.config["SOCIAL_AVAILABLE"] = True
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = False
    with app.test_request_context("/friends"):
        assert launch_social.available() is False
        assert launch_social.closed_endpoint("social.friends_page", "social", {}) is True


def test_social_blueprint_is_registered_and_non_empty(app):
    endpoints = {r.endpoint for r in app.url_map.iter_rules() if r.endpoint.startswith("social.")}
    # Guard against a vacuous matrix: the inventory in the PR lists these.
    assert {"social.friends_list", "social.friends_search", "social.friend_request",
            "social.feed_data", "social.feed_repost", "social.pump_check_like",
            "social.chat_messages", "social.chat_send", "social.send_suggestion",
            "social.respond_suggestion", "social.feed_report"} <= endpoints
    assert len(endpoints) >= 30


# ── Direct-request matrix ───────────────────────────────────────────────────

def test_every_social_route_is_closed_for_authenticated_web_users(app, client, world, eligibility):
    before = _row_counts()
    requests = _social_requests(app, world)
    check_id = world.feed_check.id  # read ids before capturing statements
    statements = _Statements()
    event.listen(db.engine, "before_cursor_execute", statements)
    try:
        for endpoint, method, url in requests:
            response = client.open(url, method=method, json={
                "body": "x", "items": [], "target_type": "pump_check",
                "target_id": check_id, "ref_id": check_id, "mode": "repost"})
            _assert_denied(endpoint, method, response)
    finally:
        event.remove(db.engine, "before_cursor_execute", statements)
    assert len(requests) >= 32  # 32 route/method pairs at d13c9fa1
    assert statements.touching_social_tables() == []
    assert _row_counts() == before


def test_every_social_route_is_closed_for_anonymous_requests(app, make_user, eligibility):
    owner = make_user("sahip")
    check = PumpCheck(user_id=owner.id, visibility="feed", valid=True)
    db.session.add(check)
    db.session.commit()
    world = SimpleNamespace(friend=owner, feed_check=check)
    anonymous = app.test_client()
    for endpoint, method, url in _social_requests(app, world):
        _assert_denied(endpoint, method, anonymous.open(url, method=method, json={}))


def test_social_routes_are_closed_for_bearer_clients(app, make_user, monkeypatch, eligibility):
    from app.services import mobile_auth
    user = make_user("mobil")
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda _: SimpleNamespace(
        user=db.session.get(User, user.id), family=None, claims={}))
    headers = {"Authorization": "Bearer fixture-access"}
    c = app.test_client()
    for url, method in [("/friends/list", "GET"), ("/feed/data", "GET"),
                        ("/chat/mobil/send", "POST"), ("/friend/request/mobil", "POST")]:
        response = c.open(url, method=method, headers=headers, json={"body": "x"})
        assert response.status_code == 404
        assert response.get_json()["code"] == CODE


def test_old_friendship_does_not_open_messages(client, world, eligibility):
    assert client.get(f"/chat/{world.friend.username}/messages").status_code == 404
    response = client.post(f"/chat/{world.friend.username}/send", json={"body": "yeni"})
    assert response.status_code == 404
    assert Message.query.count() == 1  # the historical row is kept, nothing added


# ── Pump Check: existing shares are private, stored metadata untouched ──────

def test_existing_shared_pump_checks_are_not_visible_cross_account(world, eligibility, monkeypatch):
    import s3_helper
    monkeypatch.setattr(s3_helper, "generate_presigned_url", lambda *a, **k: "https://signed")
    for check in (world.feed_check, world.friends_check):
        assert can_view_pump_check(world.me.id, check) is False
        assert pump_check_image_url(check, world.me.id) is None
        assert can_view_pump_check(world.friend.id, check) is True  # owner
        assert pump_check_image_url(check, world.friend.id) == "https://signed"
    db.session.expire_all()
    stored = {c.id: (c.visibility, c.shared_friend_ids) for c in PumpCheck.query.all()}
    assert stored[world.feed_check.id] == ("feed", [])
    assert stored[world.friends_check.id] == ("friends", [world.me.id])


def test_owner_card_projects_private_without_friend_ids(world):
    card = serialize_pump_check_card(world.friends_check, world.friend.id,
                                     include_viewer_state=False)
    assert card["visibility"] == "private"
    assert card["sharingStatus"]["value"] == "private"
    assert "sharedFriendIds" not in card


def test_owner_gallery_lists_and_deletes_own_checks(app, client, auth_user, monkeypatch, eligibility):
    import s3_helper
    monkeypatch.setattr(s3_helper, "generate_presigned_url", lambda *a, **k: "https://own")
    monkeypatch.setattr(s3_helper, "delete_managed_object", lambda *a, **k: None)
    mine = PumpCheck(user_id=auth_user.id, visibility="feed", valid=True,
                     image_key=f"pump-checks/{auth_user.id}/2026/07/m.jpg")
    db.session.add(mine)
    db.session.commit()
    items = client.get("/pump-check-gallery/data").get_json()["items"]
    assert [i["id"] for i in items] == [mine.id]
    assert items[0]["imageUrl"] == "https://own"
    assert items[0]["visibility"] == "private"
    page = client.get("/pump-check-gallery").get_data(as_text=True)
    assert "data-share-gallery" not in page
    assert client.delete(f"/pump-check-gallery/{mine.id}").status_code == 200
    assert PumpCheck.query.filter_by(user_id=auth_user.id).count() == 0


def _seed_plan(user):
    db.session.add(TrainingPlan(user_id=user.id, plan_data=json.dumps({"v": 1}), score=8.0))
    db.session.commit()


def _ready(app, user):
    """With eligibility on, the existing F01 body-photo gate needs an adult record."""
    _seed_plan(user)
    if app.config["ACCOUNT_ELIGIBILITY_ENABLED"]:
        from app.services import account_eligibility as policy
        token = policy.preview({"age": 30, "country": "GB"})["eligibility"]["registration_token"]
        policy.attach(user, policy.registration_record(token))
        db.session.commit()


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_web_completion_without_visibility_is_private_and_messages_nobody(
        app, client, world, monkeypatch, eligibility):
    _ready(app, world.me)
    monkeypatch.setattr(training_bp, "validate_pump_check",
                        lambda *a: {"valid": True, "fallback": False})
    xp_before = db.session.get(User, world.me.id).rank_points or 0
    response = client.post("/workout/complete", json={
        "image": _image_data_url("JPEG"), "location_type": "Gym"})
    assert response.status_code == 200
    check = PumpCheck.query.filter_by(user_id=world.me.id).one()
    assert check.visibility == "private" and check.shared_friend_ids == []
    assert Message.query.count() == 1
    db.session.expire_all()
    assert (db.session.get(User, world.me.id).rank_points or 0) > xp_before  # personal XP


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("payload", [
    {"visibility": "feed"},
    {"visibility": "friends", "shared_friend_ids": "FRIEND"},
])
def test_web_completion_refuses_sharing_before_ai_and_storage(
        app, client, world, monkeypatch, payload, eligibility):
    _ready(app, world.me)
    calls = []
    monkeypatch.setattr(training_bp, "validate_pump_check",
                        lambda *a: calls.append(a) or {"valid": True, "fallback": False})
    if payload.get("shared_friend_ids") == "FRIEND":
        payload = dict(payload, shared_friend_ids=[world.friend.id])
    response = client.post("/workout/complete", json={
        "image": _image_data_url("JPEG"), **payload})
    assert response.status_code == 403
    assert response.get_json()["code"] == CODE
    assert calls == []
    assert PumpCheck.query.filter_by(user_id=world.me.id).count() == 0
    assert Message.query.count() == 1


def test_completion_service_never_fans_out_friend_messages(app, world):
    with app.test_request_context("/"):
        complete_workout(CompleteWorkoutCommand(
            user_id=world.me.id, today=app_today(), visibility="friends",
            shared_friend_ids=(world.friend.id,), entry_path="route"))
    assert Message.query.count() == 1


# ── Rankings: friends-only closed, global unchanged ─────────────────────────

def test_friends_leaderboard_is_closed_and_global_is_served(client, world, eligibility):
    denied = client.get("/leaderboard/data?scope=friends")
    assert denied.status_code == 404 and denied.get_json()["code"] == CODE
    body = client.get("/leaderboard/data?scope=global").get_json()
    assert "entries" in body and "resetAt" in body
    assert client.get("/leaderboard/data").status_code == 200
    page = client.get("/leaderboard").get_data(as_text=True)
    assert 'data-scope="friends"' not in page and 'href="/friends"' not in page


def test_friends_challenge_ranking_is_closed_and_global_is_served(client, world, eligibility):
    c = Challenge(code="lv1_meals", title="Meals", metric="meal_logged", target_value=3)
    db.session.add(c)
    db.session.commit()
    denied = client.get(f"/challenges/{c.id}/leaderboard?scope=friends")
    assert denied.status_code == 404 and denied.get_json()["code"] == CODE
    board = client.get(f"/challenges/{c.id}/leaderboard?scope=global").get_json()
    assert board["scope"] == "global"
    assert client.get("/challenges/data").status_code == 200
    assert 'data-scope="friends"' not in client.get("/challenges").get_data(as_text=True)


# ── Notifications ───────────────────────────────────────────────────────────

def test_social_notifications_are_hidden_and_not_created(app, client, world, eligibility):
    social = [Notification(user_id=world.me.id, actor_id=world.friend.id, ntype=t,
                           target_type="pump_check", target_id=world.feed_check.id)
              for t in ("pump_check_like", "pump_check_comment", "repost", "quote_repost",
                        "feed_like", "feed_comment", "friend_request", "friend_accept")]
    system = Notification(user_id=world.me.id, ntype="challenge_complete",
                          target_type="challenge", target_id=1)
    db.session.add_all(social + [system])
    db.session.commit()

    body = client.get("/notifications/data").get_json()
    assert [n["type"] for n in body["notifications"]] == ["challenge_complete"]
    assert body["unreadCount"] == 1
    assert client.get("/notifications/unread-count").get_json() == {"count": 1}
    client.post("/notifications/read", json={"all": True})
    assert unread_count(world.me.id) == 0
    # Historical social rows are kept and untouched.
    assert Notification.query.filter(Notification.ntype != "challenge_complete",
                                     Notification.is_read.is_(False)).count() == 8

    with app.test_request_context("/"):
        assert notify(world.me.id, "pump_check_like", actor_id=world.friend.id) is None
        assert notify(world.me.id, "challenge_complete", target_type="challenge",
                      target_id=2) is not None


# ── Navigation and profile hub ──────────────────────────────────────────────

def test_navigation_and_profile_hub_have_no_social_entry(client, auth_user):
    from app import nav
    ids = {d["id"] for d in nav.primary_destinations() + nav.secondary_destinations()}
    assert not ids & {"friends", "feed"}
    assert {"leaderboard", "quests", "challenges", "notifications"} <= ids
    html = client.get("/edit-profile").get_data(as_text=True)
    assert 'href="/friends"' not in html and 'href="/feed"' not in html
    assert 'href="/leaderboard"' in html and 'href="/challenges"' in html


def test_personal_gamification_pages_still_render(client, auth_user):
    for route in ("/quests", "/challenges", "/leaderboard", "/notifications"):
        assert client.get(route).status_code == 200, route
