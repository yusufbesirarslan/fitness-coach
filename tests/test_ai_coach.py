"""Tests for the AI coach service (app/services/ai_coach.py).

Staged→committed loglama durum makinesi (PendingAction), birleşik besin
araması fallback zinciri, araç dispatch'i (user_id LLM'den asla gelmez),
function-calling döngüsü ve koç metin üreticileri. OpenAI mock'lu — ağ yok.

    python -m pytest tests/test_ai_coach.py -v
"""
import json
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import (DailyActivity, MealLog, PendingAction, User, UserSession,
                        WeeklyCheckIn, WorkoutLog)
from app.timeutil import app_today
from app.services import ai_coach
from app.services.ai_coach import (
    _coach_search_food,
    _dispatch_coach_tool,
    _extract_grams,
    _run_coach_conversation,
    _sanitize_client_history,
    generate_checkin_feedback,
    generate_coach_reply,
)

CHICKEN_100G = {"calories": 165.0, "protein": 31.0, "carbs": 0.0, "fat": 3.6}


# ---------------------------------------------------------------------------
# Gram yakalama + arama fallback zinciri
# ---------------------------------------------------------------------------

def test_extract_grams():
    assert _extract_grams("300g tavuk") == 300.0
    assert _extract_grams("1,5 gram tuz") == 1.5
    assert _extract_grams("150 gr pilav") == 150.0
    assert _extract_grams("tavuk göğsü") is None
    assert _extract_grams("9999g et") is None  # 5000g üst sınırı
    assert _extract_grams(None) is None


def _search_chain(monkeypatch, fatsecret=None, english="", static=None, llm=None):
    monkeypatch.setattr(ai_coach, "_food_search_fatsecret",
                        lambda q: (fatsecret or {}).get(q))
    monkeypatch.setattr(ai_coach, "_normalize_food_query_en", lambda q: english)
    monkeypatch.setattr(ai_coach, "_food_search_static", lambda q: static or [])
    monkeypatch.setattr(ai_coach, "_food_search_llm", lambda q: llm or [])


def test_coach_search_relevant_raw_hit_wins(monkeypatch):
    hit = [{"name": "Banana", "per_100g": CHICKEN_100G}]
    _search_chain(monkeypatch, fatsecret={"banana": hit})
    assert _coach_search_food("banana") == hit


def test_coach_search_filters_garbage_then_tries_english(monkeypatch):
    _search_chain(monkeypatch,
                  fatsecret={"patates kızartması": [{"name": "Soy Nuts"}],
                             "french fries": [{"name": "French Fries"}]},
                  english="french fries")
    results = _coach_search_food("patates kızartması")
    assert [r["name"] for r in results] == ["French Fries"]


def test_coach_search_falls_back_to_static_then_llm(monkeypatch):
    static = [{"name": "Muz", "per_100g": CHICKEN_100G}]
    _search_chain(monkeypatch, static=static)
    assert _coach_search_food("muz") == static

    llm = [{"name": "Egzotik", "per_100g": CHICKEN_100G}]
    _search_chain(monkeypatch, llm=llm)
    assert _coach_search_food("egzotik meyve") == llm


# ---------------------------------------------------------------------------
# Araçlar — staged → committed durum makinesi
# ---------------------------------------------------------------------------

def _stage_meal(monkeypatch, user, query="300g tavuk"):
    monkeypatch.setattr(ai_coach, "_coach_search_food",
                        lambda q: [{"name": "Tavuk Göğsü", "food_id": "1",
                                    "per_100g": CHICKEN_100G}])
    return json.loads(ai_coach._tool_fetch_nutrition_and_stage_log(user.id, query))


def test_stage_meal_scales_by_grams_and_replaces_previous(auth_user, monkeypatch):
    result = _stage_meal(monkeypatch, auth_user, "300g tavuk")
    assert result["status"] == "staged"
    assert result["staged_food"]["calories"] == 495.0   # 165 * 3
    assert result["staged_food"]["serving_size"] == "300 g"
    assert result["evaluation"] is None                  # profil hedefi yok

    # İkinci staging öncekini değiştirir — tek aktif bekleyen kayıt.
    _stage_meal(monkeypatch, auth_user, "100g tavuk")
    pendings = PendingAction.query.filter_by(user_id=auth_user.id).all()
    assert len(pendings) == 1
    assert pendings[0].payload["calories"] == 165.0


def test_stage_meal_with_profile_returns_evaluation(auth_user, monkeypatch):
    db.session.add(UserSession(user_id=auth_user.id, target_calories=2000,
                               goal="kas kazanma"))
    db.session.commit()
    result = _stage_meal(monkeypatch, auth_user)
    assert result["evaluation"] is not None
    assert 0 <= result["evaluation"]["compatibility_score"] <= 100


def test_stage_meal_handles_empty_and_not_found(auth_user, monkeypatch):
    result = json.loads(ai_coach._tool_fetch_nutrition_and_stage_log(auth_user.id, "  "))
    assert result["status"] == "error"

    monkeypatch.setattr(ai_coach, "_coach_search_food", lambda q: [])
    result = json.loads(ai_coach._tool_fetch_nutrition_and_stage_log(auth_user.id, "zxqw"))
    assert result["status"] == "not_found"
    assert PendingAction.query.count() == 0


def test_commit_meal_moves_staged_to_permanent_log(auth_user, monkeypatch):
    _stage_meal(monkeypatch, auth_user, "300g tavuk")
    result = json.loads(ai_coach._tool_confirm_and_commit_meal_log(auth_user.id))

    assert result["status"] == "committed"
    assert result["xp_awarded"] == 10
    assert result["today_totals"]["calories"] == 495
    # Koç artık tek kanonik deftere (MealLog) yazar — diyari/menü ile aynı tablo (Faz B).
    entry = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert entry.yemekler == "Tavuk Göğsü"
    assert entry.source == "coach"
    assert entry.ogun == "AI Koç"
    assert PendingAction.query.count() == 0              # staged satır silindi
    db.session.expire_all()
    assert db.session.get(User, auth_user.id).rank_points == 10


def test_commit_meal_without_pending_is_safe(auth_user):
    result = json.loads(ai_coach._tool_confirm_and_commit_meal_log(auth_user.id))
    assert result["status"] == "no_pending"
    assert MealLog.query.count() == 0


def test_today_totals_count_meals_from_any_source(auth_user):
    # Faz B unification: diyari ve koç AYNI deftere (MealLog) yazar; koçun
    # 'bugün tüketilen / kalan bütçe' hesabı her iki giriş yolunu da görür.
    from app.timeutil import day_key
    today = day_key()
    db.session.add(MealLog(user_id=auth_user.id, ogun="Öğle", yemekler="diyari yemeği",
                           kalori=400, protein=30, karb=40, yag=10, tarih=today, source="diary"))
    db.session.add(MealLog(user_id=auth_user.id, ogun="AI Koç", yemekler="koç yemeği",
                           kalori=100, protein=10, karb=5, yag=2, tarih=today, source="coach"))
    db.session.commit()

    totals = ai_coach._today_nutrition_totals(auth_user.id)
    assert totals["calories"] == 500
    assert totals["entry_count"] == 2


def test_stage_workout_defaults_and_commit(auth_user):
    result = json.loads(ai_coach._tool_stage_workout_log(
        auth_user.id, "Squat", "beş", None, -3))      # bozuk girdiler → güvenli default
    assert result["staged_workout"] == {"exercise_name": "Squat", "sets": 3,
                                        "reps": 10, "weight_kg": 0.0, "volume": 0.0}

    json.loads(ai_coach._tool_stage_workout_log(auth_user.id, "Bench", 5, 5, 100))
    result = json.loads(ai_coach._tool_confirm_and_commit_workout_log(auth_user.id))
    assert result["status"] == "committed"
    assert result["xp_awarded"] == 15
    log = WorkoutLog.query.filter_by(user_id=auth_user.id).one()
    assert log.volume == 2500.0                          # 5*5*100

    result = json.loads(ai_coach._tool_confirm_and_commit_workout_log(auth_user.id))
    assert result["status"] == "no_pending"


def test_cancel_removes_all_pending(auth_user, monkeypatch):
    _stage_meal(monkeypatch, auth_user)
    ai_coach._tool_stage_workout_log(auth_user.id, "Squat", 3, 10, 60)
    result = json.loads(ai_coach._tool_cancel_pending_log(auth_user.id))
    assert result == {"status": "cancelled", "removed": 2}
    assert PendingAction.query.count() == 0


def test_dispatch_routes_and_rejects_unknown(auth_user, monkeypatch):
    _search = []
    monkeypatch.setattr(ai_coach, "_tool_fetch_nutrition_and_stage_log",
                        lambda uid, q: _search.append((uid, q)) or "{}")
    _dispatch_coach_tool(auth_user.id, "fetch_nutrition_and_stage_log",
                         '{"food_query": "muz"}')
    assert _search == [(auth_user.id, "muz")]            # user_id sunucudan enjekte

    result = json.loads(_dispatch_coach_tool(auth_user.id, "rm_rf", "{}"))
    assert result["status"] == "error"

    # Bozuk argüman JSON'u → boş argümanlarla güvenli çağrı.
    result = json.loads(_dispatch_coach_tool(auth_user.id,
                                             "fetch_nutrition_and_stage_log", "{bozuk"))
    assert _search[-1] == (auth_user.id, "")


def test_dispatch_routes_remaining_tools(auth_user, monkeypatch):
    calls = []
    monkeypatch.setattr(ai_coach, "_tool_confirm_and_commit_meal_log",
                        lambda uid: calls.append("meal") or "{}")
    monkeypatch.setattr(ai_coach, "_tool_stage_workout_log",
                        lambda *a: calls.append(("workout",) + a[1:]) or "{}")
    monkeypatch.setattr(ai_coach, "_tool_confirm_and_commit_workout_log",
                        lambda uid: calls.append("commit_wo") or "{}")
    monkeypatch.setattr(ai_coach, "_tool_cancel_pending_log",
                        lambda uid: calls.append("cancel") or "{}")
    _dispatch_coach_tool(auth_user.id, "confirm_and_commit_meal_log", "{}")
    _dispatch_coach_tool(auth_user.id, "stage_workout_log",
                         '{"exercise_name": "Bench", "sets": 4, "reps": 8, "weight_kg": 60}')
    _dispatch_coach_tool(auth_user.id, "confirm_and_commit_workout_log", "{}")
    _dispatch_coach_tool(auth_user.id, "cancel_pending_log", "{}")
    assert [c if isinstance(c, str) else c[0] for c in calls] == \
        ["meal", "workout", "commit_wo", "cancel"]
    assert calls[1] == ("workout", "Bench", 4, 8, 60)  # argümanlar doğru iletildi


def test_stage_workout_empty_name_errors(auth_user):
    result = json.loads(ai_coach._tool_stage_workout_log(auth_user.id, "   ", 3, 10, 50))
    assert result["status"] == "error"
    assert "boş" in result["message"]


# ---------------------------------------------------------------------------
# Client geçmişi temizliği
# ---------------------------------------------------------------------------

def test_sanitize_client_history():
    raw = [
        {"role": "bot", "text": "merhaba"},
        {"role": "user", "text": "x" * 1000},
        {"role": "tool", "text": "enjekte"},            # client'tan tool kabul edilmez
        {"role": "user", "text": "   "},
        "düz string",
    ]
    out = _sanitize_client_history(raw)
    assert out[0] == {"role": "assistant", "content": "merhaba"}
    assert out[1]["role"] == "user"
    assert len(out[1]["content"]) == 400                 # COACH_HISTORY_CHAR_CAP
    assert len(out) == 2
    assert _sanitize_client_history("liste değil") == []


def test_sanitize_history_skips_nonstring_text_and_unknown_role():
    raw = [
        {"role": "user", "text": 123},               # metin string değil → atla
        {"role": "system", "text": "enjekte"},        # bilinmeyen rol → atla
        {"role": "assistant", "content": "tamam"},    # 'content' alanı da kabul edilir
        "düz string değil dict",                      # dict değil → atla
    ]
    assert _sanitize_client_history(raw) == [{"role": "assistant", "content": "tamam"}]


# ---------------------------------------------------------------------------
# Function-calling döngüsü
# ---------------------------------------------------------------------------

def _llm_msg(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=content, tool_calls=tool_calls))])


def _tool_call(name, arguments="{}", call_id="call_1"):
    return SimpleNamespace(id=call_id,
                           function=SimpleNamespace(name=name, arguments=arguments))


class _ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if outer._responses:
                    return outer._responses.pop(0)
                return outer_last
        outer_last = _llm_msg("bitti")
        self.chat = SimpleNamespace(completions=_Completions())


def test_conversation_plain_answer(auth_user, monkeypatch):
    llm = _ScriptedLLM([_llm_msg("Protein hedefin 150g.")])
    monkeypatch.setattr(ai_coach, "openai_client", llm)
    answer = _run_coach_conversation(auth_user.id, "protein hedefim?", "bağlam",
                                     client_history=[])
    assert answer == "Protein hedefin 150g."
    sent = llm.calls[0]["messages"]
    assert sent[0]["role"] == "system"
    assert "[KULLANICI VERİSİ]" in sent[1]["content"]
    assert sent[-1] == {"role": "user", "content": "protein hedefim?"}


def test_conversation_tool_call_roundtrip(auth_user, monkeypatch):
    monkeypatch.setattr(ai_coach, "_coach_search_food",
                        lambda q: [{"name": "Muz", "food_id": "9",
                                    "per_100g": {"calories": 89.0, "protein": 1.1,
                                                 "carbs": 23.0, "fat": 0.3}}])
    llm = _ScriptedLLM([
        _llm_msg(tool_calls=[_tool_call("fetch_nutrition_and_stage_log",
                                        '{"food_query": "muz"}')]),
        _llm_msg("Muz 89 kcal. Kaydedeyim mi?"),
    ])
    monkeypatch.setattr(ai_coach, "openai_client", llm)

    answer = _run_coach_conversation(auth_user.id, "muz yedim", "", client_history=[])
    assert answer == "Muz 89 kcal. Kaydedeyim mi?"
    assert PendingAction.query.filter_by(user_id=auth_user.id).count() == 1  # staged

    second_call = llm.calls[1]["messages"]
    tool_msg = second_call[-1]
    assert tool_msg["role"] == "tool"
    assert json.loads(tool_msg["content"])["status"] == "staged"


def test_conversation_tool_loop_capped(auth_user, monkeypatch):
    endless = _llm_msg(tool_calls=[_tool_call("cancel_pending_log")])
    llm = _ScriptedLLM([endless] * 5)
    monkeypatch.setattr(ai_coach, "openai_client", llm)
    answer = _run_coach_conversation(auth_user.id, "x", "", client_history=[])
    assert answer == "İşlemi tamamlayamadım, tekrar dener misin?"
    assert len(llm.calls) == 5


def test_conversation_drops_trailing_user_turns_from_history(auth_user, monkeypatch):
    llm = _ScriptedLLM([_llm_msg("tamam")])
    monkeypatch.setattr(ai_coach, "openai_client", llm)
    history = [{"role": "bot", "text": "önceki cevap"},
               {"role": "user", "text": "şimdiki soru"}]  # widget soruyu da iter
    _run_coach_conversation(auth_user.id, "şimdiki soru", "", client_history=history)
    sent = llm.calls[0]["messages"]
    user_turns = [m for m in sent if m["role"] == "user"]
    assert len(user_turns) == 1                            # çift ekleme yok


def test_conversation_session_fallback_persists_history(app, auth_user, monkeypatch):
    llm = _ScriptedLLM([_llm_msg("cevap")])
    monkeypatch.setattr(ai_coach, "openai_client", llm)
    with app.test_request_context("/"):
        from flask import session
        _run_coach_conversation(auth_user.id, "soru", "", client_history=None)
        assert session["coach_history"][-1] == {"role": "assistant", "content": "cevap"}


# ---------------------------------------------------------------------------
# Koç metin üreticileri
# ---------------------------------------------------------------------------

def test_coach_reply_frames_gain_as_success_for_bulking(app, monkeypatch):
    captured = []
    monkeypatch.setattr(ai_coach, "_heavy_chat",
                        lambda **kw: captured.append(kw["messages"][0]["content"]) or "yorum")
    reply = generate_coach_reply("yusuf", 30, "male", 82, 180, "kas kazanma",
                                 "beginner", "active", 1780, 2759, 3059,
                                 "plan", "plan", "", previous_weight=80, days_passed=14)
    assert reply == "yorum"
    assert "BAŞARI" in captured[0]                  # kas kazanmada +2 kg olumlu
    assert "2 hafta" in captured[0]


def test_coach_reply_frames_gain_as_setback_for_cutting(app, monkeypatch):
    captured = []
    monkeypatch.setattr(ai_coach, "_heavy_chat",
                        lambda **kw: captured.append(kw["messages"][0]["content"]) or "yorum")
    generate_coach_reply("yusuf", 30, "male", 82, 180, "kilo verme",
                         "beginner", "active", 1780, 2759, 2359,
                         "plan", "plan", "", previous_weight=80, days_passed=7)
    assert "istenmeyen" in captured[0]


@pytest.mark.parametrize("goal,weight,expect", [
    ("kas kazanma", 78, "istenmeyen"),                  # diff<0 kas kazanmada kötü
    ("kas kazanma", 80, "kilo değişmemiş"),             # diff==0 kas kazanmada durağan
    ("kilo verme", 78, "BAŞARI"),                       # diff<0 kilo vermede başarı
])
def test_coach_reply_progress_framings(app, monkeypatch, goal, weight, expect):
    captured = []
    monkeypatch.setattr(ai_coach, "_heavy_chat",
                        lambda **kw: captured.append(kw["messages"][0]["content"]) or "yorum")
    generate_coach_reply("y", 30, "male", weight, 180, goal, "beginner", "active",
                         1780, 2759, 2359, "plan", "plan", "",
                         previous_weight=80, days_passed=14)
    assert expect in captured[0]


def test_coach_reply_fallback_on_llm_failure(app, monkeypatch):
    def boom(**kw):
        raise RuntimeError("down")
    monkeypatch.setattr(ai_coach, "_heavy_chat", boom)
    reply = generate_coach_reply("y", 30, "male", 80, 180, "kilo verme",
                                 "beginner", "active", 1780, 2759, 2359, "p", "p", "")
    assert "tekrar dene" in reply


def test_checkin_feedback_progress_framing(app, monkeypatch):
    captured = []
    monkeypatch.setattr(ai_coach, "_heavy_chat",
                        lambda **kw: captured.append(kw["messages"][0]["content"]) or "geri bildirim")
    out = generate_checkin_feedback("y", 78, 80, 7, "kilo verme", 4, 2,
                                    "evet", 4, 4, "")
    assert out == "geri bildirim"
    assert "2 kg verdi — kilo verme hedefinde başarılı" in captured[0]


def test_checkin_feedback_fallback(app, monkeypatch):
    def boom(**kw):
        raise RuntimeError("down")
    monkeypatch.setattr(ai_coach, "_heavy_chat", boom)
    out = generate_checkin_feedback("y", 78, None, None, "x", 3, 3, "kismen", 3, 3, "")
    assert "tekrar dene" in out


@pytest.mark.parametrize("goal,weight,prev,expect", [
    ("kas kazanma", 82, 80, "kg aldı — kas kazanma hedefinde olumlu"),
    ("kas kazanma", 78, 80, "kas kazanma hedefinde bu istenmeyen"),
    ("kas kazanma", 80, 80, "kilo değişmedi — kalori artışı gerekebilir"),
    ("kilo verme", 82, 80, "kilo verme hedefinde istenmeyen"),
    ("kilo verme", 80, 80, "kilo değişmedi — plato olabilir"),
])
def test_checkin_feedback_all_progress_framings(app, monkeypatch, goal, weight, prev, expect):
    captured = []
    monkeypatch.setattr(ai_coach, "_heavy_chat",
                        lambda **kw: captured.append(kw["messages"][0]["content"]) or "ok")
    generate_checkin_feedback("y", weight, prev, 7, goal, 3, 3, "kismen", 3, 3, "")
    assert expect in captured[0]


# ---------------------------------------------------------------------------
# Bağlam toplama (fitx_mcp araçları mock'lu)
# ---------------------------------------------------------------------------

def test_fetch_coach_context_collects_sections_and_degrades(auth_user, monkeypatch):
    import fitx_mcp.server as mcp_server

    def fail(*args):
        raise RuntimeError("db down")
    monkeypatch.setattr(mcp_server, "get_user_fitness_summary", lambda uid: "özet verisi")
    monkeypatch.setattr(mcp_server, "get_user_workout_history", fail)
    monkeypatch.setattr(mcp_server, "get_user_supplement_stack", lambda uid: "kreatin")
    monkeypatch.setattr(mcp_server, "get_user_nutrition_log", fail)
    monkeypatch.setattr(mcp_server, "get_friend_activities", fail)

    context = ai_coach._fetch_coach_context(auth_user.id, "soru")
    assert "[FITNESS ÖZETİ]\nözet verisi" in context
    assert "[SUPPLEMENT STACK]\nkreatin" in context
    assert "[ANTRENMAN GEÇMİŞİ" not in context           # hata → bölüm atlandı
    assert "[PROAKTİF BİLDİRİMLER]" in context            # nudge motoru gerçek DB'den


def test_fetch_coach_context_degrades_summary_supplements_and_nudges(auth_user, monkeypatch):
    import fitx_mcp.server as mcp_server
    import analytics_engine

    def fail(*args, **kwargs):
        raise RuntimeError("db down")
    # Özet + supplement + nudge motoru patlar; diğerleri çalışır → kısmi bağlam.
    monkeypatch.setattr(mcp_server, "get_user_fitness_summary", fail)
    monkeypatch.setattr(mcp_server, "get_user_workout_history", lambda uid, d: "antrenman")
    monkeypatch.setattr(mcp_server, "get_user_supplement_stack", fail)
    monkeypatch.setattr(mcp_server, "get_user_nutrition_log", lambda uid, d: "log")
    monkeypatch.setattr(mcp_server, "get_friend_activities", lambda uid: "aktivite")
    monkeypatch.setattr(analytics_engine, "get_nudges", fail)

    context = ai_coach._fetch_coach_context(auth_user.id)
    assert "[FITNESS ÖZETİ] Veri alınamadı." in context  # özet hatası → düşürülmüş etiket
    assert "[SUPPLEMENT STACK]" not in context            # supplement hatası → atlandı
    assert "[PROAKTİF BİLDİRİMLER]" not in context         # nudge hatası → atlandı
    assert "[ANTRENMAN GEÇMİŞİ (7 gün)]\nantrenman" in context


def test_fetch_profile_and_trends_surfaces_memory_checkins_activity(auth_user):
    # Kalıcı hafıza (manage_user_memory ile yazılan veriler).
    auth_user.user_metadata = {"injuries": "Menisküs",
                               "dietary_restrictions": "laktoz intoleransı",
                               "equipment_available": "dumbbell, bench"}
    auth_user.target_weight = 82.0
    auth_user.goal_type = "cut"
    # Haftalık check-in: düşük uyku / yüksek yorgunluk / duraksamış yüklenme.
    db.session.add(WeeklyCheckIn(user_id=auth_user.id, weight=85.0, yogunluk=4,
                                 fatigue=5, uyku_kalitesi=2, beslenme_uyumu=3,
                                 progressive_overload="hayir", note="dizim ağrıyor"))
    # Günlük aktivite (son 7 gün içinde).
    db.session.add(DailyActivity(user_id=auth_user.id, steps=8000,
                                 calories_burned=350, distance_km=6.0,
                                 duration_min=60, date_key=app_today().isoformat()))
    db.session.commit()

    blob = "\n\n".join(ai_coach._fetch_profile_and_trends(auth_user.id))
    assert "[KULLANICI PROFİLİ & HAFIZA]" in blob
    assert "Menisküs" in blob
    assert "laktoz intoleransı" in blob
    assert "dumbbell, bench" in blob
    assert "82.0 kg" in blob
    assert "[HAFTALIK CHECK-IN TRENDİ" in blob
    assert "uyku kalitesi 2/5" in blob
    assert "progresif yüklenme: hayir" in blob
    assert "[GÜNLÜK AKTİVİTE (7 gün)]" in blob
    assert "toplam adım: 8000" in blob


def test_fetch_profile_and_trends_flags_missing_injuries_and_omits_empty(auth_user):
    # injuries kaydı yoksa profil bloğu DAİMA gelir ve koç sorması için işaretlenir;
    # check-in / aktivite verisi olmayan bölümler atlanır (gürültü üretme).
    blob = "\n\n".join(ai_coach._fetch_profile_and_trends(auth_user.id))
    assert "[KULLANICI PROFİLİ & HAFIZA]" in blob
    assert "injuries): KAYIT YOK" in blob
    assert "[HAFTALIK CHECK-IN TRENDİ" not in blob
    assert "[GÜNLÜK AKTİVİTE" not in blob


def test_fetch_coach_context_includes_profile_section(auth_user, monkeypatch):
    import fitx_mcp.server as mcp_server
    # MCP bölümlerini sabitle; asıl ilgi profil bloğunun bağlama girmesi.
    monkeypatch.setattr(mcp_server, "get_user_fitness_summary", lambda uid: "özet")
    monkeypatch.setattr(mcp_server, "get_user_workout_history", lambda uid, d: "antrenman")
    monkeypatch.setattr(mcp_server, "get_user_supplement_stack", lambda uid: "supp")
    monkeypatch.setattr(mcp_server, "get_user_nutrition_log", lambda uid, d: "log")
    monkeypatch.setattr(mcp_server, "get_friend_activities", lambda uid: "arkadaş")
    auth_user.user_metadata = {"dietary_restrictions": "vegan"}
    db.session.commit()

    context = ai_coach._fetch_coach_context(auth_user.id, "ne yiyeyim?")
    assert "[KULLANICI PROFİLİ & HAFIZA]" in context
    assert "vegan" in context


def test_cleanup_stale_pending_removes_aged_rows(auth_user):
    from datetime import datetime, timedelta
    old = PendingAction(user_id=auth_user.id, action_type="log_meal", payload={})
    db.session.add(old)
    db.session.commit()
    old.created_at = datetime.utcnow() - timedelta(hours=2)  # TTL (1 saat) aşıldı
    db.session.commit()

    ai_coach._cleanup_stale_pending(auth_user.id)
    assert PendingAction.query.filter_by(user_id=auth_user.id).count() == 0
