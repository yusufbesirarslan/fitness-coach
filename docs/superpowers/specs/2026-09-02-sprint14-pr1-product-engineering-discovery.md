# Sprint 14 PR1 — Product & Engineering Discovery

Discovery and prioritization only. No production behavior, schema, migration,
Flutter, feature flag, workflow or deployment was changed by this PR.

---

## 1. Baseline

| Field | Value |
|---|---|
| Repository | `yusufbesirarslan/fitness-coach` |
| Brief's expected base | `06a880b419dac09471a9441de124f9322faed3ae` — `docs(sprint13): close nutrition core (#273)` |
| **Actual `origin/main` at discovery start** | `3a8e981e77f79816a83b0dcd75c9c031a3d90e84` — `feat(training): add canonical native workout session writes (#276)` |
| Drift | main advanced **3 commits** past the brief. Discovery is written against `3a8e981`, per the brief's "if main has advanced, use the latest clean `origin/main`". |
| Branch | `sprint14-pr1-product-engineering-discovery` |
| Worktree | `.worktrees/sprint14-pr1-discovery` |
| Working tree at start | clean |
| Fresh-main re-check (§31) | re-fetched after evidence collection — `origin/main` still `3a8e981`. **No re-evaluation required.** |
| Alembic heads | **1** — `f5a6b7c8d9e0` (`add_workout_session_native_execution`), 40 revisions total |

### Commits between the brief's base and this discovery

```text
3a8e981 feat(training): add canonical native workout session writes (#276)
655f1f3 feat(ux): converge Coach entry points (#275)
d4544a9 fix(nutrition): serialize meal photo cleanup release (#274)
06a880b docs(sprint13): close nutrition core (#273)   <- brief's expected base
```

Sibling repository `yusufbesirarslan/axisai-mobile` (read-only, for §15/§16):
`origin/main` = `7f354e3` — `feat(training): add native first-plan generation flow (#19)`.

### Validation run (§32)

Only tests needed to validate factual claims were run — not the full regression.

```text
tests/test_feature_flag_registry.py
tests/test_workout_session.py
tests/test_workout_convergence.py           -> 165 passed
tests/test_mobile_workout_sessions_architecture.py
tests/test_workout_state_sessions.py        ->  43 passed
```

Plus a structural query used as evidence in §4: `grep -rn "checkpoint_revision" tests/`
returns **zero** hits outside the mobile suite.

---

## 2. Current product-state map

Legend: **shipped** = merged and unflagged; **dark** = merged behind an OFF flag;
**live** = actually reachable by a real production user today.

### 2.1 Platform / ops

| Item | State | Evidence |
|---|---|---|
| Deploy pipeline | shipped, CI-gated, health-gated, auto-rollback | `CLAUDE.md` "Deploy", `.github/workflows/deploy.yml` |
| Capacity invariant (Hardening PR4) | shipped | `app/services/ai_gate.py:60-112`; boot invariant counts model-gate excess **and** the DB pool |
| `ThreadReserve` / slot gauges | shipped, **not emitted in production** | gauges exist (`ai_gate.record_capacity_gauges`) but `RUNTIME_METRICS_ENABLED` is unset on the host |
| HTTP SLIs (`HttpOverload`, per-blueprint latency, auth outcomes) | shipped, **not emitted** | same cause |
| Rollout flags | 9 registered; **8 default OFF** | `app/feature_flags.py`, `docs/FEATURE_FLAGS.md` |

**The single most load-bearing operational fact in this repository:** `RUNTIME_METRICS_ENABLED=1`
appears as a prerequisite on **8 of the 9** rollout flags
(`app/feature_flags.py:151,186,222,256,351,394,442,502`) and is unset in production
(`docs/superpowers/specs/2026-08-26-sprint12-mobile-auth-today-production-rollout-readiness.md` §26
item 2). Every staged activation in the backlog is therefore blocked on one unmet,
shared, one-line prerequisite plus a baseline capture.

### 2.2 Web surfaces

| Domain | State | Notes |
|---|---|---|
| Global navigation | **live** — Today · Plan · Coach · Progress | UX-1 PR2 (`a678733`) made v2 chrome unconditional: `app/hooks.py:147` `"nav_v2": True`. `UIUX_NAV_V2_ENABLED` no longer selects chrome (`docs/ROLLOUT.md:107`). |
| Today (`/`) | **live = legacy `index.html`** | `UIUX_TODAY_V2_ENABLED` OFF ⇒ `app/blueprints/tracking.py:114` renders `templates/index.html`. The IA-aligned `today.html` + `today_presenter` + `today_facts` are **dark**. |
| Plan (`/training`) | **live = legacy `training.html`** | `UIUX_PLAN_V2_ENABLED` OFF (`app/blueprints/training.py:150`). The legacy page is **already server-authoritative** — `static/training.js` reads `activeTodayPlan`/`currentWorkoutState` from the server and contains no `new Date()`/`getDay()`/localStorage completion. |
| Weekly program card | dark | `WEEKLY_PROGRAM_UI_ENABLED` OFF |
| Coach (`/coach`) | **live = legacy `coach.html`** | Thin page auto-opening the one widget; opts into the page-owned launcher via `data-coach-launcher` (UX-1 PR3, `#275`), so a closed conversation is reopenable. `UIUX_COACH_PAGE_V2_ENABLED` OFF. |
| Coach streaming | **live and degraded** | see §4 F-2 |
| Coach plan editing | dark | `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` OFF; `AI_ADAPTIVE_PLAN_CONTEXT` OFF |
| Nutrition | **live and complete** | Sprint 13 PR1–PR5 closed: canonical targets, provider-truth diary, web meal correction (`#269`), meal-photo release serialization (`#274`) |
| Progress | **live and complete** | summary (`#215`), Axis Insights (`#216`), physique (`#219`), history (`#220`), IA shell (`#212`) |
| Pump Check + gallery | **live** | `#244`, `#256` |
| Weekly check-in | **live** | `app/blueprints/tracking.py:258-330`; web-only by design |
| Workout **execution** (sets during a workout) | **live but ephemeral** | `FITX_WORKOUT_SESSIONS_ENABLED` OFF ⇒ `/workout/session/*` 404; the client falls back to an in-memory session: *"The session is ephemeral (no partial state persists across reloads)"* — `static/training.js:298-299` |

### 2.3 Native (mobile) reality — §15

Backend `/api/v1` is one flag. `app/__init__.py:329-333` registers the entire mobile
blueprint only when `MOBILE_AUTH_ENABLED` is on. 27 endpoints live behind it.

| Layer | Truth |
|---|---|
| Backend flag in production | **ON** since 2026-08-26 (`docs/ROLLOUT.md:111`, readiness doc §2/§26). The repo default is OFF and the repository cannot answer the question by itself. |
| Registry record | **stale** — `app/feature_flags.py:519` still `LIFECYCLE_BLOCKED` with the prerequisite "PR4 (capacity hardening) merged", which merged as `34f8dc79`. `docs/ROLLOUT.md:111` records the truth; `docs/FEATURE_FLAGS.md` does not. |
| Native compile-time flag | `AXISAI_NATIVE_AUTH_ENABLED` **defaults false** (`lib/core/config/native_auth_rollout.dart`) and no ON build has been distributed |
| Live native repositories (`AppComposition.configured`, auth ON) | Today, Plan, WorkoutDetail, Nutrition, TrainingPreferences, PlanGeneration, PumpCheck |
| **Unavailable** native repositories in the same build | **WorkoutSession**, **ProgressSummary** (`lib/app/composition/app_composition.dart:126-131`) |
| Native Coach | `lib/features/coach/presentation/coach_screen.dart` only — **no data layer at all** |
| Fixtures | confined to `AppComposition.development`; production composition wires `Unavailable*`, never `Fixture*` |

**Consequence:** in the only build that could ship, two of the four native primary
destinations (Coach, Progress) have no live data path, and native workout execution
is `Unavailable`. That is not a live defect — nobody has this build — but it means
native distribution is blocked on **product completeness**, independently of the soak
gate. This *reinforces* the recorded Sprint 15 ownership rather than overturning it
(§4 of the brief; test not met — see §7).

### 2.4 Recent-main consequences — §7

| Merge | Capability unlocked | Gap it exposed as the next bottleneck |
|---|---|---|
| `#273` Sprint 13 closure | Nutrition declared contract-complete at N1–N10 | Nutrition stops being the frontier; what remains is P2/P3 product capability, not core |
| `#274` meal-photo cleanup serialization | delete no longer races the S3 release | none new; automatic drain scheduling stays a recorded P2 |
| `#275` Coach entry convergence | Coach is a real destination with contextual entries; the global FAB is gone | Coach's *content* is now the product surface, and its delivery is degraded (F-2) |
| `#276` native workout session writes | native execution became **durable**: revision-gated snapshots, replay identity, and a completion precondition that refuses a stale revision | **the browser half of the same flag did not move.** One table, one identity space, two divergent execution contracts (F-1) |
| `#265` idempotent native plan generation | native can create a first plan | native still cannot *execute* it (WorkoutSession `Unavailable`) |

---

## 3. Candidates considered and rejected before scoring

Recorded so the ranking is not mistaken for the whole search.

* **Nutrition post-closure backlog** (all 12 items). Every one is classified P2/P3 in
  `2026-08-30-sprint13-pr1-nutrition-closure-discovery.md` §21 and none changed
  severity at `3a8e981` — see §12. Sprint 13 deliberately moved them out; debt
  existing is not a reason to reopen a closed core.
* **`MOBILE_AUTH_ENABLED` / native-auth rollout.** Explicitly owned by the **Sprint 15
  launch-hardening resume gate** (readiness doc §33.2) and by Sprint 13 §21 ("Sprint 15
  native-auth / ops"). §7 states why the fresh evidence confirms rather than overturns
  that sequencing.
* **Observability activation as the sprint objective.** The highest architectural
  leverage of anything in the repository (8/9 flags blocked on it) but it is an `.env`
  value plus a baseline capture — operator work, not a sprint — and it delivers zero
  user-visible value on its own (§9). It is folded into the recommended candidate as
  the reliability PR instead.
* **UI polish / premium refinement.** Excluded by §20; no fresh evidence makes visual
  quality the primary bottleneck.

---

## 4. Findings that drove candidate generation

### F-1 — Two divergent workout-execution contracts on one identity space — **P2** (dark), hard activation blocker

`#276` gave the native surface a durable, revision-gated checkpoint. The browser
surface behind **the same flag** still writes the pre-PR5 heartbeat.

| | Browser | Native |
|---|---|---|
| Checkpoint route | `POST /workout/session/<public_id>/checkpoint` (`app/blueprints/training.py:556-562`) | `PUT /api/v1/training/workout-sessions/<ref>/checkpoint` |
| Service | `checkpoint_session(user_id, public_id)` — **no body, no snapshot, no revision** (`app/services/workout_session/service.py:250-267`) | `advance_checkpoint(...)` — base-revision-gated UPDATE writing `checkpoint_revision`, `checkpoint_data`, fingerprint and replay key (`app/services/workout_session/queries.py:284-327`) |
| Columns written | `last_activity_at` only (`queries.py:249-265`) | revision + snapshot + fingerprint + key + timestamps, atomically |
| Completion precondition | `CompleteWorkoutCommand(...)` built **without** `expected_checkpoint_revision` (`app/blueprints/training.py:274-300`) | passes `expected_checkpoint_revision` (`app/services/mobile_workout_sessions/service.py:376-377`) |
| Session projection | `SessionView.to_dict()` publishes **no** checkpoint field (`app/services/workout_session/models.py:172-203`) | `projection.py:40` publishes `session_ref` = the same `public_id` |

Identity is shared: the native `session_ref` **is** `WorkoutSession.public_id`
(`mobile_workout_sessions/service.py:183,268,376`). The two contracts address the
same rows.

Failure scenarios, **on activation** of `FITX_WORKOUT_SESSIONS_ENABLED`:

1. A browser workout still loses every logged set on reload — the flag ON changes
   nothing for the web client, because its checkpoint carries no set data.
2. A session the phone has been checkpointing (revision *n*) can be completed from the
   browser, which declares no revision, so `#276`'s precondition never fires and the
   progress written since is silently discarded. The precondition exists; the browser
   simply never invokes it.
3. `/workout/session/current` hands the browser a session whose durable snapshot it
   cannot see, let alone restore.

Coverage: **zero** — `grep -rn "checkpoint_revision" tests/` matches only the mobile
suite. `docs/ROLLOUT.md:106` describes flag 6 as opening "TWO surfaces at once"
without recording that the two surfaces now disagree.

Severity is **P2, not P1**, deliberately: the flag is OFF, so per §15 this is dark
code, not a live defect. It is nevertheless a hard blocker on flag 6 — that flag
cannot be activated without shipping two different products on one table.

### F-2 — `/ask/stream` no longer streams — **P2** (live, unflagged)

`docs/STREAMING.md:4` states the contract: *"the answer arrives token-by-token"*, with
*"typing indicator until the first delta"* (`:47`). Current main does not do this.

In `app/services/ai_stream.py`, every `delta` from the provider is appended to a local
`buffered` list (`:245`) and **nothing is yielded**. Text reaches the client only from
`_flush_buffered` (`:287`, `:304`) or `_emit_text` (`:375`), all of which run **after**
the turn's `final` message. The OpenAI fallback path is a non-streaming call chunked
after the fact (`:370`). The widget uses `/ask/stream` exclusively for chat
(`static/coach_widget.js:362`).

Net user experience: `meta`, then silence for the whole model turn, then the entire
reply in fast synthetic chunks, then `done`. The SSE protocol, the rAF-throttled
incremental renderer, the Stop button's mid-stream value and the `interrupted=True`
partial-save are architecturally inert.

Provenance and intent, so this is not mistaken for a bug nobody chose: buffering was
introduced deliberately by `849eb0f` (`#237`) — *"'I've added' persistence'tan önce
gidemez"* — to keep pre-tool prose from being published before a mutation commits, and
widened by `2cfd008` (`#262`) and `906b339` so
`coach_confirmation.grounded_provider_reply` can suppress a confirmation request the
server cannot substantiate. The intended rule was "hold only when a write tool may
run"; that is undecidable until `final` arrives, because a Bedrock turn's `tool_use`
blocks are only known then. So the implementation degrades to "hold always", including
the no-tool and read-only-tool cases the docstring says should stream.

Severity **P2**: nothing is fabricated, lost or wrong. It is a documented-behavior
regression on the primary AI destination, not a correctness defect — recorded at its
real severity rather than inflated (§27).

### F-3 — Production Today contradicts the chrome and misstates a shipped capability — **P3** (live)

`templates/index.html:76-80` renders **Scan Barcode** as a `disabled` tile with a
`Soon` badge (`index.qa_soon`). Barcode logging is shipped and enabled on `/nutrition`
(`templates/nutrition.html:359-362`, `data-action="logScanBarcode"`, backed by
`GET /api/food/barcode` at `app/blueprints/food.py:73`). The product's landing screen
tells the user a live capability does not exist yet.

`templates/index.html:393` decides the page's single dominant action from
`new Date().getHours()` — the **device** clock — while the entire server stack is
canonically Europe/Istanbul (`app/timeutil`, "TEK gün/saat kaynağı"). A user whose
device clock or timezone differs gets a different next action than the server's day
model would produce.

Both are exactly what the IA contract's locked Today ownership removes
(`docs/PRODUCT_IA.md`, "Home / Today ownership (locked)"). Neither is a data defect.

### F-4 — Registry/runbook disagreement on `MOBILE_AUTH_ENABLED` — **P3** (documentation integrity)

`app/feature_flags.py:519` records `LIFECYCLE_BLOCKED` on a prerequisite that merged
(`34f8dc79`), and `docs/FEATURE_FLAGS.md` reproduces it. `docs/ROLLOUT.md:111` records
the truth. The drift test (`tests/test_feature_flag_registry.py`) compares the registry
against `FEATURE_FLAGS.md`, not against reality, so the two stale documents agree with
each other and pass. Secondary stale rows in the same files: `ROLLOUT.md:102-103` still
describe flags 2 and 3 as "reachable through the legacy shell's Home/Training tab"
after UX-1 PR2 deleted that shell, and `FEATURE_FLAGS.md` still says "the everyday
coach entry point remains the floating widget" after `#275` removed it.

---

## 5. Candidates

Three candidates. Each is supported by the evidence in §4.

---

### Candidate A — Durable, single-authority workout execution

#### Objective

A workout in progress survives a reload, an app switch and a device change, under
**one** revision-gated execution authority shared by the **browser web transport**
and the **native `/api/v1` server transport** — and the flag that governs both
surfaces becomes activatable.

> **Independent-review amendment (P2-A).** The original wording said "the browser
> and the native client". The actual Flutter `WorkoutSession` client/repository is
> **out of scope for Sprint 14** — `AppComposition.configured` wires
> `UnavailableWorkoutSessionRepository` (§2.3), and §11 already excludes any
> Flutter change. The two things Sprint 14 converges are two **server transports**
> over one domain. Building the native client remains Sprint 15 / later mobile
> work in `axisai-mobile`.

#### User problem

Today, in production, a user who reloads `/training` mid-workout loses every set they
logged. The client says so in its own source: *"The session is ephemeral (no partial
state persists across reloads)"* (`static/training.js:298-299`). The durable remedy is
already built and paid for — it is dark, and since `#276` the two halves behind that
one flag no longer agree with each other (F-1).

#### Current evidence

```text
files    app/services/workout_session/{service,queries,models}.py
         app/services/mobile_workout_sessions/{service,projection}.py
         app/services/workout_completion/service.py
         app/blueprints/training.py, app/blueprints/mobile_workout_sessions.py
         static/training.js, static/workout_state_client.js
routes   POST /workout/session/{start,<id>/resume,<id>/checkpoint,<id>/abandon}
         GET  /workout/session/current
         POST /workout/complete
         POST|PUT /api/v1/training/workout-sessions[/<ref>/...]
services workout_session (shared authority), workout_completion (single mutation owner),
         workout_state (contract_version resolver)
tests    test_workout_session.py, test_workout_session_pg.py, test_workout_convergence.py,
         test_workout_state_sessions.py, test_mobile_workout_sessions_{api,architecture,pg}.py
flags    FITX_WORKOUT_SESSIONS_ENABLED (OFF, staging_only, decision=enable)
schema   migration f5a6b7c8d9e0 -- checkpoint_revision / checkpoint_data /
         checkpoint_at / checkpoint_idempotency_key / checkpoint_fingerprint /
         workout_ref / plan_lineage_id / plan_mutation_version ALREADY EXIST
gaps     F-1 in full; no session-lifecycle metric (named in the flag's own
         observability field); zero web-side coverage of checkpoint_revision
```

#### Why now

`#276` is the direct cause. It made native execution durable and, in doing so, made
the browser half of the same flag semantically obsolete. The flag cannot be activated
in either direction until they converge, and it is the only remaining rollout flag
whose decision is `enable` and whose capability is a genuine user capability rather
than a presentation change.

#### Proposed vertical slice

* Extend the shared session authority so a checkpoint is one contract: base revision +
  bounded, server-validated snapshot + replay key, reusing `advance_checkpoint`
  unchanged.
* Publish `checkpoint_revision` and the snapshot identity in `SessionView` so the
  browser can declare a base and restore progress.
* Make browser completion declare `expected_checkpoint_revision` and honour the
  `reason="revision"` refusal `#276` already implements.
* Converge the browser client: checkpoint the real set data, restore from the server
  snapshot on load, delete the "ephemeral" fallback.
* Emit the session lifecycle counter the flag record names as missing, and prove the
  cross-surface handoff and the PostgreSQL races.

#### Explicit exclusions

Native (Flutter) client work — that is
`axisai-mobile#mobile-training-pr5-session-write-contracts` and belongs to its own
repository's sequence. Flag activation in production. Any workout-UI redesign.
Adaptive coaching. Progress. Nutrition. Any new column.

#### Risk

```text
product        low    -- OFF path must stay inert (amended P2-B); enforced as S14-10
data           low    -- no migration; expand-only columns already applied
security       low    -- the client snapshot is the only new input surface;
                         bounded + typed + ownership re-enforced server-side
migration      none   -- f5a6b7c8d9e0 already supplies every column
concurrency    HIGH   -- the core of the work; one conditional UPDATE decides,
                         and PostgreSQL proof is mandatory (SQLite cannot show it)
rollout        low    -- one existing flag, OFF, instant .env rollback
compatibility  medium -- SessionView gains fields (additive); no field is removed
```

#### Dependencies (shipped authority reused)

`workout_session` (Sprint 7 PR3 lifecycle + `#276` columns), `workout_completion` (the
single canonical completion transaction shared by browser, native and the AI-coach
tool), `workout_state` (contract_version resolver), `runtime_metrics`,
`WORKOUT_CHECKPOINT_RATELIMIT` (already defined, `app/config.py:84`), the existing CSRF
and `@require_auth` boundaries.

#### Estimated PR shape

```text
PR1 discovery (this document)
PR2 canonical execution contract (backend only)
PR3 browser client convergence
PR4 reliability, concurrency proof and lifecycle observability
PR5 staged-activation readiness and closure
```

---

### Candidate B — Coach response delivery restored under the grounding contract

#### Objective

The Coach destination publishes model output incrementally again, without weakening
any grounding invariant that `#237` / `#262` / `906b339` established.

#### User problem

Every coach turn shows a typing indicator for the entire model turn — tens of seconds
when tools run — then the whole answer at once. The Stop button cannot stop a partial
answer because no partial answer exists, and the interrupted-partial-save path can
never fire. `#275` made Coach a primary destination, so this is now the flagship AI
surface's whole perceived behaviour.

#### Current evidence

```text
files    app/services/ai_stream.py:236-304 (buffer), :71-77 (_flush_buffered),
         :340-376 (_stream_openai_fallback, _emit_text)
         app/services/coach_confirmation.py:128-145
         static/coach_widget.js:362 (the only chat transport)
routes   POST /ask/stream
docs     docs/STREAMING.md:4,31,37,47 -- the violated contract
history  849eb0f (#237) introduced the buffer; 2cfd008 (#262) and 906b339 widened it
flags    none -- unconditional and live
gaps     no first-delta latency signal exists, so the regression is unmeasurable
```

#### Why now

`#275` promoted Coach from a floating utility to a primary destination. The product's
identity claim is "premium AI fitness coach"; the primary AI surface currently behaves
like a slow blocking form post.

#### Proposed vertical slice

Make publication incremental and grounding-safe: stream the segments the grounding
rule can never rewrite, hold only what it can, and emit a typed replace/suppress frame
when grounding does intervene. Add a first-delta latency signal so the property is
observable and cannot regress silently again. Update `STREAMING.md` to whatever the
new truth is.

#### Explicit exclusions

Prompts, model selection, provider fallback policy, quotas, moderation, persistence,
plan-mutation tools, `AI_ADAPTIVE_PLAN_CONTEXT`, and any Coach visual redesign.

#### Risk

```text
product        medium -- the grounding invariants are the reason the buffer exists;
                         weakening one would publish an unsubstantiated confirmation
data           none
security       low
migration      none
concurrency    low
rollout        medium -- unflagged and live; needs its own kill path
compatibility  low    -- the SSE frame vocabulary may gain one additive frame type
```

#### Dependencies

`coach_confirmation`, `ai_pipeline`, `ai_stream_concurrency_gate`, `runtime_metrics`.

#### Estimated PR shape

```text
PR1 discovery
PR2 incremental-publish contract + grounding compatibility (server)
PR3 client frame handling + observability
PR4 closure
```

---

### Candidate C — UX-1 PR4: Today convergence

#### Objective

`/` becomes the canonical Today the production chrome already promises, reading the
same canonical state the native Today endpoint reads.

#### User problem

The chrome says Today; the page is the legacy dashboard the IA contract diagnoses by
name. It advertises a shipped capability as "Soon" (F-3), picks its one dominant action
from the device clock (F-3), and leads with calorie totals and XP against locked IA
principles 1, 2, 3 and 10.

#### Current evidence

```text
files    templates/index.html (830 lines; :76-80 Soon tile, :393 device clock)
         templates/today.html + app/today_presenter.py + app/services/today_facts.py (dark)
         app/services/mobile_today.py (the native projection over the same authority)
routes   GET /
docs     docs/PRODUCT_IA.md sections H, K ("PR4+") and L, "Home / Today ownership (locked)"
flags    UIUX_TODAY_V2_ENABLED (OFF, shipped_dark, decision=enable)
gaps     today.html implements part of the locked Today ownership (next action,
         compact status, quick log) but not the Daily Coach Brief or Coach Insight
```

#### Why now

UX-1 PR1 (`#266` contract), PR2 (`a678733` chrome) and PR3 (`#275` Coach entries) are
merged. PR4 is the next item in a sequence whose ownership is already locked, and it is
the last place where production chrome and production content disagree.

#### Proposed vertical slice

Point `/` at the canonical Today read model both surfaces share; retire the demoted
modules the contract lists; remove the false "Soon" claim; make the dominant action
server-decided.

#### Explicit exclusions

The Daily Coach Brief / Coach Insight layers (they need a new AI authority — a separate
sprint), Progress internals, Nutrition, navigation, native.

#### Risk

```text
product        medium -- replaces the default landing page for every user
data           none
security       none
migration      none
concurrency    none
rollout        low    -- an existing flag, if the work stays inside it
compatibility  low
scope          HIGH   -- "Today" invites the Coach Brief, which is a different sprint
```

#### Dependencies

`today_facts`, `today_presenter`, `workout_state`, `nutrition_targets`,
`progress_summary`, `app/nav.py`.

#### Estimated PR shape

```text
PR1 discovery
PR2 canonical Today composition (server)
PR3 Today page convergence
PR4 activation readiness / closure
```

---

## 6. Ranking

Higher is better for the first six. **Lower is better** for the last two.

| Dimension | A — Workout execution | B — Coach delivery | C — Today convergence |
|---|:--:|:--:|:--:|
| Launch impact | **5** | 4 | 3 |
| User-visible value | 4 | **5** | 4 |
| Correctness / risk reduction | **5** | 2 | 2 |
| Architectural leverage | **5** | 3 | 3 |
| Implementation boundedness | 4 | 3 | **4** |
| Evidence confidence | **5** | **5** | **5** |
| *Regression risk* (lower better) | 3 | **2** | 3 |
| *Scope-expansion risk* (lower better) | **2** | 3 | 4 |

Tradeoffs, stated rather than averaged away:

* **B touches the most users most often** — every coach turn, versus A's "only when you
  reload mid-workout, until the flag flips". If frequency of contact were the sole
  criterion, B would win.
* **A is the only candidate on the §8 launch-critical list.** It is the only one that
  involves data loss, an unrecoverable partial failure, and a cross-surface
  disagreement. B's harm is perceived latency; C's is a misleading label. §8 makes
  correctness blockers outrank both.
* **C is the most bounded and the easiest to scope-creep.** "Today" pulls the Daily
  Coach Brief in with it, which needs an authority that does not exist.
* **A carries the highest concurrency risk** — that risk *is* the work, and it is the
  same risk `#276` already discharged once on the native half, with a proven pattern to
  copy.

---

## 7. Recommendation

```text
RECOMMENDED SPRINT 14 OBJECTIVE:
A workout in progress becomes durable and single-authority across the browser web
transport and the native /api/v1 server transport -- one revision-gated execution
contract, no surface able to silently discard another's progress, and
FITX_WORKOUT_SESSIONS_ENABLED made activatable with the observability its own
record says it lacks.

Out of scope, explicitly: the Flutter WorkoutSession client/repository (Sprint 15
or later, in axisai-mobile) and the staged production rollout (PR5 readiness only).
```

> **Independent-review amendment (P2-A).** Amended from "across the browser and the
> native client" for the reason recorded above. No candidate was re-ranked and no
> criterion was weakened; the objective's *content* is unchanged, only its wording
> now matches the scope §11 already declared.

### Why it wins

**Against B.** B's defect is real, live and frequent, but it is perceived latency: no
state is wrong, nothing is lost, nothing disagrees. A's is the only candidate that
appears on the launch-critical list at all — a user loses logged work today
(`static/training.js:298`), and on activation one surface can silently discard
another's committed progress (F-1). §8 is explicit that correctness blockers outrank
improvements that are not. B is nominated below as the sprint's first follow-on and
should not wait long.

**Against C.** C is presentation. Its two concrete defects — a false "Soon" badge and a
device-clock action — are P3. §20 forbids making presentation the sprint's objective
absent fresh evidence that UI is the primary bottleneck, and there is none. C's own
contract (`PRODUCT_IA.md`, PR4+) already schedules it as "ownership is already locked,
timing is not".

**On its own merits.** A is the only candidate that (i) closes a live data-loss path,
(ii) removes a cross-surface disagreement created by the newest merge on main,
(iii) reuses an existing durable authority and needs **no new table, column, migration,
flag, job, Redis key, S3 object or environment variable**, and (iv) ends with a
shipped-dark capability of real user value becoming activatable. It converts "we built
durable workout execution twice, incompatibly, and shipped neither" into one contract
that can be turned on.

**Why not the mobile-auth rollout, restated against §4.** The fresh evidence points the
other way. `AppComposition.configured` wires `UnavailableWorkoutSessionRepository` and
`UnavailableProgressSummaryRepository` even with native auth ON, and
`lib/features/coach` has no data layer — so two of four native primary destinations are
dead ends in the only build that could ship. The recorded Sprint 15 sequencing is
therefore **more** correct than when it was written, not less. Candidate A also happens
to be the backend prerequisite for the native workout screen, so Sprint 14 makes
Sprint 15 more shippable without absorbing it.

---

## 8. Formal acceptance criteria

Objectively testable. Each names an observable invariant, not a feeling.

| ID | Criterion |
|---|---|
| **S14-1** | With `FITX_WORKOUT_SESSIONS_ENABLED=1`, the browser checkpoint route persists a bounded snapshot and advances `checkpoint_revision` by exactly one, gated on the caller's declared base revision. A request declaring a stale base mutates **no** column and is refused with a typed outcome. |
| **S14-2** | Every session projection published by **either** surface carries `checkpoint_revision` and the snapshot identity. A structural test over `SessionView.to_dict()` and `mobile_workout_sessions.projection` fails if a surface publishes a session without them. |
| **S14-3** | `POST /workout/complete` declares `expected_checkpoint_revision` whenever a session id is supplied. A completion built on a stale revision is refused with `reason="revision"` and writes no `PumpCheck`, `WorkoutLog`, completion marker, XP, quest or activity row. |
| **S14-4** | Cross-**transport** regression: a session checkpointed through the native `/api/v1` transport to revision *n* and then completed through the browser web transport at revision *n-1* is refused; and the mirror case (browser web checkpoint, native `/api/v1` completion at a stale revision) is refused. Both assert zero rows written. *(Amended P2-B: "surface" read as two shipping clients; these are two server transports over one row.)* |
| **S14-5** | With the flag ON, a full page reload mid-workout restores every completed set from the server snapshot. The assertion is on restored set data, not on a request being made. |
| **S14-6** | The snapshot is server-validated and bounded: over-size, over-count and schema-invalid payloads are refused with a typed error and are never truncated, coerced or partially stored. |
| **S14-7** | A `[WORKOUT_SESSION]` lifecycle counter (`started` / `resumed` / `checkpointed` / `abandoned` / `completed` / `revision_conflict`) is emitted through `runtime_metrics` with fixed-cardinality dimensions and no user identity, closing the "No session-lifecycle metric exists" gap named in the flag's own `observability` field. |
| **S14-8** | Proven on PostgreSQL, not SQLite: two writers on one base revision produce exactly one winner and one refusal; a completion racing a checkpoint leaves exactly one terminal outcome; the `uq_workout_session_active_owner` invariant is never violated. |
| **S14-9** | Alembic remains at a **single head** and the sprint adds **no** migration, table or column. `f5a6b7c8d9e0` is sufficient; a PR that adds one has left scope. |
| **S14-10** | With `FITX_WORKOUT_SESSIONS_ENABLED = 0` the feature is **inert**: every session route is absent (404), no session state is fabricated (no `WorkoutSession` row is created or read into any response), the legacy `/workout/complete` path is behaviourally unchanged and requires no revision, the browser issues no session request, and no workout-session UI state or markup becomes active. Normalized legacy `/training` behaviour is unchanged. *(Amended P2-B: the original criterion demanded the rendered `/training` output be **byte-identical** to `3a8e981`. That is not testable — the page embeds process-specific `_BOOT_TS` cache-bust values, so two renders of unmodified code already differ. Cache-bust token differences are ignored; the invariant is inertness, not byte equality.)* |
| **S14-11** | No production flag value is changed by any PR in this sprint. Activation is a separate, operator-run, documented step. |

---

## 9. Proposed PR sequence

```text
PR1  discovery                     (this document)                      [no dependency]
PR2  canonical execution contract  backend authority + transport        [depends: PR1]
PR3  browser client convergence    adapter/UI                           [depends: PR2]
PR4  reliability + observability   concurrency proof, lifecycle metric  [depends: PR2, PR3]
PR5  staged-activation readiness   runbook, staging exercise, closure   [depends: PR4]
```

### Dependency edges, stated explicitly (§29)

* **PR3 after PR2** — adapter after authority. The browser cannot declare a revision
  before the server publishes one.
* **PR4 after PR3** — the cross-surface and reload regressions in S14-4 / S14-5 need
  both halves present to be provable rather than mocked.
* **PR5 after PR4** — rollout after reliability proof. No activation readiness may be
  claimed before the PostgreSQL concurrency evidence and the lifecycle metric exist.
* No cleanup precedes correctness; the "ephemeral session" fallback is removed in PR3
  **as part of** the convergence, not as a separate tidy-up.

### Per-PR responsibility and rollback (§30)

| PR | One reviewable responsibility | Rollback |
|---|---|---|
| PR2 | The server's checkpoint/completion contract | `git revert` only. Dark flag, additive columns already applied, nothing written on the OFF path. |
| PR3 | The browser's use of that contract | `git revert` only. Reverting alone restores the ephemeral client; PR2 stays inert while the flag is OFF. |
| PR4 | Proof and instrumentation | `git revert` only. The metric sits behind `RUNTIME_METRICS_ENABLED`; reverting emits nothing. |
| PR5 | Documentation and readiness | `git revert` only, plus **flag disable** (`FITX_WORKOUT_SESSIONS_ENABLED=0`, `docker compose up -d`) if activation has occurred. |

**No PR in this sequence requires manual data repair to roll back.** Checkpoint rows
are additive; the read contract at `contract_version=1` ignores them and never deletes
them (the property `docs/FEATURE_FLAGS.md` already records for flag 6). If any proposed
change would make that untrue, it is an architecture risk to raise **before**
implementation, not a rollback step to write down.

---

## 10. Architecture

| Question | Answer |
|---|---|
| Authority to reuse | `app/services/workout_session` (lifecycle + `advance_checkpoint`), `app/services/workout_completion` (the single canonical completion transaction shared by browser, native and the AI-coach tool), `app/services/workout_state` (contract_version resolver), `app/services/runtime_metrics` |
| New durable authority required? | **No.** The durable authority is `advance_checkpoint`; the sprint gives the browser access to it. |
| Schema change expected? | **No.** |
| Migration expected? | **No.** `f5a6b7c8d9e0` already added `checkpoint_revision`, `checkpoint_data`, `checkpoint_at`, `checkpoint_idempotency_key`, `checkpoint_fingerprint`, `workout_ref`, `plan_lineage_id`, `plan_mutation_version` — expand-only and inspector-guarded. Single head confirmed. |
| Feature flag? | **No new flag.** `FITX_WORKOUT_SESSIONS_ENABLED` already governs both surfaces. |
| Operational dependency? | **None new.** No scheduled job, no worker behaviour, no Redis key, no S3 object, no new environment variable. The lifecycle metric rides the existing `runtime_metrics` buffer + daemon flush (never inline — `put_metric_data` is a network call). Rate limiting reuses `WORKOUT_CHECKPOINT_RATELIMIT` (`app/config.py:84`). |
| Deployment sequencing? | Ordinary. No flag flip, no staged deploy, no data backfill. |

### Security / auth gate (§18) — only where relevant

| Concern | Disposition |
|---|---|
| Ownership | Already enforced: every session query is `user_id`-scoped and re-derived from `current_user.id` / the mobile principal; no client-supplied `user_id`, status, timestamp or version is trusted. Unchanged. |
| Authentication | `@require_auth` (web) and `require_mobile_auth` (native), both unchanged. The flag is a rollout gate, not an authorization gate — a property documented at `app/blueprints/training.py:491-494` and preserved by S14-10. |
| Authorization | `advance_checkpoint`'s UPDATE predicate includes `user_id`, so a cross-user checkpoint matches zero rows by construction. |
| CSRF | The browser checkpoint gains a request body; it stays a POST behind the existing two-layer CSRF gate, and `static/csrf.js` already attaches `X-CSRFToken` to same-origin `fetch`. |
| Rate limits | The browser route must adopt `WORKOUT_CHECKPOINT_RATELIMIT`, which the native route already carries — a checkpoint is now a write with a payload, not a heartbeat. |
| Idempotency / replay | `checkpoint_idempotency_key` and `checkpoint_fingerprint` exist; the browser must supply them, so a retried checkpoint is a no-op rather than a second revision. |
| Provider trust | Not applicable — no provider call is involved. |
| Secret boundaries | Untouched. |

This is deliberately not a generic security audit (§18).

---

## 11. Explicit non-goals

Sprint 14 must not absorb:

* Any Flutter change. The native client for `#276` is
  `axisai-mobile#mobile-training-pr5-session-write-contracts` and belongs to that
  repository's sequence.
* Activation of any production flag, including `FITX_WORKOUT_SESSIONS_ENABLED` and
  `RUNTIME_METRICS_ENABLED`. Sprint 14 makes activation *possible*; deciding to
  activate is a separate operator step.
* `MOBILE_AUTH_ENABLED` / `AXISAI_NATIVE_AUTH_ENABLED` rollout, the authenticated
  production smoke, and the contiguous 24-hour soak — the Sprint 15 launch-hardening
  resume gate (readiness doc §33.2).
* The Coach streaming restoration (Candidate B) — nominated as the next sprint, not
  folded in.
* Today / Home convergence (Candidate C) and any UX-1 PR4 work.
* Adaptive coaching, plan-mutation tool activation, Sprint 12 PR5 Today integration.
* Any Nutrition work. Sprint 13 is closed; its backlog stays deferred (§12).
* Progress, Pump Check, gamification, Community.
* Workout-screen visual redesign. UI changes in PR3 are strictly what durable restore
  requires.
* Any migration, new table, new column, new flag, new job, new environment variable.
* The god-module refactor (`ai_coach.py`, `social.py`, `tracking.py`) — an accepted
  structural observation, not a discrete defect
  (`docs/reports/2026-08-28-codebase-triage-remediation.md`, finding 8).

---

## 12. Existing backlog disposition (§14)

### Sprint 13 post-closure backlog — all items re-checked at `3a8e981`

| Item | Recorded | Disposition at `3a8e981` | Evidence |
|---|---|---|---|
| native nutrition history | P3 | **remains deferred**, unchanged | no new consumer; native Nutrition is live but history was never contracted |
| mobile menu adapter | P3 | **remains deferred**, unchanged | adapter still absent |
| validated nutrition intelligence domain | P3 | **remains deferred**, unchanged | PR5 retired the unowned scores; no product ask replaced them |
| web committed-entry slot move | P3 | **remains deferred**, unchanged | `mobile_diary_mutation.set_slot` still has no web transport |
| past-day correction | P3 | **remains deferred**, unchanged | route policy unchanged |
| meal photos on mobile | P3 | **remains deferred**, unchanged | mobile payload still has no photo field |
| account-erasure object-store lifecycle | P2 | **remains deferred at P2** | `#274` serialized the *release*; it did not add a first-party delete-account path, so exposure is unchanged |
| keyless raw HTTP replay | P2 | **remains deferred at P2** | no new keyless writer; no new exposure |
| manual web/mobile clamp mismatch | P2 | **remains deferred at P2** | measured, not fixed; no code moved |
| deprecated `POST /api/food/barcode/add` | P2 | **remains deferred at P2** | the C13 evidence bar (sunset + elapsed period + logs) is still unmet |
| mobile `UnreleasableStoredObject` envelope | P2 | **remains deferred at P2** | mobile DELETE still maps to a generic retryable failure |
| automatic `MealPhotoCleanup` drain scheduling | P2 | **remains deferred at P2** | `#274` made same-request release correct; it did **not** add a scheduler. Severity unchanged — a drain that never runs is still bounded by operator CLI recovery. |
| `MOBILE_AUTH_ENABLED` rollout | P2 ops, Sprint 15 | **remains deferred to Sprint 15**; see §7 | fresh mobile-composition evidence strengthens the deferral |

**No Sprint 13 backlog item changed severity, and none moves into Sprint 14.** Debt is
not promoted silently, and is not promoted at all here (§14, §27).

### New findings from this discovery

| ID | Severity | Disposition |
|---|---|---|
| F-1 two divergent execution contracts | **P2** (dark; hard activation blocker) | **moves into Sprint 14** — it is the recommended objective |
| F-2 `/ask/stream` does not stream | **P2** (live, unflagged) | **deferred**, nominated as the next sprint's objective |
| F-3 Today's "Soon" barcode tile + device-clock action | **P3** (live) | **deferred** to UX-1 PR4 (Candidate C) |
| F-4 registry says `MOBILE_AUTH_ENABLED` is blocked; production has it on | **P3** (documentation integrity) | **deferred** to the Sprint 15 gate, which owns that flag's lifecycle record. Not silently promoted. |

### Sprint 13 closed findings (§21 of the brief)

`F1, F2, F3a, F4, F5, F6 safety, F7, F8, F9, F10, F12, F14, F15, N1–N10` were read as
history only and are **not reopened**. No current-main regression against any of them
was found; the targeted runs in §1 pass. The 2026-08-14 and 2026-08-02 triage findings
were likewise verified closed in code, not merely in a report:
`_renew_provider_tokens` performs provider I/O with no transaction or lock held
(`app/services/mobile_auth.py:505-535`), mobile login and refresh consume
`blocking_concurrency_slot` (`:201`, `:512`), and `model_concurrency_slot` acquires with
a bounded deadline (`app/services/ai_gate.py:171-198`).

---

## 13. Risks to the recommendation itself

* **The concurrency work is the work.** If the browser's checkpoint is added without
  the base-revision predicate, Sprint 14 would *create* the silent-overwrite path it
  exists to close. S14-1, S14-4 and S14-8 exist to make that failure loud.
* **Scope pressure toward the workout UI.** Restoring set state invites a redesign.
  S14-10's inert OFF path and the §11 non-goals are the boundary. *(Amended P2-B:
  was "byte-identical OFF path".)*
* **PostgreSQL-only proof.** SQLite cannot demonstrate `FOR UPDATE` or
  conditional-UPDATE race outcomes. S14-8 must run on the PG gate, and the new module
  must be added to CI's explicit `pg_concurrency` file list — a step that has been
  silently missed before.
* **Activation still needs an instrument that is off.** Even with S14-7 delivered,
  `RUNTIME_METRICS_ENABLED` is unset in production, so PR5 can deliver *readiness* but
  not *proof under load*. This is stated as a limit of the sprint, not hidden.
* **Rollback philosophy.** Every PR is `git revert`-only. No PR may introduce a state
  whose rollback requires manual data repair; if implementation discovers one, that is
  an architecture risk to raise before writing the code (§30).

---

## 14. Scope confirmation for this PR

```text
production behavior     unchanged
routes/services/models  unchanged
schema                  unchanged
migration               none added (single head f5a6b7c8d9e0)
static JS / templates   unchanged
Flutter                 untouched (read-only inspection of axisai-mobile origin/main)
feature flags           unchanged (no value, default or record modified)
workflows / deployment  unchanged
```

The only change is this document.

---

## 15. PR2 implementation evidence — canonical workout execution contract

Appended by PR2. The candidate history, the ranking and the reasoning above are
preserved verbatim; sections 7, 8 and 13 carry only the two independent-review
amendments (P2-A, P2-B), each marked inline at the sentence it corrects.

### 15.1 What PR2 changed

F-1's root cause was not a missing browser feature. It was that the durable
execution rules `#276` wrote — bounds, canonical ordering, replay identity,
revision ordering, terminality — lived inside a module named for one transport.
The browser could not reach them without copying them, and a copy would have
been a second authority that agreed only by coincidence.

So PR2 moved the authority rather than duplicating it:

| Moved into the canonical domain | From | Why it was never transport-specific |
|---|---|---|
| `workout_session/checkpoint.py` | `mobile_workout_sessions/checkpoint.py` | The bounds describe a *workout*; the fingerprint describes a *snapshot*; the ordering is the *workout's* own exercise order |
| `workout_session/errors.py` | `mobile_workout_sessions/errors.py` | "the declared revision is not current" is a fact about a row, not about HTTP |
| `workout_session/execution.py` — `record_checkpoint`, `owned_session`, `reject_terminal`, `require_revision`, `prepare_completion` | `mobile_workout_sessions/service.py` | Ownership, terminality, replay ordering and the completion preflight are identical on both sides |

The native adapter re-exports the contract and delegates the orchestration. Its
public `/api/v1` behaviour is unchanged: same routes, same required `If-Match`
and `Idempotency-Key`, same `TRAINING_SESSION_*` codes, same `Session-Resolution`
and `Idempotency-Replayed` headers, same private-visibility native completion.
What is left in `mobile_workout_sessions` is what is genuinely native — resolving
an opaque HMAC `workout_ref`, and shaping the envelope.

### 15.2 The browser web transport

`POST /workout/session/<public_id>/checkpoint` stopped being a heartbeat. It now
requires `If-Match` (the declared base revision), `Idempotency-Key`, and a
bounded FULL snapshot in `{"checkpoint": {...}}`, and reaches `advance_checkpoint`
— the same single conditional UPDATE the native surface uses. It keeps the web's
own auth adapter: `@require_auth`, the app-wide two-layer CSRF gate, ownership
re-derived from `current_user`, and now `WORKOUT_CHECKPOINT_RATELIMIT`, because a
checkpoint is a write with a payload rather than a heartbeat. The flag gate is
applied *outside* the throttle, so a dark surface answers 404 and never 429.

`POST /workout/complete` closes the S14-3 backend gap. When the flag is ON **and**
`session_id` is supplied, the body must also declare
`expected_checkpoint_revision` (an integer, in the request document the route
already parses — one channel, chosen once, no `If-Match` fallback). That value is
carried into `CompleteWorkoutCommand` and verified **under the session row lock**
inside `complete_workout`. The route's own check is a cost preflight only; a
dedicated test exercises the canonical service directly, so removing the
in-transaction comparison fails even with the preflight intact.

Naming the session's workout: the browser has no opaque `workout_ref`, so its
canonical workout is resolved from the plan snapshot the session already
recorded — `source == scheduled`, the stored versioned `plan_fingerprint` still
matching the current plan's workout for that weekday slot, and every exercise
identity resolvable in the catalog. Drift is therefore decided by the *same*
`fingerprints_match` the lifecycle classifier uses, not by a second staleness
rule. An unscheduled session is refused as stale for the same structural reason a
native session without a `workout_ref` is: there is nothing to validate
membership against.

### 15.3 Criteria status after PR2

| ID | Status | Note |
|---|---|---|
| S14-1 | **backend contract satisfied**, partial | Revision-gated browser checkpoint; a stale request writes nothing and is refused with a typed code. PR3 proves the client sends it. |
| S14-2 | **satisfied (server projections)** | `SessionView` publishes `checkpoint_revision` + `checkpoint`; the native envelope now *sources* its `revision`/`checkpoint` from that same view. |
| S14-3 | **satisfied (server contract)** | The declared revision reaches the locked completion authority; a stale refusal writes zero artifacts. |
| S14-4 | **backend characterized**, open | Deterministic cross-transport tests both ways. The real PostgreSQL race proof stays PR4. |
| S14-5 | **open — PR3** | The data is available; nothing hydrates it yet. |
| S14-6 | **satisfied** | Oversized, over-count, unknown, duplicate and malformed payloads fail whole — never truncated, coerced or partially stored. |
| S14-7 | **open — PR4** | No `runtime_metrics` counter was added. |
| S14-8 | **open / partial — PR4** | PR2 introduced no new lock-dependent semantic, so no new PG module and no CI `pg_concurrency` change. `#276`'s PG suite still covers `advance_checkpoint` and the locked revision check. |
| S14-9 | **satisfied** | No model, column, table or migration. Alembic head remains `f5a6b7c8d9e0`, single. |
| S14-10 | **backend half satisfied** | Routes dark/404, no session fabricated, legacy completion unchanged and revision-free. The browser-client half is PR3. |
| S14-11 | **satisfied** | No flag value, default, environment, runbook status or rollout record changed. |

Sprint 14 is **not** complete.

### 15.4 PR3 handoff

PR3 should not need to invent any server semantics:

```text
GET  /workout/session/current
     -> session.checkpoint_revision, session.checkpoint

POST /workout/session/<public_id>/checkpoint
       If-Match: <revision>          (required)
       Idempotency-Key: <key>        (required)
       {"checkpoint": {...}}         (bounded FULL snapshot, never a patch)
     -> 200 {outcome, session, replayed}
     -> 428 revision_required        | 409 revision_conflict
        409 idempotency_conflict     | 400 invalid_checkpoint
        400 idempotency_key_invalid  | 409 session_terminal
        409 stale_session_requires_resolution
        404 not_found                | 503 session_unavailable
        (every refusal also carries Session-Resolution: retry | reread | terminal)

POST /workout/complete
       {"session_id": "<public id>", "expected_checkpoint_revision": <int>}
     -> 409 revision_conflict when progress moved on (zero artifacts written)
```

If PR3 finds itself inventing a server rule, PR2 is incomplete — that is the
test.

## 16. PR3 implementation evidence (2026-09-04)

The browser now consumes the canonical checkpoint contract. Its checkpoint
transport sends one bounded full snapshot with `If-Match` and
`Idempotency-Key`, accepts revision movement only from the canonical response,
and keeps lifecycle mutations separate from the single-flight checkpoint lane.

Reload hydration reconstructs the workout draft by canonical `exercise_id` and
set `index`, including completed flags, reps, weights, current exercise, and
elapsed time. Fresh sessions use the ordered `checkpoint_exercise_ids` projection;
checkpointed sessions hydrate from `session.checkpoint`. Missing, corrupt,
duplicate, or mismatched identity/state fails closed.

The revision/idempotency lifecycle is explicit: a command captures the current
acknowledged revision and a fresh key; ambiguous network, 429, and 5xx retries
retain that exact command; an acknowledgement retires its key and advances only
to the server-returned revision; a newer queued draft becomes a new command with
a different key. Deterministic reread responses discard the rejected draft,
refresh once, and never automatically replay it.

Completion flush ordering is enforced in the browser: Finish awaits a final
checkpoint acknowledgement before opening Pump Check, and the later completion
request supplies the client's then-current `expected_checkpoint_revision`.
Failure leaves the workout open; completion revision conflict closes stale UI
and renders the refreshed canonical state; the established already-completed
handling is unchanged.

With the flag OFF, contract v1 remains on the legacy in-memory execution path.
The browser creates no workout-session request, checkpoint timer, or checkpoint
write. No rollout/default/value changed.

The only additive server projection is
`session.checkpoint_exercise_ids: string[]`: ordered catalog IDs derived from the
session's still-matching canonical planned workout. It is read-only execution
input for the browser, contains no name-derived identity or private persistence
metadata, and adds no write authority, schema, or migration.

### 16.1 Criteria status after PR3

| ID | Status | Note |
|---|---|---|
| S14-1 | **satisfied** | The browser sends the shipped revision-gated full-snapshot contract. |
| S14-2 | **satisfied** | Canonical revision, snapshot, and minimal ordered identity projection are consumed. |
| S14-3 | **satisfied end-to-end for browser web transport** | Final flush precedes Pump Check and completion declares the acknowledged revision. |
| S14-4 | **client/server characterized; final PG closure remains PR4** | Conflict/reread and cross-surface stale completion behavior are deterministic. |
| S14-5 | **satisfied** | Acknowledged progress hydrates on reload/resume without name-derived identity. |
| S14-6 | **satisfied** | The browser emits strict full snapshots and fails closed on malformed state. |
| S14-7 | **open - PR4** | No runtime metric was added. |
| S14-8 | **open / partial - PR4** | PR3 adds no lock-dependent server semantic; final PostgreSQL reliability proof remains. |
| S14-9 | **satisfied** | No model, column, table, or migration was added. |
| S14-10 | **satisfied** | Contract v1 makes browser session persistence inert and preserves legacy execution. |
| S14-11 | **satisfied** | The feature remains dark; rollout and runbook state are unchanged. |

Sprint 14 remains open. PR4 still owns final PostgreSQL reliability proof,
observability, runtime lifecycle metrics, and accepted contract-debt review;
PR5 owns staged-activation readiness.
