"""First-party consent, independent of D1, entitlement and transfer authority.

PostgreSQL transaction advisory locks serialize health processing with decisions
across processes. They do not lock User rows or share D1's generation. An admitted
request completes before withdrawal is confirmed. No new request uses a stale
cached grant. Old mutation replays never resurrect a withdrawn permission.
"""
import contextvars
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.services import health_disclosure as disclosure

_ACTIVE = contextvars.ContextVar("axisai_first_party_health", default=None)
LOCK_NAMESPACE = 17921  # Dedicated two-int advisory-lock namespace, not D1.


class HealthConsentDenied(Exception):
    def __init__(self, code="HEALTH_CONSENT_REQUIRED", status=403):
        super().__init__(code)
        self.code, self.status = code, status


@contextmanager
def _authority(owner, *, exclusive=False):
    if type(owner) is not int or not 0 < owner <= 2147483647:
        raise HealthConsentDenied()
    try:
        with db.engine.connect() as conn:
            if conn.dialect.name != "postgresql":
                raise HealthConsentDenied("HEALTH_CONSENT_AUTHORITY_UNAVAILABLE", 503)
            with conn.begin():
                conn.execute(text("SET LOCAL lock_timeout = '2000ms'"))
                conn.execute(text("SET LOCAL statement_timeout = '5000ms'"))
                lock = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
                conn.execute(text(f"SELECT {lock}(:namespace, :owner)"),
                             {"namespace": LOCK_NAMESPACE, "owner": owner})
                yield conn
    except SQLAlchemyError:
        raise HealthConsentDenied("HEALTH_CONSENT_AUTHORITY_UNAVAILABLE", 503) from None


def _tables():
    from app.health_consent_models import HealthConsentState, HealthConsentEvent
    return HealthConsentState.__table__, HealthConsentEvent.__table__


def _state(conn, owner):
    states, _ = _tables()
    row = conn.execute(select(states).where(states.c.owner_id == owner)).mappings().one_or_none()
    return dict(row) if row else {"owner_id": owner, "generation": 0, "record": {}}


def _valid(record):
    published = disclosure.published()
    if not published or not isinstance(record, dict):
        return False
    try:
        when = datetime.fromisoformat(record["granted_at"])
        return (record["purpose"] == disclosure.PURPOSE
                and record["version"] == published["version"]
                and record["state"] == "granted"
                and record["withdrawn_at"] is None
                and published["text_digests"].get(record["locale"]) == record["text_digest"]
                and when.tzinfo is not None and when <= datetime.now(timezone.utc))
    except (KeyError, ValueError, TypeError):
        return False


def _public(state):
    record = state["record"]
    return {"purpose": disclosure.PURPOSE, "generation": state["generation"],
            "state": "granted" if _valid(record) else
                     record.get("state", "not_granted") if record.get("state") in {"declined", "withdrawn"} else "not_granted",
            "version": record.get("version"), "granted_at": record.get("granted_at"),
            "withdrawn_at": record.get("withdrawn_at"), "disclosure": disclosure.published()}


def status(owner):
    with _authority(owner) as conn:
        return _public(_state(conn, owner))


def allowed(owner):
    """For safe account projection only; storage failure never reveals health."""
    active = _ACTIVE.get()
    if active is not None:
        return active[0] == owner
    try:
        return status(owner)["state"] == "granted"
    except HealthConsentDenied:
        return False


def mutate(owner, data):
    keys = {"action", "purpose", "version", "locale", "text_digest", "expected_generation", "request_id"}
    if (not isinstance(data, dict) or set(data) != keys
            or type(data["expected_generation"]) is not int or data["expected_generation"] < 0
            or not all(isinstance(data[k], str) for k in keys - {"expected_generation"})
            or data["purpose"] != disclosure.PURPOSE
            or data["action"] not in {"affirmative_grant", "decline", "withdraw"}
            or not 8 <= len(data["request_id"]) <= 80
            or data["locale"] not in {"en", "tr"}
            or len(data["version"]) > 80 or len(data["text_digest"]) > 64):
        raise HealthConsentDenied("HEALTH_CONSENT_INVALID_REQUEST", 400)
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    states, events = _tables()
    with _authority(owner, exclusive=True) as conn:
        state = _state(conn, owner)
        prior = conn.execute(select(events.c.request_digest).where(
            events.c.owner_id == owner, events.c.request_id == data["request_id"])).scalar_one_or_none()
        if prior is not None:
            if prior != fingerprint:
                raise HealthConsentDenied("HEALTH_CONSENT_CONFLICT", 409)
            return _public(state)
        if state["generation"] != data["expected_generation"]:
            raise HealthConsentDenied("HEALTH_CONSENT_CONFLICT", 409)
        if data["action"] == "affirmative_grant":
            published = disclosure.published()
            if (not published or data["version"] != published["version"]
                    or data["text_digest"] != published["text_digests"].get(data["locale"])):
                raise HealthConsentDenied("HEALTH_CONSENT_DISCLOSURE_UNAVAILABLE", 403)
        now = datetime.now(timezone.utc)
        record = dict(state["record"])
        if data["action"] == "affirmative_grant":
            record = {k: data[k] for k in ("purpose", "version", "locale", "text_digest")}
            record.update(state="granted", granted_at=now.isoformat(), withdrawn_at=None)
        else:
            record.update(state="withdrawn" if data["action"] == "withdraw" else "declined",
                          withdrawn_at=now.isoformat())
        generation = state["generation"] + 1
        values = dict(generation=generation, record=record, changed_at=now.replace(tzinfo=None))
        if state["generation"] == 0:
            conn.execute(insert(states).values(owner_id=owner, **values))
        else:
            conn.execute(update(states).where(states.c.owner_id == owner).values(**values))
        conn.execute(insert(events).values(owner_id=owner, generation=generation,
            purpose=disclosure.PURPOSE, version=record.get("version", ""),
            locale=data["locale"], text_digest=record.get("text_digest", ""),
            action=data["action"], occurred_at=now.replace(tzinfo=None),
            request_id=data["request_id"], request_digest=fingerprint))
        return _public(dict(state, **values))


class processing:
    """Explicit scope ownership: GC must never resume a token-reset generator.

    The WSGI owner closes this scope in its originating Context. Keeping the
    token in a regular object also makes cyclic collection safe: Python may
    otherwise finalize a suspended contextmanager generator before the WSGI
    iterable's finalizer, in the collector's unrelated Context.
    """
    def __init__(self, owner, *, expected_generation=None):
        self.owner = owner
        self.expected_generation = expected_generation
        self.authority = None
        self.token = None

    def __enter__(self):
        active = _ACTIVE.get()
        if active is not None:
            if (active[0] != self.owner or (self.expected_generation is not None
                    and active[1] != self.expected_generation)):
                raise HealthConsentDenied("HEALTH_CONSENT_CHANGED")
            return active[1]
        authority = _authority(self.owner)
        conn = authority.__enter__()
        try:
            state = _state(conn, self.owner)
            if not _valid(state["record"]):
                raise HealthConsentDenied()
            if (self.expected_generation is not None
                    and state["generation"] != self.expected_generation):
                raise HealthConsentDenied("HEALTH_CONSENT_CHANGED")
            self.token = _ACTIVE.set((self.owner, state["generation"]))
            self.authority = authority
            return state["generation"]
        except BaseException:
            import sys
            authority.__exit__(*sys.exc_info())
            raise

    def __exit__(self, *exc):
        authority, self.authority = self.authority, None
        if authority is not None:
            try:
                _ACTIVE.reset(self.token)
                self.token = None
            finally:
                authority.__exit__(*exc)
        return False


def current_generation():
    active = _ACTIVE.get()
    return active[1] if active is not None else None


def fresh_request_context():
    """WSGI iterables retain their own scope, never another request's allow."""
    context = contextvars.copy_context()
    context.run(_ACTIVE.set, None)
    return context
