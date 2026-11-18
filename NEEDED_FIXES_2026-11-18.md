# FitX — Needed Fixes (Triage Report)

**Date:** 2026-11-18
**Scope:** 3 parallel read-only deep-dive audits — (1) Security, (2) Correctness / concurrency / data-integrity, (3) Request-layer & structure.
**Method:** Adversarial trace of each high-risk surface. Every reported claim was verified against the code at `file:line`, not against CLAUDE.md. Documented, deliberate tradeoffs are **not** counted as findings.
**Commit reviewed:** `2b58ef2` on `claude/amazing-dijkstra-ht8sgo`.
**Repo at a glance:** 384 Python modules under `app/`, 602 test modules, 66 Alembic revisions, exactly **one** migration head (`ad17de1e0001`), one base revision.

## Headline

The codebase remains **mature and defense-in-depth**. **No Critical or High-severity issues were found.** Two of the three audits (Security, Correctness/Concurrency) found **zero confirmed defects** — every attack surface and money/ledger/state-machine path traced to a correct guard. The request-layer audit surfaced the only actionable correctness item: **1 Medium** (unvalidated numeric input on a legacy, UI-unreferenced route), plus **2 Low** and several **Info** items that are best resolved by deleting orphaned legacy routes/flags.

| # | Severity | Area | Type | Confidence |
|---|----------|------|------|------------|
| 1 | Medium | `/chat` (`coach.py`) persists unvalidated `inf`/`nan`/negative weight-height-age into the canonical `UserSession` | Correctness / data-integrity | **Confirmed** (traced at source) |
| 2 | Low | Orphaned `GET /api/progress` & `GET /history` (`tracking.py`) return per-user JSON without `private, no-store` | Security hardening | Confirmed |
| 3 | Low | Retired no-op rollout flags (`UIUX_TODAY_V2_ENABLED`, `UIUX_NAV_V2_ENABLED`) still registered | Structure / op-clarity | Confirmed (documented debt) |
| 4 | Info | `mobile_api` catch-all labels every unhandled exception `category=storage` | Observability | Confirmed |
| 5 | Info | `mobile_exercise_notes._ready` runs schema introspection **per request** (behind default-OFF flags) | Reliability (latent) | Confirmed |
| 6 | Info | `analytics_engine` nudge averages over *logged days*, not calendar days; protein week is Monday-anchored not Sunday | Correctness (advisory-only) | Suspected |

---

## 1. MEDIUM — `/chat` persists unvalidated numeric input into the canonical `UserSession`

**File:** `app/blueprints/coach.py:85-90` (parse) → `:144-172` (compute + persist).
**Status:** CONFIRMED by reading the route.

`POST /chat` does:

```python
weight = float(data["weight"])   # "1e999" -> inf, "nan" -> nan, "-5" -> -5 all pass
height = float(data["height"])
age    = int(data["age"])
```

The only prior guard is `if not data.get(field)` (line 82), which rejects falsy/empty values but **accepts** `"inf"`, `"1e999"` (→ `inf`), `"nan"`, and negatives. There is no `math.isfinite` / range / sign check. The route then computes `bmr`/`tdee`/`target_calories` and **persists a new `UserSession(weight=…, height=…, target_calories=…)` and commits** (line 171-172).

Why it matters:
- `account_profile.canonical_session` is the *newest* `UserSession`, so this freshly written row becomes the canonical session that downstream nutrition/progress reads consume.
- `target_calories == inf` **passes** the `> 0` check in `nutrition_targets.derive_daily_macro_targets`, poisoning derived macro budgets (`inf`/`nan` grams) across the nutrition surfaces.
- Sibling writers were already hardened and this one bypasses all of it: `tracking._parse_weight` enforces `math.isfinite` + `BODY_WEIGHT_MIN/MAX_KG`; `account_profile.complete_onboarding` enforces 20–500 kg.

Scenario: an authenticated user POSTs `{"weight":"1e999","height":"180","age":"30", …}` and corrupts their own canonical target-calorie / macro state (self-inflicted; not cross-user).

Reachability / severity cap: `@require_auth` only. It is in the D1 `_CLOSED_ENDPOINTS` set, but D1/eligibility enforcement is dark (default off), so the route is **open today**. No shipped template or `static/**` references `/chat` (grep clean) — it is an **orphaned legacy route** still wired into the blueprint. Severity is capped at Medium because the blast radius is the user's own data and nothing in the UI calls it; it is still a live authenticated write of unbounded data into canonical state.

**Fix (pick one):**
- Preferred: **retire the route** — nothing in the UI calls it, and `/ask*` is the live coach path.
- Otherwise: validate `weight`/`height`/`age` with the existing finite+range helpers (reuse `account_profile` bounds / `tracking._parse_weight`) **before** computing or persisting.

---

## 2. LOW — Orphaned per-user JSON reads missing `private, no-store`

**File:** `app/blueprints/tracking.py` — `GET /api/progress` (~line 179), `GET /history` (~line 504); `_PRIVATE_JSON_PATHS` at ~line 61-69.
**Status:** CONFIRMED.

Both return per-user data (weight, `target_calories`, `coach_reply`, notes) as JSON but are **not** in `_PRIVATE_JSON_PATHS`, so the `_private_json_no_store` after-request does not add the header — unlike their siblings `/checkin-history`, `/last-session`, `/dashboard-nudges`. A shared-device bfcache/back-button can replay them after logout. Both are also **dead/orphaned**: no `static/` or `templates/` reference (`/progress` already redirects away).

**Fix:** delete the two orphaned routes (preferred), or add both paths to `_PRIVATE_JSON_PATHS`.

---

## 3. LOW — Retired no-op rollout flags still registered

**File:** `app/feature_flags.py` — `UIUX_TODAY_V2_ENABLED`, `UIUX_NAV_V2_ENABLED` in `ROLLOUT_FLAGS`.
**Status:** CONFIRMED (already flagged as cleanup debt in the module header).

Per their own capability text these flags now select **no user-reachable branch** (the legacy Home/Nav they gated was deleted) — flipping them does nothing. They still inflate `/health?deep=1` `flags` and the `[FLAGS]` boot line with keys an operator could mistake for live switches. Not a defect; op-clarity debt.

**Fix:** retire the two keys (and any dead template branches) in a dedicated cleanup. The single-registry drift test stays green.

---

## 4. INFO — `mobile_api` catch-all mislabels every unhandled exception as `category=storage`

**File:** `app/blueprints/mobile_api.py:188-200` (`normalize_unhandled_mobile_failure`).
**Status:** CONFIRMED.

The envelope (`AUTH_TEMPORARILY_UNAVAILABLE`, 503, retryable) is the correct documented backstop, and the newer routes correctly avoid reaching it (they return typed domain-shaped 503s). This is purely an observability mislabel — `category=storage` is logged for *any* unhandled exception, which could mislead triage. Low value.

**Fix:** log `category=unknown` (or derive from the exception type) in the catch-all; leave the client envelope unchanged.

---

## 5. INFO — Per-request schema introspection on a dark route

**File:** `app/blueprints/mobile_exercise_notes.py` (`_ready`).
**Status:** CONFIRMED.

`_ready` calls `sa.inspect(db.engine)` and enumerates columns/constraints/FKs/checks on **every** GET/PUT (not just boot). Behind two default-OFF flags (`FITX_TRAINING_EXECUTION_CONTEXT_ENABLED` + `FITX_WORKOUT_SESSIONS_ENABLED`), so no production impact today — but if ever enabled this adds introspection round-trips per request. The sibling `mobile_exercise_history._ready` does the right thing (config-only check).

**Fix:** cache the schema verification at boot/first-call rather than per request, matching the sibling.

---

## 6. INFO — Advisory nudge arithmetic (suspected, not a data-integrity bug)

**File:** `app/services/analytics_engine.py`.
**Status:** SUSPECTED; both trace only to `get_nudges` → AI-coach context (never user-facing data, never persisted).

- `_check_hydration` (~`:221-244`) computes `avg_cups = sum(counts) / len(rows)` — averaging over *days that have a WaterLog row*, not over the 7 calendar days in the window. A user who logs heavily on a few days and not at all on others shows a high average and is never nudged. Message says "{days} tracked days", so behaviour is self-consistent — likely intentional.
- `_check_protein_goal` uses a Monday-anchored week (`today - timedelta(days=today.weekday())`) while the app's canonical weekly boundary is Sunday-23:59 Istanbul. Differs by ~1 minute at the boundary; not material.

**Action:** confirm intent; adjust only if the nudge is meant to reflect a true 7-day average. No correctness/state/financial impact.

---

## Areas checked and found healthy (no action)

**Security (zero findings):** Mobile `/api/v1` owner is always `g.mobile_user.id`; opaque-token resolution scans the owner's own ids (forged/cross-owner token → indistinguishable 404); pump-check-comparison (highest IDOR risk) owner-scopes both referenced records. `cognito_jwt` pins RS256, verifies iss/token_use/aud + expiry; JWKS forced-refresh is single-flight+cooldown. Anti-resurrection tombstone (`deleted_identity`) is race-free via `uq_user_cognito_sub`. `cognito_identity` fails closed on ambiguity. No user data reaches string-built SQL (`text(f"…")` interpolates only validated ints / hardcoded lock-function names; owner/namespace are bound params). SSRF fetcher (`menu_remote`) is best-in-class (IP-pinned adapter closes DNS rebinding, isolated credential-free subprocess). S3 keys constrained by `fullmatch` to the minted grammar with segment-equality owner checks. Production boot raises without `SECRET_KEY`. CSRF two-layer (Origin/Referer + synchronizer token, `compare_digest`); CSP nonce-based, no `unsafe-inline`. Image upload: decode→isolate-pixels→re-encode→re-verify with caps + EXIF strip. HMAC tokens domain-separated, owner-bound through the MAC, `compare_digest`, TTL + skew bound.

**Correctness / concurrency (zero findings):** AI allowance ledger (reserve→settle, lock order owner→reservations→bucket, idempotency replay, billing tri-state never guessed, keyset+`SKIP LOCKED` purge); Apple subscriptions (entitlement freshness, `persist_verified` UNIQUE arbitration, revocation terminality, Apple I/O released before locks); account deletion (storage released while rows exist, then purge+tombstone+audit in one commit, `_hold_fence` legal-hold gate); plan mutation / owner-lock (documented lock order held, `NOWAIT` avoids inverting the D1 fence); `award_xp` reads locked row as columns to defeat identity-map stale read; retention state machine (`SKIP LOCKED` claim + expiry set in the aggregate-upsert commit, idempotent); leap-year DOB age (`age_on`, 29-Feb → 1 Mar) correct; worker/jobs isolate per-operation failures. Documented tradeoffs (planned-meal not-exactly-once, active-session-staleness-on-plan-edit, `reserve_within` caller-owned transaction) confirmed as behaving as documented.

**Structure / boot:** single migration head; all `mobile_*` modules attach to the one `mobile_api` blueprint; boot capacity invariants (pool-size ≥ threads, model-gate accounting), SECRET_KEY/token-enc/FatSecret-TLS enforcement, `MAX_CONTENT_LENGTH` cap, strict flag parser (`parse_bool_flag` refuses `true`/`yes`/`"1 "`); Redis/DB fail-closed posture (`LOGIN_FAIL_CLOSED` + `/health?deep=1` gate); typed-503 discipline on live mobile/web reads.

---

## Recommended order of work

1. **#1** — retire `/chat` (or add finite+range validation). Only actionable correctness item; a live authenticated write into canonical state.
2. **#2 + #3** — one small cleanup PR: delete the orphaned `/api/progress`, `/history` routes and the two no-op `UIUX_*` flags (and their dead template branches). Removes the no-store gap and the flag-inventory drift together.
3. **#4, #5, #6** — opportunistic: fix the catch-all log category, cache the `mobile_exercise_notes._ready` introspection, and confirm the two `analytics_engine` nudge-intent questions. No production impact today.
