# AI yanıt hattı — İstem Kurucu aşaması (Sprint 4 WS3; ai_coach.py'den taşındı).
# Sağlayıcıya giden system/messages yapılarını kurar. Şablon METİNLERİ
# app/prompts/'ta yaşar; burada yalnızca birleştirme/biçimleme mantığı var.
# Sağlayıcı bayrağı (BEDROCK_PROMPT_CACHE) BİLEREK parametredir: çağıran
# (ai_coach) kendi modül-globalini çağrı anında okuyup geçirir — testler o
# globali monkeypatch'ler. adaptive_plan_context de AYNI nedenle parametredir:
# planlama yetkisi AI_ADAPTIVE_PLAN_CONTEXT bayrağından gelir, bağlam METNİNDEN
# çıkarılmaz (bağlamda kullanıcı yazdığı alanlar var — kanonik başlığı taklit
# eden bir metin sistem promptunu çevirememeli).
from app.prompts.system import build_coach_system  # noqa: F401  (kanonik giriş)


def build_bedrock_system(context, language="tr", prompt_cache=False,
                         adaptive_plan_context=False,
                         plan_mutation_tools=False):
    """Bedrock `system` parametresini kur. Caching açıkken dile özgü sistem
    promptunu ephemeral cache breakpoint ile işaretle; DEĞİŞKEN [KULLANICI VERİSİ]
    daima önbelleğe-alınan bloğun SONRASINA gelir (asla içine gömülmez — sessiz
    invalidasyon). Caching kapalıyken düz string yeterli."""
    system_prompt = build_coach_system(
        language, adaptive_plan_context=adaptive_plan_context,
        plan_mutation_tools=plan_mutation_tools)
    if prompt_cache:
        blocks = [{"type": "text", "text": system_prompt,
                   "cache_control": {"type": "ephemeral"}}]
        if context:
            blocks.append({"type": "text", "text": f"[KULLANICI VERİSİ]\n{context}"})
        return blocks
    sys = system_prompt
    if context:
        sys += f"\n\n[KULLANICI VERİSİ]\n{context}"
    return sys


def anthropic_tools_for_call(tools_anthropic, prompt_cache=False):
    """Anthropic araç listesinin çağrı-zamanı kopyası. Caching açıkken SON araca
    bir cache breakpoint ekler (sıra sabit → stabil önbellek). Temel liste
    kirletilmez."""
    if not (prompt_cache and tools_anthropic):
        return tools_anthropic
    tools = [dict(t) for t in tools_anthropic]
    tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def build_openai_messages(language, context, history, question,
                          adaptive_plan_context=False,
                          plan_mutation_tools=False):
    """OpenAI function-calling döngüsünün açılış mesaj dizisi:
    system → [KULLANICI VERİSİ] → geçmiş → güncel soru."""
    messages = [{
        "role": "system",
        "content": build_coach_system(
            language, adaptive_plan_context=adaptive_plan_context,
            plan_mutation_tools=plan_mutation_tools),
    }]
    if context:
        messages.append({"role": "system", "content": f"[KULLANICI VERİSİ]\n{context}"})
    messages.extend(history)
    messages.append({"role": "user", "content": question})
    return messages


def build_anthropic_messages(history, question):
    """Anthropic Messages dizisi: geçmiş + güncel soru. Anthropic ilk mesajın
    'user' rolünde olmasını şart koşar; baştaki assistant turlarını (ör.
    widget'ın açılış bot mesajı) at — aksi halde ilk çağrı 400 verir.

    B15: ardışık aynı-rol turları da 400 verir. Geçmiş CEVAPSIZ bir user
    mesajıyla bitiyorsa (yarıda kesilen stream, WS2) güncel soruyu eklediğimizde
    iki user turu arka arkaya gelirdi → Bedrock 400 → sessizce gpt-4o-mini
    yedeğine düşerdik. Montaj tek kapı olduğu için birleştirmeyi burada yap."""
    convo = [dict(m) for m in history] + [{"role": "user", "content": question}]
    while convo and convo[0].get("role") != "user":
        convo.pop(0)
    merged = []
    for msg in convo:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] = f"{merged[-1]['content']}\n{msg['content']}"
        else:
            merged.append(dict(msg))
    return merged
