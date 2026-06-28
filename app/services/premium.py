"""Sunucu-taraflı freemium kapısı: AI plan üretiminde haftalık kota.

Ücretsiz kullanıcı her plan türünden (training/nutrition) Istanbul haftasında
en fazla FREE_WEEKLY_AI_PLANS kez ÜRETİM yapabilir; premium sınırsızdır
("Sınırsız yeniden planlama"). Sayaç User.user_metadata (JSONB/JSON) içinde
tutulur — Redis'ten bağımsız, yeniden başlatmaya dayanıklı ve ekstra tablo
gerektirmez.

Üretim pahalı AI çağrısıdır; bu yüzden kapı ÜRETİM uçlarına (/training-plan,
/nutrition-plan) konur, kaydetme uçlarına değil. Kota yalnızca BAŞARILI üretimde
(HTTP 200) artırılır → başarısız denemeler hakkı yakmaz.
"""
import os
from functools import wraps

from flask import current_app, jsonify
from flask_login import current_user
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.timeutil import app_today

# Ücretsiz planda plan-türü başına haftalık üretim hakkı.
FREE_WEEKLY_AI_PLANS = 1

# Ücretsiz planda haftalık AI koç sohbeti (/ask) hakkı. /ask en pahalı yoldur
# (Bedrock Sonnet tool-loop); salt 30/saat rate-limit sürekli pahalı çağrıya izin
# veriyordu (M4). Varsayılan cömert (yalnızca aşırı kötüye-kullanımı keser); ops
# env ile ayarlayabilir. Premium → sınırsız.
FREE_WEEKLY_AI_CHATS = int(os.getenv("FREE_WEEKLY_AI_CHATS", "200"))


def _week_key(d=None):
    """Istanbul gününe göre ISO hafta anahtarı ('YYYY-Www'). Yıl-hafta birlikte
    tutulur ki yıl dönümünde hafta numarası çakışmasın."""
    d = d or app_today()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _quota(user):
    """(yeni meta dict, bu haftanın kota dict'i, hafta anahtarı) döndür.

    Yeni hafta başladıysa kota sıfırdan sayılır (eski hafta verisi düşer).
    Dict'ler KOPYA olarak döner; çağıran mutasyonu sahibe geri yazmalı.
    """
    meta = dict(getattr(user, "user_metadata", None) or {})
    q = dict(meta.get("ai_plan_quota") or {})
    wk = _week_key()
    if q.get("week") != wk:
        q = {"week": wk}
    return meta, q, wk


def remaining_ai_plans(user, kind):
    """Bu hafta `kind` için kalan üretim hakkı (premium → None = sınırsız)."""
    if getattr(user, "is_premium", False):
        return None
    _meta, q, _wk = _quota(user)
    used = int(q.get(kind, 0))
    return max(FREE_WEEKLY_AI_PLANS - used, 0)


def record_ai_plan_generation(user, kind):
    """Başarılı bir `kind` üretimini bu haftaya işle (premium'da no-op)."""
    if getattr(user, "is_premium", False):
        return
    meta, q, wk = _quota(user)
    q["week"] = wk
    q[kind] = int(q.get(kind, 0)) + 1
    meta["ai_plan_quota"] = q
    user.user_metadata = meta
    flag_modified(user, "user_metadata")  # JSON in-place değişimini garantiye al
    db.session.commit()


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
    if getattr(user, "is_premium", False):
        return
    meta, q, wk = _quota(user)
    q["week"] = wk
    q["chat"] = int(q.get("chat", 0)) + 1
    meta["ai_plan_quota"] = q
    user.user_metadata = meta
    flag_modified(user, "user_metadata")  # JSON in-place değişimini garantiye al
    db.session.commit()


def premium_ai_plan_gate(kind):
    """AI plan üretim route'una sar: kota dolu (non-premium) ise 402 döndür,
    aksi halde route'u çalıştır ve BAŞARILI (200) sonuçta kotayı artır.

    @login_required ve limiter dekoratörlerinin İÇİNDE (en yakın fn'e) konmalı.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_app.config.get("AI_PLAN_QUOTA_ENABLED", True):
                return fn(*args, **kwargs)  # kota kapalı → davranış değişmez
            if remaining_ai_plans(current_user, kind) == 0:
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
            resp = current_app.make_response(fn(*args, **kwargs))
            if resp.status_code == 200:
                record_ai_plan_generation(current_user, kind)
            return resp
        return wrapper
    return decorator
