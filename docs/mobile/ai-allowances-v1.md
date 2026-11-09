# AI allowances — mobile contract v1 (MON-02-BE-03A)

Status: **V1 freeze candidate; dark**. Machine-readable response shape: [`ai-allowances-v1.schema.json`](ai-allowances-v1.schema.json). `AI_ALLOWANCE_LEDGER_ENABLED` defaults OFF. No allowance
quantities are approved, so a production server reports `policy_state:
"UNCONFIGURED"` even when the flag is ON. Clients (IOS-01A) must handle every
state below and must never infer a tier, balance or reset from local data,
StoreKit, product IDs or a cached boolean.

## `GET /api/v1/ai/allowances`

- Auth: Bearer (the mobile session). The owner is the authenticated account only.
- Request: no query, body or custom header is read. Anything sent is ignored.
- Flag OFF: `404` before authentication (treat as "capability absent"; do not retry).
- Responses carry `Cache-Control: no-store`.

### 200 body

```json
{
  "contract_version": 1,
  "policy_state": "CONFIGURED",
  "access": "STARTER",
  "subscription": {"state": "DISABLED", "entitlement": null},
  "starter": {"state": "GRANTED", "granted_at": "2026-10-14T09:00:00Z"},
  "features": [
    {"feature": "coach", "scope": "STARTER", "state": "AVAILABLE",
     "limit": 10, "used": 1, "reserved": 0, "remaining": 9,
     "reset": "NEVER", "resets_at": null}
  ]
}
```

(The numbers above are the test fixture, not product values.)

| Field | Values | Meaning |
|---|---|---|
| `contract_version` | `1` | Integer 1, never string/bool; breaking fields, types or enum changes bump it; unknown additive fields may be ignored |
| `policy_state` | `CONFIGURED` \| `UNCONFIGURED` | Whether the server has any allowance policy. `UNCONFIGURED` is not "unlimited" and not "zero" |
| `access` | `PREMIUM` \| `LAUNCH_FREE` \| `STARTER` \| `NONE` \| `UNCONFIGURED` | Which scope applies now. `LAUNCH_FREE` = the finite free campaign window (not a subscription). `NONE` = no AI allowance for this account under current policy |
| `subscription.state` | `AVAILABLE` \| `DISABLED` | `DISABLED` = subscription verification is off on this server, so Premium cannot be effective |
| `subscription.entitlement` | object \| `null` | Verbatim #436 entitlement (`tier`, `effective_premium`, `status`, `expires_at`, `verification_pending`), see `docs/APPLE_SUBSCRIPTIONS.md` |
| `starter.state` | `GRANTED` \| `ELIGIBLE` \| `NOT_ELIGIBLE` \| `UNCONFIGURED` | `ELIGIBLE` = not yet granted; the grant happens server-side at the first AI action |
| `starter.granted_at` | ISO-8601 UTC \| `null` | |
| `features[]` | always the 5 supported features, fixed order | `coach`, `training_generation`, `nutrition_generation`, `pump_check`, `qr_menu` |
| `features[].scope` | `STARTER` \| `PREMIUM` \| `LAUNCH_FREE` \| `null` | |
| `features[].state` | `AVAILABLE` \| `EXHAUSTED` \| `NOT_IN_PLAN` \| `NOT_CONFIGURED` | `NOT_IN_PLAN` = this scope does not include the feature (never unlimited) |
| `limit`, `used`, `reserved`, `remaining` | integer \| `null` | `null` = not applicable/unknown — never render as 0. `reserved` = actions in flight |
| `reset` | `NEVER` \| `WEEKLY` \| `MONTHLY` \| `CAMPAIGN_WINDOW` \| `CAMPAIGN_END` \| `null` | Starter is always `NEVER` (non-renewing). `LAUNCH_FREE`: `CAMPAIGN_WINDOW` while another campaign window follows, `CAMPAIGN_END` in the final window |
| `resets_at` | ISO-8601 UTC \| `null` | Present only when a reset actually exists. Weekly/monthly use the Europe/Istanbul calendar; `CAMPAIGN_WINDOW` gives the next window's start; `CAMPAIGN_END` is always `null` |

Unknown enum values must be treated as "unavailable", not mapped to a nearby state.

### Errors (shared mobile envelope `{"error": {code, message, retryable, request_id}}`)

| Code | HTTP | retryable | Client action |
|---|---|---|---|
| `AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE` | 503 | true (`Retry-After`) | Show "unavailable", keep session, retry later. Never show 0 |
| `AI_ALLOWANCE_ACCOUNT_UNAVAILABLE` | 409 | false | Account no longer exists/usable; re-authenticate flow |
| `AI_ALLOWANCE_POLICY_UNCONFIGURED` | 503 | false | Policy unavailable; preserve a distinct unconfigured state, not capability absence |
| `AI_ALLOWANCE_DISABLED` | 503 | false | Ledger off (normally surfaced as 404 instead) |
| `AUTH_*` | 401/429/503 | per envelope | Existing auth handling |

## Refusal codes for AI actions (defined now; emitted by BE-03B only)

While the flag is OFF, existing endpoints keep their legacy codes
(`COACH_QUOTA_EXCEEDED`, `NUTRITION_PLAN_QUOTA_EXCEEDED`, `PREMIUM_REQUIRED`, …).
After BE-03B wiring, AI endpoints may additionally return:

| Code | HTTP | retryable | Extra fields | Meaning |
|---|---|---|---|---|
| `AI_ALLOWANCE_EXHAUSTED` | 402 | false | `feature`, `scope`, `reset`, `resets_at` | No units left in the applicable scope |
| `AI_FEATURE_NOT_IN_PLAN` | 402 | false | `feature`, `scope` | Scope does not include the feature, or account not eligible |
| `AI_ALLOWANCE_IN_PROGRESS` | 409 | true | — | Same `Idempotency-Key` still executing; wait, do not resend with a new key |
| `AI_ALLOWANCE_IDEMPOTENCY_CONFLICT` | 409 | false | — | Key reused for a different feature |
| `AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE` | 503 | true | — | Ledger/subscription read failed; **no unit used** |
| `AI_CAPACITY_UNAVAILABLE` | 503 | true | — | Service capacity/spend guard refused; **no unit used** (BE-03B) |

A replayed `Idempotency-Key` within the retained reservation window returns the
original outcome and never consumes a second unit. Settlement history is retained
for 90 days; BE-03B must reject stale action replays and bind keys to logical
request identity before calling the ledger. One visible unit = one successful logical action (a Coach turn with
any number of internal tool rounds, one generated plan, one Pump Check, one menu
analysis).

## Client rules (IOS-01A)

1. Re-read this endpoint after sign-in, account switch, a purchase/restore verify,
   and any 402 from an AI action. Clear cached state on sign-out/switch.
2. Never derive `PREMIUM`, a balance or a reset date locally.
3. `null` numbers and `NOT_CONFIGURED`/`UNCONFIGURED` render as "not available", never "0 left".
4. `reset: "NEVER"` must not be described as weekly/monthly; the legacy
   "resets next week" copy applies only to legacy codes.
5. `LAUNCH_FREE` is never Premium: do not show subscription, restore or "manage
   plan" copy for it. Show `resets_at` exactly as sent (never a hard-coded date);
   `CAMPAIGN_END` / `null` means no further free window is promised.

## V1 normative semantics and integration boundary

All fields shown in the 200 schema are required, including nullable fields.
Missing fields, wrong types, duplicate/unknown feature IDs, unsupported versions
or enum values invalidate the observation: no guessed entitlement or balance.
The five feature IDs appear once each, in the documented order. Aliases are not
accepted on this read transport. Unknown additive object fields may be ignored.
Counters are nonnegative JSON integers (booleans are not counters). A finite
AVAILABLE/EXHAUSTED feature has all four counters and obeys
`remaining = limit - used - reserved >= 0`; AVAILABLE means remaining > 0,
EXHAUSTED means remaining = 0 (including a zero grant or all units held).
NOT_IN_PLAN/NOT_CONFIGURED have null counters/reset timestamps; they are distinct
from exhausted, absent capability, expired entitlement and temporary failure.
STARTER finite balances always use NEVER/null. Premium finite balances use
WEEKLY or MONTHLY with a UTC Z timestamp; timestamps are instants, not client
calendar dates. Quantities and cadence remain policy inputs, not V1 constants.

ELIGIBLE reports a server-authorized prospective lazy grant: displayed units are
policy quantities, not persisted ownership. Only GRANTED has granted_at.
GRANTED can be fully exhausted; it never implies AVAILABLE. A configured policy
without a Starter scope reports starter UNCONFIGURED, and a noneligible account
has access NONE. Effective Premium always selects PREMIUM even if its scope or
feature is missing (NOT_IN_PLAN); it never silently falls back to Starter.
UNCONFIGURED policy takes precedence over access and makes all balances unknown;
an existing historical grant may still be reported as GRANTED. Subscription
AVAILABLE includes exactly the #436 entitlement fields; DISABLED has null
entitlement. Expiry/stale verification is resolved by #436, never by a client.

**Freshness relationship:** access and feature scope are derived from the
embedded subscription observation in this response, never from a separate GET
or client cache. This is an informational read, not an authorization lease or a
cross-table serializable snapshot. A concurrent entitlement/usage change can
make it stale immediately. Separate subscription reads have independent
observation times; clients must not splice them into an allegedly atomic view.
AI actions re-authorize and atomically reserve on the server. Re-read after an
action; do not locally decrement, release or refill. No polling/retry schedule
is prescribed by V1. Retry-After is a server hint, not permission to retry
nonretryable errors. HTTP 404 before auth is capability absence even on older
servers; the body need not use the error envelope. HTTP 503 auth failure remains
an authentication transport state, not exhausted allowance.

The GET has no feature selector and cannot emit FEATURE_UNSUPPORTED for a query.
Internal/action-only `AI_ALLOWANCE_FEATURE_UNSUPPORTED` is HTTP 400, retryable
false; unsupported/undecided action features must not be mapped silently. The
other internal invalid-request/reservation codes are not GET response variants.
The action table above is a BE-03B design dependency, not a frozen action API;
BE-03A freezes only this GET and its documented error/status/retryable mapping.

**Commercial decisions do not block this transport freeze.** Existing-user
Starter eligibility, quantities, Premium reset cadence/fallback, extension
families and deletion/re-registration abuse policy remain founder decisions.
With no installed policy, read returns UNCONFIGURED and mutations refuse.
Deletion cascades ledger data; re-registration uses a new authenticated owner
and cannot inherit old balances. Whether the new owner receives a grant is a
future approved server policy decision. The provisional created_at cutoff is
only exercised with explicit test policy; it is not a production promise.
Freeze readiness requires targeted schema/error tests, complete offline
regression and disposable PostgreSQL/migration qualification. Independent
review and an immutable delivered commit are separate integration gates.

## LAUNCH_FREE campaign scope (AXISAI-LAUNCH-FREE-BE-01)

Additive to contract_version 1 (see "Versioning decision" below). Source-only
policy: `coach` 30, `training_generation` 2, `nutrition_generation` 2,
`pump_check` 20, `qr_menu` 5 per window, no rollover. Three fixed 30-day windows
from 2026-11-25 00:00 Europe/Istanbul, start-inclusive, end-exclusive:

| Window | Starts (UTC) | Ends (UTC) | `reset` | `resets_at` |
|---|---|---|---|---|
| LF1 | 2026-11-24T21:00:00Z | 2026-12-24T21:00:00Z | `CAMPAIGN_WINDOW` | `2026-12-24T21:00:00Z` |
| LF2 | 2026-12-24T21:00:00Z | 2027-01-23T21:00:00Z | `CAMPAIGN_WINDOW` | `2027-01-23T21:00:00Z` |
| LF3 | 2027-01-23T21:00:00Z | 2027-02-22T21:00:00Z | `CAMPAIGN_END` | `null` |

Precedence: effective verified Premium → active campaign window → Starter
(10/1/1/1/1, once per account). Before the start and from the end, the account
is on Starter; its refusals say `scope: "STARTER"`, `reset: "NEVER"`. Exhaustion
inside a window refuses with `scope: "LAUNCH_FREE"` and never falls through to
Starter. Window keys are server-side; reinstall/sign-out never resets usage.

Refusal (`AI_ALLOWANCE_EXHAUSTED`, 402, not retryable), LF1:

```json
{"error": {"code": "AI_ALLOWANCE_EXHAUSTED", "message": "AI allowance unavailable.",
  "retryable": false, "request_id": "0123456789abcdef",
  "details": {"feature": "coach", "scope": "LAUNCH_FREE",
              "reset": "CAMPAIGN_WINDOW", "resets_at": "2026-12-24T21:00:00Z"}}}
```

Final window: `"reset": "CAMPAIGN_END", "resets_at": null`.

Versioning decision: `contract_version` stays `1`. Bumping it would make every
shipped decoder reject the view for Starter/Premium accounts too; shipped
decoders already treat an unknown `access`/`scope`/`reset` value as unavailable
(fail closed), and the refusal decoder maps an unknown scope to `null`.
