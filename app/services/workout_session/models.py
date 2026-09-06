"""Framework-free value objects, outcome/relationship vocabulary and PURE
classification for the persisted workout-session lifecycle (Sprint 7 PR3).

No DB, no Flask, no side effects — the whole relationship/stale matrix is a total
function of its inputs, so it is unit-testable without an app context (mirrors
``workout_state/resolver.py`` and ``workout_completion/models.py``). All impure
data access lives in ``queries.py``; orchestration + transactions in
``service.py``.
"""
import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Iterable, Optional


# ── Deterministic domain outcomes ────────────────────────────────────────────
class SessionOutcome(Enum):
    CREATED = "created"                 # a new ACTIVE session was started
    EXISTING_ACTIVE = "existing_active" # idempotent replay / current active session
    RESUMED = "resumed"                 # an eligible ACTIVE session was resumed
    CHECKPOINTED = "checkpointed"       # heartbeat accepted (or coalesced no-op)
    ABANDONED = "abandoned"             # ACTIVE → ABANDONED (explicit)
    COMPLETED = "completed"             # ACTIVE → COMPLETED via PR2 completion
    ALREADY_COMPLETED = "already_completed"
    ALREADY_ABANDONED = "already_abandoned"
    STALE_SESSION_REQUIRES_RESOLUTION = "stale_session_requires_resolution"
    CONFLICT = "conflict"               # a different ACTIVE workout already exists
    NOT_FOUND = "not_found"
    INVALID_TRANSITION = "invalid_transition"


# ── Session ↔ plan relationship (derived; never trusts weekday text alone) ────
REL_MATCHES_CURRENT_PLAN = "matches_current_plan"
REL_PLAN_REGENERATED = "plan_regenerated_or_replaced"
REL_PLAN_MISSING = "plan_missing"
REL_SCHEDULE_SLOT_CHANGED = "schedule_slot_changed"
REL_UNSCHEDULED = "unscheduled"
REL_INDETERMINATE = "indeterminate"

# ── Derived stale classification (NO inactivity threshold — evidence only) ────
STALE_NONE = "none"
STALE_PREVIOUS_DAY = "previous_day"
STALE_PLAN_MISSING = "plan_missing"
STALE_PLAN_REGENERATED = "plan_regenerated_or_replaced"
STALE_SCHEDULE_SLOT_CHANGED = "schedule_slot_changed"
STALE_LIFECYCLE_INCONSISTENT = "lifecycle_inconsistent"
STALE_INDETERMINATE = "indeterminate"

# ── Session source ───────────────────────────────────────────────────────────
SOURCE_SCHEDULED = "scheduled"
SOURCE_UNSCHEDULED = "unscheduled"

# Versioned fingerprint algorithm tag. A stored fingerprint whose version differs
# from this can never *silently* match — it is classified as indeterminate.
FINGERPRINT_VERSION = "v1"

# Heartbeat coalesce window (seconds): repeated checkpoints inside this window are
# accepted as a no-op instead of amplifying writes. Not a staleness threshold.
HEARTBEAT_COALESCE_SECONDS = 30


def compute_fingerprint(exercise_names: Iterable[str]) -> str:
    """Versioned, order-preserving digest of a workout's canonical exercise list.

    Uses only server-side plan exercise names (``isim``), lower-cased for stable
    comparison, joined in order. Never derived from translated/client-visible copy
    and never accepted from the client. Returns ``"v1:<sha256hex>"``.
    """
    canonical = "\n".join((name or "").strip().casefold() for name in exercise_names)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{FINGERPRINT_VERSION}:{digest}"


def fingerprints_match(stored: Optional[str], current: Optional[str]) -> Optional[bool]:
    """Version-aware fingerprint comparison.

    Returns ``True``/``False`` only when both fingerprints use the *same*
    algorithm version; returns ``None`` (undecidable → classify safely) when
    either is missing/malformed or the versions differ. A future ``v2`` algorithm
    can never be silently treated as matching a stored ``v1`` digest.
    """
    if not stored or not current:
        return None
    stored_ver, _, stored_digest = stored.partition(":")
    current_ver, _, current_digest = current.partition(":")
    if not stored_digest or not current_digest:
        return None
    if stored_ver != current_ver:
        return None
    return stored_digest == current_digest


def classify_relationship(
    *,
    source: str,
    has_current_plan: bool,
    current_weekday_is_workout: bool,
    stored_fingerprint: Optional[str],
    current_fingerprint: Optional[str],
) -> str:
    """Classify the session's stored soft reference against the CURRENT plan.

    Explicit precedence; never assumes the same workout still exists from weekday
    text alone. A plan mismatch is surfaced, never silently re-attached.
    """
    if source == SOURCE_UNSCHEDULED:
        return REL_UNSCHEDULED
    if not has_current_plan:
        return REL_PLAN_MISSING
    if not current_weekday_is_workout:
        return REL_SCHEDULE_SLOT_CHANGED
    match = fingerprints_match(stored_fingerprint, current_fingerprint)
    if match is True:
        return REL_MATCHES_CURRENT_PLAN
    if match is None:
        return REL_INDETERMINATE
    return REL_PLAN_REGENERATED


@dataclass(frozen=True)
class RelationshipInputs:
    """Trusted, already-loaded facts for classifying an ACTIVE session (pure)."""
    status: str
    workout_date: str
    source: str
    stored_fingerprint: Optional[str]
    today: date
    completed_today: bool
    has_current_plan: bool
    current_weekday_is_workout: bool
    current_fingerprint: Optional[str]


@dataclass(frozen=True)
class SessionClassification:
    relationship: str
    stale_reason: str
    resumable: bool


def classify(inputs: RelationshipInputs) -> SessionClassification:
    """Derive (relationship, stale_reason, resumable) for an ACTIVE session.

    Precedence: a confirmed same-day completion (lifecycle inconsistency) and a
    previous-day session both block a normal resume before plan-relationship is
    even consulted. Elapsed inactivity is NEVER consulted — there is no inactivity
    staleness in PR3.
    """
    relationship = classify_relationship(
        source=inputs.source,
        has_current_plan=inputs.has_current_plan,
        current_weekday_is_workout=inputs.current_weekday_is_workout,
        stored_fingerprint=inputs.stored_fingerprint,
        current_fingerprint=inputs.current_fingerprint,
    )
    if inputs.completed_today:
        return SessionClassification(relationship, STALE_LIFECYCLE_INCONSISTENT, False)
    if inputs.workout_date != inputs.today.isoformat():
        return SessionClassification(relationship, STALE_PREVIOUS_DAY, False)
    if relationship in (REL_MATCHES_CURRENT_PLAN, REL_UNSCHEDULED):
        return SessionClassification(relationship, STALE_NONE, True)
    if relationship == REL_PLAN_MISSING:
        return SessionClassification(relationship, STALE_PLAN_MISSING, False)
    if relationship == REL_PLAN_REGENERATED:
        return SessionClassification(relationship, STALE_PLAN_REGENERATED, False)
    if relationship == REL_SCHEDULE_SLOT_CHANGED:
        return SessionClassification(relationship, STALE_SCHEDULE_SLOT_CHANGED, False)
    return SessionClassification(relationship, STALE_INDETERMINATE, False)


@dataclass(frozen=True)
class SessionView:
    """Public, serialization-ready projection of a session (no raw DB ids).

    Sprint 14 PR2 added ``checkpoint_revision`` + ``checkpoint``: the durable
    execution progress ``#276`` began persisting. Before that, this projection
    could describe *which* session a client held and *whether* it was resumable,
    but never *how far into it the user was* — which is why the browser could
    not restore a workout and could not declare what it was completing.

    Both fields are additive and every existing key keeps its meaning, so an
    older consumer that reads only what it knows is unaffected.

    Deliberately still private, because a client needs durable progress and not
    persistence internals: the raw database id, ``user_id``, the owning plan id,
    ``plan_fingerprint``, the checkpoint's semantic fingerprint and the replay
    key. The last two are the replay authority itself — publishing them would
    let a caller forge or probe another request's replay identity.
    """
    public_id: str
    status: str
    workout_date: str
    weekday_slot: Optional[str]
    source: str
    started_at: Optional[str]
    last_activity_at: Optional[str]
    completed_at: Optional[str]
    abandoned_at: Optional[str]
    terminal_reason: Optional[str]
    relationship: str
    stale_reason: str
    resumable: bool
    # The optimistic revision of the durable progress below, and the ONLY value
    # a caller may declare back as a precondition. 0 means "started, nothing
    # checkpointed yet" — never "unknown".
    checkpoint_revision: int = 0
    # The last accepted bounded full snapshot, exactly as it was validated and
    # stored, or ``None`` when no checkpoint has been accepted. Never a patch,
    # never a partial merge — see ``checkpoint.parse_checkpoint``.
    checkpoint: Optional[dict] = None
    # Ordered catalog identities for the canonical workout this session names.
    # This is execution input, not persistence authority: it is derived afresh
    # server-side and exposes none of the row/plan/replay internals.
    checkpoint_exercise_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "public_id": self.public_id,
            "status": self.status,
            "workout_date": self.workout_date,
            "weekday_slot": self.weekday_slot,
            "source": self.source,
            "started_at": self.started_at,
            "last_activity_at": self.last_activity_at,
            "completed_at": self.completed_at,
            "abandoned_at": self.abandoned_at,
            "terminal_reason": self.terminal_reason,
            "relationship": self.relationship,
            "stale_reason": self.stale_reason,
            "resumable": self.resumable,
            "checkpoint_revision": self.checkpoint_revision,
            "checkpoint": self.checkpoint,
            "checkpoint_exercise_ids": list(self.checkpoint_exercise_ids),
        }


@dataclass(frozen=True)
class SessionResult:
    """The outcome plus (when applicable) the session projection + completion.

    ``completion`` carries the PR2 :class:`CompletionResult` for completion
    outcomes so the route can serialize XP/level without a second query.
    """
    outcome: SessionOutcome
    session: Optional[SessionView] = None
    completion: object = None  # workout_completion.CompletionResult | None
    # Why a CONFLICT outcome happened, when the completion authority said so:
    # "abandoned" (terminal) or "revision" (progress moved on). None for every
    # other outcome. Lets a transport map one outcome to two public errors
    # without inspecting exception message text.
    conflict_reason: Optional[str] = None


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None
