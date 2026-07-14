# Konuşma özetleme promptu (Sprint 4 WS1 — memory_manager.maybe_summarize).
# Ucuz/hafif LLM yoluyla (gpt-4o-mini) çalışır; özet, koç bağlam penceresine
# [KONUŞMA ÖZETİ] notu olarak girer.

SUMMARIZE_SYSTEM = ("Sen bir konuşma özetleyicisisin. SADECE özet metnini döndür — "
                    "başlık, açıklama veya markdown ekleme. Türkçe yaz.")


def build_summarize_prompt(existing_summary, transcript):
    """Eski özet (varsa) + yeni turların dökümünden TEK birleşik özet ister.
    Kalıcı koç hafızası (sakatlık, hedef, kısıt) User.user_metadata'da ayrıca
    yaşar; buradaki özet yalnızca SOHBET bağlamı içindir."""
    parts = []
    if existing_summary:
        parts.append("Önceki konuşma özeti:\n" + existing_summary)
    parts.append("Yeni konuşma turları:\n" + transcript)
    parts.append(
        "Yukarıdaki fitness-koçu sohbetini, koçun SONRAKİ turlarda ihtiyaç duyacağı "
        "bilgiler kalacak şekilde TEK bir özete indir (varsa önceki özetle birleştir):\n"
        "- kullanıcının bahsettiği hedefler, kısıtlar, sakatlıklar, tercihler\n"
        "- loglanan/konuşulan yemekler ve antrenmanlar (önemli sayılarla)\n"
        "- koçun verdiği önemli öneriler ve kullanıcının kararları\n"
        "- açık kalan konular (onay bekleyen kayıt, yanıtlanmamış soru)\n"
        "En fazla 10 kısa madde kullan; alakasız sohbeti atla.")
    return "\n\n".join(parts)
