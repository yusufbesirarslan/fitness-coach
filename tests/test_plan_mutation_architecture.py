"""Authority + dependency boundaries for the plan-mutation domain (Sprint 1 PR1).

These are structural on purpose, and deliberately few: they guard the two claims
PR1 exists to make — the mutation domain never depends on AI/provider code, and
there is exactly one targeted-mutation authority over ``TrainingPlan``. Anything
finer (method layout, naming, formatting) is behaviour's job, not architecture's
(brief §20).
"""
import ast
from pathlib import Path

from tests.test_dependency_boundaries import _python_imports


MUTATION_ROOT = Path("app/services/plan_mutation")
APP_ROOT = Path("app")

# The mutation domain sits *below* every AI/prompt/provider concern. A single
# import from this list would invert the dependency direction the PR establishes.
FORBIDDEN_PREFIXES = (
    "app.services.ai",
    "app.services.ai_coach",
    "app.services.ai_pipeline",
    "app.services.ai_stream",
    "app.services.adaptive_plan_context",
    "app.services.context_builder",
    "app.services.prompt_builder",
    "app.prompts",
    "app.blueprints",
    "openai",
    "anthropic",
    "boto3",
)


def _module_matches(module, prefix):
    return module == prefix or module.startswith(prefix + ".")


def _mutation_modules():
    return sorted(MUTATION_ROOT.rglob("*.py"))


def test_plan_mutation_domain_exists_as_one_package():
    modules = {path.name for path in _mutation_modules()}

    assert "__init__.py" in modules
    assert "service.py" in modules


def test_plan_mutation_never_imports_ai_provider_or_transport():
    violations = []
    for path in _mutation_modules():
        for imported, lineno in _python_imports(path):
            if any(_module_matches(imported, p) for p in FORBIDDEN_PREFIXES):
                violations.append(f"{path}:{lineno} -> {imported}")

    assert not violations, f"plan_mutation reaches upward/outward: {violations}"


def test_no_coach_module_imports_the_mutation_domain_yet():
    """PR1 establishes the boundary; wiring the AI Coach to it is later work
    (brief §18). If a future PR connects them, it must update this guard
    deliberately rather than inherit write authority by accident."""
    coach_modules = [
        Path("app/services/ai_coach.py"),
        Path("app/services/ai_pipeline.py"),
        Path("app/services/ai_stream.py"),
        Path("app/services/context_builder.py"),
    ]
    violations = []
    for path in coach_modules:
        if not path.exists():
            continue
        for imported, lineno in _python_imports(path):
            if _module_matches(imported, "app.services.plan_mutation"):
                violations.append(f"{path}:{lineno} -> {imported}")

    assert not violations, f"AI Coach already holds plan write authority: {violations}"


def test_pure_document_layer_stays_free_of_orm_and_flask():
    """``document.py`` is the targeted-mutation engine. Keeping it free of the
    ORM and Flask is what lets the whole mutation matrix be tested without a
    database, and is the same pure/impure split the workout-state and
    workout-session services use."""
    forbidden = ("app.models", "app.extensions", "flask", "sqlalchemy")
    violations = [
        f"document.py:{lineno} -> {imported}"
        for imported, lineno in _python_imports(MUTATION_ROOT / "document.py")
        if any(_module_matches(imported, p) for p in forbidden)
    ]

    assert not violations, f"pure document layer is not pure: {violations}"


def _plan_data_assignments(path):
    """Lines where this module assigns to a ``.plan_data`` attribute.

    Structural (AST), not a source-string scan: a reformat, an extra space or a
    keyword argument named ``plan_data`` must not move this guard, and an
    assignment written unusually must not slip past it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == "plan_data":
                lines.append(node.lineno)
    return lines


def test_targeted_plan_writes_have_a_single_owner():
    """Only the mutation service may assign an existing plan row's ``plan_data``.

    A second targeted writer would recreate exactly the "second plan authority"
    this PR forbids. The pre-existing whole-plan save path is *not* on this list
    and does not need to be: it constructs a brand-new ``TrainingPlan(...)``
    rather than assigning the attribute on a loaded row.
    """
    approved = {Path("app/services/plan_mutation/service.py")}
    writers = []
    for path in APP_ROOT.rglob("*.py"):
        if path in approved:
            continue
        for lineno in _plan_data_assignments(path):
            writers.append(f"{path}:{lineno}")

    assert not writers, f"unapproved plan_data writers: {writers}"


def test_plan_data_writer_guard_detects_an_assignment(tmp_path):
    """The guard above only means something if it can actually fail."""
    competitor = tmp_path / "competitor.py"
    competitor.write_text(
        "def rewrite(plan, text):\n    plan.plan_data = text\n", encoding="utf-8")

    assert _plan_data_assignments(competitor) == [2]
