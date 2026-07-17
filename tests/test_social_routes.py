"""Route tests for the social blueprint (app/blueprints/social.py).

Arkadaşlık yaşam döngüsü (istek/kabul/ret/yeniden istek), arkadaş arama,
sohbet (okundu işaretleme dahil) ve öneri kabulündeki makro hattı
(FatSecret + LLM katmanları mock'lu).

    python -m pytest tests/test_social_routes.py -v
"""
from datetime import datetime, timedelta

import pytest

from app.blueprints import social as social_bp
from app.extensions import db
from app.models import (
    Friendship, MealLog, Message, Notification, PumpCheck, PumpCheckComment,
    User,
)
from app.services.pump_checks import can_view_pump_check


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


def test_friends_search_rate_limited(client, auth_user):
    """Kullanıcı-adı sayımına karşı /friends/search 429 ile sınırlanır."""
    from app.extensions import limiter

    limiter.reset()
    limiter.enabled = True
    try:
        for _ in range(20):                 # SEARCH_RATELIMIT: 20 per minute
            assert client.get("/friends/search?q=ab").status_code == 200
        blocked = client.get("/friends/search?q=ab")
        assert blocked.status_code == 429
        assert "Çok fazla deneme" in blocked.get_json()["error"]
    finally:
        limiter.enabled = False
        limiter.reset()


def test_friend_requests_are_rate_limited_per_user(client, auth_user):
    from app.extensions import limiter

    limiter.reset()
    limiter.enabled = True
    try:
        for _ in range(20):
            assert client.post("/friend/request/bulunamadi").status_code == 404
        blocked = client.post("/friend/request/bulunamadi")
        assert blocked.status_code == 429
        assert "Çok fazla deneme" in blocked.get_json()["error"]
    finally:
        limiter.enabled = False
        limiter.reset()


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
    # Cooldown süresinden eski bir reddedilme: yeniden istek serbest.
    db.session.add(Friendship(sender_id=other.id, receiver_id=auth_user.id,
                              status="rejected",
                              created_at=datetime.utcnow() - timedelta(hours=25)))
    db.session.commit()

    assert client.post("/friend/request/once_reddetti").status_code == 200
    fr = Friendship.query.one()
    assert fr.status == "pending"
    assert fr.sender_id == auth_user.id   # yön yeni istek sahibine döner


def test_friend_request_after_recent_rejection_blocked(client, auth_user, make_user):
    # Yeni reddedilmiş bir istek cooldown içinde yeniden atılamaz (nuisance re-spam
    # koruması) → 429 ve kayıt 'rejected' kalır.
    other = make_user("yeni_reddetti")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=other.id,
                              status="rejected",
                              created_at=datetime.utcnow() - timedelta(minutes=5)))
    db.session.commit()

    resp = client.post("/friend/request/yeni_reddetti")
    assert resp.status_code == 429
    assert Friendship.query.one().status == "rejected"


def test_friend_request_rejected_null_created_at_not_blocked(client, auth_user, make_user):
    # created_at NULL (eski kayıt) ise cooldown UYGULANMAMALI — eksik zaman damgası
    # meşru yeniden denemeyi sonsuza dek bloklamamalı (geri dönük cooldown bug'ı).
    other = make_user("zamansiz_red")
    fr = Friendship(sender_id=other.id, receiver_id=auth_user.id, status="rejected")
    db.session.add(fr)
    db.session.commit()
    # created_at kolon-default'u INSERT'te dolduğu için NULL'ı UPDATE ile zorla.
    fr.created_at = None
    db.session.commit()
    assert Friendship.query.one().created_at is None   # önkoşul: gerçekten NULL

    assert client.post("/friend/request/zamansiz_red").status_code == 200
    assert Friendship.query.one().status == "pending"


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


def test_friend_accept_foreign_request_returns_404(client, auth_user, make_user):
    sender = make_user("istekci_foreign")
    other_receiver = make_user("alici_foreign")
    fr = Friendship(sender_id=sender.id, receiver_id=other_receiver.id, status="pending")
    db.session.add(fr)
    db.session.commit()

    assert client.post(f"/friend/accept/{fr.id}").status_code == 404


def test_friend_reject(client, auth_user, make_user):
    sender = make_user("istekci")
    fr = Friendship(sender_id=sender.id, receiver_id=auth_user.id, status="pending")
    db.session.add(fr)
    db.session.commit()

    assert client.post(f"/friend/reject/{fr.id}").status_code == 200
    db.session.expire_all()
    assert db.session.get(Friendship, fr.id).status == "rejected"
    assert client.post(f"/friend/reject/{fr.id}").status_code == 400


def test_friend_reject_foreign_request_returns_404(client, auth_user, make_user):
    sender = make_user("red_sender_foreign")
    other_receiver = make_user("red_receiver_foreign")
    fr = Friendship(sender_id=sender.id, receiver_id=other_receiver.id, status="pending")
    db.session.add(fr)
    db.session.commit()

    assert client.post(f"/friend/reject/{fr.id}").status_code == 404


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
    # GET artık okundu İŞARETLEMEZ — durum değiştiren işlem CSRF-korumalı POST'a
    # taşındı (state-changing route'u GET olarak açma kuralı).
    assert Message.query.filter_by(sender_id=friend.id).one().is_read is False
    # Okundu işareti yalnızca POST /chat/<username>/read ile.
    assert client.post("/chat/arkadas/read").status_code == 200
    assert Message.query.filter_by(sender_id=friend.id).one().is_read is True


def test_chat_mark_read_requires_friend_and_post(client, auth_user, make_user):
    yabanci = make_user("yabanci_okundu")
    # Arkadaş değil → 403; GET ise route yok (yalnızca POST) → 405.
    assert client.post("/chat/yabanci_okundu/read").status_code == 403
    assert client.get("/chat/yabanci_okundu/read").status_code == 405


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


def test_respond_suggestion_foreign_message_returns_404(client, auth_user, make_user):
    sender = make_user("onerici_foreign")
    other_receiver = make_user("onerilen_foreign")
    msg = Message(sender_id=sender.id, receiver_id=other_receiver.id,
                  body="öneri", message_type="suggestion_workout")
    db.session.add(msg)
    db.session.commit()

    assert client.post(f"/suggest/respond/{msg.id}",
                       json={"action": "accept"}).status_code == 404


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
    monkeypatch.setattr(social_bp, "_estimate_macros_llm", lambda items, category_map=None: {
        "gizemli yemek": {"calories": 400.0, "protein": 20.0, "carbs": 30.0, "fat": 20.0}})

    msg = _send_suggestion(client, friend, auth_user, "suggestion_meal", "gizemli yemek")
    body = client.post(f"/suggest/respond/{msg.id}", json={"action": "accept"}).get_json()
    assert body["nutrients"]["kalori"] == 400.0


def test_accept_meal_suggestion_clamps_implausible_macros(client, auth_user, friend, monkeypatch):
    # C2: öneri-kabul yolu kanonik MealLog'a yazan TEK clamp'siz yoldu — LLM/
    # FatSecret aykırı değeri (örn. 9000 kcal) defteri bozmadan önce diğer tüm
    # yollarla aynı fiziksel-sağlık kapısından (clamp_serving_macros) geçmeli.
    monkeypatch.setattr(social_bp, "_parse_suggestion_items", lambda body: ["dev porsiyon"])

    def no_token():
        raise RuntimeError("fatsecret down")
    monkeypatch.setattr(social_bp, "_get_fatsecret_token", no_token)
    monkeypatch.setattr(social_bp, "_estimate_macros_llm", lambda items, category_map=None: {
        "dev porsiyon": {"calories": 9000.0, "protein": 800.0, "carbs": 900.0, "fat": 500.0}})

    msg = _send_suggestion(client, friend, auth_user, "suggestion_meal", "dev porsiyon")
    body = client.post(f"/suggest/respond/{msg.id}", json={"action": "accept"}).get_json()

    meal = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert meal.kalori <= 3000 and meal.protein <= 300 and meal.karb <= 300 and meal.yag <= 150
    assert body["nutrients"]["kalori"] == meal.kalori


def test_accept_meal_suggestion_unparseable_body_skips_meallog(client, auth_user, friend, monkeypatch):
    # Gövde ayrıştırılamıyorsa kanonik MealLog defterine SIFIR-makro satırı YAZILMAZ
    # (aksi halde günlük toplamlar/protein nudge/haftalık rapor sessizce bozulur).
    monkeypatch.setattr(social_bp, "_parse_suggestion_items", lambda body: [])
    msg = _send_suggestion(client, friend, auth_user, "suggestion_meal", "??!!")
    body = client.post(f"/suggest/respond/{msg.id}", json={"action": "accept"}).get_json()
    assert "nutrients" not in body
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


def test_accept_meal_suggestion_all_zero_macros_skips_meallog(client, auth_user, friend, monkeypatch):
    # Makro hattı + LLM yedeği ikisi de boş dönüp toplam tümüyle sıfır kalırsa,
    # yine sıfır-makro satırı yazmamalı (kanonik defter bozulmaz).
    monkeypatch.setattr(social_bp, "_parse_suggestion_items", lambda body: ["gizem"])

    def no_token():
        raise RuntimeError("fatsecret down")
    monkeypatch.setattr(social_bp, "_get_fatsecret_token", no_token)
    monkeypatch.setattr(social_bp, "_estimate_macros_llm",
                        lambda items, category_map=None: {})

    msg = _send_suggestion(client, friend, auth_user, "suggestion_meal", "gizem")
    body = client.post(f"/suggest/respond/{msg.id}", json={"action": "accept"}).get_json()
    assert "nutrients" not in body
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


def test_friends_page_renders(client, auth_user):
    assert client.get("/friends").status_code == 200


# ---------------------------------------------------------------------------
# Bildirim tetikleyicileri (Sprint 5 PR1)
# ---------------------------------------------------------------------------

def _feed_check(owner_id):
    """Sahibinin arkadaşlarına görünür bir pump check ekle."""
    check = PumpCheck(user_id=owner_id, visibility="feed")
    db.session.add(check)
    db.session.commit()
    return check.id


def test_like_creates_notification_for_owner(client, auth_user, make_user, login):
    bob = make_user("bob")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=bob.id,
                              status="accepted"))
    owner_id = auth_user.id
    check_id = _feed_check(owner_id)

    login("bob")                                      # oturumu bob'a geçir
    assert client.post(f"/pump-check/{check_id}/like").status_code == 200

    n = Notification.query.filter_by(user_id=owner_id, ntype="pump_check_like").one()
    assert n.actor_id == bob.id
    assert n.target_type == "pump_check" and n.target_id == check_id


def test_self_like_creates_no_notification(client, auth_user):
    check_id = _feed_check(auth_user.id)
    assert client.post(f"/pump-check/{check_id}/like").status_code == 200
    assert Notification.query.filter_by(ntype="pump_check_like").count() == 0


def test_comment_creates_notification_for_owner(client, auth_user, make_user, login):
    bob = make_user("bob")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=bob.id,
                              status="accepted"))
    owner_id = auth_user.id
    check_id = _feed_check(owner_id)

    login("bob")
    assert client.post(f"/pump-check/{check_id}/comments",
                       json={"body": "harika"}).status_code == 200

    n = Notification.query.filter_by(user_id=owner_id, ntype="pump_check_comment").one()
    assert n.actor_id == bob.id


def test_friend_request_creates_notification(client, auth_user, make_user):
    target = make_user("hedef")
    assert client.post("/friend/request/hedef").status_code == 200
    fr = Friendship.query.one()
    n = Notification.query.filter_by(user_id=target.id, ntype="friend_request").one()
    assert n.actor_id == auth_user.id and n.target_id == fr.id


def test_friend_request_reuse_after_rejection_notifies(client, auth_user, make_user):
    other = make_user("once_red")
    db.session.add(Friendship(sender_id=other.id, receiver_id=auth_user.id,
                              status="rejected",
                              created_at=datetime.utcnow() - timedelta(hours=25)))
    db.session.commit()

    assert client.post("/friend/request/once_red").status_code == 200
    n = Notification.query.filter_by(user_id=other.id, ntype="friend_request").one()
    assert n.actor_id == auth_user.id


def test_friend_accept_creates_notification_for_sender(client, auth_user, make_user):
    sender = make_user("istekci")
    fr = Friendship(sender_id=sender.id, receiver_id=auth_user.id, status="pending")
    db.session.add(fr)
    db.session.commit()

    assert client.post(f"/friend/accept/{fr.id}").status_code == 200
    n = Notification.query.filter_by(user_id=sender.id, ntype="friend_accept").one()
    assert n.actor_id == auth_user.id


# ---------------------------------------------------------------------------
# Arkadaşlıktan çıkarma + giden istek iptali (Sprint 5 PR1)
# ---------------------------------------------------------------------------

def test_unfriend_revokes_visibility_and_allows_rerequest(client, auth_user, make_user):
    other = make_user("eski_dost")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=other.id,
                              status="accepted"))
    check = PumpCheck(user_id=other.id, visibility="feed")
    db.session.add(check)
    db.session.commit()
    check_id = check.id
    assert can_view_pump_check(auth_user.id, check) is True   # önce görünür

    assert client.delete("/friend/eski_dost").status_code == 200
    assert client.delete("/friend/eski_dost").status_code == 404   # artık arkadaş değil

    db.session.expire_all()
    check = db.session.get(PumpCheck, check_id)
    assert can_view_pump_check(auth_user.id, check) is False  # sızıntı regresyonu: erişim düştü

    # Satır silindiği için yeniden istek serbest (rejected-cooldown uygulanmaz).
    assert client.post("/friend/request/eski_dost").status_code == 200


def test_unfriend_unknown_user_404(client, auth_user):
    assert client.delete("/friend/yokboyle").status_code == 404


def test_cancel_outgoing_request_sweeps_ghost_notification(client, auth_user, make_user):
    target = make_user("giden_hedef")
    assert client.post("/friend/request/giden_hedef").status_code == 200
    fr_id = Friendship.query.one().id   # silme sonrası erişmemek için önden yakala
    assert Notification.query.filter_by(user_id=target.id,
                                        ntype="friend_request").count() == 1

    assert client.delete(f"/friend/request/{fr_id}").status_code == 200
    assert Friendship.query.count() == 0
    # Hayalet "friend_request" bildirimi aynı transaction'da süpürülür.
    assert Notification.query.filter_by(ntype="friend_request",
                                        target_id=fr_id).count() == 0


def test_cancel_outgoing_only_sender_and_pending(client, auth_user, make_user):
    # Başkasının gönderdiği istek iptal edilemez (yalnızca gönderen) → 404.
    other = make_user("baskasi")
    third = make_user("ucuncu")
    foreign = Friendship(sender_id=other.id, receiver_id=third.id, status="pending")
    db.session.add(foreign)
    db.session.commit()
    assert client.delete(f"/friend/request/{foreign.id}").status_code == 404

    # Kabul edilmiş satır iptal edilemez (yalnızca pending) → 404.
    acc = make_user("kabuledilen")
    accepted = Friendship(sender_id=auth_user.id, receiver_id=acc.id, status="accepted")
    db.session.add(accepted)
    db.session.commit()
    assert client.delete(f"/friend/request/{accepted.id}").status_code == 404


# ---------------------------------------------------------------------------
# Feed V2 (Sprint 5 PR2): pump-check yorum silme matrisi + sayfalama
# ---------------------------------------------------------------------------

def _add_comment(check_id, user_id, body="yorum"):
    c = PumpCheckComment(pump_check_id=check_id, user_id=user_id, body=body)
    db.session.add(c)
    PumpCheck.query.filter_by(id=check_id).update(
        {PumpCheck.comments_count: PumpCheck.comments_count + 1}, synchronize_session=False)
    db.session.commit()
    return c


def test_comment_delete_by_author(client, auth_user, friend, login):
    check_id = _feed_check(auth_user.id)
    login("arkadas")
    cid = client.post(f"/pump-check/{check_id}/comments", json={"body": "selam"}).get_json()["id"]
    dele = client.delete(f"/pump-check/{check_id}/comments/{cid}")
    assert dele.status_code == 200
    assert dele.get_json()["commentsCount"] == 0
    assert db.session.get(PumpCheckComment, cid) is None


def test_comment_delete_by_post_owner(client, auth_user, friend, login):
    check_id = _feed_check(auth_user.id)
    login("arkadas")
    cid = client.post(f"/pump-check/{check_id}/comments", json={"body": "selam"}).get_json()["id"]
    login("testuser")  # post owner
    assert client.delete(f"/pump-check/{check_id}/comments/{cid}").status_code == 200
    assert db.session.get(PumpCheckComment, cid) is None


def test_comment_delete_by_third_party_is_403(client, auth_user, friend, make_user, login):
    third = make_user("ucuncu")
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=third.id, status="accepted"))
    db.session.commit()
    check_id = _feed_check(auth_user.id)
    comment = _add_comment(check_id, friend.id)  # authored by friend
    login("ucuncu")  # neither author nor post owner (but can view feed post)
    assert client.delete(f"/pump-check/{check_id}/comments/{comment.id}").status_code == 403
    assert db.session.get(PumpCheckComment, comment.id) is not None


def test_comment_delete_on_invisible_check_is_403(client, auth_user, make_user, login):
    owner = make_user("sahip")
    check_id = _feed_check(owner.id)  # owner is NOT a friend of auth_user → not visible
    comment = _add_comment(check_id, owner.id)
    # auth_user (testuser) is logged in via fixture; cannot view owner's feed post.
    assert client.delete(f"/pump-check/{check_id}/comments/{comment.id}").status_code == 403


def test_comment_double_delete_is_404(client, auth_user):
    check_id = _feed_check(auth_user.id)
    cid = client.post(f"/pump-check/{check_id}/comments", json={"body": "x"}).get_json()["id"]
    assert client.delete(f"/pump-check/{check_id}/comments/{cid}").status_code == 200
    assert client.delete(f"/pump-check/{check_id}/comments/{cid}").status_code == 404
    # floor-0: sayaç negatife düşmez
    assert db.session.get(PumpCheck, check_id).comments_count == 0


def test_comment_pagination_newest_first_no_dup(client, auth_user):
    check_id = _feed_check(auth_user.id)
    ids = [_add_comment(check_id, auth_user.id, body="c%d" % i).id for i in range(5)]
    seen = []
    before = 0
    for _ in range(10):
        url = f"/pump-check/{check_id}/comments?limit=2" + (f"&before_id={before}" if before else "")
        data = client.get(url).get_json()
        seen.extend(row["id"] for row in data["comments"])
        assert all(row["canDelete"] for row in data["comments"])  # owner sees delete
        if not data["hasMore"]:
            break
        before = data["nextBeforeId"]
    assert sorted(seen) == sorted(ids)
    assert seen == sorted(ids, reverse=True)  # newest-first global order
