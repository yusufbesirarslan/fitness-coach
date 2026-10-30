"""Member-facing day titles for a generated Turkish plan (MINERVA-DEMO-R4).

The general-fitness few-shot describes its sessions as programming notes:
"FULL BODY A: squat pattern, horizontal push, horizontal pull, hip hinge".
The model copied those notes, translated word for word, into the day's
``odak`` — "Full Body A – Squat, Yatay İtme/Çekme, Kalça Menteşesi" — and the
app showed that as the session's title.

The prompt now asks for "<split> <letter> — <body regions>". This is the
deterministic backstop: when a Turkish plan's ``odak`` still has exactly that
note shape (a known split label and letter followed only by pattern names in
``_REGIONS_BY_PATTERN``), it is rewritten to the split and the regions those
patterns train, in the model's own order. Any other ``odak`` — a title already
in the house style, another split, a rest-day suggestion, an English plan —
is returned unchanged. Only ``odak`` is touched: exercises, sets, reps, rest,
notes, durations and the day structure are copied as they are.

The mobile app applies the same rules on screen to plans saved before this
change (``lib/features/training/presentation/training_focus_text.dart``).
"""
from __future__ import annotations

import re

_SPLIT_SHAPE = re.compile(
    r"^(full body|tüm vücut|tam vücut)\s+([a-d])\s*[–—:-]\s*(\S.*)$", re.DOTALL
)
_LIST_SEPARATOR = re.compile(r",|\s+ve\s+|\s*\+\s*")

# Every pattern name the model writes in a full-body title, lower-cased by
# Turkish rules, and the regions it trains as a member would say them. Push
# and pull share a plane: horizontal is chest and back, vertical is shoulders
# and back. A hinge and a glute focus both train the hips.
_REGIONS_BY_PATTERN = {
    "squat": ("squat",),
    "squat paterni": ("squat",),
    "tek bacak": ("tek taraflı bacak",),
    "tek taraflı bacak": ("tek taraflı bacak",),
    "tek taraflı alt vücut": ("tek taraflı bacak",),
    "unilateral alt vücut": ("tek taraflı bacak",),
    "unilateral bacak": ("tek taraflı bacak",),
    "yatay itme/çekme": ("göğüs", "sırt"),
    "yatay itiş/çekiş": ("göğüs", "sırt"),
    "dikey itme/çekme": ("omuz", "sırt"),
    "dikey itiş/çekiş": ("omuz", "sırt"),
    "yatay itme": ("göğüs",),
    "yatay itiş": ("göğüs",),
    "yatay çekme": ("sırt",),
    "yatay çekiş": ("sırt",),
    "dikey itme": ("omuz",),
    "dikey itiş": ("omuz",),
    "dikey çekme": ("sırt",),
    "dikey çekiş": ("sırt",),
    "kalça menteşesi": ("kalça",),
    "kalça dominant": ("kalça",),
    "glute odak": ("kalça",),
    "glute odaklı": ("kalça",),
    "kalça odak": ("kalça",),
    "kalça odaklı": ("kalça",),
    "core": ("core",),
    "core stabilite": ("core",),
    "core stabilitesi": ("core",),
    "gövde stabilitesi": ("core",),
    "kondisyon": ("kondisyon",),
    "isteğe bağlı kondisyon": ("kondisyon",),
    "opsiyonel kondisyon": ("kondisyon",),
}

# Names an English title writes the same way; alone they do not make a
# title Turkish.
_SHARED_PATTERNS = frozenset({"squat", "core"})


def _turkish_lower(value: str) -> str:
    return value.replace("İ", "i").replace("I", "ı").lower()


def _capitalized(value: str) -> str:
    first = value[0]
    return ("İ" if first == "i" else first.upper()) + value[1:]


def member_focus_title(odak: str) -> str:
    """``odak`` as a member-facing title, or ``odak`` itself when its shape is
    not the programming-note shape described in the module docstring."""
    match = _SPLIT_SHAPE.match(_turkish_lower(odak.strip()))
    if match is None:
        return odak
    regions: list[str] = []
    turkish = False
    for raw in _LIST_SEPARATOR.split(match.group(3)):
        pattern = raw.strip()
        if not pattern:
            continue
        known = _REGIONS_BY_PATTERN.get(pattern)
        if known is None:
            return odak
        turkish = turkish or pattern not in _SHARED_PATTERNS
        for region in known:
            if region not in regions:
                regions.append(region)
    if not turkish or not regions:
        return odak
    listed = (
        regions[0] if len(regions) == 1
        else f"{', '.join(regions[:-1])} ve {regions[-1]}"
    )
    return f"Tüm Vücut {match.group(2).upper()} — {_capitalized(listed)}"


def normalize_generated_titles(plan: dict, language: str) -> dict:
    """A copy of a validated generated ``plan`` with each training day's
    ``odak`` passed through :func:`member_focus_title` — Turkish plans only.
    Nothing else in the plan changes, and the input is not mutated."""
    if language != "tr":
        return plan
    program = [
        {**day, "odak": member_focus_title(day["odak"])}
        if day.get("tip") == "antrenman" else day
        for day in plan["program"]
    ]
    return {**plan, "program": program}
