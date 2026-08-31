# Mobile Training PR2 Native Read Contracts Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Add three canonical, bearer-authenticated, read-only mobile Training contracts for preference metadata, the authenticated owner's current plan, and opaque workout-detail lookup.

**Architecture:** Extend the existing feature-gated `/api/v1` blueprint with a thin Training route module. Put all projection, strict validation, canonical exercise lookup, current-plan selection, Istanbul-day resolution, and deterministic owner-bound HMAC workout-reference logic in a standalone read service. Reuse the existing preference/capability, plan schema, exercise catalog, active-plan selector, and workout-state authorities; do not call browser controllers or providers and do not write persistence.

**Tech Stack:** Flask, SQLAlchemy read queries, Python stdlib `hmac`/`hashlib`/`base64`, pytest.

**Specification:** `docs/mobile/training-vertical-slice.md`, especially sections G, M, N, and R; approved PR2 execution brief from `OneDrive/Masaüstü/mc-pr2.txt`.

---

## Task 1: Pin the public metadata and plan contracts with failing API tests

**Files:**
- Create: `tests/test_mobile_training_api.py`
- Reference: `tests/test_mobile_today_api.py`
- Reference: `tests/test_sprint11_training_preference_contract.py`

1. Add literal, exact-key assertions for `GET /api/v1/training/preferences`: bearer auth, cookie-only rejection, no-store, contract version, every canonical field/default/choice, and provider-independent capability constraints.
2. Add exact assertions for `GET /api/v1/training/plans/current`: no plan is HTTP 200 and exactly `{"plan": null}`; a representative canonical seven-day plan returns lineage, mutation version, UTC creation time, nullable numeric score, server-resolved current workout reference, and bounded day/exercise DTOs.
3. Cover workout, cardio, and rest days; require catalog `exercise_id` and canonical display name; pin rest as bounded `{display_text, seconds}` with seconds populated only for losslessly recognized forms.
4. Run `python -m pytest -q --basetemp=.pytest_tmp_pr2_red tests/test_mobile_training_api.py` and confirm failures are missing routes, never fixture/setup failures.

## Task 2: Implement the preference and current-plan projections minimally

**Files:**
- Create: `app/services/mobile_training.py`
- Create: `app/blueprints/mobile_training.py`
- Modify: `app/blueprints/mobile_api.py`

1. Build deterministic preference metadata directly from the canonical constants and capability matrix vocabulary. Sort unordered canonical sets so JSON output is stable.
2. Implement a read-only active-plan projection using `today_facts.get_active_plan`, `validate_plan_structure(..., allow_exercise_id=True)`, `exercise_catalog.resolve_exercise(exercise_id=...)`, `app_today`, and `resolve_workout_state(..., plan=..., strict_reads=True)`.
3. Reject unreadable JSON, invalid canonical shape, missing/unknown/inactive exercise identity, invalid lineage/version/score/timestamp, or catalog configuration failure through a single internal `PlanUnprojectable` category.
4. Map canonical day types to closed native kinds, carry bounded focus/duration/calories, and emit exact exercise fields: `exercise_id`, `display_name`, `sets`, `reps`, `rest`, and `notes`.
5. Parse rest seconds only for exact canonical `N sn`, `N dk`, and `0` representations within a bounded seconds range; otherwise retain display text and return `seconds:null`.
6. Register thin bearer-only routes on the existing blueprint. Map `PlanUnprojectable` to the existing mobile envelope with `TRAINING_PLAN_UNPROJECTABLE`, HTTP 409, and `retryable:false`.
7. Rerun the Task 1 test file and make it green without changing `/api/v1/today`.

## Task 3: Pin and implement opaque owner-bound workout lookup

**Files:**
- Modify: `tests/test_mobile_training_api.py`
- Modify: `app/services/mobile_training.py`
- Modify: `app/blueprints/mobile_training.py`

1. Add failing tests proving every non-rest day publishes a stable bounded URL-safe reference and rest days publish null.
2. Add workout-detail exact-schema tests linking the reference to authenticated owner, current plan lineage, mutation version, and slot.
3. Add adversarial cases: another user's reference, one-character tampering, random valid-shape token, malformed token, oversized path input, plan replacement, mutation-version change, and rest-slot lookup.
4. Implement a domain-separated deterministic HMAC token over owner ID, plan lineage, mutation version, and slot. Validate syntax before lookup and compare candidates with `hmac.compare_digest`; never expose or decode internal IDs.
5. Resolve only against the authenticated user's current plan. Return private not-found behavior for malformed/non-workout references and `TRAINING_WORKOUT_STALE` HTTP 409 for syntactically valid references that do not match the current owned plan revision.
6. Run the focused API tests and complete the red/green cycle.

## Task 4: Prove architecture, read-only behavior, compatibility, and cost bounds

**Files:**
- Create: `tests/test_mobile_training_architecture.py`
- Modify: `tests/test_mobile_auth_feature_gate.py` only if its approved-route allow-list requires the three new routes
- Reference: `tests/test_mobile_today_architecture.py`

1. Add behavioral and AST/import guards proving the new route/service do not import the browser Training blueprint, ORM write APIs, AI/provider modules, prompt modules, or migration/model definitions.
2. Add provider detonator tests for all three reads and SQLAlchemy flush/write guards for current-plan and workout reads.
3. Add query-budget tests showing preference metadata performs no domain query and plan/workout reads remain constant-bounded independent of exercise count.
4. Add native-auth flag-off and shared bearer-decorator checks. Re-run existing Today API/architecture, browser Training, preference, exercise-authority, and workout-state suites to prove compatibility.
5. Run `python -m pytest -q --basetemp=.pytest_tmp_pr2_targeted tests/test_mobile_training_api.py tests/test_mobile_training_architecture.py tests/test_mobile_auth_feature_gate.py tests/test_mobile_today_api.py tests/test_mobile_today_architecture.py tests/test_sprint11_training_preference_contract.py tests/test_sprint11_exercise_authority.py tests/test_workout_state.py`.

## Task 5: Update only the canonical Training specification

**Files:**
- Modify: `docs/mobile/training-vertical-slice.md`

1. Replace PR2 recommendations with the shipped exact response schemas, closed enums, rest representation, workout-reference privacy/staleness behavior, and typed error codes.
2. Record evidence-backed query budgets and the guarantee that Today/browser Training/provider/write behavior is unchanged.
3. Do not create another architecture document.

## Task 6: Validate, review, and publish the PR without merging

**Files:** all changed files

1. Run formatting/static checks already configured by the repository, compile changed Python modules, then run the targeted suite and full `python -m pytest -q --basetemp=.pytest_tmp_pr2_full`.
2. Use `superpowers:verification-before-completion`, inspect `git diff --check`, `git status`, and the complete diff, and perform an adversarial P0/P1 review against every acceptance criterion.
3. Use `superpowers:requesting-code-review`; address only evidence-backed findings and rerun affected tests.
4. Make short English commits, push `mobile-training-pr2-native-read-contracts`, and open a PR titled `feat(api): add canonical mobile Training read contracts` with testing and risk evidence. Do not merge.
5. Wait for required checks on the exact pushed HEAD. If a check fails, inspect logs, fix test-first, push, and verify the new exact HEAD.
6. Produce the execution brief's exact A-N final report, including PR URL, branch, commits, validation commands/results, CI state, P0/P1 counts, and explicit no-merge status.
