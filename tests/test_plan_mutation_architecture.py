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


def test_the_coach_reaches_the_mutation_domain_through_exactly_one_bridge():
    """PR1 wrote this as "no Coach module imports the domain **yet**", and named
    the update as the deliberate act a later PR would have to perform. Sprint 1
    PR3 performs it — and narrows it rather than removing it.

    The Coach now has plan write authority, but only by asking
    ``coach_plan_tools``. Everything the tool boundary owns — the rollout flag,
    the strict argument parser, the server-minted operation key, ``actor``, the
    per-turn budget, the bounded result vocabulary, the transaction settle —
    lives on that one path. A direct ``plan_mutation`` import from a provider
    loop would run a mutation with none of it, and would look perfectly
    reasonable in review, which is exactly why this is a test and not a
    convention.
    """
    coach_modules = [
        Path("app/services/ai_coach.py"),
        Path("app/services/ai_pipeline.py"),
        Path("app/services/ai_stream.py"),
        Path("app/services/context_builder.py"),
        Path("app/services/prompt_builder.py"),
        Path("app/services/adaptive_plan_context.py"),
    ]
    violations = []
    for path in coach_modules:
        if not path.exists():
            continue
        for imported, lineno in _python_imports(path):
            if _module_matches(imported, "app.services.plan_mutation"):
                violations.append(f"{path}:{lineno} -> {imported}")

    assert not violations, (
        "a Coach module reaches the mutation domain directly, bypassing "
        f"coach_plan_tools: {violations}")


def test_the_bridge_package_is_the_only_new_consumer_of_the_domain():
    """Who may import ``plan_mutation`` at all, stated in one place.

    Kept as an explicit allow-list because the interesting failure is a *new*
    consumer appearing quietly — a blueprint, a job, a second AI surface — each
    of which would need its own authorization, identity and idempotency story.
    Adding a name here is cheap; the point is that it has to be a decision.
    """
    approved = {
        Path("app/services/coach_plan_tools/executor.py"),
        Path("app/services/coach_plan_tools/parser.py"),
        Path("app/services/coach_plan_tools/results.py"),
        Path("app/services/coach_plan_tools/schemas.py"),
        Path("app/services/coach_plan_tools/identity.py"),
        Path("app/services/coach_plan_tools/proposals.py"),
        Path("app/services/coach_plan_policy/decisions.py"),
    }
    consumers = set()
    for path in APP_ROOT.rglob("*.py"):
        if path in approved or MUTATION_ROOT in path.parents:
            continue
        for imported, _lineno in _python_imports(path):
            if _module_matches(imported, "app.services.plan_mutation"):
                consumers.add(str(path))

    assert not consumers, f"unapproved plan_mutation consumers: {sorted(consumers)}"


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


# ── Sprint 11 PR4 Task 5: exercise authority inside the pure engine ──────────

def _names_referenced_in(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _function_defs(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_the_exercise_catalog_is_loaded_once_per_mutation():
    """One catalog load per command, pinned structurally.

    ``document.py`` is the pure engine and ``load_exercise_catalog`` is
    ``lru_cache``d over a bundled asset, so calling it is not the I/O this layer
    forbids. Calling it per *exercise* would be a different thing: an unbounded
    number of lookups where the design promises one, and the possibility of two
    differently-loaded catalogs deciding identity inside a single command.
    """
    call_sites = _calls_named(MUTATION_ROOT / "document.py",
                              "load_exercise_catalog")

    assert len(call_sites) == 1, (
        f"document.py loads the catalog {len(call_sites)} times: {call_sites}")


def test_only_the_pure_engine_resolves_exercise_identity():
    """Exercise identity has one interpreter in this domain.

    The service, the journal and the fingerprint layer must keep treating the
    plan as opaque text. A second module resolving names would be a second
    opinion about what a plan means, reachable on a path with no document copy
    and no fail-closed context check.
    """
    approved = {MUTATION_ROOT / "document.py",
                MUTATION_ROOT / "validation.py"}
    violations = []
    for path in _mutation_modules():
        if path in approved:
            continue
        for imported, lineno in _python_imports(path):
            if _module_matches(imported, "app.services.exercise_catalog"):
                violations.append(f"{path}:{lineno} -> {imported}")

    assert not violations, f"a second exercise-identity reader: {violations}"


def test_exercise_identity_is_never_written_without_its_canonical_name():
    """P1-4, as a structural companion to the behavioural proof.

    The defect this task closes was a write that moved ``isim`` and left
    ``exercise_id`` behind. Any function that can touch ``FIELD_EXERCISE_ID``
    must therefore also be a function that touches ``FIELD_NAME`` — a helper
    that writes identity on its own is the shape of the bug coming back.
    """
    offenders = [
        node.name for node in _function_defs(MUTATION_ROOT / "document.py")
        if "FIELD_EXERCISE_ID" in _names_referenced_in(node)
        and "FIELD_NAME" not in _names_referenced_in(node)
    ]

    assert not offenders, (
        f"these write exercise identity without its name: {offenders}")


def test_the_identity_guard_detects_a_lone_identity_write(tmp_path):
    """The guard above only means something if it can actually fail."""
    competitor = tmp_path / "competitor.py"
    competitor.write_text(
        "def rewrite(entry, value):\n"
        "    entry[FIELD_EXERCISE_ID] = value\n", encoding="utf-8")

    offenders = [
        node.name for node in _function_defs(competitor)
        if "FIELD_EXERCISE_ID" in _names_referenced_in(node)
        and "FIELD_NAME" not in _names_referenced_in(node)
    ]

    assert offenders == ["rewrite"]


def test_the_cardio_placement_rule_has_a_single_definition():
    """Addendum §B. Task 4 closed a confirmed P1 — a cardio movement on a
    non-cardio day bypasses the equipment gate entirely — with one rule in
    ``training_generation.exercise_resolution``. This boundary reuses that
    function instead of restating the predicate, because two copies of an
    authority rule on two write doors is precisely how the first hole opened.
    """
    from app.services.training_generation import exercise_resolution

    source = (MUTATION_ROOT / "document.py").read_text(encoding="utf-8")

    assert "_check_placement" in source
    assert "CARDIO_MOVEMENT" not in source
    assert "CARDIO_TIP" not in source
    assert callable(exercise_resolution._check_placement)
