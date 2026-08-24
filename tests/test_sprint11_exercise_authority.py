"""Sprint 11 PR4 - canonical exercise catalog and exact resolution."""

import ast
from dataclasses import FrozenInstanceError, replace
import inspect
import io
import json
from pathlib import Path
import re
import tokenize
from types import MappingProxyType

import pytest

from app.blueprints import training as training_blueprint
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
    ID_PATTERN,
    compatible_exercises,
    is_exercise_compatible,
    load_exercise_catalog,
    normalize_exercise_lookup,
    resolve_exercise,
)
from app.services.plan_mutation import document as plan_mutation_document
from app.services import training_generation
from app.services.training_generation import exercise_context_token
from app.services.training_generation.exercise_context_token import (
    ExerciseContextInvalid,
    sign_exercise_context,
    verify_exercise_context,
)
from app.services.training_generation.exercise_resolution import (
    canonicalize_plan_exercises,
)
from app.services.training_generation.output_errors import SchemaInvalidError
from app.services.training_generation.plan_schema import (
    EXERCISE_ID_KEY,
    EXERCISE_KEYS,
    EXERCISE_KEYS_WITH_ID,
    MAX_PROVIDER_COMPLETIONS,
    PRIMARY_MAX_TOKENS,
    REPAIR_MAX_TOKENS,
)
from app.services.training_generation.prompt_builder import canonical_exercise_vocabulary
from app.services.training_generation.response_validator import validate_plan_structure


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
    # Non-ASCII, in every segment. These used to reach _signature's strict
    # ASCII encode and escape as UnicodeEncodeError - a ValueError, but not
    # an ExerciseContextInvalid, so the save layer let it out as a 500 and
    # the error store captured the raw token from the frame locals.
    "1.ab\u00fccd.AAAA",            # Latin-1 accented, payload segment
    "1.\U0001f600AAA.AAAA",         # emoji, payload segment
    "1.\ud800AAA.AAAA",             # lone surrogate, payload segment
    "1.AAAA.ab\u00fccd",            # Latin-1 accented, signature segment
    "1.AAAA.\U0001f600AAA",         # emoji, signature segment
    "1.AAAA.\ud800AAA",             # lone surrogate, signature segment
    "\u0661.AAAA.AAAA",             # Arabic-Indic digit one, version segment
    "\U0001f600.AAAA.AAAA",         # emoji, version segment
    "\ud800.AAAA.AAAA",             # lone surrogate, version segment
])
def test_context_token_rejects_malformed_tokens(token):
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(token, "secret", user_id=1)


@pytest.mark.parametrize("token", [
    "1.ab\u00fccd.AAAA", "1.\U0001f600AAA.AAAA", "1.\ud800AAA.AAAA",
    "1.AAAA.ab\u00fccd", "\u0661.AAAA.AAAA",
])
def test_context_token_never_raises_an_untyped_error_for_non_ascii(token):
    """The typed contract is the whole point: exactly one exception type out.

    Anything else escapes ``resolve_save_exercise_context``'s
    ``except ExerciseContextInvalid`` and becomes a 500 with the raw token in
    the operator's error store.
    """
    try:
        verify_exercise_context(token, "secret", user_id=1)
    except ExerciseContextInvalid:
        return
    except BaseException as exc:  # pragma: no cover - the regression itself
        raise AssertionError(
            "non-ASCII token escaped as " + type(exc).__name__) from exc
    raise AssertionError("non-ASCII token was accepted")


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


# ---------------------------------------------------------------------------
# Task 6 — one authority: no legacy KB, no fuzzy path, no persistence, no SQL
# ---------------------------------------------------------------------------

CATALOG_MODULE = Path(exercise_catalog.__file__)
GENERATION_PACKAGE = Path(training_generation.__file__).parent
MUTATION_PACKAGE = Path(plan_mutation_document.__file__).parent
LEGACY_KB_PATH = GENERATION_PACKAGE / "exercise_knowledge_base.py"
SAVE_ROUTE_MODULE = Path(training_blueprint.__file__)

# Substrings that would mean a second, approximate identity authority exists.
FORBIDDEN_RESOLUTION_TECHNIQUES = ("levenshtein", "fuzzy", "difflib", "rapidfuzz")

# Modules the scan must always reach. Not the scan's input — its floor. A glob
# that silently stopped matching would otherwise turn every source guard below
# into a test that reads nothing and passes.
REQUIRED_SCANNED_MODULES = frozenset({
    "exercise_catalog.py", "exercise_resolution.py", "exercise_context_token.py",
    "prompt_builder.py", "response_validator.py", "service.py", "document.py",
    "validation.py", "plan_schema.py", "movement_coverage.py",
})

_LOGICAL_LINE_STARTS = frozenset({
    tokenize.ENCODING, tokenize.NEWLINE, tokenize.NL,
    tokenize.INDENT, tokenize.DEDENT,
})


def executable_text(source: str) -> str:
    """The module's code with comments and docstrings removed.

    A guard must fire on the behaviour, never on the sentence that forbids it:
    ``exercise_catalog`` documents "never infer or fuzzy-match intent" and
    ``plan_mutation/document`` documents "no fuzzy matching ... ever". Scanning
    raw text would flag both, and the cheapest way to make that guard green is
    to delete the two most load-bearing sentences in the codebase. String
    *literals* are kept, so ``__import__("difflib")`` still trips the scan.
    """
    kept = []
    previous = tokenize.ENCODING
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in _LOGICAL_LINE_STARTS:
            previous = tokenize.STRING
            continue
        if token.type not in _LOGICAL_LINE_STARTS:
            kept.append(token.string)
        previous = token.type
    return "\n".join(kept)


def production_exercise_source_paths():
    """Every production module that could grow a second identity resolver.

    Derived from the package directories at test time. A hand-written list is
    exactly the thing a new module escapes: adding
    ``training_generation/exercise_matcher.py`` would leave a written-out list
    silently green.
    """
    paths = {CATALOG_MODULE}
    for package in (GENERATION_PACKAGE, MUTATION_PACKAGE):
        paths.update(
            path for path in package.rglob("*.py")
            if "__pycache__" not in path.parts
        )
    return tuple(sorted(paths))


def production_exercise_sources() -> str:
    """The concatenated executable text of every exercise-authority module."""
    return "\n".join(
        executable_text(path.read_text(encoding="utf-8"))
        for path in production_exercise_source_paths()
    )


def _module_tree(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function_def(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is no longer defined where the guard expects it")


def _called_names(node):
    """``(lineno, callee name)`` for every call inside ``node``."""
    calls = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            calls.append((child.lineno, func.id))
        elif isinstance(func, ast.Attribute):
            calls.append((child.lineno, func.attr))
    return calls


def _imported_modules(tree):
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.fixture
def query_counter(app):
    """Count SQL statements actually executed against the engine.

    An event listener, deliberately not a mocked session: a mock only proves
    the code did not call the object the test replaced, while this proves no
    statement reached the database at all. ``app`` is required — the engine is
    bound to the application context.
    """
    from sqlalchemy import event

    from app.extensions import db as extension_db

    class _QueryCounter:
        def __init__(self):
            self.statements = []

        @property
        def count(self):
            return len(self.statements)

    counter = _QueryCounter()

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        counter.statements.append(statement)

    event.listen(extension_db.engine, "before_cursor_execute", record)
    try:
        yield counter
    finally:
        event.remove(extension_db.engine, "before_cursor_execute", record)


def gym_context():
    return ExerciseContext(
        "spor_salonu", cardio_type="karisik",
        style="general_fitness", catalog_version=1)


def many_exercise_plan():
    """A full week that touches every resolution path a real plan can.

    Repeated names, alias spellings and a cardio day — so a per-occurrence
    catalog load or a per-name database lookup would show up as many
    statements, not one.
    """
    def strength(day, names):
        return {
            "gun": day, "tip": "antrenman", "odak": "Full Body",
            "sure_dk": 45, "tahmini_kalori": 320,
            "egzersizler": [
                {"isim": name, "set": 3, "tekrar": "8-12",
                 "dinlenme": "90 sn", "not": ""}
                for name in names
            ],
        }

    return {
        "program": [
            strength("Pazartesi", [
                "Barbell Back Squat", "Barbell Bench Press", "Barbell Row",
                "Dumbbell Shoulder Press", "Plank", "Dead Bug",
            ]),
            strength("Salı", [
                "Barbell Deadlift", "Lat Pulldown", "Bench Press",
                "Goblet Squat", "Push-Up", "Plank",
            ]),
            {"gun": "Çarşamba", "tip": "kardiyo", "odak": "Kardiyo",
             "sure_dk": 30, "tahmini_kalori": 250,
             "egzersizler": [
                 {"isim": "Outdoor Run", "set": 1, "tekrar": "30 dk",
                  "dinlenme": "0", "not": ""},
                 {"isim": "Running", "set": 1, "tekrar": "10 dk",
                  "dinlenme": "0", "not": ""},
                 {"isim": "Jump Rope", "set": 3, "tekrar": "2 dk",
                  "dinlenme": "60 sn", "not": ""},
             ]},
            strength("Perşembe", [
                "Barbell Back Squat", "Incline Dumbbell Press", "Dumbbell Row",
                "Pull-Up", "Leg Raise", "Farmer Carry",
            ]),
            strength("Cuma", [
                "Barbell Romanian Deadlift", "Dip", "Seated Cable Row",
                "Dumbbell Lateral Raise", "Hammer Curl", "Plank",
            ]),
            {"gun": "Cumartesi", "tip": "dinlenme", "odak": "",
             "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Pazar", "tip": "dinlenme", "odak": "",
             "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
        ],
        "haftalik_ozet": {
            "yogunluk_skoru": 7, "denge_skoru": 7, "uygunluk_skoru": 7},
    }


def test_no_legacy_exercise_kb_or_fuzzy_persistence_path():
    """One authority: the pre-PR4 knowledge base is gone and nothing approximates.

    ``exercise_knowledge_base.py`` held a second, hand-written opinion about
    exercises (risk, difficulty, progression chains). It was never wired, and
    keeping a plausible-looking second table beside the real catalog is how a
    future author picks the wrong one.
    """
    assert not LEGACY_KB_PATH.exists()

    scanned = production_exercise_source_paths()
    assert REQUIRED_SCANNED_MODULES <= {path.name for path in scanned}

    source = production_exercise_sources().casefold()
    for forbidden in FORBIDDEN_RESOLUTION_TECHNIQUES:
        assert forbidden not in source, forbidden


def test_query_counter_fixture_counts_executed_sql(app, query_counter):
    """The zero-SQL guard below is only worth its name if this counts.

    A counter wired to nothing reports 0 forever and turns the next test into
    decoration, so prove the listener is attached before trusting a zero.
    """
    from app.models import TrainingPlan

    assert query_counter.count == 0
    TrainingPlan.query.filter_by(user_id=1).all()
    assert query_counter.count == 1


def test_representative_plan_resolution_executes_no_sql(app, query_counter):
    """A full week of catalog resolution touches the database zero times.

    The catalog is bundled, reviewed, version-controlled data — not a table.
    That is what makes PR4 deployable with no migration and keeps generate and
    save free of per-exercise lookups.
    """
    canonical = canonicalize_plan_exercises(many_exercise_plan(), gym_context())

    assert query_counter.count == 0, query_counter.statements
    resolved = [
        exercise for day in canonical["program"]
        for exercise in day["egzersizler"]
    ]
    assert len(resolved) == 27
    assert all(ID_PATTERN.fullmatch(exercise["exercise_id"])
               for exercise in resolved)


def test_architecture_provider_schema_never_accepts_exercise_id():
    """Generation leaves ``allow_exercise_id`` off; only save opts in.

    Structure is where a provider-authored identity would enter, so the
    default is the safe one and the two call sites are pinned individually —
    a generation path that started passing ``True`` would accept an ID the
    model invented.
    """
    parameter = inspect.signature(
        validate_plan_structure).parameters["allow_exercise_id"]
    assert parameter.default is False
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert EXERCISE_ID_KEY not in EXERCISE_KEYS
    assert EXERCISE_ID_KEY in EXERCISE_KEYS_WITH_ID

    call_sites = {}
    for path in production_exercise_source_paths():
        tree = _module_tree(path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "validate_plan_structure"):
                continue
            enclosing = next(
                parent.name for parent in ast.walk(tree)
                if isinstance(parent, ast.FunctionDef)
                and parent.lineno <= node.lineno <= (parent.end_lineno or 0)
            )
            flags = [
                keyword.value for keyword in node.keywords
                if keyword.arg == "allow_exercise_id"
            ]
            call_sites[enclosing] = (
                None if not flags else ast.literal_eval(flags[0]))

    # Exactly two call sites. A third would be a third opinion about whether
    # a caller may assert identity.
    assert call_sites == {
        "validate_generated_plan": None,   # generation: default False
        "validate_plan_for_save": True,    # save: ID is optional INPUT
    }

    generated = many_exercise_plan()
    generated["program"][0]["egzersizler"][0]["exercise_id"] = "ex_barbell_back_squat"
    with pytest.raises(SchemaInvalidError):
        validate_plan_structure(generated, require_ozet=True)


def test_architecture_catalog_never_persists():
    """The catalog is data the server ships, never a table it writes.

    No ORM import, no session, no write. If the catalog could be written at
    runtime it would stop being reviewable, version-controlled truth.
    """
    catalog_source = CATALOG_MODULE.read_text(encoding="utf-8")
    for forbidden in ("db.session", "session.add", "app.models",
                      "app.extensions", "sqlalchemy", "flask"):
        assert forbidden not in catalog_source, forbidden

    imported = _imported_modules(_module_tree(CATALOG_MODULE))
    assert not any(
        name.startswith(("app.", "flask", "sqlalchemy")) for name in imported
    ), imported

    assert "db.session.add" not in production_exercise_sources()


def test_architecture_save_validates_before_delete():
    """Verify context → validate → only then destroy the stored plan.

    ``/training-plan/save`` is the only destructive TrainingPlan path in the
    app. Ordering is the whole guarantee: a rejected payload must leave the
    user's current plan exactly as it was.
    """
    tree = _module_tree(SAVE_ROUTE_MODULE)
    save_view = _function_def(tree, "save_training_plan")
    calls = _called_names(save_view)

    def line_of(name):
        matches = [lineno for lineno, called in calls if called == name]
        assert len(matches) == 1, (name, matches)
        return matches[0]

    resolve_line = line_of("resolve_save_exercise_context")
    validate_line = line_of("validate_plan_for_save")
    delete_line = line_of("delete")

    assert resolve_line < validate_line < delete_line


def test_architecture_provider_call_budget_is_two_with_one_repair():
    """Two provider completions, one repair, and the repair is for parse only."""
    assert MAX_PROVIDER_COMPLETIONS == 2
    assert PRIMARY_MAX_TOKENS == 4000
    assert REPAIR_MAX_TOKENS == 7000

    tree = _module_tree(GENERATION_PACKAGE / "service.py")
    budget_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "complete"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "budget"
    ]
    # Primary + at most one repair. A third call site would silently raise the
    # ceiling the constant claims to set.
    assert len(budget_calls) == 2

    budget_class = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "_CompletionBudget")
    init = _function_def(budget_class, "__init__")
    default = init.args.defaults[-1]
    assert isinstance(default, ast.Name)
    assert default.id == "MAX_PROVIDER_COMPLETIONS"


def test_architecture_generation_never_imports_plan_mutation_journal():
    """Generation writes whole plans; it never reaches the mutation journal.

    ``plan_mutation`` owns versioning, the audit journal and undo. Generation
    importing any of it would mean a regenerated plan could be entangled with
    a lineage it does not belong to — the save path is a lineage reset.
    """
    generation_modules = [
        path for path in GENERATION_PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert len(generation_modules) >= 15

    for path in generation_modules:
        imported = _imported_modules(_module_tree(path))
        offenders = [
            name for name in imported
            if "plan_mutation" in name or "coach_plan" in name
        ]
        assert not offenders, (path.name, offenders)

    generation_surface = "\n".join(
        executable_text(path.read_text(encoding="utf-8"))
        for path in generation_modules
    )
    for forbidden in ("PlanMutationRecord", "undo_last_change",
                      "mutation_version", "lineage_id"):
        assert forbidden not in generation_surface, forbidden


# ---------------------------------------------------------------------------
# Task 6 — legacy plans keep working, and are never silently upgraded
# ---------------------------------------------------------------------------

LEGACY_DAY_NAMES = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]


def legacy_program(exercise_names=("Bench Press", "Bench Press")):
    """A pre-PR4 week: name-only exercises, no ``exercise_id`` anywhere."""
    program = []
    for index, day in enumerate(LEGACY_DAY_NAMES):
        if index == 0:
            program.append({
                "gun": day, "tip": "antrenman", "odak": "İtiş",
                "sure_dk": 45, "tahmini_kalori": 320,
                "egzersizler": [
                    {"isim": name, "set": 3, "tekrar": "8-12",
                     "dinlenme": "90 sn", "not": ""}
                    for name in exercise_names
                ],
            })
        else:
            program.append({
                "gun": day, "tip": "dinlenme", "odak": "",
                "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []})
    return program


def test_legacy_bare_list_and_wrapped_plans_still_load_through_the_presenter(
        app, make_user):
    """Both stored legacy shapes render; neither gains an ID on the way out."""
    from app.extensions import db
    from app.models import TrainingPlan
    from app.services.plan_facts import gather_plan_facts

    for username, plan_data in (
        ("legacy_list", json.dumps(legacy_program(), ensure_ascii=False)),
        ("legacy_wrapped",
         json.dumps({"program": legacy_program()}, ensure_ascii=False)),
    ):
        user = make_user(username)
        db.session.add(TrainingPlan(
            user_id=user.id, plan_data=plan_data, score=7))
        db.session.commit()

        facts = gather_plan_facts(user.id)
        assert facts.read_ok is True
        assert facts.parse_ok is True
        assert len(facts.days) == 7
        assert [day.label for day in facts.days] == LEGACY_DAY_NAMES
        assert [ex.name for ex in facts.days[0].exercises] == [
            "Bench Press", "Bench Press"]
        # The presenter's exercise projection has no identity field at all.
        assert not hasattr(facts.days[0].exercises[0], "exercise_id")


def test_legacy_plan_reader_projection_drops_catalog_identity(app):
    """``exercise_id`` is server-side truth; the bounded public day omits it.

    A canonical plan and its legacy twin project onto byte-identical public
    content, so adding identity to storage changed no client contract.
    """
    from datetime import date

    from app.services.workout_state.serialization import serialize_today_plan

    monday = date(2026, 8, 17)  # a Monday
    legacy = {"program": legacy_program(exercise_names=("Bench Press",))}
    canonical = json.loads(json.dumps(legacy))
    canonical["exercise_context"] = {
        "equipment_context": "spor_salonu", "cardio_type": "yok",
        "style": "general_fitness", "catalog_version": 1}
    canonical["program"][0]["egzersizler"][0]["exercise_id"] = "ex_barbell_bench_press"

    assert serialize_today_plan(legacy, monday) == serialize_today_plan(
        canonical, monday)
    assert "exercise_id" not in json.dumps(
        serialize_today_plan(canonical, monday))


def test_ambiguous_legacy_name_is_refused_not_backfilled():
    """A legacy plan is never quietly given identity, not even a correct one.

    Two identically-named entries are ambiguous, exactly as before PR4. The
    honest outcome is a refusal — resolving by position, or stamping the
    catalog ID onto both so the day becomes addressable, would both be the
    boundary inventing an answer the caller did not give it.
    """
    from app.services.plan_mutation.commands import RemoveExerciseCommand
    from app.services.plan_mutation.document import apply_command
    from app.services.plan_mutation.errors import AmbiguousExerciseTarget

    document = {"program": legacy_program()}
    with pytest.raises(AmbiguousExerciseTarget):
        apply_command(document, RemoveExerciseCommand(
            day="Pazartesi", exercise="Bench Press"))

    assert "exercise_id" not in json.dumps(document)


def test_legacy_plan_survives_a_mutation_without_gaining_an_id():
    """A resolvable legacy edit still writes the caller's name, not the catalog's."""
    from app.services.plan_mutation.commands import ReplaceExerciseCommand
    from app.services.plan_mutation.document import apply_command

    document = {"program": legacy_program(exercise_names=("Bench Press",))}
    mutated, changed = apply_command(document, ReplaceExerciseCommand(
        day="Pazartesi", exercise="Bench Press", replacement="bench pres"))

    assert changed is True
    assert mutated["program"][0]["egzersizler"][0]["isim"] == "bench pres"
    assert "exercise_id" not in json.dumps(mutated)
