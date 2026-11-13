# Triage Findings — 2026-11-13

Automated deep-dive triage of the FitX (fitness-coach) backend at commit `dbac1fd`
(branch `claude/amazing-dijkstra-j3r7t2`). Three independent read-only audits ran in
parallel — **security**, **correctness/concurrency**, and **reliability/structure** —
each tracing real control/data flow. Every concrete finding below was then
re-verified by hand against the cited source before being written here.

No files were modified. This document is a fix list, not a changelog.

## Bottom line

This is an unusually disciplined codebase. The audits found **no confirmed
Critical or High exploitable security vulnerability**, and **no confirmed Critical
or High correctness/money defect**. Single-authority modules, AST-enforced "no
second writer" guards, DB-level unique claims, conditional CAS updates, documented
lock ordering, and typed failure modes are applied consistently. The real gaps are
narrow and cluster in the newest, deepest subsystem (the D1 consent / allowance /
provider-admission stack) and in one outlier write path.

The items below are worth fixing, ranked by priority. Each is tagged with a
verification status: **CONFIRMED** (traced and reproduced in source) or
**LATENT** (correct today, breaks under a plausible config/operational change).

---

## Severity summary

| # | Finding | Severity | Status | Location |
|---|---------|----------|--------|----------|
| 1 | First-supplement bonus lost on split-commit crash | Medium | CONFIRMED | `app/services/supplement_cabinet.py:43-73` |
| 2 | DB pool invariant undercounts consent layer's 2nd connection | Medium (High if misconfigured) | LATENT | `app/services/ai_gate.py:260-282` |
| 3 | FatSecret 24h scrub degrades to daily granularity without worker | Medium (compliance) | CONFIRMED | `app/jobs/tasks.py` + `worker.py:29` |
| 4 | Image verification fails open in debug mode | Low (dev-only) | CONFIRMED | `app/services/validators.py:54-56` |
| 5 | `ai_stream` producer thread lacks app-context/session cleanup | Low (latent trap) | LATENT | `app/services/ai_stream.py:167` |
| 6 | Pillow `MAX_IMAGE_PIXELS` mutated as a process global | Low | CONFIRMED | `app/services/validators.py:58` |
| 7 | `bedrock_health.probe()` cache not held during probe | Low | CONFIRMED | `app/services/bedrock_health.py:88-101` |
| 8 | Doc drift: `ai_consent.POLICIES` populated vs CLAUDE.md notes | Info | NEEDS RECONCILE | `app/services/ai_consent.py:49` |

---

## 1. First-supplement bonus is lost (and a 500 returned) on a split-commit crash — Medium, CONFIRMED

**Where:** `app/services/supplement_cabinet.py:43-73` (`create_supplement`)

**Evidence:** The function performs **three separate commits** for one logical
operation, with no `try/except` around the second and third:

```python
prior_count = Supplement.query.filter_by(user_id=user_id).count()
db.session.add(supp)
db.session.commit()                 # (A) row saved

if prior_count == 0:
    award_xp(user_id, 25)
    db.session.commit()             # (B) first-supplement bonus

log_activity(user_id, "new_supplement", ...)
db.session.commit()                 # (C) activity row
```

**Why it's wrong:** The bonus-decision race itself is correct (`prior_count` is read
under the owner `FOR UPDATE` lock). The defect is atomicity across (A)→(B). If a
transient DB error or process crash lands between (A) and (B):

- The row is already persisted, so the request returns HTTP 500 for a write that
  actually succeeded (client retries, sees a dup or a confusing error).
- The 25-XP first-supplement bonus is **permanently lost**: on retry `prior_count`
  is now `1`, the `if prior_count == 0` branch never re-fires, and nothing makes
  the bonus idempotent or recoverable.

This module is the outlier. Every other orchestrator in the codebase
(`complete_workout`, `consume_referral`, weekly check-in, meal log) keeps the
mutation + XP + quest + activity in **one** transaction, exactly because
`award_xp` / `log_activity` are already commit-free, session-add-no-commit helpers.

**Fix:** Collapse to a single commit — add the supplement, award the XP (when
`prior_count == 0`), and log the activity, then `db.session.commit()` **once**.
`complete_quest_for_user` can remain its own commit (it is a separately-committing
wrapper), or be converted too. This is low-risk because the helpers already assume
caller-owned transactions.

---

## 2. DB-pool safety invariant does not account for the consent layer's second pooled connection — Medium (High under a plausible misconfig), LATENT

**Where:** invariant at `app/services/ai_gate.py:260-282` (`_db_pool_problems`);
root cause at `app/services/ai_consent.py:146` + `app/services/ai_pipeline.py:208-218`

**Evidence:** The boot invariant only checks:

```python
capacity = size + overflow
if capacity >= WEB_THREADS:   # assumes 1 connection per request thread
    return []
```

But an in-flight AI request holds **two** pooled connections for the full provider
latency:

1. The request's own `db.session` connection stays checked out — `ai_pipeline`
   does no `commit`/`rollback` before entering the provider loop.
2. The D1 consent dispatch opens a **second** connection (`db.engine.connect()`,
   READ COMMITTED, `ai_consent.py:146`) and holds it across the blocking SDK call
   (`dispatch`, driven from `ai_provider_call.py`).

So worst-case connection demand is `cheap_threads × 1 + ai_threads × 2`. At shipped
defaults (`AI_MAX_CONCURRENCY=4`, `DB_POOL_SIZE=WEB_THREADS=8`,
`DB_POOL_MAX_OVERFLOW=4` → capacity 12): `4 + 4×2 = 12` — it fits **exactly**, safe
only by coincidence.

**Why it matters:** Lowering `DB_POOL_MAX_OVERFLOW`, raising `AI_MAX_CONCURRENCY`,
or the documented model-concurrency-excess path pushes demand past capacity with
**no boot guard to catch it**. The symptom is the worst possible one: `/health`'s
`SELECT 1` (`app/__init__.py:200`, hit by the container HEALTHCHECK and the deploy
gate) blocks up to `DB_POOL_TIMEOUT_SECONDS` waiting for a connection →
**false deploy rollback / restart loop** under load, i.e. an outage triggered by
the very mechanism meant to prevent one.

**Fix:** Tighten the invariant so it fails boot when the pool can't cover the
consent layer's second connection, e.g. require
`capacity >= WEB_THREADS + AI_MODEL_MAX_CONCURRENCY` (or more precisely
`WEB_THREADS + max_concurrent_ai_requests`). Alternatively, release the request's
own session connection before the provider loop so each AI request holds only one.

---

## 3. FatSecret 24h raw-value scrub degrades to daily granularity when the optional worker is down — Medium (compliance), CONFIRMED

**Where:** scrub enqueued every 300s at `worker.py:29` (`RETENTION_INTERVAL_SECONDS`)
→ `app/jobs/tasks.py:102` (`expire_provider_nutrition`); worker-less fallback via
`run_daily_maintenance` → `_provider_retention_pass` (`tasks.py:144-146,149-175`),
dispatched on the request path by `app/hooks.py:318-319` (`dispatch_background`).

**Evidence & nuance:** The FatSecret written-permission boundary requires raw
per-item provider macros to be scrubbed within ≤24h. The design meets this with a
22h cutoff (2h margin) scrubbed **every 300s** by the worker's heartbeat thread.

The worker is documented as *optional* (jobs otherwise fall inline via
`enqueue_or_run`). For retention specifically there **is** a worker-less path —
`dispatch_background` runs `run_daily_maintenance` on a short-lived daemon thread
when no queue is present, and that maintenance includes `_provider_retention_pass`.
So the earlier characterization that the scrub "silently never runs" is **not**
accurate; it does run.

The real defect is **granularity**: without the worker, the scrub runs only **once
per day** (request-path daily maintenance), not every 300s. The 22h cutoff / 2h
margin was sized for 300s cadence. At daily cadence, an item acquired just after a
daily pass can live ≈24h until the next pass catches it at >22h — marginally
breaching the ≤24h written-permission boundary. Meanwhile `/health` reports the
worker only as a **non-gating informational** field, so this degradation is
invisible to the deploy gate.

Note: the *serving* boundary still holds regardless — readers fail closed once a
row `is_due`, so no over-retained raw value is ever *published*. Only the
*storage/retention* boundary is at risk, and only while the worker is down for ~a
day.

**Fix (pick one):**
- Add a throttled request-path valve for `expire_due` (e.g. opportunistically every
  N minutes, like the existing rollover/purge throttles), so cadence doesn't depend
  on the worker.
- Or make the worker a **hard** dependency for this compliance path and alarm on
  worker-down / on a missing `release_monitoring.record_retention_pass` metric
  (the metric already exists; wire it to an alarm).

---

## 4. Image content-verification fails open in debug mode — Low (dev-only), CONFIRMED

**Where:** `app/services/validators.py:54-56`

**Evidence:**
```python
except Exception:   # Pillow unavailable
    current_app.logger.warning("Pillow unavailable; ... failed closed.")
    if current_app.debug:
        return None      # ← skips verification
    return t("route.img_invalid")
```

**Why it's low:** Not reachable in production — `debug=False` fails closed, and
every S3 upload re-runs the hardened `image_sanitization.sanitize_image` at the
sink regardless. Blast radius is a misconfigured dev/debug deploy that also lacks
Pillow. Still worth removing because it contradicts the stated "never silently
disabled" contract.

**Fix:** Drop the `if current_app.debug: return None` escape; fail closed in all
modes (or require Pillow at boot).

---

## 5. `ai_stream` producer thread has no app context / `db.session.remove()` — Low (latent trap), LATENT

**Where:** `app/services/ai_stream.py:167` (producer daemon thread); gated by
`ai_consent.stream_dispatch` which currently raises unconditionally
(`ai_consent.py:643-646`).

**Evidence:** Under D1, `stream_dispatch` always raises, so `/ask/stream` turns
fall over to the blocking OpenAI/Haiku fallback chunked into fake deltas — the
`ai_stream.py` producer-thread / cancellation machinery **never runs today** (this
is intended per its docstring). The trap: that producer daemon starts with no Flask
app context and does **not** `db.session.remove()` on exit (the `app/jobs` module
handles this correctly; `ai_stream` does not).

**Why it's latent, not active:** Harmless while `stream_dispatch` short-circuits.
But if real Bedrock SSE streaming is ever re-qualified, the consent `_transaction`
will hard-fail without app context, and any `db.session` use would **leak a pooled
connection per turn** — pool exhaustion under streaming load.

**Fix:** Before re-enabling streaming dispatch, give the producer thread a bound app
context and a `finally: db.session.remove()`, mirroring the jobs module pattern. Add
a test that asserts no connection leak across a streamed turn. Track this as a
blocker on the streaming re-qualification work.

---

## 6. Pillow `MAX_IMAGE_PIXELS` mutated as a process global — Low, CONFIRMED

**Where:** `app/services/validators.py:58` (`Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS`)

**Evidence:** Sets a **process-global** Pillow attribute on each verification call.
This contradicts the hardened `image_sanitization.py` contract, which explicitly
avoids mutating Pillow process globals. No concrete exploit (40 MP cap is the
intended bound, and the authoritative decode happens in `sanitize_image`); this is
a robustness/consistency gap only — concurrent callers share and race the global.

**Fix:** Pass the bound locally per-decode, or centralize the one decode path, so no
process-global mutation is needed in `validators.py`.

---

## 7. `bedrock_health.probe()` cache not held during the probe — Low, CONFIRMED

**Where:** `app/services/bedrock_health.py:88-101`

**Evidence:** The "one live probe per TTL" claim is not enforced across concurrency:
the cached result is written only after the probe returns, so concurrent deep-health
checks can each fire a live ~5s Bedrock probe. Internal-network-only surface,
negligible cost, but it can briefly multiply probe traffic.

**Fix:** Single-flight the probe (hold a lock / in-flight marker for the TTL window)
so concurrent deep-health checks coalesce onto one live probe.

---

## 8. Documentation drift: `ai_consent.POLICIES` is fully populated — Info, NEEDS RECONCILE

**Where:** `app/services/ai_consent.py:49` (`POLICIES = MappingProxyType({...})`)

**Evidence:** `ai_consent.POLICIES` is populated with live disclosures
(`training.plan`, `coach.conversation`, `nutrition.plan`, `pump_check.analysis`,
each pinning Bedrock Sonnet/Haiku recipients) and gates Bedrock calls. The LP21
CLAUDE.md bullet states a `POLICIES` "stays empty, nothing activated."

**Caveat / why only Info:** There are multiple `POLICIES`-named symbols in this
stack (the LP21 note is about **operation-fencing** policy, which may be a different
dict from the **disclosure-consent** `POLICIES` verified here). This is flagged for
a maintainer to reconcile the docs, **not** asserted as a code defect — a reader
trusting the LP21 wording could mis-model which consent enforcement is live.

**Fix:** Clarify in CLAUDE.md / docs which `POLICIES` each note refers to, and state
plainly that `ai_consent.POLICIES` (disclosure consent) is active in V1.

---

## Surfaces audited and found solid (no action needed)

These were traced specifically and verified to uphold their documented invariants —
recorded so a future reader knows they were checked, not skipped:

- **Access control / IDOR:** `social.py` (every route friendship/owner-scoped, whole
  blueprint closed pre-auth by `launch_social`), `nutrition/diary.py` (`db.session.get`
  immediately owner-checked + `with_for_update` re-check), profile gallery,
  notifications, gamification, wearables, food, tracking — all scoped to
  `current_user.id`; mobile services scoped to `g.mobile_user.id` with HMAC public-id
  tokens; `s3_helper` presign/get/delete enforce `expected_user_id`.
- **Auth / JWT:** single RS256 validator (`cognito_jwt.py`) with issuer/aud/`token_use`
  (access-vs-id) enforced, JWKS forced-refresh single-flight + cooldown; `auth_contract`
  leeway pinned to 0; `mobile_auth`/`auth_middleware` Bearer-only with sub↔user
  cross-check, refresh-reuse detection, credential-epoch fence, deleted-identity
  tombstone resurrection guard.
- **SSRF:** `menu_remote.py` is textbook — strict URL validation, private/reserved/
  link-local/special v4+v6 rejection, all-answers-public DNS, IP-pinned adapter with
  connected-peer verification (anti-rebinding), per-hop cookieless sessions, redirect
  re-validation + HTTPS-downgrade block, byte/time budgets, isolated credential-free
  subprocess worker.
- **Injection:** all raw SQL parameterized; `db_init` f-string table names are internal
  constants; no `eval`/`exec`/`pickle` of untrusted data; subprocess uses argv lists.
- **CSRF:** `_csrf_protect` dual-layer (Origin/Referer + per-session token), write
  methods only, mobile Bearer correctly exempt; no state-changing GETs found.
- **Money / entitlement:** Apple subscriptions + reconciliation + `subscriptions`
  (lease/generation fencing, env-scoped verification, revocation terminal state) and
  the `ai_allowances` ledger (DB-level unique claims, conditional-UPDATE bucket math,
  owner-lock serialization) — no revenue or free-service leakage found.
- **Concurrency primitives:** documented lock order (op row → user → plan rows)
  consistently applied; `populate_existing().with_for_update()` on freshness reads;
  checkpoint CAS is a single conditional UPDATE; `uq_*` unique indexes are the
  final race arbiter; native weekly check-in wires the `reload_locked_owner`
  lost-update fix.
- **Invariants spot-checked:** `created_at DESC, id DESC` tiebreak on all ~30 "latest
  row" selectors; single time source (no stray `date.today()` / `strftime("%d.%m")`
  in app code); session-add-no-commit helper discipline; `award_xp` reads counters as
  columns under `FOR UPDATE`.

## Known / documented accepted tradeoffs (not bugs)

Confirmed present and already documented as deliberate — listed so they are not
re-triaged as new issues:

- Web `/checkin` lost-update race and `/update-weight` no-lock path (trace-pinned).
- Lock-held-across-Bedrock in the web check-in feedback call (debt, documented).
- Planned-meal logging is not claimed exactly-once.
- `fitx_mcp` tools take `user_id` as a parameter with no self-authorization
  (mitigated by stdio-only / loopback constraint).
- CSP `style-src-attr 'unsafe-inline'` (accepted tradeoff).
- Web Today composes plan + workout-state across separate READ-COMMITTED reads
  (transient stale/flicker only; deliberately avoids `coherent_read_snapshot()`
  because it would detach the rendered `current_user`).

---

## Suggested fix order

1. **#1 (supplement split-commit)** — small, self-contained, clear correctness win;
   no schema/flag change. Good first PR.
2. **#2 (pool invariant)** — cheap boot-guard change that prevents a class of
   false-rollback outages; pairs well with a short load note.
3. **#3 (retention cadence)** — compliance-adjacent; wire the existing
   `record_retention_pass` metric to an alarm and/or add a request-path valve.
4. **#4, #6, #7** — minor hardening, batch into one cleanup PR.
5. **#5** — not actionable now; attach as a blocker to any future streaming
   re-qualification.
6. **#8** — docs-only reconciliation.

_Full per-agent write-ups (with additional traced-and-ruled-out candidates) were
produced during this triage; this file is the consolidated, hand-verified summary._
