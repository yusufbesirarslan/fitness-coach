# Training Editor private exercises

This additive resource is for the picker in the Training Editor. It uses the same mobile bearer, account eligibility, and first-party health-consent boundary as the editor. All paths below are under `/api/v1`. The curated catalog and editor contract continue to use their existing versions and response fields.

| Request | Result |
|---|---|
| `GET /training/private-exercises?q=...&after=...` | `{contract_version: 1, exercises: [...], next_cursor: null|string}` |
| `POST /training/private-exercises` | `{contract_version: 1, exercise: {...}}`, HTTP 201 for creation or HTTP 200 for replay |

Both GET parameters are optional. Search matches a case-folded, whitespace-normalized substring and accepts up to 80 characters. Browse omits `q`. Each page has up to 50 entries ordered by opaque ID. A non-null `next_cursor` becomes the next request's `after`, with the same query. Reset the cursor on owner or search changes.

POST accepts exactly these JSON fields:

```json
{
  "display_name": "Zercher split squat",
  "equipment": ["barbell", "rack"],
  "movement": "lunge",
  "primary_region": "lower_body"
}
```

The returned item adds `exercise_id` and uses the same five-field model as a curated picker item. A name must contain a letter or digit and be at most 120 characters. Letters, combining marks, digits, spaces and ordinary movement-name punctuation are allowed. Leading, trailing and repeated spaces are collapsed. Equipment is a nonempty list of distinct [catalog equipment tokens](../../app/services/exercise_catalog.py). Movement and primary region use the same closed vocabularies. Cardio requires the cardio region and equipment supported by the existing cardio contexts. Registration does not guarantee compatibility with the currently saved plan.

The server assigns an immutable opaque ID. The same owner and normalized name with matching metadata returns the original item, including on retries after an uncertain response. Different metadata for that name returns 409 `TRAINING_PRIVATE_EXERCISE_CONFLICT`. Invalid inputs return 400 `TRAINING_PRIVATE_EXERCISE_INVALID` or 413 for a body over 4 KiB. Transient storage errors return 503 `TRAINING_PRIVATE_EXERCISE_UNAVAILABLE`. The owner comes from the bearer principal.

In #212, add a private section or tab and a create action within the existing Add/Swap picker. Keep results bound to the active owner and query, as with the existing picker. Registration alone never changes a plan. Select the returned ID and submit the existing editor Add or atomic Replace with its current revision, receipt and reread flow. Use the server display name for private IDs, including when rendering a saved plan after a fresh launch. Treat an unavailable route on an older backend as unavailable private registration. The shared picker still works independently.

## Persistence and authorized Coach reads

The private registry is separate from the curated catalog and generation choice sets. Canonical plan rows store the same ID and name fields as curated rows. Owner-scoped resolution supports Today/Plan and workout reads, exercise notes, history, and exact Undo after removing or replacing a slot. A plan edit never creates performed history. Individual exercise rename and delete are outside the registration API. Account deletion removes the registry. First-party health erasure removes its definitions when neither plans nor workouts are held under the existing retention policy.

The Coach plan read resolves definitions by owner and ID. The provider projection can include the validated user-defined name, movement, equipment, primary region, day, position, and planned sets and reps within the existing closed reps grammar. It omits opaque IDs and arbitrary prescription prose. This extension uses the existing Training READ path. The separate D1 authorization and provider-dispatch fences come from #548 in the eventual composition. The feature does not introduce a Coach write path.

Migration `e5b27aa4101c` adds `private_exercise` after `c1a9b7e5d301`. No backfill is needed. Preserve the registry and its owner-aware readers once plans contain private IDs. The migration refuses downgrading a populated registry.
