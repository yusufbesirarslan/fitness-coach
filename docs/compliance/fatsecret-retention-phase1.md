# FatSecret 24-hour server retention, Phase 1

AXISAI-FATSECRET-RETENTION-MVP-PHASE1-01, plus founder disposition 02, 2026-11-09. This is source only. Nothing here is deployed, nothing has been run in production, and no data has been changed.

## Rights relied on (confirmed, in writing)

The source is a written clarification from FatSecret Platform API Support, 2026-11-09. In code it is recorded as `fatsecret_boundary.PROVIDER_PERSISTENCE_PERMISSION_REF`.

- Raw nutritional values for an individual food item may stay on our servers for at most 24 hours.
- Food IDs and recipe IDs may be stored indefinitely.
- Aggregated or calculated totals may be stored indefinitely, including daily calorie totals and meal summaries.
- Historic values may be stored on the user's device indefinitely. This phase does not use that permission, because the iOS app has no local nutrition store.
- FatSecret data may be used in real time for AI-assisted coaching. This phase does not change the #497 boundary.

**Not inferred:** that provider food names or serving descriptions may be kept indefinitely. They are treated as 24-hour content.

## Design

**Writers.** Two writer paths stamp `meal_log.provider_items` (references in display order: `{provider, food_id, serving_id, quantity}` or `{text}`) and `provider_acquired_at` (when FatSecret answered):
- native and web provider-backed LogFood (`mobile_log_food.log_food`);
- the diary builder (`_apply_provider_snapshot`, `diary_log_meal`).

These are the only paths the hold now opens. Web barcode add and the social suggestion total are still held by `UNCOVERED_PROVIDER_PERSISTENCE_REF = None`.

**Expiry pass.** `provider_retention.expire_due` runs once a row reaches `EXPIRE_AFTER = 22h`, in one transaction per batch, claiming rows `FOR UPDATE SKIP LOCKED`:
- A **per-item** entry — one with at most ONE provider component, including one provider food plus the user's own items (whose stored values would otherwise reveal the provider item by subtraction) — has its four macros added to `meal_log_retained_total` (owner × Istanbul day) and set to NULL on the row. The row keeps its slot, time, quantity and IDs. No new `meal_log` row is created, so entry counts and `meal_count` don't change.
- A diary entry with **two or more provider foods** is a meal summary, so its macros stay. Its published description is the provider-free label: a summary keeps its total, so Repeat stays available on it, and a live provider name must never be copyable into a new row.
- In both cases provider names and serving text leave `yemekler`. Text the user wrote, or text from an independent source, stays.
- **Saved-meal items** (`custom_meal_item`): the provider's nutrients, per-100 g values, name and serving text are scrubbed. The user's quantity, the grams and the IDs stay. Logging that saved meal requests every provider item again live, and refuses with `503 food_provider_unavailable` if the provider is down.

**Totals.** Every reader of a day total adds the retained aggregate exactly once:
- day-view, Plan facts and Coach today totals use a scalar subquery inside their existing SELECT;
- native history carries it in its day-key SELECT, with a correlated subquery;
- native diary today, web today/history, web review and barcode context add it in Python.

So totals are the same before and after expiry, with no double count.

**When it runs.** The worker's timer thread (`worker._retention_tick`) enqueues the pass every 300 s as an ordinary RQ job. It runs with zero user traffic and opens no database connection in the worker's parent process. Daily maintenance also runs the pass. Routine worst case: 22 h, plus one interval, plus the 240 s job timeout, so under 23 h. The 2 h margin absorbs queue latency and a worker restart.

**Fail-closed reads.** `provider_retention.is_due` and `published` apply to every reader. A row past 22 h, or a legacy `search`/`barcode` row, never publishes its per-item macros, even before a pass has run. The day total still counts the stored value.

**Repeat.** The shipped iOS Repeat (`NutritionHistoryMeal.repeatCommand`) needs all four macros, so a withheld entry shows "Repeat unavailable". An older client could still send an exact copy (same owner, description and four macros) of a provider row whose values are still stored. That copy inherits the original components and acquisition time, so it gets no new deadline. A copy of a legacy row is due immediately.

**Names after expiry.** Readers of past days (native and web history) request the name live by stored `food_id` through `LiveNames`:
- the cache key is `fatsecret:display:v1:<food_id>`, holding names only;
- it is written with `SET NX EX = 22h`, so a cache hit never extends the lifetime;
- at most 8 live lookups per request, and lookups stop after the first failure;
- the fallback is `FatSecret (<qty>x)`.

Legacy rows have no ID, so they show the placeholder `FatSecret` (D2).

**Deleting a summarized entry.** This is only reachable for an item logged just after midnight, on its own day. Its value is now part of the day total, so the delete is refused rather than corrupting that total: native `409 DIARY_ENTRY_SUMMARIZED`, web `409 route.meal_delete_summarized`.

**Web `/meal-log/review`.** It now follows the #497 seam, `fatsecret_boundary.intake_unproven` (founder D3), and makes no model call while intake is unproven. Production D1 keeps the route closed anyway (`AI_CONSENT_DISCLOSURE_UNAVAILABLE`).

**Caches.**
- `food:macros:v2:*` and the in-process L1 never held FatSecret values on main. Their only writers are the built-in table (`_food_search_static`) and the LLM estimate; an AST gate pins this in `tests/test_fatsecret_retention_phase1.py`.
- The name → `food_id` map is keyed by a digest, so it holds no provider text.
- Production `e7bfa139` still writes FatSecret per-100 g values to `food:macros:v1:*`. That is handled by the procedure below.

## Legacy (founder D4/D5): dry run only

```
flask --app starter fatsecret-retention-legacy-dry-run --expect-meal-log 3 --expect-items 26
```

This reproduces the DB4 predicates exactly:
- `meal_log`: `source IN (search, barcode)` with no provenance;
- `custom_meal_item`: a non-empty `fatsecret_food_id` with no provenance.

It prints aggregates only: rows, user-day buckets, macro sums to move, day totals before == after, placeholder-only rows, and untouched counts. It mutates nothing and has no apply mode. It exits 0 with `MATCH` only when the counts are exactly 3 and 26 and totals are conserved; otherwise `HOLD`, exit 2.

These records are never rewritten: the 54 `manual` rows, the 9 `diary` rows, and saved-meal items without an ID. That is a temporary non-destructive disposition, **not proof of retention rights**. The exposure stays open and recorded.

Until a founder-approved apply exists, the 3 legacy `search` rows keep their stored values. Readers already withhold them.

## Redis v1 (founder D6): post-deploy, gated

```
flask --app starter fatsecret-retention-redis-v1                     # count only
flask --app starter fatsecret-retention-redis-v1 --apply --expect N  # separately founder-gated
```

This applies to exactly `food:macros:v1:*`. The apply step refuses (`HOLD`, exit 2) unless a fresh scan still finds N keys. `v2` and `food:independent-menu:v1` are not provider data and are never touched.

## Launch fallback closure (AXISAI-FATSECRET-LAUNCH-FALLBACK-IMPLEMENTATION)

- The idempotency key is owner-scoped, not endpoint-scoped (`meal_idempotency.find_existing`), so a web write route's replay answer can return ANY of the owner's rows, including a provider row. Four web replay answers echoed the stored macros: `POST /meal-log` manual-path replay, `POST /meal-log` provider-path answer, `POST /api/quick-add-meal` replay, and the deprecated `POST /api/food/barcode/add` replay. The barcode replay runs BEFORE the D-B2 hold, so it answered even while barcode logging is held, and it coerced a scrubbed value to `0`. All four now answer through `provider_retention.published_nutrients`. A row that is due by its provenance clock (22h, even before the expiry pass runs, or while the worker is down) and every legacy `search`/`barcode` row publish `null`, never the stored number and never `0`. Fresh rows and independent rows (`manual`, `ai_plan`, legacy NULL source) are unchanged. A replay never deletes or rewrites a stored row and makes no provider lookup. Mobile `log_food` and planned-meal replays were already fingerprint-guarded and read through `published()`. Tests: `tests/test_fatsecret_replay_retention.py`.
- Which tests guard what. On main 7ba356e7 the first version of the replay file had 7 failing tests: due-row × manual/provider/quick_add and legacy × manual/quick_add × search/barcode. The barcode-add cases were added after independent review. Seven tests fail on 8b60eede: barcode-add due, legacy × search/barcode, after-pass (it got `0`), the barcode-add contract test, the strengthened `test_web_barcode_add_is_held_but_existing_replay_answers`, and `test_barcode_add_idempotency_writes_one_row`. That last test runs with D-B2 lifted. Its writer stamps no provenance, so the row is a legacy `barcode` row to every reader, and its replay is now withheld instead of repeating the first answer. Thirteen of the replay/hold tests fail on main. These are the #508 regressions. The fresh, independent-row, provider-path legacy (409), cross-owner and after-pass tests on the three original routes pass on main too. They pin preserved behaviour.
- The last #493 strict xfail (`test_unbounded_provider_snapshot_is_not_replayed_after_24h`) aged only `created_at` and read raw storage, which correctly holds values until the pass. It is replaced by `test_provider_snapshot_is_not_replayed_after_24h`, which ages the provenance clock. It pins the #499 native path (passes on main): the replay makes no provider call, returns the same record, publishes no values before the pass, nulls storage after the pass, and keeps the day total. No strict FatSecret xfail remains.

## Known residuals (independent review, all P3 or out of scope)

- **Unrouted MCP readers** (`fitx_mcp/server.py`, `app/services/coach_context_queries.py`): raw-SQL readers on the stdio MCP server only. No app route imports them and they are not deployed (unchanged from #497). They neither withhold due values nor add the retained aggregate. Keep them unrouted; converge them before any MCP exposure.
- **A copy of a legacy row** (from an older client) is due immediately, so deleting it returns the defined 409.
- **Web `/meal-log` manual `override_macros`** has no exact-copy backstop. The web app has no Repeat; typed values are the user's own.
- **A scrubbed saved-meal item** shows 0 kcal in `/api/diary/today` only during its own day's edge window. Logging it re-requests live.
- **The history revision** of a withheld row may include a live name. History is read-only; mutations use the diary/today revision, which is name-free.

## Boundaries that need a separate operational decision

- **RDS automated backups, snapshots and the DR copy** keep pre-expiry values for their own retention window. This phase does not change them.
- **Worker liveness.** If the worker is down for longer than about 1 h 50 m, stored values outlive 24 h. Readers still withhold them, and the pass logs `[RETENTION] event=provider_values_overdue`. Worker health is already reported by `/health?deep=1`.
- **`CoachMessage` text and the one pre-v2 Coach summary** may contain derived numbers. They remain quarantined from AI reuse by #497.
