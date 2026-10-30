# Triage Findings — 2026-10-30

Automated daily triage (scheduled routine). Three parallel deep-dive agents reviewed
the codebase for **security vulnerabilities**, **correctness/concurrency bugs**, and
**overall structure**. Everything below was verified against source.

## TL;DR

**No confirmed exploitable security vulnerability and no confirmed data-corruption /
lost-update / double-charge / crash bug was found.** The codebase is unusually
well-hardened and the highest-risk subsystems (AI allowances/billing, D1 consent,
Apple subscriptions, Sonnet-5.5 routing) are all dark-gated (flags default OFF,
`POLICIES={}`), so even a latent defect there is not live.

The actionable items are one **deploy-sequencing fact** (highest impact), one small
**code fix** (lingering DB lock, dark-gated), a handful of **doc-drift / maintainability**
items, and three **defense-in-depth** nits.

---

## P1 — Deploy sequencing: all paid AI is fail-closed at this HEAD

**Status: BY DESIGN — verify rollout sequencing before/at deploy. Not a code bug.**

`app/services/ai_consent.py:40` — `POLICIES = MappingProxyType({})` (empty).

`ai_provider_call._admit` requires `feature in ai_consent.BINDINGS`, and
`ai_consent.dispatch` → `_authorize` needs a valid grant from `POLICIES`. With the
empty policy map and allowance enforcement OFF (default), the live coach path never
opens an `operation_scope`, so:

- native operation features (`coach` / `vision` / `menu_extract`) →
  `admission_generation` raises `AI_CONSENT_PATH_UNAVAILABLE`
- plan / nutrition features → `dispatch` raises `AI_CONSENT_REQUIRED`

**Net: every paid AI call raises `ConsentDenied` until `POLICIES` is populated with
approved disclosures.** The docstring confirms this is intentional ("callers without a
qualified binding are unavailable … no hidden system bypass").

**Action:** confirm with the team that the follow-up disclosure/`POLICIES` rollout is
sequenced to land with or before this HEAD in production. Shipping this HEAD alone
disables Coach, menu analysis, vision, plan and nutrition AI.

---

## P2 — `subscriptions.entitlement` holds `FOR UPDATE` lock on a read-only path

**Status: CONFIRMED defect, Low severity, dark-gated (`APPLE_SUBSCRIPTIONS_ENABLED`
default OFF).**

`app/services/subscriptions.py`:

- `_owner()` (≈L27) takes the `User` row with `.with_for_update()`.
- `entitlement()` (≈L113) only catches `SQLAlchemyError` for rollback.

Two problems on this *read* path:

1. If `_eligible_view` → `entitlement_view` raises `SubscriptionError("SUBSCRIPTION_INVALID_STATE")`
   (a corrupt/unknown-status row — status not in 1..5, or missing `last_verified_at`/
   `expires_at`), it is **not** caught by `except SQLAlchemyError`, so it propagates
   with the `FOR UPDATE` lock held and the transaction open. Callers
   (`ai_allowances._subscription`) map it to `AllowanceError` without rolling back, and
   in `reserve` the `_subscription(user_id)` call sits *before* the
   `try/except AllowanceError: rollback`, so the lock lingers until request teardown.
2. The **success path** returns holding `FOR UPDATE` without commit/rollback, serializing
   concurrent entitlement reads for the same user.

**Fix options:** make `entitlement` roll back (or commit) on all exits including
`SubscriptionError`, or — cleaner — don't take `FOR UPDATE` for the read-only
entitlement view (reserve the row lock for the write paths `persist_verified` etc. that
actually need it). User-row-first lock ordering is respected, so there is no deadlock
risk; the impact is lock contention + a lingering transaction, only once Apple
subscriptions are enabled.

---

## P3 — Documentation drift (CLAUDE.md is load-bearing for onboarding)

**Status: CONFIRMED. Low code risk, real onboarding risk.**

1. **Stale Alembic head.** The live single head is
   `c6f8b0d2e4a1_aggregate_physical_budget` (nullable columns on
   `ai_usage_reservation`, backing `app/services/ai_allowances/physical.py`). CLAUDE.md's
   newest-referenced head is `e4f5a6b7c8d9`; the aggregate-physical-budget feature is
   undocumented.
2. **Stale "zero production callers" claim.** CLAUDE.md says TD-01
   `exercise_performance_history` has ZERO production callers and the H-A8 gate fails on
   any importer. Reality: `app/blueprints/mobile_exercise_history.py` +
   `app/services/mobile_exercise_history.py` now import `build_exercise_history` and are
   wired into `mobile_api`. This is **not** a violation — the gate
   (`tests/test_exercise_performance_history_architecture.py:312`) was correctly
   extended with an `APPROVED_CONSUMERS` allow-list — but the doc now contradicts the
   code.

**Action:** update CLAUDE.md's migration head reference, document the physical-budget
feature, and correct the exercise-history caller status.

---

## P4 — Maintainability / structural debt (no behavior bug)

- **`app/models.py` god-file** — 2096 lines, 71 `db.Model` classes in one file; the
  most-imported module and the merge-conflict epicenter. Splitting by domain is
  constrained by the single-file migration-autogen / `_user_child_models` conventions,
  so this is deliberate-but-growing. Watch item.
- **Fat blueprints not yet service-extracted** — `app/blueprints/social.py` (1222 lines)
  and `app/blueprints/training.py` (1005) have not had the service-extraction treatment
  the nutrition/workout/coach domains received. Next consolidation candidates.
- **Two architecture gates string-scan instead of AST-parse** —
  `tests/test_mobile_pump_check_architecture.py` and
  `tests/test_mobile_pump_check_comparison_architecture.py` lack `import ast` (19 of 21
  gate tests use real AST walking). String scanning is brittle (comments/strings can
  evade or false-trigger). Minor; upgrade for parity.
- **Capacity model fragile by design** — single gunicorn worker / 8 threads with
  synchronous blocking provider calls. Well-managed (`blocking_concurrency_slot` at 17
  call sites + boot invariants + `tests/test_capacity_invariants.py`), but 8 slow AI
  requests can still saturate the worker. Awareness only; caps horizontal headroom.

---

## P5 — Defense-in-depth nits (not exploitable)

- **[LOW, latent footgun] Opt-in ownership on S3 read/sign.**
  `s3_helper.get_object_bytes` / `generate_presigned_url` default
  `expected_user_id=None`, i.e. the owner check is skipped unless the caller passes it.
  Every current call site passes it (verified: `ai_coach.py:730`, `pump_checks.py:59`,
  `progress_physique/queries.py:192`, `mobile_pump_check_comparisons/service.py:398`,
  `validators.py:201`, `training.py:591`), and CSP `img-src` confines URLs to the app
  bucket — but a future caller that forgets the parameter gets an unscoped presign.
  Consider making ownership mandatory.
- **[INFO] Non-constant-time OAuth `state` compare** — `app/blueprints/wearables.py:101`
  (`returned != expected`). The `state` is a per-session random CSRF nonce, not a
  server-held secret; timing on a 32-byte urlsafe value is not practical. Negligible;
  listed for completeness.
- **[INFO] `style-src-attr 'unsafe-inline'`** — already a documented, accepted tradeoff
  (dynamic width styles); the `<style>`-injection vector is closed via nonce. No change
  recommended.

---

## What was verified clean

**Security (all 9 areas clean):** authorization/IDOR/ownership scoping; CSRF
(Origin/Referer + synchronizer token, constant-time compare; `mobile_api` correctly
Bearer-only exempt); SQL injection (all raw SQL parameterized); SSRF (exemplary —
scheme allowlist, public-IP enforcement, DNS-rebinding-resistant pinned-IP fetch,
per-hop redirect re-validation, credential-free killable subprocess); secret/token
handling (domain-separated HMAC subkeys, owner-bound MACs, `hmac.compare_digest`,
256-bit bearer tokens SHA256-at-rest); JWT (RS256 pinned, iss/aud/token_use/exp
validated, single-flight JWKS refresh, deleted-identity tombstone race correctly
closed); presigned URLs/S3 (server-chosen bucket, owner-scoped key grammar, fail-closed
`UnsafeObjectKey`); CSP/XSS (per-request nonce, SRI-pinned scripts, escaped
`innerHTML`); enumeration/rate-limiting (one wire answer per class, login fail-closed).

**Correctness/concurrency invariants confirmed in code:** lock order
(operation-row → user-row → plan-rows, no cycle); idempotency/exactly-once (meal,
workout checkpoint single conditional UPDATE, workout complete `expected_checkpoint_revision`
verified under the row lock before the already-completed preflight, `uq_*_day` durable
claims); billing zero-attempt settlement (`provider_attempt_possible is False` →
`CONFIRMED_NOT_BILLED`; aggregate physical reserve commits the attempt marker atomically
with the debit; budget refusal runs before the provider call); commit/rollback
(quest-challenge commit pattern correctly fixed, challenge completion a single guarded
atomic savepoint); day-key/timezone (no `date.today()`/`utcnow().date()`/`created_at.date()`
used as a day boundary; water "0→positive reset" hazard closed via persistent
`WaterLog.quest_fired` + conditional UPDATE). R6 forward-recovery core and Apple
dual-environment verify/fallback + reconciliation lease/generation fencing were checked
specifically and look correct.

**Architecture health:** exactly one Alembic head, zero `drop_column`/`drop_table` in any
`upgrade()` path (all 60 migrations AST-walked), all 46 post-R6 migrations declare
`expand_contract`; all 55 `*_pg.py` modules registered in CI; dependencies fully pinned;
feature-flag hygiene clean (single derived registry, no flag read from request/cookie/header,
no DARK flag defaulting ON); no orphan blueprints/routes; 19/21 AST gates genuine; low
skip/xfail noise.
