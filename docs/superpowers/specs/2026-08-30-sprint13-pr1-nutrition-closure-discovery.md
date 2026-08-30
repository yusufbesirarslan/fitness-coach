# Sprint 13 PR1 — Nutrition Closure: Discovery & Architecture Decision

**Type:** discovery / characterization / architecture decision. No production behaviour changes.
**Date:** 2026-08-30
**Branch:** `sprint13-pr1-nutrition-closure-discovery`
**Base:** `origin/main` = `a44f31effb4ac23a020bcef322765dfe620c88f9`

---

## 1. Executive verdict

AxisAI already has **one** canonical consumed-food ledger — `MealLog` — and every
production writer converges on it. The mobile contract published in Sprint 9
(`/api/v1/nutrition/*`) is a correct adapter over that ledger, and the Flutter
client on `axisai-mobile` `main` **fully consumes it**, including opaque entry
ids, opaque revisions, `If-Match`, slot moves, hard delete, and canonical
re-read after `412` / `404` / ambiguous transport outcomes. Sprint 9's mobile
deferrals are therefore **closed by shipped code**, not by documentation.

What is *not* closed is the **web** side of the same ledger, and a set of
**derived** nutrition figures that have no single owner:

* the web has **no correction path at all** for a committed ledger row — no
  delete, no slot change, no edit — while mobile has both supported mutations;
* **five** independent macro-target derivations exist, two of which disagree
  numerically for every non-bulk user, and two of which fabricate a `2000 kcal`
  goal for a user who never configured one;
* the main web search-and-log path posts **per-100 g** figures as a meal total,
  so quantity is silently fixed at 100 g;
* the web `/meal-log` `override_macros` branch persists **client-computed**
  nutrition (bounded only by a physical-plausibility clamp) into the same ledger
  the mobile path recomputes from provider truth.

There is also one **historical data-integrity defect**: migration `df0d08c0cd24`
backfilled old AI-Coach meals into the ledger with a `DD.MM` day key, a format
no `tarih`-keyed query can ever match.

**Nutrition core closure does not need a provenance column, a history endpoint,
a mobile menu adapter, or a nutrition intelligence domain.** It needs authority
convergence on the derived figures, a web correction path, one conditional
day-key repair, and the removal of orphaned/unsafe sibling writers. That is five
small, independently reviewable PRs.

**No P0 was found.** No writer fabricates persisted state, no double-count path
exists, no cross-user read or write was found, and the day boundary is
server-owned everywhere it matters.

### Findings index

| ID | Sev | One line |
|---|---|---|
| F13 | P1 | Migration `df0d08c0cd24` wrote `tarih` as `DD.MM`; any row it created is unreachable by every day-keyed query |
| F1 | P1 | Web cannot delete, move, or edit a committed `MealLog` row — no correction path |
| F2 | P1 | Five macro-target derivations; `barcode._target_macros` disagrees with coach/menu for every non-bulk goal |
| F3 | P1 | `barcode._target_macros` and `/meal-log/review` fabricate a `2000 kcal` target when none is configured |
| F4 | P1 | Web multi-food quick log posts **per-100 g** values as the meal total (quantity fixed at 100 g, never chosen) |
| F5 | P1 | `/meal-log` `override_macros` persists client-computed macros; the mobile path recomputes from the provider. Same ledger, two trust models |
| F6 | P2 | `POST /api/food/barcode/add` accepts a caller-supplied `food` object and has **no** frontend consumer |
| F7 | P2 | `/meal-log` does not validate `ogun`: free text reaches `MealLog.ogun` (`String(100)`) → `unknown` slot on the wire, `DataError` above 100 chars |
| F8 | P2 | Social meal-suggestion writer sets no `source` (reads back as `manual`) and uses no idempotency key |
| F9 | P2 | `/api/progress/nutrition` and `/api/progress/insights` are orphaned; the latter contains an unowned calorie-adherence heuristic |
| F10 | P2 | `static/nutrition.js` computes a 0–100 nutrition score and an A–D letter grade in the browser, with no server owner and no mobile counterpart |
| F11 | P2 | `fitx_mcp.log_nutrition_entry` writes the ledger with `user_id` as a *tool parameter* and no idempotency (not deployed) |
| F12 | P2 | `diary_log_meal` writes the ledger without an idempotency key (safe today only because of the atomic `is_logged` claim) |

---

## 2. Baseline

### Repositories

| Repository | Role | Path | SHA | State |
|---|---|---|---|---|
| `fitness-coach` | Backend + web (primary) | `C:/Users/yusuf/fitness-coach` | `origin/main` `a44f31e` (2026-08-30, `fix(pump-check): restore gallery media delivery (#244)`) | developer tree clean at branch time |
| `axisai-mobile` | Flutter client (read-only) | `C:/Users/yusuf/tmp/axisai-mobile-phase2` | `origin/main` `3386df37198ef0193c64fa4754a686357868f785` (2026-08-26, `feat(mobile): converge Today on canonical backend state`) | detached at that exact SHA; verified against `git ls-remote` |

The backend repository is still named `fitness-coach` on GitHub. AxisAI is the
product; the repository name is historical.

During this PR's validation window `origin/main` advanced to `b138778`
(`docs: specify native training vertical slice (#255)`). That commit adds one
new file, `docs/mobile/training-vertical-slice.md`, and touches no application
code, model, migration, route or flag. Every finding, matrix row and decision
below was derived at `a44f31e` and is unaffected; this branch is deliberately
left based on `a44f31e` so the evidence and the base agree.

### Worktree

```
git worktree add .worktrees/sprint13-pr1-nutrition-closure-discovery \
    -b sprint13-pr1-nutrition-closure-discovery origin/main
```

`.worktrees/` is gitignored (`.gitignore:31-32`). The developer's working tree
was not modified, branched, or checked out.

### Schema

* `migrations/versions/` holds **37** revisions with **exactly one head**:
  `c2d3e4f5a6b7` (`c2d3e4f5a6b7_add_plan_confirmation_proposal.py`).
* No nutrition migration is proposed by this PR.

### Feature flags relevant to Nutrition

| Flag | Default | Lifecycle | Effect on Nutrition |
|---|---|---|---|
| `MOBILE_AUTH_ENABLED` | **`False`** | `blocked` (review by 2026-10-01) | Gates registration of the entire `/api/v1` blueprint (`app/__init__.py:329-331`). **Every mobile nutrition route is shipped-dark in production.** |
| `AXISAI_NATIVE_AUTH_ENABLED` | `False` | `blocked` | Flutter-side native-auth path. Sprint 15. |
| `UIUX_*`, `WEEKLY_PROGRAM_UI_ENABLED`, `FITX_WORKOUT_SESSIONS_ENABLED`, `AI_ADAPTIVE_PLAN_CONTEXT`, `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` | `False` | dark / staging | No nutrition authority. |

There is **no nutrition-specific rollout flag**. Web nutrition is live and
unflagged; mobile nutrition is entirely behind `MOBILE_AUTH_ENABLED`.

---

## 3. Inspected scope and limitations

**Inspected (backend):** `app/models.py`, `app/blueprints/nutrition/{diary,meallog,plan}.py`,
`app/blueprints/{food,menu,social,tracking,training,mobile_nutrition,mobile_api,mobile_today}.py`,
`app/services/{mobile_nutrition,mobile_log_food,mobile_diary_mutation}/`,
`app/services/{meal_idempotency,mobile_food_discovery,barcode,fatsecret,nutrition_pipeline,ai_coach,ai_nutrition,coach_confirmation,analytics_engine,context_builder,mobile_today}.py`,
`app/services/{progress_summary,progress_insights,progress_history,progress_physique}/`,
`app/{timeutil,feature_flags,today_presenter,__init__}.py`, `app/services/menu_{fetch,extract,ocr}.py`,
`fitx_mcp/server.py`, `static/nutrition.js`, `templates/{index,today}.html`,
`migrations/versions/`, `tests/`.

**Inspected (mobile, read-only):** `lib/features/nutrition/**`,
`lib/app/router/{app_router,nutrition_route_context,app_navigation_coordinator}.dart`,
`fixtures/nutrition-diary-*.json`, `docs/NUTRITION.md`, `docs/adr/0006-*`.

**Limitations, stated rather than papered over:**

1. The mobile clone has uncommitted modifications, but **only** in generated
   desktop plugin registrants (`linux/`, `macos/`, `windows/`). Nothing under
   `lib/`, `test/` or `fixtures/` differs from `origin/main`, so every mobile
   claim below is a claim about `3386df3`.
2. No production database was queried. Every statement about persisted state is
   derived from models, migrations and code, not from row counts.
3. No production host was reached. Flag values quoted are the **registry
   defaults** in `app/feature_flags.py`, which the module documents as "the
   value that was already in production before PR2". A host `.env` override is
   not provable from the repository.
4. `docs/STATUS.md` is stale (`Last updated: 2026-07-15`) and was not treated as
   authority for anything.
5. An unrelated untracked file (`scripts/frontend_audit/pump_check_pr2_matrix.py`)
   appeared in the developer's main working tree during this session. It was not
   created, read into, or touched by this PR, and it is outside this worktree.

---

## 4. Sprint 9 → Sprint 13 reconciliation

Historical PR descriptions were treated as hypotheses. Each was re-verified
against `a44f31e` (backend) and `3386df3` (mobile).

| Historical claim | Source | Status on current main | Evidence |
|---|---|---|---|
| #204 published a mobile diary read contract | backend | **True and current** | `app/blueprints/mobile_nutrition.py:31`, `app/services/mobile_nutrition/` |
| #205 added mobile food discovery + canonical LogFood | backend | **True and current** | `mobile_nutrition.py:71-146`, `app/services/mobile_log_food/` |
| #206 added stale-safe mobile diary mutations | backend | **True and current** | `mobile_nutrition.py:148-248`, `app/services/mobile_diary_mutation/` |
| "Mobile PR3B must wait for backend review" | `docs/MOBILE_NUTRITION.md:511` | **Superseded** — mobile `#11` merged | mobile `8a2a79e` |
| Mobile has no diary entry identity | `docs/MOBILE_NUTRITION.md:327-347` | **Superseded** | `lib/features/nutrition/domain/diary_day.dart`, `nutrition_diary_mutation_controller.dart` |
| Mobile Sprint 9 nutrition closed by PR4 | mobile | **True** | mobile `c1fee6c` (`#12`) |
| Coach meal staging → confirmation → single ledger write | backend | **True and current** | `app/services/ai_coach.py:281-345`, `app/services/coach_confirmation.py` |
| Progress deliberately invents no nutrition intelligence | Progress Redesign PR2/PR3 | **True in the canonical services, false in a legacy orphan** | `progress_summary/analysis.py:43`, `progress_insights/models.py:35` vs `app/blueprints/tracking.py:894-935` (F9) |
| `/api/v1/today` carries nutrition | — | **False** | `app/services/mobile_today.py:182` `estimated_calories` is the *training plan's* `tahmini_kalori`, i.e. estimated burn |

**Net:** Sprint 9's *mobile* backlog is closed. What remains is web-side and
derived-figure work that Sprint 9 never scoped.

---

## 5. Canonical source-of-truth matrix

Classification vocabulary: **AP** authoritative persisted · **AD** authoritative
derived (server) · **P** presentation-only · **S** staging-only · **U**
unavailable · **L** legacy compatibility.

| Concept | Owner | Class | Evidence |
|---|---|---|---|
| Consumed-food ledger | `MealLog` | **AP** | `app/models.py:557-599` |
| Diary/builder state | `CustomMeal` + `CustomMealItem` | **S** | `app/models.py:1338-1374`; `diary.py:436-493` docstring: "KANONİK … DEĞİLDİR" |
| Nutrition plan | `NutritionPlan.plan_data` (opaque LLM JSON) | **AP** (separate domain) | `app/models.py:321-329` |
| Calendar day + timezone | `app/timeutil` (`app_today`, `day_key`, `APP_TZ = Europe/Istanbul`) | **AD** | `app/timeutil.py:12,40,45`; `MealLog.tarih` is `NOT NULL` with an `app_today()` default — but the ISO **format is not enforced by the schema** (F13) |
| Daily calories/macros | `MealLog` rows summed server-side, NULL→0 | **AD** | `mobile_nutrition/serialization.py:day_totals`; `meallog.py:217-239` |
| Calorie target | newest `UserSession.target_calories` | **AP** | `mobile_nutrition/queries.py:fetch_target_energy_kcal` |
| **Macro targets** | **no owner — 5 derivations** | **AD (contested)** | F2, §7 |
| Meal slot | `MealLog.ogun` — a Turkish **display label**, not an enum | **AP** (weakly typed) | `models.py:560`; only 4 of 8 writers use a canonical label |
| Description | `MealLog.yemekler` (free text) | **AP** | `models.py:561` |
| Source / provenance | `MealLog.source` (`String(20)`, default `"manual"`) | **AP** (lossy) | `models.py:569`; wire vocabulary in `serialization.py:KNOWN_SOURCES` |
| Provider identity (food id) | — | **U** in the ledger; **S** in `CustomMealItem.fatsecret_food_id` | `models.py:1367` |
| Serving identity / description / quantity | — | **U** in the ledger; **S** in `CustomMealItem.serving_*` | `models.py:1371-1373` |
| Quantity / mass | — | **U** in the ledger; **S** in `CustomMealItem.grams` | `models.py:1360` |
| Per-100 g reference | — | **U** in the ledger; **S** in `CustomMealItem.per_100g_*` | `models.py:1368-1371` |
| Meal photo | `MealLog.photo_key` (S3 key) | **AP** | `models.py:571` |
| Water / hydration | `WaterLog` (`user_id`,`date_key` unique) | **AP** — **separate domain** | `models.py:1064-1081`; routes live in `app/blueprints/training.py:631-700` |
| Entry identity (API) | HMAC over `(user_id, MealLog.id)`, domain-separated | **AD** | `mobile_nutrition/identity.py` |
| Entry revision / stale-write authority | HMAC over a typed canonical encoding of 14 authoritative fields | **AD** | `mobile_nutrition/revision.py` |
| Idempotency authority | `Idempotency-Key` header + `uq_meal_log_user_idempotency` | **AP** | `app/services/meal_idempotency.py`; `models.py:592-593` |
| Nutrition score / adherence | **none server-side**; a browser-only score exists | **P (unowned)** | `static/nutrition.js:187-210` (F10) |

### There is exactly one definition of "what the user ate today"

`MealLog`. The builder (`CustomMeal`) is staging: committing a builder meal
writes a `MealLog` row **and** leaves the builder row in place, which is why
both route docstrings forbid summing the two surfaces
(`diary.py:443-446`, `meallog.py:209-215`). The mobile read model repeats the
prohibition in the type system by keeping `NutritionDiaryDay` and
`NutritionDiaryDraft` as separate types
(`lib/features/nutrition/domain/nutrition_diary_day.dart:88-93`).

No third definition was found. `/api/progress/nutrition`,
`/api/progress/heatmap`, `analytics_engine`, `menu.analyze_menu`,
`ai_coach._today_nutrition_totals` and `fitx_mcp` all read `MealLog` and none
of them persists a competing total.

---

## 6. Writer inventory

Every code path that can create, change, or delete canonical `MealLog` state.
Eight in the application, one in a non-deployed tool, one in an audit script.

Legend — **Principal**: how the acting user is established. **Recompute**:
whether the server derives the persisted macros from provider truth (`server`)
or persists what the caller supplied (`client`, bounded by the clamp).

| # | Writer | Entry point | Principal | Target user | Date | Slot | Recompute | Clamp | `source` | Idempotency | Concurrency | Txn | Ack |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| W1 | Web manual / AI-estimated meal | `POST /meal-log` (`nutrition/meallog.py:27`) | Flask-Login + `cognito_sid` | `current_user` | `day_key()` | **unvalidated free text** (F7) | **client** on the `override_macros` branch (F5); LLM on the other | `clamp_serving_macros` / `sanitize_meal_total_macros` | **unset** → the column default stamps `manual` (F5) | optional header | unique key | commit-once | after commit |
| W2 | Web AI-plan quick add | `POST /api/quick-add-meal` (`nutrition/diary.py:79`) | Flask-Login | `current_user` | `day_key()` | 4 canonical labels | server (from stored plan JSON) | `_sanitize_meal_macros` | `ai_plan` | optional header | unique key | commit-once | after commit |
| W3 | Diary-builder commit | `POST /api/diary/meal/<id>/log` (`nutrition/diary.py:378`) | Flask-Login | `current_user` | `day_key()` | 4 canonical labels | server (sums builder items) | per item at write time (`_clamp_item_macros`) | `diary` | **none** (F12) | atomic `is_logged` claim | single txn | after commit |
| W4 | Web barcode add | `POST /api/food/barcode/add` (`food.py:96`) → `barcode.add_food_to_diary` | Flask-Login | `current_user` | `day_key()` | 4 canonical labels (validated) | **client-supplied `food` object accepted** (F6) | `clamp_serving_macros` | `barcode` | optional header | unique key | commit-once | after commit |
| W5 | AI Coach meal confirmation | `_tool_confirm_and_commit_meal_log` (`ai_coach.py:281`) | Flask-Login (coach turn) | `user_id` from session | `day_key()` | literal `"AI Koç"` → wire `unknown` | server (staged `PendingAction` payload) | `clamp_serving_macros` | `coach` | atomic `PendingAction` claim | delete-claim then insert | single txn | after commit |
| W6 | Shared meal suggestion | `_persist_meal_suggestion` (`social.py:1169`) | Flask-Login (receiver flow) | `snapshot.receiver_id` | `day_key()` | `"<sender> …alınan öneri"` → wire `unknown` | server (parse + provider + LLM) | `clamp_serving_macros` | **unset** → the column default stamps `manual` (F8) | **none** | atomic message claim (`social.py:1019`) | single txn | after commit |
| W7 | Mobile canonical LogFood | `POST /api/v1/nutrition/logs` (`mobile_nutrition.py:110`) | `require_mobile_auth` → `g.mobile_user` | `g.mobile_user.id`, no parameter | `day_key()` | 4 slots, strict enum | **server** — re-fetches the serving and rescales | typed `Decimal` bounds in `commands.py` | `search` / `barcode` / `manual` | **required** header + semantic fingerprint | unique key; provider I/O outside the write txn | commit-once | `201`/`200` after commit |
| W8 | Mobile slot move / delete | `PATCH`/`DELETE /api/v1/nutrition/logs/<token>` (`mobile_nutrition.py:170,207`) | `require_mobile_auth` | `g.mobile_user.id` | current `day_key()` only | 4 slots, strict enum | n/a | n/a | unchanged | `If-Match` revision (not replay-idempotent by design) | `SELECT … FOR UPDATE` + revision compare under the lock | single txn | `200` / `204` after commit |
| W9 | MCP tool (**not deployed**) | `fitx_mcp/server.py:380` | **none — `user_id` is a tool argument** | arbitrary | `_day_key()` | literal `"AI Koç"` | client (tool arguments) | `clamp_serving_macros` | `coach` | **none** | raw psycopg2 | single txn | n/a |
| W10 | Frontend-audit seeder (**not production**) | `scripts/frontend_audit/seed.py:138` | n/a | fixture | fixture | fixture | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| W11 | One-time backfill (**already ran, June 2026**) | `migrations/versions/df0d08c0cd24_…py:36-61` | n/a — bulk `INSERT … SELECT` from `user_daily_nutrition` | row's own `user_id` | **`created.strftime("%d.%m")` — malformed** (F13) | literal `"AI Koç"` | none — copied verbatim | **none** | `coach` | n/a | n/a | migration txn | n/a |

### Sibling writers that bypass the canonical mobile path's protections

Recorded, **not fixed** in PR1:

* **W1 `override_macros`** persists caller-computed nutrition. The mobile path
  (W7) treats the client as untrusted and rebuilds the numbers from
  `mobile_food_discovery.servings(food_id)`. Same table, two trust models —
  `docs/MOBILE_NUTRITION.md:315-320` states the recompute rule as if it were
  universal; it is true of W7 only. (F5)
* **W4** accepts a whole `food` dict from the request body (`food.py:123-129`),
  so the "provider-backed" claim of a `source="barcode"` row is unverified.
  It has no frontend consumer. (F6)
* **W6** loses its own provenance by not setting `source`. (F8)
* **W3** is the only application writer with no idempotency key. It is safe
  today because `_claim_diary_meal` atomically flips `is_logged` inside the same
  transaction, but the protection is structural, not declared. (F12)
* **W9** takes `user_id` as a parameter. It is not in `docker-compose.yml`, the
  `Dockerfile`, or `deploy/`, so it is a developer tool — but it is a real
  ledger writer and belongs in this inventory. (F11)

### No double-count path exists

The only two surfaces that both describe "today's food" are W3's ledger row and
the builder row it was composed from, and no reader sums them (§7).

---

## 7. Reader / downstream-consumer inventory

| Consumer | Reads | Derives new product truth? | Can disagree? | Missing vs zero | Day source |
|---|---|---|---|---|---|
| Web Nutrition Today (`/meal-log/today` → `static/nutrition.js:155`) | `MealLog` | **yes** — client macro targets `30/40/30` + fallbacks `140/200/60 g` (`nutrition.js:168-172,131-133`); per-meal score + A–D grade (`:187`) | **yes** (F2, F10) | zero-fills (`m.kalori or 0`) | server `day_key()`, published as `display_ddmm` |
| Web Nutrition history (`/meal-log/history`) | `MealLog`, newest 14 **day keys** | no | no | zero-fills | `display_ddmm` — **`DD.MM`, no year** |
| Diary builder (`/api/diary/today`) | `CustomMeal`/`CustomMealItem` | no | n/a — explicitly not consumption | zero-fills | `app_today()` |
| Dashboard rings (`templates/index.html:718`, `templates/today.html:253`) | `/meal-log/today` | no (renders server totals) | no | zero | server |
| Mobile Nutrition (`/api/v1/nutrition/diary/today`) | `MealLog` | **no** — `totals` are the server's; `goalImpact` is a null-guarded presentation projection (`nutrition_diary_day.dart:131-139`) | no | **`null` stays `null`** per macro; totals NULL→0, documented | server, published with IANA zone |
| AI Coach context (`ai_coach._today_nutrition_totals`, `_remaining_macros_for_user`) | `MealLog` + `UserSession` | **yes** — remaining-macro budget | **yes** (F2) | NULL→0 via `coalesce` | `day_key()` |
| Menu analysis (`menu.analyze_menu:301-319`) | `MealLog` + `UserSession` | **yes** — remaining-macro budget (formula duplicated from coach) | **yes** (F2) | zero-fills | `day_key()` |
| Barcode goal impact (`barcode.goal_impact_for_add`, `_target_macros`) | `MealLog` + `UserSession` | **yes** — targets/remaining, **and a `2000 kcal` default** | **yes** (F2, F3) | zero-fills | `day_key()` |
| Weekly nudges (`analytics_engine._check_protein_goal:108`, `_check_missing_logs`) | `MealLog` | **yes** — weekly protein goal (aligned to coach for protein only) | partially (F2) | NULL→0 | `MealLog.tarih` range |
| Heatmap (`tracking.py:832`) | distinct `MealLog.tarih` | no | no | absent day = level 0 | `tarih` |
| `/api/progress/nutrition` (`tracking.py:615`) | `MealLog` | no | **orphaned — no consumer** (F9) | zero-fills; `kcal > 0` used as "logged" | `tarih` |
| `/api/progress/insights` (`tracking.py:894`) | `MealLog` + `UserSession` | **yes** — calorie-adherence tone, `80 ≤ pct ≤ 110` = success | **orphaned — no consumer** (F9) | skips when `target` falsy | `app_today()` |
| Canonical Progress (`progress_summary`, `progress_insights`, `progress_history`, `progress_physique`) | **no nutrition at all** | no | no | n/a | n/a |
| Mobile Today (`/api/v1/today`) | **no nutrition at all** | no | no | n/a | server |
| `fitx_mcp` weekly report (`server.py:481`) | `MealLog` | summarises | not deployed | NULL→0 | `tarih` / UTC bounds |

**Client-side calculations that are, or could become, competing authorities:**
`static/nutrition.js` alone contains a macro-target split (`30/40/30`) that
matches none of the three server formulas, hardcoded macro fallbacks, and a
meal-scoring rule engine with a letter grade. All are presentation-only — none
is persisted or sent back to the server — but they are product truth the user
reads, and the mobile client shows nothing equivalent.

---

## 8. Web ↔ backend ↔ mobile parity matrix

| Capability | Backend | Web | Mobile (`3386df3`) | Parity |
|---|---|---|---|---|
| Read today's ledger | yes | yes | yes | ✅ |
| Server-owned day + explicit timezone | yes | **`DD.MM`, no year, no zone** | `{date, timezone}` | ⚠️ web is legacy |
| Null macros preserved | ledger keeps NULL | zero-filled | `null` preserved | ⚠️ web fabricates zeros |
| Unset calorie goal | `NULL` | ring falls back to a JS default | `goal: null` | ⚠️ |
| Food search | yes | yes | yes | ✅ |
| Serving detail | yes | yes | yes | ✅ |
| Barcode lookup | yes | yes | yes | ✅ |
| Log provider-backed food | yes | via `override_macros` (client math) | canonical, server-recomputed | ❌ F5 |
| Log manual food | yes | yes | yes (typed bounds) | ⚠️ different validation |
| Log meal photo | yes (`photo_key`) | yes | **no mobile path** | ⚠️ web-only capability |
| **Delete a committed entry** | mobile route only | **no** | yes | ❌ **F1** |
| **Move a committed entry's slot** | mobile route only | **no** | yes | ❌ **F1** |
| Edit description / nutrition of a committed entry | **unsupported by design** | no | no | ✅ consistent |
| Entry identity | opaque token (mobile), raw `meal_log_id` echoed by W4 | raw int | opaque token | ⚠️ F6 |
| Stale-write protection | `If-Match` (mobile) | n/a (no mutation) | yes | ✅ |
| Idempotent logging | header + unique key | optional | **required** | ⚠️ |
| Diary builder | yes | yes | `getDiaryDraft()` returns `notFound` — deliberately unimplemented | ✅ intentional |
| History | `/meal-log/history` (`DD.MM`) | yes | **absent**, and documented as absent (`nutrition_diary_repository.dart:38-40`) | ✅ intentional |
| Menu scan / analyse | yes | yes | **absent** | ✅ intentional |
| Nutrition plan | yes | yes | absent | ✅ separate domain |
| Water / hydration | yes | yes | absent | ✅ separate domain |
| Nutrition score / adherence | **none** | browser-only score + grade | none | ❌ F10 |

---

## 9. Sprint 9 deferral disposition

| Deferred item | Disposition | Evidence / reasoning |
|---|---|---|
| Mobile PR3B — opaque ids, revisions, `If-Match`, slot move, delete, post-`412`/`404`/ambiguous re-read, no local macro subtraction | **CLOSED — shipped** | `live_nutrition_repository.dart:145-176` sends `If-Match: "<revision>"` on PATCH and DELETE with `ReplayPolicy.never`; `nutrition_diary_mutation_controller.dart:187-249` maps `staleDiaryEntry`→re-read, `diaryEntryNotFound`→re-read, connectivity/timeout/503/429→re-read, then decides `_desiredStateReached`; totals are never recomputed client-side (`nutrition_diary_day.dart:76-86`) |
| Manual description / nutrition editing | **NO LONGER DESIRABLE as a Sprint 13 requirement** | Requires a durable entry-kind that is not persisted; the correct correction primitive (delete + re-log) already exists on mobile and is missing only on web. Closing F1 supersedes the need. See **C4** |
| Provider quantity / serving change | **NO LONGER DESIRABLE (Sprint 13)** | Same. Cannot be proven from the ledger; the builder holds the structured values, and re-logging is exact. See **C4** |
| Provider food replacement | **NO LONGER DESIRABLE (Sprint 13)** | Same |
| Provider ↔ manual conversion | **CLOSED — forbidden, permanently** | Would fabricate provenance; the current contract already forbids it |
| Provider provenance columns on `MealLog` | **STILL OPEN — SAFE POST-CLOSURE** | Not a correctness bug (see **C4**). Nothing today reads provenance it does not have; the mobile contract publishes the absence honestly |
| Nutrition history (mobile) | **STILL OPEN — SAFE POST-CLOSURE** | The legacy contract is `DD.MM` with no year (`meallog.py:280`), which the mobile read model explicitly refuses to key on. A native history contract is analytics, not consumption correctness. See **C6** |
| Mobile menu adapter + its security prerequisite | **Prerequisite CLOSED; adapter STILL OPEN — SAFE POST-CLOSURE** | `menu_fetch.py` rejects private/loopback/link-local/non-global IPs (`:10-31`), allowlists ports (`:33`), pins DNS against rebinding (`_pin_getaddrinfo:78`), follows redirects manually with `allow_redirects=False` and re-validates each hop (`_safe_requests_get:107-163`), caps bodies at 3 MB / 50 MB (Drive), and both routes sit behind rate limits plus separate scrape/AI concurrency gates (`menu.py:89-92, 276-281`). Menu **never writes the ledger** — it only reads today's totals (`menu.py:301`). See **C7** |
| Nutrition intelligence in Progress | **CLOSED as a deferral — and reaffirmed** | The canonical Progress services carry no nutrition by construction (`progress_summary/analysis.py:43`, `progress_insights/models.py:35`). Two unowned heuristics exist elsewhere and must be dealt with, not blessed (F9, F10). See **C8** |

---

## 10. Security, privacy and failure semantics

**Clean:**

* Mobile routes take the principal only from `g.mobile_user`; there is no
  account parameter on any nutrition route.
* Entry tokens are owner-bound HMACs with per-purpose domain separation
  (`identity.py:_SUBKEY_INFO`, `revision.py:_SUBKEY_INFO`), compared with
  `hmac.compare_digest`, and token resolution scans only the caller's own rows
  (`mobile_diary_mutation/service.py:_resolve_entry_id`), so an unknown token
  cannot confirm another account's entry.
* Nutrition error logs carry a type name and a request id and never a meal,
  macro, target or account id (`mobile_nutrition.py:44-52`).
* The mutation boundary takes a row lock **before** recomputing and comparing
  the revision (`service.py:_locked_current_row`), and holds no provider call
  inside the transaction.
* Provider network I/O is wrapped in `blocking_concurrency_slot()` on every
  mobile discovery path (`mobile_food_discovery.py:57,72,101`), so FatSecret
  latency cannot park all web threads.
* Menu fetching is SSRF-hardened (§9).
* DB-level `ck_meal_log_macro_bounds` rejects negatives and gross overflow on
  every write path including raw SQL (`models.py:582-588`).

**Failure semantics:**

| Situation | Behaviour |
|---|---|
| Storage fault on a mobile read | `503 NUTRITION_TEMPORARILY_UNAVAILABLE`, `retryable=true`, session **not** invalidated |
| Idempotency key reused with a different command | `409 IDEMPOTENCY_CONFLICT` (semantic fingerprint, not raw JSON) |
| Provider food/serving gone at log time | `404 FOOD_NOT_FOUND` |
| Missing / malformed `If-Match` | `428` / `400` |
| Stale revision | `412`, client re-reads |
| Unknown, cross-user, historical or malformed entry token | `404` — indistinguishable, deliberately |
| Lost DELETE response | unresolvable by retry; resolved by canonical re-read — **implemented** on mobile |
| AI macro estimation fails on `/meal-log` | `502`, **no zero-macro row is written** (`meallog.py:171-176`) |
| Suggestion parse yields all-zero macros | no row written (`social.py:1148-1153`) |

**Weaknesses (all recorded, none fixed here):** F5 (client-trusted macros on the
web), F6 (caller-supplied `food` object), F7 (unvalidated `ogun` → `DataError`
above 100 characters on PostgreSQL), F11 (`user_id` as a tool argument in a
non-deployed writer).

---

## 11. Architecture decisions

### C1 — `MealLog` remains the single canonical consumed-food ledger

*Evidence:* §5, §6. Every production writer already converges on it; every
production reader already reads it.
*Options:* (a) keep `MealLog`; (b) promote `CustomMealItem` to the ledger and
make `MealLog` derived; (c) new normalised `nutrition_entry` table.
*Chosen:* (a).
*Rejected:* (b) turns a staging model with no ownership of coach/social/barcode
writes into the ledger and would need all eight writers to compose items;
(c) is a migration whose only justification is provenance, which C4 shows the
product does not need.
*Single authority:* preserved by construction — no new table, no new totals.
*Migration:* none. *Security:* none. *Compat:* none.
*Owned by:* nothing — this decision is a constraint on later PRs.

### C2 — Macro targets get exactly one server-owned derivation

*Evidence (F2, F3):* five derivations —
`ai_coach._remaining_macros_for_user:133`, `menu.analyze_menu:311-313`,
`barcode._target_macros:219`, `analytics_engine._check_protein_goal:119`, and
`static/nutrition.js:168-172`. For `goal = "kilo verme"` or `goal = ""` (the two
non-bulk values `profile.py:135` permits), coach/menu compute carbohydrate
target `cal × 0.50 / 4` while barcode computes `cal × 0.45 / 4` — for a
2000 kcal user, **250 g vs 225 g**. `barcode._target_macros:220` additionally
substitutes `2000` when no target is configured, and `/meal-log/review:308`
does the same, which is precisely the fabrication the mobile boundary refuses
(`serialization.py:nutrition_goal`).
*Options:* (a) one pure `nutrition_targets` module every reader calls;
(b) persist macro targets on `UserSession`; (c) leave it.
*Chosen:* **(a)** — a pure, stdlib-only derivation module with the coach/menu
formula as the surviving definition, returning `None` when no target is
configured. Callers decide how to present absence; none substitutes a number.
*Rejected:* (b) is a migration to fix a duplication problem, and it would make
historic rows carry targets nobody set; (c) leaves two surfaces contradicting
each other in front of the same user on the same day.
*Single authority:* one function, five call sites.
*Migration:* none. *Security:* none. *Compat:* the barcode payload's
`targets.carbs` changes for non-bulk users — a **correction**, and the barcode
add route has no frontend consumer today (F6).
*Owned by:* **PR2**.

### C3 — Web provider-backed logging must be recomputed server-side; manual entry stays a bounded manual command

*Evidence (F4, F5):* `static/nutrition.js:517-523` sums `per_100g` across
selected foods and posts the result as `override_macros`, so a multi-food quick
log always means "100 g of each"; `nutrition.js:1502-1513` posts
`serving × quantity` computed in the browser. `meallog.py:99-124` persists both
after a plausibility clamp only.
*Options:* (a) reuse `mobile_log_food` for the web provider path and keep
`override_macros` strictly for manual entry with the mobile command's typed
bounds; (b) keep the web as-is and document the asymmetry; (c) forbid
`override_macros` entirely.
*Chosen:* **(a)**. `POST /meal-log` keeps its manual and AI-estimation
branches; the web serving modal and multi-select move onto a
provider-backed command that the server rescales from
`mobile_food_discovery.servings(food_id)` — the same code path as W7.
*Rejected:* (b) leaves the ledger's meaning dependent on which client wrote the
row; (c) removes the only way to log a food the provider does not have.
*Single authority:* one recompute implementation for both clients.
*Migration:* none. *Security:* removes a client-controlled macro path.
*Compat:* the web request shape changes; the ledger shape does not.
*Owned by:* **PR3**.

### C4 — Advanced diary editing is **not** required for Nutrition core closure; the correction primitive is delete + re-log

*Evidence:* the ledger stores totals, a display description, a slot label and a
source — and nothing that could reconstruct a provider food, serving or
quantity (§5). The builder holds those values, but only for builder-composed
meals, and only until the day rolls over. Re-logging is exact: it produces the
same row the original write would have produced, with correct provenance.
*Options:* (a) declare advanced editing out of scope and guarantee a correction
path on every client; (b) add provenance columns and build provider-aware edit;
(c) leave the historical "PR3B later" ambiguity open.
*Chosen:* **(a)**. The historical deferral is closed as *not required*, not as
*postponed*. Unsupported edits stay explicitly unsupported — which the mobile
contract already states (`docs/MOBILE_NUTRITION.md:393-400`).
*Rejected:* (b) is a migration in search of a requirement: no surface today
asks the ledger for provenance, and inferring it from `source`, description,
macro ratios, barcode text or the idempotency fingerprint is forbidden and
would be guessing. (c) is what this PR exists to end.
*Single authority:* preserved — no second representation of an entry's history.
*Migration:* none. *Security:* none. *Compat:* requires C5.
*Owned by:* the closure criteria (§12), enforced by C5.

### C5 — The web gains ledger delete and slot move; it is a core-closure blocker

*Evidence (F1):* no route mutates or deletes a `MealLog` row for a web user. A
grep of the repository finds `MealLog` construction in eight places and
`db.session.delete` on a `MealLog` row in **one** — the mobile mutation service.
The web builder's PATCH/DELETE routes operate on `CustomMealItem` and refuse to
act once `is_logged` is true (`diary.py:304,371`), which is correct for the
builder and leaves the ledger untouchable.
*Options:* (a) add web delete + slot move over the same
`mobile_diary_mutation` service; (b) add a web-only delete with its own
semantics; (c) accept that web users cannot correct a mislogged meal.
*Chosen:* **(a)** — the web routes call the existing service. Web has no
`If-Match` story, so the precondition transport differs (the row's own revision
carried in the page payload, or a form-level confirmation); the *authority*,
the row lock, and the revision comparison do not.
*Rejected:* (b) creates a second mutation semantic over one ledger; (c) makes
"core complete" untrue — a user who logs the wrong meal on the web is stuck
with it forever, and it silently corrupts every downstream total.
*Single authority:* one mutation service, two transports.
*Migration:* none. *Security:* web CSRF + `@require_auth`; ownership already
enforced inside the service. *Compat:* additive.
*Owned by:* **PR4**.

### C6 — Nutrition history is not part of core closure

*Evidence:* `/meal-log/history` publishes `display_ddmm(k)` (`meallog.py:280`),
a `DD.MM` label with no year and no zone; the mobile read model documents its
refusal to key on it (`nutrition_diary_repository.dart:38-40`). No consumer
outside `static/nutrition.js` reads it.
*Chosen:* keep the legacy web contract unchanged; publish **no** mobile history
in Sprint 13. A native history/trend contract is an analytics capability with
its own design, not a consumption-correctness requirement.
*Rejected:* building it now — it would be the largest piece of Sprint 13 and it
closes no correctness gap.
*Owned by:* a later sprint. Explicit non-goal here.

### C7 — Mobile menu scanning stays a deliberately separate capability

*Evidence:* §9. The security prerequisite is closed; only an adapter is
missing; menu never writes the ledger.
*Chosen:* menu scanning is **not** part of Nutrition closure. If it ships, it
ships as its own capability PR with its own rate-limit and concurrency budget.
*Rejected:* folding it into closure — it would add an attack surface and an
LLM/scrape cost centre to a sprint whose subject is ledger authority.
*Owned by:* a later sprint.

### C8 — No canonical Nutrition intelligence domain in Sprint 13; the two unowned ones are retired or confined

*Evidence (F9, F10):* `tracking.py:894-935` scores calorie adherence
(`80 ≤ pct ≤ 110` → `success`) and `static/nutrition.js:187-210` produces a
0–100 score and an A–D grade from a hand-tuned rule set. Neither has an owner,
a validation, or a mobile counterpart. Both `/api/progress/nutrition` and
`/api/progress/insights` have **no** frontend consumer.
*Options:* (a) build a canonical nutrition-intelligence domain; (b) retire the
orphaned endpoints and declare the browser score presentation-only and
non-exportable; (c) leave both.
*Chosen:* **(b)**. Truthful incompleteness is acceptable; two unowned
definitions of "a good nutrition day" are not.
*Rejected:* (a) — no validated authority exists to base a score on, and Progress
already refused to invent one for exactly this reason; (c) — the orphans will
be picked up by a future surface and become authority by accident.
*Migration:* none. *Compat:* route removal has no consumer; still gets its own
rollback boundary.
*Owned by:* **PR5** (small, optional, last).

### C9 — Nutrition plans are a separate planning domain

`NutritionPlan.plan_data` is opaque LLM JSON with no schema validation
(`diary.py:117-121` parses it defensively). Its only tie to the ledger is W2,
which reads a plan meal and writes a normal ledger row. Closure covers the
**consumption** ledger; plan generation, storage and validation are their own
domain and are out of scope.

### C10 — Water/hydration is out of scope

`WaterLog` is owned by `app/blueprints/training.py`, keyed `(user_id, date_key)`,
and never mixed into macro totals. It is a separate daily-habit domain.

### C11 — Closure is decoupled from mobile-auth rollout

`MOBILE_AUTH_ENABLED` defaults off and is `blocked` on a capacity prerequisite;
`AXISAI_NATIVE_AUTH_ENABLED` is Sprint 15. "Nutrition core complete" therefore
means **contract- and parity-complete**, not **activated**. No Sprint 13 PR may
change either flag, and no Sprint 13 acceptance criterion may depend on mobile
traffic reaching production.

### C12 — `MealLog.ogun` stays a display label; the web writer gains validation

Three writers deliberately store non-slot labels (`"AI Koç"`,
`"<sender>…alınan öneri"`) and the wire vocabulary already has `unknown` for
them. Converting the column to an enum would either lose those labels or force
a fabricated slot. Instead, W1 validates `ogun` against the four canonical
labels (F7), which removes the `DataError` and the arbitrary-slot path without
touching the schema or the three intentional non-slot writers.
*Owned by:* **PR3**.

### C14 — The ledger day key becomes a database-enforced invariant, and malformed rows are repaired conditionally

*Evidence (F13):* one historical writer produced `DD.MM` keys; nothing prevents
another from doing it again.
*Options:* (a) conditional repair + a `CHECK` constraint on the format;
(b) repair only; (c) constraint only; (d) leave the rows where they are, since
they are invisible rather than wrong.
*Chosen:* **(a)**. The repair is exact and needs no guesswork:
`app_date_of(created_at)` is the repository's own rule for turning the stored
naive-UTC timestamp into an Istanbul day, and it is the same rule that produced
`tarih` for every correctly written row. The `CHECK` makes the invariant the
database's, so no future writer — including a raw-SQL one like W9 or W11 — can
reintroduce the defect.
*Rejected:* (b) leaves the door open; (c) would fail to apply while malformed
rows exist; (d) leaves permanently unreachable rows inside the canonical ledger
and a schema that permits more of them.
*Single authority:* strengthens it — `app/timeutil` becomes the only producer of
a value the database will accept.
*Migration:* **yes** — one data repair plus one `CHECK`. It is a no-op on a
database with no malformed rows. This is the only migration Sprint 13 needs.
*Security:* none. *Compat:* repaired rows begin appearing in day-keyed reads,
which is the intent; a user could see old coach meals reappear on past days.
*Owned by:* **PR2A**.

### C13 — `POST /api/food/barcode/add` is removed

It has no frontend consumer, it accepts a caller-supplied `food` object, and it
echoes the raw `meal_log_id`. Its capability is fully covered by C3's
server-recomputed web provider path.
*Owned by:* **PR3** (same rollback boundary as the web logging change).

---

## 12. Formal definition — "Nutrition Core Complete"

Sprint 13 may be declared CORE COMPLETE when **all** of N1–N10 hold. Each is
stated so it can be checked, not argued.

| # | Criterion | Status today | Closed by |
|---|---|---|---|
| **N1** | Exactly one canonical consumed-food ledger (`MealLog`), and no surface persists a competing definition of what was eaten | ✅ already true | — (C1 guards it) |
| **N2** | Every supported writer converges on that ledger through a single clamp/validation gate, and no writer accepts caller-supplied nutrition for a provider-backed food | ❌ F5, F6 | PR3 |
| **N3** | Canonical daily totals cannot differ between web, mobile, Coach and downstream consumers, because all of them read the same rows and none re-derives totals | ✅ already true | — |
| **N4** | Every **derived** nutrition figure (macro targets, remaining budget) has exactly one server-owned derivation, and no surface substitutes a number for an unset target | ❌ F2, F3 | PR2 |
| **N5** | Day and day-boundary decisions are server-owned and identical on every path (`app/timeutil`), and every persisted day key is a valid ISO calendar date | ⚠️ true of every live writer; ❌ F13 for historical rows, and unenforced by the schema | PR2A |
| **N6** | Null and zero are truthful at every published boundary: a missing macro is not a measured zero, and an unset goal is not `0` | ⚠️ true on `/api/v1`; false on the legacy web payloads | accepted as legacy (C6); not a blocker |
| **N7** | User- and provider-supplied nutrition is bounded before persistence on every path | ✅ already true (clamp + DB `CHECK`) | — |
| **N8** | Repeated requests are safe on every application writer | ⚠️ W3 relies on a structural claim, W6 has none | PR3 (declare/attach keys) |
| **N9** | Mobile mutations are stale-safe, and **every** client has a correction path for a committed entry; edits that cannot be proven from stored state remain explicitly unsupported rather than silently approximated | ❌ F1 (web has no correction path) | PR4 |
| **N10** | No unowned nutrition scoring/adherence definition ships on any surface | ❌ F9, F10 | PR5 |

### What may honestly remain open after Sprint 13

* Durable provider/serving provenance on the ledger (C4).
* A native mobile nutrition history / trend contract (C6).
* A mobile menu-scanning adapter (C7).
* A validated nutrition intelligence / adherence domain (C8).
* Meal photos on mobile.
* Nutrition plan schema validation (C9).
* Hydration on mobile (C10).
* Activation of `MOBILE_AUTH_ENABLED` (C11, Sprint 15).

None of these makes the Nutrition **core** incomplete: each is an added
capability, not a correctness or authority gap.

---

## 13. Findings

### P0

**None.** Explicitly checked and clean: no fabricated persisted state, no
double-count path, no cross-user read or write, no client-owned day boundary,
no second consumed-food ledger, no unbounded macro reaching the database.

### P1

**F13 — the ledger may hold rows with an unmatchable day key.**
`migrations/versions/df0d08c0cd24_backfill_user_daily_nutrition_to_meal_.py:58`
writes `"tarih": created.strftime("%d.%m")` — a `DD.MM` label — into the column
every other writer fills with ISO `YYYY-MM-DD` (`app/timeutil.py:45-57`;
`MealLog.tarih` even defaults to `app_today().isoformat()`). Nothing at the
database level pins the format: `tarih` is `String(10) NOT NULL` with no `CHECK`.

Consequences, traced:

* `"15.06" >= "2026-08-24"` is `False` under string comparison, so every
  range-filtered reader excludes such a row: `/api/progress/nutrition`
  (`tracking.py:621`), the heatmap (`tracking.py:849`),
  `analytics_engine._check_protein_goal:129-131`, `ai_coach:602-603`.
* Every equality-filtered reader excludes it too: `/meal-log/today`,
  `/api/v1/nutrition/diary/today`, `menu.py:301`, `ai_coach:122`.
* `/meal-log/history` groups by `tarih` and takes the 14 lexicographically
  greatest keys, which `DD.MM` never reaches; `display_ddmm` would return such a
  value unchanged (`timeutil.py:93-98`).

So the rows are **consistently invisible rather than miscounted** — no total is
wrong today. They are still orphaned rows in the canonical ledger, and any
future query that loosens its filter would surface them in the wrong year.

`CLAUDE.md` already documents the invariant the migration broke — *"Eski
UserDailyNutrition verisi MealLog'a taşındı… MealLog.tarih ISO 'YYYY-MM-DD'"* —
in the same sentence that describes this backfill. The documentation and the
migration that implements it disagree, which is exactly the drift this PR's
characterization tests exist to stop.

**Whether any such row exists in production is not provable from the
repository** — it depends on whether `user_daily_nutrition` held rows when the
migration ran on 2026-06-15. The defective code path is proven; the row count is
not. → PR2A.

**F1 — the web has no correction path for the canonical ledger.**
`MealLog` rows are created by six web paths and deleted by none.
`app/blueprints/nutrition/diary.py:297,364` mutate and delete `CustomMealItem`
and refuse once `is_logged` is set. A web user who logs the wrong meal cannot
fix it, and the error propagates into every total, nudge and weekly report
forever. Blocks **N9**. → PR4.

**F2 — five macro-target derivations, two of which disagree numerically.**
`app/services/barcode.py:226` uses `calories * 0.45 / 4` for carbohydrates
unconditionally; `app/services/ai_coach.py:148` and `app/blueprints/menu.py:313`
use `0.50` for every goal except `"kas kazanma"`. `profile.py:135` permits
exactly `{"kilo verme", "kas kazanma", ""}`, so **two of the three real goal
values disagree**. `static/nutrition.js:168-172` adds a fourth split
(`30/40/30`) that matches none of them. Blocks **N4**. → PR2.

**F3 — a fabricated `2000 kcal` target.** `app/services/barcode.py:220`
(`_to_float(..., 2000) or 2000`) and
`app/blueprints/nutrition/meallog.py:308` present a goal the user never set. The
mobile boundary explicitly refuses this (`serialization.py:nutrition_goal`).
Blocks **N4**. → PR2.

**F4 — the web multi-food quick log records per-100 g values as a meal total.**
`static/nutrition.js:517-523` reduces `f.per_100g.*` across `selectedFoods` and
posts the sum as `override_macros`. There is no gram or serving control on that
path, so the ledger's quantity is implicitly and always 100 g per food. The
autocomplete row shows `f.macros` (per serving) and the selected-foods row shows
`f.per_100g`, so the two numbers a user sees for the same food differ.
Blocks **N2**. → PR3.

**F5 — two trust models over one ledger.** `nutrition/meallog.py:99-124`
persists caller-computed macros after a plausibility clamp;
`mobile_log_food/service.py:52-80` re-fetches the serving and rescales it
server-side. `docs/MOBILE_NUTRITION.md:315-320` states the recompute rule as
universal — it is true of the mobile path only. The override branch also sets
no `source`, so a browser-computed provider serving is stamped `"manual"` by the
column default and is indistinguishable from a hand-typed meal.
Blocks **N2**. → PR3.

### P2

**F6 — `POST /api/food/barcode/add` accepts a caller-supplied `food` object**
(`app/blueprints/food.py:123-129`) and echoes the raw `meal_log_id`
(`:154`). No file under `static/` or `templates/` calls it. → PR3 (C13, remove).

**F7 — `/meal-log` does not validate `ogun`** (`meallog.py:32`). Free text
reaches `MealLog.ogun` (`String(100)`), publishes as slot `unknown`, and raises
`DataError` above 100 characters on PostgreSQL. → PR3 (C12).

**F8 — the shared-meal-suggestion writer loses its own provenance.**
`app/blueprints/social.py:1174-1183` sets no `source`, so the ORM column default
(`models.py:569`) stamps `"manual"` and every reader — including the mobile
contract, where `"manual"` is a *known* source and therefore not even reported
as `unknown` — describes a suggestion-accepted meal as hand-entered. It also
uses no idempotency key. → PR3.

**F9 — orphaned nutrition read surfaces.** `/api/progress/nutrition`
(`tracking.py:615`) and `/api/progress/insights` (`tracking.py:894`) have no
consumer in `static/` or `templates/`. The latter contains an unowned
calorie-adherence rule (`80 ≤ pct ≤ 110` → `success`, `tracking.py:920-928`).
→ PR5 (C8).

**F10 — a browser-only nutrition score with no owner.**
`static/nutrition.js:187-210` computes a 0–100 value and an A–D grade from
hand-tuned thresholds and renders it per meal (`:237`). Presentation-only, but
it is a product judgement no server owns and no other client shows. → PR5 (C8).

**F11 — `fitx_mcp.log_nutrition_entry` writes the ledger with `user_id` as a
tool argument** (`fitx_mcp/server.py:380-401`), with no authenticated principal
and no idempotency. It is absent from `docker-compose.yml`, the `Dockerfile`
and `deploy/`, so it is not production-reachable — but it is a real writer and
must not be forgotten when the ledger's write rules change. → recorded; no PR.

**F12 — `diary_log_meal` writes without an idempotency key**
(`diary.py:407-417`). Safe today only because `_claim_diary_meal` flips
`is_logged` atomically in the same transaction. The protection is real but
undeclared. → PR3 (declare it, or attach a key).

---

## 14. Sprint 13 PR2+ implementation sequence

Derived from the findings, ordered by: correctness before capability, canonical
authority before adapters, backend contract before consumer, no schema change
required by any of them, one reviewable responsibility per PR, independent
rollback boundary for each.

```
PR1 (this) ── discovery + characterization
    |
    +-- PR2A ledger day-key repair + CHECK invariant   (F13 -> N5)
    |            data integrity first; no other PR depends on it, and it
    |            depends on none, so it can land immediately
    |
    +-- PR2  canonical macro-target authority          (F2, F3 -> N4)
    |
    +-- PR3  web write-path convergence                (F4, F5, F6, F7, F8, F12 -> N2, N8)
    |            depends on PR2 only for the shared "no fabricated target" rule
    |
    +-- PR4  web ledger correction path                (F1 -> N9)
    |            independent of PR2/PR3; sequenced after PR3 so the web write
    |            path is settled before a mutation path is added over it
    |
    +-- PR5  retire unowned nutrition intelligence     (F9, F10 -> N10)
                 optional, last, smallest rollback boundary
```

### PR2A — Ledger day-key repair and invariant

* **Objective:** every `MealLog.tarih` is a valid ISO calendar date, and the
  database refuses anything else.
* **Repositories:** `fitness-coach` only.
* **Authority:** *reuses* `app/timeutil.app_date_of` as the sole repair rule;
  adds no authority.
* **Schema:** **one migration** — an `UPDATE` of rows whose `tarih` does not
  match `^\d{4}-\d{2}-\d{2}$`, setting it to `app_date_of(created_at)`, followed
  by `ck_meal_log_tarih_iso`. Must be written DB-agnostically (PostgreSQL prod,
  SQLite dev), like `df0d08c0cd24` was.
* **Risks:** the repair is a no-op if no malformed rows exist, and the row count
  is unknown before it runs — so the migration must **log** how many rows it
  touched. Repaired rows become visible in past-day reads, which will change
  historical totals for affected users (a correction). Adding a `CHECK` to a
  large table takes a brief lock; the table is small enough that this is
  acceptable, but the migration should be run in the normal deploy window.
* **Prerequisite:** none.
* **Acceptance:** the migration is idempotent and reversible in the sense that
  matters (the `CHECK` drops cleanly; repaired values are not restored, and the
  down-revision must say so explicitly); after upgrade, an attempted insert of a
  non-ISO `tarih` fails at the database on both engines; the discovery test
  asserting that no live writer produces a non-ISO key still passes.
* **Out of scope:** deleting `user_daily_nutrition`; changing `display_ddmm`;
  changing the history contract (C6); any other column.

### PR2 — Canonical macro-target authority

* **Objective:** one server-owned derivation of daily macro targets and the
  remaining-macro budget; no surface substitutes a number for an unset target.
* **Repositories:** `fitness-coach` only.
* **Authority:** *changes* — the macro-target derivation moves from four copies
  to one pure module; *reuses* — `UserSession.target_calories`, `MealLog`.
* **Schema:** none.
* **Risks:** `barcode.goal_impact_for_add` carbohydrate targets change for
  non-bulk users (a correction); a user with no target now sees an explicit
  "no goal configured" instead of a `2000 kcal` comparison, which changes coach
  prompt text and the barcode payload.
* **Prerequisite:** none.
* **Acceptance:** exactly one function in the repository computes macro targets
  from a calorie target and a goal, proven by a characterization test; all four
  server call sites use it; a user with no `target_calories` receives `None`
  from every one of them; existing coach/menu/barcode/analytics tests pass.
* **Out of scope:** persisting targets; changing the calorie-target selector;
  the browser's `30/40/30` split (that is PR5's surface, and PR2 must not touch
  `static/`).

### PR3 — Web write-path convergence

* **Objective:** the web logs provider-backed food the way mobile does, manual
  entry is a bounded manual command, and the unsafe/undeclared sibling writers
  are removed or made explicit.
* **Repositories:** `fitness-coach` only (backend + `static/nutrition.js`).
* **Authority:** *changes* — provider-backed macro computation moves from the
  browser to `mobile_log_food`'s recompute; *reuses* — the ledger, the clamp,
  `meal_idempotency`.
* **Schema:** none.
* **Risks:** the largest behavioural PR of the sprint. Logged calories for the
  multi-select path change (from per-100 g to the chosen quantity) — a
  correction, but a visible one. `/api/food/barcode/add` removal is a public
  route deletion. `ogun` validation rejects requests that previously succeeded.
* **Prerequisite:** PR2 (shares the "never fabricate a target" rule).
* **Acceptance:** a provider-backed web log produces a byte-identical ledger row
  to the equivalent `/api/v1/nutrition/logs` call; `override_macros` accepts
  only manual commands within the mobile snapshot's bounds; `ogun` outside the
  four canonical labels is `400`; the suggestion writer sets
  `source="suggestion"` (a new value added to `KNOWN_SOURCES`) and uses a key;
  `diary_log_meal`'s replay safety is declared in a test; `/api/food/barcode/add`
  is gone and nothing references it.
* **Out of scope:** any mutation or deletion of a committed row (PR4); any
  scoring change (PR5); any mobile change; any UI redesign.

### PR4 — Web ledger correction path

* **Objective:** a web user can delete a committed entry and move its slot,
  through the same authority mobile uses.
* **Repositories:** `fitness-coach` only.
* **Authority:** *reuses* `app/services/mobile_diary_mutation` (renamed if the
  name stops being honest); no second mutation semantic.
* **Schema:** none.
* **Risks:** the web has no `If-Match` story, so the precondition transport must
  be designed without weakening the row-lock + revision comparison; deletion is
  hard and irreversible (as on mobile), so the confirmation UX matters.
* **Prerequisite:** PR3.
* **Acceptance:** web delete and slot move are current-day only, ownership-
  scoped, row-locked, revision-checked, and reject a stale precondition; the
  mobile contract's behaviour is bit-for-bit unchanged; a mislogged web meal can
  be corrected end-to-end; unsupported edits still return an explicit refusal
  rather than an approximation.
* **Out of scope:** editing description, nutrition, quantity, serving or
  provider (C4); soft delete, tombstones or a mutation journal; history.

### PR5 — Retire unowned nutrition intelligence

* **Objective:** no surface ships a nutrition score or adherence judgement that
  no service owns.
* **Repositories:** `fitness-coach` only.
* **Authority:** removes two; adds none.
* **Schema:** none.
* **Risks:** lowest of the sprint — both server endpoints are orphaned; the
  browser score is presentation-only.
* **Prerequisite:** none (may run in parallel with PR4).
* **Acceptance:** `/api/progress/nutrition` and `/api/progress/insights` are
  removed with no remaining reference; the browser score and grade are either
  deleted or confined behind an explicit "presentation-only, never persisted,
  never exported" boundary with a test that pins it; no new score is introduced.
* **Out of scope:** creating any adherence/intelligence domain; touching the
  canonical Progress services.

---

## 15. Migration implications

**Exactly one**, and it is a repair, not a feature: PR2A's day-key normalisation
plus `ck_meal_log_tarih_iso` (C14). Current head is `c2d3e4f5a6b7`; after
Sprint 13 the head is that one new revision. PR2, PR3, PR4 and PR5 require no
schema change at all.

Any proposal to add a provenance column must first satisfy C4 by naming the
product requirement that reads it.

## 16. Rollout implications

No Sprint 13 PR may change a feature-flag default, and none is gated behind a
new flag: PR2A–PR5 are all corrections to live, unflagged behaviour. PR2A is the
only one that touches the database and must therefore ride a normal deploy
window with the migration gate; the rest are code-only. Mobile
nutrition remains dark behind `MOBILE_AUTH_ENABLED` throughout, and nothing in
this sequence depends on that flag being enabled. Sprint 15's native-auth
rollout is not coupled to Nutrition closure in either direction.

The only user-visible number changes are corrections: repaired historical coach
meals reappearing on past days (PR2A), the barcode carbohydrate target (PR2),
the absence of a fabricated 2000 kcal goal (PR2), and the multi-food quick-log
quantity (PR3). Each should ship with a release note.

## 17. Rollback philosophy

Each PR is one `git revert` with no data to undo:

| PR | Rollback | Data left behind |
|---|---|---|
| PR2A | `alembic downgrade` drops `ck_meal_log_tarih_iso` | **repaired day keys stay repaired** — the down-revision must say so rather than pretend to restore `DD.MM`, because restoring a defect is not a rollback |
| PR2 | revert; the four call sites return to their own formulas | none — nothing persisted |
| PR3 | revert; the web returns to client-computed macros and the removed route returns | ledger rows written correctly stay correct |
| PR4 | revert; web loses the correction path again | deletions already performed stay deleted — hard delete is irreversible by design, which is why PR4 ships after PR3 and behind an explicit confirmation |
| PR5 | revert; the orphaned endpoints and the browser score return | none |

No PR writes a migration, so no PR needs a down-revision plan.

## 18. Explicit non-goals for PR1 — all honoured

No application code, mobile code, model, migration, route, serializer, API
payload, feature flag, environment value, LLM prompt, provider call, nutrition
algorithm, UI, CSS, native-auth or Sprint 15 work was changed. The diff is this
document plus one characterization test module. Severe findings were documented
and characterized, never opportunistically fixed. No existing test was
weakened, skipped, xfailed, renamed away or broadened.

## 19. Evidence produced by this PR

`tests/test_sprint13_nutrition_closure_discovery.py` — 25 discovery
characterization tests. Each maps to a claim above and fails if the claim stops
being true. Structural properties are proven by AST, import, URL-map or runtime
characterization rather than by copy or layout snapshots.

**Non-vacuity, proven by mutation.** Five architecture guards were checked by
temporarily violating the real invariant in the working tree, confirming the
guard failed, and restoring with `git checkout --`. No mutation remains in the
branch (`git status --porcelain` shows only the two new files).

| Mutation | Guard | Result |
|---|---|---|
| Added a `MealLog(...)` construction to `app/services/foodcache.py` | `test_canonical_ledger_writer_inventory_is_closed` | FAILED ✅ |
| Added a `serving_id` column to `MealLog` | `test_the_builder_holds_the_provenance_the_ledger_does_not` | FAILED ✅ |
| Added `GET /api/v1/nutrition/diary/history` | `test_mobile_nutrition_route_inventory` and `test_the_mobile_surface_publishes_no_history_menu_plan_or_water` | both FAILED ✅ |
| Imported `MealLog` into `progress_summary/queries.py` | `test_progress_and_mobile_today_consume_no_nutrition_authority` | FAILED ✅ |
| Added `DELETE /meal-log/<id>` (the PR4 capability) | `test_the_web_nutrition_blueprint_publishes_no_ledger_mutation_route` | FAILED ✅ |

### Validation run

`python -m py_compile tests/test_sprint13_nutrition_closure_discovery.py` — OK.

New suite: `pytest tests/test_sprint13_nutrition_closure_discovery.py -q -p no:randomly`
→ **25 passed**.

Focused regression gate over every architecture surface this report relies on.
Batched because the suite is slow; `-p no:randomly` so an ordering-sensitive
failure is reproducible.

| # | Surface | Files | Result |
|---|---|---|---|
| 1 | Mobile diary read + food discovery | `test_mobile_nutrition_api`, `test_mobile_nutrition_revision`, `test_mobile_food_discovery`, `test_food_discovery_characterization` | 123 passed (81.19s) |
| 2 | Mobile ledger write + mutation | `test_mobile_log_food_api`, `test_mobile_log_food_fingerprint`, `test_mobile_diary_mutation_api`, `test_mobile_diary_mutation_architecture`, `test_mobile_diary_mutation_parsing` | 103 passed (62.73s) |
| 3 | Web nutrition, macro pipeline, day keys | `test_nutrition_routes`, `test_nutrition_pipeline`, `test_timeutil`, `test_ai_nutrition_llm` | 206 passed (97.90s) |
| 4 | Food / barcode / menu writers | `test_food_routes`, `test_barcode`, `test_barcode_workflow`, `test_menu_routes`, `test_menu_fetch` | 114 passed (115.00s) |
| 5 | AI Coach meal staging + confirmation | `test_ai_coach`, `test_coach_confirmation_lifecycle`, `test_coach_routes`, `test_coach_tools` | 148 passed (155.26s) |
| 6 | Mobile auth boundary + migration graph | `test_mobile_auth_feature_gate`, `test_mobile_auth_api`, `test_migration_graph` | 45 passed (52.34s) |
| 7 | Progress aggregation + Sprint 12 discovery tripwire | `test_progress_api`, `test_progress_summary`, `test_progress_insights`, `test_sprint12_daily_coach_discovery` | 134 passed (65.64s) |

**898 tests, 0 failures, 0 skips introduced, 0 existing tests changed.** No
existing test was weakened, deleted, skipped, xfailed, renamed or broadened.

## 20. Files inspected

Backend: `app/models.py`; `app/__init__.py`; `app/timeutil.py`;
`app/feature_flags.py`; `app/today_presenter.py`;
`app/blueprints/nutrition/{__init__,diary,meallog,plan}.py`;
`app/blueprints/{food,menu,social,tracking,training,mobile_api,mobile_nutrition,mobile_today,profile}.py`;
`app/services/mobile_nutrition/{__init__,identity,queries,revision,serialization}.py`;
`app/services/mobile_log_food/{__init__,commands,fingerprint,parsing,service}.py`;
`app/services/mobile_diary_mutation/{__init__,commands,preconditions,service}.py`;
`app/services/{meal_idempotency,mobile_food_discovery,barcode,fatsecret,nutrition_pipeline,ai_coach,ai_nutrition,coach_confirmation,analytics_engine,context_builder,mobile_today,menu_fetch,menu_extract,menu_ocr}.py`;
`app/services/{progress_summary,progress_insights,progress_history,progress_physique}/`;
`fitx_mcp/server.py`; `scripts/frontend_audit/seed.py`; `static/nutrition.js`;
`templates/{index,today,plan}.html`; `migrations/versions/`; `tests/conftest.py`;
`tests/test_mobile_auth_feature_gate.py`; `tests/test_sprint12_daily_coach_discovery.py`;
`docs/{MOBILE_NUTRITION,STATUS,FEATURE_FLAGS}.md`; `CLAUDE.md`;
`docs/superpowers/specs/2026-08-23-sprint12-pr1-daily-coach-convergence-discovery.md`.

Mobile (read-only, `3386df3`): `lib/features/nutrition/**`;
`lib/app/router/{app_router,nutrition_route_context,app_navigation_coordinator}.dart`;
`fixtures/nutrition-diary-*.json`.
