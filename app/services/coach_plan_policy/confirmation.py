"""Narrow structural CONFIRM / CANCEL / NONE parser.

Not a general intent classifier. It only resolves a user response when a
pending plan confirmation already exists. Fail closed: mixed-intent, hedges,
and any negation never classify as CONFIRM. No LLM, no embeddings.
"""
import re
import unicodedata


CONFIRM = "CONFIRM"
CANCEL = "CANCEL"
NONE = "NONE"

# Exact whole-message forms. Drawn from the Coach's existing meal-log
# vocabulary (prompts/system.py) plus the PR4 brief's unambiguous set.
_CONFIRM_FORMS = frozenset({
    "yes",
    "yeah",
    "yep",
    "confirm",
    "confirmed",
    "proceed",
    "go ahead",
    "do it",
    "evet",
    "onayla",
    "uygula",
    "olur",
    "kaydet",
    "tabii",
})

_CANCEL_FORMS = frozenset({
    "no",
    "cancel",
    "don't",
    "dont",
    "do not",
    "never mind",
    "nevermind",
    "hayır",
    "hayir",
    "iptal",
    "vazgeç",
    "vazgec",
})

# A token that, anywhere in the message, forbids CONFIRM. "not yes",
# "don't do it", "yes?" with extra doubt words, "yes but ..." all fail closed.
_NEGATION_TOKENS = frozenset({
    "not",
    "don't",
    "dont",
    "no",
    "never",
    "hayır",
    "hayir",
    "değil",
    "degil",
    "olmasın",
    "olmasin",
})

# Apostrophes fold into the word ("don't" → "dont") so a cancel form stays
# one token. A question mark is meaning — "yes?" is not "yes". Other
# trailing punctuation is stripped so "evet." still matches.
_APOSTROPHE = re.compile(r"['’`]")
_SAFE_PUNCTUATION = re.compile(r"[.,;:\"!]+")
_WHITESPACE = re.compile(r"\s+")


def _normalize(message):
    if not isinstance(message, str):
        return ""
    text = unicodedata.normalize("NFC", message).strip().casefold()
    text = _APOSTROPHE.sub("", text)
    text = _SAFE_PUNCTUATION.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text


def parse_confirmation_intent(message) -> str:
    """Classify ``message`` as CONFIRM, CANCEL, or NONE.

    Only an exact bounded form is CONFIRM. Extra words, hedges, questions
    that mix a new request, and any negation token force NONE (or CANCEL
    when the whole message is itself a cancel form).
    """
    text = _normalize(message)
    if not text:
        return NONE
    if text in _CANCEL_FORMS:
        return CANCEL
    tokens = set(text.split())
    if tokens & _NEGATION_TOKENS:
        return NONE
    if text in _CONFIRM_FORMS:
        return CONFIRM
    return NONE
