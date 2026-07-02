"""Herkese açık pazarlama / büyüme sayfaları + davet ve premium akışları.

- GET /welcome   — herkese açık landing (giriş yapmış kullanıcı panoya yönlenir)
- GET /davet/<kod> — davet bağlantısı: ref cookie'sini kur, kayıt sayfasına götür
- GET /premium    — freemium tanıtımı (billing yok; upgrade-intent GA olayı)
- GET /referral   — panodaki davet kartı için JSON (kod + bağlantı + davet sayısı)
"""
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.i18n import t
from app.models import User


bp = Blueprint("pages", __name__)


# Freemium hattı — billing inşa edilmeden önce instrümantasyon ve UI için tek kaynak.
# Metinler katalogdan (locales/*.json) dile göre üretilir; aşağıdaki anahtarlar
# kanonik sıra/yapıyı belirler.
_FREEMIUM_KEYS = {
    "free": ["premium.feat_free.1", "premium.feat_free.2", "premium.feat_free.3"],
    "premium": ["premium.feat_prem.1", "premium.feat_prem.2", "premium.feat_prem.3"],
}


def _freemium():
    """Aktif dile göre freemium özellik listeleri (render her istekte g.locale ile)."""
    return {tier: [t(k) for k in keys] for tier, keys in _FREEMIUM_KEYS.items()}


@bp.route("/welcome")
def landing():
    # Giriş yapmış kullanıcı pazarlama sayfasında oyalanmasın → doğrudan panoya.
    if current_user.is_authenticated:
        return redirect(url_for("tracking.home"))
    return render_template("landing.html")


@bp.route("/davet/<code>")
def invite(code):
    """Davet bağlantısı. Kodu cookie'ye yaz ve kayıt sayfasına yönlendir; kayıt
    tamamlanınca auth.register cookie'yi okuyup çift taraflı ödülü uygular."""
    target = url_for("auth.register")
    # Giriş yapmış kullanıcı kendi davetini açtıysa panoya gönder (yeni kayıt yok).
    if current_user.is_authenticated:
        return redirect(url_for("tracking.home"))
    resp = redirect(target)
    clean = (code or "").strip().upper()[:16]  # davet kodu 16 karaktere yükseltildi
    if clean and User.query.filter_by(referral_code=clean).first():
        # 30 gün; SameSite=Lax — sadece kendi sitemizden gelen kayıt akışında okunur.
        resp.set_cookie("fitx_ref", clean, max_age=60 * 60 * 24 * 30,
                        samesite="Lax", httponly=True)
    return resp


@bp.route("/premium")
@login_required
def premium():
    return render_template("premium.html", freemium=_freemium(),
        username=current_user.username,
        profile_picture=current_user.avatar_src,
        is_premium=bool(current_user.is_premium))


@bp.route("/referral")
@login_required
def referral_data():
    # Kod kayıt sırasında veya boot backfill'inde atanır. Bu GET route'u salt-okunur
    # kalmalı; eksik kod varsa deploy/backfill sorunu görünür olsun.
    if not current_user.referral_code:
        current_app.logger.warning("[REFERRAL] Kullanıcının davet kodu eksik (user=%s)", current_user.id)
        return jsonify({"error": t("route.user_not_found")}), 404
    invite_url = url_for("pages.invite", code=current_user.referral_code, _external=True)
    count = User.query.filter_by(referred_by_id=current_user.id).count()
    return jsonify({
        "code": current_user.referral_code,
        "invite_url": invite_url,
        "referred_count": count,
    })
