# LP15-GR1 — menu error-reporting privacy

Audited base: `b6710f1014d265aa88b5bc8a66217116f3f96bce` (`origin/main`,
including #407, #413 and #414). The base is **unsafe** for menu reporting:
eight of the synthetic envelope tests fail against its real configuration.
No AWS, staging, production, deployment or mobile-client work was performed.

## Audit and resulting policy

`app/observability.py:init_sentry` is the only Sentry initializer. The pinned
SDK is 2.66.1. Flask, logging and threading integrations are enabled; logging
creates breadcrumbs at INFO and error events at ERROR. `send_default_pii=False`
does not prevent JSON request bodies, exception text, stack locals, custom user
or scope enrichment. Previously only exercise-note events/traces/breadcrumbs
were suppressed. SDK defaults allowed medium-sized request bodies and locals.

| Surface | Hardened policy |
| --- | --- |
| Request JSON | SDK capture disabled globally (`max_request_body_size="never"`); menu event allowlist also excludes injected bodies |
| Stack frames and locals | SDK locals disabled globally; menu events retain exception types only, including chained exceptions |
| Exception values and log messages | Arbitrary text removed; fixed event and type-only classification retained |
| Request URL/query/headers/cookies | Only matched menu route template and HTTP method retained |
| User, tags, extra, contexts, log parameters | Dropped; only fixed event, exception type and server-generated request ID retained |
| Breadcrumbs | Dropped during menu requests and for shared menu-helper logger categories; accumulated/injected breadcrumbs also excluded by final allowlist |
| Transactions and spans | HTTP and generative-AI menu transaction envelopes dropped even when tracing is enabled |
| Envelope attachments | Removed from the SDK hint before transport |
| Independent Sentry Logs | Explicitly disabled; ordinary logging-to-error integration stays enabled |
| Exercise notes | Existing complete event/transaction/breadcrumb suppression preserved |

Menu detection uses the four native/web menu routes, event request URL,
transaction route, shared helper logger name, or shared helper traceback module.
This covers provider/macro/fetch workers without a Flask request context. Shared
`ai`, `ai_nutrition` and `fatsecret` helper errors receive the same restricted
reporting policy, including outside menu requests, because they can carry menu
input and account context. No variable-name blacklist or body regex is used.
Worker events without a request omit its request ID rather than copying scope
identity. Other error-reporting events keep existing behavior, apart from the
explicit global body/local capture restrictions.

## Failure-path mapping

- Native analyze URL/admission, acquisition, parse and analysis rejections use
  fixed event/code logs (`mobile_menu._fail`), at INFO/WARNING; these do not
  create logging error events. Their menu breadcrumbs are suppressed.
- Unexpected acquire/analysis/provider failures use `mobile_menu._unexpected`:
  ERROR, fixed acquisition/analysis event, exception type and request ID.
  These produce sanitized logging error envelopes.
- Native confirmation validation/expiry/idempotency failures return existing
  typed responses. Unexpected confirmation/storage failures use the existing
  ERROR `menu_log_failed` log and produce sanitized error envelopes.
- Genuine unhandled Flask exceptions produce sanitized exception and logging
  envelopes. Helper worker logging/captured exceptions are classified by
  logger/traceback even without a request.
- Fetch-worker subprocess output is consumed as acquisition results, not
  replayed as log messages. Parent fetch/parser logs already use counts, types
  and redacted URL origins. Provider and OCR exception logs that printed raw
  exception objects now print types at their source. No provider, fetch,
  parser, menu, response, authentication or confirmation semantics changed.

## Evidence

`tests/test_menu_reporting_privacy.py` calls the real initializer and pinned SDK,
replacing only the transport with a local envelope collector. It serializes
complete outgoing envelopes and checks literal, JSON-escaped, URL-encoded and
base64 sentinel representations. It injects request URLs/queries/cookies/auth,
HTML/body/title/category/dish/macros, real signed confirmation proofs and their
identities, idempotency, account identity, provider input/errors, frame locals,
breadcrumbs, extra/contexts/tags and span metadata. Upstream event processors
also inject sensitive structures to exercise the final structural allowlist.

The 12 checks cover analyze fetch/parser/provider failures, real confirmation
parsing followed by storage failure, an unhandled Flask menu exception, worker
errors, enabled tracing, real note-envelope suppression, and provider/OCR source
log hygiene. Safe event/type/request-ID visibility is asserted positively.

Mutation runs restore the audited initializer (eight failures), then separately
restore request data (six failures), frame locals (two), breadcrumbs (six), user
identity (six), exception text (two), and envelope attachments (six). Re-enabling SDK body capture or locals
each fails a configuration assertion. Every mutation is restored.

Local regression coverage: 752 cases across observability, log hygiene,
exercise notes, LP15-C/D, mobile feature-gate security, shared AI, and existing
menu tests. An existing LP15-D log test falsely matched nutrition `420` inside
a random allowed request ID; its ID is now deterministic. The affected privacy
and confirmation files pass together (116 cases). No repeated full suite.
