# Needed Fixes — Triage 2026-11-07

Deep-dive triage of `/home/user/fitness-coach` (FitX / AxisAI) run by three
parallel review passes:

1. **Security** — authz/IDOR, Cognito/JWT, HMAC owner-bound tokens, CSRF/CSP,
   SSRF (menu scraping), uploads/S3, subscription/eligibility bypass, injection, DoS.
2. **Correctness & concurrency** — transactions, lock ordering, race conditions,
   idempotency/replay, AI provider fallback (B-rule), timezone/day-key, "latest row" selectors.
3. **Structure & operations** — architecture/god-modules, dead code, config/flag
   sprawl, error-handling defaults, migration safety, test fragility, doc drift.

## Headline

The codebase is **unusually well-hardened**. The security pass found **no
Critical or High vulnerabilities**; the concurrency pass confirmed the documented
lock-ordering, idempotency/replay, and AI B-rule invariants are correctly
implemented in the paths reviewed. The findings below are a small number of
genuine, narrow issues plus structural/maintainability debt. Nothing here is an
active production emergency.

Severity key: **Critical/High** = fix before next release · **Medium** = schedule
soon · **Low** = backlog / opportunistic.

---

## Correctness & concurrency

### C1. Leaderboard `lb_dirty` snapshot is not savepoint-aware → Redis board can over-count committed XP — **Medium** *(derived cache; self-heals on next `lb_rebuild`)*

`app/services/gamification.py:100-153` ↔ `app/services/challenges.py:110-158`

`_mark_lb_dirty` (gamification.py:113) stashes a **pre-commit** XP snapshot in
`db.session.info["lb_dirty"][user_id]` (a plain dict). `award_xp` is called
*inside* `begin_nested()` savepoints — notably `_try_complete`
(challenges.py:158, `count_challenge_xp=False`). A `ROLLBACK TO SAVEPOINT`
reverts the DB row but **not** `session.info`, and it does **not** fire
`after_rollback`/`after_soft_rollback` (those fire only for real/top-level
rollbacks). `_drop_lb_dirty` is registered only for those two events
(gamification.py:149-150), so a savepoint that rolls back *after* `award_xp`
marked the user dirty leaves a stale (too-high) snapshot that `_flush_lb_dirty`
(gamification.py:137-146) then writes to Redis on the eventual real commit.

**Failure scenario:** `record_event(...)` completes a challenge → `_try_complete`
runs `award_xp(reward)` (marks dirty at X+reward) → the savepoint flush raises
(e.g. a constraint violation on the `Notification`/`Activity` insert at savepoint
exit) → savepoint rolls back (DB XP reverts to X) → `record_event` swallows the
exception (challenges.py:128-132) → outer txn commits → `after_commit` syncs
X+reward to Redis. The board over-reports that user until the next `lb_rebuild`.
Window is narrowed because (a) `award_badge` now swallows its own `IntegrityError`
(badges.py:38-45), removing the most common abort trigger, and (b) a later direct
`award_xp` in the same request overwrites the entry — so the live exposure is the
paths where a challenge-reward savepoint is the *last* writer for that user
(direct `record_event` for `pump_check_created` / `active_day` / `xp_earned`).

**Fix:** make the dirty mark savepoint-safe — record only a bare user-id set and
read the committed `rank_points`/`weekly_xp`/`streak_count` fresh in
`_flush_lb_dirty` (post-commit, committed-state-safe path), instead of
snapshotting mid-savepoint. *(Verified against the two files during triage.)*

### C2. `MobileAuthSession` row lock held across the Cognito refresh network round-trip — **Medium** *(confirmed still open from NEEDED_FIXES_2026-08-02 #1)*

`app/services/mobile_auth.py` (~`:281` `with_for_update()` → ~`:336` blocking
`cognito_service.refresh_tokens(...)` → ~`:401` commit)

The session family row is locked `FOR UPDATE`, then the blocking provider refresh
runs *inside* the held lock before commit. Under a single gunicorn worker / 8
threads this parks a DB row lock + an app thread across an external round-trip;
concurrent refreshes on the same family serialize behind a network call. The
optimistic `version`-guarded UPDATE pattern is already available to fix it.

**Fix:** release the lock before the provider call and re-apply the result with
the optimistic `version` guard (read → unlock → refresh → guarded UPDATE), so the
network round-trip holds no row lock.

### C3. Hold-mode (`AI_ADAPTIVE_PLAN_CONTEXT` ON) can charge the freemium chat quota for a reply the client never saw — **Low**

`app/services/ai_pipeline.py:311-342` + `app/blueprints/coach.py:381-392`

When `hold_for_public_check` is True, visible deltas are buffered and not
forwarded until `done` (ai_pipeline.py:321-324). If the turn ends in an `error`
frame with `work_performed=True`, `coach.py` treats it as billable and does not
refund (coach.py:382-386). With the flag OFF the same provider work would have
been *visible*, so the no-refund is honest; with the flag ON the client received
zero output yet a non-premium weekly `chat` unit is consumed. Pure mid-stream
text failures correctly fall back to OpenAI instead (empty buffer →
`_BedrockFallback`), so exposure is limited to the case where a tool preamble was
flushed (or a tool ran) before the error.

**Fix:** in hold mode, base the refund decision on "did the client actually
receive visible output / a `done` frame" rather than server-side `work_performed`
(refund when no `done` was delivered and no plan mutation committed).

### C4. A few "latest/previous row" selectors omit the documented `(…DESC, id DESC)` tiebreak — **Low**

- `app/blueprints/tracking.py:162-163` — previous `WeeklyLog` via
  `order_by(WeeklyLog.created_at.desc()).offset(1).first()`, no `id` tiebreak →
  nondeterministic "previous week" when two logs share `created_at`.
- `app/blueprints/social.py:109` and `:820` — `Message.timestamp.desc()/.asc()`
  with no `id` tiebreak.

The canonical `UserSession`/`TrainingPlan`/`NutritionPlan`/`WeeklyCheckIn`
selectors all correctly carry the `id` tiebreak (verified), so impact here is
cosmetic/ordering only.

**Fix:** append the `id` tiebreak to match the repo-wide rule enforced by
`tests/test_triage_2026_10_01_fixes.py`.

---

## Security

No Critical/High findings. Three Low observations, all mitigated by
defense-in-depth that was verified to hold.

### S1. State-changing GET route bypasses the synchronizer-token CSRF layer (wearable OAuth callback) — **Low** *(mitigated)*

`app/blueprints/wearables.py:91-126` — `GET /api/auth/callback/<provider>`
performs DB writes + external token exchange. `_csrf_protect` (app/hooks.py:166)
runs the CSRF layers only for `POST/PUT/PATCH/DELETE`, so this GET isn't covered.
**Safe because** the route is `@require_auth` and gated by an unguessable
per-session OAuth `state` nonce (fail-closed on empty expected). Minor nit: the
`returned != expected` compare (wearables.py:101) is not constant-time.
**Fix (optional):** use `secrets.compare_digest` and document that OAuth `state`
is the sanctioned CSRF control for this GET.

### S2. `style-src-attr 'unsafe-inline'` left open in CSP — **Low** *(documented accepted tradeoff)*

`app/hooks.py:67` — inline `style="..."` attributes allowed (dynamic widths like
progress bars). Full `<style>` injection is already blocked (`style-src-elem`
nonce, line 66) and script vectors are closed. Residual risk is a narrow
CSS-injection/UI-redress vector only. **Fix:** backlog — refactor dynamic-style
sites to classes/CSS vars if/when convenient.

### S3. Referer-only CSRF fallback is weaker than Origin — **Low** *(defense-in-depth holds)*

`app/hooks.py:181-191` — when `Origin` is absent the Layer-1 check falls back to
`Referer`. **Safe because** Layer 2 (per-session synchronizer token) always runs
afterward and is fail-closed (hooks.py:197-203). No action required.

### Security areas verified SAFE (checked, sound — recorded so they aren't re-triaged)

- **Cognito JWT** (`cognito_jwt.py`): RS256 signature + issuer + `token_use` +
  audience/`client_id`; request auth pins `token_use="access"`, leeway 0 via the
  single `auth_contract`; forced JWKS refresh is single-flight + cooldown-gated;
  single-validator rule enforced by `VALIDATOR_CALL_SITES`.
- **HMAC owner-bound tokens** (`nutrition_native/tokens.py`,
  `mobile_nutrition/identity.py`, `mobile_menu.py:316-378`): domain-separated keyed
  digests, owner bound through the MAC (never in payload), 144-bit width,
  `hmac.compare_digest`, length caps; menu proof validates `exp-iat == TTL`,
  future-skew bound, and expiry (replay-resistant).
- **SSRF / menu fetcher** (`menu_remote.py`): rejects userinfo/non-80-443
  ports/control chars; requires **all** resolved IPs public; connection pinned to
  the vetted IP with post-connect peer re-verification (closes DNS rebinding);
  redirects re-validated with HTTPS→HTTP downgrade blocked; byte/time budgets;
  credential-less isolated subprocess.
- **S3 presigned URLs** (`s3_helper.py`, all 7 call sites): every caller passes
  `expected_user_id`; keys validated to the owner segment of the server-built
  layout; cross-user `get_object_bytes` owner-gated; keys server-generated.
- **Image handling** (`image_sanitization.py`): dimension/pixel/byte caps,
  decompression-bomb + animation handling, EXIF strip via full re-encode, output
  re-verification; fail-closed; no parser text leaks.
- **IDOR / ownership scoping**: feed/pump-check/comment ops gate on
  `can_view_pump_check` / author-or-owner / `user_id`-scoped `filter_by`; workout
  sessions resolve only via `get_owned_session(user_id, public_id)`; mobile
  pump-check analysis re-scopes every query with `id, user_id`.
- **Raw SQL**: no user-input string interpolation; parameterized `%s` in
  `coach_context_queries.py`; `completion_fence` f-strings interpolate only
  int-validated bounds; `models.py` `text()` is literal index predicates.
- **CSRF mobile bypass** (hooks.py:168): `mobile_api` early-returns, but no
  mobile route uses cookie `current_user` — all Bearer-only, so no forgeable
  ambient credential.
- **Subscription/eligibility**: `entitlement_view` requires a fresh verified row
  (`MAX_VERIFICATION_AGE`); `persist_verified` writes only Apple-signed evidence;
  no post-revocation regain without controlled repurchase; RC2 cohort is an
  operator-set `cognito_sub` allow-list, not user-settable.
- **PII/secret logging**: emails masked via `mask_email`; "token" log lines refer
  to `max_tokens`/OAuth events, not secret values; exception text suppressed
  (type-only) at provider/S3/menu boundaries.

*Note:* flag-gated DARK features (AI allowances/consent, model routing, dual-env
Apple) are default-OFF in production; reviewed structurally, runtime surface not
exercised.

---

## Structure & operations

### ST1. Blueprint catch-all returns an auth-shaped 503 — fragile convention — **Medium** *(error-handling default)*

`app/blueprints/mobile_api.py:188-199` — `@bp.errorhandler(Exception)` →
`AUTH_TEMPORARILY_UNAVAILABLE` 503. This blueprint hosts *all* `/api/v1` routes,
so any uncaught read failure falls through to an **auth-shaped** error that makes
clients drop a good session. Every read-only route must remember to catch its
storage faults in-view and re-raise a domain-typed 503 (`PROGRESS_UNAVAILABLE`,
`TODAY_TEMPORARILY_UNAVAILABLE`, …); enforced only per-route by individual tests.
One forgotten try/except silently regresses session retention.

**Fix:** make the blueprint-level `Exception` handler emit a neutral non-auth code
(e.g. `INTERNAL_UNAVAILABLE`, retryable) so the *default* is safe, keeping domain
codes as explicit refinement. Inverts the failure mode from "fragile unless every
author remembers" to "safe by default."

### ST2. `models.py` is a 2096-line, 71-class god-module — **Medium-High** *(maintainability)*

`app/models.py` holds every table across all domains. The service layer is
beautifully split (pure/impure packages + AST gates), but the data layer is one
monolith → merge-conflict magnet, hard per-domain reasoning, and a manual
`_user_child_models` cascade side-register in `app/cli.py` (guarded by
`tests/test_cascade_delete.py` but still manual).
**Fix:** split into a `models/` package by domain, re-exported from
`models/__init__.py` so imports stay stable. Mechanical, low-risk.

### ST3. No shared Jinja base template — 0 of 38 templates use `{% extends %}` — **Medium** *(maintainability)*

Verified: `grep '{% extends' templates/*.html` → 0 matches. 24 include
`_head.html` (partial dedup) but head/nav/scripts/brand are re-declared per page.
This is the standing P0 root cause from `docs/fitx-things-to-fix.md` and is *why*
UX convergence work (nav shells, brand, CSP nonce) keeps being applied N times and
drifting. **Fix:** introduce `templates/_base.html` with
`{% block head/nav/content/scripts %}` and migrate pages incrementally.

### ST4. Accreting god-modules on the service/HTTP side — **Medium** *(structure)*

`app/services/ai_coach.py` (1637 lines, grown 1199→1211→1637 across triages
despite a standing extraction recommendation): mixes tool defs, both provider tool
loops, pending-action/confirmation state, memory keys, gym-photo completion; large
`# noqa: F401 re-export` shim surface makes it a brittle test seam.
`app/blueprints/social.py` (1223 lines, feed/friends/moderation) and
`tracking.py` (836 lines) are the same pattern.
**Fix:** extract a `coach_tools/` loop module; consider per-domain splits of
`social.py`. Not urgent (no functional bug) but blast radius keeps growing.

### ST5. Single gunicorn worker / 8 threads with synchronous blocking AI calls — **Medium** *(inherent ops ceiling)*

`gunicorn.conf.py`: `workers=1, threads=8, timeout=300`. Coach/menu/plan AI calls
are synchronous; 8 slow turns can fill every app thread. Extensively mitigated
(concurrency gates, thread-reserve boot invariant, `blocking_concurrency_slot`,
`MAX_CONTENT_LENGTH`) and the raise-workers hazard is documented, but it remains
the dominant single-point capacity ceiling. **Fix:** no quick fix — the documented
path (AI endpoints → separate worker/queue, remove in-memory cache/limiter
fallback coupling) should be scheduled before scaling.

### ST6. Dual flag taxonomy + direct `os.getenv` in services — **Low-Medium** *(config consistency)*

Governed `ROLLOUT_FLAGS` registry (17 flags) coexists with ~20 operational
booleans read directly via `os.getenv` in `config.py` and direct `os.getenv` in
~11 service modules (`ai_gate.py`, `ai_spend_guard.py`, `cognito_jwt.py`,
`fatsecret.py`, `premium.py`, `session_store.py`, …) rather than
`current_app.config`. "What's configured?" has no single answer, and import-time
vs request-time reads can diverge. **Fix:** migrate service-level reads to
`current_app.config` so `/health?deep=1` flags/capacity stays authoritative;
document the two-tier split where intentional.

### ST7. Four stray top-level `axisai-*` directories — committed planning docs — **Low** *(clutter)*

`axisai-d1-migration-reconcile-01/`, `axisai-d1-r2-test-seam/`,
`axisai-mon02-be03b-r2-integration/`, `axisai-priv-app-d1-be-01/` are each a single
tracked markdown file — leftover feature-branch planning docs that landed at repo
root, referenced nowhere in `app/` or `docs/`. **Fix:** move under `docs/archive/`
or delete; they masquerade as source dirs.

### ST8. Documentation sprawl + stale canonical tracker — **Low** *(doc drift)*

122 `docs/` files + root `NEEDED_FIXES*.md`, `SEC001…`, `SEC005…`,
`TRIAGE_FINDINGS_2026-10-01.md` (+ this file). `docs/STATUS.md` declares itself the
single canonical tracker and forbids new `TRIAGE_<date>.md`, yet is last-updated
~2026-07-15 while root-level reports violate the rule. `CLAUDE.md` is 181 KB,
near the edge of usable-as-contract. **Fix:** revive STATUS.md as the live tracker
or drop the "single tracker" claim; archive point-in-time reports.

### ST9. Retired flags still carry dead branches — **Low** *(dead code)*

`UIUX_PLAN_V2_ENABLED` is "retired from the rollout registry" yet still appears at
`app/blueprints/training.py:212` (comment) and in `feature_flags.py`;
`UIUX_NAV_V2_ENABLED`, `UIUX_TODAY_V2_ENABLED`, `UIUX_COACH_PAGE_V2_ENABLED` remain
"registered historical" and select nothing. **Fix:** finish retirement — remove
no-op branches and registry rows on the next cleanup pass.

---

## Open items re-confirmed from prior triage docs

- **NEEDED_FIXES_2026-08-02 #1** — MobileAuthSession lock across Cognito refresh:
  **still open** → tracked above as **C2**.
- **NEEDED_FIXES_2026-08-02 #3** — `ai_gate` thread-reserve breachable by an
  ungated `_model_slot` caller: Hardening PR4 added a *boot* invariant; re-verify
  whether the *runtime* breach path (a caller taking `_model_slot` without the
  outer `_ai_slot`) is actually closed or only boot-validated.
- **NEEDED_FIXES_2026-08-02 #5** — `ProxyFix` trusts `X-Forwarded-Host`/`-Port`
  that nginx never sets (nginx sets only Host/X-Real-IP/X-Forwarded-For/-Proto):
  re-check `app/__init__.py` ProxyFix args. Low blast radius (CSRF Layer-2 holds);
  self-inflicted external-URL spoofing remains possible.
- **Prior #4 (OpenAI non-stream coach per-turn budget)** — appears **RESOLVED**:
  `_run_coach_conversation_openai` carries `_coach_turn_deadline()` /
  `timeout=min(30.0, remaining)` (ai_coach.py:1320,1333,1357). Drop it from the
  open lists.
- **TRIAGE_FINDINGS_2026-10-01 / CLAUDE.md known defects (tracked, intentionally
  not fixed):** (a) Coach-widget menu dish "Add" sends slot tokens `/meal-log`
  rejects → 400 (`coach_widget.js`); (b) CSP `style-src-attr 'unsafe-inline'`
  (hooks.py:67) — see **S2**. Confirm both still acceptable.
- **CLAUDE.md `OPEN:` FatSecret boundary** — diary totals reaching Coach/QR once AI
  opens, logged values retained: explicitly deferred compliance item gated on D1
  AI launch; track against that activation.

---

## Lower-confidence / not fully closed (for a deeper follow-up pass)

- **`ai_allowances` ledger + native Training allowance binding**
  (`ai_allowances/service.py`, `physical.py`, `execution.py`): large, newer,
  **default-OFF**. `canonical_within`/`finish` ordering and D1 fence placement look
  coherent, but the dispatch-marker ↔ hold ↔ settlement state machine was not
  exhaustively traced. Design note (not a confirmed bug): `Action.success()` is
  invoked for a *fallback* reply delivered as visible text, so a premium allowance
  is consumed on a fallback — opposite policy to the freemium refund; appears
  intentional (premium = provider attempt made) but worth a second look before the
  flag is turned on.
- **Apple subscription / reconciliation** paths are DARK/default-OFF; not reviewed
  in depth.

---

## Suggested order of work

1. **C1** (leaderboard over-count) and **C2** (auth lock across network) — the two
   genuine Medium correctness bugs with concrete fixes.
2. **ST1** (safe-by-default blueprint error handler) — small change, removes a
   recurring regression class.
3. **C3 / C4 / S1** — Low, quick, opportunistic.
4. **ST2 / ST3** (models + base-template splits) — scheduled maintainability work;
   mechanical and high-leverage.
5. Backlog: ST4–ST9, the DARK-feature follow-up pass, and the re-confirmed prior
   items (ai_gate runtime breach, ProxyFix).
