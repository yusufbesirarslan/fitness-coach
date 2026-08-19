# Progress History — reconstructed canonical read model

The single server-owned authority behind the Progress page's
**PROGRESS HISTORY** section. Introduced by **Progress Redesign PR5**.

It answers two questions and nothing more:

1. **How has my Progress state evolved over time?** → newest-first rows
2. **What evidence made this period look that way?** → compact drilldown

A row is **reconstructed**, not archived:

> Based on your data up to this check-in

It is **not**:

> AxisAI told you this on August 3

There is no persisted historical decision. Current canonical Progress
algorithms interpret historical facts as-of that check-in's Istanbul day.

---

## 1. Product responsibility

PROGRESS HISTORY is the fifth Progress surface. After PR1–PR4:

| Section | Canonical owner |
|---|---|
| YOUR PROGRESS | `GET /api/progress/summary` |
| WHAT CHANGED | `GET /api/progress/summary` |
| AXIS INSIGHTS | `GET /api/progress/axis-insights` |
| PHYSIQUE PROGRESS | `GET /api/progress/physique` |
| PROGRESS HISTORY | `GET /api/progress/history` |

History must not become:

- a chart dashboard;
- an audit log;
- a second training analytics engine;
- a stored snapshot system;
- an AI-generated retrospective;
- a raw WeeklyCheckIn dump;
- historical Axis Insights ("What's working / Watch this / Next move").

---

## 2. Authority map

History owns no Progress semantics. Existing canonical authorities do.

| Concept | Owner |
|---|---|
| WeeklyCheckIn facts | WeeklyCheckIn / check-in domain |
| Qualifying check-in rule | existing canonical check-in semantics (`yogunluk IS NOT NULL`) |
| Historical analysis day | `app.timeutil.app_date_of` (Europe/Istanbul) |
| Historical training signal | `training_progression.build_progression_report(..., end_day=D)` |
| Trajectory mapping | `progress_summary.trajectory_for_signal` |
| Performance mapping | `progress_summary.summarize_performance` |
| Consistency mapping | `progress_summary.summarize_consistency` |
| Analysis window | `progress_summary.SUMMARY_WEEKS` + `build_window` |
| Historical weight | the anchored qualifying WeeklyCheckIn row |
| Historical weight delta | consecutive qualifying check-ins (same 1 dp subtraction as summary body) |
| Progress History | this read-model consumer/composer |
| Browser | translation / presentation only |

`build_progress_summary` is **not** called for a past day. Its body/profile
read (`User.weight`, `User.target_weight`, latest check-in fallback) is
current-state and is not historically safe.

---

## 3. Dependency graph

```
progress_history.js
        ↓
GET /api/progress/history
        ↓
progress_history
       ↙        ↘
WeeklyCheckIn   training_progression
                   ↓
          canonical Progress Summary
             pure state mappings
```

Nothing under `training_*` or `progress_summary` imports this package.

---

## 4. Reconstructed vs persisted

PR5 V1 is a reconstructed read model.

For a historical check-in anchored on date D:

- historical training facts are evaluated as-of D (`end_day=D`);
- the **current** canonical Progress algorithms interpret those facts;
- future data must not leak backward into D;
- no stored snapshot is created.

There is no `ProgressHistory` table, no snapshot JSON, no cache, no
materialization, no migration, no backfill.

If immutable historical decisions become a product requirement later, that
is a separate architecture.

---

## 5. Historical anchor

A Progress History row is anchored by a **qualifying** WeeklyCheckIn.

Qualifying rule (already canonical, not invented here):

```
WeeklyCheckIn.yogunluk IS NOT NULL
```

This is the same filter `/checkin-history` and Progress Summary use to keep
sparse `/update-weight` rows out of Progress observations.

Each qualifying check-in is one history anchor. Rows are not deduplicated by
calendar week or day. Same-timestamp events keep deterministic order:

```
created_at DESC, id DESC
```

The internal DB id is an ordering tie-break only. It is not a public API
field.

---

## 6. Historical time authority

`WeeklyCheckIn.created_at` is a naive UTC persistence timestamp.

The analysis day is:

```
app.timeutil.app_date_of(checkin.created_at)
```

Europe/Istanbul. A check-in at `2026-08-18 21:30 UTC` belongs to
`2026-08-19` Istanbul day.

Do not use `created_at.date()`. Do not use browser-local conversion to
decide the analysis day. The wire `analysis_day` is an ISO date; the
frontend localizes display from that calendar day.

---

## 7. Historical training reconstruction

For each qualifying check-in:

```
anchor_day = app_date_of(checkin.created_at)
report = build_progression_report(user_id, weeks=SUMMARY_WEEKS, end_day=anchor_day)
trajectory = trajectory_for_signal(report.next_signal)
performance = summarize_performance(report)
consistency = summarize_consistency(report)
```

The window is the Progress Summary constant (`SUMMARY_WEEKS = 4`), not a
caller-controlled range. Unknown progression vocabulary fails closed
(`UnknownProgressionSignal` → generic HTTP 500). It is never mapped to
`building_baseline`, `needs_attention`, or empty.

---

## 8. Body-history rule

| Field | Source |
|---|---|
| `weight_kg` | the anchored check-in's stored weight when it is a positive number; otherwise `null` |
| `weight_delta_kg` | anchored qualifying weight minus previous qualifying check-in weight when both are valid; otherwise `null` |

Rounding of the derived delta matches Progress Summary (1 decimal place).

Do not use:

- `current_user.weight` for past entries;
- current target weight;
- historical distance-to-current-target;
- a good/bad verdict on weight direction.

`+2 kg` and `-2 kg` do not imply on track or needs attention. Trajectory
remains training-led.

---

## 9. Future-data isolation

A history entry anchored at D must not change merely because data **after**
D exists, except when the underlying historical facts themselves are edited.

In particular, adding after D:

- a workout;
- a later check-in;
- a current profile weight change;
- a current target-weight change;

must leave that past row's trajectory, performance, consistency, weight and
weight delta semantically unchanged.

`training_progression` already isolates workouts by `end_day`. Body facts
are taken from the anchored rows, not the live profile.

---

## 10. API contract

```
GET /api/progress/history
```

- `@require_auth`
- no `user_id` input (query `user_id=` is ignored)
- no `weeks=` / `start=` / `end=` / `range=` analysis window
- `Cache-Control: private, no-store`

### Empty

```json
{
  "contract_version": 1,
  "state": "empty",
  "entries": [],
  "has_more": false
}
```

No qualifying check-ins. HTTP 200.

### Available

```json
{
  "contract_version": 1,
  "state": "available",
  "entries": [
    {
      "checked_in_at": "2026-07-15T15:00:00+03:00",
      "analysis_day": "2026-07-15",
      "window": {
        "weeks": 4,
        "start": "2026-06-24",
        "end": "2026-07-15",
        "timezone": "Europe/Istanbul"
      },
      "trajectory": { "state": "on_track", "reason": "progressing" },
      "performance": { "state": "progressing", "volume_trend": "up" },
      "consistency": {
        "state": "consistent",
        "sessions": 4,
        "active_weeks": 4,
        "analyzed_weeks": 4
      },
      "body": { "weight_kg": 78.4, "weight_delta_kg": -0.6 }
    }
  ],
  "has_more": false
}
```

Illustrative values. Vocabulary is whatever current Progress Summary owns.

### Bounds and `has_more`

Visible limit is **12** (server-owned).

Query pattern: latest `HISTORY_LIMIT + 1` qualifying check-ins,
`created_at DESC, id DESC`.

- first 12 → visible rows
- 13th → prior context for the oldest visible row's delta, and proof of `has_more`

No `COUNT(*)`. No `WeeklyCheckIn....all()`.

### Absent from the payload

- database ids
- `coach_feedback`
- raw training rows
- provider text
- Axis Insights slots
- current profile / target

### Failure

Infrastructure or contract drift → generic JSON 500. Never `state=empty`.

Observability is coarse only: `state`, `entry_count`, `has_more`, or
`error_class`. Weights, dates, notes, coach feedback and training volume
are not logged.

---

## 11. Query budget

1 owner-scoped bounded check-in query
+
1 `build_progression_report` (itself one workout-history read) per visible entry.

Measured domain SELECTs:

| Visible entries | SELECTs |
|---|---|
| 1 | 2 |
| 6 | 7 |
| 12 | 13 |

Cost does **not** grow with undisplayed check-in history: the anchor query
is `LIMIT 13` regardless of how many older rows exist.

---

## 12. Frontend boundary

`static/progress_history.js` is the only Progress consumer of
`/api/progress/history`.

It may:

- translate bounded machine states;
- format ISO dates and numbers;
- render empty / unavailable;
- expand/collapse a row locally.

It may not:

- calculate trajectory, performance, consistency, weight delta, or window;
- fetch `/checkin-history`;
- construct unescaped HTML from payload text.

Collapsed row: date, trajectory label, one compact secondary line
(performance · consistency), optional weight.

Drilldown uses a real `<button>` with `aria-expanded` / `aria-controls`.
Trajectory meaning is textual, never colour-only.

`static/progress.js` only calls `FitXProgressHistory.load()`.

---

## 13. Legacy `/checkin-history`

Compatibility surface. Unchanged.

Consumers outside Progress still exist (`templates/index.html`,
`templates/today.html`). PR5 stops the Progress page from reading it. It
does not rename, resort, strip `coach_feedback`, or delete the route.

---

## 14. What PR5 does not do

- no LLM / Bedrock / OpenAI / Anthropic / Groq
- no historical Axis Insights
- no AdaptivePlan / planner call per row
- no nutrition / recovery / physique / Feed / XP
- no Flutter
- no schema, migration, index, snapshot, or cache
- no production config change

---

## 15. Tests

- `tests/test_progress_history.py`
- `tests/test_progress_history_api.py`
- `tests/test_progress_history_ui.py`
- hermetic browser matrix: `scripts/frontend_audit/progress_pr5_matrix.py`
