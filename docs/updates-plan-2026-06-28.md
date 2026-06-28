# Updates Roadmap — structured plan (2026-06-28)

Plan for the 5 items in `updates.txt` (masaüstü). **Planning only — not implemented in
this branch.** The triage code fixes (H1, H2, M1–M7, M9, L1–L7 from
`TRIAGE_2026-06-28.md`) are implemented separately on `fix/triage-2026-06-28`.

Referenced IDs resolved from prior reports:
- **SEC1** → `TRIAGE_2026-06-26.md`: CSP allows the entire `https://cdn.jsdelivr.net` as a script source.
- **A5** → `_repair_truncated_json` can emit invalid JSON on mid-value truncation.
- **A6** → `calculate_tdee` silently downgrades unknown activity to sedentary (root of the L2 symptom just fixed in `update_weight`).
- **D4** → `WorkoutLog` placeholder rows pollute volume aggregation (`tracking.py`).
- **D7** → `user_metadata` legacy `ALTER` is `JSONB`-only → invalid on SQLite (`db_init.py`); ties to deferred **I-M1**.

---

## 1. Close the security tail — SEC1 (pin jsdelivr + SRI, or self-host)

- **Objective:** Remove the broad `https://cdn.jsdelivr.net` script-src allowance.
- **Why:** Allowing any path on jsdelivr (arbitrary npm/GitHub content) undercuts the
  inline-nonce hardening — a compromised/typo-squatted asset, or an injection pointing
  `<script src>` at any jsdelivr URL, executes under our origin.
- **Steps:**
  1. Inventory usage: `grep -rn "cdn.jsdelivr.net" templates/ static/`.
  2. Per asset choose **self-host** (vendor into `static/vendor/<lib>@<ver>/`) — preferred,
     lets us drop the CDN host entirely — **or** pin with **SRI** (`integrity=` + `crossorigin`)
     at an exact versioned path.
  3. Update CSP in `app/hooks.py`: drop `https://cdn.jsdelivr.net` if self-hosting, or
     narrow to `cdn.jsdelivr.net/npm/<pkg>@<version>/...` if keeping the CDN.
  4. Verify each page renders (charts/libs) and the browser console shows **zero CSP
     violations**.
- **Effort:** S–M (scales with asset count). **Risk:** Low–Med (wrong SRI hash / self-host
  path breaks a lib — test every page). **Depends on:** nothing.
- **Acceptance:** No jsdelivr wildcard in CSP (or narrowed to versioned path), every CDN
  tag carries SRI, all pages render, no console CSP violations.

## 2. Locale key-parity CI check + native EN pass

- **Objective:** (a) CI gate asserting `locales/tr.json` and `locales/en.json` have identical
  key sets; (b) human/native review of the EN strings.
- **Why:** i18n is critical-coupling (canonical backend values stay TR; only display text is
  translated). Silent key drift breaks `t()` fallbacks; machine EN may read awkwardly in the
  fitness domain.
- **Steps:**
  1. Add a test (extend `tests/test_i18n.py`) that loads both JSON files and asserts equal key
     sets; also assert no empty values and that `{placeholder}` tokens match across locales.
  2. Wire into the existing GitHub Actions CI as a **required** step (block merge on mismatch).
  3. EN native pass: export EN strings, native-speaker review of tone/terminology, apply fixes.
- **Effort:** CI check **S**; EN review **M** (human-dependent). **Risk:** Low.
- **Acceptance:** CI fails on key/placeholder mismatch; EN strings reviewed + signed off.
- **Note:** The two new `coach.*` keys added with the triage fixes are already at full TR/EN
  parity (verified) — this CI gate locks that invariant going forward.

## 3. Archive the triage-markdown sprawl → one living tracker

- **Objective:** Consolidate `TRIAGE.md`, `TRIAGE_FIXES.md`, `FIXES.md`,
  `TRIAGE_2026-06-23/24/26/28.md` into a single living tracker; archive the rest.
- **Why:** Overlapping reports cause re-finds and confusion; the same findings recur across
  reports (exactly what item 4 is about).
- **Steps:**
  1. Inventory all `TRIAGE*.md` / `FIXES*.md` at repo root + `docs/`.
  2. Extract still-open items into **GitHub Issues** (severity labels) *or* a single
     `docs/STATUS.md` with Open / Done sections — pick one canonical home.
  3. Move resolved reports to `docs/archive/` (or delete — git history preserves them).
  4. Add a README pointer to the single tracker. This `updates-plan` doc can seed it.
- **Effort:** M (mostly curation). **Risk:** Low (docs only).
- **Acceptance:** One canonical tracker exists; old reports archived; no duplicate open lists.

## 4. Finish or formally backlog remaining items (A5, A6, D4, D7 hygiene)

Decide fix-now vs backlog for each; create a tracked issue either way.

| ID | Issue | Proposed action | Effort |
|----|-------|-----------------|:------:|
| **A6** | `calculate_tdee` silently → sedentary on unknown activity | Log a warning + return a fallback flag on unknown activity (root cause of the L2 symptom just patched in `update_weight`). Fix now. | S |
| **D4** | `WorkoutLog` placeholder rows inflate volume sums | Exclude placeholder rows from the volume aggregation query. Fix now. | S |
| **A5** | `_repair_truncated_json` can emit invalid JSON on mid-value truncation | Validate salvaged JSON before returning; add a mid-value-truncation test. | S–M |
| **D7** | `user_metadata` legacy `ALTER` is `JSONB`-only (breaks SQLite) | Guard by dialect, or fold into the Alembic migration cleanup. **Backlog → tie to deferred I-M1.** | M |

- **Acceptance:** Each item is fixed-with-test **or** has a tracked issue recording the
  decision; nothing left to recur silently in the next report.

## 5. Confirm CI green + consider tagging a release

- **Objective:** Verify CI is green on these merges; cut a version tag given the change volume
  (full i18n sweep PR #83–#94 + this triage-fix batch).
- **Steps:**
  1. After the triage-fix PR merges, watch GitHub Actions (`deploy.yml` + tests +
     schema-drift guard) → green.
  2. Confirm the EC2 deploy succeeded (`/health` OK).
  3. Tag an **annotated** release with a changelog summarizing the i18n sweep + triage fixes.
  4. Track that the schema-drift guard is still `continue-on-error` (non-blocking) pending
     **I-M1** — note it in the release.
- **Effort:** S. **Risk:** Low.
- **Acceptance:** CI green, deploy healthy, annotated tag pushed with changelog.

---

## Recommended sequencing

1. **This PR (done):** triage code fixes (H1, H2, M1–M7, M9, L1–L7).
2. **Next PR:** Item 2 CI parity gate (cheap, prevents i18n regressions) **+** Item 1 SEC1
   (the actual security tail).
3. **Hygiene PR:** Item 4 small fixes (A6, D4) with tests; backlog A5, D7 (D7 → I-M1).
4. **Docs PR:** Item 3 consolidation — seed the single tracker from the Item 4 backlog.
5. **Release:** Item 5 — tag once CI is green and the deploy is healthy.

> Deferred from the 2026-06-28 triage (not in this batch): **I-M1** (move boot-time schema
> init into proper Alembic migrations + gate behind a one-shot job). D7 above should be folded
> into that effort.
