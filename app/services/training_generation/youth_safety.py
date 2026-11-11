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
import logging
import re

from app.services.training_generation.output_errors import SemanticInvalidError
from app.services.training_generation.preference_contract import PreferenceContractError

_log = logging.getLogger(__name__)
MINOR_MAX_AGE = 17
REFUSED_FOCUS = frozenset({"yag_yakimi"})          # fat-loss focus
REFUSED_STYLES = frozenset({"powerlifting"})       # maximal-strength style
MAX_SETS = 4
MIN_REPS = 6
MAX_RPE = 7

_NUM = r"(\d+(?:[.,]\d+)?)"
# "RPE 8", "RPE: 9", "RPE ~8", "RPE 7-8" (the upper bound counts); "RPE 7/10"
# names the scale, never a 7-10 range.
_RPE = re.compile(r"\bRPE\s*[:~≈]?\s*" + _NUM + r"(?:\s*/\s*10\b)?(?:\s*[-–]\s*" + _NUM + r")?",
                  re.IGNORECASE)
# RPE <= 7 is roughly 3+ reps in reserve; RIR below that is near failure.
_RIR = re.compile(r"\bRIR\s*[:~≈]?\s*" + _NUM, re.IGNORECASE)
MIN_RIR = 3
_TIME = re.compile(r"\d\s*(?:sn|s|sec|secs|saniye|dk|min|dakika)\b", re.IGNORECASE)
_SETS_X_REPS = re.compile(r"^\s*\d+\s*[x×]\s*(\d+)")
_LEADING_INT = re.compile(r"^\s*(\d+)")
# "Max/maksimum reps" is maximal unless a number follows ("maksimum tekrar 12",
# "max rep range 12" are ordinary ranges).
_NOT_A_COUNT = r"(?!\s*(?:range|aralığı|sayısı)?\s*:?\s*\d)"
_FORBIDDEN = re.compile(
    r"\b1\s?-?RM\b|\bone[- ]rep max\b|\bAMRAP\b|\bmax(?:imal|imum)?\s+(?:lift|effort|weight|attempt)"
    r"|\bmax(?:imum)?\s+reps?\b" + _NOT_A_COUNT
    + r"|\bmaks(?:imal|imum)?\s+(?:kaldırış|ağırlık|deneme|efor)"
    r"|\bmaks(?:imal|imum)?\s+tekrar\b" + _NOT_A_COUNT
    + r"|\bto failure\b|tükeni[şs]e kadar|tükenmeye (?:kadar|yakın)|\bcalorie deficit\b|kalori açığı"
    r"|\bfat[- ]burn|\byağ yak|\bsupplement|besin takviye|protein tozu|\bdiet\b|\bdiyet\b|\bdiyete\b",
    re.IGNORECASE,
)
# A warning AGAINST the forbidden thing is the boundary working, but only when
# the negator is adjacent to the term: English before it ("do not go to
# failure", "avoid 1RM"), Turkish after it ("tükenişe kadar gitmeyin", "1RM
# testi yok"). A negator elsewhere ("go to failure with no rest") never counts.
_EN_NEGATION = re.compile(r"\b(?:not|don'?t|never|avoid|no|without)\b", re.IGNORECASE)
# Turkish: only unambiguous forms. "-ma/-me" alone is also a verbal noun
# ("tükenişe kadar çalışma" = training to failure), so it never negates.
_TR_NEGATION = re.compile(
    r"\b(?:yok|değil|olmadan|asla)\b|kaçın|\w+m[ae]y[ıi]n\b|\w+m[ae]m[ae]l[ıi]\w*|\w+m[ae]den\b",
    re.IGNORECASE)
# "Not:" (Turkish for "Note:") starts a new clause; it is never a negator.
_CLAUSE = re.compile(r"[.;!?\n]|:(?!\s*\d)|,\s")
_WINDOW_WORDS = 3


def _negated(clause, match):
    before = clause[:match.start()].split()[-_WINDOW_WORDS:]
    after = clause[match.end():].split()[:_WINDOW_WORDS]
    return (bool(_EN_NEGATION.search(" ".join(before)))
            or bool(_TR_NEGATION.search(" ".join(after))))


def effective_age(*ages):
    """The lowest known integer age: a stale higher copy never lifts the boundary."""
    known = [age for age in ages if type(age) is int]
    return min(known) if known else None


def _prescribes_forbidden(text):
    return any(not _negated(clause, match)
               for clause in _CLAUSE.split(text or "")
               for match in _FORBIDDEN.finditer(clause))


def _rep_floor(text):
    text = str(text or "")
    if _TIME.search(text):
        return None
    pair = _SETS_X_REPS.match(text)
    if pair:
        return int(pair.group(1))
    lead = _LEADING_INT.match(text)
    return int(lead.group(1)) if lead else None


_DIRECTIVE = {
    "tr": (
        "GENÇ KULLANICI GÜVENLİK SINIRI (18 yaş altı, zorunlu):\n"
        "- Önce teknik ve kontrol: hafif-orta yük, vücut ağırlığı ve kontrollü serbest ağırlık.\n"
        f"- Her egzersiz en fazla {MAX_SETS} set, en az {MIN_REPS} tekrar; RPE en fazla {MAX_RPE}. "
        "Maksimum kaldırış, 1RM testi, tükenişe kadar set YOK.\n"
        "- Kilo verme, kalori açığı, diyet, takviye, yağ yakımı veya vücut görünüşü hedefi YOK.\n"
        "- Notlarda ve tekrar alanında 1RM, maksimum, tükeniş, diyet veya kalori kelimelerini "
        "uyarı olarak bile kullanma.\n"
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
        "- Do not mention 1RM, max, failure, diet or calories in notes or reps, not even as a warning.\n"
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


def _reject(rule):
    # Monitoring signal (Anthropic minors guidance): fixed rule token only,
    # never age, user, plan text or provider output.
    _log.warning("[TRAINING][YOUTH_BOUNDARY] outcome=rejected rule=%s", rule)
    raise SemanticInvalidError(f"youth boundary: {rule}")


def validate_plan(age, plan: dict) -> dict:
    if not is_minor(age):
        return plan
    for day in plan.get("program", ()):
        if _prescribes_forbidden(str(day.get("odak") or "")):
            _reject("focus_term")
        for exercise in day.get("egzersizler") or ():
            reps = str(exercise.get("tekrar") or "")
            if _prescribes_forbidden(reps):
                _reject("reps_term")
            if day.get("tip") == "antrenman":
                # A strength reps value must be a count or a time; "Max" is not.
                if reps.strip() and not any(c.isdigit() for c in reps):
                    _reject("reps_term")
                sets = exercise.get("set")
                if type(sets) is int and sets > MAX_SETS:
                    _reject("sets")
                floor = _rep_floor(reps)
                if floor is not None and floor < MIN_REPS:
                    _reject("reps")
            note = str(exercise.get("not") or "")
            for low, high in _RPE.findall(note):
                if max(float(v.replace(",", ".")) for v in (low, high) if v) > MAX_RPE:
                    _reject("rpe")
            for value in _RIR.findall(note):
                if float(value.replace(",", ".")) < MIN_RIR:
                    _reject("rir")
            if _prescribes_forbidden(note):
                _reject("note_term")
    return plan
