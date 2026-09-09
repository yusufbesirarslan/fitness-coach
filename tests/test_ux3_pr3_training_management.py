"""UX-3 PR3 — Training placement + regeneration convergence.

PR3's whole claim is that Training plan creation, regeneration and
mutation-refresh become ONE coherent, canonical, version-safe workflow inside
the Plan shell. These tests hold that claim to its load-bearing parts:

* create and regenerate are DIFFERENT operations with different confirmations;
* a generated proposal is never a persisted active plan;
* a replacement is bound to the frozen ``proposal_origin`` captured at generate
  time and sent as ``expected_plan`` (PR #293); a later fresh read never upgrades
  that authority; a typed 409 does not auto-retry;
* a Coach mutation becomes visible through canonical refresh, never through
  Coach prose;
* an active session that a replacement invalidates is surfaced truthfully and
  never converted into rest / completed / resume;
* the flag-OFF legacy path keeps every capability it had.

The browser half of the contract lives in
``tests/js/training_plan_management.test.js`` and is executed from here so CI —
which runs pytest only — actually enforces it.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from app.extensions import db
from app.models import TrainingPlan
from app.plan_presenter import (
    MANAGE_CREATE,
    MANAGE_REGENERATE,
    MANAGE_UNAVAILABLE,
    PlanDay,
    PlanFacts,
    build_plan_view,
)

ROOT = Path(__file__).resolve().parents[1]

_PLAN_DOC = {
    "program": [
        {"gun": "Pazartesi", "tip": "guc", "odak": "Push", "sure_dk": 45,
         "tahmini_kalori": 300,
         "egzersizler": [{"isim": "Bench Press", "set": "3", "tekrar": "8"}]},
        {"gun": "Salı", "tip": "dinlenme", "egzersizler": []},
    ],
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _login(client, make_user, login, username="pr3user"):
    user = make_user(username, profile_complete=True)
    login(username)
    return user


def _seed_plan(user_id, document=None):
    plan = TrainingPlan(
        user_id=user_id, score=7.5,
        plan_data=json.dumps(document or _PLAN_DOC, ensure_ascii=False))
    db.session.add(plan)
    db.session.commit()
    return plan


def _facts(**overrides):
    base = dict(read_ok=True, has_active_plan=True, parse_ok=True,
                days=(PlanDay(label="Pazartesi"),))
    base.update(overrides)
    return PlanFacts(**base)


def _seed_executable_plan(user_id, exercise_ids=None, names=None):
    """Persist a plan whose TODAY is a real, fully projectable workout.

    ``save_workout_plan`` from the Sprint 14 suite writes the canonical shape
    but omits the presentation fields ``/training/bootstrap`` requires, so it is
    enriched here exactly the way that suite's own execution fixture does.
    """
    from test_sprint14_workout_execution_contract import SQUAT, BENCH, save_workout_plan
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


# ══════════════════════════════════════════════════════════════════════════
# A. The browser contract, executed for real (so CI enforces it)
# ══════════════════════════════════════════════════════════════════════════

def test_shared_training_management_contract_passes_in_node():
    """Run the real module under node.

    Everything the browser half guarantees — proposal never persisted, stale
    replacement refused without a write, failed save claims nothing, a write
    that cannot be confirmed is not reported as done — is asserted there against
    recorded HTTP traffic. Running it here is what makes those guarantees
    gate CI, which otherwise runs pytest only.
    """
    result = subprocess.run(
        ["node", "--test", "tests/js/training_plan_management.test.js"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "fail 0" in result.stdout, result.stdout
    assert "pass " in result.stdout, result.stdout


# ══════════════════════════════════════════════════════════════════════════
# B. Canonical plan identity is published, and it MOVES when the plan does
# ══════════════════════════════════════════════════════════════════════════

def test_bootstrap_publishes_the_canonical_plan_identity(app, client, make_user, login):
    user = _login(client, make_user, login)
    plan = _seed_plan(user.id)
    expected = (plan.lineage_id, plan.mutation_version)

    payload = client.get("/training/bootstrap").get_json()["plan"]

    assert payload["exists"] is True
    assert (payload["lineage_id"], payload["mutation_version"]) == expected
    # The identity is a freshness fact, not persistence internals.
    assert "id" not in payload and "user_id" not in payload


def test_active_plan_endpoint_publishes_the_same_identity(app, client, make_user, login):
    user = _login(client, make_user, login)
    lineage = _seed_plan(user.id).lineage_id

    active = client.get("/training-plan/active").get_json()
    boot = client.get("/training/bootstrap").get_json()["plan"]

    assert active["lineage_id"] == boot["lineage_id"] == lineage
    assert active["mutation_version"] == boot["mutation_version"]


def test_absent_plan_reports_absence_rather_than_a_null_identity(client, make_user, login):
    _login(client, make_user, login)
    payload = client.get("/training/bootstrap").get_json()["plan"]
    assert payload == {"exists": False}


def test_a_targeted_mutation_moves_the_published_version(app, client, make_user, login):
    """A Coach plan mutation is exactly what the freshness check must notice."""
    from app.services.plan_mutation import (
        ACTOR_AI_COACH, MutationContext, RemoveExerciseCommand, apply_plan_mutation,
    )
    user = _login(client, make_user, login)
    lineage = _seed_plan(user.id, {"program": [
        {"gun": "Pazartesi", "tip": "guc",
         "egzersizler": [{"isim": "Bench Press", "set": 3, "tekrar": "8"},
                         {"isim": "Incline Press", "set": 3, "tekrar": "8"}]},
        {"gun": "Salı", "tip": "dinlenme", "egzersizler": []},
    ]}).lineage_id
    before = client.get("/training/bootstrap").get_json()["plan"]

    apply_plan_mutation(
        user.id,
        RemoveExerciseCommand(day="Pazartesi", exercise="Incline Press"),
        MutationContext(idempotency_key="pr3-op-000001", actor=ACTOR_AI_COACH),
    )
    db.session.commit()

    after = client.get("/training/bootstrap").get_json()["plan"]
    # Same lineage (targeted edit), higher version — the page's rendered
    # identity is now provably stale without reading a single word of Coach copy.
    assert after["lineage_id"] == before["lineage_id"] == lineage
    assert after["mutation_version"] == before["mutation_version"] + 1


def test_a_whole_plan_replacement_moves_the_published_lineage(app, client, make_user, login):
    user = _login(client, make_user, login)
    _seed_plan(user.id)
    before = client.get("/training/bootstrap").get_json()["plan"]

    # The canonical replacement path: delete every row, insert a fresh lineage.
    TrainingPlan.query.filter_by(user_id=user.id).delete()
    _seed_plan(user.id)

    after = client.get("/training/bootstrap").get_json()["plan"]
    assert after["lineage_id"] != before["lineage_id"]


# ══════════════════════════════════════════════════════════════════════════
# C. Create and regenerate are DIFFERENT operations (pure presenter)
# ══════════════════════════════════════════════════════════════════════════

def test_management_state_is_distinct_per_page_state():
    cases = {
        MANAGE_UNAVAILABLE: _facts(read_ok=False, has_active_plan=False,
                                   parse_ok=False, days=()),
        MANAGE_CREATE: _facts(has_active_plan=False, parse_ok=False, days=()),
        MANAGE_REGENERATE: _facts(),
    }
    for expected, facts in cases.items():
        assert build_plan_view(facts).management_state == expected


def test_an_unreadable_plan_is_still_replaced_not_created():
    """Malformed plan data must not downgrade a destructive replacement into a
    safe creation — that is how an existing plan gets silently overwritten."""
    view = build_plan_view(_facts(parse_ok=False, days=()))
    assert view.management_state == MANAGE_REGENERATE
    assert view.management_baseline["present"] is True


def test_a_failed_read_offers_no_management_at_all():
    view = build_plan_view(_facts(read_ok=False, has_active_plan=False,
                                  parse_ok=False, days=()))
    assert view.management_state == MANAGE_UNAVAILABLE
    assert view.management_baseline == {"present": False}


def test_the_baseline_carries_the_server_identity_verbatim():
    view = build_plan_view(_facts(
        plan_revision={"plan_lineage": "lin-1", "mutation_version": 4}))
    assert view.management_baseline == {
        "present": True, "plan_lineage": "lin-1", "mutation_version": 4}


def test_a_plan_present_without_a_readable_identity_stays_present():
    """`present` comes from the read layer, never from the revision. Collapsing
    an unidentifiable plan to "no plan" would let it be replaced silently."""
    view = build_plan_view(_facts(plan_revision=None))
    assert view.management_baseline == {"present": True}


def test_regeneration_is_never_a_secondary_navigation_link():
    view = build_plan_view(_facts())
    assert all(action.href not in ("/training", "/training-plan")
               for action in view.secondary)
    assert view.primary is None


# ══════════════════════════════════════════════════════════════════════════
# D. Rendering: one placement, two operations
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def plan_v2(app):
    app.config["UIUX_PLAN_V2_ENABLED"] = True
    yield app
    app.config["UIUX_PLAN_V2_ENABLED"] = False


def test_active_plan_offers_an_explicit_destructive_regeneration(
        plan_v2, client, make_user, login):
    user = _login(client, make_user, login)
    plan = _seed_plan(user.id)
    lineage, version = plan.lineage_id, plan.mutation_version

    html = client.get("/training").get_data(as_text=True)

    assert 'data-manage-state="regenerate"' in html
    assert "data-plan-manage-open" in html            # entered deliberately
    assert "data-plan-replace-confirm" in html        # explicit confirmation
    assert "data-plan-manage-confirm" in html
    # The page hands back the server's own identity for the freshness check.
    assert _rendered_baseline(html) == {
        "present": True, "plan_lineage": lineage, "mutation_version": version}


def test_regeneration_panel_starts_closed_so_opening_it_replaces_nothing(
        plan_v2, client, make_user, login):
    user = _login(client, make_user, login)
    _seed_plan(user.id)
    html = client.get("/training").get_data(as_text=True)
    panel = html.index("data-plan-manage-panel")
    assert "hidden" in html[panel:panel + 120]
    # The proposal region ships hidden too: nothing is proposed on page load.
    proposal = html.index("data-plan-manage-proposal")
    assert "hidden" in html[proposal:proposal + 80]


def test_creation_has_no_replacement_confirmation_and_an_open_panel(
        plan_v2, client, make_user, login):
    _login(client, make_user, login)                  # no plan
    html = client.get("/training").get_data(as_text=True)

    assert 'data-manage-state="create"' in html
    assert "data-plan-replace-confirm" not in html    # nothing to destroy
    assert "data-plan-manage-open" not in html
    panel = html.index("data-plan-manage-panel")
    assert "hidden" not in html[panel:panel + 60]
    assert _rendered_baseline(html) == {"present": False}


def test_a_read_error_ships_no_management_client_at_all(
        plan_v2, client, make_user, login, monkeypatch):
    _login(client, make_user, login)
    monkeypatch.setattr("app.services.plan_facts.get_active_plan",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))

    html = client.get("/training").get_data(as_text=True)

    assert 'data-plan-state="read_error"' in html
    assert "data-plan-manage" not in html
    assert "/static/training_plan_management.js" not in html
    assert "/static/plan_training_manage.js" not in html


def test_execution_outranks_regeneration_in_the_training_placement(
        plan_v2, client, make_user, login):
    """Start/Resume is the dominant action whenever it is offered; regeneration
    is a subordinate management affordance below the plan it would replace."""
    user = _login(client, make_user, login)
    _seed_executable_plan(user.id)

    html = client.get("/training").get_data(as_text=True)

    assert 'data-workout-action="start"' in html
    start_at = html.index('data-action="startWorkout"')
    manage_at = html.index("data-plan-manage-open")
    assert start_at < manage_at
    # Exactly one dominant CTA class in the Training placement region.
    region = html[html.index('data-plan-domain="training"'):manage_at]
    assert region.count('data-action="startWorkout"') == 1


def test_today_is_untouched_by_training_management(client, make_user, login, app):
    """Regeneration is plan management, not daily guidance (brief §24)."""
    user = _login(client, make_user, login)
    _seed_plan(user.id)
    html = client.get("/").get_data(as_text=True)
    assert "data-plan-manage" not in html
    assert "planManageOpen" not in html
    assert "/static/training_plan_management.js" not in html


def _rendered_baseline(html):
    marker = "data-plan-manage-bootstrap>"
    start = html.index(marker) + len(marker)
    return json.loads(html[start:html.index("</script>", start)])


# ══════════════════════════════════════════════════════════════════════════
# E. Active session + plan replacement — the discovered canonical contract
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sessions_on(app):
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    yield app
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False


def test_replacing_the_plan_makes_a_running_session_non_resumable(
        plan_v2, sessions_on, client, make_user, login, app):
    """Discovered behaviour, not an invented one: the server does not block a
    replacement during an active session — the session's stored plan fingerprint
    stops matching, so it classifies as `plan_regenerated_or_replaced` and is no
    longer resumable. Plan must show that, and must not offer Resume."""
    from test_sprint14_workout_execution_contract import (
        ROW, SQUAT, BENCH, start_session_over_http,
    )
    user = _login(client, make_user, login)
    _seed_executable_plan(user.id, exercise_ids=(SQUAT, BENCH))
    start_session_over_http(client)

    resumable = client.get("/training/bootstrap").get_json()["workout"]["state"]
    assert resumable["session_state"] == "active_resumable"
    assert resumable["action"] == "resume"

    # Canonical whole-plan replacement with a DIFFERENT workout.
    TrainingPlan.query.filter_by(user_id=user.id).delete()
    db.session.commit()
    _seed_executable_plan(user.id, exercise_ids=(ROW,), names=["Row"])

    after = client.get("/training/bootstrap").get_json()["workout"]["state"]
    assert after["session_state"] == "active_blocked"
    assert after["session"]["resumable"] is False
    assert after["session"]["stale_reason"] == "plan_regenerated_or_replaced"
    assert after["action"] == "blocked"
    assert after["primary_state"] == "needs_attention"

    html = client.get("/training").get_data(as_text=True)
    assert 'data-workout-action="none"' in html
    assert 'data-action="startWorkout"' not in html
    # Truthful: the reason is surfaced, never converted into rest/completed.
    assert 'data-plan-session-stale="plan_regenerated_or_replaced"' in html
    # Truthfully "needs attention" — never softened into rest or completed.
    # Compared against rendered COPY: every page embeds the full i18n
    # dictionary, so a key-string assertion here would be vacuous.
    import json as _json
    copy = _json.loads((ROOT / "locales" / "tr.json").read_text(encoding="utf-8"))
    visible = _visible_text(html)
    assert copy["plan.workout_state.needs_attention"] in visible
    assert copy["plan.workout_state.rest_day"] not in visible
    assert copy["plan.workout_state.completed"] not in visible


def test_a_stale_session_is_never_shown_as_rest_or_completed(
        plan_v2, sessions_on, client, make_user, login):
    """The rendered COPY is asserted, not the locale keys.

    Every page embeds the whole ``window.I18N`` dictionary, so asserting that a
    key string is absent from the HTML would pass (or fail) for reasons that
    have nothing to do with what the user sees. These assertions read the
    rendered state line and the rendered action instead.
    """
    import json as _json
    from test_sprint14_workout_execution_contract import (
        ROW, SQUAT, start_session_over_http,
    )
    copy = _json.loads(
        (ROOT / "locales" / "tr.json").read_text(encoding="utf-8"))
    user = _login(client, make_user, login)
    _seed_executable_plan(user.id, exercise_ids=(SQUAT,))
    start_session_over_http(client)
    TrainingPlan.query.filter_by(user_id=user.id).delete()
    db.session.commit()
    _seed_executable_plan(user.id, exercise_ids=(ROW,), names=["Row"])

    html = client.get("/training").get_data(as_text=True)
    rendered = re.search(
        r'<span class="plan-domain-state">([^<]*)</span>', html).group(1)

    assert rendered == copy["plan.workout_state.needs_attention"]
    assert rendered != copy["plan.workout_state.rest_day"]
    assert rendered != copy["plan.workout_state.completed"]
    # No resume affordance, and the stale reason is stated in the user's words.
    assert copy["plan.action.resume_workout"] not in _visible_text(html)
    assert copy["plan.session_stale.plan_regenerated_or_replaced"] in html


def _visible_text(html):
    """The document with its embedded i18n/bootstrap JSON blocks removed."""
    return re.sub(r"<script.*?</script>", "", html, flags=re.S)


def test_a_replacement_during_an_active_session_warns_before_confirming(
        plan_v2, sessions_on, client, make_user, login):
    from test_sprint14_workout_execution_contract import (
        SQUAT, start_session_over_http,
    )
    user = _login(client, make_user, login)
    _seed_executable_plan(user.id, exercise_ids=(SQUAT,))
    start_session_over_http(client)

    view = build_plan_view(_pr3_facts_for(user.id))
    assert view.session_conflict is True

    html = client.get("/training").get_data(as_text=True)
    assert "plan-replace-warning" in html


def test_no_session_means_no_warning_and_no_stale_note(
        plan_v2, sessions_on, client, make_user, login):
    user = _login(client, make_user, login)
    _seed_executable_plan(user.id)

    html = client.get("/training").get_data(as_text=True)
    assert "plan-replace-warning" not in html
    assert "data-plan-session-stale" not in html
    assert build_plan_view(_pr3_facts_for(user.id)).session_conflict is False


def test_with_sessions_off_no_session_facts_reach_the_placement(
        plan_v2, client, make_user, login):
    from test_sprint14_workout_execution_contract import SQUAT
    user = _login(client, make_user, login)
    _seed_executable_plan(user.id, exercise_ids=(SQUAT,))

    facts = _pr3_facts_for(user.id, sessions_enabled=False)
    # The v1 contract carries no session at all: no conflict, no stale reason,
    # and therefore no destructive-confirmation warning to render.
    assert facts.workout_session_state not in ("active_resumable", "active_blocked")
    assert facts.workout_session_stale_reason == ""
    assert build_plan_view(facts).session_conflict is False
    html = client.get("/training").get_data(as_text=True)
    assert "data-plan-session-stale" not in html
    assert "plan-replace-warning" not in html
    # Regeneration itself does NOT require the session flag.
    assert 'data-manage-state="regenerate"' in html


def _pr3_facts_for(user_id, sessions_enabled=True):
    from app.services.plan_facts import gather_plan_facts
    return gather_plan_facts(user_id, sessions_enabled=sessions_enabled)


# ══════════════════════════════════════════════════════════════════════════
# F. Flag boundaries and legacy rollback
# ══════════════════════════════════════════════════════════════════════════

def test_flag_off_legacy_keeps_generation_regeneration_and_the_shared_contract(
        client, make_user, login):
    user = _login(client, make_user, login)
    _seed_plan(user.id)

    html = client.get("/training").get_data(as_text=True)

    assert "data-plan-v2" not in html
    assert "/static/training.js" in html
    assert "/static/training_plan_management.js" in html   # shared, not copied
    for capability in ('data-action="generatePlan"', 'data-action="savePlan"',
                       'data-action="resetPlan"', 'data-action="startWorkout"'):
        assert capability in html, capability
    # PR3 does not smuggle the Plan renderer into the rollback path.
    assert "/static/plan_training_manage.js" not in html


def test_pr3_changes_no_rollout_flag_default():
    """PR3 neither enables nor retires a rollout flag. The registry is the
    authority on both defaults, so the guard reads it rather than a config
    attribute that could be absent for an unrelated reason."""
    from app.feature_flags import FLAGS_BY_KEY

    defaults = {key: flag.default for key, flag in FLAGS_BY_KEY.items()}
    assert defaults["UIUX_PLAN_V2_ENABLED"] is False
    assert defaults["FITX_WORKOUT_SESSIONS_ENABLED"] is False


def test_the_management_placement_never_requires_the_weekly_flag(
        plan_v2, app, client, make_user, login):
    user = _login(client, make_user, login)
    _seed_plan(user.id)
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = False

    html = client.get("/training").get_data(as_text=True)
    assert 'data-manage-state="regenerate"' in html
    assert "data-weekly-program-mount" not in html


# ══════════════════════════════════════════════════════════════════════════
# G. Cost: the landing stays provider-free and gains no plan query
# ══════════════════════════════════════════════════════════════════════════

def test_plan_landing_makes_no_provider_call_and_no_extra_plan_read(
        plan_v2, client, make_user, login, monkeypatch):
    import app.blueprints.training as training_bp

    calls = []
    monkeypatch.setattr(training_bp, "_heavy_chat",
                        lambda *a, **k: calls.append("provider"))

    reads = []
    from app.services import plan_facts as plan_facts_module
    original = plan_facts_module.get_active_plan

    def counted(*args, **kwargs):
        reads.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(plan_facts_module, "get_active_plan", counted)
    user = _login(client, make_user, login)
    _seed_plan(user.id)

    assert client.get("/training").status_code == 200
    assert calls == []
    # The identity is read off the row the shell already loaded — PR3 adds no
    # second active-plan query to the landing.
    assert len(reads) == 1
