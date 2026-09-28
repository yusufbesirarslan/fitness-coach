# Nutrition VNext PR2: navigation and local validation

## 1. Final verdict

**LOCAL SHIP-READY.** Final verification and independent review passed. The result is committed locally; no remote shipment is authorized in this task.

## 2. Baseline

- origin/main and merge-base: `4cf447e4a9d356cdd84603993d62d96759200489`.
- Exact-main CI: `36341699945`, completed/success; pytest, PostgreSQL concurrency, schema drift, Linux production locks, image revision immutability all successful.
- Branch: `feat/nutrition-vnext-pr2-navigation-collapse`.
- Fresh worktree: `C:/Users/yusuf/fitness-coach/.worktrees/nutrition-vnext-pr2-navigation-collapse`.
- Final commit: recorded by the final report and `git rev-parse HEAD` after local commit.
- Final remote fetch still showed the same main SHA. The one post-PR1 commit (#356) changes Coach grounding only; Nutrition routes, markup, navigation, models and IA assumptions were unchanged.

## 3. Pre-change navigation

Five buttons were peer tabs: Today, Diary, Nutrition Plan, History, Water. All five were tab stops and labelled five tabpanels; selection lived in CSS classes/ARIA, with no query/hash parsing, navigation storage or history restoration. The page and its callers used plain `/nutrition`, `switchTab(name, button)`, and `fxGoToPlanTab()` from the no-plan shortcut. Repository searches found no panel-specific URL protocol to preserve. `fc_water` storage concerns existing hydration data only.

The browser characterization in `evidence/nutrition-pr2/baseline.json` records initial and lazy requests, SQL count and geometry at 320/390/1366. Both locale contracts were characterized by the baseline test run; post-change browser coverage tests both locales at all required widths.

## 4. Implemented IA

```text
Nutrition (/nutrition)
├── Today
│   ├── Daily state and existing logging
│   ├── Diary (meal builder, inline disclosure)
│   ├── History (retrospective detail, inline disclosure)
│   └── Water (inline capability/detail)
└── Plan
    ├── Existing Nutrition Plan
    └── Supplements → /supplements
```

Exactly one tablist and two tabs remain. Contextual disclosures appear at their relevant locations within Today, without a second navigation bar or new card family.

## 5. Today mode

The current ring/macros, meals, logging, quick-add, Coach handoff and review remain unchanged. History follows meals; Water detail follows the existing hydration quick action; Diary remains the explicit meal builder below the existing review. Native summary controls keep content inside Today and provide opening/closing without a child-route dead end.

## 6. Plan mode

The existing plan form, generation, scores, validation, proposal/save, active plan and reset behavior remain intact. A Nutrition Plan heading differentiates the content from the primary mode label Plan. Supplements is the existing child link inside this mode, with no CRUD or cabinet query on Nutrition. The no-plan shortcut retains its existing handoff and gains native keyboard activation.

## 7. Legacy entry compatibility

| Actual entry | New context | Proof |
| --- | --- | --- |
| `/nutrition` from Plan/Coach/Profile/current links | Today | Existing handoff and placement browser suites |
| `switchTab('today', button)` | Today | Dedicated programmatic-entry test |
| `switchTab('plan', button)` | Plan | Dedicated programmatic-entry test |
| `fxGoToPlanTab()` | Plan | Real no-plan shortcut keyboard activation |
| `switchTab('diary'/'history'/'water', button)` | Today with requested disclosure open | Dedicated programmatic-entry tests |
| `/supplements` | Canonical cabinet | Existing placement and CRUD suites |

No invented legacy query/hash aliases or new route names were introduced. The new History API state contains only mode/disclosure booleans; browser Back/forward and reload restore state and focus. Restored open children remain lazy on Plan reload, then load once when Today becomes visible. Re-selecting the current state adds no redundant history entry.

## 8. Route / authority proof

Production edits are confined to `templates/nutrition.html`, `static/nutrition.css`, `static/nutrition.js`, and two locale catalogs. No backend, API, model or migration file changed. Existing canonical MealLog correction/log/delete, Diary staging and explicit commit, NutritionPlan save/read, WaterLog reads/writes and Supplement cabinet CRUD remain the sole authorities. The JavaScript diff changes navigation and the no-plan navigation shortcut; all data and mutation handlers remain intact.

## 9. Performance

| Measurement | Before | After |
| --- | --- | --- |
| Initial server SQL statements (including shared auth/daily-login hooks) | 9 | At most 9, enforced by rendered-route test |
| Initial non-static requests | 6 | Same exact six |
| Initial Nutrition data requests | MealLog today, shared active plan, Water | Same |
| Initial shell reads | Notification count, Coach history | Same |
| Diary | One `/api/diary/today` on opening | Same |
| History | One `/meal-log/history` on opening | Same |
| Selecting Plan or Water detail | No additional request | Same |
| Provider/AI calls on initialization | 0 | 0 |

`after-requests.json` records the complete initial request set. No new eager food/barcode/menu/Diary/History/Supplement/AI initialization, polling or provider call was added. Existing active-plan promise sharing and eager water initialization remain unchanged.

## 10. Responsive

Dedicated Chromium coverage checks 320, 390, 430, 768, 1024 and 1366 in EN/TR, with empty and canonical populated data. It checks both modes, all open child workflows, visibility, target geometry and page/primary-nav overflow. Representative Today and Plan images are in `evidence/nutrition-pr2`; 320px EN/TR images were inspected visually. Both primary modes fit immediately at 320px.

## 11. Accessibility

One Nutrition H1; two labelled primary tabs and matching tabpanels; selected state, roving tabindex, ArrowLeft/Right and Home/End. Native summary controls have no tab role and label child regions under Today. Priority controls meet the 44px floor; focus remains visible and returns to the control on Back. Selected mode has a border indicator in addition to color. The no-plan shortcut uses a native button. Existing global navigation and the canonical Supplements landmark remain unchanged.

## 12. Failure semantics

No new fallback, invented totals or empty-state conversion. Existing target absent/unknown behavior is covered by the retained browser suite. Injected Diary/History/Plan/Water read failures leave mode controls functional. This is shell isolation evidence, not a claim that all legacy child failure handling is repaired: hydration cached/zero-like reads and optimistic confirmation remain a known separate defect, and plan/history failure presentation is preserved.

## 13. Test results

- Baseline: 50 passed.
- Initial dedicated PR2 run: 16 passed; the two-mode assertions were first observed failing against the old five-tab page in EN/TR.
- Broad affected regression: 482 passed, one obsolete five-tab assertion; that assertion was replaced with two-mode/all-capability invariants.
- Corrected placement + plan validation: 114 passed.
- Node plan-rendering/XSS/save truthfulness: 9 passed. Its minimal browser VM now supplies the History API used by navigation.
- Restoration regression: 1 passed.
- M1–M9 non-vacuity: 9 passed. Each scoped mutation causes its corresponding normal contract/browser assertion to fail; template loader patches and browser script interception leave production files untouched and are automatically restored.
- Final dedicated PR2 + mutations: **32 passed** (`pr2-final.txt`).
- Final focused rendered, keyboard, Supplements placement and locale verification: **54 passed** (`final-checks.txt`).
- Affected browser batch: 61 passed; all three obsolete placement assertions were corrected and passed in subsequent focused verification.
- JavaScript syntax, Python compileall and whitespace diff checks: verified separately before local commit.

Linux/PostgreSQL CI remains authoritative for later shipping; no new remote CI was triggered by this local-only task.

## 14. Review

Independent reviewer found and verified resolution of one Plan-reload lazy-child restoration defect. A nonblocking redundant-history issue was also resolved. Historical pre-correction failures are labelled `*-initial.txt`; final green checks have separate artifacts. Review checked two-mode hierarchy, absence of a second tab bar, reachability, authority/request boundaries, Back/focus, legacy functions, 320px usability, Supplements ownership, canonical route and scope. Final independent review: P0=0, P1=0, P2=0, P3=0.

## 15. Files changed

- `docs/NUTRITION_VNEXT_PR2.md`
- `docs/evidence/nutrition-pr2/after-requests.json`
- `docs/evidence/nutrition-pr2/baseline.json`
- `docs/evidence/nutrition-pr2/browser-corrections-initial.txt`
- `docs/evidence/nutrition-pr2/browser-initial.txt`
- `docs/evidence/nutrition-pr2/final-checks.txt`
- `docs/evidence/nutrition-pr2/non-vacuity.txt`
- `docs/evidence/nutrition-pr2/placement-final.txt`
- `docs/evidence/nutrition-pr2/plan-en-1366.png`
- `docs/evidence/nutrition-pr2/plan-en-320.png`
- `docs/evidence/nutrition-pr2/plan-render.txt`
- `docs/evidence/nutrition-pr2/plan-tr-1366.png`
- `docs/evidence/nutrition-pr2/plan-tr-320.png`
- `docs/evidence/nutrition-pr2/pr2-final.txt`
- `docs/evidence/nutrition-pr2/regression-corrections.txt`
- `docs/evidence/nutrition-pr2/regression-initial.txt`
- `docs/evidence/nutrition-pr2/today-en-1366.png`
- `docs/evidence/nutrition-pr2/today-en-320.png`
- `docs/evidence/nutrition-pr2/today-tr-1366.png`
- `docs/evidence/nutrition-pr2/today-tr-320.png`
- `docs/superpowers/plans/2026-09-28-nutrition-vnext-pr2-navigation-collapse.md`
- `locales/en.json`
- `locales/tr.json`
- `static/nutrition.css`
- `static/nutrition.js`
- `templates/nutrition.html`
- `tests/js/nutrition_plan_render.test.js`
- `tests/test_nutrition_vnext_pr2_navigation_browser.py`
- `tests/test_nutrition_vnext_pr2_navigation_contract.py`
- `tests/test_nutrition_vnext_pr2_non_vacuity.py`
- `tests/test_ux3_pr4_nutrition_placement.py`
- `tests/test_ux3_pr4_nutrition_placement_browser.py`
- `tests/test_ux3_pr5_supplements_placement.py`
- `tests/test_ux3_pr5_supplements_placement_browser.py`
- `tests/test_ux4_pr6_nutrition_supplements_browser.py`
- `tests/test_ux4_pr6_nutrition_supplements_contract.py`

## 16. Out-of-scope confirmation

No PR3 redesign, hydration repair, Log Food convergence, planned-meal fix, mobile, new API, schema, migration, Coach/provider/AI or global navigation changes.

## 17. Git state

Local commit only; clean worktree and exact ahead/behind recorded after commit. No push, PR, merge or deployment was performed.

## 18. Downstream

PR2 provides the intended structural Today/Plan IA for later work. **HYDRATION RELIABILITY REPAIR: STILL REQUIRED BEFORE NUTR-PR3.** Planned-meal user-wide idempotency replay before current-plan validation remains deferred unchanged. This map translates to future native implementation without new backend authority.

## 19. Next action

Run NUTR-PR2 shipping workflow: push exact final head, open PR, require exact-head CI, then perform independent merge-readiness review. That work requires the separate shipping task; it was not performed here.
