# Feed V2 (Sprint 5 PR2) — repost/quote + feed-item like/comment route testleri.
from app.extensions import db
from app.models import (
    FeedItem, FeedItemComment, FeedItemLike, Friendship, Notification, PumpCheck,
)


def _befriend(a_id, b_id):
    db.session.add(Friendship(sender_id=a_id, receiver_id=b_id, status="accepted"))
    db.session.commit()


def _check(user_id, visibility="feed", date_key="c1", **kw):
    pc = PumpCheck(user_id=user_id, visibility=visibility, date_key=date_key, **kw)
    db.session.add(pc)
    db.session.commit()
    return pc


# ── repost / quote ────────────────────────────────────────────────────────────

def test_repost_happy_path_bumps_count_and_notifies_owner(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    res = client.post("/feed/repost", json={"ref_type": "pump_check", "ref_id": pc.id, "mode": "repost"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["repostsCount"] == 1
    assert db.session.get(PumpCheck, pc.id).reposts_count == 1
    assert FeedItem.query.filter_by(user_id=auth_user.id, item_type="repost", ref_id=pc.id).count() == 1
    notif = Notification.query.filter_by(user_id=bob.id, ntype="repost", actor_id=auth_user.id).first()
    assert notif is not None


def test_self_repost_creates_no_notification(app, auth_user, client):
    pc = _check(auth_user.id)
    res = client.post("/feed/repost", json={"ref_id": pc.id, "mode": "repost"})
    assert res.status_code == 200
    assert Notification.query.filter_by(user_id=auth_user.id, ntype="repost").count() == 0


def test_duplicate_repost_returns_400(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    assert client.post("/feed/repost", json={"ref_id": pc.id, "mode": "repost"}).status_code == 200
    dup = client.post("/feed/repost", json={"ref_id": pc.id, "mode": "repost"})
    assert dup.status_code == 400
    assert db.session.get(PumpCheck, pc.id).reposts_count == 1  # not double-bumped


def test_repost_of_friends_only_or_private_is_403(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    fr = _check(bob.id, visibility="friends", date_key="c2", shared_friend_ids=[auth_user.id])
    pr = _check(bob.id, visibility="private", date_key="c3")
    assert client.post("/feed/repost", json={"ref_id": fr.id, "mode": "repost"}).status_code == 403
    assert client.post("/feed/repost", json={"ref_id": pr.id, "mode": "repost"}).status_code == 403


def test_repost_of_stranger_feed_check_is_403(app, auth_user, make_user, client):
    carol = make_user("carol")  # not a friend
    pc = _check(carol.id)  # feed-visible but viewer is not a friend
    assert client.post("/feed/repost", json={"ref_id": pc.id, "mode": "repost"}).status_code == 403


def test_repost_missing_original_is_404(app, auth_user, client):
    assert client.post("/feed/repost", json={"ref_id": 999999, "mode": "repost"}).status_code == 404


def test_quote_requires_body_within_limit(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    assert client.post("/feed/repost", json={"ref_id": pc.id, "mode": "quote"}).status_code == 400
    assert client.post("/feed/repost", json={"ref_id": pc.id, "mode": "quote", "body": "x" * 501}).status_code == 400
    ok = client.post("/feed/repost", json={"ref_id": pc.id, "mode": "quote", "body": "Harika!"})
    assert ok.status_code == 200
    item = FeedItem.query.filter_by(user_id=auth_user.id, item_type="quote").one()
    assert item.body == "Harika!"
    assert Notification.query.filter_by(user_id=bob.id, ntype="quote_repost").first() is not None


def test_multiple_reposts_of_distinct_posts_each_notify(app, auth_user, make_user, client):
    # Aynı aktör, aynı sahibin İKİ FARKLI gönderisini repost'larsa — ilk bildirim
    # OKUNMAMIŞ kalsa bile — ikisi de bildirilmeli (target_id=ref_id ile ayrık kimlik).
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc1 = _check(bob.id, date_key="d1")
    pc2 = _check(bob.id, date_key="d2")
    assert client.post("/feed/repost", json={"ref_id": pc1.id, "mode": "repost"}).status_code == 200
    assert client.post("/feed/repost", json={"ref_id": pc2.id, "mode": "repost"}).status_code == 200
    notifs = Notification.query.filter_by(
        user_id=bob.id, ntype="repost", actor_id=auth_user.id).all()
    assert len(notifs) == 2                                    # farklı gönderiler çakışmaz
    assert {n.target_id for n in notifs} == {pc1.id, pc2.id}


def test_multiple_quotes_of_distinct_posts_each_notify(app, auth_user, make_user, client):
    # quote_repost yolu da aynı: iki farklı gönderinin quote'u ayrı bildirim üretir.
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc1 = _check(bob.id, date_key="q1")
    pc2 = _check(bob.id, date_key="q2")
    assert client.post("/feed/repost", json={"ref_id": pc1.id, "mode": "quote", "body": "Bir"}).status_code == 200
    assert client.post("/feed/repost", json={"ref_id": pc2.id, "mode": "quote", "body": "İki"}).status_code == 200
    notifs = Notification.query.filter_by(
        user_id=bob.id, ntype="quote_repost", actor_id=auth_user.id).all()
    assert len(notifs) == 2
    assert {n.target_id for n in notifs} == {pc1.id, pc2.id}
    assert {n.payload["pumpCheckId"] for n in notifs} == {pc1.id, pc2.id}


def test_owner_deletes_repost_decrements_and_removes_children(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    client.post("/feed/repost", json={"ref_id": pc.id, "mode": "repost"})
    item = FeedItem.query.filter_by(user_id=auth_user.id, item_type="repost").one()
    res = client.delete("/feed/item/%s" % item.id)
    assert res.status_code == 200
    assert res.get_json()["repostsCount"] == 0
    assert db.session.get(FeedItem, item.id) is None
    assert db.session.get(PumpCheck, pc.id).reposts_count == 0


def test_non_owner_delete_is_404(app, auth_user, make_user, client, login):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(auth_user.id)
    login("bob")  # switch session to bob
    item = FeedItem(user_id=auth_user.id, item_type="repost", ref_type="pump_check", ref_id=pc.id)
    db.session.add(item)
    db.session.commit()
    assert client.delete("/feed/item/%s" % item.id).status_code == 404


# ── quote like / comment ──────────────────────────────────────────────────────

def _quote(client, ref_id, body="Vay!"):
    client.post("/feed/repost", json={"ref_id": ref_id, "mode": "quote", "body": body})
    return FeedItem.query.filter_by(item_type="quote", ref_id=ref_id).first()


def test_quote_like_toggle_counts_and_dedup(app, auth_user, make_user, client, login):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    quote = _quote(client, pc.id)  # authored by auth_user
    login("bob")  # bob likes auth_user's quote
    r1 = client.post("/feed/item/%s/like" % quote.id)
    assert r1.status_code == 200 and r1.get_json()["likesCount"] == 1
    # duplicate like is idempotent (no double count)
    client.post("/feed/item/%s/like" % quote.id)
    assert db.session.get(FeedItem, quote.id).likes_count == 1
    assert FeedItemLike.query.filter_by(feed_item_id=quote.id, user_id=bob.id).count() == 1
    # unlike floors at 0
    client.delete("/feed/item/%s/like" % quote.id)
    client.delete("/feed/item/%s/like" % quote.id)
    assert db.session.get(FeedItem, quote.id).likes_count == 0


def test_quote_comment_create_and_notify(app, auth_user, make_user, client, login):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    quote = _quote(client, pc.id)  # authored by auth_user
    login("bob")
    res = client.post("/feed/item/%s/comments" % quote.id, json={"body": "Süper"})
    assert res.status_code == 200 and res.get_json()["commentsCount"] == 1
    assert FeedItemComment.query.filter_by(feed_item_id=quote.id, user_id=bob.id).count() == 1
    assert Notification.query.filter_by(user_id=auth_user.id, ntype="feed_comment", actor_id=bob.id).first() is not None
