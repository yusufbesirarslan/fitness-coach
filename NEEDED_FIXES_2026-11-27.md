# FitX — Needed Fixes (Triage Report)

**Date:** 2026-11-27
**Commit reviewed:** `16cb17c` on `claude/amazing-dijkstra-2kge8u` (== `main` HEAD; through R1 F1 Coach acceptance-reader containment `#552`).
**Scope:** 3 parallel read-only deep-dive audits — (1) Auth / access-control / secrets / SSRF / injection / payment, (2) Business logic / transactions / concurrency / data-integrity, (3) AI pipeline / capacity / privacy-retention / deletion / structure.
**Method:** Adversarial trace of each attack surface and high-risk flow. Every reported claim was verified against actual source (cited `file:line`), not against CLAUDE.md/docstrings. The highest-severity finding and the two concrete code-fix items were re-verified by hand by the coordinating session.
**Baseline:** `NEEDED_FIXES.md` (2026-07-21), `NEEDED_FIXES_2026-08-02.md`, `NEEDED_FIXES_2026-08-14.md`, `TRIAGE_FINDINGS_2026-10-01.md`. Items fixed there were spot-checked as genuinely present in the code.

---

## Headline

The codebase remains **mature and defense-in-depth.** **No Critical or High-severity _security_ issue was found** — authentication, JWT validation, mobile session lifecycle, owner-bound opaque-token schemes, Fernet token storage, SSRF defense (menu fetch), SQL parameterization, CSRF/CSP, Apple payment verification, account-deletion / anti-resurrection, provider-retention withholding, and the transaction/locking/idempotency surfaces are all implemented to a high standard, with AST-gate architectural guards making whole classes of regression structurally hard.

This run surfaced **one High-confidence reliability bug** (a hard 30 s provider-timeout cap that overrides a configured 60 s value and the SDK's dynamic timeout, reachable by large plan/nutrition generations), plus **two Low correctness/consistency items** and a short list of informational/accepted-tradeoff notes. The long-standing structural tech-debt (god-modules) remains open and is carried forward.

| # | Severity | Area | Type | Status | Confidence |
|---|----------|------|------|--------|------------|
| 1 | **High** | 30 s blocking provider-timeout cap truncates large non-stream generations | Reliability | **New** | Confirmed (code); impact to verify via metrics |
| 2 | Low | Legacy Coach commit tools select "latest staged" without an `id` tiebreak | Correctness / consistency | **New** | Confirmed |
| 3 | Low | Retry path edits its (per-call copy of the) transport dict inside the loop | Latent / cosmetic | **New** | Confirmed benign |
| 4 | Low | Web menu fetch permits `http://` (outside a D1 consent op) | Security (defense-in-depth) | **New note** | Confirmed non-exploitable |
| — | Info | `/ask/stream` is non-incremental (buffered); `ProxyFix` X-Forwarded-* trust; CSP wildcards | Hardening / tradeoff | Carried / documented | Confirmed accepted |
| 5 | Low | God-modules: `ai_coach.py` (~1.5k), `social.py`, `tracking.py` | Structure | Prior — open | Confirmed |

> **Prior recurring items — status at this HEAD.** The `ai_gate` thread-reserve breach (prior 07-21 #1 / 08-02 #3 / 08-14 #4) now appears **closed**: `enforce_gate_invariants` checks the model-slot overflow *and* the DB-pool-vs-threads relation and publishes the `ThreadReserve` gauge. The `weekly_water` challenge-gaming item (prior #2) was **not re-surfaced** this run — verify separately if never addressed. `ProxyFix` X-Forwarded trust and the god-modules remain open (below). The OpenAI per-turn wall-clock budget item is effectively superseded by Finding 1's timing-layer discussion.

---

## 1. [High] Hard 30 s transport-timeout cap overrides caller intent and the SDK's dynamic timeout for large non-streaming generations

**File:** `app/services/ai_provider_call.py:345` (in `Admission.create`)

```python
bounded_transport["timeout"] = min(float(transport.get("timeout", 30.0)), 30.0)
```

Every **blocking** provider attempt is hard-capped at a 30 s transport timeout, regardless of what the caller passes. Timeouts are **not** retried (`_is_retryable` returns `False` for them), so a request that needs >30 s simply fails. The **streaming** path (`Admission.stream`, line 386) applies **no** such cap, so the two paths diverge.

Two concrete, reachable consequences:

1. **Plan / nutrition generation can be truncated at 30 s.** `ai.py::_claude_chat` calls `call.create(bedrock_client.messages.create)` with **no** timeout → the default `transport.get("timeout", 30.0)` = 30 s (verified `ai.py:146`). Training-plan generation is a **non-streaming** Bedrock call requesting `PRIMARY_MAX_TOKENS=4000` / `REPAIR_MAX_TOKENS=7000` (`training_generation/plan_schema.py:67-68`; `service.py:303,334`). A large Sonnet completion of that size can exceed 30 s; at the cap the socket times out, the generation raises, falls to the OpenAI fallback (also 30 s-capped), and can fail the whole turn. The Anthropic SDK would otherwise set a longer dynamic timeout keyed to `max_tokens` precisely for this case — the explicit `timeout=30` defeats it.
2. **Coach blocking path's explicit 60 s intent is silently halved.** `ai_coach.py:1531` passes `timeout=min(BEDROCK_CALL_TIMEOUT_SECONDS, remaining)` with `BEDROCK_CALL_TIMEOUT_SECONDS=60.0` (`config.py:121`), clearly intending up to 60 s; `create()` reduces it back to 30 s (verified). Coach output is small (~700 tokens) so practical impact here is low, but it confirms the cap overrides deliberate call-site values.

**Why High / verify-before-fix:** the code facts are confirmed, but the *user-visible* impact depends on how often real 4000–7000-token generations actually exceed 30 s. Before changing the constant, confirm with the existing `[AI-USAGE]` `outcome=timeout` counts for `feature=training_plan` / `nutrition_plan` — the metric already exists.

**Fix direction:** honor the caller's requested timeout (bounded by the per-turn deadline `self._remaining()`) instead of a fixed 30 s; or, minimally, raise the cap to at least `BEDROCK_CALL_TIMEOUT_SECONDS` for Bedrock and scale it with `max_tokens` for large non-streaming generations. Keep a cap (single worker / 8 threads means an unbounded blocking call is a real thread-exhaustion risk) but make it match the generation's needs and the configured 60 s, and add a comment recording the chosen rationale. Consider restoring timeout parity between the `create` and `stream` paths while here.

---

## 2. [Low] Legacy Coach commit tools pick "latest staged" without an `id` tiebreak

**Files:** `app/services/ai_coach.py:323-326` (`_tool_confirm_and_commit_meal_log`), `:473-476` (`_tool_confirm_and_commit_workout_log`)

Both select the pending row to commit with `order_by(PendingAction.created_at.desc()).first()` — no `id` secondary sort. The sibling **cancel** selector in the same module (`:534-537`) and every `PendingAction` selector in `coach_confirmation.py` *do* carry `PendingAction.id.desc()`, and the documented repo convention (`TRIAGE_FINDINGS_2026-10-01` #3/#4) is that "latest row" selectors break `created_at` ties by `id`.

**Failure scenario:** a user with two staged `log_meal` (or `log_workout`) `PendingAction`s sharing an identical `created_at` → the confirm tool commits a non-deterministic one of them.

**Why Low / near-unreachable:** the two writers stage in separate HTTP requests, each stamping `created_at = datetime.utcnow()` at microsecond precision, so a true tie is practically impossible; and both tools then atomically claim the chosen row via `filter_by(id=...).delete()` (rowcount-gated), so there is **no double-commit** — only the (essentially unreachable) ambiguity of *which* staged entry wins.

**Fix direction:** add `PendingAction.id.desc()` to both `order_by` clauses, for consistency with the rest of the module and the repo-wide convention.

---

## 3. [Low] Blocking retry path edits its transport dict inside the loop

**File:** `app/services/ai_provider_call.py:363-364` (in `Admission.create`)

On a retryable error the loop does `transport["timeout"] = min(transport["timeout"], max(1.0, self._remaining()))`. This mutates `transport`, but `transport = self._take(transport)` at line 330 returns a **fresh per-call dict** (`{k: v for k, v in transport.items() if v is not None}`), so the edit is local to this admission and **not** shared with the caller or across calls — it is harmless. (Noted here only to correct a reviewer's "shared dict" framing; the practical effect is a tightening timeout budget across retries, which is the intended behavior.)

**Fix direction:** none required. Optionally rename/comment to make the deliberate budget-tightening obvious.

---

## 4. [Low / note] Web menu fetch permits `http://`

**File:** `app/services/menu_remote.py` (`fetch`, ~`:247`)

HTTPS-only is enforced for the native menu path and under a D1 consent operation, but the generic web menu fetch still allows `http://` targets. **This is not an SSRF hole** — the full IP-validation guard (DNS resolution requiring *all* resolved IPs public, pin-to-literal-IP with post-connect peer verification, per-hop re-validation, HTTPS→HTTP downgrade blocked, credential/cookie/env stripped, byte/time/redirect budgets, isolated subprocess) still applies. The only residual is a plaintext fetch of a *public* host. Documented as an accepted tradeoff.

**Fix direction:** optional — require HTTPS on the web path too, for consistency with native, if plaintext public fetches are undesirable.

---

## Informational / accepted tradeoffs (not bugs)

- **`/ask/stream` is non-incremental.** `ai_stream._stream_bedrock` buffers each round's text and only releases it (re-chunked into 48-char deltas) after `get_final_message()`, so even a plain no-tool reply arrives all-at-once. This is **intentional** (so "I've added…" prose can't precede persistence / the B-rule side-effect gate), but it means the streaming endpoint gives no first-token-latency benefit for the common case. Confirm it matches product expectations.
- **`ProxyFix` trusts client `X-Forwarded-Host` / `-Port`** that nginx never sets (prior 07-21 #3 / 08-02 #5 / 08-14 #6 — carried, Low). No change required if the trust boundary stays nginx-only.
- **CSP `style-src-attr 'unsafe-inline'`** and **`script-src https://*.googletagmanager.com`** wildcard — both explicitly documented accepted tradeoffs in `app/hooks.py`.
- **`wearable_callback` is a state-changing GET** — inherent to the OAuth redirect flow, mitigated by a server-stored `state` parameter. Standard and acceptable.
- **Email recipient logging is masked** (`email_service.py`, `account_recovery.py`, `account_registration.py` route addresses through `mask_email`/`_mask_recipients`) — confirms good hygiene, not a finding.

---

## 5. [Low] Structural tech-debt (carried forward, open)

- **God-modules.** `ai_coach.py` (~1.5k lines), `social.py`, and `tracking.py` remain large multi-concern modules (prior reports #6/#7/#8/#10). No correctness impact; they raise the cost of change and the risk of a future regression slipping a review. Decompose opportunistically along the already-clear seams (tool handlers vs provider loop vs context; feed vs friends vs activity).

---

## Scope note

The **RC2 Apple pre-release / qualification-cohort** code and `ACCOUNT_ELIGIBILITY_ENABLED` enforcement are config-gated **OFF** by default (temporary launch scaffolding) and were not treated as live security boundaries. A focused follow-up on those temporary paths is warranted **once the flags flip on** for launch.

---

## Appendix — verified solid (no defect found)

Recorded so a future run knows the coverage and need not re-derive it:

**Security / auth.** RS256 enforced, issuer + audience + `token_use` checked, expiry leeway pinned to 0, kid-miss JWKS refresh single-flight + cooldown (`cognito_jwt.py`). Mobile opaque credentials hashed and compared with `hmac.compare_digest`, provider token re-validated + `sub` cross-checked user↔family↔claims, refresh rotation with credential-change epoch fence (`mobile_auth.py`). Fernet storage requires a real key in prod (`session_store.py` + `config.py` boot enforcement). Owner-bound HMAC token schemes (nutrition/pump-check/exercise-context) use per-class domain separation, owner-in-MAC, `compare_digest`; stored tokens additionally `filter_by(public_id, user_id)`. SSRF guard is textbook-correct. No raw interpolated SQL; no `eval/exec/pickle/yaml.load/os.system`. CSRF Origin+token on all writes, `mobile_api` correctly Bearer-exempt; CSP per-request nonce + pinned SRI. Apple entitlement derives from a server-side signature-verified Apple lookup bound to the per-user `appAccountToken`; a client cannot forge it. No PII/secrets in logs.

**Concurrency / data integrity.** Create-writer lock order `operation → User (FOR NO KEY UPDATE) → TrainingPlan` is consistent and the `NO KEY UPDATE` vs FK `KEY SHARE` compatibility is correct (no reverse-edge cycle). No lock held across any Bedrock/S3/Cognito/FatSecret round-trip in these paths. `advance_checkpoint` is a correct revision-keyed CAS; `complete_workout` evaluates `expected_checkpoint_revision` under the session row lock before the already-completed preflight and reconciles the `uq_workout_completion_day` race-loser. AI-allowance ledger `_move` guards + CHECK backstops, owner-locked settlement, idempotent EXPIRED/billed-release branches; LAUNCH_FREE window math correct and end-exclusive. `nutrition_targets` honors "absence is not zero"; `replace_nutrition_plan`'s blanket delete is safe because all nutrition writers serialize on the owner lock. Idempotency arbitrated by DB unique keys, not process memory. D1 consent fences fail-closed; `age_on` 29-Feb handling correct. Latest-row selectors carry the `id` tiebreak (except the two Low cases above and cosmetic display orders). No `date.today()`/`utcnow().strftime` misuse.

**AI pipeline / privacy / deletion.** B-rule (no provider fallback after a tool side-effect or after the first streamed token) correctly enforced on both blocking and streaming paths; `stop_reason=="refusal"` ends the turn, never a second provider. Soft-error fallbacks never consume quota or enter memory (B16); post-mutation fallback text truthfully says "saved". Allowance metering settles once. Provider exception text never leaks (closed-vocabulary category + request_id only); `coach_privacy.tool_result` rebuilds tool payloads from closed token vocabularies. `provider_retention` reader withholding fails closed past 22 h; `expire_due` idempotent with `FOR UPDATE SKIP LOCKED`. Account deletion / anti-resurrection tombstone write-then-check ordering is race-correct; legal-hold fence held across the lifecycle and re-checked under the owner row lock; storage released while rows still name the keys with `MealPhotoCleanup` intent persisted in the purge transaction; `DeletionIncomplete`/`DeletionUnavailable` correctly distinguished. All blocking provider calls (Apple, Cognito, wearables, FatSecret, recovery/registration) are inside `blocking_concurrency_slot`. Read failures use typed retryable 503s caught in-view, not the `AUTH_TEMPORARILY_UNAVAILABLE` catch-all.

**Overall structure.** Boundary discipline is the dominant strength: one provider door (`ai_provider_call.admit`), one deletion authority, one plan-mutation writer, one retention owner, one JWT validator — each enforced by AST-gate tests, not convention. Fail-closed is consistent ("infrastructure failure must never present as empty/rest/no-plan"). The single-gunicorn-worker / 8-thread invariant is enforced at boot. The one area where the "one owner, documented intent" rule has drifted is the **provider-call timing layer** (Finding 1) — the 30 s cap quietly contradicts two configured values and lacks a recorded rationale.
