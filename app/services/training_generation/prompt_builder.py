from app.services import injury_constraints
from app.services.training_generation.models import ClassificationResult, ProgramContext, TrainingPreferences, UserTrainingFeatures
from app.services.training_generation.program_generator import load_few_shot


def build_system_prompt(language: str = "tr") -> str:
    if language == "en":
        return (
            "You are an experienced personal trainer. Return ONLY valid JSON. "
            "Keep gun as Turkish weekday names and tip as antrenman/dinlenme/kardiyo."
        )
    return "Sen deneyimli bir kişisel antrenörsün. SADECE geçerli JSON döndür."


def build_training_prompt(
    features: UserTrainingFeatures,
    preferences: TrainingPreferences,
    classification: ClassificationResult,
    context: ProgramContext,
    language: str = "tr",
) -> str:
    few_shot = load_few_shot(preferences.antrenman_tarzi)
    injury_text = injury_constraints.build_injury_directive(preferences.injuries)
    dinlenme_gun = 7 - preferences.gun_sayisi
    output_language = "ENGLISH" if language == "en" else "Türkçe"
    cardio_labels = {
        "kosu": "koşu",
        "bisiklet": "bisiklet",
        "yuzme": "yüzme",
        "ip_atlama": "ip atlama",
        "yuruyus": "tempolu yürüyüş",
        "karisik": "karışık",
        "yok": "kardiyo yok",
    }
    cardio_label = cardio_labels.get(preferences.kardiyo_tipi, preferences.kardiyo_tipi)
    return f"""
PROGRAM GENERATION CONTRACT
- LLM sınıflandırma yapmayacak; sınıflandırma deterministik olarak önceden yapıldı.
- Final classified level: {classification.level}
- Confidence: {classification.confidence}
- Constraints applied: {', '.join(classification.constraints_applied) or 'none'}
- Risk flags: {', '.join(classification.risk_flags) or 'none'}
- Recovery capacity factor: {context.recovery_capacity_factor}
- Volume guideline: {context.volume_guideline}
- Intensity guideline: {context.intensity_guideline}
- Progression: {context.progression_guideline}
- Deload: {context.deload_guideline}
- Style directive: {context.style_directive}

USER PROFILE
- Goal: {features.goal}
- Self-reported level: {features.self_reported_level}
- Current activity: {features.current_activity}
- Age: {features.age or 'unknown'}
- Weight: {features.weight or 'unknown'} kg
- Weekly frequency tolerance: {preferences.gun_sayisi}
- Equipment: {preferences.ekipman}
- Focus: {preferences.odak}
- Session duration: {preferences.sure} dk
- Kardiyo türü: {cardio_label}
- Haftada {preferences.kardiyo_gun} gün kardiyo
- Cardio: {cardio_label}, {preferences.kardiyo_gun} gün, {preferences.kardiyo_sure} dk, {preferences.kardiyo_yogunluk}

MOVEMENT COVERAGE REQUIRED
{', '.join(context.movement_coverage)}

STYLE FEW-SHOT REFERENCE
{few_shot[:2500]}

{injury_text}

PROGRAM RULES
1. Haftanın tam 7 günü için plan yap: {preferences.gun_sayisi} antrenman günü + {dinlenme_gun} dinlenme/aktif toparlanma günü.
2. "gun" alanı sadece şu değerlerden biri olsun: Pazartesi, Salı, Çarşamba, Perşembe, Cuma, Cumartesi, Pazar.
3. "tip" alanı sadece antrenman, dinlenme veya kardiyo olsun.
4. Aynı ağır pattern iki gün üst üste gelmesin; recovery factor düşükse hacim ve RPE azalt.
5. Her antrenman gününde gerçekçi egzersiz, set, tekrar, dinlenme ve kısa teknik not yaz.
6. İçerik dili: {output_language}; ama gun ve tip kanonik Türkçe kalacak.
7. SADECE JSON döndür.

JSON FORMAT
{{"program":[{{"gun":"Pazartesi","tip":"antrenman","odak":"Full Body","sure_dk":45,"tahmini_kalori":320,"egzersizler":[{{"isim":"Goblet Squat","set":3,"tekrar":"8-12","dinlenme":"90 sn","not":"RPE 7, kontrollü tempo"}}]}}],"haftalik_ozet":{{"toplam_antrenman_gun":{preferences.gun_sayisi},"toplam_tahmini_kalori":1400,"yogunluk_skoru":7,"denge_skoru":8,"uygunluk_skoru":8}}}}
""".strip()
