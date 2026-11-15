"""Durable destruction record for completed account deletions.

    record(...)          inside the purge transaction, before its commit
    receipt(row)         plain values, read before the commit
    announce(receipt)    after the commit: one log line, the off-database copy
    purge_expired()      daily maintenance: drop records past `retain_until`
    verify()             operator, read only: integrity and tombstone coverage
    export(since)        operator, read only: sealed rows for an off-database copy
    match_subjects(...)  operator, read only: post-restore reconciliation

Founder decision AXISAI-V1-DELETION-AUDIT-LEGAL-HOLD-SOURCE-CLOSURE-20261114:
the fingerprint-plus-time tombstone is not accepted as the record of a
deletion operation, and the 30-day application log cannot carry a three-year
obligation. So every purge that commits also commits ONE row here
(docs/ACCOUNT_DELETION_AUDIT.md).

What a row holds: an opaque operation reference, the server request id (HTTP
path only), the path taken, what happened at the identity provider, the
tombstone fingerprint of the deleted subject, the UTC time, row counts per
table and object counts. Never health data, consent text, a name, an e-mail,
a username, the internal user id or the Cognito subject. `record` refuses any
`counts` value that is not a non-negative integer under a short key, so a
payload cannot ride in through it.

Atomicity: `record` only adds to the caller's transaction. The row commits
with the purge or not at all, so a failed purge never leaves a "completed"
record, and a retry that finishes later leaves exactly one.

Integrity: `integrity` is HMAC-SHA256 over the canonical form of every other
field, keyed by a subkey of `SECRET_KEY` (same idiom as the tombstone). It
detects a changed row. It does not detect a removed row; `verify` reports
tombstones written since this record existed that have no record, and the
after-commit log line is the copy outside the database. Rotating `SECRET_KEY`
makes old MACs unverifiable; keep the old key for verification.

Retention: `retain_until` = completion + three years, the minimum. A longer
legal obligation is met by moving `retain_until` later for that row; nothing
here shortens it. `purge_expired` is the only disposal.

Restore limit, stated plainly: this table lives in the database it describes.
Restoring an older snapshot removes the rows written after it. The log line is
independent but lives only as long as the log group (30 days configured) and
carries no subject fingerprint, so it counts lost deletions without naming them.
docs/BACKUP_RESTORE_REDELETION_RUNBOOK.md owns that gap.
"""
import hashlib
import hmac
import json
import secrets
from datetime import datetime

from flask import current_app
from sqlalchemy import text

from app.extensions import db
from app.models import AccountDeletionRecord, User
from app.services import deleted_identity


RECORD_YEARS = 3
SELF_SERVICE = "self_service"
OPERATOR_CLEANUP = "operator_cleanup"
CHANNELS = (SELF_SERVICE, OPERATOR_CLEANUP)
IDENTITY_STEPS = ("deleted", "already_absent", "not_attempted")
COMPLETED = "completed"
FULL_ACCOUNT = "full_account"
# `rows` is {table name: rows deleted}. The others are single counts; None
# means "this path did not do that step" (operator cleanup releases no object).
COUNT_KEYS = ("objects_released", "unmanaged_references", "health_erasures_closed")

_SUBKEY_INFO = b"axisai/account-deletion-record/v1"
_SEALED = ("operation_ref", "request_id", "channel", "outcome", "identity_step",
           "subject_fingerprint", "scope", "counts", "completed_at", "retain_until")
_MAX_KEY = 64


class InvalidDeletionRecord(ValueError):
    """The caller offered something this record must not hold."""


def _count(value, optional=False):
    if value is None and optional:
        return None
    if type(value) is not int or value < 0:
        raise InvalidDeletionRecord("counts must be non-negative integers")
    return value


def _clean_counts(counts):
    if not isinstance(counts, dict) or set(counts) != {"rows", *COUNT_KEYS}:
        raise InvalidDeletionRecord("unexpected count keys")
    rows = counts["rows"]
    if not isinstance(rows, dict):
        raise InvalidDeletionRecord("rows must map table names to counts")
    clean = {}
    for name, value in rows.items():
        if (not isinstance(name, str) or not 0 < len(name) <= _MAX_KEY
                or not name.replace("_", "").isalnum() or not name.isascii()):
            raise InvalidDeletionRecord("row keys must be table names")
        if _count(value):
            clean[name] = value
    return {"rows": dict(sorted(clean.items())),
            **{key: _count(counts[key], optional=True) for key in COUNT_KEYS}}


def retain_until(completed_at):
    try:
        return completed_at.replace(year=completed_at.year + RECORD_YEARS)
    except ValueError:  # 29 February: the anniversary is 1 March
        return completed_at.replace(year=completed_at.year + RECORD_YEARS,
                                    month=3, day=1)


def receipt(row):
    """Plain values of one record. Call before the commit expires the row."""
    return {
        "operation_ref": row.operation_ref, "request_id": row.request_id,
        "channel": row.channel, "outcome": row.outcome,
        "identity_step": row.identity_step,
        "subject_fingerprint": row.subject_fingerprint, "scope": row.scope,
        "counts": row.counts,
        "completed_at": row.completed_at.isoformat(timespec="seconds"),
        "retain_until": row.retain_until.isoformat(timespec="seconds"),
        "integrity": row.integrity,
    }


def _mac(values):
    secret = current_app.config["SECRET_KEY"]
    material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    subkey = hmac.new(material, _SUBKEY_INFO, hashlib.sha256).digest()
    canonical = json.dumps({key: values[key] for key in _SEALED},
                           sort_keys=True, separators=(",", ":"))
    return hmac.new(subkey, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def record(*, channel, identity_step, subject, counts, request_id=None, now=None):
    """Add the record to the CURRENT transaction; the caller commits it with
    the purge. `subject` is the Cognito subject or None; only its fingerprint
    is kept."""
    if channel not in CHANNELS or identity_step not in IDENTITY_STEPS:
        raise InvalidDeletionRecord("unknown channel or identity step")
    if request_id in (None, "", "-"):
        request_id = None
    elif (not isinstance(request_id, str) or len(request_id) > 80
          or not request_id.isascii() or not request_id.isprintable()):
        raise InvalidDeletionRecord("request id is not a server reference")
    completed_at = (now or datetime.utcnow()).replace(microsecond=0, tzinfo=None)
    row = AccountDeletionRecord(
        operation_ref=secrets.token_hex(16), request_id=request_id,
        channel=channel, outcome=COMPLETED, identity_step=identity_step,
        subject_fingerprint=deleted_identity.fingerprint(subject) if subject else None,
        scope=FULL_ACCOUNT, counts=_clean_counts(counts),
        completed_at=completed_at, retain_until=retain_until(completed_at))
    row.integrity = _mac(receipt(row))
    db.session.add(row)
    db.session.flush()
    return row


def announce(values):
    """The copy outside the database. Never raises: the purge has committed.

    The tombstone fingerprint is deliberately NOT logged (LP-11 invariant:
    the application never logs it; only the operator export prints it). The line therefore proves THAT an
    operation completed and seals the row (the MAC covers the fingerprint),
    but does not say WHICH account; see the restore runbook."""
    try:
        current_app.logger.info(
            "account_deletion_audit event=recorded operation_ref=%s channel=%s "
            "identity_step=%s completed_at=%s retain_until=%s integrity=%s "
            "request_id=%s",
            values["operation_ref"], values["channel"], values["identity_step"],
            values["completed_at"], values["retain_until"], values["integrity"],
            values["request_id"] or "-")
    except Exception:
        pass


def purge_expired(now=None):
    """Drop records whose retention has ended. Returns how many."""
    removed = AccountDeletionRecord.query.filter(
        AccountDeletionRecord.retain_until < (now or datetime.utcnow())).delete(
        synchronize_session=False)
    db.session.commit()
    if removed:
        current_app.logger.info(
            "account_deletion_audit event=expired count=%d", removed)
    return removed


def verify():
    """Read only. Recompute every MAC; count tombstones that lack a record.

    A tombstone older than the first record predates this table and is not
    counted. One written since then without a record means a record is missing
    (removed, or lost to a restore) and needs the runbook.
    """
    try:
        failures, fingerprints, first = [], set(), None
        total = 0
        for row in AccountDeletionRecord.query.order_by(AccountDeletionRecord.id):
            total += 1
            values = receipt(row)
            if not hmac.compare_digest(_mac(values), row.integrity or ""):
                failures.append(row.operation_ref)
            if row.subject_fingerprint:
                fingerprints.add(row.subject_fingerprint)
            if first is None or row.completed_at < first:
                first = row.completed_at
        uncovered = 0
        if first is not None:
            for value in deleted_identity.fingerprints_since(first):
                uncovered += value not in fingerprints
        return {"records": total, "integrity_failures": failures,
                "tombstones_without_record": uncovered,
                "first_record_at": first.isoformat() if first else None}
    finally:
        db.session.rollback()


def export(since=None):
    """Read only. The sealed values of every record completed at or after
    `since` (all when None), oldest first. This is what an independent copy
    must hold to survive a restore; where it is stored is not decided here
    (docs/BACKUP_RESTORE_REDELETION_RUNBOOK.md)."""
    try:
        query = AccountDeletionRecord.query
        if since is not None:
            query = query.filter(AccountDeletionRecord.completed_at >= since)
        return [receipt(row) for row in query.order_by(AccountDeletionRecord.id)]
    finally:
        db.session.rollback()


def sealed(values):
    """True when `values` (one exported row) still carries a valid MAC."""
    try:
        return hmac.compare_digest(_mac(values), values["integrity"])
    except Exception:
        return False


def match_subjects(fingerprints):
    """Read only. Local accounts whose Cognito subject has one of these
    tombstone fingerprints: accounts a restore brought back after they were
    deleted. Returns [(user id, fingerprint)]."""
    wanted = {value.strip().lower() for value in fingerprints if value.strip()}
    if any(len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
           for value in wanted):
        raise InvalidDeletionRecord("fingerprints are 64 hex characters")
    try:
        found = []
        for user_id, subject in db.session.query(User.id, User.cognito_sub).filter(
                User.cognito_sub.isnot(None)).order_by(User.id):
            value = deleted_identity.fingerprint(subject)
            if value in wanted:
                found.append((user_id, value))
        return found
    finally:
        db.session.rollback()


def verify_runtime_privileges(connection):
    """R6 read-only catalog/ACL admission; this module remains the table owner.

    The caller owns a PostgreSQL READ ONLY transaction on its actual runtime
    pool. No records are read or written and no privileges are changed.
    """
    table = connection.execute(text(
        "SELECT to_regclass('public.account_deletion_record') IS NOT NULL"
    )).scalar()
    if table is not True:
        raise RuntimeError("R6_DELETION_TABLE_REQUIRED")
    acl = connection.execute(text("""
        SELECT has_table_privilege(current_user,
                   'public.account_deletion_record', 'SELECT')
           AND has_table_privilege(current_user,
                   'public.account_deletion_record', 'INSERT')
           AND has_table_privilege(current_user,
                   'public.account_deletion_record', 'DELETE')
           AND COALESCE(has_sequence_privilege(current_user,
                   pg_get_serial_sequence('public.account_deletion_record', 'id'),
                   'USAGE'), false)
    """)).scalar()
    if acl is not True:
        raise RuntimeError("R6_DELETION_ACL_REQUIRED")
