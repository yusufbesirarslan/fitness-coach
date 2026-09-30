# Native Coach contract (LP-09)

    POST /api/v1/coach/messages
    GET  /api/v1/coach/history

Routes: `app/blueprints/mobile_coach.py` (on the existing `mobile_api` blueprint,
`MOBILE_AUTH_ENABLED`-gated, no new flag). Projection: `app/services/mobile_coach.py`.
Tests: `tests/test_mobile_coach_api.py`.

## 1. Not a second Coach

The native routes are a transport over the web Coach's own authority:

| Concern | Authority (shared with web `/ask`) |
|---|---|
| Generation, grounding, plan tools, moderation | `ai_pipeline.generate_answer` → `context_builder` → `ai_coach._run_coach_conversation` |
| Conversation memory + persistence | `memory_manager` (`CoachConversation` / `CoachMessage`, `record_turn`) |
| History window | `memory_manager.recent_messages(user_id, limit=50)` — the same call web `GET /coach/history` makes |
| Input limits | `moderation.validate_question` (`MAX_QUESTION_CHARS` = 4000, after trimming) |
| Weekly allowance | `premium.reserve_ai_quota` / `refund_ai_quota`, bucket `"chat"` — the SAME counter `/ask`, `/ask/stream` and `/chat` spend |
| Failure cooldown | `ai_recovery.ai_cooldown_remaining` / `record_ai_failure` / `clear_ai_failures` |
| Rate limits | `AI_RATELIMIT` + `AI_BURST_RATELIMIT` (the `/ask` pair), keyed on the Bearer owner |
| Concurrency | `mobile_ai_concurrency_gate` (the same `_ai_slots` semaphore as `/ask`) |
| Provider selection / spend guard | unchanged — inside the pipeline |

The only shared-domain change is additive: `memory_manager.record_turn` returns the
two rows it wrote and `generate_answer` returns them as `recorded_turn`, so the
native response is built from THIS request's rows rather than a "last two
messages" re-read that a concurrent turn could win. Web `/ask` ignores the key.

`/ask`'s orchestration was deliberately NOT extracted into a shared service: the
web tests patch `app.blueprints.coach.*` directly, and the repository already
composes these same gates per route (`/ask`, `/ask/stream`, `/chat`). The native
route composes them in the same order.

## 2. Request

```json
{"message": "How much protein should I eat?"}
```

* JSON object with EXACTLY the key `message` (a closed key set). `user_id`,
  `conversation_id`, `history`, `handoff` or any other key → 400
  `COACH_INVALID_REQUEST`. The server owns identity and the transcript; there is
  nothing for a client to submit.
* `message` must be a string; it is trimmed, then must be non-empty and at most
  4000 characters.
* Non-JSON bodies (form, `text/plain`, malformed JSON) → 400 `COACH_INVALID_REQUEST`.
* Non-streaming. No `handoff` (the web Progress → Coach draft is a web entry point).

## 3. Responses

### POST 200

```json
{
  "contract_version": 1,
  "messages": [
    {"id": "…24 chars…", "role": "user",      "text": "How much protein should I eat?",
     "created_at": "2026-09-30T10:00:00.123456Z", "interrupted": false},
    {"id": "…24 chars…", "role": "assistant", "text": "…",
     "created_at": "2026-09-30T10:00:00.123456Z", "interrupted": false}
  ]
}
```

Exactly the turn this request created, in the same item shape as history, so a
client can append it and later reconcile against `GET /history` by `id`.

`id: null` (with `created_at: null`) means the reply is real but was NOT saved —
only when Coach memory is switched off (`AI_MEMORY_ENABLED=0`) or its write failed
(the same degradation web tolerates). Such a turn will not appear in history and
the next turn will not see it.

### GET 200

```json
{"contract_version": 1, "messages": [ /* ≤ 50 items, oldest first */ ]}
```

* The owner's ACTIVE conversation only (an archived conversation — web
  `POST /coach/conversation/reset` — is not history).
* Bounded window: the newest 50 messages, returned oldest-first, ordered by the
  persisted row id (deterministic even when timestamps tie). This is the window
  web `GET /coach/history` serves; no pagination framework was added. A client
  cannot tell whether older messages exist — accepted for v1.
* No query parameter, body or header is read. `?user_id=`, `?conversation_id=`,
  `?limit=` are ignored.

### Message item

| Field | Meaning |
|---|---|
| `id` | Opaque, owner-bound identity (below); `null` only for an unsaved POST turn |
| `role` | `user` \| `assistant` |
| `text` | The persisted text. Assistant text is the final, moderated reply |
| `created_at` | UTC ISO 8601 with `Z` |
| `interrupted` | `true` when a WEB streamed reply was stopped mid-way and saved partially (`/ask/stream`); native POST turns are always `false` |

Never exposed: `CoachMessage.id`, `conversation_id`, owner id, Cognito sub, the
conversation `summary` (a model-written note fed back to the model), token
accounting, provider usage, system prompts, context blocks, tool calls.

### Identity

`id = base64url(HMAC-SHA256(HMAC(SECRET_KEY, "axisai/mobile-coach/message-id/v1"),
"<user_id>\0<message_row_id>")[:18])` — the construction
`mobile_nutrition/identity.py` established for ledger rows, with its own domain
label. Derived, not stored (no migration); stable while `SECRET_KEY` is; A's id for
row N is not B's id for row N. No route accepts a message id in v1.

## 4. Errors

All errors are the ADR 0001 envelope `{"error": {code, message, retryable,
request_id}}` with `Cache-Control: no-store`. Auth failures come unchanged from
`require_mobile_auth`.

| HTTP | Code | Retryable | When | Provider called? |
|---|---|---|---|---|
| 400 | `COACH_INVALID_REQUEST` | no | Not a JSON object, extra/missing keys, non-string `message` | no |
| 400 | `COACH_MESSAGE_EMPTY` | no | Blank after trimming | no |
| 400 | `COACH_MESSAGE_TOO_LONG` | no | > 4000 chars after trimming | no |
| 401 | `AUTH_SESSION_EXPIRED` (middleware) | no | Missing/malformed/unknown/expired/revoked Bearer | no |
| 402 | `COACH_QUOTA_EXCEEDED` | no | Non-premium owner has used `FREE_WEEKLY_AI_CHATS` (default 200) in the current Istanbul ISO week, shared with web; only when `AI_CHAT_QUOTA_ENABLED` (default on). Premium is never limited. Resets at the week boundary; no `Retry-After` (the existing product gives none) | no |
| 413 | `REQUEST_TOO_LARGE` (blueprint) | no | Body over `MAX_CONTENT_LENGTH` | no |
| 429 | `COACH_RATE_LIMITED` | yes, `Retry-After` from the limiter | `AI_RATELIMIT` (30/h) or `AI_BURST_RATELIMIT` (5/min) per owner | no |
| 429 | `COACH_COOLING_DOWN` | yes, `Retry-After` = remaining cooldown | Owner hit `AI_FAILURE_THRESHOLD` consecutive provider failures (needs Redis; otherwise never) — checked BEFORE the quota, so nothing is spent | no |
| 503 | `COACH_BUSY` | yes, `Retry-After: 15` | AI concurrency gate full | no |
| 503 | `COACH_UNAVAILABLE` | yes | Provider failure (the pipeline's error fallback) or any unexpected failure | yes |
| 503 | `COACH_REPLY_INCOMPLETE` | **no** | Provider failed AFTER a plan tool committed a change or staged a confirmation-required proposal in this turn. Retrying is a new turn with a new operation identity and could apply the edit twice — the reason the web fallback says "no need to ask again". The client should refresh the plan and history | yes |
| 503 | `COACH_HISTORY_UNAVAILABLE` | yes | History read failed (GET only) | — |

Provider failure is never a Coach reply: web `/ask` answers 200 with the friendly
fallback sentence and `is_error_fallback: true`; native gets a typed 503 instead.
The bookkeeping is identical to `/ask`: failure streak advanced, reserved
allowance refunded, turn NOT persisted (B16).

Order of checks (POST): auth → concurrency gate → rate limits → body validation →
cooldown → quota reservation → provider. Every rejection before "provider" is
proven by tests to make no provider call and spend no allowance.

## 5. Privacy

* `Cache-Control: no-store` on every response (blueprint-wide).
* No `Set-Cookie` on native responses. The shared plan-clarification code mirrors
  a bounded diagnostic record into the Flask cookie session for the web widget;
  the POST route discards any cookie-session writes before Flask saves the
  session (the authoritative copy lives in Redis/process memory). `client_history`
  is always `[]`, so the legacy cookie-history fallback in
  `ai_coach._run_coach_conversation` is never taken.
* Logs: event name, exception TYPE and request id only — never message text,
  history, prompts, provider bodies or exception text. No new success logging.

## 6. Concurrency and duplication

* Each turn is persisted once (`record_turn`: one user row + one assistant row, one
  commit) and only for a real reply.
* The POST response is built from the rows the request itself wrote, so a
  concurrent turn committing mid-generation cannot be returned (tested).
* There is NO idempotency key and NO per-user single-flight for Coach turns — the
  web Coach has none either. Two simultaneous POSTs from the same owner both run,
  both spend an allowance, and both see the history as it was when they started;
  their rows are ordered by id. The burst limit (5/min) and the concurrency gate
  bound this. A client should not send a second message before the first answers.
  Adding idempotency would change Coach product semantics and is out of LP-09.

## 7. Not in LP-09

Streaming, conversation reset, pagination beyond the window, message addressing
by id, `handoff`, native Flutter UI (LP-10).
