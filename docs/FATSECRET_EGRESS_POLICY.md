# FatSecret egress policy (source only)

Founder disposition [#547 c7240107959](https://github.com/yusufbesirarslan/fitness-coach/issues/547#issuecomment-7240107959) (F1 Option A, F2 nginx masking source only). Nothing is activated: the Food Search release switch (`launch_food_search`) still gates every provider flow first.

## What leaves AxisAI

`app/services/fatsecret_egress_policy.py` is pure and I/O-free.

**`canonical_food_id(value)`.** FatSecret documents `food_id` as a Long. Only a positive signed-64-bit ASCII decimal may leave AxisAI:

- a `str` that fully matches `[1-9][0-9]{0,18}` with a value of at most `2**63 - 1`;
- or a non-bool `int` in range (the range is checked before `str()`).

Everything else returns `None`: bool, zero, negatives, leading zeros, signs, whitespace (including a trailing newline), Unicode digits, decimals and free text.

**`withheld_search_rule(query)`.** These are block-only rules. A hit means no request. Detection normalises the text only: NFKC, `Cf` removed, a Turkish-aware casefold (`İ/I/ı` → `i`), and an ASCII fold of `ş ğ ü ö ç` for the keyword rules. An allowed query leaves exactly as typed.

| Rule | Withholds when the query has |
|---|---|
| R0 `control` | any `Cc` control character, or the query is not a `str` |
| R1 `no_letter` | no Unicode letter |
| R2 `email_or_handle` | an `@` (fullwidth forms included) |
| R3 `url` | a scheme, `www.` or a domain with a listed TLD |
| R4 `long_number` | 7 or more digits in one span joined by separators (Unicode digits count) |
| R5 `contact_keyword` | a term from the closed EN/TR contact list |
| R6 `first_person_health` | a first-person or close-family marker **and** a term from the closed health lexicon |

## Where it is enforced (before any token, slot, request, retry or fallback)

| Path | Check | Answer when withheld or invalid |
|---|---|---|
| `GET /api/v1/nutrition/foods/search` | the route, after the 2–100 length check | 200 `{"foods": []}`, plus one log line `mobile_nutrition event=food_query_withheld rule=<label> request_id=<id>` (never the text) |
| `mobile_food_discovery.search` | before `blocking_concurrency_slot` | `[]` |
| `mobile_food_discovery.servings` (detail route, save-time refetch, LiveNames, barcode cache) | before the slot | `None`: 404 `FOOD_NOT_FOUND`, `ProviderFoodNotFound` or a placeholder |
| `LiveNames._food` | before the cache read and lookup budget | placeholder; budget not spent; `_failed` not set |
| `fatsecret._food_get_raw` (every `food.get`: native, legacy web, barcode) | before `_get_fatsecret_token` | `None`, like provider error 106: no retry, no v4→v2→v1 fallback |
| `fatsecret._food_search_raw` | before the token | strict `[]`, legacy `None` |
| `fatsecret._fs_get` (transport backstop) | after the release switch, before the session | raises `ProviderRequestWithheld` (a `FoodProviderUnavailable`) |

The backstop screens `food_id` and `search_expression` in both `params` and the URL query. It refuses a raw `str` or `bytes` params value, because that cannot be screened. It covers the legacy `_fs_search_foods` (menu macros, web servings by name) and the legacy barcode name lookup. Those callers fetch a token before they call it, but no request follows.

## Unchanged

- Request and response shapes, error codes and envelopes, and the 400 `INVALID_FOOD_ID` (empty, or more than 128 characters).
- `max_results`, attribution, the 22 h expiry and replay withholding, and idempotent replay (no refetch).
- `parse_provider_identity` (it still trims a command's identity before the policy sees it).
- Redis name-cache keys for canonical IDs, persisted identities, the release switch and the no-LLM boundary.
- Valid queries and IDs produce the same provider request. An `int` ID is now sent as its decimal `str`, which is the same bytes on the wire.

No migration, flag, config or mobile change.

## Not covered (residual)

Deterministic screening reduces accidental disclosure. It is **not** anonymisation. The following still pass:

- names (they collide with brands);
- health terms with no first-person marker, and third-person narratives;
- spelled-out numbers;
- text typed before a trigger character (`ahmet.yilmaz` before `@`);
- identifying context outside the closed rules.

`fitx_mcp/server.py` is the standalone stdio MCP server, not deployed by compose. It makes its own `requests.get` FatSecret calls outside `_fs_get` and is not changed here. The legacy browser `/api/food/<food_id>/servings` route still logs its raw path argument at INFO. That is AxisAI-internal, the route is closed by the release switch, and no non-canonical ID now leaves AxisAI.

Tests: `tests/test_fatsecret_egress_policy.py` (the assessment matrix of 37 withheld, 72 allowed and 22 IDs, plus egress with a fake transport). nginx: `tests/test_priv_log_01_nginx_access_log.py` and `tests/test_priv_log_01_nginx_integration.py`; see also `deploy/nginx/README.md` R2.
