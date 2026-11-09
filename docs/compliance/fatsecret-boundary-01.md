# AXISAI-LAUNCH-FATSECRET-BOUNDARY-01

Qualification date: 2026-11-08. Base main: `1de8e7cf4d53416bc2ee956266913c112bb5ee5f` (merged #491). Refreshed origin/main; inspected merged #487 and draft #485. This isolated candidate adds evidence and synthetic probes only. **Affected launch flows are NOT QUALIFIED. No runtime exclusion or retention repair is implemented.**

The founder confirmed during this qualification that FatSecret has not replied. Two connected Gmail searches returned no correspondence; no account-specific agreement could be inspected. Earlier #485 evidence records a founder-reported Premier Free approval, US dataset and attribution, but that report is not a primary provider permission document. No pending request is treated as authorization. D1 consent grants no FatSecret licence.

## Documentary basis

Official pages fetched again on 2026-11-08:

- [Terms](https://platform.fatsecret.com/terms), §§1.3, 1.5–1.7: credit wherever Content appears; retain links; link/incorporate terms; replace or remove non-exempt Content within 24 hours unless FatSecret specifies otherwise. Advice/guidance/diagnosis using the API is prohibited under §1.7(iii).
- [Storable data](https://platform.fatsecret.com/docs/guides/storable-data): listed identifiers include food/serving IDs; nutrient snapshots, names and serving descriptions are not listed as indefinitely storable.
- [Editions](https://platform.fatsecret.com/api-editions): Premier Free offers US/English data and barcode capabilities with attribution, and advertises custom storage permissions. It supplies no account-specific retention period or AI exception.
- [Attribution](https://platform.fatsecret.com/attribution): Content surfaces, a prelogin placement, website and store description require credit under the applicable attributed plan.

These are source-based technical classifications, subject to any actual applicable account agreement. An operational key, entitlement, numeric transformation, disclaimer or user consent does not establish another right.

## Separate behavior classifications

| Behavior | Classification | Launch application |
|---|---|---|
| 1. Food search | CONDITIONALLY_PERMITTED | Attributed factual lookup in the entitled dataset; account approval remains reported rather than inspected. Preserve existing API. |
| 2. Barcode lookup | CONDITIONALLY_PERMITTED | Premier Free supports the capability, subject to account entitlement. Account barcode scope is NOT_VERIFIED; current token request still uses `basic`. No live entitlement call authorized. |
| 3. Basic metadata display | CONDITIONALLY_PERMITTED | Factual display with attribution and compliant refresh/retention. Historic retained names are a separate issue. |
| 4. Calories/macros display | CONDITIONALLY_PERMITTED | Factual values; no implied advice right. Numeric target comparison remains an unresolved interpretation, not cleared here. |
| 5. Diary persistence | NOT_VERIFIED for existing indefinite snapshots | IDs have a published storage basis; current long-term nutrient/name copies exceed the published default absent a specific exception. Initial lookup does not authorize the lifetime of a log. |
| 6. Redis/database caching | CONDITIONALLY_PERMITTED within applicable limits | Indefinite non-exempt copies are PROHIBITED under the default; a longer account exception is NOT_VERIFIED. Old rows/keys remain at rest. |
| 7. Custom meals/saved snapshots | NOT_VERIFIED for current long-term content | Provider IDs do not license the copied name, description, raw or scaled macros. Manual independent content is separate. |
| 8. Nutrition aggregates | CONDITIONALLY_PERMITTED for independent inputs | Provider-based advisory aggregates are PROHIBITED under the default advice restriction; long-term derived retention is NOT_VERIFIED. Summing does not establish independent origin. |
| 9. AI Coach context | PROHIBITED for provider-based advice under default terms | Separate nonadvisory AI onward transfer is NOT_VERIFIED. Pending reply grants neither. Unknown and mixed historical facts cannot be cleared for this pathway. |
| 10. QR scoring/recommendations | PROHIBITED when provider-derived inputs inform advice | Applies both to item macros and the consumed-intake budget, including deterministic scoring. Independent fresh estimates remain a separate existing path. |
| 11. Derived outputs | CONDITIONALLY_PERMITTED for factual serving arithmetic | Provider-based advice is PROHIBITED; broader storage/onward use is NOT_VERIFIED. Scaling, copying and summaries preserve restrictions. |
| 12. Attribution | PERMITTED and required | Mobile #173 owns UI; content lineage must remain available to it. Credit alone clears no other restriction. |

Manual logging, independent user facts and genuinely independent nutrition functions have no FatSecret dependency. Their preservation is mandatory. `source=manual` in existing storage is insufficient to establish that independence.

## Actual source flow at the base

| Boundary | Current path and lost information | Qualification |
|---|---|---|
| Discovery | `mobile_food_discovery.search/servings/barcode`, `fatsecret._food_search_fatsecret`, `barcode.build_lookup_response` normalize names, servings and nutrients. Mobile discovery projects provider identity. | Lookup works with stubs; no real entitlement tested. Web barcode remains facts-only since #487. |
| Serving selection | `provider_food_snapshot.resolve_provider_food` returns provider, food/serving IDs, quantity and scaled nutrients. | Server resolution protects truth, not licensing. |
| Canonical persistence | `mobile_log_food.service.log_food` discards the snapshot's provider/IDs and retains only `discovery_source` (`search`/`barcode`) plus name/macros in `MealLog`. Web barcode and diary commits also flatten snapshots. | No acquired-at, refresh deadline or provider permission chain; idempotent replay returns stale rows before consulting provider. |
| Custom meals | `nutrition/diary.py::_apply_provider_snapshot` retains IDs plus names, serving descriptions and per-100g/scaled macros in `CustomMealItem`; logging totals becomes `MealLog(source=diary)`. | References survive in the builder but are lost at aggregate commit. Refresh at log time does not clear old snapshots still retained. |
| Copied/manual labels | Native manual commands record `manual`. Web `/meal-log` defaults to `manual` for explicit numbers and AI-derived totals. `static/coach_widget.js::addToLog` posts selected menu macros through that same override front door without provider identity. | Manual, copied and estimated origins cannot be distinguished from the ledger label. Historical NULLs are possible too. |
| Shared macros | `foodcache` L1 and Redis use `per_100g`/`per_serving`, `food:macros:v2:*`. #487 avoids FatSecret writes in search, social and menu; v1 is not read. Menu caches estimates, static search caches table values. | Passing v1/basis isolation tests are bounded evidence. V2 carries no provenance; never certify an arbitrary v2 value by its namespace. Static table licensing/authorship is not established here. Redis default TTL is three days; L1 has no age expiry. |
| Other caches | `BarcodeFoodCache` old payloads remain; web lookup now uses live truth, native may use an existing ID. `_food_id_cache` holds name→ID with a size bound and no age expiry. Menu scan/extraction caches retain extracted menu text, separate from macro cache. | No new barcode payload rows, but old payload retention remains open. Provider-derived names used as ID-map keys are still Content; an allowed ID value does not exempt its name key. |
| Coach sums/tools | `ai_coach._today_nutrition_totals`, `_remaining_macros_for_user`, `_tool_query_fitx_metrics(calories)` sum all ledger rows; `coach_privacy.tool_result` permits numeric calories/totals without licence lineage. | Provider and unknown numbers reach the tool projection. Direct lookup inside a D1 operation is already suppressed, which does not repair historical inputs. |
| Coach context/history | `context_builder.fetch_coach_context` → `coach_handoff_context(nutrition-day)` → `nutrition_day_view` includes all logged totals. PendingAction, conversation messages/summaries and signed memory can retain previously derived facts. | Ownership signatures are not provider licences. Both model message loops need exclusions before tool/context/history insertion and before summarization/continuation. Never rely on a prompt asking the model to ignore facts. |
| QR aggregate | `menu_analysis._analyze_menu_text` sums all current-day MealLog rows → `remaining_macro_budget` → `_menu_score` and `nutrition_pipeline.score_compatibility` → sort/top-three picks, proofs and confirmations. | Even independent item estimates receive an unproven consumed budget. Stubbed scorer probes reproduce this inside a D1 operation with no FatSecret lookup. |
| Other advisory consumers | Web `/meal-log/today` exposes targets/remaining; dashboard protein nudges read ledger totals. `fitx_mcp` has a separate FatSecret ranking path but no deployed launcher is established by this trace. | Numeric comparison/nudge interpretation remains unresolved. Do not run the MCP path with real credentials as a workaround. |

This is source execution qualification, not a staging/production database or runtime inspection. No production config, old Redis TTL or actual historical population was queried.

## Minimum reliable boundary and founder stop point

The already merged isolation remains useful. The next required boundary spans shared writers and consumers; a FatSecret service-only filter cannot close it. Unknown/mixed/copied/scaled/cached/aggregated origins must be excluded from restricted consumers before arithmetic that informs advice, prompt insertion, scoring, proof creation or memory reuse. New provenance must be server-attested and propagated; never backfill trust from `manual`, an ID hash, missing provider text or an ownership signature. Preserve all historical data; no bulk migration or purge is proposed.

**Two independent decisions are required. Founder approval changes scope, never provider rights:**

1. **Retention:** obtain documentary permission covering existing and future diary/custom-meal snapshots, old caches, pending actions and conversation copies, or approve preparation of a concrete refresh/reference-only design and its exact history/display impact. Existing rows lack the IDs needed to refresh faithfully. Refreshing to today's numbers can change historical tracking; hiding/blanking/deleting rows would regress history. Neither is implemented or authorized. Blocking new writes alone leaves old retention unresolved.
2. **Advice:** await explicit permission for the described flows, or explicitly approve a bounded fallback: retain factual search/barcode, independent manual tracking and independent Coach functions; withhold logged-intake Coach review/tool totals/remaining-budget personalization and budget-dependent QR scores/reasons/ranking/picks whenever relevant intake or item origin is unproven. Exclude tainted histories, summaries, pending actions and confirmations too. Independent menu browsing/estimates remain only with independently authorized inputs and no tainted cache. This materially reduces nutrition personalization on affected days, including apparently manual legacy days. A partial intake total must not be reported as complete or replaced by zero.

The user explicitly required stopping before a material regression. Consequently **no runtime fallback, global nutrition shutdown, lookup-off flag or persistence denial is chosen in this candidate**. Current nutrition functions remain source-identical and usable, but cannot be declared launch-cleared. If retention cannot be resolved while preserving history, the exact founder choice is written retention permission versus an explicitly reviewed history-impacting remedy/affected release hold. Merely disabling new FatSecret lookups does not clear historical data.

## Shared-owner handoff (requirements, not an implemented API)

Backend integration owner controls shared release files. Proposed seam for owner review:

- Carry server-only provider identity, food/serving references, transformation/input lineage, acquisition/refresh metadata and documentary permission reference from resolution through every writer, copy, edit, replay, custom meal, pending action, menu proof and memory. Do not accept client assertions as authority. Unknown remains unknown; no migration that declares old rows safe.
- At advisory reads, return an explicit unavailable/incomplete state when any relevant dependency is unqualified. No numeric tainted or partial budget. Preserve the factual ledger contract separately. Exact wire representation requires backend/mobile agreement; do not silently omit required API fields or change existing statuses.
- Apply the seam to both Coach provider loops, metric tools, ordinary context and nutrition-day handoff, tool staging/commit responses, history/summaries/continuations, both QR scorers and ranking/proofs. Review any direct MCP entry point before activation.
- Keep source-aware, basis-aware cache envelopes and read/write namespaces. Reject unverified/legacy envelopes for restricted consumers; future source labels alone must not qualify cached outputs whose inputs were tainted. Resolve provider-derived name-key retention separately. A cache miss is preferable to relabeling an unknown hit.
- Prepare a separate narrow shared-file patch only after the above founder decisions; keep F01/D1 consent, eligibility, allowances, Apple and deployment files outside this candidate.

Mobile owner (#173): preserve attribution on search, barcode, serving selection and every provider-content display, including retained records where applicable. Keep prelogin/site/store credits and terms links. Discovery labels alone cannot identify provider content in old diary rows. Request a backend-provided attribution/provenance projection; do not infer independent permission from `manual`. If the fallback is approved, agree explicit unavailable-advice states before changing review/QR UI. No mobile source edit is made here.

## Synthetic verification

`tests/test_fatsecret_launch_qualification.py` adds negative probes for provider/unknown history at Coach tool/context and QR scorer boundaries, a raw current-v2 Redis value at the QR scorer, and a stale persisted provider snapshot replay. Search/barcode/manual/NULL labels are tested independently; even labels that appear manual are deliberately seeded as unproven. Strict expected failures document OPEN release blockers and are never a compliance PASS. A positive control preserves manual write/history without a provider; another retains the existing fresh-estimate menu path on an empty diary.

Existing `test_fatsecret_terms_boundary.py` and `test_nutrition_readiness_g2_g3.py` cover preserved search/barcode, factual-only responses, no new barcode cache rows, no provider macro writes, v1 cache rejection, basis isolation and provider-unavailable persistence refusal. Those passing negatives establish only those restrictions. They do not establish general rejection of unauthorized persistence or advice.

No live FatSecret calls, AWS work, destructive operations, production changes or source merge. Hosted CI can validate a candidate's tests but cannot supply the missing licence or clear expected-failure probes.

Local results (Python 3.11, synthetic SQLite/stubs): focused FatSecret/search/barcode/cache qualification **90 passed, 15 strict expected failures** (14 new probes plus the existing diary-to-Coach blocker). Adjacent barcode, native discovery/logging/history, Coach tools/handoff, menu and nutrition regression modules: **558 passed**. Running the new module with expected-failure handling disabled exposes the missing boundaries as ordinary failures; it is not a passing exclusion suite. Hosted exact-head results belong in the PR/qualification receipt, so this source document does not claim them in advance.
