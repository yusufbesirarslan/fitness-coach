"""Amazon Cognito (OIDC) kimlik → yerel User eşlemesi.

Cognito 'userinfo' claim'lerinden yerel bir User bulur ya da oluşturur. Yeni
kullanıcılar klasik kayıt akışıyla aynı yan etkileri alır (referral kodu üretimi,
davet tüketimi). Cognito ile giren kullanıcının yerel ŞİFRESİ YOKTUR; password_hash
sütunu NOT NULL olduğundan tahmin edilemez rastgele bir hash atanır — böylece bu
hesap klasik /login formundan ASLA giriş yapamaz (yalnızca Cognito üzerinden).
"""
import logging
import re
import secrets

from app.extensions import db
from app.models import User
from app.services.referral import consume_referral, ensure_referral_code

_logger = logging.getLogger(__name__)

# Kullanıcı adı yalnızca [A-Za-z0-9_.-] içerebilir (validate_username ile aynı);
# e-posta yerel kısmındaki diğer tüm karakterleri at.
_USERNAME_STRIP_RE = re.compile(r"[^A-Za-z0-9_.-]")


class CognitoLinkError(Exception):
    """Cognito kimliğiyle güvenli bir yerel hesap bağı kurulamadığında yükseltilir
    (örn. doğrulanmamış e-posta zaten başka bir hesaba ait). Route bunu kullanıcıya
    gösterilecek bir mesaja çevirir."""


def _coerce_bool(value):
    """Cognito email_verified claim'i bool ya da "true"/"false" string gelebilir;
    ikisini de güvenle bool'a indirger."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _unique_username(email):
    """validate_username kurallarına uyan (>=3 karakter, [A-Za-z0-9_.-]),
    çakışmayan bir kullanıcı adı üret. E-posta yerel kısmından türetir; gerekiyorsa
    kısa rastgele bir sonekle benzersizleştirir."""
    base = _USERNAME_STRIP_RE.sub("", (email or "").split("@", 1)[0])
    if len(base) < 3:
        base = f"{base}fitx"
    base = base[:64]
    candidate = base
    while User.query.filter_by(username=candidate).first() is not None:
        candidate = f"{base[:57]}_{secrets.token_hex(3)}"  # +7 char ≤ 64
    return candidate


def get_or_create_user(userinfo, ref_code=""):
    """Cognito userinfo claim'lerinden bir User döndür (gerekiyorsa oluşturur).

    Eşleme önceliği:
      1) cognito_sub ile bağlı mevcut hesap (en güçlü, e-postadan bağımsız).
      2) Aynı e-postalı mevcut yerel hesap — YALNIZCA e-posta DOĞRULANMIŞSA bağla
         (doğrulanmamış e-postayla bağlamak hesap ele geçirmeye kapı açar).
      3) Yeni hesap oluştur (rastgele şifre + referral kodu + davet tüketimi).

    Döndürür: (user, is_new).
    """
    sub = (userinfo.get("sub") or "").strip()
    email = (userinfo.get("email") or "").strip().lower()
    email_verified = _coerce_bool(userinfo.get("email_verified"))

    # 1) Daha önce bağlanmış Cognito kimliği.
    if sub:
        user = User.query.filter_by(cognito_sub=sub).first()
        if user:
            return user, False

    if not email:
        raise CognitoLinkError("Cognito hesabında e-posta bulunamadı.")

    # 2) Aynı e-postalı yerel hesap → yalnızca doğrulanmış e-postayla bağla.
    existing = User.query.filter_by(email=email).first()
    if existing:
        if not email_verified:
            raise CognitoLinkError(
                "Bu e-posta zaten kayıtlı. Cognito'da e-postanı doğrula "
                "ya da kullanıcı adı/şifre ile giriş yap.")
        if sub and not existing.cognito_sub:
            existing.cognito_sub = sub
            db.session.commit()
            _logger.info("[COGNITO] mevcut hesap (id=%s) Cognito kimliğine bağlandı", existing.id)
        return existing, False

    # 3) Yeni hesap. Parola NOT NULL → tahmin edilemez rastgele hash (kullanılamaz).
    user = User(
        username=_unique_username(email),
        email=email,
        cognito_sub=sub or None,
    )
    ensure_referral_code(user)
    db.session.add(user)
    db.session.commit()
    _logger.info("[COGNITO] yeni hesap oluşturuldu (id=%s, username=%s)", user.id, user.username)

    # Davet döngüsü: kayıt bir davet bağlantısından geldiyse iki tarafa da XP ver
    # (consume_referral kendi içinde commit eder).
    if ref_code:
        consume_referral(user, ref_code)
    return user, True
