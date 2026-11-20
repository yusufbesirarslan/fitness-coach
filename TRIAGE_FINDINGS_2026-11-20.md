# Triage Findings — 2026-11-20

Deep-dive bug/security triage of the FitX (fitness-coach) backend, run as a scheduled
review across three parallel audits:

1. **Authentication, authorization & web security** (IDOR, CSRF/CSP, JWT/session, SSRF, secrets, file upload)
2. **Data integrity, concurrency/transactions & monetization** (locks, idempotency, allowances/subscriptions, deletion, migrations, timezones)
3. **AI pipeline, coach tools, nutrition/providers & capacity** (prompt injection, tool safety, provider-switch, rate-limiting, retention)

Each audit read the actual source and verified documented `CLAUDE.md` invariants against the
code rather than trusting the docs. Severity ladder: CRITICAL / HIGH / MEDIUM / LOW.

---

## Executive summary

**The codebase is in excellent shape.** No CRITICAL, HIGH, or exploitable security issues were
found in any of the three areas. The authn/authz/web surface, the concurrency/money surface, and
the AI pipeline are all engineered to a consistently high, layered, defense-in-depth standard, and
the project's existing triage process (`NEEDED_FIXES*.md`, `TRIAGE_FINDINGS_2026-10-01.md`,
`SEC001*`, `SEC005*`) has already closed the historical bugs — re-verified as still fixed in current code.

There is **exactly one actionable finding** (MEDIUM, a concurrency-discipline deviation, not a
correctness bug) plus two cosmetic observations that need no immediate action.

| # | Area | Severity | Status |
|---|------|----------|--------|
| 1 | Wearable sync holds the shared AI concurrency slot across DB commits | **MEDIUM** | Fix recommended |
| 2 | `nutrition_plan_mutation.undo_last_change` lacks a graceful reverts-unique conflict handler | LOW (cosmetic) | Optional; flag-OFF code |
| 3 | `subscriptions.entitlement` can raise `SUBSCRIPTION_NOT_CONFIGURED` uncaught-but-handled | LOW (informational) | No action |

Plus three previously-documented **accepted tradeoffs** (see end).

---

## Finding 1 — MEDIUM: wearable sync holds the shared concurrency slot across DB writes

**Files:** `app/blueprints/wearables.py:158` → `app/services/wearables/sync.py:83-107` (commit at `sync.py:80`)

### What's wrong
`wearable_sync` wraps the **entire** `sync_provider_day(...)` call in `blocking_concurrency_slot()`:

```python
# app/blueprints/wearables.py:158-159
with blocking_concurrency_slot():
    return jsonify(sync_provider_day(current_user.id, provider, target))
```

But `sync_provider_day` does more than network I/O inside that slot:

```python
# app/services/wearables/sync.py:83-100
def sync_provider_day(user_id, provider, target_date=None):
    ...
    sleeps   = adapter.fetch_sleep(user_id, target_date)      # network — correct to gate
    workouts = adapter.fetch_workouts(user_id, target_date)   # network — correct to gate
    activity = adapter.fetch_activity(user_id, target_date, workouts=workouts)  # network
    try:
        _apply_records(...)        # db.session.add + db.session.commit()  (sync.py:80)
    except IntegrityError:
        db.session.rollback()
        _apply_records(...)        # SECOND commit — still inside the slot
```

So the slot (one of only `AI_MAX_CONCURRENCY`, default **4**, permits — **shared with heavy
Bedrock/AI model calls**) is held across the DB upsert **and** the IntegrityError retry's second
commit, not just the provider round-trips.

This violates the explicit discipline stated in `CLAUDE.md`:
> "Slot YALNIZCA ağ turunu sarsın — cache okuması/DB yazımı izin tutulurken yapılmaz."
> (The slot must wrap ONLY the network round-trip — cache reads / DB writes are not done while holding the permit.)

### Why it matters
Under the production model (1 gunicorn worker / 8 threads, 4 shared AI/scrape slots), concurrent
syncs — which the retry path explicitly exists to handle (double-tap, cron-vs-manual collision) —
each pin a scarce slot through provider I/O **plus** DB commit(s). That erodes the `ThreadReserve`
that protects `/health` and cheap routes further than the network round-trip alone would, nudging
the system toward spurious 503s / deploy-health-gate flapping under load.

**Severity is MEDIUM, not higher:** this is a discipline / defense-in-depth deviation, not a
correctness bug — the fetches dominate the hold time, so the incremental slot-hold from the commit
is modest, and `wearable_sync` is already rate-limited (`SCRAPE_RATELIMIT`).

### The codebase already shows the correct pattern
The OAuth callback directly above it keeps its DB write **outside** the slot, with a comment saying
exactly this:

```python
# app/blueprints/wearables.py:107-118
# Kapı YALNIZCA ağ alışverişini sarar; token yazımı (kısa, yerel DB işi) izni tutmaz
with blocking_concurrency_slot():
    token_data = adapter.exchange_code(code)   # network only
...
save_wearable_tokens(current_user.id, adapter.provider, token_data)  # DB write, no slot
```

`barcode.get_barcode_product` (`barcode.py:383`) and `whoop_resource` follow the same rule.

### Suggested fix
Split `sync_provider_day` into a **fetch phase** (the three `adapter.fetch_*` calls) that runs
inside the slot and a **persist phase** (`_apply_records`, including its IntegrityError retry) that
runs after the slot is released — mirroring the callback's structure. The caller in
`wearables.py` would hold the slot only around the fetch. No behavior change for the happy path;
just a shorter slot hold.

---

## Finding 2 — LOW (cosmetic): `undo_last_change` lacks a graceful reverts-unique conflict handler

**File:** `app/services/nutrition_plan_mutation/service.py` (flag-gated OFF; new C1 nutrition code)

Unlike `plan_mutation` (training), `nutrition_plan_mutation.undo_last_change` has no
`IntegrityError → UndoConflict` handler for a `uq_*_reverts` collision.

**This is not a double-undo risk.** `lock_plan_owner` serializes undos per owner, and the
byte-exact precondition (`plan.plan_data != target.after_snapshot → plan_stale`) already rejects a
second undo. The only effect of a hypothetical reverts-unique collision would be a raw 500 instead
of a clean `UndoConflict` response. The surface is **flag-OFF** (not reachable in production today).

**Action:** optional. Add the same `IntegrityError` arbitration branch `plan_mutation` uses, for
parity, before this code is ever activated.

---

## Finding 3 — LOW (informational): `subscriptions.entitlement` uncaught-but-handled config error

**File:** `app/services/subscriptions*` / `app/blueprints/mobile_subscriptions.py`

`subscriptions.entitlement` can raise `SUBSCRIPTION_NOT_CONFIGURED` (503) from `AppleAdapter(config)`
without catching it in the SQLAlchemy handler. This propagates correctly through the route's
`_ready` / allowance `_subscription` handlers and surfaces as the intended 503, so it is
**working as designed** — noted only so a future refactor doesn't mistake it for an unhandled path.

**Action:** none.

---

## Accepted tradeoffs (previously documented; re-verified, no action)

- **CSP `style-src-attr 'unsafe-inline'`** (`app/hooks.py:67`) — kept for dynamic inline
  `style="width:..%"` progress bars. A narrow CSS-injection vector only; full `<style>` injection is
  already nonce-blocked. Documented accepted tradeoff.
- **Login enumeration via `UserNotConfirmedException`** — gated behind a correct password with
  Cognito `PreventUserExistenceErrors` on, and rate-limited. Documented accepted tradeoff.
- **`Cache-Control: private, no-store` coverage** — already extended to private JSON endpoints in
  `TRIAGE_FINDINGS_2026-10-01` (#2). A few web JSON readers may still lack it; exploitability is
  minimal (authenticated `fetch()`es, shared-device bfcache only). No change warranted beyond the
  existing per-blueprint shared-guard pattern.

---

## Coverage — what was verified CLEAN

Recorded so the scope of this review is auditable.

### Authentication / authorization / web security
- **IDOR:** web mutations consistently scope `filter_by(id=…, user_id=current_user.id)` or explicit
  owner guards (social, supplements, profile gallery, nutrition diary, notifications). Mobile tokens
  are HMAC over `user_id + payload` (owner bound through the MAC, never in the payload), all checks
  `hmac.compare_digest`. S3 keys validated by path-**segment** equality + `expected_user_id`; bucket
  is server-chosen; LLM-supplied keys get the same fence.
- **CSRF/CSP:** two-layer CSRF (Origin/Referer + per-session synchronizer token, fail-closed,
  bypassed only for the cookieless `mobile_api` Bearer surface); **no GET route mutates state**;
  nonce-based `script-src` (no `unsafe-inline`), jsdelivr pinned to exact SRI, `object-src`/`base-uri`/
  `frame-ancestors` locked down.
- **JWT/session:** explicit `algorithms=["RS256"]` (no alg-confusion/`none`), `exp`/issuer/`token_use`/
  audience validated; `jwks_unavailable` (503, session preserved) distinguished from invalid-signature;
  forced JWKS refresh is single-flight + cooldown-gated; login fail-closed on Redis loss, dual
  rate-limited, `session.clear()` on login, user selected only from verified `sub`; mobile refresh is
  rotating with reuse-detection + triple ownership re-check.
- **SSRF:** menu fetch enforces scheme/port allowlist, rejects userinfo/control chars, requires **all**
  resolved IPs public, connects to the validated literal IP and re-verifies `getpeername()` (defeats
  DNS-rebinding), re-validates every redirect hop, blocks HTTPS→HTTP downgrade, runs in an isolated
  subprocess. **No SQL injection** (all `execute`/`text()` parameterized; the two f-string interpolations
  carry no user input). No `os.system`/`eval`/`pickle`/`yaml.load` on user input.
- **Secrets/config:** no hardcoded secrets; `SECRET_KEY`/`COGNITO_TOKEN_ENC_KEY` required in prod;
  TLS enforced for FatSecret; cookies `Secure`/`HttpOnly`/`SameSite=Lax`; `ProxyFix` trusts only
  `x_for`/`x_proto`; `MAX_CONTENT_LENGTH` set; AWS creds via EC2 IAM role (no static keys). No `.env`
  or private keys tracked in git; `.gitignore` correct.
- **File upload:** byte caps + anti-decompression-bomb (`MAX_IMAGE_PIXELS`, `MAX_DIMENSION`), `verify()`
  **and** `load()`, format allowlist, re-encode/strip before S3 (`AES256`), data-URL MIME validated
  against decoded bytes.

### Data integrity / concurrency / monetization
- **Locks:** repo-wide order holds (op-row → User → TrainingPlan); `lock_plan_owner` uses
  `FOR NO KEY UPDATE` (no reverse edge); `populate_existing()` everywhere a locked read must defeat
  the identity map; **no transaction held across network I/O** (rollback before every provider call).
- **Idempotency:** DB is always the final arbiter (`uq_plan_mutation_user_key`, `uq_*_day`,
  `uq_workout_session_active_owner`, `AiUsageReservation.key_digest`, Apple `(environment, original)`);
  `IntegrityError` narrowly classified, only verified unique violations become replays; `advance_checkpoint`
  is a single CAS UPDATE; gamification quest/challenge commit + `WaterLog.quest_fired` toggle-proof.
- **Money:** allowance ledger bucket math is CHECK-backed conditional UPDATEs; expired holds settle
  CONSUMED (no free retries); EXPIRED→RELEASED reconciliation guards against double-refund; Apple
  trust boundary treats client transaction IDs as lookup-only, env Apple-decided, revocation terminal,
  monotonic signed-timestamp ordering; eligibility strips Premium→FREE for ineligible accounts even with
  real Apple evidence.
- **Deletion:** runs under the exclusive health-authority fence; storage released while rows still name
  keys; resurrection race closed by write-then-check + `uq_user_cognito_sub` on all three subject-writing paths.
- **Migrations:** every unguarded `create_table` is an ancestor of the fresh-schema stamp; descendants
  use `has_table` guards; `migration_expand_contract` enforces expand/contract safety.
- **Timezones:** no raw `date.today()`/`utcnow()` day-key derivation in `app/`; all via `app/timeutil`
  (Istanbul); campaign/weekly/monthly window math is DST-correct.

### AI pipeline / coach / nutrition / capacity
- **Prompt injection / tool safety:** user-controlled context tokenized against closed vocabularies or
  reduced to deterministic structures before the prompt; no free-form text reaches a canonical header;
  FRIEND_DATA fence strips fence tokens + bidi/invisible chars; parser refuses unknown/missing/wrong-type
  fields and constructs only named frozen dataclasses by keyword (no splat/dynamic dispatch).
- **Plan-mutation safety:** per-turn budget (2) request-scoped, replay-safe; operation keys from server
  `request_id` + semantic fingerprint; model **cannot self-confirm** (confirm requires real-user `CONFIRM`
  intent, same-turn new proposal rejected); native direct-confirm owner-scoped + always under stored D1 authority.
- **Provider-switch "B-rule":** Bedrock→OpenAI fallback only when `tools_ran == 0` (both blocking and
  streaming); provider exception text never leaks (`type(e).__name__` only); interrupted streams persist
  with `interrupted=True`; `analyze_gym_photo` IDOR enforced via segment-equality key check.
- **Nutrition:** single pure target authority (`nutrition_targets.py`), all four former copies converge;
  absence → `null`, never synthetic 2000 kcal/zeros; no double-counting; FatSecret retention fails closed
  at read time past 22h even without a worker pass; AI cache keys content-only SHA-256 (no cross-user leak).
- **Input bounds / gating:** `MAX_QUESTION_CHARS=4000` on web + native coach; scrape text capped; **every**
  model call routes through `ai_provider_call.admit()` → concurrency slot (only the wearable-sync
  deviation in Finding 1 holds a slot across non-network work).

---

## Recommended next step

Address **Finding 1** (split the wearable fetch/persist phases so the slot wraps only the network
fetches). Findings 2 and 3 are optional / informational and can be deferred. No security remediation
is required.
