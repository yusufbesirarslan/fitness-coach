# FitX — Remaining Triage Fixes (backlog)

Follow-up backlog from the 2026-06-17 triage. The "act first" set (C1, C2, H1,
DB1 + the related medium bugs M3 / H1-Redis / extra FK indexes) was implemented
in commit `cefbcab` — see [`triage-fixes-2026-06-17.md`](triage-fixes-2026-06-17.md).
The items below were deliberately deferred and remain open.

## 🟡 Medium

### H2 — CSRF synchronizer token
- **What:** CSRF defense is currently Origin/Referer-only (`app/hooks.py:61-95`).
  Add a per-session synchronizer token for state-changing methods
  (POST/PUT/PATCH/DELETE) as defense-in-depth, and audit that no state-changing
  route is reachable via GET.
- **Why deferred:** Large surface — touches every form and `fetch()` in the app;
  today's Origin/Referer check + `SameSite=Lax` already mitigates.
- **Approach:** Flask-WTF `CSRFProtect`, or a hand-rolled token injected via the
  existing `inject_csp_nonce`-style context processor and validated in
  `_csrf_protect`. Expose the token to JS for `fetch` (meta tag + header).
  Note: login rotates the session (`session.clear()`), so issue/re-read the
  token *after* login.

### Premium enforcement (server-side gating)
- **What:** `is_premium` / "1 AI plan per week" is UI-advisory only — no
  server-side gate on the AI generation routes (`app/blueprints/pages.py:59-65`
  and the AI plan/coach endpoints).
- **Why deferred:** Product decision on whether/how hard to gate.
- **Approach:** A decorator or in-route check on AI generation endpoints that
  enforces the weekly quota for non-premium users (count plans in the current
  Istanbul week via `app/timeutil`).

### Rate-limiter fail-open
- **What:** When Redis is down the limiter silently falls back to in-memory
  (`app/extensions.py:28-33`), weakening the distributed login brute-force
  throttle. Currently only *warned* on boot (`warn_if_limiter_degraded`).
- **Why deferred:** Deliberate availability trade-off — fail-closed could lock
  out all logins on a Redis blip.
- **Approach:** Decide policy. Option: fail-closed only for the login throttle
  (stricter limit / 503 on auth) while keeping other routes fail-open.

### Error tracking / structured logging + dependency pins
- **What:** No Sentry/OTel; loose `>=` pins on Pillow / pdfplumber (untrusted
  upload parsers) in `requirements`.
- **Approach:** Add Sentry (DSN via env, no hardcode), structured request
  logging; pin Pillow/pdfplumber to known-good ranges and add Dependabot.

### MCP `get_today_volume` UTC day-key (same class as C2)
- **What:** `fitx_mcp/server.py:567` rolls up `workout_log` with
  `created_at::date = CURRENT_DATE` (UTC) — wrong during 00:00–03:00 Istanbul.
- **Why deferred:** `workout_log` has no `tarih` column, so unlike C2 this needs
  UTC day-bounds for the Istanbul day, not a column swap.
- **Approach:** Compute Istanbul day bounds (mirror `app/timeutil.utc_day_bounds`)
  and filter `created_at >= :start AND created_at < :end`. Audit the other
  `created_at::date` ranges at lines 661/669/677/685 for the same issue.

## 🟢 Low / hygiene
- **Username enumeration** via unthrottled `/friends/search` — add a rate limit;
  usernames are already semi-public so low severity.
- **Stale comment:** "no CASCADE" note in `_purge_user` is outdated (FKs now
  have ON DELETE CASCADE via migration `a1b2c3d4e5f6`) — fix the comment.
- **Legacy `UserDailyNutrition` model** lingering after the MealLog migration —
  confirm unused and remove the model + table (migration).
- **`nutrition.py` god-module** — consider splitting by concern.
- **`calculate_bmr`** lacks None-guards on `gender` / `goal` — add defensive
  defaults.
- **LLM prompt-injection** from scraped menus — currently contained by the
  staging→confirm flow + server-injected `user_id`; keep in mind if that flow
  changes.

---
_Verified clean in the triage (no action needed): SQL injection, IDOR, MCP HTTP
gating, secrets handling, S3 pre-signed URL scoping, file-upload validation, XSS,
open redirects._
