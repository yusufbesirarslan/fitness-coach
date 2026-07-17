"""Sunucu-taraflı freemium kapısı: AI plan üretiminde haftalık kota.

Ücretsiz kullanıcı her plan türünden (training/nutrition) Istanbul haftasında
en fazla FREE_WEEKLY_AI_PLANS kez ÜRETİM yapabilir; premium sınırsızdır
("Sınırsız yeniden planlama"). Sayaç User.user_metadata (JSONB/JSON) içinde
tutulur — Redis'ten bağımsız, yeniden başlatmaya dayanıklı ve ekstra tablo
gerektirmez.

Üretim pahalı AI çağrısıdır; bu yüzden kapı ÜRETİM uçlarına (/training-plan,
/nutrition-plan) konur, kaydetme uçlarına değil. Kota çağrıdan önce atomik olarak
rezerve edilir; başarısız denemelerde iade edilir ve hak yakılmaz.
"""
import os
from functools import wraps

from flask import current_app, jsonify
from flask_login import current_user
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import User
from app.timeutil import app_today

# Ücretsiz planda plan-türü başına haftalık üretim hakkı.
FREE_WEEKLY_AI_PLANS = 1

# Ücretsiz planda haftalık AI koç sohbeti (/ask) hakkı. /ask en pahalı yoldur
# (Bedrock Sonnet tool-loop); salt 30/saat rate-limit sürekli pahalı çağrıya izin
# veriyordu (M4). Varsayılan cömert (yalnızca aşırı kötüye-kullanımı keser); ops
# env ile ayarlayabilir. Premium → sınırsız.
FREE_WEEKLY_AI_CHATS = int(os.getenv("FREE_WEEKLY_AI_CHATS", "200"))

_RESERVATION_WEEKS_ATTR = "_ai_quota_reservation_weeks"


def _week_key(d=None):
    """Istanbul gününe göre ISO hafta anahtarı ('YYYY-Www'). Yıl-hafta birlikte
    tutulur ki yıl dönümünde hafta numarası çakışmasın."""
    d = d or app_today()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _quota_from_meta(raw_meta):
    """(yeni meta dict, bu haftanın kota dict'i, hafta anahtarı) döndür.

    Yeni hafta başladıysa kota sıfırdan sayılır (eski hafta verisi düşer).
    Dict'ler KOPYA olarak döner; çağıran mutasyonu sahibe geri yazmalı.
    """
    meta = dict(raw_meta or {})
    q = dict(meta.get("ai_plan_quota") or {})
    wk = _week_key()
    if q.get("week") != wk:
        q = {"week": wk}
    return meta, q, wk


def _quota(user):
    return _quota_from_meta(getattr(user, "user_metadata", None))


def _remember_reservation_week(user, counter_key, week):
    reservations = dict(getattr(user, _RESERVATION_WEEKS_ATTR, {}) or {})
    reservations[counter_key] = week
    setattr(user, _RESERVATION_WEEKS_ATTR, reservations)


def reservation_week(user, counter_key):
    """Return the week captured by this request's successful reservation."""
    return (getattr(user, _RESERVATION_WEEKS_ATTR, {}) or {}).get(counter_key)


def _forget_reservation_week(user, counter_key):
    reservations = dict(getattr(user, _RESERVATION_WEEKS_ATTR, {}) or {})
    reservations.pop(counter_key, None)
    setattr(user, _RESERVATION_WEEKS_ATTR, reservations)


def reserve_ai_quota(user, counter_key, limit):
    """Atomically reserve one weekly allowance before an AI provider call."""
    if getattr(user, "is_premium", False):
        return True

    fresh_meta = (db.session.query(User.user_metadata)
                  .filter_by(id=user.id).with_for_update().scalar())
    target = db.session.get(User, user.id)
    if target is None:
        db.session.rollback()
        return False

    meta, quota, week = _quota_from_meta(fresh_meta)
    used = int(quota.get(counter_key, 0))
    if used >= limit:
        db.session.commit()
        return False

    quota["week"] = week
    quota[counter_key] = used + 1
    meta["ai_plan_quota"] = quota
    target.user_metadata = meta
    flag_modified(target, "user_metadata")
    db.session.commit()
    _remember_reservation_week(user, counter_key, week)
    return True


def refund_ai_quota(user, counter_key, reserved_week=None):
    """Atomically return one reserved allowance, never dropping below zero."""
    if getattr(user, "is_premium", False):
        return

    fresh_meta = (db.session.query(User.user_metadata)
                  .filter_by(id=user.id).with_for_update().scalar())
    target = db.session.get(User, user.id)
    if target is None:
        db.session.rollback()
        return

    if reserved_week is None:
        reserved_week = reservation_week(user, counter_key)
    stored_quota = dict((fresh_meta or {}).get("ai_plan_quota") or {})
    if reserved_week is not None and stored_quota.get("week") != reserved_week:
        db.session.commit()
        _forget_reservation_week(user, counter_key)
        return

    meta, quota, week = _quota_from_meta(fresh_meta)
    quota["week"] = week
    quota[counter_key] = max(int(quota.get(counter_key, 0)) - 1, 0)
    meta["ai_plan_quota"] = quota
    target.user_metadata = meta
    flag_modified(target, "user_metadata")
    db.session.commit()
    _forget_reservation_week(user, counter_key)


def remaining_ai_plans(user, kind):
    """Bu hafta `kind` için kalan üretim hakkı (premium → None = sınırsız)."""
    if getattr(user, "is_premium", False):
        return None
    _meta, q, _wk = _quota(user)
    used = int(q.get(kind, 0))
    return max(FREE_WEEKLY_AI_PLANS - used, 0)


def _record_counter(user, counter_key):
    """Haftalık kota sayacını (`counter_key`) atomik olarak +1 işle.

    Sayaç bütün user_metadata JSON'ının okunup yeniden yazılmasıyla tutulur; iki
    eşzamanlı üretim düz oku-değiştir-yaz ile kayıp güncellemeye (her ikisi de
    used=0 görüp used=1 yazması → tek artış) yol açardı. Satırı kilitleyip
    metadata'yı KİLİT ALTINDA yeniden okuyarak artışları serileştir (D-M2).
    SQLite'ta with_for_update no-op'tur; Postgres'te grant'leri serileştirir.

    DİKKAT (C1): kilitli okuma KOLON sorgusuyla yapılır. Entity sorgusu identity
    map'teki (istek başında yüklenmiş) current_user'ı döndürür ve kilit altında
    SELECT'lenen taze metadata'yı ATAR — iki eşzamanlı üretim yine ikisi de
    used=0 okur, kota fiilen ×2 olurdu. Kolon sorgusu identity map'i atlar."""
    if getattr(user, "is_premium", False):
        return
    fresh_meta = (db.session.query(User.user_metadata)
                  .filter_by(id=user.id).with_for_update().scalar())
    target = db.session.get(User, user.id) or user  # kilit bizde
    meta, q, wk = _quota_from_meta(fresh_meta)
    q["week"] = wk
    q[counter_key] = int(q.get(counter_key, 0)) + 1
    meta["ai_plan_quota"] = q
    target.user_metadata = meta
    flag_modified(target, "user_metadata")  # JSON in-place değişimini garantiye al
    db.session.commit()


def record_ai_plan_generation(user, kind):
    """Başarılı bir `kind` üretimini bu haftaya işle (premium'da no-op)."""
    _record_counter(user, kind)


def remaining_ai_chats(user):
    """Bu hafta `user` için kalan AI koç sohbeti (/ask) hakkı (premium → None).

    Sayaç plan kotasıyla AYNI haftalık kovada ('ai_plan_quota') 'chat' anahtarında
    tutulur; hafta dönünce plan sayaçlarıyla birlikte sıfırlanır."""
    if getattr(user, "is_premium", False):
        return None
    _meta, q, _wk = _quota(user)
    used = int(q.get("chat", 0))
    return max(FREE_WEEKLY_AI_CHATS - used, 0)


def record_ai_chat(user):
    """Başarılı bir /ask çağrısını bu haftaya işle (premium'da no-op)."""
    _record_counter(user, "chat")


def premium_ai_plan_gate(kind):
    """AI plan üretim route'una sar: kota dolu (non-premium) ise 402 döndür.

    Hakkı route'dan önce rezerve et; exception veya 200 dışı yanıtta iade et.

    @login_required ve limiter dekoratörlerinin İÇİNDE (en yakın fn'e) konmalı.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_app.config.get("AI_PLAN_QUOTA_ENABLED", True):
                return fn(*args, **kwargs)  # kota kapalı → davranış değişmez
            if not reserve_ai_quota(current_user, kind, FREE_WEEKLY_AI_PLANS):
                return jsonify({
                    "error": "Ücretsiz planda haftada 1 yapay zekâ planı "
                             "oluşturabilirsin. Sınırsız yeniden planlama için "
                             "Premium'a geç.",
                    "premium_required": True,
                }), 402
            # Dönüşü Response'a normalize et: tuple `(body, code)`, düz body VEYA
            # doğrudan Response nesnesi olabilir. Eski "tuple değilse 200 say"
            # sezgisi, hata statüslü bir Response döndüren route'ta kotayı yanlışça
            # tüketiyordu. Gerçek status_code üzerinden karar ver.
            try:
                resp = current_app.make_response(fn(*args, **kwargs))
            except Exception:
                db.session.rollback()
                try:
                    refund_ai_quota(current_user, kind)
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception("AI quota refund failed")
                raise
            if resp.status_code != 200:
                db.session.rollback()
                try:
                    refund_ai_quota(current_user, kind)
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception("AI quota refund failed")
            return resp
        return wrapper
    return decorator
