"""Typed reuse boundary over the canonical Training generator."""
import json

from app.extensions import db
from app.models import User, UserSession
from app.services.training_generation.models import TrainingPreferences
from app.services.training_generation.plan_schema import WEEKDAYS
from app.services.training_generation.service import (
    GeneratedTrainingPlanCandidate,
    generate_training_plan_candidate,
    generate_training_plan_payload,
)


def _exercise(name="Goblet Squat"):
    return {
        "isim": name,
        "set": 3,
        "tekrar": "8-12",
        "dinlenme": "90 sn",
        "not": "Controlled tempo",
    }


def _provider_document():
    program = []
    for index, weekday in enumerate(WEEKDAYS):
        training = index < 3
        program.append({
            "gun": weekday,
            "tip": "antrenman" if training else "dinlenme",
            "odak": "Full Body" if training else "Recovery",
            "sure_dk": 45 if training else 0,
            "tahmini_kalori": 300 if training else 0,
            "egzersizler": (
                [_exercise(), _exercise("Row"), _exercise("Push-up")]
                if training else []
            ),
        })
    return {
        "program": program,
        "haftalik_ozet": {
            "yogunluk_skoru": 7,
            "denge_skoru": 8,
            "uygunluk_skoru": 9,
        },
    }


def _session(user):
    row = UserSession(
        user_id=user.id,
        goal="kas kazanma",
        fitness_level="intermediate",
        current_activity="active",
        tdee=2600,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _preferences(**overrides):
    values = {
        "gun_sayisi": 3,
        "ekipman": "spor_salonu",
        "odak": "tum_vucut",
        "sure": 45,
        "kardiyo_tipi": "yok",
        "kardiyo_gun": 0,
        "kardiyo_sure": 20,
        "kardiyo_yogunluk": "orta",
        "antrenman_tarzi": "genel",
        "odak_hedef": "genel",
        "injuries": "",
    }
    values.update(overrides)
    return TrainingPreferences(**values)


class _Provider:
    def __init__(self):
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        return json.dumps(_provider_document(), ensure_ascii=False)


def test_typed_candidate_runs_the_canonical_pipeline_once(make_user):
    user = make_user("candidate-once")
    session = _session(user)
    provider = _Provider()

    candidate = generate_training_plan_candidate(
        user, session, _preferences(), chat_fn=provider)

    assert isinstance(candidate, GeneratedTrainingPlanCandidate)
    assert provider.calls == 1
    assert candidate.overall_score == 8.0
    assert candidate.exercise_context.equipment_context == "spor_salonu"
    assert set(candidate.document) == {
        "program", "haftalik_ozet", "exercise_context"
    }
    exercise = candidate.document["program"][0]["egzersizler"][0]
    assert exercise["exercise_id"] == "ex_goblet_squat"
    assert exercise["isim"] == "Goblet Squat"
    assert candidate.document["exercise_context"] == {
        "equipment_context": "spor_salonu",
        "cardio_type": "yok",
        "style": "genel",
        "catalog_version": 1,
    }


def test_typed_candidate_does_not_persist_native_injury_input(make_user):
    user = make_user("candidate-no-injury-write")
    session = _session(user)
    before = dict(user.user_metadata or {})

    generate_training_plan_candidate(
        user,
        session,
        _preferences(injuries="knee pain"),
        chat_fn=_Provider(),
    )

    db.session.expire_all()
    assert (db.session.get(User, user.id).user_metadata or {}) == before


def test_browser_payload_wrapper_keeps_its_existing_contract(make_user):
    user = make_user("candidate-browser-wrapper")
    session = _session(user)
    provider = _Provider()

    payload = generate_training_plan_payload(
        user,
        session,
        {
            "gun_sayisi": 3,
            "ekipman": "spor_salonu",
            "odak": "tum_vucut",
            "sure": 45,
            "kardiyo_tipi": "yok",
            "kardiyo_gun": 0,
            "kardiyo_sure": 20,
            "kardiyo_yogunluk": "orta",
            "antrenman_tarzi": "genel",
            "odak_hedef": "genel",
            "injuries": "",
        },
        chat_fn=provider,
        context_token_factory=lambda context: "signed-browser-context",
    )

    assert provider.calls == 1
    assert set(payload) == {
        "program",
        "haftalik_ozet",
        "overall_score",
        "score_label",
        "injury_warnings",
        "classification",
        "risk_flags",
        "constraints_applied",
        "exercise_context_token",
    }
    assert payload["exercise_context_token"] == "signed-browser-context"
    assert payload["classification"] == {
        "level": payload["classification"]["level"],
        "confidence": payload["classification"]["confidence"],
        "score": payload["classification"]["score"],
    }
    assert "exercise_context" not in payload
