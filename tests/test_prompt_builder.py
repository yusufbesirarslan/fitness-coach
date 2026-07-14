"""İstem Kurucu + prompt katmanı testleri (app/services/prompt_builder.py,
app/prompts/). Sprint 4 WS3/WS4: mesaj montajı ve şablon çıktıları sabitlenir;
ai_coach sarmalayıcılarının parite testleri test_coach_tools.py'de sürüyor.

    python -m pytest tests/test_prompt_builder.py -v
"""
from app.prompts import goals, nutrition, progress, system
from app.services import memory_manager, prompt_builder


# ---------------------------------------------------------------------------
# prompts/system — build_coach_system
# ---------------------------------------------------------------------------

def test_build_coach_system_appends_lang_directive():
    tr = prompt_builder.build_coach_system("tr")
    en = prompt_builder.build_coach_system("en")
    assert tr.startswith(system.COACH_SYSTEM_PROMPT)
    assert en.startswith(system.COACH_SYSTEM_PROMPT)
    assert system.COACH_LANG_DIRECTIVE["tr"] in tr
    assert system.COACH_LANG_DIRECTIVE["en"] in en
    assert prompt_builder.build_coach_system("zz") == tr  # geçersiz dil → tr


# ---------------------------------------------------------------------------
# prompt_builder — bedrock system / araç cache / mesaj montajı
# ---------------------------------------------------------------------------

def test_build_bedrock_system_plain_when_cache_off():
    out = prompt_builder.build_bedrock_system("BAĞLAM", "tr", prompt_cache=False)
    assert isinstance(out, str)
    assert out.endswith("[KULLANICI VERİSİ]\nBAĞLAM")


def test_build_bedrock_system_blocks_when_cache_on():
    out = prompt_builder.build_bedrock_system("BAĞLAM", "tr", prompt_cache=True)
    assert isinstance(out, list)
    assert out[0]["cache_control"] == {"type": "ephemeral"}
    # DEĞİŞKEN kullanıcı verisi cache'lenen bloğun DIŞINDA (sessiz invalidasyon olmasın)
    assert "cache_control" not in out[1]
    assert out[1]["text"] == "[KULLANICI VERİSİ]\nBAĞLAM"


def test_build_bedrock_system_no_context():
    assert isinstance(prompt_builder.build_bedrock_system("", "tr", prompt_cache=False), str)
    blocks = prompt_builder.build_bedrock_system("", "tr", prompt_cache=True)
    assert len(blocks) == 1


def test_anthropic_tools_for_call_cache_breakpoint():
    tools = [{"name": "a", "input_schema": {}}, {"name": "b", "input_schema": {}}]
    plain = prompt_builder.anthropic_tools_for_call(tools, prompt_cache=False)
    assert plain is tools  # kopya yok, aynen döner
    cached = prompt_builder.anthropic_tools_for_call(tools, prompt_cache=True)
    assert "cache_control" in cached[-1]
    assert "cache_control" not in cached[0]
    assert "cache_control" not in tools[-1]  # temel liste kirletilmedi


def test_build_openai_messages_order():
    msgs = prompt_builder.build_openai_messages(
        "tr", "VERİ", [{"role": "user", "content": "önceki"}], "soru?")
    assert [m["role"] for m in msgs] == ["system", "system", "user", "user"]
    assert msgs[1]["content"] == "[KULLANICI VERİSİ]\nVERİ"
    assert msgs[-1] == {"role": "user", "content": "soru?"}
    # bağlam yoksa ikinci system mesajı eklenmez
    msgs = prompt_builder.build_openai_messages("tr", "", [], "soru?")
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_build_anthropic_messages_strips_leading_assistant():
    convo = prompt_builder.build_anthropic_messages(
        [{"role": "assistant", "content": "açılış"},
         {"role": "user", "content": "merhaba"},
         {"role": "assistant", "content": "selam"}], "soru?")
    assert convo[0]["role"] == "user"  # Anthropic ilk mesajın user olmasını ister
    assert convo[-1] == {"role": "user", "content": "soru?"}


def test_build_anthropic_messages_merges_trailing_user_turn():
    # B15: geçmiş CEVAPSIZ bir user mesajıyla bitiyorsa (yarıda kesilen stream)
    # güncel soru ikinci bir user turu yaratırdı → Bedrock 400 → sessizce
    # gpt-4o-mini'ye düşerdik. Ardışık aynı-rol turlar birleştirilir.
    convo = prompt_builder.build_anthropic_messages(
        [{"role": "user", "content": "cevapsız kalan soru"}], "yeni soru?")

    assert [m["role"] for m in convo] == ["user"]
    assert convo[0]["content"] == "cevapsız kalan soru\nyeni soru?"
    # Değişmez: hiçbir iki ardışık tur aynı rolde olamaz.
    roles = [m["role"] for m in prompt_builder.build_anthropic_messages(
        [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
         {"role": "assistant", "content": "c"}], "d")]
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1))


# ---------------------------------------------------------------------------
# prompts/goals + prompts/progress — hedef çerçevelemesi şablon katmanında
# ---------------------------------------------------------------------------

def test_goals_prompt_frames_by_goal():
    prompt, sys_tr = goals.build_plan_reply_prompt(
        "y", 30, "male", 82, 180, "kas kazanma", "beginner", "active",
        1780, 2759, 3059, "", previous_weight=80, days_passed=14, language="tr")
    assert "BAŞARI" in prompt          # kas kazanmada +2 kg olumlu
    assert "2 hafta" in prompt
    assert "Türkçe" in sys_tr

    prompt, sys_en = goals.build_plan_reply_prompt(
        "y", 30, "male", 82, 180, "kilo verme", "beginner", "active",
        1780, 2759, 2359, "", previous_weight=80, days_passed=7, language="en")
    assert "istenmeyen" in prompt      # kilo vermede +2 kg istenmeyen
    assert "English" in sys_en


def test_progress_prompt_frames_by_goal():
    prompt, _ = progress.build_checkin_feedback_prompt(
        "y", 78, 80, 7, "kilo verme", 4, 2, "evet", 4, 4, "", language="tr")
    assert "2 kg verdi — kilo verme hedefinde başarılı" in prompt
    prompt, _ = progress.build_checkin_feedback_prompt(
        "y", 82, 80, 7, "kas kazanma", 3, 3, "kismen", 3, 3, "", language="tr")
    assert "kg aldı — kas kazanma hedefinde olumlu" in prompt


# ---------------------------------------------------------------------------
# prompts/nutrition — kritik değişmezler
# ---------------------------------------------------------------------------

def test_nutrition_systems_carry_portion_sanity_rule():
    for sys_prompt in (nutrition.FOOD_SEARCH_SYSTEM, nutrition.MENU_EXTRACT_SYSTEM,
                       nutrition.SERVING_WEIGHTS_SYSTEM, nutrition.MACRO_BATCH_SYSTEM,
                       nutrition.MEAL_TOTALS_SYSTEM):
        assert sys_prompt.endswith(nutrition.PORTION_SANITY_RULE)


def test_menu_extract_prompt_fences_data():
    prompt = nutrition.build_menu_extract_prompt("MENÜ METNİ", "", "", 8, 80)
    assert "<<<MENU_DATA\nMENÜ METNİ\nMENU_DATA>>>" in prompt
    assert "kategori başına en fazla 8 yemek, toplam en fazla 80 yemek" in prompt


def test_macro_batch_prompt_grams_rule_toggle():
    with_hint = nutrition.build_macro_batch_prompt(["Ali Nazik"], "- Ali Nazik", True)
    without = nutrition.build_macro_batch_prompt(["Ali Nazik"], "- Ali Nazik", False)
    assert "porsiyon ≈NNN g" in with_hint
    assert "porsiyon ≈NNN g" not in without


# ---------------------------------------------------------------------------
# memory_manager — token tahmini (WS1 hazırlığı)
# ---------------------------------------------------------------------------

def test_estimate_tokens_heuristic():
    assert memory_manager.estimate_tokens("") == 0
    assert memory_manager.estimate_tokens(None) == 0
    assert memory_manager.estimate_tokens("x" * 400) == 100  # len // 4
