"""Three ways production could lie about the AI provider, and the guards.

The Coach ran for weeks on a provider nobody chose. The app's IAM identity had
no ``bedrock:InvokeModel``, so every Bedrock call returned 403 and every turn
was silently served by the OpenAI fallback -- the model the mutation contract
was never validated against. Three separate defects let that stay invisible:

1. ``_BedrockFallback`` carried the reason and the log site threw it away, so
   "trying OpenAI fallback" looked identical for a one-off timeout and for a
   permission failure on 100% of calls.
2. Deep health reported ``bedrock: enabled``, which was ``BEDROCK_ENABLED``
   echoed back. The deploy gate read that field and passed, every time.
3. The output guard caught a model claiming a *proposal* existed, but not one
   claiming a mutation was *already done*. Production request ``217f5ce0``
   emitted "Done - Dumbbell Biceps Curl has been added to your Monday workout."
   with no ``[COACH][PLAN_TOOL]`` line behind it: no tool ran, no plan row
   moved, and the user was told otherwise.

Each guard is proved by mutating the thing it depends on (the non-vacuity
section at the bottom): remove the completion check and the fabricated-claim
test fails; return the config echo and the reachability test fails.

    python -m pytest tests/test_coach_provider_truthfulness.py -v
"""
import ast
import inspect
import logging

import pytest

from app.services import coach_confirmation, provider_failure
from tests.test_coach_plan_tools import (  # noqa: F401
    ADD, call, canonical_user, results, seed_plan, tools_on, turn,
)


class PermissionDeniedError(Exception):
    """Shaped like the anthropic SDK's error: a class name and a status code.

    The message is never read by the classifier, so it carries the ARN the real
    403 carried -- which is how the leak test below has something to look for.
    """

    status_code = 403

    def __init__(self):
        super().__init__(
            "User: arn:aws:iam::852128326881:user/fitx-s3-user is not "
            "authorized to perform: bedrock:InvokeModel")


class _Throttled(Exception):
    status_code = 429


# -- A. the fallback reason survives to the log ------------------------------

def test_access_denied_is_classified_from_the_exception():
    assert provider_failure.classify(PermissionDeniedError()) == (
        provider_failure.ACCESS_DENIED)
    assert provider_failure.classify(_Throttled()) == provider_failure.THROTTLED
    assert provider_failure.classify(TimeoutError()) == provider_failure.TIMEOUT
    assert provider_failure.classify(None) == provider_failure.UNKNOWN
    # Every verdict is a member of the closed vocabulary -- never provider text.
    assert provider_failure.classify(RuntimeError("boom")) in (
        provider_failure.CATEGORIES)


def test_bedrock_fallback_carries_the_sanitized_category():
    from app.services import ai_coach

    cause = PermissionDeniedError()
    fallback = ai_coach._BedrockFallback(type(cause).__name__, cause=cause)

    assert fallback.category == provider_failure.ACCESS_DENIED
    assert fallback.exception_class == "PermissionDeniedError"


def test_a_fallback_with_no_cause_is_unknown_not_a_guess():
    from app.services import ai_coach

    fallback = ai_coach._BedrockFallback("empty bedrock stream")

    assert fallback.category == provider_failure.UNKNOWN
    assert fallback.exception_class == "none"


def test_stream_fallback_logs_provider_category_and_request_id(app, caplog):
    """The defect: this log line existed and said nothing usable."""
    from app.observability import assign_request_id
    from app.services import ai_coach

    fallback = ai_coach._BedrockFallback(
        "PermissionDeniedError", cause=PermissionDeniedError())

    with app.test_request_context("/ask/stream", method="POST"):
        assign_request_id()
        with caplog.at_level(logging.WARNING):
            ai_coach.log_provider_fallback(
                logging.getLogger("truthfulness"),
                "[COACH][stream] Bedrock failed before work", fallback)

    line = caplog.text
    assert "provider=bedrock" in line
    assert "fallback_provider=openai" in line
    assert "exception=PermissionDeniedError" in line
    assert "category=access_denied" in line
    assert "request_id=" in line


def test_fallback_log_never_leaks_the_provider_message(app, caplog):
    """The 403 body names the IAM principal and the resource ARN. Neither is a
    credential, and neither belongs in an application log."""
    from app.services import ai_coach

    cause = PermissionDeniedError()
    fallback = ai_coach._BedrockFallback(str(cause), cause=cause)

    with app.test_request_context("/ask/stream", method="POST"):
        with caplog.at_level(logging.WARNING):
            ai_coach.log_provider_fallback(
                logging.getLogger("truthfulness"), "[COACH][stream] x",
                fallback)

    assert "arn:aws:iam" not in caplog.text
    assert "fitx-s3-user" not in caplog.text


def test_the_stream_path_uses_the_sanitized_logger(app):
    """The streaming path is the one production runs, and the one that dropped
    the reason. Pin the call, not just the helper it should call."""
    from app.services import ai_stream

    tree = ast.parse(inspect.getsource(ai_stream.stream_coach_answer))
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "log_provider_fallback" in called
    assert "warning" not in called, (
        "a bare logger.warning here is how the reason was lost")


# -- B. deep health probes Bedrock instead of echoing config -----------------

@pytest.fixture
def bedrock_probe(monkeypatch):
    """Configured Bedrock whose reachability the test decides."""
    from app.services import bedrock_health

    bedrock_health.reset_cache()
    monkeypatch.setattr(bedrock_health, "BEDROCK_ENABLED", True)

    def _set(outcome):
        bedrock_health.reset_cache()
        monkeypatch.setattr(bedrock_health, "_probe_once", lambda: outcome)

    yield _set
    bedrock_health.reset_cache()


def test_deep_health_reports_configured_and_reachable(bedrock_probe):
    from app.services import bedrock_health

    bedrock_probe((True, None))

    assert bedrock_health.probe() == {"configured": True, "reachable": True}


def test_deep_health_reports_configured_but_unreachable(bedrock_probe):
    from app.services import bedrock_health

    bedrock_probe((False, provider_failure.ACCESS_DENIED))
    result = bedrock_health.probe()

    assert result["configured"] is True
    assert result["reachable"] is False
    assert result["failure"] == provider_failure.ACCESS_DENIED


def test_unconfigured_bedrock_is_not_probed(monkeypatch):
    from app.services import bedrock_health

    bedrock_health.reset_cache()
    monkeypatch.setattr(bedrock_health, "BEDROCK_ENABLED", False)
    monkeypatch.setattr(
        bedrock_health, "_probe_once",
        lambda: pytest.fail("an unconfigured provider must not be called"))

    assert bedrock_health.probe() == {"configured": False, "reachable": False}
    bedrock_health.reset_cache()


def test_the_probe_is_a_streaming_call_capped_at_one_token():
    """``InvokeModelWithResponseStream`` is a different IAM action from
    ``InvokeModel``, and ``/ask/stream`` is what production runs. A blocking
    probe would go green on a policy that still broke every conversation."""
    from app.services import bedrock_health

    source = inspect.getsource(bedrock_health._probe_once)

    assert "messages.stream" in source
    assert "messages.create" not in source
    assert bedrock_health.PROBE_MAX_TOKENS == 1
    assert 0 < bedrock_health.PROBE_TIMEOUT_SECONDS <= 10


def test_probe_result_is_cached_so_health_cannot_become_a_workload(monkeypatch):
    from app.services import bedrock_health

    bedrock_health.reset_cache()
    monkeypatch.setattr(bedrock_health, "BEDROCK_ENABLED", True)
    calls = []

    def _once():
        calls.append(1)
        return True, None

    monkeypatch.setattr(bedrock_health, "_probe_once", _once)
    for _ in range(5):
        bedrock_health.probe()

    assert calls == [1]
    bedrock_health.reset_cache()


def test_a_failure_is_cached_far_more_briefly_than_a_success():
    """The deploy gate retries; a short failure TTL lets it re-probe rather
    than re-read one bad moment, while success is held long enough that polling
    cannot turn health into inference load."""
    from app.services import bedrock_health

    assert bedrock_health.CACHE_TTL_FAIL_SECONDS < (
        bedrock_health.CACHE_TTL_OK_SECONDS)


def test_probe_never_raises(monkeypatch):
    """A probe that could break ``/health`` would be worse than the blind spot
    it closes."""
    from app.services import bedrock_health

    bedrock_health.reset_cache()
    monkeypatch.setattr(bedrock_health, "BEDROCK_ENABLED", True)

    class _Exploding:
        @property
        def messages(self):
            raise RuntimeError("client construction failed")

    monkeypatch.setattr("app.extensions.bedrock_client", _Exploding())
    result = bedrock_health.probe()

    assert result["configured"] is True
    assert result["reachable"] is False
    bedrock_health.reset_cache()


def test_deploy_gate_fails_when_configured_bedrock_is_unreachable(
        app, bedrock_probe):
    """The whole point: no more green deploy while every provider call 403s."""
    bedrock_probe((False, provider_failure.ACCESS_DENIED))

    with app.test_request_context(
            "/health?deep=1", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        body, status = app.view_functions["health"]()

    assert status == 503
    assert body["status"] == "error"
    assert body["bedrock"]["failure"] == provider_failure.ACCESS_DENIED


def test_deploy_gate_passes_when_bedrock_is_reachable(app, bedrock_probe):
    bedrock_probe((True, None))

    with app.test_request_context(
            "/health?deep=1", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        body, status = app.view_functions["health"]()

    assert status == 200
    assert body["bedrock"] == {"configured": True, "reachable": True}


# -- C. a claimed completion needs execution evidence ------------------------

@pytest.fixture
def plan_user(app, make_user):
    user = make_user("truthfulness")
    seed_plan(user.id, {"program": [
        {"gun": "Pazartesi", "tip": "antrenman", "odak": "Arms",
         "egzersizler": [{"isim": "Triceps Pushdown", "set": 3,
                          "tekrar": "12"}]},
        {"gun": "Cuma", "tip": "antrenman", "odak": "Legs",
         "egzersizler": [{"isim": "Back Squat", "set": 4, "tekrar": "8"}]},
    ]})
    return user


#: The verbatim production sentence, plus the forms it generalises to.
_FABRICATED = [
    "Done - Dumbbell Biceps Curl has been added to your Monday workout.",
    "I've added Lateral Raise to your Monday workout.",
    "Bench Press has been replaced with Dumbbell Press in your plan.",
    "Your Monday workout is now updated.",
    "I removed Back Squat from your plan.",
    "Planına Lateral Raise ekledim.",
    "Egzersiz programından çıkarıldı.",
]


@pytest.mark.parametrize("provider_text", _FABRICATED)
def test_a_completion_claim_without_a_mutation_cannot_escape(
        app, plan_user, provider_text):
    with app.test_request_context("/ask", method="POST"):
        reply = coach_confirmation.grounded_provider_reply(
            plan_user.id, "en", provider_text)

    assert reply != provider_text
    assert "not changed" in reply.lower()


def test_the_guard_sits_on_the_boundary_both_providers_pass_through():
    """The sentence that caused this came from the OpenAI fallback, not from
    Bedrock. One invariant on the shared prose boundary, not one per provider:
    every provider loop must route its final text through the same guard."""
    from app.services import ai_coach, ai_stream

    for func in (ai_coach._run_coach_conversation_openai,
                 ai_coach._run_coach_conversation_bedrock,
                 ai_stream._stream_bedrock):
        source = inspect.cleandoc(inspect.getsource(func))
        assert "grounded_provider_reply" in source, func.__name__


def test_a_real_mutation_may_still_report_itself(
        app, canonical_user, tools_on, turn):
    """Requirement 4: the guard suppresses fabrication, not success.

    The claim is the same past-tense shape the guard rejects above. The only
    difference is that this turn actually moved persisted state -- which is
    exactly the distinction the invariant is supposed to draw."""
    from app.services import coach_plan_tools

    claim = "I've added Resistance Band Row to your Wednesday workout."

    result = call(canonical_user.id, ADD, {
        "day": "Çarşamba", "exercise": "Band Row", "sets": 3,
        "reps": "10-12"})

    assert result["status"] == results.STATUS_APPLIED, result
    assert coach_plan_tools.plan_changed_this_turn() is True
    assert coach_confirmation.grounded_provider_reply(
        canonical_user.id, "en", claim) == claim


@pytest.mark.parametrize("provider_text", [
    # Requirement 6: advisory prose stays usable -- the fallback still has to
    # be able to coach. None of these claims a plan mutation happened.
    "Your plan looks solid. Add weight when the last set feels easy.",
    "Once you're done with your workout, log it and I'll check the volume.",
    "Which day would you like me to add it to?",
    "Great week -- you hit every session on your plan.",
    "Antrenmanı kaçırdın, bu hafta programa sadık "
    "kalmaya çalış.",
])
def test_non_claims_pass_through_untouched(app, plan_user, provider_text):
    with app.test_request_context("/ask", method="POST"):
        reply = coach_confirmation.grounded_provider_reply(
            plan_user.id, "en", provider_text)

    assert reply == provider_text


@pytest.mark.parametrize("provider_text", [
    "Shall I add Dumbbell Curl to Monday?",
    "Would you like me to remove Bench Press from your plan?",
    "Bunu programa eklememi ister misin?",
])
def test_proposal_suppression_is_unchanged(app, plan_user, provider_text):
    """Requirement 5: the guard that already worked still works."""
    with app.test_request_context("/ask", method="POST"):
        reply = coach_confirmation.grounded_provider_reply(
            plan_user.id, "en", provider_text)

    assert reply != provider_text
    assert "nothing was changed" in reply.lower()


def test_the_guard_never_executes_what_the_prose_claims(
        app, plan_user, tools_on):
    """"Do NOT invent a mutation from model prose." A suppressed completion
    claim must leave the plan exactly as it was."""
    from app.services.today_facts import get_active_plan

    with app.test_request_context("/ask", method="POST"):
        before = get_active_plan(plan_user.id).plan_data
        coach_confirmation.grounded_provider_reply(
            plan_user.id, "en",
            "Done - Lateral Raise has been added to your Monday workout.")
        after = get_active_plan(plan_user.id).plan_data

    assert after == before


# -- Non-vacuity: break the guard, watch the test fail ----------------------

def test_without_the_completion_check_the_fabricated_claim_escapes(
        app, plan_user, monkeypatch):
    """Bypass the guard and the production sentence goes straight to the user.

    Proves the assertion above is load-bearing rather than incidentally true of
    a sentence some other rule already caught."""
    monkeypatch.setattr(
        coach_confirmation, "_claims_completed_plan_change", lambda text: False)
    claim = "Done - Dumbbell Biceps Curl has been added to your Monday workout."

    with app.test_request_context("/ask", method="POST"):
        reply = coach_confirmation.grounded_provider_reply(
            plan_user.id, "en", claim)

    assert reply == claim


def test_with_a_config_only_health_field_the_gate_goes_green(app, monkeypatch):
    """Restore the old behaviour -- report configuration as reachability -- and
    the deploy gate passes while the provider is dead."""
    from app.services import bedrock_health

    bedrock_health.reset_cache()
    monkeypatch.setattr(bedrock_health, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(
        bedrock_health, "_probe_once",
        lambda: (False, provider_failure.ACCESS_DENIED))
    monkeypatch.setattr(
        bedrock_health, "probe",
        lambda force=False: {"configured": True, "reachable": True})

    with app.test_request_context(
            "/health?deep=1", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        body, status = app.view_functions["health"]()

    assert status == 200
    assert body["status"] == "ok"
    bedrock_health.reset_cache()


def test_the_shallow_health_path_never_probes(app, monkeypatch):
    """The container healthcheck hits ``/health``. It must stay free."""
    from app.services import bedrock_health

    bedrock_health.reset_cache()
    monkeypatch.setattr(bedrock_health, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(
        bedrock_health, "_probe_once",
        lambda: pytest.fail("shallow health must not call the provider"))

    resp = app.test_client().get("/health")

    assert resp.status_code == 200
    assert "bedrock" not in resp.get_json()
    bedrock_health.reset_cache()


def test_the_probe_does_not_take_a_capacity_permit():
    """Pinned, because the house rule says the opposite and the exception is
    the point: /health is the caller here, so a saturation refusal would fail
    the health check and trigger the rollback the gate exists to prevent."""
    from app.services import bedrock_health

    tree = ast.parse(inspect.getsource(bedrock_health))
    # The CALL, not the word — the module docstring names the rule it departs
    # from on purpose, and a substring check would flag its own explanation.
    called = {
        getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }

    assert "blocking_concurrency_slot" not in called
    assert "model_concurrency_slot" not in called
