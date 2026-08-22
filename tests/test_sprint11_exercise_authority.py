"""Sprint 11 PR4 - canonical exercise catalog and exact resolution."""

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import re
from types import MappingProxyType

import pytest

from app.services import exercise_catalog
from app.services.exercise_catalog import (
    CatalogConfigurationError,
    ExerciseAmbiguous,
    ExerciseCatalog,
    ExerciseContext,
    ExerciseDefinition,
    ExerciseIdentityInvalid,
    ExerciseInactive,
    ExerciseUnresolved,
    compatible_exercises,
    is_exercise_compatible,
    load_exercise_catalog,
    normalize_exercise_lookup,
    resolve_exercise,
)
from app.services.training_generation import exercise_context_token
from app.services.training_generation.exercise_context_token import (
    ExerciseContextInvalid,
    sign_exercise_context,
    verify_exercise_context,
)
from app.services.training_generation.prompt_builder import canonical_exercise_vocabulary


EXPECTED_EQUIPMENT = frozenset({
    "barbell", "bench", "bodyweight", "cable", "cardio_machine",
    "dumbbell", "kettlebell", "machine", "outdoor_running", "outdoor_walking",
    "pool", "pull_up_bar", "rack", "resistance_band", "rope",
    "stationary_bicycle",
})
EXPECTED_MOVEMENTS = frozenset({
    "anti_extension", "anti_rotation", "calf_raise", "carry", "cardio",
    "core_dynamic", "curl", "dip", "hinge", "horizontal_pull",
    "horizontal_push", "lunge", "mobility", "squat", "vertical_pull",
    "vertical_push",
})
EXPECTED_REGIONS = frozenset({
    "arms", "back", "calves", "cardio", "chest", "core", "full_body",
    "lower_body", "mobility", "shoulders",
})
ENTRY_KEYS = {
    "exercise_id", "canonical_name", "aliases", "equipment", "movement",
    "primary_region", "active",
}


def _valid_asset():
    return {
        "version": 1,
        "exercises": [{
            "exercise_id": "ex_test_squat",
            "canonical_name": "Test Squat",
            "aliases": ["Fixture Squat"],
            "equipment": ["bodyweight"],
            "movement": "squat",
            "primary_region": "lower_body",
            "active": True,
        }],
    }


@pytest.fixture
def catalog_asset_path(tmp_path, monkeypatch):
    path = tmp_path / "exercises.json"
    monkeypatch.setattr(exercise_catalog, "CATALOG_PATH", path)
    load_exercise_catalog.cache_clear()
    yield path
    load_exercise_catalog.cache_clear()


def _write_asset(path, asset):
    path.write_text(json.dumps(asset), encoding="utf-8")


def test_catalog_has_stable_product_owned_identity():
    """A display rename must not change the catalog-owned exercise ID."""
    catalog = load_exercise_catalog()
    squat = catalog.by_id["ex_barbell_back_squat"]

    assert squat.canonical_name == "Barbell Back Squat"
    assert replace(squat, canonical_name="Back Squat").exercise_id == (
        "ex_barbell_back_squat"
    )


def test_real_catalog_has_valid_closed_shape_and_unique_lookup_keys():
    """Bad IDs, metadata, active flags, or aliases must fail catalog review."""
    raw = json.loads(exercise_catalog.CATALOG_PATH.read_text(encoding="utf-8"))
    catalog = load_exercise_catalog()

    assert set(raw) == {"version", "exercises"}
    assert raw["version"] == catalog.version == 1
    assert len(raw["exercises"]) == len(catalog.exercises)

    ids = []
    normalized_names = []
    for entry in raw["exercises"]:
        assert set(entry) == ENTRY_KEYS
        assert re.fullmatch(r"ex_[a-z0-9_]+", entry["exercise_id"])
        assert entry["canonical_name"].strip()
        assert isinstance(entry["aliases"], list)
        assert entry["equipment"]
        assert set(entry["equipment"]) <= EXPECTED_EQUIPMENT
        assert entry["movement"] in EXPECTED_MOVEMENTS
        assert entry["primary_region"] in EXPECTED_REGIONS
        assert type(entry["active"]) is bool
        ids.append(entry["exercise_id"])
        normalized_names.extend(
            normalize_exercise_lookup(value)
            for value in (entry["canonical_name"], *entry["aliases"])
        )

    assert len(ids) == len(set(ids))
    assert len(normalized_names) == len(set(normalized_names))
    assert len(catalog.by_lookup) == sum(
        1 + len(entry.aliases) for entry in catalog.exercises
    )


def test_catalog_contains_bounded_core_capability_matrix():
    """Removing a supported style/equipment/cardio anchor breaks the product."""
    expected_ids = {
        "ex_barbell_back_squat", "ex_barbell_bench_press",
        "ex_barbell_deadlift", "ex_goblet_squat", "ex_push_up", "ex_pull_up",
        "ex_chin_up", "ex_dip", "ex_glute_bridge", "ex_plank",
        "ex_hollow_hold", "ex_farmer_carry", "ex_kettlebell_swing",
        "ex_step_up", "ex_pallof_press", "ex_outdoor_run", "ex_brisk_walk",
        "ex_jump_rope", "ex_stationary_cycling", "ex_swimming",
    }

    assert expected_ids <= set(load_exercise_catalog().by_id)


@pytest.mark.parametrize(("name", "exercise_id"), [
    ("Squat", "ex_barbell_back_squat"),
    ("Back Squat", "ex_barbell_back_squat"),
    ("Bench Press", "ex_barbell_bench_press"),
    ("Deadlift", "ex_barbell_deadlift"),
    ("Row", "ex_barbell_row"),
    ("Push-up", "ex_push_up"),
    ("Goblet Squat", "ex_goblet_squat"),
    ("Curl", "ex_dumbbell_biceps_curl"),
    ("Paused Squat", "ex_paused_barbell_back_squat"),
    ("Close Grip Bench", "ex_close_grip_barbell_bench_press"),
    ("Dumbbell Chest Fly", "ex_dumbbell_chest_fly"),
    ("Pec Deck", "ex_pec_deck_fly"),
    ("Rear Delt Fly", "ex_reverse_dumbbell_fly"),
    ("Hack Squat", "ex_hack_squat"),
    ("Leg Press", "ex_leg_press"),
    ("Leg Curl", "ex_lying_leg_curl"),
    ("Leg Extension", "ex_leg_extension"),
    ("Dead Hang", "ex_dead_hang"),
    ("Single-Leg Balance", "ex_single_leg_balance"),
    ("Archer Push-Up", "ex_archer_push_up"),
    ("Squat to Press", "ex_dumbbell_squat_to_press"),
    ("Hip Hinge Drill", "ex_hip_hinge_drill"),
])
def test_catalog_resolves_current_fixtures_and_concrete_few_shot_names(
    name, exercise_id,
):
    """A prompt-owned concrete exercise name must have one reviewed identity."""
    assert resolve_exercise(name=name).exercise_id == exercise_id


def test_catalog_values_and_indexes_are_immutable():
    """Callers must not be able to rewrite code-owned exercise authority."""
    catalog = load_exercise_catalog()
    exercise = catalog.by_id["ex_barbell_back_squat"]

    with pytest.raises(FrozenInstanceError):
        exercise.canonical_name = "Client Squat"
    with pytest.raises(TypeError):
        catalog.by_id[exercise.exercise_id] = replace(
            exercise, canonical_name="Client Squat",
        )
    with pytest.raises(AttributeError):
        exercise.equipment.add("dumbbell")
    with pytest.raises(FrozenInstanceError):
        catalog.version = 2
    with pytest.raises(FrozenInstanceError):
        ExerciseContext(equipment_context="ev").cardio_type = "kosu"


def test_loader_caches_the_validated_catalog(catalog_asset_path):
    """Catalog reads stay bounded and cannot drift within one process."""
    asset = _valid_asset()
    _write_asset(catalog_asset_path, asset)
    first = load_exercise_catalog()
    _write_asset(catalog_asset_path, {"invalid": True})

    assert load_exercise_catalog() is first


def test_loader_rejects_unknown_root_and_entry_keys(catalog_asset_path):
    root = _valid_asset()
    root["provider_entries"] = []
    _write_asset(catalog_asset_path, root)
    with pytest.raises(CatalogConfigurationError):
        load_exercise_catalog()

    load_exercise_catalog.cache_clear()
    entry = _valid_asset()
    entry["exercises"][0]["client_name"] = "Override"
    _write_asset(catalog_asset_path, entry)
    with pytest.raises(CatalogConfigurationError):
        load_exercise_catalog()


@pytest.mark.parametrize(("field", "bad_value"), [
    ("version", 0),
    ("version", True),
    ("version", "1"),
    ("exercises", []),
    ("exercises", {}),
])
def test_loader_rejects_malformed_catalog_root_field(
    catalog_asset_path, field, bad_value,
):
    asset = _valid_asset()
    asset[field] = bad_value
    _write_asset(catalog_asset_path, asset)

    with pytest.raises(CatalogConfigurationError):
        load_exercise_catalog()


@pytest.mark.parametrize(("field", "bad_value"), [
    ("exercise_id", "test_squat"),
    ("exercise_id", 1),
    ("canonical_name", "   "),
    ("canonical_name", []),
    ("aliases", "Fixture Squat"),
    ("aliases", [""]),
    ("aliases", ["Fixture Squat", "fixture squat"]),
    ("equipment", []),
    ("equipment", ["teleporter"]),
    ("equipment", ["bodyweight", "bodyweight"]),
    ("equipment", [["bodyweight"]]),
    ("movement", "teleport"),
    ("movement", []),
    ("primary_region", "unknown"),
    ("primary_region", []),
    ("active", 1),
    ("active", "true"),
])
def test_loader_rejects_every_malformed_exercise_field(
    catalog_asset_path, field, bad_value,
):
    """Malformed code-owned data always raises the catalog domain error."""
    asset = _valid_asset()
    asset["exercises"][0][field] = bad_value
    _write_asset(catalog_asset_path, asset)

    with pytest.raises(CatalogConfigurationError):
        load_exercise_catalog()


def test_loader_rejects_missing_entry_field(catalog_asset_path):
    asset = _valid_asset()
    del asset["exercises"][0]["active"]
    _write_asset(catalog_asset_path, asset)

    with pytest.raises(CatalogConfigurationError):
        load_exercise_catalog()


def test_loader_rejects_normalized_name_or_alias_collision(catalog_asset_path):
    asset = _valid_asset()
    duplicate = dict(asset["exercises"][0])
    duplicate.update({
        "exercise_id": "ex_second_squat",
        "canonical_name": "Second Squat",
        "aliases": ["  FIXTURE   SQUAT "],
    })
    asset["exercises"].append(duplicate)
    _write_asset(catalog_asset_path, asset)

    with pytest.raises(CatalogConfigurationError):
        load_exercise_catalog()


def test_loader_wraps_invalid_utf8_as_catalog_configuration_error(
    catalog_asset_path,
):
    catalog_asset_path.write_bytes(b"\xff")

    with pytest.raises(CatalogConfigurationError):
        load_exercise_catalog()


@pytest.mark.parametrize("raw", [
    "  BENCH   PRESS ",
    "Bench\u2013Press",
    "\uff22\uff45\uff4e\uff43\uff48 Press",
])
def test_safe_variants_resolve_to_bench_press(raw):
    """Casing, spacing, NFKC, and hyphen variants remain exact resolution."""
    assert resolve_exercise(name=raw).exercise_id == "ex_barbell_bench_press"


def test_normalization_preserves_words_and_only_canonicalizes_safe_variants():
    assert normalize_exercise_lookup(
        "  \uff22\uff45\uff4e\uff43\uff48\u2013\uff30\uff52\uff45\uff53\uff53  "
    ) == "bench-press"
    assert normalize_exercise_lookup("Incline Bench Press") == (
        "incline bench press"
    )


def test_semantically_distinct_name_is_not_fuzzy_matched():
    with pytest.raises(ExerciseUnresolved):
        resolve_exercise(name="Incline Benhc Press")


def test_unknown_supplied_id_does_not_fall_back_to_valid_name():
    with pytest.raises(ExerciseIdentityInvalid):
        resolve_exercise(exercise_id="ex_fake", name="Bench Press")


@pytest.mark.parametrize("bad_id", [1, [], {}])
def test_malformed_supplied_id_is_a_typed_identity_failure(bad_id):
    """Malformed provider/client identity cannot leak a generic Python error."""
    with pytest.raises(ExerciseIdentityInvalid):
        resolve_exercise(exercise_id=bad_id, name="Bench Press")


def test_valid_id_is_authoritative_over_a_tampered_name():
    resolved = resolve_exercise(
        exercise_id="ex_barbell_bench_press",
        name="Client Renamed Exercise",
    )

    assert resolved.canonical_name == "Barbell Bench Press"


@pytest.mark.parametrize("missing_name", [None, "", "   ", 7, []])
def test_missing_or_malformed_name_is_unresolved(missing_name):
    with pytest.raises(ExerciseUnresolved):
        resolve_exercise(name=missing_name)


def _constructed_exercise(exercise_id, canonical_name, *, active=True):
    return ExerciseDefinition(
        exercise_id=exercise_id,
        canonical_name=canonical_name,
        aliases=(),
        equipment=frozenset({"bodyweight"}),
        movement="squat",
        primary_region="lower_body",
        active=active,
    )


def _constructed_catalog(exercises, by_lookup):
    return ExerciseCatalog(
        version=1,
        exercises=tuple(exercises),
        by_id=MappingProxyType({
            exercise.exercise_id: exercise for exercise in exercises
        }),
        by_lookup=MappingProxyType(by_lookup),
    )


def test_ambiguous_lookup_fails_closed_instead_of_selecting_first():
    first = _constructed_exercise("ex_first", "First")
    second = _constructed_exercise("ex_second", "Second")
    catalog = _constructed_catalog(
        (first, second),
        {"shared squat": (first, second)},
    )

    with pytest.raises(ExerciseAmbiguous):
        resolve_exercise(name="Shared Squat", catalog=catalog)


@pytest.mark.parametrize("by_id", [True, False])
def test_inactive_identity_fails_closed(by_id):
    inactive = _constructed_exercise("ex_inactive", "Inactive Squat", active=False)
    catalog = _constructed_catalog(
        (inactive,),
        {"inactive squat": (inactive,)},
    )

    with pytest.raises(ExerciseInactive):
        if by_id:
            resolve_exercise(exercise_id="ex_inactive", catalog=catalog)
        else:
            resolve_exercise(name="Inactive Squat", catalog=catalog)


@pytest.mark.parametrize(("context_token", "compatible_ids", "incompatible_ids"), [
    (
        "ev",
        {"ex_push_up", "ex_bodyweight_squat", "ex_plank"},
        {"ex_goblet_squat", "ex_band_row", "ex_barbell_back_squat"},
    ),
    (
        "minimal",
        {"ex_push_up", "ex_goblet_squat", "ex_band_row"},
        {"ex_kettlebell_swing", "ex_hack_squat", "ex_barbell_back_squat"},
    ),
    (
        "spor_salonu",
        {"ex_push_up", "ex_goblet_squat", "ex_kettlebell_swing",
         "ex_hack_squat", "ex_barbell_back_squat"},
        set(),
    ),
])
def test_strength_compatibility_uses_closed_equipment_contexts(
    context_token, compatible_ids, incompatible_ids,
):
    catalog = load_exercise_catalog()
    context = ExerciseContext(equipment_context=context_token)

    assert all(
        is_exercise_compatible(catalog.by_id[exercise_id], context)
        for exercise_id in compatible_ids
    )
    assert not any(
        is_exercise_compatible(catalog.by_id[exercise_id], context)
        for exercise_id in incompatible_ids
    )


def test_multi_item_exercise_requires_every_item_in_context():
    catalog = load_exercise_catalog()
    step_up = catalog.by_id["ex_step_up"]
    assisted_pull_up = catalog.by_id["ex_assisted_pull_up"]

    assert is_exercise_compatible(
        step_up, ExerciseContext(equipment_context="spor_salonu"),
    )
    assert not is_exercise_compatible(
        step_up, ExerciseContext(equipment_context="ev"),
    )
    assert not is_exercise_compatible(
        step_up, ExerciseContext(equipment_context="minimal"),
    )
    assert is_exercise_compatible(
        assisted_pull_up, ExerciseContext(equipment_context="spor_salonu"),
    )
    assert not is_exercise_compatible(
        assisted_pull_up, ExerciseContext(equipment_context="minimal"),
    )


@pytest.mark.parametrize(("cardio_type", "exercise_id"), [
    ("kosu", "ex_outdoor_run"),
    ("yuruyus", "ex_brisk_walk"),
    ("ip_atlama", "ex_jump_rope"),
    ("bisiklet", "ex_stationary_cycling"),
    ("yuzme", "ex_swimming"),
])
def test_cardio_modality_is_explicit_and_independent_of_strength_context(
    cardio_type, exercise_id,
):
    catalog = load_exercise_catalog()
    exercise = catalog.by_id[exercise_id]

    assert is_exercise_compatible(
        exercise,
        ExerciseContext(equipment_context="ev", cardio_type=cardio_type),
    )
    assert is_exercise_compatible(
        exercise,
        ExerciseContext(equipment_context="spor_salonu", cardio_type=cardio_type),
    )
    assert not is_exercise_compatible(
        exercise,
        ExerciseContext(equipment_context="spor_salonu", cardio_type="yok"),
    )


def test_mixed_cardio_allows_supported_modalities_but_not_strength_inference():
    catalog = load_exercise_catalog()
    context = ExerciseContext(equipment_context="ev", cardio_type="karisik")
    cardio_ids = {
        "ex_outdoor_run", "ex_brisk_walk", "ex_jump_rope",
        "ex_stationary_cycling", "ex_swimming",
    }

    assert all(
        is_exercise_compatible(catalog.by_id[exercise_id], context)
        for exercise_id in cardio_ids
    )
    assert not is_exercise_compatible(
        catalog.by_id["ex_barbell_back_squat"], context,
    )


def test_unknown_equipment_or_cardio_context_fails_closed():
    catalog = load_exercise_catalog()

    assert not is_exercise_compatible(
        catalog.by_id["ex_push_up"],
        ExerciseContext(equipment_context="hotel_gym"),
    )
    assert not is_exercise_compatible(
        catalog.by_id["ex_outdoor_run"],
        ExerciseContext(equipment_context="ev", cardio_type="rowing_erg"),
    )


def test_compatible_exercises_excludes_inactive_entries(catalog_asset_path):
    asset = _valid_asset()
    inactive = dict(asset["exercises"][0])
    inactive.update({
        "exercise_id": "ex_inactive_fixture",
        "canonical_name": "Inactive Fixture",
        "aliases": [],
        "active": False,
    })
    asset["exercises"].append(inactive)
    _write_asset(catalog_asset_path, asset)

    compatible_ids = {
        exercise.exercise_id
        for exercise in compatible_exercises(ExerciseContext(equipment_context="ev"))
    }

    assert compatible_ids == {"ex_test_squat"}


# ── canonical_exercise_vocabulary (prompt-side hint, not an authority) ──────


def test_prompt_vocabulary_is_bounded():
    names = canonical_exercise_vocabulary(
        ExerciseContext(equipment_context="spor_salonu")
    )
    assert len("\n".join(names)) <= 8000


def test_prompt_vocabulary_is_sorted_and_deduplicated():
    names = canonical_exercise_vocabulary(ExerciseContext(equipment_context="ev"))
    assert names == tuple(sorted(names))
    assert len(names) == len(set(names))


def test_prompt_vocabulary_excludes_aliases_ids_and_equipment_metadata():
    context = ExerciseContext(equipment_context="spor_salonu")
    names = canonical_exercise_vocabulary(context)

    for exercise in compatible_exercises(context):
        assert exercise.canonical_name in names
        for alias in exercise.aliases:
            if alias != exercise.canonical_name:
                assert alias not in names
        assert exercise.exercise_id not in names
    for equipment_item in EXPECTED_EQUIPMENT:
        assert equipment_item not in names


def test_prompt_vocabulary_filters_by_equipment_context(catalog_asset_path):
    asset = _valid_asset()
    barbell_only = dict(asset["exercises"][0])
    barbell_only.update({
        "exercise_id": "ex_fixture_barbell_row",
        "canonical_name": "Fixture Barbell Row",
        "aliases": [],
        "equipment": ["barbell"],
    })
    asset["exercises"].append(barbell_only)
    _write_asset(catalog_asset_path, asset)

    home_names = canonical_exercise_vocabulary(ExerciseContext(equipment_context="ev"))
    gym_names = canonical_exercise_vocabulary(
        ExerciseContext(equipment_context="spor_salonu")
    )

    assert "Test Squat" in home_names
    assert "Fixture Barbell Row" not in home_names
    assert "Test Squat" in gym_names
    assert "Fixture Barbell Row" in gym_names


# ── Signed exercise-context token (Task 4) ─────────────────────────────────
#
# The token is the ONLY way the server-accepted equipment truth survives the
# round trip from POST /training-plan to POST /training-plan/save. It is a
# transport integrity device, not a capability grant: everything it carries is
# re-validated against the catalog on arrival. These tests pin the closed
# rejection surface, because every hole in it is a way for a client to declare
# its own equipment context.


def _mint(payload, secret="test-secret-key", version=None):
    """Forge a correctly SIGNED token around an arbitrary payload.

    Signing here (rather than mutating a real token) is deliberate: it proves
    the payload checks are real checks and not side effects of the signature
    already failing.
    """
    version_segment = str(
        exercise_context_token.TOKEN_VERSION if version is None else version)
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    encoded = exercise_context_token._b64encode(body.encode("utf-8"))
    signature = exercise_context_token._signature(secret, version_segment, encoded)
    return f"{version_segment}.{encoded}.{signature}"


def _valid_payload(user_id=7, **overrides):
    payload = {
        "v": exercise_context_token.TOKEN_VERSION,
        "uid": user_id,
        "eq": "minimal",
        "cardio": "yok",
        "style": "functional",
        "catalog": load_exercise_catalog().version,
    }
    payload.update(overrides)
    return payload


def _tamper(segment):
    """Swap one base64url character for a different, still-legal one."""
    return ("B" if segment[0] != "B" else "C") + segment[1:]


def test_context_token_round_trip_is_user_bound():
    context = ExerciseContext("minimal", cardio_type="yok", style="functional")
    token = sign_exercise_context(context, "secret", user_id=7)
    assert verify_exercise_context(token, "secret", user_id=7) == context
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(token, "secret", user_id=8)


@pytest.mark.parametrize("mutation", ["payload", "signature", "version"])
def test_context_token_tampering_fails_closed(mutation):
    context = ExerciseContext("spor_salonu", cardio_type="kosu", style="genel")
    version, payload, signature = sign_exercise_context(
        context, "secret", user_id=11).split(".")
    if mutation == "payload":
        payload = _tamper(payload)
    elif mutation == "signature":
        signature = _tamper(signature)
    else:
        version = "2"

    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(
            f"{version}.{payload}.{signature}", "secret", user_id=11)


def test_context_token_round_trip_preserves_every_context_field():
    context = ExerciseContext(
        "ev", cardio_type="ip_atlama", style="calisthenics", catalog_version=1)
    restored = verify_exercise_context(
        sign_exercise_context(context, "secret", user_id=3), "secret", user_id=3)

    assert restored.equipment_context == "ev"
    assert restored.cardio_type == "ip_atlama"
    assert restored.style == "calisthenics"
    assert restored.catalog_version == load_exercise_catalog().version


def test_context_token_payload_carries_exactly_the_declared_keys():
    token = sign_exercise_context(ExerciseContext("ev"), "secret", user_id=4)
    _, payload, _ = token.split(".")
    decoded = json.loads(exercise_context_token._b64decode(payload))

    assert set(decoded) == {"v", "uid", "eq", "cardio", "style", "catalog"}
    assert exercise_context_token.PAYLOAD_KEYS == frozenset(decoded)


def test_context_token_is_deterministic_for_the_same_inputs():
    context = ExerciseContext("minimal")
    assert (
        sign_exercise_context(context, "secret", user_id=5)
        == sign_exercise_context(context, "secret", user_id=5)
    )


def test_context_token_rejects_a_different_secret_key():
    token = sign_exercise_context(ExerciseContext("ev"), "secret-a", user_id=6)
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(token, "secret-b", user_id=6)


@pytest.mark.parametrize("token", [None, 7, b"1.abc.def", ["1", "a", "b"], {}, True])
def test_context_token_rejects_non_string_tokens(token):
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(token, "secret", user_id=1)


def test_context_token_rejects_oversized_tokens():
    oversized = "1." + ("A" * exercise_context_token.MAX_TOKEN_CHARS) + ".AAAA"
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(oversized, "secret", user_id=1)


@pytest.mark.parametrize("token", [
    "", ".", "1", "1.abc", "1.abc.def.ghi", "1..abc", "1.abc.",
    "1.not base64.AAAA", "1.AAAA.not base64", "1.AAAA.AAAA",
])
def test_context_token_rejects_malformed_tokens(token):
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(token, "secret", user_id=1)


def test_context_token_rejects_an_unknown_version_even_when_correctly_signed():
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(
            _mint(_valid_payload(v=99), version=99), "test-secret-key", user_id=7)


@pytest.mark.parametrize("payload", [
    _valid_payload(extra="x"),
    {key: value for key, value in _valid_payload().items() if key != "style"},
])
def test_context_token_rejects_unknown_or_missing_payload_keys(payload):
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(_mint(payload), "test-secret-key", user_id=7)


@pytest.mark.parametrize("overrides", [
    {"eq": "hotel_gym"},
    {"eq": ""},
    {"eq": 1},
    {"cardio": "rowing_erg"},
    {"cardio": None},
    {"style": "yoga"},
    {"style": 3},
])
def test_context_token_rejects_unknown_vocabulary(overrides):
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(
            _mint(_valid_payload(**overrides)), "test-secret-key", user_id=7)


def test_context_token_rejects_a_catalog_version_mismatch():
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(
            _mint(_valid_payload(catalog=load_exercise_catalog().version + 1)),
            "test-secret-key", user_id=7)


@pytest.mark.parametrize("uid", [8, "7", True, None, 7.0])
def test_context_token_rejects_a_uid_that_is_not_this_exact_user(uid):
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(
            _mint(_valid_payload(uid=uid)), "test-secret-key", user_id=7)


@pytest.mark.parametrize("user_id", ["7", None, True, 7.0])
def test_context_token_rejects_a_caller_identity_that_is_not_an_int(user_id):
    token = _mint(_valid_payload())
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(token, "test-secret-key", user_id=user_id)


@pytest.mark.parametrize("secret", [None, "", b"", 5])
def test_context_token_rejects_an_unusable_secret_key(secret):
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(_mint(_valid_payload()), secret, user_id=7)
    with pytest.raises(ExerciseContextInvalid):
        sign_exercise_context(ExerciseContext("ev"), secret, user_id=7)


@pytest.mark.parametrize("context", [
    ExerciseContext("hotel_gym"),
    ExerciseContext("ev", cardio_type="rowing_erg"),
    ExerciseContext("ev", style="yoga"),
    ExerciseContext("ev", catalog_version=99),
])
def test_context_token_refuses_to_sign_a_context_it_could_never_verify(context):
    with pytest.raises(ExerciseContextInvalid):
        sign_exercise_context(context, "secret", user_id=7)


def test_context_token_module_is_stdlib_crypto_and_knows_nothing_about_http():
    source = Path(exercise_context_token.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "flask", "Flask", "current_app", "request", "jsonify",
        "http_status", "i18n", "GenerationOutputError", "translate",
    ):
        assert forbidden not in source, forbidden
    for required in ("import hmac", "import hashlib", "import base64",
                     "hmac.compare_digest", "hashlib.sha256"):
        assert required in source, required


def test_context_token_module_never_logs():
    source = Path(exercise_context_token.__file__).read_text(encoding="utf-8")
    for forbidden in ("logger", "logging", "print(", "warnings"):
        assert forbidden not in source, forbidden
