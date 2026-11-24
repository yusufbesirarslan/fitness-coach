# PD2 — Onboarding training start: method → server preview → Accept

Status: **source only, dark.** Every new route is behind the default-OFF
rollout flag `FITX_TRAINING_START_PREVIEW_ENABLED` (404 before auth while OFF).
Nothing is activated by this change. Product authority: fitness-coach#547.

## What the owner sees

After onboarding the owner chooses one of four methods:

| Method  | What happens                                                                 | AI? |
|---------|-------------------------------------------------------------------------------|-----|
| AI      | One authorized generation, staged as a **pending draft**, previewed, then Accept | yes (D1-closed endpoint, consent + allowance) |
| General | Deterministic template computed and previewed (nothing written), then Accept | **never** |
| Manual  | The same deterministic starter week (days/equipment) previewed → Accept → the canonical Training Editor opens on the new plan | **never** |
| Skip    | No plan; an explicit `skip` decision is recorded                              | never |

Before anything is activated the preview shows the **canonical daily calorie
target**, the real 7-day week that Accept would activate, and three actions:
**Accept**, **Edit** (back to the method's inputs) and **Choose another method**.

## Lifecycle

```
             ┌──────────── choose another / cancel ─────────────┐
             │                                                   │
 method ──► preview ──► (pending draft) ──► Accept ──► ACTIVE plan (Today, rewards, analytics)
   │            │                              │
   │            └── replay same key = same draft (no regeneration, no charge)
   └── Skip ──► decision=skip (no plan)
```

* **Pending draft (AI)** = the existing generation ledger row in state
  `GENERATED` (`training_plan_generation_operation.candidate_plan_data`, bound
  to its consent operation authority). It is **not** a `training_plan` row, so
  `get_active_plan`, `GET /training/plans/current`, Today, rewards and
  analytics cannot see it. No new plan store.
* **Pending draft (General/Manual)** = nothing persisted. The template is
  deterministic; Accept recomputes the identical document and verifies it.
* **Accept** uses the **existing writers only**:
  AI → `store.commit_plan` (the same writer as GENERATED recovery);
  General/Manual → `store.commit_general_plan` (LP21). Lock order unchanged:
  operation row → owner row (`lock_plan_owner`) → plan absence check → insert.
  No third plan writer (`tests/test_plan_owner_lock_contract.py`).
* **Exactly-once**: the preview's Idempotency-Key is the operation identity;
  `If-Match` pins the exact candidate text (`sha256` of the staged/recomputed
  document). A repeated Accept replays the same plan (`Idempotency-Replayed:
  true`), creates nothing, charges nothing.
* **Cancel / choose another** (`/preview/discard`) ends the draft as
  `FAILED(TRAINING_PLAN_CANDIDATE_DISCARDED)`. A new AI preview under a new key
  is refused (409 `TRAINING_PLAN_PREVIEW_PENDING`) while the owner's earlier
  preview is pending and unexpired: a delivered draft is never destroyed before
  the new request's own admission (consent/allowance/quota) is known, so the
  client discards explicitly first (Edit with changed preferences, Back,
  Choose another). An expired pending preview is ended automatically. Active
  plans are never touched. Cancel records nothing (the owner returns to the method
  choice); Skip records `skip`. They stay distinct.
* **Stale**: If-Match ≠ the candidate the server would activate → 412
  `TRAINING_PLAN_PREVIEW_STALE`, nothing written, the draft stays reviewable.
  Drafts older than 24 h (`PREVIEW_TTL`) are ended on access → 409
  `TRAINING_PLAN_PREVIEW_EXPIRED`.
* **Existing plan protected**: preview, Accept and Skip all refuse when an
  active plan exists (409 `TRAINING_PLAN_REPLACEMENT_REFUSED` /
  `TRAINING_START_PLAN_EXISTS`); no provider work happens first.
* **Onboarding completion is separate from activation**: onboarding completes
  as before; the plan exists only after Accept (or never, after Skip).

## API contract (`contract_version: 1`)

All routes: Bearer auth, `mobile_api` envelope, `Cache-Control: no-store`.

### `GET /api/v1/training/start`
```json
{"contract_version": 1, "has_plan": false, "decision": null,
 "daily_calorie_target": {"state": "available", "kcal": 2150}}
```
`decision` ∈ `null | "skip"`; always `null` when `has_plan` (a plan outranks a
stale Skip). `daily_calorie_target.state` ∈ `available|empty|invalid|unavailable`
(`kcal` only when `available`).

### `PUT /api/v1/training/start` `{"decision": "skip"}`
Idempotent. 422 `TRAINING_START_INVALID_REQUEST` (any other body), 422
`TRAINING_PLAN_PREREQUISITE_MISSING` (onboarding incomplete), 409
`TRAINING_START_PLAN_EXISTS`. Returns the GET shape.

### AI preview — `POST /api/v1/training/plans?preview=1[&include_sessions=1]`
The existing D1-closed generation endpoint (same consent closure, launch
admission, AI rate limits, concurrency slot, allowance hold and quota) with
the existing native request body and `Idempotency-Key`. Stops after staging.
200:
```json
{"contract_version": 1,
 "preview": {"method": "ai", "candidate_fingerprint": "<64 hex>",
             "expires_at": "2026-11-25T10:00:00Z",
             "plan": {"days": [{"slot": 0, "weekday": "Pazartesi", "kind": "training",
                                "focus": "...", "duration_minutes": 45,
                                "estimated_calories": 300, "workout_ref": null,
                                "exercises": [...]}, ...]}},
 "daily_calorie_target": {"state": "available", "kcal": 2150}}
```
Days use the active-plan day/exercise projection (same validation, same
catalog resolution) with `workout_ref: null` — a draft cannot be started.
No `haftalik_ozet` (no AI-written summary is shown). `?preview=` other than
`1` → 422; flag OFF → 404 (never silently ignored: ignoring it would commit).
Same key replay → the SAME candidate, `Idempotency-Replayed: true`, no
provider call. The preview fingerprint domain is separate from the legacy
command's, so a legacy POST with a preview key is 409
`TRAINING_PLAN_IDEMPOTENCY_CONFLICT` and can never commit a draft.

### AI accept — `POST /api/v1/training/plans/preview/accept[?include_sessions=1]`
Headers: `Idempotency-Key: <preview key>`, `If-Match: "<candidate_fingerprint>"`.
201 `{"contract_version": 1, "origin": {"kind": "ai_generated", ...}, "plan": {...}}`
(`plan` = the current-plan projection). Errors: 428
`TRAINING_PLAN_PREVIEW_PRECONDITION_REQUIRED`, 404
`TRAINING_PLAN_PREVIEW_NOT_FOUND` (unknown/foreign/legacy key), 409
`TRAINING_PLAN_PREVIEW_DISCARDED` / `_EXPIRED`, 412 `_STALE`, 409
`TRAINING_PLAN_REPLACEMENT_REFUSED`, 409 `TRAINING_PLAN_GENERATION_IN_PROGRESS`
(retry), D1 consent envelope when the draft's consent authority was withdrawn
(the draft is then ended; no plan).

### AI discard — `POST /api/v1/training/plans/preview/discard`
`Idempotency-Key: <preview key>` → 204 (idempotent). 409
`TRAINING_PLAN_PREVIEW_ALREADY_ACCEPTED`; 409 in progress (generation still
running — retry); 404 not found.

### General/Manual preview — `POST /api/v1/training/plans/general/preview[?include_sessions=1]`
The LP21 body. Pure (no ledger row, no plan, no AI seam — AST gate). 200 same
shape with `method: "general"`, `expires_at: null` and `preview.origin`
(`general_template`, `personalized: false`).

### General/Manual accept — `POST /api/v1/training/plans/general` + `If-Match`
The existing LP21 route. With `If-Match` (flag ON only) the recomputed
document must match the preview → else 412, nothing written, key not consumed.
Without `If-Match` the LP21 contract is byte-identical. A same-key replay
returns the committed plan without comparing.

## AI consent, allowance and spend

* The preview IS the D1-closed `create_training_plan` endpoint, so consent,
  launch admission, purpose grant, allowance hold, legacy quota, AI rate
  limits and concurrency are inherited unchanged. Viewing a screen calls
  nothing; only the explicit preview POST generates.
* Accept/Discard never reach a provider; they are not D1-closed (like the
  existing replacement confirmation). Accept still fences the draft's own
  consent operation authority under the owner lock (`commit_plan`).
* Settlement: a candidate that was staged (shown) and then discarded/expired —
  or ended because consent was withdrawn after it was shown — settles as
  **success** (`allowance._settlement`) and the legacy weekly quota
  is **not** refunded — the AI work happened. Otherwise preview+discard would
  loop for free generations. Accept after the hold settles consumes nothing
  more. Duplicate Accept charges nothing.
* AI refusal (consent, allowance, provider) leaves General, Manual and Skip
  fully available; they never import a provider/consent/allowance module.
  General output is never labelled AI: origin comes from the ledger receipt.

## PD1 calorie integration requirement

PD2 never computes TDEE, pace or calories. Every preview and the start read
call `nutrition_day_view.read_daily_calorie_target(user_id)` — the SAME read
and failure guard as the Nutrition day view (`UserSession.target_calories` of
the newest onboarding session → `derive_daily_macro_targets`). **PD1 must
write its pace-derived target behind this read** (i.e. persist it as the
canonical `UserSession.target_calories`, or change `_read_target` itself);
it must not introduce a second calorie value for onboarding. Until PD1 lands
this adapter shows the existing canonical value.

## Manual — scope note

The canonical Training Editor (`/training/editor/*`) edits only the ACTIVE
plan. Making it edit a draft would create a second plan writer, which is out
of bounds. Manual therefore previews the deterministic starter week, and the
canonical editor edits that plan after Accept (automatic navigation into the
editor right after Accept is a router follow-up). Exercise-level editing
before activation is a product follow-up, not in this slice.

## Rollout / rollback

Enable order: migration `b7d2e9f4a1c3` (expand-only, table
`training_start_decision`; downgrade refuses with rows) → backend flag ON →
mobile build with `AXISAI_TRAINING_START_V2_ENABLED=true`. Rollback: flag OFF
(routes 404, legacy creation unchanged; a pending preview is ended — consumed —
by the owner's next legacy generate-and-persist, which can never activate it,
so no owner is stranded behind a preview). A mobile client with the switch ON must only talk to a
backend with this change (an older backend would ignore `?preview=1`).

## Tests

`tests/test_pd2_training_start.py` (SQLite; acceptance rows AI-1/2, GEN-1,
MAN-1, SKIP-1, PREVIEW-1/2, ACCEPT-1/2, CANCEL-1, STALE-1, CONSENT-1,
SECURITY-1, G4-1, allowance), `tests/test_pd2_training_start_pg.py`
(PostgreSQL races: concurrent Accept, Accept×Discard, Accept×General,
Accept×Skip; in the CI PG list).

## Device critical-path checklist (not executed; needs a disposable backend + GO)

Build: mobile `--dart-define=AXISAI_TRAINING_START_V2_ENABLED=true
--dart-define=AXISAI_GENERAL_TRAINING_PLAN_ENABLED=true` against a disposable
backend with `FITX_TRAINING_START_PREVIEW_ENABLED=1`, migration `b7d2e9f4a1c3`,
and a synthetic AI provider (no real Bedrock without separately authorized
credentials). Fresh accounts per row; EN and TR.

1. Onboarding completes → method screen shows 4 cards + the calorie target
   equal to Nutrition's target for the same account (PREVIEW-1, LOCALE-1).
2. General → days/equipment → Preview: 7 days, "General template, no AI";
   server ledger has no row, no plan (GEN-1). Accept → Today shows the same
   week; origin general (ACCEPT-1).
3. Manual → same preview → Accept → the app opens on the new plan; the
   canonical Training editor (Today/Plan entry) edits it and edits persist
   (MAN-1). Automatic navigation into the editor right after Accept is NOT
   wired yet (mobile exposes `openEditorAfterActivation`; router follow-up).
4. AI (consent granted) → preferences → Preview: one provider call; kill the
   app on the preview, relaunch → the SAME week, no second provider call
   (AI-2). Accept twice quickly → one plan, one allowance unit (ACCEPT-2).
5. AI → Preview → Choose another → General preview → Accept: the AI draft is
   FAILED/discarded server-side, the General plan is active (CANCEL-1).
6. AI with consent refused → AI card closed; General/Manual/Skip work
   (CONSENT-1).
7. Skip → enters the app with no plan; Today/Plan show the no-plan state;
   relogin → still skipped, no method screen (SKIP-1, RELOGIN-1).
8. Accepted plan → sign out → sign in (another device too) → plan reloads,
   no method screen, no generation (RELOGIN-1).
9. Language changed between General preview and Accept → 412 → refreshed
   preview shown, nothing activated until accepted again (STALE-1).
10. Account with an existing plan never sees the method screen; preview/
    accept/skip refused server-side (existing plan protected).
11. G4 build (`AXISAI_TRAINING_SESSIONS_ENABLED=true`) → preview shows
    "+ cardio session" rows only with the opt-in (G4-1).
