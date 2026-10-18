# Typed consent refusal continuation

Scope: first training generation, replacement, native nutrition generation; no disclosure publication or endpoint opening. Independent of Legal L1–L5. Base `0d2dbaf5ac7d5a74d8d10457cd76aa73404faedf`.

Confirmed additional loss boundaries beyond BT1: `_CompletionBudget.complete` converted `ConsentDenied` into `GenerationUnavailableError`; replacement transport caught its `BlockingConcurrencyLimit` parent; nutrition `_run` caught all exceptions. These must preserve D1 identity together, otherwise a domain-only fix still returns a misleading 503.

Correction: training completion rethrows only `ConsentDenied`; replacement and nutrition preflight the exact heavy primary recipient/model before claim/retirement or quota reservation. This check grants nothing and never replaces final D1 dispatch. A replacement revocation after preflight records the actual consent code/status and refunds via existing `fail`; nutrition refunds an acquired weekly reservation and rethrows the original typed error. The transport returns the D1 envelope. Provider/parse/IAM errors retain existing handling.

The primary route is truthful to `heavy_primary_route`: Bedrock if enabled with its adapter; otherwise OpenAI. This introduces no fallback, changes no routing and does not validate any recipient. Missing policies continue to deny. Current AI endpoint `_CLOSED_ENDPOINTS` stays intact; no production or staging use is authorized.

First-plan accounting/retirement behavior remains owned by the merged accounting lane (#447) and current #448 scope. This patch changes no allowance/store file, settlement authority, schema or quota calculation. Existing first-plan consent propagation now survives the completion layer; the focused allowance and zero-attempt regression suite must remain green. No claim that a new first-plan accounting protocol was implemented.

Tests distinguish forced preflight/final refusal from real D1 authority tests. Existing mocked-provider replacement transaction tests now explicitly opt into synthetic authority; their PASS does not establish permission. Real default-deny and PostgreSQL D1 tests remain independent. Tests never call providers.

Nutrition deterministic calorie/target calculations remain distinct from meal-plan generation. AI nutrition is mandatory for first limited qualification. No deterministic substitute or non-AI nutrition plan has been introduced.
