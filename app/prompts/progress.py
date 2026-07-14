# Haftalık check-in / ilerleme geri bildirimi promptu
# (ai_coach.generate_checkin_feedback'ten taşındı).
from app.prompts.system import coach_lang

YOGUNLUK_LABELS = {1: "çok düşük", 2: "düşük", 3: "orta", 4: "yüksek", 5: "çok yüksek"}
FATIGUE_LABELS  = {1: "hiç yorgun değil", 2: "biraz yorgun", 3: "normal", 4: "yorgun", 5: "çok yorgun"}
OVERLOAD_LABELS = {"evet": "ağırlık/tekrar artırdı", "hayir": "artıramadı", "kismen": "kısmen artırdı"}
UYKU_LABELS     = {1: "çok kötü", 2: "kötü", 3: "orta", 4: "iyi", 5: "çok iyi"}
BESLENME_LABELS = {1: "hiç uyamadı", 2: "zayıf", 3: "orta", 4: "iyi", 5: "tam uyum"}


def build_checkin_progress_text(goal, weight, prev_weight, days_passed):
    progress = "İlk check-in, geçmiş veri yok."
    if prev_weight and days_passed:
        diff = round(weight - prev_weight, 1)
        if (goal or "").lower() == "kas kazanma":  # B14: goal None guard
            if diff > 0:
                progress = f"{days_passed} günde {abs(diff)} kg aldı — kas kazanma hedefinde olumlu."
            elif diff < 0:
                progress = f"{days_passed} günde {abs(diff)} kg verdi — kas kazanma hedefinde bu istenmeyen."
            else:
                progress = f"{days_passed} günde kilo değişmedi — kalori artışı gerekebilir."
        else:
            if diff < 0:
                progress = f"{days_passed} günde {abs(diff)} kg verdi — kilo verme hedefinde başarılı."
            elif diff > 0:
                progress = f"{days_passed} günde {abs(diff)} kg aldı — kilo verme hedefinde istenmeyen."
            else:
                progress = f"{days_passed} günde kilo değişmedi — plato olabilir."
    return progress


def build_checkin_feedback_prompt(name, weight, prev_weight, days_passed,
                                  goal, yogunluk, fatigue, overload,
                                  uyku, beslenme, note, language="tr"):
    """generate_checkin_feedback için (prompt, system_prompt) çifti."""
    progress = build_checkin_progress_text(goal, weight, prev_weight, days_passed)

    checkin_lang = ('Write your ENTIRE feedback in English; address the user as "you". '
                    'The data below is in Turkish — translate it as needed.'
                    if coach_lang(language) == "en"
                    else 'Türkçe yaz, İngilizce kullanma. Kullanıcıya "sen" diye hitap et.')

    prompt = f"""Sen bir kişisel fitness koçusun. {checkin_lang}

Haftalık check-in verileri:
- İsim: {name}
- Güncel kilo: {weight} kg
- Hedef: {goal}
- İlerleme: {progress}
- Antrenman yoğunluğu: {YOGUNLUK_LABELS.get(yogunluk, 'orta')}
- Yorgunluk durumu: {FATIGUE_LABELS.get(fatigue, 'normal')}
- Progressive overload: {OVERLOAD_LABELS.get(overload, 'kısmen')}
- Uyku kalitesi: {UYKU_LABELS.get(uyku, 'orta')}
- Beslenme uyumu: {BESLENME_LABELS.get(beslenme, 'orta')}
- Kullanıcının notu: {note if note else 'Yok'}

Bu verilere dayanarak kısa ve spesifik bir koçluk geri bildirimi yaz.
Maksimum 4-5 cümle. İyi giden şeyleri vurgula, eksik olanlar için somut öneri ver.
Fatigue yüksekse dinlenme öner, progressive overload yapamadıysa nasıl yapabileceğini anlat.
Uyku kötüyse bunun etkisini açıkla."""

    system_prompt = ("You are a fitness coach. Speak concise, specific, motivating English."
                     if coach_lang(language) == "en"
                     else "Sen bir fitness koçusun. Kısa, spesifik, motive edici Türkçe konuş.")
    return prompt, system_prompt
