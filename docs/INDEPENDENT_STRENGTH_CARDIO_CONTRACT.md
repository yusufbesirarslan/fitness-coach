# Independent strength/cardio scheduling — issue #530

## Contract and dependency chain

AI: preference_contract (strength 3..6, cardio 0..6) → capability evaluator /
mobile_training.preference_contract → prompt_builder → structural validator →
semantic_validator (independent modality counts) → closed catalog choices and
exercise_resolution (identity/equipment/type) → unchanged generation ledger and
save boundary → persisted seven-day program → mobile_training current plan /
workout projections → Flutter strict DTOs → WeeklyPlan / workout execution.

General: general_plan_identity / parse_general_request → build_cardio_program →
canonical save validation → unchanged general receipt/replay/store → same reads.
General strength choices stay 3/4; cardio choices become independently 1..6.

A day containing both modalities retains `tip=antrenman`. Strength exercises
and timed cardio exercises share the existing ordered list. Cardio uses one
set, `tekrar="<minutes> dk"`; day duration includes strength plus cardio. AI
validates strength duration after subtracting timed cardio, validates cardio
duration separately, and excludes cardio from strength exercise minimums.
Dedicated cardio stays `tip=kardiyo`; rest remains empty. Exactly seven unique
canonical weekdays are required; neither a combined ceiling nor a rest floor
is imposed. Cardio before/after strength uses list order; this contract has no
time-of-day field or separate independently startable workouts on one day.

## Compatibility

`contract_version=1` and every preference/plan/workout key and type are unchanged.
Only the WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS constraint entry is removed. The
native parser accepts a variable list, so old builds consume the new matrix.
CrossFit, powerlifting equipment, no-type cardio, choice ranges and catalog
identity/type/equipment checks remain. No quota, consent, eligibility, allowance,
rollout or migration changes. The mutation caller retains its prior placement
policy; this change covers newly generated AI/general plans and their save path.

Build 101119's seven-day model already carries ordered exercises with catalog
IDs, prescription, duration and a single day reference. A mixed day therefore
needs no new enum, field or client update to read/start/complete its exercises.
The mobile follow-up verifies actual backend-produced v1 fixtures with these
existing strict DTOs and updates general-plan choices and AI form regression
fixtures. No installed App Review binary or App Store configuration is touched.

## Preservation

Reads validate stored structure without re-scheduling. Existing general request
combinations retain their previous byte content; fingerprints/domain/template
IDs stay unchanged. General idempotent replay returns the stored row rather
than rebuilding it. The expanded request space only adds previously rejected
combinations. No database schema or stored-plan migration exists.
