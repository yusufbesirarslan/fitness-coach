# Physique Progress

Progress Redesign PR4. Read-only composition of canonical Pump Check history
and any persisted Pump Check comparison for the Progress page section
**PHYSIQUE PROGRESS**.

This document is the contract. The implementation is
`app/services/progress_physique/` plus `GET /api/progress/physique`.

## 1. Product responsibility

PHYSIQUE PROGRESS answers:

> What does my visual progress actually show?

It does **not** become:

- a photo gallery;
- a second Pump Check engine;
- a body-composition estimator;
- an image-scoring system;
- an automatic comparison generator.

The user should be able to see:

1. which body area is being viewed;
2. the relevant baseline / current Pump Checks;
3. whether a canonical comparison exists;
4. whether that comparison is reliable, limited, or not comparable;
5. the cautious observations already persisted by the comparison authority.

No new visual judgment is invented here.

## 2. Authority map

| Concept | Owner |
|---|---|
| Canonical Pump Check history | `mobile_pump_checks.history` |
| Canonical Pump Check structured analysis | `mobile_pump_checks.analysis` (write-time) |
| Canonical Pump Check comparison | `mobile_pump_check_comparisons` |
| Comparison analysis text | persisted `PumpCheckComparison.analysis` |
| Comparability | persisted `PumpCheckComparison.comparability` |
| Image visibility / signing | `s3_helper.generate_presigned_url` (owner-aware) |
| Legacy web gallery | `/pump-check-gallery` (compatibility only) |
| Physique Progress | **new read-model consumer** |
| Body-region detection from images | **does not exist** |

## 3. Dependency graph

```
Progress frontend (progress_physique.js)
        ↓
GET /api/progress/physique
        ↓
progress_physique
       ↙       ↘
canonical Pump Check history
canonical Pump Check comparison
       ↓
private owner-scoped image signing
```

Nothing under `mobile_pump_checks` or `mobile_pump_check_comparisons` imports
this package. There is no mobile HTTP self-call and no provider dependency.

## 4. Canonical history semantics

Chronology is `captured_at`.

`created_at` is **never** a fallback. The expression
`effective_date = captured_at or created_at` is forbidden.

A row without `captured_at` is a **legacy** row. It may remain visible in the
legacy gallery. It is never a Physique Progress observation and is never
compared here.

Ordering of recent checks matches canonical history:

```
captured_at DESC, id DESC
```

## 5. Body-region semantics

`body_region` is canonical user/domain metadata
(`full_body`, `upper_body`, `lower_body`, `back`, `arms`, `legs`).

It is **not** computer-vision detection. This layer must never imply
"AxisAI detected your shoulders".

The vocabulary is imported from `mobile_pump_checks.service.BODY_REGIONS`.
It is not copied into a third constants file.

`?region=` is a view selector. An invalid value is `400`, never silently
another region. If omitted, the selected region is the body area of the
owner's most recent canonical Pump Check.

## 6. Comparison selection

Only a **persisted, completed** `PumpCheckComparison` is displayed.

Selection, for the selected region:

1. owner-scoped;
2. both source checks are canonical (`captured_at` and `public_id` present)
   and share that body region;
3. status is `completed`;
4. newest **current** source `captured_at`, then `PumpCheckComparison.id DESC`.

Direction is preserved: baseline is older, current is newer. IDs are never
sorted. Analysis is never recomputed. Pending / failed rows are ignored.
This package never creates a comparison and never retries one.

`current_is_latest_check` compares the comparison's current opaque id with
the newest recent check. When it is false the UI says **Latest saved
comparison**, not "latest progress".

## 7. Comparability

Owned by the comparison authority:

`comparable` · `limited` · `not_comparable`

Progress renders those values faithfully. It does not derive comparability
from quality, dates, or analysis text. An unknown persisted value is
upstream contract drift, not a user-facing comparison state. It raises
`UnknownPhysiqueComparability`; `GET /api/progress/physique` returns the
generic 500. The value is never mapped to `comparable`, `limited`,
`not_comparable`, or a local `unknown` token.

Presentation:

- **comparable** — side-by-side images, summary, a small number of
  observed / stable / focus items, next-check guidance.
- **limited** — same facts, but the limitation is explicit and prominent
  (`Limited comparison`). Limitations are not footer-sized.
- **not_comparable** — no visual-change claims.
  "We couldn't make a reliable comparison from these two checks."
  Reasons / limitations / next-check guidance may still show.

## 8. Top-level states

| State | Meaning |
|---|---|
| `empty` | no canonical Pump Check in the selected / default context |
| `single_check` | exactly one canonical check in that region |
| `history_only` | ≥2 canonical checks, no completed comparison |
| `comparison_available` | a completed persisted comparison exists |

A system failure is **not** a state. It is HTTP 500. The client renders
`unavailable`. Failure of this endpoint cannot take down YOUR PROGRESS,
WHAT CHANGED, AXIS INSIGHTS, or PROGRESS HISTORY.

## 9. Private-image policy

Images stay private.

1. The query proves ownership (`user_id` filter).
2. Only then is signing treated as pre-authorized.
3. `s3_helper.generate_presigned_url(..., expected_user_id=owner)` is reused.
4. URLs expire (1 hour). No permanent public URL.
5. `image_key` and bucket never leave the server.
6. Only the unique images actually rendered are signed (≤ 4).

## 10. Read-only guarantee

`build_progress_physique` performs no `add` / `delete` / `flush` / `commit`.
It does not call Bedrock, `analyze_images`, or `create_or_replay`.
Page load must not create a comparison.

Comparison creation remains a Pump Check-domain command
(`POST /api/v1/pump-check-comparisons`, mobile). There is no web
comparison-creation workflow in this PR, and PHYSIQUE PROGRESS does not
link to one that does not exist. Empty / history-only states link to the
existing gallery.

## 11. API contract

```
GET /api/progress/physique
GET /api/progress/physique?region=upper_body
```

Web-authenticated (`@require_auth`). No `user_id` input. A supplied
`user_id` is ignored. `Cache-Control: private, no-store`.

```json
{
  "contract_version": 1,
  "state": "comparison_available",
  "selected_region": "upper_body",
  "regions": [
    {
      "body_region": "upper_body",
      "check_count": 4,
      "latest_captured_at": "2026-08-18T08:00:00Z"
    }
  ],
  "recent_checks": [
    {
      "id": "<opaque>",
      "captured_at": "2026-08-18T08:00:00Z",
      "body_region": "upper_body",
      "analysis_status": "completed",
      "analysis_quality": "sufficient",
      "image_url": "<private-expiring-url-or-null>"
    }
  ],
  "comparison": {
    "id": "<opaque>",
    "comparability": "comparable",
    "baseline_pump_check_id": "<opaque>",
    "current_pump_check_id": "<opaque>",
    "current_is_latest_check": true,
    "baseline_captured_at": "2026-08-04T08:00:00Z",
    "current_captured_at": "2026-08-18T08:00:00Z",
    "baseline_image_url": "<private-expiring-url-or-null>",
    "current_image_url": "<private-expiring-url-or-null>",
    "analysis": {
      "summary": "...",
      "observed_changes": [],
      "stable_areas": [],
      "focus_areas": [],
      "limitations": [],
      "comparability_reasons": [],
      "next_check_guidance": "..."
    }
  },
  "legacy_gallery_available": false
}
```

Absent comparison is `null`. Never database integer ids, never image keys.

## 12. Frontend boundary

`static/progress_physique.js` fetches and renders. `static/progress.js` only
calls `FitXPhysiqueProgress.load()`.

The browser translates labels and writes persisted prose with `textContent`.
It does not decide whether the physique improved. Unknown comparability is
treated like not-comparable. Unknown states render unavailable.

Persisted comparison analysis is **not** language-localized. Structural UI
labels are. Do not add an LLM translation call to close that gap.

## 13. Safety restrictions

Forbidden, including derivation from images, analysis strings, weight, or
training data:

- body-fat / muscle-mass / circumference estimates;
- physique / transformation / attractiveness scores;
- progress percentages;
- medical, injury, or posture diagnoses;
- causal claims joining visual change to training or nutrition.

## 14. Query budget

Four owner-scoped reads, independent of total history length:

1. region aggregate (`GROUP BY body_region` where `captured_at IS NOT NULL`);
2. legacy existence (`captured_at IS NULL`, `LIMIT 1`);
3. recent checks (`LIMIT 2`);
4. newest completed comparison (`LIMIT 1`).

No `filter_by(user_id=...).all()`, no N+1, no presign per historical row.
No new index: `#218` already added `ix_pump_check_user_captured`.

## 15. Failure behavior

`GET /api/progress/physique` failing degrades only PHYSIQUE PROGRESS.
Empty / single / history-only are valid user-data states, not errors.

## 16. Legacy gallery compatibility

`/pump-check-gallery` and `/pump-check-gallery/data` are unchanged.
Canonical mobile Pump Check and comparison APIs are unchanged.

When the owner has legacy rows and no canonical history, the empty state may
say that older Pump Checks are still in the gallery.

## 17. Deferred

- **Comparison creation UX** — Pump Check domain, not Progress. Mobile
  `POST /api/v1/pump-check-comparisons` already exists.
- **PR5** — Progress History convergence / weekly trajectory journal.
- **Cross-domain physique insights** — only after a deliberate canonical
  authority exists. Do not inject physique observations into AXIS INSIGHTS.

## 18. Provider prose

Stored comparison analysis has already passed the canonical comparison
validator (no progress scores, no causal claims, no medical claims). Progress
may display that persisted text. It must not regenerate, rewrite, translate,
or summarise it through another model.
