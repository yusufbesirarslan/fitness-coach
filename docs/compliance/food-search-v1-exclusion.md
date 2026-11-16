# Türkiye V1 Food Search exclusion

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
