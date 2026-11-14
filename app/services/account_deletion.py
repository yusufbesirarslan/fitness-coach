"""The ONE account-deletion authority (LP-11).

    delete_account(user, family, claims)  ->  None, or a typed failure

Called only by the native `DELETE /api/v1/account` adapter
(`app/blueprints/mobile_account_deletion.py`). The owner is the verified Bearer
principal the middleware resolved — `user`, the `family` it authenticated
through, and that family's verified provider `claims` — and nothing a request
carries. Every identifier this module acts on (the Cognito identity, the local
rows, the storage keys) is re-derived here from that server-side state.

ORDER — and why (docs/MOBILE_ACCOUNT_DELETION.md §4):

0. Legal hold (docs/ACCOUNT_DELETION_AUDIT.md). The whole lifecycle below
   runs under the owner's EXCLUSIVE health authority, the same lock
   `health-legal-hold-add`, withdrawal and erasure take. Under it, before the
   provider is called, an unreleased `HealthLegalHold` refuses the deletion
   (`DeletionUnavailable("legal_hold")`): nothing is deleted and the account
   stays usable. No hold can be placed until this request ends, so the storage
   release and the purge cannot destroy held records. Step 4 checks again
   under the owner row lock, and `_purge_user` itself refuses a held owner.
   The authority is a transaction-scoped advisory lock on its own
   connection, not a row lock and not the ORM transaction.
1. Preflight, no side effect. Re-check the principal is coherent, decrypt the
   family's stored provider access token, and read the storage inventory. If
   the account owns objects but the object store is not configured here, stop:
   nothing irreversible has happened yet (`DeletionUnavailable`).
2. Identity. Cognito `DeleteUser` with THAT access token. It takes no username,
   so it can only delete the principal the token was issued to. A live Cognito
   identity recreates the local account at login (`mobile_auth._resolve_user`),
   which is why it goes FIRST — the same order the operator runbook uses
   (docs/STAGING.md). Failure here changes nothing (`DeletionUnavailable`), or,
   when the provider refuses this session's token, `ProviderSessionRejected`.
   `UserNotFoundException` for the token's own principal proves the identity is
   already gone (a retry after an incomplete deletion) and is treated as done.
3. Storage. Release every owned object through the canonical owner-checked
   helpers (`s3_helper.delete_managed_object` / `delete_meal_photo`) WHILE the
   rows naming them still exist: once the rows are purged the keys — which
   carry a random uuid — can never be re-derived, so a release that failed
   after the purge would strand private media with no record. Any failure
   stops here, before the purge (`DeletionIncomplete`).
4. Local purge, one transaction. Lock the owner row FOR UPDATE — the repo's
   owner-lock idiom; a concurrent child INSERT needs a key-share lock on the
   same row, so it either committed before (and is visible and purged now) or
   waits and then fails its foreign key. Re-read the inventory under the lock:
   if an object appeared that step 3 did not release, roll back and go round
   again (bounded). Then record the deleted-identity tombstone
   (`deleted_identity.record`: no login can give this Cognito subject a local
   row again) and run the canonical `_purge_user` — the same primitive the
   operator `cleanup-test-users` command uses — which deletes every row that
   references the user, including every mobile session family (their
   credentials cascade) and every web `CognitoSession`; then add the durable
   destruction record (`deletion_audit.record`: operation reference, time,
   path, row and object counts, the tombstone fingerprint, no content); then
   commit. The tombstone, the record and the sessions therefore end in the
   same commit as the account; there is no separate step that could fail on
   its own, and a purge that fails leaves no "completed" record.
5. Derived cache, best effort: drop the user from the Redis leaderboards.

Nothing here holds the ORM transaction or a row lock across a network call.

The three failures a caller can see, and what each one means for the account:

* `DeletionUnavailable` — nothing was deleted; the account is intact and
  usable; retry later.
* `ProviderSessionRejected` — nothing was deleted; the provider no longer
  accepts this session (e.g. signed out globally): sign in again, then retry.
* `DeletionIncomplete` — the identity step has run (the Cognito user is gone,
  so no new sign-in is possible) but local cleanup did not finish. The local
  account and its sessions are INTACT (the purge is one transaction), so the
  same session can retry; a retry skips the provider (already absent) and
  resumes at step 3. If the session can no longer authenticate, an operator
  finishes with `flask --app starter cleanup-test-users --username <u> --yes`.

Logs carry the request id, a stage and an exception TYPE. Only the
`incomplete` line names the internal user id, because an operator must be able
to finish that account. Never tokens, keys, e-mail or provider text.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

from flask import current_app

import s3_helper
from app.extensions import db
from app.models import MealLog, MealPhotoCleanup, PumpCheck, User
from app.observability import current_request_id
from app.services import (
    cognito_service, deleted_identity, deletion_audit, gamification,
    health_erasure, session_store,
)
from app.services.ai_gate import (
    BlockingConcurrencyLimit, blocking_concurrency_slot,
)


# How many times step 4 may find an object step 3 did not release (a Pump
# Check or meal photo committed by a racing request) before giving up. Each
# round only adds keys, so this bounds a pathological writer, not normal use.
MAX_RELEASE_ROUNDS = 3

# Provider answers that say the provider, not the request, is at fault.
_PROVIDER_TRANSIENT = frozenset({
    "", "TooManyRequestsException", "InternalErrorException",
    "LimitExceededException", "ServiceUnavailableException",
})
# The token's own principal no longer exists: the identity step is done.
_IDENTITY_ABSENT = "UserNotFoundException"
# The provider refuses this session's access token (revoked or expired).
_SESSION_REJECTED = "NotAuthorizedException"

MEAL_PHOTO = "meal_photo"
MANAGED_OBJECT = "managed_object"


class AccountDeletionFailure(Exception):
    def __init__(self, stage, retry_after=None):
        super().__init__(stage)
        self.stage = stage
        self.retry_after = retry_after


class DeletionUnavailable(AccountDeletionFailure):
    """Nothing was deleted. The account is intact; retry later."""


class ProviderSessionRejected(AccountDeletionFailure):
    """Nothing was deleted. This session cannot authorize deletion."""


class DeletionIncomplete(AccountDeletionFailure):
    """The identity is gone; local cleanup did not finish. Retry."""


@dataclass(frozen=True)
class StorageInventory:
    """Owned object references, derived ONLY from the owner's own rows.

    `objects` holds `(kind, key)` pairs that the canonical helpers will accept
    for this owner. `unmanaged` counts non-empty references they refuse — a
    key that is not in the grammar this application mints, or names another
    owner. Those are not provably ours (F4): they are never deleted and never
    block deletion; the row that names them is purged like any other.
    """
    objects: frozenset
    unmanaged: int


def _event(event, stage="-", error_type="-", level="info", user_id=None):
    if user_id is None:
        getattr(current_app.logger, level)(
            "account_deletion event=%s stage=%s error_type=%s request_id=%s",
            event, stage, error_type, current_request_id())
    else:
        getattr(current_app.logger, level)(
            "account_deletion event=%s stage=%s error_type=%s user_id=%s "
            "request_id=%s",
            event, stage, error_type, user_id, current_request_id())


def owned_storage(user_id):
    """Read the owner's storage inventory. Every query is scoped to `user_id`."""
    references = []
    avatar = db.session.query(User.profile_picture_key).filter(
        User.id == user_id).scalar()
    references.append((MANAGED_OBJECT, avatar))
    references.extend(
        (MANAGED_OBJECT, key) for (key,) in db.session.query(
            PumpCheck.image_key).filter(PumpCheck.user_id == user_id))
    references.extend(
        (MEAL_PHOTO, key) for (key,) in db.session.query(
            MealLog.photo_key).filter(MealLog.user_id == user_id))
    references.extend(
        (MEAL_PHOTO, key) for (key,) in db.session.query(
            MealPhotoCleanup.photo_key).filter(
                MealPhotoCleanup.user_id == user_id))
    objects, unmanaged = set(), 0
    for kind, key in references:
        if not key:
            continue
        deletable = (s3_helper.meal_photo_key_is_deletable(key, user_id)
                     if kind == MEAL_PHOTO
                     else s3_helper.managed_object_key_is_deletable(
                         key, user_id))
        if deletable:
            objects.add((kind, key))
        else:
            unmanaged += 1
    return StorageInventory(frozenset(objects), unmanaged)


def _preflight(user, family, claims):
    """Coherent principal + its provider token + inventory; no side effect."""
    try:
        user_id = user.id
        sub = user.cognito_sub
        coherent = (family is not None and bool(sub)
                    and family.user_id == user_id
                    and family.cognito_sub == sub
                    and (claims or {}).get("sub") == sub)
        provider_access = (session_store.decrypt_token(
            family.cognito_access_token) if coherent else None)
    except Exception as exc:
        # An unreadable principal (e.g. its rows were purged by a concurrent
        # deletion of this same account) is a dead session, not a fault.
        db.session.rollback()
        raise ProviderSessionRejected("principal") from exc
    if not coherent:
        raise ProviderSessionRejected("principal")
    if not provider_access:
        raise ProviderSessionRejected("principal")
    try:
        inventory = owned_storage(user_id)
    except Exception as exc:
        raise DeletionUnavailable("preflight") from exc
    finally:
        # The provider round-trip is next; no transaction stays open over it.
        db.session.rollback()
    if inventory.objects and not s3_helper.is_enabled():
        raise DeletionUnavailable("storage_unconfigured")
    return user_id, sub, provider_access, inventory


@contextmanager
def _hold_fence(user_id):
    """Exclusive health authority for the owner: no legal hold can be placed,
    and no erasure or consent decision can run, until the deletion ends."""
    from app.services import health_consent
    stack = ExitStack()
    try:
        stack.enter_context(health_consent._authority(user_id, exclusive=True))
    except Exception as exc:
        # Busy (another request of this owner holds it) or unavailable.
        # Nothing has been deleted yet.
        raise DeletionUnavailable("hold_authority") from exc
    try:
        yield
    finally:
        try:
            stack.close()
        except Exception:
            # Releasing a lock cannot undo a purge that already committed.
            pass


def _refuse_if_held(user_id):
    """Before the provider call, under the fence: a held account is refused."""
    try:
        held = health_erasure.active_hold(user_id)
    except Exception as exc:
        raise DeletionUnavailable("preflight") from exc
    finally:
        db.session.rollback()
    if held:
        # The controller must learn that a held owner asked for deletion: the
        # request is theirs to answer and to honour once the hold is released.
        _event("refused", "legal_hold", "-", "warning", user_id)
        raise DeletionUnavailable("legal_hold")


def _delete_identity(provider_access):
    """Returns what happened at the provider: 'deleted' or 'already_absent'."""
    try:
        with blocking_concurrency_slot():
            cognito_service.delete_user(provider_access)
    except BlockingConcurrencyLimit as exc:
        raise DeletionUnavailable("identity", retry_after=15) from exc
    except cognito_service.CognitoServiceError as exc:
        if exc.code == _IDENTITY_ABSENT:
            _event("identity_already_absent", "identity")
            return "already_absent"
        if exc.code == _SESSION_REJECTED:
            raise ProviderSessionRejected("identity") from exc
        raise DeletionUnavailable("identity") from exc
    except Exception as exc:
        raise DeletionUnavailable("identity") from exc
    return "deleted"


def _release(user_id, kind, key):
    """Release one owned object; raise if it cannot be proven released."""
    if kind == MEAL_PHOTO:
        s3_helper.delete_meal_photo(key, user_id)
        return
    if not s3_helper.delete_managed_object(key, user_id):
        # False means "not released" (unmanaged or no object store) — the
        # inventory only holds deletable keys, so it is never a success here.
        raise s3_helper.S3Error("managed object not released")


def _purge_if_released(user_id, sub, released, identity_step):
    """Step 4. Returns None when purged (or already gone), else the inventory
    that appeared under the lock and still needs releasing."""
    from app.cli import _purge_user

    owner = (db.session.query(User).filter(User.id == user_id)
             .populate_existing().with_for_update().one_or_none())
    if owner is None:
        # A concurrent deletion of this same account already committed (with
        # its tombstone). Record it anyway — idempotent — so this success can
        # never leave the deleted subject without one, whoever purged the row.
        deleted_identity.record(sub)
        db.session.commit()
        return None
    if owner.cognito_sub != sub:
        db.session.rollback()
        raise DeletionIncomplete("identity_changed")
    inventory = owned_storage(user_id)
    if not inventory.objects <= released:
        db.session.rollback()
        return inventory
    if health_erasure.active_hold(user_id):
        # Unreachable while the fence is held; kept because the owner row
        # lock is the one place a hold INSERT must also pass (its foreign key).
        db.session.rollback()
        raise DeletionIncomplete("legal_hold")
    # The anti-resurrection tombstone commits WITH the purge or not at all:
    # "user row gone, no tombstone" never exists (deleted_identity module doc).
    deleted_identity.record(sub)
    # Storage inventory was proved released above; close only these O2 debts.
    from app.health_consent_models import HealthErasure
    from datetime import datetime
    closed = HealthErasure.query.filter_by(owner_id=user_id, completed_at=None).update(
        {"completed_at": datetime.utcnow(), "summary": {"account_deleted": True}},
        synchronize_session=False)
    rows = _purge_user(owner)
    # The destruction record commits WITH the purge or not at all.
    audit = deletion_audit.receipt(deletion_audit.record(
        channel=deletion_audit.SELF_SERVICE, identity_step=identity_step,
        subject=sub, request_id=current_request_id(),
        counts={"rows": rows, "objects_released": len(released),
                "unmanaged_references": inventory.unmanaged,
                "health_erasures_closed": closed}))
    db.session.commit()
    deletion_audit.announce(audit)
    return None


def delete_account(user, family, claims):
    """Delete the authenticated account. Returns None only when it is gone."""
    user_id, sub, provider_access, inventory = _preflight(user, family, claims)
    with _hold_fence(user_id):
        _refuse_if_held(user_id)
        _delete_and_purge(user_id, sub, provider_access, inventory)


def _delete_and_purge(user_id, sub, provider_access, inventory):
    identity_step = _delete_identity(provider_access)
    # From here on the Cognito identity is gone: every failure is incomplete.

    released = set()
    stage = "storage"
    try:
        for _round in range(MAX_RELEASE_ROUNDS):
            stage = "storage"
            for kind, key in sorted(inventory.objects - released):
                _release(user_id, kind, key)
                released.add((kind, key))
            stage = "local_purge"
            remaining = _purge_if_released(user_id, sub, released, identity_step)
            if remaining is None:
                break
            inventory = remaining
        else:
            raise DeletionIncomplete("storage_unsettled")
    except DeletionIncomplete as exc:
        _event("incomplete", exc.stage, "-", "error", user_id)
        raise
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        _event("incomplete", stage, type(exc).__name__, "error", user_id)
        raise DeletionIncomplete(stage) from exc

    if inventory.unmanaged:
        current_app.logger.warning(
            "account_deletion event=unmanaged_references_skipped count=%d "
            "request_id=%s", inventory.unmanaged, current_request_id())
    gamification.lb_remove_user(user_id)
    _event("completed")
