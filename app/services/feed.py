# Feed V2 servisi (Sprint 5 PR2). Feed'i sorgu-zamaninda UC keyset kaynagindan
# birlestirir: feed-gorunur PumpCheck'ler, FeedItem'lar (repost/quote), Activity
# kilometre taslari — hepsi (arkadaslar u kendisi) kapsamindan. Kronolojik siralama
# (algoritmik dikis: _rank) + kind-basi serilestirme. Goruntuleyen-basi gizli
# satirlar duser (FeedHide; FeedReport auto-hide zaten FeedHide yazar).
import base64
import logging
from datetime import datetime

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    Activity, FeedHide, FeedItem, FeedItemLike, PumpCheck, PumpCheckLike,
)
from app.services.friends import get_friend_ids
from app.services.pump_checks import can_view_pump_check, serialize_pump_check_card
from app.timeutil import display_dt

log = logging.getLogger(__name__)

# Feed'de gosterilen kilometre tasi activity turleri (allowlist; gurultu haric).
MILESTONE_ACTIVITY_TYPES = ("level_up", "streak_milestone", "new_friend",
                            "challenge_completed")

# Esit created_at'te kaynak-arasi deterministik kopmak icin sabit sira (DESC).
SOURCE_RANK = {"pump_check": 3, "feed_item": 2, "activity": 1}

# Milestone ikonlari — gamification.ACTIVITY_ICONS ile hizali (feed-yerel kopya,
# import dongusunu onlemek icin).
MILESTONE_ICONS = {
    "level_up": "\U0001f31f",
    "streak_milestone": "\U0001f525",
    "new_friend": "\U0001f91d",
    "challenge_completed": "\U0001f3c6",
}


def encode_cursor(created_at, source, item_id):
    raw = "%s|%s|%s" % (created_at.isoformat(), source, int(item_id))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor):
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        iso, source, item_id = raw.split("|")
        return (datetime.fromisoformat(iso), source, int(item_id))
    except Exception:
        return None


def _keyset_filter(created_at_col, id_col, cursor, source):
    """Bu kaynak icin '(created_at, source_rank, id) < cursor' (DESC-sonrasi) filtresi."""
    if cursor is None:
        return None
    ct, c_source, cid = cursor
    c_rank = SOURCE_RANK.get(c_source, 0)
    s_rank = SOURCE_RANK.get(source, 0)
    if s_rank < c_rank:
        return db.or_(created_at_col < ct, created_at_col == ct)
    if s_rank == c_rank:
        return db.or_(created_at_col < ct, db.and_(created_at_col == ct, id_col < cid))
    return created_at_col < ct


def _sort_key(candidate):
    ca, source, cid, _obj = candidate
    return (ca, SOURCE_RANK.get(source, 0), cid)


def get_feed_page(viewer_id, cursor=None, limit=10):
    limit = max(1, min(int(limit or 10), 30))
    decoded = decode_cursor(cursor)
    visible_ids = get_friend_ids(viewer_id) | {viewer_id}

    # 1) COLLECT — her kaynaktan limit+1, keyset filtreli.
    candidates = []  # (created_at, source, id, obj)

    pc_q = PumpCheck.query.options(joinedload(PumpCheck.user)).filter(
        PumpCheck.visibility == "feed", PumpCheck.user_id.in_(visible_ids),
    )
    f = _keyset_filter(PumpCheck.created_at, PumpCheck.id, decoded, "pump_check")
    if f is not None:
        pc_q = pc_q.filter(f)
    for pc in pc_q.order_by(PumpCheck.created_at.desc(), PumpCheck.id.desc()).limit(limit + 1).all():
        candidates.append((pc.created_at, "pump_check", pc.id, pc))

    fi_q = FeedItem.query.options(joinedload(FeedItem.user)).filter(FeedItem.user_id.in_(visible_ids))
    f = _keyset_filter(FeedItem.created_at, FeedItem.id, decoded, "feed_item")
    if f is not None:
        fi_q = fi_q.filter(f)
    for fi in fi_q.order_by(FeedItem.created_at.desc(), FeedItem.id.desc()).limit(limit + 1).all():
        candidates.append((fi.created_at, "feed_item", fi.id, fi))

    act_q = Activity.query.filter(
        Activity.user_id.in_(visible_ids),
        Activity.activity_type.in_(MILESTONE_ACTIVITY_TYPES),
    )
    f = _keyset_filter(Activity.timestamp, Activity.id, decoded, "activity")
    if f is not None:
        act_q = act_q.filter(f)
    for act in act_q.order_by(Activity.timestamp.desc(), Activity.id.desc()).limit(limit + 1).all():
        candidates.append((act.timestamp, "activity", act.id, act))

    # 2) hidden dus (goruntuleyen-basi; FeedReport auto-hide de burada kapsanir).
    hidden = {
        (h.target_type, h.target_id)
        for h in FeedHide.query.filter(FeedHide.user_id == viewer_id).all()
    }
    if hidden:
        candidates = [c for c in candidates if (c[1], c[2]) not in hidden]

    # 3) RANK — kronolojik (algoritmik dikis). En yeni once.
    candidates.sort(key=_sort_key, reverse=True)
    page = candidates[:limit]
    has_more = len(candidates) > limit

    # 4) batch goruntuleyen-durumu.
    pc_ids = [c[2] for c in page if c[1] == "pump_check"]
    feed_items = [c[3] for c in page if c[1] == "feed_item"]
    fi_ids = [fi.id for fi in feed_items]
    original_ref_ids = [fi.ref_id for fi in feed_items if fi.ref_type == "pump_check"]
    all_pc_ids = set(pc_ids) | set(original_ref_ids)

    liked_pc = set()
    if all_pc_ids:
        liked_pc = {r for (r,) in db.session.query(PumpCheckLike.pump_check_id).filter(
            PumpCheckLike.user_id == viewer_id, PumpCheckLike.pump_check_id.in_(all_pc_ids)).all()}
    liked_fi = set()
    if fi_ids:
        liked_fi = {r for (r,) in db.session.query(FeedItemLike.feed_item_id).filter(
            FeedItemLike.user_id == viewer_id, FeedItemLike.feed_item_id.in_(fi_ids)).all()}
    my_reposts = set()
    if all_pc_ids:
        my_reposts = {r for (r,) in db.session.query(FeedItem.ref_id).filter(
            FeedItem.user_id == viewer_id, FeedItem.item_type == "repost",
            FeedItem.ref_type == "pump_check", FeedItem.ref_id.in_(all_pc_ids)).all()}
    originals = {}
    if original_ref_ids:
        for pc in PumpCheck.query.options(joinedload(PumpCheck.user)).filter(PumpCheck.id.in_(original_ref_ids)).all():
            originals[pc.id] = pc

    # 5) SERIALIZE — kind-basi registry.
    items = []
    for ca, source, cid, obj in page:
        if source == "pump_check":
            items.append(_serialize_pump_check(obj, viewer_id, liked_pc, my_reposts))
        elif source == "feed_item":
            if obj.item_type == "repost":
                items.append(_serialize_repost(obj, viewer_id, originals, liked_pc, my_reposts))
            else:
                items.append(_serialize_quote(obj, viewer_id, liked_fi, originals))
        else:
            items.append(_serialize_milestone(obj))

    next_cursor = None
    if has_more and page:
        ca, source, cid, _obj = page[-1]
        next_cursor = encode_cursor(ca, source, cid)
    return {"items": items, "hasMore": has_more, "nextCursor": next_cursor}


def _serialize_pump_check(pc, viewer_id, liked_pc, my_reposts):
    card = serialize_pump_check_card(
        pc, viewer_id, liked_pump_check_ids=liked_pc,
        image_visibility_preauthorized=True, reposted_ref_ids=my_reposts,
    )
    card["kind"] = "pump_check"
    card["engagement"] = {"target": {"type": "pump_check", "id": pc.id}}
    return card


def _serialize_repost(fi, viewer_id, originals, liked_pc, my_reposts):
    base = {
        "kind": "repost",
        "id": fi.id,
        "createdAt": fi.created_at.isoformat() if fi.created_at else None,
        "timePosted": display_dt(fi.created_at, "%d.%m.%Y %H:%M"),
        "reposter": {
            "userId": fi.user_id,
            "username": fi.user.username if fi.user else "",
            "userAvatar": fi.user.avatar_src if fi.user else None,
        },
    }
    original = originals.get(fi.ref_id) if fi.ref_type == "pump_check" else None
    if original is not None and can_view_pump_check(viewer_id, original):
        base["original"] = serialize_pump_check_card(
            original, viewer_id, liked_pump_check_ids=liked_pc,
            image_visibility_preauthorized=True, reposted_ref_ids=my_reposts,
        )
        base["engagement"] = {"target": {"type": "pump_check", "id": original.id}}
        base["unavailable"] = False
    else:
        base["original"] = None
        base["engagement"] = None
        base["unavailable"] = True
    return base


def _serialize_quote(fi, viewer_id, liked_fi, originals):
    original = None
    ref = originals.get(fi.ref_id) if fi.ref_type == "pump_check" else None
    if ref is not None and can_view_pump_check(viewer_id, ref):
        original = serialize_pump_check_card(ref, viewer_id, image_visibility_preauthorized=True)
    return {
        "kind": "quote",
        "id": fi.id,
        "createdAt": fi.created_at.isoformat() if fi.created_at else None,
        "timePosted": display_dt(fi.created_at, "%d.%m.%Y %H:%M"),
        "author": {
            "userId": fi.user_id,
            "username": fi.user.username if fi.user else "",
            "userAvatar": fi.user.avatar_src if fi.user else None,
        },
        "body": fi.body or "",
        "likesCount": fi.likes_count or 0,
        "commentsCount": fi.comments_count or 0,
        "likedByMe": fi.id in liked_fi,
        "original": original,
        "engagement": {"target": {"type": "feed_item", "id": fi.id}},
    }


def _serialize_milestone(act):
    return {
        "kind": "milestone",
        "id": act.id,
        "createdAt": act.timestamp.isoformat() if act.timestamp else None,
        "timePosted": display_dt(act.timestamp, "%d.%m.%Y %H:%M"),
        "userId": act.user_id,
        "activityType": act.activity_type,
        "icon": MILESTONE_ICONS.get(act.activity_type, "\U0001f3c5"),
        "content": act.content,
        "engagement": None,
    }
