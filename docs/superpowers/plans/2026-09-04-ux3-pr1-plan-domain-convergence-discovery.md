# UX-3 PR1 Plan Domain Convergence Discovery Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce and locally commit a repository-grounded Plan-domain architecture contract and durable characterization tests without changing production behavior.

**Architecture:** Treat Product IA ownership, stable web routes, backend authorities, and user workflows as separate contracts. Inspect current `origin/main`, preserve every existing production authority and URL, compare viable Plan landing structures, select one explicitly, and encode only load-bearing current facts in characterization tests.

**Tech Stack:** Flask, Jinja, SQLAlchemy, pytest, Markdown, Flutter repository/docs as read-only parity evidence.

**Spec:** `C:/Users/yusuf/OneDrive/Masaüstü/ux3-pr1.txt`

**Execution status:** Complete on 2026-09-05. The checklists below are the
pre-execution procedure; completion evidence and final command results are in
the local commit handoff and `docs/PLAN_DOMAIN_CONVERGENCE.md`.

## Global Constraints

- Base all findings on fetched `origin/main`. It advanced during execution from
  `000e322ccce1ebd24b0eede285b62b6604eda016` (#282) to
  `ddfbe043c8f3b472f1b2ebbc1fa222082c0804cc` (#283); the branch and audit were
  updated to the latter before commit.
- Do not modify templates, CSS, JavaScript, routes, APIs, backend logic, flags, models, migrations, auth, providers, or mobile code.
- Keep `/training`, `/nutrition`, and `/supplements` stable; do not create `/plan` in PR1.
- Product IA remains Today / Plan / Coach / Progress and is not reopened.
- Final production diff classification must contain only documentation and tests.
- Do not push, open a PR, merge, deploy, or begin UX-3 PR2.

---

### Task 1: Establish baseline and read product authorities

**Files:**
- Read: `CLAUDE.md`
- Read: `docs/PRODUCT_IA.md`
- Read: `docs/handoff.md`
- Read: `docs/TRAINING_GENERATOR.md`
- Read: `docs/TRAINING_PLANNING.md`
- Read: `docs/WORKOUT_STATE.md`
- Read: `docs/MOBILE_TODAY.md`
- Read: `docs/mobile/training-vertical-slice.md`
- Read: `docs/MOBILE_NUTRITION.md`
- Read: `docs/FEATURE_FLAGS.md`
- Read: `docs/ROLLOUT.md`

- [ ] Verify branch, worktree, clean status, HEAD, `origin/main`, and PR #282 ancestry.
- [ ] Read the product authorities and record every documentation/code drift relevant to Plan.
- [ ] Identify the directly related baseline test files from repository truth and run them with an isolated pytest base temp.

### Task 2: Map current routes, surfaces, flags, and workflows

**Files:**
- Read: `app/nav.py`
- Read: `app/hooks.py`
- Read: `app/feature_flags.py`
- Read: relevant files under `app/blueprints/`, `templates/`, `static/`, and `tests/`

- [ ] Inventory `/training`, `/nutrition`, `/supplements`, nested workflows, entry points, deep links, active nav ownership, read/write authorities, flags, and duplicated homes.
- [ ] Compare `templates/training.html` and `templates/plan.html` structurally, including dependencies, states, CTAs, generation, execution, weekly planning, Coach entry, and responsive behavior.
- [ ] Audit `UIUX_PLAN_V2_ENABLED` and every materially related Training/Nutrition flag, including defaults, selectors, observability, rollback, and test coverage.
- [ ] Map no-plan, existing-plan, workout, Nutrition, Supplements, Coach mutation, and Today-to-Plan workflows and state/failure semantics.

### Task 3: Map backend authorities and client boundaries

**Files:**
- Read: relevant models, selectors, presenters, services, API routes, web routes, mobile docs, and sibling native repository files.

- [ ] Map Training preferences, generation, active persistence, lineage/versioning, current selection, workout state, session lifecycle, completion, weekly planning, and Coach mutation.
- [ ] Map Nutrition targets, diary, meal mutations, food search, barcode, menu scan, planning, history, water, supplements, and provider serving truth.
- [ ] Separate Product ownership from backend authority and identify Account/Profile and Progress leakage.
- [ ] Measure server reads, browser bootstrap calls, provider calls, query evidence, and asset dependencies enough to constrain a future lightweight Plan shell.
- [ ] Assess native conceptual parity read-only.

### Task 4: Select and document the target architecture

**Files:**
- Create: `docs/PLAN_DOMAIN_CONVERGENCE.md`
- Modify: `docs/handoff.md`
- Modify: `CLAUDE.md`

- [ ] Score candidates A-D from the brief on all twelve required dimensions and name a recommended architecture and runner-up.
- [ ] Define the target Plan contract, stable-route table, canonical homes versus contextual entries, Today/Coach/Progress/Account boundaries, mobile parity, loading boundary, risks, and definition of complete.
- [ ] Derive bounded downstream PRs with prerequisites, non-goals, acceptance criteria, rollback, risks, and a dependency graph; name exactly one next PR.
- [ ] Add concise durable summaries to `docs/handoff.md` and `CLAUDE.md`; leave `docs/PRODUCT_IA.md` unchanged unless a proven contradiction requires a small additive clarification.

### Task 5: Add durable characterization tests

**Files:**
- Create or modify only focused files under `tests/` selected from repository conventions.

- [ ] Add behavioral tests for load-bearing current facts: stable route endpoints, Plan ownership resolution, exact `/training` template selection under `UIUX_PLAN_V2_ENABLED`, and any other non-vacuous fact needed by the selected migration sequence.
- [ ] Demonstrate non-vacuity by temporarily falsifying each protected contract or its expectation, observing the focused test fail for the intended reason, then restore the correct contract and rerun green.
- [ ] Avoid brittle DOM snapshots and do not lock accidental visual markup.

### Task 6: Validate, falsify, and commit

**Files:**
- Verify: all changed docs/tests and directly related existing test files.

- [ ] Run new characterization tests.
- [ ] Run existing nav, Plan/Training, Nutrition, Supplements, flags, Today deep-link, Coach mutation, and workout-state tests selected from the inventory.
- [ ] Run available documentation/source consistency checks and `git diff --check`.
- [ ] Confirm the diff contains only docs and tests and no production behavior changes.
- [ ] Adversarially review and try to falsify the landing recommendation, route stability, authority maps, flag conclusion, mobile parity, and PR sequence; fix all factual P0/P1 issues and record remaining P2/P3/UNKNOWN items.
- [ ] Inspect `git status`, `git diff --stat`, and `git diff`; commit locally as `docs(ux): define Plan domain convergence architecture`.
- [ ] Verify the branch is clean, exactly one local commit ahead of `origin/main`, and unpushed.
