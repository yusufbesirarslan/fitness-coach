# FatSecret egress policy (source only)

Founder disposition [#547 c7240107959](https://github.com/yusufbesirarslan/fitness-coach/issues/547#issuecomment-7240107959) (F1 Option A, F2 nginx masking source only). The pre-QA technical stabilization ([#547 c7242035071](https://github.com/yusufbesirarslan/fitness-coach/issues/547#issuecomment-7242035071), T1–T6) added the length bound, the stricter transport backstop and `diyabet`, and corrected this record.

Nothing is activated. The Food Search release switch (`launch_food_search`) is the outbound provider authority: while it is closed, no token request and no provider request is made. It is **not** the first check on every path: the service-level checks in `_food_get_raw`, `_food_search_raw` and `LiveNames._food` run before it. See [Order of checks](#order-of-checks).

## What leaves AxisAI

`app/services/fatsecret_egress_policy.py` is pure and I/O-free.

**`canonical_food_id(value)`.** FatSecret documents `food_id` as a Long. Only a positive signed-64-bit ASCII decimal may leave AxisAI:

- a `str` that fully matches `[1-9][0-9]{0,18}` with a value of at most `2**63 - 1`;
- or a non-bool `int` in range (the range is checked before `str()`).

Everything else returns `None`: bool, zero, negatives, leading zeros, signs, whitespace (including a trailing newline), Unicode digits, decimals and free text.

**`withheld_search_rule(query)`.** These are block-only rules. A hit means no request. A query longer than `MAX_QUERY_CHARS` = 100 code points is withheld as `length` before any normalisation, regex or other screening runs. 100 is the native search contract: the route answers 400 `INVALID_FOOD_QUERY` above it, so no reachable caller sends a longer query. The bound keeps the work per call finite for service and transport callers. The query is never truncated. Detection normalises the text only: NFKC, `Cf` removed, a Turkish-aware casefold (`İ/I/ı` → `i`), and an ASCII fold of `ş ğ ü ö ç` for the keyword rules. An allowed query leaves exactly as typed.

| Rule | Withholds when the query has |
|---|---|
| bound `length` | more than 100 code points (`len`), checked first |
| R0 `control` | any `Cc` control character, or the query is not a `str` |
| R1 `no_letter` | no Unicode letter |
| R2 `email_or_handle` | an `@` (fullwidth forms included) |
| R3 `url` | a scheme, `www.` or a domain with a listed TLD |
| R4 `long_number` | 7 or more digits in one span joined by separators (Unicode digits count) |
| R5 `contact_keyword` | a term from the closed EN/TR contact list |
| R6 `first_person_health` | a first-person or close-family marker **and** a term from the closed health lexicon (it includes the Turkish `diyabet` and its inflections, such as `diyabetim`, `diyabetli` and `diyabetik`) |

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

The backstop screens `food_id` and `search_expression` in both `params` and the URL query. It fails closed on any shape it cannot screen exactly:

- params other than `None`, a mapping or a list/tuple of 2-item pairs (a raw `str` or `bytes` query, a generator, a set);
- a parameter name that is not a plain `str`. `requests` sends a `bytes` name as is, so `b"food_id"` would otherwise leave unscreened;
- a name that folds (NFKC, `Cc`/`Cf` removed, strip, casefold) to a screened name without being exactly it (`FOOD_ID`, `food_id `, fullwidth forms, a zero-width or NUL character such as `?food_id%00=`);
- a value whose type is not exactly `str` or `int` (a subclass such as `bool` or one overriding `__str__`, a list, `bytes`, `None`, a float);
- a non-`str` URL;
- a GET with a `data`, `json` or `files` body.

Valid callers send a dict of `str` names to `str`/`int` values, so their requests are unchanged byte for byte.

It also covers the legacy `_fs_search_foods` (social suggestion macros, web servings by name) and the legacy barcode name lookup. See [Legacy callers](#legacy-callers-in-the-target-release) for why those cannot fetch a token in the target release.

## Order of checks

The switch is involved in four places, and the local policy checks sit between them. The order differs by entry point.

| Layer | Where | While the switch is closed |
|---|---|---|
| A. Request gate | `launch_food_search.install` (`before_request`): the native discovery endpoints and the browser `food` blueprint | 404 `FOOD_SEARCH_UNAVAILABLE` before auth, so the native route's own policy check never runs |
| B. Discovery entry | `mobile_food_discovery.search` / `servings` / `barcode_lookup` call `require_available()` **first**. Then `search` and `servings` run the policy check. `barcode_lookup` validates its ASCII-digit code (barcode has no text query), and the ID it resolves goes through `servings`. | `FoodSearchExcluded` before any policy check |
| C. Early local policy checks | `_food_get_raw`, `_food_search_raw` and `LiveNames._food` screen **before** any switch check | **They still run.** They are pure and local. A withheld query or invalid ID returns the caller's empty answer. `_food_get_raw` and `_food_search_raw` write one rule-label log line; `LiveNames._food` writes none. No token, slot, request or provider content is involved. |
| D. Token-acquisition gate | `_get_fatsecret_token` calls `require_available()` first, before even a warm token cache | `FoodSearchExcluded`. The service paths turn it into their existing unavailable answer. |
| E. Transport gate | `_fs_get` / `_fs_post` call `require_available()`, then (GET only) the `_require_screened` backstop, then the session | `FoodSearchExcluded` before the backstop |

Outbound provider authority is `launch_food_search.available()`. It needs the host flag `FITX_FOOD_SEARCH_ENABLED` **and** a request endpoint in `NATIVE_RELEASE_ENDPOINTS` (native search, servings, LogFood). Layers D and E enforce it for every outbound request.

**What this means**

- The switch closes every token request and provider request.
- It does not run before every local screening operation. Layer C runs first.
- A service caller of `_food_search_raw` with a withheld query while the switch is closed writes the rule-label line and returns its empty answer. With a valid query, the same call fails at layer D.
- The runtime default is unchanged and closed.
- `test_service_policy_checks_run_before_the_switch_but_nothing_leaves` pins this order.

## Legacy callers in the target release

Every legacy FatSecret caller outside the native journey fetches its token through `_get_fatsecret_token`. That function refuses at layer D unless the current request endpoint is one of the three native release endpoints. This holds even with `FITX_FOOD_SEARCH_ENABLED=1`. So in the target release no legacy path can acquire a token or send a request, whether the query is withheld or not.

| Caller | Exclusion evidence (in source) |
|---|---|
| Browser `food` blueprint: `/api/food/search`, `/api/food/<food_id>/servings`, `/api/food/servings-by-name`, `/api/food/barcode/...` | `launch_food_search.install` returns 404 `FOOD_SEARCH_UNAVAILABLE` before auth for `request.blueprint == "food"` whenever `available()` is false. For these endpoints it is always false. |
| Social suggestion macros (`social._calculate_meal_suggestion` → `_lookup_macros_fatsecret` → `_fs_search_foods`) | `fatsecret_boundary.uncovered_provider_persistence_allowed()` is false outside the native endpoints, so the path returns before the slot and the token |
| QR menu (`menu_analysis`) | It imports `_get_fatsecret_token` and `_lookup_macros_fatsecret` but never calls them. Macros come from `fatsecret_boundary` estimates. |
| Coach (`ai_coach._coach_search_food`) | Only the closed browser `/api/food/search` calls it. The Coach tool uses `fatsecret_boundary.independent_food_search` (an AI estimate, never FatSecret). Its FatSecret leg is `_food_search_raw`, which screens before the token anyway. |
| Barcode name lookup (`_food_find_by_barcode`) | It is reached only from browser barcode routes and `barcode.get_barcode_product`. Barcode is excluded from `NATIVE_RELEASE_ENDPOINTS` on purpose. |
| Workers and other non-request callers | There is no request context, so `_native_release_scope()` is false |

The native release endpoints screen before the token on every path (layers A–C). Because the legacy paths are already excluded, the stabilization changes none of them. `test_legacy_callers_cannot_take_a_token_with_the_flag_on` pins the exclusion. Reopening any legacy path would need its own decision, and that decision must screen before `_get_fatsecret_token`.

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

`fitx_mcp/server.py` is the standalone MCP server. It makes its own `requests.get` FatSecret calls outside `_fs_get`. It stays outside this policy because it is excluded from the deployed runtime:

- The `Dockerfile` installs only `requirements.txt`, which has no `mcp` package.
- `requirements-mcp.txt` is in `.dockerignore`.
- `server.py` imports `mcp.server.fastmcp` at module load.
- The image `CMD` is `gunicorn … starter:app`, and the compose worker runs `python worker.py`.
- No app module imports `fitx_mcp`. Only the developer launcher `start_dev.py` starts it.

The legacy browser `/api/food/<food_id>/servings` route logs `id_state=canonical|non_canonical`, the serving count and its outcome. It does not log the raw path argument (T6). The route also stays closed (see above).

Other residuals:

- The closed browser `/api/food/servings-by-name` route still logs the raw `name` and the cached or matched provider ID. It is unreachable in the target release.
- `_fs_post` is screened by the release switch only. Its one caller is the token request.
- The nginx error log (R1) and unrouted spellings (R2) are described in `deploy/nginx/README.md`.

Tests: `tests/test_fatsecret_egress_policy.py` (the assessment matrix of 37 withheld, 72 allowed and 22 IDs, plus egress with a fake transport). nginx: `tests/test_priv_log_01_nginx_access_log.py` and `tests/test_priv_log_01_nginx_integration.py`; see also `deploy/nginx/README.md` R2.
