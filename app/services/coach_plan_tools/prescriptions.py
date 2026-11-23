"""User-owned sets/reps extracted from the raw turn.

Tool arguments are not authority. A model that sends ``sets=3, reps=10``
has not prescribed anything unless the user said those numbers, or later
accepted a proposal the server itself wrote.
"""
from dataclasses import dataclass
import re
import unicodedata

from app.services.plan_mutation import InvalidPrescription
from app.services.plan_mutation.validation import validate_reps, validate_sets


# Coach prescription v1. The workout session snapshot accepts up to 20 sets.
# Cap new rep counts at 100 and timed bouts at 1 hour. Existing plan values
# are preserved when the command leaves a field unspecified.
POLICY_VERSION = "coach-training-v1"
_MAX_SETS = 20
_MAX_REPS = 100
_MAX_SECONDS = 3600
_COUNT = re.compile(r"([0-9]{1,3})(?:\s*[-–—]\s*([0-9]{1,3}))?")
_TIME = re.compile(r"([0-9]{1,4})\s*(sn|dk|sec|min)", re.IGNORECASE)


def validate_coach_prescription(command):
    sets = getattr(command, "sets", None)
    if sets is not None and validate_sets(sets) > _MAX_SETS:
        raise InvalidPrescription("sets exceed the Coach prescription limit")
    reps = getattr(command, "reps", None)
    if reps is None:
        return
    value = validate_reps(reps)
    count = _COUNT.fullmatch(value)
    if count and 1 <= int(count[1]) <= int(count[2] or count[1]) <= _MAX_REPS:
        return
    duration = _TIME.fullmatch(value)
    if duration:
        multiplier = 60 if duration[2].lower() in ("min", "dk") else 1
        if 1 <= int(duration[1]) * multiplier <= _MAX_SECONDS:
            return
    raise InvalidPrescription("reps must be a bounded count, range or duration")


@dataclass(frozen=True)
class Prescription:
    sets: object = None
    reps: object = None


# Keep signed/decimal tokens whole so a malformed request cannot donate its
# trailing digits to a different prescription. Periods remain valid punctuation.
_NUMBER_START = r"(?<![\w+.\-−])(?<!\d,)"
_NUMBER_END = r"(?!\w|[.,]\d|[-–—−]\s*[+\-−]?\s*\d)"
_RANGED_NUMBER = r"\d+(?:\s*[-–—]\s*\d+)?"
_TIME_UNIT = r"(?:seconds?|minutes?|saniye|dakika|sec|min|sn|dk)"
_COMPACT = re.compile(
    rf"{_NUMBER_START}(\d+)\s*[x×]\s*({_RANGED_NUMBER}){_NUMBER_END}", re.IGNORECASE,
)
_TIMED_SETS = re.compile(
    rf"{_NUMBER_START}(\d+)\s*(?:[x×]|sets?\b(?:\s+of)?)"
    rf"\s*(\d+)\s*({_TIME_UNIT})\b", re.IGNORECASE,
)
_SETS_WORD = re.compile(
    rf"{_NUMBER_START}(\d+)\s*sets?\b", re.IGNORECASE,
)
_REPS_WORD = re.compile(
    rf"{_NUMBER_START}({_RANGED_NUMBER})\s*(?:reps?|tekrar(?:lar)?)\b",
    re.IGNORECASE,
)

def _normalize_reps(value):
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", "", text)
    return text or None


def strip_timed_prescription(message):
    return _TIMED_SETS.sub(" ", message)


def parse_prescription(message):
    """Structural extraction from one utterance. Missing fields stay None."""
    if not isinstance(message, str) or not message.strip():
        return Prescription()
    timed = _TIMED_SETS.search(message)
    if timed:
        minutes = timed[3].lower() in ("minute", "minutes", "min", "dakika", "dk")
        return Prescription(sets=int(timed[1]),
                            reps=f"{timed[2]} {'dk' if minutes else 'sn'}")
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
