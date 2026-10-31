# Existing mobile owner — one narrow companion PR

Verified mobile main: `9ac420ac5ddda488a72e196359c5b1e1194d8f48` (fresh fetch, 2026-10-29).

The five-purpose Settings selection/review/grant/revoke flow already exists in `lib/features/ai_consent/{domain/ai_consent.dart,presentation/purpose_consent_screen.dart,presentation/ai_consent_panel.dart}`. `data/ai_consent_dto.dart` requires complete exact EN/TR server strings, digest metadata and a single displayed version. `presentation/ai_consent_controller.dart` fences mutations and late responses by authenticated owner/epoch. Keep these components.

Coach/nutrition/QR already use `lib/core/ai/ai_action_identity.dart` and `ai_action_receipt_client.dart`; Training/Pump retain their existing operation recovery. Existing `lib/core/domain/ai_refusal.dart` distinguishes finite Starter/Premium exhaustion, reset metadata and physical-budget exhaustion. Subscription pending/expired/revoked handling remains with `lib/features/monetization/data/monetization_error_mapper.dart` and its existing owner. No new StoreKit/paywall/navigation work is requested.

## Required delta

Backend adds terminal HTTP 403 `AI_SERVICE_ELIGIBILITY_REQUIRED`, with the established `{error:{code,message,retryable:false,request_id}}` envelope. It means the canonical signed adult/TR service record is unavailable or fails validation. Premium or a consent grant cannot resolve it. Do not retry automatically, send a second AI POST, prompt a new purchase or imply that granting AI consent verifies age/country.

Map this code for all five native features through the existing error mapper seams:

- `lib/features/coach/data/coach_error_mapper.dart`
- `lib/features/training/data/training_generation_error_mapper.dart`
- `lib/features/nutrition/data/nutrition_native_error_mapper.dart` (generation and QR)
- `lib/features/pump_check/data/pump_check_error_mapper.dart`

Reuse the existing account eligibility/review destination where supported and existing localized unavailable/review copy; add exact EN/TR wording only with the existing localization owner if needed. Retain non-AI/history/account access and original action identity. The refusal is before admission and does not itself spend an allowance. Do not erase unresolved identities from an earlier uncertain operation.

Verify each feature's 403 presentation, no purchase/re-consent loop, no automatic redispatch, original-key behavior, and logout/account-switch fencing. Existing allowance reset/exhaustion, physical-budget and subscription-state tests should remain green. Extra backend `external_scopes` disclosure metadata is additive; no DTO or consent-UI rebuild is required.

This is a source handoff, not mobile device, paid-provider or release end-to-end qualification. No mobile source was edited by Task 31.
