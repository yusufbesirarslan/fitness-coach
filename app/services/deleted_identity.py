"""Anti-resurrection record for provider identities deleted by LP-11.

    record(sub)                 inside the account-deletion purge transaction
    refuse_if_deleted(sub)      before a login commits a local row for `sub`

The race this closes (docs/MOBILE_ACCOUNT_DELETION.md §6): a login that
authenticated at Cognito BEFORE `DeleteUser` and reaches local-user resolution
only AFTER the purge committed finds no row for its verified subject and would
provision a new, empty one — a deleted identity back with a live session.
Nothing the deletion leaves behind in `user` can stop that, so the purge
leaves THIS instead: one row per deleted Cognito subject, written in the SAME
transaction as `_purge_user`. Afterwards the state "user row gone AND no
tombstone" cannot exist for a subject `DELETE /api/v1/account` removed.

What is stored: a keyed one-way fingerprint of the subject and the time. Never
the subject itself, a username, an e-mail, a token or any profile field. It is
a security record, not retained account data, and is kept indefinitely: a
Cognito `sub` is never reissued, so the fingerprint can only ever match the
identity that was deleted — a person who signs up again gets a new `sub`, a
different fingerprint, and is not blocked.

Key: the application `SECRET_KEY`, domain-separated by `_SUBKEY_INFO` (the
same subkey idiom as the persisted pump-check/nutrition identities). Rotating
`SECRET_KEY` orphans existing fingerprints; that only reopens the race for
logins already in flight across the rotation, because a deleted identity can
no longer authenticate at the provider.

WHY THE CHECK IS RACE-FREE (the caller's side of the contract):
the login puts the subject on its row first — a new `User(cognito_sub=sub)` or
`row.cognito_sub = sub` on a reconciled one — and only then calls
`refuse_if_deleted`, which flushes that write before it reads, in the same
transaction, before anything is committed or a session is issued.
`uq_user_cognito_sub` serializes that write against the purge: while the
deleted row still holds `sub` the write fails; while the purge that removes it
is uncommitted the write WAITS on it; so the write can only succeed after the
purge — and its tombstone — committed, and the check, a later statement
(READ COMMITTED), sees the tombstone. There is no check-then-create window,
no lock is held across a provider call, and nothing global is locked.
"""
import hashlib
import hmac
from datetime import datetime

from flask import current_app

from app.extensions import db
from app.models import DeletedIdentityTombstone


_SUBKEY_INFO = b"axisai/deleted-identity/v1"


class IdentityDeleted(Exception):
    """The verified provider subject was deleted by account deletion."""


def fingerprint(sub):
    secret = current_app.config["SECRET_KEY"]
    material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    subkey = hmac.new(material, _SUBKEY_INFO, hashlib.sha256).digest()
    return hmac.new(subkey, sub.encode("utf-8"), hashlib.sha256).hexdigest()


def record(sub):
    """Add the tombstone to the CURRENT transaction; the caller commits it
    together with the purge. Idempotent for a retried deletion."""
    value = fingerprint(sub)
    if db.session.get(DeletedIdentityTombstone, value) is None:
        db.session.add(DeletedIdentityTombstone(
            fingerprint=value, deleted_at=datetime.utcnow()))
        db.session.flush()


def refuse_if_deleted(sub):
    """Raise `IdentityDeleted` when `sub` was deleted.

    Call AFTER adding/assigning the local row that carries `sub` and BEFORE
    committing it or issuing any session. The row is flushed HERE, first, so
    its write reaches `uq_user_cognito_sub` before the check reads (module doc).
    The caller rolls back on `IdentityDeleted`."""
    db.session.flush()
    found = db.session.query(DeletedIdentityTombstone.fingerprint).filter(
        DeletedIdentityTombstone.fingerprint == fingerprint(sub)).first()
    if found is not None:
        raise IdentityDeleted()
