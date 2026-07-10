# Pump Check fixes — design / spec (2026-07-10)

Source: `pump-check.txt` (masaüstü). Five items, scoped from `pump-check.txt` and
confirmed with the user. UI is Turkish, code English.

## Task 1 — Location dropdown + conditional "where" + always-on note

**Current:** `training.html` pump modal has a `#pump-location` `<select>`
(Ev / Spor Salonu / **Diğer:**, label "Nerede antrenman yaptın?") *and* a
`#pump-desc` text input whose placeholder redundantly also asks
"Bugün antrenmanını nerde yaptın?".

**Change (confirmed: "Conditional 'where' + always-on note"):**
- Keep the `#pump-location` dropdown.
- Add a hidden text input (`#pump-location-other`) immediately after the dropdown.
  Shown **only when `Diğer:` is selected**; hidden (and cleared) otherwise.
  Placeholder e.g. "Nerede? (örn. park, otel spor salonu)".
- `#pump-desc` stays **always visible** with a generic placeholder
  (new key value, e.g. "Bir not ekle (opsiyonel)") — identical for feed and DM.
- Submit (`training.js` `submitPumpCheck`): `location_type` = trimmed
  `#pump-location-other` value when the dropdown is `Diğer:` (fallback to `"Diğer"`
  if left empty), else the dropdown value. Backend already caps to 50 chars.
- Reset the custom input in `openPumpCheck()`; wire a `change` listener on the
  dropdown to toggle visibility (CSP-safe: `data-action-change` or an init-time
  `addEventListener` inside `initPumpCheck`).

**Files:** `templates/training.html`, `static/training.js`, `locales/tr.json`,
`locales/en.json`.

## Task 2 — Frame adjustment (crop / pan / zoom) before upload

**Current:** `handlePumpFile` reads the file as a base64 data-URL and previews it
as-is; there is no way to reframe. Upload sends the whole image.

**Change:** After a photo is picked, open an in-modal **crop stage** (pure canvas —
CSP forbids external libs):
- A fixed **4:5 portrait frame** (feed/DM display uses `object-fit:cover`, so a
  predictable aspect gives the user control over the visible crop).
- **Drag to pan** (mouse + touch) and a **zoom** `<input type="range">`.
- "Onayla" renders the framed region to an offscreen canvas →
  `toDataURL('image/jpeg', 0.9)` → becomes `pumpImageData` + the preview.
- "Değiştir"/"Vazgeç" returns to the picker / cancels the crop.
- Output long edge capped (e.g. ≤1440px) to keep the payload within the existing
  ~6MB decode limit.

**Files:** `templates/training.html` (crop stage markup), `static/training.js`
(cropper logic + hook into `handlePumpFile`/`submitPumpCheck`),
`static/training.css` (crop stage styles), locale keys for the new buttons/labels.

## Task 3 — Photo not showing in Feed / DM (investigation result)

**Status: no reproducible code defect found.** Traced the full path and every
layer is correct and matches the working Pump Check Gallery:
- Decoder `validate_pump_check_image` → correct `content_type` (same code path
  meal photos use in prod).
- `s3_helper.upload_image` → key `pump-checks/<uid>/<YYYY>/<MM>/<uuid>.<ext>`,
  correct `ContentType`; `image_key` persisted.
- `generate_presigned_url(..., expected_user_id=check.user_id)` → ownership guard
  (`_key_belongs_to`) passes for owner-keyed objects.
- CSP `img-src` includes the bucket host (`config.CSP_IMG_S3_HOSTS`, both
  virtual-hosted + path style) — and the **gallery renders**, proving CSP/IAM/
  presign all work.
- Feed serialization: `image_visibility_preauthorized=True` (tested:
  `test_feed_data_uses_preauthorized_feed_visibility_for_image_urls`).
- DM serialization: `can_view_pump_check` gate then presigned URL (tested:
  `test_chat_messages_include_authorized_pump_check_payload` asserts
  `imageUrl is not None`).
- Templates: feed `cardMedia` and chat `renderPumpCheckCard` both emit
  `<img src=imageUrl>`; CSS (`.feed-img`, `.msg-pump-card img`) renders normally.

The feed/DM image code is byte-for-byte equivalent to the gallery, which the user
confirms works.

**Action:**
1. Add an **end-to-end regression test** exercising `/workout/complete` (S3
   round-trip stubbed) → assert `image_key` persisted → `/feed/data` and
   `/chat/.../messages` return a non-null `imageUrl`. This locks in the path and
   catches any future regression.
2. **Obtain one runtime artifact** to pinpoint any environment-specific defect:
   the `imageUrl` value in the live `/feed/data` JSON and the browser
   Console/Network output when an image fails (e.g., a CSP refusal, a 403 on the
   S3 URL, or `imageUrl: null`). The fix, if any, follows from that observation.

No speculative code changes without a reproduction.

**Files (test):** `tests/test_pump_check_sharing.py` (or a new
`tests/test_pump_check_image_e2e.py`).

## Task 4 — Nutrition FAB overlap

**Current:** on `/nutrition`, `.log-fab` (`bottom: calc(action-bar-h + space-4)`,
`right: space-4`) and the coach `#cw-fab` (`bottom: calc(action-bar-h + 15px)`,
`right: 20px`) sit at nearly the same bottom-right point and overlap.

**Change:** stack the `＋` log FAB **above** the coach button on nutrition. In
`static/nutrition.css`, raise `.log-fab` `bottom` by the coach button height (56px)
+ its offset + a gap, aligned to the same right rail; mirror for the ≥1024px rule.
`--z-fab (200)` is below the coach window (`z 9998`), so opening the chat cleanly
covers the FAB. Coach widget CSS stays untouched (it is global).

**Files:** `static/nutrition.css`.

## Task 5 — Global features menu ("hamburger"), mobile only

**Current:** Friends / Feed / Leaderboard / Quests / Gallery / Supplements /
Premium live only in the Profile-page hub (`edit_profile.html`), so on mobile they
are unreachable from other tabs.

**Change (confirmed: "Header ☰ drawer, mobile only"):**
- Add a `☰` button to the global header (`_nav.html`), visible **only < 1024px**
  (desktop already shows the top nav tabs; leave it unchanged).
- Clicking opens a slide-in **drawer / sheet** listing the hub features (same
  hrefs/labels as the Profile hub). Backdrop + Esc close.
- CSP-safe: toggling via `data-action` (`static/actions.js` delegation) or a small
  init script with the request nonce; styles in an external stylesheet (no
  JS-injected `<style>`).
- Update `test_shell_partials_have_five_tabs_and_no_drawer`, which currently
  asserts `"drawer" not in header` — this change intentionally introduces the
  drawer, so the assertion is revised to reflect the new mobile menu while keeping
  the five-tab action bar intact.

**Files:** `templates/_nav.html`, new drawer partial/markup, new CSS (e.g.
`static/menu-drawer.css` or into `nav.css`), `static/actions.js` if a new helper is
needed, `tests/test_pump_check_sharing.py` (nav assertion),
`tests/test_app_shell.py` if it pins nav markup.

## Ordering

3 (investigation + test) → 4 (CSS only, low risk) → 1 → 2 → 5. Each verified with
`pytest` and a local `flask run` walkthrough before moving on.
