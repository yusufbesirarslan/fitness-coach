# Rozet katalogu + verme yardımcıları (Sprint 5 PR3).
# award_badge: session-add-only, COMMIT ETMEZ (record_event ile aynı transaction).
import logging

from app.extensions import db
from app.models import UserBadge

log = logging.getLogger(__name__)

# badge_code → görsel + i18n başlık anahtarı. code kanonik slug; başlık t() ile.
BADGE_CATALOG = {
    "pump_week":    {"icon": "\U0001f4aa", "title_key": "badge.pump_week.title"},
    "active_week":  {"icon": "\U0001f525", "title_key": "badge.active_week.title"},
    "pump_perfect": {"icon": "\U0001f3c6", "title_key": "badge.pump_perfect.title"},
    "grinder":      {"icon": "⚙️", "title_key": "badge.grinder.title"},
}


def award_badge(user_id, badge_code, source=None):
    """Rozet ekle (commit etmez). None döner: kod yok / katalogda yok / zaten var."""
    if not badge_code or badge_code not in BADGE_CATALOG:
        return None
    try:
        with db.session.no_autoflush:
            exists = UserBadge.query.filter_by(
                user_id=user_id, badge_code=badge_code).first()
        if exists:
            return None
        b = UserBadge(user_id=user_id, badge_code=badge_code, source=source)
        # KENDİ savepoint'inde ekle ve HEMEN flush et. Eskiden insert yalnızca
        # session'a eklenip flush ERTELENİYORDU: bir duplicate (uq_user_badge)
        # ancak ÇAĞIRANIN savepoint'i kapanırken patlar, yani award_badge'in
        # try/except'i çoktan başarıyla dönmüş olurdu. O IntegrityError
        # _try_complete'in begin_nested'ini geri alır — KORUMALI `completed_at`
        # UPDATE'iyle birlikte — ve challenge bir daha ASLA tamamlanamazdı
        # (her yeniden deneme aynı duplicate'e çarpar; triage 2026-08-14 #3).
        # İç savepoint hatayı YEREL tutar: rollback yalnız rozet insert'ini alır.
        with db.session.begin_nested():
            db.session.add(b)
            db.session.flush()
        return b
    except Exception:
        log.warning("award_badge başarısız (yutuldu): user=%s badge=%s",
                    user_id, badge_code, exc_info=True)
        return None


def badges_for(user_id):
    """Kullanıcının rozetleri (en yeni önce). Katalog dışı kodlar atlanır."""
    rows = (UserBadge.query.filter_by(user_id=user_id)
            .order_by(UserBadge.earned_at.desc(), UserBadge.id.desc()).all())
    out = []
    for r in rows:
        meta = BADGE_CATALOG.get(r.badge_code)
        if not meta:
            continue
        out.append({"code": r.badge_code, "icon": meta["icon"],
                    "titleKey": meta["title_key"],
                    "earnedAt": r.earned_at.isoformat() if r.earned_at else None})
    return out
