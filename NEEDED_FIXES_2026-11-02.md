# FitX — Needed Fixes (Triage Report)

**Date:** 2026-11-02
**Commit reviewed:** `f438459` (branch `claude/amazing-dijkstra-pzbkww`, == `main` HEAD; through R6 forward-recovery `#477`/`#481`, Apple dual-env/lifecycle, D1 Phase 2, LP21 general/cardio plans).
**Scope:** 3 parallel read-only deep-dive audits — (1) Security vulnerabilities & auth boundaries, (2) Correctness / concurrency / data integrity, (3) Architecture & reliability / operability.
**Method:** Adversarial trace of each attack surface and high-risk flow. Every claim verified against actual source (cited `file:line`), not against CLAUDE.md/docstrings. The most actionable items were manually re-checked by the coordinator.
**Baseline:** `NEEDED_FIXES.md` (2026-07-21), `NEEDED_FIXES_2026-08-02.md`, `NEEDED_FIXES_2026-08-14.md`, `TRIAGE_FINDINGS_2026-10-01.md` (resolved by PR #374).

---

## Headline

The codebase remains **mature and defense-in-depth.** **No Critical or High-severity issues were found** — consistent with every prior triage. All three audits independently concluded that the classic attack and failure classes (IDOR, JWT confusion, CSRF, SSRF, prompt injection, upload abuse, transaction/lock races in the workout/completion/allowance layers) already have explicit, correct, test-pinned mitigations.

This run surfaced **4 Medium** items (2 of them a pair that one change fixes; 1 maintainability; 1 boot-operability), **4 Low** items, and **3 informational** hardening notes. The dominant risk is now **accumulating maintainability/operability debt**, not latent crashes or exploitable holes.

| # | Severity | Area | Type | Status | Confidence |
|---|----------|------|------|--------|------------|
| 1 | Medium | Web `/checkin` holds DB tx + owner `FOR UPDATE` lock across the Bedrock call | Reliability / DoS | Carried (CLAUDE.md LP16-A "lock-across-Bedrock debt") | Confirmed |
| 2 | Medium | Web `/checkin` lost-update: equal-to-stale weight emits no UPDATE | Correctness | Carried (service.py recorded debt; native LP16-B fixed) | Confirmed |
| 3 | Medium | Per-boot leaderboard rebuild + Alembic auto-upgrade scale with data size | Operability | New | Confirmed |
| 4 | Medium | Documentation / "one authority" sprawl (180 KB CLAUDE.md + 5 fix/triage docs) | Maintainability | New (compounding) | Confirmed |
| 5 | Low | Stray `axisai-*` per-branch dirs committed to repo root, unreferenced | Maintainability | New | Confirmed |
| 6 | Low | Import-time `int(os.getenv(...))` crashes boot on malformed value | Reliability | New | Confirmed |
| 7 | Low | `wearable_sync` holds the shared concurrency slot across DB work + mid-stream commit | Concurrency hygiene | New | Confirmed |
| 8 | Low | `whoop_resource` resolves adapter outside `try` → generic 500 vs sibling routes' typed error | Error-contract | New | Confirmed |
| — | Info | `avatar_src` presign missing `expected_user_id`; offline token TTL window; CSP `style-src-attr 'unsafe-inline'` | Hardening | — | Confirmed non-exploitable |

---

## 1. [Medium] Web `/checkin` holds a DB transaction + owner row lock across the Bedrock provider call

- **Location:** `app/blueprints/tracking.py:245-295`; lock lifecycle `app/services/weekly_checkin/service.py:75-92` (`claim_submission` → "owner lock stays held until `commit_full_checkin`").
- **Impact:** On the keyed-submission path, `claim_submission` takes `lock_owner` (`FOR UPDATE`) and the lock **plus the pooled DB connection are held across `generate_checkin_feedback`** (Sonnet/Bedrock, up to ~60s + retries, ~90s with `AI_COACH_TURN_TIMEOUT`). DB-connection occupancy and a per-user row lock are tied to external provider latency. Bounded today (`@ai_concurrency_gate` caps concurrency to `AI_MAX_CONCURRENCY=4`, pool is 12, `BEDROCK_RATELIMIT` 10/h/user), so it will not exhaust the pool now — but it is the classic antipattern the capacity rules exist to forbid, and explicitly unpaid debt.
- **Confidence:** Confirmed.
- **Fix (paired with #2):** Adopt the native LP16-B ordering — `claim_submission` → `reload_locked_owner` → stage → **commit the row**, then issue the Bedrock feedback call *outside* the lock as a follow-up update. One change removes both this coupling and #2, collapsing web + native to one behavior.

## 2. [Medium] Web `/checkin` lost update — equal-to-stale weight emits no UPDATE

- **Location:** `app/services/weekly_checkin/service.py:95-107` (`reload_locked_owner` docstring: "the web `/checkin` path does not [reload]… recorded debt").
- **Impact:** The owner row is loaded *before* the `FOR UPDATE` lock is taken and not re-read under it. A concurrent check-in that committed a different weight is a lost update when the new weight equals the pre-lock stale value (no diff → no UPDATE). Deliberately pinned "byte-identical to 088d04d"; the native LP16-B path already fixed it. Correctness defect, narrow window.
- **Confidence:** Confirmed.
- **Fix:** See #1 — `reload_locked_owner` under the lock closes it.

## 3. [Medium] Per-boot full leaderboard rebuild + per-boot Alembic auto-upgrade scale boot cost with data size

- **Location:** `app/db_init.py:38` (`lb_rebuild()` in `init_database`), `app/db_init.py:73-87` (per-boot `upgrade()`).
- **Impact:** Every web container start runs the full Alembic upgrade and then deletes+repopulates the leaderboard Redis sorted-sets from Postgres (O(users)), synchronously in `create_app` before gunicorn binds. No serving-time partial reads, but **boot latency grows with the user base**; a crash-looping container re-runs both each time, and a large data migration can exceed the 20s healthcheck `start-period` → false rollback. Documented single-container tradeoff (R6 blue/green is the planned exit), but the single biggest boot-time operability cliff as the app grows.
- **Confidence:** Confirmed.
- **Suggested direction:** Gate `lb_rebuild()` behind an explicit opt-in / move it off the boot path (background job or CLI), and prefer running migrations as a discrete deploy step (the `FITX_DB_AUTO_UPGRADE=0` escape hatch already exists) once the user base makes boot-time upgrades risky.

## 4. [Medium] Accumulating documentation / "one-authority" sprawl

- **Location:** repo root — `CLAUDE.md` (180 KB), `NEEDED_FIXES.md` + `NEEDED_FIXES_2026-08-02.md` + `NEEDED_FIXES_2026-08-14.md` + this file, `TRIAGE_FINDINGS_2026-10-01.md`, `SEC001_…md` (80 KB), `SEC005_…md`.
- **Impact:** The intended architecture and dozens of deliberate tradeoffs live almost entirely in a single 180 KB CLAUDE.md plus several overlapping fix/triage/remediation files. The *code* convention of "one canonical authority per concern" is strong and AST-gated; the *documentation* of it is the opposite — sprawling and partly historical. This is the practical ceiling on onboarding and on knowing "the one authority" without reading everything. No single failure, but the compounding risk.
- **Confidence:** Confirmed.
- **Suggested direction:** Split CLAUDE.md into a slim index + per-domain docs under `docs/` (already the home of most design records); archive resolved `NEEDED_FIXES_*`/`TRIAGE_*`/`SEC00*` under `docs/history/` so root carries only live authority.

## 5. [Low] Stray `axisai-*` per-branch directories committed to repo root

- **Location:** `axisai-d1-migration-reconcile-01/INTEGRATION.md`, `axisai-d1-r2-test-seam/REMEDIATION.md`, `axisai-mon02-be03b-r2-integration/INTEGRATION.md`, `axisai-priv-app-d1-be-01/IMPLEMENTATION.md`.
- **Impact:** Four git-tracked, **unreferenced** (grep across `*.py/*.yml/*.sh/*.toml` finds zero references — verified) per-branch handoff/implementation docs left at the repo root — leftover worktree/branch artifacts. Pure clutter that implies in-flight branches whose status is ambiguous against `main`.
- **Confidence:** Confirmed.
- **Fix:** Delete, or move under `docs/history/`.

## 6. [Low] Module-import-time env reads bypass the tolerant parser (crash-at-import on malformed value)

- **Location:** `app/services/premium.py:31` (`FREE_WEEKLY_AI_CHATS`), `app/services/foodcache.py:45` (`FOODCACHE_REDIS_TTL`), `app/jobs/__init__.py:27,29` (`WORKER_HEARTBEAT_TTL`, `FATSECRET_STATUS_TTL`); also `app/services/ai_gate.py:33-51`, `app/services/ai_spend_guard.py:73`.
- **Impact:** These values are read at *import* with bare `int(os.getenv(...))` rather than through `app/config.py`'s tolerant `_env_int` fallback, so a malformed operator value (e.g. `FREE_WEEKLY_AI_CHATS=foo`) raises `ValueError` during import and fails boot **before** `configure_app`'s structured validation/logging runs — a sharper, harder-to-diagnose failure than the documented "fall back to default" contract. (For the `ai_gate` capacity values this fail-closed behavior is arguably intentional; the quota/TTL ones are not.)
- **Confidence:** Confirmed.
- **Fix:** Route the quota/TTL reads through the same tolerant `_env_int` helper (clamp + default + one warning line) used by `app/config.py`.

## 7. [Low] `wearable_sync` holds the shared concurrency slot across DB work and a mid-stream commit

- **Location:** `app/blueprints/wearables.py:158-159` → `app/services/wearables/sync.py:83-107` → `app/services/wearables/adapters.py:117-171`.
- **Impact:** Violates the capacity invariant ("slot wraps only the network round-trip; no DB write while holding a permit") that the sibling `wearable_callback` route deliberately honors. `wearable_sync` wraps the entire `sync_provider_day` inside one `blocking_concurrency_slot()` spanning 3 sequential provider fetches (each `timeout=10`, each with a 401→force-refresh retry that `commit()`s tokens mid-stream) plus `_apply_records` upserts + a final commit. Under a provider latency spike, 8 concurrent syncs can each hold a slot 30–80s, worsening the exact exhaustion the gate prevents. Not a data-integrity bug (the IntegrityError retry path is correct) — concurrency hygiene only.
- **Confidence:** Confirmed (deviation); Plausible (practical impact).
- **Fix:** Narrow the slot to wrap only each provider HTTP call; do token persistence and `_apply_records` DB work outside the permit, mirroring `wearable_callback`.

## 8. [Low] `whoop_resource` resolves the adapter outside its `try/except` → generic 500 on an unconfigured provider

- **Location:** `app/blueprints/wearables.py:173-191` (`get_adapter("whoop")` at line 174, before the first `try` at line 178).
- **Impact:** `get_adapter` raises `WearableError` when WHOOP is unconfigured/disabled. `wearable_login`/`wearable_callback` both catch `WearableError` and return a clean 503/404; `whoop_resource` does not wrap this call, so an unconfigured WHOOP yields an unhandled exception → generic **500** instead of the typed service-unavailable the sibling routes give.
- **Confidence:** Confirmed.
- **Fix:** Move `get_adapter("whoop")` inside a `try` that returns the same typed 503 as the sibling routes.

---

## Informational (verified non-exploitable — hardening only)

- **`User.avatar_src` presigns `profile_picture_key` without `expected_user_id`** (`app/models.py:294`). The one presign call lacking the owner argument every other call carries. Harmless — the key comes from the trusted DB row of the user being rendered (never attacker-controlled) and avatars are intentionally visible within the social graph — but inconsistent; add the argument for defense-in-depth uniformity.
- **Offline mobile token validation window.** A credential change made directly at Cognito (outside the app's reset flow) isn't observed until the stored access token's TTL elapses or the local family is revoked. Documented, bounded tradeoff; the credential-epoch fence covers the app's own reset path.
- **CSP keeps `style-src-attr 'unsafe-inline'`.** Documented accepted tradeoff (dynamic width styles); narrow vector, `<style>`/`<script>` injection both still require nonces.

---

## Areas audited and confirmed well-defended (not findings)

- **Auth / JWT** (`cognito_jwt.py`, `auth_contract.py`, `mobile_auth.py`): RS256 signature, `exp` (leeway pinned 0), issuer, audience, `token_use="access"` all enforced; ID tokens rejected as credentials; unknown-`kid` JWKS refresh single-flight + cooldown-gated; refresh rotation with replay/grace detection, credential-epoch fence, deleted-identity tombstone fence.
- **CSRF** (`app/hooks.py`): two-layer (Origin/Referer + per-session synchronizer token via `compare_digest`); mobile Bearer bypass correct; no GET route mutates.
- **SSRF** (`menu_remote.py`, `menu_fetch.py`): URL grammar validation, public-IP allowlisting, per-hop IP pinning with post-connect peer verification, redirect re-validation, HTTPS-downgrade block, byte/time/request budgets, credential-free subprocess isolation.
- **S3 / media** (`s3_helper.py`, `image_sanitization.py`): owner-segmented keys, path-segment (not substring) ownership checks, strict key-grammar fail-closed delete, server-selected bucket, pixel re-encode + metadata strip + decompression-bomb/animation blocks; all other presign callers pass `expected_user_id`.
- **IDOR / injection:** every ID-parameterized route scopes to the owner; no string-interpolated SQL, no `eval`/`exec`/`render_template_string`/`os.system`; the only `subprocess` is the sandboxed menu-fetch worker.
- **Concurrency/transactions:** lock ordering consistent and documented; DB-level unique constraints / conditional UPDATEs are the race arbiters; provider calls wrapped in `blocking_concurrency_slot()` (except #1/#7); prior triage fixes (quest-commit-on-None, water-funnel toggle via durable `WaterLog.quest_fired`, after_commit leaderboard sync, column-read-under-lock in `award_xp`) verified in place.
- **Startup/migration:** exactly one Alembic head (`c6f8b0d2e4a1`); all unguarded `create_table` migrations precede the fresh-schema stamp; later ones carry `has_table` guards; boot invariants invoked; worker sets `FITX_SKIP_DB_INIT=1` (no web/worker migration race).
- **CI PG coverage:** all 55 `*_pg.py` modules referenced in `.github/workflows/ci.yml`; none orphaned.
- **Resource management:** every raw `engine.connect()` closed in `with`/`finally`; migration engine disposed; concurrency semaphores released in `finally` with double-release protection on the SSE path; DB pool explicitly sized from thread count with a boot invariant.

---

## Recommended order of fixes

1. **#1 + #2 together** — one change (web `/checkin` adopts native LP16-B ordering) pays down a Medium reliability coupling *and* a Medium correctness lost-update, and unifies two authorities.
2. **#6** — small, mechanical, removes a sharp boot-failure mode.
3. **#7 + #8** — localized wearables hygiene/error-contract fixes.
4. **#5** — delete/relocate stray dirs (trivial).
5. **#3** — plan the boot-cost mitigation before the user base forces it.
6. **#4** — schedule documentation consolidation; ongoing.
