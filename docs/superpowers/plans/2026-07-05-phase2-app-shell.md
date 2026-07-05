# Phase 2 — App Shell (Navigation & Layout) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the drawer-based hybrid navigation with a premium mobile-first app shell: shared header (brand + desktop tabs + avatar), 5-tab bottom navigation (Home, Nutrition, Workout, Progress, Profile), and a Profile hub that absorbs all secondary destinations.

**Architecture:** All 13 authenticated app pages converge on two shared partials (`_nav.html` = header, `_actionbar.html` = bottom tab bar). The sidebar drawer and its JS are deleted; its destinations (Friends, Feed, Club, Quests, Supplements, Premium, Language, Logout) move into a hub section on the Profile page. `nav.css` is rewritten as the shell stylesheet (canonical tokens only). Desktop ≥1024px shows header tabs and hides the bottom bar.

**Tech Stack:** Flask + Jinja2 templates, plain CSS (design tokens from `static/tokens.css`), pytest with `client`/`auth_user` fixtures.

## Global Constraints

- Spec: `C:\Users\yusuf\OneDrive\Masaüstü\phase-2.txt` — "Do not redesign individual page content yet. Only build the application shell."
- Preserve all backend logic; no route/model changes. `inject_rank` context processor (app/hooks.py:255) stays; templates simply stop/start consuming its vars.
- New CSS uses **canonical token names only** (`--color-*`, `--space-*`, `--radius-*`, `--duration-*`…) — no legacy aliases (docs/design-system.md policy).
- CSP: any new inline `<style>`/`<script>` in templates needs `nonce="{{ csp_nonce }}"`.
- Türkçe UI, English code. Short commit messages.
- i18n: visible text via `t(...)`; new keys added to BOTH `locales/tr.json` and `locales/en.json`.
- Tab routes: Home `/`, Nutrition `/nutrition`, Workout `/training`, Progress `/progress-page`, Profile `/edit-profile`.
- `nav_active` contract (set before includes): `home | nutrition | training | progress | profile`.
- Secondary pages (quests, friends, chat, leaderboard, manage_stack, feed, premium, edit_profile, pump_check_gallery) set `nav_active = 'profile'`.
- Do not touch: landing.html, login/register/setup/verify (auth pages), 404/500.

---

### Task 1: Consolidate — replace inline nav copies with shared partials (zero redesign)

**Files:**
- Modify: `templates/index.html` (nav_active `home`; inline header+drawer ≈ lines 13–94, inline action-bar ≈ 250–268)
- Modify: `templates/nutrition.html` (`nutrition`; ≈ 294–349, ≈ 679–698)
- Modify: `templates/training.html` (`training`; ≈ 331–386, ≈ 630–648)
- Modify: `templates/progress.html` (`progress`; ≈ 106–134, ≈ 266–272)
- Modify: `templates/quests.html` (`quests`→ keeps old drawer semantics this task: use `quests`; ≈ 174–195, ≈ 241–259)
- Modify: `templates/friends.html` (`friends`; ≈ 134–155, ≈ 205–223)
- Modify: `templates/leaderboard.html` (`club`; ≈ 159–180, ≈ 209–227)
- Modify: `templates/manage_stack.html` (`supplements`; ≈ 104–125, ≈ 251–269)
- Test: `tests/test_app_shell.py` (new, first test only)

**Interfaces:**
- Consumes: existing `templates/_nav.html` (header + drawer) and `templates/_actionbar.html` (4-tab bar) exactly as they are today.
- Produces: all 13 app templates contain `{% include "_nav.html" %}` and `{% include "_actionbar.html" %}` — single point of change for Task 2.

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_shell.py`:

```python
"""Phase 2 app shell guards: tüm uygulama sayfaları ortak kabuk parçalarını kullanır."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

APP_TEMPLATES = [
    "index.html", "nutrition.html", "training.html", "progress.html",
    "quests.html", "friends.html", "leaderboard.html", "manage_stack.html",
    "chat.html", "edit_profile.html", "feed.html", "premium.html",
    "pump_check_gallery.html",
]


def test_app_templates_use_shared_shell_partials():
    for name in APP_TEMPLATES:
        html = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert '{% include "_nav.html" %}' in html, name
        assert '{% include "_actionbar.html" %}' in html, name
        # inline kopya kalmadı
        assert html.count('class="global-header"') == 0, name
        assert html.count('class="action-bar"') == 0, name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app_shell.py -q`
Expected: FAIL on index.html (inline copies present).

- [ ] **Step 3: Replace inline blocks in the 8 pages**

For each page in the Files list: delete the inline block from the `<!-- GLOBAL HEADER -->` comment (or bare `<header class="global-header">`) through `</aside>` inclusive, and replace with:

```jinja
{% set nav_active = '<value from Files list>' %}
{% include "_nav.html" %}
```

Delete the inline `<nav class="action-bar">…</nav>` block (including its `<!-- 4+1 ACTION BAR -->` comment if present) and replace with:

```jinja
{% include "_actionbar.html" %}
```

Note: `_nav.html` consumes `username, profile_picture, user_title, user_level, xp_in_level, xp_for_next, locale` — all provided globally by `inject_rank` and i18n context processors; no route changes needed. Training.html: the LOADING OVERLAY and PUMP CHECK MODAL between `</aside>` and `<main>` stay untouched — only delete up to `</aside>`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_app_shell.py tests/test_pump_check_sharing.py tests/test_i18n.py -q`
Expected: test_app_shell PASS. `test_standalone_nav_templates_use_feed_bottom_tab_and_keep_club_in_drawer` will now FAIL (it greps for inline `href="/feed" class="ab-tab` in the 8 templates — markup moved into partials). Update it in this task: replace its body with template reads of `_actionbar.html`/`_nav.html` asserting `href="/feed" class="ab-tab` / `href="/leaderboard" class="drawer-link` there, keeping intent (feed = bottom tab, club = drawer) until Task 2 rewrites it.

- [ ] **Step 5: Commit**

```bash
git add templates tests/test_app_shell.py tests/test_pump_check_sharing.py
git commit -m "Consolidate nav markup into shared partials"
```

---

### Task 2: Redesign the shell — header + 5-tab bottom nav, drawer removed

**Files:**
- Rewrite: `templates/_nav.html` (header only: brand link + desktop `.header-nav` + avatar)
- Rewrite: `templates/_actionbar.html` (5 tabs: home/nutrition/training/progress/profile with `aria-current`)
- Rewrite: `static/nav.css` (shell v4: header, header-nav, action-bar, page shell paddings, page-enter animation, hub styles for Task 3; canonical tokens; all `.drawer-*` rules deleted)
- Delete: `static/nav.js` + its `<script>` tag in all 13 app templates
- Modify: `templates/_head.html` line 6 — viewport gets `viewport-fit=cover`
- Modify: `static/theme.css` `.main-content` (lines 24–32) — token paddings, bottom clearance moves to `.page-body`
- Modify: nav_active values — quests/friends/leaderboard/manage_stack/chat/feed/premium → `profile` (edit_profile, pump_check_gallery already `profile`)
- Modify: `templates/edit_profile.html` line ≈191 — drop obsolete `.main-content` mobile padding override (keep `.wearable-grid` rule)
- Test: `tests/test_app_shell.py` (extend), `tests/test_pump_check_sharing.py` (rewrite 2 nav tests)

**Interfaces:**
- Consumes: `nav_active` from Task 1; tokens from `static/tokens.css`.
- Produces: `.global-header`, `.header-brand`, `.header-nav`, `.hn-link(.active)`, `.header-avatar`, `.action-bar`, `.ab-tab(.active)`, `.hub`, `.hub-card`, `.hub-link`, `.hub-row`, `.hub-lang(-opt)`, `.hub-section-label` — Task 3 and future phases rely on these class names.

- [ ] **Step 1: Write failing tests** (extend `tests/test_app_shell.py`)

```python
def test_drawer_and_nav_js_are_gone():
    for name in APP_TEMPLATES + ["_nav.html", "_actionbar.html"]:
        html = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert "fx-drawer" not in html, name
        assert "drawer-trigger" not in html, name
        assert "nav.js" not in html, name
    assert not (ROOT / "static" / "nav.js").exists()
    css = (ROOT / "static" / "nav.css").read_text(encoding="utf-8")
    assert ".drawer" not in css


def test_bottom_nav_has_five_tabs_and_marks_active(client, auth_user):
    html = client.get("/").get_data(as_text=True)
    for href in ("/nutrition", "/training", "/progress-page", "/edit-profile"):
        assert f'href="{href}" class="ab-tab' in html
    assert 'href="/" class="ab-tab active"' in html
    assert 'aria-current="page"' in html


def test_secondary_pages_activate_profile_tab(client, auth_user):
    html = client.get("/friends").get_data(as_text=True)
    assert 'href="/edit-profile" class="ab-tab active"' in html


def test_viewport_fit_cover_for_safe_areas():
    head = (ROOT / "templates" / "_head.html").read_text(encoding="utf-8")
    assert "viewport-fit=cover" in head
```

Run: `python -m pytest tests/test_app_shell.py -q` — expected FAIL (drawer still present).

- [ ] **Step 2: Rewrite `templates/_nav.html`**

```jinja
{# Uygulama kabuğu üst başlığı (v4): marka + masaüstü sekmeleri (≥1024px) + avatar.
   Sayfa, include'dan ÖNCE {% set nav_active = '...' %} ile aktif sekmeyi işaretler:
   home | nutrition | training | progress | profile. Mobil birincil gezinme
   _actionbar.html'deki alt sekme çubuğundadır; stiller static/nav.css'tedir. #}
<header class="global-header">
    <a href="/" class="header-brand"><img class="brand-mark" src="/static/icon-holo.png" alt="">AxisAI</a>
    <nav class="header-nav" aria-label="{{ t('nav.primary') }}">
        <a href="/" class="hn-link{% if nav_active == 'home' %} active" aria-current="page{% endif %}">{{ t('nav.home') }}</a>
        <a href="/nutrition" class="hn-link{% if nav_active == 'nutrition' %} active" aria-current="page{% endif %}">{{ t('nav.nutrition') }}</a>
        <a href="/training" class="hn-link{% if nav_active == 'training' %} active" aria-current="page{% endif %}">{{ t('nav.training') }}</a>
        <a href="/progress-page" class="hn-link{% if nav_active == 'progress' %} active" aria-current="page{% endif %}">{{ t('nav.progress') }}</a>
        <a href="/edit-profile" class="hn-link{% if nav_active == 'profile' %} active" aria-current="page{% endif %}">{{ t('nav.profile') }}</a>
    </nav>
    <div class="header-spacer"></div>
    <a href="/edit-profile" class="header-avatar" title="{{ t('nav.profile') }}" aria-label="{{ t('nav.my_profile') }}">
        {%- if profile_picture %}<img src="{{ profile_picture }}" alt="">{% else %}{{ username[0]|upper if username else 'U' }}{% endif -%}
    </a>
</header>
```

⚠️ The `class="…{% if %} active" aria-current="page{% endif %}"` trick produces `class="hn-link active" aria-current="page"` when active — tests assert this exact serialization for `.ab-tab`; use the identical pattern in `_actionbar.html`.

- [ ] **Step 3: Rewrite `templates/_actionbar.html`**

```jinja
{# Alt sekme çubuğu (mobil birincil gezinme, <1024px'te görünür). Sayfa,
   include'dan ÖNCE {% set nav_active = '...' %} ile aktif sekmeyi işaretler:
   home | nutrition | training | progress | profile. #}
<nav class="action-bar" aria-label="{{ t('nav.primary') }}">
    <a href="/" class="ab-tab{% if nav_active == 'home' %} active" aria-current="page{% endif %}">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
        <span>{{ t('nav.home') }}</span>
    </a>
    <a href="/nutrition" class="ab-tab{% if nav_active == 'nutrition' %} active" aria-current="page{% endif %}">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"/><line x1="6" y1="1" x2="6" y2="4"/><line x1="10" y1="1" x2="10" y2="4"/><line x1="14" y1="1" x2="14" y2="4"/></svg>
        <span>{{ t('nav.nutrition') }}</span>
    </a>
    <a href="/training" class="ab-tab{% if nav_active == 'training' %} active" aria-current="page{% endif %}">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 6.5h11"/><path d="M6.5 17.5h11"/><path d="M3 8.5v7"/><path d="M21 8.5v7"/><rect x="1" y="7" width="4" height="10" rx="1"/><rect x="19" y="7" width="4" height="10" rx="1"/></svg>
        <span>{{ t('nav.training') }}</span>
    </a>
    <a href="/progress-page" class="ab-tab{% if nav_active == 'progress' %} active" aria-current="page{% endif %}">
        <svg viewBox="0 0 24 24" aria-hidden="true"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>
        <span>{{ t('nav.progress') }}</span>
    </a>
    <a href="/edit-profile" class="ab-tab{% if nav_active == 'profile' %} active" aria-current="page{% endif %}">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        <span>{{ t('nav.profile') }}</span>
    </a>
</nav>
```

- [ ] **Step 4: Rewrite `static/nav.css`** (complete file)

```css
/* ═══════════════════════════════════════════════════════
   AxisAI — APP SHELL v4.0 (Phase 2)
   Global Header · Desktop Header Tabs · Bottom Tab Bar · Profile Hub
   Yalnızca kanonik token'lar (docs/design-system.md).
   ═══════════════════════════════════════════════════════ */

/* ── 1. GLOBAL HEADER ────────────────────────────────── */
.global-header {
  position: fixed; top: 0; left: 0; right: 0;
  height: var(--header-h);
  display: flex; align-items: center; gap: var(--space-3);
  padding: 0 max(var(--space-4), env(safe-area-inset-right))
           0 max(var(--space-4), env(safe-area-inset-left));
  background: rgba(18, 18, 18, 0.92); /* not: yarı saydam yüzey token'ı yok */
  -webkit-backdrop-filter: blur(24px); backdrop-filter: blur(24px);
  border-bottom: var(--border-w-1) solid var(--color-border-1);
  z-index: var(--z-header);
}

.header-brand {
  display: inline-flex; align-items: center; gap: var(--space-2);
  min-height: 44px;
  font-family: var(--font-display);
  font-size: 22px; letter-spacing: 4px;
  color: var(--color-primary); text-decoration: none;
  line-height: var(--leading-none);
  -webkit-tap-highlight-color: transparent;
}
.brand-mark {
  width: 28px; height: 28px; border-radius: var(--radius-sm);
  display: block; flex-shrink: 0;
  box-shadow: var(--elevation-1);
}

.header-spacer { flex: 1; }

.header-avatar {
  width: 36px; height: 36px; border-radius: var(--radius-full);
  background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-info) 100%);
  display: flex; align-items: center; justify-content: center;
  font-size: var(--text-xs); font-weight: var(--weight-bold);
  color: var(--color-on-primary); text-decoration: none;
  flex-shrink: 0; overflow: hidden;
  transition: box-shadow var(--duration-base) var(--ease-standard);
  -webkit-tap-highlight-color: transparent;
}
.header-avatar img { width: 100%; height: 100%; border-radius: var(--radius-full); object-fit: cover; }
.header-avatar:hover,
.header-avatar:focus-visible { box-shadow: 0 0 0 2px var(--color-primary-glow); }

/* ── 2. DESKTOP HEADER TABS (≥1024px) ────────────────── */
.header-nav { display: none; align-items: center; gap: var(--space-1); margin-left: var(--space-6); }
.hn-link {
  display: inline-flex; align-items: center;
  min-height: 40px; padding: 0 var(--space-4);
  border-radius: var(--radius-sm);
  color: var(--color-text-3); text-decoration: none;
  font-size: var(--text-sm); font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-wide);
  transition: color var(--duration-fast) var(--ease-standard),
              background var(--duration-fast) var(--ease-standard);
}
.hn-link:hover { color: var(--color-text-1); background: var(--overlay-4); }
.hn-link.active { color: var(--color-primary); background: var(--color-primary-soft); }

@media (min-width: 1024px) {
  .header-nav { display: flex; }
}

/* ── 3. BOTTOM TAB BAR (<1024px) ─────────────────────── */
.action-bar {
  position: fixed; bottom: 0; left: 0; right: 0;
  min-height: var(--action-bar-h);
  padding-bottom: env(safe-area-inset-bottom, 0);
  background: rgba(16, 16, 16, 0.97);
  -webkit-backdrop-filter: blur(24px); backdrop-filter: blur(24px);
  border-top: var(--border-w-1) solid var(--color-border-1);
  display: flex; align-items: stretch;
  z-index: var(--z-header);
}

.ab-tab {
  flex: 1; min-width: 48px;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: var(--space-1);
  color: var(--color-text-3); text-decoration: none;
  font-size: var(--text-2xs); font-weight: var(--weight-bold);
  letter-spacing: var(--tracking-wider); text-transform: uppercase;
  position: relative;
  transition: color var(--duration-base) var(--ease-standard);
  -webkit-tap-highlight-color: transparent;
}
.ab-tab svg {
  width: var(--icon-lg); height: var(--icon-lg);
  stroke: currentColor; fill: none;
  stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round;
  transition: transform var(--duration-base) var(--ease-out-expo);
}
.ab-tab:hover { color: var(--color-text-2); }
.ab-tab.active { color: var(--color-primary); }
.ab-tab.active svg { transform: translateY(-1px) scale(1.08); }
.ab-tab.active::before {
  content: '';
  position: absolute; top: 0; left: 50%;
  transform: translateX(-50%);
  width: 24px; height: 2px;
  border-radius: 0 0 2px 2px;
  background: var(--color-primary);
}

@media (min-width: 1024px) {
  .action-bar { display: none; }
}

/* ── 4. PAGE SHELL ───────────────────────────────────── */
.page-body {
  padding-top: var(--header-h);
  padding-bottom: calc(var(--action-bar-h) + var(--space-6) + env(safe-area-inset-bottom, 0));
}
@media (min-width: 1024px) {
  .page-body { padding-bottom: var(--space-8); }
}

/* Sayfa girişi — kabuk sabit kalır, içerik yumuşak girer. */
@media (prefers-reduced-motion: no-preference) {
  .main-content { animation: page-enter var(--duration-slower) var(--ease-out-expo) both; }
}
@keyframes page-enter {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: none; }
}

/* ── 5. PROFILE HUB (ikincil gezinme — edit_profile) ─── */
.hub { display: flex; flex-direction: column; margin-bottom: var(--space-7); }
.hub-section-label {
  font-size: var(--text-2xs); font-weight: var(--weight-bold);
  color: var(--color-text-4);
  letter-spacing: var(--tracking-widest); text-transform: uppercase;
  padding: var(--space-4) var(--space-1) var(--space-2);
}
.hub-card {
  background: var(--color-surface-2);
  border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.hub-link, .hub-row {
  display: flex; align-items: center; gap: var(--space-4);
  min-height: 52px; padding: var(--space-2) var(--space-4);
  color: var(--color-text-2); text-decoration: none;
  font-size: var(--text-base); font-weight: var(--weight-medium);
  -webkit-tap-highlight-color: transparent;
}
.hub-link { transition: background var(--duration-fast) var(--ease-standard),
                        color var(--duration-fast) var(--ease-standard); }
.hub-link + .hub-link, .hub-link + .hub-row,
.hub-row + .hub-link, .hub-row + .hub-row {
  border-top: var(--border-w-1) solid var(--color-border-1);
}
.hub-link:hover { background: var(--overlay-4); color: var(--color-text-1); }
.hub-link svg, .hub-row > svg {
  width: var(--icon-md); height: var(--icon-md); flex-shrink: 0;
  stroke: currentColor; fill: none;
  stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round;
}
.hub-chevron { margin-left: auto; color: var(--color-text-4); }
.hub-link-premium { color: var(--color-primary); }
.hub-link-premium:hover { color: var(--color-primary); background: var(--color-primary-soft); }
.hub-link-danger { color: var(--color-danger); }
.hub-link-danger:hover { color: var(--color-danger); background: var(--color-danger-soft); }

.hub-lang {
  display: flex; gap: var(--space-1); margin-left: auto;
  background: var(--color-surface-3);
  border: var(--border-w-1) solid var(--color-border-1);
  border-radius: var(--radius-sm); padding: 3px;
}
.hub-lang-opt {
  min-height: 34px; padding: 0 var(--space-4);
  font-family: var(--font-body); font-size: var(--text-xs);
  font-weight: var(--weight-bold); letter-spacing: var(--tracking-wide);
  color: var(--color-text-3); background: none; border: none;
  border-radius: var(--radius-xs); cursor: pointer;
  transition: color var(--duration-fast) var(--ease-standard),
              background var(--duration-fast) var(--ease-standard);
}
.hub-lang-opt.on { background: var(--color-primary); color: var(--color-on-primary); }
.hub-lang-opt:not(.on):hover { color: var(--color-text-1); }
```

- [ ] **Step 5: Delete `static/nav.js` and its script tags**

Delete the file. Remove the `<script src="/static/nav.js…"></script>` line from: chat.html:461, friends.html:383, edit_profile.html:500, index.html:871, leaderboard.html:383, nutrition.html:729, manage_stack.html:370, feed.html:261, premium.html:111, progress.html:450, pump_check_gallery.html:121, quests.html:275, training.html:1460.

- [ ] **Step 6: `_head.html` viewport + `theme.css` container**

`templates/_head.html` line 6 →
```html
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
```

`static/theme.css` lines 24–32 →
```css
.main-content {
  padding: var(--space-4) var(--space-4) var(--space-6);
  max-width: var(--content-max);
  margin: 0 auto;
}

@media (min-width: 768px) {
  .main-content { padding: var(--space-6) var(--space-6) var(--space-8); }
}
```
(Bottom clearance for the fixed bar now lives once in `.page-body`; page-level overrides like edit_profile.html:191 are removed.)

Also delete from nav.css rewrite scope: the old `@media (min-width: 1024px) { .main-content { margin … !important } }` rule — theme.css already centers.

- [ ] **Step 7: Flip secondary pages to `nav_active = 'profile'`**

quests.html, friends.html, leaderboard.html, manage_stack.html (set in Task 1) and chat.html:189 (`friends`→`profile`), feed.html:67 (`feed`→`profile`), premium.html:54 (`premium`→`profile`).

- [ ] **Step 8: Rewrite the two legacy nav tests in `tests/test_pump_check_sharing.py`**

```python
def test_feed_page_uses_shared_app_shell(client, auth_user):
    html = client.get("/feed").get_data(as_text=True)
    # feed artık Profil sekmesi altında ikincil sayfadır
    assert 'href="/edit-profile" class="ab-tab active"' in html
    assert 'class="global-header"' in html


def test_shell_partials_have_five_tabs_and_no_drawer():
    root = Path(__file__).resolve().parents[1]
    bar = (root / "templates" / "_actionbar.html").read_text(encoding="utf-8")
    for href in ('href="/"', 'href="/nutrition"', 'href="/training"',
                 'href="/progress-page"', 'href="/edit-profile"'):
        assert f'{href} class="ab-tab' in bar
    header = (root / "templates" / "_nav.html").read_text(encoding="utf-8")
    assert "drawer" not in header
```

- [ ] **Step 9: Run tests**

Run: `python -m pytest tests/test_app_shell.py tests/test_pump_check_sharing.py tests/test_design_system.py -q`
Expected: PASS except `tests/test_i18n.py::test_drawer_level_title_localized` (fixed in Task 3 — run it to confirm it's the only casualty: `python -m pytest tests/test_i18n.py -q`).

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "Redesign app shell: 5-tab bottom nav, drop drawer"
```

---

### Task 3: Profile hub — secondary destinations, language, logout

**Files:**
- Modify: `templates/edit_profile.html` — remove back-btn block (lines ≈207–211) + `.back-btn` styles (≈179–186); add `.avatar-rank` line under `avatar-handle`; insert hub `<nav>` after the stats-row `</div>`
- Modify: `locales/tr.json`, `locales/en.json` — add `nav.section_settings`, `nav.language`
- Modify: `tests/test_i18n.py::test_drawer_level_title_localized`
- Test: `tests/test_app_shell.py` (extend)

**Interfaces:**
- Consumes: `.hub-*` classes from Task 2 nav.css; `setLang` global from static/i18n.js; `user_title`/`user_level` from inject_rank.
- Produces: Profile page is the canonical entry to Friends `/friends`, Feed `/feed`, Club `/leaderboard`, Quests `/quests`, Supplements `/supplements`, Premium `/premium`, Logout `/logout`, language toggle.

- [ ] **Step 1: Write failing tests** (extend `tests/test_app_shell.py`)

```python
def test_profile_hub_lists_secondary_destinations(client, auth_user):
    html = client.get("/edit-profile").get_data(as_text=True)
    for href in ("/friends", "/feed", "/leaderboard", "/quests",
                 "/supplements", "/premium", "/logout"):
        assert f'href="{href}" class="hub-link' in html, href
    assert 'data-action="setLang"' in html
```

Run: `python -m pytest tests/test_app_shell.py -q` — expected FAIL.

- [ ] **Step 2: i18n keys**

tr.json (nav bloğuna): `"nav.section_settings": "Ayarlar",` `"nav.language": "Dil",`
en.json (aynı yere): `"nav.section_settings": "Settings",` `"nav.language": "Language",`

- [ ] **Step 3: edit_profile.html hub markup**

After the stats-row closing `</div>` insert:

```html
<!-- ── HUB: ikincil gezinme (eski çekmecenin yeni evi) ── -->
<nav class="hub" aria-label="{{ t('nav.menu_nav') }}">
    <div class="hub-section-label">{{ t('nav.section_explore') }}</div>
    <div class="hub-card">
        <a href="/friends" class="hub-link"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>{{ t('nav.friends') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
        <a href="/feed" class="hub-link"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>{{ t('nav.feed') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
        <a href="/leaderboard" class="hub-link"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3v18"/><path d="M19 3v18"/><path d="M5 7h14"/><path d="M8 11h8"/><path d="M10 15h4"/></svg>{{ t('nav.club') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
        <a href="/quests" class="hub-link"><svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>{{ t('nav.quests') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
        <a href="/supplements" class="hub-link"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10.5 1.5H8.25A2.25 2.25 0 0 0 6 3.75v16.5a2.25 2.25 0 0 0 2.25 2.25h7.5A2.25 2.25 0 0 0 18 20.25V3.75a2.25 2.25 0 0 0-2.25-2.25H13.5m-3 0V3h3V1.5m-3 0h3m-6 18.75h9"/></svg>{{ t('nav.supplements') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
        <a href="/premium" class="hub-link hub-link-premium" data-ga-event="premium_nav_click"><svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>{{ t('nav.premium') }}<svg class="hub-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></a>
    </div>
    <div class="hub-section-label">{{ t('nav.section_settings') }}</div>
    <div class="hub-card">
        <div class="hub-row">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
            {{ t('nav.language') }}
            <div class="hub-lang" role="group" aria-label="{{ t('lang.aria') }}">
                <button type="button" class="hub-lang-opt{% if locale == 'tr' %} on{% endif %}" data-action="setLang" data-args='["tr"]'>TR</button>
                <button type="button" class="hub-lang-opt{% if locale == 'en' %} on{% endif %}" data-action="setLang" data-args='["en"]'>EN</button>
            </div>
        </div>
        <a href="/logout" class="hub-link hub-link-danger"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>{{ t('nav.logout') }}</a>
    </div>
</nav>
```

Under `<div class="avatar-handle">@{{ username }}</div>` add:
```html
<div class="avatar-rank">{{ user_title }} · Level {{ user_level }}</div>
```
and in the page's existing `<style nonce…>` block add:
```css
.avatar-rank { font-size: 11px; font-weight: 600; color: var(--volt); letter-spacing: .06em; text-transform: uppercase; margin-top: 6px; }
```
(page-level style follows the page's existing conventions).

- [ ] **Step 4: Update the i18n test** (tests/test_i18n.py:271)

```python
def test_profile_level_title_localized(app, client, make_user, login):
    """inject_rank → profil sayfası rütbe ünvanı kullanıcının diline göre (level_title)."""
    make_user("ranken", language="en")     # rank_points=0 → seviye 1
    login("ranken")
    body = client.get("/edit-profile").get_data(as_text=True)
    assert "Fitness Traveler" in body and "Fitness Yolcusu" not in body
```

- [ ] **Step 5: Run tests + commit**

Run: `python -m pytest tests/test_app_shell.py tests/test_i18n.py -q` — expected PASS.

```bash
git add -A
git commit -m "Add profile hub: secondary nav, language, logout"
```

---

### Task 4: Full verification + docs + handoff

**Files:**
- Modify: `docs/design-system.md` (App Shell section)
- Create: `docs/archive/handoff-2026-07-04-phase1-design-system.md` (move current handoff)
- Rewrite: `docs/handoff.md` (Phase 2 handoff per spec's end checklist)

- [ ] **Step 1: Full suite**

Run: `python -m pytest -q`
Expected: all pass (baseline 1081 + new shell tests, minus the 2 rewritten).

- [ ] **Step 2: Authenticated smoke of all 13 pages**

Via pytest client or scratch server: GET /, /nutrition, /training, /progress-page, /quests, /friends, /feed, /leaderboard, /supplements, /premium, /edit-profile, /pump-check-gallery + a chat page → all 200, all contain `class="global-header"` and `class="action-bar"`, none contain `fx-drawer`.

- [ ] **Step 3: Visual check (if Chrome extension available)**

Mobile viewport (390×844): bottom bar visible, 5 tabs, active state; desktop (1280): header tabs visible, bottom bar hidden. If browser unavailable, note in handoff (same caveat as Phase 1).

- [ ] **Step 4: Docs**

design-system.md: add "Uygulama Kabuğu (Phase 2)" section documenting `_nav.html`/`_actionbar.html` contract (`nav_active` values), shell classes (`.global-header .header-nav .hn-link .action-bar .ab-tab .hub-*`), breakpoint behavior (<1024 bottom bar / ≥1024 header tabs), and note drawer removal. Update "Bilinen sapmalar": 900px→1024px sweep still pending; nav.css translucent header literals documented.

handoff.md: archive Phase 1 handoff to docs/archive/, write Phase 2 handoff with: Completed work / Files modified / Components created / Architectural decisions / Remaining tasks / Known issues / Next steps / quality metrics review (responsiveness, accessibility, visual consistency, maintainability, reusability, performance, UX clarity) — explicitly flagging weak metrics.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Add app shell docs and phase 2 handoff"
```

## Self-Review Notes

- Spec coverage: bottom nav 5 tabs ✔ (T2), secondary pages into Profile ✔ (T3), remove sidebar ✔ (T2), safe areas ✔ (viewport-fit + env() paddings), sticky nav ✔ (fixed header/bar), transitions ✔ (page-enter, reduced-motion safe), desktop/tablet ✔ (≥1024 header tabs), consistent headers/padding ✔ (shared partials + tokenized .main-content), shell-only ✔ (page content untouched except mandated hub).
- "Settings" in spec = the settings hub section (language/logout); no standalone settings page exists — noted for handoff as future work.
- Type/name consistency: `nav_active` values and `.hub-*`/`.ab-tab`/`.hn-link` names used identically across T1–T3.
