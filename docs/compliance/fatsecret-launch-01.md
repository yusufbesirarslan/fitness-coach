# FatSecret launch decision — 2026-11-04

Status: **LAUNCH BLOCKED for the existing advisory paths. This PR is a bounded trace and diagnostic suite, not an implemented exclusion boundary.** No backend behavior, subscriptions, routing, data, migrations, deployment or AI-consent code changes. Founder decision on a material fallback remains pending; no source merge is authorized by this document.

Confirmed founder entitlement: approved Premier Free, US dataset, mandatory attribution. It does not establish permission for advice or a retention exception. A paid upgrade is not evidence of either permission.

## Actual data flow at base 216aa7c2

| Input / persistence | Advisory consumer | Why a local label/filter is insufficient |
| --- | --- | --- |
| `fatsecret._food_search_fatsecret` -> `ai_coach._coach_search_food` -> `_tool_fetch_nutrition_and_stage_log` | Personalized `build_evaluation` against `_remaining_macros_for_user`, then `coach_privacy.tool_result` inserted in both provider loops | Projecting only numbers still sends derived Content into advice. PendingAction retains scaled macros but no nutrition-provider lineage. |
| `provider_food_snapshot.resolve_provider_food` -> `mobile_log_food.service.log_food` | `MealLog` -> `_today_nutrition_totals`, calorie metric queries, `coach_handoff_context('nutrition-day')` -> context builder | Writer stores discovery source (`search` / `barcode`), not a server-attested provider/permission chain. `MealLog` has no food/serving reference or provider lineage. An aggregate remains tainted. A manual source alone is not proof of independent authorization. |
| `menu_analysis._lookup_macros_fatsecret`, per-serving or per-100g values scaled using estimated weight | `_menu_score`, `score_compatibility`, score sorting, top-three `coach_picks`; web and native projections | Deterministic scoring is advice too. An LLM used only for weight estimation does not cleanse the resulting macros. |
| `foodcache._cache_macros` -> L1 / Redis -> menu `macro_source='cache'` | Same menu scoring and picks | Cache stores numbers without origin, then assigns a generic source. Old mixed cache values cannot be relabeled as independently generated. |
| `MealLog` (including provider food, Coach and menu confirmations) -> menu `consumed` -> `remaining_macro_budget` | Scores, warnings, reasons and ranks of **every** menu item | Even an independently estimated menu item can be affected by FatSecret through the consumed-budget input. Filtering item sources alone does not isolate advice. |
| Coach tool-assisted replies and signed summaries in `memory_manager` / `coach_memory_provenance` | Future context windows | PA06 signatures attest ownership/authorship, not the provider licence of nutrition facts. Existing signed history/summary cannot be assumed advice-safe. Client history is already rejected, which is a useful separate control. |
| `barcode.recommend_for_food`, `analyze_food`, portion logic | Food scores, goal-specific messages and portion recommendations | Adjacent proven deterministic advisory output from the same provider food. Needs the same withholding boundary; a disclaimer does not permit it. |

Stored `CustomMealItem` additionally keeps food/serving IDs, descriptions and per-100g macros. It can supply copied/scaled logging values. No provider API, staging database or production state was queried during this trace.

## Smallest reliable separation (design; not active)

A boundary needs two planes: permit lookup, factual display and user-selected logging; give advice only server-attested, independently authorized inputs. Unknown, mixed, cached, copied, scaled and aggregated lineage must fail closed. Exclude values **before** scoring, prompt/tool insertion, retrieval and memory summarization, rather than instructing the model to ignore them.

Do not retrofit a trusted flag from client payloads, `source='manual'`, absence of the word FatSecret, current cache labels or signed ownership metadata. Existing rows cannot reliably be backfilled from those fields. A partial safe total is not the user's remaining daily budget: omit budget-dependent advice when intake completeness is unknown; never substitute zero consumption.

Fresh advisory estimates may use a separately authorized existing AI path only when it starts from independently sourced menu text/user input, uses no shared macro/scan-result cache or FatSecret lookup, and is labeled as an estimate. Merely replacing the displayed source is prohibited. Existing static-food-table authorship/licence must also be established before treating it as independently authorized.

Training, water, body measurements and independently supplied goals can continue where their actual input graph is independent. Prior nutrition-derived conversation history, summaries, pending actions and continuation/confirmation replies need quarantine from advice; preserving their factual display is a separate retention question. No changes to the LP21 consent/fence implementation are proposed.

## Exact affected behavior and bounded release fallback

**Preferred no-regression route:** hold release of the affected functionality until FatSecret explicitly permits the described Coach/tool/diary-summary and QR deterministic/AI recommendation use in writing, and confirms the required retention permissions. The founder may seek this separately. Do not send a permission request automatically.

**Alternative requiring explicit founder approval before implementation/release:**

1. Preserve permitted US food search, barcode lookup, selected-serving logging and attributed factual nutrition display, subject to the retention correction below.
2. Coach continues independent training/recovery/water/body coaching. Keep independently supplied nutrition questions possible only after removing the direct FatSecret lookup, unproven stored food data, calorie metrics, daily-review totals, remaining-budget/evaluation fields and tainted memory/continuations from every advice input. User-visible daily diary review and logged-intake personalization become unavailable. Clearly explain the limitation; do not silently drop Premium capability.
3. QR may display factual menu nutrition, but withhold compatibility scores, warnings/reasons, goal/remaining-budget-based ranking and top-three picks whenever either menu macros or consumed intake has unproven lineage. Menu browsing and explicit factual logging remain. Independently computed fresh AI estimates can be retained only through the isolated path described above; they do not restore a reliable remaining-day budget.
4. Apply the same factual-display/advice separation to barcode food/portion recommendations and Coach confirmations; otherwise they bypass the proposed boundary.
5. Keep historical tracking records visible only under verified retention permission or compliant refresh. Do not delete or blank diary history as an unapproved fallback.

This fallback **materially reduces nutrition personalization and QR recommendations**. It is not active in this PR. It needs a bounded implementation PR, positive controls for preserved advice/tracking, negative sentinels at both provider message loops and deterministic scorers, plus owner review. No feature flag, price, model or subscription change is proposed.

## Terms and retention: bounded findings

Checked official sources on 2026-11-04:

- [Attribution policy](https://platform.fatsecret.com/attribution): content screens, one prelogin placement, website and store description. The mobile/landing PRs handle attribution. Prepared TR/EN descriptions already contain `Powered by fatsecret nutrition API (www.fatsecret.com)`; no ASC mutation.
- [API Terms](https://platform.fatsecret.com/terms), §§1.3, 1.5–1.7: retain attribution links; require a terms link and application user-terms incorporation; prohibit advice under §1.7(iii). The 24-hour remove/re-request rule applies unless data is explicitly indefinitely storable or a different period is specified by FatSecret. A medical-substitute notice does not authorize advice.
- [Storable data](https://platform.fatsecret.com/docs/guides/storable-data): listed identifiers include `food_id` and `serving_id`; nutrient values, names and serving descriptions are not listed. [Editions](https://platform.fatsecret.com/api-editions) advertises caching for Premier Free, but does not specify blanket indefinite storage of nutrient snapshots. Written account-specific terms may alter permissions; none beyond the confirmed approval was supplied here.

Concrete unresolved retention paths: `foodcache` Redis default is **3 days**, L1 has no age expiry; `BarcodeFoodCache` returns a stored payload without an age check; `CustomMealItem`, `MealLog` and pending/Coach conversation content can retain provider-derived names/macros without a refresh deadline. This prevents a retention PASS under the published default. Refresh-on-read alone would not establish compliance for stale copies still retained elsewhere.

Smallest retention disposition: founder supplies any existing account-specific permission covering raw/scaled macros, historical diary/custom-meal snapshots, caches and Coach content; otherwise prepare a bounded refresh/expiry plan that retains allowed IDs and independently supplied user facts, establishes provider references on new logs, and resolves legacy logs with no IDs **without silently losing tracking history**. Deletion or history regression needs founder approval. No bulk purge, data migration or architecture audit was performed.

## Focused diagnostics and release gate

`tests/test_fatsecret_launch_boundary.py` contains strict expected failures at three real inputs: Coach direct search, diary calorie metric projection and cached menu picks. These expose absent boundaries; **expected failures are release blockers, not passed compliance tests**. Positive controls cover independent water projection and client-history exclusion. Existing FatSecret, Coach tools and menu pipeline tests validate continued behavior.

To reproduce offline (no AWS/provider calls):

```sh
python -m pytest -q -rx tests/test_fatsecret_launch_boundary.py tests/test_fatsecret.py tests/test_fatsecret_lookup.py tests/test_coach_tools.py tests/test_menu_perf_optimizations.py
```

Result: 86 passed, 3 expected failures. Tests inject synthetic data/provider stubs. Existing CI remains authoritative for the source PR, but a green CI result cannot clear the three known advisory blockers or the retention question. No actual repaired boundary has been claimed.
