# V1 adult-only eligibility — date-of-birth screen (BE-18-1)

Founder decision **FDO-AGE18-20261114**: AxisAI V1 is adult-only (18+), Türkiye
first. This supersedes the 13+ target in `PRIV_LAUNCH_ELIGIBILITY.md` and parks
`TR_MINOR_ONBOARDING.md` (its code stays, closed behind its source pins).

Source only. Nothing is activated, deployed or migrated by this change.

## Contract

`POST /api/v1/auth/eligibility` and web `POST /eligibility` accept exactly

```json
{"birth_date": "YYYY-MM-DD", "country": "TR"}
```

- The response shape is unchanged:
  `eligibility: {status, age_band, country, reason, capabilities, registration_token}`.
- The old `{age, country}` body is refused with 400
  `AUTH_ELIGIBILITY_INVALID_REQUEST`, like any other unknown shape. A
  client-supplied age is no longer an input anywhere on this screen.
- The server computes the age on the app calendar day (Europe/Istanbul,
  `app.timeutil.app_today`). The 18th birthday is the first eligible day. A
  29 February birthday completes its year on 1 March in a common year.
- Under 18 is `ineligible` / `UNDER_MINIMUM_AGE` in every supported market
  (TR, US, GB, CA, AU). `qualification_required` is no longer produced.
- Today or a future date, an impossible date, a non-`YYYY-MM-DD` string and an
  age over 120 are 400.
- `status` is still a self-declaration result. It is not age assurance, health
  consent (F01), AI permission (D1) or provider admission.

## What did not change

- Signed record schema, record `VERSION` (`age-screen-v1`), ticket payload
  (`{version, age, country}` with the **server-computed** age), ticket TTL.
- `ai_launch.APPROVED_SERVICE_COHORT_VERSION`, `d1_grant_cohort`,
  `ai_service_eligible`, `purchase_eligible`, F01 text and digests.
- No migration. Alembic head is unchanged.
- Account deletion and recovery stay outside the eligibility and health
  boundaries.

## Date of birth handling (as implemented here)

The date of birth is read from the request body for one calculation.

- It is not written to the ticket, the signed record, `User` or any other
  column. What is stored is what was stored before: the derived whole-year age
  in `User.age` and the age band in the signed record
  (`test_date_of_birth_is_not_carried_into_the_ticket_or_the_account`).
- Application code does not log it: the eligibility service and blueprint log
  nothing from the body, and Sentry is initialised with
  `max_request_body_size="never"`.
- Not covered by this statement: host-level logging outside the repository
  (reverse-proxy or WAF body capture). Confirm on the host before a public
  "never stored" claim.

## Existing accounts

| Account | Result |
|---|---|
| Signed adult record | Unchanged. |
| Signed 13–17 record, any market | No longer a record: `review_required`, every capability refused, no D1 cohort, no AI admission, no purchase. Still a minority signal, enforced with the rollout flag on or off. |
| No record (legacy) | `review_required` while the flag is on; one-time review with a new ticket. A profile age under 18 blocks an adult review (409). |
| Adult record + profile age edited under 18 | The profile write is now refused (`age` / `range`) instead of contradicting the record and locking the account out. |

A ticket minted under the old rule (old slot during a deploy, at most 600 s)
is re-screened when presented: an under-18 age in it registers nothing.

The TR 13–17 source pins (`APPROVED_TR_MINOR_NOTICE_VERSION`,
`APPROVED_TR_MINOR_TRAINING_AI_VERSION`) stay `None`. They are the only
channel through which an under-18 declaration could pass; a test pins both.

## The rollout flag at launch

Registration demands a ticket only while `ACCOUNT_ELIGIBILITY_ENABLED` is on
(off = legacy registration, kept for existing suites and staging seams). That
is closed at launch by the existing R6 deploy transaction, not by this PR:
`scripts/r6_deploy_transaction.py` and `scripts/web_slot_runtime.py` refuse to
verify or route a slot whose deep health does not report
`eligibility_enforced: true` (`tests/test_r6_request_admission.py`). A
production slot with the flag off cannot be routed.

With the flag on, registration without a valid ticket is refused on both
transports before any provider call
(`test_native_registration_and_web_cannot_bypass_canonical_ticket`).

## Paired clients

- Mobile: MOB-18-1 (date picker, `birth_date`). A build without it gets 400 on
  the eligibility step and cannot register.
- Web `/register`: date input, same body.

## Tests

`tests/test_account_eligibility.py` (boundary, Istanbul day, retired contract,
stale ticket, legacy minor record, profile floor, no DOB persistence);
helper `tests/eligibility_support.py`.
