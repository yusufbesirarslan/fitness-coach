import re

from app.services import injury_constraints
from app.services.training_generation.models import TrainingPreferences


class PlanValidationError(ValueError):
    pass


WEEKDAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
VALID_TIPS = {"antrenman", "dinlenme", "kardiyo"}


def _to_int(value, default):
    """B9: LLM sayı alanlarını savunmacı ayrıştır. "3-4", "45 dk", "~300" gibi
    değerler düz int() ile bare ValueError fırlatıp çağıranın
    (JSONDecodeError, PlanValidationError) yakalamasından kaçıp generic 500
    üretiyordu. Baştaki tamsayıyı çek; bulunamazsa default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"-?\d+", str(value if value is not None else ""))
    return int(m.group()) if m else default


def _clamp_score(value):
    parsed = _to_int(value, 7)
    if parsed == 0:
        return 7
    return max(1, min(10, parsed))


def validate_generated_plan(plan: dict, preferences: TrainingPreferences, injuries: str = "") -> tuple[dict, list[dict]]:
    if not isinstance(plan, dict):
        raise PlanValidationError("plan JSON object olmalı")
    program = plan.get("program")
    if not isinstance(program, list) or len(program) != 7:
        raise PlanValidationError("program tam 7 gün içermeli")

    injury_warnings: list[dict] = []
    training_days = 0
    seen_days: set[str] = set()
    for index, day in enumerate(program):
        if not isinstance(day, dict):
            raise PlanValidationError("her gün object olmalı")
        day["gun"] = day.get("gun") or WEEKDAYS[index]
        if day["gun"] not in WEEKDAYS:
            raise PlanValidationError("gun alanı kanonik Türkçe hafta günü olmalı")
        # B8: gün adları benzersiz olmalı — LLM aynı günü (7× "Pazartesi") tekrar
        # üretirse takvim bozulurdu.
        if day["gun"] in seen_days:
            raise PlanValidationError("gun alanları benzersiz olmalı (tekrar eden gün)")
        seen_days.add(day["gun"])
        tip = day.get("tip")
        if tip not in VALID_TIPS:
            raise PlanValidationError("tip alanı antrenman/dinlenme/kardiyo olmalı")
        if tip == "antrenman":
            training_days += 1
        day["odak"] = str(day.get("odak") or ("Aktif Toparlanma" if tip == "dinlenme" else "Antrenman"))[:120]
        day["sure_dk"] = _to_int(day.get("sure_dk"), 0) or (0 if tip == "dinlenme" else preferences.sure)
        day["tahmini_kalori"] = max(0, min(900, _to_int(day.get("tahmini_kalori"), 0)))
        exercises = day.get("egzersizler") or []
        if not isinstance(exercises, list):
            raise PlanValidationError("egzersizler liste olmalı")
        # B7: antrenman günü en az bir egzersiz içermeli — boş egzersiz listesiyle
        # "antrenman" günü anlamsız (kullanıcıya boş program).
        if tip == "antrenman" and len(exercises) < 1:
            raise PlanValidationError("antrenman günü en az bir egzersiz içermeli")
        for ex in exercises:
            if not isinstance(ex, dict):
                raise PlanValidationError("egzersiz object olmalı")
            ex["isim"] = str(ex.get("isim") or "").strip()[:120]
            if not ex["isim"]:
                raise PlanValidationError("egzersiz isim alanı boş olamaz")
            ex["set"] = _to_int(ex.get("set"), 1) or 1
            ex["tekrar"] = str(ex.get("tekrar") or "8-12")[:40]
            ex["dinlenme"] = str(ex.get("dinlenme") or "60-90 sn")[:40]
            hit = injury_constraints.find_contraindicated(ex["isim"], injuries)
            if hit:
                warn = f"⚠️ SAKATLIK RİSKİ ({hit}) - güvenli alternatifle değiştir"
                note = str(ex.get("not") or "").strip()
                ex["not"] = f"{warn}. {note}".strip()
                injury_warnings.append({"gun": day.get("gun"), "egzersiz": ex["isim"], "neden": hit})
            else:
                ex["not"] = str(ex.get("not") or "")[:240]

    if training_days != preferences.gun_sayisi:
        raise PlanValidationError(f"toplam antrenman günü {preferences.gun_sayisi} olmalı")

    ozet = plan.setdefault("haftalik_ozet", {})
    if not isinstance(ozet, dict):
        raise PlanValidationError("haftalik_ozet object olmalı")
    ozet["toplam_antrenman_gun"] = training_days
    ozet["toplam_tahmini_kalori"] = sum(day.get("tahmini_kalori", 0) for day in program)
    ozet["yogunluk_skoru"] = _clamp_score(ozet.get("yogunluk_skoru"))
    ozet["denge_skoru"] = _clamp_score(ozet.get("denge_skoru"))
    ozet["uygunluk_skoru"] = _clamp_score(ozet.get("uygunluk_skoru"))
    return plan, injury_warnings
