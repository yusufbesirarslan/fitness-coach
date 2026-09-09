"""UX-3 PR3 — the Training-management workflow in a real browser.

The unit and contract tests prove the rules. These prove the PAGE: the actual
rendered plan.html, the actual dispatcher, the actual scripts, and the actual
Flask responses — with only the plan GENERATOR stubbed, because a provider call
is the one thing a hermetic test may not make. Every write below is a real
``POST /training-plan/save`` against the real destructive route, and every
"unchanged" assertion is read back out of the database.

The routing fixture is the one Sprint 14 PR3 established
(``tests/test_training_execution_boundary.py``): browser HTTP is served by the
authenticated Flask test client, so cookies, CSRF and canonical state are real.
"""
import json

import pytest
from playwright.sync_api import expect

from app.blueprints import training as training_bp
from app.extensions import db
from app.models import TrainingPlan, User, UserSession
from app.services.training_generation.exercise_context_token import (
    ExerciseContext, sign_exercise_context,
)
from test_sprint11_training_generation_output import _week
from test_training_execution_boundary import training_page  # noqa: F401  (fixture)

PLAN_URL = "http://localhost/training"


@pytest.fixture
def plan_v2(app):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    yield app
    app.config["UIUX_PLAN_V2_ENABLED"] = False


@pytest.fixture
def profile_ready(app, auth_user):
    """A user the canonical generator will actually serve.

    `POST /training-plan` refuses with TRAINING_PLAN_NO_SESSION before it looks
    at anything else when the user has no profile session, so seeding one is
    what makes these flows exercise the workflow instead of that guard.
    """
    with app.app_context():
        db.session.get(User, auth_user.id).profile_complete = True
        db.session.add(UserSession(
            user_id=auth_user.id, goal="kas kazanma",
            fitness_level="intermediate", current_activity="active", tdee=2600))
        db.session.commit()
    return auth_user


@pytest.fixture
def generator(app, auth_user, monkeypatch):
    """Stub ONLY the provider-backed generator.

    The returned payload is the real generated-plan shape carrying a REAL signed
    exercise context, so the save boundary it feeds still performs its full
    structure / semantics / catalog / equipment validation.
    """
    state = {"calls": 0, "fail": False, "focus": "Full Body"}

    def fake(*args, **kwargs):
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("provider unavailable")
        document = _week()
        for day in document["program"]:
            if day["tip"] == "antrenman":
                day["odak"] = state["focus"]
        return {
            "program": document["program"],
            "haftalik_ozet": document["haftalik_ozet"],
            "overall_score": 8.0,
            "score_label": "İyi",
            "exercise_context_token": sign_exercise_context(
                ExerciseContext(equipment_context="spor_salonu",
                                cardio_type="yok", style="genel"),
                app.config["SECRET_KEY"], auth_user.id),
        }

    monkeypatch.setattr(training_bp, "generate_training_plan_payload", fake)
    return state


def _seed_plan(user_id, focus="Original Focus"):
    document = _week()
    for day in document["program"]:
        if day["tip"] == "antrenman":
            day["odak"] = focus
    plan = TrainingPlan(user_id=user_id, score=6.0,
                        plan_data=json.dumps(document, ensure_ascii=False))
    db.session.add(plan)
    db.session.commit()
    return plan


def _stored(app, user_id):
    with app.app_context():
        row = TrainingPlan.query.filter_by(user_id=user_id).one()
        return row.lineage_id, json.loads(row.plan_data)


def _generate(page):
    page.locator("[data-plan-manage-generate]").click()
    expect(page.locator("[data-plan-manage-proposal]")).to_be_visible()


# ══════════════════════════════════════════════════════════════════════════
# 1. No plan → create → generate → save → the SERVER renders the active plan
# ══════════════════════════════════════════════════════════════════════════

def test_creation_flow_ends_in_a_server_rendered_active_plan(
        app, plan_v2, profile_ready, generator, training_page):
    page, traffic, _, _ = training_page
    page.goto(PLAN_URL)
    expect(page.locator('[data-manage-state="create"]')).to_be_visible()

    _generate(page)
    # The proposal exists and the page still says no plan is active.
    assert page.locator('[data-plan-state="no_active_plan"]').count() == 1
    with app.app_context():
        assert TrainingPlan.query.filter_by(user_id=profile_ready.id).count() == 0

    page.locator("[data-plan-manage-confirm]").click()
    expect(page.locator('[data-plan-state="active_plan"]')).to_be_visible()

    lineage, document = _stored(app, profile_ready.id)
    assert lineage
    assert document["program"][0]["odak"] == "Full Body"
    # Freshness was proven before the write, the write happened once, and the
    # success claim came only after a canonical read that CONFIRMED the change.
    paths = [path for path, _, _ in traffic]
    assert paths.count("/training-plan/save") == 1
    save_at = paths.index("/training-plan/save")
    assert "/training/bootstrap" in paths[:save_at]
    assert "/training/bootstrap" in paths[save_at + 1:]


# ══════════════════════════════════════════════════════════════════════════
# 2/3. Regeneration: proposal, cancel, and explicit confirmed replacement
# ══════════════════════════════════════════════════════════════════════════

def test_a_proposal_never_replaces_the_plan_and_cancelling_changes_nothing(
        app, plan_v2, profile_ready, generator, training_page):
    page, traffic, _, _ = training_page
    with app.app_context():
        _seed_plan(profile_ready.id)
    before = _stored(app, profile_ready.id)

    page.goto(PLAN_URL)
    page.locator("[data-plan-manage-open]").click()
    _generate(page)

    # The active plan is still on the page, and the proposal is labelled as one.
    expect(page.locator('[data-plan-state="active_plan"]')).to_be_visible()
    assert "Original Focus" in page.content()
    assert _stored(app, profile_ready.id) == before

    page.locator('[data-action="planManageClose"]').click()
    expect(page.locator("[data-plan-manage-proposal]")).to_be_hidden()
    assert _stored(app, profile_ready.id) == before
    assert not any(path == "/training-plan/save" for path, _, _ in traffic)


def test_replacement_requires_the_confirmation_and_then_shows_canonical_state(
        app, plan_v2, profile_ready, generator, training_page):
    page, traffic, _, _ = training_page
    with app.app_context():
        _seed_plan(profile_ready.id)
    before_lineage, _ = _stored(app, profile_ready.id)

    page.goto(PLAN_URL)
    page.locator("[data-plan-manage-open]").click()
    _generate(page)
    generator["focus"] = "Full Body"

    # Confirm opens the destructive dialog; it does NOT write.
    page.locator("[data-plan-manage-confirm]").click()
    expect(page.locator("[data-plan-replace-confirm]")).to_be_visible()
    assert not any(path == "/training-plan/save" for path, _, _ in traffic)
    assert _stored(app, profile_ready.id)[0] == before_lineage

    page.locator('[data-action="planManageReplace"]').click()
    expect(page.locator('[data-plan-state="active_plan"]')).to_be_visible()

    # The canonical re-render replaced the old program with the new one.
    expect(page.get_by_text("Original Focus")).to_have_count(0)
    expect(page.get_by_text("Full Body").first).to_be_visible()

    after_lineage, document = _stored(app, profile_ready.id)
    assert after_lineage != before_lineage          # a real replacement
    assert document["program"][0]["odak"] == "Full Body"
    with app.app_context():
        assert TrainingPlan.query.filter_by(user_id=profile_ready.id).count() == 1


def test_dismissing_the_confirmation_writes_nothing(
        app, plan_v2, profile_ready, generator, training_page):
    page, traffic, _, _ = training_page
    with app.app_context():
        _seed_plan(profile_ready.id)
    before = _stored(app, profile_ready.id)

    page.goto(PLAN_URL)
    page.locator("[data-plan-manage-open]").click()
    _generate(page)
    page.locator("[data-plan-manage-confirm]").click()
    expect(page.locator("[data-plan-replace-confirm]")).to_be_visible()

    page.keyboard.press("Escape")
    expect(page.locator("[data-plan-replace-confirm]")).to_be_hidden()
    assert not any(path == "/training-plan/save" for path, _, _ in traffic)
    assert _stored(app, profile_ready.id) == before


# ══════════════════════════════════════════════════════════════════════════
# 4. A plan that moved under the page (the Coach-mutation shape)
# ══════════════════════════════════════════════════════════════════════════

def test_a_plan_changed_elsewhere_refuses_the_replacement_without_writing(
        app, plan_v2, profile_ready, generator, training_page):
    """The page renders against version N; an authorized mutation moves the
    server to N+1 before the user confirms. The replacement must be refused —
    with no save request at all — and the user told to review the current plan.
    """
    page, traffic, _, _ = training_page
    with app.app_context():
        _seed_plan(profile_ready.id)

    page.goto(PLAN_URL)
    page.locator("[data-plan-manage-open]").click()
    _generate(page)

    # A Coach plan mutation lands while the proposal is on screen.
    with app.app_context():
        from app.services.plan_mutation import (
            ACTOR_AI_COACH, MutationContext, RemoveExerciseCommand,
            apply_plan_mutation,
        )
        apply_plan_mutation(
            profile_ready.id,
            RemoveExerciseCommand(day="Pazartesi", exercise="Push-up"),
            MutationContext(idempotency_key="pr3-browser-0001",
                            actor=ACTOR_AI_COACH))
        db.session.commit()
    before = _stored(app, profile_ready.id)

    page.locator("[data-plan-manage-confirm]").click()
    page.locator('[data-action="planManageReplace"]').click()
    expect(page.locator("[data-plan-manage-msg]")).to_be_visible()

    assert not any(path == "/training-plan/save" for path, _, _ in traffic)
    assert _stored(app, profile_ready.id) == before
    assert "plan-manage-msg--error" in page.locator(
        "[data-plan-manage-msg]").get_attribute("class")


# ══════════════════════════════════════════════════════════════════════════
# 5/6. Failure semantics: the current plan survives, the proposal stays one
# ══════════════════════════════════════════════════════════════════════════

def test_generation_failure_leaves_the_current_plan_untouched(
        app, plan_v2, profile_ready, generator, training_page):
    page, traffic, _, _ = training_page
    with app.app_context():
        _seed_plan(profile_ready.id)
    before = _stored(app, profile_ready.id)
    generator["fail"] = True

    page.goto(PLAN_URL)
    page.locator("[data-plan-manage-open]").click()
    page.locator("[data-plan-manage-generate]").click()
    expect(page.locator("[data-plan-manage-msg]")).to_be_visible()

    expect(page.locator("[data-plan-manage-proposal]")).to_be_hidden()
    assert not any(path == "/training-plan/save" for path, _, _ in traffic)
    assert _stored(app, profile_ready.id) == before
    assert "Original Focus" in page.content()


def test_a_failed_save_never_lets_the_proposal_masquerade_as_active(
        app, plan_v2, profile_ready, generator, training_page, monkeypatch):
    page, traffic, _, _ = training_page
    with app.app_context():
        _seed_plan(profile_ready.id)
    before = _stored(app, profile_ready.id)

    page.goto(PLAN_URL)
    page.locator("[data-plan-manage-open]").click()
    _generate(page)

    # Make the destructive route reject AFTER the proposal exists, through the
    # route's own typed failure path (a raw exception would never reach the
    # browser as a response and would test the harness, not the workflow).
    from app.services.training_generation.output_errors import PlanValidationError

    def reject(*args, **kwargs):
        raise PlanValidationError("rejected for this test")

    monkeypatch.setattr(training_bp, "validate_plan_for_save", reject)

    page.locator("[data-plan-manage-confirm]").click()
    page.locator('[data-action="planManageReplace"]').click()
    expect(page.locator("[data-plan-manage-msg]")).to_be_visible()

    assert _stored(app, profile_ready.id) == before
    # The page still shows the plan the server owns, not the proposal.
    expect(page.locator('[data-plan-state="active_plan"]')).to_be_visible()
    assert "Original Focus" in page.content()
    assert any(path == "/training-plan/save" for path, _, _ in traffic)


# ══════════════════════════════════════════════════════════════════════════
# 5b. Coach mutation → returning to Plan shows the CANONICAL truth
# ══════════════════════════════════════════════════════════════════════════

def test_returning_to_plan_after_a_coach_mutation_surfaces_the_change(
        app, plan_v2, profile_ready, generator, training_page):
    """Plan must not keep showing a superseded program indefinitely.

    The page learns nothing from Coach copy and renders no invented plan: it
    re-reads the canonical identity on return and, when it moved, says the page
    is out of date and offers the reload that lets the SERVER re-render.
    """
    page, traffic, _, _ = training_page
    with app.app_context():
        _seed_plan(profile_ready.id)

    page.goto(PLAN_URL)
    notice = page.locator("[data-plan-stale-notice]")
    expect(notice).to_be_hidden()

    with app.app_context():
        from app.services.plan_mutation import (
            ACTOR_AI_COACH, MutationContext, RemoveExerciseCommand,
            apply_plan_mutation,
        )
        apply_plan_mutation(
            profile_ready.id,
            RemoveExerciseCommand(day="Pazartesi", exercise="Push-up"),
            MutationContext(idempotency_key="pr3-coach-000001",
                            actor=ACTOR_AI_COACH))
        db.session.commit()

    # Coming back to the page is the trigger — not a poll, not a chat message.
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(notice).to_be_visible()

    # Nothing was written, and the page never claimed to know the new plan.
    assert not any(path == "/training-plan/save" for path, _, _ in traffic)
    assert "Original Focus" in page.content()


def test_an_unchanged_plan_never_claims_the_page_is_stale(
        app, plan_v2, profile_ready, generator, training_page):
    page, _, _, _ = training_page
    with app.app_context():
        _seed_plan(profile_ready.id)

    page.goto(PLAN_URL)
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    page.wait_for_timeout(300)
    expect(page.locator("[data-plan-stale-notice]")).to_be_hidden()

# ══════════════════════════════════════════════════════════════════════════
# 7. Accessibility and responsive behaviour
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("width,height", [
    (320, 720), (390, 844), (768, 1024), (1024, 768), (1366, 900)])
def test_the_management_workflow_never_overflows_horizontally(
        app, plan_v2, profile_ready, generator, training_page, width, height):
    page, _, _, _ = training_page
    with app.app_context():
        _seed_plan(profile_ready.id)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(PLAN_URL)
    page.locator("[data-plan-manage-open]").click()
    _generate(page)
    page.locator("[data-plan-manage-confirm]").click()
    expect(page.locator("[data-plan-replace-confirm]")).to_be_visible()

    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
    # The confirmation genuinely overlays the viewport rather than rendering as a
    # clipped block inside the Training placement it is nested in.
    box = page.locator("[data-plan-replace-confirm]").bounding_box()
    assert box["width"] >= width - 1 and box["x"] <= 1, box
    card = page.locator(".plan-replace-card").bounding_box()
    assert card["width"] <= width, card


def test_the_destructive_dialog_is_keyboard_complete_and_restores_focus(
        app, plan_v2, profile_ready, generator, training_page):
    page, traffic, _, _ = training_page
    with app.app_context():
        _seed_plan(profile_ready.id)

    page.goto(PLAN_URL)
    page.locator("[data-plan-manage-open]").click()
    _generate(page)
    page.locator("[data-plan-manage-confirm]").click()

    # Focus moves into the dialog, and onto the NON-destructive control.
    focused = page.evaluate("document.activeElement.getAttribute('data-plan-replace-cancel')")
    assert focused is not None
    # Tab stays inside the dialog.
    page.keyboard.press("Tab")
    assert page.evaluate(
        "document.activeElement.closest('[data-plan-replace-confirm]') !== null")

    page.keyboard.press("Escape")
    assert not any(path == "/training-plan/save" for path, _, _ in traffic)
    # Focus returns to the control that opened the dialog.
    assert page.evaluate(
        "document.activeElement.hasAttribute('data-plan-manage-confirm')")


def test_english_locale_renders_the_management_workflow(
        app, plan_v2, profile_ready, generator, training_page):
    """TR is the default; EN comes from the user's own `language`, not a query
    string, so the test sets the canonical source rather than a URL parameter."""
    page, _, _, _ = training_page
    copy = json.loads((__import__("pathlib").Path(__file__).resolve().parents[1]
                       / "locales" / "en.json").read_text(encoding="utf-8"))
    with app.app_context():
        db.session.get(User, profile_ready.id).language = "en"
        _seed_plan(profile_ready.id)
        db.session.commit()

    page.goto(PLAN_URL)
    open_button = page.locator("[data-plan-manage-open]")
    expect(open_button).to_have_text(copy["plan.manage.regenerate"])
    open_button.click()
    page.locator("[data-plan-manage-generate]").click()
    expect(page.locator("[data-plan-manage-proposal]")).to_be_visible()
    expect(page.locator("[data-plan-manage-confirm]")).to_have_text(
        copy["plan.manage.replace"])
    page.locator("[data-plan-manage-confirm]").click()
    # The destructive dialog says "replace", never a vague "save".
    expect(page.locator("[data-plan-replace-confirm] .btn-volt")).to_have_text(
        copy["plan.manage.confirm.replace"])
    expect(page.locator("[data-plan-replace-cancel]")).to_have_text(
        copy["plan.manage.keep_current"])
    assert "plan.manage." not in page.locator("[data-plan-replace-confirm]").inner_text()


def test_an_actionable_plan_reuses_the_execution_clients_canonical_read(
        app, plan_v2, profile_ready, generator, training_page):
    """Two clients live on an actionable Plan page; they must not both poll.

    The execution client already refreshes `/training/bootstrap` on return, so
    the management renderer answers its freshness question from that reading
    instead of issuing a second one — and still detects the change.
    """
    from test_sprint14_workout_execution_contract import SQUAT, BENCH
    page, traffic, _, _ = training_page
    with app.app_context():
        _seed_executable_plan(profile_ready.id, exercise_ids=(SQUAT, BENCH))

    page.goto(PLAN_URL)
    expect(page.locator('[data-action="startWorkout"]')).to_be_visible()
    expect(page.locator("[data-plan-stale-notice]")).to_be_hidden()
    baseline_reads = sum(1 for path, _, _ in traffic if path == "/training/bootstrap")

    # Any authorized actor moving the plan is the same signal; a wholesale
    # replacement is the cheapest one to stage here (the Coach-mutation shape is
    # covered by its own test above).
    with app.app_context():
        TrainingPlan.query.filter_by(user_id=profile_ready.id).delete()
        db.session.commit()
        _seed_executable_plan(profile_ready.id)

    page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(page.locator("[data-plan-stale-notice]")).to_be_visible()

    reads = sum(1 for path, _, _ in traffic if path == "/training/bootstrap")
    assert reads == baseline_reads + 1, "the freshness check must reuse the one read"


def _seed_executable_plan(user_id, exercise_ids=None, names=None):
    from test_sprint14_workout_execution_contract import (
        SQUAT, BENCH, save_workout_plan,
    )
    plan = save_workout_plan(user_id, exercise_ids=exercise_ids or (SQUAT, BENCH),
                             names=names)
    document = json.loads(plan.plan_data)
    for day in document["program"]:
        day.update(odak="Strength", sure_dk=30, tahmini_kalori=150)
        for exercise in day["egzersizler"]:
            exercise.update(set=1, tekrar="8", dinlenme="60 sn")
    plan.plan_data = json.dumps(document, ensure_ascii=False)
    db.session.commit()
    return plan



def test_a_replaced_plan_does_not_let_an_old_browser_draft_resurrect(
        app, plan_v2, profile_ready, generator, training_page, sessions_on):
    """#285/#288 invariant, re-proved for the operation PR3 makes reachable.

    A draft belonging to a superseded plan must not survive the replacement.
    The canonical refresh stops reporting a resumable session, the execution
    client clears its checkpoint state, and the session view closes carrying
    nothing forward.
    """
    from test_sprint14_workout_execution_contract import ROW, SQUAT, BENCH
    page, traffic, _, _ = training_page
    with app.app_context():
        _seed_executable_plan(profile_ready.id, exercise_ids=(SQUAT, BENCH))

    page.goto(PLAN_URL)
    page.locator('[data-action="startWorkout"]').click()
    expect(page.locator("#session-view")).to_have_class("session-view open")
    page.locator('#sv-body [data-field="weight"]').first.fill("82.5")
    page.locator('#sv-body [data-field="done"]').first.click()
    expect(page.locator('#sv-body .set-row[data-ex="0"][data-set="0"]')).to_have_class(
        __import__("re").compile(r"\bis-done\b"))

    # The plan is replaced underneath the running workout.
    replacement_marker = len(traffic)
    with app.app_context():
        TrainingPlan.query.filter_by(user_id=profile_ready.id).delete()
        db.session.commit()
        _seed_executable_plan(profile_ready.id, exercise_ids=(ROW,), names=["Row"])

    # Returning to the page runs the canonical refresh (visibilitychange is not
    # throttled, unlike focus).
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator("#session-view")).not_to_have_class("session-view open")

    state = page.evaluate(
        "fetch('/training/bootstrap').then(r => r.json()).then(d => d.workout.state)")
    assert state["session_state"] == "active_blocked"
    assert state["session"]["stale_reason"] == "plan_regenerated_or_replaced"

    # No entry back into the superseded workout, and — the load-bearing part —
    # the client stops writing: not one checkpoint carrying the old draft is
    # issued after the plan was replaced.
    expect(page.locator('[data-action="startWorkout"]')).to_be_hidden()
    checkpoints_after = [
        path for path, _, _ in traffic[replacement_marker:]
        if path.endswith("/checkpoint")
    ]
    assert checkpoints_after == [], checkpoints_after


@pytest.fixture
def sessions_on(app):
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    yield app
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False
