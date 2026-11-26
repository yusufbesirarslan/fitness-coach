"""Evidence semantics and strict provider output qualification."""
from datetime import datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from app.services.weekly_review import contract, evidence


def _row(weight=80, at=datetime(2026, 11, 24, 21), rating=None, answer=None, fatigue=3):
    return SimpleNamespace(weight=weight, created_at=at, yogunluk=4, fatigue=fatigue,
                           uyku_kalitesi=2, progressive_overload="kismen",
                           beslenme_uyumu=rating, nutrition_adherence_v2=answer)


@pytest.mark.parametrize(("prior", "latest", "expected"), [
    (_row(rating=2), _row(rating=4), {"scale": "numeric_1_5", "before": 2, "after": 4}),
    (_row(answer="partly"), _row(answer="yes"), {"scale": "ternary_v2", "before": "partly", "after": "yes"}),
    (_row(rating=3), _row(answer="yes"), None),
    (_row(answer="no"), _row(answer="not_applicable"), None),
    (_row(answer="not_applicable"), _row(answer="no"), None),
    (_row(rating=9), _row(rating=2), None),
])
def test_nutrition_comparison_does_not_coerce_scales(monkeypatch, prior, latest, expected):
    monkeypatch.setattr(evidence.queries, "previous_full_checkin", lambda *_: prior)
    monkeypatch.setattr(evidence, "prior_weight_day_row", lambda *_: None)
    snapshot = evidence.load(1, 10, latest)
    assert snapshot["changes"].get("nutrition_adherence") == expected
    assert snapshot["recorded"]["progressive_overload"] == "partial"
    assert "weight_kg" not in snapshot["changes"]
    assert snapshot["analysis_day"] == "2026-11-25"


def test_non_weight_changes_use_same_day_but_weight_uses_earlier_istanbul_day(monkeypatch):
    current = _row(weight=80)
    previous = _row(weight=82, at=current.created_at - timedelta(seconds=0), fatigue=2)
    earlier = _row(weight=81.1, at=current.created_at - timedelta(seconds=1))
    monkeypatch.setattr(evidence.queries, "previous_full_checkin", lambda *_: previous)

    def earlier_weight(owner, day):
        assert owner == 1
        assert day.isoformat() == "2026-11-25"
        return earlier

    monkeypatch.setattr(evidence, "prior_weight_day_row", earlier_weight)
    snapshot = evidence.load(1, 10, current)
    assert snapshot["changes"] == {"weight_kg": {"delta": -1.1},
                                   "fatigue": {"before": 2, "after": 3}}


_EVIDENCE = {
    "recorded": {"fatigue": 4, "sleep_quality": 2,
                 "nutrition_adherence": {"scale": "ternary_v2", "value": "not_applicable"}},
    "changes": {"fatigue": {"before": 2, "after": 4}},
}
_SELECTION = {"language": "en", "summary": ["sleep_quality"], "changes": ["fatigue"],
              "next_week_actions": ["bedtime", "recovery"]}


def test_valid_selection_renders_only_recorded_facts_and_safe_actions():
    result = contract.validate(json.dumps(_SELECTION), "en", _EVIDENCE)
    assert result["summary"] == "Sleep quality: 2/5."
    assert result["changes"] == ["Fatigue: 2/5 → 4/5"]
    assert len(result["next_week_actions"]) == 2
    in_turkish = contract.validate(json.dumps(dict(_SELECTION, language="tr")), "tr", _EVIDENCE)
    assert in_turkish["summary"] == "Uyku kalitesi: 2/5."
    assert in_turkish["changes"] == ["Yorgunluk: 2/5 → 4/5"]


@pytest.mark.parametrize("override", [
    {"changes": ["weight_kg"]},
    {"changes": ["fatigue", "fatigue"]},
    {"changes": [{"metric": "fatigue", "observation": "Fabricated comparison"}]},
    {"summary": "Sleep improved because you restricted food."},
    {"summary": ["progressive_overload"]},
    {"next_week_actions": ["nutrition_routine"]},
    {"next_week_actions": []},
    {"next_week_actions": ["Prescribe an unsafe restriction"]},
    {"extra": True},
    {"language": "tr"},
])
def test_out_of_contract_content_cannot_become_a_completed_review(override):
    with pytest.raises(contract.InvalidReview):
        contract.validate(json.dumps(dict(_SELECTION, **override)), "en", _EVIDENCE)


def test_first_checkin_cannot_render_a_change():
    first = dict(_EVIDENCE, changes={})
    assert not contract.validate(json.dumps(dict(_SELECTION, changes=[])), "en", first)["changes"]
    with pytest.raises(contract.InvalidReview):
        contract.validate(json.dumps(_SELECTION), "en", first)
