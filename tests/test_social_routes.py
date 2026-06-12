"""Route tests for the social blueprint (app/blueprints/social.py).

Arkadaşlık yaşam döngüsü (istek/kabul/ret/yeniden istek), arkadaş arama,
sohbet (okundu işaretleme dahil) ve öneri kabulündeki makro hattı
(FatSecret + LLM katmanları mock'lu).

    python -m pytest tests/test_social_routes.py -v
"""
import pytest

from app.blueprints import social as social_bp
from app.extensions import db
from app.models import Friendship, MealLog, Message, User


@pytest.fixture
def friend(make_user, auth_user):
    other = make_user("arkadas")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=other.id,
                              status="accepted"))
    db.session.commit()
    return other


# ---------------------------------------------------------------------------
# Arkadaş listesi / arama
# ---------------------------------------------------------------------------

def test_friends_list_groups_by_status(client, auth_user, make_user):
    accepted = make_user("kabul")
    incoming = make_user("gelen")
    outgoing = make_user("giden")
    db.session.add_all([
        Friendship(sender_id=accepted.id, receiver_id=auth_user.id, status="accepted"),
        Friendship(sender_id=incoming.id, receiver_id=auth_user.id, status="pending"),
        Friendship(sender_id=auth_user.id, receiver_id=outgoing.id, status="pending"),
    ])
    db.session.commit()

    body = client.get("/friends/list").get_json()
    assert [f["username"] for f in body["friends"]] == ["kabul"]
    assert [f["username"] for f in body["incoming"]] == ["gelen"]
    assert [f["username"] for f in body["outgoing"]] == ["giden"]


def test_friends_search_excludes_self_and_reports_status(client, auth_user, make_user):
    make_user("testarayan")
    pending = make_user("testbekleyen")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=pending.id,
                              status="pending"))
    db.session.commit()

    assert client.get("/friends/search?q=t").get_json() == {"users": []}  # kısa sorgu
    users = {u["username"]: u["status"]
             for u in client.get("/friends/search?q=test").get_json()["users"]}
    assert "testuser" not in users          # kendisi listelenmez
    assert users["testarayan"] is None
    assert users["testbekleyen"] == "pending"


# ---------------------------------------------------------------------------
# Arkadaşlık isteği yaşam döngüsü
# ---------------------------------------------------------------------------

def test_friend_request_lifecycle(client, auth_user, make_user):
    make_user("hedef")
    assert client.post("/friend/request/yokboylebiri").status_code == 404
    assert client.post("/friend/request/testuser").status_code == 400  # kendine

    assert client.post("/friend/request/hedef").status_code == 200
    assert client.post("/friend/request/hedef").status_code == 400     # zaten bekliyor


def test_friend_request_after_rejection_reopens(client, auth_user, make_user):
    other = make_user("once_reddetti")
    db.session.add(Friendship(sender_id=other.id, receiver_id=auth_user.id,
                              status="rejected"))
    db.session.commit()

    assert client.post("/friend/request/once_reddetti").status_code == 200
    fr = Friendship.query.one()
    assert fr.status == "pending"
    assert fr.sender_id == auth_user.id   # yön yeni istek sahibine döner


def test_friend_accept_awards_xp_both_sides(client, auth_user, make_user, login):
    sender = make_user("istekci")
    fr = Friendship(sender_id=sender.id, receiver_id=auth_user.id, status="pending")
    db.session.add(fr)
    db.session.commit()

    response = client.post(f"/friend/accept/{fr.id}")
    assert "+50 XP" in response.get_json()["message"]
    db.session.expire_all()
    assert db.session.get(Friendship, fr.id).status == "accepted"
    assert db.session.get(User, sender.id).rank_points == 50
    assert db.session.get(User, auth_user.id).rank_points == 50

    assert client.post(f"/friend/accept/{fr.id}").status_code == 400  # zaten işlenmiş


def test_friend_reject(client, auth_user, make_user):
    sender = make_user("istekci")
    fr = Friendship(sender_id=sender.id, receiver_id=auth_user.id, status="pending")
    db.session.add(fr)
    db.session.commit()

    assert client.post(f"/friend/reject/{fr.id}").status_code == 200
    db.session.expire_all()
    assert db.session.get(Friendship, fr.id).status == "rejected"
    assert client.post(f"/friend/reject/{fr.id}").status_code == 400


# ---------------------------------------------------------------------------
# Sohbet
# ---------------------------------------------------------------------------

def test_chat_page_redirects_non_friend(client, auth_user, make_user):
    make_user("yabanci")
    response = client.get("/chat/yabanci")
    assert response.status_code == 302


def test_chat_page_renders_for_friend(client, auth_user, friend):
    assert client.get("/chat/arkadas").status_code == 200


def test_chat_send_and_read_cycle(client, auth_user, friend):
    assert client.post("/chat/arkadas/send", json={"body": "  "}).status_code == 400
    assert client.post("/chat/arkadas/send",
                       json={"body": "x" * 2001}).status_code == 400

    sent = client.post("/chat/arkadas/send",
                       json={"body": "selam", "message_type": "sacma_tip"})
    assert sent.get_json()["message_type"] == "text"  # bilinmeyen tip → text

    db.session.add(Message(sender_id=friend.id, receiver_id=auth_user.id,
                           body="cevap", is_read=False))
    db.session.commit()

    body = client.get("/chat/arkadas/messages").get_json()
    assert [(m["body"], m["is_mine"]) for m in body["messages"]] == \
        [("selam", True), ("cevap", False)]
    # Thread açıldığında karşı tarafın mesajı okundu işaretlenir.
    incoming = Message.query.filter_by(sender_id=friend.id).one()
    assert incoming.is_read is True


# ---------------------------------------------------------------------------
# Öneriler
# ---------------------------------------------------------------------------

def test_suggestion_validation(client, auth_user, friend):
    assert client.post("/suggest/arkadas",
                       json={"type": "yanlis", "body": "x"}).status_code == 400
    assert client.post("/suggest/arkadas",
                       json={"type": "suggestion_meal", "body": ""}).status_code == 400


def _send_suggestion(client, friend_user, auth_user, stype="suggestion_workout",
                     body="öneri içerik"):
    msg = Message(sender_id=friend_user.id, receiver_id=auth_user.id,
                  body=body, message_type=stype)
    db.session.add(msg)
    db.session.commit()
    return msg


def test_respond_validation(client, auth_user, friend):
    text = Message(sender_id=friend.id, receiver_id=auth_user.id,
                   body="merhaba", message_type="text")
    db.session.add(text)
    db.session.commit()
    assert client.post(f"/suggest/respond/{text.id}",
                       json={"action": "accept"}).status_code == 400   # öneri değil

    msg = _send_suggestion(client, friend, auth_user)
    assert client.post(f"/suggest/respond/{msg.id}",
                       json={"action": "belki"}).status_code == 400    # geçersiz işlem


def test_decline_suggestion(client, auth_user, friend):
    msg = _send_suggestion(client, friend, auth_user)
    body = client.post(f"/suggest/respond/{msg.id}", json={"action": "decline"}).get_json()
    assert body["new_type"] == "suggestion_workout_declined"


def test_accept_workout_suggestion_sends_reply(client, auth_user, friend):
    msg = _send_suggestion(client, friend, auth_user)
    body = client.post(f"/suggest/respond/{msg.id}", json={"action": "accept"}).get_json()
    assert body["new_type"] == "suggestion_workout_accepted"
    reply = Message.query.filter_by(sender_id=auth_user.id, receiver_id=friend.id).one()
    assert "kabul ettim" in reply.body


def test_accept_meal_suggestion_logs_meal_with_macros(client, auth_user, friend, monkeypatch):
    monkeypatch.setattr(social_bp, "_parse_suggestion_items",
                        lambda body: ["tavuk", "pilav"])
    monkeypatch.setattr(social_bp, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(social_bp, "_lookup_macros_fatsecret", lambda items, token: (
        {"tavuk": {"calories": 330.0, "protein": 62.0, "carbs": 0.0, "fat": 7.0}},
        {"pilav": {"calories": 130.0, "protein": 2.7, "carbs": 28.0, "fat": 0.3}},
    ))
    monkeypatch.setattr(social_bp, "_estimate_serving_weights_llm",
                        lambda names: {"pilav": 200.0})

    msg = _send_suggestion(client, friend, auth_user, "suggestion_meal",
                           "tavuk + pilav yemelisin")
    body = client.post(f"/suggest/respond/{msg.id}", json={"action": "accept"}).get_json()

    assert body["nutrients"]["kalori"] == 330.0 + 260.0  # pilav 100g→200g ölçeklendi
    assert "590 kcal eklendi" in body["message"]

    meal = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert meal.yemekler == "tavuk, pilav"
    assert "arkadas" in meal.ogun  # öğün başlığı gönderenin adını taşır


def test_accept_meal_suggestion_llm_fallback_for_missing(client, auth_user, friend, monkeypatch):
    monkeypatch.setattr(social_bp, "_parse_suggestion_items", lambda body: ["gizemli yemek"])

    def no_token():
        raise RuntimeError("fatsecret down")
    monkeypatch.setattr(social_bp, "_get_fatsecret_token", no_token)
    monkeypatch.setattr(social_bp, "_estimate_macros_llm", lambda items: {
        "gizemli yemek": {"calories": 400.0, "protein": 20.0, "carbs": 30.0, "fat": 20.0}})

    msg = _send_suggestion(client, friend, auth_user, "suggestion_meal", "gizemli yemek")
    body = client.post(f"/suggest/respond/{msg.id}", json={"action": "accept"}).get_json()
    assert body["nutrients"]["kalori"] == 400.0


def test_accept_meal_suggestion_unparseable_body_logs_raw(client, auth_user, friend, monkeypatch):
    monkeypatch.setattr(social_bp, "_parse_suggestion_items", lambda body: [])
    msg = _send_suggestion(client, friend, auth_user, "suggestion_meal", "??!!")
    body = client.post(f"/suggest/respond/{msg.id}", json={"action": "accept"}).get_json()
    assert "nutrients" not in body
    meal = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert meal.kalori == 0


def test_friends_page_renders(client, auth_user):
    assert client.get("/friends").status_code == 200
