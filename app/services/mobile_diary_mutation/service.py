"""Locked mutation boundary for the canonical nutrition ledger.

Named for the client that arrived first; it is the *only* ledger-mutation
authority, and the web correction transport (Sprint 13 PR4) reuses it rather
than growing a second set of semantics.
"""
import logging
from types import SimpleNamespace

import s3_helper
from app.extensions import db
from app.models import MealLog
from app.services.mobile_nutrition.identity import (
    diary_entry_id,
    matches_diary_entry_id,
)
from app.services.mobile_nutrition.revision import (
    DiaryEntryRevisionState,
    diary_entry_revision,
    matches_diary_entry_revision,
)
from app.services.mobile_nutrition.serialization import logged_meal

from .commands import SLOT_LABELS, SetSlotCommand


# Module logger, not `current_app.logger`: this boundary stays transport-free
# (guarded in tests/test_mobile_diary_mutation_architecture.py) and is called
# from both HTTP transports. Same convention `s3_helper` uses for its own
# object-store events, so orphan reports land beside the S3 warnings.
logger = logging.getLogger(__name__)


class EntryNotFound(Exception):
    pass


class StaleDiaryEntry(Exception):
    pass


class UnreleasableStoredObject(Exception):
    """The row points at a stored object this application may not delete.

    F14 fails **closed**: a ledger row whose photo reference is not one
    ``s3_helper`` minted cannot have its lifecycle closed, so the deletion is
    refused entirely. Neither orphaning the object silently nor guessing at a
    key is acceptable — database text must never become permission to delete an
    arbitrary object.
    """


class StoredObjectNotReleased(Exception):
    """The row is gone; releasing its owned object failed.

    Durable outcome: **row deleted, object retained**. The correction itself
    succeeded and is irreversible; the object is a *known* orphan, logged at
    error level so the leak is visible rather than silent. Callers must not
    report success — that would be F14 with a 204 in front of it.
    """


def _state(row):
    return DiaryEntryRevisionState(
        user_id=row.user_id,
        entry_id=row.id,
        meal_label=row.ogun,
        description=row.yemekler,
        energy_kcal=row.kalori,
        protein_g=row.protein,
        carbohydrate_g=row.karb,
        fat_g=row.yag,
        diary_date=row.tarih,
        source=row.source,
        idempotency_key=row.idempotency_key,
        idempotency_fingerprint=row.idempotency_fingerprint,
        photo_key=row.photo_key,
        created_at=row.created_at,
    )


def _canonical_meal(row, secret, user_id):
    projected = SimpleNamespace(
        user_id=row.user_id,
        entry_id=row.id,
        meal_label=row.ogun,
        description=row.yemekler,
        source=row.source,
        created_at=row.created_at,
        energy_kcal=row.kalori,
        protein_g=row.protein,
        carbohydrate_g=row.karb,
        fat_g=row.yag,
        diary_date=row.tarih,
        idempotency_key=row.idempotency_key,
        idempotency_fingerprint=row.idempotency_fingerprint,
        photo_key=row.photo_key,
    )
    return logged_meal(
        projected,
        lambda entry_id: diary_entry_id(secret, user_id, entry_id),
        lambda entry: diary_entry_revision(secret, _state(row)),
    )


def entry_identity(row, secret):
    """The correction identity of one ledger row: ``(entry_token, revision)``.

    The single projection every transport reads. The web current-day payload
    needs exactly this to issue ``DELETE + If-Match`` and nothing more — no
    database id, no revision column, no storage key — and putting it beside the
    resolver that consumes it is what keeps one token algorithm and one
    revision algorithm in the repository.
    """
    return (diary_entry_id(secret, row.user_id, row.id),
            diary_entry_revision(secret, _state(row)))


def _resolve_entry_id(user_id, diary_date, entry_token, secret):
    candidates = (db.session.query(MealLog.id)
                  .filter_by(user_id=user_id, tarih=diary_date)
                  .all())
    resolved = None
    for (entry_id,) in candidates:
        if matches_diary_entry_id(secret, user_id, entry_id, entry_token):
            resolved = entry_id
    if resolved is None:
        raise EntryNotFound
    return resolved


def _locked_current_row(user_id, diary_date, entry_token, revision, secret):
    entry_id = _resolve_entry_id(user_id, diary_date, entry_token, secret)
    row = (MealLog.query
           .filter_by(id=entry_id, user_id=user_id, tarih=diary_date)
           .with_for_update()
           .one_or_none())
    if row is None:
        raise EntryNotFound
    if not matches_diary_entry_revision(secret, _state(row), revision):
        raise StaleDiaryEntry
    return row


def set_slot(user_id, diary_date, entry_token, revision, command, secret):
    if not isinstance(command, SetSlotCommand):
        raise TypeError("command must be SetSlotCommand")
    row = _locked_current_row(
        user_id, diary_date, entry_token, revision, secret)
    row.ogun = SLOT_LABELS[command.slot]
    db.session.flush()
    meal = _canonical_meal(row, secret, user_id)
    db.session.commit()
    return meal


def delete_entry(user_id, diary_date, entry_token, revision, secret):
    """Hard-delete one current-day row and close the resources it owns (F14).

    ORDERING — the load-bearing decision. The database delete **commits first**;
    the object is released only afterwards. PostgreSQL and S3 cannot share a
    transaction, so one of the two failure shapes has to be chosen:

    * commit-then-release (this one) can leave a released row's object behind;
    * release-then-commit can leave a **surviving row pointing at a deleted
      object**, which Sprint 13's rollback plan says must never exist.

    The second is unrecoverable from the user's side, so the first is taken.
    The row lock is released by the commit, so no lock is held across the S3
    network round-trip.

    Consequences, all tested:

    * S3 failure  -> ``StoredObjectNotReleased``; row gone, object retained and
      logged. A retry converges on ``EntryNotFound`` and touches nothing else.
    * DB failure  -> raised before any object call; nothing is released.
    * concurrent  -> the row lock lets exactly one caller reach the commit, so
      exactly one object deletion is issued; the loser sees ``EntryNotFound``.
    * no photo    -> no object call at all.
    """
    row = _locked_current_row(
        user_id, diary_date, entry_token, revision, secret)
    photo_key = row.photo_key
    entry_id = row.id
    if photo_key and not s3_helper.meal_photo_key_is_deletable(
            photo_key, user_id):
        db.session.rollback()
        raise UnreleasableStoredObject
    db.session.delete(row)
    db.session.commit()
    if not photo_key:
        return
    try:
        s3_helper.delete_meal_photo(photo_key, user_id)
    except Exception as error:
        # The key is the only thing that makes this leak actionable, and it is
        # an internal object identifier - not a credential and not a presigned
        # URL. Meal text, macros and image bytes stay out of the log.
        logger.error(
            "[DIARY] event=meal_photo_orphaned user_id=%s entry_id=%s "
            "key=%s error_type=%s",
            user_id, entry_id, photo_key, type(error).__name__)
        raise StoredObjectNotReleased from error
