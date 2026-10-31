# Needed Fixes — Triage 2026-10-31

**Scope:** Full-codebase bug / security / structure triage run by three parallel
deep-dive agents plus direct verification of the headline findings.
Surfaces covered:

1. **Security** — authN/authZ (IDOR), HMAC owner-bound tokens, CSRF/CSP, SSRF,
   S3 presigned URLs, secrets/PII logging, rate limiting, Apple subscription
   verification.
2. **Backend correctness** — transactions, lock ordering, idempotency/replay,
   AI allowance ledger & consent fences, day-boundary dedup, timezone, latest-row
   selectors, migrations.
3. **API contract & structure** — mobile error-handling fall-through, input
   validation, frontend JS state machines, `Cache-Control`, dead code, module size.

All three passes cross-checked against prior triage records
(`TRIAGE_FINDINGS_2026-10-01.md`, `NEEDED_FIXES*.md`, `SEC001*`, `SEC005*`) to
avoid re-reporting already-fixed items and to catch regressions.

---

## Headline

The codebase is in **excellent** security and correctness shape. Across all three
deep dives **no new Critical or High issues were found, and no regressions of any
previously-fixed item.** The intended invariants (lock ordering, atomic commits,
unique-constraint arbitration, write-then-check resurrection defense, SSRF boundary,
S3 owner-scoping, typed mobile 503s, frontend single-flight/superseded-drop) are
all in force and backed by tests.

The actionable residue is one **Medium** (web-side `Cache-Control` gap on several
per-user JSON reads), a couple of **Low** correctness/hardening items, and some
**structural debt**. Nothing here is release-blocking.

| ID | Severity | Area | Summary |
|----|----------|------|---------|
| M1 | Medium | Privacy / caching | Several per-user **web** JSON reads served without `private, no-store` (DMs, coach history, gallery, gamification, meal-log history) |
| L1 | Low | Error handling | `POST /api/v1/training/plans` lacks the persistence catch-all its sibling routes have (latent auth-catch-all fall-through) |
| L2 | Low | Concurrency drift guard | No AST/source gate pins every `NutritionPlan` writer behind `lock_plan_owner` (unlike the TrainingPlan create path) |
| L3 | Low | Privacy / caching | `/nutrition-plan/active` omitted from `private, no-store` (known test-harness tradeoff) |
| S1 | Low | Structure | `ai_coach.py` god-module regressed to ~1592 lines; documented `coach_tools/` extraction never happened |
| S2 | Low | Structure | `social.py` multi-domain blueprint grown to ~1222 lines (chat+feed+pump-check+moderation+friends) |
| S3 | Low | Structure | Repo-root `axisai-*/` dirs carry stray integration/remediation `.md` files |
| S4 | Info | Structure | Inert retired rollout flags still registered (`UIUX_TODAY_V2_ENABLED`, `UIUX_COACH_PAGE_V2_ENABLED`, `UIUX_NAV_V2_ENABLED`) |
| I1 | Info | CSP | `style-src-attr 'unsafe-inline'` residual (documented accepted tradeoff) |
| I2 | Info | Timezone | `completed_at` stamped from `utcnow()` vs Istanbul day identity (documented hazard) |

---

## M1 — [Medium] Per-user web JSON reads served without `private, no-store`

**Files (verified 2026-10-31):**
- `app/blueprints/social.py` — **no `after_request` handler and zero `Cache-Control`** anywhere in the module. Affected per-user reads include:
  - `/chat/<username>/messages` (private DM conversation) — **highest priority**
  - `/feed/data`, `/friends/list`, `/friends/select-list`, `/friends/search`
  - `/pump-check/<id>/comments`, `/feed/item/<id>/comments`
- `app/blueprints/coach.py:430` — `GET /coach/history` (full per-user coach conversation). The only `Cache-Control` in the module is `no-cache` at `:426`, which belongs to the preceding `/ask/stream` SSE route; `no-cache` still permits on-disk storage, unlike `no-store`.
- `app/blueprints/gamification.py` — no `Cache-Control` handler (`/leaderboard/reward-check`, `/quests`).
- `app/blueprints/profile.py:239` — `/pump-check-gallery/data` not covered by the module's existing handler (only `edit_profile` is).
- `app/blueprints/nutrition/meallog.py:497` — `/meal-log/history` is not in the nutrition `_PRIVATE_JSON_PATHS` set (`app/blueprints/nutrition/__init__.py:17`).

**Why it matters.** The documented rule (CLAUDE.md, Triage 2026-10-01 #2) is that
per-user authenticated JSON reads must carry `Cache-Control: private, no-store`,
enforced via per-blueprint `_PRIVATE_JSON_PATHS` `after_request` hooks. That pattern
exists **only** in `tracking.py`, `training.py`, and `nutrition/__init__.py`, and
there is no app-global default (the `app/__init__.py` after_request chain sets only
CSP + logging). So the endpoints above emit **no** `Cache-Control` header at all.

**Failure scenario.** A browser may heuristically cache a `Cache-Control`-less GET.
On a shared device, a private DM thread or coach history can land in the disk cache
or bfcache and be re-served via the back button after logout, or a stale wrong-user
view can be shown. An intermediary cache missing the `private` directive could also
store it.

**Fix.** Add the same `_PRIVATE_JSON_PATHS` + `after_request` pattern to
`social.py`, `coach.py`, and `gamification.py`; extend `profile.py`'s handler to
`/pump-check-gallery/data`; add `/meal-log/history` to the nutrition set. Order by
sensitivity: DM `/chat/.../messages` and `/coach/history` first. A small app-global
`after_request` default that applies `private, no-store` to any JSON response on an
authenticated blueprint (with an explicit opt-out list for the documented
`/nutrition-plan/active` test constraint) would close this class permanently instead
of per-route.

---

## L1 — [Low] `POST /api/v1/training/plans` lacks a persistence catch-all

**File:** `app/blueprints/mobile_training.py:55-119` vs the sibling routes at
`:205-235` (`create_replacement_proposal`) and `:238-276` (`confirm_replacement_proposal`).

`create_training_plan` catches a fixed exception list but has **no** trailing
`except SQLAlchemyError` / `except Exception`, whereas both sibling routes
explicitly roll back and return `TRAINING_PLAN_PERSISTENCE_UNAVAILABLE`.

**Currently benign — verified.** `generate_and_persist` → `store.commit_plan` /
`stage_candidate` / `claim` wrap DB faults into `GenerationPersistenceUnavailable`
(a subclass of the caught `PlanGenerationCommandError`), so a persistence fault
today correctly yields `TRAINING_PLAN_PERSISTENCE_UNAVAILABLE`, **not** the auth
catch-all.

**Latent risk.** A future raw `SQLAlchemyError`/`Exception` from an un-wrapped step
(`allowance.require_all_feature_ready`, `_required_session`, projection edge-cases)
would fall through to the `mobile_api` catch-all's `AUTH_TEMPORARILY_UNAVAILABLE`,
making a client discard a good session — the exact anti-pattern the typed-503 rule
exists to prevent.

**Fix.** Add a trailing `except Exception: db.session.rollback()` returning a typed
retryable 503 for parity with the sibling routes.

---

## L2 — [Low] No drift guard that every `NutritionPlan` writer takes `lock_plan_owner`

**File:** `app/services/nutrition_plan_store.py:75` — `replace_nutrition_plan`
issues a blanket `NutritionPlan.query.filter_by(user_id=user_id).delete()`.

This is **currently safe**: it is the single `NutritionPlan` writer and it takes
`lock_plan_owner` first (`:61-83`), so all callers serialize on the owner row.

**Gap.** The training-plan create path has an explicit source-level contract test
(`tests/test_plan_owner_lock_contract.py`) asserting every create writer stands
behind the owner lock; there is **no equivalent AST/source gate for NutritionPlan**.
A future second writer that inserts/deletes a `NutritionPlan` without
`lock_plan_owner` would silently reintroduce the lost-plan / duplicate-row race the
training path was hardened against (the browser-vs-native double-insert class).

**Fix.** Add an AST gate mirroring `plan_owner_lock`'s, pinning `NutritionPlan`
mutation to writers that take the owner lock.

---

## L3 — [Low] `/nutrition-plan/active` omitted from `private, no-store`

**File:** `app/blueprints/nutrition/__init__.py:17-27` — `_PRIVATE_JSON_PATHS`
covers only `/meal-log/today` and `/api/diary/today`.

The omission is **deliberate and documented** (inline comment + CLAUDE.md Triage
2026-10-01 #2): under Playwright `route.fulfill`, a `no-store` response never fires
`requestfinished`, so the PR6 N7 non-vacuity test (which injects a duplicate
`/nutrition-plan/active` read) would hang on `networkidle`.

**Impact.** Minor — same shared-device bfcache retention class as M1, but limited
to the active nutrition plan and authenticated `fetch()`.

**Fix.** Set the header on the real response path and adjust the N7 harness to not
intercept a duplicate read for this path, rather than leaving the production header
off to satisfy a test.

---

## Structural debt

### S1 — [Low] `ai_coach.py` god-module regressed

`app/services/ai_coach.py` is now **~1592 lines** — NEEDED_FIXES.md #6 flagged it at
1199 lines (2026-07) as the largest module needing a `coach_tools/` extraction; it
has grown ~33% and the refactor never happened. Large monkeypatch/blast-radius
surface for tests. Structural only — no behavior bug.

### S2 — [Low] `social.py` multi-domain blueprint grown

`app/blueprints/social.py` is now **~1222 lines** (NEEDED_FIXES.md #7 flagged 1033)
spanning chat + feed + pump-check + moderation + friends on one HTTP surface. This
compounds M1 (no single place owns its cache policy). Consider splitting by domain.

### S3 — [Low] Stray integration docs committed at repo root

Four tracked directories at repo root each contain a single markdown file:
`axisai-d1-migration-reconcile-01/INTEGRATION.md`,
`axisai-d1-r2-test-seam/REMEDIATION.md`,
`axisai-mon02-be03b-r2-integration/INTEGRATION.md`,
`axisai-priv-app-d1-be-01/IMPLEMENTATION.md`.
These look like leftover per-worktree integration notes. Move them under `docs/`
(or remove if superseded) to de-clutter the repo root.

### S4 — [Info] Inert retired rollout flags still registered

`app/feature_flags.py:384,431,471` — `UIUX_TODAY_V2_ENABLED`,
`UIUX_COACH_PAGE_V2_ENABLED`, `UIUX_NAV_V2_ENABLED` are deliberately-retained inert
flags whose toggles no longer restore legacy paths (git-revert is the rollback).
Not a bug, but they inflate `/health?deep=1` `flags`, the boot `[FLAGS]` line, and
cause operator confusion ("is this on?" has no behavioral meaning). Consider
retiring them like `UIUX_PLAN_V2_ENABLED` was, or documenting them as inert.

---

## Informational (documented accepted tradeoffs — listed for completeness, no action implied)

- **I1 — CSP `style-src-attr 'unsafe-inline'`** (`app/hooks.py:67`). Inline
  `style="..."` attributes (e.g. progress-bar widths) cannot carry a per-request
  nonce. Full `<style>` injection is already closed (`style-src-elem` nonce-gated);
  this is a narrow residual CSS-injection/UI-redress vector. Accepted tradeoff
  (CLAUDE.md Triage #8).
- **I2 — `completed_at` from `datetime.utcnow()`** (`app/services/workout_completion/service.py:101`)
  while the completion-day identity uses Istanbul `command.today`. Near Istanbul
  midnight a completion can stamp a different app-date than the day claimed, which
  TI-03 correctly reads as a cross-date completion. Documented TEST HAZARD; flagged
  only so it stays on the radar if completion timestamps ever become authoritative
  for day bucketing.

---

## Surfaces verified SOUND (no findings)

**Security:** `token_use=access` enforced, expiry leeway pinned to 0 both paths,
retired skew knob rejected at boot; `jwks_unavailable` → 503+keep-session, all other
token errors → reject+destroy; JWKS forced-refresh single-flight + cooldown; refresh
rotation reuse-detection + family revocation + credential-epoch fence. Account-deletion
resurrection write-then-check ordering race-free (uq_user_cognito_sub serializes write
vs purge; tombstone is a keyed one-way fingerprint). HMAC owner-bound tokens bind
`user_id` through the MAC with constant-time compare, per-class domain separation, TTL
+ future-skew; menu item proof fully re-validated. SSRF boundary strong (scheme
allowlist, per-hop DNS re-resolution, public-IP-only, connect-time IP pinning,
downgrade refused, killable credential-free subprocess). S3 bucket server-selected,
presigned URLs + deletes gated by `expected_user_id` with segment-equality key grammar.
Apple notifications treated as hints only; environment server-decided; entitlement
scoped to user + verified environments. No hardcoded secrets (IAM instance profile);
recipient emails masked in logs. CSRF two-layer fail-closed on all state-changing
methods.

**Concurrency:** Global lock order (operation → user → training_plan) consistent
across all create/mutate writers; `lock_plan_owner` uses `FOR NO KEY UPDATE`
(KEY-SHARE-compatible, no cycle with the FK-induced journal lock). Atomic sub-steps
flush, never commit; owning transactions own the single commit. Conditional-UPDATE +
named-constraint-as-arbiter correct everywhere (fail-closed on other IntegrityErrors).
AI allowance hold-vs-marker same-instant stamping + zero-attempt settlement + billing
UNKNOWN cap correct. Day-boundary funnels (water, active_day) deduped by durable
per-day marker + conditional UPDATE, not user-resettable counters. No
`date.today()`/naive `now()`/`created_at.date()`-as-analysis-day in production.
Latest-row selectors all carry `id DESC` tiebreak. Migrations: single head
`c6f8b0d2e4a1` (60 revisions); fresh-boot reentrancy holds (unguarded table-creating
migrations are all ancestors of the `aa11bb22cc33` stamp point; descendants carry
`has_table` guards); expand/contract enforced by test.

**API / frontend:** Every mobile read route wraps faults into a typed retryable 503;
none fall through to the auth catch-all (verified across `mobile_today`,
`mobile_progress`, `mobile_account_profile`, `mobile_account_language`,
`mobile_nutrition*`, `mobile_coach`, `mobile_pump_checks`, `mobile_exercise_history`,
`mobile_today_guidance`). Input validation uses closed key sets, bounds, NaN/Inf
rejection; owner from Bearer only. Frontend state machines
(`workout_state_client.js`, `nutrition.js`) rigorous: single-flight mutations,
monotonic generation/epoch superseded-drop with `AbortController`, revision-gated
checkpoints, UNKNOWN rendered as `—` not 0, no polling loops or stale-overwrite
races. Retired `/api/progress/insights` and `/api/progress/nutrition` genuinely gone.
`nutrition_targets` is the sole daily-macro authority.

---

## Suggested priority order

1. **M1** — add `private, no-store` to the web per-user JSON reads (DM + coach
   history first); ideally via an app-global authenticated-JSON default.
2. **L1** — trailing persistence catch-all on `POST /api/v1/training/plans`.
3. **L2** — AST gate for `NutritionPlan` owner-lock writers.
4. **S3 / S4** — repo-root cleanup and inert-flag retirement (low-risk housekeeping).
5. **S1 / S2** — schedule the `ai_coach.py` / `social.py` extractions when those
   areas are next touched.

_No fix was applied; this is a triage report only._
