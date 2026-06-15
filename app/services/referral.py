"""Davet (referral) yardımcıları.

Her kullanıcının paylaşılabilir tek bir davet kodu olur (`/davet/<kod>`). Yeni
kayıt bu kodla geldiğinde davetçi ile davet edilen birbirine bağlanır ve iki
tarafa da XP verilir — "Bir Arkadaşını Davet Et" görevini de tetikler.

Kod alfabesi karışıklığı azaltacak şekilde seçilir (0/O, 1/I/L yok). Çarpışma
olasılığı düşük; yine de unique kolon + döngüyle garantiye alınır.
"""
import secrets

from app.extensions import db
from app.models import User

# Okunması kolay alfabe — kafa karıştıran karakterler (0,O,1,I,L) çıkarıldı.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REFERRAL_REWARD_XP = 75  # davetçi ve davet edilen için ayrı ayrı


def generate_referral_code(length=7):
    """Kullanılmayan benzersiz bir davet kodu üret."""
    for _ in range(20):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if not User.query.filter_by(referral_code=code).first():
            return code
    # Aşırı düşük olasılıkta çarpışma — daha uzun koda geç.
    return "".join(secrets.choice(_ALPHABET) for _ in range(length + 3))


def ensure_referral_code(user):
    """Kullanıcının davet kodu yoksa üret ve ata (commit ETMEZ)."""
    if not user.referral_code:
        user.referral_code = generate_referral_code()
    return user.referral_code


def consume_referral(new_user, code):
    """Yeni kaydı bir davet koduna bağla ve iki tarafa da ödül ver.

    Kendine davet, geçersiz kod veya zaten bağlanmış kullanıcıda sessizce no-op.
    Çağıran commit eder. Davet eden için "friend_invited" görevini tamamlar.
    Döndürür: ödül uygulandıysa davetçi User, yoksa None.
    """
    from app.services.gamification import award_xp, complete_quest_for_user, log_activity

    if not code or new_user.referred_by_id:
        return None
    referrer = User.query.filter_by(referral_code=code.strip().upper()).first()
    if not referrer or referrer.id == new_user.id:
        return None

    new_user.referred_by_id = referrer.id
    award_xp(referrer.id, REFERRAL_REWARD_XP)
    award_xp(new_user.id, REFERRAL_REWARD_XP)
    log_activity(referrer.id, "new_friend",
                 f"{new_user.username} davetinle FitX'e katıldı")
    db.session.commit()
    # Davet eden için günlük "Bir Arkadaşını Davet Et" görevini tamamla.
    complete_quest_for_user(referrer.id, "friend_invited")
    return referrer


def backfill_referral_codes():
    """Davet kodu olmayan tüm kullanıcılara kod ata (boot'ta bir kez)."""
    missing = User.query.filter(
        db.or_(User.referral_code.is_(None), User.referral_code == "")
    ).all()
    if not missing:
        return 0
    for u in missing:
        u.referral_code = generate_referral_code()
    db.session.commit()
    return len(missing)
