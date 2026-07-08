# Phase 5 Profile Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/edit-profile` into a clean, app-like identity/membership landing on the canonical AxisAI design system, with account editing behind a bottom sheet — frontend-first, existing backend preserved.

**Architecture:** Rewrite `templates/edit_profile.html` on canonical tokens + reusable components; extract inline `<style>`→`static/profile.css` and inline `<script>`→`static/profile.js`. Reuse the existing `POST /edit-profile` verbatim; the only backend change is one additive GET render-context kwarg (`is_premium`). No schema/migration (expand-only, rollback-safe).

**Tech Stack:** Flask/Jinja2, canonical CSS design system (`static/tokens.css` + `static/components.css` + `static/nav.css`), vanilla JS with `data-action` delegation (`static/actions.js`), custom i18n (`locales/{tr,en}.json` + `window.t`/`__t`), pytest.

## Global Constraints

- **Canonical tokens only** in `static/profile.css`: `--color-*` / `--space-*` / `--radius-*` / `--text-*` / `--weight-*` / `--duration-*` / `--ease-*` / `--elevation-*` / `--overlay-*`. No `--volt*`, no raw hex, no ad-hoc `rgba()`. The rendered `/edit-profile` HTML must contain **no** `--volt`.
- **Backend preserved:** the `POST /edit-profile` handler is byte-for-byte unchanged. The GET route gains exactly one additive kwarg `is_premium=current_user.is_premium`. No other `app/` change. No model/schema/migration.
- **Test-pinned rendered-HTML contract** (must stay green):
  - `<a href="/friends" class="hub-link…">` plus the same `class="hub-link…"` anchors for `/feed`, `/leaderboard`, `/quests`, `/supplements`, `/premium`, `/logout`, and `data-action="setLang"` present (`tests/test_app_shell.py::test_profile_hub_lists_secondary_destinations`).
  - EN render contains `EDIT YOUR INFO`, `Target Weight`, `SAVE`, `Türkçe`, `["kilo verme"]`; and does NOT contain `BİLGİLERİNİ DÜZENLE` / `Hedef Kilo` (`tests/test_i18n.py::test_edit_profile_renders_en`).
  - `class="global-header"`, `class="action-bar"`, no `fx-drawer` (`tests/test_app_shell.py::test_all_app_pages_render_shared_shell`).
  - GET returns 200 and existing POST behavior unchanged (`tests/test_profile_routes.py`).
- **i18n parity:** every `profile.*` key added exists in BOTH `locales/tr.json` and `locales/en.json`. Canonical backend values (`kilo verme`, `kas kazanma`, endonyms `Türkçe`/`English`) stay literal; only visible copy is translated.
- **CSP:** inline `<script nonce="{{ csp_nonce }}">` only; no JS-injected `<style>`; page CSS via `<link>`. Every page JS function called from `data-action`/inline listeners is a top-level global.
- **Membership is presentation-only** over the existing `is_premium` boolean — no plan tiers, billing, expiry, or new fields.
- **Mobile-first**, ≥44px touch targets, `:focus-visible`, `prefers-reduced-motion` respected.

## File Structure

- `locales/tr.json`, `locales/en.json` — **Modify**: add `profile.*` keys (Task 1).
- `static/profile.css` — **Create**: canonical page stylesheet (Task 2).
- `app/blueprints/profile.py` — **Modify** (`edit_profile` GET only, +`is_premium` kwarg) (Task 3).
- `templates/edit_profile.html` — **Rewrite** (Task 3).
- `static/profile.js` — **Create**: page JS (avatar upload, saveProfile, edit-sheet a11y, wearable connect/sync) (Task 3).
- `tests/test_profile_ui.py` — **Create**: render smoke + membership-state tests (Tasks 3 & 4).
- `docs/handoff.md` — **Rewrite** (Task 4); previous handoff archived.

## Reference: exact current markup to reuse (behavior-preserving)

The current `templates/edit_profile.html` (517 lines) is the source of truth for the
reused fragments below. When a task says "reuse the current X markup," copy it from
these current line ranges, changing ONLY the class names to the new `pf-*` classes
defined in `static/profile.css` and keeping every `data-action`/`data-args`/`href`/`id`:

- Goal cards (canonical `data-args='["kilo verme"]'` / `'["kas kazanma"]'`) — current lines 285–294.
- Language cards (`data-action="setLang"`, endonyms `Türkçe`/`English`) — current lines 303–312.
- Wearable cards loop (`data-provider-card`, `wearable-connect`/`wearable-sync`, providers `whoop`+`google_health`) — current lines 322–347.
- Supplement stack loop (`supplements`, `icons`, ratings, `st-active`/`st-low`, empty-state) — current lines 351–392.
- Avatar upload markup (`data-action="fxClickTarget" data-target="avatar-file-input"`, hidden file input, overlay SVG) — current lines 208–219.
- The `saveProfile()`, avatar `change` handler, `updateAvatarLetter()`, `toast()`, wearable connect/sync handlers — current lines 399–513 (moved verbatim into `static/profile.js`, unchanged logic).

---

### Task 1: Add `profile.*` i18n keys (both locales, parity)

**Files:**
- Modify: `locales/tr.json`, `locales/en.json`
- Test: `tests/test_i18n.py` (existing parity/EN tests must stay green; no new test file)

**Interfaces:**
- Produces: the `profile.*` keys consumed by the Task 3 template — exact keys:
  `profile.membership`, `profile.plan_free`, `profile.plan_free_desc`, `profile.upgrade_cta`,
  `profile.plan_premium`, `profile.plan_premium_thanks`, `profile.premium_badge`,
  `profile.edit_profile`, `profile.xp_to_next`, `profile.level`, `profile.streak`,
  `profile.sec_community`, `profile.sec_you`, `profile.sec_settings`, `profile.integrations`,
  `profile.change_photo`.

- [ ] **Step 1: Locate the `progress.*` block end in each locale**

The `progress.*` keys were added around lines 620–700 in both files (from the Progress surface). The `profile.*` keys can be added as a contiguous block immediately after the last `progress.*` key (before the next unrelated key). Order within the block does not matter; parity does.

- [ ] **Step 2: Add the block to `locales/tr.json`**

Insert these lines (valid JSON — mind the trailing comma rules; each line ends with `,` except make sure the surrounding object stays valid):

```json
  "profile.membership": "Üyelik",
  "profile.plan_free": "Ücretsiz Plan",
  "profile.plan_free_desc": "Premium ile tüm özelliklerin kilidini aç.",
  "profile.upgrade_cta": "Premium'a Yükselt",
  "profile.plan_premium": "Premium Üye",
  "profile.plan_premium_thanks": "Desteğin için teşekkürler — tüm özellikler açık.",
  "profile.premium_badge": "PREMIUM",
  "profile.edit_profile": "Profili Düzenle",
  "profile.xp_to_next": "sonraki seviyeye",
  "profile.level": "Seviye",
  "profile.streak": "Seri",
  "profile.sec_community": "Topluluk",
  "profile.sec_you": "Sen",
  "profile.sec_settings": "Ayarlar",
  "profile.integrations": "Entegrasyonlar",
  "profile.change_photo": "Fotoğrafı değiştir",
```

- [ ] **Step 3: Add the parity block to `locales/en.json`**

```json
  "profile.membership": "Membership",
  "profile.plan_free": "Free Plan",
  "profile.plan_free_desc": "Unlock every feature with Premium.",
  "profile.upgrade_cta": "Upgrade to Premium",
  "profile.plan_premium": "Premium Member",
  "profile.plan_premium_thanks": "Thanks for your support — all features unlocked.",
  "profile.premium_badge": "PREMIUM",
  "profile.edit_profile": "Edit Profile",
  "profile.xp_to_next": "to next level",
  "profile.level": "Level",
  "profile.streak": "Streak",
  "profile.sec_community": "Community",
  "profile.sec_you": "You",
  "profile.sec_settings": "Settings",
  "profile.integrations": "Integrations",
  "profile.change_photo": "Change photo",
```

- [ ] **Step 4: Verify JSON validity + parity**

Run:
```bash
python -c "import json; tr=json.load(open('locales/tr.json',encoding='utf-8')); en=json.load(open('locales/en.json',encoding='utf-8')); assert set(tr)==set(en), set(tr)^set(en); assert all(k in tr for k in ['profile.membership','profile.plan_free','profile.plan_premium','profile.upgrade_cta','profile.edit_profile']); print('parity OK', len([k for k in tr if k.startswith('profile.')]),'profile keys')"
```
Expected: `parity OK 16 profile keys` (no assertion error).

- [ ] **Step 5: Run the i18n suite**

Run: `python -m pytest tests/test_i18n.py -q`
Expected: all pass (the new keys don't break parity; the existing EN edit-profile test still passes because the template isn't changed yet).

- [ ] **Step 6: Commit**

```bash
git add locales/tr.json locales/en.json
git commit -m "Add profile.* i18n keys for profile redesign (TR/EN parity)"
```

---

### Task 2: Create `static/profile.css` (canonical page stylesheet)

**Files:**
- Create: `static/profile.css`
- Test: token-cleanliness grep + `tests/test_design_system.py` (must stay green)

**Interfaces:**
- Produces: the CSS classes consumed by the Task 3 template: `.pf-wrap`, `.pf-hero`, `.pf-avatar`, `.pf-avatar-overlay`, `.pf-id`, `.pf-name`, `.pf-handle`, `.pf-rank`, `.pf-xp`, `.pf-xp-label`, `.pf-streak`, `.pf-card`, `.pf-membership`, `.pf-plan-head`, `.pf-plan-name`, `.pf-plan-desc`, `.pf-edit-btn`, `.pf-sheet-body`, `.pf-field`, `.pf-input`, `.pf-hint`, `.pf-cards`, `.pf-choice`, `.pf-choice.selected`, `.pf-section-title`, `.pf-integrations`, `.pf-wear`, `.pf-stack`, `.pf-stack-card`.

- [ ] **Step 1: Write `static/profile.css`**

Create the file with exactly this content (canonical tokens only):

```css
/* Profile page (Phase 5 · Surface 3) — canonical AxisAI tokens only. */

.pf-wrap { max-width: 600px; margin: 0 auto; display: flex; flex-direction: column; gap: var(--space-5); }

/* ── card base (mirrors components.css .card, local so the page is self-contained) ── */
.pf-card {
  background: var(--color-surface-2); border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-lg); padding: var(--space-6);
}

/* ── identity / XP hero ── */
.pf-hero { display: flex; flex-direction: column; align-items: center; gap: var(--space-3); text-align: center; }
.pf-avatar {
  position: relative; width: 96px; height: 96px; border-radius: var(--radius-full);
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  font-family: var(--font-display); font-size: var(--text-3xl); letter-spacing: .06em;
  color: var(--color-on-primary); background: var(--color-primary);
  border: var(--border-w-3) solid var(--color-primary-glow); overflow: hidden;
}
.pf-avatar img { width: 100%; height: 100%; object-fit: cover; }
.pf-avatar-overlay {
  position: absolute; inset: 0; border-radius: var(--radius-full);
  background: var(--overlay-6); display: flex; align-items: center; justify-content: center;
  opacity: 0; transition: opacity var(--duration-fast) var(--ease-standard);
}
.pf-avatar:hover .pf-avatar-overlay, .pf-avatar:focus-visible .pf-avatar-overlay { opacity: 1; }
.pf-avatar-overlay svg { width: var(--icon-lg); height: var(--icon-lg); stroke: var(--color-text-1); fill: none; stroke-width: 1.5; }
.pf-name { font-family: var(--font-display); font-size: var(--text-2xl); letter-spacing: .04em; color: var(--color-text-1); }
.pf-handle { font-size: var(--text-xs); color: var(--color-text-3); }
.pf-rank { font-size: var(--text-2xs); font-weight: var(--weight-semibold); color: var(--color-primary); letter-spacing: var(--tracking-wide); text-transform: uppercase; }
.pf-xp { width: 100%; max-width: 320px; margin-top: var(--space-2); }
.pf-xp .pbar-fill { background: var(--color-primary); }
.pf-xp-label { font-size: var(--text-2xs); color: var(--color-text-3); margin-top: var(--space-1); letter-spacing: var(--tracking-wide); }
.pf-streak {
  display: inline-flex; align-items: center; gap: var(--space-1);
  background: var(--overlay-4); border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-full); padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs); font-weight: var(--weight-semibold); color: var(--color-text-2);
}

/* ── membership card ── */
.pf-membership { position: relative; }
.pf-plan-head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin-bottom: var(--space-2); }
.pf-plan-name { font-family: var(--font-display); font-size: var(--text-xl); letter-spacing: .04em; color: var(--color-text-1); }
.pf-plan-desc { font-size: var(--text-sm); color: var(--color-text-2); margin: 0 0 var(--space-4); }
.pf-membership .btn-volt { width: 100%; }

/* ── edit-profile trigger ── */
.pf-edit-btn { width: 100%; }

/* ── edit sheet body ── */
.pf-sheet-body { display: flex; flex-direction: column; gap: var(--space-4); }
.pf-field { display: flex; flex-direction: column; gap: var(--space-1); }
.pf-field label { font-size: var(--text-2xs); font-weight: var(--weight-bold); letter-spacing: var(--tracking-label); text-transform: uppercase; color: var(--color-text-3); }
.pf-input {
  width: 100%; box-sizing: border-box; padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm); background: var(--color-surface-1);
  border: var(--border-w-1) solid var(--color-border-1); color: var(--color-text-1);
  font-family: var(--font-body); font-size: var(--text-sm); outline: none;
  transition: border-color var(--duration-fast) var(--ease-standard), box-shadow var(--duration-fast) var(--ease-standard);
}
.pf-input:focus-visible { border-color: var(--color-primary-glow); box-shadow: var(--focus-ring); }
.pf-hint { font-size: var(--text-xs); color: var(--color-text-3); }
.pf-cards { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); }
.pf-choice {
  background: var(--overlay-2); border: var(--border-w-2) solid var(--color-border-1);
  border-radius: var(--radius-md); padding: var(--space-4); text-align: center; cursor: pointer;
  transition: border-color var(--duration-fast) var(--ease-standard), background var(--duration-fast) var(--ease-standard);
}
.pf-choice:hover { border-color: var(--color-border-2); }
.pf-choice.selected { border-color: var(--color-primary); background: var(--color-primary-soft); }
.pf-choice-icon { font-size: var(--text-2xl); margin-bottom: var(--space-1); }
.pf-choice-label { font-size: var(--text-sm); font-weight: var(--weight-semibold); color: var(--color-text-1); }
.pf-choice.selected .pf-choice-label { color: var(--color-primary); }

/* ── section titles (nav rows / integrations / stack) ── */
.pf-section-title { font-family: var(--font-display); font-size: var(--text-lg); letter-spacing: .04em; color: var(--color-text-1); margin: var(--space-2) 0 var(--space-3); }

/* ── integrations (wearables) ── */
.pf-integrations { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); }
.pf-wear {
  background: var(--color-surface-2); border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-md); padding: var(--space-4); display: flex; flex-direction: column; gap: var(--space-3);
}
.pf-wear-top { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); }
.pf-wear-name { font-weight: var(--weight-bold); color: var(--color-text-1); }
.pf-wear-status { font-size: var(--text-2xs); font-weight: var(--weight-bold); letter-spacing: var(--tracking-wide); text-transform: uppercase; color: var(--color-text-3); }
.pf-wear-status.on { color: var(--color-success); }
.pf-wear-meta { font-size: var(--text-xs); color: var(--color-text-3); min-height: 18px; }
.pf-wear-actions { display: flex; gap: var(--space-2); flex-wrap: wrap; }

/* ── supplement stack ── */
.pf-stack { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); }
.pf-stack-card {
  background: var(--color-surface-2); border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-md); padding: var(--space-3) var(--space-4); display: flex; gap: var(--space-3); align-items: flex-start;
}
.pf-stack-icon { font-size: var(--text-2xl); flex-shrink: 0; }
.pf-stack-body { flex: 1; min-width: 0; }
.pf-stack-name { font-weight: var(--weight-semibold); font-size: var(--text-sm); color: var(--color-text-1); }
.pf-stack-brand { font-size: var(--text-2xs); color: var(--color-text-3); }
.pf-stack-ratings { display: flex; gap: var(--space-2); margin-top: var(--space-1); flex-wrap: wrap; }
.pf-stack-rating { font-size: var(--text-2xs); color: var(--color-text-2); }
.pf-stack-rating .mini-stars { color: var(--color-primary); letter-spacing: 1px; }
.pf-stack-review { font-size: var(--text-2xs); color: var(--color-text-3); font-style: italic; margin-top: var(--space-1); border-left: var(--border-w-2) solid var(--color-primary); padding-left: var(--space-2); }
.pf-stack-top { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); }
.pf-stack-empty { text-align: center; color: var(--color-text-3); font-size: var(--text-sm); padding: var(--space-6); }
.pf-link { display: inline-block; margin-top: var(--space-3); font-size: var(--text-sm); color: var(--color-primary); text-decoration: none; font-weight: var(--weight-semibold); }
.pf-link:hover { text-decoration: underline; }

@media (max-width: 640px) {
  .pf-integrations { grid-template-columns: 1fr; }
  .pf-stack { grid-template-columns: 1fr; }
  .pf-card { padding: var(--space-5); }
}

@media (prefers-reduced-motion: reduce) {
  .pf-avatar-overlay, .pf-input, .pf-choice { transition: none; }
}
```

- [ ] **Step 2: Verify no legacy tokens / raw colors**

Run:
```bash
grep -nE "\-\-volt|#[0-9a-fA-F]{3,6}|rgba\(" static/profile.css || echo "CLEAN"
```
Expected: `CLEAN` (no matches).

- [ ] **Step 3: Design-system regression**

Run: `python -m pytest tests/test_design_system.py -q`
Expected: all pass (adding a page CSS file doesn't touch the component inventory).

- [ ] **Step 4: Commit**

```bash
git add static/profile.css
git commit -m "Add canonical profile.css for profile redesign"
```

---

### Task 3: Backend `is_premium` + template rewrite + `static/profile.js`

**Files:**
- Modify: `app/blueprints/profile.py` (the `edit_profile` GET `render_template(...)` call, ~lines 96–106)
- Rewrite: `templates/edit_profile.html`
- Create: `static/profile.js`
- Create: `tests/test_profile_ui.py`

**Interfaces:**
- Consumes: `profile.*` keys (Task 1); `pf-*` classes (Task 2).
- Consumes (context, already available): `username`, `full_name`, `profile_picture`, `goal`, `target_weight`, `streak_count`, `supplements`, `icons`, `wearable_connections` (route), plus `user_xp`, `user_level`, `user_title`, `xp_in_level`, `xp_for_next` (global `inject_rank` context processor).
- Produces: rendered `/edit-profile` satisfying the Global-Constraints HTML contract.

- [ ] **Step 1: Write the render smoke test (RED)**

Create `tests/test_profile_ui.py`:

```python
"""Profile page render tests (Phase 5 · Surface 3): structural anchors of the
redesigned shell, the Membership card in both is_premium states, the edit sheet,
the test-pinned hub destinations, and the canonical-tokens-only guard."""

from app.extensions import db


def _html(client):
    r = client.get("/edit-profile")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_profile_structural_anchors(client, auth_user):
    html = _html(client)
    # hero + XP + membership + edit sheet
    assert 'class="pf-hero"' in html
    assert 'class="pbar-track"' in html
    assert 'class="pf-membership' in html
    assert 'id="edit-sheet"' in html
    assert 'role="dialog"' in html
    assert 'data-action="openEditSheet"' in html
    # sheet form still carries the i18n-test-pinned pieces
    assert '["kilo verme"]' in html
    # static assets + no legacy token leak
    assert "/static/profile.js" in html
    assert "/static/profile.css" in html
    assert "--volt" not in html


def test_profile_hub_destinations_preserved(client, auth_user):
    html = _html(client)
    for href in ("/friends", "/feed", "/leaderboard", "/quests",
                 "/supplements", "/premium", "/logout"):
        assert f'href="{href}" class="hub-link' in html, href
    assert 'data-action="setLang"' in html


def test_membership_free_shows_upgrade(client, auth_user):
    # fresh users are not premium
    html = _html(client)
    assert "profile.plan_free" not in html          # key resolved, not raw
    assert 'href="/premium"' in html
    assert 'data-ga-event="premium_nav_click"' in html


def test_membership_premium_shows_badge_no_cta(client, auth_user):
    auth_user.is_premium = True
    db.session.commit()
    html = _html(client)
    assert 'class="badge badge-success' in html      # premium badge present
    # the upgrade CTA button (btn-volt in the membership card) is gone
    assert 'class="btn-volt pf-upgrade"' not in html
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `python -m pytest tests/test_profile_ui.py -q`
Expected: FAIL (new anchors `pf-hero`, `edit-sheet`, `openEditSheet`, `/static/profile.js` absent; membership markup absent).

- [ ] **Step 3: Add `is_premium` to the GET render context**

In `app/blueprints/profile.py`, the `edit_profile` GET `render_template("edit_profile.html", ...)` call currently ends with `wearable_connections=wearable_connections,`. Add one kwarg:

```python
        return render_template("edit_profile.html",
            username=current_user.username,
            full_name=current_user.full_name or "",
            profile_picture=current_user.avatar_src or "",
            goal=current_user.goal or "",
            target_weight=current_user.target_weight,
            streak_count=current_user.streak_count or 0,
            supplements=supps,
            icons=CATEGORY_ICONS,
            wearable_connections=wearable_connections,
            is_premium=bool(current_user.is_premium),
        )
```

Do not touch the POST handler.

- [ ] **Step 4: Create `static/profile.js`**

Create the file. All functions are top-level globals (resolved by `actions.js` / inline listeners). Avatar/save/wearable logic is the current template's logic, unchanged; `openEditSheet`/`closeEditSheet` mirror the Progress check-in sheet a11y.

```javascript
/* Profile page (Phase 5 · Surface 3). Top-level globals for data-action + listeners. */
var __t = (window.t) || function (k) { return k; };
var selectedGoal = (document.body.getAttribute('data-goal') || '');
var pendingAvatar = null;
var _editOpener = null;

function toast(msg, type) {
  type = type || 'info';
  var wrap = document.getElementById('toast-wrap');
  if (!wrap) return;
  var el = document.createElement('div');
  el.className = 'toast toast-' + type;
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(function () { el.style.opacity = '0'; setTimeout(function () { el.remove(); }, 300); }, 3000);
}

function selectGoal(goal, el) {
  document.querySelectorAll('.pf-goal .pf-choice').forEach(function (c) { c.classList.remove('selected'); });
  if (el) el.classList.add('selected');
  selectedGoal = goal;
}

function updateAvatarLetter() {
  var display = document.getElementById('avatar-display');
  if (!display || display.querySelector('img')) return;
  var uname = document.getElementById('username');
  var letter = ((uname && uname.value) || 'U')[0].toUpperCase();
  var span = display.querySelector('span');
  if (span) span.textContent = letter;
}

function openEditSheet(btn) {
  _editOpener = btn || document.activeElement;
  var sheet = document.getElementById('edit-sheet');
  sheet.classList.add('open');
  var dialog = sheet.querySelector('.sheet');
  var first = sheet.querySelector('input, button, [tabindex]');
  if (first) { try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); } }
  else if (dialog) { dialog.focus(); }
}

function closeEditSheet() {
  var sheet = document.getElementById('edit-sheet');
  sheet.classList.remove('open');
  if (_editOpener) { try { _editOpener.focus({ preventScroll: true }); } catch (e) { _editOpener.focus(); } }
}

async function saveProfile() {
  var btn = document.getElementById('save-btn');
  btn.disabled = true;
  btn.textContent = __t('common.saving');
  var tw = document.getElementById('target_weight');
  var payload = {
    full_name: document.getElementById('full_name').value.trim(),
    username: document.getElementById('username').value.trim(),
    goal: selectedGoal,
    target_weight: tw ? tw.value.trim() : ''
  };
  if (pendingAvatar !== null) { payload.profile_picture = pendingAvatar; }
  try {
    var res = await fetch('/edit-profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    var data = await res.json();
    if (!res.ok) { toast(data.error || __t('common.error'), 'error'); }
    else { toast(data.message, 'success'); setTimeout(function () { location.reload(); }, 800); }
  } catch (e) {
    toast(__t('common.conn_error'), 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = __t('common.save');
  }
}

document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape') return;
  var sheet = document.getElementById('edit-sheet');
  if (sheet && sheet.classList.contains('open')) { closeEditSheet(); }
});

document.addEventListener('DOMContentLoaded', function () {
  var uname = document.getElementById('username');
  if (uname) uname.addEventListener('input', updateAvatarLetter);

  var fileInput = document.getElementById('avatar-file-input');
  if (fileInput) fileInput.addEventListener('change', function (e) {
    var file = e.target.files[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { toast(__t('editprofile.avatar_max'), 'error'); return; }
    var reader = new FileReader();
    reader.onload = function (ev) {
      pendingAvatar = ev.target.result;
      var display = document.getElementById('avatar-display');
      var overlay = display.querySelector('.pf-avatar-overlay');
      var img = display.querySelector('img') || document.createElement('img');
      img.src = pendingAvatar; img.alt = 'Profil';
      var letter = display.querySelector('span');
      if (letter) letter.remove();
      if (!display.querySelector('img')) display.insertBefore(img, overlay);
      toast(__t('editprofile.avatar_updated'), 'success');
    };
    reader.readAsDataURL(file);
  });

  document.querySelectorAll('.wearable-connect').forEach(function (btn) {
    btn.addEventListener('click', function () { window.location.href = '/api/auth/wearable/' + btn.dataset.provider; });
  });
  document.querySelectorAll('.wearable-sync').forEach(function (btn) {
    btn.addEventListener('click', async function () {
      btn.disabled = true;
      try {
        var res = await fetch('/api/wearables/' + btn.dataset.provider + '/sync', { method: 'POST' });
        var data = await res.json();
        if (!res.ok) { toast(data.error || __t('wearables.sync_failed'), 'error'); }
        else { toast(__t('wearables.sync_done'), 'success'); setTimeout(function () { location.reload(); }, 700); }
      } catch (e) { toast(__t('common.conn_error'), 'error'); }
      finally { btn.disabled = false; }
    });
  });
});
```

- [ ] **Step 5: Rewrite `templates/edit_profile.html`**

Replace the whole file with the structure below. Keep `{% include "_head.html" %}`, `nav_active`, `_nav.html`, `_actionbar.html`. Link `profile.css` in `<head>` and `profile.js` before `</body>` (after `actions.js`). NO inline `<style>`. The `<body>` carries `data-goal="{{ goal }}"` so `profile.js` seeds `selectedGoal`.

```html
<!DOCTYPE html>
<html lang="{{ locale }}" data-theme="dark">
<head>
    {% include "_head.html" %}
    <title>{{ t('editprofile.page_title') }}</title>
    <link rel="stylesheet" href="/static/theme.css">
    <link rel="stylesheet" href="/static/nav.css">
    <link rel="stylesheet" href="/static/profile.css">
</head>
{% set nav_active = 'profile' %}
<body class="page-body" data-goal="{{ goal }}">

<div class="toast-wrap" id="toast-wrap"></div>

{% include "_nav.html" %}

<main class="main-content">
  <div class="page-hdr">
    <h1>{{ t('editprofile.h1_a') }}<br><span>{{ t('editprofile.h1_b') }}</span></h1>
    <p>{{ t('editprofile.page_sub') }}</p>
  </div>

  <div class="pf-wrap">

    <!-- ── IDENTITY / XP HERO ── -->
    <div class="pf-card pf-hero">
      <div class="pf-avatar" id="avatar-display" tabindex="0" role="button"
           aria-label="{{ t('profile.change_photo') }}"
           data-action="fxClickTarget" data-target="avatar-file-input">
        {% if profile_picture %}<img src="{{ profile_picture }}" alt="Profil" id="avatar-img">
        {% else %}<span id="avatar-letter">{{ username[0]|upper if username else 'U' }}</span>{% endif %}
        <div class="pf-avatar-overlay">
          <svg viewBox="0 0 24 24"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
        </div>
      </div>
      <input type="file" id="avatar-file-input" accept="image/*" hidden>
      <div class="pf-name">{{ full_name if full_name else username }}</div>
      <div class="pf-handle">@{{ username }}</div>
      <div class="pf-rank">{{ user_title }} · {{ t('profile.level') }} {{ user_level }}</div>
      <div class="pf-xp">
        <div class="pbar-track"><div class="pbar-fill" style="width: {{ ((xp_in_level / xp_for_next) * 100)|round(0, 'floor')|int if xp_for_next else 0 }}%"></div></div>
        <div class="pf-xp-label">{{ xp_in_level }} / {{ xp_for_next }} XP · {{ t('profile.xp_to_next') }}</div>
      </div>
      <div class="pf-streak">🔥 {{ streak_count }} · {{ t('profile.streak') }}</div>
    </div>

    <!-- ── MEMBERSHIP CARD ── -->
    <div class="pf-card pf-membership">
      <div class="pf-plan-head">
        <div class="pf-plan-name">{{ t('profile.plan_premium') if is_premium else t('profile.plan_free') }}</div>
        {% if is_premium %}<span class="badge badge-success">{{ t('profile.premium_badge') }}</span>{% endif %}
      </div>
      {% if is_premium %}
        <p class="pf-plan-desc">{{ t('profile.plan_premium_thanks') }}</p>
      {% else %}
        <p class="pf-plan-desc">{{ t('profile.plan_free_desc') }}</p>
        <a href="/premium" class="btn-volt pf-upgrade" data-ga-event="premium_nav_click">{{ t('profile.upgrade_cta') }}</a>
      {% endif %}
    </div>

    <!-- ── EDIT PROFILE TRIGGER ── -->
    <button class="btn-ghost pf-edit-btn" data-action="openEditSheet">{{ t('profile.edit_profile') }}</button>

    <!-- ── NAV ROWS (preserved hub — test-pinned hrefs) ── -->
    <nav class="hub" aria-label="{{ t('nav.menu_nav') }}">
      <div class="hub-section-label">{{ t('profile.sec_community') }}</div>
      <div class="hub-card">
        <a href="/friends" class="hub-link"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>{{ t('nav.friends') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
        <a href="/feed" class="hub-link"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>{{ t('nav.feed') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
        <a href="/leaderboard" class="hub-link"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3v18"/><path d="M19 3v18"/><path d="M5 7h14"/><path d="M8 11h8"/><path d="M10 15h4"/></svg>{{ t('nav.club') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
        <a href="/quests" class="hub-link"><svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>{{ t('nav.quests') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
      </div>
      <div class="hub-section-label">{{ t('profile.sec_you') }}</div>
      <div class="hub-card">
        <a href="/pump-check-gallery" class="hub-link"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>{{ t('gallery.profile_link_title') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
        <a href="/supplements" class="hub-link"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10.5 1.5H8.25A2.25 2.25 0 0 0 6 3.75v16.5a2.25 2.25 0 0 0 2.25 2.25h7.5A2.25 2.25 0 0 0 18 20.25V3.75a2.25 2.25 0 0 0-2.25-2.25H13.5m-3 0V3h3V1.5m-3 0h3m-6 18.75h9"/></svg>{{ t('nav.supplements') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
        <a href="/premium" class="hub-link hub-link-premium" data-ga-event="premium_nav_click"><svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>{{ t('nav.premium') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
        <!-- SUPPORT-SEAM: a future Support row (Help Center / FAQ / Contact) slots here without restructuring. -->
      </div>
      <div class="hub-section-label">{{ t('profile.sec_settings') }}</div>
      <div class="hub-card">
        <div class="hub-row">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
          {{ t('settings.language') }}
          <div class="hub-lang" role="group" aria-label="{{ t('lang.aria') }}">
            <button type="button" class="hub-lang-opt{% if locale == 'tr' %} on{% endif %}" data-action="setLang" data-args='["tr"]'>TR</button>
            <button type="button" class="hub-lang-opt{% if locale == 'en' %} on{% endif %}" data-action="setLang" data-args='["en"]'>EN</button>
          </div>
        </div>
        <a href="/logout" class="hub-link hub-link-danger"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>{{ t('nav.logout') }}</a>
      </div>
    </nav>

    <!-- ── INTEGRATIONS (wearables) ── -->
    <div>
      <div class="pf-section-title">{{ t('profile.integrations') }}</div>
      <div class="pf-integrations">
        {% for provider, label in [('whoop', 'WHOOP'), ('google_health', 'Google Health')] %}
        {% set conn = wearable_connections.get(provider) if wearable_connections else None %}
        <div class="pf-wear" data-provider-card="{{ provider }}">
          <div class="pf-wear-top">
            <div class="pf-wear-name">{{ label }}</div>
            <div class="pf-wear-status{% if conn %} on{% endif %}">{{ t('wearables.connected') if conn else t('wearables.not_connected') }}</div>
          </div>
          <div class="pf-wear-meta">
            {% if conn and conn.last_sync_at %}{{ t('wearables.last_sync') }} {{ conn.last_sync_at.strftime('%d.%m.%Y %H:%M') }}
            {% elif conn %}{{ t('wearables.sync_waiting') }}
            {% else %}{{ t('wearables.connect_hint') }}{% endif %}
          </div>
          <div class="pf-wear-actions">
            <button type="button" class="btn-volt wearable-connect" data-provider="{{ provider }}">{{ t('wearables.connect') }}</button>
            <button type="button" class="btn-ghost wearable-sync" data-provider="{{ provider }}"{% if not conn %} disabled{% endif %}>{{ t('wearables.sync_today') }}</button>
          </div>
        </div>
        {% endfor %}
      </div>
    </div>

    <!-- ── MY STACK (supplements, read-only) ── -->
    <div>
      <div class="pf-section-title">{{ t('editprofile.stack_title') }}</div>
      {% if supplements %}
      <div class="pf-stack">
        {% for s in supplements %}
        <div class="pf-stack-card">
          <div class="pf-stack-icon">{{ icons.get(s.category, '📦') }}</div>
          <div class="pf-stack-body">
            <div class="pf-stack-top">
              <div class="pf-stack-name">{{ s.product_name }}</div>
              <span class="badge {% if s.status == 'Active' %}badge-success{% else %}badge-warning{% endif %}">{{ s.status }}</span>
            </div>
            <div class="pf-stack-brand">{{ s.brand }} · {{ s.category }}</div>
            <div class="pf-stack-ratings">
              {% if s.rating_effect %}<span class="pf-stack-rating">{{ t('manage_stack.effect') }}: <span class="mini-stars">{{ '★' * s.rating_effect }}{{ '☆' * (5 - s.rating_effect) }}</span></span>{% endif %}
              {% if s.rating_taste %}<span class="pf-stack-rating">{{ t('manage_stack.taste') }}: <span class="mini-stars">{{ '★' * s.rating_taste }}{{ '☆' * (5 - s.rating_taste) }}</span></span>{% endif %}
              {% if s.rating_digestion %}<span class="pf-stack-rating">{{ t('manage_stack.digestion') }}: <span class="mini-stars">{{ '★' * s.rating_digestion }}{{ '☆' * (5 - s.rating_digestion) }}</span></span>{% endif %}
              {% if s.rating_price %}<span class="pf-stack-rating">{{ t('manage_stack.price') }}: <span class="mini-stars">{{ '★' * s.rating_price }}{{ '☆' * (5 - s.rating_price) }}</span></span>{% endif %}
            </div>
            {% if s.review_text %}<div class="pf-stack-review">"{{ s.review_text }}"</div>{% endif %}
          </div>
        </div>
        {% endfor %}
      </div>
      <a href="/supplements" class="pf-link">{{ t('editprofile.edit_stack') }}</a>
      {% else %}
      <div class="pf-stack-empty">{{ t('editprofile.no_supps') }}<br><a href="/supplements" class="pf-link">{{ t('editprofile.add_first_supp') }}</a></div>
      {% endif %}
    </div>

  </div>
</main>

<!-- ── EDIT PROFILE SHEET ── -->
<div class="sheet-backdrop" id="edit-sheet" data-action-self="closeEditSheet">
  <div class="sheet" role="dialog" aria-modal="true" aria-labelledby="edit-sheet-title" tabindex="-1">
    <div class="sheet-handle"></div>
    <div class="sheet-title" id="edit-sheet-title">{{ t('editprofile.edit_info') }}</div>
    <div class="pf-sheet-body">
      <div class="pf-field">
        <label for="full_name">{{ t('editprofile.full_name') }}</label>
        <input class="pf-input" type="text" id="full_name" placeholder="{{ t('editprofile.full_name_ph') }}" value="{{ full_name }}" maxlength="150">
      </div>
      <div class="pf-field">
        <label for="username">{{ t('editprofile.username') }}</label>
        <input class="pf-input" type="text" id="username" placeholder="{{ t('editprofile.username_ph') }}" value="{{ username }}" maxlength="80">
        <div class="pf-hint">{{ t('editprofile.username_hint') }}</div>
      </div>
      <div class="pf-field pf-goal">
        <label>{{ t('editprofile.fitness_goal') }}</label>
        <div class="pf-cards">
          <div class="pf-choice{% if goal == 'kilo verme' %} selected{% endif %}" data-action="selectGoal" data-args='["kilo verme"]'>
            <div class="pf-choice-icon">🏃</div><div class="pf-choice-label">{{ t('editprofile.goal_loss') }}</div>
          </div>
          <div class="pf-choice{% if goal == 'kas kazanma' %} selected{% endif %}" data-action="selectGoal" data-args='["kas kazanma"]'>
            <div class="pf-choice-icon">💪</div><div class="pf-choice-label">{{ t('editprofile.goal_gain') }}</div>
          </div>
        </div>
      </div>
      <div class="pf-field">
        <label for="target_weight">{{ t('editprofile.target_weight') }}</label>
        <input class="pf-input" type="number" id="target_weight" placeholder="{{ t('editprofile.target_weight_ph') }}" value="{{ target_weight or '' }}" step="0.1" min="30" max="300">
        <div class="pf-hint">{{ t('editprofile.target_weight_hint') }}</div>
      </div>
      <div class="pf-field">
        <label>{{ t('settings.language') }}</label>
        <div class="pf-cards">
          <div class="pf-choice{% if locale == 'tr' %} selected{% endif %}" data-action="setLang" data-args='["tr"]'>
            <div class="pf-choice-icon">🇹🇷</div><div class="pf-choice-label">Türkçe</div>
          </div>
          <div class="pf-choice{% if locale == 'en' %} selected{% endif %}" data-action="setLang" data-args='["en"]'>
            <div class="pf-choice-icon">🇬🇧</div><div class="pf-choice-label">English</div>
          </div>
        </div>
        <div class="pf-hint">{{ t('settings.language_hint') }}</div>
      </div>
      <button class="btn-volt w-full" id="save-btn" data-action="saveProfile">{{ t('common.save') }}</button>
    </div>
  </div>
</div>

{% include "_actionbar.html" %}

<script src="/static/actions.js"></script>
<script src="/static/profile.js"></script>
</body>
</html>
```

- [ ] **Step 6: Run the render tests (GREEN)**

Run: `python -m pytest tests/test_profile_ui.py -q`
Expected: all 4 pass.

- [ ] **Step 7: Syntax-check the JS**

Run: `node --check static/profile.js`
Expected: no output (valid).

- [ ] **Step 8: Run the pinned regressions**

Run: `python -m pytest tests/test_app_shell.py tests/test_i18n.py tests/test_profile_routes.py -q`
Expected: all pass (hub hrefs + setLang preserved; EN render still has `EDIT YOUR INFO`/`Target Weight`/`SAVE`/`Türkçe`/`["kilo verme"]`; POST behavior unchanged).

- [ ] **Step 9: Commit**

```bash
git add app/blueprints/profile.py templates/edit_profile.html static/profile.js tests/test_profile_ui.py
git commit -m "Rewrite profile on canonical tokens: hero, membership card, edit sheet, nav rows"
```

---

### Task 4: A11y polish, full-suite verification, and handoff

**Files:**
- Modify (only if a gap is found): `templates/edit_profile.html`, `static/profile.css`, `static/profile.js`
- Rewrite: `docs/handoff.md`
- Archive: current `docs/handoff.md` → `docs/archive/handoff-2026-07-08-phase5-progress.md`

**Interfaces:**
- Consumes: the completed page from Task 3.

- [ ] **Step 1: A11y self-check (fix inline if any fails)**

Confirm in `templates/edit_profile.html` / `static/profile.css`:
- The avatar button is keyboard-reachable (`tabindex="0"` + `role="button"`) and has an `aria-label`.
- The edit sheet has `role="dialog"` + `aria-modal="true"` + `aria-labelledby="edit-sheet-title"`, opens focus to the first field, returns focus to the opener on close, and Esc closes it (all in `profile.js`).
- Every actionable control is ≥44px tall (buttons/inputs use `--space-3`+ padding); `.pf-input:focus-visible` shows `--focus-ring`.
- `prefers-reduced-motion` block present in `profile.css`.

If any is missing, add it and note it in the commit.

- [ ] **Step 2: Full suite**

Run: `python -m pytest -q`
Expected: all pass (target: prior baseline + the new `test_profile_ui.py` tests; 0 failures).

- [ ] **Step 3: Token + parity final check**

Run:
```bash
grep -nE "\-\-volt|#[0-9a-fA-F]{3,6}|rgba\(" static/profile.css || echo "CSS CLEAN"
python -c "import json; tr=json.load(open('locales/tr.json',encoding='utf-8')); en=json.load(open('locales/en.json',encoding='utf-8')); assert set(tr)==set(en); print('PARITY OK')"
```
Expected: `CSS CLEAN` and `PARITY OK`.

- [ ] **Step 4: Archive the prior handoff and write the new one**

```bash
git mv docs/handoff.md docs/archive/handoff-2026-07-08-phase5-progress.md
```
Then create a new `docs/handoff.md` documenting: the Profile surface scope, the five reference decisions (frontend-first + additive `is_premium`; preserved `.hub` nav rows; edit-behind-a-sheet; membership presentation-only; integrations+stack kept), files created/modified, the test-pinned contract that was preserved, and the recommended next step (merge → Profile is the third of four Phase 5 surfaces; Final QA remains). Note the same manual-QA caveat as prior surfaces (charts/interaction not browser-verified).

- [ ] **Step 5: Commit**

```bash
git add docs/handoff.md docs/archive/handoff-2026-07-08-phase5-progress.md templates/edit_profile.html static/profile.css static/profile.js
git commit -m "Profile a11y polish + Phase 5 profile handoff"
```

---

## Notes for the executor

- **No `git add -A`:** the repo root has intentionally-untracked scratch (`.superpowers/sdd/*`, `AGENTS.md`). Stage only the files named in each task's commit step.
- **Branch:** all work lands on `feat/phase5-profile` (already created off `main`).
- The membership CTA in the card uses `class="btn-volt pf-upgrade"`; the test asserts this exact class combo is ABSENT for premium users — do not reuse `pf-upgrade` elsewhere.
- The `/premium` hub-link row is intentionally kept in addition to the membership card (test-pinned `href="/premium" class="hub-link`).
