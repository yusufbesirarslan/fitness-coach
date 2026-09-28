"""Progress handoff transport keeps canonical facts out of user speech."""
from types import SimpleNamespace

import pytest

from app.blueprints import coach as coach_bp
from app.coach_handoff import coach_handoff_context, coach_handoff_message
from app.services import ai_pipeline


@pytest.mark.parametrize("marker,expected", [
    ("progress-insight", "progress-insight"),
    ("other", None),
    ({"insight": "deload", "user_id": 999}, None),
    (["progress-insight"], None),
    (123, None),
    (None, None),
])
@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_route_forwards_only_allowlisted_kind(client, auth_user, app,
                                               monkeypatch, marker, expected, path):
    app.config["AI_CHAT_QUOTA_ENABLED"] = False
    seen = []

    def answer(uid, question, history, language="tr", **kw):
        seen.append((uid, question, kw.get("handoff")))
        return {"answer": "ok", "is_error_fallback": False,
                "conversation_id": None}

    monkeypatch.setattr(coach_bp, "generate_answer", answer)

    def stream(uid, question, history, language="tr", **kw):
        result = answer(uid, question, history, language, **kw)
        yield {"type": "done", "text": result["answer"],
               "is_error_fallback": False, "usage": None}

    monkeypatch.setattr(coach_bp, "stream_answer", stream)
    response = client.post(path, json={"question": "My own words", "handoff": marker},
                           buffered=True)
    response.close()
    assert response.status_code == 200
    assert seen == [(auth_user.id, "My own words", expected)]


def test_server_derives_current_owner_context(app, monkeypatch):
    import app.services.progress_insights as insights

    seen = []

    def build(uid):
        seen.append(uid)
        return SimpleNamespace(insight=SimpleNamespace(
            code="baseline", action_code="build_baseline", action=None, evidence=()))

    monkeypatch.setattr(insights, "build_progress_insights", build)
    with app.test_request_context("/coach"):
        text = coach_handoff_context("progress-insight", 17, "en")
    assert seen == [17]
    assert "Axis is still building your baseline." in text
    assert "build_baseline" not in text
    assert coach_handoff_context("forged", 17, "en") == ""
    assert seen == [17]


def test_send_uses_fresh_canonical_state_after_page_render(app, monkeypatch):
    import app.services.progress_insights as insights

    codes = iter(("baseline", "holding_steady"))

    def build(uid):
        return SimpleNamespace(insight=SimpleNamespace(
            code=next(codes), action_code="build_baseline", action=None, evidence=()))

    monkeypatch.setattr(insights, "build_progress_insights", build)
    with app.test_request_context("/coach"):
        preview = coach_handoff_message("progress-insight", 17, "en")
        current = coach_handoff_context("progress-insight", 17, "en")
    assert "building your baseline" in preview["preview"]
    assert "workload is holding" in current.lower()
    assert "building your baseline" not in current


def test_pipeline_persists_exact_question_separately(app, monkeypatch):
    from app.services import ai_coach

    recorded = []
    seen = []
    monkeypatch.setattr(ai_pipeline, "_memory_stage",
                        lambda uid: (SimpleNamespace(id=3), [], None))
    monkeypatch.setattr(ai_pipeline, "_context_stage",
                        lambda uid, question, lang, handoff: "private context")
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda uid, question, context, history, **kw:
                        seen.append((question, context)) or "Helpful answer")
    monkeypatch.setattr(ai_pipeline, "_record",
                        lambda conv, question, answer, **kw:
                        recorded.append((question, answer)))
    with app.app_context():
        result = ai_pipeline.generate_answer(
            17, "Help me make this week realistic.", handoff="progress-insight")
    assert result["answer"] == "Helpful answer"
    assert seen == [("Help me make this week realistic.", "private context")]
    assert recorded == [("Help me make this week realistic.", "Helpful answer")]


def test_failed_send_time_derivation_has_no_raw_fallback(app, monkeypatch):
    import app.services.progress_insights as insights

    def unavailable(uid):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(insights, "build_progress_insights", unavailable)
    with app.test_request_context("/coach"):
        assert coach_handoff_context("progress-insight", 17, "en") == ""


def test_database_history_contains_only_user_words(app, auth_user, monkeypatch):
    from app.models import CoachMessage
    from app.services import ai_coach

    app.config["AI_MEMORY_ENABLED"] = True
    monkeypatch.setattr(ai_pipeline, "_context_stage",
                        lambda uid, question, lang, handoff: "Current private facts")
    monkeypatch.setattr(ai_coach, "_run_coach_conversation",
                        lambda *a, **kw: "A grounded answer")
    with app.app_context():
        result = ai_pipeline.generate_answer(auth_user.id, "My exact edited words.",
                                             handoff="progress-insight")
        rows = CoachMessage.query.filter_by(
            conversation_id=result["conversation_id"]).order_by(CoachMessage.id).all()
    assert [(row.role, row.content) for row in rows] == [
        ("user", "My exact edited words."), ("assistant", "A grounded answer")]
    assert all("Current private facts" not in row.content for row in rows)


@pytest.mark.parametrize("locale,expected", [("en", "+5%"), ("tr", "%+5")])
def test_handoff_preserves_exact_planner_adjustment(app, monkeypatch, locale, expected):
    import app.services.progress_insights as insights

    monkeypatch.setattr(insights, "build_progress_insights", lambda uid:
        SimpleNamespace(insight=SimpleNamespace(
            code="ready_to_progress", action_code="progress_training", evidence=(),
            action=SimpleNamespace(week_focus="overload", volume_delta_pct=0.05))))
    with app.test_request_context("/coach"):
        context = coach_handoff_context("progress-insight", 17, locale)
    assert expected in context
    assert "volume_delta_pct" not in context


def test_handoff_builds_once_and_replaces_regular_plan_projection(app, auth_user, monkeypatch):
    from app.services import context_builder, adaptive_plan_context, progress_insights

    builds = []
    original = progress_insights.build_progress_insights
    monkeypatch.setattr(progress_insights, "build_progress_insights",
                        lambda uid: builds.append(uid) or original(uid))
    monkeypatch.setattr(adaptive_plan_context, "build_coach_plan_context",
                        lambda *args: pytest.fail("duplicate plan build"))
    app.config["AI_ADAPTIVE_PLAN_CONTEXT"] = True
    with app.app_context():
        context = context_builder.fetch_coach_context(
            auth_user.id, "Help me plan this week.", "en", "progress-insight")
    assert builds == [auth_user.id]
    assert "[PROGRESS CONTEXT]" in context
    assert "[CURRENT TRAINING GUIDANCE]" in context
