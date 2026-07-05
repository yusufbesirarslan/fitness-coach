# Phase 3 — Home Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the home dashboard (`/`) into an "AI Command Center" — identity, a calorie hero, a prominent AI Next-Action, quick actions, nutrition rings, weight card, achievements, and the daily tip — mobile-first and on canonical design-system tokens, with zero backend changes.

**Architecture:** Rewrite `templates/index.html` (markup + inline JS) and `static/dashboard.css` (full token-based rewrite reusing `components.css`). Data comes from existing endpoints (`/last-session`, `/meal-log/today`, `/water`, `/workout/status`, `/checkin-history`). The AI Next-Action is a deterministic client-side rule engine. New visible strings go into `locales/{tr,en}.json` under `index.*`.

**Tech Stack:** Flask/Jinja2, vanilla JS, Chart.js 4.4.7 (weight sparkline), CSS custom properties (design-system tokens), custom JSON i18n (`window.t`).

## Global Constraints

- **No backend changes.** Do not touch any route, service, or model. Only `templates/index.html`, `static/dashboard.css`, `locales/tr.json`, `locales/en.json`, `docs/*`, and `tests/test_i18n.py`.
- **CSP:** every inline `<style>`/`<script>` MUST carry `nonce="{{ csp_nonce }}"`. External scripts only from `cdn.jsdelivr.net`.
- **Keep the Chart.js SRI tag verbatim:** `<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js" integrity="sha384-vsrfeLOOY6KuIYKDlmVH5UiBmgIdB1oEf7p01YgWHuqmOHfZr374+odEv96n9tNC" crossorigin="anonymous"></script>` (asserted by `tests/test_hooks.py::test_chart_js_tag_carries_sri`).
- **App shell untouched:** keep `{% set nav_active = 'home' %}`, `{% include "_nav.html" %}`, `{% include "_actionbar.html" %}`. Do not add inline nav markup (asserted by `tests/test_app_shell.py`).
- **i18n coupling:** canonical backend values (goal names like `kilo verme`, `ogun` values `Kahvaltı`/`Öğle`/`Akşam`) stay Turkish; only display text is translated. Every new `index.*` key exists in BOTH `tr.json` and `en.json` with identical `{placeholder}` sets (asserted by `tests/test_i18n.py::test_locale_key_parity_tr_en`).
- **Canonical tokens only** in new CSS: `--color-*`, `--space-*`, `--radius-*`, `--text-*`, `--weight-*`, `--elevation-*`, `--ease-*`, `--duration-*`, `--icon-*`. No new `--volt`/hardcoded-gray literals. Reuse `components.css` classes: `.card`, `.avatar(-lg)`, `.badge`, `.ring-wrap/.ring-svg/.ring-track/.ring-fill/.ring-label`, `.pbar-track/.pbar-fill`, `.sec-label`.
- **Preserve verbatim** (port from current `templates/index.html`): the weight card JS (`doUpdateWeight`, `loadSparkline`, `renderSparkline`, `checkBtnGlow`, `lastWeightUpdateMs`), the daily-tip carousel (`TIPS_TR`/`TIPS_EN`, `renderTip`, `restartProgressBar`, `goToTip`, `nextTip`, `prevTip`, `resetTipTimer`, visibilitychange), `toast()`, `updateCalRing()`, `setGreeting()`, `renderStreak()`, `updateStreakBonus()`, and the entire weekly-reward modal block (markup + JS).
- **Remove:** the Activity/steps card markup (`.act-*`, intensity buttons) and its JS (`selectIntensity`, `logActivity`, `refreshActivitySummary`, `selectedIntensity`, activity-summary DOM writes); the standalone quick-add FAB block (`#quick-add-wrap` + `toggleQuickAdd`). Activity calories default to `0` in `updateCalRing`.

---

### Task 1: Add new `index.*` locale keys (TR + EN)

**Files:**
- Modify: `locales/tr.json` (insert after line 184, before `index.reward_body`)
- Modify: `locales/en.json` (same position)
- Test: `tests/test_i18n.py::test_locale_key_parity_tr_en` (existing gate)

**Interfaces:**
- Produces: the `index.*` keys consumed by Task 3's template/JS. Keys with placeholders: `index.na_water_sub` uses `{n}`.

Add these keys to **`locales/tr.json`** (immediately before the `"index.reward_body"` line):

```json
  "index.hero_goal": "Bugünün Hedefi",
  "index.of_target": "/ {target} kcal hedef",
  "index.next_action_label": "Sıradaki Adım",
  "index.quick_actions": "Hızlı İşlemler",
  "index.nutrition_summary": "Beslenme Özeti",
  "index.achievements": "Başarımlar",
  "index.qa_log_meal": "Öğün Ekle",
  "index.qa_barcode": "Barkod Tara",
  "index.qa_soon": "Yakında",
  "index.qa_menu": "Menü Tara",
  "index.qa_workout": "Antrenman",
  "index.macro_protein": "Protein",
  "index.macro_carb": "Karb",
  "index.macro_fat": "Yağ",
  "index.macro_water": "Su",
  "index.level_label": "Seviye",
  "index.view_quests": "Görevler",
  "index.na_breakfast_title": "Kahvaltını kaydet",
  "index.na_breakfast_sub": "Güne enerjiyle başla.",
  "index.na_lunch_title": "Öğle yemeği zamanı",
  "index.na_lunch_sub": "Günün yarısı geçti — öğününü kaydet.",
  "index.na_dinner_title": "Akşam yemeğini kaydet",
  "index.na_dinner_sub": "Kalori hedefini tamamla.",
  "index.na_water_title": "Su içmeyi unutma",
  "index.na_water_sub": "{n}/8 bardak — bir bardak daha ekle.",
  "index.na_workout_title": "Antrenmana başla",
  "index.na_workout_sub": "Bugünkü seansını tamamla.",
  "index.na_done_title": "Harika gidiyorsun!",
  "index.na_done_sub": "Bugünkü hedeflerini tamamladın.",
  "index.na_cta_log": "Öğün Ekle",
  "index.na_cta_water": "Su Ekle",
  "index.na_cta_workout": "Başla",
  "index.na_cta_progress": "İlerleme",
```

Add the SAME keys to **`locales/en.json`** (immediately before its `"index.reward_body"` line):

```json
  "index.hero_goal": "Today's Goal",
  "index.of_target": "/ {target} kcal goal",
  "index.next_action_label": "Next Step",
  "index.quick_actions": "Quick Actions",
  "index.nutrition_summary": "Nutrition Summary",
  "index.achievements": "Achievements",
  "index.qa_log_meal": "Log Meal",
  "index.qa_barcode": "Scan Barcode",
  "index.qa_soon": "Soon",
  "index.qa_menu": "Menu Scan",
  "index.qa_workout": "Workout",
  "index.macro_protein": "Protein",
  "index.macro_carb": "Carbs",
  "index.macro_fat": "Fat",
  "index.macro_water": "Water",
  "index.level_label": "Level",
  "index.view_quests": "Quests",
  "index.na_breakfast_title": "Log your breakfast",
  "index.na_breakfast_sub": "Start your day with energy.",
  "index.na_lunch_title": "Time for lunch",
  "index.na_lunch_sub": "Half the day's gone — log your meal.",
  "index.na_dinner_title": "Log your dinner",
  "index.na_dinner_sub": "Round out your calorie goal.",
  "index.na_water_title": "Time to hydrate",
  "index.na_water_sub": "{n}/8 glasses — add one more.",
  "index.na_workout_title": "Start your workout",
  "index.na_workout_sub": "Complete today's session.",
  "index.na_done_title": "You're crushing it!",
  "index.na_done_sub": "You've hit today's goals.",
  "index.na_cta_log": "Log Meal",
  "index.na_cta_water": "Add Water",
  "index.na_cta_workout": "Start",
  "index.na_cta_progress": "Progress",
```

- [ ] **Step 1: Insert the TR keys** into `locales/tr.json` at the position above (valid JSON — the block already ends with a comma-terminated line; ensure the inserted block's last line ends with a comma since `index.reward_body` follows).
- [ ] **Step 2: Insert the EN keys** into `locales/en.json` at the matching position.
- [ ] **Step 3: Validate JSON + parity.**

Run: `python -c "import json; json.load(open('locales/tr.json',encoding='utf-8')); json.load(open('locales/en.json',encoding='utf-8')); print('ok')"`
Expected: `ok`

Run: `python -m pytest tests/test_i18n.py::test_locale_key_parity_tr_en -q`
Expected: PASS (same key set + placeholder parity).

- [ ] **Step 4: Commit.**

```bash
git add locales/tr.json locales/en.json
git commit -m "Add Phase 3 dashboard i18n keys"
```

---

### Task 2: Rewrite `static/dashboard.css` on canonical tokens

**Files:**
- Modify (full replace): `static/dashboard.css`
- Test: `python -m pytest tests/test_design_system.py -q` (must stay green), plus full suite in Task 4.

**Interfaces:**
- Produces the class contract consumed by Task 3's markup: `.dash-grid`, `.dash-id`, `.id-avatar`, `.id-hello`, `.id-name`, `.id-meta`, `.id-chip`, `.id-chip.xp`, `.id-chip.streak`, `.hero`, `.hero-ring`, `.hero-num`, `.hero-cap`, `.hero-body`, `.hero-goal`, `.hero-sub`, `.next`, `.next-icon`, `.next-body`, `.next-title`, `.next-sub`, `.next-cta`, `.qa-grid`, `.qa-tile`, `.qa-tile.disabled`, `.qa-ic`, `.qa-lbl`, `.qa-soon`, `.nutri-grid`, `.nutri-cell`, `.nutri-val`, `.nutri-lbl`, `.wt`, `.wt-display`, `.wt-big`, `.wt-unit`, `.wt-delta`, `.wt-delta.goal-ok/.goal-warn/.eq`, `.wt-arrow`, `.wt-goal`, `.wt-goal-text`, `.wt-spark`, `.wt-form`, `.wt-input`, `.wt-btn`, `.wt-meta`, `.wm-lbl`, `.wm-val`, `.ach`, `.ach-emblem`, `.ach-body`, `.ach-title`, `.ach-xp`, `.ach-foot`, `.ach-streak`, `.ach-link`, `.tip`, `.tip-icon`, `.tip-text`, `.tip-src`, `.tip-nav`, `.tip-btn`, `.tip-dots`, `.tip-dot(.on)`, `.tip-progress`, `.tip-pbar`, `.tip-slide`.

Replace the **entire** contents of `static/dashboard.css` with the following. It uses only canonical tokens and reuses `components.css` (`.card`, `.ring-*`, `.pbar-*`, `.avatar`, `.badge`, `.sec-label`) for shared primitives.

```css
/* ═══════════════════════════════════════════════════════
   AxisAI — HOME DASHBOARD (AI Command Center) — Phase 3
   Canonical tokens only. Reuses components.css primitives.
   ═══════════════════════════════════════════════════════ */

/* ── Layout grid ─────────────────────────────────────── */
.dash-grid { display: grid; grid-template-columns: 1fr; gap: var(--space-4); }
.dash-grid > .card { animation: dash-rise 0.34s var(--ease-out-expo) backwards; }
.dash-grid > .card:nth-child(1) { animation-delay: 0s; }
.dash-grid > .card:nth-child(2) { animation-delay: 0.04s; }
.dash-grid > .card:nth-child(3) { animation-delay: 0.08s; }
.dash-grid > .card:nth-child(4) { animation-delay: 0.12s; }
.dash-grid > .card:nth-child(5) { animation-delay: 0.16s; }
.dash-grid > .card:nth-child(6) { animation-delay: 0.20s; }
.dash-grid > .card:nth-child(n+7) { animation-delay: 0.24s; }
@keyframes dash-rise { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: translateY(0); } }

/* ── 1. Identity ─────────────────────────────────────── */
.dash-id { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
.id-avatar { flex-shrink: 0; }
.id-hello { font-size: var(--text-sm); color: var(--color-text-2); }
.id-name { font-family: var(--font-display); font-size: var(--text-2xl); letter-spacing: 1px; color: var(--color-text-1); line-height: 1.1; }
.id-meta { display: flex; gap: var(--space-2); margin-left: auto; flex-wrap: wrap; }
.id-chip { display: inline-flex; align-items: center; gap: 5px; padding: 5px 12px; border-radius: var(--radius-full); font-size: var(--text-xs); font-weight: var(--weight-bold); font-variant-numeric: tabular-nums; }
.id-chip.xp { color: var(--color-primary); background: var(--color-primary-soft); }
.id-chip.streak { color: var(--color-warning); background: var(--color-warning-soft); }

/* ── 2. Hero (calorie) ───────────────────────────────── */
.hero { display: flex; align-items: center; gap: var(--space-5); }
.hero-ring { flex-shrink: 0; }
.hero-num { font-family: var(--font-display); font-size: var(--text-3xl); letter-spacing: 1px; color: var(--color-text-1); line-height: 1; font-variant-numeric: tabular-nums; }
.hero-cap { font-size: var(--text-2xs); font-weight: var(--weight-bold); letter-spacing: var(--tracking-label); text-transform: uppercase; color: var(--color-text-3); }
.hero-body { flex: 1; min-width: 0; }
.hero-goal { font-size: var(--text-md); font-weight: var(--weight-semibold); color: var(--color-text-1); margin-bottom: 4px; }
.hero-sub { font-size: var(--text-sm); color: var(--color-text-2); font-variant-numeric: tabular-nums; }

/* ── 3. AI Next Action (accent) ──────────────────────── */
.next { display: flex; align-items: center; gap: var(--space-4); border: var(--border-w-1) solid var(--color-primary-glow); background: var(--color-primary-soft); }
.next-icon { flex-shrink: 0; width: 46px; height: 46px; border-radius: var(--radius-md); display: flex; align-items: center; justify-content: center; font-size: 24px; background: var(--color-primary); }
.next-body { flex: 1; min-width: 0; }
.next-title { font-size: var(--text-md); font-weight: var(--weight-bold); color: var(--color-text-1); }
.next-sub { font-size: var(--text-sm); color: var(--color-text-2); margin-top: 2px; }
.next-cta { flex-shrink: 0; }

/* ── 4. Quick actions ────────────────────────────────── */
.qa-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: var(--space-3); }
.qa-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; min-height: 88px; padding: var(--space-4) var(--space-3); border-radius: var(--radius-md); border: var(--border-w-1) solid var(--color-border-1); background: var(--color-surface-1); color: var(--color-text-1); text-decoration: none; cursor: pointer; font-family: var(--font-body); transition: border-color var(--duration-fast) var(--ease-standard), transform var(--duration-fast) var(--ease-standard), background var(--duration-fast) var(--ease-standard); }
.qa-tile:hover { border-color: var(--color-border-2); transform: translateY(-2px); }
.qa-tile:active { transform: scale(0.97); }
.qa-ic { width: var(--icon-lg); height: var(--icon-lg); color: var(--color-primary); }
.qa-ic svg { width: 100%; height: 100%; }
.qa-lbl { font-size: var(--text-xs); font-weight: var(--weight-semibold); text-align: center; }
.qa-tile.disabled { cursor: default; opacity: var(--opacity-disabled); }
.qa-tile.disabled:hover { border-color: var(--color-border-1); transform: none; }
.qa-tile.disabled .qa-ic { color: var(--color-text-3); }
.qa-soon { font-size: var(--text-2xs); font-weight: var(--weight-bold); letter-spacing: var(--tracking-wide); text-transform: uppercase; color: var(--color-text-3); }

/* ── 5. Nutrition summary rings ──────────────────────── */
.nutri-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--space-3); }
.nutri-cell { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.nutri-val { font-size: var(--text-2xs); font-weight: var(--weight-bold); color: var(--color-text-1); font-variant-numeric: tabular-nums; }
.nutri-lbl { font-size: var(--text-2xs); font-weight: var(--weight-semibold); letter-spacing: var(--tracking-wide); text-transform: uppercase; color: var(--color-text-3); }

/* ── 6. Weight card ──────────────────────────────────── */
.wt { display: flex; flex-direction: column; }
.wt-display { display: flex; align-items: baseline; gap: 6px; }
.wt-big { font-family: var(--font-display); font-size: var(--text-3xl); letter-spacing: 1px; color: var(--color-primary); line-height: 1; font-variant-numeric: tabular-nums; }
.wt-unit { font-size: var(--text-xl); color: var(--color-text-3); font-weight: var(--weight-light); }
.wt-delta { font-size: var(--text-sm); font-weight: var(--weight-semibold); margin-top: 4px; min-height: 18px; display: flex; align-items: center; gap: 4px; font-variant-numeric: tabular-nums; }
.wt-delta.goal-ok { color: var(--color-primary); } .wt-delta.goal-warn { color: var(--color-danger); } .wt-delta.eq { color: var(--color-text-3); }
.wt-arrow { font-size: var(--text-lg); font-weight: var(--weight-bold); line-height: 1; }
.wt-goal { margin-top: var(--space-2); }
.wt-goal-text { font-size: var(--text-sm); color: var(--color-text-2); margin-bottom: 6px; }
.wt-goal-text strong { color: var(--color-primary); font-weight: var(--weight-bold); }
.wt-spark { height: 64px; position: relative; margin: var(--space-3) 0; }
.wt-form { display: flex; gap: var(--space-2); align-items: center; margin-bottom: var(--space-4); }
.wt-input { flex: 1; background: var(--color-surface-1); border: var(--border-w-1) solid var(--color-border-1); border-radius: var(--radius-sm); padding: 10px 14px; color: var(--color-text-1); font-family: var(--font-body); font-size: var(--text-md); outline: none; transition: border-color var(--duration-fast) var(--ease-standard); -webkit-appearance: none; }
.wt-input:focus { border-color: var(--color-primary); box-shadow: var(--focus-ring); }
.wt-btn { font-family: var(--font-display); font-size: var(--text-md); letter-spacing: 2px; color: var(--color-on-primary); background: var(--color-primary); border: none; border-radius: var(--radius-sm); padding: 10px 20px; cursor: pointer; transition: background var(--duration-fast) var(--ease-standard), transform var(--duration-fast) var(--ease-standard); white-space: nowrap; }
.wt-btn:hover { background: var(--color-primary-strong); }
.wt-btn:active { transform: scale(0.96); }
.wt-btn.pulse { animation: wt-pulse 2.4s ease infinite; }
@keyframes wt-pulse { 0%,100% { box-shadow: 0 0 0 0 var(--color-primary-glow); } 50% { box-shadow: 0 0 0 6px transparent; } }
.wt-meta { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-2); padding-top: var(--space-3); border-top: var(--border-w-1) solid var(--color-border-1); }
.wm-lbl { font-size: var(--text-2xs); font-weight: var(--weight-bold); letter-spacing: var(--tracking-wide); text-transform: uppercase; color: var(--color-text-3); }
.wm-val { font-family: var(--font-display); font-size: var(--text-xl); color: var(--color-primary); letter-spacing: 1px; margin-top: 3px; font-variant-numeric: tabular-nums; }

/* ── 7. Achievements ─────────────────────────────────── */
.ach { display: flex; flex-direction: column; gap: var(--space-3); }
.ach-top { display: flex; align-items: center; gap: var(--space-3); }
.ach-emblem { width: 48px; height: 48px; border-radius: var(--radius-md); flex-shrink: 0; background: linear-gradient(135deg, var(--color-primary), var(--color-primary-strong)); display: flex; align-items: center; justify-content: center; font-size: 24px; }
.ach-body { flex: 1; min-width: 0; }
.ach-title { font-family: var(--font-display); font-size: var(--text-xl); color: var(--color-primary); letter-spacing: 1px; line-height: 1; }
.ach-xp { font-size: var(--text-sm); color: var(--color-text-2); margin-top: 4px; font-variant-numeric: tabular-nums; }
.ach-foot { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.ach-streak { display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-sm); font-weight: var(--weight-semibold); color: var(--color-warning); font-variant-numeric: tabular-nums; }
.ach-link { font-size: var(--text-sm); font-weight: var(--weight-semibold); color: var(--color-primary); text-decoration: none; }
.ach-link:hover { color: var(--color-primary-strong); }

/* ── 8. AI Tip ───────────────────────────────────────── */
.tip { display: flex; flex-direction: column; }
.tip-icon { font-size: 28px; line-height: 1; display: block; margin-bottom: var(--space-3); }
.tip-text { font-size: var(--text-md); color: var(--color-text-2); line-height: var(--leading-relaxed); flex: 1; }
.tip-text strong { color: var(--color-primary); font-weight: var(--weight-semibold); }
.tip-src { font-size: var(--text-xs); color: var(--color-text-3); display: flex; align-items: center; gap: 8px; margin-top: var(--space-3); }
.tip-src::before { content: ''; width: 18px; height: 1px; background: var(--color-border-2); }
.tip-nav { display: flex; align-items: center; justify-content: space-between; padding-top: var(--space-3); margin-top: auto; }
.tip-btn { background: none; border: var(--border-w-1) solid var(--color-border-1); border-radius: var(--radius-sm); padding: 6px 13px; font-size: var(--text-xs); color: var(--color-text-2); cursor: pointer; transition: color var(--duration-fast) var(--ease-standard), border-color var(--duration-fast) var(--ease-standard); font-family: var(--font-body); }
.tip-btn:hover { color: var(--color-primary); border-color: var(--color-primary-glow); }
.tip-dots { display: flex; gap: 5px; }
.tip-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--color-border-2); transition: background var(--duration-fast), transform var(--duration-fast); cursor: pointer; }
.tip-dot.on { background: var(--color-primary); transform: scale(1.3); }
#tip-body { min-height: 88px; overflow: hidden; position: relative; }
.tip-slide { will-change: transform; }
.tip-progress { height: 2px; background: var(--color-surface-3); border-radius: 1px; margin-top: var(--space-3); overflow: hidden; }
.tip-pbar { height: 100%; background: linear-gradient(90deg, var(--color-primary-glow), var(--color-primary)); border-radius: 1px; transform-origin: left center; animation: tip-fill 8s linear forwards; }
@keyframes tip-fill { from { transform: scaleX(1); } to { transform: scaleX(0); } }

/* ── Responsive ──────────────────────────────────────── */
@media (min-width: 768px) {
  .dash-grid { grid-template-columns: repeat(2, 1fr); gap: var(--space-5); }
  .dash-id, .hero, .next, .qa-grid, .wt, .ach, .tip { grid-column: span 2; }
  .nutri-grid { grid-column: span 2; }
}
@media (min-width: 1024px) {
  .dash-id, .next { grid-column: span 2; }
  .hero { grid-column: span 2; }
  .qa-grid { grid-column: span 1; }
  .nutri-cell-wrap, .nutri-grid { grid-column: span 1; }
  .wt { grid-column: span 1; }
  .ach { grid-column: span 1; }
  .tip { grid-column: span 2; }
}

/* ── Reduced motion ──────────────────────────────────── */
@media (prefers-reduced-motion: reduce) {
  .dash-grid > .card { animation: none; }
  .tip-pbar { animation: none; }
}
```

- [ ] **Step 1: Replace** the whole `static/dashboard.css` with the block above.
- [ ] **Step 2: Verify design-system tests still pass.**

Run: `python -m pytest tests/test_design_system.py -q`
Expected: PASS (this task only touches page CSS, not `components.css`/tokens).

- [ ] **Step 3: Verify no legacy tokens leaked in.**

Run: `grep -nE "\-\-volt|#[0-9A-Fa-f]{6}|rgba\(" static/dashboard.css` — Expected: no matches (only canonical `var(--…)`; the one exception `linear-gradient(135deg,var(--color-primary)…)` uses tokens, so no bare hex/rgba should appear).

- [ ] **Step 4: Commit.**

```bash
git add static/dashboard.css
git commit -m "Rewrite dashboard.css on canonical tokens"
```

---

### Task 3: Rewrite `templates/index.html` (markup + inline JS)

**Files:**
- Modify (full replace): `templates/index.html`
- Reads classes from Task 2, keys from Task 1.

**Interfaces:**
- Consumes Jinja context: `username`, `profile_picture`, `streak_count`, `last_weight_update`, `user_xp`, `user_level`, `user_title`, `xp_in_level`, `xp_for_next`, `locale`, `csp_nonce`, `t`.
- Consumes endpoints: `/last-session`, `/meal-log/today`, `/water` (GET+POST), `/workout/status`, `/checkin-history`, `/update-weight`, `/leaderboard/reward-check`, `/leaderboard/reward-dismiss`.
- Consumes global `window.CW` (coach widget) for the menu-scan action.

Replace the whole file. Structure below. **Head/body scaffolding** stays as today (lines 1–14 of current file: doctype, `_head.html`, title, theme.css, dashboard.css, nav.css, Chart.js SRI tag, `<body class="page-body">`, `{% set nav_active='home' %}`, `{% include "_nav.html" %}`).

**A) `<main class="main-content">` markup** — replace the current greeting-row + bento with:

```html
<main class="main-content">
<div class="dash-grid">

  <!-- 1. IDENTITY -->
  <section class="card dash-id">
    <div class="avatar avatar-lg id-avatar">
      {%- if profile_picture %}<img src="{{ profile_picture }}" alt="">{% else %}{{ username[0]|upper if username else 'U' }}{% endif -%}
    </div>
    <div>
      <div class="id-hello" id="id-hello">{{ t('index.greeting') }}</div>
      <div class="id-name">{{ username }}</div>
    </div>
    <div class="id-meta">
      <span class="id-chip xp">⚡ {{ user_xp }} XP</span>
      <span class="id-chip streak">🔥 {{ t('index.streak_days', n=streak_count|default(0)) }}</span>
    </div>
  </section>

  <!-- 2. HERO — calories -->
  <section class="card hero">
    <div class="ring-wrap hero-ring" style="width:120px;height:120px;">
      <svg class="ring-svg" viewBox="0 0 120 120" width="120" height="120">
        <circle class="ring-track" cx="60" cy="60" r="52" stroke-width="10"/>
        <circle class="ring-fill" id="cal-ring" cx="60" cy="60" r="52" stroke-width="10"
                stroke="var(--color-primary)" stroke-dasharray="326.73" stroke-dashoffset="326.73"/>
      </svg>
      <div class="ring-label">
        <div class="hero-num" id="cal-num">—</div>
        <div class="hero-cap">{{ t('index.cal_remaining') }}</div>
      </div>
    </div>
    <div class="hero-body">
      <div class="hero-cap">{{ t('index.hero_goal') }}</div>
      <div class="hero-goal" id="hero-goal">{{ t('index.cal_daily') }}</div>
      <div class="hero-sub" id="hero-sub">—</div>
    </div>
  </section>

  <!-- 3. AI NEXT ACTION -->
  <section class="card next" id="next-card">
    <div class="next-icon" id="next-icon">🎯</div>
    <div class="next-body">
      <div class="hero-cap">{{ t('index.next_action_label') }}</div>
      <div class="next-title" id="next-title">…</div>
      <div class="next-sub" id="next-sub"></div>
    </div>
    <button class="btn-volt next-cta" id="next-cta" data-action="doNextAction">→</button>
  </section>

  <!-- 4. QUICK ACTIONS -->
  <section class="card">
    <div class="sec-label">{{ t('index.quick_actions') }}</div>
    <div class="qa-grid">
      <a href="/nutrition" class="qa-tile">
        <span class="qa-ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"/></svg></span>
        <span class="qa-lbl">{{ t('index.qa_log_meal') }}</span>
      </a>
      <div class="qa-tile disabled" aria-disabled="true" title="{{ t('index.qa_soon') }}">
        <span class="qa-ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7V5a1 1 0 0 1 1-1h2M17 4h2a1 1 0 0 1 1 1v2M20 17v2a1 1 0 0 1-1 1h-2M7 20H5a1 1 0 0 1-1-1v-2M7 8v8M10 8v8M13 8v8M16 8v8"/></svg></span>
        <span class="qa-lbl">{{ t('index.qa_barcode') }}</span>
        <span class="qa-soon">{{ t('index.qa_soon') }}</span>
      </div>
      <button class="qa-tile" type="button" data-action="openMenuScan">
        <span class="qa-ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 3h16v18l-3-2-2 2-3-2-3 2-2-2-3 2V3z"/><path d="M8 8h8M8 12h8M8 16h5"/></svg></span>
        <span class="qa-lbl">{{ t('index.qa_menu') }}</span>
      </button>
      <a href="/training" class="qa-tile">
        <span class="qa-ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 6.5h11M6.5 17.5h11M4 9v6M20 9v6M8 8v8M16 8v8"/></svg></span>
        <span class="qa-lbl">{{ t('index.qa_workout') }}</span>
      </a>
    </div>
  </section>

  <!-- 5. NUTRITION SUMMARY -->
  <section class="card">
    <div class="sec-label">{{ t('index.nutrition_summary') }}</div>
    <div class="nutri-grid">
      <div class="nutri-cell">
        <div class="ring-wrap" style="width:62px;height:62px;">
          <svg class="ring-svg" viewBox="0 0 62 62" width="62" height="62"><circle class="ring-track" cx="31" cy="31" r="26" stroke-width="6"/><circle class="ring-fill" id="nr-protein" cx="31" cy="31" r="26" stroke-width="6" stroke="var(--color-primary)" stroke-dasharray="163.36" stroke-dashoffset="163.36"/></svg>
        </div>
        <div class="nutri-val" id="nv-protein">—</div>
        <div class="nutri-lbl">{{ t('index.macro_protein') }}</div>
      </div>
      <div class="nutri-cell">
        <div class="ring-wrap" style="width:62px;height:62px;">
          <svg class="ring-svg" viewBox="0 0 62 62" width="62" height="62"><circle class="ring-track" cx="31" cy="31" r="26" stroke-width="6"/><circle class="ring-fill" id="nr-carb" cx="31" cy="31" r="26" stroke-width="6" stroke="var(--color-warning)" stroke-dasharray="163.36" stroke-dashoffset="163.36"/></svg>
        </div>
        <div class="nutri-val" id="nv-carb">—</div>
        <div class="nutri-lbl">{{ t('index.macro_carb') }}</div>
      </div>
      <div class="nutri-cell">
        <div class="ring-wrap" style="width:62px;height:62px;">
          <svg class="ring-svg" viewBox="0 0 62 62" width="62" height="62"><circle class="ring-track" cx="31" cy="31" r="26" stroke-width="6"/><circle class="ring-fill" id="nr-fat" cx="31" cy="31" r="26" stroke-width="6" stroke="var(--color-success)" stroke-dasharray="163.36" stroke-dashoffset="163.36"/></svg>
        </div>
        <div class="nutri-val" id="nv-fat">—</div>
        <div class="nutri-lbl">{{ t('index.macro_fat') }}</div>
      </div>
      <div class="nutri-cell">
        <div class="ring-wrap" style="width:62px;height:62px;">
          <svg class="ring-svg" viewBox="0 0 62 62" width="62" height="62"><circle class="ring-track" cx="31" cy="31" r="26" stroke-width="6"/><circle class="ring-fill" id="nr-water" cx="31" cy="31" r="26" stroke-width="6" stroke="var(--color-info)" stroke-dasharray="163.36" stroke-dashoffset="163.36"/></svg>
        </div>
        <div class="nutri-val" id="nv-water">—</div>
        <div class="nutri-lbl">{{ t('index.macro_water') }}</div>
      </div>
    </div>
  </section>

  <!-- 6. WEIGHT -->
  <section class="card wt">
    <div class="sec-label">{{ t('index.weight_title') }}</div>
    <div class="wt-display"><div class="wt-big" id="w-kg">—</div><div class="wt-unit">kg</div></div>
    <div class="wt-delta" id="w-delta"></div>
    <div class="wt-goal" id="w-goal-progress" style="display:none">
      <div class="wt-goal-text" id="w-goal-text"></div>
      <div class="pbar-track"><div class="pbar-fill" id="w-goal-bar" style="width:0;background:linear-gradient(90deg,var(--color-primary),var(--color-primary-strong));"></div></div>
    </div>
    <div class="wt-spark"><canvas id="sparkline"></canvas><div id="spark-empty" style="display:none;font-size:var(--text-xs);color:var(--color-text-3);font-style:italic;padding-top:20px;">{{ t('index.no_checkin') }}</div></div>
    <div class="wt-form">
      <input type="number" class="wt-input" id="w-input" placeholder="{{ t('index.weight_input_ph') }}" step="0.1" min="30" max="300">
      <button class="wt-btn" id="w-btn" data-action="doUpdateWeight">{{ t('index.update') }}</button>
    </div>
    <div class="wt-meta" id="w-meta" style="display:none">
      <div><div class="wm-lbl">BMR</div><div class="wm-val" id="w-bmr">—</div></div>
      <div><div class="wm-lbl">TDEE</div><div class="wm-val" id="w-tdee">—</div></div>
      <div><div class="wm-lbl">{{ t('index.cal_target') }}</div><div class="wm-val" id="w-target">—</div></div>
    </div>
  </section>

  <!-- 7. ACHIEVEMENTS -->
  <section class="card ach">
    <div class="sec-label">{{ t('index.achievements') }}</div>
    <div class="ach-top">
      <div class="ach-emblem">{% if user_level <= 5 %}🌱{% elif user_level <= 10 %}💪{% elif user_level <= 20 %}🔥{% else %}🏆{% endif %}</div>
      <div class="ach-body">
        <div class="ach-title">{{ user_title }}</div>
        <div class="ach-xp"><span id="ach-xp">{{ xp_in_level }} / {{ xp_for_next }}</span> XP · {{ t('index.level_label') }} {{ user_level }}</div>
      </div>
    </div>
    <div class="pbar-track"><div class="pbar-fill" id="ach-bar" style="width:{{ (xp_in_level / xp_for_next * 100)|int }}%;background:linear-gradient(90deg,var(--color-primary),var(--color-primary-strong));"></div></div>
    <div class="ach-foot">
      <span class="ach-streak">🔥 {{ t('index.streak_days', n=streak_count|default(0)) }}</span>
      <a class="ach-link" href="/quests">{{ t('index.view_quests') }} →</a>
    </div>
    <div class="ach-xp" id="jm-bonus" style="min-height:14px;"></div>
  </section>

  <!-- 8. AI TIP -->
  <section class="card tip">
    <div class="sec-label">{{ t('index.tip_title') }}</div>
    <div id="tip-body">
      <div class="tip-slide" id="ins-slide">
        <span class="tip-icon" id="ins-icon">⚡</span>
        <div class="tip-text" id="ins-text">{{ t('index.tip_loading') }}</div>
        <div class="tip-src" id="ins-src">{{ t('index.tip_src_default') }}</div>
      </div>
    </div>
    <div class="tip-nav">
      <button class="tip-btn" data-action="prevTip">{{ t('index.prev') }}</button>
      <div class="tip-dots" id="ins-dots"></div>
      <button class="tip-btn" data-action="nextTip">{{ t('index.next') }}</button>
    </div>
    <div class="tip-progress"><div class="tip-pbar" id="ins-pbar"></div></div>
  </section>

</div><!-- /dash-grid -->
</main>

{% include "_actionbar.html" %}

<!-- TOAST -->
<div class="toast-wrap" id="toast-wrap"></div>
```

> Note: the tip DOM ids (`ins-slide`, `ins-icon`, `ins-text`, `ins-src`, `ins-dots`, `ins-pbar`) are kept identical to the current file so the ported carousel JS works unchanged. Only their classes changed (`.ins-*` → `.tip-*`). The tip JS reads by **id**, not class, so no JS edit is needed for the carousel except `restartProgressBar` which sets `#ins-pbar` animation to `tip-fill` (see below).

**B) Weekly-reward modal block** — port the ENTIRE current block (current `index.html` lines 192–242: the `<style nonce>` reward-* rules and the `.reward-overlay` markup) **verbatim**, but change these legacy vars to canonical tokens inside that `<style>`: `--surface-2`→`--color-surface-2`, `--border-2`→`--color-border-2`, `--r-lg`→`--radius-lg`, `--text`→`--color-text-1`, `--text-2`→`--color-text-2`, `--volt`→`--color-primary`, `--r-md`→`--radius-md`, `--ease-out-expo` stays. Keep `data-action="dismissReward"` and all ids.

**C) Inline `<script nonce="{{ csp_nonce }}">`** — build it from:

1. **Ported verbatim** from current `index.html` (keep names/logic): `toast()`, `TIPS_TR`, `TIPS_EN`, `const TIPS = …`, `renderTip()`, `goToTip()`, `nextTip()`, `prevTip()`, `resetTipTimer()`, the `visibilitychange` listener, `_prefersReducedMotion`, `renderStreak()`, `updateCalRing()` (but drop the `activityCals` legend writes — see below), `doUpdateWeight()`, `loadSparkline()`, `renderSparkline()`, `checkBtnGlow()`, `lastWeightUpdateMs`, `updateStreakBonus()`.

2. **Change `restartProgressBar()`** to use the new keyframe name:

```javascript
function restartProgressBar() {
    const bar = document.getElementById('ins-pbar');
    if (!bar) return;
    bar.style.animation = 'none';
    bar.offsetHeight;
    bar.style.animation = 'tip-fill 8s linear forwards';
}
```

3. **Simplify `updateCalRing()`** — the hero ring has no legend rows now. Replace with:

```javascript
let currentConsumedCals = 0, currentTargetCals = 2000;
function updateCalRing(consumed, target) {
    currentConsumedCals = consumed; currentTargetCals = target;
    const circ = 326.73;                        // 2π·52
    const ratio = target > 0 ? Math.min(consumed / target, 1) : 0;
    const remaining = Math.max(target - consumed, 0);
    const ring = document.getElementById('cal-ring');
    ring.style.strokeDashoffset = circ * (1 - ratio);
    ring.style.stroke = ratio >= 1 ? 'var(--color-danger)' : ratio >= 0.85 ? 'var(--color-warning)' : 'var(--color-primary)';
    document.getElementById('cal-num').textContent = remaining;
    document.getElementById('hero-sub').textContent = consumed + ' / ' + target + ' kcal';
}
```

4. **Simplify `setGreeting()`** — no eyebrow/sub elements now; set the goal label + time greeting:

```javascript
let userGoal = null;
function setGreeting(goal) {
    const h = new Date().getHours();
    let gText;
    if      (h >= 5  && h < 12) gText = t('index.greet_morning');
    else if (h >= 12 && h < 17) gText = t('index.greet_noon');
    else if (h >= 17 && h < 22) gText = t('index.greet_evening');
    else                        gText = t('index.greet_night');
    const hello = document.getElementById('id-hello');
    if (hello) hello.textContent = gText + ',';
    const goalCfg = { 'kilo verme': t('index.sub_loss'), 'kas kazanma': t('index.sub_gain') };
    const g = document.getElementById('hero-goal');
    if (g) g.textContent = goalCfg[goal] ? t('index.hero_goal') : t('index.hero_goal');
}
```

   (Goal label stays `t('index.hero_goal')`; the goal-specific copy lives in the sub already via calories. Keep it simple.)

5. **New: nutrition rings.**

```javascript
function setRing(id, ratio) {
    const circ = 163.36;                        // 2π·26
    const el = document.getElementById(id);
    if (el) el.style.strokeDashoffset = circ * (1 - Math.min(Math.max(ratio, 0), 1));
}
function updateNutrition(totals, target) {
    const p = Math.round(totals.protein || 0), c = Math.round(totals.karb || 0), f = Math.round(totals.yag || 0);
    const tp = Math.round(target * 0.30 / 4), tc = Math.round(target * 0.40 / 4), tf = Math.round(target * 0.30 / 9);
    document.getElementById('nv-protein').textContent = p + '/' + tp + 'g';
    document.getElementById('nv-carb').textContent    = c + '/' + tc + 'g';
    document.getElementById('nv-fat').textContent     = f + '/' + tf + 'g';
    setRing('nr-protein', p / Math.max(tp, 1));
    setRing('nr-carb',    c / Math.max(tc, 1));
    setRing('nr-fat',     f / Math.max(tf, 1));
}
function updateWaterRing(count, goal) {
    goal = goal || 8;
    document.getElementById('nv-water').textContent = count + '/' + goal;
    setRing('nr-water', count / goal);
}
```

6. **New: AI Next-Action engine.** `mealsData`/`waterData`/`workoutData` are cached from init fetches.

```javascript
let waterCount = 0, waterGoal = 8, workoutDone = false, mealsLogged = false;
let nextAction = null;                          // {cta, kind}

function computeNextAction() {
    const h = new Date().getHours();
    const consumed = currentConsumedCals, target = currentTargetCals || 2000;
    const set = (icon, title, sub, ctaLabel, kind) => ({ icon, title, sub, ctaLabel, kind });
    if (h >= 5 && h < 11 && !mealsLogged)
        return set('🍳', t('index.na_breakfast_title'), t('index.na_breakfast_sub'), t('index.na_cta_log'), 'nutrition');
    if (h >= 11 && h < 16 && consumed < 0.35 * target)
        return set('🥗', t('index.na_lunch_title'), t('index.na_lunch_sub'), t('index.na_cta_log'), 'nutrition');
    if (h >= 16 && h < 22 && consumed < 0.75 * target)
        return set('🍽️', t('index.na_dinner_title'), t('index.na_dinner_sub'), t('index.na_cta_log'), 'nutrition');
    if (waterCount < waterGoal && h >= 12)
        return set('💧', t('index.na_water_title'), t('index.na_water_sub', { n: waterCount }), t('index.na_cta_water'), 'water');
    if (!workoutDone && h >= 8 && h < 22)
        return set('🏋️', t('index.na_workout_title'), t('index.na_workout_sub'), t('index.na_cta_workout'), 'training');
    return set('🎉', t('index.na_done_title'), t('index.na_done_sub'), t('index.na_cta_progress'), 'progress');
}
function renderNextAction() {
    nextAction = computeNextAction();
    document.getElementById('next-icon').textContent = nextAction.icon;
    document.getElementById('next-title').textContent = nextAction.title;
    document.getElementById('next-sub').textContent = nextAction.sub;
    document.getElementById('next-cta').textContent = nextAction.ctaLabel;
}
async function doNextAction() {
    if (!nextAction) return;
    if (nextAction.kind === 'nutrition') { location.href = '/nutrition'; return; }
    if (nextAction.kind === 'training')  { location.href = '/training';  return; }
    if (nextAction.kind === 'progress')  { location.href = '/progress-page'; return; }
    if (nextAction.kind === 'water') {
        const next = Math.min(waterCount + 1, waterGoal);
        try {
            const res = await fetch('/water', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ count: next }) });
            if (res.ok) { waterCount = next; updateWaterRing(waterCount, waterGoal); renderNextAction(); toast(t('index.weight_updated'), 'success'); }
        } catch (e) { toast(t('index.conn_error'), 'error'); }
    }
}
```

7. **New: menu-scan quick action.**

```javascript
function openMenuScan() {
    if (window.CW && typeof CW.startScan === 'function') {
        if (!CW.open && typeof CW.toggle === 'function') CW.toggle();
        setTimeout(function () { try { CW.startScan(); } catch (e) { location.href = '/nutrition'; } }, 300);
    } else { location.href = '/nutrition'; }
}
```

8. **New `init()`** — replaces current init; fetches, wires everything, drops all activity handling:

```javascript
async function init() {
    renderStreak({{ streak_count|default(0) }});
    checkBtnGlow();
    tipIdx = Math.floor(Date.now() / 86400000) % TIPS.length;
    renderTip(tipIdx, false);
    resetTipTimer();
    try {
        const [sessRes, mealRes, waterRes, woRes] = await Promise.all([
            fetch('/last-session'), fetch('/meal-log/today'), fetch('/water'), fetch('/workout/status')
        ]);
        const sess = await sessRes.json();
        const meals = await mealRes.json();
        const water = await waterRes.json();
        const wo = await woRes.json();

        waterCount = water.count || 0; waterGoal = water.goal || 8;
        workoutDone = !!wo.completed;
        const mealArr = (meals && meals.meals) ? meals.meals : [];
        mealsLogged = mealArr.length > 0;
        const totals = (meals && meals.totals) ? meals.totals : { kalori: 0, protein: 0, karb: 0, yag: 0 };

        let target = 2000;
        if (sess.exists) {
            userGoal = sess.goal;
            setGreeting(sess.goal);
            target = Math.round(sess.target_calories || 2000);
            const currentWeight = sess.weight || 0;
            document.getElementById('w-kg').textContent = currentWeight || '—';
            if (sess.bmr) {
                document.getElementById('w-bmr').textContent = Math.round(sess.bmr);
                document.getElementById('w-tdee').textContent = Math.round(sess.tdee);
                document.getElementById('w-target').textContent = Math.round(sess.target_calories);
                document.getElementById('w-meta').style.display = 'grid';
            }
            if (sess.target_weight && currentWeight) {
                const tw = sess.target_weight, gt = sess.goal_type;
                const remaining = gt === 'loss' ? Math.max(currentWeight - tw, 0) : Math.max(tw - currentWeight, 0);
                const goalEl = document.getElementById('w-goal-progress');
                const textEl = document.getElementById('w-goal-text');
                const barEl  = document.getElementById('w-goal-bar');
                goalEl.style.display = 'block';
                if (remaining <= 0) { textEl.innerHTML = '<strong>' + currentWeight + ' kg</strong> — ' + t('index.goal_reached'); barEl.style.width = '100%'; }
                else { const sd = remaining + 2; const pct = Math.min(((sd - remaining) / sd) * 100, 95); textEl.innerHTML = '<strong>' + currentWeight + ' kg</strong> / ' + remaining.toFixed(1) + ' ' + t('index.kg_left'); barEl.style.width = Math.max(pct, 5) + '%'; }
            }
        } else { setGreeting(null); }

        updateCalRing(Math.round(totals.kalori || 0), target);
        updateNutrition(totals, target);
        updateWaterRing(waterCount, waterGoal);
        renderNextAction();
    } catch (e) {
        setGreeting(null);
        updateCalRing(0, 2000);
        updateNutrition({}, 2000);
        updateWaterRing(waterCount, waterGoal);
        renderNextAction();
    }
    await loadSparkline();
    updateStreakBonus();
}
init();
```

9. **Keep the two trailing script tags** exactly: `<script src="/static/coach_widget.js"></script>` and `<script src="/static/actions.js"></script>`, then the **weekly-reward `<script nonce>` block ported verbatim** (current lines 776–800). `data-action` dispatch is handled by `actions.js` (verify it binds `data-action` → global function by name; the current page already relies on this for `doUpdateWeight`, `prevTip`, etc.).

- [ ] **Step 1: Write the failing render test first** (update the i18n dashboard test to the new contract). Edit `tests/test_i18n.py::test_dashboard_renders_localized`:

```python
def test_dashboard_renders_localized(app, client, make_user, login):
    # Dashboard (/) login + profile_complete ister.
    make_user("dashen", profile_complete=True, language="en")
    login("dashen")
    body = client.get("/").get_data(as_text=True)
    assert "Quick Actions" in body and "Nutrition Summary" in body
    assert "Hızlı İşlemler" not in body
    # EN tip dizisi seçili olmalı (TR tip metni gövdede olmamalı)
    assert "Sports Physiology" in body
```

- [ ] **Step 2: Run it — expect FAIL** (old markup lacks "Quick Actions").

Run: `python -m pytest tests/test_i18n.py::test_dashboard_renders_localized -q`
Expected: FAIL (`assert "Quick Actions" in body`).

- [ ] **Step 3: Replace `templates/index.html`** with the new markup (A), reward block (B), and script (C) per above.
- [ ] **Step 4: Run the render test — expect PASS.**

Run: `python -m pytest tests/test_i18n.py::test_dashboard_renders_localized -q`
Expected: PASS.

- [ ] **Step 5: Run the shell + hooks smoke — expect PASS.**

Run: `python -m pytest tests/test_app_shell.py tests/test_hooks.py::test_chart_js_tag_carries_sri -q`
Expected: PASS (home tab active, action bar, Chart.js SRI tag intact).

- [ ] **Step 6: Commit.**

```bash
git add templates/index.html tests/test_i18n.py
git commit -m "Redesign home dashboard as AI Command Center"
```

---

### Task 4: Full verification + docs

**Files:**
- Modify: `docs/design-system.md` (note dashboard is now token-compliant)
- Create: `docs/handoff.md` (Phase 3 handoff — required by phase spec's End section; archive the Phase 2 one first)
- Modify: `docs/archive/` (move current `handoff.md` → `docs/archive/handoff-2026-07-05-phase2-app-shell.md`)

- [ ] **Step 1: Full test suite.**

Run: `python -m pytest -q`
Expected: all pass (Phase 2 baseline was 1088 passed; expect ≥ that, minus none — only the i18n dashboard test changed).

- [ ] **Step 2: Manual smoke with `flask run`** (see design-system.md / local-testing memory: `FLASK_DEBUG=1`). Load `/` logged-in: verify rings animate, Next Action shows a sensible CTA, Quick Actions navigate / open the coach scan, weight update + sparkline work, tip carousel advances, weekly-reward modal still triggers. Toggle language TR/EN. Check 390px, 768px, 1280px widths.

- [ ] **Step 3: Update `docs/design-system.md`** — in "Bilinen sapmalar / Phase 3+ TODO", strike the `dashboard.css` gri-palet line (now resolved) and note dashboard is token-compliant + reuses `.ring-*`/`.pbar-*`.

- [ ] **Step 4: Archive Phase 2 handoff and write Phase 3 handoff.**

```bash
git mv docs/handoff.md docs/archive/handoff-2026-07-05-phase2-app-shell.md
```

Then create a new `docs/handoff.md` covering (per phase spec): Completed work, Files modified, Components created/refactored, Architectural decisions, Verification done, Quality metrics review (Responsiveness, Accessibility, Visual consistency, Code maintainability, Reusability, Performance, UX clarity), Known issues, Remaining tasks / next steps.

- [ ] **Step 5: Commit.**

```bash
git add docs/
git commit -m "Add Phase 3 handoff; mark dashboard token-compliant"
```

---

## Self-Review

**Spec coverage** (each spec section → task):
- Top identity (avatar/username/XP/level/streak) → Task 3 §A.1. ✓
- Hero card (goal/remaining/progress/ring) → Task 3 §A.2 + `updateCalRing`. ✓
- AI Next Action → Task 3 §C.6. ✓
- Quick Actions (Log Meal / Barcode "coming soon" / Menu Scan / Workout) → Task 3 §A.4 + `openMenuScan`. ✓
- Nutrition rings (protein/carb/fat/water) → Task 3 §A.5 + `updateNutrition`/`updateWaterRing`. ✓
- Weight card (chart + trend) → Task 3 §A.6, ported JS. ✓
- Achievements (XP/level/streak) → Task 3 §A.7. ✓
- AI Tip → Task 3 §A.8, ported carousel. ✓
- Remove steps card + FAB → Task 3 (omitted from markup/JS). ✓
- Token-based CSS → Task 2. ✓
- i18n keys + parity + test update → Task 1 + Task 3 §Step 1. ✓
- Preserve Chart.js SRI, app shell → Global Constraints + Task 3 §Step 5. ✓
- Docs (design-system + handoff) → Task 4. ✓

**Placeholder scan:** no TBD/TODO; all JS/CSS/markup given in full or as explicit verbatim-port with line refs.

**Type/name consistency:** DOM ids reused by ported carousel (`ins-*`) kept identical; new ids (`cal-ring`, `nr-*`, `nv-*`, `next-*`) consistent between markup (Task 3 §A) and JS (Task 3 §C). Ring circumferences: r=52→326.73 (hero), r=26→163.36 (nutrition) — match `stroke-dasharray` in markup and `circ` in JS. `updateCalRing(consumed, target)` signature consistent (2 args, activity dropped). `t('index.na_water_sub', {n})` placeholder matches Task 1 key. ✓
