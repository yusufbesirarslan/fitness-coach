# Sosyal bildirim servisi (Sprint 5 PR1).
#
# Sözleşme (quest/_claim_quest ile aynı): notify() satırı SESSION'a EKLER,
# COMMIT ETMEZ — tetikleyen eylemle (beğeni, yorum, istek) aynı transaction'da
# atomik yazılır; eylem rollback olursa bildirim de gitmez.
#
# Görünen metin sunucuda ÜRETİLMEZ: ntype kanonik İngilizce slug'dır, istemci
# i18n "notif.<ntype>" anahtarından metni kurar (i18n display-map kuralı).
import logging
from datetime import datetime, timedelta

from app.extensions import db
from app.models import Notification
from app.timeutil import display_dt

log = logging.getLogger(__name__)

# Saklama politikası: okunmuşlar 30 gün, her şey en geç 90 gün sonra silinir.
# Günlük self-heal purge hooks.maybe_weekly_rollover'daki mevcut throttle
# bloğuna bağlıdır (yeni anahtar/cron yok).
PRUNE_READ_DAYS = 30
PRUNE_ALL_DAYS = 90


def notify(user_id, ntype, actor_id=None, target_type=None, target_id=None, payload=None):
    """Alıcıya bildirim satırı ekle (commit etmez). Döndürür: Notification | None.

    - Kendi eylemi için bildirim yazılmaz (actor == alıcı).
    - Aynı (alıcı, aktör, tür, hedef) beşlisi için OKUNMAMIŞ satır varsa
      yenisi yazılmaz (beğen-geri al-beğen spam'i); alıcı okuduysa yeni
      olay yeniden bildirilir.
    - Asla exception sızdırmaz: bildirim yazılamaması ana eylemi KIRMAZ.
    """
    try:
        if actor_id is not None and actor_id == user_id:
            return None
        # no_autoflush: dedup SELECT'i, ÇAĞIRANIN henüz commit'lenmemiş bekleyen
        # INSERT'lerini erken FLUSH etmemeli. Aksi halde tetikleyen eylemin UNIQUE
        # ihlali (ör. çift-beğeni yarışında pump_check_like) bu SELECT'in
        # autoflush'ında patlar, notify onu yutar ama session PendingRollbackError'a
        # düşer → çağıranın `except IntegrityError` bloğu yerine commit beklenmedik
        # hata verir. no_autoflush ile ihlal, çağıranın kendi commit'inde yüzeye
        # çıkar ve orada zaten ele alınır.
        with db.session.no_autoflush:
            exists = Notification.query.filter_by(
                user_id=user_id,
                actor_id=actor_id,
                ntype=ntype,
                target_type=target_type,
                target_id=target_id,
                is_read=False,
            ).first()
        if exists:
            return None
        n = Notification(
            user_id=user_id,
            actor_id=actor_id,
            ntype=ntype,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
        )
        db.session.add(n)
        return n
    except Exception:
        log.warning("notify başarısız (yutuldu): user=%s ntype=%s", user_id, ntype, exc_info=True)
        return None


def purge_content_notifications(pump_check_ids=None, feed_item_ids=None):
    """Silinen içeriğe İŞARET EDEN bildirimleri sil (öksüz-hedef temizliği).

    Bir pump check kalıcı silinince (ör. `POST` yerine `DELETE /pump-check-gallery`)
    onu ve ona atıfta bulunan repost/quote FeedItem'larını hedef alan bildirimler
    ÖLÜ HEDEFE düşer — tıklanınca "içerik yok" stub'ı açılırdı. Bunları session'a
    uygulanan tek DELETE ile süpürür. **COMMIT ETMEZ** (çağıranın silme
    transaction'ında atomik — `notify` sözleşmesiyle aynı). Asla exception sızdırmaz.
    Döndürür: silinen satır sayısı.

    Hedef kimliği bildirim TÜRÜNE göre farklı tabloya işaret eder (pump_check.id ve
    feed_item.id ÇAKIŞABİLİR — ayrı diziler), bu yüzden ntype ile HASSAS filtrelenir:
    - `pump_check_like` / `pump_check_comment` → target_type='pump_check',
      target_id = pump check id.
    - `repost` / `quote_repost` → target_type='feed_item' AMA target_id = KAYNAK
      pump check id (PR2 dedup kimliği; FeedItem.id commit öncesi bilinmiyordu —
      bkz. dedup notu). Bu yüzden bu iki tür `pump_check_ids` ile eşlenir.
    - `feed_like` / `feed_comment` → target_type='feed_item', target_id = FeedItem.id
      → `feed_item_ids` ile eşlenir.
    """
    try:
        clauses = []
        if pump_check_ids:
            clauses.append(db.and_(
                Notification.target_type == "pump_check",
                Notification.target_id.in_(pump_check_ids)))
            clauses.append(db.and_(
                Notification.ntype.in_(("repost", "quote_repost")),
                Notification.target_id.in_(pump_check_ids)))
        if feed_item_ids:
            clauses.append(db.and_(
                Notification.ntype.in_(("feed_like", "feed_comment")),
                Notification.target_id.in_(feed_item_ids)))
        if not clauses:
            return 0
        return Notification.query.filter(db.or_(*clauses)).delete(
            synchronize_session=False)
    except Exception:
        log.warning("purge_content_notifications başarısız (yutuldu)", exc_info=True)
        return 0


def unread_count(user_id):
    return Notification.query.filter_by(user_id=user_id, is_read=False).count()


def mark_read(user_id, ids=None, mark_all=False):
    """Bildirimleri okundu işaretle — SADECE kendi satırları (WHERE user_id).

    Döndürür: etkilenen satır sayısı. Commit EDER (kendi başına uç nokta eylemi).
    """
    q = Notification.query.filter_by(user_id=user_id, is_read=False)
    if not mark_all:
        if not ids:
            return 0
        q = q.filter(Notification.id.in_(ids))
    updated = q.update({Notification.is_read: True}, synchronize_session=False)
    db.session.commit()
    return updated


def serialize_notification(n):
    actor = None
    if n.actor is not None:
        actor = {"username": n.actor.username, "avatar": n.actor.avatar_src}
    return {
        "id": n.id,
        "type": n.ntype,
        "actor": actor,
        "targetType": n.target_type,
        "targetId": n.target_id,
        "payload": n.payload,
        "isRead": bool(n.is_read),
        "createdAt": display_dt(n.created_at, "%d.%m.%Y %H:%M"),
    }


def purge_old(now=None):
    """Eski bildirimleri sil (günlük self-heal). Döndürür: silinen satır sayısı."""
    now = now or datetime.utcnow()
    read_cutoff = now - timedelta(days=PRUNE_READ_DAYS)
    all_cutoff = now - timedelta(days=PRUNE_ALL_DAYS)
    deleted = Notification.query.filter(
        db.or_(
            db.and_(Notification.is_read.is_(True), Notification.created_at < read_cutoff),
            Notification.created_at < all_cutoff,
        )
    ).delete(synchronize_session=False)
    db.session.commit()
    return deleted
