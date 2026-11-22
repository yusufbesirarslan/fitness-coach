# Exercise Catalog V3

Source-only foundation (founder order #547, stacked on #549). Nothing is merged, deployed or activated by this document.

## What changed

- **Catalog version 3.** `exercises.json` grows from 89 to 205 active canonical IDs. The 89 V2 lines are byte-identical except two reviewed alias additions (`Dumbbell Curl`, `Dumbbell Thruster`) that keep Coach grounding exact. No ID was reused, renamed, retired or re-classified in its legacy fields. `primary_region`, `movement` and cardio semantics keep their generation/validation meaning.
- **Equipment vocabulary** gains `smith_machine` and `ez_bar`. The gym context (`spor_salonu`) admits them; `ev` and `minimal` do not.
- **Additive taxonomy** `exercise_taxonomy_v3.json` (`app/services/exercise_catalog_v3.py`) adds 13 target regions, 126 exercise families and one equipment variant per member. The loader refuses any drift: an active ID outside exactly one family, a variant whose equipment the exercise does not require, duplicate equipment in a family, a cardio identity outside `cardio`, a private-namespace ID, a "Baldır" label, or an overlay for another catalog version.
- **Generation is unchanged.** The 116 V3 additions carry `"generation": false`, so the AI generator's closed choice set is exactly the V2 set (pinned by test). Admitting them is a separate reviewed overlay change after a real-provider qualification.
- **Signed drafts.** Browser drafts signed against catalog 1 or 2 still verify on save; issuing always uses the live version and a newer version is refused. Every reference is still re-resolved live.

## Target regions

| Key | TR | EN |
|---|---|---|
| `chest` | Göğüs | Chest |
| `back` | Sırt | Back |
| `shoulders` | Omuz | Shoulders |
| `biceps` | Biceps | Biceps |
| `triceps` | Triceps | Triceps |
| `quads` | Bacak (Quad) | Legs (Quads) |
| `hamstrings` | Bacak (Hamstring) | Legs (Hamstrings) |
| `glutes` | Kalça | Glutes |
| `calves` | Calf | Calves |
| `core` | Karın / Core | Core |
| `full_body` | Tüm Vücut | Full Body |
| `cardio` | Kardiyo | Cardio |
| `mobility` | Mobilite | Mobility |

One primary region per family, chosen by principal training purpose. Reviewed ambiguous calls (one data edit each to change):

- Conventional deadlift → `back`; rack pull → `back`; RDL / stiff-leg / good morning → `hamstrings`; sumo deadlift and sumo squat → `glutes`.
- Close-grip bench press → `triceps`; dip → `chest`; seated dip machine and bench dip → `triceps`.
- Hip adduction → `glutes` (kept beside abduction; there is no adductor region by design).
- Suitcase carry → `core`; farmer carry and kettlebell swing → `full_body`.
- Single-leg balance and hip hinge drill → `mobility` (technique drills, not loading).

Legacy aliases containing "Baldır" stay search/resolution aliases so stored plan names keep resolving. They are never a display label. No new alias or label uses it.

## Families and equipment

A family groups variants with meaningful functional similarity (Bench Press: Barbell / Dumbbell / Machine / Smith Machine). Technique or angle changes are separate families (Paused Back Squat, Incline Bench Press, Bulgarian Split Squat). `(family_id, equipment)` resolves to exactly one canonical ID. Anything else fails closed (`FamilyVariantUnknown`). The edit never substitutes one variant for another.

- **Variant equipment:** the dropdown value. It is one of the exercise's required items, or `bodyweight` when nothing loads the movement (so Pull-Up is `bodyweight` with a pull-up bar).
- **Required equipment:** everything the exercise needs at once (barbell + rack + bench). This, not the variant, is checked against the plan.
- **Plan equipment context:** the saved plan's `ev` / `minimal` / `spor_salonu` + cardio type. It is never changed by the picker or the edit.

## Mobile picker contract — `GET /api/v1/training/exercise-families`

Same bearer, eligibility and first-party consent boundary as the editor; non-cacheable; no provider call. A separate resource, so the strict V1 `/training/exercise-catalog` decoder (exact keys) is untouched. V1 still answers its five keys and legacy `primary_region` values; it now lists 205 IDs and `catalog_version: 3`, both of which the shipped decoder accepts (unknown equipment tokens are kept as strings, unknown regions map to null).

Query: `q` (optional, ≤80 chars, same accent-insensitive TR/EN matching as V1), `region` (optional, one of the 13 keys). Any other parameter, a repeated parameter or an unknown region → 400 `TRAINING_EDITOR_INVALID_REQUEST`. Catalog or storage fault → 503 `TRAINING_EDITOR_UNAVAILABLE` (retryable).

```json
{
  "contract_version": 1,
  "catalog_version": 3,
  "taxonomy_version": 1,
  "plan_equipment_context": "minimal",
  "regions": [{"key": "chest", "label_tr": "Göğüs", "label_en": "Chest"}],
  "equipment_labels": [{"key": "dumbbell", "label_tr": "Dumbbell", "label_en": "Dumbbell"}],
  "families": [{
    "family_id": "fam_shoulder_press",
    "display_name_tr": "Shoulder Press",
    "display_name_en": "Shoulder Press",
    "target_region": "shoulders",
    "default_exercise_id": "ex_dumbbell_shoulder_press",
    "variants": [{
      "exercise_id": "ex_machine_shoulder_press",
      "equipment": "machine",
      "display_name_tr": "Machine Shoulder Press",
      "display_name_en": "Machine Shoulder Press",
      "required_equipment": ["machine"],
      "availability": "equipment_unavailable"
    }]
  }]
}
```

- Empty `q` browses every family in region order, then name. A search orders families by their best-matching variant; `default_exercise_id` is that variant (preselect it in the dropdown). No match → `families: []`.
- `availability` is advisory: `available`, `equipment_unavailable` (the plan's equipment context lacks a required item), `cardio_unavailable` (the plan's cardio type does not admit this modality), or `null` when the owner has no plan with a verified context. The edit re-checks everything under the plan lock; day-level rules (cardio only on cardio days, 32 per day, no duplicates) still come back from the write.
- Names: TR and EN display names are the curated canonical gym names (as in the founder examples). Region and equipment labels are Turkish where the founder table is. All of these are data and can change without a contract change.

**Suggested flow (owner of mobile #212 to implement):** Add/Replace → search field + region chips → family row (`display_name`, subtitle `<region label> · <equipment label>`) → equipment dropdown with only that family's variants (hide it when there is one) → sets/reps → existing `POST /training/editor/add` or `/replace` with the chosen variant's `exercise_id`. Changing equipment on an existing row is an atomic `replace` with `replacement_id` = the other variant, keeping the server-confirmed revision, stale-revision (409), retry-same-key and Undo semantics exactly as today. For an unavailable variant, keep it visible but disabled with "Bu ekipman mevcut planının ekipman profilinde yok", and never change the plan context from the picker. A preference/equipment change remains the existing plan-regeneration path.

No write contract changes. The editor already accepts any active catalog ID compatible with the plan's context.

## Coach READ contract

`get_current_training_plan` (B1, still behind `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED`, D1/eligibility unchanged) now reports, per slot:

| Field | When | Source |
|---|---|---|
| `name`, `sets`, `reps` | always (unchanged rules) | stored plan, catalog-vouched |
| `position` | always | index in the stored day (the editor's row index) |
| `exercise_id`, `source: "curated"`, `family_id`, `target_region`, `equipment` | stored `exercise_id` is a curated catalog ID | server taxonomy for that ID |

The day comes from the enclosing day entry. `coach_privacy.tool_result` rebuilds these fields from the ID on the transfer boundary. Forged `family_id` / `target_region` / `equipment` / `source` or extra keys in the domain dict never reach a provider. The read is always the owner's latest canonical plan (`get_active_plan`), so an editor equipment change is what the next read reports. Planned prescriptions stay separate from performed history. No muscle-activation claim is published, no provider call is added, and Coach WRITE stays off and separate.

## Owner-private exercise boundary

The private workstream (`ex_private_` namespace, owner-scoped rows) stays outside the curated catalog and outside generation. V3 reserves the prefix: a curated or taxonomy ID starting with `ex_private_` fails the load. Integration, owned by the private-exercise owner:

1. Registration captures a V3 `target_region` (one of the 13 keys) plus `equipment`, and derives the legacy `primary_region` with `exercise_catalog_v3.LEGACY_REGION_FOR`.
2. Picker: owner-private results come from the private browse route, rendered with the same row shape; they are their own family (single variant) and never appear in `/exercise-families`.
3. Coach: the private branch adds `source: "owner_private"` + validated user metadata in `plan_read` *and* `coach_privacy`. Today an `ex_private_` slot gets no V3 fields (pinned by test), so nothing leaks before that lands.

## HOLD and post-R1 integration

- **Cardio modalities (treadmill, elliptical, rowing ergometer): HOLD.** They need a product decision on which `cardio_type` admits them (`kosu` ⊇ treadmill?) and a `CARDIO_EQUIPMENT` change that alters generation and scheduling. No identity is added, so placement and scheduling gates are untouched.
- **Shrugs, wrist/forearm work, burpees, ab wheel:** not added. There is no honest movement/equipment vocabulary for them yet, and inventing metadata was the bigger risk.
- **Post-R1 (#546/#548 stack):** verified at #548 head `a440859d`, that stack contains main `744ca3b2` and #537 but not #549. It still runs catalog V1 (no `hip_abduction`/`hip_adduction`, no editor search), and its `exercise_context_token` accepts only an exact version match. V3 is stacked on #549 (catalog V2), so its post-R1 integration is exactly #549's post-R1 re-land followed by this branch. V3 adds no dependency on the #537 cardio-scheduling helper: no cardio identity, `CARDIO_EQUIPMENT`, placement or scheduling code changes. Integration stays HOLD until the actual post-R1 baseline exists.

## Coverage matrix

| Region (TR / EN) | Families | IDs (V2 → V3) | Family → equipment variants (new in **bold**) |
|---|---|---|---|
| Göğüs / Chest | 12 | 14 → 24 | Archer Push-Up: Bodyweight; Bench Press: Barbell, Dumbbell, Machine, **Smith Machine**; Chest Fly: Dumbbell, Machine, Cable, **Resistance Band**; Decline Bench Press: **Barbell**, **Dumbbell**; Decline Push-Up: Bodyweight; Dip: Bodyweight, **Machine**; Floor Press: Dumbbell; Incline Bench Press: Barbell, Dumbbell, **Machine**, **Smith Machine**; Incline Fly: **Dumbbell**, **Cable**; Incline Push-Up: Bodyweight; Knee Push-Up: **Bodyweight**; Push-Up: Bodyweight |
| Sırt / Back | 16 | 14 → 28 | Back Extension: **Machine**; Bent-Over Row: Barbell, **Dumbbell**, **Smith Machine**; Chest-Supported Row: Machine, **Dumbbell**; Chin-Up: Bodyweight; Close-Grip Pulldown: **Cable**; Dead Hang: Bodyweight; Deadlift: Barbell; Inverted Row: Bodyweight; Lat Pulldown: Cable, **Machine**, Resistance Band; One-Arm Row: Dumbbell, **Kettlebell**, **Cable**; Pull-Up: Bodyweight, Resistance Band, **Machine**; Rack Pull: **Barbell**; Scapular Pull-Up: Bodyweight; Seated Row: Cable, **Machine**, Resistance Band; Straight-Arm Pulldown: **Cable**; T-Bar Row: **Machine**, **Barbell** |
| Omuz / Shoulders | 10 | 8 → 24 | Arnold Press: **Dumbbell**; Band Pull-Apart: **Resistance Band**; Face Pull: Cable, **Resistance Band**; Front Raise: **Dumbbell**, **Cable**; Landmine Press: **Barbell**; Lateral Raise: Dumbbell, Cable, **Machine**, **Resistance Band**; Pike Push-Up: Bodyweight; Reverse Fly: Dumbbell, **Machine**, **Cable**; Shoulder Press: Dumbbell, Barbell, Machine, **Smith Machine**, **Kettlebell**, **Resistance Band**; Upright Row: **Barbell**, **Dumbbell**, **Cable** |
| Biceps / Biceps | 5 | 4 → 12 | Biceps Curl: Dumbbell, Barbell, **EZ Bar**, **Cable**, Resistance Band; Concentration Curl: **Dumbbell**; Hammer Curl: Dumbbell, **Cable**; Incline Curl: **Dumbbell**; Preacher Curl: **EZ Bar**, **Dumbbell**, **Machine** |
| Triceps / Triceps | 8 | 3 → 15 | Bench Dip: **Bodyweight**; Close-Grip Bench Press: Barbell, **Smith Machine**; Diamond Push-Up: **Bodyweight**; Overhead Triceps Extension: Dumbbell, **Cable**, **EZ Bar**; Seated Dip Machine: **Machine**; Skull Crusher: **EZ Bar**, **Barbell**, **Dumbbell**; Triceps Kickback: **Dumbbell**, **Cable**; Triceps Pushdown: Cable, **Resistance Band** |
| Bacak (Quad) / Legs (Quads) | 18 | 14 → 26 | Back Squat: Barbell, **Smith Machine**; Bodyweight Squat: Bodyweight; Bulgarian Split Squat: Bodyweight, **Dumbbell**; Front Squat: Barbell; Goblet Squat: Dumbbell, **Kettlebell**; Hack Squat: Machine; Jump Squat: **Bodyweight**; Lateral Lunge: **Bodyweight**; Leg Extension: Machine; Leg Press: Machine; Paused Back Squat: Barbell; Pendulum Squat: **Machine**; Reverse Lunge: **Bodyweight**, Dumbbell; Split Squat: Dumbbell, **Bodyweight**; Step-Up: Bodyweight, **Dumbbell**; Walking Lunge: Bodyweight, Dumbbell, **Barbell**; Wall Sit: **Bodyweight**; Zercher Squat: **Barbell** |
| Bacak (Hamstring) / Legs (Hamstrings) | 8 | 4 → 11 | Good Morning: **Barbell**; Lying Leg Curl: Machine; Nordic Hamstring Curl: **Bodyweight**; Romanian Deadlift: Barbell, Dumbbell, **Kettlebell**; Seated Leg Curl: Machine; Single-Leg Romanian Deadlift: **Dumbbell**; Standing Leg Curl: **Machine**; Stiff-Leg Deadlift: **Barbell**, **Dumbbell** |
| Kalça / Glutes | 9 | 4 → 17 | Glute Bridge: Bodyweight, **Barbell**; Glute Kickback: **Cable**, **Machine**; Hip Abduction: Machine, **Cable**, **Resistance Band**; Hip Adduction: Machine; Hip Thrust: Barbell, **Dumbbell**, **Machine**, **Smith Machine**; Pull-Through: **Cable**; Single-Leg Glute Bridge: **Bodyweight**; Sumo Deadlift: **Barbell**; Sumo Squat: **Dumbbell**, **Kettlebell** |
| Calf / Calves | 4 | 3 → 7 | Leg Press Calf Raise: **Machine**; Seated Calf Raise: Machine; Single-Leg Calf Raise: **Bodyweight**; Standing Calf Raise: Bodyweight, Dumbbell, **Machine**, **Smith Machine** |
| Karın / Core / Core | 18 | 8 → 21 | Bicycle Crunch: **Bodyweight**; Bird Dog: **Bodyweight**; Crunch: **Bodyweight**, **Cable**, **Machine**; Dead Bug: Bodyweight; Decline Sit-Up: **Bodyweight**; Hanging Knee Raise: **Bodyweight**; Hanging Leg Raise: **Bodyweight**; Hollow Body Hold: Bodyweight; Leg Raise: Bodyweight; Mountain Climber: Bodyweight; Pallof Press: Resistance Band, **Cable**; Plank: Bodyweight; Reverse Crunch: **Bodyweight**; Russian Twist: **Bodyweight**; Side Plank: Bodyweight; Sit-Up: **Bodyweight**; Suitcase Carry: Dumbbell; Woodchop: **Cable** |
| Tüm Vücut / Full Body | 3 | 3 → 5 | Farmer Carry: Dumbbell, **Kettlebell**; Kettlebell Swing: Kettlebell; Thruster: Dumbbell, **Barbell** |
| Kardiyo / Cardio | 5 | 5 → 5 | Cycling: Stationary Bike; Jump Rope: Jump Rope; Running: Outdoor; Swimming: Pool; Walking: Outdoor |
| Mobilite / Mobility | 10 | 5 → 10 | 90/90 Hip Switch: **Bodyweight**; Ankle Mobility: Bodyweight; Cat-Cow: **Bodyweight**; Hip Flexor Stretch: **Bodyweight**; Hip Hinge Drill: Bodyweight; Hip Mobility Flow: Bodyweight; Shoulder Dislocate: **Resistance Band**; Single-Leg Balance: Bodyweight; Thoracic Mobility: Bodyweight; World's Greatest Stretch: **Bodyweight** |
| **Total** | **126** | **89 → 205** | |

Variant equipment distribution: bodyweight 52, dumbbell 38, machine 30, barbell 25, cable 23, resistance_band 13, smith_machine 8, kettlebell 7, ez_bar 4, outdoor_running 1, outdoor_walking 1, rope 1, stationary_bicycle 1, pool 1

Families with a real equipment dropdown (≥2 variants): 46 of 126.
