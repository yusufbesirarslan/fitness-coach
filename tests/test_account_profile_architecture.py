"""One onboarding authority, one readiness rule (LP-03 architecture gate).

Source-level guards that fail if onboarding persistence is duplicated again,
if a transport regains domain logic, if any reader goes back to its own
definition of "onboarded", or if the stored Turkish goal literals leak onto
the native surface.

    python -m pytest tests/test_account_profile_architecture.py -v
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = "app/services/account_profile.py"
WEB = "app/blueprints/profile.py"
MOBILE = "app/blueprints/mobile_account_profile.py"
MOBILE_API = "app/blueprints/mobile_api.py"

# Readers that answer "is this account onboarded / first-plan eligible".
READINESS_CONSUMERS = {
    MOBILE_API: "account_profile.onboarding_state(",
    WEB: "account_profile.onboarding_state(",
    "app/blueprints/tracking.py": "account_profile.onboarding_state(",
    "app/blueprints/training.py": "account_profile.onboarding_state(",
    "app/services/mobile_training_generation/service.py": "onboarding_state(",
}

PRESENTATION_NAMES = frozenset({
    "request", "session", "flash", "redirect", "jsonify", "render_template",
    "url_for", "make_response", "abort", "g", "current_app",
})


def _source(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def _tree(relative):
    return ast.parse(_source(relative))


def _python_sources():
    for base in ("app", "fitx_mcp"):
        for path in sorted((ROOT / base).rglob("*.py")):
            yield path.relative_to(ROOT).as_posix()


def _profile_complete_nodes(tree):
    reads, writes = 0, 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "profile_complete":
            if isinstance(node.ctx, ast.Store):
                writes += 1
            else:
                reads += 1
        if isinstance(node, ast.keyword) and node.arg == "profile_complete":
            writes += 1
    return reads, writes


def test_only_the_service_reads_or_writes_profile_complete():
    touching = {}
    for path in _python_sources():
        if path == "app/models.py":
            continue
        reads, writes = _profile_complete_nodes(_tree(path))
        if reads or writes:
            touching[path] = (reads, writes)
    assert set(touching) == {SERVICE}, touching


def _constructs_user_session(tree):
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "UserSession"
        for node in ast.walk(tree))


def test_user_session_rows_are_created_only_by_known_writers():
    # The onboarding service, and the legacy coach `/chat` history append
    # (not onboarding: it never touches completeness).
    writers = {path for path in _python_sources()
               if _constructs_user_session(_tree(path))}
    assert writers == {SERVICE, "app/blueprints/coach.py"}


def test_readiness_consumers_use_the_canonical_rule():
    for path, needle in READINESS_CONSUMERS.items():
        assert needle in _source(path), path


def test_first_plan_prerequisites_do_not_query_user_session_directly():
    for path in ("app/services/mobile_training_generation/service.py",):
        tree = _tree(path)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert "UserSession" not in names, path
    web_generate = next(
        node for node in ast.walk(_tree("app/blueprints/training.py"))
        if isinstance(node, ast.FunctionDef)
        and node.name == "training_plan_generate")
    names = {node.id for node in ast.walk(web_generate) if isinstance(node, ast.Name)}
    assert "UserSession" not in names


def test_transports_delegate_and_carry_no_domain_logic():
    for relative in (WEB, MOBILE):
        source = _source(relative)
        assert "account_profile.complete_onboarding(" in source, relative
        assert "OnboardingProfile(" in source, relative
        tree = _tree(relative)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert "app.services.calculations" not in imported, relative
        assert not _constructs_user_session(tree), relative


def test_the_service_has_no_presentation_or_transport_dependency():
    tree = _tree(SERVICE)
    modules, flask_names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
            if node.module == "flask":
                flask_names.update(alias.name for alias in node.names)
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    assert not flask_names & PRESENTATION_NAMES
    assert not {m for m in modules if m.split(".")[0] in {"flask", "flask_login", "werkzeug"}}
    assert "app.i18n" not in modules
    assert "logging" not in modules


def test_native_modules_never_carry_the_stored_goal_literals():
    for path in _python_sources():
        name = path.rsplit("/", 1)[-1]
        if not (name.startswith("mobile_") or "/mobile_" in path):
            continue
        source = _source(path)
        assert "kilo verme" not in source and "kas kazanma" not in source, path


def test_goal_token_mapping_lives_only_in_the_service():
    owners = {path for path in _python_sources()
              if '"lose_weight"' in _source(path) or '"build_muscle"' in _source(path)}
    assert owners == {SERVICE}


def test_native_body_is_a_closed_set_without_authority_fields():
    from app.blueprints import mobile_account_profile as module

    assert module.ALLOWED_FIELDS == {
        "weight_kg", "height_cm", "age", "gender", "goal", "fitness_level",
        "activity_level", "target_weight_kg"}
    assert not module.ALLOWED_FIELDS & {
        "user_id", "account_id", "owner_id", "email", "username",
        "profile_complete", "id"}


def _function(tree, name):
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == name)


@pytest.mark.parametrize("name", ["put_account_profile", "get_account_profile"])
def test_native_route_takes_the_owner_from_the_bearer_principal_only(name):
    route = _function(_tree(MOBILE), name)
    decorators = {ast.unparse(d) for d in route.decorator_list}
    assert "require_mobile_auth" in decorators
    attributes = {ast.unparse(node) for node in ast.walk(route)
                  if isinstance(node, ast.Attribute)}
    assert "g.mobile_user" in attributes
    # No `request.` at all: a query string cannot select or shape the answer.
    assert not {a for a in attributes if a.startswith("request.")}
    assert not {a for a in attributes if a.startswith("current_user")}


def test_native_read_delegates_to_the_service_projection():
    route = _function(_tree(MOBILE), "get_account_profile")
    source = ast.unparse(route)
    assert "account_profile.current_profile(g.mobile_user)" in source
    names = {node.id for node in ast.walk(route) if isinstance(node, ast.Name)}
    assert not names & {"User", "UserSession"}
    attributes = {node.attr for node in ast.walk(route)
                  if isinstance(node, ast.Attribute)}
    # Every value comes from the projection; the route reads no column.
    assert not attributes & {
        "weight", "height", "age", "gender", "goal", "fitness_level",
        "current_activity", "target_weight"}


def test_the_read_answers_exactly_the_fields_the_write_takes():
    from app.blueprints import mobile_account_profile as module

    assert set(module.READ_FIELDS) == module.ALLOWED_FIELDS


def test_the_service_read_writes_locks_and_commits_nothing():
    read = _function(_tree(SERVICE), "current_profile")
    # The docstring describes what is NOT done; only the code is checked.
    body = [statement for statement in read.body
            if not (isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant))]
    source = "\n".join(ast.unparse(statement) for statement in body)
    for forbidden in ("db.", "session", "commit", "with_for_update",
                      "_lock_owner", "UserSession", "canonical_session"):
        assert forbidden not in source, forbidden
    stores = [node for node in ast.walk(read)
              if isinstance(node, ast.Attribute)
              and isinstance(node.ctx, ast.Store)]
    assert stores == []


def test_postgres_race_proof_is_selected_by_ci():
    workflow = _source(".github/workflows/ci.yml")
    assert "tests/test_account_profile_pg.py" in workflow
