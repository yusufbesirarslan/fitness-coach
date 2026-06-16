"""Herkese açık pazarlama / büyüme sayfaları + davet ve premium akışları.

- GET /welcome   — herkese açık landing (giriş yapmış kullanıcı panoya yönlenir)
- GET /davet/<kod> — davet bağlantısı: ref cookie'sini kur, kayıt sayfasına götür
- GET /premium    — freemium tanıtımı (billing yok; upgrade-intent GA olayı)
- GET /referral   — panodaki davet kartı için JSON (kod + bağlantı + davet sayısı)
"""
from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import User
from app.services.referral import ensure_referral_code


bp = Blueprint("pages", __name__)


# Freemium hattı — billing inşa edilmeden önce instrümantasyon ve UI için tek kaynak.
FREEMIUM = {
    "free": [
        "Günlük takip, görevler ve seriler",
        "Haftada 1 yapay zekâ planı",
        "Arkadaş & liderlik tablosu",
    ],
    "premium": [
        "Sınırsız yeniden planlama",
        "Gelişmiş analizler & haftalık rapor",
        "Özel makro hedefleri",
    ],
}


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
    return render_template("premium.html", freemium=FREEMIUM,
        username=current_user.username,
        profile_picture=current_user.avatar_src,
        is_premium=bool(current_user.is_premium))


@bp.route("/referral")
@login_required
def referral_data():
    # Kod boot backfill'inde atanır; eski/yeni kenar durumları için tembel garanti.
    if not current_user.referral_code:
        ensure_referral_code(current_user)
        db.session.commit()
    invite_url = url_for("pages.invite", code=current_user.referral_code, _external=True)
    count = User.query.filter_by(referred_by_id=current_user.id).count()
    return jsonify({
        "code": current_user.referral_code,
        "invite_url": invite_url,
        "referred_count": count,
    })
