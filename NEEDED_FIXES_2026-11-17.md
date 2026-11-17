# FitX — Needed Fixes (Triage & Security Review)

**Date:** 2026-11-17
**Commit reviewed:** `4d6dc01` (branch `claude/amazing-dijkstra-730z20`; == `main` HEAD through the R6 Food-Search nginx adoption work `#521`/`#522`).
**Method:** Three parallel read-only deep-dive agents — (1) security vulnerabilities, (2) correctness / data-integrity / concurrency, (3) structure / maintainability / deploy & capacity risk. Each traced real attack surfaces and high-risk flows and cited `file:line`. The highest-value items were then **manually re-verified against source** by the coordinating session (citations below marked *Verified*).
**Baseline:** `NEEDED_FIXES.md`, `NEEDED_FIXES_2026-08-02.md`, `NEEDED_FIXES_2026-08-14.md`, `TRIAGE_FINDINGS_2026-10-01.md`. Prior items were not re-exhaustively re-audited; this run focused on **new** findings.

---

## Executive summary

**The codebase remains in excellent shape.** All three reviewers independently reached the same conclusion: this is an unusually well-hardened application with deep evidence of prior security and correctness triage. The classic vulnerability classes — IDOR, JWT confusion, CSRF, SSRF, SQL injection, prompt injection, upload abuse, IP-spoofed rate limits, and the workout/completion & money-path race conditions — all have concrete, correct, tested mitigations.

- **No exploitable Critical or High-severity *security* vulnerability was found.**
- The security surface (SSRF validator, `cognito_jwt`, HMAC token construction, image sanitization, CSRF, S3 key handling, secret management) was verified clean to a high standard.
- The most material finding is an **operational/capacity** one: the **web** Cognito auth calls have drifted off the mandatory `blocking_concurrency_slot()` invariant that the **mobile** path honors — a thread-exhaustion / false-rollback risk on the single-worker deployment.
- The remaining findings are Medium/Low correctness and structure items.

**Recommended fix order:** #1 (web Cognito concurrency slot) first — it directly threatens the documented capacity invariant and the deploy health gate, and the correct template already exists in `mobile_auth.py`. Then #2 (`/chat` canonical-target overwrite), then the Low items.

### Severity table

| # | Severity | Area | Type | Status | Confidence |
|---|----------|------|------|--------|------------|
| 1 | **High** | Web Cognito calls (login/logout/refresh) not wrapped in `blocking_concurrency_slot()` | Reliability / DoS / capacity | New | **Verified** |
| 2 | Medium | `POST /chat` appends a new `UserSession`, silently overwriting canonical nutrition targets | Correctness / data integrity | New | **Verified** |
| 3 | Medium | Synchronous Resend email + S3 uploads run on request threads with no concurrency slot | Reliability / capacity | New | Confirmed |
| 4 | Medium | Prod topology = single gunicorn worker / single web container (SPOF + per-deploy gap) | Availability | Known posture | Confirmed |
| 5 | Low | `PendingAction` "latest staged" selects lack the `id` tiebreak (`ai_coach.py` 325/475) | Correctness (nondeterminism) | New | **Verified** |
| 6 | Low | `tracking.progress_workout` `DailyActivity` query has no upper date bound | Correctness (latent) | New | Confirmed |
| 7 | Low | Orphaned `axisai-*` handoff directories tracked at repo root | Structure / clutter | New | Confirmed |
| 8 | Low | `ci.yml` push trigger carries a stale hand-maintained feature-branch list | CI / maintainability | New | Confirmed |
| 9 | Low | PG-concurrency CI job can silently deselect a listed-but-unmarked `_pg.py` module | CI / test-coverage gap | New | Confirmed |
| 10 | Low | Web login exposes a narrow confirmed-vs-unconfirmed enumeration oracle | Security (info disclosure) | Known tradeoff "M7" | Confirmed |
| — | Info | Per-checkout `SHOW TIME ZONE` round-trip; web `/ask` trusts client `history`; pre-auth eligibility endpoint | Hardening notes | — | Confirmed non-exploitable |

---

## Findings (detail)

### 1. High — Web Cognito provider calls are not wrapped in `blocking_concurrency_slot()`

**Where (Verified):**
- `app/blueprints/auth.py:432` — web login → `cognito_service.authenticate` → `initiate_auth`
- `app/blueprints/auth.py:~635` — logout → `global_sign_out`
- `app/services/session_store.py:217` — web session token refresh → `refresh_tokens`

`grep` confirms **zero** `blocking_concurrency_slot` usages in `auth.py`, `session_store.py`, and `cognito_service.py`. The identical mobile calls **do** wrap the slot: `app/services/mobile_auth.py:298` and `:616`.

**Why it matters:** `CLAUDE.md` makes the slot MANDATORY for every blocking provider call (FatSecret/Cognito/wearable/model): *"tek gunicorn worker + 8 thread; kapısız senkron ağ turu /health'i kuyruğa sokup yanlış deploy-rollback tetikler."* On the 1-worker / 8-thread gunicorn, a login flood (distinct IPs/usernames defeat the per-user 15/15min limiter) or a Cognito latency/outage lets each in-flight web login/logout/refresh park a request thread for up to the Cognito timeout. With all 8 threads parked, `/health` (same worker) queues → the container is marked unhealthy → the deploy health gate can trigger a **false rollback**. This is exactly the hazard the invariant exists to prevent; the web surface drifted from it while mobile honors it.

**Note:** `session_store.py:217` already correctly releases the DB transaction *before* the network call (good — no lock held across I/O); it only lacks the concurrency slot.

**Fix:** wrap the three web Cognito round-trips in `blocking_concurrency_slot()` (around the network call only, not the DB work), mirroring `mobile_auth.py`. Capacity refusal (`BlockingConcurrencyLimit`) should map to a 503 + `Retry-After`, as elsewhere.

---

### 2. Medium — `POST /chat` silently overwrites the user's canonical nutrition targets

**Where (Verified):** `app/blueprints/coach.py:159` is one of only **two** `UserSession(...)` writers in the app; the other is the documented single authority `app/services/account_profile.py:358`.

Every `POST /chat` recomputes BMR/TDEE/`target_calories` from the request body's `weight`/`height`/`goal`/`current_activity` and **inserts a brand-new `UserSession`** (`db.session.add(new_session); commit()`). Because `account_profile.canonical_session` selects the **newest** `UserSession`, that row immediately becomes canonical and drives every downstream nutrition-target reader (`derive_daily_macro_targets`, `/meal-log/today`, barcode, nutrition day-view, Coach remaining-macros).

**Failure scenario:** a client that calls `/chat` with a `goal`/`weight` differing from the user's profile silently shifts the user's daily calorie/macro targets outside onboarding/profile-edit — contradicting the "onboarding = the one target writer" principle (NUTR-PR6). `complete_onboarding` deliberately updates the canonical session *in place*; `/chat` **appends** instead.

**Mitigating context:** `account_eligibility.enforce_request` lists `coach.chat` under the `automated_weight_targets` gate, so maintainers evidently treat `/chat` as an intentional weight-target writer — likely longstanding legacy behavior rather than a regression.

**Fix:** confirm `/chat` is still a reachable production surface. If legacy, gate/retire it. If live, have it update the canonical session in place (as `complete_onboarding` does) rather than appending a new one, so a chat turn can't silently reset targets.

---

### 3. Medium — Synchronous Resend email + S3 uploads run on request threads with no concurrency slot

**Where:** `app/services/email_service.py:137` (`resend_mod.Emails.send(...)`) and `s3_helper.py` (uploads/presign) — both have 0 `blocking_concurrency_slot`. Callers run them inline in the request path, e.g. `account_recovery.py:398` and `account_registration.py:338` (welcome / password-changed email), and avatar/meal/pump-check uploads from `profile.py` / `tracking.py` / `nutrition/meallog.py` / `mobile_workout_sessions.py`.

**Why it matters:** same thread-exhaustion class as #1. A slow/unavailable Resend API or S3 endpoint parks gunicorn threads uncapped. Lower than #1 because these aren't in the explicitly-named invariant list and S3 is usually fast — but on a single worker they are a real capacity hole.

**Fix:** enqueue email via the existing RQ `enqueue_or_run` path (it already runs inline when no worker is present), or wrap both the email send and the S3 round-trips in a blocking slot.

---

### 4. Medium — Single gunicorn worker / single web container (SPOF + per-deploy downtime)

**Where:** `gunicorn.conf.py` (`workers=1`), `docker-compose.yml` (one `web` service, `mem_limit 1g`), `Dockerfile` (`graceful_timeout 30`).

The R6 blue/green slot infrastructure exists (`docker-compose.web-slot.yml`, `scripts/web_slot_runtime.py`) but per `CLAUDE.md` R6-01B the nginx switch/deploy action is "HENÜZ YOK" — the live path is still the single-container compose. A worker crash, OOM (Pillow/AI memory spike against the 1g cap), or a timeout kill takes the whole app down until restart, and every deploy has a recreate-gap outage.

**Fix:** this is partly the acknowledged posture. Prioritize finishing the blue/green switch; in the interim, `workers=2` is only safe **after** the in-memory limiter/cache fallback assumptions are removed (as the Dockerfile comment itself warns).

---

### 5. Low — `PendingAction` "latest staged" selects lack the `id` tiebreak

**Where (Verified):** `app/services/ai_coach.py:325` (`_tool_confirm_and_commit_meal_log`) and `ai_coach.py:475` (`_tool_confirm_and_commit_workout_log`) order by `PendingAction.created_at.desc()` only. The very same module already does it correctly at `ai_coach.py:536` (`created_at DESC, id DESC`), so the convention is present but missed at two sites — this violates the repo-wide rule from Triage 2026-10-01 #3/#4, whose source scan evidently doesn't cover `PendingAction`.

**Failure scenario:** if two `log_meal` (or `log_workout`) `PendingAction` rows for one user share an identical `created_at`, the committed row is DB-order-dependent (nondeterministic across Postgres/SQLite) → the user could confirm a different staged item than intended. Low because the `created_at` default carries microseconds (collisions rare) and normal flows keep ≤1 pending of a type.

**Fix:** add `PendingAction.id.desc()` to both `order_by`s (and consider extending the Triage 2026-10-01 source scan to include `PendingAction`).

---

### 6. Low — `tracking.progress_workout` `DailyActivity` query has no upper date bound

**Where:** `app/blueprints/tracking.py:604` filters `DailyActivity.date_key >= start.isoformat()` with no `<= today` bound. Currently harmless — the output loop and totals only read day keys within `[start, today]`, so future-dated rows are over-fetched but never summed.

**Fix (defense-in-depth):** add `DailyActivity.date_key <= app_today().isoformat()` so a future reader that sums `min_by_day` directly can't leak out-of-range activity.

---

### 7. Low — Orphaned `axisai-*` handoff directories tracked at repo root

**Where:** `axisai-d1-migration-reconcile-01/`, `axisai-d1-r2-test-seam/`, `axisai-mon02-be03b-r2-integration/`, `axisai-priv-app-d1-be-01/` — each holds only a stray `*.md` and is committed (`git ls-files` confirms tracked). No runtime impact; structural clutter that looks like stale worktree artifacts and misleads navigation.

**Fix:** move the content into `docs/` or delete.

---

### 8. Low — `ci.yml` push trigger carries a stale hand-maintained feature-branch list

**Where:** `.github/workflows/ci.yml` `on.push.branches` lists ~14 feature branches plus `main`. A contributor on a branch not in the list gets no CI on push (only on PR); renamed/removed branches silently rot the list → a false "no CI ran" surprise.

**Fix:** rely on `pull_request` + `main`, drop the per-feature-branch entries, or use a wildcard.

---

### 9. Low — PG-concurrency CI job can silently deselect a listed-but-unmarked `_pg.py` module

**Where:** `.github/workflows/ci.yml` `mobile-pg-concurrency` runs `pytest -m pg_concurrency <explicit file list>`. All current `_pg.py` files are registered (verified — no diff) and a guard test checks list membership, **but** a new `_pg.py` added to the list yet not given the `pg_concurrency` marker would be collected and silently deselected (0 tests run, job green). Latent process gap, not a live miss.

**Fix:** have the guard test also assert each listed module contains `pg_concurrency`-marked tests, or switch the job to marker-based collection (`-m pg_concurrency tests/`).

---

### 10. Low — Web login confirmed-vs-unconfirmed enumeration oracle (accepted tradeoff "M7")

**Where:** `app/blueprints/auth.py:442-444`. On `UserNotConfirmedException`, login returns a distinct `403 {needs_verification: true, username}` vs. the generic `401 bad_credentials`. Narrow enumeration signal. **Exploitability is limited**: with Cognito `PreventUserExistenceErrors` enabled, this exception only fires when the submitted password is *correct* (the signal sits behind a successful credential check), and the 15/15min per-username limiter throttles probing. Documented as accepted tradeoff "M7" in the source.

**Fix (optional):** return the same opaque response and drive the verify-UX off a server-set flag rather than the status code.

---

## Informational / hardening notes (non-exploitable)

- **Per-checkout `SHOW TIME ZONE` round-trip** (`app/extensions.py:24`, registered `app/__init__.py:186`): `_require_postgres_pool_utc` runs `SHOW TIME ZONE` + rollback on **every** pool checkout, on top of `pool_pre_ping`'s `SELECT 1` — two RDS round-trips before each DB-touching request. Deliberate R6 UTC fence; if latency matters, gate it to the `connect` event (TZ can't change mid-connection) instead of every checkout.
- **Web `/ask` trusts client-supplied `history`** (`app/blueprints/coach.py:233-235`): the browser coach accepts a client `history` array; the native coach correctly forces `[]`. Self-scoped (a user only shapes their own AI turn; `context_builder` has a `FRIEND_DATA` fence). Documented web behavior, not cross-user.
- **Unauthenticated eligibility endpoint** (`app/blueprints/account_eligibility.py:33-36`, `POST /eligibility`): intentional pre-auth native age-gate preview; rate-limited 30/hr/IP (correct real IP via `ProxyFix`), no DB/provider work, echoes only a verdict derived from the caller's own input. Not an enumeration/DoS vector; on record only.

---

## Areas verified clean (so they can be de-prioritized)

**Security (reviewer 1):** AuthN/AuthZ & IDOR (every state/data route carries `@require_auth`/`@require_mobile_auth`; object lookups owner-scoped by `user_id` / owner-bound `public_id`); SSRF (`menu_remote.py` — DNS public-IP validation, pinned-peer re-verification, per-hop redirect checks, downgrade refusal, credential-free subprocess); injection (all raw SQL uses bound `text()`/static strings; no `eval`/`os.system`/unsafe `pickle`); CSRF (two-layer Origin/Referer + per-session synchronizer with `compare_digest`; `mobile_api` Bearer exempt); JWT (`cognito_jwt` — RS256 against JWKS, issuer/audience/`token_use`/expiry, single-flight JWKS refresh, `jwks_unavailable` → 503 preserves session); HMAC/token construction (domain-separated subkeys, `compare_digest`, `secrets.token_*`); file upload (`image_sanitization.py` — caps before decode, `verify()` before `load()`, bomb handling, EXIF strip); secrets/crypto (no hardcoded creds; IAM instance profile; Fernet at rest with prod boot refusal for dev fallback keys); info disclosure (logs exception *types*/reason codes, not messages); rate-limit integrity (`ProxyFix(x_for=1,...)`).

**Correctness (reviewer 2):** quota reserve/refund (`premium.py` — locked-row reads, status-code refunds, no double-spend); water-funnel/quest atomicity (conditional `UPDATE ... WHERE quest_fired=false`, toggle-resistant); challenge completion (per-challenge savepoints, one-shot `WHERE completed_at IS NULL`); Apple money paths (`subscriptions.persist_verified`, `apple_reconciliation` — monotonic signed-timestamp ordering, terminal revocation, lease/generation fencing, no network-in-transaction); FatSecret retention (day-total conservation, retained aggregate added once, idempotent `SKIP LOCKED`); account deletion (hold fence, storage-before-purge, tombstone-with-purge, bounded re-release); meal idempotency / diary delete (owner-scoped keys, commit-then-S3 with durable `MealPhotoCleanup` intent, revision-under-lock); feed keyset pagination; streak (`hooks.update_streak` — column-select re-check under `with_for_update`); timezone discipline (no stray `date.today()`/`utcnow().strftime` bypassing `app/timeutil` in services).

**Structure / ops (reviewer 3):** single Alembic head confirmed (`ad17de1e0001`; the barcode-cache branch merged by `f8a9b0c1d2e3` — **independently re-verified**); all `*_pg.py` modules registered in the CI PG list; every table-creating migration descended from the fresh-schema stamp `aa11bb22cc33` has a `has_table`/`inspect` guard; the 46 pre-R6 migrations without `expand_contract` match the documented pinned set; config boot enforces `DATABASE_URL`/`SECRET_KEY`/Cognito-key/FatSecret-TLS fail-closed; `.dockerignore` excludes `.env`/`tests/`/`.github/`/`*.md`; base images pinned by digest; retired `UIUX_*_V2` flags are intentional registered-historical entries, not dead references.

---

## Codebase structure snapshot (for context)

- **Scale:** 384 Python modules in `app/` (115 services, 42 blueprints), 612 test files, 66 migrations (single head), 38 templates, 21 static JS files.
- **Largest modules** (candidates for future decomposition, not bugs): `app/models.py` (116 KB), `app/services/ai_coach.py` (80 KB), `app/blueprints/social.py` (54 KB), `app/feature_flags.py` (52 KB), `app/services/coach_plan_tools/grounding.py` (51 KB), `app/services/mobile_auth.py` (47 KB), `app/blueprints/training.py` (47 KB). `social.py` and the `ai_coach` tool-commit/fallback transaction settling were the two largest live surfaces not exhaustively covered this run — a reasonable target for the next deep dive.
- **Architecture:** Flask + SQLAlchemy, Bedrock/Claude (heavy AI) + OpenAI (light/fallback), FatSecret proxy, Cognito (native API, no Hosted UI), S3, Redis; deployed single-EC2 Docker Compose with a CI→deploy health gate + auto-rollback.

---

## Suggested remediation order

1. **#1** — wrap web Cognito calls in `blocking_concurrency_slot()` (copy the `mobile_auth.py` pattern). Highest value: directly protects the capacity invariant and the deploy health gate.
2. **#2** — decide `/chat`'s status (retire, or update-in-place) so a chat turn can't silently reset nutrition targets.
3. **#3** — move email to `enqueue_or_run` and/or slot-wrap email + S3.
4. **#5, #6** — one-line correctness hardening (`id` tiebreak; upper date bound).
5. **#7, #8, #9** — structural/CI housekeeping.
6. **#4** — continue the blue/green rollout as planned.

*No files were modified during this review. All `file:line` references are from commit `4d6dc01`.*
