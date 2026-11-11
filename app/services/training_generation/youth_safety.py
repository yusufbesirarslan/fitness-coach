"""Age-appropriate boundary for AI Training plans generated for a minor (<18).

Selected from the canonical profile age the generator already sends, never
from a client field. Adults are byte-identical: every function here is a no-op
unless ``is_minor(age)``. Three layers, all deterministic and fail closed:

1. Request: preferences that only make sense as adult body-composition or
   maximal-strength work are refused before any claim, hold or provider call
   (existing ``PreferenceContractError.unsupported`` wire code).
2. Prompt: one fixed youth directive (technique first, sub-maximal effort,
   no weight-loss, diet, supplement or body-image content).
3. Output: a candidate breaking the numeric bounds or naming maximal/failure
   or calorie-deficit work is ``SemanticInvalidError`` (never repaired, never
   clamped into something the provider did not say).

This is general fitness guidance, not medical screening; it does not decide a
minor's capacity to consent (that is the separate F01/D1 + legal track).
"""
import re

from app.services.training_generation.output_errors import SemanticInvalidError
from app.services.training_generation.preference_contract import PreferenceContractError

MINOR_MAX_AGE = 17
REFUSED_FOCUS = frozenset({"yag_yakimi"})          # fat-loss focus
REFUSED_STYLES = frozenset({"powerlifting"})       # maximal-strength style
MAX_SETS = 4
MIN_REPS = 6
MAX_RPE = 7

_RPE = re.compile(r"\bRPE\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_REPS = re.compile(r"^\s*(\d+)\s*(?:[-–x×]\s*\d+)?\s*(?:tekrar|reps?)?\s*$", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b1\s?-?RM\b|\bone[- ]rep max\b|\bmax(?:imal|imum)?\s+(?:lift|effort|rep|weight|attempt)"
    r"|\bmaks(?:imal|imum)?\s+(?:kaldırış|tekrar|ağırlık|deneme|efor)"
    r"|\bto failure\b|tükeni[şs]e kadar|\bcalorie deficit\b|kalori açığı"
    r"|\bfat[- ]burn|\byağ yak|\bsupplement|besin takviye|protein tozu|\bdiet\b|\bdiyet",
    re.IGNORECASE,
)

_DIRECTIVE = {
    "tr": (
        "GENÇ KULLANICI GÜVENLİK SINIRI (18 yaş altı, zorunlu):\n"
        "- Önce teknik ve kontrol: hafif-orta yük, vücut ağırlığı ve kontrollü serbest ağırlık.\n"
        f"- Her egzersiz en fazla {MAX_SETS} set, en az {MIN_REPS} tekrar; RPE en fazla {MAX_RPE}. "
        "Maksimum kaldırış, 1RM testi, tükenişe kadar set YOK.\n"
        "- Kilo verme, kalori açığı, diyet, takviye, yağ yakımı veya vücut görünüşü hedefi YOK.\n"
        "- Yeni serbest ağırlık hareketlerinde deneyimli bir yetişkin ya da antrenör eşliği öner; "
        "ağrı, baş dönmesi veya rahatsızlıkta durmasını söyle.\n"
        "- Toparlanma ve uyku için yeterli dinlenme günü bırak."
    ),
    "en": (
        "YOUTH SAFETY BOUNDARY (under 18, mandatory):\n"
        "- Technique and control first: light-to-moderate loads, bodyweight and controlled free weights.\n"
        f"- At most {MAX_SETS} sets and at least {MIN_REPS} reps per exercise; RPE at most {MAX_RPE}. "
        "No maximal lifts, 1RM testing or sets to failure.\n"
        "- No weight-loss, calorie-deficit, diet, supplement, fat-burning or appearance goals.\n"
        "- Suggest an experienced adult or coach for new free-weight movements; tell them to stop "
        "on pain, dizziness or feeling unwell.\n"
        "- Leave enough rest days for recovery and sleep."
    ),
}


def is_minor(age) -> bool:
    return type(age) is int and age <= MINOR_MAX_AGE


def require_supported(age, preferences):
    if not is_minor(age):
        return preferences
    if (preferences.odak_hedef in REFUSED_FOCUS
            or (preferences.antrenman_tarzi or "").strip().lower() in REFUSED_STYLES):
        raise PreferenceContractError.unsupported("MINOR_UNSUPPORTED_PREFERENCE")
    return preferences


def directive(age, language: str) -> str:
    if not is_minor(age):
        return ""
    return _DIRECTIVE["en" if language == "en" else "tr"]


def validate_plan(age, plan: dict) -> dict:
    if not is_minor(age):
        return plan
    for day in plan.get("program", ()):
        if _FORBIDDEN.search(str(day.get("odak") or "")):
            raise SemanticInvalidError("youth boundary: forbidden day focus")
        for exercise in day.get("egzersizler") or ():
            if day.get("tip") == "antrenman":
                sets = exercise.get("set")
                if type(sets) is int and sets > MAX_SETS:
                    raise SemanticInvalidError("youth boundary: too many sets")
                reps = _REPS.match(str(exercise.get("tekrar") or ""))
                if reps and int(reps.group(1)) < MIN_REPS:
                    raise SemanticInvalidError("youth boundary: too few reps")
            note = str(exercise.get("not") or "")
            for value in _RPE.findall(note):
                if float(value.replace(",", ".")) > MAX_RPE:
                    raise SemanticInvalidError("youth boundary: RPE above limit")
            if _FORBIDDEN.search(note):
                raise SemanticInvalidError("youth boundary: forbidden instruction")
    return plan
