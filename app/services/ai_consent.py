"""Durable consent authority. No runtime/config/client-defined policy registry.

The registry intentionally contains no published legal disclosure. Adding one
requires reviewed source with the exact disclosure digest, recipient/model set
and purpose/category projection. Existing callers without a qualified binding
are unavailable, including synthetic probes (no hidden system bypass).
"""
import contextvars
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
# Purpose projections only: no grant or published contract is installed here.
# New category tuples remain inert until exact source disclosures are approved.
# Generic nutrition, nested tools, memory and streaming stay unbound.
BINDINGS = MappingProxyType({
    "coach": ("coach.conversation", ("personal_free_text", "fitness_preferences",
        "training_history", "workout_state", "nutrition_activity", "nutrition_targets",
        "body_metrics", "recovery_ratings", "health_constraints", "tool_results")),
    "vision": ("pump_check.analysis", ("fitness_image", "fitness_context", "personal_free_text")),
    "menu_extract": ("qr_menu.analysis", ("menu_content", "food_descriptions")),
    "training_plan": ("training.plan", ("fitness_preferences", "body_metrics", "health_constraints")),
    "nutrition_plan": ("nutrition.plan", ("nutrition_targets", "food_preferences", "personal_free_text")),
})


# A physical taxonomy label cannot turn legacy image/tool paths into a new
# purpose. These native families require original logical-operation authority.
NATIVE_OPERATION_FEATURES = frozenset({"coach", "vision", "menu_extract"})


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
    from app.services.ai_consent_disclosures import available_disclosures
    return {"generation": state["generation"], "scopes": {
        purpose: {"state": "granted" if _record_valid(purpose, record) else "unavailable",
                  "version": record.get("version") if isinstance(record, dict) else None}
        for purpose, record in grants.items()
    }, "available_disclosures": available_disclosures()}



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
            from app.services.ai_consent_disclosures import localized_texts
            policy = POLICIES.get(data["version"])
            if (not policy or localized_texts(policy) is None
                    or policy.purpose != data["purpose"]
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


def _binding_for(owner, feature, recipient):
    binding = BINDINGS.get(feature)
    if not binding or recipient not in {"bedrock", "openai"}:
        raise ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    authenticated = current_subject()
    if authenticated is not None and authenticated != owner:
        raise ConsentDenied()
    return binding


def _authorize(state, binding, recipient, model):
    purpose, categories = binding
    grants = state["grants"]
    record = grants.get(purpose) if isinstance(grants, dict) else None
    if not _record_valid(purpose, record):
        raise ConsentDenied("AI_CONSENT_REQUIRED")
    policy = POLICIES[record["version"]]
    if policy.categories != categories or (recipient, model) not in policy.recipients:
        raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")


def preflight(owner, feature, recipient, model):
    """Early refusal before a metered action reserves anything. Authorizes nothing.

    The same purpose/category/recipient/model predicate as ``dispatch``, read
    under the same account lock at the current generation. A pass only means
    the final check could pass now: every physical attempt still goes through
    ``dispatch``, which also fences the generation captured at admission.
    """
    binding = _binding_for(owner, feature, recipient)
    with _transaction(owner) as conn:
        _authorize(_state(conn, owner), binding, recipient, model)


@contextmanager
def dispatch(owner, feature, recipient, payload, expected_generation):
    """Hold cross-process account lock THROUGH the bounded blocking SDK call.

    An acknowledged revoke is ordered after earlier SDK calls and before all
    later calls. Nothing already transmitted can be recalled. Streams are
    deliberately disabled: the SDK lacks a bounded total-duration handoff.
    """
    binding = _binding_for(owner, feature, recipient)
    with _transaction(owner) as conn:
        state = _state(conn, owner)
        if state["generation"] != expected_generation:
            raise ConsentDenied("AI_CONSENT_CHANGED")
        _authorize(state, binding, recipient, payload.get("model"))
        yield


# ── LP21 operation fencing ─────────────────────────────────────────────────
# An Admission's generation fences ONE physical call. A logical operation
# (first plan, replacement proposal, Nutrition proposal) spans several
# admissions, a durable candidate and a later canonical write. Its authority is
# captured ONCE here and never re-derived: the account generation is
# conservative, so ANY consent mutation after capture (revoke -> regrant is
# g+2) leaves the operation stale for good.

@dataclass(frozen=True)
class OperationAuthority:
    owner: int
    feature: str
    generation: int
    version: str

    def to_context(self):
        return {"owner": self.owner, "feature": self.feature,
                "generation": self.generation, "version": self.version}

    @classmethod
    def from_context(cls, owner, value):
        """The durable form back, for THIS owner only. Missing/corrupt -> stale."""
        if (not isinstance(value, dict)
                or set(value) != {"owner", "feature", "generation", "version"}
                or type(value["owner"]) is not int or value["owner"] != owner
                or value["feature"] not in BINDINGS
                or type(value["generation"]) is not int or value["generation"] <= 0
                or not isinstance(value["version"], str) or not value["version"]):
            raise ConsentDenied("AI_CONSENT_CHANGED")
        return cls(owner, value["feature"], value["generation"], value["version"])


_OPERATION = contextvars.ContextVar("ai_consent_operation", default=None)


def begin_operation(owner, feature, recipient, model):
    """Authorize a NEW logical operation and capture its immutable authority.

    The ``preflight`` predicate, plus the generation and policy version it was
    decided at. Recovery of existing work never calls this: it reuses the
    authority stored with that work.
    """
    binding = _binding_for(owner, feature, recipient)
    with _transaction(owner) as conn:
        state = _state(conn, owner)
        _authorize(state, binding, recipient, model)
        return OperationAuthority(owner, feature, state["generation"],
                                  state["grants"][binding[0]]["version"])


@contextmanager
def operation_scope(authority):
    """Every provider admission inside uses ``authority``'s generation."""
    if not isinstance(authority, OperationAuthority):
        raise ConsentDenied("AI_CONSENT_CHANGED")
    token = _OPERATION.set(authority)
    try:
        yield authority
    finally:
        _OPERATION.reset(token)


def current_operation():
    return _OPERATION.get()


def admission_generation(owner, feature):
    """The generation a new Admission fences on: the operation's, never a newer one."""
    operation = _OPERATION.get()
    if operation is None:
        if feature in NATIVE_OPERATION_FEATURES:
            raise ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
        return generation(owner)
    if operation.owner != owner:
        raise ConsentDenied()
    if operation.feature != feature:
        raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
    return operation.generation


def _check_operation(state, authority):
    if state["generation"] != authority.generation:
        raise ConsentDenied("AI_CONSENT_CHANGED")
    purpose, categories = BINDINGS[authority.feature]
    grants = state["grants"]
    record = grants.get(purpose) if isinstance(grants, dict) else None
    if not _record_valid(purpose, record) or record["version"] != authority.version:
        raise ConsentDenied("AI_CONSENT_REQUIRED")
    if POLICIES[record["version"]].categories != categories:
        raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")


def _operation_binding(authority, owner=None):
    if not isinstance(authority, OperationAuthority):
        # No server-held AI provenance (e.g. a client-authored document).
        raise ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    if authority.feature not in BINDINGS:
        raise ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    if owner is not None and (type(owner) is not int or owner != authority.owner):
        # One account's authority never writes another account's state.
        raise ConsentDenied()
    principal = current_subject()
    if principal is not None and principal != authority.owner:
        raise ConsentDenied()


def authorize_operation(authority):
    """Publication decision with no domain transaction to join (Nutrition review).

    Same account lock as ``mutate``: a revoke acknowledged before this returns
    withholds the result; one acknowledged after it is ordered after a release
    that cannot be recalled. Never call while the request session holds the
    owner row (that is ``fence``'s job).
    """
    _operation_binding(authority)
    with _transaction(authority.owner) as conn:
        _check_operation(_state(conn, authority.owner), authority)


# Bounded wait for the owner row inside a writer's transaction: longer than
# one bounded SDK attempt (30 s) that a same-owner ``dispatch`` may hold, never
# unbounded. Restored afterwards so the caller's own statements are unaffected.
FENCE_LOCK_TIMEOUT = "35000ms"
# Reserved key carrying the proposing operation's authority in a server-written
# Coach pending-confirmation payload (read by keyed access only).
PAYLOAD_AUTHORITY_KEY = "_d1_operation_authority"


def fence(authority, owner, feature=None):
    """Transaction-local fence for ``owner``'s publication/canonical write.

    Runs on the CALLER's ``db.session`` transaction, at the caller's place in
    the repository lock order: it takes the owner row ``FOR NO KEY UPDATE``
    (``plan_owner_lock``'s mode, conflicting with ``mutate``'s ``FOR UPDATE``;
    a no-op re-lock when the caller already holds it), then reads the committed
    consent state in that same READ COMMITTED transaction. The caller must
    perform its write and commit while still holding that lock, so a revoke
    commits either entirely before the decision (the write is refused) or
    entirely after the write. Never opens D1's separate connection, which would
    wait on the caller's own lock. The caller rolls back on denial.
    ``owner`` is the account being written; it must be the authority's.
    ``feature``, when given, is the only purpose whose result this writer
    accepts (a Nutrition authority never writes a Training plan).
    """
    if type(owner) is not int:
        raise ConsentDenied()
    _operation_binding(authority, owner)
    if feature is not None and authority.feature != feature:
        raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
    if not has_app_context():
        raise ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
    try:
        conn = db.session.connection()
        if conn.dialect.name != "postgresql":
            raise ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
        if conn.execute(text("SELECT current_setting('transaction_isolation')")).scalar_one() != "read committed":
            # A snapshot older than the lock could miss a committed revoke.
            raise ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
        _, _, users = _tables()
        previous = conn.execute(text("SELECT current_setting('lock_timeout')")).scalar_one()
        conn.execute(text("SELECT set_config('lock_timeout', :value, true)"),
                     {"value": FENCE_LOCK_TIMEOUT})
        owned = conn.execute(select(users.c.id).where(users.c.id == authority.owner)
                             .with_for_update(key_share=True)).scalar_one_or_none()
        conn.execute(text("SELECT set_config('lock_timeout', :value, true)"),
                     {"value": previous})
        if owned is None:
            raise ConsentDenied()
        state = _state(conn, authority.owner)
    except SQLAlchemyError:
        raise ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503) from None
    _check_operation(state, authority)


def fence_active(owner):
    """``fence`` the ACTIVE operation, if any; outside one, nothing changes.

    For shared writers that also serve first-party/non-AI callers (Coach plan
    tools, pending confirmations, conversation turns): every write made inside
    an ``operation_scope`` is an AI result and is fenced in the caller's
    transaction; a write made outside one is untouched. Call it BEFORE the
    writer's own row locks, where ``lock_plan_owner`` would sit.
    """
    operation = _OPERATION.get()
    if operation is not None:
        fence(operation, owner)
    return operation


def authorize_active():
    """``authorize_operation`` for the active operation, if any (publication)."""
    operation = _OPERATION.get()
    if operation is not None:
        authorize_operation(operation)
    return operation


# Internal staged AI candidates (first-plan GENERATED row, replacement
# proposal) carry the authority they were produced under, in the SAME text
# column, so recovery and confirmation fence on the original generation
# without a schema change. Never projected to a client.
_CANDIDATE_DOMAIN = "axisai/d1-operation-candidate/v1"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def bind_candidate(authority, document_text):
    """The staged text: the EXACT document bytes plus their operation authority."""
    if not isinstance(authority, OperationAuthority):
        raise ConsentDenied("AI_CONSENT_CHANGED")
    if not isinstance(document_text, str) or not isinstance(json.loads(document_text), dict):
        raise ValueError("candidate document must be a JSON object")
    return _canonical({"domain": _CANDIDATE_DOMAIN, "authority": authority.to_context(),
                       "document": document_text})


def unbind_candidate(owner, stored):
    """``(authority, document_text)``; legacy/foreign/corrupt material is stale."""
    try:
        envelope = json.loads(stored)
    except (TypeError, ValueError):
        raise ConsentDenied("AI_CONSENT_CHANGED") from None
    if (not isinstance(envelope, dict)
            or set(envelope) != {"domain", "authority", "document"}
            or envelope["domain"] != _CANDIDATE_DOMAIN
            or not isinstance(envelope["document"], str)):
        raise ConsentDenied("AI_CONSENT_CHANGED")
    return OperationAuthority.from_context(owner, envelope["authority"]), envelope["document"]


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
