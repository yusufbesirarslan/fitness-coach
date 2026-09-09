"""Structural guards for the ONE per-owner ``TrainingPlan`` create boundary.

The cross-transport race in ``tests/test_training_plan_replacement_pg.py``
proves the boundary holds for the two create writers that exist TODAY. It
cannot notice a third one. That is exactly how this finding arose: the browser
create was given an owner-row lock, the native create already had an
operation-row lock and an advisory lock of its own, and nothing anywhere said
those had to be the SAME object — so the two writers looked independently
correct and were jointly wrong.

These are source-level guards, so they fail when a create writer is ADDED
rather than when a race happens to interleave. Each is proven non-vacuous by
mutating the real source and re-running (see the module docstring of
``tests/test_mobile_today_architecture.py`` for the established pattern).
"""
import ast
import pathlib

import pytest


APP = pathlib.Path(__file__).resolve().parents[1] / "app"

LOCK_MODULE = "app/services/plan_owner_lock.py"
LOCK_FUNCTION = "lock_plan_owner"

# The canonical active-plan selector. Every create writer asks it whether the
# owner already has a plan, and that question is the one that must be asked
# under the lock.
ABSENCE_CHECK = "get_active_plan"


def _modules():
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _calls(node):
    """Every called name inside ``node``, in source order."""
    names = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            names.append((child.lineno, func.id))
        elif isinstance(func, ast.Attribute):
            names.append((child.lineno, func.attr))
    return sorted(names)


def _constructs_training_plan(tree):
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "TrainingPlan"
        for node in ast.walk(tree)
    )


def _relative(path):
    return path.relative_to(APP.parent).as_posix()


def test_every_training_plan_create_writer_takes_the_shared_owner_lock():
    """A module that inserts a ``TrainingPlan`` must stand behind the boundary.

    Not "must lock something" — must call THIS primitive. Two writers that each
    lock a different object are two boundaries, which is the same as none: the
    native command's operation row and advisory lock were both real locks and
    neither one was anything the browser contended for.
    """
    writers = {}
    for path, tree in _modules():
        if _relative(path) == LOCK_MODULE or not _constructs_training_plan(tree):
            continue
        writers[_relative(path)] = {name for _line, name in _calls(tree)}

    # Tripwire: if this set ever empties, every assertion below passes while
    # proving nothing.
    assert writers, "no TrainingPlan create writer found — the scan is broken"

    missing = sorted(mod for mod, calls in writers.items()
                     if LOCK_FUNCTION not in calls)
    assert not missing, (
        "these modules create a TrainingPlan without taking the shared "
        f"per-owner lock ({LOCK_FUNCTION}): {missing}"
    )


@pytest.mark.parametrize("module_path, function_name", [
    ("app/services/plan_replacement.py", "replace_training_plan"),
    ("app/services/mobile_training_generation/store.py", "commit_plan"),
])
def test_the_absence_check_is_made_after_the_owner_lock(module_path,
                                                        function_name):
    """Order, not mere presence.

    Taking the lock AFTER asking whether a plan exists would leave both writers
    deciding on an unlocked read and then queueing for a write they have
    already committed to — two plans, just more slowly. PostgreSQL will even
    make that look partly right on its own: the ``training_plan.user_id``
    foreign key takes a ``FOR KEY SHARE`` lock on the owner row during the
    INSERT, so a misordered writer still BLOCKS. It just blocks too late.
    """
    path = APP.parent / module_path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = next(
        (node for node in ast.walk(tree)
         if isinstance(node, ast.FunctionDef) and node.name == function_name),
        None)
    assert target is not None, f"{function_name} not found in {module_path}"

    calls = _calls(target)
    lock_lines = [line for line, name in calls if name == LOCK_FUNCTION]
    check_lines = [line for line, name in calls if name == ABSENCE_CHECK]
    assert lock_lines, f"{function_name} does not take {LOCK_FUNCTION}"
    assert check_lines, f"{function_name} does not call {ABSENCE_CHECK}"
    assert min(lock_lines) < min(check_lines), (
        f"{function_name} asks {ABSENCE_CHECK} before taking {LOCK_FUNCTION}"
    )


def test_the_owner_lock_primitive_stays_a_leaf():
    """The shared primitive must not import either writer.

    It is imported by both a browser-facing service and a native one; importing
    either back would make the boundary a cycle and, more practically, would
    tempt the next author to put a writer's policy inside the lock.
    """
    path = APP.parent / LOCK_MODULE
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # BOTH halves of an ``ImportFrom`` count. Reading only ``node.module``
    # misses ``from app.services import plan_replacement`` entirely — the most
    # natural way to write the very import this guard exists to forbid, and the
    # form that made an earlier version of this test pass under mutation.
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = node.module or ""
            imported.add(prefix)
            imported.update(
                (prefix + "." + alias.name) if prefix else alias.name
                for alias in node.names)

    forbidden = sorted(
        name for name in imported
        if "plan_replacement" in name or "mobile_training_generation" in name
        or "plan_mutation" in name
    )
    assert not forbidden, f"{LOCK_MODULE} must not import its callers: {forbidden}"
