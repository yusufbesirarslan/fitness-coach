"""Route tests for app/blueprints/notifications.py (Sprint 5 PR1).

Auth kapısı, keyset (before_id) sayfalama, okunmamış sayacı, mark-read
user_id scope'u + mark-all, geçersiz gövde 400 ve CSRF 403.

    python -m pytest tests/test_notification_routes.py -v
"""
from app.extensions import db
from app.models import Notification
from app.services.notifications import notify


def _seed(recipient_id, actor_id, count):
    """`count` farklı hedefli okunmamış bildirim ekler (dedup tetiklenmez)."""
    made = []
    for i in range(count):
        made.append(notify(recipient_id, "pump_check_like", actor_id=actor_id,
                           target_type="pump_check", target_id=i + 1))
    db.session.commit()
    return [n.id for n in made]


def test_data_requires_auth(client):
    resp = client.get("/notifications/data")
    assert resp.status_code in (301, 302, 401)


def test_data_keyset_pagination_no_dup_no_skip(client, auth_user, make_user):
    bob = make_user("bob")
    _seed(auth_user.id, bob.id, 5)

    p1 = client.get("/notifications/data?limit=2").get_json()
    assert len(p1["notifications"]) == 2
    assert p1["hasMore"] is True
    assert p1["unreadCount"] == 5
    before = p1["nextBeforeId"]
    assert before is not None

    p2 = client.get(f"/notifications/data?limit=2&before_id={before}").get_json()
    assert len(p2["notifications"]) == 2

    p3 = client.get(
        f"/notifications/data?limit=2&before_id={p2['nextBeforeId']}").get_json()
    assert len(p3["notifications"]) == 1
    assert p3["hasMore"] is False
    assert p3["nextBeforeId"] is None

    ids = [n["id"] for n in p1["notifications"] + p2["notifications"] + p3["notifications"]]
    assert len(ids) == len(set(ids))                 # çakışma yok
    assert ids == sorted(ids, reverse=True)          # atlama yok, azalan id


def test_data_limit_clamped(client, auth_user, make_user):
    bob = make_user("bob")
    _seed(auth_user.id, bob.id, 3)
    # limit üst sınırı 50; geçersiz/aşırı değerler kırmaz.
    assert client.get("/notifications/data?limit=999").status_code == 200
    assert client.get("/notifications/data?limit=abc").status_code == 200


def test_unread_count(client, auth_user, make_user):
    bob = make_user("bob")
    _seed(auth_user.id, bob.id, 3)
    assert client.get("/notifications/unread-count").get_json()["count"] == 3


def test_mark_read_scoped_to_own_rows(client, auth_user, make_user):
    # testuser, BAŞKASININ bildirim id'sini okuyamaz (WHERE user_id scope'u).
    bob = make_user("bob")
    bob_n = notify(bob.id, "pump_check_like", actor_id=auth_user.id, target_id=99)
    db.session.commit()

    resp = client.post("/notifications/read", json={"ids": [bob_n.id]})
    assert resp.status_code == 200
    db.session.expire_all()
    assert db.session.get(Notification, bob_n.id).is_read is False


def test_mark_all(client, auth_user, make_user):
    bob = make_user("bob")
    _seed(auth_user.id, bob.id, 3)
    resp = client.post("/notifications/read", json={"all": True})
    assert resp.status_code == 200
    assert resp.get_json()["unreadCount"] == 0
    assert client.get("/notifications/unread-count").get_json()["count"] == 0


def test_mark_read_invalid_body_returns_400(client, auth_user):
    assert client.post("/notifications/read", json={}).status_code == 400
    assert client.post("/notifications/read", json={"ids": []}).status_code == 400
    assert client.post("/notifications/read", json={"ids": ["x"]}).status_code == 400


def test_read_requires_csrf(raw_client):
    # raw_client Origin/token EKLEMEZ → CSRF before_request kapısı 403 (auth'tan önce).
    assert raw_client.post("/notifications/read", json={"all": True}).status_code == 403


def test_page_renders(client, auth_user):
    assert client.get("/notifications").status_code == 200
