# NUTR-PR6 — server-owned Nutrition day view + deterministic Coach handoff

Base: `origin/main` `aa81c1f` (NUTR-PR5 #368, merged, deployed, production-accepted; no intervening commits).
Scope: a bounded **read projection** over existing canonical nutrition facts, one deterministic safe
next action, a compact Today section that renders it, and an explicit, optional "Review with
AxisAI" handoff that reuses the Progress handoff architecture.

**Not** an AI-advice PR. No model, table, migration, native API, Coach tool, provider/prompt/quota
change, adherence/score/"on track", gap coaching or prescription.

> FACTS MAY BE PARTIAL. CLAIMS MAY NOT EXCEED THE FACTS.

## 1. Pre-change fact topology (aa81c1f)

| Fact | Authority | How Nutrition Today read it | Failure shown as |
| --- | --- | --- | --- |
| Daily target | latest `UserSession.target_calories` → `nutrition_targets.derive_daily_macro_targets` | `/meal-log/today` `targets` (shared with intake) | *Target unavailable* (never 0); `null` → *Target not set yet* |
| Intake | `MealLog` rows with `tarih == day_key()` (Istanbul) | `/meal-log/today` `totals` + `meals` | *Meals unavailable* + retry |
| Hydration | `WaterLog` row with `date_key == app_today()` (Istanbul) | `GET /water` | `—`, controls locked, retry |
| Plan | newest `NutritionPlan` row | `GET /nutrition-plan/active` (shared by Plan + "From your plan") | *unavailable*, never "no plan" |
| Coach entry | — | plain `<a href="/coach">` | — |

There was no server-side composition of these facts and no next-action concept on Nutrition.
The Progress → Coach handoff (`/coach?review=progress-insight`, `app/coach_handoff.py`,
`templates/_coach_handoff.html`, both `/ask` routes allowlisting one constant) was the only
handoff and the architectural precedent.

## 2. Canonical authority map (unchanged)

| Section | Authority | Read (one bounded SELECT each, own savepoint) |
| --- | --- | --- |
| `target` | `UserSession` (newest `created_at`) through `derive_daily_macro_targets` | `target_calories, goal … ORDER BY created_at DESC LIMIT 1` |
| `intake` | `MealLog` | `COUNT(id), COALESCE(SUM(kalori|protein|karb|yag),0) WHERE user_id AND tarih = day` |
| `hydration` | `WaterLog` | `count WHERE user_id AND date_key = day` |
| `plan` | `NutritionPlan` (newest) | `plan_data … ORDER BY created_at DESC LIMIT 1` |
| `day` | `app.timeutil.app_today()` | computed ONCE per build, shared by intake + hydration |

Never used as a fact: DOM state, `localStorage`/`sessionStorage`, client totals, generated
proposal cards, Coach history, AI output. A generated plan proposal is never a `NutritionPlan`
row, so it cannot appear as the active plan.

## 3. NutritionDayView

Service: `app/services/nutrition_day_view.py` (`build_nutrition_day_view(user_id)` →
frozen `NutritionDayView`; `nutrition_day_view_payload(view)` → wire dict). Transport-neutral:
no Flask request/response, no `current_user`. Transport: `GET /nutrition-day-view`
(`app/blueprints/nutrition/day_view.py`, `@require_auth`, owner = session user, no query
parameter selects anything, `Cache-Control: private, no-store`, a defect → typed 503
`{"error": "nutrition_day_view_unavailable"}`, never a partial body). Not a native endpoint.

```json
{
  "contract_version": 1,
  "day": "2026-09-30",
  "target":    {"state": "available", "value": 2100.0, "unit": "kcal"},
  "intake":    {"state": "available", "totals": {"calories": 1235.0, "protein": 72.0,
                 "carbs": 140.0, "fat": 30.0}, "meal_count": 2},
  "hydration": {"state": "available", "amount": 3, "unit": "glass"},
  "plan":      {"state": "available", "summary": {"name": "Lean plan", "planned_meal_count": 2}},
  "next_action": {"state": "available", "kind": "log_food", "label_key": "nutrition.next.log_food"}
}
```

Values are canonical and unrounded (presentation rounds). No DOM concept (`button_id`,
`css_class`, `modal_name`, URLs) and no user-facing sentence leaves the service.

## 4. State vocabulary

| State | Meaning | Values |
| --- | --- | --- |
| `loading` | client-only, before the answer | — |
| `available` | successful read, something recorded | the facts |
| `empty` | successful read that proves "nothing" | target: `value: null` (not configured; `null`/≤0 per the existing contract); intake: `meal_count 0` + measured zero totals; hydration: `amount 0`; plan: `summary: null` |
| `unavailable` | the read failed | every value `null` — unknown, never 0, never empty |
| `invalid` | read succeeded, value cannot be represented truthfully | target non-finite; intake count/total non-finite, negative or non-numeric; hydration negative/non-int; plan not JSON, not an object, or no meal object (same "drawable" rule as the Plan tab) |

`unknown → 0` and `failure → empty` are impossible by construction and tested per section.
**No aggregate `partial` field** is exposed: a composition is partial exactly when some
sections are `unavailable`/`invalid` and others readable, and every consumer sees that from the
sections themselves. No presentation needed a summary state, so none was invented.

## 5. Partial composition

Each section read runs in its own `begin_nested()` savepoint inside `_guard`; one failure yields
that section `unavailable` and leaves the session usable (proved with a real failing statement).
Healthy siblings are byte-identical to a fully healthy build (tested for each of the four).

## 6. Day / timezone boundary

`app_today()` (Europe/Istanbul) — the same boundary `/meal-log/today` (`day_key()`) and
`/water` (`app_today()`) use. The build computes it once and passes the same ISO day to the
intake and hydration reads. Tested with `audit_clock` at 23:30 Istanbul (20:30 UTC) and 00:30
Istanbul (21:30 UTC the previous day): the view, `/meal-log/today` and `/water` name the same day
and agree on totals/count, and the second case proves a UTC guess would pick the wrong day. The
browser never picks the day.

## 7. next_action — deterministic decision table

Pure `derive_next_action(intake_state, target_state)`; hydration and plan are deliberately not
inputs.

| intake | target | next_action |
| --- | --- | --- |
| `unavailable` | any | `retry` |
| `invalid` | any | none (`state: empty`, `label_key: nutrition.next.none`) |
| `available` / `empty` | `empty` (genuinely not set) | `set_target` |
| `available` / `empty` | `available` / `unavailable` / `invalid` | `log_food` |
| anything else | anything else | none |

Deviation from the brief's suggested precedence, deliberate: **invalid intake → no action**
(not retry). Re-reading corrupt persisted rows cannot repair them; a futile retry is a guess.
A target that failed to read or is stored unusably is not "missing" → `log_food`, never
`set_target`.

Allowed kinds (closed): `log_food`, `set_target`, `retry`. No advisory kind (`eat_more`,
`drink_more`, `hit_protein`, `follow_plan`, `change_plan`, …).

| kind | label_key | UI action | canonical destination | network on click |
| --- | --- | --- | --- | --- |
| `log_food` | `nutrition.next.log_food` | Next step button | `openLogSheet()` — the PR4 chooser | **0** (opening writes and reads nothing) |
| `set_target` | `nutrition.next.set_target` | Next step link | `/setup?yeniden=1` — the onboarding form, i.e. `account_profile.complete_onboarding`, the one writer of `UserSession.target_calories` (repeat-safe; `?yeniden=1` is the existing deliberate re-setup entry so a complete-but-targetless legacy account is not redirected away) | 1 navigation GET; **0 writes** until the user submits the existing form |
| `retry` | `nutrition.next.retry` | Next step button | re-read `/nutrition-day-view` + Today's ledger `/meal-log/today` (the failed factual read) | 2 GETs |

No second target writer was created. When the whole day-view read fails (HTTP/network/malformed),
the section is `unavailable` with its own retry (day view only, 1 GET).

## 8. Why adherence / scoring is intentionally absent

No reviewed server rule owns "on track", "behind", "ahead", adherence %, compliance, a
nutrition/health score, protein/calorie/hydration gaps or prescriptions, and the inputs
(self-logged, partial, possibly failed) cannot support them. Enforced by an AST scan of the
service (identifiers and non-docstring strings), a payload scan, a copy scan of every PR6
locale key (EN + TR), a check that no comparison relates intake to target, and P6-N6.

## 9. Today presentation

`Today | Plan` unchanged (exactly two tabs, one H1). One compact section, **Next step**
(`#nut-next`, `h2`), after "From your plan" and before the secondary disclosures — it displaces
nothing (PR3's pinned order is still green). Ghost style (quieter than the volt "Log food").
Lead line `role="status"` (polite): loading / the kind's lead / "No next step to suggest right
now." / "The next step couldn't be loaded." Control ≥44 px; text wraps (no clipped TR copy).

The browser maps ONLY allowlisted kinds (`NEXT_ACTIONS`, frozen, `hasOwnProperty`-checked, and
the server `label_key` must equal the allowlist's). Unknown kind, mismatched key, `toString` /
`__proto__` / `constructor`, arrays, missing object, `state != available` → non-actionable
"no step". No `eval`, no URL from JSON, no dynamic function lookup; `runNextAction` re-checks
the allowlist at click time. The `set_target` href is a constant in the template.

Focus: a retried control keeps focus on the section's new control (the heading holds it while
loading). The log-food chooser returns focus to the Next step button on dismiss.

"Review with AxisAI" refines the existing Tier-3 Coach link (`#nut-review-coach`,
`/coach?review=nutrition-day`, ghost, ≥44 px).

## 10. Coach handoff architecture (generalized, not duplicated)

`app/coach_handoff.py` now owns an allowlist `HANDOFF_KINDS = {progress-insight, nutrition-day}`
and `handoff_marker(value)` (exact string match; lists/dicts/numbers/whitespace variants → `None`).
Both `/ask` and `/ask/stream` forward only `handoff_marker(data.get("handoff"))`; every other body
field (e.g. `calories`, `context`, `meals`) is ignored. `_coach_handoff.html` renders one aside per
kind (Progress keeps `#coach-progress-context` / `#coach-progress-dismiss`; Nutrition uses
`#coach-nutrition-context` / `#coach-nutrition-dismiss`), sets `CW.handoff` to the server's kind
and prefills the draft only if the composer is empty. `coach_widget.js` is unchanged.

`replaces_plan_projection(kind)`: Progress context IS the plan projection (unchanged: one build,
no second report); Nutrition context is additive — the normal adaptive plan projection stays.

### Page (render) — 0 model calls, 0 sends
`/coach?review=nutrition-day` → `coach_handoff_message` re-reads the day view for the session
user and renders at most two bounded lines ("Meals logged today: 2 · 1235 kcal" / "Daily target:
2100 kcal" | "Daily target not set yet" | "Daily target unavailable"), the label "From Nutrition",
a dismiss button and the editable draft "Review today's nutrition with me." / "Bugünkü
beslenmemi benimle değerlendir." — no meal names, no plan contents, no digits. If intake is
not readable (or anything raises) → no aside, normal Coach, no claim of attached context.

### Send — canonical re-read
`coach_handoff_context` rebuilds the day view **at send time** for the authenticated owner and
emits typed lines (`day`, `daily target`, `logged today`, `hydration today`, `nutrition plan`),
each with its truthful state: `unknown (read failed)`, `unreadable`, `none`, `not set`,
`no meals logged yet (measured 0)`. It instructs the model that unknown is not zero and not to
compute adherence or invent values. No meal names, no plan name. If intake is not readable →
`""` (no context attached; existing failure semantics).

## 11. Privacy / trust boundary

The URL carries only the constant `nutrition-day` (no number, meal, water amount, plan). The
send body is `{question, history, handoff}`; facts never ride it. The draft contains no fact.
Context lives in the model prompt only — never `record_turn`, `CoachMessage`, browser history,
summaries or a handoff store (tested against the real `CoachMessage` rows). Browser day-view data,
markers and label keys are untrusted and allowlisted. All rendering is `textContent` / Jinja
autoescape.

## 12. Lifecycle (unchanged semantics, now per kind)

| Event | Result |
| --- | --- |
| Review click (single or double) | navigation to `/coach?review=nutrition-day`; fresh server preview; unsent draft; 0 `/ask*` |
| Edit draft / send | first send carries `handoff: nutrition-day`; facts re-derived now |
| Dismiss (mouse or keyboard) | aside removed, `CW.handoff = null`, URL `/coach`, focus → composer; next send has no marker |
| First successful reply | aside removed, marker cleared, URL `/coach` |
| Send failure / fallback error | marker kept for the explicit retry (existing `_lastHandoff` path) |
| Second message, normal `/coach` | no handoff |
| Page refresh with `?review=` | fresh derivation (explicit continuation) |

Send failure never mutates nutrition (PR6 adds no writer and no tool).

## 13. AI / provider calls

Initial `/nutrition`, `/nutrition-day-view`, the Plan tab, the chooser, Review navigation and the
Coach preview: **0** LLM / food-provider / barcode / menu / plan-generation calls (tested with
`socket.connect`, `_heavy_chat`, `_openai_chat`, `_claude_chat` and `_run_coach_conversation`
refusing). Only an explicit Coach Send uses the existing inference path. Provider, prompt
infrastructure, quota, premium gate and rate limits unchanged.

## 14. Request topology (measured, `test_request_topology`)

| Step | Before (aa81c1f) | After |
| --- | --- | --- |
| A. initial `/nutrition` | `/nutrition`, `/meal-log/today`, `/nutrition-plan/active`, `/water`, `/notifications/unread-count`, `/coach/history` ×1 | same **+ `/nutrition-day-view` ×1** |
| B. day-view read | — | exactly one at load; 4 SELECTs + 4 SAVEPOINT/RELEASE, 0 writes, bounded by history size (tested) |
| Plan tab / Today redraw | Plan 0; Plan→Today `/meal-log/today` ×1 | unchanged; **0** day-view requests |
| C. Log food chooser open (either button) | 0 | 0 |
| D. retry | — | `retry` kind: `/nutrition-day-view` ×1 + `/meal-log/today` ×1; unavailable: `/nutrition-day-view` ×1; double click = 1 |
| E. Review with AxisAI | `/coach` page | `/coach?review=nutrition-day` page (+ its existing `/coach/history`); 0 `/ask*`, 0 model |
| F. actual Coach send | `/ask/stream` (fallback `/ask`) | same, one request, existing inference |
| idle | 0 | 0 (no polling) |

One extra: when Today's own ledger read succeeds while the Next step still shows `retry`, the day
view is re-read once (single flight) so the section cannot contradict a recovered ledger.

## 15. Race handling

Every day-view read takes a ticket (`_dayViewSeq`); reads are single-flight (`_dayViewInFlight`).
Tested: A/H an older answer finishing after a newer successful read is dropped; B Today → Plan →
Today while pending issues no second request and the pending answer renders; C double retry /
direct calls = one request; D Review double-click sends nothing; E/F send-time facts differ from
the preview and the latest canonical facts win; G send-time re-read failure attaches no context.

## 16. Accessibility / responsive

One H1, exactly two tabs, Next step `h2`, controls ≥44 px (Next step button/link, Review link),
visible `:focus-visible` outline, keyboard activation (Enter) for Next step and Coach dismiss,
lead `role="status"`, state in words (never colour). Verified 320/390/430/768/1024/1366 × EN/TR:
no horizontal overflow, no clipped copy, controls in view; 150 % root text at 320 px +
`prefers-reduced-motion: reduce`; Coach preview < 150 px tall at every width. PR5's
`.apd-meal-name` guard remains in the PR5 browser suite (green).

## 17. Future PR7 reuse

PR7 wraps `build_nutrition_day_view` + `nutrition_day_view_payload` in a protected `/api/v1`
transport (Bearer principal, typed 503 on defect) without duplicating any rule. Stable semantics:
field names, `SECTION_STATES`, `NEXT_ACTION_KINDS`, `label_key` meaning, partial/failure rules,
authority ownership, `contract_version: 1`. A native Review handoff still needs a native Coach
destination/context contract (deferred per the contract).

## 18. Tests and non-vacuity

- `tests/test_nutrition_vnext_pr6_day_view.py` — shape, ownership, thin route, typed failure, no
  persistence/writes, exact authorities + 4 bounded SELECTs, no AI/network, no score semantics,
  every section state, isolation with a real failing statement, day boundary, table-driven
  `next_action` (+ totality, hydration/plan independence, real reads), locale resolution, client
  renders-never-decides, read-once topology, copy parity/plainness.
- `tests/test_nutrition_vnext_pr6_coach_handoff.py` — allowlist, both routes, page preview/draft
  with 0 model calls, TR copy, truthful target line, failure → normal Coach, typed send-time
  context, owner re-derivation, freshness, partial unknowns, failed intake/build → no context,
  unknown marker, additive plan projection, forged calories on a real `/ask`, stored history,
  question/context separation, no writer, Progress unchanged.
- `tests/test_nutrition_vnext_pr6_browser.py` — allowlisted actions (EN/TR), set-target mapping,
  retry + focus, HTTP failure, forged kinds, topology, races B/C/H, Review link, Review → Coach →
  Send, dismiss, send failure retry, failed derivation, a11y/responsive, large text + reduced
  motion, Coach preview widths.
- `tests/test_nutrition_vnext_pr6_non_vacuity.py` — P6-N1…P6-N14, each mutation applied to the
  real service source (executed in the real module), served script, Jinja template or real Coach
  seam, and shown to fail its guard.
- Intentionally superseded older assertions (each adds exactly the one documented read, the new
  route or the new kind — nothing else): `tests/test_coach_entry_convergence.py` (Nutrition's
  entry now carries `?review=nutrition-day`), `tests/test_nutrition_vnext_pr3_daily_contract.py`
  `KNOWN_ENDPOINTS` (+ `/nutrition-day-view`), initial-topology pins in the PR2 navigation, PR3,
  PR4, PR5 and hydration browser suites and the UX4-PR8 `DEFAULT_READS["nutrition"]`
  (+ `/nutrition-day-view` ×1), the Sprint 13 nutrition route inventory (+ the GET route),
  `tests/test_progress_axis_insight.py` handoff import set (+ `math`, the day-view service),
  `tests/js/nutrition_plan_render.test.js` DOM stub (`contains`/`removeAttribute`).

## 19. Known limitations (not claimed)

- Carried from PR5 (unchanged, out of scope): `/nutrition-plan/save` has no expected-plan
  precondition; planned-meal quick-add is not exactly-once.
- The day view duplicates reads the page already makes (by design: it is the server authority
  for the decision and the future native contract); PR6 does not refactor Today onto it.
- `nutrition.ask_coach` copy is no longer rendered on Nutrition (replaced by
  `nutrition.review_with_axisai`).
