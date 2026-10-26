# Route and operation coverage

All new integration below is default OFF. Existing D1 closed endpoints stay
closed before quota, upload or AI context work. “Bound” describes source wiring,
not live availability or completed client/transport qualification.

| Actual transport / entry point | Canonical family | Execution / settlement authority | Enabled-mode disposition |
|---|---|---|---|
| POST `/api/v1/coach/messages` | `coach` | `ai_pipeline.generate_answer`, original native OperationAuthority; canonical tools mark receipt | Bound; native protected publication retained |
| POST `/ask` | `coach` | Same shared pipeline | Bound; final nonstream response uses existing protected publication contract |
| POST `/chat` | `coach` | `ai_coach.generate_coach_reply` | Bound once around the legacy tool loop |
| POST `/ask/stream` | `coach` | `ai_pipeline.stream_answer` has logical scope; HTTP admission remains D1-closed | Explicit synchronous refusal before legacy quota or SSE headers; not an available launch transport |
| POST `/api/v1/training/plans` | `training_generation` | Original native operation + allowance binding + dispatch commit | Reused, strict entitlement in unified mode; original attempt/expiry/recovery preserved |
| POST `/training-plan` | `training_generation` | `training_generation.service.generate_training_plan_payload` after preference validation | Bound generic marker; one unit includes schema repair |
| POST `/api/v1/training/plans/replacement-proposals` | UNDECIDED; proposed `training_generation` | Original replacement operation lease/dispatch; test-only explicit extension mapping | Refuses without approved injected mapping; binding commits with dispatch |
| POST `/api/v1/training/plans/replacement/confirm` | Already-generated deterministic confirmation | Original proposal OperationAuthority and confirmation fencing | No additional generation unit |
| POST `/api/v1/nutrition/plan/generate` | `nutrition_generation` | `nutrition_native.plan.generate_proposals`, outer validation/review publication | Bound once; nested `generate_plan_options` reuses action |
| POST `/nutrition-plan` | `nutrition_generation` | `nutrition_plan_generation.generate_plan_options` | Bound; internal generation/correction shares one action |
| GET `/api/v1/nutrition/plan`, PUT same path, POST planned meal `/api/v1/nutrition/plan/meals/<planned_meal_id>/log` | Deterministic read/save/log | Existing ownership, revision and nutrition authorities | No AI generation charge |
| POST `/nutrition-plan/save`, GET `/nutrition-plan/active` | Deterministic save/read | Existing D1 provenance closure/write fences retained | No extra charge; closure is not bypassed |
| POST `/api/v1/pump-checks` | `pump_check` | Existing command fingerprint + analysis lease, reservation in lease commit | Bound before storage/provider; completed result replay/read is free |
| GET `/api/v1/pump-checks`, GET `/api/v1/pump-checks/<pump_check_token>` | Read/gallery | Existing image/result ownership | No reservation or provider |
| POST `/api/v1/pump-check-comparisons` | UNDECIDED; proposed `pump_check` | Existing pair + request identities, lease commit | Explicit extension mapping required; one pair = one dispatch, even with different keys |
| GET `/api/v1/pump-check-comparisons/<comparison_id>` | Read | Existing pair ownership | No charge |
| POST `/api/v1/nutrition/menu/analyze` | `qr_menu` | URL + profile validation, outer scope covers acquisition/OCR and joined analysis batches | Bound once, all workers share capped attempt count |
| POST `/api/menu/analyze` | `qr_menu` | `menu_analysis.analyze_menu_text` after profile/text validation | Bound once for supplied text; repairs/batches share it |
| POST `/api/proxy/scan-menu` | Acquisition; paid OCR is part of proposed complete QR action | Existing LP15-B1 network/privacy boundary | Deterministic acquisition unchanged; paid OCR lacks analysis scope and refuses. Combined web operation/client handoff required |
| Training active/current/bootstrap/preferences, Coach history/reset, saved menu/result reads | Deterministic | Existing ownership/auth | No charge; ordinary reading does not mint grants |

## Exempt / undecided execution table

| Operation/source | Decision | Admission rule in unified mode |
|---|---|---|
| `menu_extract.validate_pump_check`, workout completion photo proof | Existing exemption `workout_photo_proof` | Explicit narrow scope permits only `vision`; existing consent, proof/day authority and spend/input limits still apply |
| `memory_manager.maybe_summarize` | Existing exemption `coach_summary` | Explicit narrow scope permits only `summary`; original D1 summary availability remains authoritative |
| Check-in / plan feedback (`ai_coach` feedback helpers) | `legacy_web_ai` UNDECIDED | No logical scope ⇒ provider backstop refuses; no arbitrary Coach allowance mapping |
| Nutrition food lookup/AI macro estimation outside a menu/Coach action; meal review in `nutrition/meallog.py` | `legacy_web_ai` UNDECIDED | Provider refuses; deterministic food/search/persistence stays under existing rules |
| Social AI / other legacy provider calls | `legacy_web_ai` UNDECIDED | Provider refuses; inventory is a source call-door backstop, not a new paid entitlement |
| Health probe | Existing bounded operational probe | Existing special probe path, one output token, never a customer allowance |
| Internal Coach tool nutrition/vision, menu OCR/macro/weight calls | Internal suboperations | Must match active logical family and share its finite attempt ceiling; no extra visible unit |

The common physical provider door covers paid Bedrock/OpenAI calls; low-level SDK
direct invocation remains prohibited by existing architecture tests. Explicit
exemptions do not imply unlimited physical spending: existing spend guards remain.
No photo, prompt, answer, URL, health context, payment credential or secret is
added to metering logs. Stored generic fingerprints are keyed semantic digests.

Pump comparison and replacement mappings are tested as proposed configurations,
not production defaults. Nutrition replacement through a generation endpoint
spends one nutrition unit; deterministic application of an already generated
proposal does not. A future new replacement endpoint requires explicit coverage.

The intended enabled configuration has no unbound paid provider bypass; closed
or unresolved paths are unavailable. Availability of every transport is not
implied. An atomic paid launch must either qualify and disclose the actual
offered transports or keep them explicitly unavailable before purchase.
