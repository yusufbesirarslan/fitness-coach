# D1 backend consent implementation — draft, activation HOLD

This branch starts at `36d833b21aeef8f7adb204f8bac6ed5b29803b9b`, merged PA06 #438. It adds PostgreSQL account-scoped consent state, append-only API mutation evidence, authenticated status/grant/revoke APIs, and a guard at the existing physical SDK boundary. It deliberately ships **no published disclosures**. No customer transfer can be authorized by this branch as shipped. Backend foundation qualification is not recipient attestation, legal clearance, or launch readiness.

## Authority and API

`GET /api/ai-consent` and `POST /api/ai-consent` use the existing web authentication/CSRF checks. The same methods at `/api/v1/ai-consent` use the existing feature-gated bearer API and no-store policy. GET returns current generation, scope validity and server-published disclosure references. POST requires exactly `action`, `purpose`, `version`, `locale`, `text_digest`, `contract_digest`, `expected_generation`, `request_id`; action is `affirmative_grant` or `revoke`. There is no owner field. Unknown fields, client booleans, unknown/obsolete policy revisions and arbitrary recipients cannot mint grants. An affirmative grant must match the server-owned immutable disclosure registry, including the exact localized text digest and semantic contract digest. The contract digest covers purpose, categories, recipients/models and localized text references; existing grants and stale screens deny if a version is accidentally changed in place. The API does not invent/display legally approved copy.

All mutations serialize on the existing account row and increment a durable generation. Repeating the identical request ID returns **current** state without rewriting; a reused ID with different content or stale expected generation returns `AI_CONSENT_CONFLICT` (409). A grant replay after withdrawal never restores permission. Revocation remains possible for an existing scope even if its policy has been removed. Audit events contain version/action/digest references, not prompts, photos, health values, IPs or credentials. Account deletion cascades both tables and fences account-bound dispatch through the existing user row.

Authority unavailable or unsupported database returns `AI_CONSENT_AUTHORITY_UNAVAILABLE` (503); SQLite cannot claim cross-process fencing. All missing/invalid grants deny. No account backfill is performed.

## Dispatch and withdrawal

`ai_provider_call.Admission.create` validates current consent on a separate READ COMMITTED connection **after capacity/backoff and before every actual SDK invocation**. The account row lock remains held through the blocking SDK call. SDK automatic retries remain disabled. Each retry checks the original admission generation and fresh state. Exact service and model/profile must appear in the policy tuple. Unknown provider labels deny rather than normalizing to OpenAI. `gate=False` and `charge=False` do not bypass consent.

Linearization is conservative: an acknowledged revoke commits only after preceding guarded SDK calls leave their fences. Already transmitted requests cannot be recalled. The lock wait is capped at two seconds per SQL lock acquisition; on timeout withdrawal returns 503 and **has not committed**. The client must retry and may not present withdrawal as complete. SDK timeout is capped at 30 seconds (per transport timeout phase, not a claimed total wall-clock cancellation guarantee). Connection-pool waits and HTTP transport behavior can extend wall-clock duration. Streaming is disabled because no bounded total-duration submission/finalization handoff is qualified.

A new attempt after committed revocation is denied, and revoke/regrant invalidates pre-existing admissions. The code does not promise cancellation of a completed transfer. Response-finalization/domain-write fencing for an activated multi-step generation flow remains an activation qualification item; no production disclosure is published while that item remains open.

## Physical inventory and exact functional consequences

The existing A01–A23 evidence is reused, including independently reachable direct OpenAI helpers. Every SDK bound method remains passed through `ai_provider_call`; architecture tests forbid directly invoking model SDK methods elsewhere.

| Inventory | Physical path | Behavior as shipped |
|---|---|---|
| A01–A03 | Coach blocking, stream/fallback and legacy profile Coach | Early typed refusal; `coach` is unqualified at SDK boundary. New AI replies unavailable; history reads retained. |
| A04 | RQ/deferred/stream-finally summaries | Independent memory gate false; old queued work does not summarize or rewrite history. `summary` also denied at SDK boundary. |
| A05 | Tool execution and follow-up turns | Coach external execution denied. Memory tool independently unavailable. Existing PA06 owner/projection protections retained. |
| A06 | Coach food tool, normalization and estimates | Generic nutrition helpers denied at SDK boundary; no independent OpenAI bypass. |
| A07 | Coach photo validator | `vision` denied; no image transfer. |
| A08–A09 | Training initial, replacement and repair | Narrow `training.plan` binding requires fitness preferences, body metrics and health constraints; exact recipients/models and version checked. HTTP generation routes remain closed pending activation qualification. No customer grant is publishable. Existing saved-plan reads and first-party confirmations remain available. |
| A10 | Nutrition-plan generation | Separate `nutrition.plan` binding requires nutrition targets, food preferences and potentially personal free text. HTTP generation unavailable pending activation. Existing plan reads/save and manual logging retained. |
| A11–A13 | Pump Check, comparison and workout photo proof | Early refusal before sensitive feature upload; `vision` denied at physical boundary. PA03/K6 non-photo completion redesign remains separate: photo-dependent completion is unavailable, not falsely verified. Checkpoints and manual workout logs retained. |
| A14–A17 | OCR, menu text, food normalization/macro/serving helpers, parallel batches | `menu_ocr`, `menu_extract`, `nutrition` and unqualified `other` deny, including direct OpenAI and gate-free OCR. Missing thread owner/app context also denies. |
| A18 | Web food discovery | Existing first-party/provider/static discovery branches may return; AI branches cannot dispatch. Mobile raw FatSecret discovery is not gated by AI sharing. |
| A19 | Web AI meal totals | Deny before optional photo upload and before SDK; manual macros and provider-backed diary paths preserved. |
| A20 | Web daily meal review | Early typed refusal; no SDK review. |
| A21 | Check-in feedback | Check-in saves first-party data and returns an explicit AI-unavailable explanation; no SDK attempt. |
| A22 | Other-user suggestion analysis | PA06 deterministic/no-AI semantic behavior retained. Generic helper guard prevents any accidental external analysis. |
| A23 | Synthetic deep-health probe | Also denied. No unqualified health-probe exception introduced. Deep inference health may report unavailable; shallow health stays first-party. |

The closed HTTP surfaces return stable `AI_CONSENT_DISCLOSURE_UNAVAILABLE` or `AI_CONSENT_PATH_UNAVAILABLE`, without sensitive input and without an automatic retry recommendation. The registry is intentionally empty: tests use **synthetic** disclosure objects, not invented approved text or production recipient representations. Only the two named plan builders have candidate purpose/category bindings. Coach/Training do not share a permission. Nutrition, vision and memory cannot inherit Training permission. All unqualified paths are deliberately closed, not reported as migrated positive flows.

## AI Memory and PA06

`AI_MEMORY_ENABLED` defaults OFF and cannot independently authorize memory. `memory_available` also stays false while summary is unqualified. Ordinary displayable conversation history can still be recorded by the pipeline; no previous summary/history is added as personalization without independent permission. The memory tool and queued summary functions check the independent gate. No historical rows are deleted, rewritten, backfilled, or promoted to consent. Signed PA06 provenance remains payload provenance only.

Existing minimization/provenance test assertions remain. Their explicit synthetic authority fixtures isolate those tests' ALLOW-side projection/budget/domain behavior. They are **not consent authority evidence**. D1 default-deny/SDK tests run without those fixtures; PostgreSQL D1 tests exercise real durable state, account locks and final mock SDK submission.

## Migration and #436

D1 adds `d1c0a15e0001` after main's `b1a8c9d0e1f2`. PR #436 at `2ba712531aa9f94043e81716007a57f0c1a281f2` adds sibling `c2b9d0e1f2a3` after that same ancestor. Neither migration was renumbered/reused, and #436/#437 were not modified. Additive DDL was tested in both orders in disposable PostgreSQL schemas: owner preserved, consent/subscription tables empty. This does **not** settle Alembic ancestry. HOLD merge until #436 resolves and an explicit reviewed ancestry reconciliation is prepared. Fresh full-chain PostgreSQL bootstrap and zero schema drift are qualified. Existing create_all-first tables are accepted only after exact columns/types/keys/checks validation; partial or NOT VALID authority constraints are rejected. Populated D1 downgrade refuses to erase consent evidence; runtime rollback must retain the tables and deny forwarding.

## Activation blockers and targeted independent review

1. Verify the cross-process account fence, all retry/fallback/stream rejection behavior, strict policy matching, ownership, idempotency and revocation acknowledgment semantics using the new PostgreSQL tests. Inspect exact final SDK capture, not only guard unit tests.
2. Review each deliberately closed A01–A23 path and the two candidate plan projections. Do not publish a disclosure or enable a route without complete purpose/data/recipient validation, request-level generation propagation and response-finalization/domain-write fencing. Old queued payloads must not restart after regrant.
3. Verify non-AI behavior and full hosted CI. Projection tests' mock ALLOW is explicit; it cannot establish release authorization. Resolve remaining hosted failures without introducing a runtime bypass.
4. Reconcile migration ancestry only after #436 resolves. No merge/deploy/production operation is authorized by this draft.
5. Separate owner/legal work must supply reviewed localized disclosure text/digests and attested service/model/profile/region/vendor facts. Do not assume repository model defaults are actual production recipients. No lawful basis for health data or international transfers is inferred.

Client UX remains separate: contextual affirmative action, independent optional Memory, scope-specific nutrition/body image permission, generation-aware conflict/revocation handling, terminal unavailable responses, and preservation of manual fitness flows. No iOS/web consent screens are part of this backend branch.
