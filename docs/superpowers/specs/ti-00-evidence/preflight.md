# TI-00 preflight evidence

Recorded before fetch on 2026-10-05. Initial original worktrees were clean.
The initial status/SHA/divergence output was captured during discovery; logs below
are reproduced from the recorded immutable commit objects for durable evidence.
No changes to either original branch were made.

## fitness-coach

Initial command output:

```text
On branch training/unresolved-pr-a
Your branch is up to date with origin/training/unresolved-pr-a.
nothing to commit, working tree clean
branch: training/unresolved-pr-a
HEAD: f5687978f9b0a2bc4be49df11d48b19d0f2402c8
origin/main: 1a6ae1d871f734948623441c83de0afe6c4ee019
origin/main...HEAD: 8	1
f568797 test(training): pin unresolved exercise contract and bounded diagnostics
98164da test(training): pin completion-rejection contract + bounded log (LP-13 PR3) (#380)
d1252df feat(account): add native profile read for edit prefill (#376)
f216188 fix(account): close registration resurrection race (#377)
d864009 test(training): wait for replacement refusal state (#375)
5101971 fix: resolve 2026-10-01 triage findings (#374)
e17ed7d feat(nutrition): close native Nutrition API contracts (#373)
56bcf18 feat(nutrition): add bounded day view and Coach handoff (#371)
886ea69 feat(account): add authenticated mobile deletion lifecycle (#372)
2815605 feat(coach): add mobile messages and history API (#370)
aa81c1f feat(ux): make Nutrition Plan explicit and truthful (#368)
f99272a fix: resolve PR #367 correctness and reliability findings (#369)
e10abf9 docs(staging): correct email boundary and record LP-07 cleanup (#366)
6ab28a1 feat(ux): converge Nutrition logging behind Log food (#365)
606e16d feat(progress): expose canonical mobile progress summary (#364)
```

Fetch: `git fetch origin --prune`, exit 0.

Post-fetch HEAD: `f5687978f9b0a2bc4be49df11d48b19d0f2402c8`; origin/main: `1a6ae1d871f734948623441c83de0afe6c4ee019`; divergence `8	1`.

```text
1a6ae1d fix(workout): serialize same-day completion and session start (LP-13 P2) (#387)
48c7812 feat(training): server-validated catalog identity for generated exercises (#382)
000412c fix(training): fail closed on unverified workout completion proof (LP-13 P2) (#386)
c618c0f fix(workout): refuse a new session after same-day completion (LP-13 P2) (#385)
1132aca security: isolate AI provider budgets per user and feature (#378)
3a5e00f Fix PR383 triage findings (#384)
2afcb0b SEC-001: least-privilege runtime IAM — cutover complete (record) (#379)
2b50280 test(training): pin unresolved exercise contract and bounded diagnostics (#381)
98164da test(training): pin completion-rejection contract + bounded log (LP-13 PR3) (#380)
d1252df feat(account): add native profile read for edit prefill (#376)
f216188 fix(account): close registration resurrection race (#377)
d864009 test(training): wait for replacement refusal state (#375)
5101971 fix: resolve 2026-10-01 triage findings (#374)
e17ed7d feat(nutrition): close native Nutrition API contracts (#373)
56bcf18 feat(nutrition): add bounded day view and Coach handoff (#371)
```

## axisai-mobile

Initial command output:

```text
On branch lp14-mobile-pr2-english-coherence
Your branch is up to date with origin/lp14-mobile-pr2-english-coherence.
nothing to commit, working tree clean
branch: lp14-mobile-pr2-english-coherence
HEAD: 17a6d571b088e12c1ccc09823b3fe30f31f2ad9d
origin/main: c1576419949b91e3368d6295e1453a3cdd1082c0
origin/main...HEAD: 0	1
17a6d57 fix(training): preserve English mobile presentation (LP-14 PR2)
c157641 fix(workout): keep typed reps/weight when a set is toggled (LP-13 P2) (#42)
3dcba79 fix(workout): gate Start to today's workout and keep refusal semantics (LP-13 PR2) (#41)
b071582 fix(workout): keep the session ACTIVE after a completion refusal (LP-13 PR1) (#40)
7a7ccfa fix(pump-check): delete the picker's copy once a pick is held (LP-12 P2) (#39)
507204b feat(settings): delete the account from Settings (LP-12 PR2) (#38)
1d79ba0 feat(settings): add Settings account destination and profile editing (#37)
3ce33ff feat(nutrition): converge Flutter Nutrition on native contracts (#36)
c560d86 fix(onboarding): dismiss keyboard on background tap (#35)
a34561b feat(onboarding): complete first-plan activation flow (#34)
0ed64c0 fix(coach): allow keyboard dismissal on conversation screen (#33)
38fad28 feat(coach): add native Coach experience (#31)
d0e824f fix(navigation): preserve Pump Check internal back stack (#32)
be8204b fix(navigation): restore iOS back stack for Pump Check (#30)
7d1834f feat(progress): wire canonical live mobile summary (#29)
```

Fetch: `git fetch origin --prune`, exit 0.

Post-fetch HEAD: `17a6d571b088e12c1ccc09823b3fe30f31f2ad9d`; origin/main: `be7c65b5afffb99d9dae06e7c0d4ab9d8960829b`; divergence `1	1`.

```text
be7c65b fix(training): preserve English mobile presentation (LP-14 PR2) (#43)
c157641 fix(workout): keep typed reps/weight when a set is toggled (LP-13 P2) (#42)
3dcba79 fix(workout): gate Start to today's workout and keep refusal semantics (LP-13 PR2) (#41)
b071582 fix(workout): keep the session ACTIVE after a completion refusal (LP-13 PR1) (#40)
7a7ccfa fix(pump-check): delete the picker's copy once a pick is held (LP-12 P2) (#39)
507204b feat(settings): delete the account from Settings (LP-12 PR2) (#38)
1d79ba0 feat(settings): add Settings account destination and profile editing (#37)
3ce33ff feat(nutrition): converge Flutter Nutrition on native contracts (#36)
c560d86 fix(onboarding): dismiss keyboard on background tap (#35)
a34561b feat(onboarding): complete first-plan activation flow (#34)
0ed64c0 fix(coach): allow keyboard dismissal on conversation screen (#33)
38fad28 feat(coach): add native Coach experience (#31)
d0e824f fix(navigation): preserve Pump Check internal back stack (#32)
be8204b fix(navigation): restore iOS back stack for Pump Check (#30)
7d1834f feat(progress): wire canonical live mobile summary (#29)
```

Backend fetch: no reported changes. Mobile fetch: main advanced c157641→be7c65b;
several merged remote branches were pruned, including the current LP14 remote
branch. The local feature branch was retained.

Inspection worktrees: backend `/Users/yusuf/develop/fitness-coach-ti00` on
`docs/ti-00-contract-freeze`; mobile `/Users/yusuf/develop/axisai-mobile-ti00` detached
at fetched main. Neither repository has a tracked AGENTS.md; user-provided AWS
access rules apply. No AWS tools were invoked.

Final pre-commit fetch recheck: both origins unchanged at the recorded main SHAs;
inspection worktrees remained `0 0` versus main before committing TI-00.
