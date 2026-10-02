"""The ONE Supplement cabinet persistence boundary (web ``/supplement/*`` + native).

``Supplement`` is the sole cabinet (UX-3 PR5). NUTR-PR7 adds a native transport,
not a second cabinet: both transports create, change and delete rows through
this module, so the first-supplement bonus, the activity line and the quest
stay one rule.

Every write runs under the owner's ``user`` row lock — the idiom the browser
add route already used to serialize the first-supplement bonus — and accepts an
optional ``check`` callable that runs UNDER that lock and raises to refuse. The
browser passes none (its semantics are unchanged); the native transport passes
its precondition, so nothing is written unless the state the client read is
still the state on disk.
"""
from app.extensions import db
from app.models import Supplement, User
from app.services.gamification import award_xp, complete_quest_for_user, log_activity


SUPPLEMENT_CATEGORIES = ["Protein", "Amino Acid", "Pre-Workout", "Vitamin/Health", "Creatine", "Other"]
SUPPLEMENT_STATUSES = ["Active", "Low Stock", "Finished"]

# Every column a client may change, in a fixed order (revision input order).
MUTABLE_FIELDS = (
    "product_name", "brand", "category", "status", "rating_effect",
    "rating_taste", "rating_digestion", "rating_price", "review_text",
    "price_paid", "is_public",
)


def lock_owner(user_id):
    """``SELECT … FOR UPDATE`` on the owner row (no-op on SQLite)."""
    db.session.query(User.id).filter_by(id=user_id).with_for_update().first()


def owner_ids(user_id):
    """The owner's supplement ids, ascending — one bounded column read."""
    return [row[0] for row in (db.session.query(Supplement.id)
                               .filter_by(user_id=user_id)
                               .order_by(Supplement.id.asc()).all())]


def create_supplement(user_id, fields, check=None):
    """Insert one row + the canonical side effects. Returns ``(row, quest)``.

    ``fields`` is a dict over :data:`MUTABLE_FIELDS` that the transport has
    already validated. ``check(owner_ids)`` runs under the owner lock.
    """
    supp = Supplement(user_id=user_id, **fields)
    # İlk supplement bonusu (25 XP) tam olarak BİR kez verilmeli: kararı
    # kullanıcı satırı kilidiyle serileştir — kilidi count'tan ÖNCE al ve supp
    # commit'ine kadar tut (BUG-2).
    lock_owner(user_id)
    if check is not None:
        try:
            check(owner_ids(user_id))
        except Exception:
            db.session.rollback()
            raise
    prior_count = Supplement.query.filter_by(user_id=user_id).count()
    db.session.add(supp)
    db.session.commit()

    if prior_count == 0:
        award_xp(user_id, 25)
        db.session.commit()

    # Activity yazısını HEMEN commit'le (1.2).
    log_activity(user_id, "new_supplement",
                 f"{supp.product_name} ({supp.category}) stack'ine eklendi")
    db.session.commit()
    quest_result = complete_quest_for_user(user_id, "supplement_added")
    return supp, quest_result


def locked_supplement(user_id, supplement_id):
    """The owner's row under ``FOR UPDATE``, or ``None``. Owner-scoped always."""
    return (Supplement.query.filter_by(id=supplement_id, user_id=user_id)
            .populate_existing().with_for_update().one_or_none())


def update_supplement(user_id, supplement_id, changes, check=None):
    """Apply already-validated ``changes``; ``check(row)`` runs under the lock.

    Returns the committed row, or ``None`` when the owner has no such row.
    """
    lock_owner(user_id)
    row = locked_supplement(user_id, supplement_id)
    if row is None:
        db.session.rollback()
        return None
    if check is not None:
        try:
            check(row)
        except Exception:
            db.session.rollback()
            raise
    for field, value in changes.items():
        if field not in MUTABLE_FIELDS:
            raise ValueError("not a mutable supplement field")
        setattr(row, field, value)
    db.session.commit()
    return row


def delete_supplement(user_id, supplement_id, check=None):
    """Delete one owned row; ``check(row)`` runs under the lock. Returns bool."""
    lock_owner(user_id)
    row = locked_supplement(user_id, supplement_id)
    if row is None:
        db.session.rollback()
        return False
    if check is not None:
        try:
            check(row)
        except Exception:
            db.session.rollback()
            raise
    db.session.delete(row)
    db.session.commit()
    return True
