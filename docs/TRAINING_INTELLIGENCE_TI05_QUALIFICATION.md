# TI-05 — Training Intelligence qualification and release readiness

Date: 2026-10-06. TI-05B current verdict: **QUALIFICATION BLOCKED**. Release recommendation: **HOLD P1**.

The original TI-05 finding and evidence below are preserved as history. The appended
TI-05B record supersedes its privacy verdict; native release gates remain pending.

This record qualifies frozen production sources with local endpoint integration,
real PostgreSQL, Flutter integration/widget tests, architecture guards, a
cross-repository golden payload, and deliberate mutations. It does not claim
physical-device or native simulator qualification, or a single live phone-to-server
end-to-end run. No production implementation, flags, deployment, AWS resource,
live database, or release-channel configuration changed.

## Frozen baselines

| Authority | Exact commit |
| --- | --- |
| Backend main / TI-03 merge, PR #398 | `8983968ad351595cb81e6fad7c73f4c3dffc6ce0` |
| Mobile main / TI-04 merge, PR #55 | `cab3b3f208e98f59837ffa545fd797e350d9a994` |

Both isolated `test/ti05-qualification` worktrees began clean. Before running tests,
`git status --short`, `git rev-parse HEAD`, and `git log -1 --oneline` were recorded
in both. The existing primary worktrees were on unrelated branches and were not
qualified or modified. Qualification additions are only docs, fixtures, and tests.

## Release blocker and bounded repair recommendation

**TI05-PRIVACY-01 — REPAIR REQUIRED; release-blocking privacy requirement violation.**
The actual native start → V2 checkpoint → complete flow emits shared operational
logs containing `user_id`. Locations:

- `app/services/workout_session/service.py:438`, `_log`: `[WORKOUT_SESSION] ... user_id=%s`.
- `app/services/workout_completion/service.py:288`, `_log`: `[WORKOUT_COMPLETION] ... user_id=%s`.

This predates TI-03 and is not an insight-projection leak, but the integrated chain
fails the explicit TI-05 privacy gate. The TI-01A record also says user identities
are not logged. Do not waive the finding because insight GET itself is safe.
No cross-account UI leakage was reproduced; no raw checkpoint, note text, or
insight payload appeared in these events. The defect is identifier exposure in logs.

**TI-06 recommendation:** remove user identity from those two fixed operational log
formats; keep fixed event/outcome/entry and existing request correlation. Add a
privacy assertion over actual native execution/completion and retain completion
reliability tests. Do not change contracts, checkpoint logic, provider behavior,
or product surfaces. Re-run the privacy gate without xfail and then qualification.
No production repair was implemented inside TI-05.

`tests/qualification/test_ti05_privacy_gate.py` records this as strict XFAIL so a
qualification-only PR can be reviewed without disguising the finding. XFAIL is
**not a passing release gate**. Running with `--runxfail` reproduces **1 failed**
in 3.02s with **0 external network attempts**. A repair produces strict XPASS until
the marker is explicitly removed.

## Contract audit

Backend models/projection are authoritative. The byte-identical
`ti05_contract.json` is checked against backend constants and mobile closed enums.
Numeric metric boundaries are exercised through the actual mobile DTO, including
rejection at maximum + 1. The golden JSON is also byte-identical across repositories.
There is no operational cross-repository dependency.

| Contract item | Backend | Mobile | Result |
| --- | --- | --- | --- |
| Contract / ruleset | `1` / `ti_rules_v1` | same | MATCH |
| States | available, insufficient_data, not_comparable | same closed enum | MATCH |
| Kinds | performance_declined, performance_improved, performance_stable, execution_quality_context_changed, effort_increased, effort_decreased, effort_stable, effort_mixed, volume_increase_context, exposure_frequency_context, insufficient_comparable_history | same closed enum; null kind for not_comparable | MATCH |
| Reserved kind | short_rest_confound unreachable | rejected | MATCH |
| Missing codes | missing_prescription, missing_execution, missing_rir, missing_tempo, rest_method_unsupported, insufficient_pairs, plan_changed, mixed_performance, incomplete_coverage, session_not_completed, history_unavailable, no_completed_sets | same 12 closed values | MATCH |
| Metrics / units | reps/int, weight_kg/number, rir/token, tempo/token, logging_interval_seconds/int, set_count/int, frequency_days/int | same metric union; DTO unit checked | MATCH |
| Numeric maxima | reps 20000; kg 1000; interval 3600; sets 12800; frequency 56 | same DTO bounds | MATCH |
| RIR | 0, 1, 2, 3, 4_plus | same P0 and P1 enums | MATCH |
| Tempo | as_prescribed, faster, slower, lost_control | same P0 and P1 enums | MATCH |
| Action tuples | tempo/follow_prescribed_tempo; effort/aim_prescribed_effort; current_approach/hold_current_approach | same closed tuples | MATCH |
| Observe | next_comparable_session | same | MATCH |
| Payload bounds | evidence ≤8; missing ≤12; paired sets ≤20 | same | MATCH |
| Checkpoint scope | exercises ≤32; sets/exercise ≤20 | same checkpoint bounds | MATCH |
| History | 56-day horizon; ≤8 recent sessions/exercise; ≤64 queried rows | exclusively server owned; no mobile history query | MATCH — authority boundary |
| Identity | owned opaque ref, canonical revision, catalog exercise ID | exact session/revision check; canonical ID-to-display resolution | MATCH |
| P0 context | optional actual_rir, tempo_adherence, actual_rest with completion_gap/foreground_contiguous | frozen codec; null, zero, clear, and 4_plus tested | MATCH |

Malformed cases rejected include unknown state/kind/missing code/metric, wrong
metric-unit pair, nine evidence entries, duplicate metric, invalid action tuple,
wrong title, missing/null/wrong-type exercise identity, empty reference, wrong
revision, extra keys, unsupported version and ruleset. Unknown canonical exercise
identity is hidden by the presentation owner, never fabricated or selected by name.
Opaque references are path encoded, not interpreted as exercise or training facts.

## Qualification matrix

PASS below means the named automated or architectural evidence passed. Manual
rows remain NOT RUN. Product scope exclusions are not evidence for manual gates.

| Dimension / scenario | Result | Evidence |
| --- | --- | --- |
| P0 OFF / P1 OFF | PASS | backend flag matrix, V1 lifecycle suites, mobile dark/composition and completion tests; no insight read/card |
| P0 ON / P1 OFF | PASS | V2 context persistence; separate P1 readiness false; mobile capability test; no inference from P0 |
| P0 ON / P1 ON | PASS, automated | native backend lifecycle + golden handoff + completed mobile surface |
| P0 OFF / P1 ON | PASS, fails closed | backend 404/capability false; mobile invalid readiness refused; mutation detected |
| Sessions OFF | PASS | route family absent regardless of P0/P1 |
| Local mobile switch OFF, backend ahead | PASS | composition contains no insight repository; completion read count zero; mutation detected |
| Backend ahead/missing P1 capability | PASS | failed/absent/malformed discovery clears readiness; V1 core remains usable |
| Unknown startup readiness | PASS | repository/controller readiness starts false; optional subject absent |
| V1 start/checkpoint/resume/complete | PASS | session/completion suites and mobile execution/coordinator suites |
| V1 historical completion | PASS | bounded missing prescription/execution; no backfill/current-plan reconstruction |
| V2 RIR/tempo/gap/null/zero/clear | PASS | TI-01A + TI-02 context/codec/coordinator tests |
| Dirty checkpoint → acknowledgement → terminal write | PASS | coordinator drain/barrier tests + real native endpoint lifecycle |
| Insight success/pending forever/network/503/malformed | PASS | completion UI remains terminal; Back usable; insight optional |
| Session/revision mismatch | PASS | live repository exact subject test; stale response unsupported |
| Account switch/logout during insight read | PASS | repository epoch, controller, completed surface races |
| Account switch with P0 draft/recovery/interval | PASS | TI-02 epoch/recovery/context suite; no inheritance by B |
| Background/foreground while active | PASS, automated | interval resets; no interval reconstructed across lifecycle gap |
| Background/foreground completed | PASS, automated | same subject one read; no durable insight authority |
| Navigation departure/retained branch/covering route | PASS | disposal and visibility fences; no re-entry resurrection |
| Capability OFF during pending or loaded read | PASS | generation wins; stale answer discarded/card cleared; mutation detected |
| Process restart | PASS, automated persistence boundary | canonical-server-first recovery tests; no unfinished interval anchor; insight has no disk-store dependency |
| Timeout/offline/reset/503/404 | PASS | typed transient/absent handling; service failure never insufficient_data |
| Available | PASS | real performance_improved; deterministic tests/fixtures cover declined, tempo/effort/exposure contexts |
| Insufficient_data | PASS | insufficient_pairs, prescription/execution/no-completed-set cases; neutral card |
| Not_comparable | PASS | plan_changed and mixed_performance; null kind and bounded explanation |
| Action null / frozen actions | PASS | no synthesis; exhaustive localized actions; null-action mutation detected |
| Server primary exercise authority | PASS | one projection, no mobile ranking/diagnostic/history dependency |
| Notes/Pump Check/provider independence | PASS | before/after note/Pump Check persistence tests, import/AST guards, network guard |
| Read determinism/idempotence | PASS | repeated semantic/byte equality; unchanged session columns/revision; no persisted insight entity |
| Istanbul midnight / Monday / Sunday / cross-date | PASS | facts tests and real PG Istanbul edge/window test; mismatched date excluded, never re-dated |
| Query/history truncation | PASS | exactly 2 SELECTs on PG; 64-row limit, incomplete_coverage, recent-session horizon |
| Screen reader / disclosure / long names | PASS, widgets | order, expanded/button semantics, all localized values |
| 320px / 2× text / action / full evidence union | PASS, widgets | card tests; no layout exception/overflow |
| Privacy: P1 metrics/payload/mobile analytics | PASS | fixed Event dimension; no insight analytics taxonomy or raw values |
| Privacy: full execution/completion logging | **FAIL** | TI05-PRIVACY-01, reproduced gate |
| Physical iPhone release candidate | **NOT RUN** | human interaction/build/environment evidence pending |
| Native simulator flow | **NOT RUN** | booted simulator available; repository has widget integration tests, no existing native integration_test/drive harness |
| Live phone ↔ controlled backend full chain | **NOT RUN** | automated backend and mobile connected by golden contract, not a live native transport run |

Current legacy prescription has no structured tempo/RIR target. Only lost_control
capture is available without a target; unsupported choices are refused. Structured
cue/phases prerequisites are covered by deterministic fixtures. The golden flow
leaves tempo optional/null; it does not invent a legacy target to enable an action.

## Golden flow and failure flow

Backend test `test_canonical_lifecycle_matches_cross_repo_golden` uses the actual
native routes, immutable lineage `ti03-e2e-lineage`, version 2, Thursday prescribed
squat/bench, Istanbul 2026-07-23 and 2026-07-30 at 15:00. Three squat sets at 60kg:
previous 8/8/8, current 9/9/8. V2 RIR is bucket 2; tempo null; sets 1/2 record 120s
completion gaps. Checkpoint If-Match 0 acknowledges revision 1 before completion
If-Match 1. The proof/provider boundary is a hermetic fixture; no AI call is made.

Canonical COMPLETED then yields performance_improved, reps 24 → 26, identical
load/RIR, logging interval 120 → 120, set/frequency context, missing_tempo and
rest_method_unsupported, action null. Only randomly generated owned references
are normalized to `ti05_current` / `ti05_previous` for the cross-repo JSON assertion.
No facts, versions, titles, revisions, units, values, or actions are normalized.

Mobile golden integration exercises protected repository → strict DTO → subject
owner/controller → localized card → evidence disclosure. The session ref/revision
match exactly; canonical display name renders; null action yields no recommendation;
logging interval never becomes rest; raw tokens remain absent; twenty repeated
show events produce no further read. Completion integration separately proves no
read during ACTIVE and one optional read after canonical completion.

Failure flow completes through the canonical coordinator, then subjects the live
insight repository to 503/network/malformed/unknown-kind response and a forever
pending read. Completed state remains final, Back works, raw errors stay hidden,
and there is no rollback. Explicit retry joins no concurrent retry and does not
change completion. **Insight can block completion: NO. Insight failure rolls back
completion: NO** (automated integration evidence).

## Authority and epistemic safety

| Boundary | Answer |
| --- | --- |
| completion_gap presented as physiological rest | NO |
| recovery/fatigue inferred | NO |
| e1RM computed | NO |
| Flutter diagnostics, ranking, action synthesis | NO |
| TrainingPlan / targets / sets / exercise mutation | NO |
| Exercise Notes consumed | NO |
| Pump Check consumed by deterministic insight | NO |
| LLM/provider required by deterministic insight | NO |
| Persisted Training Insight entity/cache | NO |

The existing workout-completion proof/photo path is separate. It was hermetically
stubbed, not qualified as a provider/photo integration in this lane. TI-03 needs
no Bedrock/Anthropic/OpenAI/Groq invocation. Architectural tests and deliberate
note/plan-writer mutations make these boundaries non-vacuous.

## Validation commands and exact results

Backend uses the existing Python 3.11 venv; `PYTHON` below denotes
`/Users/yusuf/develop/fitness-coach/.venv/bin/python`. Commands ran from the frozen
backend qualification worktree. No AWS CLI/SDK operation was performed.

```sh
$PYTHON -m pytest -p tests.qualification.ti05_network_guard -q \
  tests/test_ti01a_execution_context.py tests/test_ti01a_migration.py \
  tests/test_ti01b_exercise_notes.py tests/test_ti01b_migration.py \
  tests/test_ti03_api.py tests/test_ti03_facts.py tests/test_ti03_diagnostics.py \
  tests/test_ti03_architecture.py tests/test_workout_session.py \
  tests/test_workout_completion.py tests/test_mobile_workout_sessions_api.py \
  tests/test_mobile_workout_sessions_architecture.py \
  tests/test_sprint14_workout_execution_contract.py tests/test_sprint14_activation_readiness.py
```

**641 passed**, 310.99s. **0 unexpected external network attempts**.

Disposable local PostgreSQL 16, loopback port 55405, database `ti05_qualification`;
not an application or live database. The test URL contains only disposable local
fixture credentials. `FITX_PG_CONCURRENCY_TEST=1` and `PG_TEST_DATABASE_URL` set.

```sh
$PYTHON -m pytest -p tests.qualification.ti05_network_guard -q \
  tests/test_ti01a_execution_context_pg.py tests/test_ti01b_exercise_notes_pg.py \
  tests/test_ti03_pg.py tests/test_mobile_workout_sessions_pg.py \
  tests/test_workout_session_pg.py tests/test_workout_completion_pg.py \
  tests/test_sprint14_workout_execution_reliability_pg.py
$PYTHON -m pytest -p tests.qualification.ti05_network_guard -q -s \
  tests/test_ti05_qualification.py tests/qualification/test_ti05_privacy_gate.py
$PYTHON -m pytest -p tests.qualification.ti05_network_guard -q --runxfail \
  tests/qualification/test_ti05_privacy_gate.py
```

PG: **43 passed**, 212.49s, no skips, **0 external attempts**.
Qualification additions: **4 passed, 1 xfailed**, 1.74s, **0 external attempts**.
Explicit privacy release gate: **1 failed**, 3.02s; an expected, unwaived finding.

Backend full baseline automated authority:
[exact-main CI run 37434045230](https://github.com/yusufbesirarslan/fitness-coach/actions/runs/37434045230).
`python -m pytest -q`: **10,720 passed, 96 skipped, 8 deselected, 14 warnings**, 2994.62s.
All five CI jobs succeeded, including PostgreSQL concurrency, schema drift, Linux
locks and image revision immutability. This is baseline authority, not CI evidence
for later qualification-only additions. No local full backend rerun was performed.
Any qualification PR needs its own exact-head required CI before merge; baseline
CI must not be substituted for that check. No production release is approved here.

Mobile commands from its frozen qualification worktree:

```sh
flutter pub get --offline
flutter test --no-pub test/features/workout test/features/auth test/app \
  test/architecture test/core/config \
  --file-reporter json:/private/tmp/ti05-mobile-focused.json
flutter test --no-pub --file-reporter json:/private/tmp/ti05-mobile-full.json
flutter analyze --no-pub
dart format --output=none --set-exit-if-changed .
git diff --check
git status --short
```

Focused: **1,613 passed**, 335s. Final full suite: **3,410 passed**, 100.434s, success.
Analysis: **No issues found**, 8.8s. Formatting: **604 files, 0 changed**, 1.74s.
Initial sandbox Flutter attempt could not bind local runner sockets; discarded as
an environment failure and re-run with approved local execution. It is not a
Training Intelligence failure. The first golden fixture expectation omitted two
server-emitted exposure rows; the fixture was corrected to actual canonical output
before final qualification. Neither case required production changes.

## Performance and query bounds

Representative local insight GET (20 repeated endpoint reads with stable state):
median **0.95ms**, max **2.44ms**. All 20 responses semantically identical.
Real PostgreSQL full projection asserts exactly **SELECT, SELECT**; no write or N+1.
Tests cover 56 days, ≤8 recent sessions per exercise, ≤64 queried rows, 32 exercises,
20 sets each; limit coverage is incomplete_coverage. Worst-case fixture output <4KB.

Full-suite Flutter timing for actual golden parser/vocabulary test: **44ms**;
golden live-repository/controller/card/disclosure test: **628ms**. These include
test/pump overhead and are not native-device frame/latency benchmarks. No pathological
local behavior was observed. No polling, history prefetch or automatic retry loop
exists; completed foreground/rebuild/retained-route tests pin bounded read ownership.

## Non-vacuity

All mutations ran sequentially in disposable copies, never in qualification
worktrees. Every mutation produced an assertion failure rather than a compilation
failure. Original bytes were restored in `finally` and SHA-256 compared. Final
comparison also verified all **311 backend app/** and **337 mobile lib/** tracked
files in copies equal their frozen sources.

| Deliberate mutation | Detecting gate | Result |
| --- | --- | --- |
| P1 advertised/visible without P0 | invalid capability readiness | DETECTED |
| await optional read before completion | forever-pending completion integration | DETECTED |
| accept A response after switch to B | controller account-epoch race | DETECTED |
| logging interval mapped to Rest time | actual presentation-label test | DETECTED |
| accept unknown backend kind | DTO closed-kind rejection | DETECTED |
| synthesize action when null | card null-action assertion | DETECTED |
| note text changes insight title | persisted-note before/after equality | DETECTED |
| plan writer import reachable | package architecture dependency guard | DETECTED |
| ignore local release switch | production composition switch matrix | DETECTED |
| retain stale loaded card when capability off | integrated loaded-card invalidation | DETECTED |

Machine summary: `docs/evidence/ti05/mutations.json`. Portable runner:
`tests/qualification/run_ti05_mutations.py`; pass disposable copies without `.git`,
`--mobile-copy`, `--backend-copy`, `--output`, `--flutter`, and `--python`.
Copies need their own offline-resolved mobile dependencies. Large raw logs remain
outside Git; no personal/device identifiers are retained in qualification artifacts.

## Privacy, observability, hermeticity

P1 metric `TrainingInsight` has only fixed `Event`: insight_generated,
insight_insufficient, insight_not_comparable, insight_unavailable, history_row_excluded.
Session lifecycle metric includes context_accepted, checkpointed, completed,
revision_conflict with only fixed Event. Mobile analytics retains its existing
closed UI taxonomy; no card_shown event is equated to backend generation.

Operators should watch context_accepted, checkpointed/revision_conflict, completed,
and all existing insight events. Dedicated RIR-provided/skipped and
logging-interval-observed/unavailable metrics are **not present** in the inspected
merged taxonomy; do not invent them or claim measured baselines. Use existing
signals and hold P1 when capture/completion/projection health is uncertain.

The process-level backend guard denies and counts external DNS/connect/connect_ex;
loopback permits only local test infrastructure. Focused, PG and qualification
runs all recorded **NONE** unexpected external attempts. Guard sees swallowed
attempts and forces a nonzero session result too. Explicit `guard_probe.py` self-check
passed three rejection assertions (DNS/connect/connect_ex), counted three intentional
reserved-target probes and forced process exit 1. No probe was dispatched. These
intentional probes are separate from the zero unexpected attempts in TI suites. TI-03 package also prohibits
provider/network imports. Flutter tests use scripted protected transports and
widget-test HTTP isolation; native external-network instrumentation was NOT RUN.

The previously reported non-TI Cognito auth/logout isolation issue is separate.
No non-TI full local network-guard audit was performed, so the green baseline CI
is not evidence that all backend tests are externally hermetic. TI-05 did not repair
Cognito/auth isolation or touch AWS credentials. Privacy failure is separately
listed above and is not an unexpected-network finding.

## Device and outstanding manual evidence

**Physical-device qualification: NOT RUN.** Paired iPhone 13 is available; OS/build
mode/backend environment/flag state and manual outcomes are **not recorded**.
No login, installation, private data inspection or live workout was performed.
Human device qualification was requested; no completed result was supplied.

Booted simulator: iPhone 18 Pro, iOS 27.0. Native simulator release-candidate flow:
**NOT RUN**. Existing deterministic widget integration flows did run, but must not
be labeled simulator or physical proof. Native harness creation/build execution
was not expanded after the privacy release blocker.

Before a ready verdict: repair privacy; complete controlled native simulator and
physical RC launch/login/start/logging/RIR/tempo/lifecycle/completion/insight/evidence/
Back/account checks. Record model, OS, build, backend environment and flags without
personal identifiers. Verify screen-reader order and large text on device, plus a
live controlled backend handoff. First-release historical re-entry is not promised.

## Release readiness checklist

| Question | YES/NO |
| --- | --- |
| Canonical execution trustworthy in automated qualification? | YES |
| V1 fallback intact? | YES |
| P0 V2 capture qualified in automated tests? | YES |
| Persistent notes isolated? | YES |
| Workout completion independent? | YES |
| TI-03 deterministic? | YES |
| TI-04 strict frozen consumer? | YES |
| Account isolation proven in automated races? | YES |
| Feature flags fail closed? | YES |
| Contract drift absent? | YES |
| No unsupported rest claim? | YES |
| No plan mutation? | YES |
| No LLM dependency in insight path? | YES |
| No unexpected external network in guarded TI qualification? | YES |
| Full-chain privacy gate passed? | **NO** |
| Physical device qualified? | **NO** |
| Native simulator flow qualified? | **NO** |
| Live native end-to-end transport qualified? | **NO** |
| Rollback path clear? | YES |

## Rollback and staged release recommendation

**HOLD P1.** No live change is authorized or performed by this record.
When operations are explicitly authorized, rollback order is:

1. P1 OFF: `FITX_TRAINING_INSIGHTS_ENABLED=false` and mobile local kill switch OFF.
2. Keep P0 ON / P1 OFF to retain rich capture when P0 health is acceptable.
3. Emergency P0 OFF / P1 OFF for V1 behavior. Keep session support as needed for
   canonical V1 execution; do not erase stored rich context or notes.

No destructive schema rollback, backfill, checkpoint rewrite, or data deletion.
Verify capability refresh suppresses the card and late responses remain fenced.

After repair and missing manual gates, proposed stages are code deployed dark;
P0 only with checkpoint/completion observation; small controlled P0+P1 rollout;
expand only while existing health signals remain healthy. No invented percentages.
Before P1: stable P0, checkpoint errors at accepted baseline, healthy completion,
correct readiness, healthy TI-03 projection, released TI-04 consumer, matching
contract, and no account leak. Hold P1 if any prerequisite is absent.

Abort/rollback on completion regression, checkpoint conflict spike, insight 5xx,
cross-account leakage, malformed contract, surface crash, P1 without P0,
unsupported rest claim, plan mutation, or privacy regression. Numerical thresholds
need existing owner/operator baselines; none are invented by qualification.

## Accepted product limitations and next step

No historical/Today re-entry, Progress archive, Coach handoff, physiological rest,
e1RM, autonomous plan mutation, or full physical setup/ROM evidence. Legacy sessions
may lack prescription/context. These are boundaries, not newly discovered defects.

TI-05 has found a release blocker; **qualification is not complete and Training
Intelligence is not approved for release**. Isolate TI-06 privacy repair, retain P1
OFF, and re-run the failing gate. Finish native/device qualification afterward.
Qualification PRs may be reviewed now; any new production repair belongs in a
bounded TI-06/TI-07 lane. Do not label automated qualification complete while this
gate fails or substitute baseline CI for candidate exact-head required checks.


## TI-05B — post-repair qualification (2026-10-06)

Current system verdict: **QUALIFICATION BLOCKED**. Recommendation: **HOLD P1**.
No production rollout, AWS operation, deployment, live database mutation, or
qualification-PR merge was performed. Missing authenticated simulator and physical
release-candidate flows remain required gates, not accepted product limitations.

### Baselines and reconciliation

| Baseline | Exact SHA |
| --- | --- |
| Backend main at discovery / TI-07 merge #403 | `0e560f93142434265be5814b345bceedd2654555` |
| Mobile main at discovery | `ed4fcb09988c486c4de724e4cf6bb4481911d94e` |
| TI-06 merge #402 | `b21f0309218479ccb51fd937478ff0e98a42e971` |
| Original backend #400 qualification head | `a1ac2f8bc6e78732404b172557393cc06b2ef253` |
| Original mobile #57 qualification head | `2f9f048b053c394c46c6feb48a214d947a27e409` |

The primary checkouts were clean but on unrelated feature branches, not main.
Remote main was fetched before qualification. Existing qualification worktrees
were clean and matched their open PR heads. Backend merged main to consume both
privacy repairs. Mobile merged main because #56/#58 touch workout draft weight
parsing/serialization, active completion surface, localization, router and app
composition. This is a correctness dependency, not a rebase for unrelated motion.
No production files were edited by qualification. PR diffs against the integrated
main baselines contain only qualification docs, fixtures, tests, and runners.
Final candidate SHA authority is the exact PR head/check rollup (a document cannot
embed its own final commit SHA). Freeze the pushed heads; do not move them for
unrelated main changes.

### Repair closure and integrated privacy

**TI05-PRIVACY-01: CLOSED.** TI-06 and TI-07 are merged and consumed.
The former strict XFAIL in `tests/qualification/test_ti05_privacy_gate.py` was
removed. Its ordinary runtime test now uses numeric owner `1987654399`, a
synthetic username/email/Cognito subject, and a real start → V2 checkpoint →
completion followed by a deliberately failing native Today workout-state read.
The exception message contains all four identities. All three log families must
be observed; every message, original logger argument, and captured record attribute
is checked for each actual identity value and the public session reference.
Exact full-message shapes reject replacement correlation fields, hashes, raw
values, payloads, and unbounded detail. Request `rid` is the sole correlation.
State detail is exactly `SyntheticOwnerReadError` in this integrated case; TI-07's
all-category runtime proof retains exception class or `-` for all categories.

Integrated workout privacy gate: **PASS**.

- WORKOUT_SESSION owner identity logged: **NO**.
- WORKOUT_COMPLETION owner identity logged: **NO**.
- WORKOUT_STATE owner identity logged: **NO**.
- Strict privacy XFAIL remaining: **NO**.
- Privacy qualification passes normally: **YES**.

Privacy gate plus TI-06/TI-07 runtime/source guards: **11 passed, 0 skipped,
0 failed**, 13.07s; **0 unexpected external network attempts**.
The broader guarded suite includes the same final gate.

### Final privacy non-vacuity

`tests/qualification/run_ti05b_privacy_mutations.py` operates only on a disposable
copy without `.git`. Six sequential mutations all caused an assertion failure
in the final integrated runtime gate: user_id in each of SESSION, COMPLETION,
STATE; relabelled `owner=`; unlabelled raw numeric identity; and `str(exc)` in place
of exception class. Each produced **1 failed**; no compile/loading error counted.
All mutated files were restored byte-for-byte in `finally` and their SHA-256
values checked. Summary: `docs/evidence/ti05b/privacy-mutations.json`. No production
mutation remains in either qualification worktree. The existing ten contract,
completion, authority, lifecycle and copy mutations are also repeated for TI-05B.

### Contract, rollout, execution, and safety

The full contract table above remains the contract audit; both manifest and golden
payload are byte-identical across repos after integration. Backend constant checks
and actual strict mobile DTO checks are rerun. Every row remains MATCH, including
contract 1 / ti_rules_v1, closed states/kinds/missing codes, metrics/units, categorical
4_plus, tempo/action/observe tokens, numeric maxima, payload/execution/history bounds.
The frozen endpoint remains GET /api/v1/training/workout-sessions/<session_reference>/training-insight.
Mobile owns no history query or timezone recomputation.

All four P0/P1 cases and the local switch are requalified by existing actual
readiness/composition/completion gates: OFF/OFF uses V1 with no insight; ON/OFF
persists V2 context with no insight; ON/ON enables the optional read; OFF/ON fails
closed. Backend P1 ON with mobile local switch OFF produces no repository/read/card.

Canonical sequence: dirty checkpoint drained → checkpoint acknowledged → canonical
completion accepted → completed screen usable → optional insight GET. Insight is
absent from the terminal barrier. Success, network error, 503, malformed success,
unsupported contract/ruleset and forever-pending reads leave canonical completion
successful. **Insight can block completion: NO. Insight failure rolls back
completion: NO.** Account switch/logout, capability OFF mid-read/loaded,
covered/retained routes, active/completed background/foreground and process recovery
retain existing fences. P0 recovers server-first; insight remains non-durable.

V1 start/set/checkpoint/resume/completion and V2 actual RIR/tempo/completion-gap
serialization/persistence pass. Null is not zero; 4_plus is categorical; explicit
clear stays cleared. Available, insufficient_data, not_comparable, transport failure,
malformed success and unsupported contract/ruleset remain typed safe states.

Completion_gap presented as physiological rest: **NO**. short_rest_confound from
completion_gap: **NO**. Fatigue/recovery/injury inferred: **NO**. e1RM: **NO**.
Logging interval retains “Between-set logging interval”; the Rest-time mutation
must fail. Flutter diagnostics/primary selection/recommendation creation: **NO**.
TrainingPlan mutation, Notes/Pump Check consumption, LLM/provider dependency,
raw history fetched by mobile: **NO**. Determinism and read-only session/revision/
plan/note/persisted-insight boundaries are requalified. Metrics retain only bounded
Event dimensions; no personal/session/exercise/payload dimensions were added.

### Backend validation and real PostgreSQL

Final guarded focused run: **752 passed, 0 skipped, 0 failed**, 87.93s.
Includes TI-00, TI-01A migrations/context, TI-01B boundary/migrations, TI-03 API/
facts/diagnostics/architecture, WorkoutSession/Completion/State/session-state,
mobile APIs/architecture, feature flags/readiness, TI-06, TI-07 and TI-05 gates.
Command retains `-p tests.qualification.ti05_network_guard`.

Disposable PostgreSQL 16, loopback port 55405, database `ti05b_qualification`:
**43 passed, 0 skipped, 0 failed**, 20.85s. Includes TI-01A/TI-01B/TI-03,
mobile/session/completion/concurrency reliability PG suites.
`test_full_projection_on_postgres_is_exact_isolated_and_two_statements` asserts
exactly **SELECT, SELECT** (target lookup + bounded history), no writes/N+1.
56-day horizon, ≤8 sessions/exercise, ≤64 queried rows, ≤32 exercises, ≤20 sets
and incomplete_coverage behavior remain pinned. Istanbul midnight, Monday/Sunday
and cross-date evidence remain in the rerun facts/API/PG suites.
Unexpected TI external calls: **NONE** in both runs. Separate non-TI Cognito
isolation remains outside this claim. Guard self-check intentionally blocks three
reserved-target probes and must force exit 1 despite three passed assertions.

### Exact-head CI authority

The old green PR heads are historical, not final repaired-candidate authority.
After the final qualification-only commit is pushed, accept backend #400 only
when all five checks are SUCCESS on that exact head: pytest; PostgreSQL concurrency;
schema-drift guard; authoritative Linux production locks; authoritative image
revision immutability. Accept mobile #57 only when classify, ubuntu and macos are
SUCCESS on its exact final head. A policy-classified macos no-op is not native
simulator flow evidence. Exact SHAs and job results are also recorded in the TI-05B
final response and PR descriptions. CI pending means automated qualification is
not complete. Do not merge either PR automatically.

### Native gates and release disposition

Simulator discovered: **iPhone 18 Pro, iOS 27.0, booted**.
Physical device discovered: **iPhone 13, iOS 26.6.1, Developer Mode enabled**.
No account/device identifiers are retained here. Authenticated happy and controlled
insight-failure flows are **NOT RUN** for both. Build/environment/P0/P1/local-switch
state and physical interaction outcomes are not yet qualified. No existing native
integration_test/drive harness exists. A controlled backend/test-account setup and
human physical interactions were requested; no private credentials were requested.
Widget integration and a simulator compile must never be labelled native RC flow.

Missing required native evidence: login/Today/Plan/start/sets/optional RIR/tempo,
background/foreground/resume, immediately usable completion, insight/name/evidence/
action when supplied/Back, controlled failure preserving completion, no duplicate/
stale card, long-name/large-text/disclosure/action layout and available semantics.
These must be recorded against the exact build/commit and controlled flags before
P1 approval. There is no newly discovered production defect in automated evidence.

Rollback remains: FITX_TRAINING_INSIGHTS_ENABLED=false plus
AXISAI_TRAINING_INSIGHTS_ENABLED=false in the mobile build; keep
FITX_TRAINING_EXECUTION_CONTEXT_ENABLED=true if P0 is healthy. Emergency both
server P0/P1 OFF and mobile insight switch OFF for V1 behavior. Do not delete rows,
notes, checkpoints or destructively roll back schema. Refresh capability and verify
late responses cannot revive cards.

**HOLD P1** until both native gates pass. After all gates: deploy dark OFF/OFF;
enable P0 while P1 OFF; observe capture/checkpoint/completion; then controlled
P0+P1; expand only with observed stability. No percentages or activation are assumed.
Accepted limitations remain only those in the original product-boundary section:
no historical/Today/Progress re-entry, Coach handoff, physiological rest, e1RM,
plan mutation, persistent insight cache; legacy context/prescription may be absent.
Future feature work belongs in a new scoped lane.


### TI-05B final local mobile results and main movement

Mobile focused: **1,694 passed, 0 skipped, 0 failed**, 145.985s.
Full: **3,500 passed, 0 skipped, 0 failed**, JSON reporter success.
Analysis: **No issues found**, 5.0s. Format: **617 files, 0 changed**, 4.32s.
Both `git diff --check` gates PASS. Ignored generated localization was regenerated
with `flutter gen-l10n` after integrating main; the initial stale-generated-output
attempt is excluded from valid qualification results. No production repair.
All ten existing mutations PASS detection; final summary is
`docs/evidence/ti05b/mutations.json`. All 311 backend app and 339 mobile lib tracked
files in disposable copies were verified equal to qualified sources after restoration.

Native simulator compile **PASS**, 125.2s: unsigned debug simulator binary,
native auth ON, local insight switch ON, reserved `https://api.example.invalid`
validation URL. Backend P0/P1 not connected/not exercised. This is not native
happy/failure flow proof. Physical installation/flows remain NOT RUN.

Backend main subsequently advanced to `01d3a711e2cae14fd6dd9adeedc2290e3474b272`
(#404, Cognito code-email language). Inspection shows no TI contract/lifecycle/
privacy code changes; the conftest change is an optional registration-stub language
argument unused by TI fixtures. Candidate stays on repaired integrated main
`0e560f93142434265be5814b345bceedd2654555`; no unrelated reintegration.
Final PR diffs use merge-base comparison and contain **no production code changes**.
The native gate still prevents P1 activation irrespective of CI results.
