# AI unit economics and Bedrock reconciliation

Billing is the financial authority; provider-reported tokens are the usage authority; application events are the attribution authority. This document qualifies FINOPS-01 against base 2a437ef350b275744ef7c417709a1a01b8d66a2d. It does not authorize deployment, inference tests, cache activation, or infrastructure changes.

## Events and identities

The only paid SDK door is `app/services/ai_provider_call.admit()`. The ordering remains final payload → hard input/output budget → capacity permit → spend charge → physical SDK attempt → event. SDK retries are disabled; explicit door retries charge and emit separately. A physical attempt is an SDK invocation, including a failed invocation; a guard/input refusal is an event but **not** a provider attempt. `health_probe` bypasses the application spend charge, not AWS billing.

Every physical attempt normally emits one `[AI-USAGE]` record. Extraction/emission failures cannot affect serving; consequently a failing log sink can lose an event. Exactly-once delivery to CloudWatch is not guaranteed. Duplicate schema-2 exports can be detected using (admission_id, attempt); the offline helper refuses overlap rather than silently deduplicating. Legacy duplicates cannot reliably be detected.

Legacy event = implicit schema 1 (no schema_version). Current code = schema 2. Existing ordered keys and price fields retain their prefix order; new fields are appended:

| Field | Authority and finite semantics |
|---|---|
| schema_version | Integer 2; absent means legacy 1 |
| admission_id | Generated UUID hex, 32 lowercase hexadecimal characters; identifies one admitted payload across retries, not a product action |
| job_id | Existing current RQ job UUID only; arbitrary custom job names are excluded |
| billing_profile | global, geographic, direct, unknown; derived from exact code-owned runtime model identities |
| fallback | Boolean; set only at existing OpenAI fallback decisions, for the matching feature |

No environment field is added. Production web/worker identity can be derived from the known EC2 log-stream prefix and Docker service/project labels in `/axisai/app`. This establishes the source of those events, not of all account billing. Other environments, CI, operators and AWS callers remain unproven sources of the residual. Never derive environment from request headers, hostnames or caller input.

Subject identity remains the existing internal numeric account ID from `subject_scope`. Authenticated web/mobile routes use the canonical owner; background RQ summarization loads Conversation.user_id from the database. No new subject scope or entitlement change is introduced. Health probes legitimately have null subjects. Any other null/invalid subject must be exposed explicitly, not folded into a user denominator.

Request correlation reuses the server-generated request ID. Streaming already transfers it to its producer; macro/menu executor workers and deferred summary now preserve it through `bind_request`. RQ jobs retain their existing UUID instead of inventing a synchronous request ID. Thread state is restored after execution. IDs are structural, never content-derived. The offline helper outputs aggregates, never individual IDs.

## Canonical capability inventory

The taxonomy in `ai_input_budget.normalize_feature()` is unchanged: changing it could change guard/budget semantics. Generic forwarders accept the audited caller's feature. `other` is a compatibility bucket, not an intentional current product capability.

| Feature / product capability | Callers / entry points | Provider | Action and attribution |
|---|---|---|---|
| coach | ai_coach Bedrock/OpenAI tool loops; ai_stream stream loop; ai.get_ai_feedback / generate_workout | Bedrock, OpenAI fallback | Server request per turn; authenticated subject; rounds/retries and streams. Tool nutrition/vision attempts retain their own feature and shared request |
| training_plan | web/mobile training generation inject partial(_heavy_complete, feature=training_plan), including repair | Bedrock, OpenAI fallback | Generation request; canonical owner; multiple completion/repair calls can share the request |
| nutrition_plan | nutrition_plan_generation; mobile_nutrition_closure forwarding helper | Bedrock, OpenAI fallback | Plan-generation request; owner; forwarded feature remains nutrition_plan |
| nutrition | ai_nutrition normalization/search/suggestion/weights/macro batches; nutrition.meallog log_meal/review_meals; menu_analysis weights/macros | Both | Request may fan out into worker batches; owner/request propagated. These are different low-cost operations within the existing bounded nutrition policy |
| menu_extract | menu_extract.extract_meals_from_menu | Bedrock, OpenAI fallback | Extraction request; owner; associated OCR/nutrition can share correlation but keep their features |
| menu_ocr | menu_ocr._extract_text_from_image; scanned PDF pages | OpenAI vision | Per-upload request, multiple image/page attempts; image units recorded; owner inherited |
| vision | ai_coach._tool_analyze_gym_photo; menu_extract.validate_pump_check; mobile_pump_checks.analysis.analyze_image; mobile_pump_check_comparisons provider helper | Bedrock | Existing default vision feature; request/owner; one/two image units. No new budget label |
| summary | memory_manager.summarize_if_needed via tasks.summarize_conversation or deferred ai_pipeline | OpenAI | Deferred request or RQ job; database conversation owner; background cost separately identifiable |
| health_probe | bedrock_health._probe_once, startup/deep health invocation | Bedrock | Non-product, null subject, charge=False; timeout 5 seconds / output cap 1 preserved |
| other | No intentional current production caller | Both compatibility only | Historical unknown attempt must remain explicit; no invented action/owner |

All production-capable SDK sends were searched structurally, including SDK methods, model invocation/converse, messages/streams, images and OpenAI completion/response APIs. No paid direct bypass was found. Eight SDK send references plus the generic stream producer are admitted; client construction is not an invocation. Tests scan app, fitx_mcp and scripts (excluding tests/docs) and exercise injected bypass examples. Arbitrary dynamic aliases cannot be universally proven by syntax alone; inventory and actual provider-boundary tests remain complementary.

A logical action is an existing request/job, not an attempt or admission. Requests can include rounds, retries, provider fallback and cross-feature fan-out. The helper counts unique safe request/job IDs per capability and refuses a complete denominator if any attempt is uncorrelated. Its per-feature cost/action is **that capability's cost within correlated requests**, not an all-in Coach turn or menu upload when separately labeled tools also run. Sum attempts across all features sharing the existing request/job to inspect all-in actions in a restricted offline analysis. A job with several product actions or multiple HTTP requests for one action needs a stronger existing authority; report NOT YET ATTRIBUTABLE instead of equating requests with actions. Background summary costs remain separately visible. The production sample below contains no observed tool-round/fallback events, so all-in action economics beyond this sample are not established.

## Usage and price semantics

Success and complete streams retain final provider usage. Bedrock input excludes separately reported cache creation/read; do not add cache into normal input again. OpenAI prompt_tokens includes cached_tokens, so input_tokens = prompt_tokens − cached_tokens; cache_read_tokens holds the cached part. Images use existing payload-derived image_units, not image content.

Provider errors, timeouts, disconnects and successes without usage retain the admitted input upper bound with usage_source=estimated and output_tokens=null. Unknown output is never zero. Cost on an incomplete attempt is a known-component list estimate, not complete COGS. Provider totals are null when any attempt lacks an authoritative component; known_provider_tokens and uncertain_token_attempts expose the known subtotal and missing coverage. Aggregate estimates retain known components. Complete action costs and user median/p95 require provider quantities for all four token components and known prices for every included attempt. Users with incomplete economics are counted and excluded from complete-cost statistics.

PRICING_VERSION remains 2026-09-23. The existing Sonnet 4.5 global table ($3 input / $15 output / $3.75 cache creation / $0.30 cache read per million) and gpt-4o-mini table ($0.15 / $0.60, cache read $0.075) are preserved; no memory-based repricing. Sources: [AWS Bedrock pricing](https://aws.amazon.com/bedrock/pricing/), [OpenAI model pricing](https://developers.openai.com/api/docs/models/gpt-4o-mini), [OpenAI cache pricing](https://openai.com/index/api-prompt-caching/). September observed Sonnet billing independently agrees with uncached table arithmetic. List estimates rounded to six decimals can differ from exact token recomputation; preserve tokens for historical repricing. AWS credits are not applied to feature COGS.

Exact runtime identity recognition prevents a future Claude generation from inheriting Sonnet 4.5 pricing. Known Sonnet/Haiku 4.5 IDs distinguish global / geographic / direct; unrecognized IDs become other/unknown and stay unpriced. Geographic/direct Bedrock estimates are withheld pending a separately verified class-specific price table. This changes telemetry identity only, never the configured model. No giant raw ARN is logged. Observed account metrics include global Sonnet 4.5 and an October 9 eu.anthropic.claude-haiku-5-5 identifier absent from application events; its 10 input / 32 output tokens are not relabeled Haiku 4.5.

## Historical boundary and evidence

Instrumentation introduction commit: 258f84f11dd009443875ca9e2d2090c49e674271 (2026-09-23 14:05:25 UTC). First main/released revision: 5636610ffa3b26da6cf0bcbb43c998bd6882b786, main merge 15:33:30 UTC. First successful production deployment: [run 35885285890](https://github.com/yusufbesirarslan/fitness-coach/actions/runs/35885285890), deploy step 15:57:52–15:58:46 UTC. First durable event: **TELEMETRY_START_UTC = 2026-09-23T15:58:38.746Z** (CloudWatch timestamp); inner event ts is slightly earlier. CloudWatch retention verified read-only: 30 days.

Legacy events precede schema 2 and have no explicit fallback/job/admission/profile fields. Missing fallback means unknown, not false; missing job correlation may prevent action counts. For this exported production interval only, legacy Sonnet profile is global based on observed AWS runtime-model dimensions and exact class token reconciliation. Do not adopt global as a universal legacy default. Historical logs are never rewritten.

FINOPS-00 comparison was September 10 inclusive to October 10 exclusive, UTC: $4.2682354 gross Bedrock versus $0.155820 application list estimate (3.6507%). The gap $4.1124154 decomposes without an other bucket:

| Component | Gross USD | Evidence status / interpretation |
|---|---:|---|
| Complete UTC days before instrumentation (Sep 10–22) | 1.299417 | CONFIRMED pre-instrumentation billing; current coverage cannot be judged on it |
| Sep 23 boundary-day residual | 0.049041 | CONFIRMED quantity, timing/caller split UNPROVEN; do not assign the whole day to before deployment |
| Sep 24 Haiku 4.5 billed class absent from application export | 0.0606364 | CONFIRMED model-class gap; originating environment/caller UNPROVEN |
| Oct 1–9 billing beyond observed health events | 2.703321 | CONFIRMED aggregate residual, source UNPROVEN; October billing provisional |

No retention gap was observed within the corrected interval. Web/worker logs continue after the last durable AI event (October 6 07:29:00.640 UTC). This supports an attribution/source gap, not a proven logger defect. Unknown environment/manual calls, missing attempt events, unknown usage, model/profile mismatch, and timing effects remain hypotheses unless linked evidence exists. Cache accounting and price mismatch do not explain the settled Sonnet class: its tokens/dollars agree exactly. October's 10/32 metric-only tokens remain UNPROVEN billing/timing difference, not a deducted adjustment. No failure usage is observed in the application export; this does not prove account failures were unbilled.

## Reconciliation procedure

Use explicit UTC [start,end) endpoints; Istanbul dates are UTC+03 and must be converted before export. Prefer complete settled UTC days after TELEMETRY_START_UTC. Telemetry ts is **completion/emission time**; AWS metrics/invoice can use invocation or processing time. Identical endpoints alone do not establish identical populations. Boundary-spanning/inflight calls need explicit evidence-backed timing adjustments; otherwise keep their residual UNATTRIBUTED. The selected settled Sonnet class happens to reconcile exactly, not a general guarantee of timestamp equivalence.

1. Export only sanitized AI-USAGE JSON fields from production log streams. Retain refusal events separately; separate health probes. Never export @message, prompt or response content in the analysis artifact.
2. Get AWS/Bedrock Sum metrics with account-only dimensions (avoid double-counting model + account series), plus per-runtime-model dimensions for identity checks. Query eu-central-1 and investigate additional regions if cross-region billing differs; don't assume source region equals inference/billing region.
3. Get Cost Explorer gross Bedrock usage/charges by UsageType for the same UTC days; exclude credits/refunds/tax. Convert 1M-token usage quantities to integer tokens. Preserve CE Estimated and metric collection times. Do not equate Invocation count with successful billed attempts without its outcome semantics.
4. Prepare AWS input below. Distinct model/profile billing_classes map provider-token components to observed AWS component charge/quantity. Unknown identities are not financial attribution. Duplicate classes, class quantities above aggregate observations, matched app quantities above billed class quantities, or component charges above the bill are refused.
5. Run the credential-free helper, inspect token deltas first, dollars second. Only CONFIRMED explicitly documented non-application/timing token adjustments reduce residuals. SUPPORTED/ESTIMATED/UNPROVEN hypotheses remain visible but cannot erase a discrepancy. Health events already explain their own AWS tokens and must not be added again.

```powershell
python scripts/ai_unit_economics.py summarize usage.jsonl --start 2026-09-24T00:00:00Z --end 2026-10-01T00:00:00Z
python scripts/ai_unit_economics.py reconcile --usage usage.jsonl --aws aws-bedrock-usage.json
```

All output is deterministic JSON; no credentials/network/SDK writes, no user-content parsing/output. Input accepts sanitized JSONL, AI-USAGE-prefixed lines or Docker log wrappers containing only the structured event. The helper outputs counts/distributions only, not per-subject IDs. Unpriced or incomplete costs stay visibly partial. Missing AWS cache quantities are null, not zero. All four token components must be known and within tolerance to pass.

Equation: application provider-reported usage + observed health/non-application usage + CONFIRMED timing/billing adjustments + **UNATTRIBUTED** = AWS observed token usage. Input/output/cache each reconcile separately. Negative residuals indicate over-observation and remain visible. List estimate delta is not a billed-attribution delta. Without an explicit safe class-charge mapping, attributed_usd is null and the entire bill remains financially UNATTRIBUTED.

Token tolerance for these complete UTC integer-quantity exports: **0 tokens**. This is a comparison tolerance, not a target attribution percentage. Exact settled Sonnet quantities justify no numerical rounding allowance; incomplete October billing or timestamp-population uncertainty cannot be hidden in a percentage. A later source with rounding must specify its actual resolution and evidence in tolerance_reason. Dollar calculations use Decimal component rates; no arbitrary financial tolerance erases a residual.

AWS JSON fields: start, end, input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, invocations, billed_gross_usd (decimal string preferred), token_tolerance, tolerance_reason. Optional billed_token_quantities distinguishes invoice quantities from metrics; billing_provisional preserves collection context in the input. Optional billing_classes: bounded model, billing_profile, four token quantities and each token field suffixed _gross_usd. Optional legacy_billing_profile requires interval-specific evidence. Optional adjustments: category explicit_non_application|billing_timing, status CONFIRMED|SUPPORTED|ESTIMATED|UNPROVEN, evidence plus token quantities. Adjustment evidence is required but is not copied into aggregate output, preventing arbitrary text leakage. Source inputs/evidence remain auditable separately.

## Observed production reconciliation (not new-code production validation)

[Settled input](finops/evidence/aws-settled.json), [result](finops/evidence/reconciliation-settled.json); [longer provisional input](finops/evidence/aws-longest-provisional.json), [result](finops/evidence/reconciliation-longest-provisional.json). Exports are sanitized aggregates only; raw subject/request IDs stay outside the repository.

| Quantity | Sep 24–Oct 1 UTC (settled) | Sep 24–Oct 10 UTC (longer, October provisional) |
|---|---:|---:|
| Bedrock application / health attempts | 12 / 17 | 12 / 32 |
| AWS account invocations | 94 | 249 |
| App input / AWS metric input | 37,541 / 79,605 | 37,661 / 342,282 |
| Input residual | 42,064 (52.8409%) | 304,621 (88.9971%) |
| App output / AWS metric output | 2,828 / 5,440 | 2,843 / 133,199 |
| Output residual | 2,612 (48.0147%) | 130,356 (97.8656%) |
| AWS billed input / output quantity | 79,605 / 5,440 | 342,272 / 133,167 |
| App/AWS cache write and read | 0 / 0 | 0 / 0 |
| App list estimate / mapped AWS attributed gross USD | 0.155043 / 0.155043 | 0.155628 / 0.155628 |
| AWS gross USD | 0.2156794 | 2.9195854 |
| USD difference / UNATTRIBUTED | 0.0606364 (28.1141%) | 2.7639574 (94.6695%) |

Settled Sonnet is exactly explained by application+health: input 37,541, output 2,828; Haiku accounts for all remaining settled tokens and dollars. Account coverage is 71.8859% dollars for settled days, 5.3305% for the longer provisional interval. There are no confirmed non-application adjustments beyond observed health events. AWS account ClientErrors: one in the settled interval, two in the longer interval; application export has no provider errors/timeouts/disconnects/refusals. Don't invent their token costs or attribute the 65/205 invocation gaps to errors alone.

## Unit economics demonstration

[Safe production aggregate](finops/evidence/unit-economics-production.json), September 24–October 10 UTC: 54 events/attempts total (44 Bedrock + 10 OpenAI); all provider-reported successful attempts. 22 subject-attributed; 32 expected null health; zero unexpected null/invalid; zero feature=other. One active AI user; median/p95 observed estimated gross cost $0.155018, with all included usage priced/complete. This tiny, incomplete account-coverage sample cannot estimate future active-user economics.

| Capability | Attempts | Correlated request actions | Input / output | Gross list USD | Observed capability cost/action |
|---|---:|---:|---:|---:|---:|
| coach | 4 | 4 | 34,043 / 1,406 | 0.123219 | 0.03080475 |
| nutrition (both providers) | 18 | 12 | 5,410 / 1,955 | 0.031799 | 0.00264992 |
| health_probe | 32 | Non-product | 256 / 32 | 0.001248 | Not applicable |

Training, nutrition-plan, menu extraction/OCR, vision and summary have no observed events in this sample: **NOT YET ATTRIBUTABLE**, not zero-cost actions. Retry attempts observed: zero (attempt=1); legacy fallback classification unknown for all 54. New schema qualifies retries/tool rounds/fallback locally but is not deployed. All-in action denominators and missing account activity must not be inferred from physical attempts.

BEDROCK_PROMPT_CACHE remains unchanged, default OFF. Observed cache write/read is zero. The production [FLAGS] inventory excludes operational flags, so it does not independently prove the live cache flag OFF; effective live flag is not re-read from production .env. The handoff expects OFF; no activation occurred. Zero cache is not a defect. No hypothetical savings claim is made from this small sample.

## Logs Insights queries and privacy

Use /axisai/app, a known production stream filter, explicit UTC bounds, and the complete PAID outcome list. This JSON parsing form was executed successfully against the legacy Docker-wrapped production events; appended schema fields are compatible. AWS documents [jsonParse](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax-operations-functions.html) and [discovered JSON fields](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_AnalyzeLogData-discoverable-fields.html).

```text
filter log like /AI-USAGE/
| parse log "[AI-USAGE] *" as usage_json
| fields jsonParse(usage_json) as u
| filter u.outcome in ["success","provider_error","timeout","client_disconnect"]
| stats count(*) as attempts, sum(u.input_tokens) as in_total,
        sum(u.output_tokens) as out_total, sum(u.cache_write_tokens) as cw_total,
        sum(u.cache_read_tokens) as cr_total, sum(u.estimated_cost_usd) as usd_total
  by u.provider, u.feature, u.outcome, u.usage_source, bin(1d)
```

For token reconciliation insert `filter u.usage_source = "provider"`; retain estimated-attempt counts separately. For outcomes/refusals remove the PAID filter. For a sanitized offline export replace stats with display of the explicit event field allowlist, including ts/request_id/subject_id and optional schema-2 fields; do not display @message/@ptr. Request/job grouping belongs in logs/offline restricted analysis, never metric dimensions. Legacy regex queries in RATE_LIMITING.md still parse the preserved key prefix; JSON parsing is preferred. Missing schema fields do not break legacy exports.

Never log prompts/responses, tool/user text, exception messages, emails/names/Cognito sub, credentials, IPs, food/body/workout/conversation content, images or secret-bearing URLs. New fields are fixed enums, booleans or generated structural IDs. Sensitive sentinel tests cover request/response/tool payload and exception content; sink/extraction failures are isolated. No per-user/custom CloudWatch metrics are added. No application database analytics schema is introduced.

## Qualification and limitations

Tests cover exact success/retry/error/refusal/stream/disconnect/health/tool-round/fallback/worker event counts; subject ownership; request transfer; finite model/profile taxonomy; null/cache/list-cost arithmetic; static provider-door inventory; sink/extraction failure behavior. Existing guard/budget/provider/stream/coach suites exercise serving constraints. Independent review additionally pins incomplete economics and unsafe billing mappings.

No new fields are production-validated: this branch is not deployed. Emission is best-effort, retention is finite, account source identity is incomplete, legacy fallback/job/admission fields are unavailable, and completion-time/billing-time boundary populations may differ. October billing is provisional. OpenAI invoice reconciliation is out of FINOPS-01 Bedrock scope. Cross-region aggregate/model dimensions and exact usage types must be rechecked for each future interval. No confirmed source is assigned to Haiku or October residual; those remain explicit UNATTRIBUTED until evidence links them. FINOPS-02 safe infrastructure savings remain unestablished by this observability work.
