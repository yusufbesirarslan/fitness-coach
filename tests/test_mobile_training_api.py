"""Native Training read-contract tests.

These endpoints are projections over existing canonical authorities. The
fixtures use real persistence and literal expected payloads so the tests catch
contract drift rather than mirroring the implementation.
"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import TrainingPlan, User
from app.services import mobile_auth
from app.timeutil import APP_TZ, audit_clock


PREFERENCES_PATH = "/api/v1/training/preferences"
CURRENT_PLAN_PATH = "/api/v1/training/plans/current"
FIXED_NOW = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
WEEKDAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]


@pytest.fixture
def mobile_user(make_user):
    return make_user("training-mobile")


@pytest.fixture
def other_user(make_user):
    return make_user("training-other")


@pytest.fixture
def as_mobile(monkeypatch):
    def _headers(user):
        monkeypatch.setattr(
            mobile_auth,
            "authenticate_access",
            lambda raw: mobile_auth.MobilePrincipal(
                user, SimpleNamespace(id=1), {"sub": user.cognito_sub}),
        )
        return {"Authorization": "Bearer opaque-access-credential"}

    return _headers


def _exercise(rest="90 sn", *, exercise_id="ex_barbell_back_squat", name="Squat"):
    return {
        "exercise_id": exercise_id,
        "isim": name,
        "set": 3,
        "tekrar": "8-10",
        "dinlenme": rest,
        "not": "Controlled tempo",
    }


def _day(weekday, kind="dinlenme", *, rest="90 sn"):
    is_rest = kind == "dinlenme"
    return {
        "gun": weekday,
        "tip": kind,
        "odak": "Recovery" if is_rest else "Full body",
        "sure_dk": 0 if is_rest else 45,
        "tahmini_kalori": 0 if is_rest else 320,
        "egzersizler": [] if is_rest else [_exercise(rest)],
    }


def _plan_document(*, thursday_rest="90 sn"):
    days = [_day(name) for name in WEEKDAYS]
    days[2] = _day("Çarşamba", "kardiyo", rest="2 dk")
    days[3] = _day("Perşembe", "antrenman", rest=thursday_rest)
    return {"program": days}


def _save_plan(user, *, raw=None, thursday_rest="90 sn", **columns):
    values = {
        "user_id": user.id,
        "plan_data": raw if raw is not None else json.dumps(
            _plan_document(thursday_rest=thursday_rest), ensure_ascii=False
        ),
        "score": 8.5,
        "created_at": datetime(2026, 7, 1, 8, 30),
        "lineage_id": "training-lineage-a",
        "mutation_version": 4,
    }
    values.update(columns)
    row = TrainingPlan(**values)
    db.session.add(row)
    db.session.commit()
    return row


def _get_current(client, headers):
    with audit_clock(FIXED_NOW):
        return client.get(CURRENT_PLAN_PATH, headers=headers)


def test_training_reads_require_bearer_and_never_redirect(raw_client):
    for path in (
        PREFERENCES_PATH,
        CURRENT_PLAN_PATH,
        "/api/v1/training/workouts/AAAAAAAAAAAAAAAAAAAAAAAA",
    ):
        response = raw_client.get(path)

        assert response.status_code == 401
        assert response.is_json
        assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"
        assert set(response.json["error"]) == {
            "code", "message", "retryable", "request_id"
        }
        assert "Location" not in response.headers
        assert response.headers["Cache-Control"] == "no-store"


def test_browser_cookie_auth_cannot_authorize_training_reads(client, mobile_user):
    with client.session_transaction() as session:
        session["_user_id"] = str(mobile_user.id)
        session["_fresh"] = True

    response = client.get(CURRENT_PLAN_PATH)

    assert response.status_code == 401
    assert response.json["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_preferences_publish_the_exact_canonical_rendering_contract(
    client, mobile_user, as_mobile
):
    response = client.get(PREFERENCES_PATH, headers=as_mobile(mobile_user))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json == {
        "contract_version": 1,
        "fields": {
            "gun_sayisi": {"type": "integer", "default": 3, "choices": [3, 4, 5, 6]},
            "ekipman": {
                "type": "token",
                "default": "spor_salonu",
                "choices": ["ev", "minimal", "spor_salonu"],
            },
            "odak": {
                "type": "token",
                "default": "tum_vucut",
                "choices": ["alt_vucut", "core", "sirt", "tum_vucut", "ust_vucut"],
            },
            "sure": {"type": "integer", "default": 45, "choices": [30, 45, 60, 90]},
            "kardiyo_tipi": {
                "type": "token",
                "default": "yok",
                "choices": [
                    "bisiklet", "ip_atlama", "karisik", "kosu", "yok", "yuruyus", "yuzme"
                ],
            },
            "kardiyo_gun": {
                "type": "integer",
                "default": 0,
                "choices": [0, 1, 2, 3, 4, 5, 6],
            },
            "kardiyo_sure": {
                "type": "integer",
                "default": 20,
                "choices": [15, 20, 30, 45],
            },
            "kardiyo_yogunluk": {
                "type": "token",
                "default": "orta",
                "choices": ["dusuk", "karisik", "orta", "yuksek"],
            },
            "antrenman_tarzi": {
                "type": "token",
                "default": "genel",
                "choices": [
                    "bodybuilding",
                    "calisthenics",
                    "crossfit",
                    "fonksiyonel",
                    "genel",
                    "powerlifting",
                ],
            },
            "odak_hedef": {
                "type": "token",
                "default": "genel",
                "choices": ["esneklik", "genel", "guc", "kas_kutlesi", "kondisyon", "yag_yakimi"],
            },
            "injuries": {"type": "string", "default": ""},
        },
        "capability_constraints": [
            {
                "status": "unsupported",
                "reason": "CROSSFIT_SCHEMA_UNSUPPORTED",
                "when": {"antrenman_tarzi": ["crossfit"]},
            },
            {
                "status": "unsupported",
                "reason": "POWERLIFTING_REQUIRES_GYM_EQUIPMENT",
                "when": {
                    "antrenman_tarzi": ["powerlifting"],
                    "ekipman": ["ev", "minimal"],
                },
            },
            {
                "status": "conflicting",
                "reason": "CARDIO_DAYS_WITHOUT_TYPE",
                "when": {"kardiyo_tipi": ["yok"], "kardiyo_gun": [1, 2, 3, 4, 5, 6]},
            },
            {
                "status": "conflicting",
                "reason": "WEEK_ALLOCATION_EXCEEDS_SEVEN_DAYS",
                "when": {"rule": "gun_sayisi + effective_kardiyo_gun > 7"},
            },
        ],
    }


def test_no_plan_is_exactly_a_successful_null_product_state(
    client, mobile_user, as_mobile
):
    response = _get_current(client, as_mobile(mobile_user))

    assert response.status_code == 200
    assert response.json == {"plan": None}
    assert response.headers["Cache-Control"] == "no-store"


def test_current_plan_never_falls_back_to_another_users_plan(
    client, mobile_user, other_user, as_mobile
):
    _save_plan(other_user, lineage_id="other-users-lineage")

    response = _get_current(client, as_mobile(mobile_user))

    assert response.status_code == 200
    assert response.json == {"plan": None}


def test_current_plan_projects_bounded_native_days_and_canonical_exercises(
    client, mobile_user, as_mobile
):
    _save_plan(mobile_user)

    response = _get_current(client, as_mobile(mobile_user))

    assert response.status_code == 200
    plan = response.json["plan"]
    assert set(plan) == {
        "plan_lineage",
        "mutation_version",
        "created_at",
        "score",
        "current_workout_ref",
        "days",
    }
    assert plan["plan_lineage"] == "training-lineage-a"
    assert plan["mutation_version"] == 4
    assert plan["created_at"] == "2026-07-01T08:30:00Z"
    assert plan["score"] == 8.5
    assert isinstance(plan["current_workout_ref"], str)
    assert len(plan["current_workout_ref"]) == 24
    assert len(plan["days"]) == 7

    rest_day = plan["days"][0]
    assert rest_day == {
        "slot": 0,
        "weekday": "Pazartesi",
        "kind": "rest",
        "focus": "Recovery",
        "duration_minutes": 0,
        "estimated_calories": 0,
        "workout_ref": None,
        "exercises": [],
    }

    cardio_day = plan["days"][2]
    assert cardio_day["kind"] == "cardio"
    assert cardio_day["exercises"][0] == {
        "exercise_id": "ex_barbell_back_squat",
        "display_name": "Barbell Back Squat",
        "sets": 3,
        "reps": "8-10",
        "rest": {"display_text": "2 dk", "seconds": 120},
        "notes": "Controlled tempo",
    }

    current_day = plan["days"][3]
    assert current_day["kind"] == "training"
    assert current_day["workout_ref"] == plan["current_workout_ref"]
    assert current_day["exercises"][0]["rest"] == {
        "display_text": "90 sn",
        "seconds": 90,
    }


def test_nullable_plan_score_remains_null(client, mobile_user, as_mobile):
    _save_plan(mobile_user, score=None)

    response = _get_current(client, as_mobile(mobile_user))

    assert response.status_code == 200
    assert response.json["plan"]["score"] is None


@pytest.mark.parametrize("score", [-1, 11, float("inf")])
def test_out_of_contract_plan_score_is_unprojectable(
    client, mobile_user, as_mobile, score
):
    _save_plan(mobile_user, score=score)

    response = _get_current(client, as_mobile(mobile_user))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_UNPROJECTABLE"


@pytest.mark.parametrize("display", ["about 90 seconds", "1.5 dk", "-"])
def test_unstructured_rest_is_preserved_without_guessing_seconds(
    client, mobile_user, as_mobile, display
):
    _save_plan(mobile_user, thursday_rest=display)

    response = _get_current(client, as_mobile(mobile_user))

    assert response.status_code == 200
    assert response.json["plan"]["days"][3]["exercises"][0]["rest"] == {
        "display_text": display,
        "seconds": None,
    }


@pytest.mark.parametrize("raw", ["not-json", json.dumps({"program": []})])
def test_existing_unprojectable_plan_is_a_typed_conflict_not_no_plan(
    client, mobile_user, as_mobile, raw
):
    _save_plan(mobile_user, raw=raw)

    response = _get_current(client, as_mobile(mobile_user))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_UNPROJECTABLE"
    assert response.json["error"]["retryable"] is False
    assert set(response.json["error"]) == {
        "code", "message", "retryable", "request_id"
    }


@pytest.mark.parametrize("breakage", ["missing_id", "unknown_id", "too_many"])
def test_noncanonical_or_unbounded_exercises_make_the_existing_plan_unprojectable(
    client, mobile_user, as_mobile, breakage
):
    document = _plan_document()
    exercise = document["program"][3]["egzersizler"][0]
    if breakage == "missing_id":
        exercise.pop("exercise_id")
    elif breakage == "unknown_id":
        exercise["exercise_id"] = "ex_not_in_catalog"
    else:
        document["program"][3]["egzersizler"] = [exercise.copy() for _ in range(33)]
    _save_plan(mobile_user, raw=json.dumps(document, ensure_ascii=False))

    response = _get_current(client, as_mobile(mobile_user))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_PLAN_UNPROJECTABLE"


def test_workout_detail_is_exactly_linked_to_the_current_owned_plan(
    client, mobile_user, as_mobile
):
    _save_plan(mobile_user)
    headers = as_mobile(mobile_user)
    current = _get_current(client, headers).json["plan"]
    reference = current["days"][3]["workout_ref"]

    response = client.get(
        f"/api/v1/training/workouts/{reference}", headers=headers
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json == {
        "workout": {
            "plan_lineage": "training-lineage-a",
            "mutation_version": 4,
            "workout_ref": reference,
            "slot": 3,
            "weekday": "Perşembe",
            "kind": "training",
            "focus": "Full body",
            "duration_minutes": 45,
            "estimated_calories": 320,
            "exercises": [
                {
                    "exercise_id": "ex_barbell_back_squat",
                    "display_name": "Barbell Back Squat",
                    "sets": 3,
                    "reps": "8-10",
                    "rest": {"display_text": "90 sn", "seconds": 90},
                    "notes": "Controlled tempo",
                }
            ],
        }
    }


def test_workout_references_are_stable_and_rest_days_never_publish_one(
    client, mobile_user, as_mobile
):
    _save_plan(mobile_user)
    headers = as_mobile(mobile_user)

    first = _get_current(client, headers).json["plan"]
    second = _get_current(client, headers).json["plan"]

    assert [day["workout_ref"] for day in first["days"]] == [
        None,
        None,
        first["days"][2]["workout_ref"],
        first["days"][3]["workout_ref"],
        None,
        None,
        None,
    ]
    assert [day["workout_ref"] for day in second["days"]] == [
        day["workout_ref"] for day in first["days"]
    ]


def test_another_users_reference_cannot_address_the_callers_plan(
    client, mobile_user, other_user, as_mobile
):
    _save_plan(mobile_user)
    _save_plan(
        other_user,
        lineage_id="training-lineage-b",
        created_at=datetime(2026, 7, 2, 8, 30),
    )
    other_user_id = other_user.id
    owner_headers = as_mobile(mobile_user)
    reference = _get_current(client, owner_headers).json["plan"]["days"][3][
        "workout_ref"
    ]
    other_user = db.session.get(User, other_user_id)

    response = client.get(
        f"/api/v1/training/workouts/{reference}", headers=as_mobile(other_user)
    )

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_WORKOUT_STALE"
    assert response.json["error"]["retryable"] is False


@pytest.mark.parametrize("token", ["not-valid!", "a" * 4096])
def test_malformed_or_oversized_workout_reference_is_private_not_found(
    client, mobile_user, as_mobile, token
):
    _save_plan(mobile_user)

    response = client.get(
        f"/api/v1/training/workouts/{token}", headers=as_mobile(mobile_user)
    )

    assert response.status_code == 404
    assert response.json["error"]["code"] == "TRAINING_WORKOUT_NOT_FOUND"
    assert response.json["error"]["retryable"] is False


def test_tampered_and_random_valid_shape_references_are_stale_without_oracle(
    client, mobile_user, as_mobile
):
    _save_plan(mobile_user)
    headers = as_mobile(mobile_user)
    reference = _get_current(client, headers).json["plan"]["days"][3]["workout_ref"]
    tampered = reference[:-1] + ("A" if reference[-1] != "A" else "B")

    for token in (tampered, "A" * 24):
        response = client.get(
            f"/api/v1/training/workouts/{token}", headers=headers
        )

        assert response.status_code == 409
        assert response.json["error"]["code"] == "TRAINING_WORKOUT_STALE"
        assert response.json["error"]["retryable"] is False


@pytest.mark.parametrize("change", ["version", "replacement", "rest"])
def test_reference_is_stale_after_revision_replacement_or_rest_reclassification(
    client, mobile_user, as_mobile, change
):
    row = _save_plan(mobile_user)
    row_id = row.id
    user_id = mobile_user.id
    headers = as_mobile(mobile_user)
    current = _get_current(client, headers).json["plan"]
    slot = 2 if change == "rest" else 3
    reference = current["days"][slot]["workout_ref"]
    row = db.session.get(TrainingPlan, row_id)

    if change == "version":
        row.mutation_version = 5
    elif change == "replacement":
        _save_plan(
            db.session.get(User, user_id),
            lineage_id="training-lineage-replacement",
            mutation_version=0,
            created_at=datetime(2026, 7, 3, 8, 30),
        )
    else:
        document = json.loads(row.plan_data)
        document["program"][2] = _day("Çarşamba")
        row.plan_data = json.dumps(document, ensure_ascii=False)
    db.session.commit()

    response = client.get(
        f"/api/v1/training/workouts/{reference}", headers=headers
    )

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_WORKOUT_STALE"
    assert response.json["error"]["retryable"] is False
