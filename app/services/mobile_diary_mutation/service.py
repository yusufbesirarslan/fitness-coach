"""Locked mutation boundary for the canonical nutrition ledger.

Named for the client that arrived first; it is the *only* ledger-mutation
authority, and the web correction transport (Sprint 13 PR4) reuses it rather
than growing a second set of semantics.
"""
import logging
from collections import namedtuple
from datetime import datetime
from types import SimpleNamespace

import s3_helper
from app.extensions import db
from app.models import MealLog, MealPhotoCleanup
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
    """The row is gone; releasing its owned object failed — but not silently.

    Durable outcome: **row deleted, object retained, cleanup intent retained**.
    The correction itself succeeded and is irreversible; the object is still
    there, and — the part that matters — the server still knows its exact key,
    because the intent was written inside the same transaction that deleted the
    row. So this is a *pending* release, not a lost one: the same request can be
    retried and will converge (:func:`delete_entry`), and
    :func:`drain_meal_photo_cleanups` finishes it without the user.

    Callers must not report success. A 204 here would say the lifecycle closed
    when it has not yet — the shape of F14 with a 204 in front of it.
    """


# One drained batch. Deliberately bounded: an operator command that walks an
# unbounded table is a command nobody dares run on production.
DEFAULT_CLEANUP_DRAIN_LIMIT = 200

#: What happened to one cleanup intent during a drain. ``status`` is one of
#: ``released`` (object gone, intent removed — the only terminal success),
#: ``released_intent_retained`` (object gone, intent removal failed; harmless,
#: the next run repeats an idempotent delete and clears it), ``failed`` (object
#: still there, intent kept for the next run) or ``unsafe_key`` (the stored key
#: no longer parses as one we minted — fail closed, never guess, keep the row).
CleanupOutcome = namedtuple(
    "CleanupOutcome", "cleanup_id user_id photo_key status error_type")


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


def _forget_cleanup(cleanup_id):
    """Drop one settled cleanup intent, by primary key, tolerating absence.

    A primary-key ``DELETE`` rather than ``session.delete(obj)``: the retry path
    and the operator drain may release the same object concurrently, and the ORM
    unit of work raises when its ``DELETE`` matches zero rows. Zero rows here
    means *the other one got there first*, which is convergence, not failure.
    Filtering by the primary key is also what makes a concurrent drain
    structurally unable to clear a different intent.
    """
    (db.session.query(MealPhotoCleanup)
     .filter_by(id=cleanup_id)
     .delete(synchronize_session=False))
    db.session.commit()


def _release_owned_object(user_id, entry_id, photo_key, cleanup_id):
    """Release one owned object and, only then, retire its cleanup intent.

    Never inverted. Dropping the intent first would take the object's identity
    down with an S3 failure, which is F14 again. Failure therefore leaves the
    intent exactly where it is and the caller is told the lifecycle is open.
    """
    try:
        s3_helper.delete_meal_photo(photo_key, user_id)
    except Exception as error:
        # The key is the only thing that makes this actionable, and it is an
        # internal object identifier - not a credential and not a presigned
        # URL. Meal text, macros and image bytes stay out of the log. The object
        # is NOT orphaned: `cleanup_id` names it durably.
        logger.error(
            "[DIARY] event=meal_photo_release_pending user_id=%s entry_id=%s "
            "key=%s cleanup_id=%s error_type=%s",
            user_id, entry_id, photo_key, cleanup_id, type(error).__name__)
        raise StoredObjectNotReleased from error
    try:
        _forget_cleanup(cleanup_id)
    except Exception as error:
        # The object IS gone and the ledger IS corrected, so the request
        # succeeded; only our own bookkeeping row survives. S3 DeleteObject is
        # idempotent, so the next retry or drain repeats a harmless delete and
        # clears it. Reporting failure here would be the false-negative twin of
        # reporting success while the object is still there.
        db.session.rollback()
        logger.warning(
            "[DIARY] event=meal_photo_cleanup_record_retained user_id=%s "
            "cleanup_id=%s error_type=%s",
            user_id, cleanup_id, type(error).__name__)


def _pending_cleanup(user_id, diary_date, entry_token, revision, secret):
    """The cleanup intent an already-committed correction left behind, if any.

    Resolution is the ledger's own algorithm (`_resolve_entry_id`) applied to
    the surviving durable identity: the intent stores the deleted ``MealLog.id``
    so the same owner-bound ``matches_diary_entry_id`` digest recognises the
    same opaque token. No second token algorithm, and the scan is user-scoped by
    construction, so one account's token can never name another account's
    pending release.

    Day and revision are re-checked for the reason the live path checks them:
    this is the *same* request arriving again and it must not become a weaker
    one. Without the stored revision the precondition would silently accept any
    ``If-Match`` the moment the row was gone.
    """
    candidates = (MealPhotoCleanup.query
                  .filter_by(user_id=user_id, diary_date=diary_date)
                  .all())
    for record in candidates:
        if matches_diary_entry_id(
                secret, user_id, record.entry_id, entry_token):
            if record.entry_revision != revision:
                raise StaleDiaryEntry
            return record
    return None


def delete_entry(user_id, diary_date, entry_token, revision, secret):
    """Hard-delete one current-day row and close the resources it owns (F14).

    ORDERING - the load-bearing decision. The database delete **commits first**;
    the object is released only afterwards. PostgreSQL and S3 cannot share a
    transaction, so one of the two failure shapes has to be chosen:

    * commit-then-release (this one) can leave a released row's object behind;
    * release-then-commit can leave a **surviving row pointing at a deleted
      object**, which Sprint 13's rollback plan says must never exist.

    The second is unrecoverable from the user's side, so the first is taken.
    The row lock is released by the commit, so no lock is held across the S3
    network round-trip.

    DURABILITY - what makes that ordering survivable. Committing the row delete
    while recording nothing would destroy the object's *identity*: the key
    carries a random uuid4 that no amount of token arithmetic reconstructs, so a
    failed release would leave behind an object nothing could ever name again.
    The same transaction that deletes the row therefore also writes a
    ``MealPhotoCleanup`` intent. Two state transitions, one commit - a failure
    of either leaves the row present, no intent, and S3 untouched.

    Only after that commit is the object released and the intent retired.
    Everything that can go wrong from there leaves the intent standing, and the
    intent is enough to finish the job later:

    * S3 failure  -> ``StoredObjectNotReleased``; row gone, object retained,
      intent retained. Retrying this exact request converges (below), and
      `drain_meal_photo_cleanups` converges without the user.
    * DB failure  -> raised before any object call; nothing is released.
    * concurrent  -> the row lock lets exactly one caller reach the commit, so
      exactly one intent is written; the loser sees ``EntryNotFound``.
    * no photo    -> no intent, no object call at all.

    RETRY. Once the row is gone the token resolves to nothing, so a plain
    ``EntryNotFound`` would strand the caller on a request that is genuinely
    still unfinished. The pending intent is consulted first: if this user has
    one for this exact entry, day and revision, the release is retried and
    success is returned. A genuinely absent entry with no pending intent still
    raises ``EntryNotFound``.
    """
    try:
        row = _locked_current_row(
            user_id, diary_date, entry_token, revision, secret)
    except EntryNotFound:
        pending = _pending_cleanup(
            user_id, diary_date, entry_token, revision, secret)
        if pending is None:
            raise
        _release_owned_object(
            pending.user_id, pending.entry_id, pending.photo_key, pending.id)
        return
    photo_key = row.photo_key
    entry_id = row.id
    if photo_key and not s3_helper.meal_photo_key_is_deletable(
            photo_key, user_id):
        db.session.rollback()
        raise UnreleasableStoredObject
    cleanup = None
    if photo_key:
        cleanup = MealPhotoCleanup(
            user_id=user_id,
            entry_id=entry_id,
            photo_key=photo_key,
            entry_revision=revision,
            diary_date=diary_date,
            created_at=datetime.utcnow(),
        )
        db.session.add(cleanup)
    db.session.delete(row)
    # One flush, so the intent INSERT and the row DELETE are the same unit of
    # work, and so the intent id is readable without a post-commit refresh.
    db.session.flush()
    cleanup_id = cleanup.id if cleanup is not None else None
    db.session.commit()
    if not photo_key:
        return
    _release_owned_object(user_id, entry_id, photo_key, cleanup_id)


def drain_meal_photo_cleanups(limit=DEFAULT_CLEANUP_DRAIN_LIMIT):
    """Finish the releases that committed corrections could not finish (F14).

    The operational half of the lifecycle. Without it a pending intent would
    depend on the user happening to retry, and durable knowledge of an object
    nobody will ever act on closes nothing.

    Safe to run repeatedly, and safe to run beside a user retry:

    * every stored key is re-validated *here* rather than trusted because it was
      valid when written - an unparseable key is reported and left alone, never
      guessed at;
    * the object delete happens outside any row lock and S3 ``DeleteObject`` is
      idempotent, so a duplicate release racing the retry path is harmless;
    * only an intent whose object is actually gone is removed, and it is removed
      by primary key, so a concurrent worker can never clear a different one;
    * a failure keeps its intent for the next run.

    Returns one `CleanupOutcome` per intent examined.
    """
    batch = [
        (record.id, record.user_id, record.photo_key)
        for record in (MealPhotoCleanup.query
                       .order_by(MealPhotoCleanup.created_at.asc(),
                                 MealPhotoCleanup.id.asc())
                       .limit(limit)
                       .all())
    ]
    # Read the batch out as plain tuples: the loop commits, which expires every
    # attached instance, and a later attribute read on a row another worker
    # already deleted would raise instead of converging.
    db.session.expunge_all()

    outcomes = []
    for cleanup_id, user_id, photo_key in batch:
        if not s3_helper.meal_photo_key_is_deletable(photo_key, user_id):
            # Only reachable if the stored key was corrupted after the fact.
            # Fail closed and keep the row: an operator can still read the exact
            # string, and no guess is ever turned into a delete.
            logger.error(
                "[DIARY] event=meal_photo_cleanup_unsafe_key cleanup_id=%s "
                "user_id=%s", cleanup_id, user_id)
            outcomes.append(CleanupOutcome(
                cleanup_id, user_id, photo_key, "unsafe_key", None))
            continue
        try:
            s3_helper.delete_meal_photo(photo_key, user_id)
        except Exception as error:
            db.session.rollback()
            logger.warning(
                "[DIARY] event=meal_photo_cleanup_failed cleanup_id=%s "
                "user_id=%s error_type=%s",
                cleanup_id, user_id, type(error).__name__)
            outcomes.append(CleanupOutcome(
                cleanup_id, user_id, photo_key, "failed",
                type(error).__name__))
            continue
        try:
            _forget_cleanup(cleanup_id)
        except Exception as error:
            db.session.rollback()
            outcomes.append(CleanupOutcome(
                cleanup_id, user_id, photo_key, "released_intent_retained",
                type(error).__name__))
            continue
        outcomes.append(CleanupOutcome(
            cleanup_id, user_id, photo_key, "released", None))
    return outcomes
