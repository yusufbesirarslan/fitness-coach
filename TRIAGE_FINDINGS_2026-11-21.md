# FitX — Bug & Security Triage (2026-11-21)

Automated triage run. Three parallel deep-dive agents reviewed the codebase:

1. **Security** — auth boundaries, IDOR / owner-bound tokens, SSRF (menu fetch), CSRF/CSP, secrets & injection, S3 key handling, rate-limit abuse, image handling.
2. **Concurrency & billing** — lock ordering, idempotency/replay, Apple subscriptions + allowance ledger, D1 consent fencing, account deletion & legal hold.
3. **Structure & correctness** — domain logic, timezone discipline, error handling, API contract parity, feature-flag consistency, overall architecture.

## Headline

**No critical or high-severity defects were found.** The codebase is unusually well-hardened: the high-risk surfaces (SSRF, JWT, IDOR token schemes, image decoding, lock ordering, idempotency, billing) are implemented with care and backed by unique indexes, row locks, HMAC domain separation, and PG-only concurrency tests. Everything below is **low-severity hardening, defense-in-depth, or informational.** Line references were spot-verified against the working tree.

Severity legend: 🟡 LOW (worth fixing) · ⚪ INFO/ops (note, likely accept).

---

## Recommended fixes (priority order)

### 🟡 FIX-1 — Make S3 ownership scoping mandatory (latent IDOR footgun)
**Files:** `s3_helper.py:286` `get_object_bytes(key, expected_user_id=None)`, `s3_helper.py:308` `generate_presigned_url(key, expires_in=3600, expected_user_id=None)`

Ownership is enforced **only when `expected_user_id` is passed**. All 8 live call sites pass it today, so **there is no current bug** — but one of those is the LLM-tool path (`ai_coach.py:758`) where the S3 key can be model-influenced. The only thing preventing a cross-user S3 read is each caller remembering an *optional* kwarg. A future caller that omits it = silent IDOR.

Note `s3_helper.py:209 delete_managed_object(key, expected_user_id)` already makes it **required** — the read/presign primitives are simply inconsistent with the delete primitive.

**Fix:** make `expected_user_id` a required positional on `get_object_bytes` and `generate_presigned_url` (or default-deny when absent), matching `delete_managed_object`. Update the 8 call sites (all already pass it).

Live call sites (all currently correct): `pump_checks.py:62`, `validators.py:201`, `mobile_pump_checks/service.py:400`, `progress_physique/queries.py:192`, `mobile_pump_check_comparisons/service.py:398`, `ai_coach.py:758`, `training.py:598`; `models.py:294` loads the owner's own object.

---

### 🟡 FIX-2 — Non-deterministic "latest staged PendingAction" selector (missing `id` tiebreak)
**Files:** `app/services/ai_coach.py:325` (log_meal commit), `app/services/ai_coach.py:475` (log_workout commit)

Both select the row to commit with `.order_by(PendingAction.created_at.desc()).first()` — no `id` secondary sort. If a user has two staged actions written in the same microsecond, `.first()` is non-deterministic and may commit the wrong one (persists the non-latest staged entry; the other is deleted). User-visible, but not a double-write or corruption.

This is the exact class that triage 2026-10-01 #3/#4 fixed for `UserSession`/`TrainingPlan`/`WeeklyCheckIn`. **There is already a correct precedent in the same file** — `ai_coach.py:536` uses `.order_by(PendingAction.created_at.desc(), PendingAction.id.desc())`. Lines 325 and 475 were simply missed.

**Fix:** append `, PendingAction.id.desc()` to the `order_by` at lines 325 and 475.

---

### 🟡 FIX-3 — Non-finite macros can reach the mobile nutrition wire
**File:** `app/services/mobile_nutrition/serialization.py` — `_amount()` returns `None if value is None else float(value)`

A stored `float('nan')`/`inf` macro passes straight through, and Flask `jsonify` emits the non-standard `NaN`/`Infinity` tokens, which strict clients (Dart/Swift) reject → a parse failure instead of a clean `null`. In practice all canonical writers sanitize via `clamp_serving_macros` / `_finite_nonnegative`, so this is **defense-in-depth only**.

**Fix:** fold non-finite values to `None` in `_amount` (mirror `_finite_nonnegative`): `if value is None or not math.isfinite(float(value)): return None`.

---

### 🟡 FIX-4 — Unbounded legacy query in `checkin_history`
**File:** `app/blueprints/tracking.py:319-322`

`WeeklyCheckIn.query.filter_by(user_id=...).filter(yogunluk.isnot(None)).order_by(created_at.asc()).all()` has no `.limit()`. User-scoped so not catastrophic, but check-ins accumulate (~52/yr) and this legacy route still feeds Today/Home, while the canonical `progress_history` read is bounded to 12.

**Fix:** add a `.limit(N)` matching the canonical window, or point Today/Home at the bounded read model.

---

## Informational / accept-or-confirm (no code change recommended now)

### ⚪ INFO-1 — `complete_workout` revision check precedes the already-completed preflight
`app/services/workout_completion/service.py:130-145` — the PR5 `expected_checkpoint_revision` mismatch is evaluated under the session row lock **before** the `already_completed_today` replay short-circuit. A client replaying a completion whose declared revision has since drifted gets a 409 revision-conflict instead of an idempotent `ALREADY_COMPLETED`. **Fail-closed, no bad write** — a retry-ergonomics nit. The ordering is load-bearing (the revision guard closes the silent-progress-loss gap); **recommend leaving as-is** unless product wants replay-wins semantics.

### ⚪ INFO-2 — Web menu scan is an (SSRF-safe) plaintext fetch proxy
`app/services/menu_remote.py:242 fetch()` requires HTTPS only when an AI-consent operation is active (`:247`); the web paths `/api/proxy/scan-menu` and `/api/menu/analyze` (`menu.py:47/64`, both `@require_auth` + rate-limited) run with no operation, so `http://` public URLs are fetched. **SSRF itself is fully closed** (see "Verified secure"). Abuse surface is small: auth required, SCRAPE/AI rate limits, content-type allowlist, 3 MB cap, 5-redirect cap, no body relayed verbatim. Note only.

### ⚪ INFO-3 — `/health?deep=1` flag/capacity disclosure relies on nginx restriction
Flag names and capacity/thread-reserve blocks are exposed on the deep health endpoint (documented as intended, inner-network restricted). Enforcement is at **nginx, not in-app** — if the reverse-proxy restriction is ever misconfigured, the flag inventory leaks. **Ops action:** confirm the nginx restriction is present in prod.

### ⚪ INFO-4 — Per-username login throttle enables targeted rate-limit DoS
`app/blueprints/auth.py:398` limits login `15 per 15 minutes` keyed on username with `deduct_when=401`. An attacker who knows a victim's username can keep that account throttled with bad-password attempts. Standard brute-force-protection tradeoff; documented. Availability consideration only.

### ⚪ INFO-5 — Hydration nudge averages tracked days, not calendar days
`app/services/analytics_engine.py:221-244 _check_hydration` — `days = len(rows)` counts only days with a `WaterLog` row, so `avg_cups` is over logged days, not the 7-day window. An intermittent logger can escape a low-hydration nudge. Arguably intentional (avoids false positives for untracked days, documented "Hiç kayıt yoksa sessiz"). Behavior note.

### ⚪ INFO-6 — `clamp_serving_macros` passes through density-only violations
`app/services/nutrition_pipeline.py:394-418` — when `check_serving` rejects for a non-absolute-cap reason (e.g. `caloric_density_exceeds_900`, `macro_weight_exceeds_serving`) and no per-dimension absolute cap is exceeded, `ratios` is empty → `scale=1.0` → values returned unchanged (except the separate `calories_exceed_macro_energy` rebase). Likely acceptable since the field-case caps are the real targets; worth a clarifying comment or an explicit density clamp if these values can reach the canonical `MealLog`.

---

## Verified secure / correct (do NOT re-flag)

**Security**
- **SSRF (menu_remote.py):** `validate_url` blocks userinfo/`@`/`%`/non-80-443 ports/control chars; `resolve` requires **all** A/AAAA records public (handles IPv4-mapped, 6to4, teredo, NAT64, loopback, link-local, reserved) and caps record count; `pinned_adapter` connects the **literal resolved IP** and re-checks `getpeername()` == resolved IP (**DNS rebinding closed**); every redirect hop re-validates+re-resolves; HTTPS→HTTP downgrade blocked; credential-less subprocess; no URL/body serialized on error. Decimal/octal/hex IP tricks caught (resolved-address check).
- **JWT (cognito_jwt.py):** RS256 only (no alg-confusion/`none`), `token_use`/`iss`/`aud`(id)/`client_id`(access)/`exp` all checked; JWKS forced-refresh is single-flight + cooldown (thread-exhaustion closed). `auth_contract.py` pins `token_use=access` for requests / `id` for login, leeway=0 both paths; retired skew key hard-fails boot.
- **Auth boundaries:** `mobile_auth.authenticate_access` fails **closed** on storage errors (503), re-validates the Cognito access token, enforces `sub == family.cognito_sub == user.cognito_sub`. Web `require_auth` binds the cookie session to a freshly-verified token sub and asserts identity equality. `session.clear()` before `login_user` (fixation closed); login fail-closed on Redis loss (`LOGIN_FAIL_CLOSED` default True).
- **Owner-bound tokens** (nutrition identity, nutrition_native, menu-item proof, pump-check/comparison ids): HMAC-SHA256 under per-class domain-separated subkeys of `SECRET_KEY`, owner bound via MAC only, `hmac.compare_digest`, 144-bit width, TTL + bounded future-skew. Pump-check `public_id` is a random 256-bit nonce HMAC.
- **IDOR:** comparison service scopes **every** query by `user_id` and re-checks source-image ownership; the many `db.session.get(...)` loads in `social.py` are in the **social blueprint, 404-gated entirely in V1** (`launch_social.SOCIAL_AVAILABLE=False`).
- **S3 keys:** `_key_belongs_to` uses path-**segment** equality (not substring), delete primitives regex-bounded to the minted grammar, bucket server-chosen.
- **Image handling (image_sanitization.py):** decompression-bomb safe — dimension/pixel caps enforced **before** `load()`; truncated/animated images refused; EXIF stripped via re-paste; MIME derived from bytes.
- **CSRF/CSP (hooks.py):** two layers (Origin/Referer + per-session synchronizer token, constant-time); `mobile_api` correctly exempted (Bearer-only); per-request nonce; `script-src` = nonce + SRI-pinned files; `script-src-attr 'none'`.
- **Injection:** every raw `text(f"...")` interpolates only constants / validated ints / an internal table allowlist; `health_erasure_monitoring.py:109 connection.eval(...)` is **Redis Lua** (parameterized), not Python `eval`. No pickle / yaml.load / eval / exec / render_template_string / os.system. Direct `requests.get` sites use fixed config/provider URLs, not user input.

**Concurrency & billing**
- Lock order `training_plan_generation_operation → user → training_plan` holds everywhere; owner locked `FOR NO KEY UPDATE` (no reverse plan→user edge); no writer holds a row lock across a provider/network call.
- Idempotency: `meal_idempotency` unique-key arbiter with winner re-read; `plan_mutation` serves the **stored after-snapshot** on replay (never current state), no-ops persisted; coach op keys from server `request_id` + semantic fingerprint (never raw model JSON, no occurrence counter); checkpoint replay is a pure function of (snapshot fingerprint, stored revision) via one conditional UPDATE; `parse_revision_value` rejects `bool`.
- Apple/billing: environment-ranked entitlement (no cross-env row merge), monotonic signed timestamps, terminal revocation, strictly-gated dual-env fallback (404+`4040010` / `INVALID_APP_IDENTIFIER`/`INVALID_ENVIRONMENT`), stale-snapshot refusal (>300s); allowance ledger = single CHECK-guarded conditional UPDATE, UNKNOWN billing stored NULL, `max_billed_releases` cap, abandoned holds expire consumed. No double-grant path.
- D1 consent: `OperationAuthority` captures generation once; revoke→regrant (g+2) permanently fences retries/fallback/repair; cohort check inside the owner-locked read-committed transaction.
- Deletion: tombstone write-then-check is genuinely race-free (subject write flushed, serialized by `uq_user_cognito_sub`, before the read-committed tombstone read); purge atomic under owner `FOR UPDATE`; legal-hold fence refuses a held owner before the provider call and re-checks under the owner row lock.
- Water/quest: durable `WaterLog.quest_fired` flag flipped by conditional UPDATE (not a resettable 0→positive counter), held to the reward commit, `uq_user_water_day` single-claim.

**Structure & correctness**
- Trailing window geometry `[end_day-6, end_day]` correct; no `date.today()`/`utcnow().strftime`/`created_at.date()` day-key violations in `app/`.
- null-vs-zero consistently honored; rounding reproduces JS half-up at **display time only** (persisted values never re-rounded).
- Error handling systematically fail-closed (storage faults → typed retryable 503, never the auth catch-all, never a baseline/empty body); `except Exception: pass` instances are best-effort rollbacks or optional observability.
- Streaming (`ai_stream`): consumer disconnect sets a `threading.Event` in `finally`; producer exits — no billed-token leak on abort.
- `foodcache` module dicts guarded by `_cache_lock`; no mutable default args.
- Feature flags: single derived inventory, all defaults OFF, strict `parse_bool_flag`, none read from request/cookie/header.

---

## Overall structure assessment

Health is **excellent.** The architecture leans on a repeated, effective pattern: a **pure analysis/serialization core** (no Flask/DB/clock) behind a **thin impure query layer** and a **thin transport adapter**, with a **single canonical authority per domain fact** and AST-gated guards preventing duplicate authorities or forbidden imports. Timezone, day-keys, macro targets, workout completion, plan mutation, and nutrition totals each have exactly one owner; the "web and native read one path" principle is enforced by parity tests. Error handling is systematically fail-closed with PII-free logging and typed, retryable-vs-terminal error envelopes.

**Residual risk / tech-debt hotspots** (where *future* bugs are most likely, not current bugs):
1. **Legacy web blueprints** (`tracking.py`, `social.py`, `nutrition/diary.py`+`meallog.py`) are the least-converged — a few inline readers and unbounded `.all()`s the newer read-models superseded elsewhere. `social.py` is fully gated off for launch, so its debt is latent.
2. **Flag sprawl** (~22 rollout flags, many shipped-dark with overlapping `depends_on` chains — Apple stack, allowance ledger/enforcement). Composition is correct but activation ordering is intricate and only documentation-enforced.
3. **FatSecret retention + allowance/billing** are the most stateful, highest cognitive-load paths — correct per design and well-tested, but the area where a careless edit is most dangerous.
4. **Single gunicorn worker / 8 threads** with synchronous blocking AI calls remains the core capacity constraint (well-documented, gated by `blocking_concurrency_slot` + thread-reserve invariants).

---

## Suggested sequencing

- **This sprint (quick, safe):** FIX-2 (one-line `id` tiebreak ×2), FIX-3 (non-finite fold), FIX-4 (`.limit()`). All low-risk, test-covered areas.
- **Small follow-up PR:** FIX-1 (make S3 `expected_user_id` required + update 8 call sites). Mechanical but touches a shared primitive — do it as its own change.
- **Ops:** INFO-3 (confirm nginx `/health?deep=1` restriction in prod).
- **Accept as documented:** INFO-1, INFO-2, INFO-4, INFO-5, INFO-6.

*No files were modified during this triage. Findings are report-only; line numbers verified against the working tree on 2026-11-21.*
