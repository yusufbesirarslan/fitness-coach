# Hedefe duyarlı plan-koçluğu promptu (ai_coach.generate_coach_reply'den taşındı).
# Kas kazanmada kilo artışı OLUMLU, kilo vermede azalış OLUMLU çerçevelenir —
# metinler test_ai_coach.py'deki çerçeveleme testleriyle sabitlenmiştir.
from app.prompts.system import coach_lang

ACTIVITY_LABELS = {
    "sedentary"  : "hareketsiz (masa başı iş)",
    "active"     : "aktif (haftada 3-5 gün)",
    "very_active": "çok aktif (haftada 5-6 gün)",
}


def build_progress_text(goal, weight, previous_weight, days_passed):
    """Önceki kayda göre ilerleme çerçevesi. Hedefe göre aynı kilo değişimi
    başarı ya da uyarı olarak sunulur."""
    diff = 0
    sure = "0 gün"
    progress_text = "İlk kayıt — geçmiş veri yok."
    if previous_weight and days_passed is not None:
        diff = round(weight - previous_weight, 1)
        sure = f"{days_passed} gün"
        if days_passed >= 7:
            sure = f"{days_passed // 7} hafta {days_passed % 7} gün"

    if (goal or "").lower() == "kas kazanma":  # B14: goal None → AttributeError guard
        # Kas kazanmada kilo artışı OLUMLU
        if diff > 0:
            progress_text = (
                f"Kullanıcı {sure} önce {previous_weight} kg'dı, "
                f"şimdi {weight} kg — {abs(diff)} kg almış. "
                f"KAS KAZANMA hedefinde bu bir BAŞARI. Tebrik et. "
                f"Kalori fazlası doğru çalışıyor, devam etmesini söyle."
            )
        elif diff < 0:
            progress_text = (
                f"Kullanıcı {sure} önce {previous_weight} kg'dı, "
                f"şimdi {weight} kg — {abs(diff)} kg vermiş. "
                f"KAS KAZANMA hedefinde bu istenmeyen bir durum. "
                f"Kalori alımını artırması gerektiğini vurgula. "
                f"Kalori açığı değil, kalori fazlası hedefleniyor."
            )
        else:
            progress_text = (
                f"Kullanıcı {sure} önce de {previous_weight} kg'dı, kilo değişmemiş. "
                f"KAS KAZANMA hedefinde kilo artışı bekleniyor. "
                f"Kalori alımını biraz artırmasını öner."
            )
    else:
        # Kilo verme hedefinde
        if diff < 0:
            progress_text = (
                f"Kullanıcı {sure} önce {previous_weight} kg'dı, "
                f"şimdi {weight} kg — {abs(diff)} kg vermiş. "
                f"KİLO VERME hedefinde bu bir BAŞARI. Tebrik et. "
                f"Verme hızını değerlendir: {sure}de {abs(diff)} kg."
            )
        elif diff > 0:
            progress_text = (
                f"Kullanıcı {sure} önce {previous_weight} kg'dı, "
                f"şimdi {weight} kg — {abs(diff)} kg almış. "
                f"KİLO VERME hedefinde bu istenmeyen bir durum. "
                f"Nazikçe değin, kalori takibine odaklanmasını söyle."
            )
        else:
            progress_text = (
                f"Kullanıcı {sure} önce de {previous_weight} kg'dı, kilo değişmemiş. "
                f"KİLO VERME hedefinde plato olabilir. "
                f"Kalori açığını gözden geçirmesini öner."
            )
    return progress_text


def build_plan_reply_prompt(name, age, gender, weight, height,
                            goal, level, current_activity,
                            bmr, tdee, target_calories,
                            user_message,
                            previous_weight=None, days_passed=None, language="tr"):
    """generate_coach_reply için (prompt, system_prompt) çifti."""
    activity_text = ACTIVITY_LABELS.get(current_activity, current_activity)
    progress_text = build_progress_text(goal, weight, previous_weight, days_passed)

    if coach_lang(language) == "en":
        lang_line = ('You speak ENGLISH. Address the user directly as "you". Write your '
                     'ENTIRE answer in English and translate the Turkish section headers '
                     'below into natural English headers.')
    else:
        lang_line = ('Türkçe konuşuyorsun. Kullanıcıya "sen" diye hitap et, hiçbir zaman '
                     '"siz" kullanma. Yanıtında tek bir İngilizce kelime bile kullanma, '
                     'tamamen Türkçe yaz.')

    prompt = f"""Sen deneyimli, samimi ve motive edici bir kişisel fitness koçusun.
Şeker boyamıyorsun ama insanı kırmıyorsun da.
{lang_line}
Tavsiyelerin bu kişinin spesifik verilerine dayansın — genel geçer şeyler söyleme.
"ÇOK ÖNEMLİ: Kas kazanma hedefinde kilo artışı OLUMLUDUR, bunu asla negatif algılama. "
"Kilo verme hedefinde kilo azalışı OLUMLUDUR. "
"Hedefe göre değerlendirme yap, genel kalıplarla düşünme."

Kullanıcı bilgileri:
- İsim: {name}, Yaş: {age}, Cinsiyet: {gender}
- Güncel kilo: {weight}kg, Boy: {height}cm
- BMR: {round(bmr)} kcal, TDEE: {round(tdee)} kcal
- Hedef kalori: {round(target_calories)} kcal/gün
- Hedef: {goal}
- Fitness seviyesi: {level}
- Günlük aktivite: {activity_text}
- Kullanıcının sorusu/mesajı: {user_message if user_message else 'Yok'}

İlerleme durumu:
{progress_text}

Aşağıdaki formatta kişiye özel koçluk yorumu yaz:

GENEL DEĞERLENDİRME:
(2-3 cümle — mevcut durumu ve ilerlemeyi değerlendir, sayıları kullan)

EN KRİTİK ADIM:
(şu an odaklanması gereken tek şey, spesifik ve ölçülebilir olsun)

BU HAFTA YAP:
1. (spesifik görev — gün, süre, miktar belirt)
2. (spesifik görev — gün, süre, miktar belirt)
3. (spesifik görev — gün, süre, miktar belirt)

MOTİVASYON:
(bu kişinin durumuna özel, güçlü bir cümle)"""

    system_prompt = ("You are a fitness coach. Speak English — friendly, specific, "
                     "motivating; use numbers and durations."
                     if coach_lang(language) == "en"
                     else "Sen bir fitness koçusun. Türkçe, samimi, spesifik ve motive edici konuş. Sayılar ve süreler kullan.")
    return prompt, system_prompt
