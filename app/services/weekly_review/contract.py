"""V1 provider-selected evidence and actions rendered as localized plain text."""
import json

VERSION = 1
MAX_OUTPUT_TOKENS = 520
INSTRUCTIONS = (
    "Select a short weekly fitness review using only the supplied data. Return one JSON object "
    "with exactly language, summary, changes, and next_week_actions. Copy the language tag. "
    "Summary is an array with one or two distinct keys from evidence.recorded, ordered by "
    "what matters most for the upcoming week. Changes is an array of zero to three distinct "
    "keys from evidence.changes. Include only meaningful changes. Next_week_actions is an "
    "array of one or two distinct keys from suggested_actions, in priority order. Prioritize "
    "concrete, sustainable habits relevant to the current evidence. Use only supplied keys. "
    "No explanatory prose, additional keys, plans or diagnoses."
)

_LABELS = {
    "en": {
        "weight_kg": "Weight",
        "training_intensity": "Training intensity",
        "fatigue": "Fatigue",
        "sleep_quality": "Sleep quality",
        "nutrition_adherence": "Nutrition adherence",
        "progressive_overload": "Progressive overload",
    },
    "tr": {
        "weight_kg": "Kilo",
        "training_intensity": "Antrenman yoğunluğu",
        "fatigue": "Yorgunluk",
        "sleep_quality": "Uyku kalitesi",
        "nutrition_adherence": "Beslenme planına uyum",
        "progressive_overload": "Kademeli yük artışı",
    },
}
_VALUES = {
    "en": {"yes": "Yes", "partly": "Partly", "partial": "Partial", "no": "No",
           "not_applicable": "Not applicable"},
    "tr": {"yes": "Evet", "partly": "Kısmen", "partial": "Kısmen", "no": "Hayır",
           "not_applicable": "Uygulanamaz"},
}
_ACTIONS = {
    "bedtime": ("Choose a regular bedtime you can keep on most days.",
                "Çoğu gün sürdürebileceğin düzenli bir uyku saati seç."),
    "recovery": ("Pick a day for lighter activity and check how rested you feel afterward.",
                 "Daha hafif hareket edeceğin bir gün seç ve sonrasında ne kadar dinlenmiş hissettiğine bak."),
    "training_log": ("Record the sets and reps in one workout to give yourself a point of comparison.",
                     "Karşılaştırma yapabilmek için bir antrenmandaki setleri ve tekrarları kaydet."),
    "nutrition_routine": ("Choose one meal you can plan ahead on a busy day.",
                          "Yoğun bir günde önceden planlayabileceğin bir öğün seç."),
    "weigh_in_routine": ("If you track weight again, use similar conditions and look at the longer trend.",
                         "Tekrar tartılırsan benzer koşulları seç ve uzun vadeli değişime bak."),
}


class InvalidReview(ValueError):
    """The provider returned an unusable review."""


def suggested_actions(evidence):
    recorded = evidence["recorded"]
    actions = {"bedtime": _ACTIONS["bedtime"][0], "training_log": _ACTIONS["training_log"][0]}
    if recorded.get("fatigue", 0) >= 3 or recorded.get("training_intensity", 0) >= 4:
        actions["recovery"] = _ACTIONS["recovery"][0]
    if recorded.get("nutrition_adherence", {}).get("value") not in (None, "not_applicable"):
        actions["nutrition_routine"] = _ACTIONS["nutrition_routine"][0]
    if "weight_kg" in recorded:
        actions["weigh_in_routine"] = _ACTIONS["weigh_in_routine"][0]
    return actions


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidReview()
        result[key] = value
    return result


def _selection(value, allowed, min_count, max_count):
    if not isinstance(value, list) or not min_count <= len(value) <= max_count:
        raise InvalidReview()
    seen = set()
    for key in value:
        if not isinstance(key, str) or key not in allowed or key in seen:
            raise InvalidReview()
        seen.add(key)
    return value


def _display(language, value, metric):
    if isinstance(value, dict):
        return _display(language, value["value"], metric)
    if isinstance(value, str):
        return _VALUES[language][value]
    if metric == "weight_kg":
        number = f"{value:.2f}".rstrip("0").rstrip(".")
        if language == "tr":
            number = number.replace(".", ",")
        return f"{number} kg"
    return f"{value}/5"


def _observed(language, evidence, metric):
    label = _LABELS[language][metric]
    value = _display(language, evidence["recorded"][metric], metric)
    # One short summary can highlight two observations without inventing a
    # narrative about causes, risk, or comparable history.
    return f"{label}: {value}"


def _change(language, evidence, metric):
    label = _LABELS[language][metric]
    change = evidence["changes"][metric]
    if metric == "weight_kg":
        delta = change["delta"]
        amount = _display(language, abs(delta), metric)
        sign = "+" if delta > 0 else "−"
        suffix = {"en": "since the previous recorded day", "tr": "önceki kayıt gününe göre"}
        return f"{label}: {sign}{amount} ({suffix[language]})"
    before = _display(language, change["before"], metric)
    after = _display(language, change["after"], metric)
    return f"{label}: {before} → {after}"


def validate(raw, language, evidence):
    if not isinstance(raw, str) or len(raw) > 2048:
        raise InvalidReview()
    try:
        value = json.loads(raw, object_pairs_hook=_object)
    except (ValueError, RecursionError):
        raise InvalidReview() from None
    if (not isinstance(value, dict)
            or set(value) != {"language", "summary", "changes", "next_week_actions"}
            or language not in _LABELS or value["language"] != language):
        raise InvalidReview()
    summary = _selection(value["summary"], evidence["recorded"], 1, 2)
    changes = _selection(value["changes"], evidence["changes"], 0, 3)
    actions = _selection(value["next_week_actions"], suggested_actions(evidence), 1, 2)
    translation = 1 if language == "tr" else 0
    return {
        "language": language,
        "summary": ". ".join(_observed(language, evidence, metric) for metric in summary) + ".",
        "changes": [_change(language, evidence, metric) for metric in changes],
        "next_week_actions": [_ACTIONS[action][translation] for action in actions],
    }
