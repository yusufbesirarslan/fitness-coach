"""Durable consent authority. No runtime/config/client-defined policy registry.

The registry intentionally contains no published legal disclosure. Adding one
requires reviewed source with the exact disclosure digest, recipient/model set
and purpose/category projection. Existing callers without a qualified binding
are unavailable, including synthetic probes (no hidden system bypass).
"""
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime
from types import MappingProxyType

from flask import has_app_context
from sqlalchemy import select, insert, update, text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.services.ai_spend_guard import AISpendLimitExceeded, current_subject


class ConsentDenied(AISpendLimitExceeded):
    def __init__(self, code="AI_CONSENT_REQUIRED", status=403):
        super().__init__("consent", "heavy")
        self.code, self.status = code, status


@dataclass(frozen=True)
class Disclosure:
    version: str
    purpose: str
    categories: tuple[str, ...]
    recipients: tuple[tuple[str, str], ...]  # exact (service, model/profile)
    texts: tuple[tuple[str, str], ...]  # exact (locale, SHA-256)


# NO approved text, vendor terms or production recipient facts inferred here.
POLICIES = MappingProxyType({})
# Only these existing builders have an unambiguous, bounded purpose. Coach,
# generic nutrition helpers, vision, memory and streaming remain disabled.
BINDINGS = MappingProxyType({
    "training_plan": ("training.plan", ("fitness_preferences", "body_metrics", "health_constraints")),
    "nutrition_plan": ("nutrition.plan", ("nutrition_targets", "food_preferences", "personal_free_text")),
})


def contract_digest(policy):
    # Bind historical grants to the entire semantic contract, not just its
    # name. Accidentally editing a version's recipient/category tuple denies
    # existing grants rather than broadening them.
    encoded = json.dumps(asdict(policy), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _tables():
    from app.models import AIConsentState, AIConsentEvent, User
    return AIConsentState.__table__, AIConsentEvent.__table__, User.__table__


@contextmanager
def _transaction(owner):
    # A separate READ COMMITTED connection prevents a request's identity map /
    # earlier reads from supplying stale consent. Lock even an absent state.
    if type(owner) is not int or owner <= 0:
        raise ConsentDenied()
    if not has_app_context():
        raise ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
    principal = current_subject()
    if principal is not None and principal != owner:
        raise ConsentDenied()
    try:
        with db.engine.connect().execution_options(isolation_level="READ COMMITTED") as conn:
            if conn.dialect.name != "postgresql":
                raise ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
            with conn.begin():
                conn.execute(text("SET LOCAL lock_timeout = '2000ms'"))
                conn.execute(text("SET LOCAL statement_timeout = '5000ms'"))
                _, _, users = _tables()
                if conn.execute(select(users.c.id).where(users.c.id == owner).with_for_update()).scalar_one_or_none() is None:
                    raise ConsentDenied()
                yield conn
    except SQLAlchemyError:
        raise ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503) from None


def _state(conn, owner):
    states, _, _ = _tables()
    row = conn.execute(select(states).where(states.c.owner_id == owner)).mappings().one_or_none()
    return dict(row) if row else {"owner_id": owner, "generation": 0, "grants": {}}


def _public(state):
    grants = state["grants"] if isinstance(state["grants"], dict) else {}
    return {"generation": state["generation"], "scopes": {
        purpose: {"state": "granted" if _record_valid(purpose, record) else "unavailable",
                  "version": record.get("version") if isinstance(record, dict) else None}
        for purpose, record in grants.items()
    }, "available_disclosures": [
        {"version": p.version, "contract_digest": contract_digest(p), "purpose": p.purpose, "categories": list(p.categories),
         "recipients": [list(r) for r in p.recipients], "texts": dict(p.texts)}
        for p in POLICIES.values()
    ]}


def status(owner):
    with _transaction(owner) as conn:
        return _public(_state(conn, owner))


def _record_valid(purpose, record):
    if not isinstance(record, dict):
        return False
    if (set(record) != {"version", "locale", "text_digest", "contract_digest", "action", "state", "granted_at", "revoked_at"}
            or not all(isinstance(record.get(k), str) for k in
                       ("version", "locale", "text_digest", "contract_digest", "action", "state", "granted_at"))
            or record.get("revoked_at") is not None):
        return False
    try:
        granted = datetime.fromisoformat(record["granted_at"])
        if granted > datetime.utcnow():
            return False
    except (ValueError, TypeError):
        return False
    policy = POLICIES.get(record["version"])
    return bool(policy and policy.version == record["version"] and policy.purpose == purpose
                and record["state"] == "granted"
                and dict(policy.texts).get(record["locale"]) == record["text_digest"]
                and record["contract_digest"] == contract_digest(policy)
                and record["action"] == "affirmative_grant")


def mutate(owner, data):
    required = {"action", "purpose", "version", "locale", "text_digest", "contract_digest", "expected_generation", "request_id"}
    if (not isinstance(data, dict) or set(data) != required
            or type(data.get("expected_generation")) is not int
            or data["expected_generation"] < 0
            or not all(isinstance(data.get(k), str) for k in required - {"expected_generation"})
            or not 8 <= len(data["request_id"]) <= 80
            or data["action"] not in {"affirmative_grant", "revoke"}):
        raise ConsentDenied("AI_CONSENT_INVALID_REQUEST", 400)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    states, events, _ = _tables()
    with _transaction(owner) as conn:
        state = _state(conn, owner)
        prior = conn.execute(select(events.c.request_digest).where(
            events.c.owner_id == owner, events.c.request_id == data["request_id"])).scalar_one_or_none()
        if prior:
            if prior != digest:
                raise ConsentDenied("AI_CONSENT_CONFLICT", 409)
            return _public(state)  # replay NEVER resurrects an earlier grant
        if data["expected_generation"] != state["generation"]:
            raise ConsentDenied("AI_CONSENT_CONFLICT", 409)
        grants = dict(state["grants"]) if isinstance(state["grants"], dict) else {}
        if data["action"] == "affirmative_grant":
            policy = POLICIES.get(data["version"])
            if (not policy or policy.purpose != data["purpose"]
                    or dict(policy.texts).get(data["locale"]) != data["text_digest"]
                    or data["contract_digest"] != contract_digest(policy)):
                raise ConsentDenied("AI_CONSENT_DISCLOSURE_UNAVAILABLE", 403)
        elif data["purpose"] not in grants:
            raise ConsentDenied("AI_CONSENT_INVALID_REQUEST", 400)
        now = datetime.utcnow()
        record = dict(grants.get(data["purpose"], {}))
        if data["action"] == "affirmative_grant":
            record = {k: data[k] for k in ("version", "locale", "text_digest", "contract_digest", "action")}
            record.update(state="granted", granted_at=now.isoformat(), revoked_at=None)
        else:
            record.update(state="revoked", revoked_at=now.isoformat())
        grants[data["purpose"]] = record
        generation = state["generation"] + 1
        values = dict(generation=generation, grants=grants, changed_at=now)
        if state["generation"] == 0:
            conn.execute(insert(states).values(owner_id=owner, **values))
        else:
            conn.execute(update(states).where(states.c.owner_id == owner).values(**values))
        conn.execute(insert(events).values(owner_id=owner, request_id=data["request_id"],
            request_digest=digest, generation=generation, purpose=data["purpose"],
            version=record.get("version", ""), action=data["action"],
            locale=record.get("locale", ""), text_digest=record.get("text_digest", ""), contract_digest=record.get("contract_digest", ""), occurred_at=now))
        return _public(dict(state, **values))


def generation(owner):
    with _transaction(owner) as conn:
        return _state(conn, owner)["generation"]


@contextmanager
def dispatch(owner, feature, recipient, payload, expected_generation):
    """Hold cross-process account lock THROUGH the bounded blocking SDK call.

    An acknowledged revoke is ordered after earlier SDK calls and before all
    later calls. Nothing already transmitted can be recalled. Streams are
    deliberately disabled: the SDK lacks a bounded total-duration handoff.
    """
    binding = BINDINGS.get(feature)
    if not binding or recipient not in {"bedrock", "openai"}:
        raise ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    authenticated = current_subject()
    if authenticated is not None and authenticated != owner:
        raise ConsentDenied()
    with _transaction(owner) as conn:
        state = _state(conn, owner)
        if state["generation"] != expected_generation:
            raise ConsentDenied("AI_CONSENT_CHANGED")
        purpose, categories = binding
        grants = state["grants"]
        record = grants.get(purpose) if isinstance(grants, dict) else None
        if not _record_valid(purpose, record):
            raise ConsentDenied("AI_CONSENT_REQUIRED")
        policy = POLICIES[record["version"]]
        if (policy.categories != categories
                or (recipient, payload.get("model")) not in policy.recipients):
            raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
        yield


def memory_available(owner):
    # Summary dispatch is deliberately unqualified in this release. Even a
    # stored or signed PA06 record cannot enable optional personalization.
    if "summary" not in BINDINGS:
        return False
    try:
        with _transaction(owner) as conn:
            state = _state(conn, owner)
            grants = state["grants"]
            return isinstance(grants, dict) and _record_valid("ai.memory", grants.get("ai.memory"))
    except ConsentDenied:
        return False


def stream_dispatch(*args, **kwargs):
    # No bounded total-duration transport handoff has been qualified. This
    # remains closed even if ordinary blocking sharing permission exists.
    raise ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
