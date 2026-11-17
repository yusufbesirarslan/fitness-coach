# Workout sessions: production activation record (2026-11-17)

This file is a record, not a runbook. Nothing in this repository activates a flag
(ROLLOUT.md §0). The production host `.env` change happens outside the repository,
under a separate founder GO. This commit only gives the next protected blue/green
deployment a reviewed, behaviour-neutral revision. That deployment starts the new
slot web and worker from the updated host `.env`.

## Why

The iOS 1.0.0 (101118) production device QA on 1931ccb4 (2026-11-17, 10:46–10:47Z)
could not start a workout, 2/2 attempts. The client showed its "no active workout"
state, which maps from `404 TRAINING_SESSION_NOT_FOUND`. A read-only production
readback (SSM command a34c9be2-aa4c-4a0d-86f4-04960a69b562, 11:13:45Z) showed
`FITX_WORKOUT_SESSIONS_ENABLED` unset in the host `.env`, in the green slot web and
in the worker. The native `/api/v1/training/workout-sessions*` contracts are
therefore absent (feature_flags.py, mobile_workout_sessions.py `_disabled`).

## Scope

- Only `FITX_WORKOUT_SESSIONS_ENABLED` changes, from unset to on, in the production host `.env`.
- These stay unset:
  - `FITX_TRAINING_EXECUTION_CONTEXT_ENABLED` and `FITX_TRAINING_INSIGHTS_ENABLED`, so contract v2 answers 404 and 101118 falls back to v1;
  - every AI allowance, Apple and AI activation flag.
- Prerequisites:
  - migrations `a994f9bed783` and `f5a6b7c8d9e0` are ancestors of the applied head `ad17de1e0001`;
  - `MOBILE_AUTH_ENABLED=1` and `RUNTIME_METRICS_ENABLED=1` were confirmed by the same readback.
- WORKOUT_SESSION_ACTIVATION.md stays staging-only and is not used. ROLLOUT.md's
  `docker compose up -d` is not used, because it is unsafe on the R6 slot topology.

## Rollback

1. Remove the `.env` line with the reviewed one-key writer (rollback mode).
2. Run the next protected deployment, which starts the processes with the flag unset.

Sessions already persisted are ignored, not deleted. Migrations are expand-only and are not rolled back.
