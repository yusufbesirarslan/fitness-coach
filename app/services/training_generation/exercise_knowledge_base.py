EXERCISE_KB = {
    "push_up": {
        "pattern": "horizontal_push",
        "difficulty": 1,
        "joints": ["shoulder", "elbow", "core_stability"],
        "risk_level": "low",
        "progression": ["incline_pushup", "push_up", "decline_pushup", "weighted_pushup"],
    },
    "goblet_squat": {
        "pattern": "squat",
        "difficulty": 1,
        "joints": ["knee", "hip", "core_stability"],
        "risk_level": "low",
        "progression": ["box_squat", "goblet_squat", "front_squat", "back_squat"],
    },
    "romanian_deadlift": {
        "pattern": "hinge",
        "difficulty": 2,
        "joints": ["hip", "hamstring", "lumbar_spine"],
        "risk_level": "medium",
        "progression": ["hip_hinge_drill", "db_rdl", "barbell_rdl", "deadlift"],
    },
    "lat_pulldown": {
        "pattern": "vertical_pull",
        "difficulty": 1,
        "joints": ["shoulder", "elbow"],
        "risk_level": "low",
        "progression": ["band_pulldown", "lat_pulldown", "assisted_pullup", "pullup"],
    },
    "seated_row": {
        "pattern": "horizontal_pull",
        "difficulty": 1,
        "joints": ["shoulder", "elbow", "thoracic_spine"],
        "risk_level": "low",
        "progression": ["band_row", "seated_row", "chest_supported_row", "barbell_row"],
    },
    "dead_bug": {
        "pattern": "core_anti_extension",
        "difficulty": 1,
        "joints": ["core_stability"],
        "risk_level": "low",
        "progression": ["dead_bug", "plank", "ab_rollout"],
    },
}


REQUIRED_MOVEMENT_COVERAGE = [
    "squat",
    "hinge",
    "horizontal_push",
    "vertical_push",
    "horizontal_pull",
    "vertical_pull",
    "core_anti_extension",
    "core_anti_rotation",
]

