"""Canonical weekday identity vs Coach presentation.

Storage and tool vocabulary stay the generator's Turkish ``WEEKDAYS``.
English names exist only as input aliases (so "Friday" is Cuma) and as
presentation at the Coach copy boundary (so Cuma is Friday). Nothing here
renames a persisted ``gun``.
"""
from app.services.plan_mutation.validation import WEEKDAYS


# Same order as ``WEEKDAYS`` and ``date.weekday()`` (Monday=0). This is the
# English calendar, not a second product vocabulary; tests pin it against
# ``DAY_LABELS_EN`` in static/training.js so the Training UI and Coach copy
# cannot drift.
ENGLISH_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)

_CANONICAL_FOLD = {day.casefold(): day for day in WEEKDAYS}
_ENGLISH_FOLD = {name.casefold(): WEEKDAYS[i]
                 for i, name in enumerate(ENGLISH_WEEKDAYS)}
_ENGLISH_ABBREV = {
    "mon": "Pazartesi",
    "tue": "Salı",
    "tues": "Salı",
    "wed": "Çarşamba",
    "thu": "Perşembe",
    "thur": "Perşembe",
    "thurs": "Perşembe",
    "fri": "Cuma",
    "sat": "Cumartesi",
    "sun": "Pazar",
}
_ASCII_FOLD = {
    "pazartesi": "Pazartesi",
    "sali": "Salı",
    "carsamba": "Çarşamba",
    "persembe": "Perşembe",
    "cuma": "Cuma",
    "cumartesi": "Cumartesi",
    "pazar": "Pazar",
}


def _fold_ascii(value):
    table = str.maketrans({
        "ı": "i", "İ": "i", "â": "a", "ç": "c", "Ç": "c",
        "ğ": "g", "Ğ": "g", "ö": "o", "Ö": "o", "ş": "s", "Ş": "s",
        "ü": "u", "Ü": "u",
    })
    return value.translate(table).casefold()


_TR_SUFFIXES = (
    "ye", "ya", "yı", "yi", "yu", "yü",
    "de", "da", "den", "dan",
    "nin", "nın", "nun", "nün", "ne", "na",
    "si", "sı", "su", "sü",
)


def canonicalize_weekday(value):
    """Return a canonical Turkish weekday, or ``None`` if ``value`` is not one.

    Accepts the stored identity, English day names, common English
    abbreviations, ASCII-folded Turkish spellings, and inflected Turkish
    forms (``pazartesiye``, ``Cuma'ya``). Never guesses a day from a
    workout nickname — that belongs to ``workout_targets``.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.strip().casefold().split()).replace("'", "")
    if not text:
        return None
    direct = _lookup_weekday(text)
    if direct is not None:
        return direct
    ascii_text = _fold_ascii(text)
    if ascii_text in _ASCII_FOLD:
        return _ASCII_FOLD[ascii_text]
    for suffix in _TR_SUFFIXES:
        if text.endswith(suffix) and len(text) - len(suffix) >= 4:
            stemmed = _lookup_weekday(text[:-len(suffix)])
            if stemmed is not None:
                return stemmed
            ascii_stem = _fold_ascii(text[:-len(suffix)])
            if ascii_stem in _ASCII_FOLD:
                return _ASCII_FOLD[ascii_stem]
    return None


def _lookup_weekday(text):
    if text in _CANONICAL_FOLD:
        return _CANONICAL_FOLD[text]
    if text in _ENGLISH_FOLD:
        return _ENGLISH_FOLD[text]
    if text in _ENGLISH_ABBREV:
        return _ENGLISH_ABBREV[text]
    return None


def localize_weekday(canonical, locale="tr"):
    """Presentation-only. Unknown labels pass through unchanged."""
    if canonical not in WEEKDAYS:
        return canonical
    if locale == "en":
        return ENGLISH_WEEKDAYS[WEEKDAYS.index(canonical)]
    return canonical


def localize_weekday_text(value, locale="tr"):
    """Replace canonical Turkish weekday tokens in Coach presentation copy.

    English names the user typed are left alone. Storage identity is not
    changed — this is copy only.
    """
    if not isinstance(value, str) or not value:
        return value or ""
    if locale != "en":
        return value
    result = value
    for token in _tokenize(value):
        canonical = canonicalize_weekday(token)
        if canonical not in WEEKDAYS:
            continue
        if _fold_ascii(token) != _fold_ascii(canonical) and token.casefold() != canonical.casefold():
            continue
        result = result.replace(token, localize_weekday(canonical, "en"))
    return result


def find_explicit_weekday(message):
    """The one explicit weekday named in ``message``, or ``None``.

    Two different weekdays in one turn are not a weekday request — the
    caller must not pick one.
    """
    if not isinstance(message, str) or not message.strip():
        return None
    tokens = _tokenize(message)
    found = []
    for token in tokens:
        day = canonicalize_weekday(token)
        if day is not None and day not in found:
            found.append(day)
    # Also accept full names that survive as multi-token? English/Turkish
    # weekdays are single tokens after punctuation split.
    if len(found) == 1:
        return found[0]
    return None


def _tokenize(message):
    text = message.replace("'", " ")
    out = []
    current = []
    for char in text:
        if char.isalnum() or char in "çÇğĞıİöÖşŞüÜ":
            current.append(char)
        else:
            if current:
                out.append("".join(current))
                current = []
    if current:
        out.append("".join(current))
    return out
