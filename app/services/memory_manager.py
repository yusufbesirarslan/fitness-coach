# AI yanıt hattı — Hafıza Yöneticisi aşaması (Sprint 4 WS3; ai_coach.py'den taşındı).
# Bugün: widget'ın gönderdiği kısa dönem sohbet geçmişini sınırlar/temizler ve
# kaba token tahmini sağlar. WS1 (kalıcı konuşma hafızası) bu modülü DB-destekli
# rolling-window + otomatik özetlemeyle genişletecek.

COACH_HISTORY_LIMIT = 6          # son 3 alışveriş (user+assistant) yeterli bağlam


COACH_HISTORY_CHAR_CAP = 400     # her turu kırp ki istem şişmesin


# Kaba karakter→token oranı. Türkçe/İngilizce karışık metinde ~4 karakter ≈ 1
# token; kesin sayım İSTEMEZ — bütçe kararları güvenli tarafta kalsın diye
# kullanılır, gerçek kullanım sağlayıcı yanıtındaki `usage` alanından okunur.
CHARS_PER_TOKEN = 4


def estimate_tokens(text):
    """Metin için kaba token tahmini (len // 4). None güvenli."""
    return len(text or "") // CHARS_PER_TOKEN


def sanitize_client_history(raw):
    """Widget'ın gönderdiği konuşma geçmişini güvenli OpenAI mesajlarına çevir.

    Widget formatı: [{"role": "user"|"bot", "text": "..."}]. 'bot' → 'assistant'
    eşlenir, metin kırpılır, yalnızca düz-metin user/assistant turları geçer
    (tool/sistem rolleri client'tan KABUL EDİLMEZ). Bozuk girdi sessizce atlanır."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[-COACH_HISTORY_LIMIT:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        role = "assistant" if role in ("bot", "assistant") else "user" if role == "user" else None
        if role is None:
            continue
        text = item.get("text") or item.get("content") or ""
        if not isinstance(text, str):
            continue
        text = text.strip()
        if text:
            content = text[:COACH_HISTORY_CHAR_CAP]
            # B15: Anthropic ardışık aynı-rol turlarında 400 verir; iki user (veya
            # iki assistant) turu arka arkaya gelirse birleştir ki Bedrock çağrısı
            # 400'lenip sessizce gpt-4o-mini yedeğine düşmesin.
            if out and out[-1]["role"] == role:
                merged = f"{out[-1]['content']}\n{content}"[:COACH_HISTORY_CHAR_CAP]
                out[-1]["content"] = merged
            else:
                out.append({"role": role, "content": content})
    return out
