"""Scorers for the local Haiku parity harness. No network, no credentials."""
import importlib.util
from pathlib import Path


def _metrics():
    path = Path(__file__).resolve().parents[1] / "scripts" / "haiku_light_parity_eval.py"
    spec = importlib.util.spec_from_file_location("haiku_light_parity_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_rejects_prose_and_accepts_a_short_term():
    metrics = _metrics()
    prose = metrics.score_normalize("lentil soup\nbecause it is a soup", ["lentil soup"])
    assert prose["parse_success"] is False
    assert prose["schema_success"] is False
    long = metrics.score_normalize("a" * 81, ["lentil soup"])
    assert long["parse_success"] is False
    chatty = metrics.score_normalize(
        "This food is best described as lentil soup for lookup", ["lentil soup"])
    assert chatty["parse_success"] is True
    assert chatty["exact_or_accepted"] is False
    assert chatty["extra_prose"] is True
    clean = metrics.score_normalize("lentil soup", ["lentil soup"])
    assert clean["parse_success"] is True
    assert clean["exact_or_accepted"] is True
    assert clean["extra_prose"] is False


def test_meal_schema_requires_numeric_keys():
    metrics = _metrics()
    ok = metrics.score_meal_totals('{"kalori": 330, "protein": 62, "karb": 0, "yag": 7.2}')
    assert ok["parse_success"] is True
    assert ok["schema_success"] is True
    missing = metrics.score_meal_totals('{"kalori": 330}')
    assert missing["parse_success"] is True
    assert missing["schema_success"] is False
    prose = metrics.score_meal_totals("The meal is about 330 kcal.")
    assert prose["parse_success"] is False


def test_menu_recall_and_hallucination():
    metrics = _metrics()
    expected = {"Çorbalar": ["Mercimek Çorbası"], "Izgara": ["Köfte"]}
    raw = '{"categories": {"Çorbalar": ["Mercimek Çorbası"], "Izgara": ["Köfte", "X-RAY PIZZA"]}}'
    scored = metrics.score_menu(raw, expected)
    assert scored["parse_success"] is True
    assert scored["item_recall"] == 1.0
    assert scored["hallucinated_count"] == 1
    assert "X-RAY PIZZA" in scored["hallucinated_items"]


def test_summary_flags_invented_facts_and_keeps_required_ones():
    metrics = _metrics()
    raw = "- Diz ağrısı var\n- Hedef kas kazanmak\n- Bench 80 kg\n- Maraton planı"
    scored = metrics.score_summary(
        raw, [["diz"], ["kas"], ["80"]], ["maraton"])
    assert scored["fact_recall"] == 1.0
    assert scored["invented_count"] == 1


def test_ocr_preserves_turkish_items_and_counts_extras():
    metrics = _metrics()
    text = "Çorbalar\nMercimek Çorbası\nIzgara\nTavuk Şiş\nX-RAY PIZZA"
    scored = metrics.score_ocr(
        text, ["Mercimek Çorbası", "Tavuk Şiş"], ["Çorbalar", "Izgara"])
    assert scored["item_recall"] == 1.0
    assert scored["turkish_character_preservation"] == 1.0
    assert scored["hallucinated_count"] >= 1


def test_coach_protocol_requires_a_valid_tool_and_a_final_answer():
    metrics = _metrics()
    good = metrics.score_coach([
        {"tool_name": "fetch_nutrition_and_stage_log",
         "tool_input": {"food_query": "200 gram ızgara tavuk"}, "text": ""},
        {"tool_name": None, "tool_input": None, "text": "Kaydedeyim mi?"},
    ])
    assert good["protocol_success"] is True
    assert good["schema_success"] is True
    bad = metrics.score_coach([
        {"tool_name": "not_a_tool", "tool_input": {}, "text": ""},
    ])
    assert bad["protocol_success"] is False
    assert bad["schema_success"] is False
