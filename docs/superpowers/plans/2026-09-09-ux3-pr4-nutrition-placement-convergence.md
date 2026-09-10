# UX-3 PR4 Nutrition Placement Convergence Implementation Plan

> **For Codex:** Execute with test-driven development and verify each checkpoint before continuing.

**Goal:** Make Nutrition an explicit, truthful child of global Plan while preserving `/nutrition` as the canonical five-area Nutrition workspace and leaving every Nutrition write contract unchanged.

**Architecture:** Extend the existing server-side Plan facts seam with three independently isolated, owner-scoped reads: latest `UserSession` calorie target, current-day canonical `MealLog` aggregate, and `NutritionPlan` presence. Carry those facts through the pure Plan presenter and render a bounded read-only summary. Add compact `Plan / Nutrition` orientation to `/nutrition`, set global Plan active state, and clarify the local plan tab as “Nutrition Plan.”

**Tech Stack:** Flask, SQLAlchemy, Jinja, vanilla CSS/JS, pytest.

**Spec:** `docs/PLAN_DOMAIN_CONVERGENCE.md` (UX-3 PR4) plus the user-supplied `OneDrive/Masaüstü/ux3-pr4.txt` implementation brief.

---

## Task 1: Characterize and lock the Nutrition placement contract

- [ ] Add focused tests for target present/missing, intake zero/nonzero, Nutrition Plan present/missing, and independent failures.
- [ ] Add presenter purity/copy-through assertions and query-budget assertions for session flag OFF/ON.
- [ ] Add route/UI tests for Plan → Nutrition hierarchy, five local areas, global Plan active state, locale parity, single Nutrition CTA, and unchanged stable routes.
- [ ] Run the new tests and confirm the expected RED failures.

## Task 2: Implement bounded canonical Nutrition facts

- [ ] Import only `MealLog`, `NutritionPlan`, SQL aggregate helpers, and `app_today` into the existing Plan read layer.
- [ ] Give target, current-day intake, and plan-presence reads their own nested transaction boundary.
- [ ] Represent zero matching MealLog rows as known zero and read exceptions as unavailable.
- [ ] Keep Nutrition sub-read failures independent from each other, Supplements, and Training.
- [ ] Run focused tests to GREEN.

## Task 3: Present the read-only Plan summary

- [ ] Extend frozen `PlanFacts`/`PlanView` with stable non-localized state/value fields.
- [ ] Copy facts through the pure presenter without I/O or inference.
- [ ] Render target, current-day intake, restrained progress only when both facts are known, Nutrition Plan presence, and exactly one “Open Nutrition” domain CTA.
- [ ] Preserve Supplements as the nested boundary without implementing PR5.
- [ ] Run focused presentation/template tests.

## Task 4: Converge `/nutrition` placement without changing behavior

- [ ] Set global primary navigation active state to Plan.
- [ ] Add compact linked hierarchy context `Plan / Nutrition` above the existing page title.
- [ ] Preserve Today, Diary, Nutrition Plan, History, and Water as the five local tabs with existing actions and routes.
- [ ] Add only scoped responsive/focus styles; preserve existing tokens and visual language.
- [ ] Verify EN/TR copy and responsive widths 320, 390, 768, 1024, and 1366.

## Task 5: Regression, documentation, and handoff

- [ ] Run focused Plan/Nutrition tests, browser workflows, JS checks, and broad regression.
- [ ] Confirm no provider, LLM, history, water, or write call is added to Plan rendering.
- [ ] Update `docs/PLAN_DOMAIN_CONVERGENCE.md`, `docs/handoff.md`, and `CLAUDE.md` with measured query counts and verification evidence.
- [ ] Fetch/rebase if `origin/main` advanced, rerun verification, obtain independent code review, and resolve findings by severity.
- [ ] Commit locally as `feat(ux): converge Nutrition placement in Plan`; do not push, merge, create a PR, or deploy.
