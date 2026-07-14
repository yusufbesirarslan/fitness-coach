# AI yanıt hattı — İstem Kurucu aşaması (Sprint 4 WS3; ai_coach.py'den taşındı).
# Sağlayıcıya giden system/messages yapılarını kurar. Şablon METİNLERİ
# app/prompts/'ta yaşar; burada yalnızca birleştirme/biçimleme mantığı var.
# Sağlayıcı bayrağı (BEDROCK_PROMPT_CACHE) BİLEREK parametredir: çağıran
# (ai_coach) kendi modül-globalini çağrı anında okuyup geçirir — testler o
# globali monkeypatch'ler.
from app.prompts.system import build_coach_system  # noqa: F401  (kanonik giriş)


def build_bedrock_system(context, language="tr", prompt_cache=False):
    """Bedrock `system` parametresini kur. Caching açıkken dile özgü sistem
    promptunu ephemeral cache breakpoint ile işaretle; DEĞİŞKEN [KULLANICI VERİSİ]
    daima önbelleğe-alınan bloğun SONRASINA gelir (asla içine gömülmez — sessiz
    invalidasyon). Caching kapalıyken düz string yeterli."""
    system_prompt = build_coach_system(language)
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


def build_openai_messages(language, context, history, question):
    """OpenAI function-calling döngüsünün açılış mesaj dizisi:
    system → [KULLANICI VERİSİ] → geçmiş → güncel soru."""
    messages = [{"role": "system", "content": build_coach_system(language)}]
    if context:
        messages.append({"role": "system", "content": f"[KULLANICI VERİSİ]\n{context}"})
    messages.extend(history)
    messages.append({"role": "user", "content": question})
    return messages


def build_anthropic_messages(history, question):
    """Anthropic Messages dizisi: geçmiş + güncel soru. Anthropic ilk mesajın
    'user' rolünde olmasını şart koşar; baştaki assistant turlarını (ör.
    widget'ın açılış bot mesajı) at — aksi halde ilk çağrı 400 verir."""
    convo = [dict(m) for m in history] + [{"role": "user", "content": question}]
    while convo and convo[0].get("role") != "user":
        convo.pop(0)
    return convo
