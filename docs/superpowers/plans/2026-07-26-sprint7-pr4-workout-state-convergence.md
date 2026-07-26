# Sprint 7 PR4 — Workout State Convergence Implementation Plan

Implement one coherent `/training/bootstrap` snapshot from one user/date/flag/
plan context; share its pure workout-state envelope with status and Progress; and
converge Training, barcode, and Coach onto PR1/PR2/PR3 authorities.

- Keep PR1 state resolution, PR2 completion, and PR3 session lifecycle as the
  only authorities. Add no migration, model, or feature flag.
- Fail bootstrap closed and expose only a bounded server-selected `today_plan`.
- Use ordered Training refreshes, v1 legacy behavior, v2 lifecycle calls, a
  visible single-flight 60-second heartbeat, and no durable browser workout truth.
- Preserve Progress history while adding `current`; use canonical completion in
  barcode and one current-state projection in Coach.
- Verify backend contracts, deterministic reversed-response/fake-clock client
  tests, existing frontend suites, and available PostgreSQL/browser gates.

Local implementation only: no push, PR, merge, deploy, production database, or
production feature-flag change.
