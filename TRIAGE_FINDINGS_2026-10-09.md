# Fitness Coach (FitX) — Triage & Security Review

**Date:** 2026-10-09
**HEAD:** `f7203f7` (feat(progress): extract canonical weekly check-in service, #419)
**Method:** Three parallel deep-dive agents — (1) security vulnerabilities, (2) concurrency / transactions / data-integrity, (3) correctness / API contracts / frontend — each doing read-only triage over the repo. The two actionable findings (#1, #2) were manually re-verified against source before publishing.

---

## Executive summary

**The codebase remains in very good shape.** As with the 2026-10-01 pass, all three reviewers independently reported the same signal: this is an unusually well-hardened application. The common vulnerability and correctness classes are each closed deliberately, usually with an in-code comment citing the prior triage that fixed them.

- **No critical or high-severity issues were found** in any of the three reviews.
- **No confirmed security vulnerability at any severity.** Authn/authz & owner-scoping, JWT validation, SSRF defenses (menu fetch), S3 key handling, HMAC token design, CSRF/CSP, mass-assignment, and the account-deletion resurrection logic are all implemented to a high standard.
- The real findings are **one MEDIUM** (a DB row lock held across an S3 network call on the meal-photo cleanup path) and a handful of LOW / latent / informational items.

**Recommended order of fixes:** #1 (lock across S3 DeleteObject) first — it's the only one with a runtime cost (a pooled connection parked idle-in-transaction) and the fix is low-risk because the operation being serialized is already idempotent. #2 (day-view kcal rounding) is a latent parity trap worth closing before another client renders that field. The rest are defense-in-depth / consistency polish.

---

## Needed fixes (ranked)

### 1. [MEDIUM] `MealPhotoCleanup` `FOR UPDATE` lock held across the S3 `DeleteObject` *(confirmed)*
- **Files:** `app/services/mobile_diary_mutation/service.py:220-250` (`_release_owned_object`), same pattern in `drain_meal_photo_cleanups`.
- **Problem:** `_claim_cleanup(cleanup_id)` takes `SELECT … FOR UPDATE` on the cleanup-intent row (`:213-217`); the lock is then held across `s3_helper.delete_meal_photo(...)` — a network round-trip to S3 (`:238`) — before `_forget_cleanup` + commit. This is on the hot path of every photo-bearing diary delete (`delete_entry`), not just the operator drain. It violates the repo rule "NEVER hold a DB transaction across a network round-trip; `blocking_concurrency_slot` wraps ONLY the network call." A pooled DB connection sits idle-in-transaction holding a row lock for the full S3 latency.
- **Why only MEDIUM, not higher:** contention is only among the user's own delete retry, a concurrent retry, and the operator drain, all for the *same* cleanup identity. Pool starvation isn't reachable (`DB_POOL_SIZE + overflow ≥ web threads`). The cost is a parked connection (holding back PostgreSQL's xmin horizon → vacuum/bloat) and inconsistency with the discipline enforced elsewhere.
- **Key fact that makes the fix safe:** the module's own comment (`:255`) states **S3 `DeleteObject` is idempotent** — so the lock-based serialization held across the network is not needed for correctness; a double-delete is harmless.
- **Suggested fix:** claim via a conditional write that commits *before* the S3 call (e.g. `DELETE … WHERE id=:id RETURNING` or `UPDATE … WHERE claimed_at IS NULL`), then call S3 outside any open transaction — exactly as `mobile_log_food/service.py` already does for its provider I/O. Alternatively commit/rollback to release the lock before S3 and accept the harmless double-delete.

### 2. [LOW / latent] Nutrition day-view ships the calorie target as a raw unrounded float *(confirmed representation divergence; impact latent)*
- **File:** `app/services/nutrition_day_view.py:206` — `TargetSection(AVAILABLE, value=float(macros.calories))`.
- **Problem:** `app/services/plan_facts.py:171-198` (`_display_kcal`) exists specifically because Python `round()` (banker's) and JS `Math.round()` (`floor(x+0.5)`) disagree at `.5` boundaries; Plan and `/meal-log/today` route every displayed kcal through it so no two surfaces print a different integer for the same stored target. The day-view target `value` bypasses it (a stored `2200.5` → `2200.5`, where Plan shows `2201`).
- **Why only LOW/latent:** the current consumer (`static/nutrition.js` `#nut-next`) reads only `next_action.kind` and never renders `target.value` as a number, so there is no user-visible divergence today. It becomes a real parity defect the moment any client renders `nutrition.day-view.target.value` next to a Plan/Today figure.
- **Suggested fix:** project `target.value` through the same `_display_kcal` rule, or document that day-view `target.value` is unrounded and clients must `Math.round` it.

### 3. [LOW] Completion routes leave the preflight read transaction open across the Bedrock vision call + S3 upload *(plausible)*
- **Files:** `app/blueprints/training.py:424-508` (browser) and `app/blueprints/mobile_workout_sessions.py:404-412` (native).
- **Problem:** `prepare_completion(...)` runs `owned_session` + `already_completed_today(...)` SELECTs (autobegins a transaction), then — with that read transaction still open — calls `validate_pump_check(...)` (Bedrock Sonnet vision) and `s3_helper.upload_image(...)`, and only afterward opens the real write transaction in `complete_workout`. The preflight docstrings claim the proof runs "entirely BEFORE the completion transaction," but the preflight reads already opened one.
- **Contrast:** `app/services/mobile_log_food/service.py:59-62` explicitly `db.session.rollback()`s to close the preflight read transaction "before any provider network I/O." The completion routes omit this.
- **Impact:** no row locks are held (plain SELECTs → no deadlock/lock-order exposure); the cost is a pooled connection parked idle-in-transaction for the multi-second Bedrock call (xmin horizon again) and inconsistency with the established pattern.
- **Suggested fix:** `db.session.rollback()` after the preflight reads and before `validate_pump_check`/upload on both routes. Correctness is re-established under the row lock inside `complete_workout`, so closing the read tx is safe.

### 4. [LOW / informational] `PendingAction` "latest staged" selectors lack the `id` tiebreak
- **File:** `app/services/ai_coach.py:293` and `:422` (`_tool_confirm_and_commit_meal_log` / `_tool_confirm_and_commit_workout_log`) order by `created_at DESC` only.
- **Problem:** if two actions were staged with identical `created_at`, which commits is DB-arbitrary. `PendingAction` is outside the canonical latest-row list (UserSession / TrainingPlan / NutritionPlan / WeeklyCheckIn, which all carry `id DESC`), and staging within a Coach turn is effectively sequential, so this is theoretical.
- **Suggested fix:** align to `created_at DESC, id DESC` for consistency with the repo-wide rule.

### 5. [LOW / informational] TI-03 surfaces a weekday-slot mismatch as `insufficient_pairs` rather than a not-comparable reason
- **File:** `app/services/training_intelligence/comparability.py:73-74` → `pair_exercise:107-108`.
- **Problem:** when the only prior occurrence is on a different weekday slot, `context_reason` returns `INSUFFICIENT_PAIRS`, so the user sees *insufficient data* rather than *not comparable*. Likely a deliberate vocabulary constraint (CLAUDE.md pins TI-03 to "only the 12 TI-00 missing codes"); the insight stays safe either way. Flagged for a spec owner to confirm against the TI-00 spec (not in repo).

### 6. [LOW / belt-and-suspenders] JWT validation does not assert `nbf`
- **File:** `app/services/cognito_jwt.py:136-141` — `JWTClaimsRegistry` marks only `exp` essential.
- **Not exploitable** for the current threat model: Cognito tokens are RS256-signature-pinned with `iss`/`aud`/`client_id`/`token_use` all equality-checked, and Cognito does not issue `nbf`. Noted only because migrating to a provider that sets `nbf` would silently accept not-yet-valid tokens. Add `nbf` to the registry if desired.

---

## Verified correct — do not re-investigate

Recorded so these aren't re-flagged in a future pass (each consumed real review time):

**Security**
- JWT: RS256 pinned (no alg-confusion / `none`); `iss`/`aud`(id)/`client_id`(access)/`token_use` equality-checked; leeway pinned to 0 on web + mobile via `auth_contract`.
- Mobile session lifecycle: refresh rotation + reuse detection, credential-epoch fence against the reset race, offline provider-token validation, ownership re-checks; no client-supplied identity; `/api/v1` limiter ignores cookie identity.
- Account-deletion resurrection: tombstone is a keyed one-way fingerprint; write→flush→check is race-free via `uq_user_cognito_sub`; all four subject-writing paths call `refuse_if_deleted`.
- SSRF (`menu_remote.py`): per-hop `validate_url`, DNS resolve with full private/reserved-range rejection, IP pinning + connected-peer verification (DNS-rebinding closed), redirect re-validation + https→http downgrade block, byte/time/redirect budgets, credential-free killable subprocess with wiped env.
- S3 (`s3_helper.py`): server-chosen bucket, regex-bounded keys, segment-equality owner check, presign/download require `expected_user_id`, delete primitives fail-closed on any key the app didn't mint.
- Owner-scoping: diary, supplements, tracking/progress, native HMAC tokens (owner-scoped scan), feed (friend/self + image preauth after `can_view_pump_check`).
- HMAC tokens (`nutrition_native/tokens.py`, `mobile_menu.py`): per-class domain labels (no cross-class replay), owner bound through the MAC, constant-time compare, length + TTL + future-skew bounds.
- CSRF (Origin + per-session synchronizer, default-deny; `/api/v1` exempt; GET `/logout` Sec-Fetch guarded); CSP (nonce script-src, no `unsafe-inline` scripts, jsdelivr SRI-pinned).
- No raw SQL injection surface; no `eval`/`exec`/`pickle`/`yaml.load`/`shell=True`/`render_template_string`/`send_file`.
- Mass-assignment: native profile PUT + web edit-profile enforce closed key sets; username immutable.

**Concurrency**
- Lock ordering (op-row → user-row → plan-rows) consistent across `plan_owner_lock`, `plan_replacement` (2→3), `mobile_training_generation/store.commit_plan` (1→2→3, owner lock before the `get_active_plan` check), `plan_mutation/service` (plan row alone). All use column `FOR UPDATE` + `populate_existing()`.
- Unique-constraint arbitration (`uq_pump_check_day`, `uq_workout_session_active_owner`, `uq_plan_mutation_user_key`, `uq_weekly_checkin_user_key`, `uq_user_water_day`) each classify the specific violation and fail closed on any other `IntegrityError`.
- Idempotency/replay: `plan_mutation`, `meal_idempotency`, `mobile_log_food`, `workout_session/execution` (single conditional CAS UPDATE on base revision), `coach_plan_tools/executor`. Keys not consumed on validation failure.
- "0→positive" double-fire: water funnel uses the persistent `WaterLog.quest_fired` flag via conditional `UPDATE … WHERE quest_fired=false`. No gatekeeper watches a user-resettable count.
- Latest-row singletons (`get_active_plan`, `canonical_session`, NutritionPlan reads, `weekly_checkin.latest_full_checkin`) all carry `created_at DESC, id DESC`.
- Day boundaries: no `date.today()`/`utcnow().strftime()` day-key usage outside `timeutil`; TI-03 excludes cross-date completions, never re-dates.

**Correctness**
- `meallog.py:554 … else 2000` feeds only the LLM prompt; JSON `target`/`targets` are `null` when unconfigured (F3a/F3b split).
- Native hydration commit-then-award: a gamification fault can't turn a successful water write into a retryable error.
- `weekly_checkin` body-weight: `_parse_weight` 400s on missing weight before `stage_full_checkin`, so a stored weight is never nulled.
- `progress_history._whole_days`/`incomplete_day` trim + marking correct across all three windowing cases.
- `workout_state` completion proof gates on `PumpCheck.date_key IS NOT NULL`; marker-without-pumpcheck is an anomaly, not completion.
- Mobile read-failure handlers (`mobile_today`, `mobile_progress`, `mobile_pump_checks`, `mobile_nutrition_closure`, `mobile_menu`) each return a typed capability-specific retryable 503, never auth-shaped, never empty/baseline.
- `static/nutrition.js` water/day-view/target: seq/epoch single-flight guards; unknown renders `—` (never 0); client "remaining/over" is the documented client derivation.
- `barcode._daily_progress` divide-by-zero guard unreachable for a configured positive target.

---

## Explicitly skipped (documented accepted tradeoffs, per CLAUDE.md)

Not re-reported as new findings:

- CSP `style-src-attr 'unsafe-inline'` (accepted, documented).
- Web keyed `/checkin` holds the owner `FOR UPDATE` lock across the Bedrock `generate_checkin_feedback` call ("lock-across-Bedrock debt" for LP16-A; unkeyed submissions take no such lock).
- `record_legacy_weight_update` (`/update-weight`): no-lock + same-day full-row overwrite preserved on purpose.
- JWKS forced-refresh cooldown (anti-DoS availability tradeoff).
- Menu web path allows `http://` (still fully SSRF-pinned); planned-meal exactly-once deferred.

---

## Overall structure (orientation)

Mature, single-tenant-per-user Flask monolith, ~68k LOC across ~314 Python modules, 440 test files, fully version-pinned dependencies, and an extensive `docs/` record (per-feature design docs + prior triage history).

- **Entry:** `starter.py` → `app/__init__.py` application factory → blueprint registration.
- **Two client surfaces:** server-rendered web (Jinja + `static/*.js`) and a native mobile API under `/api/v1` (`mobile_api` blueprint, Bearer/Cognito, `MOBILE_AUTH_ENABLED`-gated, approved-route allowlist, uniform no-store / typed-error / 429 envelope).
- **Service-oriented core:** `app/services/` holds the canonical authorities — each domain concept (weekly check-in, workout state/session/completion, plan mutation/replacement, nutrition targets/day-view, progress summary/history/physique/insights, training history/progression/planning) has ONE owner module with a documented pure/impure split (`queries` → pure `facts`/`analysis` → `service`) and architecture tests (AST gates) enforcing the boundary.
- **AI pipeline:** `ai_pipeline` orchestrator → `context_builder` → `memory_manager` → `prompt_builder` → provider loops (Bedrock primary, OpenAI fallback) → `moderation` → `response_formatter`, with a cache/recovery/concurrency layer and strict provider-switch (B-rule) and quota/cooldown gates.
- **Time:** single source `app/timeutil.py` (fixed Europe/Istanbul day keys).
- **DB:** SQLite local, PostgreSQL prod; Alembic migrations auto-applied at boot (expand/contract discipline enforced in CI); concurrency races proven on real PostgreSQL in CI-selected `*_pg.py` modules.
- **Deploy:** push → CI (pytest + schema-drift) → gated deploy to EC2 Docker Compose with `/health?deep=1` gate + auto-rollback; single gunicorn worker × 8 threads (capacity invariants enforced at boot).

**Posture:** the architecture's defining trait is that bug classes are closed structurally (AST/source-scan gates, `_pg.py` race tests, byte-identical web/native parity tests) rather than by convention, and prior triage remediations (`S*`/`F*`/`Hardening PR`/dated `TRIAGE_*`) are recorded in both code comments and docs. This triage found no regression in that posture.
