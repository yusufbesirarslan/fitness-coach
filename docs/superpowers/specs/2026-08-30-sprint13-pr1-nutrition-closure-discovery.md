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
* the **daily macro-target split and the remaining-macro budget** are derived
  independently by three server call sites, two of which disagree numerically
  for every non-bulk user, and one of which fabricates a `2000 kcal` goal for a
  user who never configured one — with a fourth, competing split living in the
  browser;
* the main web search-and-log path posts **per-100 g** figures as a meal total,
  so quantity is silently fixed at 100 g;
* the web `/meal-log` `override_macros` branch persists **client-computed**
  nutrition (bounded only by a physical-plausibility clamp) into the same ledger
  the mobile path recomputes from provider truth;
* deletion — the single correction primitive this closure rests on — has **no
  stored-object lifecycle**: the row goes, the S3 meal photo stays forever, and
  the repository has no object-deletion primitive to call (F14).

**The ledger's day keys are not a defect.** This report as first written raised a
P1 (`F13`) over a historical `DD.MM` backfill. That conclusion was wrong and is
**withdrawn**: the transient state was normalised by that migration's own direct
successor, `9be792c80008`. See **§5.1**.

**Nutrition core closure does not need a provenance column, a history endpoint,
a mobile menu adapter, a nutrition intelligence domain, or a schema migration.**
It needs authority convergence on the derived figures, a web correction path
whose resource lifecycle is closed, and the removal of orphaned or unsafe
sibling writers. That is **four** small, independently reviewable PRs and **no
migration at all**.

**No P0 was found.** No writer fabricates persisted state, no double-count path
exists, no cross-user read or write was found, and the day boundary is
server-owned everywhere it matters.

### Findings index

| ID | Sev | One line |
|---|---|---|
| F1 | P1 | Web cannot delete, move, or edit a committed `MealLog` row — no correction path |
| F14 | P1 | Deleting a ledger row releases no S3 meal photo; `s3_helper` exposes no deletion primitive at all |
| F2 | P1 | Three server derivations of one daily macro-target split; `barcode._target_macros` disagrees with coach/menu for every non-bulk goal |
| F3a | P2 | `barcode._target_macros` fabricates a `2000 kcal` target and publishes it on the **live** `GET /api/food/barcode` payload — currently rendered by nothing |
| F4 | P1 | Web multi-food quick log posts **per-100 g** values as the meal total (quantity fixed at 100 g, never chosen) |
| F5 | P1 | `/meal-log` `override_macros` persists client-computed macros; the mobile path recomputes from the provider. Same ledger, two trust models |
| F6 | P2 | `POST /api/food/barcode/add` accepts a caller-supplied `food` object; no first-party consumer, but a documented `/api/food/*` compatibility surface — deprecate before removing (C13) |
| F7 | P2 | `/meal-log` does not validate `ogun`: free text reaches `MealLog.ogun` (`String(100)`) → `unknown` slot on the wire, `DataError` above 100 chars |
| F8 | P2 | Social meal-suggestion writer sets no `source` (reads back as `manual`) and uses no idempotency key |
| F9 | P2 | `/api/progress/nutrition` and `/api/progress/insights` are orphaned; the latter contains an unowned calorie-adherence heuristic |
| F10 | P2 | `static/nutrition.js` computes a 0–100 nutrition score and an A–D letter grade in the browser, with no server owner and no mobile counterpart |
| F11 | P2 | `fitx_mcp.log_nutrition_entry` writes the ledger with `user_id` as a *tool parameter* and no idempotency (not deployed) |
| F12 | P2 | `diary_log_meal` writes the ledger without an idempotency key (safe today only because of the atomic `is_logged` claim) |
| F3b | — | `/meal-log/review`'s `2000` is an internal LLM-prompt fallback for qualitative text, never a published target — **benign**, recorded so it is not re-raised |
| ~~F13~~ | — | **WITHDRAWN by independent review.** The `DD.MM` backfill was normalised by its own direct successor `9be792c80008`; there is no live defect. See **§5.1** |

### Review remediation

This document was independently reviewed after its first commit (`3b49fcf`). The
review verdict was **APPROVED WITH REQUIRED CHANGES**. Incorporated here:

| # | Correction |
|---|---|
| 1 | `F13` **withdrawn** — the day-key defect was already repaired in June 2026 (**§5.1**) |
| 2 | `C14` **retired** — no repair migration and no `ck_meal_log_tarih_iso`; **Sprint 13 requires no migration** |
| 3 | `PR2A` **removed** from the implementation sequence |
| 4 | `N5` **satisfied**, not open |
| 5 | `F3` **downgraded to P2** and split into `F3a` (live but unrendered fabrication) and `F3b` (benign prompt fallback) |
| 6 | `C2` **narrowed** to one shared domain fact; analytics reclassified as a consumer; the browser split assigned to `PR5` so `N4` is satisfiable |
| 7 | `C4` — *"re-logging is exact"* **removed**; deletion is a lossy primitive |
| 8 | `F14` **added** (P1) — no S3 object lifecycle on delete; owned by `PR4` |
| 9 | `C5` **split** — web delete is a core blocker, web slot move is not |
| 10 | `N9` **scoped** to current-day entries, with deletion as the required primitive |
| 11 | `C13` **softened** — `/api/food/barcode/add` is a legacy compatibility surface, deprecated before removal |

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
| Calendar day + timezone | `app/timeutil` (`app_today`, `day_key`, `APP_TZ = Europe/Istanbul`) | **AD** | `app/timeutil.py:12,40,45`; `MealLog.tarih` is `NOT NULL` with an `app_today()` default. No schema `CHECK` pins the format, and **§5.1** explains why adding one is not warranted |
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

### 5.1 Day-key chronology — why no repair and no `CHECK` are required

This subsection exists because the original discovery got this wrong. The
correction matters more than the appearance of a clean report.

PR1 as first written raised a P1 (`F13`):
`migrations/versions/df0d08c0cd24_backfill_user_daily_nutrition_to_meal_.py:58`
backfills old AI-Coach meals with `created.strftime("%d.%m")`, a yearless key no
`tarih`-filtered query can match. That observation about *that migration* is
accurate. The conclusion drawn from it was not — **the discovery missed the
direct successor migration.**

| Revision | Parent | Created | Effect on `meal_log.tarih` |
|---|---|---|---|
| `df0d08c0cd24` | `54f2eb195404` | 2026-06-15 20:00:19 | backfills `user_daily_nutrition` rows with a yearless `DD.MM` key |
| **`9be792c80008`** | **`df0d08c0cd24`** | **2026-06-15 20:18:37** | **rewrites every row**: `created_at` (naive UTC) → `Europe/Istanbul` → `date().isoformat()` |
| `b8c9d0e1f2a3` | later | — | backfills NULL `tarih` from `created_at` at Istanbul, then `SET NOT NULL` (PostgreSQL) |
| `f6a7b8c9d0e1` | later | — | `DROP TABLE IF EXISTS user_daily_nutrition` — the malformed producer's source table no longer exists |

`9be792c80008` is the **immediate child** of the backfill, eighteen minutes later
in migration history, and its docstring states the intent outright:
*"meal_log.tarih'i yıl-içermeyen '%d.%m'den ISO 'YYYY-MM-DD'ye taşı…
Idempotent: tekrar çalışsa aynı ISO değerini üretir."* Its rule —
`created.replace(tzinfo=UTC).astimezone(Europe/Istanbul).date().isoformat()` —
is **exactly** the repair the withdrawn `PR2A` proposed, applied unconditionally
to every row instead of conditionally to malformed ones.

The graph makes that repair unavoidable rather than optional: all **37**
revisions are ancestors of the single head `c2d3e4f5a6b7`, so no database at
head can have run `df0d08c0cd24` without also running `9be792c80008`. Both
migrations were replayed in sequence against an isolated database during review;
rows written as `['15.06', '15.06', '02.01']` came out as
`['2025-06-15', '2026-06-15', '2026-01-03']` — the last confirming the Istanbul
day-shift of a `22:30 UTC` timestamp.

**Why no `CHECK` replaces it.** The retired `ck_meal_log_tarih_iso` was not
merely unnecessary, it was weaker than it read. `MealLog.tarih` is `String(10)`,
so a constraint can only pin a **pattern, never a calendar date** — `2026-13-45`
satisfies every form of it. PostgreSQL's `~` is a syntax error on SQLite, so a
single portable `CheckConstraint` in `__table_args__` cannot be a regex at all;
the portable `GLOB` / `LIKE` forms that do build on both engines accept
impossible dates. That is unlike `ck_meal_log_macro_bounds`, which *is* portably
expressible — the precedent does not transfer. Weighed against a lock on the
ledger table, for a producer that no longer exists, the trade is not worth
making.

**Consequences, recorded plainly:**

* `F13` is **withdrawn**, not downgraded. It was never a live defect.
* No day-key repair migration is required, so **`PR2A` does not exist**.
* **`N5` is satisfied**, not open.
* One residual theoretical gap is recorded and deliberately *not* promoted to a
  finding: a row written before June 2026 with `created_at IS NULL` is
  `continue`d past by `9be792c80008`. `MealLog.created_at` is nullable, but every
  writer in §6 relies on its `datetime.utcnow` default, `b8c9d0e1f2a3` later
  backfills NULL day keys on PostgreSQL, and the only producer of non-ISO keys
  was dropped along with its source table. A bounded historical curiosity, not a
  P1.
* The characterization suite now pins the **repair**, not only the transient
  defect (§19). Before this remediation, deleting `9be792c80008` would have
  failed no test — a false negative in the guard set.

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
| W11 | One-time backfill (**already ran, June 2026**) | `migrations/versions/df0d08c0cd24_…py:36-61` | n/a — bulk `INSERT … SELECT` from `user_daily_nutrition` | row's own `user_id` | `created.strftime("%d.%m")` — **transient**, normalised to ISO by its direct successor `9be792c80008` (§5.1) | literal `"AI Koç"` | none — copied verbatim | **none** | `coach` | n/a | n/a | migration txn | n/a |

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
| Barcode goal impact (`barcode.goal_impact_for_add`, `_target_macros`) | `MealLog` + `UserSession` | **yes** — targets/remaining, **and a `2000 kcal` default** | **yes** (F2, F3a) | zero-fills | `day_key()` |
| Weekly nudges (`analytics_engine._check_protein_goal:108`, `_check_missing_logs`) | `MealLog` | answers a **different question** — a *weekly* protein goal, already aligned to the coach percentage | **no** — a downstream **consumer** of the same ratio, not a competing target authority | NULL→0 | `MealLog.tarih` range |
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
| **Delete a committed entry** | mobile route only | **no** | yes | ❌ **F1** — core blocker (C5) |
| Release the stored photo when an entry is deleted | **no path anywhere** | n/a | n/a | ❌ **F14** — the object outlives the row on every client |
| Move a committed entry's slot | mobile route only | no | yes | ⚠️ product gap, **not** a closure blocker (C5) |
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
| Provider quantity / serving change | **NO LONGER DESIRABLE (Sprint 13)** | Same. Cannot be proven from the ledger; the builder holds the structured values, and delete + re-log reproduces the row within the bounds **C4** now states explicitly — a stored meal photo is **not** among them (F14) |
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

### C2 — The daily macro-target split has exactly one server-owned derivation

*The domain fact:* **the authoritative daily macro-target split and the derived
remaining-macro budget** — one question ("given this user's configured calorie
target and goal, how much protein / carbohydrate / fat does the day allow, and
how much of it is left?"), currently answered more than once.

*Evidence (F2, F3a):* three server call sites derive it —
`ai_coach._remaining_macros_for_user:133`, `menu.analyze_menu:311-313` and
`barcode._target_macros:219`. For `goal = "kilo verme"` or `goal = ""` (two of
the three values `profile.py:135` permits), coach and menu compute carbohydrate
target `cal × 0.50 / 4` while barcode computes `cal × 0.45 / 4` — for a
2000 kcal user, **250 g vs 225 g**. `barcode._target_macros:220` additionally
substitutes `2000` when no target is configured, which is precisely the
fabrication the mobile boundary refuses (`serialization.py:nutrition_goal`).

*What is **not** a competing authority:*

* `analytics_engine._check_protein_goal:119` answers a **different question** — a
  *weekly* protein goal — and already consumes the coach percentage
  deliberately. It is a downstream **consumer** of the same ratio, not a rival
  target authority. Converging it on the canonical ratio is fine; describing it
  as a fifth contradicting derivation was not.
* `/meal-log/review`'s `2000` (F3b) is an internal fallback inside an LLM prompt
  that produces qualitative text. It publishes no number as a configured target
  and must not be forced to become one.

*What **is** the other half of the problem:* `static/nutrition.js:168-172`
carries a browser-owned `30/40/30` split matching none of the server formulas.
It is presentation, but it is the number a web user actually reads. **PR2 is
backend-only and must not touch `static/`**, so that half is explicitly assigned
to **PR5** — without which N4 could never be marked satisfied.

*Options:* (a) one pure `nutrition_targets` module every server reader calls;
(b) persist macro targets on `UserSession`; (c) leave it.
*Chosen:* **(a)** — a pure, stdlib-only derivation with the coach/menu formula
as the surviving definition, returning `None` when no target is configured.
Callers decide how to present absence; none substitutes a number.
*Rejected:* (b) is a migration to fix a duplication problem, and it would make
historic rows carry targets nobody set; (c) leaves two surfaces contradicting
each other in front of the same user on the same day.
*Single authority:* one function; three server call sites, plus analytics
consuming the same ratio for its own question.
*Migration:* none. *Security:* none. *Compat:* the barcode payload's
`targets.carbs` changes for non-bulk users — a **correction**. Note the
fabrication ships on the **live** `GET /api/food/barcode?code=` lookup route
(`static/nutrition.js:497`), not only on the unconsumed add route; no first-party
surface renders it today (F3a).
*Owned by:* **PR2** (server) and **PR5** (browser split).

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

### C4 — Advanced diary editing is **not** required for Nutrition core closure; the correction primitive is delete + re-log, and it is lossy

*Evidence:* the ledger stores totals, a display description, a slot label and a
source — and nothing that could reconstruct a provider food, serving or
quantity (§5). The builder holds those values, but only for builder-composed
meals, and only until the day rolls over.

*Delete + re-log is **bounded**, not exact.* Re-logging reproduces the ledger's
macros, description, slot and source. It does **not** reproduce:

* the **stored meal photo** — `photo_key` is written by the web `/meal-log` path
  only, and mobile has no photo-logging path at all (§8), so a mobile user who
  deletes a web-logged photo meal cannot reproduce it on that client;
* the original `created_at`, which the new row re-stamps;
* the original idempotency key and fingerprint.

The stored object itself is left behind entirely (**F14**). Any claim that
re-logging is "exact" is **withdrawn**. Deletion is an acceptable correction
primitive only where its losses are explicit, surfaced to the user before they
confirm, and — for the photo — accompanied by an object lifecycle. **PR4** owns
that.

*Options:* (a) declare advanced editing out of scope and guarantee a bounded,
honestly-described correction path on every client; (b) add provenance columns
and build provider-aware editing; (c) leave the historical "PR3B later" ambiguity
open.
*Chosen:* **(a)**. The historical deferral is closed as *not required*, not as
*postponed*. Unsupported edits stay explicitly unsupported — which the mobile
contract already states (`docs/MOBILE_NUTRITION.md:393-400`).
*Rejected:* (b) is a migration in search of a requirement: no surface today asks
the ledger for provenance, and inferring it from `source`, description, macro
ratios, barcode text or the idempotency fingerprint is forbidden and would be
guessing. Making deletion honest is cheaper, and truer, than making editing
perfect. (c) is what this PR exists to end.
*Single authority:* preserved — no second representation of an entry's history.
*Migration:* none — and no provenance/schema expansion is introduced merely to
make editing perfect. *Security:* none. *Compat:* requires C5.
*Owned by:* the closure criteria (§12), enforced by C5 and by F14's fix in PR4.

### C5 — The web gains ledger **delete** (a core blocker); web slot move is a post-closure product gap

*Evidence (F1):* no route mutates or deletes a `MealLog` row for a web user. A
grep of the repository finds `MealLog` construction in eight places,
`db.session.delete` on a `MealLog` row in **one**, and attribute mutation of one
in **one** — both inside the mobile mutation service. The web builder's
PATCH/DELETE routes operate on `CustomMealItem` and refuse to act once
`is_logged` is true (`diary.py:304,371`), which is correct for the builder and
leaves the ledger untouchable.

**These are two decisions, not one.**

| Capability | Classification | Reasoning |
|---|---|---|
| **Web delete** | **CORE BLOCKER** | The product lets a web user create canonical consumed-food rows and gives them no truthful way to remove a mistaken one. That is a missing *authority*, not a missing feature: the error propagates into every total, nudge and weekly report forever |
| **Web slot move** | **PRODUCT GAP / POST-CLOSURE** | By C4 the correction primitive is delete + re-log, and by C12 `ogun` is a display label rather than an enum. Requiring it is mobile parity, and parity is not what closure means |

*Options:* (a) add web delete over the same `mobile_diary_mutation` service and
defer slot move; (b) add both; (c) add a web-only delete with its own semantics;
(d) accept that web users cannot correct a mislogged meal.
*Chosen:* **(a)** — the web route calls the existing service. Web has no
`If-Match` story, so the precondition transport differs (the row's own revision
carried in the page payload, or a form-level confirmation); the *authority*, the
row lock, and the revision comparison do not.
*Rejected:* (b) widens the sprint for parity rather than correctness; (c) creates
a second mutation semantic over one ledger; (d) makes "core complete" untrue — a
user who logs the wrong meal on the web is stuck with it forever.
*Note on day scope:* the service is day-agnostic — `_locked_current_row` takes
`diary_date` as a parameter — so the current-day restriction is **route policy**,
not a service constraint. N9 is scoped to match what PR4 actually ships.
*Single authority:* one mutation service, two transports.
*Migration:* none. *Security:* web CSRF + `@require_auth`; ownership already
enforced inside the service. *Compat:* additive.
*Owned by:* **PR4**. Web slot move → post-closure backlog (§12).

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

### C13 — `POST /api/food/barcode/add` is made safe and deprecated; removal needs evidence

It has no consumer under `static/` or `templates/`, it accepts a caller-supplied
`food` object, and it echoes the raw `meal_log_id`. Its capability is fully
covered by C3's server-recomputed web provider path.

But "no first-party consumer" is not "provably dead". `docs/MOBILE_NUTRITION.md:19,277`
records the `/api/food/*` routes as **keeping their paths** — a documented
compatibility surface. Deleting the route on internal-reference evidence alone
would be withdrawing a contract the repository still advertises.

*Chosen:* **PR3 makes it safe and marks it deprecated** — the caller-supplied
`food` object stops being trusted and the raw `meal_log_id` stops being echoed —
while the path keeps responding. **PR5 may remove it** only if deprecation or
disuse evidence exists by then. The unsafe behaviour is fixed either way; the
route's continued existence is a separate decision with a separate evidence bar.
*Rejected:* unconditional deletion in PR3 — it couples a real safety fix to an
unproven compatibility claim, and the two carry different rollback risks.
*Owned by:* **PR3** (safety + deprecation); **PR5** (removal, conditional).

### C14 — RETIRED / NOT REQUIRED

*Original proposal:* repair historical `MealLog.tarih` values conditionally and
add a `ck_meal_log_tarih_iso` `CHECK` constraint, owned by a `PR2A`.

*Retired by independent review.* Both halves fail on their own evidence:

* **the repair already happened.** `9be792c80008` normalised every row in June
  2026 using the identical rule PR2A proposed, and the malformed producer's
  source table was subsequently dropped (**§5.1**);
* **the `CHECK` cannot enforce what it claimed.** `tarih` is `String(10)`, so a
  constraint pins a *pattern*, never a calendar date, and no portable form exists
  across PostgreSQL and SQLite (**§5.1**).

*Architecture conclusion recorded in its place:* **no Sprint 13 migration is
required for day-key correctness.** `app/timeutil` is the sole live producer of
day keys and that is sufficient. A future schema change here would require new
evidence — a live writer producing a non-ISO key, or a demonstrated malformed
row — and is **not** pre-authorized by this discovery.

*Owned by:* nobody. **`PR2A` does not exist.**

---

## 12. Formal definition — "Nutrition Core Complete"

Sprint 13 may be declared CORE COMPLETE when **all** of N1–N10 hold. Each is
stated so it can be checked, not argued.

| # | Criterion | Status today | Closed by |
|---|---|---|---|
| **N1** | Exactly one canonical consumed-food ledger (`MealLog`), and no surface persists a competing definition of what was eaten | ✅ already true | — (C1 guards it) |
| **N2** | Every supported writer converges on that ledger through a single clamp/validation gate, and no writer accepts caller-supplied nutrition for a provider-backed food | ❌ F5, F6 | PR3 |
| **N3** | Canonical daily totals cannot differ between web, mobile, Coach and downstream consumers, because all of them read the same rows and none re-derives totals | ✅ already true | — |
| **N4** | The daily macro-target split and the remaining-macro budget have exactly one server-owned derivation; no first-party surface — server or browser — presents a different interpretation of that same configured daily target; and no surface substitutes a number for an unset target. Analytical questions that are genuinely different (a weekly protein goal, a recommendation heuristic) are **not** required to become identical | ❌ F2, F3a (server); ❌ F10 (browser split) | PR2 (server) + PR5 (browser) |
| **N5** | Day and day-boundary decisions are server-owned and identical on every path (`app/timeutil`), and every persisted day key is a valid ISO calendar date | ✅ **already true** — every live writer derives its day key from `app/timeutil`, and the one transient yearless backfill was normalised by its direct successor `9be792c80008` (§5.1). No schema `CHECK` is needed to hold this | — |
| **N6** | Null and zero are truthful at every published boundary: a missing macro is not a measured zero, and an unset goal is not `0` | ⚠️ true on `/api/v1`; false on the legacy web payloads | accepted as legacy (C6); not a blocker |
| **N7** | User- and provider-supplied nutrition is bounded before persistence on every path | ✅ already true (clamp + DB `CHECK`) | — |
| **N8** | Repeated requests are safe on every application writer | ⚠️ W3 relies on a structural claim, W6 has none | PR3 (declare/attach keys) |
| **N9** | Mobile mutations are stale-safe, and **every supported first-party client that can create a current-day consumed-food entry has a truthful correction path for that current-day entry** — deletion being the required primitive. That path must be ownership-isolated, stale-safe, free of client-fabricated totals, must close the lifecycle of a photo-bearing entry, and must tell the user where deletion is lossy. Edits that cannot be proven from stored state remain explicitly unsupported rather than silently approximated; correction of **past-day** entries and web slot move are outside Sprint 13 | ❌ F1, F14 (web has no correction path; deletion releases no stored object) | PR4 |
| **N10** | No unowned nutrition scoring/adherence definition ships on any surface | ❌ F9, F10 | PR5 |

### What may honestly remain open after Sprint 13

* Durable provider/serving provenance on the ledger (C4).
* A native mobile nutrition history / trend contract (C6).
* A mobile menu-scanning adapter (C7).
* A validated nutrition intelligence / adherence domain (C8).
* Meal photos on mobile.
* Web **slot move** for a committed entry (C5) — a product gap, not a closure gap.
* Correction of entries on **past** days, on either client (C5, N9).
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

**F1 — the web has no correction path for the canonical ledger.**
`MealLog` rows are created by six web paths and deleted by none.
`app/blueprints/nutrition/diary.py:297,364` mutate and delete `CustomMealItem`
and refuse once `is_logged` is set. A web user who logs the wrong meal cannot
fix it, and the error propagates into every total, nudge and weekly report
forever. Blocks **N9**. → PR4.

**F14 — deleting a ledger row releases no stored meal photo.**
`MealLog.photo_key` holds an S3 object key (`models.py:573`), written by the web
`/meal-log` path (`meallog.py:63,118`) and read back by every serializer. **No
application path deletes it.** `s3_helper.py` exposes `upload_image`,
`get_object_bytes`, `generate_presigned_url` and `key_belongs_to_user` — and **no
deletion function at all**, so there is nothing for a caller to call.
`mobile_diary_mutation/service.py:delete_entry` therefore removes the row and
orphans the object permanently.

Stated precisely: this is **not** cross-user access and **not** current data
corruption. The object stays private and the ledger stays internally consistent.
It is a **resource-lifecycle defect in the exact primitive this sprint makes its
correction model** (C4, C5, N9). PR4 would otherwise knowingly ship canonical
deletion with an unclosed durable resource behind it, and every subsequent
delete would widen the leak. It is also what makes delete + re-log lossy in a way
a mobile user cannot repair at all, since mobile has no photo-logging path
(§8). Blocks **N9**. → PR4 (which must add the deletion primitive as well as
call it).

**F2 — three server derivations of one daily macro-target split, two of which
disagree numerically.** `app/services/barcode.py:226` uses `calories * 0.45 / 4`
for carbohydrates unconditionally; `app/services/ai_coach.py:148` and
`app/blueprints/menu.py:313` use `0.50` for every goal except `"kas kazanma"`.
`profile.py:135` permits exactly `{"kilo verme", "kas kazanma", ""}`, so **two of
the three real goal values disagree** — 250 g vs 225 g at 2000 kcal, confirmed by
executing both functions. `static/nutrition.js:168-172` adds a fourth split
(`30/40/30`) in the browser that matches none of them.
`analytics_engine._check_protein_goal:119` is deliberately **not** counted here:
it answers a weekly question and already consumes the coach percentage, so it is
a consumer of the ratio rather than a competing authority. Blocks **N4**.
→ PR2 (server) and PR5 (browser).

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
column default and is indistinguishable from a hand-typed meal. Note this is a
finding about **provider-backed** logging: genuinely manual entry is
user-authoritative by design and stays that way (C3).
Blocks **N2**. → PR3.

### P2

**F3a — a fabricated `2000 kcal` target, published on a live route.**
`app/services/barcode.py:220` (`_to_float(..., 2000) or 2000`) returns a complete
target set for a user who configured nothing — verified by execution:
`_target_macros(SimpleNamespace(target_calories=None, goal=None))` yields
`{'calories': 2000, 'protein': 125.0, 'carbs': 225.0, 'fat': 55.6}`. Coach
returns `None` in the same situation and menu returns `400`, so barcode is the
**sole** server fabricator, and the mobile boundary explicitly refuses the
pattern (`serialization.py:nutrition_goal`).

It ships in `daily_context` from `build_lookup_response`, i.e. on the **live**
`GET /api/food/barcode?code=` route that `static/nutrition.js:497` calls — not
only on the unconsumed `add` route, which is where the original report's
compatibility reasoning was mistakenly attached. Severity is **P2**, not P1,
because no first-party surface renders it: `daily_context`, `recommendations`,
`portion_recommendation` and `axisai_food_score` have **zero** occurrences under
`static/` and `templates/`, and `resolveBarcode` reads only
`food_id` / `name` / `brand` / `servings`. A real authority defect — the API
publishes a goal nobody set — with no current user-visible blast radius.
→ PR2.

**F3b — `/meal-log/review`'s `2000` is an internal prompt fallback, not a
published target. Benign.** `app/blueprints/nutrition/meallog.py:308` substitutes
`2000` when no `target_calories` exists, but the value is interpolated into an
LLM prompt that returns *qualitative* review text. It is neither persisted nor
published as a number, so it is materially different from F3a and is **not**
required to become the user's canonical target. Recorded so it is not re-raised
as a fabrication; unless a consumer is shown to read it as a configured goal, no
action. → no PR.

**F6 — `POST /api/food/barcode/add` accepts a caller-supplied `food` object**
(`app/blueprints/food.py:123-129`) and echoes the raw `meal_log_id`
(`:154`). No file under `static/` or `templates/` calls it — but
`docs/MOBILE_NUTRITION.md:19,277` documents the `/api/food/*` paths as retained,
so it is a **legacy compatibility surface**, not a proven-dead route.
→ PR3 makes it safe and deprecates it; PR5 may remove it only with evidence
(C13).

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
(`tracking.py:615`) and the legacy `/api/progress/insights` (`tracking.py:894`)
have no consumer in `static/` or `templates/`. The latter contains an unowned
calorie-adherence rule (`80 ≤ pct ≤ 110` → `success`, `tracking.py:920-928`).
The **live** Axis Insights consumer is the different `/api/progress/axis-insights`
route, and `tracking.py:742` itself documents the legacy route as untouched —
these two must not be confused when PR5 removes one of them.
→ PR5 (C8).

**F10 — a browser-only nutrition score, grade and macro split with no owner.**
`static/nutrition.js:187-210` computes a 0–100 value and an A–D grade from
hand-tuned thresholds and renders it per meal (`:237`); `:168-172` carries a
`30/40/30` macro-target split matching no server formula. Presentation-only, but
they are product judgements no server owns and no other client shows — and the
split is the remaining half of N4. → PR5 (C8, C2).

**F11 — `fitx_mcp.log_nutrition_entry` writes the ledger with `user_id` as a
tool argument** (`fitx_mcp/server.py:380-401`), with no authenticated principal
and no idempotency. It is absent from `docker-compose.yml`, the `Dockerfile`
and `deploy/`, so it is not production-reachable — but it is a real writer and
must not be forgotten when the ledger's write rules change. → recorded; no PR.

**F12 — `diary_log_meal` writes without an idempotency key**
(`diary.py:407-417`). Safe today only because `_claim_diary_meal` flips
`is_logged` atomically in the same transaction. The protection is real but
undeclared. → PR3 (declare it, or attach a key).

### Withdrawn

**F13 — "the ledger may hold rows with an unmatchable day key" — WITHDRAWN.**
The original finding correctly observed that
`df0d08c0cd24_backfill_user_daily_nutrition_to_meal_.py:58` writes
`created.strftime("%d.%m")`. It incorrectly concluded that such rows persist. The
migration's **direct successor** `9be792c80008` rewrote every row to the ISO
Istanbul day eighteen minutes later in migration history, using the same rule
PR2A would have used; the producer's source table was later dropped; and all 37
revisions are ancestors of the single head, so the repair cannot be skipped. Full
chronology, the empirical replay, and the reasoning against a prophylactic
`CHECK` are in **§5.1**. Consequences: no repair, no constraint, no `PR2A`, and
**N5 is satisfied**.

---

## 14. Sprint 13 PR2+ implementation sequence

Derived from the findings, ordered by: correctness before capability, canonical
authority before adapters, backend contract before consumer, **no schema change
in any of them**, one reviewable responsibility per PR, and an independent
rollback boundary for each.

```
PR1 (this) ── discovery + characterization
    |
    +-- PR2  canonical daily macro-target authority  (F2, F3a -> N4, server half)
    |
    +-- PR3  web write-path convergence              (F4, F5, F6, F7, F8, F12 -> N2, N8)
    |
    +-- PR4  web ledger correction path              (F1, F14 -> N9)
    |
    +-- PR5  retire/confine unowned intelligence     (F9, F10 -> N10, N4 browser half)
```

Dependency edges, stated explicitly rather than implied by the order:

```
PR2 ──────→ PR3 ──────→ PR5
  │                      ↑
  └────────────────────┘

PR3 ──────→ PR4        (operational ordering, not a technical dependency)
```

* **`PR2 → PR3`** — PR3 inherits the "never fabricate a target" rule.
* **`PR2 → PR5`** — PR5 retires the browser split, which requires a server
  authority to defer to.
* **`PR3 → PR5`** — PR5 may remove `/api/food/barcode/add` only after PR3 has
  made it safe and deprecated it (C13).
* **`PR3 → PR4`** — **operational, not technical.** PR4 has no schema dependency
  on PR2 and no code dependency on PR3; it is sequenced after PR3 so the web
  write path is settled before a correction surface is built over it. It may land
  earlier if the correction gap is judged more urgent than that ordering benefit.

**There is no `PR2A`, and no migration gate anywhere in this sequence.**

### PR2 — Canonical daily macro-target authority

* **Objective:** one pure server-owned derivation of the configured daily calorie
  target's macro split and the remaining-macro budget; no surface substitutes a
  number for an unset target.
* **Repositories:** `fitness-coach` only.
* **Authority:** *changes* — the derivation moves from three server copies to one
  pure module; *reuses* — `UserSession.target_calories`, `MealLog`.
* **Shape:** pure and stdlib-only. Independent of Flask request state; no UI or
  client types; nothing persisted. Returns **no fabricated configured target**
  when none exists.
* **Consumers to converge**, each confirmed against current code before it is
  changed: `ai_coach._remaining_macros_for_user`, `menu.analyze_menu`, and
  `barcode._target_macros` / `goal_impact_for_add`.
  `analytics_engine._check_protein_goal` may consume the canonical **ratio**
  where the question is semantically identical, but must not be refactored into
  — or described as — a competing target authority; its weekly framing stays.
* **Schema:** none. **Migration:** none.
* **Risks:** `barcode.goal_impact_for_add` carbohydrate targets change for
  non-bulk users (a correction); a user with no target now sees an explicit "no
  goal configured" instead of a `2000 kcal` comparison, which changes coach
  prompt text and the barcode payload.
* **Prerequisite:** none.
* **Acceptance:** exactly one function in the repository derives a macro split
  from a calorie target and a goal, proven by a characterization test; all three
  server call sites use it; a user with no `target_calories` receives `None` from
  every one of them; `/meal-log/review`'s qualitative prompt fallback (F3b) is
  left as-is or documented, never promoted to a published target; existing
  coach/menu/barcode/analytics tests pass.
* **Out of scope:** persisting targets; changing the calorie-target selector;
  recommendation heuristics that answer a different question; and the browser's
  `30/40/30` split — **PR2 must not touch `static/`**. That half of N4 is PR5's.

### PR3 — Web nutrition write-path convergence

* **Objective:** the web logs provider-backed food the way mobile does, genuinely
  manual entry stays user-authoritative within typed bounds, and the unsafe or
  undeclared sibling writers are made explicit.
* **Repositories:** `fitness-coach` only (backend + `static/nutrition.js`).
* **Authority:** *changes* — provider-backed macro computation moves from the
  browser to the canonical logging service's recompute; *reuses* — the ledger,
  the clamp, `meal_idempotency`.
* **Architectural rule — reuse the authority, do not fork it.**
  `app/services/mobile_log_food` was independently verified to be **domain-pure**:
  it imports no `flask`, references no `request`, and reads no `g`. A web adapter
  can call it directly despite the `mobile_` name. If a rename or move is needed
  to make shared ownership honest now that the caller is not only mobile, keep
  that refactor **bounded and behaviour-preserving**. Naming alone must not be
  allowed to create a second service.
* **Schema:** none. **Migration:** none unless new implementation evidence proves
  otherwise — this discovery does not authorize one.
* **Risks:** the largest behavioural PR of the sprint. Logged calories for the
  multi-select path change (from per-100 g to the chosen quantity) — a
  correction, but a visible one. `ogun` validation rejects requests that
  previously succeeded.
* **Prerequisite:** PR2.
* **Acceptance:** a provider-backed web log produces a ledger row identical to
  the equivalent `/api/v1/nutrition/logs` call; `override_macros` accepts only
  genuinely manual commands, within the mobile snapshot's typed bounds; `ogun`
  outside the four canonical labels is `400`; the suggestion writer sets
  `source="suggestion"` (a new value added to `KNOWN_SOURCES`) and uses a key;
  `diary_log_meal`'s replay behaviour is declared in a test;
  `POST /api/food/barcode/add` no longer trusts a caller-supplied `food` object
  and no longer echoes a raw `meal_log_id`, and is marked deprecated **while
  still responding** (C13).
* **Out of scope:** any mutation or deletion of a committed row (PR4); any
  scoring change (PR5); **removing** `/api/food/barcode/add` (PR5, conditional);
  any mobile change; any UI redesign.

### PR4 — Web ledger correction path

* **Objective:** a web user can delete a committed entry through the same
  authority mobile uses, and that deletion closes the durable resources it owns.
* **Repositories:** `fitness-coach` only.
* **Core requirement: web delete only.** Web slot move is **not** required for
  Sprint 13 Core Complete (C5); it moves to the post-closure backlog.
* **Authority:** *reuses* `app/services/mobile_diary_mutation` (renamed if the
  name stops being honest); no second mutation semantic.
* **Must preserve, unchanged:** owner isolation; current-day semantics; the
  `SELECT … FOR UPDATE` row lock; the revision / stale-precondition comparison
  *under* that lock; canonical re-read where required; server-owned totals; no
  client-fabricated totals; and the mobile contract's behaviour bit-for-bit.
* **Photo-object lifecycle (F14) — PR4 owns it.** The acceptance criteria must
  **state**, not assume, all of:
  * what deletes the database row;
  * what releases the associated S3 object — noting that `s3_helper` exposes **no
    deletion primitive today**, so PR4 must add one;
  * the ordering of the two, and which is permitted to fail first;
  * retry safety after a partial failure;
  * what happens if the row delete succeeds and object cleanup fails, and the
    converse;
  * whether cleanup is transactional, compensating, queued or best-effort —
    chosen explicitly rather than by default;
  * observability when orphan cleanup fails, so a leak is visible rather than
    silent;
  * a user-facing confirmation that deletion is lossy, naming the stored photo
    where one exists.
* **Schema:** none. **Migration:** none — `photo_key` already exists.
* **Risks:** the web has no `If-Match` story, so the precondition transport must
  be designed without weakening the row lock + revision comparison; deletion is
  hard and irreversible (as on mobile), so the confirmation UX matters; releasing
  the object before the row is gone can leave a row pointing at nothing.
* **Prerequisite:** PR3 operationally; none technically.
* **Acceptance:** a mislogged web meal can be corrected end-to-end; delete is
  current-day, ownership-scoped, row-locked, revision-checked, and rejects a
  stale precondition; the photo lifecycle above is implemented and tested,
  including its partial-failure path; unsupported edits still return an explicit
  refusal rather than an approximation.
* **Out of scope:** editing description, nutrition, quantity, serving or provider
  (C4); **web slot move**; correction of past-day entries; advanced diary
  editing of any kind; soft delete, tombstones or a mutation journal; history.

### PR5 — Retire or confine unowned Nutrition intelligence and presentation authority

* **Objective:** no surface ships a nutrition score, an adherence judgement, or a
  macro target that no service owns.
* **Repositories:** `fitness-coach` only.
* **Authority:** removes; adds none.
* **Scope — investigate and finalise:**
  * `/api/progress/nutrition` and the **legacy** `/api/progress/insights` (F9).
    The live Axis Insights consumer is the different `/api/progress/axis-insights`
    route; do not confuse the two.
  * the browser score and A–D grade (F10) — delete, or confine behind an explicit
    "presentation-only, never persisted, never exported" boundary with a test
    that pins it.
  * the browser `30/40/30` macro split (`static/nutrition.js:168-172`) — retire it
    in favour of PR2's server authority. **This is what closes N4's remaining
    half**, and it is why PR5 depends on PR2.
  * `POST /api/food/barcode/add` — remove **only if** deprecation or disuse
    evidence exists by then (C13). Absence of first-party references is not that
    evidence on its own, and no compatibility route may be deleted solely because
    the first-party UI does not call it.
* **Schema:** none. **Migration:** none.
* **Risks:** lowest of the sprint for the server endpoints, which are orphaned.
  The browser change is user-visible: displayed macro targets move to the
  canonical split.
* **Prerequisite:** PR2 (for the split); PR3 (for the deprecation, if removal
  happens here).
* **Acceptance:** the orphaned endpoints are removed with no remaining reference;
  the browser presents the server's macro split or none at all; the score and
  grade are deleted or provably confined; no new score is introduced; any
  compatibility route removed here has recorded evidence behind it.
* **Out of scope:** creating any adherence or intelligence domain; touching the
  canonical Progress services.

---

## 15. Migration implications

**None.** Sprint 13 requires **no schema migration**. The current Alembic head is
`c2d3e4f5a6b7`, and it is still the head after Sprint 13: PR2, PR3, PR4 and PR5
each ship with an unchanged migration graph.

The day-key repair this report originally proposed does not exist, because the
repair already happened in June 2026 (§5.1; C14 retired). Any **future** schema
change in this area — including a `tarih` format constraint — requires new
evidence, such as a live writer producing a non-ISO key or a demonstrated
malformed row, and is **not** pre-authorized by this discovery.

Any proposal to add a provenance column must first satisfy C4 by naming the
product requirement that reads it. PR4's photo lifecycle (F14) is an object-store
concern and needs no column: `photo_key` already exists.

## 16. Rollout implications

No Sprint 13 PR may change a feature-flag default, and none is gated behind a new
flag: PR2–PR5 are all corrections to live, unflagged behaviour. **No PR touches
the database**, so none needs a migration deploy window. Mobile nutrition remains
dark behind `MOBILE_AUTH_ENABLED` throughout, and nothing in this sequence
depends on that flag being enabled. Sprint 15's native-auth rollout is not
coupled to Nutrition closure in either direction.

The only user-visible number changes are corrections: the barcode carbohydrate
target and the absence of a fabricated 2000 kcal goal (PR2), the multi-food
quick-log quantity (PR3), and the browser macro split aligning with the server
(PR5). Each should ship with a release note. PR4 additionally introduces a
**destructive** user action and must ship with a confirmation that states what
deletion loses (C4, F14).

## 17. Rollback philosophy

Each PR is one `git revert`. **No PR writes a migration**, so no PR needs a
down-revision plan and no PR has schema to undo.

| PR | Rollback | Data left behind |
|---|---|---|
| PR2 | revert; the three call sites return to their own formulas | none — nothing persisted |
| PR3 | revert; the web returns to client-computed macros and the deprecated route loses its safety fix | ledger rows written correctly stay correct |
| PR4 | revert; the web loses the correction path again | deletions already performed stay deleted — hard delete is irreversible by design, which is why PR4 ships after PR3 and behind an explicit confirmation. **Objects already released stay released**, so the photo lifecycle must be ordered such that a revert can never leave a surviving row pointing at a deleted object |
| PR5 | revert; the orphaned endpoints and the browser score return | none |

## 18. Explicit non-goals for PR1 — all honoured

No application code, mobile code, model, migration, route, serializer, API
payload, feature flag, environment value, LLM prompt, provider call, nutrition
algorithm, UI, CSS, native-auth or Sprint 15 work was changed. The diff is this
document plus one characterization test module. Severe findings were documented
and characterized, never opportunistically fixed. No existing test was
weakened, skipped, xfailed, renamed away or broadened.

## 19. Evidence produced by this PR

`tests/test_sprint13_nutrition_closure_discovery.py` — **26** discovery
characterization tests. Each maps to a claim above and fails if the claim stops
being true. Structural properties are proven by AST, import, URL-map or runtime
characterization rather than by copy or layout snapshots.

### What the review remediation changed in this module

| Test | Change | Why |
|---|---|---|
| `test_the_historical_backfill_wrote_a_day_key_no_query_can_match` | **replaced** by `test_the_yearless_backfill_was_repaired_by_its_direct_successor` | The old test was mechanically correct and architecturally misleading. It pinned only the transient defect, so **deleting the repair migration `9be792c80008` failed no test** — a false negative that let a withdrawn finding look live. The replacement pins the whole chain: the defect existed, the repair exists, it is the backfill's direct child, it derives the day from `created_at` → Istanbul → ISO, and it is an ancestor of the single head |
| `test_the_schema_does_not_yet_enforce_the_day_key_format` | **reframed** as `test_the_ledger_schema_pins_macro_bounds_and_not_the_day_key_format` | Its stated purpose was to justify `ck_meal_log_tarih_iso`. C14 is retired, so it now characterizes the constraint set as it is — including that `tarih` is `String(10)`, which is *why* a CHECK could only ever pin a shape |
| `test_a_malformed_day_key_is_invisible_to_the_canonical_readers` | kept; docstring corrected | The reader behaviour is still load-bearing — it is what made the transient state harmless between the two June 2026 migrations. Only the finding it hung from changed |
| `test_the_barcode_target_fabricates_a_goal_the_user_never_configured` | **extended** | Now also asserts that `daily_context`, `portion_recommendation` and `axisai_food_score` have no first-party consumer — the fact that makes F3a a P2 rather than a P1, so the severity cannot drift silently |
| `test_deleting_a_ledger_row_releases_no_stored_meal_photo` | **added** | Pins F14 in both halves: `s3_helper` exposes no deletion primitive at all, and `delete_entry` reaches for none |

Every other guard was left untouched. Nothing was weakened, deleted, skipped,
xfailed, renamed away or broadened, and no production code was modified.

### Non-vacuity, proven by mutation

Seven architecture guards were checked by temporarily violating the real
invariant, confirming the guard failed, and restoring exactly. No mutation
remains in the branch.

| Mutation | Guard | Result |
|---|---|---|
| Added a `MealLog(...)` construction to `app/services/foodcache.py` | `test_canonical_ledger_writer_inventory_is_closed` | FAILED ✅ |
| Added a `serving_id` column to `MealLog` | `test_the_builder_holds_the_provenance_the_ledger_does_not` | FAILED ✅ |
| Added `GET /api/v1/nutrition/diary/history` | `test_mobile_nutrition_route_inventory` and `test_the_mobile_surface_publishes_no_history_menu_plan_or_water` | both FAILED ✅ |
| Imported `MealLog` into `progress_summary/queries.py` | `test_progress_and_mobile_today_consume_no_nutrition_authority` | FAILED ✅ |
| Added `DELETE /meal-log/<id>` (the PR4 capability) | `test_the_web_nutrition_blueprint_publishes_no_ledger_mutation_route` | FAILED ✅ |
| **Deleted `9be792c80008`, the repair migration** | `test_the_yearless_backfill_was_repaired_by_its_direct_successor` | **FAILED ✅** — the false negative the review found is now closed |
| **Repointed `9be792c80008.down_revision` away from `df0d08c0cd24`** | same | **FAILED ✅** |

The last two are the guards this remediation exists to add. Both mutations were
made in the review environment only and restored byte-for-byte; `git status`
after restoration showed nothing but the two intended files.

### Validation run — review remediation

`python -m py_compile tests/test_sprint13_nutrition_closure_discovery.py` — OK.
`git diff --check` — clean.

| # | Command | Result |
|---|---|---|
| 1 | `pytest tests/test_sprint13_nutrition_closure_discovery.py tests/test_migration_graph.py -q -p no:randomly` | **36 passed** (38.61s) |
| 2 | `pytest tests/test_barcode.py tests/test_nutrition_routes.py tests/test_timeutil.py -q -p no:randomly` — the corrected macro-target and day-key claims | **65 passed** (80.66s) |
| 3 | `pytest tests/test_mobile_diary_mutation_api.py tests/test_mobile_diary_mutation_architecture.py tests/test_mobile_log_food_api.py -q -p no:randomly` — the mutation/delete surface F14 and C5 rest on | **55 passed** (70.10s) |

**156 tests, 0 failures.** The full 898-test discovery gate was not re-run: the
remediation diff is confined to this document and the characterization module,
and the module's imports and runtime surface are unchanged.

### Validation run — original discovery commit (`3b49fcf`)

Recorded for provenance and unchanged by this remediation. New suite:
**25 passed**. Focused regression gate, batched because the suite is slow, with
`-p no:randomly` so an ordering-sensitive failure is reproducible:

| # | Surface | Result |
|---|---|---|
| 1 | Mobile diary read + food discovery | 123 passed (81.19s) |
| 2 | Mobile ledger write + mutation | 103 passed (62.73s) |
| 3 | Web nutrition, macro pipeline, day keys | 206 passed (97.90s) |
| 4 | Food / barcode / menu writers | 114 passed (115.00s) |
| 5 | AI Coach meal staging + confirmation | 148 passed (155.26s) |
| 6 | Mobile auth boundary + migration graph | 45 passed (52.34s) |
| 7 | Progress aggregation + Sprint 12 discovery tripwire | 134 passed (65.64s) |

**898 tests, 0 failures, 0 skips introduced, 0 existing tests changed.**

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
