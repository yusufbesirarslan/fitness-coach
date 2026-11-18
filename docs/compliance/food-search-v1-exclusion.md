# Food Search release gate (2026-11-18)

Founder decision, 18 November 2026: restore text Food Search on the existing
FatSecret Premier Free entitlement (US dataset, `scope=basic`). This
supersedes the V1 exclusion below for one journey only, behind the default-OFF
rollout flag `FITX_FOOD_SEARCH_ENABLED`:

| Surface | Flag OFF (default) | Flag ON |
|---|---|---|
| `GET /api/v1/nutrition/foods/search` | 404 `FOOD_SEARCH_UNAVAILABLE` | FatSecret `foods.search` (8 results) |
| `GET /api/v1/nutrition/foods/fatsecret/<id>/servings` | 404 | FatSecret `food.get*` projection |
| provider-backed `POST /api/v1/nutrition/logs` (+ replay, provider-copy repeat) | 503 held | server re-resolves serving × quantity, stamps 24h provenance |
| native/browser barcode, browser `food` blueprint, browser diary builder | closed | **closed** |
| Coach, QR Menu, social suggestions, workers calling FatSecret | refused | **refused** |
| worker / deep-health proxy probe | `disabled` | probes |

`launch_food_search.available()` is true only when the flag is ON and the
current request endpoint is in `NATIVE_RELEASE_ENDPOINTS`; every FatSecret
HTTP/token call keeps calling `require_available()`, so a shared-client caller
outside those endpoints still refuses. The 24h retention boundary
(`provider_retention`), attribution and the #497 advice seam are unchanged.
The mobile client is gated separately at compile time
(`AXISAI_FOOD_SEARCH_ENABLED`, default false), so the build in App Review
never calls these routes even if the server flag were ON.

Activation (not authorized by this change): host FatSecret key, an https
`FATSECRET_BASE_URL` with allowlisted egress (the public nginx
`/fatsecret/rest/server.api` location stays closed), real staging E2E, and a
mobile release built with the define.

---

# Türkiye V1 Food Search exclusion (historical, superseded in part above)

Founder decision, 16 November 2026: [issue #520](https://github.com/yusufbesirarslan/fitness-coach/issues/520).

`launch_food_search.FOOD_SEARCH_AVAILABLE=False` is source-controlled, not an
operator flag. Configuration, cached tokens, consent, subscriptions or country
selection cannot reopen discovery. The full browser food blueprint and native
search/servings/barcode routes refuse before authentication, cache reads, query
processing, AI fallback or provider work. All FatSecret HTTP wrappers and token
acquisition also refuse. Native discovery services refuse before caches.

The existing retention permission is preserved as evidence but cannot grant
market access: provider persistence requires both permissions. Provider-backed
LogFood (including replay) is held before reading/persistence. Resolver-based
builder writes stay held; identifiable provider-provenance copies cannot be
imported as new manual writes. Historical reads, revisioned manual edit/delete,
manual numeric entry and independent menu_estimated writes retain their prior
health/consent/provenance/retention boundaries. No migration or purge is added.

Browser discovery controls and provider-serving edit/import controls are hidden
or disabled, and no provider-serving refresh runs on existing-data read.
Native hubs retain manual entry and the separate QR scanner handoff.

Tests that characterize dormant provider paths explicitly request
`food_search_reopened` (test-only, no production config equivalent). Default
closed exclusion tests never request it and cover warm/cold provider isolation,
manual persistence/mutation and nonretryable route denial. Existing provider
retention mechanics are maintained for historical data, not launch enablement.

Release acceptance must bind final backend/mobile SHAs, closed loaded constants,
blocked stale routes, manual save/read/edit/delete/reopen and independent QR
consent/result/diary parity to the final RC. Positive Food Search/serving/barcode
provider QA and commercial entitlement are deferred under #520, not mandatory V1
gates after this exclusion is technically qualified. No provider request is
needed to prove denial. No deployment or activation authority is granted.

Worker heartbeat and daily maintenance also use the closed gate: the optional
FatSecret proxy sampler returns disabled before reading provider config or
sending a request. Deep health reports disabled without consulting a stale
proxy cache; no fake healthy sample is written. Heartbeat and historical
provider-retention operations remain independent and continue normally.

The legacy standalone fitx_mcp source is not a V1 entrypoint/service or Coach
registry route; its MCP dependency is absent from the production dependency
install. Do not start/register it under this launch. This closure does not
claim that manually running unused standalone source grants provider rights.
