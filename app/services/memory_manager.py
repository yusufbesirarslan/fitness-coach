# AI yanıt hattı — Hafıza Yöneticisi aşaması (Sprint 4 WS3 + WS1).
# İki hafıza katmanı:
#  1) Kalıcı DB hafızası (CoachConversation/CoachMessage): rolling context
#     window + otomatik özetleme + reset. Tarayıcı yenilense de sürer; kullanıcı
#     başına izole (tüm sorgular user_id'ye scope'lu).
#  2) Eski kısa-dönem yol (sanitize_client_history): widget'ın gönderdiği geçmiş —
#     AI_MEMORY_ENABLED=0 veya DB arızasında fallback olarak yaşamaya devam eder.
# Uzun-süreli YAPISAL hafıza (sakatlık/hedef/kısıt) ayrı bir kanaldır:
# User.user_metadata + manage_user_memory aracı (ai_coach).

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


# ── Kalıcı konuşma hafızası (WS1) ────────────────────────────────────────────

# Pencere kurulumunda DB'den çekilecek azami satır (bütçe zaten sınırlar; bu,
# patolojik uzun kuyruklarda sorguyu kapatan güvenlik tavanıdır).
HISTORY_FETCH_LIMIT = 100

# Özetleme daima en yeni N mesajı DIŞARIDA bırakır — taze bağlam ham kalsın,
# model az önce söyleneni özetten değil mesajdan görsün.
SUMMARY_KEEP_RECENT = 6

# Tek özetleme çağrısına giren döküm tavanı (~6k token). Kuyruk bundan uzunsa
# yalnızca sığan EN ESKİ mesajlar katlanır; summarized_upto_id son KATLANAN
# mesaja ilerler, kalanlar sonraki tura kalır (bilgi kaybı yok).
SUMMARY_TRANSCRIPT_CHAR_CAP = 24000

MAX_SUMMARY_CHARS = 4000  # özet metni tavanı (pencere bütçesini yutmasın)


def get_or_create_active_conversation(user_id):
    """Kullanıcının aktif (archived_at IS NULL) konuşmasını getir; yoksa oluştur.

    Bir-aktif-per-kullanıcı değişmezi kod seviyesinde korunur: premium.py
    kota deseniyle aynı şekilde User satırı kilitlenir ki eşzamanlı iki istek
    çift aktif konuşma yaratmasın (SQLite'ta with_for_update no-op — tek yazar)."""
    from app.extensions import db
    from app.models import CoachConversation, User

    db.session.query(User.id).filter_by(id=user_id).with_for_update().first()
    conv = (CoachConversation.query
            .filter_by(user_id=user_id, archived_at=None)
            .order_by(CoachConversation.id.asc())
            .first())
    if conv is None:
        conv = CoachConversation(user_id=user_id)
        db.session.add(conv)
    db.session.commit()  # kilidi bırak (conv varsa da yeni yaratıldıysa da)
    return conv


def archive_active_conversation(user_id):
    """Reset: aktif konuşmayı arşivle (SİLME — eski mesajlar güvenle saklanır).
    Arşivlenen konuşma sayısını döndürür (0 = zaten aktif konuşma yoktu)."""
    from datetime import datetime

    from app.extensions import db
    from app.models import CoachConversation

    count = (CoachConversation.query
             .filter_by(user_id=user_id, archived_at=None)
             .update({"archived_at": datetime.utcnow()}, synchronize_session=False))
    db.session.commit()
    return count


def recent_messages(user_id, limit=50):
    """Aktif konuşmanın son mesajları (eski→yeni) — GET /coach/history ve
    widget hidrasyonu için. Aktif konuşma yoksa boş liste."""
    from app.models import CoachConversation, CoachMessage

    conv = (CoachConversation.query
            .filter_by(user_id=user_id, archived_at=None)
            .order_by(CoachConversation.id.asc()).first())
    if conv is None:
        return None, []
    rows = (CoachMessage.query
            .filter_by(conversation_id=conv.id)
            .order_by(CoachMessage.id.desc())
            .limit(limit).all())
    return conv, list(reversed(rows))


def record_turn(conversation, question, answer, usage=None, interrupted=False):
    """Bir user+assistant turunu kalıcılaştır. usage: sağlayıcı yanıtındaki
    gerçek token sayıları ({'prompt_tokens': .., 'completion_tokens': ..})."""
    from app.extensions import db
    from app.models import CoachMessage

    usage = usage or {}
    db.session.add(CoachMessage(
        conversation_id=conversation.id, role="user", content=question,
        token_estimate=estimate_tokens(question)))
    db.session.add(CoachMessage(
        conversation_id=conversation.id, role="assistant", content=answer,
        token_estimate=estimate_tokens(answer),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        interrupted=bool(interrupted)))
    db.session.commit()


def build_context_window(conversation, budget=None):
    """Rolling context window: [KONUŞMA ÖZETİ notu] + en yeni mesajlardan
    geriye, token bütçesi dolana kadar (pruning: daha eskiler pencere DIŞI
    kalır — silinmez). OpenAI mesaj formatında (role/content) döner; Bedrock
    tarafı prompt_builder.build_anthropic_messages ile aynı listeyi işler.

    Bütçe aşımı KESİN engellenir: tek mesaj bile bütçeden büyükse kırpılır —
    'never exceed model token limits' (spec) buradaki üst sınırla başlar."""
    from app.config import AI_CONTEXT_TOKEN_BUDGET
    from app.models import CoachMessage

    if budget is None:
        budget = AI_CONTEXT_TOKEN_BUDGET

    rows = (CoachMessage.query
            .filter(CoachMessage.conversation_id == conversation.id,
                    CoachMessage.id > (conversation.summarized_upto_id or 0))
            .order_by(CoachMessage.id.desc())
            .limit(HISTORY_FETCH_LIMIT).all())

    used = estimate_tokens(conversation.summary or "")
    window = []
    for m in rows:  # yeniden eskiye
        cost = m.token_estimate or estimate_tokens(m.content)
        if used + cost > budget:
            if window:
                break  # bütçe doldu — kalan eski turlar pencere dışı
            # Pencerede hiçbir şey yokken ilk (en yeni) mesaj bile sığmıyorsa
            # kırpıp al: bağlamsız kalmaktansa kesilmiş son tur daha iyi.
            content = m.content[: max(budget - used, 1) * CHARS_PER_TOKEN]
            window.append({"role": m.role, "content": content})
            break
        window.append({"role": m.role, "content": m.content})
        used += cost
    window.reverse()

    # B15: Anthropic ardışık aynı-rol turlarında 400 verir (yarıda kesilen
    # stream'ler ardışık user turu bırakabilir) — birleştir.
    merged = []
    for msg in window:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] = f"{merged[-1]['content']}\n{msg['content']}"
        else:
            merged.append(dict(msg))

    if conversation.summary:
        # Özet, geçmişin başına kullanıcı-turu notu olarak girer (system'e değil:
        # Bedrock prompt-cache'lenen system bloğu değişken içerik istemez).
        note = {"role": "user",
                "content": f"[KONUŞMA ÖZETİ — önceki turların özeti]\n{conversation.summary}"}
        if merged and merged[0]["role"] == "user":
            merged[0]["content"] = f"{note['content']}\n\n{merged[0]['content']}"
        else:
            merged.insert(0, note)
    return merged


def maybe_summarize(conversation):
    """Özetlenmemiş kuyruk AI_SUMMARY_TRIGGER_TOKENS'ı aşarsa eski turları tek
    hafif LLM çağrısıyla conversation.summary'ye katla. İstek başında lazy
    çalışır (WS8'de RQ arka-plan işine taşınacak). Hata sohbeti BOZMAZ:
    başarısızlıkta False döner, bir sonraki istekte yeniden denenir."""
    from flask import current_app

    from app.config import AI_SUMMARY_TRIGGER_TOKENS
    from app.extensions import db
    from app.models import CoachMessage

    msgs = (CoachMessage.query
            .filter(CoachMessage.conversation_id == conversation.id,
                    CoachMessage.id > (conversation.summarized_upto_id or 0))
            .order_by(CoachMessage.id.asc())
            .limit(HISTORY_FETCH_LIMIT * 2).all())
    if len(msgs) <= SUMMARY_KEEP_RECENT:
        return False
    backlog = sum(m.token_estimate or estimate_tokens(m.content) for m in msgs)
    if backlog <= AI_SUMMARY_TRIGGER_TOKENS:
        return False

    candidates = msgs[:-SUMMARY_KEEP_RECENT]
    # Döküm tavanına sığan EN ESKİ mesajları katla; upto son katlanana ilerler.
    lines, chars, upto_id = [], 0, None
    for m in candidates:
        line = f"{'Kullanıcı' if m.role == 'user' else 'Koç'}: {m.content}"
        if lines and chars + len(line) > SUMMARY_TRANSCRIPT_CHAR_CAP:
            break
        lines.append(line)
        chars += len(line)
        upto_id = m.id
    if upto_id is None:
        return False

    from app.prompts.memory import SUMMARIZE_SYSTEM, build_summarize_prompt
    from app.services.ai import _openai_chat  # hafif yol (gpt-4o-mini)
    try:
        summary = _openai_chat(
            messages=[{"role": "user",
                       "content": build_summarize_prompt(conversation.summary,
                                                         "\n".join(lines))}],
            system_prompt=SUMMARIZE_SYSTEM,
            max_tokens=500,
            temperature=0.2,
        ).strip()
    except Exception:
        current_app.logger.warning("[MEMORY] konuşma özetleme başarısız", exc_info=True)
        return False
    if not summary:
        return False

    conversation.summary = summary[:MAX_SUMMARY_CHARS]
    conversation.summary_tokens = estimate_tokens(conversation.summary)
    conversation.summarized_upto_id = upto_id
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.warning("[MEMORY] özet commit başarısız", exc_info=True)
        return False
    return True
