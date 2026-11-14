# V1 backend release-blocker closure: teen D1 grant cohort + Coach injury prompt

Task `AXISAI-V1-BACKEND-TEEN-CONSENT-AND-COACH-PROMPT-CLOSURE` (founder decision
`GO_NARROW_BACKEND_SOURCE_FIX_ONLY`). Source only: no migration, flag, deploy,
staging run or consent-record change.

## A. Teen D1 grant cohort (finding F-QR-W1)

**Evidence.** In the 2026-11-13 staging QR window 1, TR teen user 101 (age 15)
recorded an affirmative `qr_menu.analysis` grant (request `df9b2cae`). No
provider call followed: provider admission refused it, as designed.

**Two gates, inspected separately.**

| Gate | Where | Cohort check before this change |
|---|---|---|
| Provider admission | `ai_launch.require_cohort` → `account_eligibility.ai_service_eligible(user, feature)` at `admit_endpoint`, `check_operation_within` and allocation | Yes. Adults need the signed adult-TR record; TR 13-17 get `training_plan` only, behind its own pin. |
| Consent write | `ai_consent.mutate` (`POST /api/ai-consent`, native consent route) | **None.** It validated the version, text and contract digest only, so any account could record any launch purpose. |

**Fix.** `ai_consent._require_grant_cohort` runs inside `mutate`'s
owner-locked transaction, for `affirmative_grant` only, after disclosure
validation and before any state or event write. It refuses with
`403 AI_CONSENT_PURPOSE_UNAVAILABLE_FOR_ACCOUNT` (not retryable) when both hold:

- the account carries a minority signal (`account_eligibility.minority_signal`):
  either a signed 13-17 record (whether or not the pin is open) or a profile age
  under 18; and
- the purpose is outside the minor purposes. These are derived from
  `TR_MINOR_AI_FEATURES` through `BINDINGS`, which gives exactly `training.plan`.

(2026-11-14: superseded in part by `docs/V1_D1_SCREENED_GRANT_CLOSURE.md`. A new
grant now also requires an affirmative screened cohort, so unscreened adults are
refused too.)

The check is restrict-only. It is deliberately not `ai_service_eligible`,
because that requires a signed adult record, and using it would change grant
behaviour for unscreened adult accounts. Admission is unchanged, and passing
the write gate admits nothing.

Unchanged:
- revocation;
- `status` (historical grants stay visible as recorded);
- replay of an existing request id;
- the event table.

A refused write consumes no request id and changes no generation. Existing
grants are never rewritten or revoked by code.

**Mobile.** The mobile client maps an unknown 4xx consent code to
`incompatibleResponse`. A dedicated mapping, and hiding non-Training
disclosures for minors, are mobile follow-ups.

## B. Coach injury/medical prompt

`app/prompts/system.py` on `dbac1fda`:
- Step 2 stopped plan generation and asked "Menisküs, Kifoz, Skolyoz gibi bir
  durumun var mı?" whenever `injuries` was null or
  `constraints_require_confirmation` was true. `coach_privacy` sets that flag on
  every stored injury, so the question could recur on every plan request.
- Step 3 then saved the answer.

Steps 1–3 now:
- tell the model not to ask about, or invite disclosure of, injuries, illness,
  diagnoses or medical history, and not to make advice wait on that
  information;
- apply stored `banned_movements` as protective restrictions, without asking
  the user to confirm them;
- with no stored restriction, plan normally;
- when the user volunteers pain, injury or a condition: stay general and low
  risk, ask for no details, and refer the user to a doctor or physiotherapist.
  Acute symptoms mean stop and seek medical help.

The old "save the answer" step is removed. `manage_user_memory` is an
unqualified D1 tool, so it is never offered to the model. Its description
(`ai_coach.py`) no longer says "ask the user, then save".

Step 4 is unchanged byte for byte, because the adaptive variant substitutes it
by exact match. "Tıbbi teşhis veya reçete VERME" is kept.

Unchanged: routing, allowance, D1 policy, the tool set and its execution, and the memory summarizer. Only the `manage_user_memory` descriptions changed.

## Tests

| File | Covers |
|---|---|
| `tests/consent_cohort_grant_support.py` | Shared scenarios on the real launch disclosures |
| `tests/test_ai_consent_cohort_grant_pg.py` | Real PostgreSQL authority; in the CI PG list |
| `tests/test_ai_consent_cohort_grant.py` | SQLite stand-in for the transaction, plus the signal unit tests and the envelope |
| `tests/test_coach_prompt_no_medical_solicitation.py` | The Coach prompt change |

Scenarios:
- adult, all purposes;
- unscreened adult;
- teen Training grant and withdrawal;
- teen refused for each other purpose, in TR and EN;
- minor by profile age only, and a contradicted adult record;
- legacy grant: inspect, refused repeat grant, replay, withdraw, refused re-grant, lineage kept;
- open teen pins do not widen grants.

Without the fix, 4 consent scenarios and 6 of 7 prompt tests fail.

`tests/test_tr_minor_training_ai_pg.py::test_teen_grant_of_another_published_purpose_never_opens_it`
previously expected a minor's non-Training grant to succeed. It now asserts the
403 refusal (no event, no generation change). It then seeds a grant through
the pre-fix path and proves admission still refuses it.
