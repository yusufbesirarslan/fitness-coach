"""Bedrock "Tool Use" ekosistemi testleri (app/services/ai_coach.py).

Kapsam: kanonik araç kayıt defteri + sağlayıcı adaptörleri, üç yeni araç backend'i
(manage_user_memory, query_fitx_metrics, analyze_gym_photo) ve Bedrock araç-kullanım
döngüsü + OpenAI'ya fallback yönlendirmesi.

Ağ/Bedrock yok — bedrock_client/_bedrock_validate_image/s3_helper monkeypatch'li,
SQLite test DB'si (conftest) kullanılır.

    python -m pytest tests/test_coach_tools.py -v
"""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.services import ai_coach
from app.timeutil import app_today, day_key, utc_day_bounds


# ---------------------------------------------------------------------------
# Kanonik kayıt defteri + adaptörler
# ---------------------------------------------------------------------------

def test_registry_derives_both_provider_formats():
    names = [t["name"] for t in ai_coach._COACH_TOOL_DEFS]
    # Yeni 3 araç eklendi, eskiler korundu.
    assert {"manage_user_memory", "query_fitx_metrics", "analyze_gym_photo"} <= set(names)
    # OpenAI biçimi
    oa = [t["function"]["name"] for t in ai_coach.COACH_TOOLS]
    assert oa == names
    assert ai_coach.COACH_TOOLS[0]["type"] == "function"
    assert "parameters" in ai_coach.COACH_TOOLS[0]["function"]
    # Anthropic biçimi: input_schema == parameters (şema kayması yok)
    an = [t["name"] for t in ai_coach.COACH_TOOLS_ANTHROPIC]
    assert an == names
    assert ai_coach.COACH_TOOLS_ANTHROPIC[0]["input_schema"] == \
        ai_coach._COACH_TOOL_DEFS[0]["parameters"]
    assert "type" not in ai_coach.COACH_TOOLS_ANTHROPIC[0]  # Anthropic biçiminde 'type' yok


def test_anthropic_tools_cache_breakpoint_gated_by_flag(monkeypatch):
    monkeypatch.setattr(ai_coach, "BEDROCK_PROMPT_CACHE", False)
    assert all("cache_control" not in t for t in ai_coach._anthropic_tools_for_call())
    monkeypatch.setattr(ai_coach, "BEDROCK_PROMPT_CACHE", True)
    tools = ai_coach._anthropic_tools_for_call()
    assert "cache_control" in tools[-1]  # yalnızca SON araca breakpoint
    assert all("cache_control" not in t for t in tools[:-1])


def test_build_bedrock_system_keeps_context_outside_cached_block(monkeypatch):
    monkeypatch.setattr(ai_coach, "BEDROCK_PROMPT_CACHE", True)
    blocks = ai_coach._build_bedrock_system("KULLANICI BAĞLAMI")
    assert isinstance(blocks, list)
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    # Değişken bağlam önbelleğe-alınan bloğun İÇİNE gömülmemeli.
    assert "KULLANICI BAĞLAMI" not in blocks[0]["text"]
    assert "KULLANICI BAĞLAMI" in blocks[1]["text"]
    assert "cache_control" not in blocks[1]
    # Caching kapalıyken düz string.
    monkeypatch.setattr(ai_coach, "BEDROCK_PROMPT_CACHE", False)
    assert isinstance(ai_coach._build_bedrock_system("X"), str)


# ---------------------------------------------------------------------------
# manage_user_memory
# ---------------------------------------------------------------------------

def test_manage_user_memory_roundtrip_and_second_update(app, make_user):
    user = make_user("mem")
    # boş başlangıç
    assert json.loads(ai_coach._tool_manage_user_memory(user.id, "get", "injuries"))["value"] is None
    # update → persist
    ai_coach._tool_manage_user_memory(user.id, "update", "injuries", "Menisküs")
    assert json.loads(ai_coach._tool_manage_user_memory(user.id, "get", "injuries"))["value"] == "Menisküs"
    # İKİNCİ update başka bir anahtara da kalıcı olmalı (flag_modified bug yakalama)
    ai_coach._tool_manage_user_memory(user.id, "update", "fitness_goals", "kas kazanma")
    mem = json.loads(ai_coach._tool_manage_user_memory(user.id, "get", ""))["memory"]
    assert mem == {"injuries": "Menisküs", "fitness_goals": "kas kazanma"}


def test_manage_user_memory_rejects_unknown_key_and_action(app, make_user):
    user = make_user("mem2")
    assert json.loads(ai_coach._tool_manage_user_memory(user.id, "update", "evil", "x"))["status"] == "error"
    assert json.loads(ai_coach._tool_manage_user_memory(user.id, "frobnicate", "injuries", "x"))["status"] == "error"


def test_manage_user_memory_update_requires_value_and_does_not_wipe(app, make_user):
    """None/boş value ile 'update' KAYITLI veriyi silmemeli (kazara wipe koruması)."""
    user = make_user("mem3")
    ai_coach._tool_manage_user_memory(user.id, "update", "injuries", "Menisküs")
    # value yok → hata, mevcut değer korunur
    out = json.loads(ai_coach._tool_manage_user_memory(user.id, "update", "injuries", None))
    assert out["status"] == "error"
    assert json.loads(ai_coach._tool_manage_user_memory(user.id, "get", "injuries"))["value"] == "Menisküs"
    # boş string de reddedilir
    assert json.loads(ai_coach._tool_manage_user_memory(user.id, "update", "injuries", "   "))["status"] == "error"
    assert json.loads(ai_coach._tool_manage_user_memory(user.id, "get", "injuries"))["value"] == "Menisküs"


# ---------------------------------------------------------------------------
# query_fitx_metrics
# ---------------------------------------------------------------------------

def _seed_metrics(db, user_id):
    from app.models import MealLog, WaterLog, WeeklyLog, WorkoutLog
    today_iso = day_key()
    start, _ = utc_day_bounds()
    dt = start + timedelta(hours=2)  # bugünün Istanbul günü içinde (naive UTC)
    db.session.add(MealLog(user_id=user_id, ogun="öğle", yemekler="x",
                           kalori=600, protein=40, karb=50, yag=20, tarih=today_iso))
    db.session.add(MealLog(user_id=user_id, ogun="akşam", yemekler="y",
                           kalori=400, protein=30, karb=40, yag=10, tarih=today_iso))
    db.session.add(WaterLog(user_id=user_id, date_key=today_iso, count=5))
    db.session.add(WorkoutLog(user_id=user_id, exercise_name="Squat",
                              sets=5, reps=5, weight_kg=100, volume=2500, created_at=dt))
    db.session.add(WeeklyLog(user_id=user_id, weight=80.0, created_at=dt))
    db.session.commit()


def test_query_metrics_calories_and_water(app, make_user):
    from app.extensions import db
    user = make_user("metrics")
    _seed_metrics(db, user.id)
    cal = json.loads(ai_coach._tool_query_fitx_metrics(user.id, "calories", "today"))
    assert cal["status"] == "ok" and cal["value"] == 1000 and cal["unit"] == "kcal"
    water = json.loads(ai_coach._tool_query_fitx_metrics(user.id, "water_ml", "today"))
    assert water["value"] == 5 * 240 and water["unit"] == "ml"


def test_query_metrics_volume_and_weight_datetime_cols(app, make_user):
    from app.extensions import db
    user = make_user("metrics2")
    _seed_metrics(db, user.id)
    vol = json.loads(ai_coach._tool_query_fitx_metrics(user.id, "volume_lifted", "last_7_days"))
    assert vol["value"] == 2500 and vol["unit"] == "kg"
    w = json.loads(ai_coach._tool_query_fitx_metrics(user.id, "weight_kg", "current_month"))
    assert w["value"] == 80.0 and w["unit"] == "kg"


def test_query_metrics_iso_and_datetime_ranges_agree_on_today(app, make_user):
    """ISO-string (calories) ve datetime (volume) aralıkları AYNI mantıksal 'bugün'de
    aynı kaydı saymalı."""
    from app.extensions import db
    user = make_user("metrics3")
    _seed_metrics(db, user.id)
    cal = json.loads(ai_coach._tool_query_fitx_metrics(user.id, "calories", "today"))
    vol = json.loads(ai_coach._tool_query_fitx_metrics(user.id, "volume_lifted", "today"))
    assert cal["value"] == 1000 and vol["value"] == 2500  # ikisi de bugünü gördü


def test_query_metrics_sleep_unsupported_and_bad_enums(app, make_user):
    user = make_user("metrics4")
    assert json.loads(ai_coach._tool_query_fitx_metrics(user.id, "sleep_hours", "today"))["status"] == "unsupported"
    assert json.loads(ai_coach._tool_query_fitx_metrics(user.id, "bogus", "today"))["status"] == "error"
    assert json.loads(ai_coach._tool_query_fitx_metrics(user.id, "calories", "all_time"))["status"] == "error"


def test_query_metrics_scoped_to_user(app, make_user):
    from app.extensions import db
    a = make_user("owner_a")
    b = make_user("owner_b")
    _seed_metrics(db, a.id)  # yalnızca A'nın verisi
    assert json.loads(ai_coach._tool_query_fitx_metrics(b.id, "calories", "today"))["value"] == 0


# ---------------------------------------------------------------------------
# analyze_gym_photo — sahiplik (IDOR) + idempotent ödül
# ---------------------------------------------------------------------------

def test_analyze_gym_photo_rejects_foreign_key(app, make_user, monkeypatch):
    """Anahtar '/<user_id>/' içermiyorsa gerçek get_object_bytes sahiplik kontrolünde
    reddeder (boto3 gerekmez — kontrol istemci kurulmadan ÖNCE)."""
    import s3_helper
    user = make_user("photo")
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(s3_helper, "is_enabled", lambda: True)
    out = json.loads(ai_coach._tool_analyze_gym_photo(user.id, "pump-checks/99999/2026/06/x.jpg"))
    assert out["status"] == "error"


def test_analyze_gym_photo_awards_once_per_day(app, make_user, monkeypatch):
    import s3_helper
    from app.models import PumpCheck
    user = make_user("photo2")
    key = f"pump-checks/{user.id}/2026/06/abc.jpg"
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(s3_helper, "get_object_bytes", lambda k, expected_user_id=None: b"jpeg-bytes")
    monkeypatch.setattr(ai_coach, "_bedrock_validate_image",
                        lambda *a, **k: '{"is_gym": true, "form_rating": 8, "reason": "spor salonu"}')

    first = json.loads(ai_coach._tool_analyze_gym_photo(user.id, key))
    assert first["status"] == "committed" and first["awarded"] is True and first["form_rating"] == 8
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1
    # Aynı gün ikinci çağrı → tekrar ödül YOK
    second = json.loads(ai_coach._tool_analyze_gym_photo(user.id, key))
    assert second["status"] == "already_done" and second["awarded"] is False
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1


def test_analyze_gym_photo_not_gym_no_award(app, make_user, monkeypatch):
    import s3_helper
    from app.models import PumpCheck
    user = make_user("photo3")
    key = f"pump-checks/{user.id}/2026/06/def.jpg"
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(s3_helper, "get_object_bytes", lambda k, expected_user_id=None: b"jpeg-bytes")
    monkeypatch.setattr(ai_coach, "_bedrock_validate_image",
                        lambda *a, **k: '{"is_gym": false, "form_rating": 0, "reason": "mutfak"}')
    out = json.loads(ai_coach._tool_analyze_gym_photo(user.id, key))
    assert out["is_gym"] is False and out["awarded"] is False
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 0


# ---------------------------------------------------------------------------
# Bedrock araç-kullanım döngüsü + OpenAI fallback yönlendirmesi
# ---------------------------------------------------------------------------

def _text_resp(text):
    return SimpleNamespace(stop_reason="end_turn",
                           content=[SimpleNamespace(type="text", text=text)])


def _tool_use_resp(tool_id, name, tool_input):
    return SimpleNamespace(stop_reason="tool_use", content=[
        SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)])


def _fake_bedrock(monkeypatch, responses):
    """bedrock_client.messages.create'i sırayla `responses` döndürecek şekilde sahtele."""
    seq = list(responses)
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        r = seq.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(ai_coach, "bedrock_client",
                        SimpleNamespace(messages=SimpleNamespace(create=create)))
    return calls


def test_router_uses_openai_when_bedrock_disabled(app, monkeypatch):
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_bedrock",
                        lambda *a, **k: pytest.fail("Bedrock kapalıyken çağrılmamalı"))
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai", lambda *a, **k: "OPENAI")
    with app.app_context():
        assert ai_coach._run_coach_conversation(1, "merhaba", "", client_history=[]) == "OPENAI"


def test_router_uses_bedrock_when_enabled(app, monkeypatch):
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    _fake_bedrock(monkeypatch, [_text_resp("MERHABA")])
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai",
                        lambda *a, **k: pytest.fail("Bedrock başarılıyken OpenAI çağrılmamalı"))
    with app.app_context():
        assert ai_coach._run_coach_conversation(1, "selam", "", client_history=[]) == "MERHABA"


def test_bedrock_loop_chains_tool_then_text(app, make_user, monkeypatch):
    user = make_user("loop")
    ai_coach._tool_manage_user_memory(user.id, "update", "injuries", "Hiçbiri")
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    calls = _fake_bedrock(monkeypatch, [
        _tool_use_resp("t1", "manage_user_memory", {"action": "get", "key": "injuries"}),
        _text_resp("Sakatlığın yok, harika."),
    ])
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai",
                        lambda *a, **k: pytest.fail("Araç çalıştıktan sonra OpenAI'ya düşülmemeli"))
    with app.app_context():
        out = ai_coach._run_coach_conversation(user.id, "plan ver", "", client_history=[])
    assert out == "Sakatlığın yok, harika."
    assert calls["n"] == 2  # tool_use turu + final metin turu


def test_bedrock_first_call_error_falls_back_to_openai(app, monkeypatch, caplog):
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    _fake_bedrock(monkeypatch, [RuntimeError("bedrock down")])
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai", lambda *a, **k: "FALLBACK")
    with app.app_context(), caplog.at_level("WARNING"):
        assert ai_coach._run_coach_conversation(1, "x", "", client_history=[]) == "FALLBACK"
    assert any("OpenAI'ya düşülüyor" in r.getMessage() for r in caplog.records)


def test_bedrock_empty_response_falls_back_to_openai(app, monkeypatch):
    """Bedrock hiç araç çalışmadan boş (metin bloğu olmayan) bir yanıt verirse,
    yumuşak hata DÖNDÜRMEK yerine OpenAI'ya düşülmeli."""
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    _fake_bedrock(monkeypatch, [SimpleNamespace(stop_reason="end_turn", content=[])])
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai", lambda *a, **k: "OPENAI")
    with app.app_context():
        assert ai_coach._run_coach_conversation(1, "x", "", client_history=[]) == "OPENAI"


def test_bedrock_strips_leading_assistant_turn(app, monkeypatch):
    """convo Anthropic için 'user' ile başlamalı; baştaki assistant turu atılmalı."""
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return _text_resp("ok")
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(ai_coach, "bedrock_client",
                        SimpleNamespace(messages=SimpleNamespace(create=create)))
    history = [{"role": "assistant", "content": "Merhaba, ben FitX koçu!"}]
    with app.app_context():
        ai_coach._run_coach_conversation_bedrock(1, "soru", "", history)
    assert captured["messages"][0]["role"] == "user"


def test_bedrock_no_fallback_after_tool_ran(app, make_user, monkeypatch):
    """Bir araç YAN ETKİ ürettikten sonra hata → sağlayıcı değiştirilmez (aracı tekrar
    çalıştırma riski yok); yumuşak hata döner."""
    user = make_user("loop2")
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    _fake_bedrock(monkeypatch, [
        _tool_use_resp("t1", "query_fitx_metrics", {"metric_type": "calories", "date_range": "today"}),
        RuntimeError("bedrock down mid-loop"),
    ])
    monkeypatch.setattr(ai_coach, "_run_coach_conversation_openai",
                        lambda *a, **k: pytest.fail("Araç çalıştıktan sonra OpenAI'ya düşülmemeli"))
    with app.app_context():
        out = ai_coach._run_coach_conversation(user.id, "kaç kalori", "", client_history=[])
    assert "tamamlayamadım" in out
