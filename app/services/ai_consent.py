"""Durable consent authority. No runtime/config/client-defined policy registry.

The source candidate registers the existing Training contract and the four
founder-content-approved (2026-11-12) Coach, Nutrition plan, Pump Check and
QR Menu contracts. Adding one requires reviewed source with the exact
disclosure digest, recipient/model set and purpose/category projection. Existing callers without a qualified binding
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
    # Non-AI sharing scopes are separately approved in the SAME immutable
    # contract. No external recipient facts are installed by this extension.
    external_scopes: tuple[str, ...] = ()
    # Optional bounded helpers of THIS purpose, included in contract_digest.
    # Never imports a separate purpose's grant or an accounting alias.
    nested_features: tuple[str, ...] = ()


# Source preparation authorized 2026-11-11 (Training) and 2026-11-12 (Coach,
# Nutrition plan, Pump Check, QR Menu; founder content approval, not counsel
# sign-off). Deployment/production activation remain separate.
POLICIES = MappingProxyType({
    "d1-closure01-training-candidate": Disclosure(
        version="d1-closure01-training-candidate",
        purpose="training.plan",
        categories=("fitness_preferences", "body_metrics", "health_constraints"),
        recipients=(("bedrock", "eu.anthropic.claude-sonnet-5-5"),
                    ("bedrock", "eu.anthropic.claude-haiku-5-5")),
        texts=(("en", "467fc1fb54fca648cec3e737ad7a78fc969f4f74ae0ef735b223d018ab130054"),
               ("tr", "062ae5123955dae849ef48f91aa60387443bb53e14e94b2a1eb53808ecd82b80")),
    ),
    "d1-closure02-coach-candidate-r2": Disclosure(
        version="d1-closure02-coach-candidate-r2",
        purpose="coach.conversation",
        categories=("personal_free_text", "fitness_preferences", "training_history",
                    "workout_state", "nutrition_activity", "nutrition_targets",
                    "body_metrics", "recovery_ratings", "health_constraints",
                    "tool_results", "fitness_image"),
        recipients=(("bedrock", "eu.anthropic.claude-sonnet-5-5"),
                    ("bedrock", "eu.anthropic.claude-haiku-5-5")),
        texts=(("en", "4666f7fc249f0916ed9f861f7114193407fa2f394cec1556209fdadeb04650e7"),
               ("tr", "1c93b22650a21c80e6b28a87eec41492cef9dfd307c6312bf387f58db85b9b9b")),
        nested_features=("nutrition", "vision"),
    ),
    "d1-closure02-nutrition-plan-candidate": Disclosure(
        version="d1-closure02-nutrition-plan-candidate",
        purpose="nutrition.plan",
        categories=("nutrition_targets", "food_preferences", "personal_free_text"),
        recipients=(("bedrock", "eu.anthropic.claude-sonnet-5-5"),
                    ("bedrock", "eu.anthropic.claude-haiku-5-5")),
        texts=(("en", "6b51fe2dd8177ec2369aed2131da057850aad159b1b0e05a06c5bf2794017b75"),
               ("tr", "0c1f84a3ecac79382d81dc223327f9fc5e01687585db8a1d1df2e0974b2e6bb8")),
    ),
    "d1-closure02-pump-check-candidate": Disclosure(
        version="d1-closure02-pump-check-candidate",
        purpose="pump_check.analysis",
        categories=("fitness_image", "fitness_context", "personal_free_text"),
        recipients=(("bedrock", "eu.anthropic.claude-sonnet-5-5"),
                    ("bedrock", "eu.anthropic.claude-haiku-5-5")),
        texts=(("en", "816f7a3af11fa48edebcac266278e821963bad59670510f503aebd1e5a4b9089"),
               ("tr", "c67fbdfac82ba21fd57cf1b000ff46bac79e0668c6c3119d446cd4cb21bb0637")),
    ),
    "d1-closure02-qr-menu-candidate": Disclosure(
        version="d1-closure02-qr-menu-candidate",
        purpose="qr_menu.analysis",
        categories=("menu_content", "food_descriptions"),
        recipients=(("bedrock", "eu.anthropic.claude-sonnet-5-5"),
                    ("bedrock", "eu.anthropic.claude-haiku-5-5")),
        texts=(("en", "c143131cb0ac8ef8e82d795dff3b50d3260a7c646959f3d0ca57bd818c6d589c"),
               ("tr", "fd2f0d2eb34a3d328aa4a12091b9e7b2f917a9d74657ab3ee28bcf9100e5a74f")),
        external_scopes=("public_https_menu_source",),
    ),
})
# Purpose projections only: a binding is not a user grant.
# New category tuples remain inert until exact source disclosures are approved.
# Generic nutrition, memory and streaming stay unbound. Nested helpers require
# explicit qualification in the original purpose's immutable disclosure.
BINDINGS = MappingProxyType({
    "coach": ("coach.conversation", ("personal_free_text", "fitness_preferences",
        "training_history", "workout_state", "nutrition_activity", "nutrition_targets",
        "body_metrics", "recovery_ratings", "health_constraints", "tool_results", "fitness_image")),
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


def _minor_grant_purposes():
    # The D1 purposes behind the minor AI-service features (Training only).
    from app.services.account_eligibility import TR_MINOR_AI_FEATURES
    return frozenset(BINDINGS[feature][0] for feature in TR_MINOR_AI_FEATURES)


def _require_grant_cohort(conn, owner, purpose):
    """Consent-WRITE cohort gate, inside the owner-locked transaction.

    A NEW affirmative grant needs an affirmative screened cohort from the
    canonical signed record (account_eligibility.d1_grant_cohort), whatever
    ACCOUNT_ELIGIBILITY_ENABLED says: a qualified adult TR account may grant
    any launch purpose, a qualified TR 13-17 account only its cohort's
    purposes (Training), and everything else is refused before any
    state/event write. Any minority signal still restricts on top.
    Independent of provider admission (ai_launch.require_cohort), which still
    decides every request: passing here admits nothing. Revocation, status,
    replay and existing (historical) grants are untouched.
    """
    from datetime import timezone
    from types import SimpleNamespace
    from app.services.account_eligibility import (
        D1_GRANT_ADULT_TR, D1_GRANT_TR_MINOR, d1_grant_cohort, minority_signal)
    _, _, users = _tables()
    row = conn.execute(select(users.c.cognito_sub, users.c.age, users.c.user_metadata)
                       .where(users.c.id == owner)).mappings().one_or_none()
    if row is None:
        raise ConsentDenied()
    account = SimpleNamespace(**row)
    cohort = d1_grant_cohort(account, datetime.now(timezone.utc))
    allowed = ((cohort == D1_GRANT_ADULT_TR and not minority_signal(account))
               or (cohort == D1_GRANT_TR_MINOR and purpose in _minor_grant_purposes()))
    if not allowed:
        raise ConsentDenied("AI_CONSENT_PURPOSE_UNAVAILABLE_FOR_ACCOUNT", 403)


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
            from app.services.ai_consent_disclosures import LAUNCH_VERSIONS
            if (not policy or localized_texts(policy) is None
                    or (LAUNCH_VERSIONS and LAUNCH_VERSIONS.get(data["purpose"]) != data["version"])
                    or policy.purpose != data["purpose"]
                    or dict(policy.texts).get(data["locale"]) != data["text_digest"]
                    or data["contract_digest"] != contract_digest(policy)):
                raise ConsentDenied("AI_CONSENT_DISCLOSURE_UNAVAILABLE", 403)
            from app.services import coach_capabilities
            if not coach_capabilities.grant_open(policy):
                # A Coach capability purpose is grantable only when its exact
                # contract is approved in source (default closed).
                raise ConsentDenied("AI_CONSENT_DISCLOSURE_UNAVAILABLE", 403)
            _require_grant_cohort(conn, owner, data["purpose"])
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
    binding = provider_binding(owner, feature)
    if not binding or recipient not in {"bedrock", "openai"}:
        raise ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    authenticated = current_subject()
    if authenticated is not None and authenticated != owner:
        raise ConsentDenied()
    return binding


def provider_binding(owner, feature):
    """Physical composition is explicit; accounting labels confer no consent."""
    operation = current_operation()
    if operation is not None:
        _operation_binding(operation, owner)
        if operation.feature == "menu_extract" and feature == "nutrition":
            return BINDINGS["menu_extract"]
        if operation.feature == "coach" and feature in qualified_coach_helpers(operation):
            return BINDINGS["coach"]
        if operation.feature != feature:
            raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
    return BINDINGS.get(feature)


def qualified_coach_helpers(authority):
    """Source contract capability, not a second consent grant/registry.

    Dispatch still rechecks the original owner, generation, exact published
    text, categories and each helper's actual recipient/model under D1's lock.
    Memory, summaries and nested plan generation cannot be added by a tuple.
    """
    if not isinstance(authority, OperationAuthority) or authority.feature != "coach":
        return ()
    policy = POLICIES.get(authority.version)
    if (policy is None or policy.purpose != BINDINGS["coach"][0]
            or policy.categories != BINDINGS["coach"][1]
            or type(policy.nested_features) is not tuple
            or len(set(policy.nested_features)) != len(policy.nested_features)
            or not set(policy.nested_features) <= {"nutrition", "vision"}):
        return ()
    return policy.nested_features


def _authorize(state, binding, recipient, model):
    purpose, categories = binding
    grants = state["grants"]
    record = grants.get(purpose) if isinstance(grants, dict) else None
    if not _record_valid(purpose, record):
        raise ConsentDenied("AI_CONSENT_REQUIRED")
    policy = POLICIES[record["version"]]
    from app.services.ai_consent_disclosures import localized_texts
    if localized_texts(policy) is None:
        raise ConsentDenied("AI_CONSENT_DISCLOSURE_UNAVAILABLE")
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
        operation = current_operation()
        if operation is not None:
            _check_operation(state, operation, conn)
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
    original = current_operation()
    if original is not None:
        _operation_binding(original, owner)
        if original.feature != feature:
            raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
    binding = _binding_for(owner, feature, recipient)
    with _transaction(owner) as conn:
        state = _state(conn, owner)
        _authorize(state, binding, recipient, model)
        if original is not None:
            _check_operation(state, original, conn)
            return original
        authority = OperationAuthority(owner, feature, state["generation"],
                                       state["grants"][binding[0]]["version"])
        from app.services import ai_launch
        ai_launch.check_operation_within(conn, state, authority)
        return authority


def begin_independent_operation(owner, feature, recipient, model):
    """``begin_operation`` for a SECOND, separately held purpose authority.

    Adaptive Coach C2: a Coach turn that may touch the saved Nutrition Plan
    needs the owner's own ``nutrition.plan`` grant in addition to the Coach
    one (F-1b). This runs the unmodified ``begin_operation`` predicate with no
    active operation in a copied context, so the active Coach operation is not
    switched, widened or re-authorized, and the new authority confers nothing
    until a caller enters it with ``operation_scope`` or passes it to a fence.
    """
    def capture():
        _OPERATION.set(None)
        return begin_operation(owner, feature, recipient, model)
    return contextvars.copy_context().run(capture)


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


def authorize_coach_read(owner, *categories):
    """Use the current Coach disclosure for a bounded owner read."""
    authority = current_operation()
    if authority is None:
        return False
    if type(owner) is not int or owner <= 0:
        raise ConsentDenied()
    _operation_binding(authority, owner)
    if authority.feature != "coach":
        raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
    policy = POLICIES.get(authority.version)
    if (policy is None or policy.purpose != BINDINGS["coach"][0]
            or not set(categories).issubset(policy.categories)):
        return False
    authorize_operation(authority)
    return True


def admission_generation(owner, feature):
    """The generation a new Admission fences on: the operation's, never a newer one."""
    operation = _OPERATION.get()
    if operation is None:
        if feature in NATIVE_OPERATION_FEATURES:
            raise ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
        return generation(owner)
    if operation.owner != owner:
        raise ConsentDenied()
    if operation.feature != feature and not (
            (operation.feature == "menu_extract" and feature == "nutrition")
            or (operation.feature == "coach" and feature in qualified_coach_helpers(operation))):
        raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
    return operation.generation


def _check_operation(state, authority, conn=None):
    if state["generation"] != authority.generation:
        raise ConsentDenied("AI_CONSENT_CHANGED")
    purpose, categories = BINDINGS[authority.feature]
    grants = state["grants"]
    record = grants.get(purpose) if isinstance(grants, dict) else None
    if not _record_valid(purpose, record) or record["version"] != authority.version:
        raise ConsentDenied("AI_CONSENT_REQUIRED")
    if POLICIES[record["version"]].categories != categories:
        raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
    from app.services.ai_consent_disclosures import localized_texts
    if localized_texts(POLICIES[record["version"]]) is None:
        raise ConsentDenied("AI_CONSENT_DISCLOSURE_UNAVAILABLE")
    if conn is not None:
        from app.services import ai_launch
        ai_launch.check_operation_within(conn, state, authority)


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
        _check_operation(_state(conn, authority.owner), authority, conn)


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
    _check_operation(state, authority, conn)


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
        capabilities = _CAPABILITIES.get()
        if capabilities:
            _fence_capabilities(owner, capabilities)
    return operation


# ── Coach capability authority (#533 / #547 F3) ───────────────────────────
# A capability (Coach Training plan editing, saved Nutrition Plan access) is a
# SEPARATE D1 purpose with its own affirmative grant. It is captured once,
# inside the active Coach operation and at the SAME generation, and never
# re-derived: any consent mutation after capture leaves it stale for good,
# exactly like the operation it belongs to. Registry: ``coach_capabilities``
# (empty -> every capture refuses).

@dataclass(frozen=True)
class CapabilityAuthority:
    owner: int
    capability: str
    purpose: str
    version: str
    generation: int

    def to_context(self):
        return {"owner": self.owner, "capability": self.capability,
                "purpose": self.purpose, "version": self.version,
                "generation": self.generation}

    @classmethod
    def from_context(cls, owner, value, capability):
        """The durable form for THIS owner and capability. Missing/corrupt -> refused."""
        from app.services import coach_capabilities
        spec = coach_capabilities.spec(capability)
        if (spec is None or not isinstance(value, dict)
                or set(value) != {"owner", "capability", "purpose", "version", "generation"}
                or type(value["owner"]) is not int or value["owner"] != owner
                or value["capability"] != capability or value["purpose"] != spec.purpose
                or type(value["generation"]) is not int or value["generation"] <= 0
                or not isinstance(value["version"], str) or not value["version"]):
            raise ConsentDenied("AI_CAPABILITY_CONSENT_REQUIRED")
        return cls(owner, capability, spec.purpose, value["version"], value["generation"])


_CAPABILITIES = contextvars.ContextVar("ai_consent_capabilities", default=())
# Reserved key carrying a proposal's capability authorities (list of contexts).
PAYLOAD_CAPABILITY_KEY = "_d1_capability_authorities"


def _capability_version(state, capability):
    """The owner's current, approved capability grant version, or a refusal."""
    from app.services import coach_capabilities
    spec = coach_capabilities.spec(capability)
    if spec is None:
        raise ConsentDenied("AI_CAPABILITY_UNAVAILABLE")
    grants = state["grants"]
    record = grants.get(spec.purpose) if isinstance(grants, dict) else None
    if not _record_valid(spec.purpose, record):
        raise ConsentDenied("AI_CAPABILITY_CONSENT_REQUIRED")
    if not coach_capabilities.version_open(capability, POLICIES[record["version"]]):
        raise ConsentDenied("AI_CAPABILITY_UNAVAILABLE")
    if spec.requires_nutrition_plan_grant:
        purpose = BINDINGS["nutrition_plan"][0]
        nutrition = grants.get(purpose)
        if not _record_valid(purpose, nutrition):
            raise ConsentDenied("AI_CONSENT_REQUIRED")
    return record["version"]


def _check_capability(state, authority):
    if not isinstance(authority, CapabilityAuthority):
        raise ConsentDenied("AI_CAPABILITY_CONSENT_REQUIRED")
    if state["generation"] != authority.generation:
        raise ConsentDenied("AI_CONSENT_CHANGED")
    if _capability_version(state, authority.capability) != authority.version:
        raise ConsentDenied("AI_CAPABILITY_CONSENT_REQUIRED")


def capture_capability(owner, capability):
    """Capture ``capability`` for the ACTIVE Coach operation, or refuse.

    Never outside a Coach operation, never for another owner, and only at the
    operation's own generation (``_check_operation`` proves it unchanged).
    Account-independent closure is decided before any database access.
    """
    from app.services import coach_capabilities
    if not coach_capabilities.source_open(capability):
        raise ConsentDenied("AI_CAPABILITY_UNAVAILABLE")
    operation = current_operation()
    if operation is None or operation.feature != "coach":
        raise ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    _operation_binding(operation, owner)
    with _transaction(owner) as conn:
        state = _state(conn, owner)
        _check_operation(state, operation, conn)
        version = _capability_version(state, capability)
        return CapabilityAuthority(owner, capability, coach_capabilities.SPECS[capability].purpose,
                                   version, state["generation"])


@contextmanager
def capability_scope(*authorities):
    """Canonical writes inside also fence these capabilities (``fence_active``)."""
    operation = current_operation()
    for authority in authorities:
        if (not isinstance(authority, CapabilityAuthority) or operation is None
                or authority.owner != operation.owner
                or authority.generation != operation.generation):
            raise ConsentDenied("AI_CAPABILITY_CONSENT_REQUIRED")
    token = _CAPABILITIES.set(tuple(authorities))
    try:
        yield authorities
    finally:
        _CAPABILITIES.reset(token)


def active_capability(owner, capability):
    """The active scope's authority for ``capability`` and ``owner``, or None."""
    for authority in _CAPABILITIES.get():
        if authority.owner == owner and authority.capability == capability:
            return authority
    return None


def active_capabilities():
    return _CAPABILITIES.get()


def _fence_capabilities(owner, capabilities):
    """In the caller's transaction, AFTER ``fence`` locked the owner row."""
    if not has_app_context():
        raise ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
    try:
        conn = db.session.connection()
        if conn.dialect.name != "postgresql":
            raise ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503)
        state = _state(conn, owner)
    except SQLAlchemyError:
        raise ConsentDenied("AI_CONSENT_AUTHORITY_UNAVAILABLE", 503) from None
    for authority in capabilities:
        if authority.owner != owner:
            raise ConsentDenied()
        _check_capability(state, authority)


def fence_capabilities(owner, *authorities):
    """``fence``'s capability half, for writers that call ``fence`` directly.

    Must run in the caller's transaction AFTER ``fence`` took the owner row.
    """
    if not authorities:
        raise ConsentDenied("AI_CAPABILITY_CONSENT_REQUIRED")
    _fence_capabilities(owner, authorities)


def authorize_capabilities(*authorities):
    """Publication-time recheck with no domain transaction to join."""
    operation = current_operation()
    if operation is None:
        raise ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    with _transaction(operation.owner) as conn:
        state = _state(conn, operation.owner)
        _check_operation(state, operation, conn)
        for authority in authorities:
            if authority.owner != operation.owner:
                raise ConsentDenied()
            _check_capability(state, authority)


def _offered_policy(purpose):
    """The launched policy a client is shown for ``purpose``, or None.

    Exactly the ``available_disclosures`` rule (D-2 included): a purpose whose
    launched contract is not published offers nothing to grant.
    """
    from app.services import coach_capabilities
    from app.services.ai_consent_disclosures import LAUNCH_VERSIONS, localized_texts
    policy = POLICIES.get(LAUNCH_VERSIONS.get(purpose))
    if policy is None or policy.purpose != purpose or localized_texts(policy) is None:
        return None
    for capability, capability_spec in coach_capabilities.SPECS.items():
        if capability_spec.purpose == purpose and not coach_capabilities.version_open(
                capability, policy):
            return None
    return policy


def capability_request_open(owner, capability):
    """Whether THIS owner may be offered a review of ``capability`` now (#533 D-1).

    True only when every one of these holds; anything else, including an
    outage, is False (fail closed). It never grants, writes or raises:

    * ``capability`` is a registered spec whose launched contract is open
      (``coach_capabilities.version_open``: approved, launched, published);
    * an ACTIVE Coach operation exists for ``owner`` and is still valid at
      its own generation (``_check_operation``: unchanged generation and the
      same Coach grant), so a consent change mid-turn retires the request;
    * the capability is NOT already in effect: at least one of its own grant
      (at the launched version) or, for Nutrition, the owner's separate
      ``nutrition.plan`` grant is missing. A granted capability is never
      requested again;
    * every missing purpose is itself offered (its launched contract is
      published) and grantable for this account (``_require_grant_cohort``,
      the same gate ``mutate`` applies), so the client's Allow can succeed.
    """
    from app.services import coach_capabilities
    capability_spec = coach_capabilities.spec(capability)
    if capability_spec is None or not coach_capabilities.source_open(capability):
        return False
    launched = _offered_policy(capability_spec.purpose)
    if launched is None:
        return False
    operation = current_operation()
    if (not isinstance(operation, OperationAuthority) or operation.feature != "coach"
            or type(owner) is not int or operation.owner != owner):
        return False
    try:
        _operation_binding(operation, owner)
        with _transaction(owner) as conn:
            state = _state(conn, owner)
            _check_operation(state, operation, conn)
            grants = state["grants"] if isinstance(state["grants"], dict) else {}
            missing = []
            record = grants.get(capability_spec.purpose)
            if not (_record_valid(capability_spec.purpose, record)
                    and record["version"] == launched.version):
                missing.append(capability_spec.purpose)
            if capability_spec.requires_nutrition_plan_grant:
                nutrition = BINDINGS["nutrition_plan"][0]
                if not _record_valid(nutrition, grants.get(nutrition)):
                    if _offered_policy(nutrition) is None:
                        return False
                    missing.append(nutrition)
            if not missing:
                return False
            for purpose in missing:
                _require_grant_cohort(conn, owner, purpose)
            return True
    except ConsentDenied:
        return False


def authorize_active():
    """``authorize_operation`` for the active operation, if any (publication)."""
    operation = _OPERATION.get()
    if operation is not None:
        authorize_operation(operation)
    return operation


@contextmanager
def menu_source_dispatch():
    """QR-only, public HTTPS/text acquisition under the original generation.

    The credential-free fetch worker retains DNS/peer/redirect/size/deadline
    protections. The contract must explicitly approve contacting public menu
    websites; this scope never authorizes FatSecret/proxies or image/OCR work.
    Legacy first-party acquisition is unchanged and cannot open an AI endpoint.
    """
    authority = current_operation()
    if authority is None:
        yield
        return
    _operation_binding(authority)
    if authority.feature != "menu_extract":
        raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
    with _transaction(authority.owner) as conn:
        _check_operation(_state(conn, authority.owner), authority, conn)
        if "public_https_menu_source" not in POLICIES[authority.version].external_scopes:
            raise ConsentDenied("AI_CONSENT_RECIPIENT_OR_SCOPE_UNAVAILABLE")
        yield


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
