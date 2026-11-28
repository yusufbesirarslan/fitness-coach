"""Deterministic FatSecret egress policy (founder disposition #547 c7240107959).

Two pure, I/O-free checks run before any FatSecret credential, token, request,
retry, method fallback or concurrency slot:

* ``canonical_food_id`` — the provider documents ``food_id`` as a Long. Only a
  positive signed-64-bit ASCII decimal (``[1-9][0-9]{0,18}``, at most
  ``2**63 - 1``) may leave AxisAI. Anything else is a miss, never a request.
* ``withheld_search_rule`` — block-only rules R0–R6 for ``search_expression``.
  A hit means the query is not sent at all; the caller answers with its
  existing empty result. The query is NEVER rewritten: normalisation below is
  for detection only, and an allowed query leaves byte-for-byte as given.

Diagnostics may carry the returned rule label only. Never log the query, its
normalised form or the matched substring.

This reduces accidental disclosure in a free-text food search. It is not
anonymisation and does not guarantee that personal or health data never
leaves (names, health terms without a first-person marker, spelled-out
numbers and text typed before a trigger character still pass).
"""
import re
import unicodedata

LONG_MAX = 2 ** 63 - 1
_FOOD_ID = re.compile(r"[1-9][0-9]{0,18}")

# Rule labels (the only diagnostic vocabulary).
CONTROL = "control"                          # R0
NO_LETTER = "no_letter"                      # R1
EMAIL_OR_HANDLE = "email_or_handle"          # R2
URL = "url"                                  # R3
LONG_NUMBER = "long_number"                  # R4
CONTACT_KEYWORD = "contact_keyword"          # R5
FIRST_PERSON_HEALTH = "first_person_health"  # R6
RULES = (CONTROL, NO_LETTER, EMAIL_OR_HANDLE, URL, LONG_NUMBER,
         CONTACT_KEYWORD, FIRST_PERSON_HEALTH)
LONG_NUMBER_DIGITS = 7

_URL = re.compile(
    r"(?:\b[a-z][a-z0-9+.-]*://|\bwww\.|"
    r"\b[a-z0-9-]+\.(?:com|net|org|info|io|app|ai|me|ly|co|tr|edu|gov)(?:\.tr)?(?:/|\b))")
_DIGIT_SPAN = re.compile(r"\+?\d(?:[\d\s().\-/]*\d)?")
_CONTACT = re.compile(
    r"\b(?:e-?mail|e-?posta|mail adresi|tel|telefon|phone|gsm|cep (?:tel\w*|no|numara\w*)|"
    r"whatsapp|instagram|gmail|hotmail|outlook|yandex|icloud|yahoo|iban|tckn|tc kimlik|kimlik no|passport|pasaport|"
    r"adres\w*|address)\b")
_FIRST_PERSON = re.compile(
    r"\b(?:i|i'm|im|i've|ive|my|me|myself|ben|benim|bana|beni|bende|bence|annem|babam|esim|oglum|kizim)\b")
_HEALTH = re.compile(
    r"\b(?:diabet\w*|doctor|doktor\w*|diagnos\w*|teshis\w*|tani\w*|disease|hastalig\w*|hasta\w*|"
    r"medication\w*|medicine|ilac\w*|pill\w*|insulin|insulin\w*|blood sugar|kan sekeri\w*|"
    r"pregnan\w*|hamile\w*|allerg\w*|alerji\w*|surgery|ameliyat\w*|treatment|tedavi\w*|"
    r"prescri\w*|recete\w*|mg/dl|mmol|chemo\w*|kemoterapi\w*|hiv|cancer|kanser\w*|"
    r"depress\w*|depresyon\w*)\b")
_TR_ASCII = str.maketrans({"ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c"})


def canonical_food_id(value):
    """The canonical provider food ID as a plain ``str``, or ``None``.

    Accepts a non-bool ``int`` in range, or a ``str`` that is exactly
    ``[1-9][0-9]{0,18}`` (ASCII digits, no sign, whitespace, leading zero or
    trailing newline) with a value of at most ``2**63 - 1``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = int(value)
        # Range first: str() of a huge int raises (int max str digits).
        return str(number) if 0 < number <= LONG_MAX else None
    if not isinstance(value, str) or not _FOOD_ID.fullmatch(value):
        return None
    return str(value) if int(value) <= LONG_MAX else None


def _fold(text):
    """Detection-only normalisation: NFKC, Cf removed, Turkish-aware casefold."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = text.replace("İ", "i").replace("I", "ı").casefold()
    return text.replace("ı", "i").replace("̇", "")


def withheld_search_rule(query):
    """The rule label when ``query`` must not leave AxisAI, else ``None``.

    A non-``str`` query is withheld (``control``): only text can be screened.
    """
    if not isinstance(query, str):
        return CONTROL
    if any(unicodedata.category(ch) == "Cc" for ch in query):
        return CONTROL
    folded = _fold(query)
    if not any(unicodedata.category(ch).startswith("L") for ch in folded):
        return NO_LETTER
    if "@" in folded:
        return EMAIL_OR_HANDLE
    # ASCII fold of the Turkish letters for the keyword rules only.
    folded_ascii = folded.translate(_TR_ASCII)
    if _URL.search(folded_ascii):
        return URL
    for span in _DIGIT_SPAN.findall(folded):
        if sum(ch.isdigit() for ch in span) >= LONG_NUMBER_DIGITS:
            return LONG_NUMBER
    if _CONTACT.search(folded_ascii):
        return CONTACT_KEYWORD
    if _FIRST_PERSON.search(folded_ascii) and _HEALTH.search(folded_ascii):
        return FIRST_PERSON_HEALTH
    return None
