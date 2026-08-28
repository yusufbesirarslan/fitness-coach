# Codebase Triage Remediation Design

Date: 2026-08-28

## Purpose

Review the findings recorded by commits `e3643b8` (2026-08-14) and
`95630a5` (2026-08-28), reproduce each actionable defect against
`origin/main` at `a6d6b2e`, and fix confirmed defects without broad refactors or
breaking established deployment contracts.

## Scope and disposition

The 2026-08-14 report is primarily a regression audit. Its water-event dedup,
chat/request size caps, badge flush handling, bounded AI model-slot waits,
mobile refresh transaction split, proxy trust, and coach turn deadline must be
verified by focused tests. Existing fixes will not be rewritten when the
behavior is already protected.

The 2026-08-28 findings are handled as follows:

- F1: make account-erasure coverage enumerate every foreign key that targets
  the user table, including non-`user_id` columns. Explicitly identify manual
  and polymorphic cleanup paths so a new user reference cannot pass CI unseen.
- F2: on fresh databases, complete Alembic stamp and upgrade before any seed,
  backfill, or leaderboard work can commit. Add an ordering regression test and
  a guard for the fixed fresh-schema stamp contract.
- F3: retain the once-per-day NX scheduler lock, but dispatch maintenance
  purges outside the triggering request. Use the existing jobs layer and keep a
  safe worker-less fallback that does not delay the response.
- F4: sum prompt and completion token usage across every Bedrock tool-loop
  model call. Preserve `None` when no provider reports usage.
- F5: yield the terminal streaming `done` event before worker-less
  summarization. Run the deferred callback during stream/response close so it
  still executes when the route stops iteration immediately after `done`.
- F6: do not reject legitimate punctuation in human names. Add a narrowly
  scoped input guard only for control characters and verify cross-user HTML/JS
  rendering remains escaped.
- F7: remove the live FatSecret request from deep-health. Read a bounded-age
  cached reachability sample maintained by the existing capacity/background
  lane; an absent or stale sample is reported as unknown and remains
  non-gating.
- F8: include model-slot consumers that exceed active route gates when
  calculating the runtime thread-reserve gauge, matching
  `blocking_thread_ceiling()`.
- F9: avoid hydration false positives from sparse observations. A hydration
  nudge requires enough tracked days, and its average is calculated over the
  tracked observations; no-data and sparse-data cases stay silent.
- F10: keep confirmation cleanup best-effort but emit a debug log with
  exception context when rollback or expiration fails.
- F11: refund reserved AI-plan quota for HTTP failures (`>= 400`), not for all
  non-200 successful responses.

Accepted-tradeoff notes in the reports remain documentation-only unless a
reproduction contradicts the report.

## Architecture

Each finding is an independent, test-first change. Existing module boundaries
remain intact: CLI erasure metadata stays with `app/cli.py`, database boot
ordering stays in `app/db_init.py`, asynchronous maintenance stays in
`app/jobs`, AI usage and stream lifecycle stay in the AI service modules, and
analytics rules stay in `analytics_engine.py`.

No new infrastructure service is introduced. RQ remains the production worker
lane. Worker-less development uses a bounded daemon execution helper with its
own Flask application context and fresh database-session cleanup; it must never
hold up the triggering response or prevent process exit.

Deep-health becomes a read-only view over cached capacity state. The sampler
owns outbound reachability checks, records timestamp/status only (no secrets or
response bodies), and treats provider failure as an informational signal rather
than application unhealthiness.

## Data and error handling

Fresh-DB initialization remains fail-fast by default. If stamp or upgrade
fails, no application seed/backfill commit is allowed to occur first. Existing
`FITX_DB_UPGRADE_FAIL_OPEN=1` behavior remains explicit and logged.

Background maintenance is idempotent. Queue/enqueue failures are logged and
may use the worker-less fallback; purge failures roll back their own session and
must not affect the request that scheduled them.

Streaming callbacks are once-only. Normal completion, generator close, and
client disconnect must not run summarization twice. A callback failure is
logged and never alters an already emitted terminal frame.

## Testing

Every behavioral fix follows red-green-refactor with a focused regression test:

- mapper metadata fixtures prove nonstandard user foreign keys are detected;
- patched boot collaborators prove migration-before-seed ordering and that a
  failed upgrade prevents seed commits;
- maintenance tests prove the request hook schedules without executing purge
  work inline;
- multi-round synthetic Bedrock messages prove literal accumulated usage;
- stream lifecycle tests prove `done` is observed before summarization and the
  callback runs once on close;
- profile tests cover control characters and legitimate punctuation;
- health tests prove no request-time HTTP call and correct cached-state output;
- capacity tests cover excess model activity;
- analytics fixtures cover sparse, adequate, and genuinely low hydration;
- confirmation cleanup verifies debug logging without raising;
- quota-gate tests cover 200, 201, 204, redirect, and failure responses.

The existing regression tests covering the 2026-08-14 fixes will be run as a
separate focused group. Completion requires the complete default pytest suite,
plus repository lint/static checks discovered in CI configuration. Pre-existing
baseline failures are reported distinctly and are fixed only if their root
cause intersects this remediation.

## Non-goals

- Replacing the single-worker/process-local AI gate architecture.
- Splitting large service modules solely for line-count reduction.
- Changing accepted login-oracle, JWKS cooldown, or CSP tradeoffs.
- Introducing a new queue, scheduler, cache, or paid managed service.
- Rejecting apostrophes, ampersands, quotes, or non-ASCII letters in names.
