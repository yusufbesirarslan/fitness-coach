# Arkadaşlık sorguları için TEK kaynak (Sprint 5 PR1 konsolidasyonu).
# Daha önce üç kopya vardı: pump_checks.get_friend_ids, social.are_friends,
# gamification._accepted_friend_ids — hepsi artık buraya delege eder.
from app.extensions import db
from app.models import Friendship


def get_friend_ids(user_id):
    """Kabul edilmiş arkadaşların id kümesi (her iki yönden)."""
    rows = Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(Friendship.sender_id == user_id, Friendship.receiver_id == user_id),
    ).all()
    ids = set()
    for row in rows:
        ids.add(row.receiver_id if row.sender_id == user_id else row.sender_id)
    return ids


def are_friends(user_a_id, user_b_id):
    """İki kullanıcı arasında kabul edilmiş arkadaşlık var mı (yön bağımsız)."""
    return Friendship.query.filter(
        Friendship.status == "accepted",
        db.or_(
            db.and_(Friendship.sender_id == user_a_id, Friendship.receiver_id == user_b_id),
            db.and_(Friendship.sender_id == user_b_id, Friendship.receiver_id == user_a_id),
        )
    ).first() is not None
