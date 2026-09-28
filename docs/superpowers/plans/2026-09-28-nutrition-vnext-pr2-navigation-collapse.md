# Nutrition PR2 implementation plan

> Execute inline with superpowers:executing-plans; review the final branch independently.

**Goal:** exactly two Nutrition modes, Today and Plan, preserving all existing workflows.
**Spec:** `docs/NUTRITION_VNEXT_CONTRACT.md` and the owner's complete PR2 task, recovered from the previous session.
**Architecture:** retain the canonical page and its existing read/write handlers. Nest native Diary, History and Water disclosures in Today, each with a labelled region. Place the existing Supplements link in Plan. Use roving primary tabs and page-local history state for mode/disclosure restoration; add no routes, storage, providers or requests.
**Tech stack:** Jinja, existing token CSS, vanilla JavaScript, pytest and Chromium.

## Constraints

Baseline `4cf447e4a9d356cdd84603993d62d96759200489`, green CI `36341699945`. Preserve MealLog, NutritionPlan, WaterLog and Supplement authority; no backend, API, mobile, schema, hydration, planned-meal or PR3 changes. Preserve the existing initial active-plan and water reads and lazy Diary/History reads. No push, PR, merge or deployment.

## Review focus

Native disclosure toggles must load once per deliberate opening; restoration must not double-load. Keyboard selection must not hide focus. Back must return to the originating summary or mode control. Plan failure must not affect tab selection. Existing global navigation and programmatic `switchTab`/`fxGoToPlanTab` entries must retain their intent.

## Tasks

- [x] Capture pre-change server query count, browser traffic, five-control semantics, EN/TR geometry and programmatic entry points.
- [x] Write dedicated rendered and browser tests; prove the two-mode assertion fails on the old five-tab markup.
- [x] Replace five-peer markup with two tabs and three inline Today disclosures; move Supplements into Plan; add only navigation copy in EN/TR.
- [x] Implement roving keyboard selection, deterministic local Back/reload restoration, legacy function mapping, and lazy disclosure hooks. Keep existing initialization and business handlers untouched.
- [x] Replace obsolete five-tab assertions with stronger two-mode/disclosure invariants. Run dedicated and directly affected regressions, locale and design checks.
- [x] Run M1–M9 controlled non-vacuity mutations, using scoped in-memory mutations with automatic restoration. Review the diff independently, resolve findings, recheck main, commit locally, and record the requested final report.
