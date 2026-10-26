# Mobile and web handoff

No mobile StoreKit #144, convergence, price or marketing files are edited.
Do not expose the new offer until founder quantities, Starter eligibility,
client behavior, D1, payments and deployment/activation are approved together.

Continue using authenticated `GET /api/v1/ai/allowances`, contract_version 1,
`docs/mobile/ai-allowances-v1.schema.json`. There are exactly five canonical
feature rows. Display granted units from **`limit`** (do not invent a `granted`
v1 property), and use `used`, `reserved`, `remaining`, `scope`, `state`, `reset`,
`resets_at`. Top-level `policy_state`, `access`, `subscription` and `starter`
explain eligibility and authoritative entitlement. NULL means unavailable/
unconfigured, never unlimited or zero. Read does not mint or expire balances.

Starter is an account lifetime grant with `reset=NEVER`; it is not Free or a
weekly Premium trial. Reinstall, device changes and login do not refill it.
Premium uses verified server entitlement and finite weekly buckets. A canceled
auto-renewing subscription remains Premium until authoritative entitlement ends.
After expiry refresh entitlement and allowance projection; never show cached
Premium unlimited access or transfer a prior account's balances. Clear all
account-scoped state on account switch. Verification-pending/unavailable means
retry/verification, not a guessed downgrade to Starter.

Show “remaining / limit” with reserved units represented as pending, not used.
Use the supplied UTC reset timestamp formatted in Europe/Istanbul; weekly reset
is Monday 00:00 Istanbul (Sunday 21:00 UTC under current UTC+3 rules). Do not
calculate a rolling seven-day timer or infer ISO week from local device time.
No reset display for Starter. Refresh after success, terminal failure, restore,
expiry, foreground/account switch and an exhausted/in-progress response.

Each new logical action sends a unique `Idempotency-Key` conforming to the
existing ledger key grammar (8–128 printable non-space ASCII characters). Persist that
key across transport retries. Native Training/Pump/comparison keep their existing
domain key contracts. Never generate a fresh key automatically after a timeout,
unknown outcome or in-progress response. Generic terminal replay returns 409
without redispatch; it has no new answer cache. Native durable result routes may
recover a saved result. Generic replay protection has the existing 90-day terminal
retention horizon; retired keys must never be reused for a new user action.

| Existing code | HTTP / retryable | Client behavior |
|---|---|---|
| `AI_ALLOWANCE_EXHAUSTED` | 402 / false | Stop automatic retry; refresh balance/reset; show reset or Starter exhaustion |
| `AI_FEATURE_NOT_IN_PLAN` | 402 / false | Explain unavailable feature/tier; paywall only if an approved paid offer actually includes it |
| `AI_ALLOWANCE_POLICY_UNCONFIGURED`, `AI_ALLOWANCE_DISABLED` | 503 / false | Unavailable service, not a purchase prompt |
| `AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE` | 503 / true | Preserve key; bounded retry after supplied Retry-After; includes uncertain subscription authority |
| `AI_ALLOWANCE_IN_PROGRESS` | 409 / true | Keep pending state/key, bounded polling/recovery, no second action |
| `AI_ALLOWANCE_IDEMPOTENCY_CONFLICT` | 409 / false | Command/key mismatch; do not silently retry with a new key |
| `AI_ALLOWANCE_RESERVATION_CONFLICT` | 409 / false | Retained terminal/expired generic operation; recover saved result where available; explicit new user action only |
| Existing provider/menu/domain failure | Existing typed contract | Refresh projection; failed work may release or consume according to durable billing evidence; never promise all failures free |
| Existing `AI_CONSENT_*` | Existing D1 statuses | Existing consent flow; balance does not grant processing permission |

All new allowance failures use the existing native-style `error.code`, `message`,
`retryable`, `request_id` envelope on mobile and web. `Cache-Control: no-store`.
Reset details come from the read projection. Backend messages are stable generic
English; localize by code, not by parsing prose. A 402 alone is insufficient to
decide to show a paywall. No one-account balance can be cached globally.

## Proposed localized copy (requires product approval)

| State | EN | TR |
|---|---|---|
| Remaining | “{remaining} of {limit} uses remaining this week” | “Bu hafta {limit} haktan {remaining} hakkın kaldı” |
| Premium exhausted | “You've used this week's allowance. Resets {date}.” | “Bu haftaki kullanım hakkın doldu. {date} tarihinde yenilenir.” |
| Starter | “{remaining} introductory uses remaining. These do not reset.” | “{remaining} başlangıç hakkın kaldı. Bu haklar yenilenmez.” |
| Starter exhausted | “Your introductory uses are complete. View Premium's weekly allowances.” | “Başlangıç hakların doldu. Premium'un haftalık haklarını incele.” |
| In progress | “Your request is still processing. Please wait.” | “İsteğin hâlâ işleniyor. Lütfen bekle.” |
| Temporary / entitlement | “We couldn't verify access right now. Try again shortly.” | “Erişim şu anda doğrulanamadı. Biraz sonra tekrar dene.” |
| Unavailable feature | “This feature is unavailable in your current plan.” | “Bu özellik mevcut planında kullanılamıyor.” |

Disable duplicate submit while pending. Keep UI loading bounded; an ambiguous
timeout must not silently spend a new key. For web QR, replace the split paid-OCR
and analysis interaction with an approved combined operation before enabling it.
The web streaming endpoint remains unavailable under D1; use a separately
qualified bounded transport, without treating allowance approval as consent.

## Copy-change inventory (not published)

`locales/en.json` and `locales/tr.json` keys `premium.hero_sub`,
`coach.chat_quota_reached`, `premium.feat_prem.1` currently claim unlimited plans,
chat or re-planning. `premium.py` has legacy Premium unlimited copy/comments.
The mobile owner must inventory corresponding paywall/StoreKit product screens;
mobile sources were intentionally not changed here. Replace with the exact
approved weekly quantities, reset timezone, Starter lifetime terms and feature
availability before purchase. Monthly and annual need the same limit disclosure;
annual payment does not imply a larger weekly grant. No promise of unlimited
regeneration, comparison or streaming while those mappings/transports remain
unapproved. Subscription marketing publication needs a separate founder decision.
