# Needed Fixes — Triage 2026-10-10

Deep-dive triage of the FitX (fitness-coach) backend across three dimensions:
**security**, **correctness/concurrency**, and **architecture/structure**. Each
finding below was verified against source, not inferred from docs.

## Executive summary

The codebase is in **genuinely good health**. The service-owns-authority
discipline (pure/dirty split, AST gates, canonical read models) is real and
holds: BMR/TDEE and macro targets are fully de-duplicated, there is a single
Alembic head, the cascade-delete list is complete, and every `*_pg.py`
concurrency module is registered in CI. The security surface (authz/IDOR, auth,
CSRF, injection, SSRF, token signing) is defensively implemented with **no
Critical/High/Medium vulnerability found**.

The real debt is concentrated in a handful of legacy, pre-discipline areas
(`social.py`, `tracking.py`) and one systemic error-envelope design smell on the
mobile surface. One confirmed data-integrity bug is live.

Priority order: **C1 → A1 → A2 → the Low correctness bugs → A3/M1 cleanups.**

---

## Confirmed bugs (fix these)

### C1 — MEDIUM · `reposts_count` drifts permanently upward (quote create/delete asymmetry)
- **Files:** `app/blueprints/social.py:299-301` (increment) vs `:326, :329-334` (decrement); model `app/models.py:1056`.
- **Verified:** `feed_repost` increments `PumpCheck.reposts_count` **unconditionally for both `repost` and `quote` modes** (the increment is above the mode branch). `feed_item_delete` decrements **only when `item.item_type == "repost"`**, so deleting a quote never decrements. The `FeedItem` unique constraint `(user_id, item_type, ref_type, ref_id)` lets `repost` and `quote` coexist for the same pump check, so the two sides of the lifecycle disagree on whether a quote counts.
- **Failure scenario:** User quotes pump check P → `P.reposts_count = 1`. User deletes the quote → no decrement → count stays 1 forever. Every quote (and every quote→delete cycle) permanently inflates the displayed `repostsCount` (`app/services/pump_checks.py:133`). The counter cannot self-correct.
- **Fix:** Make create/delete symmetric — either decrement for the same `item_type` set the increment covers (repost **and** quote), or only increment on `repost`. Given the product intent ("reposts count"), aligning the decrement to cover quotes is the smaller change.

### C2 — LOW · Duplicate notifications from check-then-act dedup with no backing constraint
- **File:** `app/services/notifications.py:44-64`.
- **Verified:** `notify()` dedups by a `no_autoflush` SELECT for an existing unread row, then INSERTs if none found. There is no unique constraint on `(user_id, actor_id, ntype, target_type, target_id, is_read)`.
- **Failure scenario:** Two near-simultaneous `POST /pump-check/<id>/comments` by the same user (`app/blueprints/social.py:229`) both pass the dedup SELECT and both INSERT → two unread notifications where one is intended. Cosmetic only (badge/list), no data corruption. The *like* path is NOT affected (its `uq_pump_check_like_user` constraint rolls the loser back in the shared transaction).
- **Fix:** Add a partial unique index for unread dedup rows, or accept as cosmetic and document. Low urgency.

### C3 — LOW · Coach meal-confirm uses a different "latest pending" selector than the preview
- **Files:** `app/services/ai_coach.py:291-294` vs `app/services/coach_confirmation.py:287-292`.
- **Verified:** `_tool_confirm_and_commit_meal_log` orders pending actions by `created_at.desc()` only; `active_log_pending` (what the user is shown/confirms) orders by `created_at.desc(), id.desc()`.
- **Failure scenario:** Two staged `log_meal` PendingActions with identical `created_at` → preview and committing tool can disagree on which is logged. Narrow window (`should_refuse_new_staging` blocks same-turn staging), practically rare.
- **Fix:** Align the confirm selector to the canonical `created_at DESC, id DESC`.

### C4 — LOW · `/log` weight-diff message compares against a possibly-non-deterministic row
- **File:** `app/blueprints/tracking.py:161-163`.
- **Verified:** After inserting the new `WeeklyLog`, the "vs previous" message reads `order_by(WeeklyLog.created_at.desc()).offset(1).first()` with no `id` tiebreak.
- **Failure scenario:** Two rows sharing a `created_at` make the `offset(1)` row non-deterministic (can resolve to the just-inserted row) → wrong/`=0` diff. Message text only, no persisted effect.
- **Fix:** Add `, WeeklyLog.id.desc()` to the ordering.

### C5 — LOW · Non-constant-time OAuth `state` comparison
- **File:** `app/blueprints/wearables.py:101` — `if not expected or returned != expected:`.
- **Verified:** The wearable OAuth callback compares the returned `state` against the session nonce with `!=`, while every other secret comparison in the codebase (CSRF token, HMAC tokens) uses `hmac.compare_digest`.
- **Impact:** Realistically negligible — `state` is a per-session `secrets`-generated nonce stored server-side; the timing channel is infeasible to exploit. Flagged only for consistency with the codebase's own constant-time convention.
- **Fix:** `secrets.compare_digest(returned, expected)` behind the existing `not expected` guard.

---

## Structural / architecture fixes

### A1 — HIGH · `mobile_api` catch-all is auth-shaped, forcing a fragile per-route workaround
- **File:** `app/blueprints/mobile_api.py:185` — `@bp.errorhandler(Exception)` returns `AUTH_TEMPORARILY_UNAVAILABLE`.
- **Problem:** An auth-shaped error makes a native client discard a *good* session, so **every feature must defensively re-catch its own exceptions in-route** and emit a feature-specific typed 503 (`PROGRESS_UNAVAILABLE`, `TODAY_TEMPORARILY_UNAVAILABLE`, `NUTRITION_TEMPORARILY_UNAVAILABLE`, `PUMP_CHECK_TEMPORARILY_UNAVAILABLE`, …). ~13 modules carry this workaround. This is backwards: the default should be a neutral, non-auth `INTERNAL_TEMPORARILY_UNAVAILABLE`, with auth paths opting *into* auth semantics.
- **Risk:** Any new route that forgets the workaround silently gets wrong (session-dropping) error semantics — a correctness trap that scales with every feature.
- **Fix:** Make the catch-all neutral once; remove the ~13 defensive copies. (Audit each removal — some routes need a feature-specific code for other reasons.)

### A2 — HIGH · `social.py` is a god-blueprint with business logic inline (no service layer)
- **File:** `app/blueprints/social.py` — ~1254 lines, 32 routes, ~40 inline `db.session.add/commit/flush/delete` calls.
- **Problem:** Likes, comments, reposts, hides, reports, friendships, messages all mutate state directly in the transport with ownership/validation interleaved — the single largest deviation from "blueprints are thin transports." Feed *reads* were extracted to `app/services/feed.py`; writes were not.
- **Risk:** This is exactly the surface where the app has repeatedly found ownership/idempotency/commit-ordering bugs (the 2026-09-30 and 2026-10-01 triage rounds, and **C1 above**), yet it has the least structural protection.
- **Fix:** Extract the write transactions into a `social_*` service (owner-scoped, atomic, testable). Next extraction target.

### A3 — MEDIUM · Service-layer HTTP/request leaks in legacy (non-gated) services
The AST gates only cover newer modules, so these inverse-of-discipline leaks persist:
- `app/services/meal_idempotency.py:4,16` — imports `from flask import request` and reads `request.headers.get("Idempotency-Key")` directly. **Fix:** pass the key in from the transport.
- `app/services/gamification.py:3, 458-522` — returns `jsonify(...)` from the leaderboard authority. **Fix:** return data; jsonify in the route.
- `app/services/premium.py:16,193` — returns `jsonify`. **Fix:** same.
- `app/services/ai_gate.py:31` — returns `jsonify` (most defensible; it is explicitly an HTTP concurrency gate).
- Low effort; prevents confusion for future extraction.

### A4 — MEDIUM · Two "today guidance" stacks with a name collision; newest is built-but-unwired
- **Live:** `app/today_guidance.py` (UX-2 PR5), consumed by `today_presenter.py` + `plan_presenter.py`.
- **Unwired:** `app/services/today_guidance_read_model.py` + `today_guidance_projection.py` (LP17 / commit #423) — imported only by tests, wired to no route.
- **Assessment:** The isolated landing is consistent with the project's "land the authority before the transport" pattern, so it is intentional. The risk is navigational: a future reader can't tell which is canonical without archaeology.
- **Fix:** Rename the new read model to disambiguate, or add a short note pinning which stack (web vs. native) will converge on it.

### A5 — MEDIUM · `tracking.py` / `fitx_mcp` / `analytics_engine` still carry inline `WorkoutLog` readers
- **File:** `app/blueprints/tracking.py:779 progress_heatmap()` reads `WorkoutLog` inline instead of delegating to the canonical `training_history` foundation.
- **Assessment:** Documented as deferred ("sonraki PR") — but it is a live second reader of a canonical fact (the exact defect class the architecture is organized to prevent), and the "next PR" has not arrived across many sprints. "Documented deferred" has quietly become "permanent."
- **Fix:** Converge onto `training_history`, or explicitly re-classify as permanent and stop calling it deferred.

---

## Lower-priority / watch-list

- **L1 — `ai_coach.py` mega-module (1454 lines)** kept wide by backward-compat re-export shims (`:28-65`). The modular AI-pipeline refactor is only half-realized at the import layer. `coach_plan_tools/grounding.py` (1299) and `executor.py` (793) are large and tightly coupled. Watch; refactor opportunistically.
- **L2 — Feature-flag dependency chains** (11 flags with `depends_on`, `app/feature_flags.py`): several core surfaces (workout sessions, training insights, plan-mutation tools) are gated OFF behind multi-level chains, so a meaningful fraction of shipped service code is dark in production and only exercised by tests → latent drift risk. Well-centralized; just a large dark-code surface to track.
- **L3 — Retired flags still registered:** `UIUX_TODAY_V2_ENABLED` and `UIUX_NAV_V2_ENABLED` remain in `ROLLOUT_FLAGS` but select nothing (`nav_v2` hardcoded `True` at `app/hooks.py:147`). Inventory noise that implies a rollback lever that no longer exists. Remove from the registry or mark clearly inert.

---

## Verified clean (no action needed)

- **Security:** JWT validation pins RS256 + issuer + `token_use` + audience + `exp` (`cognito_jwt.py:136-166`); all `mobile_*` routes carry `@require_mobile_auth`; web login is session-fixation safe; mobile refresh rotates/revokes token families. CSRF two-layer on all write methods; `mobile_api` correctly Bearer-exempt. Raw SQL is parameterized/read-only (`coach_context_queries.py`). Menu fetcher (`menu_remote.py`) is a best-in-class SSRF defense (public-IP-only DNS, connection-pinned peer-IP verification, per-hop redirect re-validation, subprocess isolation). HMAC tokens domain-separated, owner-bound, constant-time verified. No hardcoded secrets; emails masked in logs.
- **Concurrency:** `award_xp`/`_claim_quest`, `challenges.record_event`, `hydration.claim_water_funnel_for_today`, `weekly_checkin` (web + native), `workout_completion.complete_workout`, `premium` quota, social like/comment counters, `mobile_diary_mutation` delete — all use correct locking / guarded SQL-side increments / unique-constraint race arbiters / savepoint isolation / canonical Istanbul day keys.
- **Numeric:** `nutrition_plan_schema._number` / `_parse_weight` reject NaN/Inf/bool/out-of-range.
- **Architecture:** AST gates genuinely hold (gated services are Flask-free); macro-target + BMR/TDEE duplication fully resolved; single migration head; cascade list complete incl. dual-FK models; table-creating migrations use `has_table` re-runnability guards; all PG concurrency modules in the CI list; retired `GET /api/progress/{insights,nutrition}` actually gone.
