# Feed V2 (Sprint 5 PR2) — model + servis + /feed/data testleri.
from datetime import datetime

from app.extensions import db
from app.models import Activity, FeedItem, Friendship, PumpCheck
from app.services import feed as feed_svc


def _feed_check(user_id, created_at=None, visibility="feed", date_key=None, **kw):
    if date_key is None:
        date_key = ("k%02d%02d" % (created_at.minute, created_at.second)) if created_at else "k0"
    pc = PumpCheck(user_id=user_id, visibility=visibility,
                   date_key=date_key, created_at=created_at, **kw)
    db.session.add(pc)
    db.session.commit()
    return pc


def _befriend(a_id, b_id):
    db.session.add(Friendship(sender_id=a_id, receiver_id=b_id, status="accepted"))
    db.session.commit()


def _repost(user_id, ref_id, created_at, item_type="repost", body=None):
    fi = FeedItem(user_id=user_id, item_type=item_type, ref_type="pump_check",
                  ref_id=ref_id, body=body, created_at=created_at)
    db.session.add(fi)
    db.session.commit()
    return fi


def _milestone(user_id, timestamp, activity_type="level_up", content="Seviye 3!"):
    act = Activity(user_id=user_id, activity_type=activity_type, content=content, timestamp=timestamp)
    db.session.add(act)
    db.session.commit()
    return act


def _dt(minute):
    return datetime(2026, 7, 16, 10, minute, 0)


def test_feed_item_and_reposts_count_persist(app, make_user):
    user = make_user("owner")
    pc = _feed_check(user.id)
    item = FeedItem(user_id=user.id, item_type="repost", ref_type="pump_check", ref_id=pc.id)
    db.session.add(item)
    PumpCheck.query.filter_by(id=pc.id).update({PumpCheck.reposts_count: PumpCheck.reposts_count + 1})
    db.session.commit()
    assert db.session.get(PumpCheck, pc.id).reposts_count == 1
    assert db.session.get(FeedItem, item.id).item_type == "repost"


def test_cursor_roundtrip_and_garbage(app):
    dt = datetime(2026, 7, 16, 12, 30, 0)
    cur = feed_svc.encode_cursor(dt, "pump_check", 42)
    assert feed_svc.decode_cursor(cur) == (dt, "pump_check", 42)
    assert feed_svc.decode_cursor("!!!not-base64!!!") is None
    assert feed_svc.decode_cursor("") is None
    assert feed_svc.decode_cursor(None) is None


def test_feed_merges_sources_and_excludes_strangers(app, auth_user, make_user, client):
    bob = make_user("bob")
    carol = make_user("carol")  # NOT a friend
    _befriend(auth_user.id, bob.id)
    a = _feed_check(auth_user.id, created_at=_dt(1))         # own pump check
    b = _feed_check(bob.id, created_at=_dt(2))               # friend pump check
    _feed_check(carol.id, created_at=_dt(3))                 # stranger — must be absent
    rp = _repost(bob.id, a.id, _dt(4))                       # friend reposts my check
    ms = _milestone(bob.id, _dt(5))                          # friend milestone

    res = client.get("/feed/data")
    assert res.status_code == 200
    data = res.get_json()
    kinds = [(it["kind"], it["id"]) for it in data["items"]]
    # newest-first: milestone(5), repost(4), bob check(2), own check(1)
    assert kinds == [("milestone", ms.id), ("repost", rp.id), ("pump_check", b.id), ("pump_check", a.id)]
    assert data["hasMore"] is False


def test_feed_cursor_no_dup_no_skip(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    ids = []
    for m in range(1, 6):
        ids.append(("pump_check", _feed_check(bob.id, created_at=_dt(m)).id))

    seen = []
    cursor = None
    for _ in range(10):
        url = "/feed/data?per_page=2" + (("&cursor=" + cursor) if cursor else "")
        data = client.get(url).get_json()
        seen.extend((it["kind"], it["id"]) for it in data["items"])
        cursor = data["nextCursor"]
        if not data["hasMore"]:
            break
    assert sorted(seen) == sorted(ids)            # every id exactly once
    assert len(seen) == len(set(seen))            # no duplicates


def test_feed_hides_restricted_and_repost_stub(app, auth_user, make_user, client):
    bob = make_user("bob")
    carol = make_user("carol")  # stranger
    _befriend(auth_user.id, bob.id)
    # friends-only / private posts by friend are NOT in feed (feed == visibility 'feed').
    _feed_check(bob.id, created_at=_dt(1), visibility="friends", shared_friend_ids=[auth_user.id])
    _feed_check(bob.id, created_at=_dt(2), visibility="private")
    # stranger milestone absent
    _milestone(carol.id, _dt(3))
    # bob reposts a stranger's (unviewable) pump check → unavailable stub
    stranger_check = _feed_check(carol.id, created_at=_dt(4))
    rp = _repost(bob.id, stranger_check.id, _dt(5))

    data = client.get("/feed/data").get_json()
    kinds = [(it["kind"], it["id"]) for it in data["items"]]
    assert kinds == [("repost", rp.id)]           # only bob's repost is visible
    stub = data["items"][0]
    assert stub["unavailable"] is True
    assert stub["original"] is None
    assert stub["engagement"] is None
