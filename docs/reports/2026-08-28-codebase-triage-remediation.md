# 2026-08-28 Codebase Triage Remediation

## Outcome

The actionable findings in `NEEDED_FIXES_2026-08-14.md` and
`docs/TRIAGE_2026-08-28.md` were reproduced or re-verified against
`origin/main` (`a6d6b2e`). F1-F11 are covered by regression tests and fixed.
The 2026-08-14 structural god-module note remains an accepted refactoring
observation because it is not a discrete correctness or security defect.

## 2026-08-14 disposition

| Finding | Disposition | Evidence |
| --- | --- | --- |
| 1. Weekly water could be completed by same-day toggles | Verified already fixed | Five `test_training_routes.py` water regressions pass, including zero-toggle replay and genuinely-new-day behavior. |
| 2. `/chat` and global request bodies lacked caps | Verified already fixed | Chat rejects over-cap input, accepts the boundary, and the global body ceiling/JSON 413 tests pass. |
| 3. Deferred badge flush could poison the outer transaction | Verified already fixed | Badge deduplication and nested-savepoint rollback tests pass. |
| 4. Ungated model slots could consume the thread reserve | Verified already fixed; telemetry strengthened by F8 | Model-slot deadline/capacity regressions pass; runtime reserve now also counts excess model activity. |
| 5. Mobile refresh held a row lock during Cognito I/O | Verified already fixed | `test_refresh_provider_renewal_runs_without_database_transaction` passes. |
| 6. ProxyFix trusted forwarded host/port | Verified already fixed | `test_proxyfix_ignores_forwarded_host_and_port_but_trusts_for_and_proto` passes. |
| 7. Coach turns lacked a shared deadline | Verified already fixed | Nine OpenAI/Bedrock deadline and timeout regressions pass. |
| 8. Large service/blueprint modules | Accepted as documented | Structural refactoring is intentionally outside this defect-remediation change. |

Fresh verification for this matrix: **30 passed, 302 deselected**.

## 2026-08-28 disposition

| ID | Disposition | Implementation and regression coverage |
| --- | --- | --- |
| F1 | Fixed | `app/cli.py` declares manual cleanup for nonstandard user FKs; `test_purge_user_covers_every_foreign_key_to_user` enumerates mapper metadata rather than column names alone. |
| F2 | Fixed | `app/db_init.py` stamps/upgrades a fresh schema before seed writes. DB-init tests prove upgrade sees zero quest rows and failed upgrade commits no seeds. |
| F3 | Fixed | Daily purges are dispatched through RQ or a daemon fallback, never inline on the request thread. Jobs/hooks tests cover queue-less dispatch and failure isolation. |
| F4 | Fixed | `app/services/ai_stream.py` accumulates usage across every Bedrock tool-loop call. Stream tests assert summed prompt/completion tokens. |
| F5 | Fixed | Worker-less summarization runs in generator finalization after the terminal `done` frame, including immediate `close()`, and runs once. |
| F6 | Fixed | Full names reject Unicode control/invisible categories while preserving legitimate punctuation and Unicode. Profile route regressions cover NUL rejection and `O'Connor & Sons <TR>`. |
| F7 | Fixed | FatSecret reachability is sampled in background maintenance/worker paths and cached with a bounded timestamp/TTL. Deep health performs no outbound HTTP and reports absent/stale samples as non-gating `unknown`. |
| F8 | Fixed | `ThreadReserve` subtracts `max(0, model_active - ai_active)` so out-of-route model work cannot inflate reserve. |
| F9 | Fixed | Hydration nudges require at least three tracked days and average over observed rows; sparse and adequate tracked-day cases are covered. |
| F10 | Fixed | Best-effort plan-confirmation cleanup remains non-fatal but logs failures at DEBUG with traceback context. |
| F11 | Fixed | Premium plan quota refunds only HTTP failures (`>= 400`); the status matrix covers 200, 201, 204, 302, 400, and 500. |

The source report's accepted-tradeoff section remains unchanged: single-worker
in-process concurrency, fail-open migration policy, Redis-less local fallbacks,
and informational worker/FatSecret health remain explicit operational contracts.

## Verification

| Check | Result |
| --- | --- |
| Touched subsystem suite | **290 passed** |
| 2026-08-14 focused regression matrix | **30 passed, 302 deselected** |
| `python -m compileall -q app tests` | Passed |
| Full `python -m pytest -q --basetemp=.pytest-full-final` | **5,105 passed, 12 skipped, 3 deselected** |
| `git diff --check origin/main...HEAD` | Passed |

The touched suite covered cascade deletion, database initialization, hooks/jobs,
AI streaming/pipeline, profiles, health/extensions, AI capacity, hydration
analytics, plan confirmation, and premium quota behavior. Deprecation warnings
for naive `datetime.utcnow()` calls remain pre-existing cleanup debt and did not
fail verification.

The first unrestricted full-suite attempt reached all test bodies but reported
95 setup errors after Windows denied pytest access to its shared user temp root
(`AppData/Local/Temp/pytest-of-yusuf`). Every affected file then passed in an
isolated worktree temp root (**575 passed**), and the complete isolated-temp run
above passed. The setup errors were therefore environmental rather than code
regressions.
