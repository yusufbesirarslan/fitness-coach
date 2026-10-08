# LP15-C — native menu analysis

`POST /api/v1/nutrition/menu/analyze` — Bearer-authenticated, read-only
restaurant-menu analysis for the native client. Backend only: no QR scanning,
no camera, no OCR, no diary write (LP15-D owns confirmation → write).

## Architecture

One canonical path, two transports.

| Layer | Module | Owns |
|---|---|---|
| Canonical service | `app/services/menu_analysis.py` | `acquire_menu(url, https_only=False)` (URL admission, Drive dispatch, scan cache, bounded main/sub-page/WordPress acquisition, section extraction) and `analyze_menu_text(user_id, …)` (extraction + extraction cache, cache → FatSecret → LLM macro resolution, stated-gram re-estimate, plausibility/band checks, canonical targets/remaining, deterministic score). Moved verbatim from the web blueprint. |
| Web adapter | `app/blueprints/menu.py` | `/api/proxy/scan-menu` and `/api/menu/analyze`: browser request parsing + the historical status codes/strings. Behaviour pinned by `tests/test_menu_web_characterization.py` against a golden recorded on the pre-refactor base. |
| Native adapter | `app/services/mobile_menu.py` + `app/blueprints/mobile_menu.py` | Strict HTTPS intake, closed error table, bounded DTO; route, phases, limits. |

All network bytes still go through the unchanged LP15-B1 boundary
(`menu_fetch` → `menu_remote`, see `docs/LP15_B1_MENU_FETCH_SECURITY.md`):
credential-free subprocess worker, `trust_env=False`, no proxy/netrc/env CA,
DNS + connected-peer admission, manual redirect re-admission, HTTPS→HTTP
rejection, one 30 s / 8 MB / 48-request budget per `acquire_menu` call
(`@menu_operation`), bounded parsers, remote PDF/images refused. Nothing in
that boundary was modified.

The analysis input is composed server-side from the scan exactly as the web
client always composed it (`static/coach_widget.js`: title + headings + body
text, plus source, framework state and headings) —
`menu_analysis.analysis_request_from_scan`. The native client sends a URL
only; it never relays (and so can never substitute) remote text.

## Request

```json
{"url": "https://restaurant.example/menu"}
```

* Closed key set `{"url"}`; any other key (menu text, macros, headings,
  framework state, `user_id`, account, …) → `400 INVALID_MENU_REQUEST`.
* Owner is `g.mobile_user` only; query string/headers are not read.
* HTTPS only: `http://` → `400 MENU_HTTPS_REQUIRED`, before the shared
  HTTP/HTTPS fetcher is called. Userinfo, other schemes, malformed authority,
  control characters/backslash, scoped IPv6, ports other than 80/443, > 2048
  characters → `400 INVALID_MENU_URL`. The canonical `menu_remote.validate_url`
  is then re-applied by the fetcher on every hop.
* Discovered `http://` sub-pages are never followed for native intake, and the
  native scan cache uses its own namespace (`menu:scan:v2:https-only:`) so a web
  scan of the same URL (which may include HTTP sub-pages) is never served to it.
  Redirects that downgrade are rejected by the boundary for every caller.

## Response (200)

```json
{"menu_analysis": {
  "contract_version": 1,
  "source": {"kind": "web_page|google_drive|unknown", "host": "restaurant.example", "title": "…|null (web pages only)"},
  "day": {
    "target":    {"energy_kcal": 2000, "protein_g": 150, "carbohydrate_g": 225, "fat_g": 56},
    "consumed":  {"energy_kcal": 0, "protein_g": 0, "carbohydrate_g": 0, "fat_g": 0},
    "remaining": {"energy_kcal": 2000, "protein_g": 150, "carbohydrate_g": 225, "fat_g": 56}
  },
  "categories": [
    {"name": "Ana Yemekler", "items": [{
      "item_id": "opaque-24-char",
      "name": "Izgara Tavuk",
      "nutrition": {"status": "estimated", "energy_kcal": 330, "protein_g": 62, "carbohydrate_g": 0, "fat_g": 7},
      "estimate": {"source": "fatsecret_serving", "confidence": 0.9},
      "fit": {"score": 100, "flags": ["low_fat", "high_protein", "fits_calorie_budget"], "warnings": []}
    }]}
  ],
  "coach_pick_ids": ["opaque-24-char"],
  "item_count": 1
}}
```

* `day` — the canonical `nutrition_targets` target and remaining budget and
  today's (Istanbul) `MealLog` intake, the same numbers web analysis uses.
* `categories` — extraction order; items sorted by (−score, name) exactly as
  the web response. Bounded by the canonical `MAX_MENU_ITEMS` (80). Dish and
  category names are the menu's own strings, untranslated, truncated only past
  200 / 120 characters. A dish repeated inside one category appears once.
* `nutrition.status = "unknown"` when no authority produced a value: all four
  numbers, `estimate.*` and `fit.score` are `null` (never 0 — the web body
  shows 0 there; native does not fabricate).
* `estimate.source` — canonical provenance token: `fatsecret_serving`, `cache`,
  `fatsecret_scaled`, `llm_stated_grams`, `llm`, `fatsecret_scaled_fallback`;
  `confidence` the canonical 0–1 value (capped at 0.5 after a portion-band clamp).
* `fit` — `nutrition_pipeline.score_compatibility` against the remaining
  budget: `score` 0–100, `flags` ⊆ {`high_protein`, `low_fat`,
  `fits_calorie_budget`, `low_protein_food`}, `warnings` ⊆
  {`exceeds_daily_budget`, `approaching_calorie_budget`,
  `approaching_fat_budget`, `high_carbohydrate_load`}. Locale-free tokens;
  the web body's Turkish `reason`/`warnings` strings are not published.
* `coach_pick_ids` — the canonical top three scored items.
* Never published: fetched body text, headings, framework state, crawl
  errors/sub-page URLs, the (cleaned) source URL beyond its host, raw provider
  output, internal ids.

`item_id` is HMAC-SHA256(SECRET_KEY subkey `axisai/mobile-menu/item-id/v1`,
`user_id \0 category \0 name`)[:18] base64url: opaque, owner-bound, stable for
the same dish/category/user. It is a reference for LP15-D's selection UI, **not
a write authority**: LP15-C persists nothing and resolves nothing. LP15-D must
define its own confirmation authority (it must not trust client-supplied
macros or names).

## Errors

Mobile envelope `{"error": {"code", "message", "retryable", "request_id"}}`.
Codes come from the canonical failure reason, never an exception message.

| Code | HTTP | retryable | When |
|---|---|---|---|
| `INVALID_MENU_REQUEST` | 400 | no | body not exactly `{"url": <string>}` |
| `INVALID_MENU_URL` | 400 | no | malformed URL, userinfo, scheme ≠ https/http, bad port, too long |
| `MENU_HTTPS_REQUIRED` | 400 | no | `http://` input |
| `MENU_HTTPS_REQUIRED` | 422 | no | an HTTPS hop redirected to HTTP |
| `MENU_DESTINATION_BLOCKED` | 422 | no | private/special DNS answer or peer, inadmissible redirect target |
| `MENU_FETCH_TIMEOUT` | 504 | yes | worker/operation deadline |
| `MENU_FETCH_FAILED` | 502 | yes | connection failure, DNS failure, upstream 5xx |
| `MENU_FETCH_FAILED` | 502 | no | upstream 4xx, redirect limit, Drive link private/missing |
| `MENU_UNSUPPORTED_MEDIA` | 422 | no | PDF (MIME or magic), images, other MIME, compressed body, Drive confirmation page |
| `MENU_CONTENT_TOO_LARGE` | 422 | no | body/aggregate/work limit, parser structure limit |
| `MENU_PARSE_FAILED` | 422 | no | unreadable page, nothing extracted |
| `MENU_PROFILE_INCOMPLETE` | 409 | no | no configured daily calorie target |
| `MENU_ANALYSIS_BUSY` | 503 | yes (`Retry-After: 15`) | scrape or AI permit unavailable |
| `MENU_ANALYSIS_RATE_LIMITED` | 429 | yes (`Retry-After`) | per-owner ceiling |
| `MENU_ANALYSIS_FAILED` | 503 | yes | any unexpected storage/provider/code fault |

Every menu fault is caught in the route — none reaches the blueprint
catch-all (`AUTH_TEMPORARILY_UNAVAILABLE`), so a client never discards a valid
session over a menu failure (tested by re-using the same credential).

## Capacity and cost

* Limits (checked up front, keyed on the Bearer owner, after intake
  validation so a malformed request costs nothing): `SCRAPE_RATELIMIT` +
  `AI_RATELIMIT` + `BEDROCK_RATELIMIT` — one native analysis spends what the
  web scan + analyze pair spends. Per-route buckets, as for every route; the
  aggregate provider bound remains `ai_spend_guard`.
* Two sequential phases in one request: acquisition under
  `ai_gate.blocking_scrape_slot()` (the web scrape semaphore, 10 s wait), then
  analysis under `blocking_concurrency_slot()` (the AI semaphore). The scrape
  permit is released before the AI permit is taken — a request never holds both,
  so the `AI + SCRAPE + model excess ≤ threads − 2` reserve invariant is
  unchanged. Model calls inside analysis still take `model_concurrency_slot`
  through the provider door; no new fan-out was added.
* Wall time: ≤ 30 s acquisition + analysis; nginx/gunicorn allow 300 s.

## Logging

Native lines: `mobile_menu event=<fixed> code=<native code> request_id=…`,
`event=<phase>_failed error_type=<type>`, and on success
`event=analyzed categories=<n> items=<n>`. No URL, dish/category/heading/title,
body, model output, exception message, credential or account identity. The
shared service keeps the LP15-B1 hygiene (counts, fixed codes, exception types,
`loggable_url` scheme://host/<path-redacted>); the mobile access log already
writes `user=-`.

## Write boundary

`MealLog = NutritionPlan = CustomMeal = CustomMealItem = 0` writes: the
service reads `UserSession` and today's `MealLog` only. Only the existing
Redis macro / scan / extraction caches are written (content-keyed, not
per-user). AST gate + statement capture in the tests.

## Tests

* `tests/test_lp15c_native_menu_analysis.py` — route-level, real opaque
  Bearer sessions; acquisition through the real `menu_remote` code on the
  offline routed wire (`tests/menu_wire_support.py`) or the real subprocess
  worker for literal private addresses; provider faked only at
  `ai_nutrition._heavy_chat`. Covers the 14 required properties plus phases,
  busy gates, limits, caches and architecture. Mutation evidence (12 mutants,
  all killed): HTTPS intake, DNS admission, downgrade, HTTP sub-pages, dual
  permits, body leak, log leak, MealLog write, auth-shaped fault, extra fields,
  null-vs-zero, limit ordering.
* `tests/test_menu_web_characterization.py` + `tests/fixtures/menu_web_golden.json`
  — 28 web scenarios (+ extraction-call and cache-key observations) recorded
  before the refactor.

## Deferred (P3)

1. Shared extraction swallows provider/spend-guard failures into an empty
   result, so native reports them as `MENU_PARSE_FAILED` (non-retryable) — the
   same ambiguity as web `OUTPUT_PARSING_FAILED`. Separating them needs a typed
   failure from `ai_nutrition._extract_categorized_items` (shared helper,
   web-log-pinned).
2. Google Drive failures other than `MENU_*` boundary codes are historical
   Turkish strings; native maps them to non-retryable `MENU_FETCH_FAILED`,
   including a Drive timeout.
3. Native and web scan caches are separate namespaces, so the same HTTPS URL
   analyzed from both surfaces is fetched twice within the TTL.
