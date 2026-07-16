# Feed V2 (Sprint 5 PR2) — model + servis + /feed/data testleri.
from datetime import datetime

from app.extensions import db
from app.models import FeedItem, PumpCheck
from app.services import feed as feed_svc


def _feed_check(user_id, date_key="2026-07-16", **kw):
    pc = PumpCheck(user_id=user_id, visibility="feed", date_key=date_key, **kw)
    db.session.add(pc)
    db.session.commit()
    return pc


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
