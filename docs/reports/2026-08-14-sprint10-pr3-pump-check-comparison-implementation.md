# Sprint 10 PR3 — Pump Check Comparison Intelligence — Implementation Report

**Date:** 2026-08-14
**Branch:** `sprint10-pr3-pump-check-comparison-intelligence`
**Worktree:** `C:\Users\yusuf\develop\fitness-coach\.worktrees\sprint10-pr3-pump-check-comparison-intelligence`
**HEAD at verification:** `3b3e131`
**Delivery state:** local only — not pushed, no pull request, not merged, not deployed.

---

## Report structure — REVIEW CONDITION, READ FIRST

The committed plan's Task 9 Step 9 requires this report to carry **the 27
headings of `C:\Users\yusuf\OneDrive\Masaüstü\cf-sprint10-pr3.txt`, verbatim and
in order**.

**Those 27 headings could not be recovered.** That file was overwritten with an
"AUTHORITATIVE HANDOFF CHECKPOINT — RECONSTRUCT BEFORE CONTINUING" note (1,255
bytes) after a tool-usage limit interrupted the previous agent. It was re-opened
during Step 9 and contains only the checkpoint text; the original 27-section
contract is not present there, is not committed anywhere in this repository, and
is not quoted in the plan or the design spec.

The headings were therefore **not fabricated**. The sections below are an
explicit reconstruction covering every content item the plan's Step 9 enumerates
(normalization, provider payload, idempotency/races, migration/drift,
exclusions, no Flutter/PR4 work, `a69c958` separated from PR3 commits, and one
exact verdict). **A reviewer holding the original brief must re-check this
report against its real 27 headings before accepting the structure.** This is
the primary reason the verdict below is conditional rather than unconditional.

---

## 1. Scope and deliverable

Backend-only, owner-private, idempotent comparison of an **explicitly ordered
pair** of canonical Sprint 10 PR1 Pump Checks, interpreted with **one** bounded
two-image Bedrock call.

PR1 is untouched in contract terms: its routes, request/response payloads,
prompt, analysis schema, and `pump_check` columns are unchanged, and no
comparison field was added to `pump_check`. The only PR1-adjacent change is that
its single-image path now routes through the promoted shared image-preparation
utility (Task 1), with characterization tests fixing the existing behavior.

## 2. Commit inventory — `a69c958` is NOT PR3 work

**Preparatory test-harness fix, made before any PR3 production change:**

| Commit | Subject | Files |
|---|---|---|
| `a69c958` | test: isolate audit app database configuration | `scripts/frontend_audit/app.py`, `tests/test_frontend_audit_app.py` |

`a69c958` fixed a **deterministic pre-existing test-harness isolation defect
discovered during mandatory baseline validation before any Sprint 10 PR3
production changes**. `tests/test_frontend_audit_app.py` built an app whose
SQLAlchemy configuration leaked into the process, so
`tests/test_gamification_routes.py::test_leaderboard_orders_by_xp_then_streak`
failed or passed depending on test-file ordering within a session. The defect
existed at the branch point. It is a test-harness fix, must be reviewed as such,
and must not be attributed to the comparison feature.

**PR3 commits (18), oldest first:**

| Commit | Subject | Task |
|---|---|---|
| `ca0c929` | docs(api): design canonical pump check comparisons | design |
| `fe48ab2` | docs(api): plan pump check comparison implementation | plan |
| `a0b0e74` | refactor(ai): share bounded vision image preparation | 1 |
| `963fd1d` | fix(ai): enforce vision image byte ceiling | 1 |
| `0ad61d7` | feat(ai): add bounded pump check comparison analysis | 2 |
| `4f24b7e` | fix(ai): harden comparison safety and logging | 2 |
| `e62fc4a` | fix(ai): close comparison language bypasses | 2 |
| `a513dcf` | feat(db): add canonical pump check comparisons | 3 |
| `568068c` | fix(db): fail closed on comparison schema drift | 3 |
| `09123ca` | fix(db): verify reflected comparison types and check literals | 3 |
| `32cafc3` | feat(api): validate pump check comparison pairs | 4 |
| `2ee1675` | feat(api): orchestrate pump check comparison analysis | 5 |
| `e66e44c` | feat(api): expose owner-only pump check comparisons | 6 |
| `64b9af8` | fix(privacy): erase pump check comparison records | 7 |
| `05cbb1f` | test: cover pump comparison postgres races | 8 |
| `425e657` | fix(db): accept reflected comparison check SQL on postgres | 8/3 |
| `e3049ef` | test: separate comparison eligibility from idempotency races | 8 |
| `3b3e131` | test: register comparison route in ai gate scope | 8/6 |

The documentation commit produced by Task 9 Step 11 (`a54c0b5 docs(api):
document pump check comparisons`) lands after this table and is **docs-only** —
six files under `docs/`, zero production or test code.
`tests/test_mobile_pump_check_comparison_architecture.py` was staged with it per
the plan but was already complete and unmodified at that point (its
documentation guard shipped in `e66e44c`), so it contributed no change.

## 3. Files changed

36 files, +6,710 / −78 versus `origin/main`. Backend, tests, docs, and CI only.

**Created — production:** `app/services/vision_images.py`;
`app/services/mobile_pump_check_comparisons/{__init__,identity,analysis,service}.py`;
`app/blueprints/mobile_pump_check_comparisons.py`;
`migrations/versions/fa1b2c3d4e5f_add_pump_check_comparisons.py`.

**Modified — production:** `app/models.py` (two new authorities),
`app/blueprints/mobile_api.py` (route registration), `app/cli.py` (erasure
ordering), `app/services/ai.py` (two-image adapter only),
`app/services/menu_ocr.py` + `app/services/mobile_pump_checks/analysis.py`
(consume the promoted utility), `s3_helper.py` (one public wrapper,
`key_belongs_to_user`, so eligibility can check ownership **before** download
without a second copy of the key grammar).

**Created — tests:** `test_vision_images.py`,
`test_bedrock_comparison_adapter.py`, `test_pump_check_comparison_analysis.py`,
`test_pump_check_comparison_identity.py`,
`test_pump_check_comparison_migration.py`,
`test_pump_check_comparison_service.py`,
`test_pump_check_comparison_lifecycle.py`,
`test_mobile_pump_check_comparison_api.py`,
`test_mobile_pump_check_comparison_architecture.py`,
`test_mobile_pump_check_comparison_pg.py`.

**Modified — tests/CI:** `test_menu_ocr.py`, `test_pump_check_analysis.py`,
`test_cascade_delete.py`, `test_migration_graph.py`,
`test_mobile_auth_feature_gate.py`, `test_mobile_pump_check_architecture.py`,
`test_ai_gate.py`, `.github/workflows/ci.yml`.

## 4. Public contract

`POST /api/v1/pump-check-comparisons` and owner-only
`GET /api/v1/pump-check-comparisons/<comparison_id>`, both Bearer-authenticated
on the existing `mobile_api` blueprint — one `/api/v1` surface, one `no-store`,
one 429 shape, the same `MOBILE_AUTH_ENABLED` gate, the same approved-route
allow-list.

The create body is exactly `baseline_pump_check_id` and
`current_pump_check_id`; unknown fields, missing fields, non-strings, and
malformed tokens are rejected. 201 when this request produced the canonical
comparison, 200 on replay or convergence. The single serializer returns exactly
`id`, `baseline_pump_check_id`, `current_pump_check_id`, `status`,
`comparability`, `analysis`, `analysis_version`, `created_at`.

GET is **strictly read-only**: it never claims work, retries, repairs, or
touches S3 or Bedrock. Unknown, malformed, and another owner's token all return
the same private 404.

## 5. Directionality and deterministic eligibility

The pair is directional and is **never sorted** — `(A, B)` and `(B, A)` are
different comparisons. Seven rules run to completion **before any S3 read or
Bedrock call**, and a failure creates neither a comparison row nor a ledger row:

1. Both tokens resolve through owner-scoped canonical queries.
2. The two Pump Checks are distinct.
3. Both carry `captured_at` and `baseline.captured_at < current.captured_at`.
4. Both carry the same non-empty `body_region` (exact equality in v1).
5. Both are `completed` `pump-check-analysis/v1` rows whose stored analysis
   still parses, and are `valid`.
6. Both carry a private S3 key owned by the authenticated user
   (`s3_helper.key_belongs_to_user`).
7. Neither source analysis has `quality=insufficient`.

If either source is `quality=limited`, the pair proceeds but the result is
**capped at `limited`**; provider output claiming `comparable` over a limited
source is rejected as invalid output. This is the source-quality-laundering
mitigation, not a cosmetic downgrade.

`tests/test_mobile_pump_check_comparison_pg.py::test_reversed_pair_is_ineligible_before_idempotency_is_consulted`
proves rule 3 fires on its own merits even on a previously used key, so the
"same key, different command" race genuinely exercises the ledger rather than
tripping over chronology.

## 6. Image normalization and provider payload

Each provider image is prepared **in memory** by `app/services/vision_images.py`
to at most `1_500_000` bytes and a `1_600`-pixel longest edge, with the byte
ceiling enforced as a hard postcondition (`963fd1d`). The stored S3 object is
read but **never modified, replaced, or re-uploaded**.

The utility was promoted from `menu_ocr` rather than duplicated;
`_compress_image_for_vision` remains as a compatibility name, and
`_bedrock_validate_image`'s signature and the single-image message layout are
preserved byte-for-byte (`tests/test_bedrock_comparison_adapter.py`,
`tests/test_menu_ocr.py`, `tests/test_pump_check_analysis.py`).

Bedrock receives exactly: the two normalized images with an explicit
A-baseline/B-current label ordering, the body region, and bounded instructions.
**Stored PR1 narratives are used only as an eligibility signal and are never
forwarded**, so interpretations do not compound across versions.

Analysis version is `pump-check-comparison-analysis/v1`. Required provider keys
are `summary`, `observed_changes`, `stable_areas`, `focus_areas`, `limitations`,
`comparability_reasons`, `next_check_guidance`, and `comparability`.
`comparability` is promoted out of the JSON into its own column so there is
exactly one public authority for it. Beyond the PR1 validators, comparison text
also rejects progress scores, body-fat estimates, circumference deltas,
muscle-growth percentages, causal claims, and medical inference (`4f24b7e`,
`e62fc4a`). `not_comparable` is a legitimate **completed** answer, never an
error.

## 7. Idempotency, convergence, leases, and fencing

`PumpCheckComparison` owns the one canonical result for an owner, directional
pair, and version. `PumpCheckComparisonRequest` only maps owner-scoped
idempotency keys to that result — it is a ledger, not a second authority.

The semantic fingerprint is the ordered baseline token, ordered current token,
and comparison analysis version in domain
`axisai/mobile-pump-check-comparison-create/v1`; it is never sorted and never
logged. Reusing a key with a different fingerprint is 409.

Uniqueness on `(owner, baseline, current, analysis_version)` means two different
keys for the same directional pair **converge on one row and one model call**.
Work is claimed by atomically moving `pending`, a reclaimable `failed`, or an
**expired** `analyzing` lease (900 s) to `analyzing` with an incremented,
monotonic attempt generation. Finalization is conditional on owner, id, status,
**and attempt**, so a stale generation can never overwrite a newer result. An
unexpired lease held by another worker returns that canonical `analyzing`
representation with HTTP 200 — it is not an error.

**No transaction or row lock spans S3 or Bedrock I/O.** Convergence is decided
by database constraints, never by process-local locking.

**Accepted, documented artifact:** an idempotency conflict detected at
ledger-attach time can leave an orphan `pending` comparison row, because the
ledger's FK to `comparison_id` is NOT NULL and the comparison row must exist
before the ledger insert can be attempted. The pair unique constraint makes a
later legitimate request converge onto that same row, so there is **no duplicate
model spend and no duplicate canonical comparison**. Reaping orphan `pending`
rows is PR4 retention work.

## 8. Concurrency gate

The create route consumes the shared heavy-AI concurrency gate exactly like the
single pump-check route, because it makes a blocking Bedrock call and would
otherwise park a worker thread outside the thread-reserve arithmetic. It is
declared in `tests/test_ai_gate.py::EXPECTED_GATED_ENDPOINTS` as
`mobile_api.create_pump_check_comparison` (`3b3e131`), so
`test_gate_marker_set_only_on_heavy_ai_routes` keeps the registry and the code
in agreement. The GET route is **not** gated — it performs no provider I/O.

## 9. Privacy, erasure, and logging

Comparison IDs are owner-bound 144-bit URL-safe HMAC tokens in the distinct
domain `axisai/mobile-pump-check-comparison/id/v1`. Responses carry **no** image
URL, S3 key, internal integer ID, idempotency key, fingerprint, prompt, provider
response, model metadata, lease field, or failure internals.

Account erasure removes comparison rows and their ledger rows in the correct
order (`app/cli.py`, `64b9af8`); `tests/test_cascade_delete.py` verifies the
model registry by introspection, so a future user-child model cannot be
forgotten silently.

Logs carry only event, status, comparability class, attempt, and duration. No
image content or base64, S3 key, opaque token, description, prompt, provider
output, idempotency key, or fingerprint is ever logged.

## 10. Persistence and migration `fa1b2c3d4e5f`

Additive, no backfill, sole Alembic head, parent `b3c4d5e6f7a8`.

> Parent updated during integration. This migration originally chained off
> `e9f0a1b2c3d4`. While the branch was open, `main` advanced and added two
> children of that same revision (`f0a1b2c3d4e5`, `b3c4d5e6f7a8`), so keeping
> the original parent would have left the graph with two heads and forced boot's
> automatic `db upgrade` to choose one. The revision was repointed onto
> `b3c4d5e6f7a8` — the later of the two — in merge commit `5caa31a`. Nothing
> else about the migration changed; see §14 for the re-verification.

The migration is **verify-or-create and re-runnable**, because `app/db_init.py`
runs `db.create_all()` **before** Alembic on a fresh database — so this
table-creating migration also executes against a schema `create_all` already
built. When the tables exist it does **not** blanket-skip: it reflects columns,
types, indexes, and CHECK constraints and **fails closed** on drift (`568068c`,
`09123ca`).

**The one real production defect found and fixed on this branch (`425e657`):**
SQLAlchemy's inspector does **not** return PostgreSQL's `pg_get_constraintdef`
text. It strips redundant parentheses around AND-groups and renders membership
as `= ANY (ARRAY[...])` with a single opening paren. Two consequences broke a
real `create_all`-then-`upgrade` boot on PostgreSQL 16 with
`incompatible pump_check_comparison schema: check ck_pump_comparison_comparability has wrong SQL`:

1. a single optional-paren `ANY (ARRAY[...])` pattern consumed the closing paren
   of the enclosing group;
2. the terminal-fields check compared unequal as text purely because of the
   dropped redundant parens.

The verifier now canonicalizes both spellings — string literals masked, casts
dropped, `ANY (ARRAY[...])` rewritten to an IN-list, `!=` normalized to `<>`,
and the predicate rebuilt through an explicit recursive-descent AND/OR tree —
and it **fails closed** (returns the input unchanged, so verification raises) if
any token does not parse.

Tolerating the spelling must not tolerate a wrong value.
`tests/test_pump_check_comparison_migration.py` now asserts the **exact
inspector reflection strings captured verbatim** from real PostgreSQL 16 and a
real SQLite file, and asserts that adding one unexpected enum member
(`'unknown'`) to the inspector's `ANY (ARRAY[...])` form still raises. The
pre-existing tests only used `pg_get_constraintdef` forms — which is precisely
why CI stayed green while the production fresh-DB boot path failed. That gap is
now closed by regression coverage, not by inspection.

Rollback note (repo rule A2): the migration is additive, so a code rollback that
leaves it applied is safe.

## 11. Verification evidence — all at HEAD `3b3e131`

**PostgreSQL 16 (disposable container `fitx-pg16-race`):**

- `create_all` → `db stamp e9f0a1b2c3d4` → `db upgrade` — **exit 0** (this is the
  production fresh-DB boot path that was broken before `425e657`).
- Full `db upgrade` from an **empty** database — **exit 0**.
- `flask --app starter db heads` → **`fa1b2c3d4e5f (head)`**, sole head.
- `flask --app starter db check` → **"No new upgrade operations detected"**,
  exit 0 — zero model/migration drift.

**Focused suites:**

- 13-module PR3 + adjacent suite (plan Step 4 command verbatim) — **338 passed**.
- `tests/test_pump_check_comparison_migration.py` alone — **36 passed**.

**Mandatory harness regression sequence (preserves `a69c958`):**

- `tests/test_frontend_audit_app.py` + the leaderboard test — **8 passed**.
- the leaderboard test alone — **1 passed**.

**Authoritative baseline shards** (deterministic modulo-8 over 178 test files,
regenerated and re-run from scratch after `3b3e131`):

| Shard | Result |
|---|---|
| 1 | 431 passed, 1 skipped, 3 deselected |
| 2 | 464 passed, 1 skipped |
| 3 | 479 passed, 1 skipped |
| 4 | 308 passed, 1 skipped |
| 5 | 502 passed |
| 6 | 654 passed, 1 skipped |
| 7 | 329 passed, 3 skipped |
| 8 | 487 passed, 1 skipped |
| **Total** | **3654 passed, 9 skipped, 3 deselected, ZERO failures** |

`python -m pytest --collect-only -q` → **3658 collected / 3 deselected**,
exit 0. (3654 passed + 4 in-run skips = 3658; the remaining 5 skips are
module-level `pg_concurrency` skips that never enter collection totals in the
default run.)

**Real PostgreSQL race suite — the exact CI command, 5 modules, `-m pg_concurrency`
with `FITX_PG_CONCURRENCY_TEST=1`: 17 passed.** The comparison module
contributes: same key + same command converges once (one Bedrock call, one
ledger row); different keys + same pair converge (one row, two ledger rows);
same key + genuinely different command yields exactly one winner and one
`IdempotencyConflict`; reversed pair is ineligible **before** idempotency is
consulted; cross-user same key stays independent (two rows, two calls); a stale
generation cannot overwrite a newer result; an expired lease is reclaimed by
**exactly one** contender (attempt 3 → 4, one call).

## 12. Exclusions honored

No history, no automatic previous-check selection, no image URLs, **no
Flutter/mobile client work of any kind**, no progress scores, no heatmaps, no
body-fat estimates, no numeric deltas, no program rewrites, no social behavior,
no second provider, and **no PR4 retention/history work**. No new feature flag
was introduced — the routes sit behind the existing `MOBILE_AUTH_ENABLED` gate.
`git diff --name-only origin/main...HEAD` confirms backend, tests, docs, and CI
only. `git diff --check origin/main...HEAD` reports no whitespace errors.

## 13. Verdict

**READY WITH CONDITIONS**

All executable verification is green at HEAD `3b3e131`, including real
PostgreSQL 16 migration, drift, and race evidence. The conditions are:

1. **Report structure is a reconstruction.** The 27 verbatim headings required
   by plan Task 9 Step 9 are unrecoverable (§ "Report structure" above). A
   reviewer holding the original brief must re-check this report against its
   real 27 headings.
2. **CI has not run.** Every result here was produced locally on **Python
   3.14.3**; CI runs **Python 3.11**. CI green — including the PostgreSQL race
   job that `05cbb1f` extended with
   `tests/test_mobile_pump_check_comparison_pg.py` — remains a merge condition.
   The branch is intentionally unpushed, so no CI run exists.
3. **Orphan `pending` rows on ledger conflict** are an accepted structural
   artifact (§7). They cost no duplicate model spend and produce no duplicate
   canonical comparison, but they are unreaped until PR4 retention work lands.
   Review should either accept this explicitly or move reaping into scope.

No unresolved P0 or P1 threat remains. Delivery is local: **not pushed, no pull
request, not merged, not deployed.**

---

## 14. Post-integration re-verification and shipping review (HEAD `62c26f1`)

Sections 1–13 were written at `3b3e131`, before `main` advanced. Sections 1–12
still describe the shipped design accurately; §11's evidence and §13's verdict
are superseded here.

### 14.1 What changed after `3b3e131`

| Commit | Change |
|---|---|
| `1e42e68` | API documentation for the comparison surface. |
| `5caa31a` | Merge `origin/main` (`9bc2998`) in. Resolves the Alembic head divergence, the `app/cli.py` erasure list, the CI race-job module list, and the `docs/handoff.md` append conflict. |
| `c0d036a` | Safety fix: block bare growth/gain/hypertrophy **rate** claims and `skeletal disorder`/`pathology`/`condition`. |
| `62c26f1` | Menu OCR fix: catch the parent `ImagePreparationError`, not just `ImageTooLargeError`. |

`main` gained two children of `e9f0a1b2c3d4` while this branch was open, so
`fa1b2c3d4e5f` was repointed onto `b3c4d5e6f7a8` (see §10). `main` was **merged**,
not rebased onto — the shipping brief and the committed reports cite `a69c958`
and `05cbb1f` by SHA, and a rebase would rewrite both.

This supersedes §3's file count. The diff against `origin/main` is now **41
files, +7,445 / −85** — still backend, tests, docs, and CI only, with **no
Flutter or mobile-repository file of any kind**. §3 counted 36 because it scoped
itself to PR3 product work; the full branch diff additionally carries
`scripts/frontend_audit/app.py` and `tests/test_frontend_audit_app.py` (the
`a69c958` baseline harness fix, reviewed separately in §2) and the documentation
this section updates. No production module beyond §3's list is touched.

### 14.2 Independent full-diff review

Three findings, all fixed or explicitly accepted on this branch; no P0.

**P1 — safety-boundary bypass (fixed, `c0d036a`).** An adversarial probe pushing
every prohibited-claim string through the *real* parser in *all seven* text
fields of both contracts found `growth rate` passing 7/7 fields and
`skeletal disorder` passing 7/7 — **14 leaks**. Root cause: the numeric patterns
required a digit, and a *rate* of growth is a quantified progress claim with no
digit in it; and the medical pattern listed only `skeletal abnormality`, so the
same diagnosis under another noun sailed through. Both spellings are now
rejected, with parametrized regressions over every field of *both* contracts
(`tests/test_pump_check_comparison_analysis.py`,
`tests/test_pump_check_analysis.py`). Re-running the probe: **0 leaks**.

**P2 — 500 on the `/menu` path (fixed, `62c26f1`).** Reproduced concretely:
`_extract_text_from_image(b'not an image' + NUL padding past the ceiling,
'image/jpeg')` raised `ImagePreparationError`, and `app/blueprints/menu.py:110`
calls that path with no `try/except`. Introduced when PR3 extracted the
byte-ceiling helper into the shared `app/services/vision_images.py`, whose
contract raises the parent class for an undecodable image as well as
`ImageTooLargeError` for the ceiling. Now returns `""`, restoring the friendly
"could not read the menu" answer.

**P2 — orphan `pending` row (accepted, see §7 and §14.5).**

No false-positive findings are carried into this report.

### 14.3 Error taxonomy (brief §35)

Every failure is a typed code in the mobile envelope
(`{code, message, retryable, request_id}`). No raw provider or internal
exception text ever reaches the client, and no state has to be inferred from the
HTTP status alone — `retryable` carries the retry semantics explicitly.

| Code | HTTP | `retryable` | Retry same key? | Different pair? | GET afterwards | Terminal |
|---|---|---|---|---|---|---|
| `INVALID_IDEMPOTENCY_KEY` | 400 | false | No — send a valid key | n/a | nothing created | yes, for that request |
| `INVALID_PUMP_CHECK_COMPARISON` | 400 | false | No — fix the body | n/a | nothing created | yes |
| `PUMP_CHECK_NOT_FOUND` | 404 | false | No | may succeed | nothing created | yes |
| `PUMP_CHECKS_NOT_COMPARABLE` | 422 | false | No — the same pair always fails | may succeed | row may exist as `failed`/`invalid_media` | yes for that pair |
| `IDEMPOTENCY_CONFLICT` | 409 | false | No — use a **new** key | n/a | prior comparison unaffected | yes for that key |
| `PUMP_CHECK_COMPARISON_UNAVAILABLE` | 503 | **true** | **Yes** — resumes the same row | n/a | row readable, non-terminal | no |
| `PUMP_CHECK_COMPARISON_PROVIDER_BUSY` | 503 + `Retry-After: 15` | **true** | **Yes** — nothing was attempted | n/a | unchanged | no |
| `AUTH_RATE_LIMITED` | 429 + `Retry-After` | **true** | **Yes** | n/a | unchanged | no |
| `REQUEST_TOO_LARGE` | 413 | false | No | n/a | unchanged | yes |
| `PUMP_CHECK_COMPARISON_NOT_FOUND` | 404 | false | n/a (GET) | n/a | — | yes |

Two deliberate collapses. `PUMP_CHECK_NOT_FOUND` covers both "does not exist"
and "is not yours", so ownership never leaks. `PUMP_CHECKS_NOT_COMPARABLE`
covers both deterministic ineligibility and permanently unusable canonical
media, so a client cannot probe which rule fired — both are non-retryable for
that pair, which is the only distinction a client needs to act on.

An unexpected exception outside the service's typed set falls to the blueprint's
catch-all and returns `AUTH_TEMPORARILY_UNAVAILABLE` 503 `retryable=true`. That
code name is inherited from the shared mobile surface and is slightly generic
for this route, but the envelope, the retry semantics, and the absence of any
internal detail are all correct. Not introduced by PR3; noted, not changed.

### 14.4 Performance (brief §67)

Every statement is a primary-key or unique-index lookup. There is no list query,
no history scan, no polling, and no long transaction.

**Cold create (new pair, new key)** — 14 bounded statements: ledger lookup (1),
two owned-source lookups (2), existing-pair lookup plus insert (2), ledger
insert (1), terminal-state read (1), lease claim plus attempt read-back (2),
generation-ownership check (1), finalize (1), re-read (1), and two relationship
loads while serializing (2). Exactly **2 S3 reads** (one per source) and exactly
**one** Bedrock call.

**Replay (same key, already completed)** — 5 statements. **Zero** S3 reads,
**zero** Bedrock calls.

**GET** — 3 statements (one row lookup plus two relationship loads); no write,
no S3 read, no provider call, and no side effect of any kind. The handler never
claims a lease, never re-runs analysis, and never mutates status.

**Image normalization is bounded before any provider call.**
`prepare_image_for_vision` caps decompression at 50 MP, longest edge at 1600 px,
and output at 1 500 000 bytes, stepping JPEG quality down 85 → 70 → 55 → 40 →
30. A decompression bomb raises rather than allocating.

**No lock spans I/O.** Each helper commits or rolls back immediately, so the
session holds no open transaction while S3 or Bedrock is being called. The lease
is a row *value* (`status` + `analysis_started_at`), not a database lock, and
convergence is settled by unique constraints — so a worker that dies mid-call
holds nothing at all.

Two P2 performance observations, both accepted: `serialize_comparison` triggers
two lazy relationship loads per response (2 extra PK selects — bounded, and the
alternative is denormalizing the public IDs into the comparison row), and the
replay-but-not-yet-completed path re-runs eligibility resolution to rebuild
media (3 extra selects, still with no S3 or provider work before the lease is
claimed).

### 14.5 Orphan `pending` row — severity reconfirmed

Independently reconstructed rather than copied from §7. The exact sequence:

1. Two concurrent requests use the **same** `Idempotency-Key` with **different**
   commands.
2. Both reach `_create_or_get_pair_and_request`. The loser's pair row is created
   and **committed** by `_create_or_get_pair` before `_attach_ledger` runs.
3. `_attach_ledger` hits `uq_pump_comparison_request_user_key`, resolves the
   winner's row, sees a different fingerprint, and raises `IdempotencyConflict`.
4. The loser's `pending` comparison row survives with no ledger entry pointing
   at it.

Impact, each checked against the code rather than assumed:

- **Correctness** — none. The row is `pending`, carries no analysis, and is
  never served: the request 409s, and a GET would need the opaque public ID,
  which was never returned to anyone.
- **Retry** — none, and it is in fact self-healing. Pair uniqueness means a
  later legitimate request for that same directional pair converges **onto this
  row**, and `_claim_analysis` accepts `pending`, so it is picked up and
  completed normally rather than duplicated.
- **Pair** — none. The unique constraint is intact; there is still exactly one
  row per `(owner, baseline, current, version)`.
- **Provider spend** — none. `IdempotencyConflict` is raised inside
  `_create_or_get_pair_and_request`, strictly **before** `_run_or_reuse_analysis`
  is entered. No S3 read and no Bedrock call occur on this path.
- **Privacy** — none. The row is owner-scoped, its public ID was never emitted,
  and it holds no analysis content.
- **Growth** — bounded by pair uniqueness × the user's own Pump Check count ×
  `BEDROCK_RATELIMIT`, and only along a path a client reaches by actively
  misusing one key for two different commands.

**Severity P2, accepted for merge.** It is a hygiene artifact: an unreaped row
with no correctness, privacy, starvation, or duplicate-spend consequence. PR4's
retention work is the right place to reap it. Forcing atomicity here would mean
holding one transaction across both the pair insert and the ledger insert on a
path only a misbehaving client reaches.

### 14.6 Verification evidence — all re-executed at HEAD `62c26f1`

Local interpreter Python 3.14.3; **CI is authoritative on 3.11**. Every result
below was produced after `c0d036a` and `62c26f1`, so none of it is stale.

**PostgreSQL 16** (disposable container, databases recreated per scenario):

- empty database → full `db upgrade` — **exit 0**; the chain ends
  `b3c4d5e6f7a8 -> fa1b2c3d4e5f`.
- `db heads` → **`fa1b2c3d4e5f (head)`**, sole head.
- `db check` → **"No new upgrade operations detected"**, exit 0.
- real fresh-DB boot path (`create_all` → automatic stamp → automatic upgrade)
  → `alembic_version = ['fa1b2c3d4e5f']`; all five `pump_check*` tables plus
  `plan_mutation_record` present.
- incremental `b3c4d5e6f7a8` → `fa1b2c3d4e5f` — exit 0; comparison tables
  **0 before, 2 after**.
- schema-drift probe — **9/9 as designed** (2 accepted, 7 rejected with explicit
  messages; enumerated in `docs/handoff.md`).
- real opt-in race suite, the exact CI command (6 modules) — **22 passed**.

**Full local suite** — 8 deterministic modulo-8 shards over 181 test files,
every shard exit 0:

| Shard | Result |
|---|---|
| 0 | 721 passed, 1 skipped |
| 1 | 415 passed, 4 skipped |
| 2 | 443 passed |
| 3 | 467 passed |
| 4 | 506 passed, 1 skipped |
| 5 | 417 passed |
| 6 | 395 passed, 2 skipped |
| 7 | 477 passed, 2 skipped |
| **Total** | **3841 passed, 10 skipped, ZERO failures** |

`pytest --collect-only -q` over `tests/` → **3845/3848 collected (3 deselected**,
the `-m "not load"` load tests**)**. The same 8 shard file groups collect
721/418/443/467/506/417/396/477 = **3845**. The 6-line gap against 3841 + 10 is
the six `pg_concurrency` modules, which skip at **collection** time
(`allow_module_level=True`): one skip line each, zero collected items each.
3845 = 3841 passed + 4 in-run skips.

**Python 3.11 compatibility** — all 33 changed `.py` files parse under
`ast.parse(..., feature_version=(3, 11))`; no `except*`, no PEP 695 syntax.

### 14.7 Verdict at `62c26f1`

**Local verification is complete and green.** Two of the three §13 conditions
are now closed: the independent safety review found and fixed a real field-wide
bypass, and the orphan `pending` row has been independently reconstructed and
accepted as a bounded P2.

The remaining conditions are unchanged. **Every result above is local, on Python
3.14.3; CI runs Python 3.11 and is authoritative**, so merge readiness depends
on CI being green at the final pushed SHA. And the 27 verbatim headings required
by plan Task 9 Step 9 remain unrecovered, so this report's structure is still a
reconstruction that a reviewer holding the original brief must cross-check.
