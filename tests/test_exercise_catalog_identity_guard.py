"""H-I5: catalog deletion and coarse-reclassification guard (TD-01 T10).

ADR 0002 D1a makes a stored ``exercise_id`` the historical identity of a
performed-set fact, so an assigned ID must never disappear or change meaning.
This test freezes every ID assigned on ``3df0945`` (catalog version 1, 73 IDs)
with its movement and primary region, and asserts that each one is still in the
catalog with the same coarse classification. A retirement must flip ``active``,
never delete the entry.

What it does NOT prove: that an ID is never semantically re-meant. A changed
exercise with the same movement and region still passes. "Never re-mean an
existing exercise_id" remains an ADR 0002 review invariant; this guard is not a
catalog governance system and not a second source of exercise definitions.
"""
import json
from types import MappingProxyType

from app.services.exercise_catalog import CATALOG_PATH, load_exercise_catalog

# exercise_id -> (movement, primary_region), as assigned on 3df0945.
ASSIGNED = {
    "ex_barbell_back_squat": ("squat", "lower_body"),
    "ex_paused_barbell_back_squat": ("squat", "lower_body"),
    "ex_barbell_front_squat": ("squat", "lower_body"),
    "ex_goblet_squat": ("squat", "lower_body"),
    "ex_bodyweight_squat": ("squat", "lower_body"),
    "ex_hack_squat": ("squat", "lower_body"),
    "ex_leg_press": ("squat", "lower_body"),
    "ex_leg_extension": ("squat", "lower_body"),
    "ex_bulgarian_split_squat": ("lunge", "lower_body"),
    "ex_dumbbell_split_squat": ("lunge", "lower_body"),
    "ex_walking_lunge": ("lunge", "lower_body"),
    "ex_dumbbell_walking_lunge": ("lunge", "lower_body"),
    "ex_step_up": ("lunge", "lower_body"),
    "ex_single_leg_balance": ("mobility", "lower_body"),
    "ex_barbell_deadlift": ("hinge", "lower_body"),
    "ex_barbell_romanian_deadlift": ("hinge", "lower_body"),
    "ex_dumbbell_romanian_deadlift": ("hinge", "lower_body"),
    "ex_lying_leg_curl": ("hinge", "lower_body"),
    "ex_kettlebell_swing": ("hinge", "full_body"),
    "ex_glute_bridge": ("hinge", "lower_body"),
    "ex_hip_hinge_drill": ("hinge", "lower_body"),
    "ex_barbell_hip_thrust": ("hinge", "lower_body"),
    "ex_barbell_bench_press": ("horizontal_push", "chest"),
    "ex_close_grip_barbell_bench_press": ("horizontal_push", "chest"),
    "ex_incline_dumbbell_press": ("horizontal_push", "chest"),
    "ex_dumbbell_chest_fly": ("horizontal_push", "chest"),
    "ex_pec_deck_fly": ("horizontal_push", "chest"),
    "ex_dumbbell_floor_press": ("horizontal_push", "chest"),
    "ex_push_up": ("horizontal_push", "chest"),
    "ex_archer_push_up": ("horizontal_push", "chest"),
    "ex_incline_push_up": ("horizontal_push", "chest"),
    "ex_decline_push_up": ("horizontal_push", "chest"),
    "ex_dip": ("dip", "chest"),
    "ex_dumbbell_shoulder_press": ("vertical_push", "shoulders"),
    "ex_dumbbell_squat_to_press": ("vertical_push", "full_body"),
    "ex_pike_push_up": ("vertical_push", "shoulders"),
    "ex_lateral_raise": ("vertical_push", "shoulders"),
    "ex_barbell_row": ("horizontal_pull", "back"),
    "ex_dumbbell_row": ("horizontal_pull", "back"),
    "ex_band_row": ("horizontal_pull", "back"),
    "ex_inverted_row": ("horizontal_pull", "back"),
    "ex_reverse_dumbbell_fly": ("horizontal_pull", "shoulders"),
    "ex_seated_cable_row": ("horizontal_pull", "back"),
    "ex_pull_up": ("vertical_pull", "back"),
    "ex_assisted_pull_up": ("vertical_pull", "back"),
    "ex_chin_up": ("vertical_pull", "back"),
    "ex_scapular_pull_up": ("vertical_pull", "back"),
    "ex_dead_hang": ("vertical_pull", "back"),
    "ex_band_pulldown": ("vertical_pull", "back"),
    "ex_lat_pulldown": ("vertical_pull", "back"),
    "ex_dumbbell_biceps_curl": ("curl", "arms"),
    "ex_hammer_curl": ("curl", "arms"),
    "ex_band_biceps_curl": ("curl", "arms"),
    "ex_triceps_pushdown": ("horizontal_push", "arms"),
    "ex_overhead_dumbbell_triceps_extension": ("vertical_push", "arms"),
    "ex_calf_raise": ("calf_raise", "calves"),
    "ex_dumbbell_calf_raise": ("calf_raise", "calves"),
    "ex_plank": ("anti_extension", "core"),
    "ex_hollow_hold": ("anti_extension", "core"),
    "ex_dead_bug": ("anti_extension", "core"),
    "ex_pallof_press": ("anti_rotation", "core"),
    "ex_leg_raise": ("core_dynamic", "core"),
    "ex_mountain_climber": ("core_dynamic", "core"),
    "ex_farmer_carry": ("carry", "full_body"),
    "ex_suitcase_carry": ("carry", "full_body"),
    "ex_hip_mobility_flow": ("mobility", "mobility"),
    "ex_thoracic_mobility": ("mobility", "mobility"),
    "ex_ankle_mobility": ("mobility", "mobility"),
    "ex_outdoor_run": ("cardio", "cardio"),
    "ex_brisk_walk": ("cardio", "cardio"),
    "ex_jump_rope": ("cardio", "cardio"),
    "ex_stationary_cycling": ("cardio", "cardio"),
    "ex_swimming": ("cardio", "cardio"),
}


def _violations(catalog_by_id):
    found = []
    for exercise_id, (movement, region) in ASSIGNED.items():
        current = catalog_by_id.get(exercise_id)
        if current is None:
            found.append(f"deleted:{exercise_id}")
        elif (current.movement, current.primary_region) != (movement, region):
            found.append(f"reclassified:{exercise_id}")
    return found


def test_frozen_list_is_the_complete_3df0945_assignment():
    assert len(ASSIGNED) == 73


def test_every_assigned_id_survives_with_its_coarse_classification():
    assert _violations(load_exercise_catalog().by_id) == []


def test_the_on_disk_asset_still_holds_every_assigned_id():
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    on_disk = {entry["exercise_id"] for entry in raw["exercises"]}
    assert set(ASSIGNED) <= on_disk


def test_guard_is_non_vacuous():
    from dataclasses import replace
    by_id = dict(load_exercise_catalog().by_id)
    deleted = MappingProxyType({k: v for k, v in by_id.items() if k != "ex_barbell_back_squat"})
    assert _violations(deleted) == ["deleted:ex_barbell_back_squat"]
    moved = dict(by_id)
    moved["ex_barbell_bench_press"] = replace(moved["ex_barbell_bench_press"], movement="hinge")
    assert _violations(MappingProxyType(moved)) == ["reclassified:ex_barbell_bench_press"]
    retired = dict(by_id)
    retired["ex_barbell_row"] = replace(retired["ex_barbell_row"], active=False)
    assert _violations(MappingProxyType(retired)) == []
