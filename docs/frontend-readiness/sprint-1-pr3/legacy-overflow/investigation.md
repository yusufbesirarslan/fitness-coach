# Legacy `/training` 320px horizontal-overflow — root-cause investigation & fix

**Context.** The PR3 Plan matrix (`validation-manifest.json`) reported 84/86, with two
failing cells:

- `A-plan-20__active_plan__320__en__plan-off_coach-off_weekly-off`
- `A-plan-20__active_plan__320__tr__plan-off_coach-off_weekly-off`

Both render the **legacy** `training.html` (Plan V2 flag **OFF**) — i.e. the
independently-reversible rollback path. `answer.txt` (2026-07-26) declined to accept
"pre-existing legacy, out of scope" as an implicit quality-gate exception and required a
real reconciliation: prove the defect against base `9400641`, identify the *precise*
legacy cause (not assumed from the aggregate overflow), and apply the smallest safe fix
if one exists that is PR3-scoped.

This note records that reconciliation. Raw evidence lives beside it:
`diag-overflow-base.json`, `diag-overflow-head.json`, `diag-overflow-headfix.json`, and
the four before / two after screenshots.

---

## 1. Reproduction & proof it is pre-existing legacy

Measured with the hermetic Sprint-0 audit harness (`create_audit_app` + `AuditServer` +
fixed browser clock + Chromium rev 1228), forcing the **exact** failing-cell environment:
Nav ON, Today ON, **Plan/Coach/Weekly OFF**, scenario `active-workout`, viewport
**320×720**, locale EN and TR.

**Exact base command** (run under WSL Ubuntu-24.04, Sprint-0 venv):

```
PLAYWRIGHT_BROWSERS_PATH=/home/yusuf/.cache/axisai-sprint0-playwright \
  /home/yusuf/axisai-sprint0-audit-venv/bin/python diag_overflow.py \
    --repo <REPO> --label <base|head|headfix> --out <this dir>
```

Two repos were measured: a **clean detached checkout of base `9400641`** (git worktree,
none of PR3's changes present) and the **PR3 HEAD worktree with every PR3 flag OFF**.

| repo / locale | doc scrollWidth | doc clientWidth | overflow | `#active-plan-view` scroll/client | `plan_present` |
|---|---|---|---|---|---|
| base 9400641 / EN | 344 | 320 | **24px** | 328 / 288 | true |
| base 9400641 / TR | 356 | 320 | **36px** | 340 / 288 | true |
| HEAD flags-OFF / EN | 344 | 320 | **24px** | 328 / 288 | true |
| HEAD flags-OFF / TR | 356 | 320 | **36px** | 340 / 288 | true |

The base and HEAD-flags-OFF numbers are identical, and the rendered screenshots are
**byte-for-byte identical** (`legacy-training-320-en-base.png` == `…-head.png`, 48 272
bytes; TR == 48 437 bytes). This proves:

- the overflow is **pre-existing on base `9400641`** — PR3 did not introduce it; and
- it reproduces with **every PR3 flag OFF** (the rollback path); and
- PR3's only shared-file changes on this render path (`nav.css` adds unused
  `.coach-page-*` classes; `actions.js` adds a `window.fxReload` function;
  `coach_widget.js` lifecycle guard) do **not** alter the legacy layout — the byte-identical
  render is the mechanical proof. The legacy render files themselves
  (`training.html`, `_head/_nav/_actionbar`, `theme.css`, `training.css`, `training.js`)
  are `git diff`-identical to base.

## 2. Precise legacy cause (measured, not assumed)

The overflow is **not** from a modal, the nav, a fixed width, a transform, or a long
single label in isolation. Per-element enumeration (every `body *` whose
`getBoundingClientRect().right > clientWidth+1`) plus the widest-reach ancestor chain
pinpoints one element as the sole `scrollWidth` driver:

```
main.main-content        clientWidth=320  scrollWidth=344(EN)/356(TR)  padding 16/16 → content box 288
  #active-plan-view       clientWidth=288  scrollWidth=328(EN)/340(TR)
    #wstats               grid-template-columns (computed):
                            EN: 106.7px 106px 99.7px   (tracks 312.4 + 2×8 gap = 328.4)
                            TR: 122.9px 106px 94.6px   (tracks 323.5 + 2×8 gap = 339.5)
      .stat-card          right = 344.41(EN) / 355.52(TR)   ← element with max right edge
```

**Rule:** `static/training.css` — `.wstats { display: grid;
grid-template-columns: repeat(3, 1fr); gap: var(--space-2); }` (no narrow-width
fallback). The three `.stat-card` grid items have the default `min-width: auto`, so each
`1fr` track cannot shrink below its **text min-content** (~100–123px). Three such tracks
plus two gaps total 328px (EN) / 340px (TR), which exceeds the 288px content box and the
320px viewport.

The overflow is **locale-dependent** (TR 36px > EN 24px) precisely because the first
Turkish stat label forces a wider track (122.9px vs 106.7px). A fixed-width/ring/canvas
cause would overflow identically in both locales — it does not, which is corroborating
evidence that the cause is min-content text sizing inside a fixed 3-column grid.

`#cw-send` (the floating coach widget's send button) also reports a rect past the edge,
but at 332px < the 344px `scrollWidth`; it is `position: fixed` and is **not** the
document `scrollWidth` driver — confirmed because Plan V2 (`plan.html`) loads the same
`coach_widget.js` yet its 320px cells are overflow-clean.

## 3. Smallest safe compatibility fix (implemented)

```css
/* static/training.css, immediately after the base .wstats rule */
@media (max-width: 380px) {
  .wstats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
```

At 320px this gives two tracks of ~140px each — comfortably above the largest observed
card min-content (122.9px, TR) — so the row fits with zero document overflow.
`minmax(0, 1fr)` additionally lets the tracks shrink if labels grow further.

**Why this is safe and PR3-scoped (answer.txt criteria):**

- **Presentation-only** — one CSS media query; no template, JS, or business-rule change.
  Legacy plan-generation, training authority, workout selection and localStorage
  semantics are untouched.
- **Narrowly limited to the legacy ~320px layout** — the `≤380px` breakpoint leaves the
  passing **390px+** layout byte-identical (the matrix already passed at 390/768/1024/1366).
- **Legacy-only** — `training.css` is loaded **solely** by `templates/training.html`, and
  `.wstats` (`#wstats`, `#weekly-summary`) appears in **no other template**. Plan V2
  (`plan.html` + `plan.css`) has no `.wstats`, so the rule is inert under Plan ON;
  Nav (PR1) and Today (PR2) never load `training.css`.
- **Not a legacy redesign** — it does not touch the shared `.stat-card` component, the
  weekly-program rules (the `.weekly-program-*` anti-drift test is unaffected), or any
  other selector.

**After the fix** (`diag-overflow-headfix.json`, screenshots `…-headfix.png`):

| locale | doc scrollWidth | doc clientWidth | overflow |
|---|---|---|---|
| EN | 320 | 320 | **0px** |
| TR | 320 | 320 | **0px** |

## 4. Verification

- Regression test: `tests/test_training_ui.py::test_wstats_collapses_to_fewer_columns_on_narrow_screens`
  (parses `training.css`; asserts a `≤389px` media query collapses `.wstats` to two
  columns). Verified genuine — it finds nothing in base `9400641`'s CSS and `[380]` in the
  fixed CSS.
- Affected regression files green: `test_training_ui.py`, `test_training_page_characterization.py`,
  `test_weekly_program_ui.py`, `test_weekly_program_ui_js.py` → 225 passed.
- Browser re-run: full Plan matrix at 320/390/768/1024/1366 × EN/TR × Plan{off,on} plus
  Plan V2 320px cells — see the regenerated `validation-manifest.json`.

**Authorization boundary unchanged:** local implementation and validation only — nothing
pushed, no PR, no merge, no deploy, no production flag changed.
