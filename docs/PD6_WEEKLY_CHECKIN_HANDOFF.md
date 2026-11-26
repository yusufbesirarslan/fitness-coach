# PD6 handoff — committed weekly check-in evidence

- Canonical identity is `WeeklyCheckIn.id`. Ownership is the authenticated
  internal `User.id` stored in `WeeklyCheckIn.user_id`. Use the pair
  `(owner_id, check_in_id)` for retrieval and deduplication. Native v2 GET
  entries and POST snapshots expose `check_in_id` to that owner. A v1
  snapshot lacks the ID by contract. The server can resolve its canonical
  row by authenticated owner and idempotency key with
  `weekly_checkin.queries.find_by_idempotency_key` after the commit.
- From a new transaction after successful commit, call
  `weekly_checkin.queries.full_checkin_by_id(owner_id, checkin_id)`.
  A missing or foreign row returns no result. The full-row predicate is
  `yogunluk IS NOT NULL`, which excludes legacy sparse weight-only records.
  An erased or deleted record is unavailable. A flush or a generated
  identifier alone does not establish that a check-in has committed.
- `created_at` is naive UTC by database convention. Use
  `app.timeutil.app_date_of` for the Europe/Istanbul `analysis_day` and
  `mobile_weekly_checkin.week_start` for the Monday of its Monday–Sunday
  week. Multiple full check-ins in a week or day are valid. History
  orders by `created_at DESC, id DESC` and v2 snapshots retain the
  original date on replay.
- Call `weekly_checkin.models.recorded_nutrition_adherence(row)`.
  `numeric_1_5` returns the exact legacy score. `ternary_v2` returns
  `yes`, `partly`, `no` or `not_applicable` as recorded.
  `(None, None)` means missing or unusable historical adherence. A read
  failure is unavailable. The current plan's applicability must not fill
  in past adherence.
- For the preceding full observation, use
  `weekly_checkin.queries.previous_full_checkin(owner_id, checkin_id)`.
  It uses the same ordering and can return a same-day check-in. For the
  product's weight delta, follow the Progress History rule instead: use
  the most recent positive full-check-in weight from an earlier Istanbul
  day. The legacy web weight-update path can still edit a same-day weight
  on the canonical row. The native POST snapshot is the immutable
  original submission and is not a current-row read.
- Weight in kg, validated 1–5 workout intensity, fatigue and sleep quality,
  and unchanged progressive-overload answers are comparable when both
  values are available and in their original domains. The overload token
  `partial` expresses load progression. Compare legacy nutrition scores
  only with other valid legacy scores and ternary answers only with other
  applicable ternary answers. Skip cross-scale nutrition changes and any
  change involving missing or not-applicable adherence. No numerical
  deltas or score equivalents exist for ternary choices.
- A successful new commit and a replay resolve to one check-in identity.
  A replay produces no new check-in or weight effect. PD6's automatic
  post-commit evaluation should use that identity and deduplicate end to
  end, even if the response was lost. This handoff establishes the
  evidence boundary. PD6 owns provider authority, consent, allowance,
  deduplication and the pending or unavailable result lifecycle.
