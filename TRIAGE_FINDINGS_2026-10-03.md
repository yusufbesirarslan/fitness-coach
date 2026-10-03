# Triage Findings — 2026-10-03

Deep-dive triage of the fitness-coach backend across three tracks: **security
vulnerabilities**, **backend correctness / concurrency / data integrity**, and
**overall structure + frontend/API robustness**. Every actionable item below was
read in source, not inferred from names.

## Headline

**No Critical or High vulnerability or data-corruption bug was found.** The
codebase is unusually well-hardened: owner-scoping, CSRF, HMAC/JWT handling, S3
key grammar, SSRF defenses, transaction/commit boundaries, FOR UPDATE lock
ordering, unique-index race resolution, and idempotency replay are all correctly
implemented and match the invariants documented in `CLAUDE.md`. The items below
are one confirmed low–medium functional bug, a medium privacy-retention gap, and
a set of lower-priority hygiene / operational / consistency items.

Severity key: **Medium** = worth scheduling; **Low** = hygiene/awareness;
**Info** = documented accepted tradeoff, listed so it is not re-flagged.

---

## Actionable

### FIX-1 — Native hydration write reported as failure after it already committed (Low–Medium) ✅ verified

- **Where:** `app/services/nutrition_native/hydration.py:81-83` (`set_today`),
  `app/services/hydration.py:62-66` (`award_water_logged`),
  `app/services/gamification.py:378` (`complete_quest_for_user`).
- **What:** `set_today` calls `hydration.set_today_count(...)` which **commits**
  the absolute water count (authoritative), then calls
  `hydration.award_water_logged(...)`. That path reaches
  `complete_quest_for_user`, which runs `_claim_quest` (→ `award_xp` +
  `log_activity`) **outside** its try/except — only the `db.session.commit()` is
  guarded. A DB error in `_claim_quest` therefore propagates out of
  `award_water_logged` → out of `set_today`, where the closure route's
  `_run`/`_failure` wrapper rolls back and returns a retryable
  **503 `HYDRATION_TEMPORARILY_UNAVAILABLE`**.
- **Failure scenario:** the count IS persisted, but the native client is told the
  write failed. On retry it re-sends the same `If-Match` revision, which no longer
  matches the now-updated count → **412 `StaleHydration`**. The client can never
  confirm its own successful write.
- **Why it's path-specific:** the quest side-effect is meant to be best-effort
  (the count is the authority, the quest is a funnel), but here a quest/XP
  exception is not isolated from success reporting. The browser `/water` route
  (`training.py:926`) has the same quest-after-commit shape but tolerates it
  because it has no `If-Match` precondition — a retry just re-sets the absolute
  count idempotently. This is a native-path regression introduced by layering the
  precondition on top of the shared authority.
- **Fix:** make the `award_water_logged(...)` call in `set_today` best-effort
  (swallow its exception, as with other in-request best-effort side-effects) so
  the committed payload is returned as success. No data corruption exists today
  (the count is correct and the quest cleanly rolls back); the defect is purely in
  what the client is told.

### FIX-2 — Orphaned S3 media on the operator / anti-resurrection deletion path (Medium, privacy retention)

- **Where:** `app/cli.py` `_user_child_models()` / `_purge_user` (~lines 98–148).
- **What:** `_purge_user` deletes `MealLog` and `MealPhotoCleanup` rows but does
  **no object-store I/O**, so meal photos and pump-check images in S3 are left
  orphaned when deletion runs through the CLI `_purge_user` path (e.g.
  `cleanup-test-users` and the anti-resurrection purge). The in-code comment
  documents this as accepted debt.
- **Not affected:** the native self-service deletion path
  (`app/services/account_deletion.py`, LP-11) releases owned objects *before*
  calling the purge, so it is covered.
- **Why it matters:** deleted users' body photos persist in the bucket — a
  privacy/retention (and GDPR-erasure) concern, not just storage cost.
- **Fix:** have the operator/purge path enumerate and release owned objects with
  the canonical owner-checked helpers before (or alongside) the row purge, or
  enqueue `MealPhotoCleanup`-style intents that the existing
  `cleanup-pending-meal-photos` drainer already knows how to converge.

### FIX-3 — `NutritionPlan` "newest row" selectors omit the `id` tiebreak (Low, consistency; not currently triggerable)

- **Where:** `app/services/nutrition_day_view.py:249` (`_read_plan`),
  `app/services/plan_facts.py:234`, `app/blueprints/nutrition/diary.py:201`,
  `app/blueprints/nutrition/plan.py:76`.
- **What:** these readers order by `created_at DESC` only. The canonical
  `nutrition_plan_store.newest_plan_query` orders `created_at DESC, id DESC` and
  its docstring promises the primary-key tiebreak. This diverges from the Triage
  2026-10-01 #3/#4 rule (though that rule was scoped to `UserSession`/
  `TrainingPlan`).
- **Why it's not triggerable today:** `replace_nutrition_plan` does
  `DELETE WHERE user_id` + one INSERT in a single transaction, so a user holds at
  most one `NutritionPlan` row and the tiebreak can never change the result.
- **Fix:** add `, NutritionPlan.id.desc()` to the four selectors for consistency
  and to make the day-view and plan tab provably agree. Low priority.

### FIX-4 — `derive_daily_macro_targets` does not reject NaN/±Inf (Low, theoretical)

- **Where:** `app/services/nutrition_targets.py:109-131`.
- **What:** the guard is only `calories <= 0`; NaN and ±Inf both evaluate that to
  `False`, so a non-finite `target_calories` would yield a `DailyMacros` with
  non-finite grams. `nutrition_day_view._read_target` is protected (it checks
  `math.isfinite` first), but other direct callers (barcode `_target_macros`,
  `menu.analyze_menu`, coach) rely on the stored `UserSession.target_calories`
  being finite — which it is, since onboarding writes a positive number. Hence
  effectively unreachable.
- **Fix:** add a `math.isfinite(calories)` check to the guard so the module's
  "absence is not a number" contract also covers "non-finite is not a number."

---

## Structure / hygiene (lower priority)

### STR-1 — God modules (Medium, organizational; no bug)

Large files that are real review/maintenance hazards and are candidates for the
same package decomposition already applied elsewhere in the repo:

- `app/models.py` — ~1627 lines (every model in one file)
- `app/services/ai_coach.py` — ~1450 lines (partly a legacy re-export surface
  over the modular `ai_pipeline`/`context_builder`)
- `app/blueprints/social.py` — ~1254 lines (Feed V2 + moderation + comments)
- `static/nutrition.js` — ~2899 lines (Today + Diary + Plan + History + Water +
  log chooser; carries most of the frontend state-machine logic)
- also: `training.py` ~976, `tracking.py` ~913, `nutrition_pipeline.py` ~862,
  `fatsecret.py` ~840, `ai_nutrition.py` ~802.

### STR-2 — `datetime.utcnow()` deprecation (Low, forward-compat)

~66 call sites of the deprecated, naive-returning `datetime.utcnow()`
(concentrated in `app/services/wearables/*`, `mobile_auth.py`, `challenges.py`,
`coach_context_queries.py`). The project pins **Python 3.11**, so there is no
active warning today, but this is a hard blocker for a 3.12+ upgrade and a latent
naive-vs-aware hazard. These are UTC-timestamp usages (token expiry, `updated_at`),
**not** day-key derivation — verified: zero `strftime("%d.%m")` day-key usages
exist outside `app/timeutil.py`, so the `CLAUDE.md` day-key rule holds.

### STR-3 — Broad-exception volume (Low, awareness)

~403 `except Exception` and ~68 `except Exception: pass` across `app/` (zero bare
`except:`). Samples inspected are all legitimate fail-soft (rollback guards,
optional boto3 error-code extraction, best-effort metrics/cache), and most swallows
at least log or re-raise a typed error. No action required, but the volume means a
genuinely-swallowed error would be easy to miss in review. **FIX-1 is one concrete
instance where a swallow is actually needed and currently absent.**

### STR-4 — Single gunicorn worker + 8 threads with synchronous blocking AI (Medium, operational; documented)

The top scaling ceiling, documented in `CLAUDE.md`/Dockerfile and defended by
`ai_gate` slots, `blocking_concurrency_slot()`, and boot-time capacity invariants
(`tests/test_capacity_invariants.py`). The mitigation is **load-shedding
(503 + Retry-After), not throughput** — 8 concurrent long AI/stream requests still
saturate the app. Keep in mind for any growth; do not raise worker count until the
in-memory cache/limiter fallbacks are removed or AI moves to a queue.

---

## Security — accepted tradeoffs / informational (no action)

Listed so they are not mistaken for new defects. The security track found **no
confirmed Critical/High vulnerability.**

- **CSP `style-src 'unsafe-inline'` / `style-src-attr 'unsafe-inline'`**
  (`app/hooks.py`): `script-src` is nonce-based with no `unsafe-inline` and
  `script-src-attr 'none'`, so the primary XSS execution vector is closed. Inline
  style fallback and dynamic `style="..."` (progress-bar widths) remain open —
  documented accepted tradeoff. Optional hardening: extend nonce coverage to every
  inline `<style>` block.
- **Broad GA wildcard hosts in CSP** (`*.googletagmanager.com` etc.) — per Google's
  official gtag guidance; inherent to using GA.
- **`.env` plaintext on the EC2 host** — no hardcoded secrets exist in the code
  (S3/Bedrock use the IAM instance profile); documented next step is SSM Parameter
  Store SecureString. Deployment-hardening item.
- **JWKS forced-refresh cooldown (default 60s)** can briefly reject a freshly
  rotated Cognito key — deliberate DoS-prevention tradeoff; Cognito keeps signing
  with the old key during rotation.
- **Avatar presigned URLs (6h) without `expected_user_id`**
  (`app/models.py:114`) — intentional; avatars render to friends/feed/leaderboard
  and the key always comes from the model row, never client input. Every other
  presign call site does pass `expected_user_id` (verified).

---

## Verified-healthy (do not re-flag)

- **AuthZ / IDOR:** every record-by-id load checked is owner-scoped (web
  `social`/`nutrition.diary`/`profile`/`supplements`; mobile `/api/v1` derives
  owner from `g.mobile_user` only — no user/plan/db id read from body/query/headers
  across all 12 closure routes).
- **HMAC tokens:** domain-separated, versioned `SECRET_KEY` subkeys; `user_id`
  bound into the MAC; derived (never stored); all comparisons use
  `hmac.compare_digest`.
- **CSRF:** app-wide `before_request` two-layer (Origin/Referer + per-session
  synchronizer token); `mobile_api` correctly exempt (Bearer, not cookie); the one
  state-changing GET (`/logout`) has its own `Sec-Fetch-Site`/Referer default-DENY.
- **SQL injection:** none — no interpolated `text()`; raw cursor paths use `%s`
  placeholders.
- **SSRF (menu scraper):** positive global-IP allow-check, port allow-list,
  per-hop redirect re-validation, DNS-rebinding TOCTOU closed via pinned
  getaddrinfo, size caps.
- **JWT:** `algorithms=["RS256"]` pinned, issuer + `token_use` (rejects ID tokens
  as credentials) + audience + `exp` enforced; request-path leeway 0; retired
  skew setting boot-enforced.
- **S3:** bucket server-chosen; delete primitives fail-closed on any key not
  matching the `_build_key` grammar + owner segment (segment equality, not
  substring); SSE-AES256.
- **AI coach plan mutation:** cross-user mutation structurally impossible (commands
  carry no user/plan id; `user_id` from authenticated context; owner-scoped lock;
  DB-unique idempotency).
- **Transactions / concurrency:** `workout_completion`, `plan_mutation`,
  `plan_owner_lock`, `nutrition_plan_store`, `supplement_cabinet`, `premium`,
  `workout_session.advance_checkpoint`/heartbeat/abandon, and
  `meal_idempotency`/native planned-meal log all verified correct (single-commit
  atomicity, user→plan lock prefix with no cycle, single-conditional-UPDATE race
  resolution, fingerprint-ordered replay). Native planned-meal logging is actually
  exactly-once.
- **Gamification quest commit** and **water funnel claim** are the documented
  triage 2026-09-30 #1/#5 fixes — correct (modulo FIX-1's isolation issue).
- **Migrations:** single head `d0e1f2a3b4c5`, one proper merge, 44 revisions; all
  table-creating migrations after the stamp point are `has_table`-guarded; no
  destructive non-expand/contract migrations.
- **Retired flags/routes** (`UIUX_PLAN_V2_ENABLED`, `/api/progress/insights`,
  `/api/progress/nutrition`, voice placeholder, `plan_create.js`) are genuinely
  gone, not half-removed.
- **Frontend:** `esc()` on all interpolated user/AI data; coach markdown via
  `marked` + `DOMPurify`; single-flight/race guards (`waterSeq`, `_dayViewSeq`,
  `_activePlanEpoch`, plan save/gen locks) present and matching the `CLAUDE.md`
  contract; no XHR/sendBeacon for state-changing requests (all `fetch`,
  auto-CSRF'd).
- **Dependencies** fully pinned with `==`; Dependabot on the untrusted parsers;
  external scripts SRI-hashed + pinned to jsdelivr.
- **Cascade delete** list thorough, ordered, and introspection-tested.

---

## Suggested order

1. **FIX-1** — smallest change, real user-facing native hydration bug (swallow the
   best-effort quest side-effect in `set_today`).
2. **FIX-2** — privacy-retention gap on the operator deletion path.
3. **STR-1 / STR-4** — keep in view for growth; schedule decomposition of the
   god-modules opportunistically.
4. **FIX-3 / FIX-4 / STR-2 / STR-3** — low-risk hygiene, batch when convenient.

*Produced by an automated triage routine. Findings were verified against source;
severity reflects confirmed impact, not theoretical worst case.*
