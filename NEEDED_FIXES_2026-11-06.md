# FitX / AxisAI — Needed Fixes (Triage Report)

**Date:** 2026-11-06
**Scope:** 3 parallel read-only deep-dive audits — (1) Security, (2) Backend
correctness / concurrency / money, (3) Structure / Frontend / API contract /
migrations / CI.
**Method:** Each reviewer traced the actual code paths and verified every claim
against source (`file:line`), **not** against `CLAUDE.md` (which is large and may
overstate guarantees). The two most actionable items were re-verified by hand
before inclusion.
**Commit reviewed:** `e7bfa13` (`main` merge of #486, D1 Launch V1) on branch
`claude/amazing-dijkstra-zagjky`.

---

## Headline

The codebase remains **mature, hardened, and defense-in-depth.** All three
reviewers independently reported the same signal: the strong guarantees
`CLAUDE.md` claims (lock ordering, exactly-once via DB constraints, SSRF defense,
JWT hardening, expand/contract migrations, single-flight frontend reads) are
**actually implemented in code and hold up under adversarial inspection.**

- **No Critical, High, or Medium severity issues were found** on any of the three
  surfaces.
- Findings are **2 Low (actionable)** + **4 Low / latent** + **2 informational
  config-drift invariants** worth a guard. These are hardening / resilience /
  housekeeping improvements, not active exploits or live data bugs.

| # | Severity | Area | Type | Confidence |
|---|----------|------|------|------------|
| 1 | Low | `today.js` couples two independent reads into one failure path | Frontend resilience | Confirmed |
| 2 | Low | 4 stray tracked top-level `axisai-*` dirs are cruft | Structure / housekeeping | Confirmed |
| 3 | Low (latent) | Leaf write helpers call `db.session.commit()` on the shared scoped session | Backend footgun | Confirmed |
| 4 | Low (by design) | `ai_allowances.reserve` permanently refuses a key whose hold expired | Metering edge case | Confirmed |
| 5 | Low (footgun) | `bind_for_dispatch` guard depends on an unasserted `RESERVATION_TTL <= ABANDON_AFTER` invariant | Config coupling | Confirmed |
| 6 | Low | CSP keeps `style-src`/`style-src-attr 'unsafe-inline'` | Security hardening (accepted) | Confirmed |
| 7 | Info | Rate-limit / deep-health trust depends on single-proxy nginx XFF | Config-drift invariant | Confirmed |
| 8 | Info | RQ job args pickled in Redis (deployment-contained) | Security invariant | Confirmed |

> The three audits map onto the prior `TRIAGE_FINDINGS_2026-10-01.md` (PR #374)
> and `NEEDED_FIXES.md` lineage. The #374 remediations (food-search overload
> `503`, path-keyed `no-store`, `UserSession`/`TrainingPlan` `id` tiebreaks,
> `fitx_ref` secure cookie, `auth.js` storage try/catch) were spot-verified as
> present. No prior finding has regressed.

---

## Needed fixes (ranked)

### 1. [LOW] `today.js` couples the calorie and water reads into one failure path
- **File:** `static/today.js:145-156` *(verified)*
- **Problem:** `loadStatus()` issues both reads under a single `Promise.all` with
  one `.catch`:
  ```js
  function loadStatus() {
    Promise.all([json('/meal-log/today'), json('/water')])
      .then(function (res) { renderNutrition(res[0]); renderWater(res[1]); })
      .catch(function () {
        // Leave both values as "—" and say so. Unknown is not zero.
        var err = $('today-status-error');
        if (err) err.hidden = false;
      });
  }
  ```
  If `/water` fails but `/meal-log/today` succeeded (or vice-versa), **both**
  values are blanked to "—" and the status-error banner is shown. The file's own
  header comment and the `loadStatus`/`loadProgress` split emphasise independent
  failure, but *within* `loadStatus` the two reads are coupled.
- **Impact:** A transient hydration outage hides a calorie total that was in fact
  read successfully. **Not a data-correctness bug** — unknown renders as "—",
  never `0`, which is the right invariant — purely a resilience gap vs. the file's
  stated intent.
- **Fix:** Settle the two reads independently (`Promise.allSettled`, or two
  separate `json().then().catch()` chains, each rendering/failing its own
  section). Matches the independent-section-state pattern already used in
  `nutrition.js`.

### 2. [LOW] Four stray tracked top-level directories are cruft
- **Files:** `axisai-d1-migration-reconcile-01/INTEGRATION.md`,
  `axisai-d1-r2-test-seam/REMEDIATION.md`,
  `axisai-mon02-be03b-r2-integration/INTEGRATION.md`,
  `axisai-priv-app-d1-be-01/IMPLEMENTATION.md` *(verified: each is a single
  markdown note; `grep` of `app/ scripts/ tests/` for the dir names is empty)*
- **Problem:** Leftover artifacts of parallel worktree / integration branches,
  committed into the main tree and referenced by no code. They clutter the repo
  root and blur "module vs. note".
- **Fix:** Move the notes under `docs/` (if the history is worth keeping) or
  delete them.
- **Do NOT touch:** `release/completion-capabilities` is **not** cruft — it is
  read at runtime by `scripts/r6_deploy_transaction.py:153` (`CAPABILITY_SOURCE`).

### 3. [LOW / latent] Leaf write helpers call `db.session.commit()` on the shared scoped session
- **Files:** `app/services/workout_session/queries.py:347,364,417,441`
  (`heartbeat`, `touch_active`, `advance_checkpoint`, `terminalize_abandon`),
  `app/services/hydration.py:58` (`set_today_count`),
  `app/services/mobile_training_generation/store.py:195`
  (`mark_attempt_possible`) *(hydration.py:58 verified by hand)*
- **Problem:** Each issues `db.session.commit()` directly on the request's shared
  scoped session, committing **whatever else is pending** in that session. On
  every path traced today these are leaf operations with no other pending ORM
  writes, so the behaviour is correct **now**. But it is a standing footgun: a
  future caller that stages ORM changes *before* invoking one of these helpers
  would have that work committed prematurely (partial state on a later failure).
- **Could not fully rule out:** the `attempt_observer` / `mark_attempt_possible`
  True-marker commit during native metered generation — the design comments
  assert a clean session at `_before_physical_attempt()`, but the full native
  generation request was not traced end-to-end to confirm no pending writes exist
  at that point. If a caller ever left pending writes there, the marker commit
  would also commit them.
- **Fix:** Add an `assert not db.session.dirty` (or a small "no pending writes"
  guard) at the top of these leaf helpers, and/or a targeted test for the metered
  generation flow. Cheap insurance against a whole class of future partial-state
  bugs.

### 4. [LOW / by design] `ai_allowances.reserve` permanently refuses an idempotency key whose hold expired
- **File:** `app/services/ai_allowances/service.py:339-350` *(reported)*
- **Problem:** A replay of a key whose `RESERVED` hold already expired is settled
  `EXPIRED` and returned as a terminal replay (`may_execute == False`), so that
  exact key can never obtain a new unit. This is **deliberate** anti-double-charge
  conservatism (a provider run can exceed the TTL), and clients are expected to
  mint a fresh key. Flagged only so the behaviour is a documented choice, not a
  surprise: a legitimately slow action that times out exactly at the TTL boundary
  cannot be retried under the same key.
- **Fix:** None required. Confirm the mobile/native clients mint a new
  idempotency key on retry rather than reusing the old one.

### 5. [LOW / footgun] `bind_for_dispatch` guard depends on an unasserted cross-module invariant
- **File:** `app/services/mobile_training_generation/allowance.py:120`; constants
  `RESERVATION_TTL` (`ai_allowances/service.py:35`) and `ABANDON_AFTER`
  (`mobile_training_generation/store.py:42`), both currently 15 min *(reported)*
- **Problem:** The fail-closed guard refuses dispatch when
  `hold.expires_at > now + store.ABANDON_AFTER`. This is safe **only while**
  `RESERVATION_TTL <= ABANDON_AFTER`. The two constants live in different modules
  with no shared assertion; raising `RESERVATION_TTL` above `ABANDON_AFTER` would
  make **every** metered dispatch refuse with `AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE`.
  A fail-closed footgun, not a correctness bug.
- **Fix:** Add a boot-time or import-time assertion that
  `RESERVATION_TTL <= ABANDON_AFTER`, co-located with one of the constants, so a
  future change fails loudly instead of silently disabling metered generation.

### 6. [LOW / accepted] CSP retains inline-style allowances
- **File:** `app/hooks.py:65-67` *(reported; consistent with the documented
  "accepted tradeoff" in the 2026-09-30 triage)*
- **Problem:** `style-src 'unsafe-inline'` and `style-src-attr 'unsafe-inline'`
  remain for legacy-browser support. This leaves a narrow CSS-injection /
  UI-redress / selector-exfil vector **only if** a separate HTML-injection sink
  ever exists (none was found; script injection is fully closed — nonce + SRI
  only).
- **Fix:** When legacy-browser support can be dropped, move dynamic widths to CSS
  custom properties and remove `style-src-attr 'unsafe-inline'`. Low priority.

### 7. [INFO] Rate-limit & deep-health trust depends on the single-proxy nginx invariant
- **Files:** `app/config.py:327` (`ProxyFix(x_for=1)`), `nginx.conf:170`
  (`$proxy_add_x_forwarded_for`) *(reported)*
- **Problem:** Rate-limit keying and `/health?deep=1` gating use
  `request.remote_addr` behind `ProxyFix(x_for=1)`. This is **correct as
  configured** — nginx appends the real client IP and the web container is
  loopback-bound, so a client-supplied `X-Forwarded-For` cannot spoof
  `remote_addr`. But it is a standing invariant: adding a second proxy hop, or
  changing the nginx XFF directive, would silently enable rate-limit bypass and
  deep-health exposure.
- **Fix:** Add a config-drift guard / deploy check asserting the single-hop XFF
  assumption (e.g. the deploy pipeline already `sed`-checks nginx CSP; extend it
  to the XFF directive).

### 8. [INFO] RQ job arguments are pickled in Redis (deployment-contained)
- **File:** `app/jobs/__init__.py:9` *(reported)*
- **Problem:** RQ stores job arguments as pickle. Anyone who can write to the
  Redis job list achieves RCE in the worker. Contained today because Redis is
  bound to `127.0.0.1:6379` with no exposed port and is not internet-reachable.
- **Fix:** None required while Redis stays loopback-only. Keep it that way; never
  expose Redis without auth + network isolation. (Also note: `FATSECRET_ALLOW_INSECURE=1`
  at `config.py:308` is a cleartext escape hatch — boot-enforced off; ensure it is
  never set in any prod/staging `.env`.)

---

## Verified robust (the boundaries most likely to be broken, that aren't)

Recorded so a future audit need not re-derive them, and so none of these is
mistaken for an open gap.

**Security**
- **JWT** (`cognito_jwt.py`): RS256 pinned (no alg-confusion), `exp` essential,
  leeway pinned to 0 on both web and mobile with the retired skew knob *rejected
  at boot*, `token_use=access` enforced, `iss`/`aud` checked, unknown-kid JWKS
  refresh single-flight + cooldown (closes a thread-park DoS).
- **Mobile auth** (`mobile_auth.py`): refresh rotation with reuse detection +
  grace window + family revoke; credential-epoch fence closes the
  login-in-flight-vs-password-reset race (write-then-check under row lock);
  256-bit opaque credentials, SHA-256 stored, constant-time compare.
- **IDOR**: diary / social / S3 all owner-scoped; workout-share friend fan-out
  validates `get_friend_ids` before sending (`training.py:487-493`); AI-coach
  LLM-supplied S3 key enforces `expected_user_id`; `_key_belongs_to` is
  segment-equality, not substring.
- **SSRF** (`menu_remote.py`): all resolved IPs must be public, connection pinned
  to the literal IP with peer re-verification (anti-rebind), cookie-less per-hop
  sessions, HTTPS-downgrade blocked, byte/time/redirect budgets, credential-less
  subprocess isolation.
- **CSRF/CSP**: two-layer CSRF; `mobile_api` correctly Bearer-only exempt (no
  ambient cookie consumed); no state-changing GET routes; `script-src` nonce +
  SRI only.
- **Crypto/config**: `SECRET_KEY` required in prod, `COGNITO_TOKEN_ENC_KEY`
  required at boot, secure cookies; NUTR HMAC tokens domain-separated +
  owner-bound-via-MAC + TTL/skew; Apple IAP uses the official verifier with
  online cert checks + full post-verify + `appAccountToken` owner binding.

**Backend / concurrency / money**
- **Lock order** operation → user → plan is real and consistent
  (`plan_owner_lock.py:67`, `plan_replacement.py:213-221`, `store.commit_plan`
  `store.py:379-390` takes the lock *before* the `get_active_plan` check). The
  "two plans for one owner" cross-transport race is genuinely closed.
- **Exactly-once** for workout completion (`uq_workout_completion_day` /
  `uq_pump_check_day`; `IntegrityError` path treats only a *verified* violation as
  replay) and checkpoints (replay-before-mutate + single CAS `advance_checkpoint`
  `WHERE status='active' AND checkpoint_revision=:base`). `award_xp` /
  `_claim_quest` / `log_activity` are genuinely commit-free, so the single commit
  is atomic.
- **Nutrition double-counting** warning confirmed clean — day-view and native
  surface aggregate `MealLog` only, never summed with the diary builder.
- **Time handling**: no `date.today()` / `utcnow().strftime` / `utcnow().date()`
  bypasses; completion claims and `completed_days` use Istanbul `date_key`, never
  `created_at` bucketing.
- **AI fallback B-rule** (`ai_stream.py:299-318`): OpenAI fallback only when no
  delta emitted AND no tool ran; refusal / spend-ceiling / deadline never fall
  back; retries charged per physical attempt; timeouts not retried.
- **Apple money path**: monotonic signed timestamps, terminal revocation,
  lease+generation fencing, provider I/O released before DB locks everywhere.

**Structure / frontend / contract / migrations / CI**
- **PG-concurrency CI registration** (the `CLAUDE.md` "silently never runs"
  hazard): CLEAN — all 57 `tests/test_*_pg.py` modules are referenced in
  `ci.yml`; `deploy.yml:35` gates on full CI success, so a failing PG job blocks
  deploy.
- **Alembic**: single head (`c6f8b0d2e4a1`), no divergence.
- **expand/contract gate**: 60 migrations, 46 baseline-pinned, all 14 forward
  migrations declare `expand_contract`; gate script is AST-based, fail-unknown-as-
  unsafe.
- **Table-creating migrations past the create_all boot point** carry `has_table`
  guards (re-runnable).
- **XSS**: consistent `esc()` / DOMPurify escaping everywhere incl. `data-f` JSON
  attrs and the markdown fallback; **error-as-zero avoided** (renders "—").
- **Frontend concurrency**: generation tickets + single-flight + `AbortController`
  consistently applied and correct; **CSRF**: no `sendBeacon`/XHR state-changing
  calls.
- **Mobile error envelope** consistent; catch-all returns 503 retryable (not a
  session-dropping 401); feature routes catch their own faults with typed 503s.
- **PII logs**: recovery uses `mask_email`; no raw email/username/password in log
  calls. Retired flags are documented no-ops or actively boot-refused.

---

## Observations (not confirmed bugs — candidates for a dedicated pass)

- **71 `except Exception:` blocks in `app/`.** Spot-checked ones were legitimate
  best-effort cleanups (rollbacks, side-effect isolation, CDN fallbacks); no
  specific one was isolated swallowing a request-path fault. Worth a dedicated
  sweep if time allows.
- **Large modules** (none a true god-file, but split candidates):
  `app/models.py` (2096), `app/services/ai_coach.py` (1637),
  `app/services/coach_plan_tools/grounding.py` (1299),
  `app/blueprints/social.py` (1222), and the biggest frontend file
  `static/nutrition.js` (2899, carries today/plan/diary/water/search/history).
- **Known / documented deferred defect** (not re-reported): coach-widget
  menu-dish "Add" sends slot tokens `/meal-log` rejects with 400 (`CLAUDE.md`
  NUTR-PR4).

---

## Suggested order of work

1. **#1** (`today.js` read coupling) — small, self-contained frontend fix with a
   clear correct pattern already in the repo.
2. **#3** (`assert not db.session.dirty` guards) — cheap insurance against a whole
   class of future partial-state bugs; closes the one thing the backend reviewer
   could not fully rule out.
3. **#5** (TTL ≤ ABANDON_AFTER assertion) and **#7** (nginx XFF drift guard) —
   one-line invariants that turn silent footguns into loud failures.
4. **#2** (delete/relocate stray `axisai-*` dirs) — housekeeping.
5. **#4, #6, #8** — accepted/by-design; confirm the documented assumptions still
   hold, no code change required.

_No Critical/High/Medium work is outstanding. This report lists hardening and
housekeeping only._
