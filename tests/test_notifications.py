"""Service tests for app/services/notifications.py (Sprint 5 PR1).

notify sözleşmesi (commit etmez, kendi-kendine bildirim yok, okunmamış-dedup),
mark_read scope'u, saklama süpürmesi ve serializer şekli.

    python -m pytest tests/test_notifications.py -v
"""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Notification
from app.services import notifications as notif_service
from app.services.notifications import (
    mark_read, notify, purge_old, serialize_notification, unread_count,
)


@pytest.fixture
def two_users(make_user):
    return make_user("alice"), make_user("bob")


def test_notify_adds_row_without_commit(app, two_users):
    alice, bob = two_users
    n = notify(alice.id, "pump_check_like", actor_id=bob.id,
               target_type="pump_check", target_id=1)
    assert n is not None
    # Henüz commit yok — rollback bildirimi de götürmeli (tetikleyen eylemle
    # aynı transaction garantisi).
    db.session.rollback()
    assert Notification.query.count() == 0

    notify(alice.id, "pump_check_like", actor_id=bob.id,
           target_type="pump_check", target_id=1)
    db.session.commit()
    assert Notification.query.count() == 1


def test_self_notification_skipped(app, two_users):
    alice, _ = two_users
    assert notify(alice.id, "pump_check_like", actor_id=alice.id) is None
    db.session.commit()
    assert Notification.query.count() == 0


def test_unread_dedup_and_renotify_after_read(app, two_users):
    alice, bob = two_users
    notify(alice.id, "pump_check_like", actor_id=bob.id,
           target_type="pump_check", target_id=7)
    db.session.commit()

    # Aynı beşli OKUNMAMIŞKEN tekrar bildirmez (beğen-geri al-beğen spam'i).
    assert notify(alice.id, "pump_check_like", actor_id=bob.id,
                  target_type="pump_check", target_id=7) is None
    db.session.commit()
    assert Notification.query.count() == 1

    # Farklı hedef yine bildirir.
    assert notify(alice.id, "pump_check_like", actor_id=bob.id,
                  target_type="pump_check", target_id=8) is not None
    db.session.commit()
    assert Notification.query.count() == 2

    # Okunduktan sonra aynı beşli YENİDEN bildirilir.
    mark_read(alice.id, mark_all=True)
    assert notify(alice.id, "pump_check_like", actor_id=bob.id,
                  target_type="pump_check", target_id=7) is not None
    db.session.commit()
    assert Notification.query.count() == 3


def test_notify_swallows_errors(app, two_users, monkeypatch):
    alice, bob = two_users

    class Boom:
        class query:
            @staticmethod
            def filter_by(**kwargs):
                raise RuntimeError("db down")

    monkeypatch.setattr(notif_service, "Notification", Boom)
    # Bildirim yazılamaması ana eylemi KIRMAMALI — exception sızmaz, None döner.
    assert notify(alice.id, "pump_check_like", actor_id=bob.id) is None


def test_unread_count_and_mark_read_scoped(app, two_users):
    alice, bob = two_users
    notify(alice.id, "friend_request", actor_id=bob.id, target_id=1)
    notify(alice.id, "friend_accept", actor_id=bob.id, target_id=2)
    notify(bob.id, "friend_request", actor_id=alice.id, target_id=3)
    db.session.commit()
    assert unread_count(alice.id) == 2
    assert unread_count(bob.id) == 1

    bob_notif = Notification.query.filter_by(user_id=bob.id).one()
    # Alice, Bob'un bildirim id'sini işaretleyemez (WHERE user_id scope'u).
    assert mark_read(alice.id, ids=[bob_notif.id]) == 0
    assert unread_count(bob.id) == 1

    # mark_all yalnızca kendi satırlarını okur.
    assert mark_read(alice.id, mark_all=True) == 2
    assert unread_count(alice.id) == 0
    assert unread_count(bob.id) == 1

    # ids verilmeden mark_all=False → hiçbir şey yapmaz.
    assert mark_read(bob.id) == 0
    assert unread_count(bob.id) == 1


def test_purge_old_boundaries(app, two_users):
    alice, bob = two_users
    now = datetime.utcnow()

    def _mk(days_old, is_read):
        n = Notification(user_id=alice.id, actor_id=bob.id, ntype="pump_check_like",
                         is_read=is_read, created_at=now - timedelta(days=days_old))
        db.session.add(n)
        return n

    kept_read = _mk(29, True)       # okunmuş, 30 günden yeni → kalır
    purged_read = _mk(31, True)     # okunmuş, 30 günden eski → silinir
    kept_unread = _mk(89, False)    # okunmamış, 90 günden yeni → kalır
    purged_unread = _mk(91, False)  # okunmamış, 90 günden eski → silinir
    db.session.commit()
    # id'leri süpürmeden ÖNCE yakala — silinen+expired ORM örneğinin .id'sine
    # erişmek commit sonrası yeniden-yükleme tetikler (silinmiş satır → hata).
    kept_ids = {kept_read.id, kept_unread.id}
    purged_ids = {purged_read.id, purged_unread.id}

    assert purge_old(now) == 2
    remaining = {n.id for n in Notification.query.all()}
    assert remaining == kept_ids
    assert remaining.isdisjoint(purged_ids)


def test_serialize_shape(app, two_users):
    alice, bob = two_users
    n = notify(alice.id, "pump_check_comment", actor_id=bob.id,
               target_type="pump_check", target_id=5, payload={"x": 1})
    db.session.commit()

    data = serialize_notification(n)
    assert data["type"] == "pump_check_comment"
    assert data["actor"]["username"] == "bob"
    assert data["targetType"] == "pump_check"
    assert data["targetId"] == 5
    assert data["payload"] == {"x": 1}
    assert data["isRead"] is False
    assert data["createdAt"]  # görüntü formatı dolu

    # Sistem bildirimi (actor yok) — actor alanı None.
    sys_n = notify(alice.id, "friend_accept")
    db.session.commit()
    assert serialize_notification(sys_n)["actor"] is None


def test_purge_content_notifications(app, two_users):
    """Sprint 5 PR3: silinen içeriğe işaret eden bildirimler süpürülür — pump_check_like/
    comment + repost/quote (target_id=KAYNAK pump check id) + feed_like/comment
    (target_id=FeedItem.id). ntype ile HASSAS filtreleme çakışan id'leri korur
    (pump_check.id ile feed_item.id ayrı diziler → sayısal çakışabilir)."""
    from app.services.notifications import purge_content_notifications
    alice, bob = two_users
    # Silinen içerik: pump check P=10, repost FeedItem'ları F=[20, 21]. Öksüz olacaklar:
    notify(alice.id, "pump_check_like", actor_id=bob.id, target_type="pump_check", target_id=10)
    notify(alice.id, "pump_check_comment", actor_id=bob.id, target_type="pump_check", target_id=10)
    notify(alice.id, "repost", actor_id=bob.id, target_type="feed_item", target_id=10)        # =KAYNAK pump check id
    notify(alice.id, "quote_repost", actor_id=bob.id, target_type="feed_item", target_id=10)
    notify(alice.id, "feed_like", actor_id=bob.id, target_type="feed_item", target_id=20)      # =FeedItem.id
    notify(alice.id, "feed_comment", actor_id=bob.id, target_type="feed_item", target_id=21)
    # KORUNMASI GEREKENLER:
    notify(alice.id, "pump_check_like", actor_id=bob.id, target_type="pump_check", target_id=99)  # başka pump check
    notify(alice.id, "feed_like", actor_id=bob.id, target_type="feed_item", target_id=10)      # ÇAKIŞMA: FeedItem 10 SİLİNMİYOR
    notify(alice.id, "challenge_complete", target_type="challenge", target_id=10)              # farklı target_type
    db.session.commit()
    assert Notification.query.count() == 9

    deleted = purge_content_notifications(pump_check_ids=[10], feed_item_ids=[20, 21])
    db.session.commit()
    assert deleted == 6
    kinds = {(n.ntype, n.target_type, n.target_id) for n in Notification.query.all()}
    assert kinds == {
        ("pump_check_like", "pump_check", 99),    # başka pump check korunur
        ("feed_like", "feed_item", 10),           # silinmeyen FeedItem 10 korunur (ntype+id hassasiyeti)
        ("challenge_complete", "challenge", 10),  # farklı target_type korunur
    }


def test_purge_content_notifications_noop_and_safe(app, two_users):
    # Argümansız çağrı hiçbir şey yapmaz; helper commit ETMEZ (çağıranın tx'i).
    from app.services.notifications import purge_content_notifications
    alice, bob = two_users
    notify(alice.id, "pump_check_like", actor_id=bob.id, target_type="pump_check", target_id=5)
    db.session.commit()
    assert purge_content_notifications() == 0
    # Süpürme session'a uygulanır ama commit etmez → rollback geri alır.
    assert purge_content_notifications(pump_check_ids=[5]) == 1
    db.session.rollback()
    assert Notification.query.filter_by(target_id=5).count() == 1


def test_purge_user_removes_actor_side_notifications(app, two_users):
    # _purge_user: kullanıcının ALDIKLARI child-model döngüsüyle, TETİKLEDİKLERİ
    # (actor_id) açık filtreyle silinmeli — SQLite FK cascade zorlamaz.
    from app.cli import _purge_user
    alice, bob = two_users
    notify(alice.id, "pump_check_like", actor_id=bob.id, target_id=1)
    notify(bob.id, "pump_check_like", actor_id=alice.id, target_id=2)
    db.session.commit()

    _purge_user(alice)
    db.session.commit()

    assert Notification.query.filter_by(user_id=alice.id).count() == 0   # aldıkları
    assert Notification.query.filter_by(actor_id=alice.id).count() == 0  # tetikledikleri
    assert Notification.query.filter_by(user_id=bob.id).count() == 0
