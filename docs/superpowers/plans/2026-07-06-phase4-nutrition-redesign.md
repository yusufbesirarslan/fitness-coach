# Phase 4 — Nutrition Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Nutrition page into an image-first meal-logging experience (macro ring, meal timeline, FAB bottom sheet, integrated barcode) on the canonical AxisAI design system, preserving all backend logic.

**Architecture:** Two small additive backend changes (a FatSecret barcode lookup route + a `created_at` field on the today response) support a full front-end rewrite of `templates/nutrition.html` + `static/nutrition.js` on `tokens.css`/`components.css`. AI Score is a pure client-side deterministic function. Barcode reading uses the browser `BarcodeDetector` with a manual-number fallback.

**Tech Stack:** Flask + SQLAlchemy, FatSecret REST API, vanilla JS with `data-action` delegation (`static/actions.js`), canonical CSS design tokens, Chart.js (history), pytest.

## Global Constraints

- **Preserve backend business logic** — no changes to macro calc, plan generation, diary, menu extraction, water. Only two additive backend edits are permitted (Tasks 1–2).
- **No DB migration** — `created_at` already exists on `MealLog`; add no columns.
- **Canonical values stay TR** — meal keys `Kahvaltı / Öğle / Akşam / Ara Öğün`, food/day codes are TR in code and payloads; only *display* text is localized via existing `mealLabel()`/`t()` maps.
- **CSP** — every inline `<script>` uses `nonce="{{ csp_nonce }}"`; never inject `<style>` from JS; load page CSS via `<link href="/static/nutrition.css?v={{ _v }}">`. External scripts only from `cdn.jsdelivr.net` / `*.googletagmanager.com`.
- **CSRF** — state-changing calls use `fetch` (auto-wrapped by `static/csrf.js`); keep `_head.html` included; never expose a state-changing route as GET.
- **Ownership** — every query scoped to `current_user.id` (backend already does this; keep it).
- **Design system** — reuse `components.css` primitives (`.card`, `.ring-*`, `.stat-card`, `.pbar-*`, `.badge-*`, `.sheet*`, `.empty-state`, `.tab-*`, `.skeleton`, `.modal*`, `.toast*`). No `--volt`/hex/rgba literals in new nutrition CSS — use canonical tokens.
- **Test:** `python -m pytest -q` must stay green; run affected tests per task.
- **Do NOT `git add -A`** — untracked scratch exists at repo root; stage explicit paths only.

## File Structure

- `app/services/fatsecret.py` — MODIFY: add `_food_find_by_barcode(code)` helper.
- `app/blueprints/food.py` — MODIFY: add `GET /api/food/barcode` route.
- `app/blueprints/nutrition/meallog.py` — MODIFY: add `created_at` to `/meal-log/today` meal dicts.
- `static/nutrition.css` — CREATE: page-specific styles on canonical tokens (timeline card, FAB sheet options, barcode overlay, voice placeholder, macro header).
- `templates/nutrition.html` — REWRITE: canonical shell, macro-ring header, meal timeline, FAB+sheet, barcode overlay, voice placeholder, restyled tabs.
- `static/nutrition.js` — REWRITE/EXTEND: macro ring, timeline render, `mealScore()`, sheet controller, photo flow, barcode flow, voice placeholder, existing flows preserved.
- `locales/tr.json`, `locales/en.json` — MODIFY: new UI keys.
- `tests/test_barcode.py` — CREATE: barcode route tests.
- `tests/` (existing nutrition/i18n) — MODIFY: `created_at` assertion + render assertions.
- `docs/handoff.md` — REWRITE at phase end.

---

### Task 1: FatSecret barcode lookup (backend)

**Files:**
- Modify: `app/services/fatsecret.py` (add `_food_find_by_barcode`)
- Modify: `app/blueprints/food.py` (add route)
- Test: `tests/test_barcode.py` (create)

**Interfaces:**
- Consumes: `_get_fatsecret_token()`, `_fs_get(url, ...)`, `_food_get_servings(food_id)`, `FATSECRET_API_URL` (all existing in the module / config).
- Produces:
  - `_food_find_by_barcode(code: str) -> dict | None` → `{"food_id": str, "name": str, "brand": str, "servings": list}` or `None` when not found.
  - Route `GET /api/food/barcode?code=<digits>` → `200 {food_id,name,brand,servings}` | `400 {error}` (bad/empty code) | `404 {error}` (not found).

- [ ] **Step 1: Write the failing test**

Create `tests/test_barcode.py`:

```python
from unittest.mock import patch
import app.services.fatsecret as fs


def test_normalize_and_find(monkeypatch):
    # find_id_for_barcode returns a food_id; food.get returns name/brand.
    calls = {}

    class FakeResp:
        def __init__(self, data): self._d = data
        def json(self): return self._d

    def fake_get(url, params=None, headers=None, timeout=None):
        method = params.get("method")
        calls[method] = params
        if method == "food.find_id_for_barcode":
            return FakeResp({"food_id": {"value": "77777"}})
        if method.startswith("food.get"):
            return FakeResp({"food": {"food_id": "77777",
                                      "food_name": "Protein Bar",
                                      "brand_name": "Acme"}})
        return FakeResp({})

    monkeypatch.setattr(fs, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fs, "_fs_get", fake_get)
    monkeypatch.setattr(fs, "_food_get_servings", lambda fid: [{"serving_description": "1 bar"}])

    out = fs._food_find_by_barcode("5000159407236")
    assert out["food_id"] == "77777"
    assert out["name"] == "Protein Bar"
    assert out["brand"] == "Acme"
    assert out["servings"] == [{"serving_description": "1 bar"}]
    # 13-digit GTIN passed through
    assert calls["food.find_id_for_barcode"]["barcode"] == "5000159407236"


def test_not_found_returns_none(monkeypatch):
    class FakeResp:
        def __init__(self, data): self._d = data
        def json(self): return self._d
    monkeypatch.setattr(fs, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fs, "_fs_get",
        lambda *a, **k: FakeResp({"food_id": {"value": "0"}}))
    assert fs._food_find_by_barcode("12345678") is None


def test_short_code_left_padded_to_13(monkeypatch):
    seen = {}
    class FakeResp:
        def __init__(self, data): self._d = data
        def json(self): return self._d
    def fake_get(url, params=None, headers=None, timeout=None):
        seen["barcode"] = params.get("barcode")
        return FakeResp({"food_id": {"value": "0"}})
    monkeypatch.setattr(fs, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(fs, "_fs_get", fake_get)
    fs._food_find_by_barcode("40822938")   # UPC-ish, <13
    assert seen["barcode"] == "0000040822938"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_barcode.py -q`
Expected: FAIL — `AttributeError: module 'app.services.fatsecret' has no attribute '_food_find_by_barcode'`.

- [ ] **Step 3: Implement the helper**

Append to `app/services/fatsecret.py` (near `_food_get_servings`):

```python
def _food_find_by_barcode(code):
    """FatSecret barkod → food_id → ad/marka + porsiyonlar.

    ``code`` yalnızca rakamlardan oluşmalı; GTIN-13'e sola-sıfır dolgulanır
    (FatSecret 13 haneli GTIN bekler; UPC-A/EAN-8 kısa gelir). Bulunamazsa
    (food_id == '0') None döner. Ağ/parse hatasında None (fail-soft)."""
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if not digits:
        return None
    gtin = digits[-13:].rjust(13, "0")
    try:
        token = _get_fatsecret_token()
    except Exception as e:  # pragma: no cover - token yolu ayrı test edilir
        current_app.logger.error("_food_find_by_barcode token failed: %s", e)
        return None

    try:
        resp = _fs_get(FATSECRET_API_URL, params={
            "method": "food.find_id_for_barcode",
            "barcode": gtin, "format": "json",
        }, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        data = resp.json()
    except Exception as e:
        current_app.logger.warning("_food_find_by_barcode lookup failed: %s", e)
        return None

    fid = str((data.get("food_id") or {}).get("value", "0"))
    if not fid or fid == "0":
        return None

    name, brand = "", ""
    try:
        for method in ("food.get.v4", "food.get.v2", "food.get"):
            gr = _fs_get(FATSECRET_API_URL, params={
                "method": method, "food_id": fid, "format": "json",
            }, headers={"Authorization": f"Bearer {token}"}, timeout=5)
            gdata = gr.json()
            if "error" in gdata:
                continue
            food = gdata.get("food", {}) or {}
            name = food.get("food_name", "") or name
            brand = food.get("brand_name", "") or brand
            if name:
                break
    except Exception as e:
        current_app.logger.info("_food_find_by_barcode name lookup soft-fail: %s", e)

    servings = _food_get_servings(fid) or []
    return {"food_id": fid, "name": name, "brand": brand, "servings": servings}
```

- [ ] **Step 4: Add the route**

In `app/blueprints/food.py`, import the helper and add a route (mirror `food_servings` auth/limit):

```python
from app.services.fatsecret import (
    _food_find_by_barcode, _food_get_servings, _fs_get, _get_fatsecret_token,
)


@bp.route("/api/food/barcode")
@login_required
@limiter.limit(FOOD_SEARCH_RATELIMIT, key_func=_user_or_ip_key)
def food_by_barcode():
    code = request.args.get("code", "").strip()
    if not any(ch.isdigit() for ch in code):
        return jsonify({"error": "invalid_barcode"}), 400
    result = _food_find_by_barcode(code)
    if not result:
        return jsonify({"error": "not_found"}), 404
    return jsonify(result)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_barcode.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add app/services/fatsecret.py app/blueprints/food.py tests/test_barcode.py
git commit -m "Add FatSecret barcode lookup endpoint"
```

---

### Task 2: `created_at` on `/meal-log/today` (backend)

**Files:**
- Modify: `app/blueprints/nutrition/meallog.py:233-243` (today meal dict)
- Test: `tests/test_meallog_today.py` (create or extend)

**Interfaces:**
- Produces: each object in `meals[]` of `GET /meal-log/today` gains `"created_at"` (ISO 8601 UTC string, or `None`). Additive; existing keys unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_meallog_today.py` (uses whatever authed-client fixture the suite already provides; pattern mirrors existing nutrition tests):

```python
def test_today_meal_has_created_at(auth_client, seed_meal):
    # seed_meal inserts one MealLog for the logged-in user for today.
    r = auth_client.get("/meal-log/today")
    assert r.status_code == 200
    meals = r.get_json()["meals"]
    assert meals and "created_at" in meals[0]
```

> If the suite lacks `auth_client`/`seed_meal` fixtures, reuse the login/seed
> helper pattern from the existing nutrition test module and inline the setup.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_meallog_today.py -q`
Expected: FAIL — `KeyError`/assertion: `created_at` not in meal dict.

- [ ] **Step 3: Implement**

In `app/blueprints/nutrition/meallog.py`, inside `today_meals()` where each meal dict is built (currently ends with `"photo_url": _meal_photo_url(m)`), add:

```python
        result.append({
            "ogun": m.ogun,
            "yemekler": m.yemekler,
            "kalori": m.kalori,
            "protein": m.protein,
            "karb": m.karb,
            "yag": m.yag,
            "source": getattr(m, "source", "manual") or "manual",
            "photo_url": _meal_photo_url(m),
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_meallog_today.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/blueprints/nutrition/meallog.py tests/test_meallog_today.py
git commit -m "Expose meal created_at in /meal-log/today"
```

---

### Task 3: New page stylesheet `static/nutrition.css`

**Files:**
- Create: `static/nutrition.css`

**Interfaces:**
- Produces CSS classes consumed by Task 4/5 markup: `.nut-hero`, `.nut-macros`,
  `.meal-timeline`, `.meal-slot`, `.slot-head`, `.meal-card`, `.mc-img`,
  `.mc-body`, `.mc-macros`, `.mc-score`, `.mc-edit`, `.slot-empty`,
  `.log-fab`, `.log-sheet-opt`, `.log-sheet-grid`, `.scan-overlay`,
  `.scan-video`, `.scan-frame`, `.scan-manual`, `.voice-placeholder`.

- [ ] **Step 1: Write the stylesheet**

Create `static/nutrition.css`. Use canonical tokens only (no `--volt`/hex). Key blocks (representative — fill remaining states to match `dashboard.css` density):

```css
/* Phase 4 nutrition page — canonical tokens, mobile-first */

/* ── Macro header ── */
.nut-hero { display: grid; grid-template-columns: auto 1fr; gap: var(--space-5);
  align-items: center; }
@media (max-width: 560px) { .nut-hero { grid-template-columns: 1fr;
  justify-items: center; text-align: center; } }
.nut-macros { display: grid; grid-template-columns: repeat(4, 1fr);
  gap: var(--space-2); }
@media (max-width: 420px) { .nut-macros { grid-template-columns: repeat(2, 1fr); } }

/* ── Meal timeline ── */
.meal-timeline { display: flex; flex-direction: column; gap: var(--space-4); }
.meal-slot { }
.slot-head { display: flex; align-items: center; gap: var(--space-2);
  margin-bottom: var(--space-2); }
.slot-head .slot-emoji { font-size: 18px; }
.slot-head .slot-name { font-family: var(--font-display);
  letter-spacing: 1.5px; color: var(--color-text-1); font-size: var(--text-md); }
.slot-head .slot-kcal { margin-left: auto; color: var(--color-text-3);
  font-size: var(--text-sm); }

.meal-card { display: flex; gap: var(--space-3); align-items: stretch;
  background: var(--color-surface-2);
  border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-lg); padding: var(--space-3);
  margin-bottom: var(--space-2); animation: card-in var(--duration-slow) var(--ease-out-quint); }
.mc-img { width: 64px; height: 64px; flex-shrink: 0; border-radius: var(--radius-md);
  object-fit: cover; background:
    linear-gradient(135deg, var(--color-primary-soft), var(--overlay-4)); }
.mc-body { flex: 1; min-width: 0; }
.mc-title { color: var(--color-text-1); font-weight: var(--weight-semibold);
  font-size: var(--text-md); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
.mc-macros { display: flex; gap: var(--space-3); margin-top: 4px;
  font-size: var(--text-sm); color: var(--color-text-3); }
.mc-time { font-size: var(--text-xs); color: var(--color-text-4); margin-top: 2px; }
.mc-side { display: flex; flex-direction: column; align-items: flex-end;
  justify-content: space-between; }
.mc-edit { min-width: 44px; min-height: 44px; display: flex; align-items: center;
  justify-content: center; color: var(--color-text-3); background: none;
  border: none; cursor: pointer; border-radius: var(--radius-md); }
.mc-edit:hover { color: var(--color-primary); background: var(--overlay-4); }

.slot-empty { display: flex; align-items: center; gap: var(--space-2);
  padding: var(--space-3); border: var(--border-w-1) dashed var(--color-border-2);
  border-radius: var(--radius-lg); color: var(--color-text-3);
  font-size: var(--text-sm); cursor: pointer; min-height: 44px; }
.slot-empty:hover { border-color: var(--color-primary); color: var(--color-primary); }

@keyframes card-in { from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; } }

/* ── Log FAB (single, prominent, bottom-right) ── */
.log-fab { position: fixed; right: var(--space-4);
  bottom: calc(var(--action-bar-h, 64px) + var(--space-4)); z-index: var(--z-fab);
  width: var(--fab-size, 56px); height: var(--fab-size, 56px);
  border-radius: var(--radius-full); border: none; cursor: pointer;
  background: var(--color-primary); color: var(--color-on-primary);
  box-shadow: var(--elevation-3); display: flex; align-items: center;
  justify-content: center; }
.log-fab svg { width: 24px; height: 24px; stroke: currentColor; fill: none;
  stroke-width: 2.4; stroke-linecap: round; }
.log-fab:active { transform: scale(0.94); }

/* ── FAB sheet options ── */
.log-sheet-grid { display: flex; flex-direction: column; gap: var(--space-2); }
.log-sheet-opt { display: flex; align-items: center; gap: var(--space-3);
  width: 100%; min-height: 56px; padding: var(--space-3) var(--space-4);
  background: var(--color-surface-3);
  border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-lg); color: var(--color-text-1); cursor: pointer;
  text-align: left; transition: border-color var(--duration-fast) var(--ease-standard),
    background var(--duration-fast) var(--ease-standard); }
.log-sheet-opt:hover { border-color: var(--color-primary);
  background: var(--color-primary-soft); }
.log-sheet-opt .lso-ic { width: 40px; height: 40px; flex-shrink: 0;
  border-radius: var(--radius-md); display: flex; align-items: center;
  justify-content: center; background: var(--color-primary-soft);
  color: var(--color-primary); }
.log-sheet-opt .lso-txt { flex: 1; min-width: 0; }
.log-sheet-opt .lso-title { font-weight: var(--weight-semibold); }
.log-sheet-opt .lso-sub { font-size: var(--text-sm); color: var(--color-text-3); }
.log-sheet-opt .lso-badge { font-size: var(--text-xs); color: var(--color-text-4); }

/* ── Barcode scan overlay ── */
.scan-overlay { position: fixed; inset: 0; z-index: var(--z-overlay);
  background: #000; display: none; flex-direction: column; }
.scan-overlay.open { display: flex; }
.scan-video { flex: 1; width: 100%; object-fit: cover; }
.scan-frame { position: absolute; top: 50%; left: 50%;
  transform: translate(-50%, -50%); width: min(72vw, 320px); height: 180px;
  border: 3px solid var(--color-primary); border-radius: var(--radius-lg);
  box-shadow: 0 0 0 9999px rgba(0,0,0,0.45); }
.scan-manual { padding: var(--space-4); background: var(--color-surface-2); }

/* ── Voice placeholder ── */
.voice-placeholder { text-align: center; padding: var(--space-6) var(--space-4);
  display: flex; flex-direction: column; align-items: center; gap: var(--space-3); }
.voice-mic { width: 88px; height: 88px; border-radius: var(--radius-full);
  background: var(--color-primary-soft); color: var(--color-primary);
  display: flex; align-items: center; justify-content: center; }
```

- [ ] **Step 2: Sanity-check token references**

Run: `grep -nE "\-\-volt|#[0-9a-fA-F]{3,6}|rgba\(" static/nutrition.css`
Expected: no `--volt` and no raw hex (the `#000` on `.scan-overlay` for a full-bleed camera background is the single allowed exception; confirm nothing else). Fix any stragglers to tokens.

- [ ] **Step 3: Commit**

```bash
git add static/nutrition.css
git commit -m "Add canonical nutrition.css for Phase 4 redesign"
```

---

### Task 4: Rewrite `templates/nutrition.html` shell + Today markup

**Files:**
- Rewrite: `templates/nutrition.html`

**Interfaces:**
- Consumes: `nutrition.css` classes (Task 3), `components.css` primitives, existing includes (`_head.html`, `_nav.html`, `_actionbar.html`), `t()` keys (Task 10).
- Produces DOM ids/hooks consumed by Task 5+ JS: `#calorie-ring`, `#ring-eaten`, `#ring-pct`, `#ring-target`, macro tile ids `#macro-protein/#macro-karb/#macro-yag` + bars, `#meal-timeline`, `.tab-btn[data-args]`, `#log-fab`, `#log-sheet` (`.sheet-backdrop`), `#scan-overlay`, `#scan-video`, `#voice-sheet`, plus preserved modal ids `#serving-modal`, `#water-modal`.

- [ ] **Step 1: Rewrite the template**

Replace `templates/nutrition.html`. Structure:
- `<head>`: keep `_head.html`; swap `theme.css`→ keep `theme.css` only if still referenced by shared partials, otherwise rely on `tokens.css`/`components.css` already loaded by `_head.html` (verify what `_head.html` loads). Add `<link rel="stylesheet" href="/static/nutrition.css?v={{ _v }}">`. Move the page-specific `<style>` block's still-needed rules into `nutrition.css`; delete `--volt` rules.
- Keep loading overlay, toast wrap, `_nav.html`, page header, `.tab-bar` (5 tabs) — restyle via canonical classes.
- **Today panel**: replace the calorie card with `.card > .nut-hero` (ring + `.nut-macros` of four `.stat-card`s), then `.meal-timeline#meal-timeline` (JS-rendered), then remove the inline manual form + old bottom-left FAB from the panel (manual entry now lives in the sheet).
- After `</main>`: add the single `.log-fab#log-fab` (`data-action="openLogSheet"`), the `.sheet-backdrop#log-sheet` containing `.sheet` with `.sheet-handle`, `.sheet-title`, and `.log-sheet-grid` of 5 `.log-sheet-opt` buttons (Take Photo / Scan Barcode / Menu Scanner / Voice / Manual Entry) each with `data-action`.
- Add hidden `<input type="file" accept="image/*" capture="environment" id="photo-input">` for Take Photo.
- Add `#scan-overlay` (barcode camera) with `<video id="scan-video">`, `.scan-frame`, a close button, and `.scan-manual` (number input + submit + "type barcode" copy).
- Add `#voice-sheet` (`.sheet-backdrop`) with `.voice-placeholder` ("Available in the mobile app").
- Keep `#serving-modal` and `#water-modal` (restyled). Keep script includes: `nutrition.js`, `coach_widget.js`, `actions.js`.

Representative FAB + sheet markup:

```html
<button class="log-fab" id="log-fab" data-action="openLogSheet"
        aria-label="{{ t('nutrition.log_add') }}">
  <svg viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
</button>

<div class="sheet-backdrop" id="log-sheet" data-action-self="closeLogSheet">
  <div class="sheet" role="dialog" aria-modal="true" aria-label="{{ t('nutrition.log_add') }}">
    <div class="sheet-handle"></div>
    <div class="sheet-title">{{ t('nutrition.log_add') }}</div>
    <div class="log-sheet-grid">
      <button class="log-sheet-opt" data-action="logTakePhoto">
        <span class="lso-ic"><!-- camera icon --></span>
        <span class="lso-txt"><span class="lso-title">{{ t('nutrition.log_photo') }}</span>
          <span class="lso-sub">{{ t('nutrition.log_photo_sub') }}</span></span>
      </button>
      <button class="log-sheet-opt" data-action="logScanBarcode">
        <span class="lso-ic"><!-- barcode icon --></span>
        <span class="lso-txt"><span class="lso-title">{{ t('nutrition.log_barcode') }}</span>
          <span class="lso-sub">{{ t('nutrition.log_barcode_sub') }}</span></span>
      </button>
      <button class="log-sheet-opt" data-action="logMenuScan">
        <span class="lso-ic"><!-- menu icon --></span>
        <span class="lso-txt"><span class="lso-title">{{ t('nutrition.log_menu') }}</span>
          <span class="lso-sub">{{ t('nutrition.log_menu_sub') }}</span></span>
      </button>
      <button class="log-sheet-opt" data-action="logVoice">
        <span class="lso-ic"><!-- mic icon --></span>
        <span class="lso-txt"><span class="lso-title">{{ t('nutrition.log_voice') }}</span>
          <span class="lso-sub">{{ t('nutrition.log_voice_sub') }}</span></span>
        <span class="lso-badge">{{ t('nutrition.mobile_only') }}</span>
      </button>
      <button class="log-sheet-opt" data-action="logManual">
        <span class="lso-ic"><!-- pencil icon --></span>
        <span class="lso-txt"><span class="lso-title">{{ t('nutrition.log_manual') }}</span>
          <span class="lso-sub">{{ t('nutrition.log_manual_sub') }}</span></span>
      </button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Verify _head.html CSS + CSP nonce**

Run: `grep -nE "tokens.css|components.css|theme.css|dashboard.css" templates/_head.html`
Confirm which base stylesheets load globally so the page doesn't double-load or miss tokens. Ensure any inline `<script>` in the template has `nonce="{{ csp_nonce }}"`.

- [ ] **Step 3: Smoke render**

Run: `python -m pytest tests/test_i18n.py -q -k nutrition` (or the existing nutrition render test). Expected: PASS or a clear assertion pointing at renamed markup (update those assertions in Task 10). At minimum the template must render without a Jinja error — verify with a quick `flask` route test if available.

- [ ] **Step 4: Commit**

```bash
git add templates/nutrition.html
git commit -m "Rewrite nutrition template: macro header, timeline, FAB sheet"
```

---

### Task 5: Macro ring + meal timeline + AI Score (JS)

**Files:**
- Modify: `static/nutrition.js`

**Interfaces:**
- Consumes: `GET /meal-log/today` (`{meals:[{ogun,yemekler,kalori,protein,karb,yag,photo_url,created_at}], totals}`), `GET /last-session` (target calories), existing helpers `esc`, `mealLabel`, `showToast`, `updateRing`.
- Produces: `mealScore(meal) -> {value:int, grade:str, tone:str}`, `renderTimeline(meals)`, `SLOTS` order const, `quickEditMeal(el)`. Ring/macro update reuses existing `updateRing`/`updateMacroBars` retargeted to new ids.

- [ ] **Step 1: Add `mealScore` + a unit check**

Add to `static/nutrition.js`:

```javascript
/* Deterministic client-side meal quality score (0-100 + grade + tone).
   Pure function of the meal's own macros. No network. */
function mealScore(m) {
  var kcal = Math.max(+m.kalori || 0, 1);
  var pK = (+m.protein || 0) * 4, cK = (+m.karb || 0) * 4, fK = (+m.yag || 0) * 9;
  var sum = Math.max(pK + cK + fK, 1);
  var pShare = pK / sum, cShare = cK / sum, fShare = fK / sum;

  // 1) Protein density (0-40): ideal protein share >= 0.30
  var pScore = Math.min(pShare / 0.30, 1) * 40;
  // 2) Macro balance (0-35): penalize extreme fat (>0.40) or carb (>0.60) share
  var balance = 35;
  if (fShare > 0.40) balance -= (fShare - 0.40) * 60;
  if (cShare > 0.60) balance -= (cShare - 0.60) * 50;
  balance = Math.max(0, balance);
  // 3) Calorie sanity (0-25): full up to 900 kcal, decays after
  var cal = 25;
  if (kcal > 900) cal -= Math.min((kcal - 900) / 30, 25);
  cal = Math.max(0, cal);

  var value = Math.round(pScore + balance + cal);
  var grade, tone;
  if (value >= 75)      { grade = 'A'; tone = 'success'; }
  else if (value >= 55) { grade = 'B'; tone = 'success'; }
  else if (value >= 40) { grade = 'C'; tone = 'warning'; }
  else                  { grade = 'D'; tone = 'danger';  }
  return { value: value, grade: grade, tone: tone };
}
```

Temporary console check (remove before commit): a high-protein 400 kcal meal scores ≥75; a 1500 kcal all-carb meal scores <40.

- [ ] **Step 2: Implement `renderTimeline`**

Replace `renderTodayMeals` with a slot-grouped `renderTimeline(meals)`:

```javascript
var SLOTS = [
  { key: 'Kahvaltı',  emoji: '🍳' },
  { key: 'Öğle',      emoji: '🥗' },
  { key: 'Akşam',     emoji: '🍽️' },
  { key: 'Ara Öğün',  emoji: '🥜' },
];

function fmtTime(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleTimeString(_EN ? 'en-GB' : 'tr-TR',
      { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Istanbul' });
  } catch (e) { return ''; }
}

function mealCardHTML(m) {
  var s = mealScore(m);
  var img = m.photo_url
    ? '<img class="mc-img" src="' + esc(m.photo_url) + '" alt="">'
    : '<div class="mc-img"></div>';
  return '<div class="meal-card">' + img +
    '<div class="mc-body"><div class="mc-title">' + esc(m.yemekler) + '</div>' +
      '<div class="mc-macros"><span>' + Math.round(m.kalori||0) + ' kcal</span>' +
      '<span>P ' + Math.round(m.protein||0) + '</span>' +
      '<span>K ' + Math.round(m.karb||0) + '</span>' +
      '<span>Y ' + Math.round(m.yag||0) + '</span></div>' +
      '<div class="mc-time">' + fmtTime(m.created_at) + '</div></div>' +
    '<div class="mc-side"><span class="badge badge-' + s.tone + '">' + s.grade + '</span>' +
      '<button class="mc-edit" data-action="quickEditMeal" data-args=\'["' +
        esc(m.ogun) + '"]\' aria-label="' + __t('nutrition.quick_edit') + '">' +
        '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>' +
      '</button></div></div>';
}

function renderTimeline(meals) {
  var box = document.getElementById('meal-timeline');
  if (!box) return;
  var bySlot = { 'Kahvaltı': [], 'Öğle': [], 'Akşam': [], 'Ara Öğün': [] };
  (meals || []).forEach(function (m) {
    (bySlot[m.ogun] || (bySlot['Ara Öğün'])).push(m);
  });
  box.innerHTML = SLOTS.map(function (slot) {
    var items = bySlot[slot.key] || [];
    var kcal = items.reduce(function (a, m) { return a + (m.kalori || 0); }, 0);
    var head = '<div class="slot-head"><span class="slot-emoji">' + slot.emoji +
      '</span><span class="slot-name">' + mealLabel(slot.key) + '</span>' +
      '<span class="slot-kcal">' + Math.round(kcal) + ' kcal</span></div>';
    var body = items.length
      ? items.map(mealCardHTML).join('')
      : '<div class="slot-empty" data-action="logManualSlot" data-args=\'["' +
          esc(slot.key) + '"]\'>+ ' + __t('nutrition.add_to_meal') + '</div>';
    return '<div class="meal-slot">' + head + body + '</div>';
  }).join('');
}
```

- [ ] **Step 3: Retarget `loadTodayData`**

Update `loadTodayData()` to call `renderTimeline(meals)` (instead of `renderTodayMeals`), keep `updateRing`/`updateMacroBars` (retarget ids if the header ids changed), and drop the removed quick-add-section/manual-form calls that no longer exist. `quickEditMeal(ogunKey)` opens the manual-entry sheet with that meal type preselected (reuse `selectMealType` + `openManualSheet` from Task 8).

- [ ] **Step 4: Manual browser verification**

Run the app (`flask run` with a seeded user) → open `/nutrition` → confirm: ring fills, four macro tiles animate, timeline shows four slots, logged meals render as cards with image/macros/time/score badge/edit; empty slots show the add prompt.

- [ ] **Step 5: Commit**

```bash
git add static/nutrition.js
git commit -m "Add macro timeline + client-side AI meal score"
```

---

### Task 6: FAB bottom sheet + Take Photo flow (JS)

**Files:**
- Modify: `static/nutrition.js`, `templates/nutrition.html` (photo confirm markup if needed)

**Interfaces:**
- Consumes: `#log-sheet`, `#photo-input`, `POST /meal-log` (accepts `{ogun, yemekler, image}`; image = base64 data URL).
- Produces: `openLogSheet()`, `closeLogSheet()`, `logTakePhoto()`, `logMenuScan()`, `logVoice()`, `logManual()`, `logManualSlot(ogun)`, `_readFileAsDataURL(file)`.

- [ ] **Step 1: Sheet open/close + option handlers**

```javascript
function openLogSheet()  { document.getElementById('log-sheet').classList.add('open'); }
function closeLogSheet() { document.getElementById('log-sheet').classList.remove('open'); }

function logTakePhoto() { closeLogSheet(); document.getElementById('photo-input').click(); }
function logMenuScan()  { closeLogSheet();
  if (window.CW && window.CW.toggle) { window.CW.toggle(); if (window.CW.startScan) window.CW.startScan(); }
  else { location.href = '/nutrition#menu'; } }
function logVoice()     { closeLogSheet(); document.getElementById('voice-sheet').classList.add('open'); }
function logManual()    { closeLogSheet(); openManualSheet(); }              // Task 8
function logManualSlot(ogun) { selectMealType(ogun); openManualSheet(); }    // Task 8
```

- [ ] **Step 2: Photo capture → confirm → log**

`#photo-input`'s `change` reads the file, shows a confirm mini-form (meal type + optional text), then POSTs to `/meal-log` with `image`:

```javascript
function _readFileAsDataURL(file) {
  return new Promise(function (res, rej) {
    var r = new FileReader();
    r.onload = function () { res(r.result); };
    r.onerror = rej; r.readAsDataURL(file);
  });
}

async function onPhotoPicked(el) {
  var file = el.files && el.files[0];
  if (!file) return;
  var dataUrl = await _readFileAsDataURL(file);
  el.value = '';                        // allow re-pick same file
  openPhotoConfirm(dataUrl);            // shows meal-type + note, default current slot
}

async function submitPhotoMeal(dataUrl, ogun, note) {
  var r = await fetch('/meal-log', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ogun: ogun, yemekler: note || mealLabel(ogun), image: dataUrl }) });
  var d = await r.json();
  if (!r.ok) { showToast(d.error || 'error', 'error'); return; }
  showToast(__t('nutrition.meal_saved'), 'success');
  closePhotoConfirm(); loadTodayData();
}
```

`openPhotoConfirm`/`closePhotoConfirm` toggle a small confirm block (add to the sheet or a dedicated `.modal`; reuse `.modal` primitives). Wire buttons via `data-action`.

- [ ] **Step 3: Wire `change` binding**

Add `<input ... id="photo-input" data-action-change="onPhotoPicked" style="display:none">` (actions.js supports `data-action-change`).

- [ ] **Step 4: Manual browser verification**

Open `/nutrition` → tap FAB → sheet opens with 5 options → Take Photo opens picker → after choosing an image, confirm form logs a meal with the photo (appears in timeline with `mc-img`).

- [ ] **Step 5: Commit**

```bash
git add static/nutrition.js templates/nutrition.html
git commit -m "Add FAB log sheet + Take Photo meal logging"
```

---

### Task 7: Barcode scanner + FatSecret log flow (JS)

**Files:**
- Modify: `static/nutrition.js`, `templates/nutrition.html` (`#scan-overlay` exists from Task 4)

**Interfaces:**
- Consumes: `GET /api/food/barcode?code=` (Task 1), existing serving modal (`openServingModal`/`fetchServings`/`confirmServingModal` path already in nutrition.js), `BarcodeDetector` (browser).
- Produces: `logScanBarcode()`, `startBarcodeScan()`, `stopBarcodeScan()`, `onBarcodeManual(el)`, `resolveBarcode(code)`.

- [ ] **Step 1: Feature-detect + open overlay**

```javascript
function logScanBarcode() { closeLogSheet(); openScanOverlay(); }

function openScanOverlay() {
  document.getElementById('scan-overlay').classList.add('open');
  if ('BarcodeDetector' in window) startBarcodeScan();
  else showManualBarcodeOnly();      // hide video, focus number input
}
```

- [ ] **Step 2: Live detection loop**

```javascript
var _scanStream = null, _scanRAF = null;
async function startBarcodeScan() {
  var video = document.getElementById('scan-video');
  try {
    _scanStream = await navigator.mediaDevices.getUserMedia(
      { video: { facingMode: 'environment' } });
  } catch (e) { showManualBarcodeOnly(); return; }
  video.srcObject = _scanStream; await video.play();
  var det = new window.BarcodeDetector(
    { formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e'] });
  var tick = async function () {
    if (!document.getElementById('scan-overlay').classList.contains('open')) return;
    try {
      var codes = await det.detect(video);
      if (codes && codes.length) { stopBarcodeScan(); resolveBarcode(codes[0].rawValue); return; }
    } catch (e) { /* transient */ }
    _scanRAF = requestAnimationFrame(tick);
  };
  _scanRAF = requestAnimationFrame(tick);
}

function stopBarcodeScan() {
  if (_scanRAF) cancelAnimationFrame(_scanRAF);
  if (_scanStream) { _scanStream.getTracks().forEach(function (t) { t.stop(); }); _scanStream = null; }
}
function closeScanOverlay() { stopBarcodeScan();
  document.getElementById('scan-overlay').classList.remove('open'); }
```

- [ ] **Step 3: Resolve + hand to serving modal**

```javascript
function onBarcodeManual(el) { var c = (el.value || '').trim(); if (c) resolveBarcode(c); }

async function resolveBarcode(code) {
  closeScanOverlay();
  showToast(__t('nutrition.barcode_looking'), 'info');
  var r = await fetch('/api/food/barcode?code=' + encodeURIComponent(code));
  if (r.status === 404) { showToast(__t('nutrition.barcode_not_found'), 'error'); return; }
  if (!r.ok) { showToast(__t('nutrition.barcode_error'), 'error'); return; }
  var d = await r.json();
  // Reuse the existing serving-selector modal with the resolved food + servings.
  openServingModal({ food_id: d.food_id, name: d.name, brand: d.brand,
                     servings: d.servings, source: 'barcode' });
}
```

> Verify the exact signature of the existing serving-modal opener in nutrition.js
> (Task 5 area near `fetchServings`/`confirmServingModal`) and adapt the call so a
> pre-resolved `servings` array is used directly instead of re-fetching. On
> confirm, it logs through the existing meal path (serving → `/meal-log`).

- [ ] **Step 4: Manual browser verification**

Desktop Chrome with webcam OR type a known EAN into the manual field → food resolves → serving modal → confirm → meal appears in timeline. On a browser without `BarcodeDetector`, overlay shows manual-entry only.

- [ ] **Step 5: Commit**

```bash
git add static/nutrition.js templates/nutrition.html
git commit -m "Integrate barcode scanning into meal logging"
```

---

### Task 8: Manual-entry sheet, Voice placeholder, Menu reuse (JS/HTML)

**Files:**
- Modify: `static/nutrition.js`, `templates/nutrition.html`

**Interfaces:**
- Produces: `openManualSheet()`, `closeManualSheet()`, `closeVoiceSheet()`. Reuses existing `selectMealType`, food autocomplete (`searchFood`/`selectFood`), `logMeal`.

- [ ] **Step 1: Relocate manual form into a sheet**

Move the previous inline manual form (meal-type grid + food autocomplete + textarea + "Log Meal") into a `.sheet-backdrop#manual-sheet`. `openManualSheet()` opens it; `logMeal()` (existing) on submit; on success close sheet + `loadTodayData()`.

- [ ] **Step 2: Voice placeholder content**

`#voice-sheet` `.voice-placeholder` shows mic icon, `t('nutrition.voice_title')`, `t('nutrition.voice_body')` ("Available in the mobile app"), and a disabled/close button. `closeVoiceSheet()` closes it. Leave a documented hook comment: `// NATIVE-VOICE-HOOK: wire native STT result into openManualSheet() + prefill textarea`.

- [ ] **Step 3: Menu reuse already wired** (Task 6 `logMenuScan`). Confirm `window.CW` exists (coach_widget.js loaded on page).

- [ ] **Step 4: Manual browser verification**

FAB → Manual Entry opens the manual sheet and logs a meal; FAB → Voice shows the placeholder; FAB → Menu Scanner opens the coach widget.

- [ ] **Step 5: Commit**

```bash
git add static/nutrition.js templates/nutrition.html
git commit -m "Add manual-entry sheet, voice placeholder, menu reuse"
```

---

### Task 9: Restyle Diary / Plan / History / Water tabs

**Files:**
- Modify: `templates/nutrition.html`, `static/nutrition.css`, `static/nutrition.js` (only class/markup, no logic)

**Interfaces:** none new — purely visual token migration.

- [ ] **Step 1: Migrate remaining panels**

Replace `--volt`/`theme.css` inline styles for Diary, Plan, History, Water panels with canonical `.card`, `.stat-card`, `.badge`, `.sec-label`, `.empty-state`, `.pbar-*`, `.fc-input`, `.btn-volt/.btn-ghost`. Move any leftover page-specific rules from the template `<style>` into `nutrition.css` using tokens. Water tab keeps its glasses UI but on tokens.

- [ ] **Step 2: Grep for stragglers**

Run: `grep -nE "\-\-volt" templates/nutrition.html static/nutrition.css`
Expected: no matches.

- [ ] **Step 3: Manual browser verification**

Cycle all 5 tabs → visually consistent, no broken layout; Diary add/remove, Plan generate, History chart, Water add still work.

- [ ] **Step 4: Commit**

```bash
git add templates/nutrition.html static/nutrition.css static/nutrition.js
git commit -m "Restyle diary/plan/history/water tabs on canonical tokens"
```

---

### Task 10: i18n keys (TR/EN)

**Files:**
- Modify: `locales/tr.json`, `locales/en.json`
- Modify: existing i18n render test

**Interfaces:** new `nutrition.*` keys used by Tasks 4–9.

- [ ] **Step 1: Add keys**

Add to both locales (TR canonical values, EN translations). Keys:
`nutrition.log_add`, `log_photo`, `log_photo_sub`, `log_barcode`, `log_barcode_sub`,
`log_menu`, `log_menu_sub`, `log_voice`, `log_voice_sub`, `log_manual`,
`log_manual_sub`, `mobile_only`, `voice_title`, `voice_body`, `quick_edit`,
`add_to_meal`, `meal_saved`, `barcode_looking`, `barcode_not_found`,
`barcode_error`, `barcode_manual_ph`, `barcode_manual_hint`, `camera_unavailable`.

Example (tr.json):

```json
"nutrition.log_add": "Öğün Ekle",
"nutrition.log_photo": "Fotoğraf Çek",
"nutrition.log_photo_sub": "Yemeğini çek, otomatik hesaplansın",
"nutrition.log_barcode": "Barkod Tara",
"nutrition.log_barcode_sub": "Ambalajlı ürünü tara",
"nutrition.log_voice": "Sesli Giriş",
"nutrition.log_voice_sub": "Söyle, yazalım",
"nutrition.mobile_only": "Mobil uygulamada",
"nutrition.voice_title": "Sesli Giriş",
"nutrition.voice_body": "Sesli öğün girişi mobil uygulamada kullanılabilir.",
"nutrition.barcode_not_found": "Ürün bulunamadı"
```

- [ ] **Step 2: Update render assertions**

Update the existing nutrition/i18n render test to assert new visible strings (e.g. "Öğün Ekle" present) and drop assertions on removed markup.

- [ ] **Step 3: Run i18n + full suite**

Run: `python -m pytest tests/test_i18n.py -q` then `python -m pytest -q`.
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add locales/tr.json locales/en.json tests/test_i18n.py
git commit -m "Add i18n keys for nutrition redesign"
```

---

### Task 11: Improvements pass — a11y, empty/loading states, responsive

**Files:**
- Modify: `static/nutrition.css`, `templates/nutrition.html`, `static/nutrition.js`

- [ ] **Step 1: A11y + touch targets**

Ensure: sheet/modal have `role="dialog" aria-modal="true"` + focus moves in on open and returns on close + `Esc` closes; tabs have `aria-selected`; all icon buttons have `aria-label`; interactive targets ≥44px; `:focus-visible` rings via tokens.

- [ ] **Step 2: Loading + empty states**

Timeline shows `.skeleton` while `/meal-log/today` is in flight; a fully-empty day shows a friendly `.empty-state` ("Bugün henüz öğün yok — ekle"). Barcode-not-found and camera-unavailable use `.empty-state`/toast copy.

- [ ] **Step 3: Responsive check**

Verify at 360px / 768px / 1024px: no horizontal overflow, ring+tiles stack then split, sheet centers ≥768px, cards wrap gracefully. (Mirror the dashboard overflow fix from commit afc9626.)

- [ ] **Step 4: Reduced motion**

Wrap non-essential animations in `@media (prefers-reduced-motion: no-preference)` or add a `prefers-reduced-motion: reduce` override that disables transforms.

- [ ] **Step 5: Full verification**

Run: `python -m pytest -q` → all pass. Manually walk every FAB path + all 5 tabs at mobile width.

- [ ] **Step 6: Commit**

```bash
git add static/nutrition.css templates/nutrition.html static/nutrition.js
git commit -m "Nutrition a11y, empty/loading states, responsive polish"
```

---

### Task 12: Handoff doc

**Files:**
- Rewrite: `docs/handoff.md`

- [ ] **Step 1: Write handoff** per phase-4.txt "End" checklist: Completed work, Files modified, Components created/refactored, Architectural decisions, Remaining tasks, Known issues, Next steps, plus the quality review (Responsiveness / Accessibility / Visual consistency / Code maintainability / Reusability / Performance / UX clarity). Note any weak metric + follow-up.

- [ ] **Step 2: Commit**

```bash
git add docs/handoff.md docs/superpowers/plans/2026-07-06-phase4-nutrition-redesign.md
git commit -m "Phase 4 nutrition redesign handoff"
```

---

## Self-Review

**Spec coverage:** Macro ring + tiles → Task 4/5. Meal timeline cards (image, kcal, macros, time, AI score, quick edit) → Task 5. FAB bottom sheet (Photo/Barcode/Menu/Voice/Manual) → Task 4/6/7/8. Image-first, search demoted → Task 6/8. Barcode fully integrated → Task 1/7. Voice placeholder "mobile app" → Task 8. AI score client-side → Task 5. Restyle all tabs → Task 9. `created_at` backend → Task 2. i18n → Task 10. Animations/loading/empty/a11y/responsive → Task 11. Handoff → Task 12. All spec sections covered.

**Placeholder scan:** No "TBD/TODO"; the two `>` notes (fixture reuse in Task 2, serving-modal signature in Task 7) are explicit "verify exact existing signature" instructions, not deferrals — the surrounding code is concrete.

**Type consistency:** `_food_find_by_barcode(code) -> {food_id,name,brand,servings}` used identically in Task 1 route and Task 7 `resolveBarcode`. `mealScore(m) -> {value,grade,tone}` produced in Task 5, `tone` maps to `.badge-{tone}` classes present in components.css (`success/warning/danger`). `openLogSheet/closeLogSheet`, `openManualSheet` names consistent across Tasks 6/8. `created_at` key consistent Task 2 ↔ Task 5.

**Known deviation from strict TDD:** UI Tasks (4–9, 11) verify via browser + render tests rather than unit tests — appropriate for a template/JS redesign; backend Tasks 1–2 are fully TDD.
