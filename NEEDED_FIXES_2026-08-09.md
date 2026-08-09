# FitX — Needed Fixes (Triage Report)

**Date:** 2026-08-09
**Commit reviewed:** `34f8dc7` on `claude/amazing-dijkstra-i7errp` (== `main` HEAD; "Hardening PR4 — concurrency, overload & recovery closure" #200).
**Scope:** 3 parallel read-only deep-dive audits — (1) Security, (2) Correctness bugs, (3) Architecture/Reliability. Focus on code changed **after** the prior triage (`NEEDED_FIXES_2026-08-02.md` @ `392c556`): commits #194–#200 — observability/runtime SLIs (#195), feature-flag registry (#196), rollout runbook (#197), auth-contract verification (#198), triage-findings 1–9 fixes (#199), and Hardening PR4 capacity/concurrency closure (#200).
**Method:** Adversarial trace of each attack surface and high-risk flow. Every claim verified against actual code (cited `file:line`), not against CLAUDE.md/docstrings. Every open item from the two prior reports was re-verified at HEAD; only still-open ones are carried forward.

---

## Headline

The codebase remains **mature and defense-in-depth**. The `#194`–`#200` hardening series is genuine and thorough: **all 9 concrete findings from the 2026-08-02 report (#1–#9) are fixed at HEAD**, confirmed independently by all three audits at the code level (not from changelogs).

**No new Critical, High, or Medium issues were found.** This run surfaces **2 carried-over correctness items still open (Low / Low–Medium)**, **1 genuinely-new Low reliability coupling** (flagged independently by both the security and architecture audits), and the **long-standing structural god-module debt** (carried over, and grown).

| # | Severity | Area | Type | Status | Confidence |
|---|----------|------|------|--------|------------|
| 1 | Low–Medium | `weekly_water` challenge counts water **toggles**, not days — completable in one day | Correctness / gamification integrity | Prior 2026-07-21 #2 — re-verified **still open** | Confirmed |
| 2 | Low | Serializer-bounds fix (#7) is **incomplete** — `save_training_plan` write path is unvalidated → `/training/bootstrap` still 500s | Correctness | Partial fix of 2026-08-02 #7 | Confirmed |
| 3 | Low | Mobile-auth availability now **coupled to AI-feature availability** via the shared 4-permit `_ai_slots` pool | Reliability / availability | **New** (side-effect of #2/#199 fix) | Confirmed (2 audits) |
| 4 | Low | God-modules continue to accrete (`ai_coach.py` 1247, `social.py` 1191, `tracking.py` 728) | Structure / tech debt | Prior #6/#7/#10 — open, **worse** | Confirmed |

> **Prior open list fully closed.** All items #1–#9 from `NEEDED_FIXES_2026-08-02.md` were re-verified as genuinely fixed at HEAD — see the verification table at the bottom. The only prior item that survives is the god-module structural debt (#10), now item #4 here.

---

## 1. [Low–Medium] `weekly_water` challenge counts water toggles, not days — completable in a single day

**Confidence: Confirmed. Carried over from 2026-07-21 report #2; the 2026-08-02 report explicitly deferred it ("Verify #2 separately"). Re-verified STILL OPEN at HEAD.**

**Files:**
- `app/blueprints/training.py:613-614` — event emit gated on `count > 0 and prev_count == 0`
- `app/services/gamification.py:280-282` — `_claim_quest` calls `record_event` **at the very top, before** the per-day `UserQuestProgress` dedup (comment at `:279` states this is intentional "so challenge progress is recorded even without a DailyQuest row")
- `app/services/challenges.py:110-127` — `record_event` blindly does `progress = progress + amount`; only a **weekly** `period_key`, with **no per-day guard**

**Failure scenario:** `count` is user-supplied, clamped 0..8 (`training.py:581`). The DailyQuest XP is safely deduped (`_claim_quest` returns `None` on the 2nd claim of the day), **but `record_event` runs unconditionally before that check.** So on a single calendar day:

1. POST `count=5` (prev=0 → fires; challenge progress **+1**)
2. POST `count=0` (prev=5 → no fire)
3. POST `count=5` (prev=0 → **fires again; challenge progress +1**)

Repeat ×5. A "log water on 5 different days this week" challenge (`metric="water_logged"`, `target_value=5`) completes in one afternoon, awarding its XP + badge + feed post via `_try_complete`. The `prev_count==0` guard is defeatable because `prev_count` is mutable per-request state, unlike the genuinely idempotent `FOR UPDATE`-locked `active_day` pattern it claims parity with.

**Fix:** Add per-day idempotency for day-semantic challenge metrics — persist a `last_event_day` on the progress row (or a "first positive water log today" marker) and make `record_event` a no-op when the same day-metric already fired today.

---

## 2. [Low] The `#7` serializer-bounds fix is incomplete — the real client write path (`save_training_plan`) is unvalidated

**Confidence: Confirmed. Partial fix of 2026-08-02 report #7.**

**Files:**
- Fix applied **only** to the AI path: `app/services/training_generation/response_validator.py:66,83` — new `_bounded_int` caps `set`→[1,100], `sure_dk`→[0,1440].
- **Bypassed write path:** `app/blueprints/training.py:188-208` (`save_training_plan`) writes `data.get("plan")` directly to the DB via `json.dumps` with **zero validation** — never calls `validate_generated_plan`.
- Serializer still **raises** (not clamps) on out-of-range: `app/services/workout_state/serialization.py:87-89` (`_bounded_text` raises for over-long `odak`/`isim`) and `:95-99` (`_bounded_int` raises for `set>100` / `sure_dk>1440`). `serialize_plan` is called at `training.py:409` inside `/training/bootstrap`, which converts the raise into a generic `bootstrap_unavailable` 500.

**Failure scenario:** The 2026-08-02 report noted the real client write path is `training.js → save_training_plan`. PR #199 chose "cap in the validator" but covered only AI-generated plans. A crafted (or legacy/alternate-writer) plan saved via `save_training_plan` with `set:150`, `sure_dk:5000`, or an `odak`/`isim` string >120 chars persists cleanly and renders fine via `/training-plan/active` + legacy `training.js`, but **500s `/training/bootstrap`** for that user. Self-inflicted, per-user scoped, blast radius = own bootstrap endpoint — genuinely Low, but the fix was claimed complete.

**Fix:** Validate in `save_training_plan` (run it through `validate_generated_plan` or the same `_bounded_int`/`_bounded_text` bounds), **or** have the serializer clamp rather than raise on large-but-parseable values so a stored plan can never crash its own read path.

---

## 3. [Low, New] Mobile-auth availability is now coupled to AI-feature availability through the shared `_ai_slots` pool

**Confidence: Confirmed. Independently surfaced by both the security and architecture audits. New — a side-effect of the #2/#199 fix, not a regression.**

**Files:** `app/services/mobile_auth.py:201` (`login`), `:512` (refresh renewal) → `app/services/ai_gate.py:129-140` (`blocking_concurrency_slot` acquires the shared `_ai_slots` semaphore, capacity `AI_MAX_CONCURRENCY`, default **4**).

**Analysis:** The #2 fix (folding blocking Cognito calls into `blocking_concurrency_slot`) correctly protects the `/health` 2-thread reserve — that was the goal, and it is strictly better than the previous ungated state. The residual is a **new cross-feature coupling**: `login` is unauthenticated and holds a slot across up to two serial Cognito round-trips (`authenticate` at `:203`, then a conditional `refresh_tokens` at `:239`; ~40 s worst case under a Cognito latency spike). Four such in-flight logins occupy all 4 AI slots, so **AI coach / plan / nutrition requests for unrelated users get 503'd** (`error.ai_busy`) during a Cognito incident. `login` is throttled only per-IP (10/min, 50/hour) and per-username — distributed or organic bursts during an incident are **not globally bounded**. This is exactly the residual the Aug-02 #2 fix text itself flagged ("consider a global … cap on concurrent in-flight login attempts"), which was not separately addressed.

**Why Low, not Medium:** graceful load-shedding (503 + `Retry-After`), bounded to 4 concurrent, `/health` and the deploy gate stay protected, strictly better than pre-fix.

**Impact / fix:** Operators sizing `AI_MAX_CONCURRENCY` must now account for mobile-auth load, and a Cognito outage degrades AI features for web users. Decouple the two failure domains with a **dedicated (small) semaphore for the mobile-auth blocking surface**, or a **global in-flight login cap**, so auth and AI don't share one 4-permit pool. No action required unless AI-coach availability under login/Cognito-incident bursts becomes an SLO concern.

---

## 4. [Low] God-modules continue to accrete (structural tech debt)

**Confidence: Confirmed. Carried over (prior #6/#7/#10); grown since baseline.**

`wc -l` at HEAD vs baseline `392c556`:

| Module | HEAD | Baseline | Δ | Domains |
|---|---|---|---|---|
| `app/services/ai_coach.py` | **1247** | 1211 | +36 | coach tools + provider loops |
| `app/blueprints/social.py` | **1191** | 1033 | **+158** | feed / friends / suggestions + moderation (3 domains) |
| `app/blueprints/tracking.py` | **728** | 728 | 0 | progress / heatmap / insights |

No functional bug — this is blast-radius and monkeypatch-surface debt. `social.py` grew the most and is now the second-largest module. The prior reports' suggested `coach_tools/` extraction and per-domain social blueprint split still have not happened. Schedule as their own PRs; not urgent.

---

## Prior open list (2026-08-02) — re-verified fixed at HEAD

All three audits independently confirmed these at the code level. **Do not re-report.**

| Aug-02 # | Item | Status | Evidence |
|---|---|---|---|
| 1 | `mobile_auth.refresh()` holds `FOR UPDATE` across Cognito network call | **Fixed** (#199) | Two-phase: `_snapshot_refresh` locks + validates + `rollback()`s the lock (`mobile_auth.py:495`) **before** `_renew_provider_tokens`, which asserts no open tx (`:515-517`); persist re-locks with optimistic `version`-guarded UPDATE (`:639-647`). |
| 2 | Mobile auth ungated / unaccounted in thread reserve | **Fixed** (#199/#200) | `login` (`:201`) + refresh renewal (`:512`) wrap Cognito in `blocking_concurrency_slot()`; friendly 503 on exhaustion. |
| 3 | `ai_gate._model_slots` unbounded acquire breaches reserve | **Fixed** (#200) | `model_concurrency_slot` now `_acquire_before_deadline` (raises, no infinite park; `ai_gate.py:197-200`); ceiling counts `max(0, AI_MODEL_MAX − AI_MAX)` (`:265-266`), enforced fatally at boot; `food_search`/`respond_suggestion` converted to `blocking_concurrency_slot`. |
| 4 | Mobile login discloses verification state (enumeration) | **Fixed** (#199) | `UserNotConfirmedException` → `AUTH_INVALID_CREDENTIALS`/401 (`mobile_auth.py:206-210`). |
| 5 | `ProxyFix` trusts client `X-Forwarded-Host`/`-Port` | **Fixed** | `config.py:292` now `x_host=0, x_port=0`. |
| 6 | `plan_facts` rejects `{"program":[...]}` wrapper | **Fixed** | `plan_facts.py:110-114` unwraps `dict.get("program")` before the list check. |
| 7 | `set`/`sure_dk` validator/serializer bound mismatch → bootstrap 500 | **Fixed** for the AI path (`response_validator.py:66,83`); **incomplete** for the direct write path — see item #2 above. |
| 8 | Completing today terminalizes a stale prior-day ACTIVE session | **Fixed** (#199) | `resolve_for_completion(today)` returns `STALE_SESSION_REQUIRES_RESOLUTION` when `workout_date != today` (`workout_session/service.py:283-286`); 409 at `training.py:65`. |
| 9 | OpenAI non-stream coach loop had no per-turn budget | **Fixed** (#199) | Loop guards `_remaining_coach_turn_seconds(deadline)` and passes `timeout=min(30.0, remaining)` (`ai_coach.py:1027-1046`), matching the Bedrock loop. |

Additionally, the JWKS unknown-`kid` thread-park vector noted in the CLAUDE.md Hardening PR4 entry is genuinely closed: `_forced_refresh_for` is single-flight under `_refresh_lock`, re-checks the cache after acquiring, and enforces `JWKS_FORCED_REFRESH_COOLDOWN_SECONDS` with the timestamp written **before** the fetch (`cognito_jwt.py:98-122`).

---

## Areas audited and verified clean (not risks — recorded to avoid re-litigation)

- **New `auth_contract.py`:** request auth pins `token_use="access"` (rejects ID tokens as API creds), leeway pinned to 0 on both web & mobile paths (not config-readable), retired `MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS` rejected unconditionally at boot (`enforce_retired_settings`, `config.py:309-310`). Transient/definitive split centralized; `jwks_unavailable` → 503 preserves session. Outcome metrics carry no PII.
- **New `runtime_metrics.py`:** default-off; request path writes only to an in-process buffer under lock (µs), never to the network; `put_metric_data` runs only on the daemon flush thread with tight boto3 timeouts (connect 2 s / read 3 s / 1 attempt); `_drain()` copies-and-clears atomically (bounded memory on CloudWatch outage). Fixed-cardinality dimensions (blueprint × status-class × client), client class derived from `request.blueprint` server-side, never a client header. Double-hooked idempotent shutdown flush.
- **`ThreadReserve` gauge now actually emitted** (was documented-but-missing) via the flush-thread sampler reading in-process semaphore counters + `db.engine.pool` stats (no connection checkout, no network).
- **Capacity invariants wired & fatal at boot:** `enforce_gate_invariants` (`app/__init__.py:277-278`); DB pool `pool_size + max_overflow >= FITX_WEB_THREADS` enforced and the pool is now actually configured (`config.py:363-376`); `WEB_WORKERS != 1` fatal.
- **New `feature_flags.py` + `config.py`:** strict boot-time env parsing; flags read from env at boot only (grep-confirmed no `request`/cookie/header reads); no `eval`/`exec`/import-by-name; presentation-only, never authorization.
- **`social.py respond_suggestion` refactor:** network/gate work moved out of the DB transaction (`rollback()` at `:1013` before provider/cache work), atomic conditional-UPDATE claim preserved, ownership still `receiver_id=current_user.id`, suggestion regexes linear (no ReDoS).
- **`wearables.py`:** all three provider routes now wrap external HTTP in `blocking_concurrency_slot()`; raw exception text suppressed from client responses.
- **Deep-health additions** (`flags`/`auth_contract`/`capacity` blocks) gated behind `_deep_health_allowed()`; expose names + booleans/integers only, no secrets; source resolved via nginx-appended real IP (not client-spoofable).
- **Adaptive engine math re-verified:** trailing weekly-window geometry (`training_history/analysis.py:41-61`), deload/plateau/Epley (`training_progression/analysis.py`), weekly_program baseline/target `None`-vs-`0.0` propagation and `round(baseline*(1+delta), 2)` — all consistent, no off-by-one.
- **No new migrations** since baseline; **no new `text()`/raw-SQL/state-changing-GET** introduced (grep-confirmed). Mobile-auth crypto/rotation lifecycle, workout completion/session atomicity, `public_id` IDOR resistance, SSRF allow-lists, two-layer CSRF + per-request CSP nonce, DOMPurify coach-output sanitization — all unchanged and sound.

---

## Recommended priority

1. **Item #1 (`weekly_water` toggle)** — the only finding with a real user-facing integrity impact (challenge XP/badge gaming). Small, contained fix (per-day idempotency on `record_event`). Do first.
2. **Item #2 (unvalidated `save_training_plan`)** — low blast radius but a self-inflicted 500; fix alongside #1 since both are small backend guards.
3. **Item #3 (auth/AI slot coupling)** — operator-note now; implement a dedicated auth semaphore or global login cap if a Cognito incident degrading AI features becomes an SLO concern.
4. **Item #4 (god-modules)** — schedule `coach_tools/` and social-blueprint split as their own refactor PRs; not urgent.

*Report generated by 3 parallel read-only deep-dive audits (security / correctness / architecture-reliability), each verifying every claim against `file:line` at commit `34f8dc7`.*
