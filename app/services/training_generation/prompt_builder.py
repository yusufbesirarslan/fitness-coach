from app.services import injury_constraints
from app.services.training_generation.exercise_choices import (
    CompatibleExerciseChoices,
)
from app.services.training_generation.models import ClassificationResult, ProgramContext, TrainingPreferences, UserTrainingFeatures
from app.services.training_generation.preference_contract import load_focus_directive
from app.services.training_generation.program_generator import canonical_style, load_few_shot


def build_system_prompt(language: str = "tr") -> str:
    if language == "en":
        return (
            "You are an experienced personal trainer. Return ONLY one valid "
            "JSON object. No markdown, no code fences, no commentary. "
            "Keep gun as Turkish weekday names and tip as antrenman/dinlenme/kardiyo. "
            "exercise_id values are opaque catalog IDs: copy them exactly from "
            "the supplied list; never translate or invent them."
        )
    # Prompt gövdesi (contract + few-shot) İngilizce-ağırlıklı; tek satırlık
    # zayıf yönerge modeli İngilizce içeriğe kaydırabiliyor → direktif vurgulu.
    return (
        "Sen deneyimli bir kişisel antrenörsün. SADECE tek bir geçerli JSON "
        "nesnesi döndür. Markdown, kod çiti veya açıklama yok. "
        "odak, not ve tüm görünen metin alanlarını TÜRKÇE yaz — talimat ve "
        "örnekler İngilizce olsa bile. exercise_id bir metin değil, opak "
        "katalog kimliğidir: verilen listeden BİREBİR kopyala, asla çevirme "
        "veya uydurma."
    )


# The JSON FORMAT example names one real ID. It is taken from the request's
# own choice set, so the example itself can never show an ID the server will
# refuse for this request (e.g. a dumbbell lift to a bodyweight-only user).
_EXAMPLE_EXERCISE_IDS = ("ex_goblet_squat", "ex_bodyweight_squat")


def _example_exercise_id(exercise_choices: CompatibleExerciseChoices) -> str:
    for exercise_id in _EXAMPLE_EXERCISE_IDS:
        if exercise_id in exercise_choices:
            return exercise_id
    return exercise_choices.choices[0].exercise_id


def build_training_prompt(
    features: UserTrainingFeatures,
    preferences: TrainingPreferences,
    classification: ClassificationResult,
    context: ProgramContext,
    language: str = "tr",
    *,
    exercise_choices: CompatibleExerciseChoices,
) -> str:
    """The generation prompt. ``exercise_choices`` is required and non-empty:
    the provider output contract is an ``exercise_id`` from this closed set,
    so a prompt without it would ask for something the server must refuse."""
    if not isinstance(exercise_choices, CompatibleExerciseChoices) or not exercise_choices.choices:
        raise ValueError("a non-empty closed exercise choice set is required")
    few_shot = load_few_shot(preferences.antrenman_tarzi)
    injury_text = injury_constraints.build_injury_directive(preferences.injuries)
    cardio_days = preferences.kardiyo_gun if preferences.kardiyo_tipi != "yok" else 0
    dinlenme_gun = 7 - preferences.gun_sayisi - cardio_days
    if language == "en":
        lang_rule = ("İçerik dili: ENGLISH; ama gun ve tip kanonik Türkçe kalacak; "
                     "exercise_id opak kimliktir, çevrilmez.")
    else:
        # Few-shot ve kural metinleri İngilizce; içerik dili kuralı vurgulu
        # olmazsa model İngilizceye kayıyor (TR kullanıcı EN plan görüyordu).
        lang_rule = ("İçerik dili: TÜRKÇE — odak, not ve tüm görünen metinler "
                     "Türkçe yazılacak (yukarıdaki İngilizce kural/örnek "
                     "metinlerine rağmen); gun ve tip kanonik Türkçe kalacak; "
                     "exercise_id görünen metin DEĞİLDİR, opak kimliktir ve "
                     "ASLA çevrilmez.")
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
    style_key = canonical_style(preferences.antrenman_tarzi)
    focus_directive = load_focus_directive(preferences.odak_hedef)
    if preferences.kardiyo_tipi != "yok":
        cardio_block = (
            f"- Kardiyo türü: {cardio_label}\n"
            f"- Haftada {preferences.kardiyo_gun} gün kardiyo\n"
            f"- Cardio: {cardio_label}, {preferences.kardiyo_gun} gün, "
            f"{preferences.kardiyo_sure} dk, {preferences.kardiyo_yogunluk}"
        )
    else:
        cardio_block = "- Cardio: none (no dedicated cardio-day allocation)"
    choice_lines = "\n".join(
        f"- {choice.exercise_id} | {choice.canonical_name}"
        for choice in exercise_choices.choices
    )
    exercise_choices_block = (
        "\nEXERCISE CHOICES (kapalı liste — exercise_id | katalog adı)\n"
        "Her egzersiz için \"exercise_id\" alanına SADECE aşağıdaki listeden "
        "bir kimliği BİREBİR kopyala.\n"
        "- exercise_id opak bir kimliktir: çevirme, yeniden yazma, kısaltma, "
        "harf değiştirme; listede olmayan kimlik uydurma.\n"
        "- İçerik dili kuralı exercise_id değerlerine UYGULANMAZ.\n"
        "- Katalog adı yalnızca seçim içindir; çıktıya \"isim\" yazma — "
        "egzersiz adını sunucu katalogdan yazar.\n"
        "- Sunucu her exercise_id değerini bu listeye karşı doğrular; listede "
        "olmayan bir kimlik planı geçersiz kılar.\n"
        f"{choice_lines}\n"
    )
    example_id = _example_exercise_id(exercise_choices)
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
- Canonical program style: {style_key} ({preferences.antrenman_tarzi})

USER PROFILE
- Profile goal (body/composition context): {features.goal}
- Program focus (odak_hedef={preferences.odak_hedef}): {focus_directive}
- Self-reported level: {features.self_reported_level}
- Current activity: {features.current_activity}
- Age: {features.age or 'unknown'}
- Weight: {features.weight or 'unknown'} kg
- Weekly frequency tolerance: {preferences.gun_sayisi}
- Equipment: {preferences.ekipman}
- Focus: {preferences.odak}
- Session duration: {preferences.sure} dk
{cardio_block}

MOVEMENT COVERAGE REQUIRED
{', '.join(context.movement_coverage)}
{exercise_choices_block}
STYLE FEW-SHOT REFERENCE
{few_shot[:2500]}

{injury_text}

PROGRAM RULES
1. Haftanın tam 7 günü için plan yap: {preferences.gun_sayisi} antrenman günü + {cardio_days} kardiyo günü + {dinlenme_gun} dinlenme/aktif toparlanma günü.
2. "gun" alanı sadece şu değerlerden biri olsun: Pazartesi, Salı, Çarşamba, Perşembe, Cuma, Cumartesi, Pazar.
3. "tip" alanı sadece antrenman, dinlenme veya kardiyo olsun.
4. Aynı ağır pattern iki gün üst üste gelmesin; recovery factor düşükse hacim ve RPE azalt.
5. Her antrenman gününde gerçekçi egzersiz, set, tekrar, dinlenme ve kısa teknik not yaz.
6. {lang_rule}
7. SADECE tek bir JSON nesnesi döndür. Markdown/kod çiti/yorum yok.
8. Yalnızca şemadaki anahtarları kullan; ekstra anahtar ekleme.
9. Egzersiz nesnesi tam olarak exercise_id, set, tekrar, dinlenme, not içersin; exercise_id EXERCISE CHOICES listesinden birebir kopyalanır, "isim" alanı yazılmaz.
10. set bir tamsayı olsun; sure_dk ve tahmini_kalori tamsayı olsun.
11. Kardiyo egzersizleri (koşu, yürüyüş, ip atlama, bisiklet, yüzme) SADECE tip="kardiyo" günlerine yazılsın; tip="antrenman" gününe kardiyo egzersizi koyma.
12. tip="dinlenme" günü HİÇBİR egzersiz içermez: "egzersizler": [] olmalı. Aktif toparlanma önerisini o günün "odak" alanına yaz; yürüyüş/esneme/mobilite dahil hiçbir hareketi dinlenme gününün egzersiz listesine ekleme.

JSON FORMAT
{{"program":[{{"gun":"Pazartesi","tip":"antrenman","odak":"Full Body","sure_dk":45,"tahmini_kalori":320,"egzersizler":[{{"exercise_id":"{example_id}","set":3,"tekrar":"8-12","dinlenme":"90 sn","not":"RPE 7, kontrollü tempo"}}]}}],"haftalik_ozet":{{"toplam_antrenman_gun":{preferences.gun_sayisi},"toplam_tahmini_kalori":1400,"yogunluk_skoru":7,"denge_skoru":8,"uygunluk_skoru":8}}}}
""".strip()
