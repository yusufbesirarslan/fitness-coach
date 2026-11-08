# AXISAI-LAUNCH-FREE-BE-01 — Launch Free backend (source only)

Audit reference: AXISAI-LAUNCH-FREE-2026-COMPREHENSIVE-AUDIT-01 (baseline
`d13c9fa1`, which is also this branch's base). Nothing here activates AI,
changes a flag default, D1 texts/policies, the service cohort pin, model
routing, Apple configuration or AWS.

## What changed

| Area | Change |
|---|---|
| `ai_allowances/policy.py` | `SCOPE_LAUNCH_FREE`, `CampaignPolicy`, `PERIOD_CAMPAIGN` (`CAMPAIGN_WINDOW`), `RESET_CAMPAIGN_END`, `campaign_window/period_key/reset`; `AllowancePolicy.launch_free`, `scope()` and `bucket_version()` know the third scope; Premium periods tightened to WEEKLY/MONTHLY |
| `ai_allowances/launch_policy.py` | `LAUNCH_FREE_CAMPAIGN` (approved values) attached to `LAUNCH_V1_POLICY`; installer refuses a partial campaign |
| `ai_allowances/service.py` | `_authorize` order Premium → campaign → Starter; `_period_bucket` shared lazy bucket creation; campaign exhaustion details; settlement version per scope; allowance view `LAUNCH_FREE`; `_dispatch_subscription` accepts Apple OFF only under a campaign policy |
| `ai_allowances/execution.py` | `require_ready()` drops the Apple flag from its tuple; Apple OFF requires the complete campaign |
| `ai_allowances/training_compensation.py` | Expiry correction recognizes an installed-campaign bucket |
| `models.py` + `d8f1a3c5e7b9` | Scope CHECK `STARTER | PREMIUM | LAUNCH_FREE`; pinned in `REVIEWED_DDL` |
| `db_init.py` | Fresh create_all-first boots replay the shipped narrow predicate on tables they just created, so immutable `a9c1d2e3f4b5` verifies and `d8f1` widens |
| Docs | `AI_ALLOWANCE_LEDGER.md`, `mobile/ai-allowances-v1.{md,schema.json}`, `CLAUDE.md` |

## Entitlement invariants

1. Effective verified Premium (Apple ON) uses only Premium buckets, unchanged.
2. Otherwise, inside a campaign window, the account uses `LAUNCH_FREE`.
3. Otherwise the existing once-per-account Starter applies.

`LAUNCH_FREE` never writes `effective_premium`, Apple rows or `is_premium`, and
never yields unlimited. Apple OFF alone opens nothing: the execution gate still
needs the ledger flag, Training enforcement, a finite installed policy that
includes the campaign, D1 recipient/purpose grants, the signed adult TR cohort
(`APPROVED_SERVICE_COHORT_VERSION`, still `None` in source), the spend guard and
Redis, and physical per-action budgets. Subscription verification failures with
Apple ON stay `AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE`.

## Migration `d8f1a3c5e7b9`

Expand only, parent `c6f8b0d2e4a1`. Reads the named CHECK; widened and validated
→ no-op; exact original → drop + add the widened predicate (one transaction,
`ACCESS EXCLUSIVE` on the small bucket table for the validation scan, bounded by
the migration lock timeout); anything else (including `NOT VALID`) → stop.
Writes no rows. Previous code is compatible with the widened schema. Downgrade
restores the narrow predicate only while no `LAUNCH_FREE` row exists.

Two repository gates shaped this:

- **Static expand gate** (`scripts/migration_expand_contract.py`) rejects any
  `drop_constraint`/`create_check_constraint` in an expand migration. The
  widening is overlap-safe (the serving revision writes only STARTER/PREMIUM),
  so the file is pinned by digest in `REVIEWED_DDL` (precedent: the K6 fence
  `d7e3a9c1f5b8`). **This pin needs line-by-line human review**; any edit to the
  file fails the gate until re-pinned.
- **R6 shipped-history immutability** (`r6_deploy_transaction.
  classify_migration_delta`) refuses a shipped migration whose bytes changed.
  `a9c1d2e3f4b5` shipped with R1 and pins the narrow predicate in its
  create_all-first verifier, so it is left byte-identical. A fresh boot instead
  presents the shipped predicate on tables it just created
  (`db_init.replay_shipped_predicates`), then the chain widens it.
  Verified: `classify_migration_delta` against `e7bfa139` and `d13c9fa1` →
  `{'d8f1a3c5e7b9': 'expand'}`, no problems.

## Deployment prerequisites (each separately approved; none done here)

- Founder source-merge approval of this PR; exact-head CI.
- Migration applied through the normal R6 release (expand-first).
- Existing launch gates, unchanged by this PR: D1 texts and `LAUNCH_VERSIONS`,
  F01 consent, service cohort pin, `AI_MODEL_ROUTING`/IAM, spend ceilings,
  `AI_ALLOWANCE_LEDGER_ENABLED`, `AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED`,
  `AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED`.
- `APPLE_SUBSCRIPTIONS_ENABLED` may stay 0.

## Rollback

Close AI admission (execution/ledger flags as today). Code rollback = previous
SHA on the widened schema (expand-only; campaign buckets are retained and
inert to old code, which never selects them). Ending the campaign early is a
reviewed source change to `LAUNCH_FREE_CAMPAIGN`.

## Residual risks

- With Apple ON during a campaign window, effective Premium (60/2/2/16/2) has
  less `pump_check` (16 vs 20) and `qr_menu` (2 vs 5) than LAUNCH_FREE, because
  precedence is fixed. Founder decision if Apple is enabled before 2027-02-23.

- Opening AI before 2026-11-25 00:00 TRT gives Starter, and that one-time grant
  is then not available after 2027-02-23.
- Account re-creation yields a new account with fresh campaign units (the
  accepted Launch V1 identity policy); bounded by Cognito verification, the
  signed cohort and spend ceilings.
- A start-date revision after accounts opened `LF1` stretches their existing
  `LF1` bucket to the new window (never a second grant). A different campaign
  must use a new `key_prefix` (validated `[A-Z]{2,8}`) and version.
- The shipped mobile allowance decoder rejects `LAUNCH_FREE` in the view; the
  free build does not read the view (audit), the February build must learn it.

## Cross-repository contract handoff (MOB-01)

- **Scope encoding:** the string `"LAUNCH_FREE"` in `error.details.scope`,
  `access` and `features[].scope`.
- **Exhaustion:** HTTP 402, `AI_ALLOWANCE_EXHAUSTED`, `retryable: false`,
  `details = {feature, scope, reset, resets_at}` (no other keys leave the
  server).
- **`resets_at`:** LF1 → `2026-12-24T21:00:00Z`, LF2 → `2027-01-23T21:00:00Z`
  with `reset: "CAMPAIGN_WINDOW"`; LF3 → `null` with `reset: "CAMPAIGN_END"`.
  Always render the server value; never hard-code dates.
- **After the campaign:** refusals become `scope: "STARTER"`, `reset: "NEVER"`,
  `resets_at: null` — distinct from campaign exhaustion.
- **Other refusals unchanged:** `AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE` (503,
  retryable; also when the runtime gates are incomplete), D1
  `AI_CONSENT_*` envelopes, `AI_SERVICE_ELIGIBILITY_REQUIRED` (403),
  `AI_ACTION_BUDGET_EXHAUSTED` (422).
- **`GET /api/v1/ai/allowances`:** not required by the free build. If read, it
  reports `access: "LAUNCH_FREE"`; `contract_version` stays 1.
- **Prerequisites per account:** signed adult (18+) TR eligibility record and
  the D1 grant for the feature's purpose, exactly as today.
- **Mobile must accept:** `AiRefusal` already maps an unknown scope to `null`
  and ignores `reset`; MOB-01 should add `LAUNCH_FREE` to its scope set and
  neutral (no subscription) exhaustion copy that shows `resets_at` when present.

Sanitized fixtures:

```json
{"error": {"code": "AI_ALLOWANCE_EXHAUSTED", "message": "AI allowance unavailable.",
  "retryable": false, "request_id": "0123456789abcdef",
  "details": {"feature": "pump_check", "scope": "LAUNCH_FREE",
              "reset": "CAMPAIGN_WINDOW", "resets_at": "2026-12-24T21:00:00Z"}}}
```

```json
{"error": {"code": "AI_ALLOWANCE_EXHAUSTED", "message": "AI allowance unavailable.",
  "retryable": false, "request_id": "0123456789abcdef",
  "details": {"feature": "coach", "scope": "LAUNCH_FREE",
              "reset": "CAMPAIGN_END", "resets_at": null}}}
```

```json
{"error": {"code": "AI_ALLOWANCE_EXHAUSTED", "message": "AI allowance unavailable.",
  "retryable": false, "request_id": "0123456789abcdef",
  "details": {"feature": "qr_menu", "scope": "STARTER",
              "reset": "NEVER", "resets_at": null}}}
```

```json
{"contract_version": 1, "policy_state": "CONFIGURED", "access": "LAUNCH_FREE",
 "subscription": {"state": "DISABLED", "entitlement": null},
 "starter": {"state": "ELIGIBLE", "granted_at": null},
 "features": [
  {"feature": "coach", "scope": "LAUNCH_FREE", "state": "AVAILABLE", "limit": 30, "used": 4,
   "reserved": 0, "remaining": 26, "reset": "CAMPAIGN_WINDOW", "resets_at": "2026-12-24T21:00:00Z"}]}
```

(The view fixture shows one of the five features for brevity.)

Open proposal (not implemented): an additive `details.campaign_ends_at` would
let the final-window copy name the end date. Needs a mobile decision first.
