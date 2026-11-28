# Product analytics v2: dashboard specifications (SPEC ONLY)

No vendor account, project or dashboard exists. The importable definitions are in [`dashboards.json`](dashboards.json): each panel is a PostHog SQL-insight body (`DataVisualizationNode` over `HogQLQuery`) for an **authorized** project. This file is generated from that JSON. **The HogQL has not been executed**; run it on the synthetic project at G5. Event and property references are pinned to the catalog by `tests/test_product_analytics_v2_dashboards.py`.

Conventions:

* Unit: `distinct_id` (a per-grant pseudonym, personless).
* Time is stored in UTC. Days and ISO weeks (starting Monday) use Europe/Istanbul.
* A 24 h ingestion watermark applies.
* Only mature windows are reported. Every panel shows x/n.
* Every panel is **survivor-biased**: withdrawal and rotation delete history.
* No opt-in rate is computable from vendor data.

## P1 Onboarding completion

* **Numerator:** subjects with axis_v2_onboarding_completed within 24 h after their first axis_v2_onboarding_started
* **Denominator:** subjects with axis_v2_onboarding_started whose axis_v2_analytics_enabled precedes it
* **Cohort / period:** ISO week (Europe/Istanbul) of onboarding_started
* **Maturity:** started_at <= now - 48 h (24 h window + 24 h ingestion watermark)
* **Known limitations:**
  * Only people who consented BEFORE onboarding are observable; with Settings-only consent this is near empty (no onboarding prompt by founder direction).
  * Step funnel (step1..4) is available from axis_v2_onboarding_step_completed.milestone; not shown here.
  * Survivor bias: withdrawal deletes history.

## P2 Time to first value

* **Numerator:** per subject: first training_plan_created | workout_completed | coach_reply_completed at or after onboarding_completed
* **Denominator:** subjects with onboarding_completed whose analytics_enabled precedes it
* **Cohort / period:** ISO week of onboarding_completed
* **Maturity:** done_at <= now - 8 d (7 d window + 1 d watermark)
* **Known limitations:**
  * Median/P75 are over subjects that reached value; within_24h/within_7d are over all completers (no censoring bias).
  * Existing users without an observed onboarding are excluded (a separate series anchored on analytics_enabled can be added).
  * Under model L only plan_created and coach_reply_completed count as value (workout is Tier 2).

## P3a First plan creation within 7 days

* **Numerator:** completers with a first training_plan_created within 7 d (split by path of that first plan)
* **Denominator:** subjects with onboarding_completed whose analytics_enabled precedes it
* **Cohort / period:** ISO week of onboarding_completed
* **Maturity:** done_at <= now - 8 d
* **Known limitations:**
  * Web (browser) plan replacement is not instrumented (first public = mobile).

## P3b Plan preview acceptance rate

* **Numerator:** count(training_plan_created path=ai_preview_accepted)
* **Denominator:** count(training_plan_proposed)
* **Cohort / period:** ISO week of the event (period ratio, not a per-proposal join)
* **Maturity:** week closed + 1 d watermark
* **Known limitations:**
  * Period ratio: an acceptance can fall in the week after its proposal; read over >= 4 weeks.
  * Event counts, not subjects; a re-generated preview counts as a new proposal.

## P4 Workout completion engagement

* **Numerator:** subjects with >= 1 axis_v2_workout_completed in the week
* **Denominator:** subjects with any axis_v2 event in the week (excluding analytics_enabled)
* **Cohort / period:** ISO week
* **Maturity:** week closed + 1 d
* **Known limitations:**
  * One completion per canonical completion day (server claim), so multiple sessions per day count once.
  * Tier 2: consenters only under every model.

## P5a Coach adoption

* **Numerator:** cohort subjects with >= 1 coach_reply_completed within 7 d of analytics_enabled
* **Denominator:** subjects whose first analytics_enabled is in the week
* **Cohort / period:** ISO week of analytics_enabled
* **Maturity:** enabled_at <= now - 8 d
* **Known limitations:**
  * coach_message_sent (client) vs coach_reply_completed (server) gap = sends without a completed reply; interrupted replies are excluded by design.

## P5b Coach mutation success and proposal acceptance

* **Numerator:** training acceptance = confirmed_training / proposed (domain=training); nutrition acceptance = applied / proposed (domain=nutrition)
* **Denominator:** count(coach_change_proposed) per domain (new pending proposals only; reuse, undo, chat messages and refusals excluded)
* **Cohort / period:** ISO week of the event (period ratio)
* **Maturity:** week closed + 1 d
* **Known limitations:**
  * DELTA-1 events are pending catalog review.
  * Direct (no-confirmation) training applies have NO attempt denominator: a direct-apply success RATE is not measurable; only counts.
  * Cancelled vs stale proposals are not distinguished (proposed - confirmed - still pending).
  * Nutrition: Tier 2 (consent only).

## P6 Weekly check-in adoption

* **Numerator:** subjects with >= 1 weekly_checkin_submitted in the week
* **Denominator:** subjects active in the week whose analytics_enabled is >= 7 d before week start
* **Cohort / period:** ISO week
* **Maturity:** week closed + 1 d
* **Known limitations:**
  * Tier 2: consenters only.
  * A check-in submitted for a past week counts in the week it was submitted.

## P7 D1/D7/D30 retention

* **Numerator:** cohort subjects with >= 1 meaningful event exactly on cohort day + N (Europe/Istanbul)
* **Denominator:** subjects whose first analytics_enabled is on the cohort day
* **Cohort / period:** Istanbul calendar day of analytics_enabled
* **Maturity:** report dN only for cohort_day <= today - (N + 1); earlier cells are immature and must be blank
* **Known limitations:**
  * Secondary series: replace the meaningful-event set with axis_v2_app_opened/axis_v2_app_resumed.
  * Pseudonym rotation (RETENTION.md) creates subjects without analytics_enabled; they never enter a cohort.
  * Under model L the meaningful set shrinks to plan_created + coach_reply_completed (Tier 1).

## P8 Feature reliability: AI plan generation

* **Numerator:** count(training_plan_created path in ai_*)
* **Denominator:** count(training_plan_created ai_*) + count(feature_failed feature=training_plan_generation)
* **Cohort / period:** ISO week
* **Maturity:** week closed + 1 d
* **Known limitations:**
  * Previews that were generated but never accepted are neither success nor failure here (see P3b).
  * Consent refusals (AI_CONSENT_*) are excluded from failures by design.

## P9 Weekly founder review

* **Numerator:** per metric (see columns)
* **Denominator:** shown with P1-P8 x/n for the last mature week
* **Cohort / period:** ISO week
* **Maturity:** week closed + 1 d
* **Known limitations:**
  * Opt-in rate is NOT computable from vendor data (refusers emit nothing).
  * Export for 12-month retention only as counts with cells < 5 shown as '<5' (RETENTION.md); never export distinct_ids.

## Coverage by processing model

| Panel | Model C (opt-in, current) | Model L Tier 1 (if approved) |
|---|---|---|
| P1 | consenters who opted in before onboarding (near empty with Settings-only consent) | funnel only if client events are allowed (Q-4) |
| P2, P3, P8 | consenters | everyone who has not objected |
| P4, P6 | consenters | consenters (Tier 2) |
| P5 | consenters | training: everyone; nutrition: consenters |
| P7 | consenters, all meaningful events | plans + Coach only, unless Tier 2 is consented |
| P9 | composite | composite |
