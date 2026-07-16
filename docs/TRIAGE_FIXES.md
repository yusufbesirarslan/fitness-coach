# Triage — Needed Fixes

Automated deep-dive triage of the FitX codebase across three areas:
**(1) auth & web-security**, **(2) AI pipeline & services**, **(3) data layer,
models & blueprints**. Findings below are verified against the current code on
branch `claude/amazing-dijkstra-wkjjfv`; each lists file:line, severity, the
concrete failure scenario, and a proposed fix.

Overall the codebase is **well-hardened** — auth/JWT/CSRF/SSRF/IDOR controls are
genuinely sound (not just nominal), migrations are re-entrant, cascade deletes
are complete, and concurrency-sensitive counters use `with_for_update`. No
Critical/High **security** vulnerability was found. The one Critical item is a
**concurrency deadlock** in the streaming coach path.

---

## Priority summary

| # | Severity | Area | One-liner |
|---|----------|------|-----------|
| 1 | **Critical** | AI streaming | Re-entrant acquire of non-reentrant model semaphore → deadlock |
| 2 | Medium | AI streaming | Model slot held during client delivery → slot starvation |
| 3 | Medium | AI streaming | Mid-stream error after tool commit refunds quota + false failure |
| 4 | Medium | Data | AI meal-total ingest path bypasses macro-sanity clamp |
| 5 | Medium | Data | No range validation on user weight → bad profile + latent 500 |
| 6 | Low | AI memory | Summary note not counted against context token budget |
| 7 | Low | AI quota | Stream refund loses reservation-week guard |
| 8 | Low | Data | Meal-log writes have no idempotency → double-submit double-counts |
| 9 | Low | Data | Unbounded `User.query.all()` in leaderboard rebuild (boot-blocking) |
| 10 | Low | Data | `diary_create_meal` accepts arbitrary client `date_key` |
| 11 | Low | AI | Redundant double `finalize_reply` (latent trap) |
| 12 | Low | Observability | AI-turn metrics only emitted on streaming path |
| 13 | Low | Security | CSRF Origin check ignores scheme/port; `Origin: null` allowed |
| 14 | Low | Security | Deep-health trusts entire RFC1918 space, not just loopback |

---

## Critical

### 1. Deadlock: re-entrant acquire of the non-reentrant model-concurrency semaphore
**Files:** `app/services/ai_gate.py:52,57-63`, `app/services/ai_stream.py:95-143`,
`app/services/ai.py:129,163`

`_model_slots = threading.BoundedSemaphore(...)` (ai_gate.py:52) is **not
reentrant**. The streaming Bedrock loop holds this slot across the *entire*
tool-dispatch loop:

```python
# ai_stream.py:95
with model_concurrency_slot():
    for _ in range(ai_coach._COACH_TOOL_LOOP_CAP):
        ...
        out = ai_coach._dispatch_coach_tool(user_id, block.name, ...)  # line 138
```

Two coach tools re-acquire the *same* semaphore inside dispatch:
- `analyze_gym_photo` → `_bedrock_validate_image` (ai.py:129 `with model_concurrency_slot()`)
  — hit on **every** gym-photo coach turn.
- `fetch_nutrition_and_stage_log` → `_coach_search_food` → `_food_search_llm`
  → `_heavy_chat` (ai.py:163) — hit whenever FatSecret + static cache both miss
  (obscure/Turkish foods).

**Failure scenario:** With `AI_MODEL_MAX_CONCURRENCY=1` (a documented knob), a
single streaming coach turn that analyzes a gym photo or stages an obscure food
**self-deadlocks permanently**. At the default (4), four concurrent such turns
hold all outer slots and each blocks forever on the inner acquire. Deadlocked
threads also hold their `_ai_slots` route slot → permanent capacity loss until
process restart.

**Note on scope:** The **blocking** path (`ai_coach.py:1089`) and the OpenAI loop
(`ai_coach.py:1000-1039`) correctly scope the `with model_concurrency_slot()` to
just the provider `create()` call and dispatch tools *outside* it — so they are
**safe**. The streaming path is the anomaly.

**Fix:** Do not hold `_model_slots` across tool dispatch in `_stream_bedrock`.
Scope the slot to only the `bedrock_client.messages.stream(...)` call (and
`get_final_message()`), releasing it before `_dispatch_coach_tool`, mirroring the
blocking/OpenAI structure. (This also resolves finding #2.) A per-thread
reentrant gate would work but narrowing the critical section is safer.

---

## Medium

### 2. Streaming holds a model slot during client network delivery
**File:** `app/services/ai_stream.py:95`

`_stream_bedrock` holds `_model_slots` for the whole loop **and** all token
streaming to the client. A few slow-consuming SSE clients can pin every model
slot for their full session, starving blocking `/ask`, `/api/menu/analyze`, and
plan generation. Combines with #1 to make deadlock easier.
**Fix:** hold the model slot only during actual provider inference, not during
client delivery (same change as #1).

### 3. Mid-stream provider error after tool commit refunds quota + records false failure
**Files:** `app/services/ai_stream.py:111-120`, `app/blueprints/coach.py:277-282`,
`app/services/ai_pipeline.py:172-176`

When a Bedrock stream fails *after* a tool side-effect (`tools_ran > 0`) or after
deltas were sent, `_stream_bedrock` emits an `error` frame; the route then calls
`record_ai_failure` + `_refund_chat_quota`.
**Scenario:** "300g tavuk yedim" → model stages/commits the meal (real DB write)
and streams "Kaydettim…" → network blip on the final turn → `error`. The user got
a partial answer **and** a logged meal, but the weekly AI-chat credit is refunded
and the consecutive-failure counter is bumped (can falsely trip the WS7
cooldown). The partial answer is also **not** persisted to memory here (unlike the
`GeneratorExit` path at ai_pipeline.py:190-200), so the next turn loses context.
**Fix:** on a mid-stream error where `parts`/`tools_ran` indicate real work, treat
it like an interruption (persist partial, keep quota consumed) rather than a clean
failure/refund.

### 4. AI meal-total ingest path bypasses the macro-sanity clamp
**File:** `app/blueprints/nutrition/meallog.py:156-170` (`log_meal`, non-override branch)

Every other canonical-ledger writer routes through
`nutrition_pipeline.clamp_serving_macros` before writing `MealLog` (override path,
`quick_add_meal`, diary items, barcode). The primary AI-estimate path parses LLM
JSON, does `round(float(...), 1)`, and writes straight to `MealLog` with no clamp.
The only guard is the deliberately-generous DB `CheckConstraint`
(`models.py:242-248`, `kalori <= 100000`).
**Scenario:** A hallucinated total ("1 elma" → 9000 kcal, or a negative value)
corrupts daily totals, the protein nudge, weekly reports, and `_today_totals`. A
negative macro additionally raises `IntegrityError` at commit → uncaught → 500.
**Design tension:** the loose DB check is intentional (per-serving 3000-kcal
ceiling would wrongly reject legitimate multi-item meal *totals*). That justifies
skipping the per-*serving* clamp, but leaves this one path with no upper bound.
**Fix:** apply a generous absolute/Atwater sanity check (or `max(x, 0)` flooring
plus a meal-total ceiling) to the AI path; at minimum floor negatives to 0 so the
DB CHECK never 500s.

### 5. No range validation on user-submitted weight
**Files:** `app/blueprints/tracking.py:51-56, 159-162, 274-280` → `_apply_weight_to_profile:124`

The only guard is `if not weight` (rejects 0/None/""). A negative or absurd weight
(`-5`, `5000`) passes `float()` and is written to `current_user.weight`,
`WeeklyLog`/`WeeklyCheckIn`, and drives `calculate_bmr/tdee/target` (negative
values persisted to `UserSession`).
**Downstream 500:** `log_daily_activity` (tracking.py:334) uses
`weight = current_user.weight or 70`; a negative stored weight is truthy, so `-5`
reaches `calculate_activity_calories`, which raises `ValueError` on
`weight_kg <= 0` (calculations.py:50) — unhandled → 500 on every activity log
until weight is corrected.
**Fix:** validate a sane range (e.g. `20 <= weight <= 500`) in the three weight
entry points; return 400 otherwise.

---

## Low

### 6. Context-window token budget can be exceeded by the summary note
**File:** `app/services/memory_manager.py:171-205`

`used` is seeded with `estimate_tokens(conversation.summary)` (:171), but the
injected block is `"[KONUŞMA ÖZETİ …]\n" + summary` (:200), prepended *after* the
budget loop — so the header text is never counted. With a max-length summary
(`MAX_SUMMARY_CHARS=4000`) the window can run over `AI_CONTEXT_TOKEN_BUDGET`. Low
impact (soft guard). **Fix:** count `estimate_tokens(note.content)` including the
header.

### 7. Streaming quota refund loses the reservation-week guard
**Files:** `app/blueprints/coach.py:216-225`, `app/services/premium.py:102-129`

`reserve_ai_quota` stamps the reservation week on the request-scoped
`current_user` (`_ai_quota_reservation_weeks`). The stream refund path loads a
*fresh* `db.session.get(User, user_id)` lacking that attribute, so
`refund_ai_quota` sees `reserved_week=None` and skips the week-rollover guard. At
a week boundary between reserve and refund, it can decrement the *new* week's
counter. Blocking `/ask` is unaffected. **Fix:** carry the reserved week into the
stream refund, or refund against the same user object.

### 8. Meal-log writes have no idempotency/dedup
**Files:** `nutrition/meallog.py log_meal`, `nutrition/diary.py quick_add_meal`,
`food.py barcode_add_to_diary`

`MealLog` has no uniqueness on `(user_id, ogun, tarih, source)`; a rapid double
POST (double-click/retry) writes two rows and double-counts calories. Contrast
`WaterLog`/`DailyActivity`/`CustomMeal`/`PumpCheck`, which got day-scoped unique
constraints + IntegrityError-upsert. Legitimate repeat meals make a hard unique
constraint inappropriate. **Fix:** client-side debounce or a short server-side
idempotency key.

### 9. Unbounded `User.query.all()` in leaderboard rebuild (boot-blocking)
**File:** `app/services/gamification.py:38` (`lb_rebuild`), also `:107` (cleanup)

Runs on every boot (`db_init.py:137`) and after each weekly rollover, loading all
users into memory to rebuild Redis sorted sets. Fine at current scale; blocks
boot and won't scale linearly. **Fix:** batched iteration / server-side
pagination.

### 10. `diary_create_meal` accepts arbitrary client `date_key`
**File:** `app/blueprints/nutrition/diary.py:165` (client `date_key`, unvalidated)
vs `:388` (`today = day_key()` server)

A user can build diary meals for arbitrary past/future dates (self-only), and a
meal built under a non-today `date_key` writes its `MealLog` under *today* when
logged — a mild date mismatch. Self-scoped, no cross-user impact. **Fix:**
validate `date_key` against an allowed window, or derive it server-side.

### 11. Redundant double `finalize_reply` in the blocking path
**Files:** `app/services/ai_coach.py:979`, `app/services/ai_pipeline.py:115`

`_run_coach_conversation` finalizes and returns finalized text; `generate_answer`
finalizes again. Idempotent today (fallbacks re-detected via `_ALL_FALLBACKS`), so
harmless, but a latent trap: any future non-idempotent formatting would misclassify
`is_error_fallback`. **Fix:** finalize in exactly one layer (the pipeline).

### 12. AI-turn metrics only emitted on the streaming path
**File:** `app/services/ai_pipeline.py:67-79, 120-121`

`_emit_metrics` is called only from `stream_answer` with `mode:"stream"`. Blocking
`/ask` emits no `AITurn`/`AIErrors`/token metrics, so CloudWatch dashboards
undercount and the "mode" dimension misleads. **Fix:** emit metrics from
`generate_answer` too with `mode:"blocking"`.

### 13. CSRF Origin check ignores scheme/port; `Origin: null` allowed
**File:** `app/hooks.py:143-144`

Layer-1 compares only `urlparse(origin).hostname` to `request.host` hostname; a
same-host different-scheme/port origin passes Layer 1. Not independently
exploitable — Layer 2 (per-session synchronizer token, unreadable cross-origin) is
always enforced afterward. Hardening only. **Fix:** also compare scheme and reject
`Origin: null`.

### 14. Deep-health trusts entire RFC1918/private space, not just loopback
**File:** `app/__init__.py:37` (`_deep_health_allowed`)

Returns true for any `is_private` (not just loopback), exposing internal posture
(login-offline, Redis state, Bedrock flag) + an outbound FatSecret probe.
Currently safe (gunicorn loopback-bound; ProxyFix `x_for=1` reads the real client
IP so a spoofed `X-Forwarded-For` can't reach it). Becomes a leak only if ever
exposed on a shared private network. **Fix:** gate on `is_loopback` + an explicit
allowlist.

---

## Verified sound (no action needed)

- **JWT** (`cognito_jwt.py`): RS256 pinned (blocks `alg:none`/HS256 confusion),
  `exp` essential, `iss`/`token_use`/audience checked; JWKS over TLS with a
  jwks-unavailable→503 distinction that doesn't destroy sessions.
- **Login** (`auth.py:414-463`): only the crypto-re-validated id-token `sub`
  selects the local user; MFA/`ChallengeName` responses rejected (no empty-result
  bypass).
- **Session fixation**: `_login_fresh` clears session before `login_user`.
- **IDOR / ownership**: every state-changing route across social/profile/nutrition/
  food/tracking/training/gamification/supplements/wearables scopes to
  `current_user.id`; ID-loaded records verify ownership. None missing.
- **Mass assignment**: no path writes `is_premium`/`rank_points`/`cognito_sub`/role
  from request body.
- **SSRF (menu scraper)**: positive `is_global` allowlist, IPv4-mapped-IPv6 unwrap,
  port allowlist, per-hop re-validation, DNS-pinning (closes rebinding TOCTOU),
  body size cap.
- **Provider-fallback B-rule**: Bedrock→OpenAI switch correctly gated on
  `not parts and tools_ran == 0` in both paths.
- **FRIEND_DATA fence / prompt injection**: `neutralize_friend_content` strips
  fences + zero-width/bidi chars; same-turn stage→confirm lock blocks injected
  auto-commit; `user_id` always server-injected.
- **Cache keys**: content-hashed, deterministic, no user id in payload → no
  cross-user leakage.
- **Error-fallback handling**: fallback replies excluded from memory and quota in
  both paths.
- **Migration reentrancy**: stamp point `aa11bb22cc33` near chain end; the two
  later migrations are guarded (`CREATE OR REPLACE`/`has_table`). Fresh-DB boot
  invariant holds.
- **Cascade deletes**: all 24 `user_id`-bearing models covered in
  `cli._user_child_models`, FK-safe purge order; no orphan gap.
- **Timezone/day-key**: consistent `app/timeutil` usage; no drift.
- **Concurrency (streak/quest/XP/quota)**: `with_for_update` column reads +
  unique-constraint upserts; leaderboard sync moved to `after_commit`.
- **Cookies**: HttpOnly, SameSite=Lax, Secure (prod), bounded lifetime,
  `session_protection="strong"`.
- **Avatar upload**: format allowlist + Pillow re-verify (fails closed in prod) +
  `MAX_IMAGE_PIXELS` bomb cap.

---

## Watch-outs (not bugs, but fragile)

- **daily_activity Postgres trigger** (`bb22cc33dd44`) recomputes
  `calories_burned`/`distance_km`/`duration_min`, overriding the Python
  computation. SQLite (local/test) and Postgres (prod) reach the same numbers via
  two independent code paths — any future edit to `MET_CONFIG` must be mirrored in
  the trigger or the environments silently diverge.
- No user-facing account-deletion route exists — deletion is CLI-only.

---

## Recommended order of work

1. **#1 deadlock** — highest impact, reachable in normal usage (every gym-photo
   streaming turn); fixing it also fixes **#2**.
2. **#3** quota/failure correctness on mid-stream error.
3. **#4 / #5** data-integrity guards (macro clamp, weight range) — cheap, prevent
   corruption + 500s.
4. Remaining Low items as cleanup.
