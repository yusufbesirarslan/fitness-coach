# Streaming (Sprint 4 WS2 + WS10)

`POST /ask/stream` is the SSE twin of the blocking `POST /ask`. Same auth, gates,
quota, and pipeline; the difference is the answer arrives token-by-token. `/ask`
is untouched and remains the fallback for clients that can't stream.

## SSE protocol

Frames: `meta → delta* → done | error`.

```
event: meta
data: {"conversation_id": 7, "request_id": "a1b2c3d4e5f6a7b8"}

event: delta
data: {"text": "merhaba"}

event: done
data: {"text": "<final>", "is_error_fallback": false}

event: error
data: {"message": "<friendly i18n text>"}
```

- `meta` carries the conversation id and the WS6 `request_id` (correlate a stream
  with server logs — see [OBSERVABILITY.md](OBSERVABILITY.md)).
- `error` frames carry **friendly i18n text only** — provider exception text never
  reaches the client.
- Frames use `ensure_ascii=False` so Turkish characters don't bloat to `\uXXXX`.

Response headers: `Content-Type: text/event-stream`, `X-Accel-Buffering: no`,
`Cache-Control: no-cache`.

## Provider fallback (B-rule)

Bedrock→OpenAI switch is allowed only *before* the first delta reaches the client
**and** before any tool side effect. After either, a mid-stream failure emits a
friendly `error` frame instead of silently switching providers. Tool rounds run
non-streamed; only the final turn streams.

## Client (`static/coach_widget.js`)

- **fetch POST + `ReadableStream`** SSE reader — `EventSource` can't send
  `X-CSRFToken` (csrf.js wraps `fetch`, not EventSource), so streaming posts via
  fetch and parses frames from the body stream.
- `AbortController`-backed **Stop**; **Regenerate** re-sends the last question.
- Typing indicator until the first delta; rAF-throttled incremental DOM append (no
  full re-render per token).
- Markdown via **marked + DOMPurify**, loaded from `cdn.jsdelivr.net` with pinned
  exact-file URLs + SRI (CSP `script-src` allows those exact URLs).
- On open, hydrates from `GET /coach/history`.

## Concurrency gate

`ai_stream_concurrency_gate` holds the AI slot until the response **closes**
(`call_on_close`), not on view return. The normal `ai_concurrency_gate` releases
on return — for a streamed response that is *before* the first token, so it would
never actually limit streams. A `threading.Event` guards against double-release.

Note: the werkzeug test client's `get_data()` consumes the streamed body but does
**not** fire `call_on_close`; tests must `resp.close()` explicitly or the slot
leaks. Production gunicorn closes the iterator.

## Operational constraints

- gunicorn runs **1 worker × 8 threads**. Each stream pins a thread + AI slot +
  model slot for the full generation. The AI concurrency gate (default 4) + scrape
  gate must stay ≥2 below `FITX_WEB_THREADS` so `/health` and cheap routes keep a
  thread reserve.
- nginx already sets `proxy_buffering off` on `location /`; the `X-Accel-Buffering:
  no` header is belt-and-suspenders for intermediate proxies.
- gunicorn/nginx read/send timeouts are 300s.
