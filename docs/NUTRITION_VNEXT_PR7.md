# NUTR-PR7 — Native Nutrition API closure

Existing canonical authority → transport-neutral service → Bearer `/api/v1` adapter → (PR8 Flutter later).
No second Nutrition backend, ledger, plan authority, cabinet or Flutter-owned truth. No model, schema
or migration change (Alembic head unchanged). No Flutter change.

## 1. Baseline

| Item | Value |
| --- | --- |
| Repository | `yusufbesirarslan/fitness-coach` |
| `origin/main` at start | `56bcf18bafd87012d82170883eb9f9c5b9a5f8d8` (= deployed NUTR-PR6, deploy run 36817325702) |
| Branch / worktree | `feat/nutrition-vnext-pr7-native-api-closure` / `.worktrees/nutrition-vnext-pr7-native-api-closure` |
| PR6 release gate | CLOSED — public smoke PASS (`/health` 200 db ok + redis limiter, `/login` 200, `/nutrition` 302 → `/login?next=%2Fnutrition`), authenticated visual smoke WAIVED (no safe session; zero production mutations) |

## 2. Pre-change native endpoint inventory

`GET /api/v1/nutrition/diary/today`, `GET /api/v1/nutrition/foods/search`,
`GET /api/v1/nutrition/foods/fatsecret/<food_id>/servings`, `GET /api/v1/nutrition/foods/barcode`,
`POST /api/v1/nutrition/logs` (Idempotency-Key + semantic fingerprint),
`PATCH|DELETE /api/v1/nutrition/logs/<entry_token>` (strong `If-Match`), `POST /api/v1/coach/messages`
(`{"message"}` only), `GET /api/v1/coach/history`. All preserved byte-for-byte.

## 3. Capability gap matrix (A–O)

| # | Capability | Canonical authority | Web route | Native before | PR7 result | Auth | Write semantics / precondition | Owner isolation | PR8 needs |
|---|---|---|---|---|---|---|---|---|---|
| A | Day view read | `nutrition_day_view.build_nutrition_day_view` | `GET /nutrition-day-view` | none | **Implemented** `GET /api/v1/nutrition/day-view` | Bearer | read-only | `g.mobile_user` | yes |
| B | Plan active read | `NutritionPlan` newest row (`nutrition_plan_store.newest_plan`) | `GET /nutrition-plan/active` | none | **Implemented** `GET /api/v1/nutrition/plan` | Bearer | read-only | owner-scoped | yes |
| C | Plan generation | `nutrition_plan_generation` (extracted verbatim from web) + premium `nutrition` bucket | `POST /nutrition-plan` | none | **Implemented** `POST /api/v1/nutrition/plan/generate` | Bearer + AI limits + AI gate + quota | proposal only, no write | owner-scoped target | yes |
| D | Plan save/replace | `nutrition_plan_store.replace_nutrition_plan` (shared with web) | `POST /nutrition-plan/save` | none | **Implemented** `PUT /api/v1/nutrition/plan` | Bearer | `If-Match` revision / `If-None-Match: *` + signed proposal | owner-bound revision + proposal | yes |
| E | Hydration read | `WaterLog` via `hydration.read_today` | `GET /water` | none | **Implemented** `GET /api/v1/nutrition/hydration` | Bearer | read-only | owner-scoped | yes |
| F | Hydration write | `hydration.set_today_count` (+ shared `water_logged` funnel) | `POST /water` | none | **Implemented** `PUT /api/v1/nutrition/hydration` | Bearer | absolute amount, `If-Match` | owner-bound revision | yes |
| G | History | `MealLog` | `GET /meal-log/history` | none | **Implemented** `GET /api/v1/nutrition/history` | Bearer | read-only, cursor | owner-bound cursor | yes |
| H | Supplements list | `Supplement` via `supplement_cabinet` | `/supplements` page | none | **Implemented** `GET /api/v1/nutrition/supplements` | Bearer | read-only | owner-scoped | yes |
| I | Supplement create | `supplement_cabinet.create_supplement` (shared with web) | `POST /supplement/add` | none | **Implemented** `POST /api/v1/nutrition/supplements` | Bearer | `If-Match` cabinet revision | owner-bound | yes |
| J | Supplement update | `supplement_cabinet.update_supplement` | `POST /supplement/edit/<id>` | none | **Implemented** `PATCH /api/v1/nutrition/supplements/<token>` | Bearer | `If-Match` item revision | owner-bound token | yes |
| K | Supplement delete | `supplement_cabinet.delete_supplement` | `POST /supplement/delete/<id>` | none | **Implemented** `DELETE /api/v1/nutrition/supplements/<token>` | Bearer | `If-Match` item revision | owner-bound token | yes |
| L | Planned meal → consumed | `MealLog` via `meal_idempotency.commit_once` | `POST /api/quick-add-meal` (user-wide key replay — not copied) | none | **Implemented** `POST /api/v1/nutrition/plan/meals/<planned_meal_id>/log` | Bearer | `If-Match` plan revision + required `Idempotency-Key` + fingerprint | owner-bound | yes |
| M | Menu analysis | `/api/proxy/scan-menu`, `/api/menu/analyze` (logic inside web routes) | yes | none | **Deferred — OPTIONAL PARITY** (§17) | — | — | — | no (PR9 parity) |
| N | Custom/build meal | `CustomMeal`/`CustomMealItem` staging | `/api/diary/*` | none | **Deferred — OPTIONAL PARITY** (§17) | — | — | — | no (PR9 parity) |
| O | Coach handoff | `coach_handoff` + LP-09 pipeline | `/coach?review=nutrition-day` | message only | **Implemented** optional `handoff` marker on `POST /api/v1/coach/messages` | Bearer | one model call, shared quota | server re-derives day view | yes |

## 4. Final route table (all `@require_mobile_auth`, `mobile_api` blueprint, `MOBILE_AUTH_ENABLED`-gated, `Cache-Control: no-store`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/nutrition/day-view` | PR6 day view, verbatim |
| GET | `/api/v1/nutrition/plan` | saved plan state + generation catalogue |
| POST | `/api/v1/nutrition/plan/generate` | explicit generation → proposals |
| PUT | `/api/v1/nutrition/plan` | save/replace from a proposal |
| POST | `/api/v1/nutrition/plan/meals/<planned_meal_id>/log` | planned meal → MealLog |
| GET | `/api/v1/nutrition/hydration` | today's hydration |
| PUT | `/api/v1/nutrition/hydration` | absolute desired count |
| GET | `/api/v1/nutrition/history` | bounded history page |
| GET | `/api/v1/nutrition/supplements` | cabinet |
| POST | `/api/v1/nutrition/supplements` | create |
| PATCH | `/api/v1/nutrition/supplements/<supplement_token>` | update |
| DELETE | `/api/v1/nutrition/supplements/<supplement_token>` | delete |
| POST | `/api/v1/coach/messages` (existing) | + optional `"handoff": "nutrition-day"` |

Every path is admitted by EXACT entry in `tests/test_mobile_auth_feature_gate.py`,
`tests/test_sprint12_daily_coach_discovery.py` and `tests/test_sprint13_nutrition_closure_discovery.py`
(no prefix wildcard). Native-auth OFF → all 404 (feature-gate test).

## 5. Authority map

| Fact | Single authority | Web transport | Native transport |
|---|---|---|---|
| Day view | `app/services/nutrition_day_view.py` | `nutrition/day_view.py` | `mobile_nutrition_closure.nutrition_day_view_native` |
| Saved plan persistence | `app/services/nutrition_plan_store.py` | `/nutrition-plan/save` (UNCONDITIONAL) | `PUT /plan` (precondition) |
| Plan schema / score | `nutrition_plan_schema`, `plan_score` | same | same |
| Plan generation | `app/services/nutrition_plan_generation.py` | `/nutrition-plan` | `/plan/generate` |
| Water | `app/services/hydration.py` (`WaterLog`) | `/water` (+ moved `claim_water_funnel_for_today`) | `/hydration` |
| Cabinet | `app/services/supplement_cabinet.py` (`Supplement`) | `/supplement/*` | `/supplements*` |
| Consumed food | `MealLog` via `meal_idempotency.commit_once` | quick-add, `/meal-log` | `/plan/meals/<id>/log`, existing `/logs` |
| Coach context | `coach_handoff.coach_handoff_context` | `/ask` | `/coach/messages` |

Native contract code: `app/services/nutrition_native/` (`tokens`, `preconditions`, `errors`, `plan`,
`hydration`, `history`, `supplements`) — adapters only.

## 6. DTO schemas

Nutrients object `N` = `{"energy_kcal": number|null, "protein_g": number|null, "carbohydrate_g": number|null, "fat_g": number|null}` (null = unknown, never 0).

**Day view** — byte-identical to PR6: `{contract_version:1, day:"YYYY-MM-DD", target:{state,value:number|null,unit:"kcal"}, intake:{state,totals:{calories,protein,carbs,fat}|null,meal_count:int|null}, hydration:{state,amount:int|null,unit:"glass"}, plan:{state,summary:{name:string|null,planned_meal_count:int}|null}, next_action:{state:"available"|"empty",kind:"log_food"|"set_target"|"retry"|null,label_key:string}}`. Section states `available|empty|unavailable|invalid` (never `loading`).

**Plan read** — `{contract_version:1, plan:P, generation_options:{proteins:[string],carbs:[string],fats:[string],max_per_group:10,max_custom_foods:10}}` where
`P = {state:"active"|"absent"|"invalid", revision:string|null, saved_at:ISO-8601+03:00|null, food_rating:number(0..10)|null, name:string|null, meals:[PM]|null, totals:N|null}` and
`PM = {id:string(24), slot:"kahvalti"|"ogle"|"aksam"|"ara_ogun", items:[string]|null, nutrition:N, loggable:bool}`.
`absent`: all fields null except `state`. `invalid`: `revision` + `saved_at` present, content null (replaceable).
`generation_options` strings are DISPLAY labels in the authenticated owner's `User.language` (LP14): exactly `"en"` → English, anything else → Turkish — the same partition `build_prompts` uses for the generated plan. No request field, header or query chooses it. The canonical identity stays the Turkish `FOOD_DATABASE` `isim`; the static map lives in `app/services/nutrition_native/generation_labels.py` (validated at import: complete per locale, no label names two foods).

**Generation request** — closed `{proteins:[name], carbs:[name], fats:[name], custom_foods?:[string]}`; names = known catalogue labels of that group in ANY supported locale (current English, current Turkish/canonical, or stale after a language switch), mapped to the canonical name before rating/prompt — unknown strings refused; 1–10 per group, unique AFTER mapping; custom foods ≤10, 1–60 chars, no control chars.
**Generation response** — `{contract_version:1, target:{value:int,unit:"kcal"}, food_rating:number, proposals:[{plan:D, proposal_token:string}]}` where proposal document
`D = {name:string|null, meals:[{slot, items:[string]|null, nutrition:N}], totals:N}`.

**Save request** — headers: exactly one of `If-Match: "<plan revision>"` or `If-None-Match: *`; body closed `{plan:D, proposal_token:string}`. **Response** `{contract_version:1, plan:P}` (= next GET).

**Planned meal log** — headers `If-Match: "<plan revision>"`, `Idempotency-Key` (8–64 `[A-Za-z0-9._:-]`); body absent or `{}`. Response `{meal: M}` where `M` is the existing LogFood projection `{id, revision, slot, description, source:"ai_plan", logged_at, nutrition:N, day}`; 201 created, 200 replay.

**Hydration** — `{contract_version:1, hydration:{day, timezone:"Europe/Istanbul", state:"available"|"empty"|"invalid", amount:int|null, unit:"glass", max_amount:8, revision:string}}`. Write: `If-Match: "<revision>"`, body closed `{amount:int 0..8}` → same object.

**History** — `GET ?limit=1..14 (default 7)&cursor=<opaque>` → `{contract_version:1, timezone, state:"available"|"empty", days:[{date, meals:[M without day], totals:{energy_kcal,protein_g,carbohydrate_g,fat_g}}], next_cursor:string|null}`.

**Supplements** — `S = {id, revision, product_name, brand, category:"protein"|"amino_acid"|"pre_workout"|"vitamin_health"|"creatine"|"other"|"unknown", status:"active"|"low_stock"|"finished"|"unknown", ratings:{effect,taste,digestion,price: int 1..5|null}, review_text:string|null, price_paid:number|null, is_public:bool|null, created_at:ISO Z}`.
List `{contract_version:1, cabinet:{state:"available"|"empty", revision, truncated:bool, supplements:[S]}}` (≤200, newest first).
Create body (closed): required `product_name`(1–150), `brand`(1–100), `category`, `is_public`(bool); optional `status`, `ratings`, `review_text`(≤2000), `price_paid`(0–100000). Response 201 `{contract_version:1, supplement:S, cabinet_revision}`.
Update body: non-empty subset of the same keys (`null` clears `ratings.*`/`review_text`/`price_paid`) → `{contract_version:1, supplement:S}`. Delete: no body → 204.

**Coach** — `{"message": string, "handoff"?: "nutrition-day"}`; response unchanged (LP-09).

## 7. State semantics

unknown ≠ 0 (null), failed read ≠ empty (typed 503), no active plan (`absent`) ≠ plan read failure (503) ≠ unreadable stored plan (`invalid`), zero water (`empty`, amount 0) ≠ water read failure (503), no supplement rows (`empty`) ≠ storage failure (503), no history (`state:"empty"`) ≠ history failure (503). A planned meal with any missing macro is shown but `loggable:false` (logging it would write invented zeroes).

## 8. Error taxonomy (ADR 0001 envelope `{error:{code,message,retryable,request_id}}`)

| Code | HTTP | retryable | Meaning / client action |
|---|---|---|---|
| `AUTH_SESSION_EXPIRED` etc. | 401 | false | existing auth |
| `AUTH_RATE_LIMITED` | 429 | true | default limiter (existing) |
| `NUTRITION_PRECONDITION_REQUIRED` | 428 | false | send If-Match / If-None-Match |
| `INVALID_NUTRITION_PRECONDITION` | 400 | false | malformed/weak/`*`/list/both headers |
| `INVALID_IDEMPOTENCY_KEY` | 400 | false | key required |
| `IDEMPOTENCY_CONFLICT` | 409 | false | key belongs to another command — new key |
| `NUTRITION_DAY_VIEW_UNAVAILABLE` | 503 | true | retry |
| `NUTRITION_PLAN_UNAVAILABLE` | 503 | true | read again before any action |
| `STALE_NUTRITION_PLAN` | 412 | false | refresh plan |
| `INVALID_NUTRITION_PLAN` | 400 | false | body/schema invalid |
| `INVALID_PLAN_PROPOSAL` | 400 | false | tampered/foreign proposal — regenerate |
| `PLAN_PROPOSAL_EXPIRED` | 400 | false | >24 h — regenerate |
| `INVALID_PLAN_GENERATION_REQUEST` | 400 | false | fix request |
| `NUTRITION_TARGET_REQUIRED` | 409 | false | send user to target setup |
| `NUTRITION_PLAN_RATE_LIMITED` | 429 | true | wait Retry-After |
| `NUTRITION_PLAN_QUOTA_EXCEEDED` | 402 | false | weekly allowance used |
| `NUTRITION_PLAN_GENERATION_BUSY` | 503 | true | AI gate full, Retry-After 15 |
| `NUTRITION_PLAN_GENERATION_FAILED` | 503 | true | provider/parse failure, allowance refunded |
| `PLANNED_MEAL_NOT_FOUND` | 404 | false | refresh plan |
| `PLANNED_MEAL_NOT_LOGGABLE` | 422 | false | meal lacks values |
| `INVALID_PLANNED_MEAL_COMMAND` | 400 | false | body must be empty or `{}` |
| `HYDRATION_UNAVAILABLE` | 503 | true | read again (write may have committed) |
| `STALE_HYDRATION` | 412 | false | read, compare desired |
| `INVALID_HYDRATION_COMMAND` | 400 | false | |
| `NUTRITION_HISTORY_UNAVAILABLE` | 503 | true | |
| `INVALID_HISTORY_CURSOR` / `INVALID_HISTORY_LIMIT` | 400 | false | restart from first page |
| `SUPPLEMENTS_UNAVAILABLE` | 503 | true | read again |
| `SUPPLEMENT_NOT_FOUND` | 404 | false | private not-found (unknown, forged, cross-user, deleted) |
| `STALE_SUPPLEMENT` / `STALE_SUPPLEMENT_CABINET` | 412 | false | refresh list |
| `INVALID_SUPPLEMENT_COMMAND` | 400 | false | |
| `SUPPLEMENT_CABINET_FULL` | 409 | false | 200-row native cap |
| `NUTRITION_TEMPORARILY_UNAVAILABLE` | 503 | true | planned-meal unexpected fault; retry with SAME key |
| `COACH_INVALID_REQUEST` | 400 | false | unknown marker or extra key |

## 9. Identity / revision strategy

All tokens: HMAC-SHA256 under a `SECRET_KEY` subkey with a per-class label (`axisai/nutrition-native/<class>/v1`), 144-bit base64url (24 chars), derived — never stored — and owner-bound. Classes: plan-revision (owner, row id, `plan_data`, score, `created_at`), planned-meal-id (owner, plan row id, slot), plan-proposal (signed payload `{d:digest,s:score,t:issued}`, owner bound through the MAC only), hydration-revision (owner, server day, count), supplement-id (owner, row id), supplement-revision (owner, row id, every mutable column, `created_at`), cabinet-revision (owner, sorted id set), history-cursor (signed `{before:day}`). None reuses the diary entry label (test `test_token_classes_are_domain_separated`). No sequential id crosses the wire.

## 10. Idempotency strategy

Only the planned-meal log needs a key: it is the one write whose retry could create a second consumed record. Key = existing `MealLog(user_id, idempotency_key)` unique constraint; fingerprint = sha256 of `{domain:"axisai/nutrition-native/planned-meal-log/v1", plan_revision, planned_meal}` stored in `idempotency_fingerprint`. Same key + same fingerprint → original (200); anything else → 409; a LogFood key cannot be replayed as a planned meal (different domain). Hydration (absolute) and plan/supplement writes are made retry-safe by preconditions instead: a resend after a committed write meets a changed revision (412) — no schema change needed. Supplement create uses the **cabinet revision** as its create precondition; every committed create changes the id set (PostgreSQL ids are never reused), so a duplicate tap / blind resend / second device is refused instead of creating a second row. (A durable create `Idempotency-Key` would need a new `supplement` column — not required, so not added.) Exactly-once is claimed only where the database proves it (unique key, row/owner locks).

## 11. Ambiguous-write matrix

| Write | A reject pre-commit | B ok | C commit ok, response lost | D duplicate | E same key/same cmd | F same key/diff cmd | G stale | H two devices |
|---|---|---|---|---|---|---|---|---|
| Plan save | 4xx, old plan canonical | 200 + plan | GET plan; if it shows your proposal → done; never resend blindly (resend → 412) | 412 | n/a | n/a | 412, refresh | one 200, other 412 (PG P1/P2) |
| Planned meal | 4xx, no row | 201 meal | resend SAME key → 200 same meal, or GET diary | same key → 200 replay | 200 replay | 409 | 412 / 404 | same key → one row (PG P3); different keys → two intentional servings |
| Hydration | 4xx, unchanged | 200 view | GET; compare amount with desired | 412 | n/a | n/a | 412 (incl. stale day) | one 200, other 412 (PG P4/P5) |
| Supplement create | 4xx | 201 + `cabinet_revision` | GET list; your item present → done | 412 | n/a | n/a | 412 | one 201, other 412 (PG P6) |
| Supplement update | 4xx | 200 | GET list; compare | 412 | n/a | n/a | 412 | one 200, other 412 (PG P7) |
| Supplement delete | 4xx | 204 | resend → 404 = converged (gone); or GET | 404 | n/a | n/a | 412 | one 204, other 412/404 |
| 503 on any write | — | — | treat as C: read, never auto-resend a different command | | | | | |

## 12. Plan replacement contract

Order (AST-pinned for the browser route and the shared boundary; behaviourally pinned for native by N7-07): proposal token verified (owner, signature, ≤24 h) → score from token through `parse_plan_score` → strict native→canonical parse + `validate_nutrition_plan_for_save` → digest must equal the token's (no calorie, meal, item, name or score from the client is trusted) → `replace_nutrition_plan`: owner `user` row `FOR UPDATE` → newest plan row `FOR UPDATE` → precondition check → delete + insert → commit. Any 4xx leaves the old plan canonical. Generation never writes a plan (`test_generation_returns_proposals_and_saves_nothing`). The browser save calls the same boundary with `UNCONDITIONAL` (unchanged behaviour; the PR5 cross-tab residual is NOT copied to native).

## 13. Hydration contract

Server Istanbul day only. Absolute `amount` 0–8 glasses (the bound the web route clamps to; native refuses instead). Unit `glass` on the wire. Missing row = confirmed 0. Write locks owner row then day row, checks revision, writes, commits, then runs the shared once-per-day `water_logged` funnel.

## 14. History paging contract

Whole Istanbul days, newest first; default 7, max 14 days; cursor = signed "before <day>" for this owner; malformed/forged/foreign cursor → 400; `next_cursor` null at the end; meals per day in diary order with the diary projection (today identical to `/diary/today`). Two SELECTs per page; correction stays current-day-only (published revisions for past days are rejected by the diary mutation day check).

## 15. Supplement contract

See §6, §9, §10. Browser and native share `supplement_cabinet` (first-supplement 25 XP bonus under the owner lock, activity line, `supplement_added` quest). Stored English labels map to stable tokens; out-of-vocabulary stored values surface as `unknown`.

## 16. Planned-meal contract

Identity = (plan revision via `If-Match`, planned-meal id from (plan row, slot)). Display name/slot/calories are never identity (`test_identity_is_not_the_display_name`). Values = the plan's own macros rounded to 0.1 through `clamp_serving_macros`; `source = "ai_plan"`; description = items joined by `", "`. Planned → Logged only after the 201/200 answer (or a diary read showing the entry). The legacy web quick-add user-wide replay is NOT the native contract (web route unchanged).

## 17. Menu / builder decision

**Menu analysis (M): DEFERRED — OPTIONAL PARITY.** Fetch, SSRF, scan, extraction and AI gating live inside ~470 lines of `app/blueprints/menu.py` route bodies; a safe native adapter requires extracting that network-security code into a transport-neutral service — a materially independent refactor of the SSRF boundary. PR8's scope (contract §PR8: Plan selector + Nutrition Today/Plan) does not require it; PR9 owns log-method parity. Guard: any `/api/v1/nutrition/menu*` route fails the exact inventories (N7-15/16).
**Custom/build meal (N): DEFERRED — OPTIONAL PARITY.** The builder is staging (`CustomMeal`/`CustomMealItem`) addressed by sequential ids across six routes; a native contract needs opaque meal+item identities, item revisions and a commit precondition — a staging redesign. Native search/barcode/manual LogFood already cover consumed-food logging for PR8. No second builder was created.

## 18. Native Coach handoff

`POST /api/v1/coach/messages` accepts `{"message"}` (unchanged) or `{"message","handoff":"nutrition-day"}`. Any other key or marker value (including `progress-insight`, case/space variants, lists, objects, null, any nutrition fact) → 400 `COACH_INVALID_REQUEST`, nothing spent. At send the server calls `coach_handoff_context("nutrition-day", g.mobile_user.id)` → fresh PR6 day view; intake unreadable → no context. Context is additive model context, never stored as user speech. Same gates, one model call, shared `chat` quota, same failure semantics. No auto-send; reads never call the model.

## 19. Query / performance characterization (domain SELECTs, excluding the auth principal lookup)

day-view 4 (one per section, each in a savepoint) · plan read 1 · hydration read 1 · supplement list 1 (+1 id read only when >200 rows) · history page 2 · planned-meal log: key lookup 1 + owner lock + plan row lock + insert · hydration write: owner lock + row lock + upsert. No provider/AI call on any read; generation = exactly one heavy call (`feature="nutrition_plan"`). No polling, no N+1.

## 20. PostgreSQL races

`tests/test_nutrition_vnext_pr7_pg.py` (registered in the existing `mobile-pg-concurrency` CI job): P1 plan replace from one revision, P2 plan create, P3 same-key planned meal ×3, P4 hydration from one state, P5 first water write of the day, P6 supplement create from one cabinet revision, P7 supplement update. Locally on PostgreSQL 16.15: 7/7 pass; with the replacement check or cabinet revision neutralized, P1/P2/P6 fail.

## 21. Privacy / security

Owner only from the Bearer principal; cookie session never authenticates native routes (N7-19). Closed key sets on every body; authority-bearing fields (`user_id`, `plan_id`, `score`, `revision`, client calories) refused. Logs: `mobile_nutrition event=<name> error_type=<class> request_id=<id>` only — no meal, menu, plan, supplement, token or message text. Unknown/forged/cross-user tokens resolve by scanning the owner's own rows only (no existence/timing oracle across accounts). Generation custom foods bounded (10 × 60 chars, no control characters); output re-validated by the canonical schema. Returned strings are JSON data; free text is validated for control characters.

## 22. Backward compatibility

Existing native routes/payloads unchanged (guards N7-21/22). Web routes unchanged in behaviour: plan save/generation, water and supplement add/edit/delete now call the shared services with identical semantics (PR5, hydration, supplement and i18n suites green). Coach body stays compatible (message-only still valid). `/api/v1` only; no v2.

## 23. Non-vacuity evidence

`tests/test_nutrition_vnext_pr7_non_vacuity.py` plants each defect into shipped code and asserts the real guard fails: N7-01 reimplemented day view · 02 missing target → 0 · 03 hydration failure → 0 · 04 plan failure → absent · 05 stale revision ignored · 06 generation auto-saves · 07 delete before validation · 08 supplement token not owner-bound · 09 stale update ignored · 10 unbounded history · 11 raw MealLog id · 12 name-based planned-meal id · 13 key accepts different command · 14 stale plan still logs · 15/16 native menu adapter appears · 17 client facts reach Coach · 18 unknown marker reaches model · 19 cookie auth · 20 route growth past exact gate · 21 LogFood key regression · 22 diary If-Match regression · 23 web save inherits native precondition · 24 cross-user token resolves · 25 failed write reported as success. A control run with every defect removed passes all 25.

## 24. PR8 IMPLEMENTATION MAP

| Repository method | HTTP | DTO | Domain state | Precondition | Retry rule | Refresh after write |
|---|---|---|---|---|---|---|
| `NutritionDayRepository.read()` | GET `/nutrition/day-view` | day view | section states | — | 503 → retry | — |
| `NutritionPlanRepository.read()` | GET `/nutrition/plan` | P + options | active/absent/invalid | — | 503 → retry | — |
| `.generate(choice)` | POST `/nutrition/plan/generate` | proposals | proposal (never saved) | — | 503/429 retry after delay; 402/409/400 never | none (nothing saved) |
| `.save(proposal, current)` | PUT `/nutrition/plan` | P | active | `If-Match` revision, or `If-None-Match: *` when absent | never auto; 412 → re-read; 503/timeout → re-read and compare name/meals | use response; GET on ambiguity |
| `.logPlannedMeal(meal, plan)` | POST `/nutrition/plan/meals/{id}/log` | M | Planned→Logged on 201/200 | `If-Match` plan revision + new key per intent | on timeout/503 resend SAME key; 409 never | GET diary + day view |
| `HydrationRepository.read()` | GET `/nutrition/hydration` | H | available/empty/invalid | — | 503 retry | — |
| `.set(amount, current)` | PUT `/nutrition/hydration` | H | confirmed only on 200 | `If-Match` revision | never auto; 412/503/timeout → GET, compare | response |
| `HistoryRepository.page(cursor)` | GET `/nutrition/history` | page | available/empty | — | 503 retry; 400 cursor → restart | — |
| `SupplementRepository.list()` | GET `/nutrition/supplements` | cabinet | available/empty | — | 503 retry | — |
| `.create(fields, cabinetRev)` | POST `/nutrition/supplements` | S + cabinet_revision | created on 201 | `If-Match` cabinet revision | never auto; 412/503/timeout → GET list | use returned `cabinet_revision` |
| `.update(id, fields, rev)` | PATCH `/nutrition/supplements/{id}` | S | | `If-Match` item revision | never auto; 412 → GET | response |
| `.delete(id, rev)` | DELETE `/nutrition/supplements/{id}` | — | | `If-Match` item revision | 404 after a lost 204 = gone | GET list |
| `CoachRepository.send(message, handoff?)` | POST `/coach/messages` | LP-09 | | — | LP-09 rules (`COACH_REPLY_INCOMPLETE` never retry) | — |

Native-auth OFF: every repository is unavailable (no fixtures).
