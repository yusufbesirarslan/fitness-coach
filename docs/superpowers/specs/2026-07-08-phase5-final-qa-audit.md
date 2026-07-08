# Phase 5 · Surface 4 — Final QA Audit (Stage 0 findings)

Date: 2026-07-08
Method: static inventory (grep) + Playwright screenshot pass driving all 13 core
surfaces authenticated at 390px + 1280px (seed: `qauser`, profile-complete, with
supplements/meals/friend/messages + 3 ranked users). Baselines in the session
scratchpad as `qa-<surface>-{desktop,mobile}.png`.

Severity policy: **High + Med are fixed** this phase; **Low** is logged as
remaining tech debt (not fixed). Each finding names the file + symptom + fix and
the Stage/Task that resolves it.

---

## Global findings (Stage 1)

- **G1 [High] Legacy `--volt*` aliases still used — ~91 occurrences.** Files:
  `coach_widget.css` (16), `theme.css` (16); inline `<style>` in `chat.html`
  (15), `friends.html` (12), `manage_stack.html` (9), `leaderboard.html` (8),
  `premium.html` (7), `feed.html` (4), `quests.html` (4). Aliases defined at
  `tokens.css:208-211` (`--volt→--color-primary`, `--volt-dim→--color-primary-soft`,
  `--volt-glow→--color-primary-glow`, `--volt-dark→--color-primary-strong`).
  **Fix (Task 2):** migrate all usages to canonical, then delete the alias block.
- **G2 [High] `coach_widget.css` has 79 raw colors** (hex/`rgba`) — the biggest
  token-compliance offender; app-wide widget. **Fix (Task 3):** map to canonical
  tokens; add shared tokens for genuine gaps.
- **G3 [Med] Residual raw colors:** `components.css` 21, `nutrition.css` 6,
  `theme.css` 7. **Fix (Task 4):** tokenize (keep only justified, annotated
  primitives).
- **G4 [High] Inline `<style>` blocks in 9 templates:** `index`, `friends`,
  `feed`, `leaderboard`, `quests`, `manage_stack`, `premium`, `chat`,
  `pump_check_gallery`. **Fix (Tasks 5–13):** extract each to `static/<surface>.css`
  (`index` → append to existing `dashboard.css` if already linked).

## Per-surface findings

Overall the surfaces are visually strong and consistent on the canonical system
(shared nav/header/card/badge/tab/stat-card/sheet). The concrete cross-surface
defects are the two below; most surfaces have **no** High/Med visual findings
beyond the global refactor.

### Cross-surface

- **S1 [Med] Empty-state inconsistency.** `friends` ("Arkadaşlarım" → bare text
  line), `feed` (bare text in a card), and `pump_check_gallery` (a small,
  left-aligned cramped card) do **not** use the canonical `.empty-state`
  component (centered icon + title + body + optional CTA) that `leaderboard`,
  `progress`, and `profile` use. **Fix:** convert these three empty states to
  `.empty-state`. Owners: Task 6 (friends), Task 7 (feed), Task 13 (pump gallery).
- **S2 [Med] Turkish copy missing diacritics** in `locales/tr.json` (readability;
  sibling keys like `friends.page_sub` are correct, so this is authoring drift,
  not a font issue). Keys to correct:
  - `feed.page_sub` "cevrenden…paylasimlari" → "çevrenden…paylaşımları"
  - `feed.comments_loading` "yukleniyor" → "yükleniyor"
  - `feed.comments_empty` "Henuz yorum yok" → "Henüz yorum yok"
  - `feed.comments_error` "yuklenemedi" → "yüklenemedi"
  - `feed.empty` "Henuz…paylasimi…arkadas ekle" → "Henüz…paylaşımı…arkadaş ekle"
  - `gallery.page_sub` "fotograflarin ve paylasim gecmisin" → "fotoğrafların ve paylaşım geçmişin"
  - `gallery.empty` "Henuz kaydedilmis" → "Henüz kaydedilmiş"
  - `gallery.profile_link_desc` "fotograflarini goruntule" → "fotoğraflarını görüntüle"
  - `chat.pump_unavailable` "artik goruntulenemiyor" → "artık görüntülenemiyor"
  **Fix:** correct the TR values (keys unchanged → EN parity preserved). Owner:
  fold into the owning surface tasks — feed keys in Task 7, gallery keys in Task
  13, `chat.pump_unavailable` in Task 12. (All are `tr.json` edits; one commit
  per owning task is fine.)

### index (dashboard, `/`)
Clean. Welcome card + XP/streak, calorie ring, next-step banner, quick-actions
2×2, macro rings, weight tracker, achievements, tip-of-day; mobile stacks well.
**Only global G4** (extract inline `<style>`; note: verify whether it belongs in
`dashboard.css`). No High/Med visual findings.

### nutrition (`/nutrition`)
Clean (Phase 4). Tabs, calorie ring + macro bars, meal timeline with A-score
badges, empty meal slots, quick-add, AI eval. No High/Med findings. (Already
extracted — no inline `<style>`.)

### training (`/training`)
Clean (Phase 5). Preferences form (days/style/goal/equipment/focus/duration/
cardio/injury chips) renders consistently. No High/Med findings. (Already
extracted.)

### progress (`/progress-page`)
Clean (Phase 5). No High/Med findings. (Already extracted.)

### edit_profile (`/edit-profile`)
Clean (Phase 5, just shipped). No High/Med findings. (Already extracted.)

### friends (`/friends`)
G4 (extract inline `<style>`). **S1** empty-state. Low: invite link shows
"Yükleniyor…" because `/referral` 404s in the local env — **backend/local
artifact, out of scope** (S-Low-1).

### feed (`/feed`)
G4 (extract). **S1** empty-state. **S2** feed.* TR diacritics.

### leaderboard (`/leaderboard`)
G4 (extract) + G1 volt. Empty-state here is the canonical reference — leave as
is. No other High/Med findings.

### quests (`/quests`)
G4 (extract) + G1 volt. Rank card + header render fine. **Low:** quest list not
auditable locally (no seeded quests) — verify the list/empty state renders after
extraction using prod-like data if available (S-Low-2).

### manage_stack (supplements, `/supplements`)
G4 (extract) + G1 volt. Add-supplement form + stack cards render cleanly;
diacritics fine. No other High/Med findings.

### premium (`/premium`)
G4 (extract) + G1 volt. Two-plan comparison + CTA render cleanly. No other
High/Med findings.

### chat (`/chat/<username>`)
G4 (extract) + **G1 volt (15 — the most)**. DM header/bubbles/input render
cleanly; diacritics fine. **S2** `chat.pump_unavailable` TR fix. No other
High/Med visual findings.

### pump_check_gallery (`/pump-check-gallery`)
G4 (extract). **S1** empty-state (cramped card → `.empty-state`). **S2**
gallery.* TR diacritics.

## Remaining tech debt (Low — logged, not fixed this phase)

- **S-Low-1:** `/friends` invite link stuck on "Yükleniyor…" when `/referral`
  404s — a backend/endpoint concern, out of this frontend-only phase's scope.
- **S-Low-2:** quests list rendering unverified locally (no seed data for
  `DailyQuest`); the page shell/rank card are fine.
- The 5 legacy auth/onboarding pages (`login`, `register`, `setup`, `verify`,
  `landing`) remain on `style.css` — explicitly out of scope (separate cycle).

## Summary for Stage 2 dispatch

Every in-scope surface's task is: **extract its inline `<style>` (G4)** + **apply
its S1/S2 items if listed** + Stage-1 already handled its `--volt`/raw-color
debt globally. Surfaces with no S-items (index, leaderboard, quests,
manage_stack, premium) are extraction-only. nutrition/training/progress/
edit_profile need no Stage-2 change (already clean + extracted) → Task 14 is a
near-no-op (confirm + skip).
