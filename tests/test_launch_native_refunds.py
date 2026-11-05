"""Actual native Training terminal receipts, using synthetic SDK results."""
from dataclasses import replace
import json

import pytest

from app.services.ai_allowances import launch_policy
from app.extensions import db
from app.models import AiAllowanceBucket, AiUsageReservation, User
from app.services import ai, ai_consent
from app.services.ai_allowances import execution as meter
from physical_budget_support import before_attempt
from nutrition_pr7_support import native, bearer, set_target
from test_premium_allowance_execution import ready
from test_d1_launch_integration import contracts
from tests.test_mobile_training_allowance import (
    CANONICAL, POST_PATH, as_mobile, mobile_user, metered, provider, _ledger,
)


@pytest.mark.usefixtures("synthetic_ai_layer")
@pytest.mark.parametrize("mode", ["unavailable", "invalid"])
def test_native_training_terminal_failure_refunds_and_replays_without_provider(
        app, client, mobile_user, as_mobile, metered, provider, mode):
    app.config["AI_ALLOWANCE_POLICY"] = replace(launch_policy.LAUNCH_V1_POLICY,
        version="synthetic-native-refund")
    provider.mode = mode
    headers = as_mobile(mobile_user, "launch-native-failed-key")
    first = client.post(POST_PATH, json=CANONICAL, headers=headers)
    assert first.status_code in (422, 503)
    assert provider.calls == 1
    state = _ledger(mobile_user.id)
    assert state.bucket[1:3] == (0, 0)
    assert state.holds[0][0] == "RELEASED"
    assert state.holds[0][2:] == ("UNKNOWN", None)
    again = client.post(POST_PATH, json=CANONICAL, headers=headers)
    assert again.status_code == first.status_code
    assert provider.calls == 1
    assert _ledger(mobile_user.id).bucket[1:3] == (0, 0)


@pytest.mark.parametrize("options", [[], {"wrong": "shape"}, [{"invalid": True}]])
def test_native_nutrition_rejects_unusable_options_and_refunds(
        app, native, bearer, synthetic_ai_layer, ready, monkeypatch, options):
    app.config["AI_ALLOWANCE_POLICY"] = replace(launch_policy.LAUNCH_V1_POLICY,
        version="synthetic-native-refund")
    user = db.session.get(User, ready)
    set_target(ready)
    calls = []

    def generate(**kwargs):
        before_attempt(ready, "nutrition_plan")
        calls.append(True)
        return json.dumps({"planlar": options})

    monkeypatch.setattr(ai, "_heavy_chat", generate)
    headers = bearer(user, **{"Idempotency-Key": "native-nutrition-unusable-key"})
    command = {"proteins": ["Tavuk Göğsü"], "carbs": ["Pirinç"], "fats": ["Zeytinyağı"]}
    response = native.post("/api/v1/nutrition/plan/generate", json=command, headers=headers)
    assert response.status_code == 503
    row = AiUsageReservation.query.one()
    assert (row.state, row.physical_attempts, row.billing_evidence) == ("RELEASED", 1, "UNKNOWN")
    assert (AiAllowanceBucket.query.one().used, AiAllowanceBucket.query.one().reserved) == (0, 0)
    assert native.post("/api/v1/nutrition/plan/generate", json=command, headers=headers).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize("raw,valid", [("not JSON", False), ("{}", False),
    ('{"is_gym":"false","form_rating":3,"reason":"x"}', False),
    ('{"is_gym":false,"form_rating":0,"reason":"kitchen"}', True)])
def test_qualified_coach_photo_distinguishes_invalid_output_from_valid_negative(
        app, ready, contracts, monkeypatch, raw, valid):
    from app.services import ai_coach
    app.config["AI_ALLOWANCE_POLICY"] = replace(launch_policy.LAUNCH_V1_POLICY,
        version="synthetic-native-refund")
    monkeypatch.setitem(ai_consent.POLICIES, "coach",
        replace(ai_consent.POLICIES["coach"], nested_features=("vision",)))
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(ai_coach.s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(ai_coach.s3_helper, "get_object_bytes", lambda *a, **kw: b"synthetic")

    def analyze(*args, **kwargs):
        before_attempt(ready, "vision")
        return raw

    monkeypatch.setattr(ai_coach, "_bedrock_validate_image", analyze)
    authority = ai_consent.OperationAuthority(ready, "coach", 5, "coach")
    with ai_consent.operation_scope(authority), meter.logical_action(
            ready, "coach", {"photo": "synthetic"}, key="qualified-coach-photo-key") as action:
        result = json.loads(ai_coach._dispatch_coach_tool(ready, "analyze_gym_photo",
            json.dumps({"s3_key": f"pump-checks/{ready}/photo.jpg"})))
        assert result["status"] == ("ok" if valid else "error")
        if valid:
            assert result["is_gym"] is False
            action.success()
    row = AiUsageReservation.query.one()
    assert (row.state, row.physical_attempts) == ("COMMITTED" if valid else "RELEASED", 1)
    assert AiAllowanceBucket.query.one().used == int(valid)
