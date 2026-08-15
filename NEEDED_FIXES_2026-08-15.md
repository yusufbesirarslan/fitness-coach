# FitX — Needed Fixes (Triage Report)

**Date:** 2026-08-15
**Scope:** 3 parallel read-only deep-dive audits — (1) Security, (2) Correctness bugs, (3) Architecture/Reliability.
**Method:** Adversarial trace of every attack surface and high-risk flow. Every claim was verified against the code (cited `file:line`), not against CLAUDE.md. Headline findings re-verified directly during synthesis.
**Branch:** `claude/amazing-dijkstra-obb8e1`.
**Baseline:** Prior triage `NEEDED_FIXES.md` (2026-07-21). That report's items are still tracked there; this report lists what the fresh audits surfaced, cross-referencing the prior file where a finding overlaps.

---

## Headline

The codebase remains **mature and defense-in-depth**. All three audits independently concluded that most classic vulnerability and correctness classes are already closed with documented, code-verified mitigations. **No Critical issues were found.**

The one item worth fixing promptly is a **newly-surfaced correctness bug**: meals added through the AI coach's menu card are silently written with a non-canonical `ogun` token and end up mis-slotted (bucketed as snack on web, `unknown` on mobile). Everything else is reliability hardening or acknowledged tech debt.

| # | Severity | Area | Type | Confidence |
|---|----------|------|------|------------|
| 1 | **Medium** | Coach "Add to log" writes non-canonical `ogun` → meal mis-slotted | Correctness / data integrity | **Confirmed (re-verified)** |
| 2 | Medium | Web auth (login/register/reset) makes **ungated** blocking Cognito calls → thread starvation | Reliability | Confirmed |
| 3 | Low–Medium | Meal slot derived from **browser-local** clock, not Istanbul; inconsistent thresholds | Correctness | Confirmed |
| 4 | Medium (High if HTTP enabled) | `fitx_mcp` tools take `user_id` param with **zero** authz | Security | Confirmed (documented risk) |
| 5 | Medium | Two parallel nutrition stores; "single canonical ledger" enforced by discipline, not structure | Structure | Confirmed |
| 6 | Low | Fresh-boot migration idempotency window is convention-enforced, **untested** | Reliability | Confirmed |
| 7 | Low | Four UIUX rollout flags have no per-flag observability | Ops | Confirmed |
| 8 | Low | Residual indirect prompt-injection via friend content (well-contained) | Security | Confirmed |
| 9 | Low | Structural / cleanup: god-modules, duplicate gauge, image-verify fails-open in debug | Structure | Confirmed |

> **Note on the single-process ceiling (H2 in the arch audit):** production is architecturally pinned to one gunicorn worker + 8 threads (the capacity invariant *hard-fails boot* if `workers != 1`). Every stateful mechanism — in-memory limiter fallback, foodcache L1, all three concurrency semaphores — assumes one process. This is documented, guard-railed, and not a defect, but it is the biggest structural ceiling in the system and the prerequisite for any horizontal scale. Treat as a tech-debt epic, not a line-item fix.

---

## 1. [Medium] Coach "Add to log" writes a non-canonical `ogun`, mis-slotting the meal everywhere

**Writer:** `static/coach_widget.js:662-677`
**Store (verbatim):** `app/blueprints/nutrition/meallog.py:32` (`ogun = data.get("ogun", "")`), persisted at `:115`
**Consumers that break:** `static/nutrition.js:259` (web grouping), `app/services/mobile_nutrition/serialization.py:33-54` (mobile slot token)

`addToLog` posts to `/meal-log` with `ogun: 'kahvalti' | 'ogle' | 'aksam' | 'ara'` (lowercase ASCII tokens). `/meal-log` stores the value **verbatim** — it does no token→label conversion. The canonical stored form used everywhere else is the Turkish label (`"Kahvaltı"`, `"Öğle"`, `"Akşam"`, `"Ara Öğün"`). Only `/api/quick-add-meal` (`app/blueprints/nutrition/diary.py:86-90`) carries the token→label map; `/meal-log` does not.

**What breaks** — every meal a user adds through the coach's menu-analysis card:
- **Web** nutrition page groups by Turkish label with fallback `bySlot[m.ogun] || bySlot['Ara Öğün']` (`nutrition.js:259`) → the meal is silently bucketed as **snack (Ara Öğün)** regardless of the real slot.
- **Mobile** diary `slot_token('kahvalti')` misses the label map → returns **`unknown`** (`serialization.py:50-54`).
- The token is doubly wrong: coach sends `'ara'`, but the real snack token is `'ara_ogun'`.

Macros and day totals are unaffected — only the slot label is corrupted — but it is a real user-facing data-integrity bug on a live path.

**Repro:** open coach → scan/enter a menu → "Günlük Kayda Ekle" → the logged meal shows under the wrong slot on web and as `unknown` on mobile.

**Fix (pick one):**
- Convert the token to the Turkish label in `coach_widget.js` before posting (reuse the `diary.py` `MEAL_LABELS` map), **or**
- Point `addToLog` at `/api/quick-add-meal` (already normalizes), **or**
- Normalize `ogun` server-side in `log_meal` (most robust — closes the gap for any future caller).

---

## 2. [Medium] Web auth (login/register/reset) makes ungated blocking Cognito calls → thread starvation

**Area:** `app/blueprints/auth.py` → `app/services/cognito_service.py:200` (`authenticate`, `refresh_tokens`, `sign_up`, `forgot_password`, `confirm_forgot_password`)

These web paths invoke blocking `initiate_auth` / `admin_*` network round-trips (bounded at connect 5s / read 10s / 2 attempts ≈ up to ~30s) **without `blocking_concurrency_slot()`**. The mobile path (`app/services/mobile_auth.py:201,512`) *is* gated, and the `MOBILE_AUTH_ENABLED` flag record explicitly names this exact hazard as a rollout blocker for `/api/v1/auth/login` — but the already-live web path has the same shape and is reachable **pre-auth**.

The thread-reserve invariant (`ai_gate.blocking_thread_ceiling` / `enforce_gate_invariants`) does **not** account for these endpoints. Per-IP rate limits don't stop a distributed flood, so a login/reset botnet can park all 8 web threads and starve `/health`, tripping a false deploy-rollback. The bounded botocore timeout mitigates but does not remove the risk.

**Fix:** wrap the Cognito calls in `blocking_concurrency_slot()` (or a dedicated auth semaphore) and fold that ceiling into `enforce_gate_invariants` so the capacity math stays honest. This is the same mechanism PR #199 used to close the mobile-Cognito and wearable-provider gaps.

---

## 3. [Low–Medium] Meal slot chosen from browser-local time, not Istanbul (+ inconsistent thresholds)

**Files:** `static/coach_widget.js:662` (`new Date().getHours()`), `static/nutrition.js:353,409-410`

The meal slot is derived from the **browser's local clock**, violating the CLAUDE.md rule that day/time authority is server-side Istanbul only. A user in another timezone (or with a skewed clock) gets the wrong default slot. Thresholds also disagree: coach_widget uses `11/15/20`, nutrition.js uses `11/16/22`.

For `nutrition.js` these are user-editable suggestions (low impact). For `coach_widget.addToLog` the value is **submitted with no confirmation**, which compounds finding #1.

**Fix:** derive the suggested slot from a server-provided Istanbul hour, or drop the auto-suggestion on the coach path and require the canonical label (folds naturally into the #1 fix).

---

## 4. [Medium — High if HTTP transport enabled] `fitx_mcp` tools take `user_id` with zero authorization

**File:** `fitx_mcp/server.py` — `log_workout_entry` (`:329`), `log_nutrition_entry` (`:380`), read tools (`:112-146`), HTTP gate (`:641-655`)

Every MCP tool takes `user_id: int` as a plain parameter and performs **no** ownership/authorization check — the only gate is `SELECT id FROM "user" WHERE id=%s` (existence). `log_workout_entry` / `log_nutrition_entry` will INSERT into any user's ledger; read tools return any user's data.

The SQL itself is parameterized and safe (no SQLi); the missing **authorization** is the issue. The sole protection is that HTTP transport is opt-in (`FITX_MCP_ALLOW_HTTP=1`, bound to loopback). If that flag is ever set, or the server shares a network namespace with the web app / untrusted code, any co-located process can read and write any user's workout/nutrition ledger (including planting fabricated entries).

**Fix:** keep the transport strictly stdio/in-process in production. If HTTP is ever required, require a shared secret / mTLS on the listener, bind to a permission-restricted unix socket, and assert the calling principal matches `user_id` (the way in-process `context_builder.assert_principal` already does). Also: `log_workout_entry` has no volume clamp (`volume = sets*reps*weight_kg`, `>0` only) while `log_nutrition_entry` calls `clamp_serving_macros` — add a symmetric sanity clamp.

---

## 5. [Medium] Two parallel nutrition stores — "single canonical ledger" is aspirational

**Area:** `MealLog` vs `CustomMeal` / `CustomMealItem`; `app/blueprints/nutrition/diary.py:439` (grand_total over CustomMeal) vs `app/services/mobile_nutrition` (MealLog only)

CLAUDE.md states nutrition lives in one canonical ledger (MealLog), but `CustomMeal`/`CustomMealItem` is a second store with its own totals. The mobile boundary must *actively avoid* querying it to prevent double-counting — which is the tell that this isn't structurally one ledger. Surfaces are consistent today only because each picks one store; any future aggregation that sums both silently double-counts calories.

**Fix:** fold `CustomMeal` into `MealLog` (as `UserDailyNutrition` already was, migration `f6a7b8c9d0e1`), or add a test/assertion that no aggregation path reads both stores.

---

## 6. [Low] Fresh-boot migration idempotency window is convention-enforced, untested

**Area:** `app/db_init.py:139` (`stamp(revision="aa11bb22cc33")`)

On a fresh DB the boot path runs `create_all()` → stamp `aa11bb22cc33` → `upgrade()` to head, so **every** table/column-creating migration between that fixed revision and head must be idempotent. The current chain is clean (all post-stamp `create_table` / `add_column` migrations guard with `has_table` / `get_columns`), but **nothing tests this invariant**, and the "must stay idempotent" window grows unbounded as the chain extends. A future migration that forgets a guard breaks every fresh deploy, and the failure only appears when a brand-new environment boots.

**Fix:** add a test that runs the full `create_all → stamp → upgrade` path against an empty DB, or asserts every post-stamp migration inspects before creating.

---

## 7. [Low] Four UIUX rollout flags have no independent observability

**Area:** `app/feature_flags.py` — `UIUX_TODAY_V2` / `UIUX_PLAN_V2` / `UIUX_COACH_PAGE_V2` / `UIUX_NAV_V2` all carry `observability="PARTIAL — no feature-specific log line or metric"`.

Abort signals are tied to blueprint-level SLIs, so a regression from one of these UI flags can't be attributed to it — and `UIUX_NAV_V2` (widest blast radius) is the least observable. During staged rollout an operator can't distinguish "the flag broke something" from "any other change on that blueprint."

**Fix:** at minimum a per-flag `[UIUX] flag=... route=...` debug line so 5xx/4xx can be attributed during rollout.

---

## 8. [Low] Residual indirect prompt-injection via friend content (well-contained)

**Area:** `app/services/context_builder.py:162-182` (FRIEND_DATA fence), coach write tools in `app/services/ai_coach.py`

Third-party content (a friend's `full_name` / activity text) is injected into the coach prompt alongside write tools. `neutralize_friend_content` strips fence tokens + invisible chars and instructs the model to treat it as data — a solid mitigation, but LLM injection is never fully guaranteed. **Blast radius is limited:** tool `user_id` is server-supplied (the chatting user), so a successful injection can at most cause spurious writes to the **victim's own** account (nuisance data / memory pollution) — no cross-user compromise or exfiltration.

**Fix:** nothing urgent given containment. If tightening: keep write tools out of turns that carry third-party context, or require explicit user confirmation before any `commit_*`.

---

## 9. [Low] Structural / cleanup

- **God-modules (carried over from prior triage #6/#7):** `ai_coach.py` (~1247 LOC, 35 functions — tool defs + staging→commit + pending-actions + both provider loops + memory glue) and `social.py` (~1191 LOC, ~32 routes across feed/friends/pump-checks/messaging/moderation). Natural next splits; they concentrate change- and merge-conflict risk.
- **Duplicate DbPool gauge emission:** `app/__init__.py:145` (flush-thread sampler) and `:255` (`/health?deep=1` path) both write `DbPoolCheckedOut/Overflow/Size`. The sampler was added *because* `/health` isn't polled regularly, so the `/health`-path copy (lines ~89-104, 255) is now redundant. Harmless (last-writer-wins) but confusing — remove the `/health`-path copy.
- **Image-verify fails open under Flask debug:** `app/services/validators.py:49-55` — if Pillow is unavailable, `_verify_image_bytes` returns `None` (accept) when `current_app.debug` is true. Production correctly fails closed; only a misconfigured live `FLASK_DEBUG=1` is affected. Optionally gate on an explicit test flag rather than `debug`.
- **Permanent-looking re-export shim:** `ai_coach.py:28-57` re-exports `_fetch_coach_context`, `_sanitize_client_history`, `_COACH_FALLBACKS`, etc. to preserve old import paths / test monkeypatch points. Fine as a transition aid; add a dated TODO or move to a test-only shim so production imports migrate off it.

---

## Verified sound (no action needed)

The audits explicitly confirmed these against the code:

- **JWT / auth:** RS256 enforced, issuer/audience/`token_use` checked (access for request-auth, id for login — confusion prevented), `exp` essential, leeway pinned to 0 via `auth_contract`; forced-JWKS-refresh single-flight + cooldown; transient (jwks_unavailable → 503, session kept) vs definitive failure split.
- **Mobile auth:** opaque credentials hashed at rest, `hmac.compare_digest`, refresh-token rotation with reuse detection + family revocation, subject-mismatch → revoke, bearer-only (correct CSRF exemption).
- **CSRF/CSP:** Origin/Referer + per-session synchronizer token; nonce-per-request, no `unsafe-inline` in `script-src`, `object-src`/`frame-ancestors`/`base-uri` locked down.
- **SSRF (`menu_fetch.py`):** positive allow-check, IPv4-mapped-IPv6 unwrap, port allow-list, per-hop re-validation with manual redirects, DNS-pinning against rebinding.
- **S3 / IDOR:** segment-equality ownership on keys, `expected_user_id` enforced on reads/presign; web routes consistently scope by `current_user.id`; DMs friend-gated; mobile diary mutations owner-bound HMAC revision tokens.
- **SQLi:** all queries parameterized; no string-built SQL.
- **Secrets:** no hardcoded keys, IAM instance profile for S3/Bedrock, Cognito tokens Fernet-encrypted, `.env` gitignored, PII-free logs.
- **Correctness invariants:** challenge dedup (`WaterLog.quest_fired` conditional UPDATE, toggle-proof), streak double-fire (column re-read under `with_for_update`), XP lost-update (fresh column read), workout-completion atomicity (`uq_pump_check_day` claim, single commit), AI last-good cache (byte-identical input keying — no cross-user leak), streaming B-rule (provider switch only pre-first-delta / no side-effects), memory token budgeting, meal idempotency (quest XP gated behind `created`).
- **Purity boundaries:** `training_history` / `training_progression` / `training_planning` / `weekly_program` / `workout_state` / `plan_presenter` / `plan_mutation` analysis+model layers verified free of Flask/ORM imports, as documented.
- **Migration & cascade hygiene:** expand/contract discipline, post-stamp migrations guarded, boot upgrade FATAL with documented escape hatch, `_user_child_models` cascade coverage introspection-tested.

---

## Recommended order of action

1. **#1** — meal-slot `ogun` normalization (concrete user-facing data bug, small fix). Fold **#3** into the same change.
2. **#2** — gate web Cognito calls with `blocking_concurrency_slot()` + update the capacity invariant.
3. **#5 / #6 / #7** — nutrition-store consolidation guard, fresh-boot migration test, per-flag observability line.
4. **#4, #8, #9** — hardening / tech-debt as capacity allows. #4 requires no code change *unless* HTTP transport is ever enabled.
