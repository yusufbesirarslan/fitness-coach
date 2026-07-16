# Feed V2 (Sprint 5 PR2) — moderasyon (gizle/şikayet) route testleri.
from app.extensions import db
from app.models import FeedHide, FeedReport, Friendship, PumpCheck


def _befriend(a_id, b_id):
    db.session.add(Friendship(sender_id=a_id, receiver_id=b_id, status="accepted"))
    db.session.commit()


def _check(user_id, date_key="m1"):
    pc = PumpCheck(user_id=user_id, visibility="feed", date_key=date_key, valid=True)
    db.session.add(pc)
    db.session.commit()
    return pc


def test_hide_is_idempotent(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    body = {"target_type": "pump_check", "target_id": pc.id}
    assert client.post("/feed/hide", json=body).status_code == 200
    assert client.post("/feed/hide", json=body).status_code == 200
    assert FeedHide.query.filter_by(user_id=auth_user.id, target_type="pump_check", target_id=pc.id).count() == 1


def test_cannot_hide_own_content(app, auth_user, client):
    pc = _check(auth_user.id)
    res = client.post("/feed/hide", json={"target_type": "pump_check", "target_id": pc.id})
    assert res.status_code == 400
    assert FeedHide.query.count() == 0


def test_unhide_removes_row(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    body = {"target_type": "pump_check", "target_id": pc.id}
    client.post("/feed/hide", json=body)
    assert client.post("/feed/unhide", json=body).status_code == 200
    assert FeedHide.query.count() == 0


def test_hide_removes_item_from_feed(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    assert len(client.get("/feed/data").get_json()["items"]) == 1
    client.post("/feed/hide", json={"target_type": "pump_check", "target_id": pc.id})
    assert client.get("/feed/data").get_json()["items"] == []


def test_report_auto_hides_and_dedup(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    body = {"target_type": "pump_check", "target_id": pc.id, "reason": "spam"}
    r1 = client.post("/feed/report", json=body)
    assert r1.status_code == 200
    assert FeedReport.query.filter_by(user_id=auth_user.id, target_id=pc.id).count() == 1
    # auto-hide → feed'den düşer
    assert client.get("/feed/data").get_json()["items"] == []
    # aynı hedefi ikinci kez şikayet → 400
    assert client.post("/feed/report", json=body).status_code == 400


def test_report_invalid_type_or_reason_400(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    assert client.post("/feed/report", json={"target_type": "bogus", "target_id": pc.id, "reason": "spam"}).status_code == 400
    assert client.post("/feed/report", json={"target_type": "pump_check", "target_id": pc.id, "reason": "bogus"}).status_code == 400
    assert FeedReport.query.count() == 0


def test_hide_does_not_leak_across_viewers(app, auth_user, make_user, client, login):
    bob = make_user("bob")
    carol = make_user("carol")
    _befriend(auth_user.id, bob.id)
    _befriend(carol.id, bob.id)
    pc = _check(bob.id)
    # auth_user hides bob's check for THEMSELVES
    client.post("/feed/hide", json={"target_type": "pump_check", "target_id": pc.id})
    assert client.get("/feed/data").get_json()["items"] == []
    # carol still sees it
    login("carol")
    kinds = [(it["kind"], it["id"]) for it in client.get("/feed/data").get_json()["items"]]
    assert kinds == [("pump_check", pc.id)]
