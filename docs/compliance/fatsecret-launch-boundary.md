# FatSecret launch boundary

Founder correction, 2026-11-06: food search, barcode lookup, manual logging and QR menu stay in the launch product. The earlier FatSecret-off decision is superseded. This records what the source does about FatSecret's terms and what still needs rights. It reuses the trace in draft PR #485, which predates #486.

No written FatSecret permission is on file. **Nothing here claims compliance, and configured credentials are not evidence of it.**

## Terms relied on

Read from the public pages on 2026-11-06. They state no effective date.

| Source | Text |
|---|---|
| [Terms](https://platform.fatsecret.com/terms) §1.7(iii) | Must not "use the fatsecret Platform API to provide diet, nutrition or health advice, guidance or diagnosis" |
| Terms §1.5 | Remove or re-request Content not "explicitly identified as being storable indefinitely" "within 24 hours", "or such other time as fatsecret may specify" |
| [Storable data](https://platform.fatsecret.com/docs/guides/storable-data) | Indefinite: IDs such as `food_id`, `serving_id`. "all other information must be requested from fatsecret each time" |
| Terms §1.3 | Attribution "in every place that Content is provided or displayed" |
| [Editions](https://platform.fatsecret.com/api-editions) | Premier Free: US only, English, attribution required. Premier: 62+ countries, 26 languages, priced on request. "Custom Caching and Data Storage Permissions" is listed as a feature |

## Paths with the provider configured

| Path | State in this source | Result |
|---|---|---|
| Live search, servings, barcode, selected-serving logging | Unchanged | Permitted use, subject to retention below |
| Web barcode advice (`analysis`, `recommendations`, `portion_recommendation`, `daily_context`) and `goal_impact` on add | **Removed from the responses.** No shipped client read them | Closed |
| `BarcodeFoodCache` payload re-served without an age limit | **Live lookup only; no new rows.** Native only ever read `food_id` | Closed for serving. Existing rows remain at rest |
| FatSecret macros in the shared Redis and L1 cache | **Not written:** search, social and menu analysis. Old `food:macros:v1:*` isn't read and expires on `FOODCACHE_REDIS_TTL` (3 days by default; the production value is unverified) | Closed. The cache can't feed provider values to QR or Coach |
| Name-to-`food_id` map in process memory (`_cache_food_id`) | Unchanged. Holds a food name as its key, bounded, no TTL, never persisted | Low-impact; the value is an ID |
| Direct Coach and QR lookups inside D1 | No request, since #486 | Closed |
| Diary totals to Coach tools, Coach context, the nutrition-day handoff and the QR budget | **#497:** any `MealLog` row today makes the day unproven (`fatsecret_boundary.intake_unproven`); Coach gets no remaining budget, the calorie tool is unsupported, context carries the notice, QR advice is null (never zero) with no ranking | Closed (the #485 boundary, shipped as #497) |
| Logged values kept past 24 hours: `MealLog`, `CustomMealItem`, `PendingAction`, Coach content | Unchanged | **OPEN.** Rights required, no small source fix |
| `BarcodeFoodCache` rows and old Redis keys at rest | Not purged; no purge authorized | **OPEN**, retention decision |
| Web `/meal-log/today` `targets` and `remaining` over logged totals; the native nutrition day's calorie target beside its totals | Unchanged and reachable today. Arithmetic, no recommendation text | **OPEN**, founder or counsel judgement on whether numeric target comparison is "guidance" |
| Web `/dashboard-nudges` weekly protein congratulation from `MealLog` | Unchanged; native doesn't use it | Low-impact, founder or counsel judgement |
| `fitx_mcp` `analyze_and_rank_menu`: live FatSecret macros to a score and warnings | Unchanged. Nothing starts it and `requirements-mcp.txt` is excluded from the image, though the code ships in it | Not reachable as deployed. **Don't run it with real credentials** |

The diary and Coach paths can't be solved by filtering: a partial total isn't the user's remaining budget. `manual` isn't proof of independence either, since web AI estimates and Repeat write it.

## Before AI opens with FatSecret configured

The gate required one of: (1) FatSecret's written permission covering this use, or (2) the #485 boundary. **(2) is met by #497** (merged 2026-11-09), which closed the former strict expected failure for the diary path. Founder decision G1, 2026-11-12, records this basis for registering the D1 purposes; it relies on no FatSecret email.

`tests/test_fatsecret_terms_boundary.py::test_fatsecret_advice_boundary_holds_with_all_d1_purposes_registered` runs with all five D1 purposes registered. It fails if the registry changes, or if any FatSecret-derived ledger value reaches Coach or QR advice. Companion tests pin that D1 Coach food lookup makes no FatSecret call and that Nutrition Plan generation reads no ledger or provider data.

What this boundary costs, unchanged since #497: on a day with any logged meal, Coach has no logged-intake budget and QR has no budget ranking. Restoring either needs a separate, permission-backed change (real-time totals only), with this gate retargeted to that exact scope.

## Before public launch with FatSecret configured

- Written permission for keeping logged nutrient values, or a different provider or rights basis.
- Barcode working: #478, because the token request still asks for `scope=basic`, and an observed `barcode` entitlement.
- Attribution in the binary (mobile #173), landing (#77) and store text.
- Premier Free is US and English only, so Turkish food data needs Premier and #479.

## Provider unavailable

Removing the configuration, or an outage, serves no stored payload. Web barcode answers 404, native discovery 503 `FOOD_PROVIDER_UNAVAILABLE`, and provider-backed diary commits 503. Manual logging keeps working. This is failure behaviour, not a launch mode.
