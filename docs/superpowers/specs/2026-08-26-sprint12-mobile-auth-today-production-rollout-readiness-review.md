# Sprint 12 — Mobile Auth + Today Production Rollout Readiness — Independent Review

Later same-day gate: live smoke/soak status is in
[parent document §29](./2026-08-26-sprint12-mobile-auth-today-production-rollout-readiness.md).
That gate did not re-run this review. Verdict after smoke: **WAIT — SOAK IN PROGRESS**.

- **Reviewer:** independent rollout-focused review (read-only)
- **Date:** 2026-08-26
- **Document under review:** [2026-08-26-sprint12-mobile-auth-today-production-rollout-readiness.md](./2026-08-26-sprint12-mobile-auth-today-production-rollout-readiness.md)
- **Authority SHAs:** backend `a6d6b2e60dc7718bd47590d64b5f74542294025c`; mobile `3386df37198ef0193c64fa4754a686357868f785` (mobile sources not in this worktree)
- **Scope:** rollout dimensions only. No application-code change. No flag flip, deploy, or PR5.

This review spot-checked cited backend facts against the current tree and the public GitHub runs. It did not re-run SSM, pytest, or Flutter CI, and it could not open the private mobile repository.

---

## Summary

The assessment is sound. Architecture and product code on backend `a6d6b2e` do not contain a P0/P1 auth-bypass, cross-user leak, concurrency-gate regression, fixture fallback, or Today-authority split. The historical Cognito-thread-exhaustion blocker remains closed (Hardening PR4 `34f8dc79` is an ancestor; login takes `blocking_concurrency_slot()` only around provider I/O, then writes the session outside the slot; refresh snapshots, releases the DB, then takes the slot).

The verdict **READY WITH CONDITIONS** is the correct one. It is **not** **READY FOR STAGED ROLLOUT**. Operational P1 observability is still open, authenticated production Today was not smoked, and native-auth ON distribution remains a no-go. Those conditions are already in the assessment; this review does not invent a product-code blocker on top of them.

The document does **not** overclaim live state: running SHA, CI #360 success, Deploy #227 success at 06:20Z, and the 06:21Z vs 10:40Z recreate gap are consistent with public GitHub evidence. It does **not** understate that `MOBILE_AUTH_ENABLED` is already ON — that is the headline. Abort and backend rollback are executable if operators follow the written steps (exactly `0`, compose recreate, confirm `404` not `401`, confirm `/health` 200). Mobile store rollback is correctly left unproven.

One factual error in the assessment (unreadable plan → 503) does not change the product contract or the verdict. Stale registry/docs remain an operator-confusion residual; the one-line `docs/ROLLOUT.md` pointer is a partial, not complete, fix.

---

## Issues (P0/P1/P2 structured)

### P0

None.

No auth bypass, no caller-supplied user/date selector on Today, no cookie path onto `/api/v1/today`, no fixture fallback in the production Today route, no second unguarded Today surface.

### P1

**P1-1 — Observability is insufficient for the documented HTTP abort path while `/api/v1` is already public.** (dimension 13; assessment already filed)

Confirmed in code and docs, not a new finding:

- `RUNTIME_METRICS_ENABLED` defaults to `0` (`app/config.py`). Assessment says host `.env` leaves it unset.
- `AuthOutcomes` is a no-op when metrics are off (`app/services/auth_contract.py` `record_outcome`). Login and refresh never call it even when metrics are on; only `require_mobile_auth` does.
- HTTP SLIs (`HttpOverload`, `HttpServerErrors`, `HttpLatency`) are also no-op when the flag is off (`app/observability.py`).
- `ThreadReserve` is sampled from the flush thread (`app/__init__.py` `_install_capacity_sampling`) **and** is live on loopback/CIDR-only `/health?deep=1`. CloudWatch names without HTTP SLIs are correctly treated as possibly stale.
- `docs/ROLLOUT.md` §2 still lists `RUNTIME_METRICS_ENABLED=1` as a prerequisite for any activation. Production has already skipped that prerequisite.

This remains the operational P1 that **blocks READY FOR STAGED ROLLOUT** (native-auth ON distribution / cohort expansion). It does **not** require flipping `MOBILE_AUTH_ENABLED` off by itself. The assessment’s condition 3 (turn metrics on **or** formally accept log-only abort for the low-volume soak) is the right containment for Stage 2/3, not a substitute for SLIs before a native cohort.

No other P1 was found.

### P2

**P2-1 — Assessment misstates the unreadable-plan contract.** (dimension 11, document error; product is correct)

§4 says “unreadable plan → 503 `TODAY_TEMPORARILY_UNAVAILABLE`”. The shipped code does the opposite on purpose: `_plan_content` swallows `ValueError`/`TypeError` and returns `None`, and the canonical resolver classifies that as `needs_attention` / `schedule_unavailable` on a **200**. Infrastructure faults are the 503 path (`TodayUnavailable`). Operators grepping production for 503 on a corrupt `plan_data` row will miss the real user-visible state. Does not change rollout: fail-closed still holds (not empty, not rest).

**P2-2 — Stale “blocked / production default OFF” text is wider than the three files the assessment lists.** (dimension 15)

Confirmed still stale:

- `app/feature_flags.py` `lifecycle=LIFECYCLE_BLOCKED` plus PR4-as-future prerequisite
- `docs/FEATURE_FLAGS.md` table row 9 and §9 “Blocked until PR4 merges”
- `docs/AUTH_CONTRACT.md` §6 still says `MOBILE_AUTH_ENABLED=0 (current production default)` and “Activation is blocked until PR4”

The one-line `docs/ROLLOUT.md` order-#9 pointer (added after the assessment) now tells operators the `blocked` label is stale and to follow the readiness document before native-auth. It still does **not** say the backend flag is already ON. An operator who only reads the table can still think Stage 1 remains to be executed. Condition 5 is therefore only partially closed.

**P2-3 — Log-only abort is executable at low volume and lossy under a burst.** (dimensions 13–14 residual)

`docker-compose.yml` caps web logs at json-file `10m × 3`. Abort in §17 depends on grepping those logs for 5xx/`AUTH_TEMPORARILY_UNAVAILABLE`/`refresh_reuse` until HTTP SLIs exist. That is enough for the current single-IP volume the assessment observed; it is not enough if a scanner storm rotates the evidence away. This is why P1-1 still blocks native expansion, and why accepting log-only must stay explicit and bounded to the soak.

**P2-4 — Worker “unhealthy” is explained by the image HEALTHCHECK, not by a dead RQ worker.** (dimension 17, assessment gap #6)

The image `HEALTHCHECK` probes `http://127.0.0.1:5000/health`. The worker service runs `python worker.py` and does not listen on 5000, so Compose will keep marking it unhealthy even when deep health reports `worker: alive`. Do not treat Compose STATUS as an abort signal for this soak. Informational; do not “fix” it as part of this rollout.

**P2-5 — Backend rollback is executable; a malformed `.env` edit is not auto-recovered.** (dimension 14 residual)

The written procedure is the right one: exactly `MOBILE_AUTH_ENABLED=0`, `docker compose up -d`, prove `GET /api/v1/today` is **404 not 401**, prove `/health` 200. That 404/401 check is the real gate (Compose may or may not recreate on `env_file` change depending on version; the probe does not care). Residual: this path is **not** the GitHub deploy health gate. `MOBILE_AUTH_ENABLED=` (empty) or `true` is a boot failure for the whole web process, so a sloppy rollback can take the site down with no automatic PREV_COMMIT revert. The assessment already says “exactly 0, not empty”; operators must treat step 4 as mandatory.

Store/TestFlight native-auth OFF rollback remains unproven, as stated. Sideload + backend-off containment is the only proven pair.

### Dimensions with no issue

| # | Dimension | Review |
|---|---|---|
| 1 | Auth bypass | None. `@require_mobile_auth` on `GET /today`; missing/malformed Bearer → 401 JSON `AUTH_SESSION_EXPIRED`; CSRF skipped only for `mobile_api`; cookie session cannot satisfy Bearer. Production unauthenticated probe reported as 401, matching flag-ON. |
| 2 | Cross-user leakage | None in code. Identity is `g.mobile_user.id` only; `build_today` has no date/owner override; tests prove query/header spoofing is inert and another user’s rest day does not leak. Production two-user probe correctly left as Stage 2, not a defect. |
| 3 | Blocking concurrency | PR4 controls present. Login wraps Cognito authenticate (+ optional provider refresh) in the shared slot and releases before DB commit. Refresh forbids lock+network in one function (AST). Overload → 503 `AUTH_TEMPORARILY_UNAVAILABLE` + `Retry-After: 15`. |
| 4 | Refresh loops | Backend reuse revokes the family. Mobile 3s refresh / coordinator serialization was not re-read here (private repo); nothing in backend contradicts the assessment. |
| 5 | JWKS failure | Transient `jwks_unavailable` → 503, session kept; single-flight + 60s cooldown. No CW counter (P2 in the assessment; accepted). |
| 6 | DB/thread exhaustion | Pool aligned with 8 threads in code/invariants. Not load-tested in production. Residual P2, as stated. |
| 7 | Backend flag ordering | Backend-before-native is correct **and already inverted vs the stale `blocked` label**. Do not enable again. |
| 8 | Mobile flag ordering | Compile-time default OFF in the registry; do not ship ON until conditions close. Not independently re-read in Flutter sources. |
| 9 | Backend-off containment | Backend 404 when flag OFF is tested. Mobile no-fixture containment accepted from the assessment’s 3386df3 tests; this review did not re-run them. |
| 10 | Fixture regression | Production Today route/service have no fixture/sample/demo fallback (architecture test). |
| 12 | Mobile startup failure | Not re-verified (private repo). Assessment’s config-failure screen / no crash-loop claim is not contradicted by backend evidence. |
| 16 | Secrets/logging | Mobile request log forces `user=-` and logs the route template, not the concrete path. No token logging found on the Today/auth boundary. Host `.env` mode 600 remains known M4. |

---

## Verdict agreement or dissent

**Agree with READY WITH CONDITIONS.**

Dissent from any reading that this is **READY FOR STAGED ROLLOUT**. Operational P1-1 is still open. The assessment already withholds that stronger verdict; this review keeps it withheld.

Checked against the four questions:

1. **Is READY WITH CONDITIONS justified?** Yes. Product P0 = 0 and product-code P1 = 0 on the backend SHA. Remaining work is operator smoke, soak, metrics-or-log-only, origin choice, and a native-auth OFF sideload artifact — not a code rewrite.
2. **Does it overclaim production state?** No. Public GitHub confirms backend SHA `a6d6b2e`, CI run 360 success, Deploy run 227 success. The assessment distinguishes proven (flag ON, unauthenticated 401, health 200) from unproven (authenticated Today body, TestFlight/Play binary, CW alarms, exact flag-flip time). It does not claim the 10:40Z recreate **is** the enablement instant.
3. **Does it understate the risk that `MOBILE_AUTH_ENABLED` is already ON?** No. That is the first reason the verdict is not “READY FOR STAGED ROLLOUT”. Pre-auth login/refresh are already on the internet; PR4 is the reason that is not still a P0 thread-exhaustion hole. Existing traffic from `85.107.65.28` is reported as owner-IP / not this assessment’s credentials — appropriately not treated as a proven native-auth ON store build. Residual: soak start is inferred from container recreate, so exposure may already be longer than 24h; the extra 24h is additional observation, not a claim that the surface was closed before 10:40Z.
4. **Are abort/rollback procedures executable?** Yes, with the caveats already written. Backend abort: SSM docker logs + deep-health `thread_reserve` + public `/health` + unauthenticated Today status. Backend rollback: `.env` `=0` + compose recreate + **404 vs 401** proof; minutes, not seconds; does not revert DB or issued-credential history. Native rollback: halt distribution, sideload OFF, optional backend-off containment; App Store/TestFlight rollback is **not** a procedure that exists yet. Do not use `www.axisaiapp.com` or `api.axisaiapp.com` as the native origin.

`docs/ROLLOUT.md` order #9 now points at the assessment. Treat that pointer as operator routing, not as closure of condition 5, and not as permission to distribute a native-auth ON build.

Do not start PR5. Do not “fix” worker Compose health as a soak abort. Do not flip `MOBILE_AUTH_ENABLED` again in this window.

---

## Status: open issues count

**Open: 1 P1, 5 P2. P0 = 0.**

READY FOR STAGED ROLLOUT remains blocked on **P1-1**. READY WITH CONDITIONS stands. Native-auth ON distribution remains **NO-GO** until the assessment’s §26 / §28 conditions are actually closed, not merely documented.
