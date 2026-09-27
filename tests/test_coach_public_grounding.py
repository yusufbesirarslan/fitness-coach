"""Coach-facing planning context is a presentation of the canonical decision."""

import pytest

from app.services.training_planning import AdaptivePlan


@pytest.mark.parametrize("language", ["en", "tr"])
def test_consistency_projection_has_one_action_without_machine_vocabulary(language):
    from app.services.adaptive_plan_context import project_coach_plan

    plan = AdaptivePlan(weeks=4, has_data=True, week_focus="build_consistency",
                        reason_codes=("inconsistent_training",))
    result = project_coach_plan(plan, language)
    forbidden = ("inconsistent_training", "build_consistency", "next_signal",
                 "reason_codes", "schema_version", "week_focus", "AdaptivePlan")
    assert all(token.lower() not in result.lower() for token in forbidden)
    assert ("one training session" if language == "en" else "en az bir antrenman") in result


@pytest.mark.parametrize("focus,delta,expected", [
    ("overload", 0.05, "+5%"),
    ("deload", -0.4, "-40%"),
])
def test_projection_copies_only_canonical_volume_delta(focus, delta, expected):
    from app.services.adaptive_plan_context import project_coach_plan

    plan = AdaptivePlan(weeks=4, has_data=True, week_focus=focus,
                        volume_action="increase" if delta > 0 else "decrease",
                        intensity_action="progress" if delta > 0 else "deload",
                        volume_delta_pct=delta)
    result = project_coach_plan(plan, "en")
    assert expected in result


def test_unknown_focus_fails_neutral_without_guessing():
    from app.services.adaptive_plan_context import project_coach_plan

    result = project_coach_plan(AdaptivePlan(weeks=0, week_focus="future_focus"), "en")
    assert "unavailable" in result.lower()
    assert "future_focus" not in result


@pytest.mark.parametrize("focus,expected", [
    ("insufficient_data", "building your baseline"),
    ("maintenance", "volume and intensity where they are"),
    ("steady", "without adding or cutting work"),
])
def test_baseline_and_holds_do_not_become_progression(focus, expected):
    from app.services.adaptive_plan_context import project_coach_plan

    result = project_coach_plan(AdaptivePlan(weeks=4, week_focus=focus), "en")
    assert expected in result
    assert "increase" not in result.lower()


def test_raw_checkin_nudges_do_not_override_canonical_hold(app, auth_user, monkeypatch):
    from app.services import adaptive_plan_context, analytics_engine, context_builder
    from tests.test_adaptive_plan_context import _stub_baseline_context_sources

    _stub_baseline_context_sources(monkeypatch)
    monkeypatch.setattr(analytics_engine, "get_nudges", lambda *_args, **_kwargs: [
        "NUDGE_RECOVERY: Suggest lowering training volume/intensity.",
        "NUDGE_OVERLOAD_STALL: Suggest a small load increase.",
        "NUDGE_HYDRATION: Drink some water.",
    ])
    monkeypatch.setattr(adaptive_plan_context, "build_adaptive_plan",
                        lambda _uid: AdaptivePlan(weeks=4, week_focus="steady"))
    app.config["AI_ADAPTIVE_PLAN_CONTEXT"] = True
    context = context_builder.fetch_coach_context(auth_user.id, "question", "en")
    assert "NUDGE_RECOVERY" not in context
    assert "NUDGE_OVERLOAD_STALL" not in context
    assert "NUDGE_HYDRATION" in context
    assert "without adding or cutting work" in context


@pytest.mark.parametrize("language", ["en", "tr"])
def test_adaptive_prompt_states_public_claim_limits(language):
    from app.prompts.system import build_coach_system

    prompt = build_coach_system(language, adaptive_plan_context=True)
    for phrase in ("internal identifiers", "unlock criteria", "background monitoring",
                   "One training session", "asks for a schedule"):
        assert phrase in prompt


@pytest.mark.parametrize("text", [
    "next_signal = build_consistency",
    "The AdaptivePlan contract says reason_codes=inconsistent_training.",
    "NUDGE TRIGGERED: make a plan",
    '"schema_version": 1',
])
def test_exact_machine_vocabulary_is_detected(text):
    from app.services.moderation import leaks_internal_coach_term

    assert leaks_internal_coach_term(text)
    assert not leaks_internal_coach_term("Consistency is the priority this week.")


@pytest.mark.parametrize("language", ["en", "tr"])
def test_mocked_provider_leak_is_not_published_or_saved(app, monkeypatch, language):
    from app.services import ai_coach, ai_pipeline, context_builder
    from app.services.response_formatter import error_fallback

    app.config["AI_MEMORY_ENABLED"] = False
    app.config["AI_ADAPTIVE_PLAN_CONTEXT"] = True
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda *_args, **_kwargs: "safe context")
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda *_args, **_kwargs: "next_signal = build_consistency")
    with app.test_request_context("/"):
        result = ai_pipeline.generate_answer(1, "Review my progress", language=language)
    assert result["answer"] == error_fallback(language)
    assert result["is_error_fallback"] is True


def test_stream_holds_machine_code_until_final_check(app, monkeypatch):
    from app.services import ai_pipeline, ai_stream, context_builder
    from app.services.response_formatter import error_fallback

    app.config["AI_MEMORY_ENABLED"] = False
    app.config["AI_ADAPTIVE_PLAN_CONTEXT"] = True
    monkeypatch.setattr(context_builder, "fetch_coach_context",
                        lambda *_args, **_kwargs: "safe context")
    def fake_stream(*_args, **_kwargs):
        yield {"type": "delta", "text": "next_"}
        yield {"type": "delta", "text": "signal = build_consistency"}
        yield {"type": "done", "text": "next_signal = build_consistency"}
    monkeypatch.setattr(ai_stream, "stream_coach_answer", fake_stream)
    with app.test_request_context("/"):
        events = list(ai_pipeline.stream_answer(1, "Review my progress", language="en"))
    assert [e["type"] for e in events] == ["meta", "delta", "done"]
    assert events[1]["text"] == error_fallback("en")
    assert events[2]["is_error_fallback"] is True
