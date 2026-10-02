# Needed Fixes — Triage 2026-10-02

Automated triage: three parallel deep-dive agents (security, correctness, architecture)
swept the Flask backend (`app/`), frontend (`static/`), tests, migrations, and CI.

**Headline:** The codebase is unusually well-hardened. Security found **no
critical or high-severity exploitable vulnerabilities**; correctness found **no
missing-commit, lost-update, or double-apply bugs** on the hot concurrency paths.
The one HIGH-severity item is a **CI coverage gap** (verified below), not a
production defect. Everything else is low/medium hygiene and documented tradeoffs.

Severity legend: 🔴 High · 🟠 Medium · 🟡 Low · ℹ️ Info/accepted.

---

## 1. 🔴 HIGH — Two PostgreSQL concurrency tests never run in CI  *(verified)*

- **Where:** `.github/workflows/ci.yml` `mobile-pg-concurrency` job (explicit path
  list, lines 248–270) vs `tests/test_workout_completion_pg.py` and
  `tests/test_workout_session_pg.py`.
- **What:** 24 `tests/test_*_pg.py` modules exist and all carry
  `pytestmark = pytest.mark.pg_concurrency`. The CI job runs
  `python -m pytest -m pg_concurrency -q` against an **explicit list of only 22
  paths**. Because collection is limited to the listed paths, the marker filter
  can never pull in the two unlisted files — they run on **no** platform.
  - Confirmed: on-disk set = 24 files; CI list = 22; the diff is exactly
    `test_workout_completion_pg.py` and `test_workout_session_pg.py`; both carry
    the `pg_concurrency` marker.
- **Why it matters:** These cover the canonical `workout_completion` transaction
  (atomic PumpCheck + marker + XP + quest, the `uq_pump_check_day` race) and the
  `workout_session` lifecycle (`uq_workout_session_active_owner`, terminal-transition
  locking) — precisely the invariants SQLite cannot prove and that CLAUDE.md
  repeatedly flags ("a new PG module not added there never runs in CI"). A
  regression in completion/session locking would pass CI green.
- **Fix:**
  1. Add both paths to the CI job list (immediate).
  2. Durable fix: replace the hand-maintained list with glob collection
     (`tests/test_*_pg.py`) so new PG modules are picked up automatically and this
     drift class is eliminated. Consider a drift guard that fails if an on-disk
     `*_pg.py` marker file is absent from the CI invocation.

---

## 2. 🟠 MEDIUM — God modules carrying heavy logic

- **Where:** `app/models.py` (1,627 lines, 52 models), `app/services/ai_coach.py`
  (1,450 lines; ~40 lines of re-export shims at the top), `coach_plan_tools/grounding.py`
  (1,299), `app/blueprints/social.py` (1,254, 32 routes), `app/services/mobile_auth.py`
  (1,096), `app/blueprints/training.py` (970), `app/blueprints/tracking.py` (901).
- **Why it matters:** Highest-churn, hardest-to-reason-about surfaces; the
  `ai_coach.py` shim layer makes its public surface wider than its real
  responsibility and ripples through tests on any import-path change.
- **Fix (decomposition backlog, non-urgent):** split `models.py` by domain behind
  a package `__init__` re-export (no caller changes); finish the Sprint-4 WS3
  `ai_coach.py` split by extracting the provider loop; split `social.py` into
  feed / moderation / comments blueprints; retire re-export shims as call sites
  migrate.

## 3. 🟠 MEDIUM — Frontend god file, no build tooling

- **Where:** `static/nutrition.js` (2,887 lines, 265 functions); also
  `plan_workout.js` (1,034), `coach_widget.js` (998), `progress_presentation.js`
  (859), `progress.js` (745). 21 raw files served directly, no bundler.
- **Why it matters:** Every Nutrition change serializes on one file; shared client
  primitives (sequence tickets, single-flight, state machines) are re-implemented
  per feature block; high regression risk on the most-edited frontend surface.
- **Fix:** Extract shared client primitives; carve `nutrition.js` into per-concern
  files (native `<script type=module>` if CSP/SRI allow, else same-page split).

## 4. 🟠 MEDIUM — CSP `script-src` wildcard for Google Tag Manager

- **Where:** `app/hooks.py:54` — `script-src ... https://*.googletagmanager.com`.
- **What:** Any script from any `*.googletagmanager.com` subdomain executes with
  full trust, weakening the otherwise strict nonce-based XSS defense.
- **Status:** Documented accepted tradeoff (L7); likelihood low (Google-controlled
  host). The wildcard is the concern, not a direct bug.
- **Fix (if tightening):** pin the exact gtag host(s), or self-host analytics.

---

## 5. 🟡 LOW-MEDIUM — Leaderboard sync markers dropped on nested-savepoint rollback

- **Where:** `app/services/gamification.py:149-153` (`_drop_lb_dirty`), with
  `_mark_lb_dirty` (100-117) / `award_xp` (156-191); triggered from
  `challenges.py` `record_event` / `_get_or_create_global_row` / `_try_complete`.
- **What:** `_drop_lb_dirty` is registered on both `after_rollback` **and**
  `after_soft_rollback`. In the pinned SQLAlchemy 2.0.51, a `begin_nested()`
  SAVEPOINT rollback fires both events, so it pops the entire
  `session.info["lb_dirty"]` dict — not just on a true top-level rollback.
  `record_event` uses per-challenge savepoints and `_get_or_create_global_row`
  rolls one back on a UNIQUE race as **normal control flow**, so a sibling
  savepoint rollback can silently drop pending Redis leaderboard markers for XP
  that commits durably in the same request.
- **Impact:** Temporarily stale/understated leaderboard rank until the next
  `lb_rebuild` (boot or weekly rollover). Mostly masked because common helpers run
  a plain `award_xp` after all `record_event` savepoints, re-marking the user.
  Redis here is a derived cache.
- **Fix:** Don't let a SAVEPOINT rollback drop markers — scope capture/replay to
  the outer transaction (distinguish a real DBAPI rollback from a savepoint
  rollback, or snapshot+restore `lb_dirty` around `begin_nested` sites), or
  re-derive `lb_dirty` from committed `User` rows at `after_commit`.

## 6. 🟡 LOW — Inconsistent "newest UserSession" selector (missing `id` tiebreak)

- **Where (order by `created_at.desc()` only):** `nutrition_day_view.py:193`,
  `plan_facts.py:205`, `nutrition/meallog.py:387` & `:541`,
  `tracking.py:289/404/573/594`, `menu.py:304`, `coach.py:129`.
- **Canonical (correct) form:** `mobile_nutrition/queries.py:85` and
  `nutrition_native/plan.py:357` use `(created_at.desc(), id.desc())`; CLAUDE.md
  LP-03 defines the canonical session as "newest `created_at`, **then** `id`".
- **What:** Multiple `UserSession` rows per user genuinely exist (`/chat` inserts
  one per call, `coach.py:159`). When two rows share an identical `created_at`,
  the web target/day-view selectors pick a non-deterministic row that can differ
  from the canonical session native/onboarding/generation use → `/nutrition-day-view`
  target can disagree with `/meal-log/today`. Rare under `utcnow()` microsecond
  resolution; reachable via coarse timestamps, backfills, or bulk inserts.
- **Fix:** Append `, UserSession.id.desc()` to every "newest session" selector so
  all target authorities agree on one row.

## 7. 🟡 LOW — Orphaned S3 object on concurrent same-Idempotency-Key meal-log

- **Where:** `app/blueprints/nutrition/meallog.py:116-146` (`log_meal`); same shape
  possible in diary/pump photo-upload paths.
- **What:** Flow is `find_existing(key)` → `upload_image` → build `MealLog` →
  `commit_once(entry, key)`. Two concurrent requests with the same Idempotency-Key
  both pass `find_existing` and both upload a distinct S3 object; only one wins
  `commit_once`, the loser rolls back — and its already-uploaded object is
  referenced by no row and gets no `MealPhotoCleanup` intent, leaking permanently.
- **Fix:** Upload only after winning `commit_once`; or delete the loser's key in
  the `IntegrityError` branch; or record a cleanup intent for it.
- **Note:** Without an Idempotency-Key, `/meal-log` has no dedup (a double-submit
  creates two rows) — appears to be an accepted design choice, not a bug.

## 8. 🟡 LOW — Parallel Today composition in two places

- **Where:** `app/services/today_facts.py` (web) and `app/services/mobile_today.py`
  (native) both assemble Today from `get_active_plan` → `resolve_workout_state` →
  `serialize_today_plan`, differing in one load-bearing detail (native opens
  `coherent_read_snapshot()`; web must not, or it detaches `current_user`).
- **Fix:** Extract a shared composition helper parameterized by read strategy
  (snapshot vs plain) so the sequence lives once.

## 9. 🟡 LOW — Minor security hygiene (all documented accepted tradeoffs)

- **Login account-enumeration** (`app/blueprints/auth.py` ~437): `UserNotConfirmedException`
  returns `403 {needs_verification, username}`, distinguishing unconfirmed accounts.
  Requires the correct password; throttled 15/15min (M7). Neutralize if desired.
- **CSP `style-src-attr 'unsafe-inline'`** (`app/hooks.py:67`): narrow CSS-injection
  vector for dynamic progress-bar widths (triage #8). Move widths to nonce'd
  `<style>` / CSS custom properties to close.
- **CSRF Referer-only fallback** (`app/hooks.py:181-191`): accepted when `Origin`
  absent; Layer-2 synchronizer token still runs, so defense-in-depth holds. Info.
- **Presigned URL lifetime 3600s** (`s3_helper.py:303`): a leaked image URL is
  replayable up to an hour. Consider a shorter default (~300s) for sensitive media.

## 10. 🟡 LOW — Test/registry hygiene

- **Browser tests gate deploy** (`ci.yml` `tests` job): 50 Playwright modules run
  in the merge/deploy-gating job — the dominant flakiness + wall-clock source in a
  critical gate. Split into a separate parallel/retried job; keep the fast
  unit/contract suite as the blocking gate.
- **Lapsed feature-flag review** (`app/feature_flags.py`): `UIUX_TODAY_V2_ENABLED`
  selects nothing (its branch is gone) and its `review_by="2026-10-01"` has lapsed.
  Formally retire it or extend the date; clean stale `UIUX_PLAN_V2_ENABLED` /
  `UIUX_NAV_V2_ENABLED` comment residue in `training.py`/`hooks.py`.
- **Tech debt in prose, not tracked:** open items live across `NEEDED_FIXES.md`,
  `NEEDED_FIXES_2026-08-02.md`, `NEEDED_FIXES_2026-08-14.md`, and the 148KB
  CLAUDE.md (only 1 TODO/FIXME in all of `app/`). Migrate open items to the issue
  tracker / one living backlog.

---

## Suggested order of work

1. **#1 (HIGH)** — add the two PG paths to CI *now*; then switch to glob collection.
   One-line unblock, closes a silent gating hole on the completion/session locking
   invariants.
2. **#5, #6, #7 (LOW correctness)** — small, localized, verifiable fixes with real
   (if rare) user-visible effects.
3. **#2, #3, #10 (MEDIUM/hygiene)** — decomposition + CI split as planned backlog.
4. **#4, #9 (security tradeoffs)** — revisit only if tightening the posture;
   each is already a documented, bounded decision.

## What was checked and found correct (do not re-triage)

Security: full auth surface (web + mobile Bearer), IDOR/owner-scoping across all
blueprints, SSRF in `menu_fetch.py` (allowlist + redirect re-validation +
DNS-rebinding pin), injection (no raw SQL / eval / pickle / template injection),
HMAC/token construction, Cognito JWT (RS256, issuer/aud/`token_use`/expiry),
S3 key grammar + owner checks, CSRF two-layer, session fixation, DoS guards.

Correctness: `plan_mutation/service.py`, `workout_completion/service.py`,
`hydration.py`, `premium.py` quota reserve/refund, `challenges._try_complete`,
`account_deletion.py`, `mobile_diary_mutation/service.py`,
`plan_confirmation` + `coach_plan_tools/executor.py`, workout checkpoint
revision/idempotency, timeutil Istanbul-day boundaries.

Architecture: pure/impure separation (purity claims hold), clean layering
(no services→blueprints imports), single Alembic head (`d0e1f2a3b4c5`),
40 architecture/drift guards, `nutrition_targets.py` consolidation, single
time source.
