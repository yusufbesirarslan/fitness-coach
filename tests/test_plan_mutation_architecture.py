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


# ── Sprint 1 PR2: history, version and privacy boundaries ────────────────────

def _attribute_assignments(path, attr):
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
            if isinstance(target, ast.Attribute) and target.attr == attr:
                lines.append(node.lineno)
    return lines


def _calls_named(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if called == name:
            lines.append(node.lineno)
    return lines


def test_the_plan_version_has_a_single_writer():
    """Only the mutation service may move ``mutation_version``.

    A version another module can advance is not a version: two writers means two
    opinions about where history stands, and an undo standing on the wrong one
    restores a snapshot over state it does not explain.
    """
    approved = {Path("app/services/plan_mutation/service.py")}
    writers = []
    for path in APP_ROOT.rglob("*.py"):
        if path in approved:
            continue
        for lineno in _attribute_assignments(path, "mutation_version"):
            writers.append(f"{path}:{lineno}")

    assert not writers, f"unapproved mutation_version writers: {writers}"


def test_journal_rows_are_built_in_one_place():
    """``PlanMutationRecord(...)`` may only be constructed by the journal module.

    Every row must carry the same invariants — exact snapshots, both
    fingerprints, the operation key. A second construction site is how a row
    ends up with a projection instead of a snapshot, which an undo would then
    restore.
    """
    approved = {Path("app/services/plan_mutation/journal.py")}
    builders = []
    for path in APP_ROOT.rglob("*.py"):
        if path in approved:
            continue
        for lineno in _calls_named(path, "PlanMutationRecord"):
            builders.append(f"{path}:{lineno}")

    assert not builders, f"unapproved journal writers: {builders}"


def test_the_mutation_domain_does_not_write_workout_session_state():
    """PR1 decided this boundary is not a second writer of workout-session or
    completion state, and PR2 keeps that decision. Importing those owners is how
    a plan change would start silently re-blessing sessions."""
    forbidden = (
        "app.services.workout_session",
        "app.services.workout_completion",
        "app.services.workout_state",
    )
    violations = []
    for path in _mutation_modules():
        for imported, lineno in _python_imports(path):
            if any(_module_matches(imported, p) for p in forbidden):
                violations.append(f"{path}:{lineno} -> {imported}")

    assert not violations, f"mutation domain reaches into session state: {violations}"


def test_the_journal_is_never_logged():
    """Snapshots, command payloads, reasons and operation keys are exactly the
    material that must not reach a log line. The cheapest durable guarantee is
    that this package has no logger at all."""
    emitters = {"print", "logger", "log", "current_app", "capture_message"}
    violations = []
    for path in _mutation_modules():
        for imported, lineno in _python_imports(path):
            if _module_matches(imported, "logging"):
                violations.append(f"{path}:{lineno} -> {imported}")
        # AST, not a substring scan: "print(" also matches "fingerprint(".
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            if name in emitters:
                violations.append(f"{path}:{node.lineno} -> {name}")

    assert not violations, f"plan_mutation can emit audit material: {violations}"


def test_the_journal_has_no_transport_surface_yet():
    """No history endpoint, no mobile or AI serialization in PR2. A blueprint
    that imports the journal would be publishing an audit trail through a
    boundary nobody has reviewed for authorization or privacy."""
    violations = []
    for path in list(Path("app/blueprints").rglob("*.py")):
        for imported, lineno in _python_imports(path):
            if _module_matches(imported, "app.services.plan_mutation"):
                violations.append(f"{path}:{lineno} -> {imported}")
        if "PlanMutationRecord" in path.read_text(encoding="utf-8"):
            violations.append(f"{path} -> PlanMutationRecord")

    assert not violations, f"the journal already has a transport surface: {violations}"


def test_postgresql_ci_executes_the_plan_mutation_races():
    """The races that matter cannot run on SQLite, and a suite nobody runs is
    not coverage. CI must name this file explicitly."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "tests/test_plan_mutation_history_pg.py" in workflow


def test_the_fingerprint_layer_is_pure_and_deterministic():
    """A replay comparator that depends on a clock, a random source, the ORM or
    request state is not a comparator — the same command would fingerprint
    differently in two processes and replay would quietly stop working."""
    forbidden = ("app.models", "app.extensions", "flask", "sqlalchemy",
                 "random", "secrets", "time", "datetime", "uuid")
    violations = [
        f"fingerprint.py:{lineno} -> {imported}"
        for imported, lineno in _python_imports(MUTATION_ROOT / "fingerprint.py")
        if any(_module_matches(imported, p) for p in forbidden)
    ]

    assert not violations, f"fingerprints are not reproducible: {violations}"
