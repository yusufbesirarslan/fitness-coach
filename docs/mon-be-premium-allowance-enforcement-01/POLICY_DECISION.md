# Allowance decision — estimates, not approved production policy

Recommend **P_30_SAFE (3/1/1/1/1 weekly)** for the next technical qualification,
with no installation or activation. Its name identifies the conservative candidate;
it is **not** a guarantee of profitability under every supported provider payload,
six-week exposure, exchange-rate or failure scenario. The physical-tail results
below prevent an unconditional launch approval. P_LAUNCH should not be represented
as universally profitable at 30% commission.

| Policy | Coach | Training | Nutrition | Pump | QR | Basis |
|---|---:|---:|---:|---:|---:|---|
| P_LAUNCH | 20 | 2 | 2 | 3 | 1 | Original FINOPS proposal, current routing |
| P_30_SAFE | 3 | 1 | 1 | 1 | 1 | Conservative current-cost qualification candidate |
| P_ROUTING_FUTURE | 12 | 2 | 2 | 3 | 1 | Only after future routing/cache qualification |

Starter remains the D22 candidate 10/1/1/1/1, **lifetime, NEVER reset**. No
eligibility cutoff, backfill, NULL-date treatment or account re-registration rule
is selected. Existing account-scoped grants are reused and cannot be replenished
by reinstall, login or another device. Deletion/re-registration policy needs a
founder decision; no cross-account identity tracking is added.

## Evidence and reproducibility

Recovered `~/axisai-finops-pricing-unit-economics-01/` including the requested
decision model, evidence/telemetry, Python model and claim verifier. Original
`verify_claims.py` passed **58/58**. Run the companion
`model/compare_policies.py` from this checkout; it loads the original model's
definitions and SHA-pinned inputs read-only. Complete calculations are in
`out/policy_comparison.json`; the original pack is an explicit reproducibility
dependency, not an unversioned live price lookup. Its source SHA is recorded.

Only four Coach turns from one user and one staging Training observation support
the sparse measured sample; other profiles, population weights, activity, retries
and fallbacks are assumptions/source-derived. **P90/P99 are synthetic heavy-user
profile names, not observed distribution percentiles.** No new observed usage
distribution is invented. AWS credits are excluded. Cache/routing savings are not
live and cannot finance current launch limits.

The pack's actual ASC monthly export row is ₺399.99 retail / ₺269.16 proceeds
at its 15% scenario. The founder's current Small Business Program status controls:
NOT SUBMITTED / NOT VERIFIED. The export is evidence of a price/proceeds row,
not acceptance evidence. Standard 30% is the primary launch sensitivity.
Apple says first-year subscription proceeds are 70% **after applicable taxes**,
and payment/proceeds reports account for tax/commission and settlement effects:
[subscription proceeds](https://developer.apple.com/app-store/subscriptions/),
[payments and proceeds](https://developer.apple.com/help/app-store-connect/getting-paid/view-payments-and-proceeds).
ASC pricing exports show proceeds; VAT precedes commission:
[subscription pricing](https://developer.apple.com/help/app-store-connect/manage-subscriptions/manage-pricing-for-auto-renewable-subscriptions).

We normalize the actual after-tax export as `269.16 / .85`, then apply the
commission counterfactual, FX and refunds. We do not subtract nominal commission
directly from tax-inclusive retail. The original model's 20% VAT / 5% digital
services tax convention reconciles that row; it is not independent new tax advice.
At the pack's pinned USD/TRY 49.654 and assumed 3% refunds, proceeds per month are:

| Offer | 15% | 30% |
|---|---:|---:|
| Monthly ₺399.99 | $5.2581 | $4.3302 |
| Annual ₺3,999.99 / 12 | $4.3818 | $3.6086 |

Annual uses the user's revised ₺3,999.99, replacing the old pack's ₺3,990.
Annual proceeds and the 30% rows are **modeled extrapolations**, not actual ASC
settlements. Obtain the annual/current standard-commission ASC export before
commercial approval. No prices, agreements, purchases or Apple settings changed.

## Cost comparison (USD)

Current means legacy Sonnet 4.5/global plus gpt-4o-mini, cache OFF. Future below
means the pack's C_TARGET_CACHE EU 5.5 scenario, tokens ×1.15 and assumed cache
hits. The JSON also includes future B_TARGET without cache, A_STRESS and D_STRESS.

| Exposure | P_LAUNCH current | P_30_SAFE current | P_ROUTING_FUTURE hypothetical cache |
|---|---:|---:|---:|
| Expected **active** month AI | 3.4090 | 1.1844 | 1.8917 |
| Expected monthly-subscriber AI (80% active) | 2.7272 | 0.9475 | 1.5133 |
| Full 4 weeks, median call mix | 4.5872 | 1.3764 | 2.5594 |
| Full 52/12 weeks | 4.9694 | 1.4911 | 2.7727 |
| Full 5 weeks | 5.7340 | 1.7205 | 3.1992 |
| Full 6 intersected ISO weeks | 6.8807 | 2.0646 | 3.8391 |
| Synthetic P90 active profile | 5.6596 | 1.6346 | 3.0046 |
| Synthetic P99 active profile | 7.4320 | 1.8952 | 3.7794 |
| Full 5 weeks / P90 call mix | 6.7586 | 1.9179 | 3.6593 |
| Full 5 weeks / P99 call mix | 8.6702 | 2.2184 | 4.4407 |

Calendar months can intersect **six**, not merely four or five, Monday-based
allowance periods. The 52/12 assumption is a long-run model; it does not erase
that cash exposure. A reservation stays in its original bucket across a reset.
Bucket quantities are snapshotted and never increased by a policy version update.

Full-use methodology change: Pump uses the more expensive of single/comparison
as a conservative candidate-mapping sensitivity; exemptions are explicitly added.
This gives P_LAUNCH $4.9694 versus the original $4.8604, which used the single-Pump
mix. Original expected $2.7672 subscriber **AI + non-AI** cost still reconciles
exactly (AI $2.7272 + non-AI $0.04). Unlimited remains approximately $6.59 and
−25.3% in the original scenario, not a fresh cohort observation.

## Expected contributions

Original assumed monthly activity 80%, annual 70%, non-AI variable cost $0.05
per active month. These are estimates; high-cost sensitivity uses $0.20 and every
subscriber active. Expected margins are not heavy-user guarantees.

| Policy / cost scenario | Monthly 15% contribution / margin | Monthly 30% contribution / margin | Annual 15% contribution/year / margin | Annual 30% contribution/year / margin |
|---|---:|---:|---:|---:|
| P_LAUNCH current | $2.4909 / 47.4% | $1.5630 / 36.1% | $23.5261 / 44.7% | $14.2469 / 32.9% |
| P_30_SAFE current | $4.2706 / 81.2% | $3.3427 / 77.2% | $42.2133 / 80.3% | $32.9341 / 76.1% |
| P_ROUTING_FUTURE hypothetical cache | $3.7047 / 70.5% | $2.7768 / 64.1% | $36.2720 / 69.0% | $26.9928 / 62.3% |

The JSON contains every monthly/annual, 15%/30%, 4/5/6-week full-utilization
contribution and annualized contribution. Full-use P_LAUNCH already exceeds
monthly standard proceeds. P_30_SAFE sacrifices frequent Coach chat (85% fewer
weekly turns than P_LAUNCH), extra plans and comparisons to preserve a useful
weekly check-in/plan/photo/menu offer. Whether that value supports ₺399.99 is a
product decision; the backend cannot decide the commercial offer.

## Retries, failures and extreme physical exposure

The pack models normal repair/fallback mixes and stress adds 10% failed heavy
input-attempt cost. Each JSON scenario records these assumptions. An additional
**one billed/UNKNOWN released action per feature per week** adds about **$1.2602
over five weeks** on current average call mixes (future cache $1.0378). This is
separate from ordinary internal retries, which never spend another visible unit.
No production `max_billed_releases` is installed. Recommend testing **0** for
UNKNOWN/billed releases initially, with confirmed zero-attempt failures released;
if the founder chooses 1, rerun the cost decision including its extra exposure.
Support can review proven provider faults without permitting an automatic free
retry loop. This customer-impact choice requires explicit disclosure/approval.

Source-only generic physical attempt ceilings are Coach 30, web Training 12,
Nutrition 8, Pump 4, QR 48. Every retry/tool/batch/fallback shares its action's
ceiling. Native Training keeps its original bounded completions, recovery and
pre-attempt Boolean evidence; the generic 12 is not a replacement native authority.
Existing spend/input guards can refuse sooner. At the **default source** input/
output budgets and current maximum route price, conservative per-action upper
cost estimates are $6.075 Coach, $2.412 web Training, $1.008 Nutrition, $0.216 Pump,
$12.816 QR. These loose bounds deliberately overestimate some real call mixes,
but show that finite visible allowances alone do **not** prove unit profitability.
Actual deployment budget overrides and retry configuration must be qualified.

No policy is declared safe at those maximum physical tails. Qualify materially
tighter supported payload/tool/batch budgets, usable menu fan-out and measured
costs before launch, with product-visible constraints where relevant. Do not add
an undisclosed dollar-only customer quota or depend on speculative routing.
The existing spend guard is an attempt-rate guard, not a USD monthly per-account
profit guarantee; it can also use local-process fallback under degraded shared
storage. It remains necessary but insufficient for financial certification.

## Sensitivity and decision

The reproducible grid spans FX 45/49.654/55/60/65, refunds 1%/3%/8%, both offers,
all routing scenarios. Under current P_30_SAFE, full five-week P99 mix ×1.5 cost
uncertainty + $0.20 non-AI = **$3.5276**. Annual 30% proceeds $3.6086 leaves
$0.0809, **2.24%**. Six-week exposure makes that stress negative. At FX65/refunds8%,
annual proceeds $2.6145 yields **−34.92%** under the same five-week stress.
P_LAUNCH stress annual margin is −265.94%; hypothetical cached future is −90.13%
at baseline FX/refunds. No positive expected margin should hide these tails.

Smallest founder package: choose candidate quantities; choose billed-release
budget; resolve Starter cohort/deletion rules and comparison/replacement mapping;
provide current monthly/annual proceeds exports; decide supported physical
workflow budgets and purchase disclosures. Recommend P_30_SAFE for isolated
qualification, reject unlimited/P_LAUNCH as a default launch offer, and retain
P_ROUTING_FUTURE as a future experiment only. **No candidate is installed.**
