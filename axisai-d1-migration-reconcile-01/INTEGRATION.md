# D1 current-main reconciliation

Original accepted HEAD: `1fd6fa9fe87fce910de82c1ab2ec7af560a19fc6`.
Refreshed main: `551f711b7c0d1dea24ddad7de6f598ed9df60c42`.
Merge base: `36d833b21aeef8f7adb204f8bac6ed5b29803b9b`.

One controlled merge preserves the original R2 commit identities. Six conflicts:

- `app/blueprints/mobile_api.py`: retain consent registration/error handling and both subscription/allowance imports.
- `tests/test_mobile_auth_feature_gate.py`: union of approved consent, subscription and allowance routes.
- `tests/test_migration_graph.py`: 51 revisions; D1 is the only head.
- `tests/test_lp18_b0_migration.py`, `tests/test_lp18_b1_migration.py`, `tests/test_mobile_workout_sessions_architecture.py`: retain D1 as final head after the merged main chain.

The model, config, CLI and workflow merges retain both authorities. Explicit consent erasure remains alongside reservation-before-bucket deletion. Memory stays OFF. Subscription and allowance rollout flags stay OFF, allowance policy remains unconfigured, and the disclosure registry remains empty. There is no new runtime API abstraction or enforcement wiring.

## Migration

Main: `b1a8c9d0e1f2 -> c2b9d0e1f2a3 -> a9c1d2e3f4b5`.
Original D1: `b1a8c9d0e1f2 -> d1c0a15e0001` (sibling of the merged chain).
Reconciled: `b1a8c9d0e1f2 -> c2b9d0e1f2a3 -> a9c1d2e3f4b5 -> d1c0a15e0001`.

Only D1's parent and explanatory header change. Its DDL, validation, constraints and evidence-preserving downgrade are unchanged. Every main migration, including Apple and allowance, is byte-for-byte preserved. The inspected draft PR and prior qualification record contain only disposable migration evidence and explicit no-deployment statements; no evidence of D1 adoption outside disposable databases was found. No external database was accessed or deployment state inferred.

## Reviewable regression evidence

`tests/test_ai_consent_pg.py` remains in the existing mandatory PostgreSQL CI job. Added cases run on PostgreSQL 16 and cover:

- Empty database through the full Alembic chain, one head, 51 reachable revisions, model/schema parity and no implicit user, subscription, allowance or consent rows.
- Real upgrade to main's `a9c1d2e3f4b5`, then synthetic users, subscriptions, account associations, allowance grants/buckets/reservations and training plans. Upgrade D1 and compare all pre-existing tables' rows, columns, PKs, FKs, unique constraints, checks and indexes. No stamp/create_all substitutes for migration ancestry.
- Alembic and D1 direct reentry; validated D1 checks; account cascade and FK rejection; populated evidence blocks downgrade.
- Name-only tautologies and NOT VALID replacements rejected for all three D1 CHECK constraints.
- Real paid entitlement plus fresh reserved allowance still denies final SDK dispatch for both recipients and both owners without consent. No provider is called, no consent rows are created, and Memory stays unavailable.

The accepted R2 authorization fixtures, explicit per-test ALLOW markers, assertions and shared fixture-audit helpers are preserved. Four reviewed modules are byte-identical; `test_pa06_privacy.py` only adds response closure and a capacity-restoration assertion to its existing first-party transport test. Hosted integration exposed two unclosed SSE test responses holding permits before the subscription capacity tests; the fix changes no runtime code or authorization seam. Prior independent R2 approval is reused, with regression tests only; it is not repeated as a new independent security review.

The local report at `/Users/yusuf/axisai-d1-migration-reconcile-01/FINAL_REPORT.md` records final SHA, commands, counts, hosted CI binding and handoff. The PR description links exact-head CI after completion.

Independent integration review remains required for ancestry, upgrade safety, preservation of merged migrations, conflict resolutions, default deny/PA06 and CI binding. Section E remains NOT_ASSESSED. No merge, deployment, consent/subscription activation, payment activation or public paid launch is authorized.
