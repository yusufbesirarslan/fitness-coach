# Feature flags — inventory and lifecycle

Source of truth: **`app/feature_flags.py`**. This document is the readable
projection of that registry; `tests/test_feature_flag_registry.py` fails if a
flag exists in one and not the other.

Nothing here activates a feature. Every default below is the value that was
already running in production. Activation is operator-run runbook work —
see [ROLLOUT.md](ROLLOUT.md).

---

## Three kinds of boolean setting

They are routinely confused, and the confusion is expensive: an "unused flag
cleanup" that deletes a kill switch removes the ability to stop an incident.

| Kind | Default | Expiry | Purpose |
|---|---|---|---|
| **Rollout flag** | OFF | Yes — review date + terminal decision | Gates a merged-but-not-activated capability. Ends its life enabled-and-removed, or removed-unshipped. |
| **Operational configuration** | varies | No | Permanent runtime setting (provider choice, cache, metrics). Changing it is a tuning decision, not a rollout. |
| **Emergency kill switch** | ON | No — **never remove** | Exists so a subsystem can be disabled fast during an incident. |

Only rollout flags appear in `FEATURE_FLAG_KEYS`, in `/health?deep=1`'s `flags`
block, and in the `[FLAGS] enabled=…` boot line. The other two are recorded in
`feature_flags.OPERATIONAL_BOOLEAN_KEYS` so the drift test can tell them apart:

| Key | Default | Category |
|---|---|---|
| `LOGIN_FAIL_CLOSED` | 1 | kill switch — Redis unavailable ⇒ login 503 rather than an unthrottled login path |
| `AI_MEMORY_ENABLED` | 1 | kill switch — persistent coach memory |
| `AI_CACHE_ENABLED` | 1 | kill switch — AI response cache |
| `AI_RECOVERY_ENABLED` | 1 | kill switch — retry/fallback ladder |
| `AI_PLAN_QUOTA_ENABLED` | 1 | operational — freemium plan quota |
| `AI_CHAT_QUOTA_ENABLED` | 1 | operational — freemium chat quota |
| `BEDROCK_ENABLED` | 0 | operational — primary heavy-AI provider selection |
| `BEDROCK_PROMPT_CACHE` | 0 | operational — Bedrock prompt caching |
| `AI_METRICS_ENABLED` | 0 | operational — `FitX/AI` CloudWatch metrics |
| `RUNTIME_METRICS_ENABLED` | 0 | operational — `FitX/Runtime` SLIs (PR1) |

---

## How a flag value is read

Rollout flags are resolved in `configure_app` through
`feature_flags.resolve_rollout_flags(os.environ)`.

| Value | Result |
|---|---|
| unset | default (OFF) |
| `` (empty / whitespace) | default (OFF) — `KEY=` is a normal "not set" in a `.env` |
| `0` | OFF |
| `1` | ON |
| anything else — `true`, `TRUE`, `yes`, `on`, `1 `, ` 1`, `01` | **boot fails**, naming every offending key |

**Why raise instead of defaulting.** The historical idiom
`os.getenv(KEY, "0") == "1"` read `true` and `TRUE` as a **silent OFF**: an
operator who believed they enabled a feature got a disabled one, with no error
anywhere and no way to tell from outside. And we deliberately do **not**
strip-then-accept, because `KEY=1 ` would then *activate* a capability on a host
where it is currently off — a rollout nobody decided to run. Refusing to guess
is the only reading that neither silently activates nor silently ignores.

**The cost, stated plainly.** A host carrying a malformed value boots today
(flag OFF) and will fail to boot after this change. That failure is caught by
the deploy's `/health?deep=1` gate, which rolls back automatically — but the
right time to find it is *before* deploying. [ROLLOUT.md](ROLLOUT.md) has the
pre-deployment check, and `MOBILE_AUTH_ENABLED` has behaved this way since it
shipped, so this is existing precedent rather than a new posture.

`MOBILE_AUTH_ENABLED` keeps its own validator
(`mobile_credentials.validate_mobile_auth_config`) because it also validates
credential lifetimes and the derivation keyring, and must run before the
blueprint-registration decision. It shares the same parser but sets
`allow_empty=False`: it opens a pre-auth attack surface, so `MOBILE_AUTH_ENABLED=`
is rejected rather than read as OFF. That difference is intentional and tested.

---

## The eight backend rollout flags

Owner for all eight: **@yusufbesirarslan** (single-maintainer repository; the
field exists so a second owner has somewhere to be recorded rather than being
folklore).

Rows are in the recommended staged activation order.

| # | Flag | Default | Lifecycle | Observability | Review by | Decision |
|---|---|---|---|---|---|---|
| 1 | `WEEKLY_PROGRAM_UI_ENABLED` | OFF | shipped_dark | **Full** — `[TRAINING][WEEKLY_PROGRAM]` state line (2 sites) | 2026-09-01 | enable |
| 2 | `UIUX_NAV_V2_ENABLED` | OFF | shipped_dark | **Partial** — HTTP SLIs only | 2026-10-01 | enable |
| 3 | `UIUX_TODAY_V2_ENABLED` | OFF | shipped_dark | **Partial** — HTTP SLIs only | 2026-10-01 | enable |
| 4 | `UIUX_PLAN_V2_ENABLED` | OFF | shipped_dark | **Partial** — weekly section only | 2026-10-01 | enable |
| 5 | `UIUX_COACH_PAGE_V2_ENABLED` | OFF | shipped_dark | **Partial** — HTTP SLIs only | 2026-10-01 | enable |
| 6 | `FITX_WORKOUT_SESSIONS_ENABLED` | OFF | staging_only | **Partial** — anomaly logs, no lifecycle metric | 2026-11-01 | enable |
| 7 | `AI_ADAPTIVE_PLAN_CONTEXT` | OFF | staging_only | **Partial** — quality is not observable | 2026-11-01 | retain experimentally |
| 8 | `MOBILE_AUTH_ENABLED` | OFF | **blocked** | **Full** — security events + client-class split | 2026-10-01 | enable (after PR4) |

### Observability is the constraint on the order

This is the finding that shaped the recommendation. Four of the eight —
`UIUX_NAV_V2_ENABLED`, `UIUX_TODAY_V2_ENABLED`, `UIUX_PLAN_V2_ENABLED`,
`UIUX_COACH_PAGE_V2_ENABLED` — emit **no feature-specific log line or metric at
all**. After PR1 they are visible only through per-blueprint HTTP SLIs, which
cannot separate a navigation regression from any other change on the same
blueprint. Enabling two of them in the same window makes an incident ambiguous:
you would know something regressed, not which flag did it. Hence: one flag per
window, and the best-instrumented flag first.

### Per-flag records

Full records — capability, dependencies, prerequisites, success and abort
signals, rollback — live in `app/feature_flags.py` and are reproduced in the
runbook's activation procedures. The summary that matters here:

**1. `WEEKLY_PROGRAM_UI_ENABLED`** — read-only weekly-program card on `/training`
(mount shell + `weekly_program.js` + one `GET /api/training/weekly-program`).
Presentation only; the endpoint stays `@require_auth` in every flag state.
Interacts with `UIUX_PLAN_V2_ENABLED`, which honours it for its weekly section.
Abort on any `state=error`, a training-blueprint 5xx rise, or a `/training` p95
regression.

**2. `UIUX_NAV_V2_ENABLED`** — four-destination shell (Today/Plan/Coach/Progress)
replacing the legacy 5-tab shell; Nutrition + Community move to the drawer tier.
Widest UI blast radius of the eight. The specific abort signal is a *fall* in
requests to a demoted blueprint (nutrition, social) — that means it became
unreachable, which a 5xx-based alarm would never catch.

**3. `UIUX_TODAY_V2_ENABLED`** — PR2 Today hierarchy (`today.html`) instead of the
legacy dashboard (`index.html`). Decide flag 2 first: Today is the new shell's
default destination.

**4. `UIUX_PLAN_V2_ENABLED`** — server-authoritative Plan v2 (`plan.html`),
removing the legacy client's clock-based "today" selection, rest-day inference
and localStorage completion. Decide flag 1 first — they share a page.

**5. `UIUX_COACH_PAGE_V2_ENABLED`** — hardened Coach destination reusing the
existing widget, guaranteeing exactly one interactive instance. Changes no AI
prompt, model, streaming protocol, persistence, rate limit or moderation policy.
Abort signal: duplicated `/coach/history` fetches (a double mount).

**6. `FITX_WORKOUT_SESSIONS_ENABLED`** — persisted workout-session lifecycle;
`/workout/session/*` stops 404-ing and the resolver emits the additive
`contract_version=2`. **Requires migration `a994f9bed783`** — the partial unique
index `uq_workout_session_active_owner` *is* the at-most-one-ACTIVE-session
invariant, so enabling without it is unsafe. Turning it back off is safe with
sessions already persisted: the read contract ignores those rows, never deletes
them. The migration is not rolled back (expand-only, by design).

**7. `AI_ADAPTIVE_PLAN_CONTEXT`** — adds the versioned read-only AdaptivePlan
block to coach context **and** switches the coach system prompt to
`ADAPTIVE_COACH_SYSTEM_PROMPT`. Broadest behavioural change of the eight: it
alters what the AI says, on every turn, for every user. **Answer quality is not
observable by any metric** — only human review of staging answers can judge it,
which is why the decision is *retain experimentally* rather than *enable*.

**8. `MOBILE_AUTH_ENABLED`** — registers the `/api/v1` mobile blueprint and the
opaque credential flow. Unlike the other seven this is an **attack-surface**
change, not a presentation change. **Blocked until PR4 merges**:
`NEEDED_FIXES_2026-08-02.md` finding 2 records that `/api/v1/auth/login` and
`/refresh` make blocking Cognito calls (~20 s each, ~40 s chained on login) with
no concurrency gate and no thread-reserve accounting, and are reachable
pre-auth. Enabling it before that fix hands an unauthenticated caller a way to
exhaust all 8 web threads. Rollback also invalidates issued mobile credentials —
clients must re-authenticate.

---

## Cross-repository: `AXISAI_NATIVE_AUTH_ENABLED`

| Field | Value |
|---|---|
| Repository | `axisai-mobile` (Flutter) |
| Declared in | `lib/core/config/native_auth_rollout.dart` |
| Mechanism | Dart `bool.fromEnvironment` compile-time constant, set at `flutter build --dart-define` |
| Depends on | `MOBILE_AUTH_ENABLED` |
| Rollback | ship a new build with the flag off and wait for client adoption |
| Lifecycle | blocked |
| Review by | 2026-10-01 |
| Decision | enable |

**This is not a ninth backend flag and no backend runtime control will be built
for it.** It is baked into a shipped binary: the backend cannot read it, flip it
or roll it back, and a released build keeps whatever value it was compiled with.
A backend switch would be a promise this process cannot honour. Rollback is
release-cycle work measured in days, not the seconds an `.env` edit takes — plan
it accordingly, and never enable it before `MOBILE_AUTH_ENABLED`.

---

## Adding a new rollout flag

`tests/test_feature_flag_registry.py` enforces all of this:

1. Add a `FeatureFlag(...)` record to `ROLLOUT_FLAGS` with **every** field
   populated — including at least one prerequisite, one success signal and one
   abort signal. A flag with no abort signal has no defined way to be called
   broken.
2. Do **not** add `X = os.getenv("X", "0") == "1"` to `app/config.py`; the
   registry resolves it. (A flag added the old way would silently read `true` as
   OFF again — the test rejects it.)
3. Add a commented `# X=0` line to `.env.example`.
4. Add the flag to the tables above and to `docs/ROLLOUT.md`.
5. Default must be OFF.

If the setting is permanent configuration or a kill switch, add it to
`OPERATIONAL_BOOLEAN_KEYS` instead — it is not a rollout flag and must not carry
a review date.

## Answering "what is on in production right now?"

Rollout flags live in the host `.env`, which the deploy pipeline never ships
(`git reset --hard origin/main` does not touch it). The repository therefore
cannot answer the question. Two surfaces can:

- the `[FLAGS] enabled=…` line in the boot log (deploy output / container logs);
- the `flags` block in `/health?deep=1` (internal networks only — names and
  booleans, never values, secrets or connection strings).

There is deliberately **no public flag endpoint**.
