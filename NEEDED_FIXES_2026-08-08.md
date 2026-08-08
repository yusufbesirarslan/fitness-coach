# FitX — Needed Fixes (Triage Report)

**Date:** 2026-08-08
**Scope:** 3 parallel read-only deep-dive audits — (1) Security, (2) Correctness bugs, (3) Architecture / Reliability.
**Method:** Adversarial trace of every high-risk flow and attack surface. Every claim is verified against the code (cited `file:line`), not against `CLAUDE.md` prose.
**Commit reviewed:** `34f8dc7` on `claude/amazing-dijkstra-3refp5` (HEAD, == `main`; two PRs ahead of the last audit baseline: `#199` triage fixes 1–9 and `#200` Hardening PR4).
**Baseline:** Prior triages `NEEDED_FIXES.md` (2026-07-21) and `NEEDED_FIXES_2026-08-02.md`. **All items from both were independently re-verified as genuinely fixed at HEAD** (evidence table below). This report lists only **new / still-open** findings.

---

## Headline

The codebase remains **mature and defense-in-depth**. **No Critical or High-severity issues were found by any of the three audits**, and no new Medium security or correctness bug was found. The recent hardening PRs (`#199`, `#200`) landed correctly with no regressions.

The one genuinely-open integrity bug has now **survived two prior audits**: the `weekly_water` challenge counter can be inflated within a single day (#1). Everything else is new-coupling awareness, hardening, and long-standing structural debt.

| # | Severity | Area | Type | Confidence | Status |
|---|----------|------|------|------------|--------|
| 1 | Low–Medium | `weekly_water` challenge counts toggle-events, not days | Correctness / integrity | Confirmed | **Still open (2× missed)** |
| 2 | Low–Medium | Mobile-auth bursts share the coach's `_ai_slots` → can 503 coach | Reliability / coupling | Confirmed | New (from `#199` fix) |
| 3 | Low–Medium | PR4 capacity gauges (`ThreadReserve`…) are dark by default | Observability | Confirmed | New |
| 4 | Low | `model_concurrency_slot` now fail-fast if `AI_MODEL_MAX < AI_MAX` | Reliability / config hazard | Confirmed | New (from `#200`) |
| 5 | Low | AST structural guards use hardcoded route-name allowlists | Test brittleness | Confirmed | New |
| 6 | Low | God-modules (`ai_coach.py` 1247, `social.py` 1191) — still growing | Structure | Confirmed | Still open |
| 7 | Low (cosmetic) | `sure_dk: 0` no longer substitutes default duration | Correctness | Confirmed | New (benign) |

---

## Prior findings — re-verified RESOLVED at HEAD `34f8dc7`

Three independent audits converged on the same conclusion: every prior open item is closed.

| Prior finding | Evidence at HEAD |
|---|---|
| `ai_gate` reserve breachable by ungated `_model_slots` callers (both reports, Medium) | `model_concurrency_slot` now bounded-acquire (`ai_gate.py:186-200`); reserve invariant counts model-excess + DB pool (`ai_gate.py:230-252`) |
| `ProxyFix` trusts client `X-Forwarded-Host`/`-Port` (both, Low) | `ProxyFix(..., x_host=0, x_port=0)` (`config.py:292`) |
| `mobile_auth.refresh()` held `FOR UPDATE` across Cognito network call (08-02 #1, Medium) | Two-phase optimistic rewrite: `_snapshot_refresh` locks → `db.session.rollback()` releases → network in `_renew_provider_tokens` outside tx (asserts `not in_transaction()`, `mobile_auth.py:512-517`) → `_lock_and_revalidate_refresh` re-locks with full-field drift guard → `refresh_conflict` |
| Mobile auth endpoints ungated / unaccounted (08-02 #2, Medium) | `blocking_concurrency_slot()` at `mobile_auth.py:201, 512` |
| Mobile login user-enumeration (403 vs 401) (08-02 #4, Low) | `UserNotConfirmedException` → `AUTH_INVALID_CREDENTIALS` 401 (`mobile_auth.py:206-209`) |
| OpenAI non-stream coach loop had no per-turn wall-clock budget (both, Low) | Deadline + `timeout=min(30, remaining)` (`ai_coach.py:1027-1049`) |
| `plan_facts` rejected `{"program":[...]}` wrapper (08-02 #6, Low) | Unwraps dict before list check (`plan_facts.py:110-111`) |
| `/training/bootstrap` 500 on `set`/`sure_dk` over serializer bounds (08-02 #7, Low) | `_bounded_int(set,1,1,100)` / `sure_dk,…,0,1440` (`response_validator.py:66-83`) |
| Stale prev-day ACTIVE session terminalized as COMPLETED (08-02 #8, Low) | `resolve_for_completion` returns `STALE_SESSION_REQUIRES_RESOLUTION` when `session.workout_date != today` (`workout_session/service.py:283-287`) |
| `ai_stream` producer-thread leak (older triage) | `cancelled` Event + `call_on_close` teardown (`ai_stream.py:73/89/108`) |
| DB pool < thread count (implicit SQLAlchemy defaults) | Explicit pool sizing + boot invariant (`config.py:362-376`) |
| Wearables sync parking ungated threads | `blocking_concurrency_slot()` (`wearables.py:109/158/183`) |

**Independently re-verified clean:** IDOR/ownership scoping across all blueprints; JWT validation (`RS256` pinned, `token_use` enforced, `jwks_unavailable`→transient); prompt-injection fences (`context_builder.neutralize_friend_content`); metrics PII (fixed-cardinality, server-derived dimensions only); CSP/XSS (no `|safe` on user data, only `tojson` JSON blocks); SSRF/OAuth (hardcoded provider URLs, single-use `state`); `fitx_mcp` parameterized SQL; avatar upload validation; timezone/day-keys (no `date.today()`/`utcnow().strftime` bypass of `app/timeutil`); one-way layering (pure layers import no Flask/ORM/DB — grep-confirmed); single migration head; `db_init.py` FATAL boot-upgrade fail-fast.

---

## Open findings

### 1. [Low–Medium · Confirmed · STILL OPEN, missed by two prior reports] `weekly_water` challenge counter inflatable in a single day
**Area:** Gamification integrity
**Files:** `app/services/gamification.py:280-282`, `app/blueprints/training.py:613-614`, `app/services/challenges.py:122-124`

`_claim_quest` calls `record_event(user_id, quest_type)` **at the top, before** the per-day `UserQuestProgress` dedup (`gamification.py:280-293`). The dedup only guards the **DailyQuest XP** — the **challenge counter** in `record_event` increments `progress = progress + amount` with only a weekly `period_key` and **no per-day guard** (`challenges.py:122-124`).

The emit gate in `training.py:613` (`if count > 0 and prev_count == 0`) is meant to fire only on the day's 0→positive transition, but `prev_count = row.count` reads the mutable stored water count, so it **resets whenever water is set back to 0**.

**Failure scenario (same Istanbul day):**
`POST /water count=5` (prev=0 → fires) → `POST count=0` → `POST count=5` (prev=0 → **fires again**) → repeat. The `weekly_water` challenge (`target_value=5`, "log water on 5 days") completes in a single afternoon, awarding its ~75 XP + progress. The DailyQuest XP itself stays correctly deduped; only the challenge integrity is broken.

**Fix:** Give day-semantic metrics a per-day idempotency key in `record_event` (persist `last_event_day` on the progress row and no-op if it equals today), **or** gate the `training.py:613` emit on a persisted "first positive water log today" marker instead of the mutable `prev_count`. This is the correct fix for the root cause the prior gate only papered over.

---

### 2. [Low–Medium · Confirmed · NEW coupling introduced by the `#199` fix] Mobile-auth bursts share the coach's `_ai_slots` and can 503 it
**Area:** Reliability / availability coupling
**Files:** `app/services/ai_gate.py:129-140` (`blocking_concurrency_slot` acquires `_ai_slots`) vs `ai_gate.py:345` (`ai_concurrency_gate` also uses `_ai_slots`)

The `#199` fix correctly wrapped `login()`/`refresh()` in `blocking_concurrency_slot` to protect the `/health` thread reserve — but that draws on the **same 4-permit `_ai_slots` semaphore** (`AI_MAX_CONCURRENCY=4`) as coach `/ask`, `food_search`, and `respond_suggestion`.

**Failure scenario:** During a Cognito latency spike, a burst of slow mobile logins (each holding an `_ai_slot` for ~20–40 s of network) can occupy all 4 slots, pushing concurrent **coach/food/suggestion** requests to `503 ai_busy`. Because `AI_GATE_WAIT_SECONDS=0`, contention fails fast with `Retry-After` rather than hanging — so this is bounded degradation, not an outage. But the availability coupling between the mobile-auth surface and the coach is **new and undocumented**.

**Fix (if undesired):** Give mobile auth its own small semaphore folded into the reserve arithmetic, rather than sharing the coach's `_ai_slots`. At minimum, document the coupling as a capacity-planning input.

---

### 3. [Low–Medium · Confirmed · NEW] The PR4 capacity safety-net is dark by default
**Area:** Observability
**Files:** `config.py:194,203` (`AI_METRICS_ENABLED`, `RUNTIME_METRICS_ENABLED` both default `0`); consumers `ai_gate.py:102-112` (`record_capacity_gauges` → `ThreadReserve`/`AiSlotsActive`/…), `runtime_metrics.py`; rollout `abort_signals` in `feature_flags.py:428-437`

PR4's headline improvement — promoting the thread reserve from boot-time arithmetic to a **measured** `ThreadReserve` gauge — only emits when `RUNTIME_METRICS_ENABLED=1`, which **defaults OFF**. Multiple rollout flags name "`ThreadReserve` gauge stays above its floor" / "no rise in `HttpOverload`" as their go/no-go `abort_signals`, yet in a default production process those gauges never fire. The flag registry does list `RUNTIME_METRICS_ENABLED=1` as a prerequisite, so this is a documented dependency rather than a defect — but the safety net exists only if an operator remembers to arm it, and nothing enforces the coupling at boot.

**Fix:** Have boot warn at error-level (like the `BEDROCK_ENABLED` warning at `config.py:408`) when a rollout flag is ON while `RUNTIME_METRICS_ENABLED` is OFF. Better: make the metrics prerequisite a **hard boot check** for the attack-surface flag (`MOBILE_AUTH_ENABLED`), so its abort signal can never be blind.

---

### 4. [Low · Confirmed · NEW behavioral change from `#200`] `model_concurrency_slot` is now fail-fast if `AI_MODEL_MAX_CONCURRENCY < AI_MAX_CONCURRENCY`
**Area:** Reliability / config hazard
**Files:** `app/services/ai_gate.py:186-200` + `_acquire_before_deadline:119-126`

Old `model_concurrency_slot` blocked indefinitely on `_model_slots.acquire()`; the new one, under the default `AI_GATE_WAIT_SECONDS=0`, does a non-blocking acquire and **raises `BlockingConcurrencyLimit`** when the model semaphore is full. With the **default** equal caps (`AI_MODEL_MAX_CONCURRENCY == AI_MAX_CONCURRENCY == 4`) this never bites (the outer `_ai_slots` gate admits ≤4 and there are exactly 4 model slots).

**Failure scenario:** `AI_MODEL_MAX_CONCURRENCY` is a documented, supported knob. If an operator sets it **below** `AI_MAX_CONCURRENCY`, admitted AI requests that pass the outer gate can now fail-fast to a user-visible fallback ("AI servisi şu an yoğun") under moderate load, where the old code would have queued ~1 s and succeeded. `BlockingConcurrencyLimit` (a `RuntimeError`) propagates past the provider-only `except` in `_claude_chat`/`_openai_chat` up to each caller's fallback — no 500, but a degraded response.

**Fix:** Enforce `AI_MODEL_MAX_CONCURRENCY >= AI_MAX_CONCURRENCY` at boot (the reserve invariant already computes `model_excess` but doesn't guard the *under*-provisioned direction), **or** give the inner model gate a small non-zero wait so admitted requests briefly queue instead of failing.

---

### 5. [Low · Confirmed · NEW] AST structural guards use hardcoded route-name allowlists
**Area:** Test coverage / guard brittleness
**Files:** `tests/test_capacity_invariants.py:416-438` (enumerates `"wearable_callback"`, `"wearable_sync"`, `"whoop_resource"`, `"save_wearable_tokens"` by string)

These guards defend against a broken scan (they assert `saw_network`/`saw_lock` so they can't silently pass on zero matches — good). But the **set of routes to check** is a hand-maintained string list. A newly added or renamed provider-backed route is simply not covered: the guard keeps passing while a new blocking-network surface goes ungated and unaccounted — the exact class of bug `#200` just closed. This is weaker than the `auth_contract`/`feature_flags` pattern, where the allowlist is **derived from the registry itself**.

**Fix:** Derive the "provider-backed route" set structurally (any view whose body calls `cognito_service.*`/`requests.*`/a provider client) rather than by name, so a new offender fails the guard by construction.

---

### 6. [Low · Confirmed · STILL OPEN and growing] God-modules
**Area:** Structure / maintainability
**Files:** `app/services/ai_coach.py` **1247** lines (was 1211 → 1199 across prior reports — still climbing); `app/blueprints/social.py` **1191** (was 1033 — grew via `#199`); `app/models.py` 1109; `app/services/mobile_auth.py` 865; `app/services/nutrition_pipeline.py` 862; `app/services/ai_nutrition.py` 792; `app/services/fatsecret.py` 790; `app/blueprints/tracking.py` 728

`social.py` still spans four product domains in one blueprint (friends, feed+moderation, chat/messaging, AI workout suggestions). `ai_coach.py` remains the single largest module and the widest test-monkeypatch surface. Both were flagged by the two prior audits and both **grew rather than shrank**. Blast-radius/maintainability debt, not a functional bug.

**Fix:** Split `social.py` into per-domain blueprints (`friends`/`feed`/`chat`/`suggestions`); extract `ai_coach.py` tool implementations into a `coach_tools/` package (the re-export shim already exists, so import paths and the test monkeypatch surface stay stable).

---

### 7. [Low · cosmetic · benign] `sure_dk: 0` no longer substitutes the default duration
**Area:** Correctness (presentation)
**File:** `app/services/training_generation/response_validator.py:66-68`

`_bounded_int(day.get("sure_dk"), default_duration, 0, 1440)` replaced `_to_int(...) or default_duration`. A training day with an explicit `sure_dk: 0` now persists as `0` instead of falling back to `preferences.sure` (the old `or`-default fired on falsy `0`). Missing/`None` still defaults correctly. Presentation-only and arguably *more* correct — noted for completeness, no action needed.

---

## Speculative (not rated — awareness only)

- **Concurrent mobile `refresh()` under provider-side rotation.** In the two-phase `refresh()`, two concurrent refreshes of one token family both snapshot the same encrypted provider refresh token; if renewal is needed, both call `cognito_service.refresh_tokens` with it. **If Cognito refresh-token rotation were enabled provider-side** (it is **not** by default), the loser's call would get `NotAuthorizedException` → `_revalidate_and_revoke_provider_failure` revokes the family. Bounded, and only reachable with non-default Cognito config — flagging for awareness, not as a finding.
- **Self-scoped coach prompt context is unfenced.** The coach's `[KULLANICI PROFİLİ & HAFIZA]` block injects the user's own `injuries`/`preferences`/check-in `note` unfenced (`context_builder.py:44-85`). This is self-injection only (a user manipulating their own coach); no cross-user impact — not a security issue.

---

## Recommended priority

1. **Fix #1 (`weekly_water`)** — the only genuinely-open integrity bug; it has now survived two audits. Small, well-scoped fix in `record_event` / the water endpoint.
2. **Decide on #2 & #4** — both are consequences of the (correct) `#199`/`#200` hardening. Either accept and document the tradeoffs, or add the small dedicated semaphore (#2) and the `>=` boot guard (#4).
3. **#3** — cheap boot warning that closes a real "safety-net is blind in prod" gap and makes rollout abort-signals trustworthy.
4. **#5, #6** — structural hygiene; schedule, don't rush.

**Bottom line:** No Critical/High anywhere. The three audits confirm the recent hardening work is correct and complete; the outstanding list is one small integrity bug plus awareness/hygiene items.
