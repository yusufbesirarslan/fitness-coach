# F01 free-launch staging qualification investigation

Task: AXISAI-IOS-POST-PHYSICAL-QA-FOUNDER-UX-FIXES-20261111.
This is a source investigation and a proposed qualification procedure, not authority to execute it. No AWS request, environment change, provider call, synthetic account creation, production change or device mutation was performed.

## Finding: Apple subscriptions are not required

`app/services/ai_launch.py::require_runtime` deliberately refuses disclosure admission when the approved cohort pin, exact installed allowance policy, execution enforcement, spend guard or Redis are unavailable. Its broad refusal is `AI_CONSENT_DISCLOSURE_UNAVAILABLE`; the name covers runtime admission as well as publication. This error alone does not establish that disclosure text is absent or Apple is required.

`app/services/ai_allowances/execution.py::require_ready` already explicitly accepts `APPLE_SUBSCRIPTIONS_ENABLED=false` when the installed policy has the complete finite campaign scope (`campaign_ready`). Without that scope, Apple-off refuses rather than inventing an unlimited or partially metered tier. No subscription/payment activation or source bypass is necessary to support a free launch.

| Setting | Role in proposed bounded qualification |
|---|---|
| `AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=true` | Existing execution/metering entry point; required by native AI launch admission despite its historical name. Does not grant Premium. |
| `AI_ALLOWANCE_LEDGER_ENABLED=true` | Required atomic finite usage ledger; keep enabled. |
| `AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=true` | Required Training enforcement; keep enabled. |
| `APPLE_SUBSCRIPTIONS_ENABLED=false` | Supported with complete finite campaign policy; keep purchases/verification inactive. |

The existing installed `LAUNCH_V1_POLICY` contains finite five-family Starter, monthly and LAUNCH_FREE scopes. LAUNCH_FREE starts **2026-11-25 00:00 Europe/Istanbul**, for three anchored 30-day windows (30 Coach / 2 Training / 2 Nutrition / 20 Pump / 5 QR). On 2026-11-11 a newly eligible account uses its once-per-account **Starter Training allowance of one**, not an early campaign window. Do not shift the clock/date, fabricate an entitlement, reset an account budget or substitute unlimited policy. Policy completeness is required even outside the campaign window.

## Source and local evidence

Closed base: `8154e1b3f8e3ae7ad7d8aca2ad9490eac6c138a1`. It retains unpublished cohort/health/Training pins. Existing separately prepared source candidate `f1ebaf56fceeaaf9b52b0922adce92c9e5d29a62` pins the reviewed health disclosure, adult-TR cohort and **Training only**. See that candidate's `docs/privacy/F01_TRAINING_SOURCE_CANDIDATE_APPROVAL.md` for exact approval manifest, versions, EN/TR hashes and provider tuples. This report does not import, merge, activate or independently approve that candidate. Other AI purposes and Premium purchase readiness remain closed.

Against exact candidate f1ebaf56, local tests `tests/test_f01_training_source_registration.py` and `tests/test_launch_free_allowance.py`: **115 passed**. The focused Apple-off/cohort/runtime subset: **28 passed, 85 deselected**. Fixtures emulate identities/provider entry points and operational dependencies; these are source tests, not a real-provider or deployed F01 PASS. Apple-off still requires ledger, enforcement, finite policy, correct cohort and Redis; purchase availability stays closed. No source defect requiring Apple-on was found. Improving the generic runtime refusal diagnostic would be a separate concern, not authority to loosen it.

## Minimum safe future staging-only qualification

1. Obtain a new exclusive ownership receipt, receiving-owner acceptance and explicit authorization for the bounded configuration/image/schema changes, synthetic account scope, provider invocation, cleanup and rollback. Staging was returned stopped; this document authorizes neither starting it nor reusing previous ownership. Preserve owner zero-health-traffic observation, recovery snapshots and exact source/image/config receipts.
2. Bind the final independently reviewed backend/mobile source and all serving images. Review the candidate's exact health, cohort and Training disclosure registration, EN/TR text/contract digests, signed adult-TR authority and approved Bedrock EU recipient/model tuples. Current closed base cannot be opened with flags alone. A source-preparation approval is not a deployment/activation approval.
3. Authorize the three enforcement flags above and exact finite policy, while Apple subscriptions and purchase surfaces remain off. Verify account eligibility admission, PostgreSQL authority/schema/role, Redis, spend guard, bounded physical attempt/retry/cost ceilings and cancellation/owner-generation fences. Do not print secrets or sensitive parameter values.
4. With one explicitly authorized fresh adult-TR synthetic account, exercise registration, first-party health disclosure/grant, profile, Training-only disclosure/grant and one real-provider first plan. Verify canonical retrieval, stable idempotency replay and exactly one Starter Training unit. No bulk accounts or additional provider attempts without scope authorization.
5. Qualify missing/wrong/withdrawn/version-invalid grants, unsupported cohort/recipient, erasure/revocation and replay failures without unintended dispatch or allowance consumption. Bind planned failure fixtures and their cost/attempt scope before running remaining F01 failure steps; prior step 8 was not run and step 12 only partial. Local stubs do not close these requirements.
6. Produce the F01 receipt, clean up authorized synthetic data, restore the agreed stopped/configuration state and return ownership with receiving-owner acceptance. Do not merge, deploy production, upload to App Store or expose other AI purposes under this procedure.

## Independent remaining decisions

Teen access remains separate: product minimum age 13 does not itself approve TR 13–17 service classification/capacity, health processing, AI purpose authority or representative authorization. No representative flow exists to be bypassed with a checkbox. The reviewed adult-TR candidate does not approve teen access.

FatSecret rights, actual provider reachability, complete physical first-plan behavior and monitoring/recovery evidence require their own current receipts. Enabling allowance enforcement does not settle any of these. The previous physical F01 was blocked before a real model call; it remains BLOCKED pending a newly authorized qualification window.
