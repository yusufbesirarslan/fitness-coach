# F01/D1 source PR preparation — 12 November 2026

Task: `AXISAI-F01-D1-SOURCE-PR-PARALLEL`.
Founder decision: `GO_DRAFT_PR_PREPARATION_ONLY`.

This draft ports candidate `f1ebaf56fceeaaf9b52b0922adce92c9e5d29a62`
onto main `94a3f170bff544d68b3e7f656a92ba896b097b37`. It retains the
candidate's four application files without byte changes: `health_disclosure.py`,
`ai_consent.py`, `ai_consent_disclosures.py`, and `ai_launch.py`.
The approved F01 version, bilingual F01 hashes, Training D1 R1/R2 bodies,
recipient tuples, and contract digest remain those recorded in
[the original source approval](F01_TRAINING_SOURCE_CANDIDATE_APPROVAL.md).
Main's #506 qualification documentation and #508 FatSecret replay-retention
fixes are retained. No old main snapshot replaces current source.

## Boundaries

The approved source registrations are intentional candidate content; they are
not a production activation receipt. No production environment, consent/AI
runtime switch, feature-flag default, provider route, deployment file, schema,
purchase configuration, or teen pin is changed by this PR. Other D1 purposes
remain unregistered. Users still need fresh F01 and Training grants, canonical
cohort authority, finite allowance/execution and spend guards, and all existing
revocation, erasure and publication fences.

This task performs no AWS/staging access, real-provider acceptance, merge,
deployment, feature activation or App Store action. The active teen execution
checkout and QA-only `efdad102` tree are excluded. Local composition uses a
new disposable worktree and local PostgreSQL; identity and model SDK calls
are synthetic and Redis is emulated. Source tests do not renew or replace
the ongoing TR 13–17 real-Claude staging acceptance.

## Source integration dependencies

| Source | Pinned HEAD | Relationship |
|---|---|---|
| This draft | See PR exact HEAD and CI | Independent against current main; installs F01/adult cohort/Training registration |
| Backend #507 | `1b6f752f0f10747a6729f989f35fb8d0aabc4687` | TR teen onboarding; separate draft; notice approval stays `None` |
| Backend #509 | `ca833ec6086d08b13d89f8cbc3cf2f233fed998a` | Stacked on #507; separates AI from purchase eligibility and adds youth safety; teen AI approval stays `None` |

Combining this draft with #509's pinned history merges cleanly, including
the shared `ai_launch.py`: the adult cohort pin is retained alongside #509's
feature-specific AI eligibility checks. #507/#509 source, youth safety,
onboarding and test changes are not copied into or overwritten by this draft.
Their PostgreSQL fixture recognizes and reuses the installed exact Training
contract, avoiding a separate teen disclosure. The isolated composition also
runs the adult bilingual registration/replay/revoke/withdrawal journey.

Proposed source merge order, only after a separate founder decision:

1. This F01/D1 draft, after exact-HEAD hosted CI and independent review.
2. #507, refreshed against the resulting main and checked again; preserve its
   closed notice pin and paired mobile onboarding dependency (mobile #189).
3. #509, retargeted/refreshed after #507, preserving this draft's adult cohort
   pin and exact Training registry; requalify the combined source and leave
   its teen AI pin closed.

#507 can be integrated independently before this draft, but #509 depends on
#507 and needs this Training registration for the actual approved-text path.
Any changed HEAD invalidates exact-HEAD evidence until rechecked. Future
deployment/activation additionally depends on the separate staging acceptance
receipt, paired client source, final source/image/config binding, and explicit
operational approval. Source merge readiness does not authorize deployment;
main CI has a production deployment workflow and must remain under that
separate decision. No PR is merged by this task.

## Required verification

- Frozen source registration tests assert exact approved F01 and Training hashes.
- Candidate PostgreSQL journey checks both locales, actual consent/allowance
  authorities, replay, revoke, withdrawal, and no SDK call/hold when publication,
  Redis, cohort, recipient or allowance switches refuse execution.
- That journey is added to the existing hosted PostgreSQL concurrency job;
  all existing CI jobs and gates are retained.
- Isolated composition runs #507/#509 onboarding, closed teen-pin and youth
  boundary tests plus PostgreSQL teen and adult journeys. Positive teen source
  tests open pins only within fixtures.
- The draft PR and external evidence package record final exact HEAD, hosted
  run/jobs, local results and independent review; pending results are not passes.
