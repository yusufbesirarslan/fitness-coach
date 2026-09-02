"""User-owned sets/reps extracted from the raw turn.

Tool arguments are not authority. A model that sends ``sets=3, reps=10``
has not prescribed anything unless the user said those numbers, or later
accepted a proposal the server itself wrote.
"""
from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class Prescription:
    sets: object = None
    reps: object = None


# 3x12, 3 x 12, 3×8-12, 3x8–12
_COMPACT = re.compile(
    r"(?<!\w)(\d+)\s*[x×]\s*(\d+(?:\s*[-–—]\s*\d+)?)(?!\w)",
    re.IGNORECASE,
)
_SETS_WORD = re.compile(
    r"(?<!\w)(\d+)\s*(?:sets?|set)\b",
    re.IGNORECASE,
)
_REPS_WORD = re.compile(
    r"(?<!\w)(\d+(?:\s*[-–—]\s*\d+)?)\s*(?:reps?|tekrar(?:lar)?)\b",
    re.IGNORECASE,
)


def _normalize_reps(value):
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", "", text)
    return text or None


def parse_prescription(message):
    """Structural extraction from one utterance. Missing fields stay None."""
    if not isinstance(message, str) or not message.strip():
        return Prescription()
    compact = _COMPACT.search(message)
    if compact:
        return Prescription(
            sets=int(compact.group(1)),
            reps=_normalize_reps(compact.group(2)),
        )
    sets = None
    reps = None
    sets_match = _SETS_WORD.search(message)
    if sets_match:
        sets = int(sets_match.group(1))
    reps_match = _REPS_WORD.search(message)
    if reps_match:
        reps = _normalize_reps(reps_match.group(1))
    return Prescription(sets=sets, reps=reps)


def merge_prescription(user_owned, tool_sets=None, tool_reps=None,
                       user_message_present=False):
    """Prefer user-owned numbers. Tool values are used only when the turn
    had no user text (direct tool-harness calls). A non-empty user turn
    that omitted sets/reps does *not* inherit the model's fabrication.
    """
    if not user_message_present:
        return Prescription(sets=tool_sets, reps=tool_reps)
    return Prescription(sets=user_owned.sets, reps=user_owned.reps)
