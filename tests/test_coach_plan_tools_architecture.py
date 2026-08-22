"""Structural guards for the AI Coach's plan-mutation tool boundary (PR3).

The behavioural suite proves the boundary does the right thing today. These
prove the *shape* that makes it hard to do the wrong thing tomorrow — the
properties a reviewer would have to notice by eye, on a diff that looks
entirely reasonable:

* a schema that quietly grows a ``user_id``,
* a parser that starts splatting model arguments into a constructor,
* a second module that imports ``plan_mutation`` and skips the whole trusted
  context,
* an ``actor`` or ``reason`` that starts coming from somewhere else,
* a budget raised past the tool-loop cap, which would make it decorative.

All of it is AST- or data-driven. A source-substring scan would break on a
reformat and pass on a rename.
"""
import ast
import inspect
from pathlib import Path

import pytest

from app.services import coach_plan_tools
from app.services.coach_plan_tools import executor, identity, parser, results
from app.services.coach_plan_tools import schemas
from app.services.plan_mutation import ACTOR_AI_COACH, PLAN_MUTATION_COMMANDS
from tests.test_dependency_boundaries import _python_imports


PACKAGE_ROOT = Path("app/services/coach_plan_tools")


def _package_modules():
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_matches(module, prefix):
    return module == prefix or module.startswith(prefix + ".")


def _walk_properties(schema, path=()):
    """Every ``(dotted path, property name, subschema)`` in a JSON schema."""
    for name, subschema in (schema.get("properties") or {}).items():
        yield ".".join(path + (name,)), name, subschema
        if isinstance(subschema, dict):
            yield from _walk_properties(subschema, path + (name,))
    items = schema.get("items")
    if isinstance(items, dict):
        yield from _walk_properties(items, path + ("[]",))


# ── What the model may name ──────────────────────────────────────────────────

#: Concepts the server owns. A tool property with any of these names would let
#: the model choose a victim, a lineage, a version, an audit actor or an
#: idempotency identity — the exact things brief §12 says it must never supply.
SERVER_OWNED = (
    "user", "user_id", "userid", "owner", "account",
    "plan_id", "planid", "training_plan_id", "row_id", "id", "pk",
    "lineage", "lineage_id", "version", "mutation_version", "plan_version",
    "mutation_id", "journal", "record", "record_id",
    "actor", "reason", "idempotency_key", "operation_key", "key",
    "snapshot", "before", "after", "fingerprint", "digest",
    "token", "auth", "authorization", "principal", "session",
    "proposal", "proposal_id", "confirmation_id", "confirmation_token",
    "impact", "risk", "requires_confirmation", "reason_codes", "verdict",
)

_ALL_PLAN_TOOL_DEFS = (
    list(schemas.PLAN_MUTATION_TOOL_DEFS) + list(schemas.CONFIRMATION_TOOL_DEFS)
)

#: A generic escape hatch is the other way the boundary dies: one tool with an
#: ``operation``/``payload``/``path`` pair is an arbitrary mutation language
#: wearing a narrow tool's name (brief §11).
GENERIC_SHAPES = ("operation", "op", "action", "command", "payload", "patch",
                  "path", "pointer", "field", "column", "attribute", "value",
                  "data", "json", "query", "sql", "expression")

#: Sprint 11 PR4 Task 5. Exercise IDENTITY is the catalog's, and the catalog is
#: the server's. The model may name an exercise the way a user would — that is
#: what ``exercise``/``replacement`` are for — but a property that let it supply
#: a catalog ID, an equipment context or a catalog version would let it declare
#: the very facts this PR exists to take away from it. ``exercise_id`` in
#: particular would be a claim the boundary would then have to re-verify, which
#: is a strictly worse contract than never accepting it.
CATALOG_OWNED = (
    "exercise_id", "exerciseid", "catalog", "catalog_version",
    "equipment", "equipment_context", "exercise_context",
    "cardio_type", "movement", "canonical_name", "alias", "aliases",
)


@pytest.mark.parametrize("definition", _ALL_PLAN_TOOL_DEFS,
                         ids=lambda d: d["name"])
def test_no_tool_property_names_a_server_owned_concept(definition):
    named = [
        dotted for dotted, name, _sub
        in _walk_properties(definition["parameters"])
        if name.lower() in SERVER_OWNED
    ]

    assert not named, f"{definition['name']} exposes server-owned: {named}"


@pytest.mark.parametrize("definition", _ALL_PLAN_TOOL_DEFS,
                         ids=lambda d: d["name"])
def test_no_tool_property_names_a_catalog_owned_concept(definition):
    named = [
        dotted for dotted, name, _sub
        in _walk_properties(definition["parameters"])
        if name.lower() in CATALOG_OWNED
    ]

    assert not named, f"{definition['name']} exposes catalog-owned: {named}"


@pytest.mark.parametrize("definition", schemas.PLAN_MUTATION_TOOL_DEFS,
                         ids=lambda d: d["name"])
def test_no_tool_offers_a_generic_mutation_language(definition):
    generic = [
        dotted for dotted, name, _sub
        in _walk_properties(definition["parameters"])
        if name.lower() in GENERIC_SHAPES
    ]

    assert not generic, f"{definition['name']} is not narrow: {generic}"


@pytest.mark.parametrize("definition", _ALL_PLAN_TOOL_DEFS,
                         ids=lambda d: d["name"])
def test_every_schema_is_closed_and_internally_consistent(definition):
    parameters = definition["parameters"]
    properties = parameters.get("properties") or {}

    assert parameters["type"] == "object"
    # Closed on purpose: an open schema invites the model to add a field, and
    # a field the parser then refuses is a worse experience than one the schema
    # never advertised.
    assert parameters["additionalProperties"] is False
    assert set(parameters.get("required", [])) <= set(properties)
    assert definition["description"].strip()
    for _dotted, name, subschema in _walk_properties(parameters):
        assert subschema.get("type"), f"{name} has no declared type"
        assert subschema.get("description") or subschema.get("enum"), name


def test_the_published_schema_and_the_parser_agree_field_for_field():
    """One drift here is a tool the model can call but the server refuses.

    The schema is what the provider sees; ``TOOL_ARGUMENTS`` is what the server
    accepts. Two hand-maintained lists is how "the model keeps sending an
    argument that gets rejected" happens, and it is invisible in a diff.
    """
    for definition in _ALL_PLAN_TOOL_DEFS:
        name = definition["name"]
        properties = set(definition["parameters"].get("properties") or {})
        required = set(definition["parameters"].get("required", []))
        if name in {schemas.UNDO_TOOL, schemas.CONFIRM_TOOL,
                    schemas.CANCEL_PENDING_TOOL}:
            assert properties == set()
            assert name not in parser.TOOL_ARGUMENTS
            continue
        declared_required, declared_optional = parser.TOOL_ARGUMENTS[name]
        assert set(declared_required) == required, name
        assert set(declared_required) | set(declared_optional) == properties, name


def test_every_accepted_field_has_a_declared_type_in_the_parser():
    """A field with no type would reach ``_coerce``'s unreachable branch and be
    refused at runtime — better to fail here, at build time."""
    typed = parser._TEXT_FIELDS | parser._INT_FIELDS
    for required, optional in parser.TOOL_ARGUMENTS.values():
        assert set(required) | set(optional) <= typed


def test_the_tool_names_do_not_collide_with_the_existing_coach_tools():
    from app.services import ai_coach

    existing = {t["name"] for t in ai_coach._COACH_TOOL_DEFS}

    assert not existing & coach_plan_tools.PLAN_MUTATION_TOOL_NAMES
    assert not existing & coach_plan_tools.CONFIRMATION_TOOL_NAMES
    assert len(coach_plan_tools.PLAN_MUTATION_TOOL_NAMES) == \
        len(schemas.PLAN_MUTATION_TOOL_DEFS)


def test_every_typed_command_has_exactly_one_tool():
    """No command reachable two ways, and none reachable through a tool that
    builds it dynamically."""
    built = {
        node.func.id
        for node in ast.walk(_tree(PACKAGE_ROOT / "parser.py"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id.endswith("Command")
    }

    assert built == {command.__name__ for command in PLAN_MUTATION_COMMANDS}


def test_commands_are_never_built_by_splatting_model_arguments():
    """``Command(**arguments)`` would hand the model the constructor.

    Every field would then be whatever the parser happened to let through, and
    adding a field to a command would silently expose it. Each branch names its
    keywords instead, so a new field is a deliberate line of code.
    """
    splats = [
        node.lineno
        for node in ast.walk(_tree(PACKAGE_ROOT / "parser.py"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.endswith("Command")
        and any(keyword.arg is None for keyword in node.keywords)
    ]

    assert not splats, f"model arguments splatted into a command: {splats}"


# ── What the boundary owns ───────────────────────────────────────────────────

def test_the_audit_envelope_is_built_in_one_place_with_fixed_values():
    """``actor`` and ``reason`` are server decisions, not parameters.

    ``actor='ai_coach'`` is audit metadata (§13) and ``reason=None`` keeps
    untrusted user text and unverified model narration out of a durable column
    (§14). Both are asserted structurally so a later "just pass the user's
    message as the reason" cannot land quietly.
    """
    contexts = [
        node for node in ast.walk(_tree(PACKAGE_ROOT / "executor.py"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "MutationContext"
    ]

    assert len(contexts) == 1
    keywords = {k.arg: k.value for k in contexts[0].keywords}
    assert set(keywords) == {"idempotency_key", "actor", "reason"}
    assert isinstance(keywords["actor"], ast.Name)
    assert keywords["actor"].id == "ACTOR_AI_COACH"
    assert isinstance(keywords["reason"], ast.Constant)
    assert keywords["reason"].value is None
    assert executor._context("k").actor == ACTOR_AI_COACH
    assert executor._context("k").reason is None


def test_the_operation_key_is_derived_only_from_the_turn_and_the_command():
    """Nothing model-authored, nothing wall-clock, nothing random.

    A key that varied per attempt would give no replay protection at all; one
    hashed from raw model JSON would mint a fresh key for the same intent every
    time the whitespace changed (§18).
    """
    forbidden = ("random", "secrets", "time", "datetime", "uuid", "json",
                 "app.models", "sqlalchemy")
    violations = [
        f"identity.py:{lineno} -> {imported}"
        for imported, lineno in _python_imports(PACKAGE_ROOT / "identity.py")
        if any(_module_matches(imported, p) for p in forbidden)
    ]

    assert not violations, f"the operation key is not reproducible: {violations}"
    assert list(inspect.signature(
        identity.mutation_operation_key).parameters) == ["command"]
    assert list(inspect.signature(identity.undo_operation_key).parameters) == []


def test_the_budget_is_stricter_than_the_tool_loop_cap():
    """A budget at or above the loop cap is decorative (§34).

    The assertion lives here rather than in the package because importing the
    Coach from inside its own tool package would invert the dependency the
    package exists to keep one-directional.
    """
    from app.services import ai_coach

    assert 1 <= coach_plan_tools.MAX_PLAN_OPERATIONS_PER_TURN
    assert coach_plan_tools.MAX_PLAN_OPERATIONS_PER_TURN < \
        ai_coach._COACH_TOOL_LOOP_CAP


def test_the_package_never_reads_or_writes_the_journal_itself():
    """Undo is the one sanctioned way to consult history (§71).

    Anything else — "show me my last change", "undo the one before" — is a
    transport surface over an audit trail nobody has reviewed for authorization
    or privacy, and PR3 does not add one.
    """
    violations = []
    for path in _package_modules():
        for imported, lineno in _python_imports(path):
            if _module_matches(imported, "app.services.plan_mutation.journal"):
                # The bounded outcome constants are contract, not data access.
                continue
            if _module_matches(imported, "app.models"):
                violations.append(f"{path}:{lineno} -> {imported}")
        source = path.read_text(encoding="utf-8")
        if "PlanMutationRecord" in source:
            violations.append(f"{path} -> PlanMutationRecord")
        if ".query" in source:
            violations.append(f"{path} -> ad-hoc query")

    assert not violations, f"the tool boundary reads storage directly: {violations}"


def test_the_flag_is_read_through_the_canonical_registry():
    """Not ``os.getenv``. A private read would be invisible to
    ``/health?deep=1``, the ``[FLAGS]`` boot line and the drift tests, and it
    would parse ``true`` as OFF (§44)."""
    from app import feature_flags

    assert coach_plan_tools.FLAG_KEY in feature_flags.FLAGS_BY_KEY
    flag = feature_flags.FLAGS_BY_KEY[coach_plan_tools.FLAG_KEY]
    assert flag.default is False
    assert flag.parsed_by == feature_flags.PARSED_BY_REGISTRY

    for path in _package_modules():
        for imported, _lineno in _python_imports(path):
            assert not _module_matches(imported, "os"), path


def test_the_package_stays_below_the_coach_and_the_providers():
    """One-directional: the Coach calls in, this package never calls back.

    An import of ``ai_coach`` here would also make the module graph circular
    the moment the Coach imports this package, which it does.
    """
    forbidden = (
        "app.services.ai_coach", "app.services.ai_stream",
        "app.services.ai_pipeline", "app.services.prompt_builder",
        "app.services.context_builder", "app.prompts", "app.blueprints",
        "openai", "anthropic", "boto3",
    )
    violations = []
    for path in _package_modules():
        for imported, lineno in _python_imports(path):
            if any(_module_matches(imported, p) for p in forbidden):
                violations.append(f"{path}:{lineno} -> {imported}")

    assert not violations, f"the tool boundary reaches upward: {violations}"


# ── What may be said, and to whom ────────────────────────────────────────────

def test_the_error_vocabulary_is_closed_and_fully_documented():
    codes = {value for name, value in vars(results).items()
             if name.startswith("ERROR_")}

    assert codes == set(results._ERROR_MESSAGES)
    assert len(codes) == len({code.upper() for code in codes})
    # Every domain exception the boundary can raise has a code of its own, so
    # nothing user-actionable arrives dressed as INTERNAL_ERROR.
    mapped = {code for _exception, code in results._ERROR_BY_EXCEPTION}
    assert mapped <= codes
    assert results.ERROR_INTERNAL not in mapped


def test_no_module_in_the_package_can_log_audit_material():
    """Arguments, plan text, summaries, keys and fingerprints must never reach
    a log line (§60). The executor's single call site logs the tool name and a
    bounded outcome; nothing else in the package logs at all.
    """
    # AST, not a substring scan: "print(" also matches "fingerprint(", which is
    # a real identifier in this package.
    emitters = {"print", "logger", "current_app", "capture_message"}
    allowed = {PACKAGE_ROOT / "executor.py"}
    for path in _package_modules():
        if path in allowed:
            continue
        for node in ast.walk(_tree(path)):
            name = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            assert name not in emitters, f"{path}:{node.lineno} can emit: {name}"

    # What the one permitted logger may interpolate. Checking the ARGUMENTS
    # rather than the format string is the meaningful test: the format is a
    # server-authored constant, the arguments are where user or model data
    # would enter. Each of these is fixed-cardinality — a request id, a tool
    # name from a closed set, an outcome from a closed set (§61).
    loggable_names = {"name", "outcome", "failure"}
    loggable_calls = {"current_request_id"}
    calls = [
        node for node in ast.walk(_tree(PACKAGE_ROOT / "executor.py"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("info", "warning", "error", "exception", "debug")
    ]
    assert calls
    for call in calls:
        assert isinstance(call.args[0], ast.Constant), call.lineno
        # No keyword arguments at all. `exc_info=True` is the one that matters:
        # it is not itself audit material, but it emits the exception's own
        # message, and the likely unexpected failure here is a SQLAlchemy
        # StatementError whose message embeds the bound `plan_data`. `extra=`
        # and `stack_info=` smuggle unvetted material the same way, and none of
        # the three is visible to the positional-argument check below.
        assert not call.keywords, (
            f"executor.py:{call.lineno} passes a logging keyword that can "
            "carry unvetted material")
        for argument in call.args[1:]:
            if isinstance(argument, ast.Name):
                assert argument.id in loggable_names, call.lineno
            elif isinstance(argument, ast.Call):
                assert isinstance(argument.func, ast.Name), call.lineno
                assert argument.func.id in loggable_calls, call.lineno
            else:
                raise AssertionError(
                    f"executor.py:{call.lineno} logs an unvetted expression")


def test_the_result_shape_is_a_closed_set_of_keys():
    """Whatever appears here is billed on the next model call and can be
    paraphrased to the user; the set of things that may appear is fixed."""
    allowed = {"status", "operation", "plan_version", "change", "summary",
               "note", "error", "message", "detail", "reason_codes"}
    literal_keys = set()
    for path in (PACKAGE_ROOT / "results.py", PACKAGE_ROOT / "executor.py"):
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Dict):
                literal_keys |= {
                    key.value for key in node.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
            elif (isinstance(node, ast.Subscript)
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                literal_keys.add(node.slice.value)

    # Dict literals in these modules are results and the change projection; the
    # change projection's own keys are command fields, which are user intent.
    command_fields = {"day", "target_day", "exercise", "replacement",
                      "sets", "reps"}
    assert literal_keys <= allowed | command_fields, sorted(
        literal_keys - allowed - command_fields)
