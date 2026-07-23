# Sprint 0 frontend-readiness evidence — 2026-07-23 (supported run)

Supported-environment run executed inside **WSL Ubuntu-24.04** (Linux,
`operating_system.supported: true`), Python 3.12.3, Playwright 1.61.0, Chromium
revision 1228 (major 149).

## Result
- Preflight: `success: true` (launch, loopback navigation, screenshot, clean shutdown).
- `capture --tier full`: 325 resolved captures — **277 Chromium captured, 0 failed**;
  24 WebKit + 24 Firefox `blocked` (optional browsers not installed; their system
  libraries need privileged apt access). Chromium is the mandatory browser, so the
  run exits 0.
- `verify --manifest`: **`audit artifacts valid`**.

## Contents
- `manifest.json` — full run manifest (all 325 capture rows: 277 captured + 48 blocked).
- `results/` — all 277 per-capture check JSONs (console/page errors, failed
  requests, horizontal overflow, placeholders, fixed-bottom occlusion,
  active-navigation, viewport/scroll widths).
- `screenshots-sample/` — 14 representative Chromium screenshots (6 key routes at
  390×844 and 1440×900, plus two narrow-width issue exemplars).

## Note on screenshots
The complete set of **277 full-page screenshots (~16 MB)** was produced and
validated locally on 2026-07-23 and is deliberately kept out of git to avoid
bloating shared history. Only the representative sample above is committed. To
regenerate the full set, re-run `capture --tier full` per
`../../visual-qa-harness.md`. The raw run lives at `~/axisai-audit-run/full`
(WSL) and `artifacts/ui-audit/` (both gitignored).

## Automated signal (277 Chromium captures)
0 template placeholders, 0 uncaught page errors; horizontal overflow on 22
captures / 5 routes; fixed-bottom occlusion on 152 captures / 15 routes; console
errors on 22 captures / 4 routes; 268 `failed_requests` captures that largely
reflect the isolated audit factory blocking external CDN/analytics requests
(triage against the allowlist before treating as defects).
