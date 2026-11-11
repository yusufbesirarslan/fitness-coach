# TR 13–17 onboarding (minimum, source-pinned, closed by default)

Founder product decision: minimum age 13. This change prepares registration for
Türkiye 13–17 under the live Terms §3 ("18 yaşından küçükseniz"). **No capability opens.**

## Where `qualification_required` came from (before this change)

| Layer | Source | Effect on TR 13–17 |
|---|---|---|
| Declaration | `account_eligibility.evaluate` (`country == "TR" and age < 18`) | `qualification_required` / `MARKET_QUALIFICATION_REQUIRED`, no registration ticket |
| Stored record | `account_eligibility._record` (TR + `13_17` → `None`) | any TR minor record reads as `review_required` |
| Features | `_capabilities` + `enforce_request` | `body_photos`, `automated_weight_targets` adult-only; profile setup/edit, check-ins, web coach refused |
| F01 health grant | `health_consent._respond` | refused (was proxied through `automated_weight_targets`) |
| AI | `ai_launch.require_cohort` → `purchase_eligible` | adult TR only → `AI_SERVICE_ELIGIBILITY_REQUIRED` |
| Flag off | `preview` window mode | minors get 503 `AUTH_ELIGIBILITY_UNAVAILABLE` |

## What changes

- `APPROVED_TR_MINOR_NOTICE_VERSION = None` (source pin; no env/config/client channel).
  When it equals `TR_MINOR_NOTICE_VERSION` (`tr-minor-notice-v1`), TR 13–17 declarations become
  `screen_passed` / `13_17` with **all capabilities false**.
- Registration for that cohort requires `minor_notice_version == "tr-minor-notice-v1"` on
  `POST /api/v1/auth/register` (the minor's own acknowledgment on the existing sign-up screen).
  It is bound into the signed `_account_eligibility_v1` record as `minor_notice`. It is never sent or
  accepted for any other cohort. Web `/register` and existing-account review cannot attach a TR
  minor record (no notice channel) and fail closed.
- Removing the pin returns every TR minor record to `review_required`.
- F01 grant now checks its own internal `health_data` capability (adult-only), separate from
  `automated_weight_targets`. Wire projection key set is unchanged (shipped mobile parser).

## Still closed for TR 13–17 (each needs its own reviewed decision)

Health-data processing (F01), all five D1 AI purposes, Pump Check/body photos, automated weight
targets (profile setup/edit, check-ins), purchases, social. The acknowledgment is not parental
consent, not age assurance and not capacity evidence.

**Product consequence while those stay closed:** a registered TR minor can sign in, manage Settings
and delete the account, but cannot complete the current profile (every goal is weight-based). Do not
pin the notice until a teen-safe profile/first-plan path is decided, or the account is a dead end.

Tests: `tests/test_tr_minor_onboarding.py`.
