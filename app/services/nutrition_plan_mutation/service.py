"""Transaction owner for targeted Nutrition Plan edits (Adaptive Coach C1).

Two phases, because F-3 requires explicit confirmation of every re-estimate:

    prepare_meal_edit   read the plan, apply the item edit to a copy, run the
                        whole-meal estimate (provider call, NO transaction
                        held), recompute meal + daily totals, validate with the
                        persisted schema. Writes nothing. The result is what a
                        caller shows the user and stores for confirmation.
    apply_prepared_edit lock owner -> D1 fence -> plan row, replay by key,
                        require the plan to be EXACTLY the prepared base, then
                        write document + version + journal in ONE commit.

``undo_last_nutrition_edit`` restores the exact stored bytes of the state the
newest still-effective edit started from, as a new forward version.

Lock order is ``replace_nutrition_plan``'s: owner row (NO KEY UPDATE, taken by
the fence / ``lock_plan_owner``) -> ``nutrition_plan`` row. The database is the
replay arbiter (``uq_nutrition_mutation_user_key``): a lost insert race replays
the winner's durable result. Nothing here logs plan content.
"""
import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import NutritionPlanMutationRecord, _new_plan_lineage_id
from app.services import ai_consent
from app.services.nutrition_plan_schema import (
    NutritionPlanInvalid,
    validate_nutrition_plan_for_save,
)
from app.services.nutrition_plan_store import newest_plan, newest_plan_query
from app.services.plan_mutation import PLAN_MUTATION_ACTORS, MutationContext
from app.services.plan_owner_lock import lock_plan_owner

from .commands import apply_item_edit, command_type, semantic_fingerprint, validate_command
from .errors import (
    InvalidNutritionEdit,
    NutritionIdempotencyConflict,
    NutritionPlanNotEditable,
    NutritionPlanNotFound,
    NutritionPlanStateConflict,
    NutritionUndoConflict,
    NutritionUndoUnavailable,
)
from .estimate import estimate_meal, recompute

KIND_EDIT = "edit"
KIND_UNDO = "undo"
OUTCOME_APPLIED = "applied"
OUTCOME_NO_OP = "no_op"
UNDO_COMMAND_TYPE = "undo_last_edit"
_UNDO_FINGERPRINT = hashlib.sha256(b"nutrition-edit/v1\0undo").hexdigest()
_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
_FEATURE = "nutrition_plan"


def snapshot_fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _serialize(document):
    return json.dumps(document, ensure_ascii=False)


@dataclass(frozen=True)
class PreparedMealEdit:
    """A fully computed edit, bound to the exact plan state it was made from.

    Plain data only, so a confirmation layer can store it (``to_payload``) and
    hand it back unchanged. Applying it never re-estimates: what the user
    confirmed is what is written, or nothing is.
    """
    command_type: str
    command_fingerprint: str
    plan_id: int
    lineage_id: Optional[str]
    base_version: int
    base_fingerprint: str
    changed: bool
    document: Any
    provenance: Optional[dict] = field(default=None)

    def to_payload(self):
        return {
            "v": 1, "command_type": self.command_type,
            "command_fingerprint": self.command_fingerprint,
            "plan_id": self.plan_id, "lineage_id": self.lineage_id,
            "base_version": self.base_version,
            "base_fingerprint": self.base_fingerprint,
            "changed": self.changed, "document": self.document,
            "provenance": self.provenance,
        }

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise InvalidNutritionEdit("unreadable prepared edit")
        try:
            return cls(**{k: payload[k] for k in (
                "command_type", "command_fingerprint", "plan_id", "lineage_id",
                "base_version", "base_fingerprint", "changed", "document",
                "provenance")})
        except (KeyError, TypeError):
            raise InvalidNutritionEdit("unreadable prepared edit") from None


@dataclass(frozen=True)
class NutritionEditResult:
    outcome: str
    plan: Any
    plan_version: int
    mutation_id: str
    replayed: bool = False

    @property
    def changed(self):
        return self.outcome == OUTCOME_APPLIED


def _validate_context(context):
    if not isinstance(context, MutationContext):
        raise InvalidNutritionEdit("a mutation context is required")
    key = context.idempotency_key
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise InvalidNutritionEdit("idempotency key is malformed")
    if context.actor not in PLAN_MUTATION_ACTORS:
        raise InvalidNutritionEdit("unsupported actor")
    reason = context.reason
    if reason is not None:
        if not isinstance(reason, str) or len(reason.strip()) > 200:
            raise InvalidNutritionEdit("reason out of bounds")
        reason = reason.strip() or None
    return MutationContext(idempotency_key=key, actor=context.actor, reason=reason)


def _parsed(text):
    try:
        return validate_nutrition_plan_for_save(json.loads(text))
    except (TypeError, ValueError, NutritionPlanInvalid):
        raise NutritionPlanNotEditable("the saved plan cannot be edited") from None


# ── prepare ────────────────────────────────────────────────────────────────


def prepare_meal_edit(user_id, command, estimate_items):
    """Compute one meal edit for ``user_id``'s own plan. Writes nothing.

    ``estimate_items`` is the injected estimator (production:
    ``estimate.server_estimator``, run by the caller inside its D1 operation).
    The read transaction is closed BEFORE the estimator runs, so no lock or
    snapshot is held across provider I/O.
    """
    command = validate_command(command)
    try:
        row = newest_plan(user_id)
        if row is None:
            raise NutritionPlanNotFound("no saved nutrition plan")
        base_text = row.plan_data
        plan_id, lineage_id, version = row.id, row.lineage_id, row.mutation_version
    finally:
        db.session.rollback()

    document = _parsed(base_text)
    edited, changed = apply_item_edit(document, command)
    provenance = None
    if changed:
        estimates = estimate_meal(edited[command.meal]["yemekler"], estimate_items)
        edited, provenance = recompute(copy.deepcopy(edited), command.meal, estimates)
        try:
            edited = validate_nutrition_plan_for_save(edited)
        except NutritionPlanInvalid:
            raise InvalidNutritionEdit("the edited plan is not a valid plan") from None
    return PreparedMealEdit(
        command_type=command_type(command),
        command_fingerprint=semantic_fingerprint(command),
        plan_id=plan_id, lineage_id=lineage_id, base_version=version,
        base_fingerprint=snapshot_fingerprint(base_text),
        changed=changed, document=edited if changed else document,
        provenance=provenance,
    )


# ── shared write helpers ───────────────────────────────────────────────────


def _locked_plan(user_id):
    row = (newest_plan_query(user_id).populate_existing().with_for_update().first())
    if row is None:
        raise NutritionPlanNotFound("no saved nutrition plan")
    return row


def _find(user_id, key):
    return NutritionPlanMutationRecord.query.filter_by(
        user_id=user_id, idempotency_key=key).first()


def _replay(record, expected_type, expected_fingerprint):
    if (record.command_type != expected_type
            or record.command_fingerprint != expected_fingerprint):
        raise NutritionIdempotencyConflict(
            "this operation key is bound to a different change") from None
    return NutritionEditResult(
        outcome=record.outcome, plan=json.loads(record.after_snapshot),
        plan_version=record.after_version, mutation_id=record.public_id,
        replayed=True)


def _arbitrated(user_id, key, expected_type, expected_fingerprint):
    record = _find(user_id, key)
    try:
        if record is None:
            return None
        return _replay(record, expected_type, expected_fingerprint)
    finally:
        db.session.rollback()


def _record(*, user_id, lineage_id, context, kind, ctype, fingerprint, outcome,
            before_version, after_version, before, after, provenance=None,
            reverts=None):
    return NutritionPlanMutationRecord(
        user_id=user_id, plan_lineage_id=lineage_id, operation_kind=kind,
        command_type=ctype, command_fingerprint=fingerprint,
        actor=context.actor, reason=context.reason, outcome=outcome,
        before_version=before_version, after_version=after_version,
        before_snapshot=before, after_snapshot=after,
        before_fingerprint=snapshot_fingerprint(before),
        after_fingerprint=snapshot_fingerprint(after),
        provenance=(json.dumps(provenance, ensure_ascii=False)
                    if provenance is not None else None),
        idempotency_key=context.idempotency_key, reverts_mutation_id=reverts,
    )


# ── apply ──────────────────────────────────────────────────────────────────


def apply_prepared_edit(user_id, prepared, context, *, authority):
    """Write a prepared edit atomically, or nothing.

    ``authority`` is the D1 Nutrition Plan operation the estimate was made
    under; it is fenced under the owner lock in THIS transaction (``None`` is
    refused, as for ``replace_nutrition_plan``). The plan must still be the
    exact prepared base — same row, lineage, version and bytes — else
    ``NutritionPlanStateConflict`` and zero writes.
    """
    if not isinstance(prepared, PreparedMealEdit):
        raise InvalidNutritionEdit("a prepared edit is required")
    context = _validate_context(context)
    ctype = prepared.command_type
    # The replay identity covers the written result too: one key can never
    # stand for two different estimates of the same edit.
    fingerprint = hashlib.sha256(
        (prepared.command_fingerprint + "\0"
         + snapshot_fingerprint(_serialize(prepared.document))).encode()).hexdigest()

    try:
        existing = _find(user_id, context.idempotency_key)
        if existing is not None:
            result = _replay(existing, ctype, fingerprint)
            db.session.rollback()
            return result

        lock_plan_owner(user_id)
        ai_consent.fence(authority, user_id, _FEATURE)
        row = _locked_plan(user_id)

        existing = _find(user_id, context.idempotency_key)
        if existing is not None:
            result = _replay(existing, ctype, fingerprint)
            db.session.rollback()
            return result

        before = row.plan_data
        if (row.id != prepared.plan_id
                or row.lineage_id != prepared.lineage_id
                or row.mutation_version != prepared.base_version
                or snapshot_fingerprint(before) != prepared.base_fingerprint):
            raise NutritionPlanStateConflict("the plan changed since this edit was prepared")

        if row.lineage_id is None:
            # First targeted edit of a row written without one (pre-C1 or the
            # previous revision during a release): its history starts here.
            row.lineage_id = _new_plan_lineage_id()

        if prepared.changed:
            # Re-validated at the write: a stored payload is data, not trust.
            try:
                document = validate_nutrition_plan_for_save(prepared.document)
            except NutritionPlanInvalid:
                raise InvalidNutritionEdit("the prepared plan is not valid") from None
            after = _serialize(document)
            after_version = row.mutation_version + 1
            outcome = OUTCOME_APPLIED
        else:
            document = _parsed(before)
            after, after_version, outcome = before, row.mutation_version, OUTCOME_NO_OP

        record = _record(
            user_id=user_id, lineage_id=row.lineage_id, context=context,
            kind=KIND_EDIT, ctype=ctype, fingerprint=fingerprint, outcome=outcome,
            before_version=row.mutation_version, after_version=after_version,
            before=before, after=after, provenance=prepared.provenance)
        db.session.add(record)
        if prepared.changed:
            row.plan_data = after
            row.mutation_version = after_version
        db.session.flush()
        mutation_id = record.public_id
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        arbitrated = _arbitrated(user_id, context.idempotency_key, ctype, fingerprint)
        if arbitrated is None:
            raise NutritionPlanStateConflict(
                "the plan changed while this edit was being applied") from None
        return arbitrated
    except Exception:
        db.session.rollback()
        raise
    return NutritionEditResult(outcome=outcome, plan=document,
                               plan_version=after_version, mutation_id=mutation_id)


# ── undo ───────────────────────────────────────────────────────────────────


def _latest_reversible(user_id, lineage_id):
    reverted = db.session.query(NutritionPlanMutationRecord.reverts_mutation_id).filter(
        NutritionPlanMutationRecord.user_id == user_id,
        NutritionPlanMutationRecord.reverts_mutation_id.isnot(None))
    return (NutritionPlanMutationRecord.query.filter(
        NutritionPlanMutationRecord.user_id == user_id,
        NutritionPlanMutationRecord.plan_lineage_id == lineage_id,
        NutritionPlanMutationRecord.operation_kind == KIND_EDIT,
        NutritionPlanMutationRecord.outcome == OUTCOME_APPLIED,
        NutritionPlanMutationRecord.id.notin_(reverted),
    ).order_by(NutritionPlanMutationRecord.id.desc()).first())


def undo_last_nutrition_edit(user_id, context):
    """Reverse the newest still-effective edit of the owner's current plan.

    No estimate and no new AI result: the exact earlier bytes come back, so a
    user's undo needs no D1 authority of its own. Inside an AI operation the
    write is still fenced (``fence_active``). Fails closed unless the plan is
    exactly the state that edit produced.
    """
    context = _validate_context(context)
    try:
        existing = _find(user_id, context.idempotency_key)
        if existing is not None:
            result = _replay(existing, UNDO_COMMAND_TYPE, _UNDO_FINGERPRINT)
            db.session.rollback()
            return result

        lock_plan_owner(user_id)
        ai_consent.fence_active(user_id)
        row = _locked_plan(user_id)
        existing = _find(user_id, context.idempotency_key)
        if existing is not None:
            result = _replay(existing, UNDO_COMMAND_TYPE, _UNDO_FINGERPRINT)
            db.session.rollback()
            return result

        target = (_latest_reversible(user_id, row.lineage_id)
                  if row.lineage_id is not None else None)
        if target is None:
            raise NutritionUndoUnavailable("there is no edit to undo")
        before = row.plan_data
        if (snapshot_fingerprint(before) != target.after_fingerprint
                or row.mutation_version < target.after_version):
            raise NutritionUndoConflict("the plan has changed since that edit")
        restored = _parsed(target.before_snapshot)
        after_version = row.mutation_version + 1
        record = _record(
            user_id=user_id, lineage_id=row.lineage_id, context=context,
            kind=KIND_UNDO, ctype=UNDO_COMMAND_TYPE, fingerprint=_UNDO_FINGERPRINT,
            outcome=OUTCOME_APPLIED, before_version=row.mutation_version,
            after_version=after_version, before=before,
            after=target.before_snapshot, reverts=target.id)
        db.session.add(record)
        row.plan_data = target.before_snapshot
        row.mutation_version = after_version
        db.session.flush()
        mutation_id = record.public_id
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        arbitrated = _arbitrated(
            user_id, context.idempotency_key, UNDO_COMMAND_TYPE, _UNDO_FINGERPRINT)
        if arbitrated is None:
            raise NutritionUndoConflict("the plan has changed since that edit") from None
        return arbitrated
    except Exception:
        db.session.rollback()
        raise
    return NutritionEditResult(outcome=OUTCOME_APPLIED, plan=restored,
                               plan_version=after_version, mutation_id=mutation_id)
