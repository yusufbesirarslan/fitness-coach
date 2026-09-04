# UX-2 PR5 Today Guidance Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make web Today choose one deterministic, server-authoritative guidance action from the canonical workout snapshot while failing closed and preserving PR4's UI and domain boundaries.

**Architecture:** Add a pure `app/today_guidance.py` decision layer between canonical facts and presentation. It validates the state/action pair, builds a closed candidate set, applies an explicit priority table, and emits a frozen semantic decision; `today_presenter.py` remains responsible for routes and localization keys, while the template and JavaScript only render or hydrate supplied values.

**Tech Stack:** Python 3.14, Flask, frozen dataclasses, pytest, Jinja, repository JSON i18n catalogs, Node syntax check, hermetic Playwright/Chromium frontend audit.

**Spec:** `docs/superpowers/specs/2026-09-04-ux2-pr5-today-guidance-orchestration-design.md`

## Global Constraints

- Base is `origin/main` at `ab178830e527a18867c2f35037ee5e83dca0174d`; PR4 must remain an ancestor.
- Consume `app.services.workout_state` state and action values verbatim; never inspect raw session rows or re-derive workout state.
- Primary action precedence is Resume Workout, Start Workout, Create Plan, then no primary action.
- Check-in due, recovery/readiness, daypart ranking, and Nutrition urgency stay deferred; Nutrition remains supporting-only.
- All decisions are server-side and deterministic; `static/today.js` must not choose an action or interpret workout meaning.
- Unknown state, canonical read failure, and incompatible state/action pairs fail to the honest `error` view.
- At most one primary action and at least one valid continuation remain structural invariants.
- No schema, migration, auth, navigation, Training mutation/generation, Nutrition target computation, Progress algorithm, Coach, provider, LLM, feature-flag, or visual redesign change.
- New user-facing copy, if required, must be complete in `locales/en.json` and `locales/tr.json`.
- Do not push, open a PR, merge, deploy, or activate flags.

---

### Task 1: Pure guidance decision contract

**Files:**
- Create: `tests/test_today_guidance.py`
- Create: `app/today_guidance.py`

**Interfaces:**
- Consumes: canonical constants from `app.services.workout_state.models`.
- Produces: `Candidate(kind: str, priority: int, reason: str)`, `TodayDecision(state: str, primary_kind: str | None, emphasis: str, decision_reason: str)`, `rank_candidates(candidates: tuple[Candidate, ...]) -> Candidate | None`, and `decide_today_guidance(*, read_ok: bool, primary_state: str, action: str) -> TodayDecision`.

- [ ] **Step 1: Write failing tests for explicit precedence**

Create `tests/test_today_guidance.py` with direct behavior tests. The precedence test must construct competing candidates so it is non-vacuous:

```python
from app.today_guidance import (
    CANDIDATE_CREATE_PLAN,
    CANDIDATE_RESUME_WORKOUT,
    CANDIDATE_START_WORKOUT,
    Candidate,
    rank_candidates,
)


def test_resume_outranks_start_and_create_plan():
    candidates = (
        Candidate(CANDIDATE_CREATE_PLAN, 30, "canonical_no_plan"),
        Candidate(CANDIDATE_START_WORKOUT, 20, "canonical_start"),
        Candidate(CANDIDATE_RESUME_WORKOUT, 10, "canonical_resume"),
    )
    assert rank_candidates(candidates).kind == CANDIDATE_RESUME_WORKOUT


def test_start_outranks_create_plan():
    candidates = (
        Candidate(CANDIDATE_CREATE_PLAN, 30, "canonical_no_plan"),
        Candidate(CANDIDATE_START_WORKOUT, 20, "canonical_start"),
    )
    assert rank_candidates(candidates).kind == CANDIDATE_START_WORKOUT
```

- [ ] **Step 2: Run the precedence tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_today_guidance.py
```

Expected: collection fails because `app.today_guidance` does not exist.

- [ ] **Step 3: Implement the minimal closed candidate model**

Create `app/today_guidance.py` with frozen value objects, named priorities, and stable identifiers:

```python
from dataclasses import dataclass

CANDIDATE_RESUME_WORKOUT = "resume_workout"
CANDIDATE_START_WORKOUT = "start_workout"
CANDIDATE_CREATE_PLAN = "create_plan"

PRIORITY_RESUME_WORKOUT = 10
PRIORITY_START_WORKOUT = 20
PRIORITY_CREATE_PLAN = 30


@dataclass(frozen=True)
class Candidate:
    kind: str
    priority: int
    reason: str


def rank_candidates(candidates: tuple[Candidate, ...]) -> Candidate | None:
    return min(candidates, key=lambda item: item.priority, default=None)
```

- [ ] **Step 4: Run the precedence tests and verify GREEN**

Run `python -m pytest -q tests/test_today_guidance.py`.

Expected: both precedence tests pass.

- [ ] **Step 5: Write failing tests for eligibility and fail-closed behavior**

Add parameterized tests covering every canonical state/action pair, read failure, unknown state, and incompatible pairs:

```python
@pytest.mark.parametrize(("state", "action", "primary_kind"), [
    (PRIMARY_IN_PROGRESS, ACTION_RESUME, CANDIDATE_RESUME_WORKOUT),
    (PRIMARY_SCHEDULED_NOT_STARTED, ACTION_START, CANDIDATE_START_WORKOUT),
    (PRIMARY_NO_PLAN, ACTION_NONE, CANDIDATE_CREATE_PLAN),
    (PRIMARY_REST_DAY, ACTION_NONE, None),
    (PRIMARY_EXECUTION_RECORDED, ACTION_NONE, None),
    (PRIMARY_UNSCHEDULED_EXECUTION, ACTION_NONE, None),
    (PRIMARY_COMPLETED, ACTION_NONE, None),
    (PRIMARY_UNSCHEDULED_COMPLETED, ACTION_NONE, None),
    (PRIMARY_NEEDS_ATTENTION, ACTION_BLOCKED, None),
])
def test_canonical_state_action_matrix(state, action, primary_kind):
    decision = decide_today_guidance(
        read_ok=True, primary_state=state, action=action)
    assert decision.state == state
    assert decision.primary_kind == primary_kind


@pytest.mark.parametrize(("state", "action"), [
    (PRIMARY_SCHEDULED_NOT_STARTED, ACTION_RESUME),
    (PRIMARY_COMPLETED, ACTION_START),
    (PRIMARY_REST_DAY, ACTION_START),
    (PRIMARY_NO_PLAN, ACTION_BLOCKED),
])
def test_incompatible_state_action_pair_fails_closed(state, action):
    decision = decide_today_guidance(
        read_ok=True, primary_state=state, action=action)
    assert decision.state == STATE_ERROR
    assert decision.primary_kind is None
```

Also assert `read_ok=False` and an unknown state produce `STATE_ERROR`, no candidate, and a stable diagnostic reason.

- [ ] **Step 6: Run the new tests and verify RED**

Run `python -m pytest -q tests/test_today_guidance.py`.

Expected: tests fail because `TodayDecision` and `decide_today_guidance` are absent.

- [ ] **Step 7: Implement the total compatibility and eligibility table**

Add canonical imports, `STATE_ERROR = "error"`, a total `_EXPECTED_ACTION_BY_STATE` mapping, `TodayDecision`, and the decision function. The function must validate before creating candidates:

```python
expected_action = _EXPECTED_ACTION_BY_STATE.get(primary_state)
if not read_ok or expected_action is None or action != expected_action:
    return TodayDecision(
        state=STATE_ERROR,
        primary_kind=None,
        emphasis=STATE_ERROR,
        decision_reason="canonical_state_unavailable",
    )

candidates = _eligible_candidates(primary_state, action)
winner = rank_candidates(candidates)
return TodayDecision(
    state=primary_state,
    primary_kind=winner.kind if winner else None,
    emphasis=primary_state,
    decision_reason=winner.reason if winner else "no_primary_action",
)
```

`_eligible_candidates` must use exact compatible state/action pairs and must not accept Nutrition, check-in, insight, clock, session-row, or raw plan inputs.

- [ ] **Step 8: Run focused and mutation-sensitive tests**

Run:

```powershell
python -m pytest -q tests/test_today_guidance.py
```

Expected: all tests pass. Manually swap Resume and Start priority constants, rerun the two precedence tests and observe failure, then restore the constants and rerun green.

- [ ] **Step 9: Commit the pure decision contract**

```powershell
git add app/today_guidance.py tests/test_today_guidance.py
git commit -m "feat: add Today guidance decision contract"
```

---

### Task 2: Presenter and template integration

**Files:**
- Modify: `app/today_presenter.py`
- Modify: `templates/today.html`
- Modify: `tests/test_today_v2.py`
- Modify if new copy is necessary: `locales/en.json`
- Modify if new copy is necessary: `locales/tr.json`

**Interfaces:**
- Consumes: `decide_today_guidance()` and candidate identifiers from Task 1.
- Produces: `TodayView.brief_key: str`, with `TodayView.primary` still exactly `Action | None`; `build_today_view(TodayFacts) -> TodayView` remains the route-facing API.

- [ ] **Step 1: Write failing presenter tests**

Update `tests/test_today_v2.py` to assert:

```python
def test_presenter_maps_the_ranked_semantic_action_to_copy_and_route():
    view = build_today_view(_facts(
        primary_state=STATE_IN_PROGRESS, action=ACTION_RESUME))
    assert view.primary == Action(
        "today.action.resume_workout", "/training", primary=True)
    assert view.brief_key == "today.brief.in_progress"


def test_presenter_fails_closed_on_an_incompatible_canonical_pair():
    view = build_today_view(_facts(
        primary_state=STATE_COMPLETED, action=ACTION_START))
    assert view.state == STATE_ERROR
    assert view.primary is None
    assert view.brief_key == "today.brief.error"
    assert view.secondary
```

Extend the all-state invariant to assert every view has a localized `brief_key`, at most one primary, no secondary action competing with a primary, and a continuation when primary is absent.

- [ ] **Step 2: Run the presenter tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_today_v2.py -k "presenter or primary or dead_end or brief"
```

Expected: new assertions fail because `TodayView.brief_key` and guidance integration do not exist.

- [ ] **Step 3: Integrate the decision without moving presentation ownership**

In `today_presenter.py`:

- import `decide_today_guidance` and the three candidate identifiers;
- replace `_primary_for(state, action)` with `_primary_for_kind(kind)`;
- keep `_HREF_BY_LABEL`, `Action`, insight mapping, and secondary mapping in the presenter;
- add `brief_key: str` to `TodayView`;
- call `decide_today_guidance(read_ok=facts.read_ok, primary_state=facts.primary_state, action=facts.action)` once;
- build `brief_key` from the validated semantic emphasis using the complete key `f"today.brief.{decision.emphasis}"`;
- suppress plan and insight data when the decision fails closed, matching existing read-failure behavior.

The action mapping must be closed:

```python
_PRIMARY_PRESENTATION = {
    CANDIDATE_RESUME_WORKOUT: (
        "today.action.resume_workout", _ROUTE_PLAN),
    CANDIDATE_START_WORKOUT: (
        "today.action.start_workout", _ROUTE_PLAN),
    CANDIDATE_CREATE_PLAN: (
        "today.action.create_plan", _ROUTE_PLAN),
}
```

- [ ] **Step 4: Render the supplied brief key**

Change the Today heading in `templates/today.html` from concatenating the state
to rendering the complete server-supplied key:

```jinja2
<h1 class="today-brief-line" id="today-brief-line">{{ t(today.brief_key) }}</h1>
```

Do not add a client-side state branch or new layout.

- [ ] **Step 5: Complete localization only if the semantic table needs new copy**

If all decisions reuse the existing total `today.brief.<state>` vocabulary, do
not churn either locale. If a distinct full-sentence key is necessary, add the
same key to both catalogs and update the parity test; do not concatenate
fragments.

- [ ] **Step 6: Run focused integration tests and verify GREEN**

Run:

```powershell
python -m pytest -q tests/test_today_guidance.py tests/test_today_v2.py
```

Expected: all guidance and Today tests pass.

- [ ] **Step 7: Run canonical authority and mobile parity regression**

Run:

```powershell
python -m pytest -q tests/test_workout_state.py tests/test_mobile_today_api.py tests/test_mobile_today_architecture.py tests/test_sprint12_daily_coach_discovery.py
```

Expected: all pass; web and mobile retain the same canonical day, primary state,
and workout action semantics.

- [ ] **Step 8: Commit presenter integration**

```powershell
git add app/today_presenter.py templates/today.html tests/test_today_v2.py locales/en.json locales/tr.json
git commit -m "feat(ux): orchestrate Today guidance from canonical state"
```

Omit unchanged locale paths from `git add`.

---

### Task 3: Architecture guards, performance proof, and durable contract docs

**Files:**
- Modify: `tests/test_today_v2.py`
- Modify: `tests/test_sprint12_daily_coach_discovery.py` only if its characterization must name the new layer without weakening an existing guard
- Modify: `CLAUDE.md`
- Modify: `docs/handoff.md`

**Interfaces:**
- Consumes: the completed guidance and presenter contracts.
- Produces: structural enforcement of the server/client and authority boundary plus durable PR5 documentation.

- [ ] **Step 1: Write failing architecture tests**

Add source/AST-oriented tests asserting:

- `app/today_guidance.py` imports no ORM model, Flask request/session/current user, time helper, Nutrition, check-in, Progress, provider, or AI module;
- `static/today.js` contains none of the candidate identifiers or priority constants and does not inspect `data-today-state` to choose an action;
- `templates/today.html` renders `today.primary` once and does not branch on canonical state/action strings;
- every `_EXPECTED_ACTION_BY_STATE` key exactly matches the canonical primary-state vocabulary;
- no check-in-due, readiness/recovery score, or Nutrition urgency field appears in the new decision API.

Use executable behavior assertions for precedence and state output; source checks protect only ownership boundaries.

- [ ] **Step 2: Run the architecture tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_today_v2.py tests/test_sprint12_daily_coach_discovery.py -k "architecture or authority or client or due or readiness"
```

Expected: at least the new exact-vocabulary or pure-module guard fails before its corresponding export/structure is added.

- [ ] **Step 3: Expose only the bounded constants needed by guards**

Make the minimal implementation adjustment required for exact vocabulary
comparison, such as exporting `SUPPORTED_STATE_ACTIONS` as an immutable tuple of
`(state, action)` pairs. Do not expose raw facts or make diagnostics user-visible.

- [ ] **Step 4: Add a no-I/O/query proof**

Add a test that calls `decide_today_guidance` across the full supported matrix
outside a Flask app context. Its ability to run without application setup is the
direct proof that PR5 adds zero reads. Preserve the existing route/request count
guards in `tests/test_today_v2.py` unchanged.

- [ ] **Step 5: Update durable documentation**

Append a concise UX-2 PR5 section to `docs/handoff.md` and add one concise bullet
to `CLAUDE.md` covering:

- the three candidates and exact precedence;
- canonical predicate authorities;
- total state/action compatibility and fail-closed behavior;
- supporting-only Axis Insight/Nutrition;
- deferred check-in due, recovery/readiness, daypart ranking, and Nutrition urgency;
- zero new reads and the server/client boundary;
- rollback by revert and unchanged scope.

- [ ] **Step 6: Run focused tests and static checks**

Run:

```powershell
python -m pytest -q tests/test_today_guidance.py tests/test_today_v2.py tests/test_sprint12_daily_coach_discovery.py
node --check static/today.js
git diff --check
```

Expected: all commands exit zero.

- [ ] **Step 7: Commit guards and documentation**

```powershell
git add tests/test_today_v2.py tests/test_sprint12_daily_coach_discovery.py CLAUDE.md docs/handoff.md
git commit -m "docs: record Today guidance authority"
```

Omit an unchanged discovery-test path from `git add`.

---

### Task 4: Browser matrix and final regression

**Files:**
- Modify only if required for new material states: `scripts/frontend_audit/today_pr2_matrix.py`
- Create only if repository convention stores PR5 evidence: `docs/frontend-readiness/ux2-pr5/validation-manifest.json`
- Create only if repository convention stores PR5 evidence: `docs/frontend-readiness/ux2-pr5/screenshots/`

**Interfaces:**
- Consumes: final server-rendered Today behavior.
- Produces: responsive/a11y evidence and a clean, locally committed review branch.

- [ ] **Step 1: Inspect the existing hermetic Today audit interface**

Run:

```powershell
python -m scripts.frontend_audit.today_pr2_matrix --help
```

Use its supported state/locale/viewport inputs. Do not rewrite the harness if the
existing error state already represents incompatible-pair fail-closed behavior.

- [ ] **Step 2: Run the smallest representative browser matrix**

Cover:

- English and Turkish;
- widths 320, 390, 768, 1024, and 1366;
- Resume, Start, Create Plan, rest/no-primary, completed/no-primary, and error;
- the longest localized copy and one partial-data state.

Each cell must prove no horizontal overflow, clipped copy, action-bar occlusion,
raw locale key, console error, duplicate primary CTA, or dead end. If the current
harness cannot seed `in_progress`, extend only its fixture/state list and test
that extension before the full matrix.

- [ ] **Step 3: Run adjacent product regressions**

Run:

```powershell
python -m pytest -q tests/test_app_shell.py tests/test_nav_contract.py tests/test_coach_entry_convergence.py tests/test_i18n.py
```

Expected: four-primary navigation, absent drawer/global Coach FAB, Today Coach
boundary, and locale parity all pass.

- [ ] **Step 4: Run the full non-load regression suite**

Run:

```powershell
python -m pytest -q
```

If the known Windows deploy test is the only platform failure, rerun with that
exact node excluded and record both commands and outputs. Do not generalize from
partial suites.

- [ ] **Step 5: Run database and static final gates**

Run:

```powershell
$env:FITX_SKIP_DB_INIT='1'
flask --app starter db check
Remove-Item Env:FITX_SKIP_DB_INIT
node --check static/today.js
git diff --check
git status --short
```

Expected: no schema drift, valid JavaScript, clean diff, and only intended audit
evidence changes before their commit.

- [ ] **Step 6: Commit audit evidence only when generated and repository-owned**

```powershell
git add scripts/frontend_audit/today_pr2_matrix.py docs/frontend-readiness/ux2-pr5
git commit -m "test: validate Today guidance matrix"
```

Skip this commit if no tracked harness/evidence file changed.

- [ ] **Step 7: Perform independent review**

Review the complete range `ab178830..HEAD` for P0/P1/P2/P3, with explicit focus
on false health guidance, cross-user reads, incorrect precedence, multiple CTAs,
device clock use, fabricated due/urgency/readiness, duplicated workout authority,
partial-failure reassurance, mobile/web disagreement, unnecessary queries, and
responsive regressions. Fix every introduced P0/P1 using a new failing test first.

- [ ] **Step 8: Verify final Git state**

Run:

```powershell
git status --short
git log --oneline --decorate origin/main..HEAD
git rev-list --left-right --count origin/main...HEAD
git merge-base --is-ancestor ab178830 HEAD
```

Expected: empty status, PR4 is an ancestor, branch is zero behind latest
`origin/main`, and all commits are local only.
