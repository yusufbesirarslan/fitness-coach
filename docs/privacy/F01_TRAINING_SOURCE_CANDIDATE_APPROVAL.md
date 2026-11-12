# Founder-approved F01/cohort and Training source candidate — 11 November 2026

Task: AXISAI-FREE-LAUNCH-F01-FOUNDER-APPROVAL-20261111.
Authority: founder's explicit `APPROVE_SOURCE_CANDIDATE_PREPARATION` message.
Approved manifest SHA-256: `e5b2515ea80bf8ab944c1c09d46f713074420dc5a8dfd5de061e95ae0114325d`.
Base: `8154e1b3f8e3ae7ad7d8aca2ad9490eac6c138a1`.
Paired mobile source: `2d54958d419779d9c8072b56af9dc4a11d7dbbd5`.

This isolated source candidate prepares the approved pins. It is not installed
in staging or production. No merge, deployment, environment flag change or iOS
release is authorized by the source-preparation decision.

| Pin | Prepared value |
|---|---|
| `health_disclosure.APPROVED_VERSION` | Existing `health-fitness-v2-review` |
| `ai_launch.APPROVED_SERVICE_COHORT_VERSION` | Existing `age-screen-v1` |
| `ai_consent.POLICIES` | Existing proposed `d1-closure01-training-candidate` only |
| `ai_consent_disclosures.PUBLISHED_TEXTS` | Exact R1/R2 Training EN/TR bodies |
| `ai_consent_disclosures.LAUNCH_VERSIONS` | `training.plan` → `d1-closure01-training-candidate` |

F01 exact text SHA-256s remain EN
`11a79e6110bc70e9e62d27ad8422dcc3d12ad142aa41824f417e991850572c0c`, TR
`10844c943db3c80c6bb89352d62b13f1575ddd3fa45fa280fd5f4d7c75a20ae6`.
O2-B behavior, 13+ product policy and teen/market runtime restrictions are unchanged.
The founder accepts adult-TR signed self-declaration as the current AI service
cohort, including its documented lack of independent age/capacity verification
and periodic declaration expiry. The 600-second expiry is for registration
tickets only. No new age/guardian/renewal mechanism is introduced.

The Training version is recovered from the existing closure-01 approval
manifest; it is not a new ID. Exact current R1/R2 text hashes are EN
`467fc1fb54fca648cec3e737ad7a78fc969f4f74ae0ef735b223d018ab130054`, TR
`062ae5123955dae849ef48f91aa60387443bb53e14e94b2a1eb53808ecd82b80`.
Contract digest is
`2d14b846a5e2b273ca1bb724f507336989998b81cbcafbd7360ba4ea28b05968`.
It binds purpose `training.plan`, ordered categories `fitness_preferences`,
`body_metrics`, `health_constraints`, and only the existing proposed Bedrock EU
Sonnet/Haiku 5.5 tuples, with no nested features or external scopes. This is a
source contract digest, not an assertion of live provider configuration or
production activation approval. Legacy/OpenAI/Global recipients do not match it.

The other four Launch V1 purposes retain their product scope and closed source
registries. Training registration does not open them or Premium purchases.
There is no consent backfill or implicit user grant. PG authority, exact text
and selected contract, fresh user grant, finite allowance/execution, Redis,
spend and physical budgets, and owner/generation/publication fences remain.

Tests retaining historical closed-source scenarios now explicitly remove the
relevant registry/pin in their own fixture. Source assertions verify the exact
single Training registration. Synthetic contract helpers isolate their own
selected versions. No application fail-closed authority is patched to support
the new local source-composition test.

Local source composition uses disposable PostgreSQL, emulated Redis and only
synthetic identity/model-provider entry points. It proves bilingual fresh
registration → F01 → profile → Training consent → canonical first plan, one
finite unit, replay, revocation and withdrawal erasure. Wrong F01 digest,
missing Training publication, absent Redis, retired cohort and an unlisted
Global model deny without a model call or consumed hold. These checks do not
qualify a deployed environment or actual provider availability.

Staging execution requires explicit M4/M5 owner handoff and authorized synthetic
scope, after the owner's zero-health-traffic observation and cleanup. Target
M4/M5 and the complete deployed fresh-account/client flow remain HOLD. A later
production decision must bind final source/images/config, monitoring receipts,
activation order and safe recovery. The dark M4/M5 base keeps approvals closed;
do not deploy this publication candidate into that observation window.
