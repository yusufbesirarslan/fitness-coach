# FitX — Needed Fixes (Triage Report)

**Date:** 2026-08-21
**Scope:** 3 parallel read-only deep-dive audits — (1) Security, (2) Backend correctness / races / transactions, (3) Route + model/migration + frontend layer.
**Method:** Adversarial trace of each attack surface and high-risk flow. Every claim below was verified against the code (cited `file:line`), not against `CLAUDE.md`. The top findings were additionally re-confirmed by hand after the audits.
**Commit reviewed:** `95fb056` on `claude/amazing-dijkstra-lqs2qz`.
**Baseline:** Prior triages (`NEEDED_FIXES.md` @ `21f2608`, `NEEDED_FIXES_2026-08-02.md`) were re-checked. **Every previously-documented finding that could be traced is genuinely applied in the current source** (verified in code, not trusted from docs — see "Prior findings re-verified" below). This report lists only **new / still-open** items.

## Headline

The codebase remains **mature and defense-in-depth**. **No Critical or High-severity issues were found in any of the three audits.** The security and backend-correctness sweeps surfaced **no new Critical/High/Medium** issues. The route/model/frontend sweep surfaced **3 Medium** items — all unbounded-query or input-validation gaps, none of them data-corruption or auth holes — plus **6 Low** items. A handful of previously-known Low structural/hardening items remain open and are relisted for continuity.

| # | Severity | Area | Type | Confidence |
|---|----------|------|------|------------|
| 1 | Medium | `chat_messages` loads entire conversation history, no pagination + N+1 on pump-check rows | Reliability / perf | Confirmed |
| 2 | Medium | `friends/select-list` materializes all messages with every friend to rank "recent" | Reliability / perf | Confirmed |
| 3 | Medium | `MealLog.ogun` written unvalidated to `String(100)` → 500 on Postgres, silent truncate on SQLite | Correctness / input-val | Confirmed |
| 4 | Low | Mobile credential tables orphaned on SQLite user purge | Data hygiene | Confirmed |
| 5 | Low | Toast helpers interpolate messages into `innerHTML` unescaped (latent XSS) | Security-hardening | Confirmed |
| 6 | Low | `notifications/read` accepts an uncapped `ids` list | DoS-hardening | Confirmed |
| 7 | Low | Pump-check gallery uses deep OFFSET pagination with unbounded `page` | Reliability / perf | Confirmed |
| 8 | Low | `target_weight` accepts any float (no range/finite validation) | Input-val | Confirmed |
| 9 | Low | `friends/list` N+1 on relationship + per-row avatar presign | Perf | Confirmed |
| 10 | Low | `fitx_mcp` HTTP transport is unauthenticated cross-user DB access if ever enabled | Security (operational) | Confirmed (by design) |
| 11 | Low | `style-src-attr 'unsafe-inline'` remains in CSP | Security-hardening | Confirmed (tradeoff) |

> Items #10–#11 and the carried-over structural items in the appendix are **documented, defensible tradeoffs or opt-in operational risks**, not exploitable holes in the default configuration. They are listed for completeness.

---

## 1. [Medium] `chat_messages` loads the entire conversation with no pagination (+ N+1 pump-check hydration)
**File:** `app/blueprints/social.py:774-805`

`GET /chat/<username>/messages` pulls **every** message ever exchanged between the two users on every chat open:

```python
messages = Message.query.filter(
    db.or_( ... sender/receiver both directions ... )
).order_by(Message.timestamp.asc()).all()
```

There is no `limit`/keyset bound. For a long-running friendship this payload grows without bound → large responses, high memory, slow first paint — on a hot path that fires each time a chat is opened. Additionally, every `message_type == "pump_check"` row triggers a per-row `db.session.get(PumpCheck, ...)` + `serialize_pump_check_card` (N+1). The sibling endpoints (`_serialize_comment_page`, `notifications_data`) already use bounded keyset pagination — chat is the outlier.

**Fix:** Add keyset pagination (`before_id` + capped `limit`, mirroring `_serialize_comment_page`) returning newest-N with a "load older" cursor; batch-load the referenced pump checks in one query instead of per-row `get`.

---

## 2. [Medium] `friends/select-list` materializes all messages with every friend to compute a "recent" ranking
**File:** `app/blueprints/social.py:104-113`

The pump-check share picker pulls **all** messages between the user and every friend, unbounded, then iterates in Python only to keep the newest per friend:

```python
latest_rows = Message.query.filter(
    db.or_( ... current_user ↔ friend_ids both directions ... )
).order_by(Message.timestamp.desc()).all()
recent_rank = {}
for msg in latest_rows:
    other_id = msg.receiver_id if msg.sender_id == current_user.id else msg.sender_id
    recent_rank.setdefault(other_id, len(recent_rank))
```

Only the newest message per friend is actually needed. For heavy users this is an expensive full scan on a UI-interactive path.

**Fix:** Replace with a per-friend `MAX(timestamp)` aggregate (GROUP BY the other participant) or Postgres `DISTINCT ON`, so only one row per friend is fetched.

---

## 3. [Medium] `MealLog.ogun` written unvalidated to a `String(100)` column
**File:** `app/blueprints/nutrition/meallog.py:32-36,114-119,181-191` — column at `app/models.py:482`

`ogun = data.get("ogun", "")` flows straight into `MealLog.ogun` (`db.Column(db.String(100), nullable=False)`) with no length check and no `try/except` around the commit. The empty-string case is caught (`if not ogun or not yemekler: ...`), but an over-100-char `ogun` is not. On **PostgreSQL** (prod) this raises `StringDataRightTruncation` → unhandled → HTTP 500; on **SQLite** (local/tests, no length enforcement) it stores the full string, so prod and local diverge silently. The API accepts arbitrary values even though the UI uses a fixed dropdown. Contrast `complete_workout`, which correctly slices `location_type[:50]` / `description[:200]`.

**Fix:** Validate/slice `ogun` to the column bound at the boundary — reject over-length with a 400 (or slice to `[:100]`), matching the slicing pattern already used in `complete_workout`.

---

## 4. [Low] Mobile credential tables orphaned on SQLite user purge
**File:** `app/cli.py:143-228` + `app/models.py:208-290`

`_purge_user` hand-deletes child rows that lack a `user_id` (CustomMealItem, CoachMessage) before the `_user_child_models` loop, but `MobileAccessCredential` and `MobileRefreshCredential` (keyed by `session_id`, no `user_id`) are not handled. `MobileAuthSession` is deleted in the loop; on **Postgres** its FK `ondelete="CASCADE"` cleans the credentials, but on **SQLite** (FK enforcement off in local/tests) the credential rows are orphaned. `tests/test_cascade_delete` won't catch this — it only introspects `user_id`-bearing models.

**Fix:** Explicitly delete the two credential tables (scoped via the user's `MobileAuthSession` ids) alongside the CustomMealItem/CoachMessage handling.

---

## 5. [Low] Toast helpers interpolate messages into `innerHTML` unescaped (latent XSS)
**Files:** `static/progress.js:28`, `static/nutrition.js:62`, `static/training.js:101`

```js
t.innerHTML = `<span class="toast-icon">${icons[type]||'ℹ'}</span><span>${msg}</span>`;
```

`msg` is not escaped. Every current caller passes app-owned i18n / server `t()` strings or `err.message`, so it is **not presently** a cross-user XSS vector — but it is one careless caller away from injection. The coach widget's `_toast` already does this correctly via `this._esc(msg)`.

**Fix:** Build the message span with `textContent` (DOM APIs) or escape `msg` before interpolation.

---

## 6. [Low] `notifications/read` accepts an uncapped `ids` list
**File:** `app/blueprints/notifications.py:60-66`

`ids` is validated as a non-empty list of ints but has no length cap, then flows into `mark_read` → `UPDATE ... WHERE id IN (<list>)`. A client can submit an enormous list (resource/DoS concern, minor).

**Fix:** Cap `len(ids)` (e.g. ≤ 200) and return 400 otherwise.

---

## 7. [Low] Pump-check gallery uses deep OFFSET pagination with unbounded `page`
**File:** `app/blueprints/profile.py:172-205`

`per_page` is bounded (1–36) but `page` is only floored at 1 with no ceiling; `.offset((page-1)*per_page)` permits arbitrarily deep offsets (`?page=9999999`) → slow scans, and OFFSET pagination degrades on large galleries.

**Fix:** Prefer keyset pagination (`before_id`) like the comment/notification endpoints, or bound `page`.

---

## 8. [Low] `target_weight` accepts any float (no range/finite validation)
**File:** `app/blueprints/profile.py:49-52,144-149` (+ `setup`)

`current_user.target_weight = float(...)` accepts negative, zero, absurd, or non-finite-adjacent values silently (`except: pass`). Weight *logging* goes through the strict `_parse_weight` (20–500, finite) in `tracking.py`, but the profile target-weight path has no equivalent guard, so downstream progress/goal computations can ingest nonsense.

**Fix:** Reuse a bounded numeric validator for `target_weight` (align with `_parse_weight`'s range/finite checks).

---

## 9. [Low] `friends/list` N+1 on relationship + per-row avatar presign
**File:** `app/blueprints/social.py:61-89`

`friends_list` iterates accepted friendships accessing `f.receiver`/`f.sender` lazily and `friend.avatar_src` per row (each S3-backed avatar triggers a presign). Bounded by friend count so impact is small, but easily eliminated.

**Fix:** `joinedload` the sender/receiver (and incoming/outgoing) relationships; consider batching/caching avatar presigns.

---

## 10. [Low] `fitx_mcp` HTTP transport is unauthenticated cross-user DB access if ever enabled
**File:** `fitx_mcp/server.py:328-434,642-657` (read tools `111-146`)

Every MCP tool takes `user_id: int` and performs **no authorization** — `log_workout_entry`, `log_nutrition_entry`, `get_user_*`, `generate_weekly_report` read/write **any** user's data for any `user_id` passed. Protected only by `FITX_MCP_ALLOW_HTTP=1` + loopback binding (`127.0.0.1:8100`). Not exploitable in the default/stdio config (the transport refuses to start without the flag). If the flag is ever set and any reverse proxy or SSRF primitive can reach loopback:8100, it becomes a full cross-user read/write IDOR.

**Fix:** Keep the flag off in production. If HTTP is ever needed, put a per-request auth token / principal binding in front of the tools rather than relying on loopback alone; document that it must never sit behind a proxy that forwards to loopback.

---

## 11. [Low] `style-src-attr 'unsafe-inline'` remains in the CSP
**File:** `app/hooks.py:67`

`script-src` and `style-src-elem` are nonce-locked and `script-src-attr 'none'` closes inline event handlers, but dynamic `style="..."` attributes still require `style-src-attr 'unsafe-inline'`. Narrow residual vector: if any stored/reflected HTML injection ever slipped through, an attacker could set inline style attributes (CSS-based UI redressing / selector-based exfil). Much weaker than full `<style>`/script injection, which is blocked.

**Fix (longer-term):** Move the remaining dynamic width/progress-bar styles to nonce'd `<style>` rules or CSS custom properties set via a nonce'd block, then drop `style-src-attr 'unsafe-inline'`.

---

## Appendix A — Prior findings re-verified as genuinely FIXED (spot-check of the backend sweep)

Each verdict below was confirmed by reading current source at the cited lines, not trusted from docs:

- Streaming coach self-deadlock (re-entrant `_model_slots` across tool dispatch) → **fixed**, slot scoped to `messages.stream()` only (`ai_stream.py:78-121,240`).
- `_model_slots.acquire()` no-timeout thread-reserve breach → **fixed**, bounded `_acquire_before_deadline` + `model_excess` folded into boot invariant (`ai_gate.py:119-127,172-200,255-296`).
- `mobile_auth.refresh()` holding `FOR UPDATE` across the Cognito network call → **fixed** via snapshot → rollback-to-release → network → re-lock + optimistic version guard (`mobile_auth.py:442-541`).
- OpenAI non-stream coach loop per-turn wall-clock budget → **fixed** (`ai_coach.py:1097-1116`).
- `weekly_water` toggle-event counting → **fixed** via `WaterLog.quest_fired` + `uq_user_water_day` (`training.py:655-677`).
- `detect_deload_due` forward-window bug → **fixed**, `weekly_windows` now trailing (`training_history/analysis.py:41-61`).
- Stale previous-day ACTIVE session terminalized as COMPLETED → **fixed** (`workout_session/service.py:271-292` + `training.py:270-277`).
- `plan_facts` rejecting `{"program":[...]}` wrapper, `/training/bootstrap` 500 on over-bound `set`/`sure_dk`, summary-note token accounting, stream quota refund week-guard, AI meal-total macro clamp, meal double-submit, user-weight range validation → **all fixed** at the cited sites.

## Appendix B — Carried-over Low structural / hardening items (still open, unchanged)

Not re-worked here because they are structure/hardening rather than new correctness bugs:

- `ProxyFix` trusts client `X-Forwarded-Host`/`-Port` that nginx never sets (`config.py`).
- CSRF Layer-1 Origin check ignores scheme/port and allows `Origin: null` (`hooks.py`) — Layer-2 synchronizer token still mandatory, so not independently exploitable.
- Deep-health endpoint trusts all RFC1918, not just loopback (`app/__init__.py`).
- Mobile-login 403-vs-401 account-enumeration signal (`mobile_auth.py`) — mitigated by Cognito `PreventUserExistenceErrors` + rate limits.
- AI-turn metrics emitted only on the streaming path (`ai_pipeline.py`).
- God-module sizes: `ai_coach.py`, `social.py`, `tracking.py`.

---

## Suggested order of work

1. **#3** (`ogun` validation) — smallest change, removes a real prod-only 500 and a prod/local divergence. One-line slice/validate.
2. **#1 + #2** (unbounded message queries) — same file, same class of fix (bound + aggregate), meaningful latency/memory win on hot paths.
3. **#4** (mobile credential purge) — data-hygiene, quick and self-contained.
4. **#5–#9** — low-risk hardening; batch them opportunistically.
5. **#10–#11** and Appendix B — track as accepted tradeoffs; act only if the threat model changes.

_No Critical/High issues. The three Medium items are the only findings that warrant near-term code changes; all are localized and low-risk to fix._
