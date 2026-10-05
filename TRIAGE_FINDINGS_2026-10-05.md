# Fitness Coach (FitX) — Triage & Security Review

**Date:** 2026-10-05
**Method:** Three parallel read-only deep-dive agents over the repo —
(1) security vulnerabilities (authn/authz, IDOR, HMAC/opaque tokens, injection/SSRF, secrets, CSRF/CSP, input validation),
(2) concurrency / transactions / data integrity (lock ordering, idempotency, commit discipline, unique-constraint arbitration, timezone day-keys),
(3) logic / correctness + structure (recent LP-13 workout work, catalog-identity generation, native Nutrition closure, progress read models).
Each agent traced candidate issues by reading the actual code, not just grep. The one Medium item was then re-verified by hand against the source.

---

## Executive summary

**The codebase remains in very good shape.** All three reviewers independently reported the same signal as the 2026-10-01 pass: an unusually well-hardened application with extensive prior triage evidence.

- **No Critical or High-severity findings** in any of the three areas.
- Authentication, authorization/ownership scoping (IDOR), SQL parameterization, SSRF defenses (DNS pinning + positive `is_global` check + per-hop redirect re-validation), owner-bound HMAC tokens, Fernet/HKDF secret handling, CSRF two-layer, CSP, and most of the transaction/concurrency surface are all implemented to a high standard.
- The actionable surface is **one Medium concurrency bug** (a genuine, reachable cross-transaction deadlock introduced with the native hydration path) plus **three Low / defense-in-depth items**.

**Recommended order of fixes:** #1 (hydration lock-order inversion) first — it is a real deadlock reachable from the live native API, and the fix has an in-repo correct template to copy. The remaining items are hardening and can ride a later change.

---

## Needed fixes (ranked)

### 1. [MEDIUM] Hydration "water funnel" inverts the per-owner lock order → cross-transaction deadlock
- **Files:**
  - `app/services/hydration.py:44-46` — `set_today_count` locks the **`User` row FOR UPDATE, then the `WaterLog` row FOR UPDATE**.
  - `app/services/hydration.py:69-98` — `claim_water_funnel_for_today` runs `UPDATE WaterLog … SET quest_fired=true` (acquiring the **`WaterLog` row lock**, held to commit per the function's own docstring), after which `award_water_logged` → `complete_quest_for_user` → `gamification.award_xp` takes `SELECT User … FOR UPDATE` (`app/services/gamification.py:168`).
  - Native entry: `app/services/nutrition_native/hydration.py:80-82` — `set_today` calls `set_today_count(...)` (commits) and then `award_water_logged(...)` (opens the second transaction).
- **The inversion (verified):** The repository's established per-owner order is **`User` row (tier 2) → child row (tier 3)** — followed by `plan_owner_lock`, `supplement_cabinet.create_supplement`, and `nutrition_plan_store`. `set_today_count` obeys it (User → WaterLog). But the funnel-award transaction takes the **opposite** order (WaterLog → User), because `claim_water_funnel_for_today` deliberately does not commit (its reward must be atomic with the claim) and holds the `WaterLog` row lock through `award_xp`'s `User` lock.
- **Concrete interleaving** — two concurrent native `PUT /api/v1/nutrition/hydration` for the same user on the first water log of the day (double-tap / retry / two devices):
  1. Request A finishes its `set_today_count` commit, enters `award_water_logged`: its transaction runs `UPDATE WaterLog … quest_fired` and now **holds the WaterLog row lock**, and proceeds toward `award_xp`, which **wants the User lock**.
  2. Request B is inside `set_today_count`: it already **holds the User lock** (line 44) and now **wants the WaterLog row lock** (lines 45-46) for the same (user, day) row.
  3. A waits for User (held by B); B waits for WaterLog (held by A) → PostgreSQL deadlock detector aborts one transaction.
- **Consequence:** one of the two concurrent requests dies with a deadlock error surfacing as an unexpected 500/503. If B (the `set` side) is aborted, the count the client sent is not saved. The browser `/water` route (`app/blueprints/training.py:940-979`) is **not** a trigger — it never takes `User → WaterLog` (it inserts and resolves the "no row yet" create via a `uq_user_water_day` IntegrityError re-read). The `User → WaterLog` order exists only in `set_today_count`, so this became reachable with NUTR-PR7.
- **Suggested fix (cleanest):** remove the `User`-row lock from `set_today_count` and handle the "no row yet" create race the way the browser route already proves is sufficient — insert, and on a `uq_user_water_day` IntegrityError roll back, re-read and update. That deletes the only `User → WaterLog` lock site, so no cycle can form. (Alternative: make the funnel-award path take the `User` owner lock **before** the `WaterLog` UPDATE, restoring the canonical User→child order on the award side too.)
- **Add a regression test** on real PostgreSQL (a deadlock cannot be reproduced on SQLite): two concurrent same-user first-of-day hydration writes must both resolve without a deadlock abort. Register it in the CI PostgreSQL-concurrency module list.

---

### 2. [LOW] `meal_idempotency.commit_once` catches `IntegrityError` without classifying the constraint
- **File:** `app/services/meal_idempotency.py:27-39` *(verified)*
- **Problem:** Unlike the rest of the codebase — which classifies a unique violation by constraint identity before treating it as a benign replay (`workout_completion.queries.is_pump_check_day_violation`, `workout_session.queries.is_active_session_owner_violation`, both fail closed and re-raise anything they cannot positively identify) — `commit_once` uses a blanket `except IntegrityError` and lets the re-read be the sole arbiter. If the MealLog insert fails with an IntegrityError that is **not** the idempotency unique violation (an unrelated unique/FK/CHECK constraint) **and** a prior row with the same client-supplied `Idempotency-Key` already exists for that user, the real error is masked and the caller is told the write replayed. The planned-meal caller (`nutrition_native/plan.py`) partly mitigates this by re-checking `idempotency_fingerprint` afterward; other callers do not, and the key is client-controlled.
- **Severity rationale:** narrow (requires a genuinely different integrity failure to coincide with a pre-existing same-key row); the re-read correctly handles the real replay race.
- **Suggested fix:** classify the violation by constraint name (mirror `is_pump_check_day_violation`) and only treat the idempotency-key unique constraint as a replay; re-raise everything else.

---

### 3. [LOW] `nutrition_targets.derive_daily_macro_targets` does not guard NaN `target_calories`
- **Files:** `app/services/nutrition_targets.py` (`derive_daily_macro_targets`, `remaining_macro_budget` / `_to_number`) *(verified)*
- **Problem:** The guard is `target_calories <= 0` plus a numeric-type check, but `float('nan') <= 0` is `False`, so a stored NaN would pass and yield NaN macros; `_to_number` would likewise propagate NaN from a NaN `consumed` value. In practice `target_calories` is an int column and `MealLog` sums are coalesced, so this is **currently unreachable** — but the sibling authority `nutrition_day_view._read_target` already `math.isfinite`-guards the same value, so the canonical targets module is marginally less strict than one of its own callers.
- **Suggested fix:** add a `math.isfinite(...)` check (treat non-finite as absent → `None`) in `derive_daily_macro_targets` for defense in depth and parity with `nutrition_day_view`.

---

### 4. [LOW] `s3_helper` presigned-URL / byte-read ownership check defaults to off
- **Files:** `app/services/s3_helper.py` (`generate_presigned_url`, `get_object_bytes`); intentional exception at `app/models.py:114` (avatar). *(verified)*
- **Problem:** Ownership is only enforced when `expected_user_id is not None` (default `None` = no check). Every live caller passes it, so this is **not currently exploitable**, but a safe default (require `expected_user_id`, or fail closed when absent) would stop a future caller from silently introducing an IDOR. The avatar property deliberately omits it (avatars are cross-viewer by design); that one call is also the pattern that would mask a mistake if copied.
- **Suggested fix:** make `expected_user_id` required (or raise when it is `None`), and give the avatar path an explicit opt-out constant so the intent is visible at the call site.

---

## Informational (no fix recommended on its own)

- **`_verify_image_bytes` fails open when Pillow is unavailable in debug/test** (`app/services/validators.py:54`). Production fails closed; dev-only, but note it for any environment where `app.debug` could be true.
- **GIF proof-image asymmetry:** `vision_images.detect_image_media_type` (used by `menu_extract.validate_pump_check`, browser data-URL path) accepts GIF, but the mobile multipart gate `validate_uploaded_pump_check_image` accepts only JPEG/PNG/WEBP — a GIF pump-check proof is rejected on mobile but would be accepted on the browser. Harmless (GIF proofs are degenerate) but inconsistent.
- **`mark_session_completed` version bump** maps a stored `0` to `2` (skips 1) via `version = (version or 1) + 1`; versions start at 1 in practice, so unreachable.

---

## Structure notes

- **Duplicated-authority consolidation is the dominant and healthy theme** of the recent window: `nutrition_targets`, `nutrition_plan_store`, `hydration`, `supplement_cabinet`, `workout_completion.completed_days`, `exercise_resolution`, and `coach_plan_tools` each explicitly collapse a fact previously derived in 2–4 places, with AST/source guards pinning single ownership. No new duplicated authority was introduced in the audited window.
- `app/services/ai_coach.py` is large and carries re-exported legacy names (a documented compatibility surface). `menu_extract.py` now mixes menu extraction with pump-check proof validation; the `validate_pump_check` family arguably belongs in a dedicated workout-proof module, though it is correctly isolated. Neither is a bug.
- No dead/orphaned routes found in the changed set; the retired `GET /api/progress/{insights,nutrition}` (Sprint 13 PR5) are confirmed no longer registered.

---

## Paths audited and confirmed correct (no finding)

**Security:** Cognito JWT validation (signature/issuer/audience/`exp`/`token_use`, JWKS forced-refresh single-flight + cooldown); `auth_contract` leeway pinned to 0 with retired-key boot rejection; Bearer-only mobile middleware with explicit pre-auth allowlist; stored-access-token re-validation + family/sub coherence + refresh-reuse detection + credential-epoch fence; 256-bit opaque credentials with `hmac.compare_digest` throughout; deleted-identity write-then-check anti-resurrection; Fernet fail-closed in production. IDOR: social/feed/pump-check, diary `CustomMealItem` (transitive ownership), supplements, native token resolvers all owner-scoped. No SQL injection (all `text()` static); SSRF defenses intact; S3 keys server-minted with segment-equality ownership and regex-fullmatch delete grammar. CSRF two-layer + mobile Bearer exemption; 12 MiB body cap + typed 413.

**Concurrency:** Global lock order (gen-op row → user row → training_plan rows) obeyed by `plan_owner_lock`, `plan_replacement`, `mobile_training_generation.store.commit_plan`; no provider/network call under a DB row lock or open ORM transaction; `uq_pump_check_day` / `uq_workout_session_active_owner` / `uq_plan_mutation_user_key` classified by constraint identity and fail closed; checkpoint exactly-once via one conditional `UPDATE … WHERE checkpoint_revision=:base`; commit discipline (`award_xp`/`_claim_quest`/`log_activity`/`record_event`/`claim_water_funnel_for_today` are all commit-free, owners hold the single commit); day-keys all via `app/timeutil` (no `date.today()`/`created_at.date()` used as a day key).

**Correctness:** LP-13 completion-proof fail-closed (byte-derived media type, `fallback` checked independently of `valid`, validator-unavailable path); LP-13 same-day start/completion serialization (session-row → day-advisory → artifacts, re-check on fresh snapshot, `expected_checkpoint_revision` under the session lock); catalog-identity generation (validate by `exercise_id` against the closed choice set, name written from catalog); `nutrition_targets` splits/Atwater/absence→None; `nutrition_day_view` per-section savepoints + unknown→`unavailable`; progress_summary qualified `yogunluk IS NOT NULL` filter + `end_day` resolved once; AI spend-guard MULTI/EXEC incr-then-check with compensation; registration resurrection write-then-check ordering.

---

### Verification status

| # | Severity | Verified by hand |
|---|----------|------------------|
| 1 | Medium | Yes — read `hydration.py`, `nutrition_native/hydration.py`, `gamification.award_xp`; inversion confirmed against the browser `/water` reference path |
| 2 | Low | Agent-traced; pattern confirmed against the two in-repo classifiers it contrasts |
| 3 | Low | Agent-traced; confirmed unreachable today, parity gap with `nutrition_day_view` is real |
| 4 | Low | Agent-traced; default-off confirmed, no exploitable caller today |
