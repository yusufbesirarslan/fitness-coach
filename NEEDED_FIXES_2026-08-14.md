# FitX — Needed Fixes (Triage Report)

**Date:** 2026-08-14
**Commit reviewed:** `dc6fda1` on `claude/amazing-dijkstra-49qpm4` (== `main` HEAD; through mobile pump-checks `#207`, mobile diary mutations `#206`, mobile food discovery/logging `#205`, mobile nutrition diary `#204`).
**Scope:** 3 parallel read-only deep-dive audits — (1) Auth/AuthZ & web-security boundaries, (2) Core business logic / DB integrity / race conditions, (3) AI pipeline / external integrations / input & file handling.
**Method:** Adversarial trace of each attack surface and high-risk flow. Every claim was verified against actual source (cited `file:line`), not against CLAUDE.md/docstrings. Prior-report items were re-checked at HEAD; still-open ones are carried forward.
**Baseline:** `NEEDED_FIXES.md` (2026-07-21 @ `21f2608`) and `NEEDED_FIXES_2026-08-02.md` (2026-08-02 @ `392c556`).

## Headline

The codebase remains **mature and defense-in-depth**. **No Critical or High-severity issues were found.** All three audits independently concluded that the classic attack classes (IDOR, JWT confusion, CSRF, SSRF, prompt injection, upload abuse, race conditions in the workout/completion layer) already have explicit, correct mitigations in place.

This run surfaced **1 Medium** correctness/integrity bug (a re-confirmation of a prior finding that was never fixed), **2 Low** items, and a handful of informational hardening notes. The two long-standing **Medium reliability** items and structural tech-debt from the prior reports remain **open** and are carried forward.

| # | Severity | Area | Type | Status | Confidence |
|---|----------|------|------|--------|------------|
| 1 | Medium | `weekly_water` challenge is completable in a single day by toggling water count | Correctness / gaming | **Re-confirmed** (prior 2026-07-21 #2, never fixed) | Confirmed |
| 2 | Low | `/chat` enforces no input-length cap (unlike `/ask`); no global `MAX_CONTENT_LENGTH` | Reliability / cost | **New** | Confirmed |
| 3 | Low | `award_badge` deferred-flush can roll back a challenge completion into a retry loop | Correctness (latent) | **New** | Confirmed (not currently reachable) |
| 4 | Medium | `ai_gate` thread-reserve breachable by ungated `_model_slots` callers | Reliability / DoS | Prior 07-21 #1 / 08-02 #3 — **re-verified open** | Confirmed |
| 5 | Medium | `mobile_auth.refresh()` holds `FOR UPDATE` row lock across a synchronous Cognito network call | Reliability / DoS | Prior 08-02 #1 — carried (verify) | Prior-confirmed |
| 6 | Low | `ProxyFix` trusts client `X-Forwarded-Host`/`-Port` that nginx never sets | Security | Prior 07-21 #3 / 08-02 #5 — carried | Confirmed |
| 7 | Low | OpenAI non-stream coach loop has no per-turn wall-clock budget | Reliability | Prior 07-21 #4 / 08-02 #9 — carried | Prior-confirmed |
| — | Info | `nbf`/`iat` not validated; menu-fetch leaks exception *type* name; `/health?deep=1` outbound-on-GET; `_pin_getaddrinfo` global monkeypatch | Hardening | New notes | Confirmed non-exploitable |
| 8 | Low | God-modules: `ai_coach.py`, `social.py` (1191), `tracking.py` (728) | Structure | Prior — open | Confirmed |

---

## 1. [Medium] `weekly_water` challenge can be completed in a single day by toggling the water count

**Files:** `app/blueprints/training.py:613-616` (`set_water`, funnel emit) → `app/services/gamification.py` (`complete_quest_for_user` → `_claim_quest` → `record_event("water_logged")`) → `app/services/challenges.py` (`record_event`, no per-day dedup) → seed `challenges.py` (`weekly_water`, `metric="water_logged"`, `target_value=5`, 75 XP).

This is a **re-confirmation of finding #2 from the 2026-07-21 report**, which the 2026-08-02 report explicitly flagged as "not independently re-surfaced — verify separately if not addressed." It was **not addressed** and an independent audit this run rediscovered it.

The `weekly_water` challenge is documented as "log water on **5 different days** this week." The challenge metric is advanced through a funnel that the route guards with a *transition* check rather than a durable per-day marker:

```python
# app/blueprints/training.py:613
if count > 0 and prev_count == 0:
    quest_result = complete_quest_for_user(current_user.id, "water_logged")
```

`prev_count` is read from the existing `WaterLog` row (`training.py:585`) and reflects the row's live value, which the same route lets the user set to any number — including back to `0`. So the `0 → positive` transition re-arms every time the count returns to zero.

**Failure sequence (single day, one user):**
1. `POST /water {count:1}` → `prev_count` 0 → funnel fires (`water_logged` +1)
2. `POST /water {count:0}` → no fire
3. `POST /water {count:1}` → `prev_count` is 0 again → **fires again** (+1)
4. Repeat steps 2–3 three more times → challenge progress reaches 5 → `weekly_water` completes in one day, awarding the 75 XP + badge designed to take 5 separate days.

Contrast with the two sibling funnels that are correctly deduped: `active_day` (deduped in `hooks.update_streak` by a real `last_login == today` date check) and `meal_logged` (each fire corresponds to a genuinely-created `MealLog` row). The daily *quest* XP is unaffected (deduped via `UserQuestProgress`), but the *challenge* metric is inflatable. The inline comment at `training.py:609-611` acknowledges the "5 gün" intent but the guard it added only dedups within a single monotonic burst, not across toggles.

**Fix:** Gate the funnel on a persisted per-day marker, matching the `active_day` pattern — e.g. fire `water_logged` only on the day's *first* creation of a positive `WaterLog` row (row did not exist → exists-positive), or record a per-day "water counted" flag and check it before firing. The row-existence transition is durable across toggles; the count-value transition is not.

---

## 2. [Low] `/chat` endpoint enforces no input-length cap; no global request-body ceiling

**Files:** `app/blueprints/coach.py:90` (`user_message = data.get("message", "")`, unbounded) → `generate_coach_reply` → `app/prompts/goals.py` (`build_plan_reply_prompt` interpolates `user_message` verbatim) → `_heavy_chat` (Bedrock Sonnet). Contrast: `app/services/moderation.py:11-20` (`validate_question`, `MAX_QUESTION_CHARS = 4000`) used by `/ask` and `/ask/stream`.

The `/ask*` endpoints enforce a 4000-char cap on user input specifically to prevent LLM token-cost amplification (per the cap's own inline comment). `/chat` reads `message` with **no length validation** and interpolates it directly into the LLM prompt with no truncation. Aggravating factors:
- No global `MAX_CONTENT_LENGTH` is configured (`app/config.py`, `app/__init__.py`, `app/hooks.py` all checked) — the Flask layer imposes no body-size ceiling.
- `/chat` is **not** behind the weekly AI-plan quota (only `/ask*` reserve quota); it is bounded only by `AI_RATELIMIT`/`BEDROCK_RATELIMIT`/the concurrency gate, which cap request *count* but not per-request token *cost*.

**Scenario:** An authenticated user POSTs a multi-megabyte `message` on each `/chat` call the rate limiter allows; each triggers a full Sonnet call with a giant prompt → outsized token/dollar cost per allowed request.

**Fix:** Cap `message` in the `/chat` handler (reuse `validate_question`, or slice to `MAX_QUESTION_CHARS`), and/or set `app.config["MAX_CONTENT_LENGTH"]` to a sane global ceiling.

---

## 3. [Low] `award_badge` deferred flush can roll back a challenge completion into a permanent retry loop (latent)

**Files:** `app/services/badges.py:19-35` (`award_badge`) + `app/services/challenges.py:135-172` (`_try_complete`).

`award_badge` does a `no_autoflush` exists-check then `db.session.add(b)` **without flushing**. If a duplicate `(user_id, badge_code)` ever reached the flush, the `uq_user_badge` violation would surface at the enclosing `begin_nested()` savepoint exit in `_try_complete` — **after** `award_badge`'s own `try/except` has already returned — rolling back the whole savepoint, *including the guarded `completed_at` UPDATE*. The challenge would then never complete, and every retry would re-hit the same duplicate.

**Currently not reachable** because (a) the `no_autoflush` exists-check catches the common re-completion case, and (b) each `badge_code` in `CHALLENGE_SEED` belongs to exactly one challenge, so no two challenges race on the same badge. It is fragile to future catalog changes (two challenges sharing a `badge_code`, or concurrent same-challenge completion if the guarded-UPDATE ordering changes).

**Fix:** Wrap the badge insert in its own savepoint and catch `IntegrityError`, or flush inside `award_badge`'s existing `try` so the duplicate is caught locally instead of at the parent savepoint boundary.

---

## Carried-forward open items (re-verified, not re-fixed)

### 4. [Medium] `ai_gate` thread-reserve breachable by ungated `_model_slots` callers
`model_concurrency_slot()` acquires the `_model_slots` semaphore with **no timeout**, and is used by routes *not* behind `ai_concurrency_gate` (`app/blueprints/food.py` `food_search`, `app/blueprints/social.py` `respond_suggestion`). A cross-user burst on these can park web threads past the "reserve 2 for `/health`" invariant, risking a false deploy-gate rollback. First raised 2026-07-21 (#1), re-verified open 2026-08-02 (#3). **Fix:** bounded `acquire(timeout=…)` → 503, and/or gate those routes, and/or fold `_model_slots` consumers into the reserve math.

### 5. [Medium] `mobile_auth.refresh()` holds a `FOR UPDATE` row lock across a synchronous Cognito network call
Per 2026-08-02 #1: the refresh transaction locks the `MobileAuthSession` family row and holds the lock + DB connection across a blocking Cognito `refresh_tokens` round-trip (~20 s worst case) before commit — violating the "no network call inside a transaction" rule the completion service enforces. This run did not re-audit the mobile-auth internals line-by-line; **verify whether it was addressed** since 08-02, and if not, release the lock before the network call (snapshot → network → re-lock → conditional update).

### 6. [Low] `ProxyFix` trusts client `X-Forwarded-Host`/`-Port` that nginx never sets
Carried from 07-21 #3 / 08-02 #5. Low blast radius; harden by pinning host/port or configuring `ProxyFix` to not trust those headers.

### 7. [Low] OpenAI non-stream coach loop has no per-turn wall-clock budget
Carried from 07-21 #4 / 08-02 #9.

### 8. [Low/Structure] God-modules
`ai_coach.py`, `social.py` (1191 lines, 3 domains), `tracking.py` (728 lines) remain oversized. Ongoing tech-debt; no behavioral defect.

---

## Informational / hardening notes (verified non-exploitable)

- **`app/services/cognito_jwt.py:136-141`** — `JWTClaimsRegistry` enforces only `exp`; `nbf`/`iat` not validated. Not exploitable (Cognito issues immediately-valid tokens; issuer/audience/`token_use` all checked separately), but validating `nbf` would be marginally stricter.
- **`app/blueprints/menu.py:143,190`** — menu-fetch failures return the exception *class name* (`type(e).__name__`) and HTTP status to the client. The exception *message* and internal URLs are never leaked; low value, noted for completeness.
- **`app/__init__.py:234-242`** — `/health?deep=1` makes an outbound request to `FATSECRET_BASE_URL` on a GET. Correctly gated to loopback + `DEEP_HEALTH_TRUSTED_CIDRS` via a single-hop `ProxyFix(x_for=1)`; residual risk is the documented single-trusted-proxy assumption.
- **`app/services/menu_fetch.py:92-104`** — `_pin_getaddrinfo` monkeypatches `socket.getaddrinfo` process-globally under a lock during connect (correct SSRF DNS-rebinding defense); it serializes the connect phase of the otherwise-parallel sub-page crawl. Performance nuance, not a security issue.

---

## Areas independently verified as correct (attempted to break, could not)

**Auth/AuthZ:** JWT algorithm pinned to RS256 (no `none`/alg-confusion), key by `kid`, `exp` essential, issuer/`token_use`/audience checked; access-vs-ID token confusion prevented (`token_use=access` required for request-auth); session-bound cross-check of `cognito_sub` → `current_user.id` with fail-closed session destruction; transient JWKS/Cognito outages → 503 without mass-logout; leeway pinned to 0 both paths with retired knob rejected at boot.

**IDOR:** every checked record load is scoped to `current_user.id` or re-verifies ownership (supplements, profile, feed items, pump-check comments, friendships, notifications, workout sessions via `public_id`); feed pre-authorized image visibility is safe (source query filters `visibility=='feed' AND user_id IN friends|self`); mobile diary/pump-check tokens are `HMAC(SECRET_KEY, user_id‖id)` recomputed with the session user.

**CSRF:** two-layer (Origin/Referer + per-session synchronizer token via `compare_digest`), fail-closed; GET `/logout` and the wearable OAuth callback have dedicated guards; `mobile_api` is Bearer-only with no ambient cookie authority.

**SSRF (`menu_fetch`):** positive `is_global` IP allowlist, IPv4-mapped-IPv6 unwrap, `{80,443}` port allowlist, per-hop re-validation with manual redirects, DNS-rebinding TOCTOU closed via IP pinning; WordPress/Drive fallbacks routed through the same guarded helper with body-size caps; cloud-metadata/RFC1918/loopback/CGNAT/TEST-NET all rejected.

**CSP:** nonce-based `script-src` with no `unsafe-inline`, `script-src-attr 'none'`, external scripts limited to pinned jsdelivr file URLs, `object-src`/`frame-ancestors 'none'`, `base-uri`/`form-action 'self'`; no `|safe` in templates, i18n via `|tojson`.

**Prompt injection:** friend content wrapped in a data-only `FRIEND_DATA` fence with token/zero-width/bidi stripping; `user_id` injected server-side into tool dispatch (never from the LLM) with `_assert_principal`; adaptive-plan/system-prompt authority comes from the server flag, not context text.

**AI cache:** `lastgood_key` hashes full `(model, system_prompt, messages, max_tokens)` incl. private context → no cross-user leakage; user-agnostic caches (`food_normalize`, temperature-0 menu extract) are legitimately deterministic.

**Uploads:** strict `data:image/...;base64` regex, explicit byte caps, real Pillow `verify()` decode, decompression-bomb pixel ceiling, format allowlist, declared-vs-detected MIME cross-check; S3 keys server-generated with ownership enforced by path-segment equality (incl. the LLM-supplied `s3_key` in `analyze_gym_photo`).

**Workout completion / sessions / gamification:** atomic PumpCheck+marker+XP+quest+activity transactions; DB-level unique claims (`uq_pump_check_day`, `uq_workout_session_active_owner`); lost-update-safe `award_xp`/`update_streak` (column-scoped `FOR UPDATE` read); `count_challenge_xp=False` breaks the `xp_earned` recursion; guarded once-only challenge completion; fixed lock-order session terminalization with race-loser reconciliation; commit-free helpers confirmed.

**Training analysis:** trailing window geometry `[end_day-6, end_day]` correct; trend functions guard `len < 2`; `select_volume_baseline` skips zero-volume weeks; no div-by-zero; `WorkoutLog` numeric columns `NOT NULL` (no None-sum crash).

**Migrations:** all fresh-boot-reachable table creates are `has_table`-guarded; the unguarded `barcode_food_cache` create is an *ancestor* of the fresh-boot stamp point so it never runs post-`create_all`; the Postgres `calc_activity_calories` trigger is byte-identical to the Python formula.

---

*Generated by 3 parallel read-only deep-dive audits. Findings verified against source at `dc6fda1`; no code was modified.*
