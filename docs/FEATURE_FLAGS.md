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
| `FLASK_DEBUG` | 0 | environment — environment identity, not a tuning knob |
| `FLASK_ENV` | — | environment — environment identity |
| `FITX_SKIP_DB_INIT` | 0 | environment — boot mode (skips `create_all` + migrations) |
| `FATSECRET_ALLOW_INSECURE` | 0 | escape hatch — permits a non-`https://` FatSecret base URL |
| `FITX_DB_AUTO_UPGRADE` | 1 | escape hatch — `0` disables automatic boot migrations |
| `FITX_DB_UPGRADE_FAIL_OPEN` | 0 | escape hatch — `1` boots past a failed migration |

**Environment** and **escape hatch** are separated from operational on purpose.
An operational setting is a knob you may tune in production; an environment key
answers "which environment is this?" and changes the security posture of the
whole process; an escape hatch deliberately disables a safety property and exists
for one documented incident procedure. None of the three may be staged, rolled
out, or "enabled to see what happens".

---

## How a flag value is read

Rollout flags are resolved in `configure_app` through
`feature_flags.resolve_rollout_flags(os.environ)`.

**The eight flags do not all treat an empty value the same way.** Seven share one
behaviour; `MOBILE_AUTH_ENABLED` is deliberately stricter, and the difference is
tested:

| Value | Seven registry-parsed flags | `MOBILE_AUTH_ENABLED` |
|---|---|---|
| unset | default (OFF) | OFF |
| `` (empty / whitespace) | default (OFF) — `KEY=` is a normal "not set" in a `.env` | **rejected** — `CredentialConfigurationError: invalid MOBILE_AUTH_ENABLED` |
| `0` | OFF | OFF |
| `1` | ON | ON |
| anything else — `true`, `TRUE`, `yes`, `on`, `1 `, ` 1`, `01` | **boot fails**, naming every offending key | **boot fails** — `CredentialConfigurationError` |

Two differences, both intentional and both pre-existing rather than introduced by
PR2: `MOBILE_AUTH_ENABLED` refuses an empty value (`allow_empty=False`), and it
raises `CredentialConfigurationError` instead of `FeatureFlagConfigurationError`.
It opens a **pre-auth attack surface**, so "the operator left it blank" is not a
safe thing to interpret as OFF — an ambiguous value must be corrected, not
guessed. Anything relying on a uniform empty-value rule across all eight flags is
relying on something that is not true.

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
blueprint-registration decision. It shares the canonical parser — there is one
definition of what a flag value means — but keeps the two differences in the
table above. Its record declares this in `parsed_by`, and the drift gate derives
its allowance from that field, so the record and the code cannot disagree about
who owns the read.

## Nothing may read a rollout flag from the environment

Rollout flags are resolved **once at boot** and read from `current_app.config`
thereafter. `tests/test_feature_flag_registry.py` scans every module in `app/`
(plus `starter.py`) and fails the build if a registered key is read via
`os.getenv` / `os.environ.get` / `os.environ[...]` anywhere except the module its
own record names in `parsed_by`.

The gate exists because a single `os.getenv("UIUX_NAV_V2_ENABLED")` inside a
blueprint would restore every failure mode PR2 removed, all at once: it would
skip the strict parser (so `true` becomes a silent OFF again), skip `app.config`
(so `/health?deep=1` and the `[FLAGS]` boot line would stop describing what the
process is actually doing), and skip the registry (so the flag would have no
owner, no review date and no rollback procedure). The same scan rejects any new
boolean env switch anywhere in the package that is not classified as either a
rollout flag or one of the four non-rollout categories.

---

## The eight backend rollout flags

Owner for all eight: **@yusufbesirarslan** (single-maintainer repository; the
field exists so a second owner has somewhere to be recorded rather than being
folklore).

Rows are in the recommended staged activation order.

| # | Flag | Default | Lifecycle | Observability | Review by | Decision |
|---|---|---|---|---|---|---|
| 1 | `WEEKLY_PROGRAM_UI_ENABLED` | OFF | shipped_dark | **Full** — `[TRAINING][WEEKLY_PROGRAM]` state line (2 sites) | 2026-09-01 | enable |
| 2 | `UIUX_TODAY_V2_ENABLED` | OFF | shipped_dark | **Partial** — HTTP SLIs only; no longer a Home selector (UX-2 PR4) | 2026-10-01 | enable |
| 3 | `UIUX_COACH_PAGE_V2_ENABLED` | OFF | shipped_dark | **Partial** — HTTP SLIs only | 2026-10-01 | enable |
| 4 | `UIUX_NAV_V2_ENABLED` | OFF | shipped_dark | **Partial** — HTTP SLIs only | 2026-10-01 | enable |
| 5 | `FITX_WORKOUT_SESSIONS_ENABLED` | OFF | staging_only | **Full** — anomaly logs + bounded `WorkoutSessionLifecycle` events | 2026-11-01 | enable |
| 6 | `AI_ADAPTIVE_PLAN_CONTEXT` | OFF | staging_only | **Partial** — quality is not observable | 2026-11-01 | retain experimentally |
| 7 | `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` | OFF | staging_only | **Full** — `[COACH][PLAN_TOOL]` outcome line + the durable mutation journal | 2026-11-01 | enable |
| 8 | `MOBILE_AUTH_ENABLED` | OFF | **blocked** | **Full** — security events + client-class split | 2026-10-01 | enable (after PR4) |

### Observability is the constraint on the order

This is the finding that shaped the recommendation. Three of the eight —
`UIUX_NAV_V2_ENABLED`, `UIUX_TODAY_V2_ENABLED`, `UIUX_COACH_PAGE_V2_ENABLED` —
emit **no feature-specific log line or metric at all**. After PR1 they are
visible only through per-blueprint HTTP SLIs, which cannot separate a navigation
regression from any other change on the same blueprint. Enabling two of them in
the same window makes an incident ambiguous: you would know something
regressed, not which flag did it. Hence: one flag per window, and the
best-instrumented flag first.

`UIUX_PLAN_V2_ENABLED` was retired in WEB-UX3-PR6B and is no longer in this
inventory. See **Retired flags** below. `UIUX_TODAY_V2_ENABLED` and
`UIUX_NAV_V2_ENABLED` remain registered historical no-ops; retiring them is
separate cleanup debt.

### Why Nav v2 goes last among the presentation flags

`UIUX_NAV_V2_ENABLED` pairs the **widest** blast radius with the **weakest**
observability, so it is the one flag whose failure would be hardest to attribute
— and, activated early, every subsequent rollout would sit behind a shell change
no metric can absolve. It therefore goes after the active destination rollout it
still hosts: Coach.

**There is no technical dependency requiring it first.** Verified in the source:

- `app/nav.py` points the v2 primary tier at four **pre-existing canonical
  routes** — `/`, `/training`, `/coach`, `/progress-page`. All four respond
  regardless of the remaining Today/Coach v2 flags, and `/training` now always
  renders Plan, so Nav v2 renders correctly before those flags activate.
- The dependency runs the *other* way, and only as a scheduling constraint:
  `/` is the legacy shell's **Home** tab and `/training` is its **Training** tab,
  so Today and the unconditional Plan surface remain fully reachable while Nav
  v2 is off.

One asymmetry is worth stating rather than hiding: **the legacy shell has no
`/coach` entry point at all** — not a tab, not a drawer link (`templates/_nav.html`).
Until Nav v2 promotes Coach to the primary tier, `/coach` is reached only by
direct URL, while the everyday coach entry point remains the floating widget,
which this flag does not change. So a clean observation window for
`UIUX_COACH_PAGE_V2_ENABLED` at position 3 proves less than it appears, and its
signals must be **re-checked after Nav v2 activates**. That is recorded in the
flag's own prerequisites. It is an argument for watching Coach twice, not for
moving Nav earlier.

### Per-flag records

Full records — capability, dependencies, prerequisites, success and abort
signals, rollback — live in `app/feature_flags.py` and are reproduced in the
runbook's activation procedures. The summary that matters here:

**1. `WEEKLY_PROGRAM_UI_ENABLED`** — read-only weekly-program card on `/training`
(mount shell + `weekly_program.js` + one `GET /api/training/weekly-program`).
Presentation only; the endpoint stays `@require_auth` in every flag state.
Mounts on the canonical Plan page at `/training`. It does not depend on the
retired Plan rollout flag. Abort on any `state=error`, a training-blueprint
5xx rise, or a `/training` p95 regression.

**2. `UIUX_TODAY_V2_ENABLED`** — historical presentation flag. UX-2 PR4 made the
Today hierarchy (`today.html`) the production Home and deleted the legacy
dashboard (`index.html`), so the key no longer selects a user-reachable branch:
`/` renders Today at either value. Setting it to `0` does **not** restore the
legacy dashboard — rolling that back is a `git revert` of the UX-2 PR4 Home
convergence plus a redeploy. Nothing here has a rollout window left; the row
stays only so `/health?deep=1` and the `[FLAGS]` boot line keep listing it.
Retiring this no-op is separate cleanup debt, not WEB-UX3-PR6B.

**3. `UIUX_COACH_PAGE_V2_ENABLED`** — hardened Coach destination reusing the
existing widget, guaranteeing exactly one interactive instance. Changes no AI
prompt, model, streaming protocol, persistence, rate limit or moderation policy.
Abort signal: duplicated `/coach/history` fetches (a double mount). Re-check its
signals after flag 4 — see the ordering note above.

**4. `UIUX_NAV_V2_ENABLED`** — historical presentation flag. UX-1 PR2 made
Today/Plan/Coach/Progress production chrome. The key remains in the registry
but no longer selects a user-reachable legacy five-tab branch or drawer.
Setting it to `0` does **not** restore Home/Nutrition/Training/Progress/Profile
or the hamburger. Rollback is a git revert of that chrome change, not an env
flip. Nutrition and Community stay reachable by their locked URLs; they are
not a fifth primary tab. Retiring this no-op is separate cleanup debt, not
WEB-UX3-PR6B.

**5. `FITX_WORKOUT_SESSIONS_ENABLED`** — persisted workout-session lifecycle;
`/workout/session/*` stops 404-ing and the resolver emits the additive
`contract_version=2`. **Requires migration `a994f9bed783`** — the partial unique
index `uq_workout_session_active_owner` *is* the at-most-one-ACTIVE-session
invariant, so enabling without it is unsafe. Turning it back off is safe with
sessions already persisted: the read contract ignores those rows, never deletes
them. The migration is not rolled back (expand-only, by design). Lifecycle
observability is `WorkoutSessionLifecycle` in `FitX/Runtime`, with the single
fixed `Event` dimension (`started`, `resumed`, `checkpointed`, `abandoned`,
`completed`, `revision_conflict`). It counts committed transitions, not replays,
and contains no user/session identity. Staging therefore also requires
`RUNTIME_METRICS_ENABLED=1`; neither flag's repository default changes here.
The full operator procedure — prerequisites, activation order, lifecycle
exercise matrix, success/abort signals, rollback and post-rollback inertness —
is [WORKOUT_SESSION_ACTIVATION.md](WORKOUT_SESSION_ACTIVATION.md). Readiness
status at 2026-09-09: **GO for technical staging-readiness, pending independent
PR5 review.** The lifecycle exercise has been run in the isolated staging
environment ([STAGING.md](STAGING.md), #290); see
[WORKOUT_SESSION_ACTIVATION.md](WORKOUT_SESSION_ACTIVATION.md) §14.1. The
repository default stays OFF. The flag stays `staging_only`. This is not
production activation.

**6. `AI_ADAPTIVE_PLAN_CONTEXT`** — adds the versioned read-only AdaptivePlan
block to coach context **and** switches the coach system prompt to
`ADAPTIVE_COACH_SYSTEM_PROMPT`. Broadest behavioural change of the eight: it
alters what the AI says, on every turn, for every user. **Answer quality is not
observable by any metric** — only human review of staging answers can judge it,
which is why the decision is *retain experimentally* rather than *enable*.

**7. `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED`** — publishes six narrow plan-editing
tools (replace / add / remove an exercise, update a prescription, move a day,
undo the last change) to both coach providers and lets the Coach execute them
through `app/services/coach_plan_tools` → `app/services/plan_mutation`. **This is
the only flag of the eight that lets the AI cause a durable write to user data**;
every other one changes presentation, context or attack surface. **Requires
migration `b3c4d5e6f7a8`** — undo, replay and the audit trail are all built on
the mutation journal. Decide flag 6 first: it owns which system prompt is
active, and the plan-mutation policy block is appended to whichever one is, so
activating both in one window makes a bad answer impossible to attribute.

The abort signals are behavioural rather than numeric, and they are the point:
any `actor='ai_coach'` journal row the user did not explicitly ask for, a
mutation applied while the answer claims none was, an undo that reverses
something other than the newest change, or an `AMBIGUOUS_TARGET` followed by a
mutation in the same turn (the model guessed instead of asking). None of those
is a 5xx, so the journal — not an alarm — is the instrument. Rollback stops new
AI writes; it does **not** revert mutations already applied, which remain
ordinary versioned plan history the user can undo.

**8. `MOBILE_AUTH_ENABLED`** — registers the `/api/v1` mobile blueprint and the
opaque credential flow. Unlike the presentation flags this is an
**attack-surface** change. **Blocked until PR4 merges**:
`NEEDED_FIXES_2026-08-02.md` finding 2 records that `/api/v1/auth/login` and
`/refresh` make blocking Cognito calls (~20 s each, ~40 s chained on login) with
no concurrency gate and no thread-reserve accounting, and are reachable
pre-auth. Enabling it before that fix hands an unauthenticated caller a way to
exhaust all 8 web threads. Rollback also invalidates issued mobile credentials —
clients must re-authenticate.

---

## Retired flags

**`UIUX_PLAN_V2_ENABLED`** — retired in WEB-UX3-PR6B. GET `/training` always
renders `templates/plan.html`. The key is absent from `ROLLOUT_FLAGS`,
`FEATURE_FLAG_KEYS`, `/health?deep=1`, and the `[FLAGS]` boot line. A leftover
host `.env` value is ignored and does not fail boot. Rollback is `git revert`
of WEB-UX3-PR6B plus a redeploy, not an env flip. Do not re-add this key as a
historical no-op, operational boolean, kill switch, or compatibility alias.

`UIUX_TODAY_V2_ENABLED` and `UIUX_NAV_V2_ENABLED` remain registered historical
no-ops. They are **separate cleanup debt** and are not a reason to keep the
Plan flag.

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

**This is not a backend rollout flag and no backend runtime control will be built
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
