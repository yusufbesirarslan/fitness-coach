# LP15-A native food contract reconciliation

Base: `e6cd07bcbe569d2779e7fb4da956acc0882f42df` (2026-10-07).
Authority: merged backend source. Discovery: complete
`lp15-preflight-2026-10-07/docs/discovery/lp15-native-nutrition-qr-menu-preflight.md`;
mobile comparison snapshot `c8025a9301d9c9e3ff8ab02cfd7a7148aa694a6e`.

## Scope and isolation

Fetched origin/main; it equals the discovery baseline. Original backend working
tree was clean. Implementation uses the dedicated
`feat/lp15-food-contract-reconciliation` worktree.
Open backend PRs inspected: #400 TI qualification (tests/docs), #383 triage docs,
#342 AI provider migration. None overlaps the changed food files. #342 touches
menu/AI/config/conftest, which this PR does not edit.

TI-09 is in the mobile repository, branch inspected read-only through its local
worktree: `338981f` changed `docs/LP15_WORKOUT_EXERCISE_NAVIGATION.md`,
`lib/features/workout/domain/workout_checkpoint_coordinator.dart`,
`lib/features/workout/presentation/state/workout_session_controller.dart`,
`test/features/workout/presentation/active_workout_exercise_navigation_test.dart`,
`test/support/presentation_literal_allowlist.json`. File overlap: zero.
No TI branch is used as backend implementation authority. PR-L qualification
and artifacts remain separate.

## Canonical entities and identity

There is no native AxisAI Food/Serving table or new food UUID. Provider food is
identified by `(provider="fatsecret", food_id)`, and serving by
`(provider, food_id, serving_id)`. IDs are nonempty strings, at most 128
characters; provider integer IDs are projected as strings. Client intent parsing
trims identity strings; returned provider IDs must already be unambiguous.
Provider names/brands/descriptions are display text, never lookup keys.
Duplicate names and duplicate serving descriptions preserve their distinct IDs.
Returned provider food_id, when present, must match the requested ID. Missing,
malformed or duplicate serving IDs produce a sanitized provider failure.

`BarcodeFoodCache.id` is a private DB cache-row ID, not food identity. Its
`food_id` refers to the same provider food. Web cache payloads may contain
synthetic `100g_calc` serving IDs and estimated mass. Native barcode reads now
reuse only cached food identity, then fetch canonical provider servings. This
adds a provider read on cache hits and can return 503 during outages; stale
cache nutrition is no longer presented as canonical native serving truth.
Native reads never populate the cache or write consumption.

`CustomMeal`/`CustomMealItem` are web staging entities, not native food identity
or a second consumption ledger. `MealLog` is the owner-scoped consumption
ledger; its native id/revision are owner-bound opaque tokens. It stores rendered
description and nutrition snapshot, not structured provider IDs or quantities.
Never parse its description to recover food identity.

## Exact food and write schemas

`N = {energy_kcal:number|null, protein_g:number|null,
carbohydrate_g:number|null, fat_g:number|null}`. Null means unknown; zero means
measured zero. No provider body, URL, credential or exception text is projected.

`SearchFood = {provider:"fatsecret", food_id:string, name:string, brand:string}`.
Search returns exactly `{foods:[SearchFood]}`; no macros or menu fields.

`Serving = {serving_id:string, description:string, nutrition:N,
metric_mass:null|{amount:number>0,unit:"g"}, nutrition_per_100g:null|N}`.
`Food = {provider:"fatsecret",food_id:string,name:string,brand:string,
servings:[Serving]}`. Serving and barcode reads return exactly `{food:Food}`.
No label-derived or synthetic serving identity is added. Household units/amounts
stay in the provider description; metric mass is measured grams or null.
`ml`/`oz` are not grams and are not converted. Per-100g reference requires actual
positive grams and all four known nutrients; it is rounded to four decimals.
Normalization overflow yields a sanitized503, never non-finite JSON numbers.
No new default-serving flag is invented; the client chooses from list order and
submits the ID. Quantity default remains one complete selected serving.

Provider log body is closed:
`{kind:"provider_backed",provider:"fatsecret",food_id,serving_id,quantity?,slot,
discovery_source?:"search"|"barcode"}`. Slot is one of
`kahvalti|ogle|aksam|ara_ogun`; quantity defaults to 1 and discovery_source to
search. Client nutrition fields are forbidden.
Manual body is closed `{kind:"manual",description,slot,nutrition:N}` with all
four finite nonnegative values required, description trimmed 1–500, energy
at most 100000 and each macro at most 50000.

Quantity is a multiplier of the **entire selected serving**, not grams or
provider number_of_units. Preferred wire representation: a JSON number,
finite, `0 < quantity <= 1000`. Existing compatibility also accepts a canonical
Decimal-compatible numeric string (dot decimal or exponent). Booleans, null,
zero, negatives, NaN/infinity, over-limit values and decimal commas are rejected.
No explicit fractional-digit cap exists; do not invent one in this PR. JSON
numbers first use Python int/float parsing, then Decimal(str(value)); strings
retain Decimal input precision, while multiplication uses the existing Decimal
context (normally 28 significant digits) and persisted ledger values are floats.
UI locale parsing is not an API authority. No quantity/parser change here.

Macro authority remains `provider_food_snapshot.resolve_provider_food`:
resolve the food, match serving_id only within its catalog, scale base macros
once with Decimal quantity, validate ledger bounds, persist snapshot. Food A +
Serving B deterministically returns FOOD_NOT_FOUND and writes nothing (P1 if
ever accepted). Search → serving → log IDs remain compatible without changes to
the log command, fingerprint, replay behavior, ledger or summary calculation.
Mobile displays base-serving/per100g values; it does not own logged macro math.

`M = {id:string,revision:string,slot,description:string,source,logged_at:
offset_ISO|null,nutrition:N}`; log responses add `day:ISO_date` inside M.
Diary response is `{day:{date:ISO_date,timezone:"Europe/Istanbul"},meals:[M],
totals:N,goal:null|{target_energy_kcal:number}}`.

## Native endpoint matrix

All paths have `/api/v1/nutrition` prefix, require opaque Bearer auth via
`require_mobile_auth`, use owner `g.mobile_user`, and return no-store. Common
errors: native auth 401, AUTH_RATE_LIMITED 429 (retryable), REQUEST_TOO_LARGE 413.
Current configured default request-body cap is 12MiB. Food query max is 100
characters, food IDs 128, barcode input at most 13 digits after trim; no input
can select a different account. Inherited error envelope is exactly
`{error:{code:string,message:string,retryable:boolean,request_id:string}}`.
Messages are stable fallback prose; mobile localizes by code.

Every current native nutrition method/path is recorded below. Closure schema
symbols D/P/H/S and their complete request/success field sets are those in
[NUTRITION_VNEXT_PR7.md §6](NUTRITION_VNEXT_PR7.md#6-dto-schemas), unchanged;
this matrix incorporates that exact schema definition by reference. Closure
error families refer to the exact codes in its §8, not newly defined envelopes.

| Method / path | Request schema | Success schema | Capability errors | Retry | Pagination | Canonical identity | Class |
|---|---|---|---|---|---|---|---|
| GET /foods/search | q trimmed 2–100 Unicode characters | {foods:[SearchFood]} | INVALID_FOOD_QUERY400; FOOD_PROVIDER_UNAVAILABLE503 | safe read | no cursor; max8 | provider,food_id | RECONCILE |
| GET /foods/fatsecret/{food_id}/servings | nonempty ID≤128 | {food:Food} | INVALID_FOOD_ID400; FOOD_NOT_FOUND404; FOOD_PROVIDER_UNAVAILABLE503 | safe read | none | provider,food_id,serving_id | RECONCILE |
| GET /foods/barcode | code trimmed ASCII digits, length6/8/12/13 | {food:Food} | INVALID_BARCODE400; FOOD_NOT_FOUND404; FOOD_PROVIDER_UNAVAILABLE503 | safe read | none | same food/serving tuple | RECONCILE |
| POST /logs | closed provider/manual above; Idempotency-Key 8–64 [A-Za-z0-9._:-] | {meal:M+day}201/200 | INVALID_IDEMPOTENCY_KEY400; INVALID_LOG_FOOD_COMMAND400; IDEMPOTENCY_CONFLICT409; FOOD_NOT_FOUND404; NUTRITION_TEMPORARILY_UNAVAILABLE503 | same frozen command/key | none | owner-bound M id/revision | KEEP |
| GET /diary/today | none; date/zone overrides ignored | diary above | NUTRITION_TEMPORARILY_UNAVAILABLE503 | safe read | none | owner-bound M id/revision | KEEP |
| PATCH /logs/{entry_token} | If-Match quoted revision; closed {operation:"set_slot",slot} | {meal:M} | DIARY_PRECONDITION_REQUIRED428; INVALID_DIARY_PRECONDITION400; INVALID_DIARY_MUTATION400; DIARY_ENTRY_NOT_FOUND404; STALE_DIARY_ENTRY412; NUTRITION_TEMPORARILY_UNAVAILABLE503 | re-read on ambiguity | none | entry_token,revision | KEEP |
| DELETE /logs/{entry_token} | If-Match; no body | 204 | same diary mutation codes | re-read on ambiguity | none | entry_token,revision | KEEP |
| GET /day-view | none | D,contract_version1 | NUTRITION_DAY_VIEW_UNAVAILABLE503; sections may be unavailable in200 | safe read | none | server ISO day | OUT_OF_SCOPE |
| GET /plan | none | {contract_version:1,plan:P,generation_options} | NUTRITION_PLAN_UNAVAILABLE503 | safe read | none | plan revision,PM.id | OUT_OF_SCOPE |
| POST /plan/generate | proteins/carbs/fats 1–10 unique catalog names; custom_foods≤10×1–60 | version1,target,food_rating,proposals[D+proposal_token] | INVALID_PLAN_GENERATION_REQUEST400; NUTRITION_TARGET_REQUIRED409; NUTRITION_PLAN_QUOTA_EXCEEDED402; NUTRITION_PLAN_RATE_LIMITED429; NUTRITION_PLAN_GENERATION_BUSY503; NUTRITION_PLAN_GENERATION_FAILED503 | no automatic retry; spend | none | signed owner-bound proposal_token | OUT_OF_SCOPE |
| PUT /plan | exactly If-Match or If-None-Match:*; {plan:D,proposal_token} | {contract_version:1,plan:P} | nutrition preconditions428/400; INVALID_NUTRITION_PLAN400; INVALID_PLAN_PROPOSAL400; PLAN_PROPOSAL_EXPIRED400; STALE_NUTRITION_PLAN412; NUTRITION_PLAN_UNAVAILABLE503 | re-read/compare | none | plan revision,proposal_token | OUT_OF_SCOPE |
| POST /plan/meals/{planned_meal_id}/log | If-Match plan revision; Idempotency-Key; absent/{} body | {meal:M+day}201/200 | nutrition preconditions; INVALID_IDEMPOTENCY_KEY400; INVALID_PLANNED_MEAL_COMMAND400; IDEMPOTENCY_CONFLICT409; STALE_NUTRITION_PLAN412; PLANNED_MEAL_NOT_FOUND404; PLANNED_MEAL_NOT_LOGGABLE422; nutrition503 | same intent/key | none | owner-bound planned_meal_id,revision,M.id | OUT_OF_SCOPE |
| GET /hydration | none | {contract_version:1,hydration:H} | HYDRATION_UNAVAILABLE503 | safe read | none | owner/day/revision | OUT_OF_SCOPE |
| PUT /hydration | If-Match; {amount:int0..8} | same H | nutrition preconditions; INVALID_HYDRATION_COMMAND400; STALE_HYDRATION412; HYDRATION_UNAVAILABLE503 | re-read/compare | none | owner/day/revision | OUT_OF_SCOPE |
| GET /history | limit1..14 default7; owner-signed cursor | version1,timezone,state,days[date+M+totals],next_cursor | INVALID_HISTORY_CURSOR400; INVALID_HISTORY_LIMIT400; NUTRITION_HISTORY_UNAVAILABLE503 | safe read | newest whole days,opaque cursor | owner-bound cursor,M.id/revision | KEEP |
| GET /supplements | none | version1,cabinet[state,revision,truncated,supplements:S[]] | SUPPLEMENTS_UNAVAILABLE503 | safe read | no cursor; max200 | owner-bound item id/revision,cabinet revision | OUT_OF_SCOPE |
| POST /supplements | If-Match cabinet; closed create schema §6 | version1,supplement:S,cabinet_revision201 | nutrition preconditions; INVALID_SUPPLEMENT_COMMAND400; STALE_SUPPLEMENT_CABINET412; SUPPLEMENT_CABINET_FULL409; SUPPLEMENTS_UNAVAILABLE503 | re-read/compare | none | cabinet revision,item id | OUT_OF_SCOPE |
| PATCH /supplements/{token} | If-Match; nonempty subset of create schema §6 | version1,supplement:S | nutrition preconditions; INVALID_SUPPLEMENT_COMMAND400; STALE_SUPPLEMENT412; SUPPLEMENT_NOT_FOUND404; SUPPLEMENTS_UNAVAILABLE503 | re-read/compare | none | owner-bound token,revision | OUT_OF_SCOPE |
| DELETE /supplements/{token} | If-Match; no body | 204 | same item mutation codes | re-read on ambiguity | none | owner-bound token,revision | OUT_OF_SCOPE |

No native endpoint is removed or designated for later deprecation. Web-only
`/api/food/search`, `/{id}/servings`, `/servings-by-name`, `/barcode` remain
OUT_OF_SCOPE, session-authenticated comparison contracts. Web barcode/add is
already DEPRECATE_LATER with its existing header/successor link; untouched.
Native never imports web search translation, AI/static fallback, name-keyed
cache or synthesized serving semantics.

## Search bounds and errors

Search makes one raw `foods.search` operation with parameter dictionary
`search_expression=q`, `max_results=8`, timeout5s, no page_number. Shared HTTP
adapter has its existing bounded retry total2 (429/502/503/504). There is no
native pagination, cursor, total count, or stable ranking guarantee. Extra
cursor/page/limit parameters are ignored and never forwarded. Locally cap the
response at eight even if upstream exceeds its requested limit. Empty provider
food list/empty foods container yields 200 `{foods:[]}`; transport failures do
not. Refresh preserves IDs where the provider returns the same entities, not
result positions or ordering. Unicode/Turkish characters pass as parameter data
without interpolation into method/URL. Invalid lengths reject before provider.

Native opts into `strict=True` on existing raw helpers; default remains the
legacy web fail-soft behavior. Token/network/connection/timeouts, HTTP non-2xx
(including429), upstream error payloads, malformed JSON/shape/identity map to
FOOD_PROVIDER_UNAVAILABLE503 retryable=true. No raw error/body/URL appears in
response or food-discovery logs. Missing serving food (explicit null or code106)
maps404; barcode value0 maps404. Unknown errors never become a miss. Food get
retains bounded v4→v2→legacy attempts; successful fallback is usable, but if all
attempts fail and any was an outage/malformed result, return503 instead of404.
Provider failures during an existing log remain the unchanged
NUTRITION_TEMPORARILY_UNAVAILABLE503 with same-key retry.

## Barcode boundary

Native accepts trimmed ASCII decimal digits of exactly6/8/12/13 characters,
preserves leading zeros and left-pads to13 only for the upstream lookup. No
truncation of overlong client input, separator removal, numeric coercion, Unicode
digit acceptance or checksum validation is promised. Invalid input400 precedes
provider/cache access. Search and barcode return the same Food/Serving shape.
Local consistency fix: Unicode isdigit inputs now reject rather than reaching
upstream. Shared web normalization is unchanged.

Lengths8/12/13 align with EAN8/UPCA/EAN13 numeric lookup. Length6 is preserved
legacy compatibility, **not a promise of correct UPC-E expansion**; this API has
no symbology field and does not expand UPC-E. Provider docs require UPC-E→UPC-A
conversion before GTIN13 lookup. Do not advertise native camera format support
from the accepted digit lengths. Camera scanning and optional format expansion
remain later product work (P3 legacy support limitation, not a new feature).
The preflight P3 M6 is mobile local-length validation; it stays deferred because
mobile is out of scope. Provider error-code meanings and barcode padding were
checked against [official error codes](https://platform.fatsecret.com/docs/guides/error-codes)
and [barcode documentation](https://platform.fatsecret.com/docs/v1/food.find_id_for_barcode).

## Language, day, security and preserved boundaries

IDs are independent of User.language/Accept-Language. Display/provider names
remain source text; no translation or generated description is added.
User.language remains the existing authority for generated plan prose; no
second header authority. Timezone remains server app.timeutil.APP_TZ
Europe/Istanbul. Diary write/day boundaries, summaries, plans/prompts, history,
mobile, workout/TI, auth architecture, infra/deploy/AWS are untouched.

Food reads are authenticated shared provider catalog data, not account-private
catalogs. Writes/diary use authenticated owner only; foreign user fields reject,
same key across owners creates distinct owner-bound entries. Reads have no
consumption/cache-fill side effects. Existing request cap, query/identity bounds,
blocking concurrency admission and provider timeout/retry ceilings remain.
No external-network penetration tests or real food-provider calls were made.

MENU_CODE_TOUCHED=no. MENU_P0_GATES_STILL_OPEN=yes: preflight B2
credential/scheme/connection policy and B3 globally bounded fetching/parsing
remain P0 gates for later native menu exposure. LP15-A neither fixes nor
downgrades them. LP15-B is not started.

## Validation evidence

Focused: **90 passed** (11.64s). Related nutrition/auth regression: **817 passed**
(45.70s), including native routes, web food characterization, diary mutations,
summary/day view, plan/read/planned logging, barcode, auth and error envelope.
One full backend suite used the repository's existing pytest defaults:
**11142 passed, 20 failed, 95 skipped, 8 deselected** (2492.92s). All failures
are in `tests/test_production_deploy_script.py`; no nutrition/food/barcode/auth
failure occurred. Full-suite opt-in Linux authority/load markers remain
deselected as configured, and opt-in environment tests retain their skips.

All **20/20** failed cases reproduce on the unchanged base (37.81s). The local
macOS fixture selects `/bin/bash` without `mapfile`; its temp archive template
`.axisai-build-archive.XXXXXX.tar` remains literal under BSD mktemp and collides
during rollback. These are existing local platform/fixture failures, outside
LP15-A; deployment scripts and their tests are byte-identical to base. No
deployment was performed: these tests use temporary fake host/tool fixtures.
No second full suite was run, and no unrelated code was changed to address
them. Existing Linux CI is the remaining platform authority.

See [validation metadata](evidence/lp15-a/validation.json),
[full failed-case list and totals](evidence/lp15-a/full-suite-summary.txt),
[base comparison](evidence/lp15-a/base-comparison.json) and its
[assertion transcript](evidence/lp15-a/base-comparison.txt).
Browser tests regenerated tracked nutrition-PR2 evidence; those artifacts were
restored to base bytes and are excluded from this PR. No workflow changes,
no-op CI commits, AWS calls, staging actions or deployment.

`tests/test_lp15_native_food_contract.py` exercises native routes through stubbed
provider HTTP, including duplicate names/labels, server scaling/replay,
cross-food rejection, bounded search, malformed provider payloads, gram/null
semantics, barcode/cache identity, sanitized failures and auth/account isolation.
Existing tests retain web and diary/plan/summary behavior.

Mutation runner retained locally at `/private/tmp/run_lp15_food_mutations.py`
(not committed, as requested). Each selector
must pass without the mutation, fail by contract assertion with it, then restore
all source bytes with SHA256 evidence. Fresh bytecode caches prevent stale
imports. [Mutation results](evidence/lp15-a/mutations.json) and individual
failure transcripts prove M1–M6; no mutation source is committed. Operators were:
M1 food ID→display name; M2 remove serving membership check; M3 remove query
upper bound; M4 publish raw exception message; M5 remove GTIN padding and route
validation; M6 serving ID→label. The JSON records exact test selectors and source
hashes for reproduction against this checkout.
