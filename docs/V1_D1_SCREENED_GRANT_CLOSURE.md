# V1 D1 screened-grant boundary closure

Task `AXISAI-V1-D1-SCREENED-GRANT-CLOSURE-20261114` (founder decision
`GO_NARROW_BACKEND_SOURCE_FIX_ONLY`). Source only: no migration, flag, pin,
deploy, staging run or consent-record change. Base: main `17f522bf` (#514).

## Gap

After #514, `ai_consent._require_grant_cohort` was restrict-only. It refused
non-Training purposes for an account with a minority signal, and let every
other account record any launch purpose. An account with no valid canonical
signed age-screen record could therefore record an affirmative D1 grant. That
included:
- a legacy account;
- an account with only an editable profile age;
- an account with a forged, foreign or malformed record;
- a non-TR adult.

`ACCOUNT_ELIGIBILITY_ENABLED` did not change this, because the write gate
never read it and no other check required a screened cohort.

## Fix

`account_eligibility.d1_grant_cohort(user, now)` is the one consent-write cohort
predicate. It returns:

| Result | Requires |
|---|---|
| `adult_tr` | `_adult_tr` (the existing purchase/AI adult rule): a canonical `_record`, `18_plus`, country `TR`, a subject, `checked_at` aware and not after `now` |
| `tr_minor` | a canonical `_record` with `13_17` + `TR` (so the TR minor notice pin is open and the notice acknowledged), a subject, profile age an int 13–17, `checked_at` aware and not after `now` |
| `None` | anything else |

`_record` already rejects a record that is:
- unsigned, tampered, oversized or not a string;
- for a foreign subject;
- of the wrong schema, version or source;
- for an unknown band or market;
- for an unpinned TR minor;
- contradicted by profile age (under 13, or under 18 on an adult record).

`ai_consent._require_grant_cohort` runs as before:
- inside `mutate`'s owner-locked transaction;
- for `affirmative_grant` only;
- after disclosure validation;
- before any state or event write.

It now allows a new grant only when one of these holds:
- the cohort is `adult_tr` and there is no `minority_signal`. Any launch
  purpose already validated against `POLICIES`/`LAUNCH_VERSIONS` is allowed.
- the cohort is `tr_minor` and the purpose is in `_minor_grant_purposes()`,
  which is exactly `training.plan`.

Everything else is refused with
`403 AI_CONSENT_PURPOSE_UNAVAILABLE_FOR_ACCOUNT`. This is the existing #514
code, not retryable, so the mobile client needs no new mapping.

**Independent of the rollout flag.** `d1_grant_cohort`, `_record` and
`_adult_tr` never read `enabled()` or `_enforced()` (a source-inspection test
pins this). The result is the same with `ACCOUNT_ELIGIBILITY_ENABLED` off or on.

**Not provider admission.** The consent-write gate never calls
`ai_service_eligible`, `ai_launch.require_cohort` or any `ai_launch` code, and
`ai_launch` never calls the gate. Passing it admits nothing. Provider dispatch
still needs:
- `require_cohort`;
- the TR minor Training AI pin for teens;
- `require_runtime` (allowance policy, ledger flag, spend guard plus Redis,
  `require_ready`);
- the D1 dispatch fence.

The teen consent write deliberately does not require
`APPROVED_TR_MINOR_TRAINING_AI_VERSION`. That pin is provider admission. A
grant recorded while it is closed admits nothing.

## Unchanged

- **D1 disclosures:** `POLICIES`, `PUBLISHED_TEXTS`, `LAUNCH_VERSIONS`, digests
  and versions.
- **Withdrawal (`revoke`):** never cohort-checked, so a historical grant stays
  withdrawable whatever the account's record now says.
- **Replay of an existing request id:** answered from the event table before
  any cohort check; never resurrects a grant.
- **`status`:** historical grants are reported as recorded and never rewritten
  or revoked by code.
- **Elsewhere:** provider admission, runtime safeguards, F01 publication,
  processing and erasure, eligibility pins and flags, AI quotas, spend guards
  and model routing.

A refused grant writes no event, consumes no request id and leaves the
generation unchanged.

## Release consequence

With the shipped pins, a new D1 grant is possible only for an account carrying
the canonical adult TR record. Accounts get one through registration while
eligibility is on, or through `review_existing` (named qualification cohort,
or eligibility on). Teen grants additionally need the TR minor notice pin,
which ships `None`. Unscreened accounts must complete the age screen before
granting. The mobile client already treats the 403 as a refusal, but the
dedicated copy and routing to the screen are mobile follow-ups.

## Tests

- **`tests/consent_cohort_grant_support.py`:** shared scenarios on the real
  launch disclosures:
  - screened adult, all purposes, flag off and on;
  - screened adult without profile age;
  - unscreened legacy, profile-age-only adult and profile-age-only minor, flag
    off and on, every purpose in both locales;
  - a refused id later usable;
  - 13 malformed or unsupported records: tampered, forged, non-string,
    oversized, foreign subject, wrong version, wrong source, extra key, bad
    band, naive date, future date, US, DE;
  - five conflicting profile ages;
  - qualified teen: Training grant and withdrawal, other purposes refused in
    TR and EN;
  - unpinned teen refused, Training included;
  - teen record with a missing or wrong notice;
  - legacy grants (unscreened adult and non-canonical teen): status, replay,
    withdrawal, withdrawal replay, refused re-grant, lineage;
  - a screened adult's replay and withdrawal after record loss;
  - pins never widen teen grants.
- **`tests/test_ai_consent_cohort_grant_pg.py`:** the same scenarios on the
  real PostgreSQL authority. It is in the CI PG list.
- **`tests/test_ai_consent_cohort_grant.py`:**
  - SQLite stand-in for the same scenarios;
  - a `d1_grant_cohort` matrix (flag × notice pin × age/band/market);
  - subject and declaration-time checks;
  - flag-independence and gate-separation source inspections.
- **`tests/d1_screened_support.py`:** attaches a real canonical adult TR
  record to the synthetic owners of suites that grant D1 for another purpose
  (fencing, publication, allowance, launch integration). It does not bypass
  the gate.
