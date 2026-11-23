# Fitness Coach (FitX) — Triage & Security Review

**Date:** 2026-11-23
**Commit reviewed:** `16cb17c` on `claude/amazing-dijkstra-kd9b12`
**Method:** Three parallel read-only deep-dive agents — (1) security vulnerabilities, (2) backend correctness / data-integrity, (3) overall structure & code health — each tracing the real source (cited `file:line`), not CLAUDE.md. The one concrete code-level bug (#1) was additionally hand-verified against the source.

---

## Executive summary

**The codebase remains in excellent shape.** All three reviewers independently reached the same conclusion: this is an unusually well-hardened application with deep evidence of prior security and correctness triage.

- **No Critical or High-severity issues were found** in any of the three dimensions.
- Authentication, JWT validation (token-use confusion prevented, leeway pinned to 0), session/refresh rotation with reuse detection, authorization/ownership scoping, SSRF defenses, CSRF, CSP, secret/PII hygiene, the transaction/lock-ordering graph, the money/allowance/subscription paths, and date/leap-year handling are all implemented to a high standard.
- Findings are **1 latent (currently-gated) correctness bug** plus a set of **Low / Medium process, hygiene, and documented-debt items**. The highest-value action is cheap repo hygiene (removing stale cross-branch docs) and a feature-flag triage pass, not an emergency fix.

### Findings at a glance

| # | Severity | Area | Type | Status |
|---|----------|------|------|--------|
| 1 | Low (latent → High if branch reopens) | `/meal-log/review` AI-advice branch | Correctness | Confirmed, currently unreachable |
| 2 | Medium | Stale committed cross-branch docs contradict migration state | Structure / docs | Confirmed |
| 3 | Medium | Feature-flag review discipline lapsed (19/22 flags past `review_by`) | Process / tech-debt | Confirmed |
| 4 | Low | Menu fetch permits plaintext `http://` outside a consent op | Security (confidentiality) | Confirmed |
| 5 | Low | `premium.py` legacy "remaining" counters wrong when ledger active | Correctness | Confirmed, flag-gated |
| 6 | Low | Service-layer volume / large hot modules (`ai_coach.py` 1667 lines) | Structure | Confirmed |
| 7 | Low | CSP `style-src-attr 'unsafe-inline'` (accepted tradeoff) | Security hardening | Confirmed, documented |
| 8 | Low | Legacy `/update-weight` lost-update + same-day overwrite | Correctness | Documented debt, intentional |
| 9 | Low | Stale comments / retired flags still parsed | Structure | Confirmed |

---

## Detailed findings

### 1. [Low today — latent; becomes High if the branch reopens] `/meal-log/review` AI-advice branch: `round(None)` 500 + fabricated 2000 kcal target

**File:** `app/blueprints/nutrition/meallog.py:578`, prompt builders at `:609` (EN) / `:626` (TR).

```python
target = last_session.target_calories if last_session else 2000   # :578
...
f"Daily calorie target: {round(target)} kcal\n"                   # :609 / :626
```

- When a user **has** a `UserSession` but `target_calories` is NULL (very common — any user who started but never completed the profile calc), `target` becomes `None`, and `round(None)` raises `TypeError` → **HTTP 500**.
- When there is no session at all, the model is handed a **fabricated `2000 kcal` / goal `"genel sağlık"`** target — the exact F3a "fabricated structured target" pattern the `nutrition_targets` refactor set out to eliminate. (The *published* JSON `target` field at `:592` is correctly `None`; only the AI advice prompt is computed against the fabricated target.)

**Why it's Low today:** the AI branch (`:595–634`) is currently unreachable. Reaching it needs `meals` non-empty (`:561`), but a non-empty today's `MealLog` makes `fatsecret_boundary.intake_unproven()` return True, which early-returns at `:585` (None-safe, uses `configured`). So the branch is dead code right now.

**Why it still matters:** the comment at `:584` states this review branch "reopens with no other change" once attested provenance lands / `UNCOVERED_PROVIDER_PERSISTENCE_REF` is set. At that moment it becomes a reachable 500 (null target) **and** a fabricated-target advice path.

**Fix (already in hand):** the None-safe `configured` is computed two lines above (`:575`). The prompt should use `configured.calories` and omit the target line (or say "not set") when `configured is None`, instead of the `2000`/`None` `target`. Trivial, self-contained, no schema change.

---

### 2. [Medium] Stale committed cross-branch coordination docs contradict the current migration state

**Files:** `axisai-d1-migration-reconcile-01/INTEGRATION.md`, `axisai-d1-r2-test-seam/REMEDIATION.md`, `axisai-mon02-be03b-r2-integration/INTEGRATION.md`, `axisai-priv-app-d1-be-01/IMPLEMENTATION.md` (last touched 2026-10-28, commit `61e2a13`).

These are branch handoff notes at the repo top level that reference developer-machine paths (`/Users/yusuf/...`) and assert a migration state that is now **wrong**: they call `d1c0a15e0001` the single head with "52 reachable revisions", but `d1c0a15e0001` is now a mid-chain revision and the real single head is **`ad17de1e0001` with 66 revisions** (verified: full-ancestor traversal, zero orphans, one legitimate merge revision `f8a9b0c1d2e3`).

**Risk:** an engineer (or an agent) trusting these docs reasons about the wrong migration graph/head.

**Fix:** delete them, or archive into `docs/` with an explicit "historical/superseded" banner. (By contrast `release/completion-capabilities` and `qualification/{f01-d1,r1-recovery}` are intentional — the release manifest is read by the Dockerfile at `/app/COMPLETION_CAPABILITIES`, and the qualification artifacts are committed on purpose — keep those.)

---

### 3. [Medium] Feature-flag review discipline has lapsed (19 of 22 flags past `review_by`)

**File:** `app/feature_flags.py` `ROLLOUT_FLAGS`.

As of 2026-11-23, 19/22 `review_by` dates are overdue (five at `2026-10-01`, one `2026-09-01`, the rest `2026-11-01/-13/-17/-21`). The registry's own contract is "decide by then or extend deliberately." Combined with nearly all 22 flags being default-OFF `shipped_dark` / `staging_only`, this is genuine flag sprawl: a large volume of merged-but-dark code accumulating without activation or cleanup — both a maintenance liability and a concentration of rollout risk.

**Fix:** run a flag-triage pass — enable-and-remove the ready ones, re-date or remove the rest. The 19 overdue reviews indicate the process stalled.

---

### 4. [Low — confidentiality only] Menu fetch permits plaintext `http://` outside a consent operation

**File:** `app/services/menu_remote.py:242-249`.

`fetch()` only enforces HTTPS when `ai_consent.current_operation() is not None`. Web menu scans (no active consent operation) may fetch `http://` public URLs, so the fetched menu content travels in cleartext.

**This is not an SSRF hole** — every hop still goes through the IP-allowlist + pinned-adapter + per-hop redirect revalidation path, which blocks all internal/metadata/private/loopback addresses, and the fetch runs in a credential-free subprocess with byte/time budgets. Confidentiality-only.

**Fix:** enforce HTTPS unconditionally for the menu fetcher, or document why the web path is exempt.

---

### 5. [Low — flag-gated] `premium.py` legacy "remaining" counters are wrong while the allowance ledger is active

**File:** `app/services/premium.py:162` (`remaining_ai_plans`), `:172` (`remaining_ai_chats`).

When `ai_allowances.execution.enabled()` is True, `reserve_ai_quota` returns True without touching the legacy `user_metadata` counter, but `remaining_ai_plans()` / `remaining_ai_chats()` still read that legacy counter — so they report the full free allowance while the real limit lives in the ledger's `allowance_view`.

Harmless in prod today (ledger flag default OFF), but any consumer reading the legacy "remaining" alongside an active ledger would see a wrong number.

**Fix:** route the "remaining" readers through the ledger view when `execution.enabled()`, or clearly mark the legacy readers as not authoritative under the ledger.

---

### 6. [Low] Service-layer volume / large hot modules

**Files:** 307 `.py` under `app/services/` (115 top-level), 42 blueprints, 66 migrations. Largest modules: `ai_coach.py` (1667 lines, still re-exporting legacy names), `coach_plan_tools/grounding.py` (1299), `mobile_auth.py` (1096), `ai_allowances/service.py` (950), `coach_plan_tools/executor.py` (938); `social.py` blueprint (1228 lines) is almost entirely dead behind `launch_social.SOCIAL_AVAILABLE=False`.

Not a bug — the per-feature "canonical owner" package style is deliberate and well-tested — but it multiplies module count and cognitive load.

**Fix (when convenient):** decompose `ai_coach.py` (overdue); consider moving the fully-dark `social.py` behind a clearly-labeled `legacy/` boundary.

---

### 7. [Low — accepted/documented] CSP `style-src-attr 'unsafe-inline'`

**File:** `app/hooks.py:67`.

Inline style attributes are globally allowed (needed for dynamic progress-bar widths). A narrow CSS-injection / UI-redress vector would survive *if* attacker-controlled data ever reached a `style="…"` attribute. Jinja autoescape is on and there is no `|safe` in any template, so this is **not currently reachable**; it is explicitly documented as an accepted tradeoff in the same file. Listed only for completeness.

---

### 8. [Low — documented, intentional] Legacy `/update-weight` lost-update + same-day overwrite

**File:** `app/services/weekly_checkin/service.py:193` (`record_legacy_weight_update`).

Takes no owner lock and overwrites the weight of any row created in the current Istanbul day — including a full check-in (clobbering its reported weight). Both defects are called out in the module docstring as "kept on purpose"; the native check-in path correctly avoids this via `reload_locked_owner`, and the web `/checkin` SQL is intentionally pinned byte-identical. On the record only — not a new bug.

---

### 9. [Low] Stale comments / retired flags still parsed

- `app/config.py:140` comment says "eight rollout flags" ("Sekiz rollout bayrağının…") — there are now 22. `FEATURE_FLAG_KEYS` is correctly *derived* from the registry, so behavior is fine; only the prose is wrong.
- Retired flags `UIUX_TODAY_V2_ENABLED` and `UIUX_NAV_V2_ENABLED` remain in `ROLLOUT_FLAGS` and are read each boot though they no longer select a user-reachable branch (documented intentional — to stop an operator thinking 0/1 restores legacy). Acceptable, but they inflate the surface and the overdue-review count.
- CLAUDE.md is ~196 KB (332 very long lines) — a single flat feature-ledger; an onboarding/context cost in itself.

---

## Suggested priority order

1. **#2 (delete/archive the four stale `axisai-*` handoff dirs)** — cheap, removes an active source of wrong migration-graph beliefs. Do first.
2. **#1 (fix `/meal-log/review` before the branch reopens)** — trivial, self-contained, and prevents a guaranteed 500 + the fabricated-target regression the moment attested provenance lands. Fix proactively rather than waiting for reactivation.
3. **#3 (feature-flag triage pass)** — enable-and-remove ready flags, re-date or drop the rest.
4. **#5, #9 (ledger "remaining" readers; stale comments)** — small correctness/hygiene cleanups.
5. **#6 (`ai_coach.py` decomposition)** — schedule, not urgent.

Items #4, #7, #8 are documented/accepted tradeoffs — address only if the surrounding constraint changes (e.g. #4 when the menu path leaves the consent-op gating, #1's twin F3a concern when provenance attestation lands).

---

## Areas verified healthy (no action)

**Security:** RS256 pinned (no alg confusion); `token_use` enforced (`aud` for ID vs `client_id` for access); expiry leeway pinned to 0 both paths; JWKS forced-refresh single-flight + cooldown-gated; refresh rotation with reuse detection + credential-epoch fence; every data query checked is `user_id`-scoped; opaque API tokens are domain-separated HMAC digests owner-bound through the MAC (not replayable cross-account); S3 presigned/get_object callers all pass `expected_user_id` with path-segment ownership checks; SSRF defense is strong (URL validation, public-IP allowlist, connect-time peer re-verification, per-hop redirect revalidation, isolated credential-free subprocess with budgets); CSRF two-layer (Origin/Referer + per-session synchronizer token), `mobile_api` correctly Bearer-exempt, OAuth callback GET protected by session-bound `state`; no raw SQL from user input; upload data-URLs validated via Pillow `verify()` + format allowlist + pixel-bomb cap; the large `social.py` IDOR surface is neutralized by `SOCIAL_AVAILABLE=False` 404ing the blueprint ahead of auth. No regression of the `TRIAGE_FINDINGS_2026-10-01.md` fixes.

**Correctness:** `plan_replacement` / `plan_owner_lock` / `plan_mutation` lock order (user→plan rows), staleness guard, `expected_plan` precondition under lock, idempotency via DB unique constraint, undo byte-fingerprint precondition all consistent; `ai_allowances` reserve/finalize idempotency, conditional-UPDATE bucket math with CHECK backstops, billed-release cap, lazy expiry, owner-lock serialization all check out; `workout_completion`/`workout_session`/`execution` single atomic completion transaction, `uq_workout_completion_day` race-loser reconciliation, revision-gated checkpoint replay-before-mutate, fixed session→day→User lock order; `account_deletion` + `_purge_user` hold fence, identity-before-purge ordering, tombstone-with-purge, storage-release-before-row-delete, full cascade coverage; `apple_reconciliation` lease fencing + generation compare + SKIP LOCKED; `provider_retention` "retained aggregate once" rule correct (disjoint folded/unfolded sets across all readers); `account_eligibility.age_on` leap-year + `born >= today` guards correct; water quest "0→positive" dedup is a durable per-day `WaterLog.quest_fired` conditional UPDATE held to the award commit; `award_xp` column-level `FOR UPDATE`; NULL-vs-0 discipline clean (absence→`None`, measured zero→`0`), latest-row selectors all carry the `created_at DESC, id DESC` tiebreak.

**Structure:** single Alembic head `ad17de1e0001`, 66 revisions, zero orphans; grandfathered pre-R6 digest-pinned migrations + all post-R6 declaring `expand_contract`; the 2 unguarded `create_table` migrations sit before the fresh-stamp point so never re-run; all 69 `tests/test_*_pg.py` present in the ci.yml explicit PG lists (the "silent never-runs" hazard is clean, with a guard test); every route lacking an auth decorator is genuinely public; all 41 blueprints imported/registered, no double registration; fail-closed on missing `DATABASE_URL`/`SECRET_KEY`/`COGNITO_TOKEN_ENC_KEY` in prod; strict flag parser rejects `true`/`yes`/`"1 "`; DB pool explicit and boot-invariant-checked against `FITX_WEB_THREADS`; retired `MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS` actively rejected at boot; `deploy.yml` gated via `workflow_run` on CI success + head-sha match with `/health?deep=1` rollback gate; requirements fully `==`-pinned; exactly 1 TODO/FIXME/HACK hit in `app/` and it's a false positive.

---

*Generated by an automated triage routine (three read-only deep-dive agents). No files were modified and the app was not run during this review.*
