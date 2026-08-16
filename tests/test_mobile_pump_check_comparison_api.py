"""Owner-only HTTP contract for canonical Pump Check comparisons."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import PumpCheck, PumpCheckComparison
from app.services import ai_gate, mobile_auth
from app.services.mobile_pump_check_comparisons import service
from app.services.mobile_pump_check_comparisons.analysis import (
    ANALYSIS_VERSION,
    InvalidComparisonAnalysis,
)
from app.services.mobile_pump_checks.analysis import (
    ANALYSIS_VERSION as SOURCE_ANALYSIS_VERSION,
)
import s3_helper


PATH = "/api/v1/pump-check-comparisons"
BASELINE_TOKEN = "A" * 24
CURRENT_TOKEN = "B" * 24


def _source_analysis(quality="sufficient"):
    return {
        "summary": "Visible definition can be assessed in this image.",
        "observations": ["Shoulder outline is visible."],
        "strengths": ["Consistent framing."],
        "focus_areas": ["Keep lighting even."],
        "limitations": ["One image cannot establish progress."],
        "next_check_guidance": "Repeat this framing next time.",
        "quality": quality,
    }


def _analysis():
    return {
        "summary": "Framing stayed consistent between the two images.",
        "observed_changes": ["Outline appears similar."],
        "stable_areas": ["Lighting is comparable."],
        "focus_areas": ["Keep the same distance."],
        "limitations": ["Two images cannot establish progress."],
        "comparability_reasons": ["Framing and region match."],
        "next_check_guidance": "Repeat this framing next time.",
    }


def _make_check(user, token, captured_at):
    row = PumpCheck(
        user_id=user.id,
        public_id=token,
        captured_at=captured_at,
        body_region="upper_body",
        location_type="gym",
        visibility="private",
        valid=True,
        analysis_status="completed",
        analysis=_source_analysis(),
        analysis_version=SOURCE_ANALYSIS_VERSION,
        image_key=f"pump-checks/{user.id}/2026/08/{token[:8]}.jpg",
    )
    db.session.add(row)
    db.session.commit()
    return row


def _command(baseline=BASELINE_TOKEN, current=CURRENT_TOKEN):
    return {
        "baseline_pump_check_id": baseline,
        "current_pump_check_id": current,
    }


@pytest.fixture
def mobile_user(make_user):
    return make_user("comparison-mobile")


@pytest.fixture
def stranger(make_user):
    return make_user("comparison-outsider")


@pytest.fixture
def auth_headers(monkeypatch, mobile_user):
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            mobile_user, SimpleNamespace(id=1),
            {"sub": mobile_user.cognito_sub}))
    return {
        "Authorization": "Bearer opaque-access-credential",
        "Idempotency-Key": "comparison-operation-0001",
    }


@pytest.fixture
def pair(mobile_user):
    baseline = _make_check(
        mobile_user, BASELINE_TOKEN, datetime(2026, 8, 1, 9, 0, 0))
    current = _make_check(
        mobile_user, CURRENT_TOKEN, datetime(2026, 8, 14, 9, 0, 0))
    return baseline, current


@pytest.fixture
def dependencies(monkeypatch):
    calls = {"s3": 0, "bedrock": 0}

    def read(key, expected_user_id=None):
        calls["s3"] += 1
        return b"private-image-bytes"

    def analyze(*args, **kwargs):
        calls["bedrock"] += 1
        return "comparable", _analysis()

    monkeypatch.setattr(service.s3_helper, "get_object_bytes", read)
    monkeypatch.setattr(
        service, "prepare_image_for_vision",
        lambda raw, media_type: (raw, media_type))
    monkeypatch.setattr(service, "analyze_images", analyze)
    return calls


def test_create_requires_bearer_json_and_idempotency(
        client, auth_headers, pair, dependencies):
    assert client.post(PATH, json=_command()).status_code == 401

    assert client.post(
        PATH, data="not-json", headers=auth_headers,
        content_type="text/plain").status_code == 400

    headers = {
        key: value for key, value in auth_headers.items()
        if key != "Idempotency-Key"}
    assert client.post(
        PATH, json=_command(), headers=headers).status_code == 400

    assert dependencies == {"s3": 0, "bedrock": 0}


@pytest.mark.parametrize("body", [
    {},
    {"baseline_pump_check_id": BASELINE_TOKEN},
    dict(_command(), extra=True),
    {"baseline_pump_check_id": BASELINE_TOKEN, "current_pump_check_id": 7},
    {"baseline_pump_check_id": "short", "current_pump_check_id": CURRENT_TOKEN},
])
def test_create_rejects_malformed_commands(
        client, auth_headers, pair, dependencies, body):
    response = client.post(PATH, json=body, headers=auth_headers)

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == (
        "INVALID_PUMP_CHECK_COMPARISON")
    assert dependencies == {"s3": 0, "bedrock": 0}


def test_create_and_get_share_private_shape(
        client, auth_headers, pair, dependencies):
    created = client.post(PATH, json=_command(), headers=auth_headers)

    assert created.status_code == 201
    comparison = created.get_json()["pump_check_comparison"]
    assert set(comparison) == {
        "id", "baseline_pump_check_id", "current_pump_check_id",
        "status", "comparability", "analysis",
        "analysis_version", "created_at",
    }
    assert comparison["baseline_pump_check_id"] == BASELINE_TOKEN
    assert comparison["current_pump_check_id"] == CURRENT_TOKEN
    assert comparison["status"] == "completed"
    assert comparison["comparability"] == "comparable"
    assert comparison["analysis_version"] == ANALYSIS_VERSION

    fetched = client.get(
        f"{PATH}/{comparison['id']}", headers=auth_headers)

    assert fetched.status_code == 200
    assert fetched.get_json()["pump_check_comparison"] == comparison
    assert dependencies["bedrock"] == 1


def test_replay_returns_two_hundred_without_new_analysis(
        client, auth_headers, pair, dependencies):
    first = client.post(PATH, json=_command(), headers=auth_headers)
    second = client.post(PATH, json=_command(), headers=auth_headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert (first.get_json()["pump_check_comparison"]["id"]
            == second.get_json()["pump_check_comparison"]["id"])
    assert dependencies["bedrock"] == 1


def test_converged_second_key_returns_two_hundred(
        client, auth_headers, pair, dependencies):
    client.post(PATH, json=_command(), headers=auth_headers)
    other = dict(auth_headers, **{"Idempotency-Key": "comparison-operation-2"})

    response = client.post(PATH, json=_command(), headers=other)

    assert response.status_code == 200
    assert PumpCheckComparison.query.count() == 1
    assert dependencies["bedrock"] == 1


def test_reused_key_with_a_different_command_conflicts(
        client, auth_headers, pair, dependencies):
    client.post(PATH, json=_command(), headers=auth_headers)

    response = client.post(
        PATH, json=_command(CURRENT_TOKEN, BASELINE_TOKEN),
        headers=auth_headers)

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_unknown_and_cross_owner_sources_are_the_same_private_404(
        client, auth_headers, stranger, dependencies):
    _make_check(stranger, BASELINE_TOKEN, datetime(2026, 8, 1, 9, 0, 0))
    _make_check(stranger, CURRENT_TOKEN, datetime(2026, 8, 14, 9, 0, 0))

    cross_owner = client.post(PATH, json=_command(), headers=auth_headers)
    unknown = client.post(
        PATH, json=_command("Z" * 24, "Y" * 24),
        headers=dict(auth_headers, **{"Idempotency-Key": "comparison-op-3"}))

    assert cross_owner.status_code == unknown.status_code == 404
    assert (cross_owner.get_json()["error"]["code"]
            == unknown.get_json()["error"]["code"] == "PUMP_CHECK_NOT_FOUND")
    assert cross_owner.get_json()["error"]["message"] == (
        unknown.get_json()["error"]["message"])
    assert dependencies == {"s3": 0, "bedrock": 0}


def test_deterministic_incompatibility_is_unretryable_422(
        client, auth_headers, pair, dependencies):
    baseline, _current = pair
    baseline.body_region = "back"
    db.session.commit()

    response = client.post(PATH, json=_command(), headers=auth_headers)

    assert response.status_code == 422
    body = response.get_json()["error"]
    assert body["code"] == "PUMP_CHECKS_NOT_COMPARABLE"
    assert body["retryable"] is False
    assert dependencies == {"s3": 0, "bedrock": 0}


def test_unusable_media_is_unretryable_422(
        monkeypatch, client, auth_headers, pair, dependencies):
    from app.services.vision_images import ImagePreparationError

    def corrupt(raw, media_type):
        raise ImagePreparationError("image bytes are not decodable")

    monkeypatch.setattr(service, "prepare_image_for_vision", corrupt)

    response = client.post(PATH, json=_command(), headers=auth_headers)

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "PUMP_CHECKS_NOT_COMPARABLE"
    assert dependencies["bedrock"] == 0


@pytest.mark.parametrize("failure", [
    lambda: (_ for _ in ()).throw(s3_helper.S3Error("transient")),
    lambda: (_ for _ in ()).throw(
        InvalidComparisonAnalysis("comparison response is invalid")),
])
def test_transient_failures_are_retryable_503(
        monkeypatch, client, auth_headers, pair, dependencies, failure):
    monkeypatch.setattr(
        service, "analyze_images", lambda *args, **kwargs: failure())

    response = client.post(PATH, json=_command(), headers=auth_headers)

    assert response.status_code == 503
    body = response.get_json()["error"]
    assert body["code"] == "PUMP_CHECK_COMPARISON_UNAVAILABLE"
    assert body["retryable"] is True


def test_unexpired_lease_returns_the_analyzing_representation(
        client, auth_headers, pair, dependencies):
    baseline, current = pair
    row = PumpCheckComparison(
        user_id=baseline.user_id,
        baseline_pump_check_id=baseline.id,
        current_pump_check_id=current.id,
        public_id="C" * 24,
        status="analyzing",
        analysis_started_at=datetime.utcnow(),
        analysis_attempt=2,
        analysis_version=ANALYSIS_VERSION,
    )
    db.session.add(row)
    db.session.commit()

    response = client.post(PATH, json=_command(), headers=auth_headers)

    assert response.status_code == 200
    payload = response.get_json()["pump_check_comparison"]
    assert payload["status"] == "analyzing"
    assert payload["analysis"] is None
    assert payload["comparability"] is None
    assert dependencies == {"s3": 0, "bedrock": 0}


def test_saturated_gate_is_a_retryable_503_with_retry_after(
        monkeypatch, client, auth_headers, pair, dependencies):
    monkeypatch.setattr(ai_gate._ai_slots, "acquire", lambda timeout=0: False)

    response = client.post(PATH, json=_command(), headers=auth_headers)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "15"
    assert response.get_json()["error"]["code"] == (
        "PUMP_CHECK_COMPARISON_PROVIDER_BUSY")
    assert dependencies == {"s3": 0, "bedrock": 0}


def test_get_is_read_only(
        monkeypatch, client, auth_headers, pair, dependencies):
    created = client.post(PATH, json=_command(), headers=auth_headers)
    token = created.get_json()["pump_check_comparison"]["id"]
    monkeypatch.setattr(
        service, "_claim_analysis",
        lambda *args, **kwargs: pytest.fail("GET must not claim"))
    monkeypatch.setattr(
        service.s3_helper, "get_object_bytes",
        lambda *args, **kwargs: pytest.fail("GET must not read storage"))

    response = client.get(f"{PATH}/{token}", headers=auth_headers)

    assert response.status_code == 200
    assert dependencies["bedrock"] == 1


def test_unknown_malformed_and_cross_owner_comparisons_share_one_404(
        client, auth_headers, monkeypatch, mobile_user, stranger, pair,
        dependencies):
    created = client.post(PATH, json=_command(), headers=auth_headers)
    token = created.get_json()["pump_check_comparison"]["id"]

    responses = [
        client.get(f"{PATH}/{'D' * 24}", headers=auth_headers),
        client.get(f"{PATH}/not-a-valid-token", headers=auth_headers),
    ]
    monkeypatch.setattr(
        mobile_auth, "authenticate_access",
        lambda raw: mobile_auth.MobilePrincipal(
            stranger, SimpleNamespace(id=1), {"sub": stranger.cognito_sub}))
    responses.append(client.get(f"{PATH}/{token}", headers=auth_headers))

    assert {response.status_code for response in responses} == {404}
    assert {
        response.get_json()["error"]["code"] for response in responses
    } == {"PUMP_CHECK_COMPARISON_NOT_FOUND"}


def test_responses_never_carry_media_or_internal_authority(
        client, auth_headers, pair, dependencies):
    created = client.post(PATH, json=_command(), headers=auth_headers)

    raw = created.get_data(as_text=True)
    for forbidden in (
            "image_url", "image_key", "pump-checks/", "idempotency",
            "fingerprint", "analysis_attempt", "analysis_started_at",
            "analysis_failure_kind", "user_id"):
        assert forbidden not in raw
