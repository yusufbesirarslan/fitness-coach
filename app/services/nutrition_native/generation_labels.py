"""LP14 — display labels for the native plan-generation food picker.

``nutrition_plan_generation.FOOD_DATABASE`` is canonical business data and its
``isim`` values are the food IDENTITY (Turkish in every locale): the rating, the
prompt and every downstream rule key on them. This module never changes that.
It only maps

    canonical name  →  display label   (per supported locale, static)
    display label   →  canonical name  (any supported locale, for parsing)

so an English account sees English chips while generation still runs on the
canonical names. Immutable, import-time data — no request-time translation, no
process-global "current locale". The catalogue is validated at import: every
canonical food has exactly one label per locale, and no label can name two
different canonical foods (within or across locales), so a reverse lookup is
never a silent guess.

The browser generator does not use this module; its catalogue is unchanged.
"""
from types import MappingProxyType

from app.i18n import AVAILABLE_LOCALES
from app.services import nutrition_plan_generation as generation

DEFAULT_LOCALE = "tr"

# English UI labels for each canonical (Turkish) food name.
_ENGLISH = {
    "Tavuk Göğsü": "Chicken Breast",
    "Yumurta": "Eggs",
    "Ton Balığı": "Tuna",
    "Kırmızı Et": "Red Meat",
    "Yoğurt": "Yogurt",
    "Somon": "Salmon",
    "Mercimek": "Lentils",
    "Nohut": "Chickpeas",
    "Tofu": "Tofu",
    "Kinoa": "Quinoa",
    "Edamame": "Edamame",
    "Yulaf Ezmesi": "Oats",
    "Pirinç": "Rice",
    "Bulgur": "Bulgur",
    "Tatlı Patates": "Sweet Potato",
    "Tam Buğday Ekmeği": "Whole Wheat Bread",
    "Muz": "Banana",
    "Elma": "Apple",
    "Zeytinyağı": "Olive Oil",
    "Avokado": "Avocado",
    "Badem": "Almonds",
    "Ceviz": "Walnuts",
    "Fındık": "Hazelnuts",
}


class CatalogueContractError(RuntimeError):
    """The label catalogue does not cover / uniquely identify the foods."""


def _build():
    labels = {
        # The canonical names ARE the Turkish labels.
        "tr": {name: name for name in generation.FOOD_NAMES},
        "en": dict(_ENGLISH),
    }
    if set(labels) != set(AVAILABLE_LOCALES):
        raise CatalogueContractError("locales")
    canonical = {}
    for locale, mapping in labels.items():
        if set(mapping) != generation.FOOD_NAMES:
            raise CatalogueContractError(f"incomplete {locale} catalogue")
        if len(set(mapping.values())) != len(mapping):
            raise CatalogueContractError(f"duplicate {locale} label")
        for name, label in mapping.items():
            if not isinstance(label, str) or not label.strip():
                raise CatalogueContractError(f"empty {locale} label")
            if canonical.setdefault(label, name) != name:
                raise CatalogueContractError(f"ambiguous label {label!r}")
    return (MappingProxyType({locale: MappingProxyType(mapping)
                              for locale, mapping in labels.items()}),
            MappingProxyType(canonical))


LABELS, _CANONICAL_BY_LABEL = _build()


def catalogue_locale(language):
    """Stored ``User.language`` → picker locale.

    The same partition the generator's prompt uses (``build_prompts``: exactly
    ``"en"`` is English, everything else — ``None``, ``"tr"``, unsupported — is
    Turkish), so the picker and the generated plan never disagree.
    """
    return "en" if language == "en" else DEFAULT_LOCALE


def display_label(name, language):
    """The picker label for canonical ``name`` in the account's language."""
    return LABELS[catalogue_locale(language)][name]


def canonical_food(label):
    """A known label in ANY supported locale → its canonical name, else ``None``.

    Any-locale on purpose: a client that loaded the picker before the account
    language changed still submits a valid, known food. Exact match only — an
    unknown string never resolves.
    """
    return _CANONICAL_BY_LABEL.get(label) if isinstance(label, str) else None
