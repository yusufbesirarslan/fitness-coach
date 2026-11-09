"""Erase first-party health records after the F01 permission is withdrawn (A1 = O2-B).

    request(conn, owner, generation, now)   inside the withdrawal transaction
    pending(conn, owner)                    the grant path refuses while True
    run(owner)                              erase now; safe to repeat
    run_pending(limit)                      daily maintenance and the operator CLI

Founder decision AXISAI-F01-A1-O2B-FOUNDER-DISPOSITION-01:

* Withdrawal stops health processing and AI access at once. That is the
  existing health boundary: every product route needs a live grant.
* Health records are erased without undue delay. The withdrawal request makes
  the first attempt; daily maintenance and ``flask health-erasure-run`` retry
  until done. The published outer bound is 30 days.
* A new grant never brings records back. It is refused while any erasure for
  the owner is unfinished, so a new grant always starts from no health history.
* Ordinary account data stays: identity, sign-in, language, eligibility,
  subscription, allowance ledgers, permission evidence, age and gender.
* Ambiguous workout records are treated as health data (conservative scope),
  until a confirmed classification says otherwise.
* A controller-only legal hold (``HealthLegalHold``) exempts the named
  categories for one documented matter until it is released. Held records stay
  unreachable: the owner cannot grant again while the erasure is unfinished,
  and the boundary refuses every health route without a grant.

Storage objects are released first, while the rows naming them still exist,
then every row change commits in one transaction under the owner row lock,
the same order as account deletion (``account_deletion`` module doc). Logs
carry the owner id, counts and an exception type, never health content.
"""
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import func, insert, select

import s3_helper
from app.extensions import db

ERASURE_DEADLINE = timedelta(days=30)
ERASURE_RECORD_YEARS = 3
MEAL_PHOTO = "meal_photo"
MANAGED_OBJECT = "managed_object"

# Coach memory keys and the Coach turn-signature index. Every other key in
# User.user_metadata is a system key (eligibility, referral, quota) and stays.
MEMORY_KEYS = ("injuries", "fitness_goals", "equipment_available",
               "dietary_restrictions", "preferences")
COACH_INDEX_KEY = "_pa06_coach_turns_v2"
PROFILE_FIELDS = ("weight", "height", "target_weight", "goal_type",
                  "goal", "fitness_level", "current_activity")
WORKOUT_COUNTERS = ("streak_count", "weekly_xp", "rank_points")

CATEGORIES = ("profile", "checkins", "activity", "plans", "workouts",
              "nutrition", "coach", "pump")


class ErasureUnavailable(Exception):
    """Erasure is incomplete; keep the obligation pending and retry safely."""


def _tables():
    from app.health_consent_models import HealthErasure, HealthLegalHold
    return HealthErasure.__table__, HealthLegalHold.__table__


def request(conn, owner, generation, now):
    """Record an owed erasure in the caller's withdrawal transaction."""
    erasures, _ = _tables()
    conn.execute(insert(erasures).values(
        owner_id=owner, generation=generation, requested_at=now.replace(tzinfo=None)))


def pending(conn, owner):
    erasures, _ = _tables()
    return conn.execute(select(erasures.c.id).where(
        erasures.c.owner_id == owner, erasures.c.completed_at.is_(None)).limit(1)).first() is not None


def public_state(conn, owner):
    """The latest erasure for the status response, or None."""
    erasures, _ = _tables()
    row = conn.execute(select(erasures).where(
        erasures.c.owner_id == owner, erasures.c.completed_at.is_(None))
        .order_by(erasures.c.requested_at, erasures.c.id).limit(1)).mappings().one_or_none()
    open_rows = row is not None
    if row is None:
        row = conn.execute(select(erasures).where(erasures.c.owner_id == owner)
            .order_by(erasures.c.requested_at.desc(), erasures.c.id.desc())
            .limit(1)).mappings().one_or_none()
    if row is None:
        return None
    requested = row["requested_at"]
    return {"state": "pending" if open_rows else "completed",
            "requested_at": requested.isoformat() + "Z",
            "due_by": (requested + ERASURE_DEADLINE).isoformat() + "Z"}


def _held(owner, now):
    from app.health_consent_models import HealthLegalHold
    held = set()
    for hold in HealthLegalHold.query.filter(HealthLegalHold.owner_id == owner,
                                             HealthLegalHold.released_at.is_(None)):
        held.update(c for c in (hold.categories or []) if c in CATEGORIES)
        if hold.review_at <= now:
            current_app.logger.warning(
                "health_erasure event=hold_review_due hold_id=%s owner_id=%s", hold.id, owner)
    return held


def storage(owner, held=frozenset()):
    """Owned health objects: Pump Check images and meal photos, never the avatar."""
    from app.models import MealLog, MealPhotoCleanup, PumpCheck, User
    avatar = db.session.query(User.profile_picture_key).filter(User.id == owner).scalar()
    references = []
    if "pump" not in held:
        references += [(MANAGED_OBJECT, key) for (key,) in db.session.query(
            PumpCheck.image_key).filter(PumpCheck.user_id == owner)]
    if "nutrition" not in held:
        references += [(MEAL_PHOTO, key) for (key,) in db.session.query(
            MealLog.photo_key).filter(MealLog.user_id == owner)]
        references += [(MEAL_PHOTO, key) for (key,) in db.session.query(
            MealPhotoCleanup.photo_key).filter(MealPhotoCleanup.user_id == owner)]
    objects = set()
    for kind, key in references:
        if not key or key == avatar:
            continue
        if kind == MANAGED_OBJECT and not key.startswith("pump-checks/"):
            continue
        deletable = (s3_helper.meal_photo_key_is_deletable(key, owner) if kind == MEAL_PHOTO
                     else s3_helper.managed_object_key_is_deletable(key, owner))
        if deletable:
            objects.add((kind, key))
    return frozenset(objects)


def _release(owner, kind, key):
    if kind == MEAL_PHOTO:
        s3_helper.delete_meal_photo(key, owner)
        return
    if not s3_helper.delete_managed_object(key, owner):
        raise s3_helper.S3Error("managed object not released")


def _delete(model, *criteria):
    return model.query.filter(*criteria).delete(synchronize_session=False)


def _release_rankings(owner):
    """Prove removal of owned workout scores; configured cache errors retry."""
    from app.services import gamification
    if gamification.redis_client is None:
        return
    for key in (gamification.LB_ALLTIME_KEY, gamification.LB_WEEKLY_KEY):
        gamification.redis_client.zrem(key, str(owner))


def _delete_feed_refs(m, ref_type, ref_ids):
    """Feed items (anyone's) that repost or quote the erased records."""
    item_ids = [i for (i,) in db.session.query(m.FeedItem.id).filter(
        m.FeedItem.ref_type == ref_type, m.FeedItem.ref_id.in_(ref_ids))]
    if not item_ids:
        return 0
    return (_delete(m.FeedItemLike, m.FeedItemLike.feed_item_id.in_(item_ids))
            + _delete(m.FeedItemComment, m.FeedItemComment.feed_item_id.in_(item_ids))
            + _delete(m.FeedItem, m.FeedItem.id.in_(item_ids)))


def _erase_rows(user, held):
    """Every row change, in FK-safe order. Returns {category: rows changed}."""
    from app import models as m
    from sqlalchemy.orm.attributes import flag_modified
    uid, counts = user.id, {}

    if "profile" not in held:
        changed = 0
        for field in PROFILE_FIELDS:
            if getattr(user, field) is not None:
                setattr(user, field, None)
                changed += 1
        from app.services import account_profile
        account_profile.invalidate_health_onboarding(user)
        meta = dict(user.user_metadata or {})
        for key in MEMORY_KEYS:
            if meta.pop(key, None) is not None:
                changed += 1
        if meta != (user.user_metadata or {}):
            user.user_metadata = meta
            flag_modified(user, "user_metadata")
        # The onboarding session copies weight/height and derives BMR/TDEE,
        # targets and plan text from them: health data in full. Without it the
        # canonical onboarding state is incomplete (account_profile owns the
        # flag itself), so a new grant re-runs onboarding.
        changed += _delete(m.UserSession, m.UserSession.user_id == uid)
        counts["profile"] = changed

    if "checkins" not in held:
        counts["checkins"] = (_delete(m.WeeklyCheckIn, m.WeeklyCheckIn.user_id == uid)
                              + _delete(m.WeeklyLog, m.WeeklyLog.user_id == uid))

    if "activity" not in held:
        counts["activity"] = sum(_delete(model, model.user_id == uid) for model in (
            m.DailyActivity, m.WearableSleepLog, m.WearableActivityLog, m.WearableWorkoutLog))

    if "plans" not in held:
        changed = sum(_delete(model, model.user_id == uid) for model in (
            m.PlanMutationRecord, m.TrainingPlanConfirmationProposal,
            m.TrainingPlanReplacementReceipt, m.TrainingPlanReplacementProposal,
            m.TrainingPlan, m.NutritionPlan))
        # Generation ledgers stay (idempotency and AI allowance accounting are
        # ordinary records); only the plan content they carry is removed.
        changed += m.TrainingPlanGenerationOperation.query.filter(
            m.TrainingPlanGenerationOperation.user_id == uid,
            m.TrainingPlanGenerationOperation.candidate_plan_data.isnot(None)).update(
            {"candidate_plan_data": None}, synchronize_session=False)
        replacement = m.TrainingPlanReplacementGenerationOperation
        changed += replacement.query.filter(
            replacement.user_id == uid,
            db.or_(replacement.generation_context.isnot(None), replacement.review_data.isnot(None))
        ).update({"generation_context": None, "review_data": None}, synchronize_session=False)
        counts["plans"] = changed

    if "workouts" not in held:
        day_ids = [i for (i,) in db.session.query(m.WorkoutCompletionDay.id).filter(
            m.WorkoutCompletionDay.user_id == uid)]
        changed = 0
        activity_ids = [i for (i,) in db.session.query(m.Activity.id).filter(m.Activity.user_id == uid)]
        if activity_ids:
            changed += _delete_feed_refs(m, "activity", activity_ids)
        if day_ids and db.engine.dialect.name != "postgresql":
            changed += _delete(m.CompletionClaimTransaction,
                               m.CompletionClaimTransaction.claim_id.in_(day_ids))
        changed += sum(_delete(model, model.user_id == uid) for model in (
            m.WorkoutCompletionDay, m.WorkoutLog, m.WorkoutSession, m.ExerciseNote, m.Activity))
        if day_ids and db.engine.dialect.name == "postgresql":
            # Validate the erasure exception while the obligation is still open.
            # Binding deletion remains restricted to the claim FK cascade.
            db.session.execute(db.text(
                "SET CONSTRAINTS completion_fence_claim_retention IMMEDIATE"))
        for field in WORKOUT_COUNTERS:
            setattr(user, field, 0)
        counts["workouts"] = changed

    if "nutrition" not in held:
        meal_ids = [i for (i,) in db.session.query(m.CustomMeal.id).filter(m.CustomMeal.user_id == uid)]
        changed = _delete(m.CustomMealItem, m.CustomMealItem.custom_meal_id.in_(meal_ids)) if meal_ids else 0
        changed += sum(_delete(model, model.user_id == uid) for model in (
            m.MealPhotoCleanup, m.MealLog, m.MealLogRetainedTotal, m.WaterLog, m.Supplement, m.CustomMeal))
        counts["nutrition"] = changed

    if "coach" not in held:
        meta = dict(user.user_metadata or {})
        if COACH_INDEX_KEY in meta:
            del meta[COACH_INDEX_KEY]
            user.user_metadata = meta
            flag_modified(user, "user_metadata")
        conv_ids = [i for (i,) in db.session.query(m.CoachConversation.id).filter(
            m.CoachConversation.user_id == uid)]
        changed = _delete(m.CoachMessage, m.CoachMessage.conversation_id.in_(conv_ids)) if conv_ids else 0
        changed += _delete(m.PendingAction, m.PendingAction.user_id == uid)
        changed += _delete(m.CoachConversation, m.CoachConversation.user_id == uid)
        counts["coach"] = changed

    if "pump" not in held:
        pump_ids = [i for (i,) in db.session.query(m.PumpCheck.id).filter(m.PumpCheck.user_id == uid)]
        changed = 0
        if pump_ids:
            comparisons = [i for (i,) in db.session.query(m.PumpCheckComparison.id).filter(db.or_(
                *(getattr(m.PumpCheckComparison, c.name).in_(pump_ids)
                  for c in m.PumpCheckComparison.__table__.columns
                  if any(f.column.table.name == "pump_check" for f in c.foreign_keys))))]
            if comparisons:
                changed += _delete(m.PumpCheckComparisonRequest,
                                   m.PumpCheckComparisonRequest.comparison_id.in_(comparisons))
                changed += _delete(m.PumpCheckComparison, m.PumpCheckComparison.id.in_(comparisons))
            changed += _delete(m.PumpCheckLike, m.PumpCheckLike.pump_check_id.in_(pump_ids))
            changed += _delete(m.PumpCheckComment, m.PumpCheckComment.pump_check_id.in_(pump_ids))
            changed += _delete_feed_refs(m, "pump_check", pump_ids)
        changed += _delete(m.PumpCheckComparisonRequest, m.PumpCheckComparisonRequest.user_id == uid)
        changed += _delete(m.PumpCheckComparison, m.PumpCheckComparison.user_id == uid)
        changed += _delete(m.PumpCheck, m.PumpCheck.user_id == uid)
        counts["pump"] = changed
    return counts


def _open_rows(owner):
    from app.health_consent_models import HealthErasure
    return HealthErasure.query.filter(HealthErasure.owner_id == owner,
                                      HealthErasure.completed_at.is_(None)).all()


def _log(event, owner, **fields):
    extra = " ".join(f"{k}={v}" for k, v in sorted(fields.items()))
    current_app.logger.info("health_erasure event=%s owner_id=%s %s", event, owner, extra)


def run(owner):
    """Serialize erasure, holds and consent decisions, including storage I/O.

    The independent authority connection holds no ORM changes. The ORM
    transaction is released before storage, then the User row is locked for
    the atomic row erase. Repeated workers recheck the obligation after waiting.
    """
    from app.services import health_consent
    with health_consent._authority(owner, exclusive=True) as authority:
        return _run_locked(owner, authority)


def _run_locked(owner, authority):
    """Erase what is owed for ``owner``. Returns 'none', 'completed' or 'held'.

    Raises ErasureUnavailable when storage cannot be released; nothing in the
    database changed then, and the erasure stays pending.
    """
    from app.models import User
    from app.services import health_consent
    now = datetime.utcnow()
    try:
        if not _open_rows(owner):
            db.session.rollback()
            return "none"
        if db.session.get(User, owner) is None:
            # No rows remain to inventory storage. Only canonical account
            # deletion can prove its objects were released and close this debt.
            db.session.rollback()
            raise ErasureUnavailable("account_storage_unproven")
        if health_consent._valid(health_consent._state(authority, owner)["record"]):
            # Unreachable while grants are refused during erasure; never erase
            # records collected under a live permission.
            db.session.rollback()
            current_app.logger.error("health_erasure event=skipped_live_grant owner_id=%s", owner)
            return "none"
        held = _held(owner, now)
        objects = storage(owner, held)
        db.session.rollback()  # no ORM transaction stays open over storage I/O
    except Exception:
        db.session.rollback()
        raise
    if objects and not s3_helper.is_enabled():
        raise ErasureUnavailable("storage_unconfigured")
    released = set()
    try:
        for kind, key in sorted(objects):
            _release(owner, kind, key)
            released.add((kind, key))
    except Exception as exc:
        _log("incomplete", owner, stage="storage", error_type=type(exc).__name__)
        raise ErasureUnavailable("storage") from exc
    try:
        user = (db.session.query(User).filter(User.id == owner)
                .populate_existing().with_for_update().one_or_none())
        if user is None:
            db.session.rollback()
            raise ErasureUnavailable("account_storage_unproven")
        held = _held(owner, now)
        if not storage(owner, held) <= released:
            db.session.rollback()
            raise ErasureUnavailable("storage_changed")
        counts = _erase_rows(user, held)
        if "workouts" not in held:
            # The ordinary best-effort account-deletion helper cannot prove
            # erasure. A partial Redis failure leaves the durable debt pending;
            # repeated ZREM of an already removed member is safe.
            _release_rankings(owner)
        summary = {"rows": counts, "objects": len(released), "held": sorted(held)}
        for row in _open_rows(owner):
            row.summary = summary
            if not held:
                row.completed_at = now
        db.session.commit()
    except ErasureUnavailable:
        raise
    except Exception as exc:
        db.session.rollback()
        _log("incomplete", owner, stage="local", error_type=type(exc).__name__)
        raise ErasureUnavailable("local") from exc
    _log("held" if held else "completed", owner, objects=len(released),
         held=",".join(sorted(held)) or "-")
    return "held" if held else "completed"


def run_pending(limit=200):
    """Retry every unfinished erasure; drop erasure records past their retention."""
    from app.health_consent_models import HealthErasure
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive batch size")
    # A held/failing low owner id must never starve later withdrawals. Scan
    # the initial owner range in bounded batches, once per maintenance run.
    ceiling = db.session.query(func.max(HealthErasure.owner_id)).filter(
        HealthErasure.completed_at.is_(None)).scalar() or 0
    db.session.rollback()
    results = {"completed": 0, "held": 0, "failed": 0}
    after = 0
    while after < ceiling:
        owners = [o for (o,) in db.session.query(HealthErasure.owner_id).filter(
            HealthErasure.completed_at.is_(None), HealthErasure.owner_id > after,
            HealthErasure.owner_id <= ceiling).distinct().order_by(
                HealthErasure.owner_id).limit(limit)]
        db.session.rollback()
        if not owners:
            break
        after = owners[-1]
        for owner in owners:
            try:
                outcome = run(owner)
            except Exception:
                db.session.rollback()
                results["failed"] += 1
                continue
            if outcome in results:
                results[outcome] += 1
    now = datetime.utcnow()
    try:
        cutoff = now.replace(year=now.year - ERASURE_RECORD_YEARS)
    except ValueError:  # leap day: anniversary is February 28
        cutoff = now.replace(year=now.year - ERASURE_RECORD_YEARS, day=28)
    results["records_expired"] = HealthErasure.query.filter(
        HealthErasure.completed_at.isnot(None), HealthErasure.completed_at < cutoff).delete(
        synchronize_session=False)
    # A documented legal hold is the lawful reason an erasure stays open past
    # 30 days; its review date is reported by hold_review_due instead.
    from app.health_consent_models import HealthLegalHold
    held_owners = select(HealthLegalHold.owner_id).where(HealthLegalHold.released_at.is_(None))
    overdue = HealthErasure.query.filter(
        HealthErasure.completed_at.is_(None),
        HealthErasure.owner_id.notin_(held_owners),
        HealthErasure.requested_at < datetime.utcnow() - ERASURE_DEADLINE).count()
    db.session.commit()  # leaves no transaction open after maintenance
    if overdue:
        current_app.logger.error("health_erasure event=overdue count=%d", overdue)
    results["overdue"] = overdue
    return results
