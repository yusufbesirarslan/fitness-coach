# Training Editor private exercises

This additive resource is for the picker in the Training Editor. It uses the same mobile bearer, account eligibility, and first-party health-consent boundary as the editor. All paths below are under `/api/v1`. The curated catalog and editor contract continue to use their existing versions and response fields.

Private contract version 2 is a draft for coordination with the #212 mobile owner and the V3 catalog owner before activation. It supersedes the earlier unpublished private contract proposal.

| Request | Result |
|---|---|
| `GET /training/private-exercises?q=...&after=...` | `{contract_version: 2, exercises: [...], next_cursor: null|string}` |
| `POST /training/private-exercises` | `{contract_version: 2, exercise: {...}}`, HTTP 201 for creation or HTTP 200 for replay |

Both GET parameters are optional. Search matches a case-folded, whitespace-normalized substring and accepts up to 80 characters. Browse omits `q`. Each page has up to 50 entries ordered by opaque ID. A non-null `next_cursor` becomes the next request's `after`, with the same query. Reset the cursor on owner or search changes.

POST accepts exactly these JSON fields:

```json
{
  "display_name": "Barbell Romanian deadlift",
  "equipment": ["barbell", "rack"],
  "movement": "hinge",
  "target_region": "hamstrings"
}
```

The returned item adds `exercise_id` and the derived legacy `primary_region` (here `lower_body`). A name must contain a letter or digit and be at most 120 characters. Letters, combining marks, digits, spaces and ordinary movement-name punctuation are allowed. Leading, trailing and repeated spaces are collapsed. Equipment is a nonempty list of distinct catalog tokens. Movement uses the closed catalog vocabulary. [Catalog tokens](../../app/services/exercise_catalog.py)

The picker uses the V3 catalog labels for these keys, including Turkish **Calf** for `calves`.

| Selected V3 target region | Derived legacy primary region |
|---|---|
| `chest`, `back`, `shoulders`, `calves`, `core`, `full_body`, `cardio`, `mobility` | The same key |
| `biceps`, `triceps` | `arms` |
| `quads`, `hamstrings`, `glutes` | `lower_body` |

The cardio movement and target region must be paired, with equipment supported by the existing cardio contexts. The mobility target requires the mobility movement. A mobility movement can also target a body region. Other allowed movement patterns can target multiple regions. The editor checks placement and equipment against the saved plan when the owner selects the registered exercise.

## Registration recovery

The server assigns an immutable opaque ID scoped to the bearer owner. The same owner and normalized name with matching equipment, movement and target region returns the original item. Equipment order is ignored. A matching name with conflicting metadata returns 409 `TRAINING_PRIVATE_EXERCISE_CONFLICT`. Invalid inputs return 400 `TRAINING_PRIVATE_EXERCISE_INVALID` or 413 for a body over 4 KiB. Transient storage errors return 503 `TRAINING_PRIVATE_EXERCISE_UNAVAILABLE`.

Preserve the original body and its owner binding once dispatched. A transport loss, timeout, 503 or malformed success may follow a committed registration. While still acting as the original owner, reconcile using the owner-private registry and the original body's normalized name and metadata, or replay that exact body. A changed name or changed metadata is a new intent. A changed name can create a second entry, and changed metadata under the same name returns 409. Omit `Idempotency-Key` on registration. The server ignores that header for this resource. Canonical Add, Replace and Undo retain their explicit operation keys.

In #212, add a private section or tab and a create action within the existing Add/Swap picker. Keep results bound to the active owner and query, as with the existing picker. Registration alone never changes a plan. Select the returned ID and submit the existing editor Add or atomic Replace with its current revision, receipt and reread flow. Use the server display name for private IDs, including when rendering a saved plan after a fresh launch. Treat an unavailable route on an older backend as unavailable private registration. The shared picker still works independently.

## Persistence and authorized Coach reads

The private registry is separate from the curated catalog and generation choice sets. Canonical plan rows store the same ID and name fields as curated rows. Owner-scoped resolution supports Today/Plan and workout reads, exercise notes, history, and exact Undo after removing or replacing a slot. A plan edit never creates performed history. Individual exercise rename and delete are outside the registration API. Account deletion removes the registry. First-party health erasure removes its definitions when neither plans nor workouts are held under the existing retention policy.

The Coach plan read resolves definitions by owner and ID. The provider projection can include the validated user-defined name, movement, equipment, primary region, target region, day, position, and planned sets and reps within the closed prescription grammar. For example, a saved `8–12` projects as `8-12` and `30 sn` stays `30 sn`. It omits opaque IDs and arbitrary prescription prose. This extension uses the existing Training READ path. The separate D1 authorization and provider-dispatch fences come from #548 in the eventual composition. Coach WRITE stays off.

The unpublished migration `e5b27aa4101c` adds `private_exercise` with a required `target_region` after `c1a9b7e5d301`. Upgrade from C1 needs no backfill. Fresh startup verifies the current model-created table. Use a fresh disposable database for environments that ran an earlier draft of this revision. Preserve the registry and its owner-aware readers once plans contain private IDs. The migration refuses downgrading a populated registry.
