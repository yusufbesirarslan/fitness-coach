# FitX (fitness-coach) — Triage & Needed Fixes

_Generated 2026-09-30 by an automated deep-dive triage (3 parallel review agents: security, core business logic, API/data/structure). Read-only investigation — no code was changed. Every finding below is backed by code that was actually read; the two highest-impact items were independently re-verified._

## Executive summary

The codebase is **mature and unusually well-hardened**. Security posture is strong (no Critical/High/Medium security vulnerabilities found), the pure/dirty service layering is rigorous and enforced by AST/import gates, single-authority discipline has been actively maintained, and concurrency-critical paths are covered by CI-selected PostgreSQL race suites.

The findings are **consistency gaps and a gamification correctness bug**, not structural rot. Two items have real user-facing consequences and are the recommended fixes:

| # | Finding | Severity | User impact |
|---|---------|----------|-------------|
| 1 | Meal-logging drops "log N meals/week" challenge progress after the first meal each day | **Medium** | Weekly meal challenge is effectively uncompletable; users lose earned XP/badges |
| 2 | Pump-check read routes report storage faults as `AUTH_TEMPORARILY_UNAVAILABLE` | **Medium** | Native clients log the user out on a transient DB blip |
| 3 | `/chat` bypasses the AI weekly quota / failure-cooldown that `/ask` enforces | Low | Freemium boundary inconsistency; `UserSession` row inflation |
| 4 | `/chat` has no try/except or rollback around the AI call | Low | Latent generic 500 vs. the hardened `/ask` path |
| 5 | Water quest claim and its award commit in two separate transactions | Low | Rare commit-failure window loses that day's water XP |
| 6 | `_apply_move` reports `changed=True` for a no-op day swap | Low | Version churn / needless `plan_data` rewrite |
| 7 | Raw exception objects logged on a few provider-failure paths | Low | Minor internal info in server logs (not to clients) |
| 8 | CSP keeps `'unsafe-inline'` for inline `style` attributes | Low | Defense-in-depth gap (no live sink) |

---

## 1. Meal challenge progress silently dropped after the first meal each day — Medium

**Files**
- `app/services/gamification.py:318-362` (`_claim_quest`, `complete_quest_for_user`) — **verified**
- Callers: `app/blueprints/nutrition/diary.py:249,608`, `app/blueprints/nutrition/meallog.py:200,272,325`

**Mechanism**
`_claim_quest` calls `record_event(user_id, "meal_logged")` **first** — which stages a `weekly_meals` challenge `+1` via `begin_nested` savepoints and deliberately does **not** commit. It then checks the per-day `UserQuestProgress`; if today's daily quest is already claimed it `return None`. `complete_quest_for_user` sees `None` and returns **without `db.session.commit()`** (see `gamification.py:354-356`). The meal row itself was already committed earlier (`meal_idempotency.commit_once` / explicit commit), so the staged challenge increment sits in a fresh uncommitted transaction and is rolled back at request teardown.

**Failure scenario**
User logs 4 meals today:
- Meal #1: daily quest unclaimed → `complete_quest_for_user` commits → `weekly_meals` progress `+1` persists.
- Meals #2–#4: daily quest already claimed → `_claim_quest` returns `None` before the commit → each `+1` is staged and lost.

Net: the "Bu hafta 10 öğün kaydet" challenge (`target_value=10`, metric `meal_logged`) increments **at most once per day**, so it maxes at 7/week and is effectively uncompletable. Users lose challenge XP/badges they legitimately earned.

**Why other funnels are safe**
- `workout_logged` → `workout_completion.complete_workout` commits unconditionally, and is 1/day anyway.
- `water_logged` is gated to 1/day by `WaterLog.quest_fired` **and** its challenge counts days, so once/day is correct.
- Only the meal path counts per-event while being gated by a per-day quest claim.

**Fix direction**
When `_claim_quest` finds the daily quest already claimed but `record_event` still staged challenge progress, that staged work must be committed. Options: have the meal-logging callers commit the session after `complete_quest_for_user` returns `None` (since `record_event` no-commit is by contract), or split the challenge funnel so `record_event`'s staged increments are committed independently of the daily-quest short-circuit. Add a regression test that logs ≥2 meals in one day and asserts `weekly_meals` progress increments per meal.

_Pre-existing (history obscured by a file rename in commit e8b34d3)._

---

## 2. Pump-check read routes report storage faults as an auth failure — Medium

**Files**
- `app/blueprints/mobile_pump_checks.py:79-104` (`list_pump_checks`), `:107-119` (`get_pump_check`) — **verified**
- `app/services/mobile_pump_checks/history.py`, `service.py:get_owned` (read paths raise only `InvalidPageSize`/`InvalidCursor`/`PumpCheckNotFound`)
- Catch-all: `app/blueprints/mobile_api.py:184-196` (`normalize_unhandled_mobile_failure` → `AUTH_TEMPORARILY_UNAVAILABLE` 503)

**Mechanism**
The two GET routes catch only their expected domain exceptions. A DB/storage fault during a history list or single read is uncaught in-route, so it falls through to the blueprint-wide catch-all which answers `AUTH_TEMPORARILY_UNAVAILABLE` 503.

**Impact**
This is exactly the anti-pattern the codebase explicitly guards against elsewhere (`mobile_progress.py:41-60`, `mobile_today.py:34-51`, `mobile_nutrition.py:43-59`, `mobile_training.py` `TrainingReadUnavailable`): a well-behaved native client that reads `AUTH_TEMPORARILY_UNAVAILABLE` discards a perfectly good session and forces re-login. The pump-check read routes are the one native surface missing the domain-shaped 503 wrapper their siblings all have. (No stack-trace/DB-identity leak — the catch-all logs the type name only.)

**Fix direction**
Wrap the two GET service calls and map unexpected exceptions to a pump-check-shaped retryable 503 (e.g. `PUMP_CHECK_TEMPORARILY_UNAVAILABLE`, `retryable=True`), mirroring the sibling routes. Add a test that forces a storage fault on these GET routes (currently untested).

---

## 3. `/chat` is not covered by the AI weekly quota / failure-cooldown that `/ask` enforces — Low

**File:** `app/blueprints/coach.py:70-175`

`/chat` calls `generate_coach_reply` (the same Bedrock Sonnet path as `/ask`) with only `AI_RATELIMIT` + `BEDROCK_RATELIMIT` limiters and the `MAX_QUESTION_CHARS` gate. It has **no** `reserve_ai_quota`, **no** `_ai_cooldown_response()`, and commits a new `UserSession` row on every call.

**Impact:** A non-premium user can drive unlimited (within rate limit) heavy Sonnet calls through `/chat`, bypassing the freemium `FREE_WEEKLY_AI_CHATS` intent that `/ask` and `/ask/stream` enforce; also inflates the `UserSession` table (the `target_calories` authority). Exposure is limited by the legacy calculator path requiring a full BMR payload — hence Low, but it is an inconsistent freemium boundary.

**Fix direction:** Bring `/chat` under the same `reserve_ai_quota` + cooldown treatment as `/ask`, or explicitly document why `/chat` is exempt.

---

## 4. `/chat` has no try/except around the AI call and does not roll back — Low

**File:** `app/blueprints/coach.py:127-155`

Unlike `/ask` (which wraps generation, rolls back, records failure, refunds quota), `/chat` calls `generate_coach_reply` bare. In practice `generate_coach_reply` returns a friendly fallback rather than raising (COACH_FALLBACKS), so this is latent — but any unexpected raise yields a generic Flask 500 with no `db.session.rollback()`. No commit precedes it, so no data corruption; just an inconsistency with the hardened `/ask` path. Fix alongside #3.

---

## 5. Water quest claim and its award live in two separate transactions — Low

**File:** `app/blueprints/training.py:955-984` (`set_water` / `_claim_water_funnel_for_today`)

`_claim_water_funnel_for_today` commits `quest_fired=True` in its own transaction, then `complete_quest_for_user("water_logged")` commits the DailyQuest claim + XP + challenge increment separately. If the second commit fails/rolls back, `quest_fired` stays `True`, permanently suppressing retry → that day's water challenge/quest XP is lost. Narrow (commit-failure) window.

**Fix direction:** Set `quest_fired` and perform the award in a single transaction, or make the flag advisory (re-check actual award state) so a failed award can be retried.

---

## 6. `_apply_move` reports `changed=True` for a no-op day swap — Low

**File:** `app/services/plan_mutation/document.py:605-625` (`_apply_move`)

Every non-same-day move returns `True`, so swapping two days whose content is identical bumps `mutation_version` and rewrites `plan_data` bytes despite no effective change — contradicting the module's "deterministic no-op" principle honored by the other `_apply_*` helpers. Churn only; the resulting plan is correct.

**Fix direction:** Compare source/target content and return `changed=False` when the move produces byte-identical `plan_data`.

---

## 7. Raw exception objects logged on a few provider-failure paths — Low

**Files:** `app/services/ai_coach.py:686`, `app/services/fatsecret.py:304`, and several full-URL logs in `app/blueprints/menu.py`

Most of the codebase logs `type(e).__name__` only (PII/secret-free) and masks emails. A handful of paths log the full exception object, which for an `S3Error`/provider error can carry internal detail (S3 object keys, bucket, internal URLs) into logs. Server-logs-only exposure; requires log access to read. Inconsistent with the repo's otherwise-strict PII-free logging convention.

**Fix direction:** Normalize these to `type(e).__name__` (or an explicitly sanitized message) to match convention.

---

## 8. CSP permits inline `style` attributes — Low (accepted tradeoff)

**File:** `app/hooks.py:62-67` (`style-src-attr 'unsafe-inline'`)

The CSP keeps `'unsafe-inline'` for inline `style="..."` attributes (used for dynamic progress-bar widths) while correctly forbidding inline `<script>`/`<style>` blocks (nonce-gated) and `on*` handlers (`script-src-attr 'none'`). If a stored/reflected HTML-injection primitive were ever introduced, an attacker could inject `style` attributes (CSS-based UI-redress / limited selector-based exfil) — no script execution. There is currently no injection sink feeding this (Jinja autoescape + `|tojson`). Documented, deliberate tradeoff.

**Fix direction (optional):** Move dynamic widths to nonce'd `<style>` or CSS custom properties and drop `style-src-attr 'unsafe-inline'`.

---

## What was audited and found solid (no action)

**Security (strong):**
- JWT validation (`cognito_jwt.py`): RS256 enforced, issuer + audience/client_id + `token_use` checked; forced JWKS refresh is single-flight + cooldown-gated; `jwks_unavailable` (503, session preserved) distinguished from definitive rejects. `auth_contract.py` pins `token_use="access"`, leeway hard-pinned to 0, AST allow-list of call sites.
- CSRF: Origin/Referer + per-session synchronizer token (constant-time compare); mobile API correctly Bearer-only.
- SSRF (`menu_fetch.py`, `wearables.py`): positive `is_global` IP check, IPv4-mapped-IPv6 unwrap, port allow-list, per-hop re-validation, DNS-rebinding TOCTOU closed via pinned `getaddrinfo`, size caps.
- Path traversal / S3 (`s3_helper.py`): bucket server-chosen; strict key grammar + owner-equality; `expected_user_id` IDOR defense-in-depth (incl. LLM-supplied keys).
- Injection: no `eval`/`exec`/`os.system`/`subprocess`/`pickle`; all raw SQL parameterized; no SSTI.
- File upload (`validators.py`): data-URL decode + Pillow `verify()` + format allow-list + 40MP decompression-bomb cap + size caps + global `MAX_CONTENT_LENGTH`.
- IDOR / owner-scoping: reads/writes consistently `filter_by(user_id=current_user.id)`; `CustomMealItem` queries safe via owner-scoped parent load; social gated by friend/visibility checks.
- Secrets/cookies: `SECRET_KEY` required in prod; HttpOnly/SameSite=Lax/Secure cookies; IAM instance profiles (no hardcoded AWS keys); emails masked in logs.

**Core logic (correct, high confidence):**
- `nutrition_targets.py` macro split / remaining budget + all four consumers (F2/F3a consistent, null-vs-zero handled).
- JS↔Python rounding parity (`plan_facts._display_kcal` reproduces `Math.round`); no meal double-counting (ledger-only reads).
- `plan_mutation` / `plan_replacement` / `plan_owner_lock` / `plan_confirmation`: lock ordering (op-row → user → plan-rows), `populate_existing().with_for_update()` staleness fix, `expected_plan` freshness all correct.
- `workout_session` checkpoint (single conditional UPDATE keyed on base revision), replay/idempotency, single-active-session partial-unique-index, completion double-write prevention (`uq_pump_check_day`), `expected_checkpoint_revision` verified under row lock.
- `timeutil.py`: no forbidden `date.today()`/`utcnow().strftime`/`created_at.date()` bypasses; Istanbul-day windows + ISO-week boundaries consistent.
- `award_xp`/`update_streak`: column-level `FOR UPDATE` re-read avoids identity-map lost-update; `record_event` per-challenge savepoint isolation avoids session poisoning.

**API / data / structure (healthy):**
- Migration re-runnability: every table-creating migration after the fresh-schema stamp is `has_table`/column-inspection guarded.
- Cascade-delete: all 41 user-FK models covered (in `cli.py _user_child_models` or via parent/bidirectional handling).
- AI pipeline: Bedrock→OpenAI B-rule guard consistent; cache-key isolation hashes full context (no cross-user collision); error-fallback correctly skips persistence (B16); no transaction held across provider network I/O.
- Boot fail-fast on migration failure (health gate → rollback); expand/contract migration discipline honored.

**Structure notes:**
- `ai_coach.py` (~70KB) and `social.py` (~55KB) are god-modules — the hardest surfaces to reason about; `ai_coach` is partly mitigated by extraction with back-compat re-exports. Candidate for further decomposition, not urgent.
- The blueprint-wide `@bp.errorhandler(Exception)` on `mobile_api` is a double-edged net: it prevents leaks but converts un-wrapped domain faults into an auth outcome (root cause of #2). Consider a non-auth default (`TEMPORARILY_UNAVAILABLE`) so a missed wrapper degrades safely.
- Test gaps: mobile pump-check read-failure path (#2) untested; Windows-only `node -e` presentation tests are CI-authoritative only.

---

## Recommended order of work

1. **#1 (meal challenge)** — real correctness bug affecting earned rewards; add a per-day multi-meal regression test.
2. **#2 (pump-check 503)** — one-surface consistency fix with clear user impact; add a storage-fault test.
3. **#3/#4 (`/chat` hardening)** — align the freemium/rollback boundary with `/ask`.
4. **#5, #6, #7** — low-risk polish.
5. **#8 + the `mobile_api` catch-all default** — defense-in-depth, schedule as tech-debt.
