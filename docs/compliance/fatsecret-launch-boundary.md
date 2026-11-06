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
| Web barcode advice (`analysis`, `recommendations`, `portion_recommendation`) | **Removed from the response.** No shipped client read them | Closed |
| `BarcodeFoodCache` payload re-served without an age limit | **Live lookup only; no new rows.** Native only ever read `food_id` | Closed for serving. Existing rows remain at rest |
| FatSecret macros in the shared Redis and L1 cache | **Not written:** search, social and menu analysis. Old `food:macros:v1:*` isn't read and expires within 3 days | Closed. The cache can't feed provider values to QR or Coach |
| Direct Coach and QR lookups inside D1 | No request, since #486 | Closed |
| Diary totals to Coach tools, Coach context, the nutrition-day handoff and the QR budget | Unchanged. `MealLog` has no reliable lineage | **OPEN.** Unreachable only while AI is closed |
| Logged values kept past 24 hours: `MealLog`, `CustomMealItem`, `PendingAction`, Coach content | Unchanged | **OPEN.** Rights required, no small source fix |
| `BarcodeFoodCache` rows and old Redis keys at rest | Not purged; no purge authorized | **OPEN**, retention decision |
| Web `/dashboard-nudges` weekly protein congratulation from `MealLog` | Unchanged; native doesn't use it | Low-impact, founder or counsel judgement |

The diary and Coach paths can't be solved by filtering: a partial total isn't the user's remaining budget. `manual` isn't proof of independence either, since web AI estimates and Repeat write it.

## Before AI opens with FatSecret configured

`tests/test_fatsecret_terms_boundary.py` fails when `POLICIES` is populated, and it keeps a strict expected failure for the diary path. One of these must hold first:

1. FatSecret's written permission covers this use.
2. The #485 boundary ships. It removes logged-intake personalization and budget-based QR ranking on days with unproven rows, and that regression needs founder approval.

## Before public launch with FatSecret configured

- Written permission for keeping logged nutrient values, or a different provider or rights basis.
- Barcode working: #478, because the token request still asks for `scope=basic`, and an observed `barcode` entitlement.
- Attribution in the binary (mobile #173), landing (#77) and store text.
- Premier Free is US and English only, so Turkish food data needs Premier and #479.

## Provider unavailable

Removing the configuration, or an outage, serves no stored payload. Web barcode answers 404, native discovery 503 `FOOD_PROVIDER_UNAVAILABLE`, and provider-backed diary commits 503. Manual logging keeps working. This is failure behaviour, not a launch mode.
