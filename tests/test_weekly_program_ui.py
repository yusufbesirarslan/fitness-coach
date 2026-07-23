"""Server-side rollout contract for the Adaptive Weekly Program UI shell (Sprint 6 PR6.1).

Two guarantees, in order of importance:

1. **OFF is nothing.** With `WEEKLY_PROGRAM_UI_ENABLED` off, `/training` carries no
   shell, no mount attribute, no script tag, no spacing. The template omits the block
   server-side rather than rendering hidden markup, and whitespace-control markers keep
   the OFF document byte-identical to the pre-PR6.1 page (verified once during
   implementation with a normalized before/after diff; see the PR6.1 handoff).
2. **ON is only a mount point.** Exactly one inert `<section>` and one script tag —
   no recommendation values, no payload, no user id, no fake loading/empty/error copy.
   PR6.1 is not a data consumer; the endpoint is never called from here.

Note for future readers: presence/absence must be asserted with precise markers
(`id="weekly-program"`, `data-weekly-program-mount`, the asset path). The bare
substring `weekly_program` is already on every page — `_head.html` injects the whole
locale catalog and `locales/{tr,en}.json` carries an unused `training.weekly_program`
key. See tests/test_training_page_characterization.py.

    python -m pytest tests/test_weekly_program_ui.py -v
"""
import ast
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TRAINING_BLUEPRINT = ROOT / "app" / "blueprints" / "training.py"
PAGE_VIEW_NAME = "training"

SHELL_ID = 'id="weekly-program"'
MOUNT_ATTR = "data-weekly-program-mount"
SCRIPT_PATH = "/static/weekly_program.js"

# The published WeeklyProgramRecommendation surface (app/services/weekly_program/) —
# none of it may reach the page in PR6.1.
RECOMMENDATION_FIELDS = [
    "week_focus", "volume_action", "intensity_action", "volume_delta_pct",
    "overload_ready", "maintenance_recommended", "baseline_weekly_volume",
    "target_weekly_volume", "reason_codes", "explanation_keys",
    "unsupported_capabilities", "has_data",
]


@pytest.fixture
def render_training(client, make_user, login):
    """Render `/training` for a fresh signed-in user at a given flag state."""
    counter = {"n": 0}

    def _render(app_obj, *, enabled=False, coach_flag=False):
        counter["n"] += 1
        username = "wpui%d" % counter["n"]
        app_obj.config["WEEKLY_PROGRAM_UI_ENABLED"] = enabled
        app_obj.config["AI_ADAPTIVE_PLAN_CONTEXT"] = coach_flag
        make_user(username, profile_complete=True)
        login(username)
        response = client.get("/training")
        assert response.status_code == 200
        return response.get_data(as_text=True)

    return _render


# ── OFF path ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("marker", [SHELL_ID, MOUNT_ATTR, SCRIPT_PATH])
def test_off_renders_no_weekly_program_surface(app, render_training, marker):
    assert marker not in render_training(app, enabled=False)


def test_off_leaves_the_existing_page_intact(app, render_training):
    html = render_training(app, enabled=False)
    for marker in ('id="active-plan-view"', 'id="setup-form"', 'id="workout-hero"',
                   'id="week-strip"', 'id="wstats"', 'class="apv-meta-row"',
                   'data-action="startWorkout"', 'data-action="generatePlan"',
                   "/static/training.js"):
        assert marker in html, marker


def test_off_adds_no_markup_between_weekly_stats_and_the_plan_meta_row(app, render_training):
    """The mount sits between #wstats and .apv-meta-row; OFF must leave that seam —
    including its whitespace — exactly as it was, so no layout spacing can appear."""
    html = render_training(app, enabled=False)
    seam = html[html.index('id="wstats"'):html.index('class="apv-meta-row"')]
    assert seam.endswith('></div>\n\n        <!-- Plan meta + reset -->\n        <div ')


# ── ON path ─────────────────────────────────────────────────────────────────────

def test_on_renders_exactly_one_shell(app, render_training):
    html = render_training(app, enabled=True)
    assert html.count(SHELL_ID) == 1
    assert html.count(MOUNT_ATTR) == 1


def test_on_loads_the_initializer_exactly_once(app, render_training):
    html = render_training(app, enabled=True)
    assert html.count(SCRIPT_PATH) == 1
    assert re.search(r'<script src="/static/weekly_program\.js\?v=\d+"></script>', html)


def test_on_shell_is_empty_and_semantic(app, render_training):
    """A mount point, not a card: a semantic container with no children, no text, no
    heading, no focusable control, and no announcement while it has no data."""
    html = render_training(app, enabled=True)
    match = re.search(r"<section[^>]*\bdata-weekly-program-mount\b[^>]*>(.*?)</section>",
                      html, re.DOTALL)
    assert match, "mount shell not found"
    assert match.group(1).count("data-weekly-program-copy") == 1
    assert '<script type="application/json"' in match.group(1)
    assert not re.search(r"<(?:button|a|input)\b", match.group(1))
    assert 'aria-hidden="true"' in match.group(0)
    assert "aria-live" not in match.group(0)
    assert "role=" not in match.group(0)


def test_on_shell_sits_inside_the_active_plan_view(app, render_training):
    """Chosen information architecture: the weekly overview region of the existing
    training destination, never the plan-creation form and never a new page."""
    html = render_training(app, enabled=True)
    assert html.index('id="active-plan-view"') < html.index(MOUNT_ATTR)
    assert html.index('id="wstats"') < html.index(MOUNT_ATTR)
    assert html.index(MOUNT_ATTR) < html.index('class="apv-meta-row"')
    assert html.index(MOUNT_ATTR) < html.index('id="setup-form"')


@pytest.mark.parametrize("field", RECOMMENDATION_FIELDS)
def test_on_embeds_no_recommendation_data(app, render_training, field):
    html = render_training(app, enabled=True)
    without_copy = re.sub(
        r'<script type="application/json" data-weekly-program-copy>.*?</script>',
        "", html, flags=re.DOTALL)
    assert field not in without_copy


def test_on_embeds_no_payload_endpoint_or_identifiers(app, render_training):
    html = render_training(app, enabled=True)
    assert "/api/training/weekly-program" not in html
    assert "WeeklyProgramRecommendation" not in html
    assert "AdaptivePlan" not in html
    assert "WEEKLY_PROGRAM_UI_ENABLED" not in html      # the flag itself never ships
    assert "weeklyProgram = {" not in html              # no bootstrapped payload


def test_on_embeds_no_user_identifier(app, client, make_user, login):
    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = True
    user = make_user("wpuiid", profile_complete=True)
    login("wpuiid")
    html = client.get("/training").get_data(as_text=True)
    section = re.search(r"<section[^>]*\bdata-weekly-program-mount\b[^>]*>", html).group(0)
    assert str(user.id) not in section
    assert user.username not in section
    assert user.cognito_sub not in html          # no identity leaked by the shell
    assert "acc-" not in section and "id-" not in section   # no session token


def test_on_renders_no_placeholder_or_state_copy(app, render_training):
    """No fake loading, neutral, empty or error copy — PR6.2 owns every user-facing
    state, and inventing one now would ship a claim the backend never made."""
    html = render_training(app, enabled=True)
    section = re.search(r"<section[^>]*\bdata-weekly-program-mount\b[^>]*>.*?</section>",
                        html, re.DOTALL).group(0)
    for word in ("Yükleniyor", "Loading", "skeleton", "Hata", "Error",
                 "Yeterli veri", "not enough", "Haftalık Program", "Weekly Program"):
        assert word not in section, word


def test_on_leaves_the_existing_controls_intact(app, render_training):
    html = render_training(app, enabled=True)
    for action in ("startWorkout", "generatePlan", "savePlan", "resetPlan",
                   "submitPumpCheck", "finishSession"):
        assert 'data-action="%s"' % action in html, action
    for asset in ("/static/training.js", "/static/coach_widget.js", "/static/actions.js",
                  "/static/training.css"):
        assert asset in html, asset


def test_on_adds_no_navigation_entry(app, render_training):
    off = render_training(app, enabled=False)
    on = render_training(app, enabled=True)
    nav = lambda html: html[html.index("<nav"):html.index("</nav>")]  # noqa: E731
    assert nav(on) == nav(off)


def test_on_is_the_off_document_plus_the_shell_and_script_only(app, render_training):
    """Whole-document delta: enabling the flag may add exactly two lines.

    Normalizes the three legitimately per-boot/per-request values (`_v` cache-buster,
    CSP nonce, CSRF token) — the same normalization used for the one-time OFF-path
    byte-identity diff recorded in the PR6.1 handoff."""
    def strip_v(html):
        html = re.sub(r"\?v=\d+", "?v=<B>", html)
        html = re.sub(r'nonce="[^"]*"', 'nonce="<N>"', html)
        return re.sub(r'(name="csrf-token"\s+content=)"[^"]*"', r'\1"<C>"', html)

    off = strip_v(render_training(app, enabled=False)).splitlines()
    on = strip_v(render_training(app, enabled=True)).splitlines()
    added = [line for line in on if line not in off]
    assert len(added) == 4
    assert added[0] == (
        '        <section id="weekly-program" data-weekly-program-mount aria-hidden="true">')
    assert re.fullmatch(
        r'\s*<script type="application/json" data-weekly-program-copy>.+</script>',
        added[1])
    assert added[2:] == ['        </section>',
                         '<script src="/static/weekly_program.js?v=<B>"></script>']
    assert [line for line in off if line not in on] == []


# ── flag isolation ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("ui_flag", "coach_flag", "shell_expected"), [
    (False, False, False),
    (False, True, False),
    (True, False, True),
    (True, True, True),
])
def test_complete_four_way_runtime_flag_matrix(
        app, render_training, ui_flag, coach_flag, shell_expected):
    html = render_training(app, enabled=ui_flag, coach_flag=coach_flag)

    assert (SHELL_ID in html) is shell_expected
    assert (MOUNT_ATTR in html) is shell_expected
    assert (SCRIPT_PATH in html) is shell_expected
    assert app.config["WEEKLY_PROGRAM_UI_ENABLED"] is ui_flag
    assert app.config["AI_ADAPTIVE_PLAN_CONTEXT"] is coach_flag


def test_coach_flag_alone_does_not_render_the_shell(app, render_training):
    html = render_training(app, enabled=False, coach_flag=True)
    assert SHELL_ID not in html
    assert MOUNT_ATTR not in html
    assert SCRIPT_PATH not in html


def test_shell_depends_only_on_the_ui_flag(app, render_training):
    """Same UI-flag state under both coach-flag states must produce the same shell."""
    with_coach = render_training(app, enabled=True, coach_flag=True)
    without_coach = render_training(app, enabled=True, coach_flag=False)
    assert with_coach.count(MOUNT_ATTR) == without_coach.count(MOUNT_ATTR) == 1
    assert with_coach.count(SCRIPT_PATH) == without_coach.count(SCRIPT_PATH) == 1


def test_ui_flag_does_not_alter_the_coach_configuration(app, render_training):
    render_training(app, enabled=True, coach_flag=False)
    assert app.config["AI_ADAPTIVE_PLAN_CONTEXT"] is False


# ── route boundary (AST) ────────────────────────────────────────────────────────

def _page_view_ast():
    tree = ast.parse(TRAINING_BLUEPRINT.read_text(encoding="utf-8"),
                     filename=str(TRAINING_BLUEPRINT))
    views = [node for node in ast.walk(tree)
             if isinstance(node, ast.FunctionDef) and node.name == PAGE_VIEW_NAME]
    assert len(views) == 1, "expected exactly one %s view" % PAGE_VIEW_NAME
    return views[0]


def test_page_view_reads_no_history_planner_or_weekly_program_service():
    """PR6.1 renders a shell; it must not become a data consumer through the back
    door. Mirrors the AST guard on the endpoint (tests/test_weekly_program_route.py)."""
    view = _page_view_ast()
    names = {node.id for node in ast.walk(view) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(view) if isinstance(node, ast.Attribute)}
    forbidden = {"build_weekly_program", "weekly_program_payload", "build_adaptive_plan",
                 "build_progression_report", "build_training_history_summary",
                 "WorkoutLog", "TrainingPlan", "PumpCheck"}
    assert not (names | attributes) & forbidden


def test_page_view_exposes_only_the_boolean_flag():
    """Exactly one config read, and it is the UI flag — not the whole config object."""
    source = ast.unparse(_page_view_ast())
    assert source.count("current_app.config") == 1
    assert "WEEKLY_PROGRAM_UI_ENABLED" in source
    assert "AI_ADAPTIVE_PLAN_CONTEXT" not in source
    assert "current_app.config)" not in source          # no whole-config dump
    assert "config.items" not in source


def test_page_view_issues_no_extra_query(app, client, make_user, login):
    """Flag ON must add zero SQL — the shell is static markup, not a data consumer.

    The first `/training` of a session runs one-off work (streak/rollover hooks, session
    touch) that later requests skip, so a warm-up request precedes the measurement;
    otherwise the comparison would just be measuring that warm-up."""
    from sqlalchemy import event
    from app.extensions import db

    statements = []

    def before_cursor(conn, cursor, statement, *args):
        statements.append(statement)

    make_user("wpuisql", profile_complete=True)
    login("wpuisql")

    app.config["WEEKLY_PROGRAM_UI_ENABLED"] = False
    client.get("/training")                       # warm-up: reach steady state
    event.listen(db.engine, "before_cursor_execute", before_cursor)
    try:
        client.get("/training")
        off_statements = list(statements)
        statements.clear()
        app.config["WEEKLY_PROGRAM_UI_ENABLED"] = True
        client.get("/training")
        on_statements = list(statements)
    finally:
        event.remove(db.engine, "before_cursor_execute", before_cursor)

    assert len(on_statements) == len(off_statements)
    assert not [s for s in on_statements if "workout_log" in s.lower()]
    assert not [s for s in on_statements if "training_plan" in s.lower()]


def _copy_payload(html):
    match = re.search(
        r'<script type="application/json" data-weekly-program-copy>(.*?)</script>',
        html, re.DOTALL)
    assert match, "feature copy bundle not found"
    return json.loads(match.group(1))


def test_off_emits_no_feature_copy_namespace(app, render_training):
    html = render_training(app, enabled=False)
    assert "data-weekly-program-copy" not in html
    assert '"weekly_program.' not in html


def test_on_copy_bundle_is_complete_localized_and_not_visible_markup(
        app, render_training):
    html = render_training(app, enabled=True)
    payload = _copy_payload(html)
    from app.i18n import catalog

    expected = {
        key for key in catalog("tr")
        if key.startswith("weekly_program.")
    } | {"training.weekly_program"}
    assert set(payload) == expected
    assert all(isinstance(value, str) and value.strip() for value in payload.values())
    assert all(value != key for key, value in payload.items())

    visible = re.sub(
        r'<script type="application/json" data-weekly-program-copy>.*?</script>',
        "", html, flags=re.DOTALL)
    assert '"weekly_program.' not in visible


def test_copy_bundle_tojson_escapes_hostile_plain_text(
        app, render_training, monkeypatch):
    import app.i18n as i18n

    hostile = 'T\u00fcrk\u00e7e "quote" <tag> & </script><script>alert(1)</script>'
    monkeypatch.setitem(i18n._CATALOG["tr"], "weekly_program.loading", hostile)
    html = render_training(app, enabled=True)
    section = re.search(
        r"<section[^>]*\bdata-weekly-program-mount\b[^>]*>.*?</section>",
        html, re.DOTALL).group(0)

    assert hostile not in section
    assert section.count("</script>") == 1
    assert "\\u003c" in section and "\\u003e" in section and "\\u0026" in section
    assert _copy_payload(html)["weekly_program.loading"] == hostile
