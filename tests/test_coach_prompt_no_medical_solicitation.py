"""Coach system prompt must not solicit injury/medical history (finding
BACKEND_COACH_INJURY_PROMPT_FINDING_20261113, app/prompts/system.py:21-25 on
dbac1fda). Safety redirection and professional referral stay."""
import pytest

from app.prompts import system

PROMPTS = {"legacy": system.COACH_SYSTEM_PROMPT, "adaptive": system.ADAPTIVE_COACH_SYSTEM_PROMPT}

# Exact solicitation phrases removed from dbac1fda.
SOLICITATION = (
    "Menisküs, Kifoz, Skolyoz gibi bir durumun var mı",
    "kullanıcıya sakatlık/tıbbi durumunu sor",
    "plan üretimini DURDUR",
    "Cevabı almadan egzersiz önerme",
    "Kullanıcı cevap verince `manage_user_memory`",
    "Yoksa 'Hiçbiri' yaz",
)


@pytest.mark.parametrize("variant", sorted(PROMPTS))
def test_no_proactive_medical_solicitation(variant):
    prompt = PROMPTS[variant]
    for phrase in SOLICITATION:
        assert phrase not in prompt, (variant, phrase)
    assert "Sakatlık, hastalık, teşhis veya tıbbi geçmiş SORMA" in prompt
    assert "ayrıntı, teşhis veya geçmiş SORMA" in prompt
    assert "kullanıcıdan doğrulama veya ayrıntı isteme" in prompt


@pytest.mark.parametrize("variant", sorted(PROMPTS))
def test_safety_redirection_and_referral_kept(variant):
    prompt = PROMPTS[variant]
    assert "uygun bir sağlık uzmanına danışmasını öner" in prompt
    assert "hemen tıbbi yardım almasını söyle" in prompt
    assert "Tıbbi teşhis veya reçete VERME" in prompt
    assert "banned_movements listesindeki hareketleri ASLA önerme" in prompt


def test_adaptive_variant_still_substitutes_its_instructions():
    # The adaptive prompt is built by exact str.replace; an edit that broke the
    # anchor would silently keep the legacy text.
    assert system._LEGACY_INJURY_INSTRUCTION in system.COACH_SYSTEM_PROMPT
    assert system._LEGACY_CHECKIN_INSTRUCTION in system.COACH_SYSTEM_PROMPT
    assert system._LEGACY_INJURY_INSTRUCTION not in system.ADAPTIVE_COACH_SYSTEM_PROMPT
    assert system._ADAPTIVE_INJURY_INSTRUCTION in system.ADAPTIVE_COACH_SYSTEM_PROMPT


@pytest.mark.parametrize("language", ["tr", "en"])
def test_built_coach_system_prompt_carries_the_fix(language):
    from app.services import prompt_builder
    for adaptive in (False, True):
        built = prompt_builder.build_coach_system(language, adaptive_plan_context=adaptive)
        assert "Menisküs, Kifoz, Skolyoz gibi bir durumun var mı" not in built
        assert "Sakatlık, hastalık, teşhis veya tıbbi geçmiş SORMA" in built


def test_memory_tool_description_does_not_solicit_medical_history():
    from app.services import ai_coach
    tool, = [t for t in ai_coach._COACH_TOOL_DEFS if t["name"] == "manage_user_memory"]
    description = tool["description"]
    assert "yoksa kullanıcıya sor" not in description
    assert "tıbbi durumlar" not in description
    assert "sakatlık veya tıbbi durum SORMA" in description


def test_prompt_does_not_offer_to_save_through_an_unoffered_tool():
    # manage_user_memory is never offered to the model (unqualified D1 tool).
    from app.services import ai_coach
    assert "manage_user_memory" in ai_coach._UNQUALIFIED_D1_TOOLS
    assert 'action="update", key="injuries"' not in system.COACH_SYSTEM_PROMPT
