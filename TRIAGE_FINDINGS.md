# Daily Triage — Needed Fixes

**Date:** 2026-09-29
**Scope:** Automated deep-dive triage across three axes — security vulnerabilities, correctness/concurrency bugs, and structure/tech-debt. Read-only audit; no code changed.

## Executive summary

The codebase is in **strong health**. The security audit found **no Critical or High vulnerabilities** — the app shows consistent defense-in-depth (positive-allowlist SSRF guard with DNS-rebind pinning, two-layer CSRF, per-request CSP nonce, parameterized SQL everywhere, canonical JWT validation, owner-scoped queries, fail-closed error handling). The correctness review found the concurrency-critical paths (locking order, idempotency/replay, completion races, keyset pagination) largely correct, with most "suspicious" patterns confirmed as deliberate, documented decisions. The structural review rated it "unusually disciplined" — invariants are enforced by AST/architecture tests rather than convention, locale parity is exact, and there are no orphaned modules.

**Nothing here is an emergency.** The single most actionable item is a **Medium** latency/contention bug (a global row-lock held across a blocking AI call). Everything else is Low/Informational hardening and cleanup.

---

## Priority 1 — Medium (should fix)

### F-1. `User` row `FOR UPDATE` lock held across a blocking AI call in check-in
- **File:** `app/blueprints/tracking.py` — `checkin()`, lock at line 263, blocking AI call at line 300, commit at line 335.
- **What:** On the idempotent submission path (`Idempotency-Key` present, key unseen), the route takes `SELECT … FROM user WHERE id=me FOR UPDATE`, then calls `generate_checkin_feedback(...)` → `_heavy_chat` → Bedrock/OpenAI (blocking network I/O, seconds; tens of seconds worst-case under retry/fallback), and only releases the lock at commit.
- **Why it matters:** The `user` row is the **repository-wide level-2 serialization point** (documented in `app/services/plan_owner_lock.py`). It is shared by the first-request-of-day streak update (`app/hooks.py`), every `TrainingPlan` create/replace, and AI-quota reservation. Holding it across an AI round-trip means any concurrent same-user operation blocks for the whole model-call window, while also parking a web thread + DB connection + AI-gate slot together on an 8-thread worker — a contention amplifier under load. This is the exact "no lock held while a round-trip is outstanding" anti-pattern the repo warns against (`Slot YALNIZCA ağ turunu sarsın`), applied to a DB row lock.
- **Trigger:** User A submits a keyed check-in while the provider is slow; concurrently A's client fires a plan generate/save, or it's A's first request of the day (streak hook). The second op blocks until the AI response returns.
- **Correctness note:** This is a latency/contention risk, **not** data corruption. The durable `WeeklyCheckIn(user_id, idempotency_key)` unique constraint is already the correctness backstop. The in-code comment shows the lock-across-model-call was intentional (to avoid two identical keyed submissions both calling the model), but only reasoned about duplicate submissions — the cross-operation contention on the shared lock appears unconsidered.
- **Suggested fix:** Drop the user-row lock before the model call (rely on the unique constraint + the existing `IntegrityError` reconciliation at lines 336-344), or generate the feedback *before* taking the lock. Mirror the pattern in `nutrition/diary.py` and `menu.py`, which `db.session.rollback()` to close the read transaction before any blocking provider call. The only thing lost is the duplicate-model-call optimization on a genuine race — acceptable given the constraint backstop.

---

## Priority 2 — Low (worth doing; cheap hardening & cleanup)

### F-2. Two PostgreSQL concurrency test files never run in CI
- **Area:** `.github/workflows/ci.yml` `mobile-pg-concurrency` job (explicit module list) vs `tests/test_workout_completion_pg.py`, `tests/test_workout_session_pg.py`.
- **What:** Both files are `pytest.mark.pg_concurrency` and self-skip in the default job; the PG job runs an explicit file list that omits both. Their race proofs (single-winner completion; at-most-one-ACTIVE-session `uq_workout_session_active_owner`; completion-vs-abandon terminal race) execute **nowhere**. This is precisely the "a new `_pg.py` not added to the CI list never runs" hazard CLAUDE.md warns about.
- **Mitigation (why Low, not higher):** Tracked as **P2-6 "OPEN — ACCEPTABLE FOR ACTIVATION"** in `docs/superpowers/specs/2026-09-07-sprint14-pr5-...md`, which argues the explicitly-selected `test_sprint14_workout_execution_reliability_pg.py` covers the same underlying invariants. Still, the specific two-contender proofs are dead.
- **Suggested fix:** Either add the two files to the CI job's module list, **or** (better, closes the class of bug) add a drift-guard test that asserts every `tests/*_pg.py` file appears in the CI `mobile-pg-concurrency` list — converting the convention into enforcement.

### F-3. UTC/naive-date used instead of Istanbul in AI-coach context reads
- **File:** `app/services/coach_context_queries.py` — line 138 (`datetime.utcnow() - timedelta(days=days)`, then compares `.date().isoformat()` against Istanbul-keyed `date_key` at line 161) and line 128 (`str(c["created_at"].date())` naive-UTC date for check-in dates).
- **What:** The module already defines Istanbul helpers `_app_today()`/`_day_key()` but `get_user_workout_history` doesn't use them. The lookback boundary is computed in UTC while the compared column is Istanbul-keyed (~3h/one-day edge skew); a check-in at 00:00–03:00 Istanbul is labelled a day early. CLAUDE.md forbids direct `utcnow()`/`created_at.date()` day math; `context_builder.py:78` does this correctly (comment "B13").
- **Severity:** Low — feeds only fuzzy AI-coach context ("last N days"), not a user-facing count or day-boundary decision; no corruption.
- **Suggested fix:** Use the existing `_app_today()` / `app_date_of()` helpers for the window boundary and the date rendering.

### F-4. `get_active_plan` has no deterministic tiebreak
- **File:** `app/services/today_facts.py:80-84` — `order_by(TrainingPlan.created_at.desc()).first()`, no `id` tiebreak.
- **What:** If two active plans ever shared a `created_at` microsecond, `.first()` is nondeterministic — and this selector is the single authority every reader/mutation trusts.
- **Severity:** Low / defense-in-depth only. `plan_owner_lock` + the browser-replace/native-refuse contract structurally prevent a user ever holding two plans, and `created_at` has microsecond precision.
- **Suggested fix:** One-line `.order_by(TrainingPlan.created_at.desc(), TrainingPlan.id.desc())` for robustness.

### F-5. Registered-but-inert feature flags mislead operators
- **File:** `app/feature_flags.py` `ROLLOUT_FLAGS`.
- **What:** `UIUX_TODAY_V2_ENABLED` and `UIUX_NAV_V2_ENABLED` are historical no-ops — flipping 0/1 selects nothing (their templates/CSS are deleted; routes render unconditionally). They still appear in the boot `[FLAGS]` line and `/health?deep=1`, implying a selectable capability that no longer exists. (`UIUX_PLAN_V2_ENABLED` was correctly removed from the registry already.)
- **Severity:** Low — live drift between flag *inventory* and flag *effect*; honest state only visible by reading the docstring.
- **Suggested fix:** Retire the two no-op keys from the registry (separate cleanup PR), or mark them explicitly inert in the health/boot output.

### F-6. Rollout flags past their `review_by` date
- **File:** `app/feature_flags.py`.
- **What:** `WEEKLY_PROGRAM_UI_ENABLED` has `review_by="2026-09-01"` (overdue); `UIUX_*`, `MOBILE_AUTH_ENABLED`, `AXISAI_NATIVE_AUTH_ENABLED` are `review_by="2026-10-01"` (imminent). The flag lifecycle model (owner/prereqs/abort-signals/rollback per flag) is excellent, but nothing enforces the review date — an overdue flag just sits `shipped_dark`.
- **Suggested fix:** Add a one-line test asserting no rollout flag is past its `review_by`, and either retire or re-date the overdue keys.

### F-7. Documentation drift — stale Alembic head in CLAUDE.md
- **File:** CLAUDE.md.
- **What:** Multiple entries assert "Alembic head hâlâ `f5a6b7c8d9e0`" (and the meal-photo entry cites `e4f5a6b7c8d9`). Actual current head is **`c8d9e0f1a2b3`** (`checkin_submission_key`, 43 revisions). The `test_migration_graph.py` single-head guard is the real authority and is green — the prose is just wrong now.
- **Suggested fix:** Refresh the head references, or stop pinning a specific head in prose and defer to the migration-graph guard.

---

## Priority 3 — Informational (no action required; noted for awareness)

### F-8. God modules concentrating change/merge-conflict risk (Medium maintenance, Low urgency)
- **Backend:** `app/models.py` (1610 LOC — documented single-file choice), `app/services/ai_coach.py` (1451), `app/services/coach_plan_tools/grounding.py` (1299), `app/blueprints/social.py` (1254), `app/services/mobile_auth.py` (1066).
- **Frontend:** `static/nutrition.js` (**2111 LOC** — largest single liability; mixes water-state, diary, targets, quick-add), `static/plan_workout.js` (1034), `static/coach_widget.js` (998).
- **Note:** Newer services are cleanly decomposed (pure/dirty split), so the debt is concentrated in the oldest/most-central files, not pervasive. Worth scheduling `nutrition.js` and `ai_coach.py` decomposition as their own PRs when convenient.

### F-9. CSP hardening residue (all documented accepted tradeoffs)
- `app/hooks.py:67` — `style-src-attr 'unsafe-inline'` remains open (dynamic progress-bar widths). Narrow CSS-injection surface only; no script execution (`script-src-attr 'none'`, nonce-gated `script-src`). No stored-XSS sink found.
- `app/hooks.py:69` — `img-src data:` (base64 avatars when S3 disabled); mitigated by `validate_profile_picture` restricting stored values to `data:image/(png|jpe?g|gif|webp)`.
- `app/hooks.py:54` — `https://*.googletagmanager.com` wildcard in `script-src` (per Google's official GA CSP guidance).
- **Note:** All three are self-documented in-code as accepted tradeoffs. No change recommended unless the threat model tightens.

---

## Verified sound — no action (highlights)

These were inspected and confirmed correct, several after looking specifically for the bug:

- **Auth/JWT:** signature + issuer + audience + `token_use=access` validated; leeway 0 both paths; retired skew knob rejected at boot; unknown-`kid` JWKS refresh is single-flight + cooldown-gated; transient (`jwks_unavailable`) vs definitive split preserves sessions on infra outage; login uses *verified* claims, session-fixation protection, fail-closed brute-force throttle.
- **Ownership/IDOR:** all ID-loaded records enforce `user_id` scoping (directly, or via user-scoped parent for `CustomMealItem`); social routes gate through visibility checks; presigned-URL callers pass `expected_user_id`; S3 delete primitives grammar-restricted to app-minted keys + owner segment match.
- **Injection:** `fitx_mcp` raw SQL fully parameterized; no f-string SQL; SSRF guard has positive `is_global` check, IPv4-mapped-IPv6 unwrap, port allowlist, per-hop revalidation, and `getaddrinfo` IP-pinning (DNS-rebind TOCTOU closed).
- **Concurrency:** fixed lock order (session row first), conditional revision-gated `advance_checkpoint`, `uq_pump_check_day`/`uq_workout_session_active_owner` as DB arbiters, race-loser reconciliation, byte-fingerprint undo precondition, monotonic mutation version.
- **AI B-rule after mutation:** Bedrock→OpenAI fallback only fires when no tool ran, correctly preventing re-running a committed mutation on the fallback provider.
- **Nutrition double-count / NULL-vs-0:** diary `CustomMeal` total deliberately separate from `MealLog`; `nutrition_targets` handles `None`-in/`None`-out uniformly; the 4-way macro-target duplication is consolidated with an AST guard.
- **Challenges dedup:** day-meaning funnels (`water_logged`, `active_day`) externally gated by `quest_fired` conditional UPDATE / locked streak recheck; per-event funnels correctly fire per event.
- **feed keyset pagination:** `(created_at, source_rank, id)` composite cursor lexicographically correct in all rank cases; `has_more` correct.
- **Capacity:** DB pool sized to thread count, concurrency gates reserve-counted and enforced at boot; heavy paths gated; `model_concurrency_slot` bounded with deadline acquire.
- **CI/deploy structure:** `ci.yml` gates `deploy.yml` via `workflow_run`; schema-drift `flask db check` blocking; single Alembic head enforced; `/health?deep=1` deploy gate with auto-rollback.

---

## Recommended order of work

1. **F-1** — refactor the check-in idempotent path to not hold the user-row lock across the AI call (only Medium item; real production contention risk).
2. **F-2** — add the `tests/*_pg.py` → CI-list drift guard (closes F-2 and the un-guarded-convention class in one move).
3. **F-3, F-4** — trivial correctness/robustness one-liners (Istanbul date helpers; `id` tiebreak).
4. **F-5, F-6, F-7** — flag/doc hygiene (retire no-op flags, add `review_by` guard, refresh CLAUDE.md head references).
5. **F-8** — schedule god-module decomposition (`nutrition.js`, `ai_coach.py`) as standalone PRs when convenient.
