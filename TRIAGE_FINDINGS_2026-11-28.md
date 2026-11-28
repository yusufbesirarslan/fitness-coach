# Triage Findings — 2026-11-28

Automated triage sweep of the FitX codebase (~85.6K LOC across `app/`, 66 migrations,
610 test modules). Three independent deep-dive audits were run in parallel:

1. **Security** — authN/authZ, IDOR, injection, SSRF, CSRF, secrets/PII, headers/cookies, HMAC tokens, DoS.
2. **Correctness & concurrency** — transactions/locking, idempotency/replay, timezone/day-keys, error contracts, numeric math.
3. **Structure & service-layer** — architecture boundaries, AI pipeline, nutrition/training services, config/deploy/ops.

## Headline

The codebase is **mature and well-hardened**. There are **no Critical/High security
vulnerabilities** and **no High-severity correctness or ops bugs**. The owner-bound
opaque tokens, AST import gates, SSRF pinning, documented lock orders, and fail-closed
error handling are notably strong.

The only concrete code defects found are **three bugs confined to one module** —
`app/services/coach_context_queries.py`, which backs the `fitx_mcp` AI-Coach DB tools.
The production `/ask` coach pipeline (which uses `context_builder`) is unaffected and
was verified correct. Everything else below is process/hygiene.

---

## Needed fixes (confirmed code defects)

All three live in `app/services/coach_context_queries.py` (the MCP AI-Coach context reader).
Impact is scoped to whether `fitx_mcp` is in use; the module feeds prompt context, so a
defect here feeds the coaching LLM a distorted picture of the user's history.

### FIX-1 — MCP nutrition log undercounts macros for expired provider meals
- **Severity:** Medium
- **Location:** `app/services/coach_context_queries.py:329-332` (`get_user_nutrition_log`)
- **Bug:** Per-day totals are summed straight from the `meal_log` columns
  (`m["kalori"] or 0`, etc.) and never add the `provider_retention` retained aggregate.
  After the ~22h FatSecret retention deadline, `provider_retention.expire_due` sets a
  single-item provider meal's `kalori/protein/karb/yag` to NULL and moves those values
  into `meal_log_retained_total`. This reader does not query that table, so those meals
  contribute **0** to the daily totals.
- **Scenario:** User logs a 600 kcal barcode/search meal Monday; the retention worker
  expires it ~22h later. Wednesday the coach calls `get_user_nutrition_log(days=7)` —
  Monday shows the provider meal at 0 kcal/macros, so the LLM is told the user ate far
  less than they did and may "correct" advice on false history. Every **live** canonical
  reader (`ai_coach`, `nutrition_day_view`, `barcode`) already adds the retained
  aggregate; this MCP reader is the one that was missed.
- **Fix:** After building `by_date`, fold in the per-day retained totals via
  `provider_retention.retained_totals(user_id, list(by_date))` (or `with_retained`) —
  add `energy_kcal/protein_g/carbohydrate_g/fat_g` into each day's `total_*`, mirroring
  what `ai_coach` already does. (Mind the key naming: retained aggregate uses
  `energy_kcal` etc. vs. this module's `total_cal`/`total_carb`.)
- **Confidence:** Confirmed (code path unambiguous; verified at source).

### FIX-2 — MCP workout-history window uses a UTC date as an Istanbul day-key bound
- **Severity:** Low
- **Location:** `app/services/coach_context_queries.py:140` + `:163` (`get_user_workout_history`)
- **Bug:** `cutoff = datetime.utcnow() - timedelta(days=days)`, then the completed-workout
  query filters `user_quest_progress.date_key >= cutoff.date().isoformat()`. `date_key`
  is a canonical **Europe/Istanbul** day key, but `cutoff.date()` is the **UTC** calendar
  date — the two differ by up to 3h around midnight, so the lookback boundary is off by up
  to one day. CLAUDE.md explicitly flags `utcnow().date()` as a day-key anti-pattern.
- **Scenario:** Shortly after Istanbul midnight (e.g. 01:00 Istanbul = 22:00 prior UTC
  day), a 7-day history request computes a cutoff `date_key` one day earlier than the
  Istanbul-correct boundary, pulling in an extra day of completed-workout rows and
  inflating `total_workouts_completed`.
- **Fix:** Derive the cutoff day key from Istanbul time:
  `(app_today() - timedelta(days=days)).isoformat()` via `app.timeutil`, instead of
  `cutoff.date().isoformat()`. (The `training_plan.created_at >= cutoff` comparison is
  fine — `created_at` is naive-UTC.)
- **Confidence:** Confirmed. Low impact (soft lookback window for prompt context).

### FIX-3 — MCP reader defines a second day-source, bypassing the canonical clock
- **Severity:** Low / informational
- **Location:** `app/services/coach_context_queries.py:18-19` (`_app_today`)
- **Bug:** `_app_today()` computes `datetime.now(_APP_TZ).date()` directly instead of
  going through `app.timeutil.app_now()/app_today()`. It is Istanbul-correct in production
  but does **not** honor the `audit_clock` ContextVar, so these MCP reads can't be frozen
  in tests, and it is a second day-source contrary to the "timeutil is the single source"
  rule. (The module already imports `app_date_of` from `app.timeutil` — the local helper
  is gratuitous.)
- **Fix:** Route `_app_today`/`_day_key` through `app.timeutil`. Fixing this also removes
  the root cause behind FIX-2.
- **Confidence:** Confirmed. No production day-boundary bug on its own.

> Note: all three share a root cause — this module predates / sidesteps the `app.timeutil`
> and `provider_retention` canonical authorities. A single small PR consolidating
> `coach_context_queries.py` onto `app.timeutil` (day source) and `provider_retention`
> (retained macros) closes all three.

---

## Process / hygiene findings (no runtime risk)

### H-1 — Every rollout flag is past its `review_by` date, unenforced
- **Severity:** Medium (process, not runtime)
- **Location:** `app/feature_flags.py` — `ROLLOUT_FLAGS` + `CROSS_REPOSITORY_FLAGS`
- **Detail:** All ~21 lifecycle records have a `review_by` in the past (2026-09-01 …
  2026-11-27; audit date 2026-11-28). The registry's stated contract is "decide by then
  or extend deliberately," but nothing (no CI test, no boot check) acts on the dates, so
  every flag silently sits dark past its decision point — and a deliberately-extended flag
  is indistinguishable from a forgotten one. Several are `SHIPPED_DARK`/`STAGING_ONLY`
  with `decision=enable` yet months overdue. This is the one place the codebase's own
  stated discipline isn't being met, with no guardrail catching it.
- **Fix:** Add a CI/test gate that fails (or warns loudly) on a past-due `review_by`, then
  bulk-triage the backlog — advance each flag to `rolling_out`/`enable`, or re-stamp a new
  deliberate review date.

### H-2 — Registered no-op flags accumulating
- **Severity:** Low (documented-intentional debt)
- **Location:** `app/feature_flags.py` — `UIUX_TODAY_V2_ENABLED`, `UIUX_NAV_V2_ENABLED`
  (select no user-reachable branch).
- **Detail:** CLAUDE.md documents these as intentional "separate cleanup debt," but they
  still surface in `[FLAGS]`/`/health` output as live knobs that do nothing, compounding H-1.
- **Fix:** Retire the two no-op keys in one cleanup PR alongside the H-1 triage.

### H-3 — `log_provider_fallback` couples a logging helper to transaction/consent control flow
- **Severity:** Low (likely unreachable)
- **Location:** `app/services/ai_coach.py:1194-1217`
- **Detail:** A helper whose job is to emit one log line has an
  `except ai_consent.ConsentDenied: db.session.rollback(); raise` branch — a logging call
  becomes a control-flow + transaction side-channel. In practice the wrapped
  `logger.warning(...)`/`current_request_id()` won't raise `ConsentDenied`, so the branch
  is effectively dead defensive code, but it's surprising.
- **Fix:** Move any D1/consent handling to the caller; keep the logger helper pure
  (swallow-and-continue only).

---

## Security — informational hygiene notes (not vulnerabilities)

The security audit found **no Critical/High/Medium issues**. Two Low/informational notes:

- **S-1 — Non-constant-time OAuth state compare.** `app/blueprints/wearables.py:101`
  (`returned != expected`). The state is a server-minted value in the caller's own session
  used for OAuth-CSRF, not a secret held against the user, so a timing side-channel yields
  no advantage. Hygiene fix: use `secrets.compare_digest`.
- **S-2 — Web menu route accepts `http://` while mobile LP15-C enforces HTTPS-only.**
  `app/services/menu_fetch.py` / `menu_remote.ALLOWED_SCHEMES` includes `http`. SSRF is
  still fully blocked for http by the same DNS-resolution + `public_ip` + pinned-adapter
  path; the only effect is a plaintext fetch of an external public menu page. Not a finding.

---

## Verified clean (no action)

Confirmed correct by reading full code paths — recorded so the next sweep can skip them:

- **Security:** IDOR/ownership scoping (web + mobile, all owner-bound), HMAC tokens
  (domain-separated, constant-time, MAC-before-parse), JWT/Cognito (RS256-pinned, `token_use`
  checked), SSRF (`menu_remote` DNS + peer-IP re-verification), SQL injection (no user data
  in raw SQL), CSRF (two-layer, mobile correctly exempt), cookies/CSP, S3 key construction,
  social kill switch, secrets/PII in logs, request-body cap + concurrency gates.
- **Correctness/concurrency:** AI allowance ledger, plan mutation/confirmation/coach executor
  (journal-keyed idempotency), workout-session checkpoint CAS, workout completion (lock order +
  atomic claim), plan replacement (`expected_plan` under lock), hydration/meal idempotency/weekly
  check-in races, numeric paths (`_display_kcal`, `nutrition_targets`, `age_on` Feb-29,
  `weekly_windows` trailing geometry), `provider_retention` on all **live** readers.
- **Structure:** `nutrition_targets` convergence (all 6 callers), `provider_retention` math,
  `nutrition_day_view`, `menu_analysis`, ai_coach B-rule (`tools_ran==0` gate), ai_recovery,
  prompt_builder cache ordering, single Alembic head (`ad17de1e0001`), capacity invariants
  (thread/pool/concurrency, fail-closed at boot), B2 direct plan-confirmation (gated 3 deep).

---

## Suggested order of work

1. **FIX-1 / FIX-2 / FIX-3** — one small PR consolidating `coach_context_queries.py` onto
   `app.timeutil` + `provider_retention` (closes all three; only user-facing correctness item).
2. **H-1** — add the past-due `review_by` CI gate and triage the flag backlog.
3. **H-2 / H-3 / S-1** — fold into routine cleanup; no urgency.

_Triage generated by three parallel deep-dive audits; all findings re-verified against source
before inclusion._
