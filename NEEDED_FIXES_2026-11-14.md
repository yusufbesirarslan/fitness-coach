# FitX — Needed Fixes (Triage Report)

**Date:** 2026-11-14
**Scope:** 3 parallel read-only deep-dive audits — (1) Security vulnerabilities, (2) Correctness / concurrency / transactions, (3) Structure / code-health / recent V1-launch changes.
**Method:** Adversarial trace of attack surfaces and high-risk flows. Every reported item was verified against the current source (cited `file:line`), not against CLAUDE.md. The two highest-value actionable items were additionally re-verified by hand.
**Commit reviewed:** `17f522b` on `claude/amazing-dijkstra-5ar4lk`.

---

## Headline

The codebase remains **mature and defense-in-depth**. **No Critical or High-severity issues were found** in any of the three audits. Authentication/authorization, owner-bound HMAC tokens, SQL parameterization, SSRF defense, CSRF/CSP, secret hygiene, and the transaction/locking surfaces are all implemented to a high standard, with extensive evidence of prior hardening.

The findings below are **1 Medium launch-hygiene item**, **1 Medium correctness item (known & accepted)**, and **4 Low** hardening / cleanup items. None is an actively exploitable vulnerability at `17f522b`.

| # | Severity | Area | Type | Confidence |
|---|----------|------|------|------------|
| 1 | Medium | RC2 temporary Apple bypasses not yet removed (their own "remove at D1 launch" contract) | Launch hygiene / latent auth | Confirmed |
| 2 | Medium | `record_legacy_weight_update` — unlocked lost update + same-day check-in clobber | Correctness / data integrity | Confirmed (accepted debt) |
| 3 | Low | `s3_helper` ownership check is opt-in (`expected_user_id=None`) | Security footgun | Confirmed |
| 4 | Low | `subscriptions.entitlement()` raises list-wide on one bad row | Reliability (latent) | Plausible / not reachable today |
| 5 | Low | Web `/checkin` lost update (native path already fixed) | Correctness | Confirmed (accepted debt) |
| 6 | Low | Orphaned branch-process doc dirs committed at repo root | Repo hygiene | Confirmed |

> Low-noise items already documented and accepted in-code are listed so they aren't re-triaged next cycle, not as new discoveries.

---

## Findings

### 1. [Medium] RC2 temporary Apple bypasses still present — their documented "remove in the D1 launch commit" obligation is unmet

**Confirmed.** `app/services/subscriptions.py:229-248` (`_prerelease_401_subject`, Production→Sandbox HTTP-401 downgrade) and `:72-98` (`_qualification_subject` named-cohort purchase admission); `app/services/account_eligibility.py:76-91` (`qualification_subject`, `prerelease_401_window_open`); `app/config.py:324-408`.

CLAUDE.md marks both "RC2 pre-release Production lock" and "RC2 named qualification cohort" as **"TEMPORARY, remove in the D1 launch commit."** The recent commit range is the V1/D1 launch integration work, yet no commit removes these paths and the code remains shipped (`git log --oneline -40` shows no RC2 removal).

**Why it matters:** a Production→Sandbox 401 downgrade and a hard-coded named-UUID purchase-admission cohort are latent subscription-auth bypasses. They are currently **default-OFF and fail-closed** — `_prerelease_401_deadline` (config.py:347) returns `None` on invalid/missing/passed/>30-day dates, and `prerelease_401_window_open` additionally requires `ENABLED is True`, a tz-aware future deadline, a non-empty control list, and `qualification_open()` — so they are not exploitable as shipped. The risk is precisely what the "remove at launch" note guards against: carrying a dormant auth bypass into GA.

**Fix:** delete both RC2 code paths and their config keys in the launch commit as documented. If launch slips, add a boot assertion that both flags are OFF in Production so the bypass cannot be enabled by stray host env.

### 2. [Medium] `record_legacy_weight_update` — unlocked read-modify-write + same-day check-in clobber (web `POST /update-weight`)

**Confirmed** (and self-documented as accepted debt). `app/services/weekly_checkin/service.py:193-214`.

The legacy web weight-update path takes **no owner lock** (read-modify-write ⇒ lost update under two concurrent updates) and overwrites the weight of **any row created in the current Istanbul day — including a full `WeeklyCheckIn`** (`today_checkin.weight = weight` at `:209`), silently clobbering a richer record. The docstring (`:194-199`) states both are "known defects kept on purpose"; the native check-in API (LP16-A/B) already routes through the locked `apply_body_weight` + `claim_submission` primitives.

**Trigger:** two near-simultaneous web weight updates, or a web weight update on the same Istanbul day the user submitted a full check-in.

**Fix:** route the web path through the same locked primitives as native (`reload_locked_owner` + `apply_body_weight`), and insert a weight-only row rather than mutating a same-day full check-in. Low blast radius (single self-reported field), hence Medium not High.

### 3. [Low] `s3_helper` ownership check is opt-in

**Confirmed.** `s3_helper.py:286` (`get_object_bytes`) and `:308` (`generate_presigned_url`) both default `expected_user_id=None` and skip the `_key_belongs_to` owner check when it's omitted.

Every current caller passes the id and is safe (`pump_checks.py:62`, `validators.py:201`, `mobile_pump_checks/service.py:400`, `progress_physique/queries.py:192`, `training.py:598`, `models.py:294`). The footgun: a *future* caller presigning an LLM- or client-supplied key without the id gets a cross-user IDOR.

**Fix:** make `expected_user_id` a required positional argument (all callers already pass it), or add an AST/test guard forbidding the `None` default at these two sites.

### 4. [Low] `subscriptions.entitlement()` can raise list-wide on a single malformed row

**Plausible, not reachable today.** `app/services/subscriptions.py:151` — `[_eligible_view(user, row, now) for row in rows]`. If any one row raised `SUBSCRIPTION_INVALID_STATE` (status ∉ {1..5}, or null `last_verified_at`/`expires_at`), the whole comprehension raises; `entitlement()`'s guard only catches `SQLAlchemyError`, not `SubscriptionError`, so a user with one bad row would lose Premium even with another ACTIVE row.

Not reachable now because every writer (`persist_verified`/`_fields`) constrains `int(item.status)` to 1..5 and always sets the timestamps. It would become a real "one bad row denies Premium" bug if any future writer could persist a partial row.

**Fix (defensive):** skip/scrub per-row inside the comprehension instead of letting one row raise list-wide.

### 5. [Low] Web `/checkin` lost update (accepted debt; native path fixed)

**Confirmed.** The web `/checkin` path deliberately does not call `reload_locked_owner`, so an equal-to-stale weight emits no UPDATE and an earlier-serialized check-in's weight can remain current. Recorded debt in LP16-B; the native check-in path already applies the fix. Fold into the fix for #2 — same primitive.

### 6. [Low] Orphaned branch-process doc directories at repo root

**Confirmed** (grep finds no references outside themselves/CLAUDE.md). `axisai-d1-migration-reconcile-01/INTEGRATION.md`, `axisai-d1-r2-test-seam/REMEDIATION.md`, `axisai-mon02-be03b-r2-integration/INTEGRATION.md`, `axisai-priv-app-d1-be-01/IMPLEMENTATION.md` — each a single tracked, unreferenced branch-process draft. Repo-root clutter.

**Fix:** move into `docs/` or delete. **Do not touch** `qualification/f01-d1/*` and `release/completion-capabilities` — those are referenced by `tests/test_f01_activation_monitoring.py` and `tests/test_completion_fence.py` respectively.

---

## Cross-checked clean (verified against source — do not re-dig next cycle)

**Security.** SSRF menu fetcher (`menu_remote.py`) is best-in-class: `public_ip()` blocks private/reserved/loopback/link-local incl. the 169.254.169.254 metadata endpoint, IPv4-mapped-IPv6, 6to4/Teredo/NAT64; per-hop IP pinning with `getpeername()` re-verify before sending bytes (no DNS rebinding); HTTPS→HTTP downgrade refused; runs credential-free in a killable subprocess. Owner-bound HMAC token schemes (`nutrition_native/tokens.py`, `mobile_nutrition/identity.py`, supplements, menu-item proofs, entry tokens) bind owner through the MAC only, use `hmac.compare_digest`, domain-separate per class, and resolve by scanning the authenticated user's own row ids; no request-supplied `user_id` is trusted. No f-string/%/.format user data in SQL; the 4 dynamic `text(f"...")` sites interpolate only validated ints / literal choices / SHA-validated revisions / a constant allowlist. JWT validation pins RS256, checks `token_use="access"`, issuer, audience; mobile request auth triple-binds `sub == family.cognito_sub == user.cognito_sub`. Image uploads fully re-encode + strip EXIF, block polyglots/decompression bombs. Fernet dev-key fallback is prod-boot-gated.

**Concurrency.** Lock order holds everywhere checked (operation → user → plan rows; session → day → artifacts → user); no writer takes a User lock while holding a plan lock; no provider I/O (Bedrock/S3/FatSecret/Cognito) under any DB lock. AI allowance ledger reserve/finalize/release, zero-attempt sealing, billed-release caps, EXPIRED reconciliation all correct. Water funnel toggle-proof via `UPDATE … WHERE quest_fired=false`. Apple reconciliation claim commits before provider I/O and fences on lease equality; revocation terminal-bypass closed; monotonic signed-timestamp guard intact. No day-key derivation outside `app/timeutil` (Istanbul discipline intact).

**Recent V1 work.** Teen consent gate (#514) `_require_grant_cohort` is correctly restrict-only, owner-locked, `affirmative_grant`-only, minors limited to `training.plan`; `minority_signal` age check (`is int` on an Integer column) sound. Coach prompt + memory-tool medical-solicitation removal internally consistent; step-4 adaptive anchor byte-identical. `launch_social.py` single-owner boundary has no fail-open path. FatSecret replay false-zero fix (#508) correctly routes barcode-add replay through `published_nutrients` (`None if value is None`). Migrations: single head `f01a4e000003`, 65 revisions, no fork. Boot fail-open switches (`FITX_DB_UPGRADE_FAIL_OPEN`, `LOGIN_FAIL_CLOSED=1`) default safe. No bare `except:` in `app/`.

---

## Recommended order

1. **#1 (RC2 removal)** — launch-blocking hygiene; it's a documented deletion, not new design. Do it in the launch commit, or add the OFF-in-Production boot assertion if launch slips.
2. **#2 + #5 (legacy weight update)** — one shared fix: route the web paths through the locked native primitives.
3. **#3 (S3 ownership arg)** — make `expected_user_id` required; mechanical, all callers already comply.
4. **#6 (repo cleanup)**, then **#4 (defensive per-row entitlement)** as low-priority polish.
