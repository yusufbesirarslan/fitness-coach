"""Provider-shape fixtures for training generation tests (PR B).

Since PR B the provider returns a chosen ``exercise_id`` per exercise and no
``isim``; the catalog writes the name. Many tests describe a week by
readable catalog names, so this converts such a week into what a provider
now actually returns.
"""
import copy

from app.services.exercise_catalog import ExerciseResolutionError, resolve_exercise


def provider_exercise_id(name):
    """The catalog ID for ``name``; an invented name gets a well-formed ID
    the catalog does not own, so "unknown exercise" tests stay unknown."""
    try:
        return resolve_exercise(name=name).exercise_id
    except ExerciseResolutionError:
        slug = "".join(ch if ch.isalnum() else "_" for ch in name.lower())
        return f"ex_{slug.strip('_')}"


def as_provider_document(document):
    """The same week in the provider shape: ``exercise_id`` first, no name."""
    converted = copy.deepcopy(document)
    for day in converted["program"]:
        day["egzersizler"] = [
            {"exercise_id": provider_exercise_id(ex["isim"]),
             **{k: v for k, v in ex.items() if k != "isim"}}
            for ex in day["egzersizler"]
        ]
    return converted
