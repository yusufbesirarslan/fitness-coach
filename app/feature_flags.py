"""Single lifecycle inventory for product rollout flags.

This module owns the *records*; ``app/config.py`` owns the *reading*. PR1 added a
central inventory (``FEATURE_FLAG_KEYS`` / ``feature_flag_state``) so an operator
could ask "which flags are on right now?" without SSH-ing to the host. PR2 grows
that same inventory into a lifecycle record — it deliberately does NOT introduce a
second registry: ``app.config.FEATURE_FLAG_KEYS`` is now *derived* from
``ROLLOUT_FLAGS`` below, so the health endpoint, the boot log and this file can
never disagree.

Three kinds of boolean setting exist in this codebase and they are NOT the same
thing:

* **Rollout flag** (this registry) — gates a capability that is already merged but
  not yet activated in production. Default OFF, carries a review date, and is
  expected to end its life either enabled-and-removed or removed-unshipped.
* **Operational configuration** — a permanent runtime setting (provider choice,
  cache, metrics). No expiry, no rollout plan; changing it is a tuning decision.
* **Emergency kill switch** — permanent, default ON, exists so a subsystem can be
  turned off fast during an incident. Must never be "cleaned up".

Only the first kind belongs here. ``OPERATIONAL_BOOLEAN_KEYS`` records the other
two so that a drift test can tell a newly added rollout flag (which must be
registered) from a newly added operational setting (which must not be).

This module is intentionally dependency-free (stdlib only): ``app/config.py`` and
``app/services/mobile_credentials.py`` both import it, so it must not import them.

No flag is activated by this module. Every ``default`` below is the value that was
already in production before PR2.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Value parsing ──────────────────────────────────────────────────────────
ACCEPTED_VALUES = ("0", "1")


class FeatureFlagConfigurationError(RuntimeError):
    """An explicitly set flag carries a value we refuse to guess the meaning of."""


def parse_bool_flag(key, raw, *, default, allow_empty=True):
    """Parse one flag value strictly.

    ``None`` (unset) and — when ``allow_empty`` — an empty/whitespace-only value
    fall back to ``default``. ``"0"`` and ``"1"`` parse. Anything else raises.

    The strictness matters in one specific direction: the historical idiom
    ``os.getenv(KEY, "0") == "1"`` reads ``true``, ``TRUE``, ``yes`` and ``"1 "``
    (trailing space) as **OFF**, so an operator who believed they enabled a
    feature got a silently disabled one with no error anywhere. We do not
    strip-then-accept either: ``"1 "`` would then *activate* a capability on a
    host where it is currently off, which is exactly the surprise this parser
    exists to prevent. Refusing to guess is the only reading that neither
    silently activates nor silently ignores.

    Unset and empty stay permissive on purpose — ``KEY=`` is a normal way to
    write "not set" in a ``.env`` file, it means OFF today, and it keeps this
    change backward-compatible for every value that is currently accepted.
    """
    if raw is None:
        return default
    if allow_empty and raw.strip() == "":
        return default
    if raw in ACCEPTED_VALUES:
        return raw == "1"
    raise FeatureFlagConfigurationError(
        f"invalid {key} (expected exactly '0' or '1')")


# ── Lifecycle vocabulary ───────────────────────────────────────────────────
# Where a flag is in its life. Not a synonym for its current value.
LIFECYCLE_SHIPPED_DARK = "shipped_dark"      # merged, never enabled in production
LIFECYCLE_STAGING_ONLY = "staging_only"      # may be exercised in staging only
LIFECYCLE_BLOCKED = "blocked"                # a named prerequisite must land first
LIFECYCLE_ROLLING_OUT = "rolling_out"        # activation in progress
LIFECYCLE_STATES = (
    LIFECYCLE_SHIPPED_DARK, LIFECYCLE_STAGING_ONLY,
    LIFECYCLE_BLOCKED, LIFECYCLE_ROLLING_OUT,
)

# How the flag's life is expected to end.
DECISION_ENABLE = "enable"                       # enable, then retire the flag
DECISION_RETAIN_EXPERIMENTAL = "retain_experimental"
DECISION_REMOVE = "remove"                       # delete the flag and one branch
DECISIONS = (DECISION_ENABLE, DECISION_RETAIN_EXPERIMENTAL, DECISION_REMOVE)

# Who reads the env var. Rollout flags are resolved here unless another module
# already owns a stricter validation path (MOBILE_AUTH_ENABLED does).
PARSED_BY_REGISTRY = "app.feature_flags.resolve_rollout_flags"
PARSED_BY_MOBILE_CREDENTIALS = (
    "app.services.mobile_credentials.validate_mobile_auth_config")

# Single maintainer repository: one owner for every flag today. The field exists
# so a second owner has somewhere to be recorded instead of being folklore.
_OWNER = "@yusufbesirarslan"


@dataclass(frozen=True)
class FeatureFlag:
    """One rollout flag's complete lifecycle record."""

    key: str
    capability: str            # what turning it ON actually changes
    owner: str
    default: bool              # the production default; PR2 changes none of these
    depends_on: tuple          # other flags/config this one interacts with
    observability: str         # what an operator can actually see during rollout
    prerequisites: tuple       # what must be true BEFORE enabling
    success_signals: tuple     # evidence the rollout is working
    abort_signals: tuple       # evidence to roll back immediately
    rollback: str              # the exact rollback action
    lifecycle: str
    review_by: str             # ISO date; decide by then or extend deliberately
    decision: str
    parsed_by: str = PARSED_BY_REGISTRY


_ENV_ROLLBACK = ("set {key}=0 in the host .env and `docker compose up -d`; "
                 "no merge, no redeploy")


def _rollback(key):
    return _ENV_ROLLBACK.format(key=key)


# ── The eight backend rollout flags ────────────────────────────────────────
# Order is the recommended staged activation order (see docs/ROLLOUT.md); it is
# documentation, not an enforcement mechanism.
ROLLOUT_FLAGS = (
    FeatureFlag(
        key="WEEKLY_PROGRAM_UI_ENABLED",
        capability=(
            "Renders the read-only weekly-program card on /training (mount shell "
            "+ static/weekly_program.js + one GET /api/training/weekly-program). "
            "Presentation only — the endpoint stays @require_auth in every flag "
            "state, and OFF emits no markup, script, request or whitespace."),
        owner=_OWNER,
        default=False,
        depends_on=("UIUX_PLAN_V2_ENABLED",),
        observability=(
            "[TRAINING][WEEKLY_PROGRAM] request_id=... state={neutral|"
            "missing_baseline|populated|error} (2 sites, PII-free) plus the PR1 "
            "HttpRequests/HttpLatency SLIs for the training blueprint."),
        prerequisites=(
            "at least one user with enough WorkoutLog history to produce a "
            "non-neutral recommendation, otherwise the card only ever renders "
            "its insufficient_data state",
            "RUNTIME_METRICS_ENABLED=1 so the training blueprint has a latency "
            "and error baseline to compare against",
        ),
        success_signals=(
            "training blueprint 5xx rate unchanged versus the pre-activation "
            "baseline",
            "/api/training/weekly-program p95 latency within the training "
            "blueprint's existing envelope",
            "state=error absent from the [TRAINING][WEEKLY_PROGRAM] log line",
        ),
        abort_signals=(
            "any state=error occurrence attributable to the card",
            "training blueprint 5xx rate above its pre-activation baseline",
            "/training p95 latency regression",
        ),
        rollback=_rollback("WEEKLY_PROGRAM_UI_ENABLED"),
        lifecycle=LIFECYCLE_SHIPPED_DARK,
        review_by="2026-09-01",
        decision=DECISION_ENABLE,
    ),
    FeatureFlag(
        key="UIUX_TODAY_V2_ENABLED",
        capability=(
            "Renders the PR2 Today hierarchy (templates/today.html: one "
            "authoritative next action + honest missing/completed/error "
            "semantics) instead of the legacy dashboard (templates/index.html). "
            "Presentation only; read from current_app.config, never from a "
            "query param, cookie, header or browser storage."),
        owner=_OWNER,
        default=False,
        depends_on=(),
        observability=(
            "PARTIAL — no feature-specific log line or metric. Visible only "
            "through the PR1 tracking-blueprint HTTP SLIs."),
        prerequisites=(
            "RUNTIME_METRICS_ENABLED=1 with a tracking-blueprint baseline",
            "UIUX_NAV_V2_ENABLED still OFF — Today is the nav shell's default "
            "destination, so activating both in one window makes an incident "
            "ambiguous. The dependency is one-directional: `/` is the legacy "
            "shell's Home tab, so Today v2 is fully reachable and testable "
            "without the new shell",
        ),
        success_signals=(
            "tracking blueprint 5xx rate and p95 unchanged",
            "no rise in client errors on the Today route",
        ),
        abort_signals=(
            "5xx on the Today route",
            "p95 regression on the tracking blueprint",
        ),
        rollback=_rollback("UIUX_TODAY_V2_ENABLED"),
        lifecycle=LIFECYCLE_SHIPPED_DARK,
        review_by="2026-10-01",
        decision=DECISION_ENABLE,
    ),
    FeatureFlag(
        key="UIUX_PLAN_V2_ENABLED",
        capability=(
            "Renders the server-authoritative Plan v2 surface "
            "(templates/plan.html) instead of the legacy /training page, "
            "removing the legacy client's clock-based 'today' selection, "
            "rest-day inference and localStorage completion. Presentation only."),
        owner=_OWNER,
        default=False,
        depends_on=("WEEKLY_PROGRAM_UI_ENABLED",),
        observability=(
            "PARTIAL — no feature-specific log line or metric of its own. The "
            "weekly section it hosts is covered by [TRAINING][WEEKLY_PROGRAM]; "
            "the page itself is visible only through the PR1 training-blueprint "
            "HTTP SLIs."),
        prerequisites=(
            "RUNTIME_METRICS_ENABLED=1 with a training-blueprint baseline",
            "WEEKLY_PROGRAM_UI_ENABLED decided first — Plan v2 honours it for "
            "its weekly section, so the two interact on one page",
        ),
        success_signals=(
            "training blueprint 5xx rate and p95 unchanged",
            "weekly section reaches populated/insufficient_data, not error",
        ),
        abort_signals=(
            "read_error page state observed in production",
            "training blueprint 5xx or p95 regression",
        ),
        rollback=_rollback("UIUX_PLAN_V2_ENABLED"),
        lifecycle=LIFECYCLE_SHIPPED_DARK,
        review_by="2026-10-01",
        decision=DECISION_ENABLE,
    ),
    FeatureFlag(
        key="UIUX_COACH_PAGE_V2_ENABLED",
        capability=(
            "Renders the hardened Coach destination (templates/coach_v2.html), "
            "which reuses the existing floating widget and guarantees exactly "
            "one interactive Coach instance, instead of the legacy thin /coach "
            "host. Changes no AI prompt, model, streaming protocol, "
            "persistence, rate limit or moderation policy."),
        owner=_OWNER,
        default=False,
        depends_on=(),
        observability=(
            "PARTIAL — no feature-specific log line or metric. Covered "
            "indirectly by the PR1 coach-blueprint HTTP SLIs and the existing "
            "FitX/AI turn metrics, neither of which can attribute a change to "
            "this flag."),
        prerequisites=(
            "RUNTIME_METRICS_ENABLED=1 with a coach-blueprint baseline",
            "a manual check that only one Coach instance mounts on /coach — "
            "double-mount is the specific failure this flag guards against",
            "re-check this flag's signals AFTER UIUX_NAV_V2_ENABLED activates: "
            "the legacy shell has no /coach entry point at all (not a tab, not "
            "a drawer link), so until the new shell promotes Coach to the "
            "primary tier this route is reached only by direct URL and a clean "
            "window here proves less than it appears",
        ),
        success_signals=(
            "coach blueprint 5xx rate and p95 unchanged",
            "no increase in /coach/history request volume per session, which "
            "would indicate a duplicated widget",
        ),
        abort_signals=(
            "duplicated /coach/history fetches",
            "coach blueprint 5xx or p95 regression",
        ),
        rollback=_rollback("UIUX_COACH_PAGE_V2_ENABLED"),
        lifecycle=LIFECYCLE_SHIPPED_DARK,
        review_by="2026-10-01",
        decision=DECISION_ENABLE,
    ),
    FeatureFlag(
        key="UIUX_NAV_V2_ENABLED",
        capability=(
            "Replaces the legacy 5-tab shell (Home/Nutrition/Training/Progress/"
            "Profile) with the four-destination information architecture "
            "(Today/Plan/Coach/Progress) and moves Nutrition + Community + "
            "utility into the secondary drawer tier. Presentation only; every "
            "route keeps its own @require_auth."),
        owner=_OWNER,
        default=False,
        depends_on=(),
        observability=(
            "PARTIAL — no feature-specific log line or metric exists. Visible "
            "only through the PR1 per-blueprint HttpRequests/HttpLatency/"
            "HttpClientErrors SLIs, which cannot separate a navigation "
            "regression from any other change on the same blueprint."),
        prerequisites=(
            "RUNTIME_METRICS_ENABLED=1 with a per-blueprint baseline",
            "every destination reachable in the new shell verified by hand — "
            "the flag has the widest UI blast radius of the eight",
            "the presentation flags it hosts (Today/Plan/Coach v2) settled "
            "first. This flag goes LAST among the presentation flags because it "
            "pairs the widest blast radius with the weakest observability: "
            "activating it earlier would put every subsequent rollout behind a "
            "shell change that no metric can attribute a regression to",
        ),
        success_signals=(
            "no increase in 4xx on any blueprint reachable from the shell",
            "no drop in requests to blueprints demoted to the drawer tier "
            "(nutrition, social), which would indicate they became unreachable",
        ),
        abort_signals=(
            "requests to a demoted blueprint fall sharply",
            "4xx/5xx increase on any blueprint after activation",
        ),
        rollback=_rollback("UIUX_NAV_V2_ENABLED"),
        lifecycle=LIFECYCLE_SHIPPED_DARK,
        review_by="2026-10-01",
        decision=DECISION_ENABLE,
    ),
    FeatureFlag(
        key="FITX_WORKOUT_SESSIONS_ENABLED",
        capability=(
            "Enables the persisted workout-session lifecycle: the "
            "/workout/session/* write routes stop returning 404 and the state "
            "resolver emits the additive contract_version=2 vocabulary "
            "(resume/in_progress from a persisted ACTIVE session). OFF "
            "preserves the contract_version=1 contract exactly."),
        owner=_OWNER,
        default=False,
        depends_on=(),
        observability=(
            "[WORKOUT_STATE] anomaly and [WORKOUT_SESSION] log lines (PII-free) "
            "plus the PR1 training-blueprint HTTP SLIs. No session-lifecycle "
            "metric exists — counts of active/abandoned/stale sessions are only "
            "obtainable by querying the database."),
        prerequisites=(
            "migration a994f9bed783 applied — the partial unique index "
            "uq_workout_session_active_owner is the at-most-one-ACTIVE-session "
            "invariant, so enabling without it is unsafe",
            "RUNTIME_METRICS_ENABLED=1 with a training-blueprint baseline",
            "staging exercise of the full lifecycle including the "
            "abandoned/stale paths",
        ),
        success_signals=(
            "no is_active_session_owner_violation IntegrityError in logs",
            "no [WORKOUT_STATE] completion_marker_mismatch anomalies",
            "clients continue to accept contract_version=2 (additive)",
        ),
        abort_signals=(
            "any unexpected IntegrityError on WorkoutSession",
            "sessions stuck ACTIVE after a completion",
            "contract_version=2 rejected by a released client",
        ),
        rollback=(
            "set FITX_WORKOUT_SESSIONS_ENABLED=0 in the host .env and "
            "`docker compose up -d`. Safe with sessions already persisted: the "
            "read contract ignores those rows, it never deletes them. The "
            "migration is NOT rolled back (expand-only, by design)."),
        lifecycle=LIFECYCLE_STAGING_ONLY,
        review_by="2026-11-01",
        decision=DECISION_ENABLE,
    ),
    FeatureFlag(
        key="AI_ADAPTIVE_PLAN_CONTEXT",
        capability=(
            "Adds the versioned read-only AdaptivePlan block to the coach "
            "context AND switches the coach system prompt to "
            "ADAPTIVE_COACH_SYSTEM_PROMPT, which removes the older ad-hoc "
            "planning authority and makes AdaptivePlan the single planning "
            "source. Broadest behavioural change of the eight: it alters what "
            "the AI says, on every coach turn, for every user."),
        owner=_OWNER,
        default=False,
        depends_on=(),
        observability=(
            "[COACH][ADAPTIVE_PLAN] log lines (5 sites, PII-free) plus the PR1 "
            "AiProviderCalls/AiProviderLatency/AiRetries SLIs and the existing "
            "FitX/AI turn + token metrics. Output QUALITY is not observable by "
            "any metric — only human review of coach answers can judge it."),
        prerequisites=(
            "staging evaluation of coach answers under both prompts on the "
            "same inputs — no metric can detect a worse answer",
            "RUNTIME_METRICS_ENABLED=1 with an AI provider baseline",
            "AI token-cost baseline recorded, since the block enlarges every "
            "prompt",
        ),
        success_signals=(
            "AI error/retry rate unchanged",
            "prompt token growth within the recorded budget",
            "coach answers judged no worse in the staging evaluation",
        ),
        abort_signals=(
            "AI error or retry rate increase",
            "coach answers contradicting the deterministic plan",
            "prompt token growth beyond budget",
        ),
        rollback=_rollback("AI_ADAPTIVE_PLAN_CONTEXT"),
        lifecycle=LIFECYCLE_STAGING_ONLY,
        review_by="2026-11-01",
        decision=DECISION_RETAIN_EXPERIMENTAL,
    ),
    FeatureFlag(
        key="MOBILE_AUTH_ENABLED",
        capability=(
            "Registers the /api/v1 mobile blueprint and the opaque "
            "server-issued credential flow. Unlike the other seven this is an "
            "ATTACK-SURFACE change, not a presentation change: it exposes "
            "pre-auth endpoints to the internet."),
        owner=_OWNER,
        default=False,
        depends_on=(
            "MOBILE_AUTH_DERIVATION_KEYRING",
            "MOBILE_AUTH_ACTIVE_DERIVATION_KEY_VERSION",
        ),
        observability=(
            "structured _security_event records (10 sites) plus the PR1 "
            "mobile-vs-web client-class split on every HTTP SLI, so mobile "
            "traffic is separable from web traffic in CloudWatch."),
        prerequisites=(
            "PR4 (capacity hardening) merged — NEEDED_FIXES_2026-08-02.md "
            "finding 2: /api/v1/auth/login and /refresh make blocking Cognito "
            "calls (~20s each, ~40s chained) with no concurrency gate and no "
            "thread-reserve accounting, and are reachable pre-auth. Enabling "
            "before that fix hands an unauthenticated caller a way to exhaust "
            "all 8 web threads",
            "derivation keyring provisioned and the active key version present",
            "RUNTIME_METRICS_ENABLED=1 with a capacity baseline",
        ),
        success_signals=(
            "ThreadReserve gauge stays above its floor under mobile load",
            "no rise in HttpOverload (503) on web routes",
            "refresh-family reuse detections at zero",
        ),
        abort_signals=(
            "thread reserve exhausted or HttpOverload on web routes",
            "any refresh-token reuse detection",
            "Cognito call latency saturating the mobile auth path",
        ),
        rollback=(
            "set MOBILE_AUTH_ENABLED=0 in the host .env and "
            "`docker compose up -d`; the /api/v1 blueprint is not registered. "
            "Issued mobile credentials stop being accepted — clients must "
            "re-authenticate when it is turned back on."),
        lifecycle=LIFECYCLE_BLOCKED,
        review_by="2026-10-01",
        decision=DECISION_ENABLE,
        # Keeps its own stricter validator: it also validates credential
        # lifetimes and the derivation keyring, and it must run before the
        # blueprint registration decision. Registering a second parse path here
        # would be exactly the duplicate registry this PR avoids.
        parsed_by=PARSED_BY_MOBILE_CREDENTIALS,
    ),
)

# PR1's inventory, now DERIVED rather than hand-maintained. Import site
# (app/config.py) re-exports it, so /health?deep=1 and the [FLAGS] boot log keep
# working unchanged while there is only one list in the codebase.
FEATURE_FLAG_KEYS = tuple(flag.key for flag in ROLLOUT_FLAGS)

FLAGS_BY_KEY = {flag.key: flag for flag in ROLLOUT_FLAGS}


# ── Not rollout flags ──────────────────────────────────────────────────────
# Permanent settings. They are listed here ONLY so the drift test can tell an
# undocumented new rollout flag from a deliberate non-rollout switch. They are
# never reported by feature_flag_state() and have no review date: an emergency
# kill switch that gets "cleaned up" is an incident waiting to happen.
#
# Four categories, because they are not interchangeable and an operator needs to
# know which one they are touching:
CATEGORY_OPERATIONAL = "operational"    # production tuning knob; safe to change
CATEGORY_KILL_SWITCH = "kill_switch"    # default ON; exists to stop an incident
CATEGORY_ENVIRONMENT = "environment"    # environment identity / boot mode
CATEGORY_ESCAPE_HATCH = "escape_hatch"  # weakens a safety property; incident-only
NON_ROLLOUT_CATEGORIES = (
    CATEGORY_OPERATIONAL, CATEGORY_KILL_SWITCH,
    CATEGORY_ENVIRONMENT, CATEGORY_ESCAPE_HATCH,
)

# Name kept for continuity: this is every boolean env key in the application
# package that is deliberately NOT a rollout flag.
OPERATIONAL_BOOLEAN_KEYS = {
    "AI_PLAN_QUOTA_ENABLED": CATEGORY_OPERATIONAL,
    "AI_CHAT_QUOTA_ENABLED": CATEGORY_OPERATIONAL,
    "BEDROCK_ENABLED": CATEGORY_OPERATIONAL,
    "BEDROCK_PROMPT_CACHE": CATEGORY_OPERATIONAL,
    "AI_METRICS_ENABLED": CATEGORY_OPERATIONAL,
    "RUNTIME_METRICS_ENABLED": CATEGORY_OPERATIONAL,
    "LOGIN_FAIL_CLOSED": CATEGORY_KILL_SWITCH,
    "AI_MEMORY_ENABLED": CATEGORY_KILL_SWITCH,
    "AI_CACHE_ENABLED": CATEGORY_KILL_SWITCH,
    "AI_RECOVERY_ENABLED": CATEGORY_KILL_SWITCH,
    # Environment identity and boot mode. Not tuning knobs and not rollout
    # candidates: they answer "which environment is this?", not "is this feature
    # on?". Flipping one in production changes the security posture of the whole
    # process, not the behaviour of one capability.
    "FLASK_DEBUG": CATEGORY_ENVIRONMENT,
    "FLASK_ENV": CATEGORY_ENVIRONMENT,
    "FITX_SKIP_DB_INIT": CATEGORY_ENVIRONMENT,
    # Escape hatches: each one deliberately disables a safety property, and each
    # exists for a documented incident procedure. They must never be staged,
    # rolled out, or "enabled to see what happens".
    "FATSECRET_ALLOW_INSECURE": CATEGORY_ESCAPE_HATCH,   # allows non-https base URL
    "FITX_DB_AUTO_UPGRADE": CATEGORY_ESCAPE_HATCH,       # disables boot migrations
    "FITX_DB_UPGRADE_FAIL_OPEN": CATEGORY_ESCAPE_HATCH,  # boots past a failed migration
}


# ── Cross-repository lifecycle entries ─────────────────────────────────────
@dataclass(frozen=True)
class CrossRepositoryFlag:
    """A flag this backend can neither read nor change.

    Recorded so it has an owner, a review date and a decision — NOT so the
    backend can pretend to control it.
    """

    key: str
    repository: str
    declared_in: str
    mechanism: str
    capability: str
    owner: str
    depends_on: tuple
    rollback: str
    lifecycle: str
    review_by: str
    decision: str


CROSS_REPOSITORY_FLAGS = (
    CrossRepositoryFlag(
        key="AXISAI_NATIVE_AUTH_ENABLED",
        repository="axisai-mobile (Flutter)",
        declared_in="lib/core/config/native_auth_rollout.dart",
        mechanism=(
            "Dart bool.fromEnvironment compile-time constant, baked in at "
            "`flutter build --dart-define`. The backend cannot read it, flip it "
            "or roll it back, and a released binary keeps whatever value it was "
            "compiled with. NO backend runtime-control mechanism exists for it "
            "and none will be built: that would be a promise this process "
            "cannot honour."),
        capability="Selects the native-auth path in the Flutter client.",
        owner=_OWNER,
        depends_on=("MOBILE_AUTH_ENABLED",),
        rollback=(
            "ship a new build compiled with the flag off and wait for client "
            "adoption — there is no server-side switch. Plan rollback in "
            "release-cycle time, not in seconds."),
        lifecycle=LIFECYCLE_BLOCKED,
        review_by="2026-10-01",
        decision=DECISION_ENABLE,
    ),
)


# ── Resolution ─────────────────────────────────────────────────────────────
def resolve_rollout_flags(environ):
    """Resolve every registry-parsed rollout flag from ``environ``.

    Returns a ``{key: bool}`` mapping ready for ``app.config.update``. Flags
    owned by another validator (``parsed_by`` != registry) are skipped — they
    write their own key.

    Every malformed key is collected before raising, so an operator with two bad
    values fixes both in one pass instead of discovering the second on the next
    failed deploy.
    """
    resolved = {}
    invalid = []
    for flag in ROLLOUT_FLAGS:
        if flag.parsed_by != PARSED_BY_REGISTRY:
            continue
        try:
            resolved[flag.key] = parse_bool_flag(
                flag.key, environ.get(flag.key), default=flag.default)
        except FeatureFlagConfigurationError:
            invalid.append(flag.key)
    if invalid:
        raise FeatureFlagConfigurationError(
            "invalid feature flag values: " + ", ".join(sorted(invalid))
            + " (each must be set to exactly '0' or '1', or left unset)")
    return resolved
