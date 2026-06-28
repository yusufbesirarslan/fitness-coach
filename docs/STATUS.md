# FitX — Triage & Hardening Tracker (living document)

**This is the single canonical tracker.** Add new triage findings and their status
*here* — do **not** create new `TRIAGE_<date>.md` files at the repo root (that sprawl
is what this document replaces). Historical reports are preserved under
[`docs/archive/`](archive/) and in git history.

Roadmap detail for the in-flight workstream lives in
[`updates-plan-2026-06-28.md`](updates-plan-2026-06-28.md).

Last updated: 2026-06-28.

---

## ✅ Resolved (recent)

| Item | Summary | Shipped in |
|------|---------|-----------|
| H1 | Diary ingest clamps macros via `clamp_serving_macros` | PR #99 |
| H2 | `/ask` caps question length (400 on oversize) | PR #99 |
| M1 | Display dates routed through `timeutil` (Istanbul) | PR #99 |
| M2 | `meal_history` capped by days, not 50 rows | PR #99 |
| M3 | One activity row per day (no intensity double-count) | PR #99 |
| M4 | Premium-aware weekly `/ask` chat quota | PR #99 |
| M5 | Silent LLM/JSON `except` paths now log `warning`+`exc_info` | PR #99 |
| M6 | MCP FatSecret token cache: lock across fetch + payload validation | PR #99 |
| M7 | Account-enum tradeoff documented (Cognito-gated + rate-limited) | PR #99 |
| M9 | Dev flag evaluated once in `configure_app` | PR #99 |
| L1–L6 | NULL weight, TDEE-default guard, rejected-status hidden, print→logger, XFF→remote_addr | PR #99 |
| SEC1 | jsdelivr pinned + SRI; CSP narrowed to exact files | PR #100 |
| i18n CI gate | TR/EN key/placeholder parity test blocks PRs | PR #100 |
| A6 | `calculate_tdee` logs a warning on unknown activity (no longer silent) | already in tree |
| A5 | `_repair_truncated_json` handles mid-value truncation + validates output | this PR |
| D4 | Completion-marker `WorkoutLog` excluded from volume/count aggregation | this PR |

## 🔧 Open / backlog

| ID | Summary | Next action | Effort |
|----|---------|-------------|:------:|
| **I-M1** | Boot-time schema init (raw ALTERs + `create_all` + auto-upgrade in `db_init.py`) should move into proper Alembic migrations, gated behind a one-shot job | Dedicated PR; un-blocks the schema-drift CI guard (currently `continue-on-error`) | L |
| **D7** | `user_metadata` legacy `ALTER` is `JSONB`-only → invalid on SQLite (`db_init.py`) | Fold into **I-M1** (guard by dialect / proper migration) | M |
| **D4-mcp** | MCP server (`fitx_mcp/server.py`) computes its own workout totals and does **not** yet exclude the completion marker — app-side fixed this PR; MCP parity still open | Mirror the `WORKOUT_COMPLETION_MARKER` filter in the MCP SQL | S |

## 🅾️ Accepted tradeoffs (won't fix — documented)

| ID | Why accepted |
|----|--------------|
| M7 | `UserNotConfirmedException` hint preserves the verify-redirect UX; Cognito-gated + rate-limited |
| M8 | Per-username lockout is a documented brute-force tradeoff |
| I-M2 | `drop_user_daily_nutrition` is a one-time, backfill-preceded data drop |
| L5 | Vision OCR output is bounded (4000 tok) + rate-limited; cost covered by H2/M4 |
| L7 | `style-src-attr 'unsafe-inline'` (dynamic bars) + GA wildcard host (Google's official CSP guidance) |

## 📁 Archive

Full historical reports: [`docs/archive/`](archive/) — `FIXES.md`, `TRIAGE.md`,
`TRIAGE_FIXES.md`, `TRIAGE_2026-06-23/24/26/28.md`, and the earlier 2026-06-17 docs.
They are point-in-time snapshots; treat **this file** as the current truth.
