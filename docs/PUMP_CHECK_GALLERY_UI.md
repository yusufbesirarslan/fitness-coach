# Pump Check Gallery — UI contract (PR2)

Presentation layer only. PR #244 (`fix(pump-check): restore gallery media
delivery`) owns media delivery — S3 addressing, presigned-URL lifecycle, object
keys, storage permissions, gallery ownership and CSP `img-src` — and none of it
is touched here. This document records the visual decisions and the measured
contract that `tests/test_pump_check_gallery_ui.py` and
`scripts/frontend_audit/pump_check_pr2_matrix.py` enforce.

## Scope

Every `.gallery-*` selector is exclusive to `templates/pump_check_gallery.html`
and `static/pump_check_gallery.css` (grep-verified: no other template, script or
stylesheet references one). The shared layer is read, not rewritten. It gains
exactly three things, each verified against its existing consumers:

- `--leading-headline` and `--tracking-tight` in `tokens.css`, additive with no
  prior consumers (see below).
- `text-decoration: none` on `.btn-ghost` in `components.css`. The component is
  now also rendered as an `<a>` (the empty-state CTA) and arrived underlined;
  every pre-existing consumer is a `<button>`, where the property is a no-op.

## Typography

The app-wide `.page-hdr h1` role is Bebas Neue: condensed, all-caps, fluid
38–52px, positive tracking, two lines. That is the right poster title for
`PROGRESS` or `NUTRITION`. On the one surface whose subject is the user's own
body it competes with the photographs and reads as a gym flyer, so this page
takes a neutral headline role **locally** — `.gallery-hdr h1`. Retiring
`--font-display` app-wide is a different change with different consumers and is
deliberately not made here.

| Role | Before | After |
|---|---|---|
| Page title | Bebas Neue · 38–52px · `PUMP CHECK` / `GALLERY` · +1.52px tracking · 2 lines | `--font-sans` · `--text-display-sm` (28–36px) · `Pump Check Gallery` · `--tracking-tight` · 1 line |
| Supporting copy | `--text-base`, muted | unchanged (already correct) |
| Card metadata | date 11px `--color-text-3`, description 13px `--color-text-1` | date `--text-md` medium `--color-text-1`, description `--text-sm` `--color-text-3` |
| Detail heading | inline `--font-display` + `.08em` tracking | `.gallery-detail-title`, `--text-2xl` semibold, `--tracking-tight` |

The date is the card's primary metadata and the description its secondary
detail; the previous card had that hierarchy inverted.

### Shared tokens added

Both are additive and had zero consumers before this PR, so no existing surface
can regress:

- `--leading-headline: 1.1` — the leading scale jumped `1 → 1.2` with nothing in
  between, so a page headline either sat on its own descenders or read as body.
- `--tracking-tight: -0.01em` — optical correction for large type only. Never
  for body copy or micro labels.

## Media frame

`aspect-ratio: 4 / 5`, `object-fit: cover`.

Pump Checks are phone photographs of a standing body. The previous 1:1 frame
cropped the head or the legs out of the exact content the gallery exists to
show. 4:5 is portrait-biased without destroying a landscape upload, which the
`cover` crop still centres.

`.gallery-media` is the single geometry authority: the photo, the loading
placeholder and the photo-unavailable state all occupy that same box, so nothing
reflows when bytes land or fail to.

## Responsive grid

A deliberate minimum card width per tier — not "as many columns as fit". The
previous `minmax(150px, 1fr)` inside an 880px wrapper produced five ~168px
thumbnails on a 1280px viewport: the photos were the smallest thing on a page
about photos. The 880px wrapper cap is gone; `.main-content` (`--content-max`
1280px) already centres and bounds the page.

| Tier | `--gallery-card-min` | gap |
|---|---|---|
| `< 520px` | 132px | `--space-3` |
| `≥ 520px` | 200px | `--space-4` |
| `≥ 1024px` | 260px | `--space-5` |

Measured (Chromium, `pump_check_pr2_matrix`):

| Viewport | Before | After |
|---|---|---|
| 320 | 1 column × 288px, 1:1 | 2 columns × 138px, 4:5 |
| 390 | 2 × 174px, 1:1 | 2 × 173px, 4:5 |
| 768 | 4 × 172.5px, 1:1 | 3 × 229px, 4:5 |
| 1280 | 5 × 168px, 1:1 | 4 × 293px, 4:5 |
| 1440 | 5 × 168px, 1:1 | 4 × 293px, 4:5 |

The card width *floor* is asserted per viewport, not just the column count: a
grid that fits more columns by shrinking the photo is a regression on this page.

## States

- **loading** — `data-state="loading"`. Two reserved rows of `.gallery-skeleton`
  at the grid's *actual* column count, read from the resolved
  `grid-template-columns`, so the placeholder occupies the geometry the photos
  will occupy rather than a guessed number. One slow opacity pulse, silenced
  under `prefers-reduced-motion`.
- **populated** — `data-state="populated"`.
- **photo unavailable** — `.gallery-media-fallback`, `role="img"` with an
  accessible name, absolutely positioned inside the same frame so its geometry
  is identical to a photo's. Both paths are covered: the server resolving no
  `imageUrl`, and a signed URL that 404s (the client swap PR #244 introduced —
  unchanged, only restyled). Deliberately quiet: a missing file is not an error
  the user caused or can act on.
- **empty** — `data-state="empty"`. Title, an explanation of *why* it is empty,
  and a link to `/training`, the established route where a Pump Check is
  captured. No new product flow is invented.
- **unavailable** — `data-state="unavailable"`. A read failure is not an empty
  gallery; presenting one as the other would tell the user their Pump Checks are
  gone. Same rule the Progress read models already follow.

## Detail view

Below 900px the panel stacks (photo, then facts) with the photo capped at
`52vh`. At and above 900px it becomes a two-column grid — photo in the wider
column at full panel height, facts beside it — so the image holds the majority
of the composition on desktop.

The desktop panel takes a *definite* `height: min(72vh, 660px)` over a single
`minmax(0, 1fr)` row. With `min-height` the row sized itself to the facts
column's content, `max-height` then clipped the result, and the browser matrix
measured a 899.6px media row inside an 828px panel — the bottom of the photo and
the action buttons were cut off rather than the facts column scrolling. Measured
after the fix: 1280 → panel 576px / media 674.7×574 (share 1.0), 1440 → panel
648px / media 674.7×646 (share 1.0), `detail_overflow` false at every viewport.

Accessibility: a labelled close control (`aria-label`, `training.close`) inside
the dialog receives focus on open, Tab is kept inside the panel, Escape closes,
and focus returns to the card that opened it. Background scroll stays locked.

`z-index` moved from a raw `1000` to `--z-overlay`, so toasts (`--z-toast`) sit
above the dialog again rather than behind it.

## Not changed

Media contract, S3 addressing, presigned-URL lifecycle, storage persistence,
object keys, gallery ownership/authorization, CSP media policy, the
`/pump-check-gallery/data` payload, `serialize_pump_check_card`, and
`loading="lazy"` / `decoding="async"` on gallery photos.
