# Scoped unit economics requalification

Only the proposed 20 / 2 / 2 / 3 / 1 weekly offer is recomputed. No price, visible allowance, deployed setting, routing or caching change is assumed. All figures are USD unless TRY is explicit. This is a contribution model, excluding fixed costs, CAC and taxes not already reflected in the exported proceeds.

## Evidence and method

`model/qualified_inputs.json` freezes the existing FINOPS model’s SHA-checked affected unit-cost outputs, profile weights and provenance. `model/requalify.py` reproduces this package offline with Python’s standard library; `model/results.json` contains all unrounded rows. The inherited profiles and population weights are scenarios, not observed production percentiles or a completion-rate study.

The earlier qualification’s current-live assumption remains global Sonnet 4.5 plus gpt-4o-mini, caching OFF. This task does not reattest deployment configuration. The source permits the existing dark Sonnet 5.5 / Haiku 5.5 routing but does not select it. The current catalog uses exact model identities and standard prices, with regional 10% premiums; unknown models and nonstandard tariffs refuse. Catalog values are conservative engineering list-price assumptions, not an AWS invoice, billing reconciliation, or a consent grant.

Primary price references checked 2026-10-26: [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing), [OpenAI gpt-4o-mini](https://developers.openai.com/api/docs/models/gpt-4o-mini), [AWS Bedrock pricing](https://aws.amazon.com/bedrock/pricing/), [AWS Haiku 5.5 model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-5-5.html). Sonnet 4.5 global input/output = $3/$15 per million tokens; gpt-4o-mini = $0.15/$0.60; future Sonnet 5.5 global = $2/$10, Haiku 5.5 = $0.10/$0.50 below 100K input. The existing per-call input caps never reach Haiku’s higher long-context tier. No cache hit or batch discount is assumed.

The monthly price is 399.99 TRY and Annual 3999.99 TRY. Only the Monthly 15% after-tax export of 269.16 TRY is observed evidence. Annual proceeds and Apple 30% are proportional estimates, normalized from that row, not ASC settlement records. Base FX = 49.654 TRY/USD; refunds = 3%; Monthly active share = 80%, Annual = 70%; non-AI variable cost = $0.05 per active month. Annual rows below are per month of the Annual subscription.

Expected means preserve the inherited token/tool/repair/retry distributions, replacing unqualified Pump comparisons with the qualified single-image operation. Coach summaries remain in the original amortized Coach cost once. Means are never clipped to an admission ceiling: refusal is not a cheaper successful answer. The old corpus lacks final frozen request vectors, so scenario fit is conditional; synthetic fixtures below do not establish production completion probability.

## Expected utilization

| Plan / Apple | Net proceeds / month | AI / subscriber month | Contribution / month | Margin |
|---|---:|---:|---:|---:|
| monthly_30 | 4.3302 | 2.7157 | 1.5745 | 36.36% |
| annual_30 | 3.6086 | 2.3762 | 1.1974 | 33.18% |
| monthly_15 | 5.2581 | 2.7157 | 2.5024 | 47.59% |
| annual_15 | 4.3818 | 2.3762 | 1.9706 | 44.97% |

## Full allowance utilization and calendar exposure

Full utilization uses the median profile’s token/tool mix at every visible unit, all subscribers active, plus median standalone exempt lookup/workout-proof use and $0.05 non-AI/month. It is not a claim that every workload fits, nor the physical worst case.

| Weekly buckets in month | AI cost | Monthly 30% contribution | Annual 30% contribution / month |
|---|---:|---:|---:|
| 4 | 4.4865 | -0.2064 | -0.9280 |
| 4.33 | 4.8604 | -0.5802 | -1.3018 |
| 5 | 5.6082 | -1.3280 | -2.0496 |
| 6 | 6.7298 | -2.4496 | -3.1712 |

Actual Annual exposure is 52/53/54 weekly buckets, not six resets in every month. Current full-use Annual contribution at Apple 30% is -$15.62 / -$16.73 / -$17.84 per year respectively, including calendar-scaled exempt and non-AI costs. At Apple 15% it is -$6.34 / -$7.45 / -$8.56. A six-bucket month remains a valid local peak, not a new monthly allowance.

## Enforced physical tail, retries and UNKNOWN

| Routing sensitivity | Weighted offer / week upper bound | Five weeks | Six weeks | Extra five-week UNKNOWN release tail* |
|---|---:|---:|---:|---:|
| current | 20.1708 | 100.8540 | 121.0248 | 13.4490 |
| future_global | 13.5160 | 67.5800 | 81.0960 | 9.3100 |
| future_eu | 14.8676 | 74.3380 | 89.2056 | 10.2410 |

*Sensitivity only: one additional UNKNOWN/billed released logical action per family per weekly bucket. The proposed release policy is not installed. Each row includes all retries inside each action’s vector; do not add per-attempt caps again. New keys consume the existing finite allowance/release authorities. UNKNOWN receives no physical credit, even if telemetry says zero or is absent. Proven zero-attempt refusals release without billed-release consumption. These are upper bounds, not forecasts, and exclude separate exempt operations, infrastructure and fixed costs. Each feature vector can be individually tighter; vector extrema need not be simultaneously attainable in the real workflow.

## Future routing sensitivity, cache OFF

| Scenario | Expected active AI / month | Expected Monthly 30% margin | Expected Annual 30% margin | Full five-week AI | Full six-week AI |
|---|---:|---:|---:|---:|---:|
| A_CURRENT | 3.3946 | 36.36% | 33.18% | 5.6082 | 6.7298 |
| A_STRESS | 4.5762 | 14.53% | 10.26% | 7.8608 | 9.4329 |
| B_TARGET | 2.8639 | 46.17% | 43.48% | 4.7310 | 5.6772 |
| B_GLOBAL_PARITY | 2.2639 | 57.25% | 55.11% | 3.7399 | 4.4879 |
| D_STRESS | 4.3641 | 18.45% | 14.37% | 7.4960 | 8.9952 |

`A_CURRENT` is inherited expected current routing. `A_STRESS` uses the inherited retry/repair-heavy mix plus 10% failed heavy attempts. `B_TARGET` is future EU routing with tokens ×1.15, `B_GLOBAL_PARITY` is an optimistic global token-parity sensitivity, and `D_STRESS` is future EU routing with tokens ×1.30 and the heavy failure mix. None activates routing. Future EU mean-mix five/six-week full usage is still loss-making against base 30% proceeds.

## FX and refund sensitivity

`model/fx_refund_sensitivity.csv` includes 120 combinations: FX 45/49.654/55/60/65, refunds 1%/3%/8%, Monthly/Annual, Apple 15%/30%, and five/six-week exposure. Expected current-routing contribution ranges from $0.20 to $3.17 per subscriber month across those inherited scenarios. All full-use five/six-week 30% cases remain negative. Annual export evidence, realized retry/UNKNOWN frequency, subscription activity and payload distribution remain decision gaps. No new FX feed, ASC export, live provider benchmark or financial forecast was invented.

## Decision

The physical execution boundary removes unbounded per-action escalation. It does not make the proposed offer profitable at full utilization. Keep the commercially compelling 20-Coach qualification target unchanged and require an explicit financial acceptance decision, real payload-fit/answer-quality evaluation and remaining consent/transport qualification before paid launch. No price or allowance reduction is proposed or installed.
