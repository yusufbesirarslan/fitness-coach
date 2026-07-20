import ast
import re
from pathlib import Path

import pytest


WEB_REQUIREMENTS = Path("requirements.txt")
MCP_REQUIREMENTS = Path("requirements-mcp.txt")
DEV_REQUIREMENTS = Path("requirements-dev.txt")
CI_WORKFLOW = Path(".github/workflows/ci.yml")
APP_ROOT = Path("app")
TRAINING_LAYERS = {
    "history": Path("app/services/training_history"),
    "progression": Path("app/services/training_progression"),
    "planning": Path("app/services/training_planning"),
}

UPPER_OR_PROVIDER_PREFIXES = (
    "app.services.adaptive_plan_context",
    "app.services.context_builder",
    "app.services.ai_coach",
    "app.services.ai_pipeline",
    "app.services.ai_stream",
    "app.services.prompt_builder",
    "app.prompts",
    "openai",
    "anthropic",
)

FORBIDDEN_TRAINING_IMPORTS = {
    "history": UPPER_OR_PROVIDER_PREFIXES + (
        "app.services.training_progression",
        "app.services.training_planning",
    ),
    "progression": UPPER_OR_PROVIDER_PREFIXES + (
        "app.services.training_planning",
    ),
    "planning": UPPER_OR_PROVIDER_PREFIXES,
}


FORBIDDEN_WEB_REQUIREMENTS = {'mcp', 'pytest', 'pytest-cov'}


def _module_matches(module, prefix):
    return module == prefix or module.startswith(prefix + ".")


def _python_package(path):
    parts = path.parts
    app_indexes = [index for index, part in enumerate(parts) if part == "app"]
    if not app_indexes:
        return ""
    return ".".join(parts[app_indexes[-1]:-1])


def _resolved_import_from_module(path, node):
    if not node.level:
        return node.module or ""

    package = _python_package(path).split(".")
    parents_to_remove = node.level - 1
    if parents_to_remove:
        package = package[:-parents_to_remove]
    if node.module:
        package.extend(node.module.split("."))
    return ".".join(package)


def _python_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_import_from_module(path, node)
            if module:
                imports.append((module, node.lineno))
            imports.extend(
                (
                    ".".join(part for part in (module, alias.name) if part),
                    node.lineno,
                )
                for alias in node.names
                if alias.name != "*"
            )
    return imports


def _training_import_violations(training_layers):
    violations = []
    reported = set()
    for layer, root in training_layers.items():
        forbidden = FORBIDDEN_TRAINING_IMPORTS[layer]
        for path in root.rglob("*.py"):
            for imported, lineno in _python_imports(path):
                matched = next(
                    (prefix for prefix in forbidden if _module_matches(imported, prefix)),
                    None,
                )
                report_key = (path, lineno, matched)
                if matched and report_key not in reported:
                    violations.append(f"{path}:{lineno} -> {imported}")
                    reported.add(report_key)
    return violations


def _adaptive_plan_serializer_ownership(app_root, adapter):
    definitions = []
    competing_json_serializers = []

    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports_adaptive_plan = any(
            isinstance(node, ast.ImportFrom)
            and _module_matches(
                _resolved_import_from_module(path, node),
                "app.services.training_planning",
            )
            and any(alias.name == "AdaptivePlan" for alias in node.names)
            for node in ast.walk(tree)
        )
        json_modules = {
            alias.asname or "json"
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "json"
        }
        json_functions = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and not node.level
            and node.module == "json"
            for alias in node.names
            if alias.name in {"dump", "dumps"}
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name == "serialize_adaptive_plan"
            ):
                definitions.append(f"{path}:{node.lineno}")
            if path != adapter and imports_adaptive_plan and isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id in json_modules
                    and func.attr in {"dump", "dumps"}
                ) or (
                    isinstance(func, ast.Name)
                    and func.id in json_functions
                ):
                    competing_json_serializers.append(f"{path}:{node.lineno}")

    return definitions, competing_json_serializers


def test_app_runtime_does_not_import_fitx_mcp():
    violations = []
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            else:
                continue
            if any(name == "fitx_mcp" or name.startswith("fitx_mcp.") for name in imported):
                violations.append(f"{path}:{node.lineno}")

    assert not violations, f"production app imports fitx_mcp: {violations}"


def _requirement_lines(path):
    assert path.is_file(), f"missing dependency file: {path}"
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _normalized_requirement_name(line):
    stripped = line.strip()
    if not stripped or stripped.startswith(('#', '-')):
        return None

    match = re.match(r'([A-Za-z0-9][A-Za-z0-9._-]*)', stripped)
    if match is None:
        return None
    return re.sub(r'[-_.]+', '-', match.group(1)).lower()


def _assert_web_requirements_exclude_tooling(lines):
    names = {
        name
        for line in lines
        if (name := _normalized_requirement_name(line)) is not None
    }
    forbidden = names.intersection(FORBIDDEN_WEB_REQUIREMENTS)
    assert not forbidden, f'web requirements contain tooling packages: {sorted(forbidden)}'


def _workflow_jobs(workflow):
    jobs = {}
    current_job = None
    in_jobs = False

    for line in workflow.splitlines():
        if line == 'jobs:':
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line and not line[0].isspace():
            break

        job_header = re.match(r'^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$', line)
        if job_header:
            current_job = job_header.group(1)
            jobs[current_job] = []
        elif current_job is not None:
            jobs[current_job].append(line)

    return {name: '\n'.join(lines) for name, lines in jobs.items()}


def _installed_requirement_files(job_body):
    installed = set()
    for line in job_body.splitlines():
        command = line.strip()
        inline_run = re.match(r'(?:-\s*)?run:\s*(.*)', command)
        if inline_run:
            command = inline_run.group(1)
        if not re.match(r'(?:python\s+-m\s+)?pip\s+install\b', command):
            continue
        installed.update(re.findall(r'(?:^|\s)-r\s+([^\s#]+)', command))
    return installed


def _assert_ci_job_installs_dev(workflow, job_name):
    jobs = _workflow_jobs(workflow)
    assert job_name in jobs, f'missing CI job: {job_name}'

    installed = _installed_requirement_files(jobs[job_name])
    assert 'requirements-dev.txt' in installed
    assert 'requirements.txt' not in installed


@pytest.mark.parametrize(
    ('line', 'expected'),
    [
        ('PyTest[plugin]>=10; python_version > \'3.11\'', 'pytest'),
        ('pytest_cov ~= 7.2', 'pytest-cov'),
        ('MCP[CLI] @ https://example.invalid/mcp.whl ; os_name == \'nt\'', 'mcp'),
    ],
)
def test_requirement_names_are_normalized_across_pep_508_syntax(line, expected):
    assert _normalized_requirement_name(line) == expected


@pytest.mark.parametrize(
    'line',
    [
        'PyTest[plugin]>=10; python_version > \'3.11\'',
        'pytest_cov ~= 7.2',
        'MCP[CLI] @ https://example.invalid/mcp.whl ; os_name == \'nt\'',
    ],
)
def test_web_guard_rejects_tooling_regardless_of_requirement_syntax(line):
    with pytest.raises(AssertionError, match='tooling packages'):
        _assert_web_requirements_exclude_tooling([line])


def test_web_requirements_exclude_mcp_and_test_tooling():
    web = _requirement_lines(WEB_REQUIREMENTS)

    _assert_web_requirements_exclude_tooling(web)

    assert "mcp[cli]==1.28.1" not in web
    assert "pytest==9.1.1" not in web
    assert "pytest-cov==7.1.0" not in web


def test_mcp_requirements_include_web_runtime():
    mcp = _requirement_lines(MCP_REQUIREMENTS)

    assert "-r requirements.txt" in mcp
    assert "mcp[cli]==1.28.1" in mcp


def test_dev_requirements_include_mcp_and_test_tooling():
    dev = _requirement_lines(DEV_REQUIREMENTS)

    assert "-r requirements-mcp.txt" in dev
    assert "pytest==9.1.1" in dev
    assert "pytest-cov==7.1.0" in dev


@pytest.mark.parametrize('job_name', ['tests', 'migration-drift'])
def test_each_ci_job_installs_development_requirements(job_name):
    workflow = CI_WORKFLOW.read_text(encoding='utf-8')

    _assert_ci_job_installs_dev(workflow, job_name)


@pytest.mark.parametrize(
    ('job_name', 'install_commands'),
    [
        ('tests', ['echo missing-install']),
        (
            'migration-drift',
            [
                'pip install -r requirements-dev.txt',
                'pip install -r requirements.txt',
            ],
        ),
    ],
)
def test_ci_job_guard_rejects_missing_or_direct_web_install(job_name, install_commands):
    job_steps = [f'      - run: {command}' for command in install_commands]
    workflow = '\n'.join(['jobs:', f'  {job_name}:', '    steps:', *job_steps])

    with pytest.raises(AssertionError):
        _assert_ci_job_installs_dev(workflow, job_name)


def test_ci_job_guard_does_not_credit_installs_from_other_jobs():
    workflow = '\n'.join(
        [
            'jobs:',
            '  tests:',
            '    steps:',
            '      - run: pip install -r requirements-dev.txt',
            '  migration-drift:',
            '    steps:',
            '      - run: echo missing-install',
            '  lint:',
            '    steps:',
            '      - run: pip install -r requirements-dev.txt',
        ]
    )

    with pytest.raises(AssertionError):
        _assert_ci_job_installs_dev(workflow, 'migration-drift')


def test_adaptive_training_layers_preserve_one_way_imports():
    violations = _training_import_violations(TRAINING_LAYERS)

    assert not violations, f"reverse/provider training imports: {violations}"


def test_adaptive_plan_prompt_serializer_has_one_owner():
    adapter = Path("app/services/adaptive_plan_context.py")

    definitions, competing_json_serializers = (
        _adaptive_plan_serializer_ownership(APP_ROOT, adapter)
    )

    assert len(definitions) == 1, (
        f"AdaptivePlan serializer definitions: {definitions}"
    )
    assert definitions[0].replace("\\", "/").startswith(
        "app/services/adaptive_plan_context.py:"
    )
    assert not competing_json_serializers, (
        f"competing AdaptivePlan serializers: {competing_json_serializers}"
    )


def test_training_import_guard_reports_reverse_and_provider_imports(tmp_path):
    history = tmp_path / "history"
    history.mkdir()
    violation = history / "violation.py"
    violation.write_text(
        "import openai\nfrom app.services.training_planning import AdaptivePlan\n",
        encoding="utf-8",
    )

    violations = _training_import_violations({"history": history})

    assert violations == [
        f"{violation}:1 -> openai",
        f"{violation}:2 -> app.services.training_planning",
    ]


def test_serializer_guard_reports_competing_owner(tmp_path):
    app_root = tmp_path / "app"
    services = app_root / "services"
    services.mkdir(parents=True)
    adapter = services / "adaptive_plan_context.py"
    adapter.write_text(
        "def serialize_adaptive_plan(plan):\n    return '{}'\n",
        encoding="utf-8",
    )
    competitor = services / "competitor.py"
    competitor.write_text(
        "import json\n"
        "from app.services.training_planning import AdaptivePlan\n\n"
        "def serialize_adaptive_plan(plan):\n"
        "    return json.dumps(plan)\n",
        encoding="utf-8",
    )

    definitions, serializers = _adaptive_plan_serializer_ownership(
        app_root, adapter
    )

    assert definitions == [f"{adapter}:1", f"{competitor}:4"]
    assert serializers == [f"{competitor}:5"]


def test_training_import_guard_normalizes_parent_and_relative_imports(tmp_path):
    history = tmp_path / "app" / "services" / "training_history"
    history.mkdir(parents=True)
    parent_import = history / "parent_import.py"
    parent_import.write_text(
        "from app.services import training_planning\n",
        encoding="utf-8",
    )
    relative_parent = history / "relative_parent.py"
    relative_parent.write_text(
        "from .. import training_planning\n",
        encoding="utf-8",
    )
    relative_module = history / "relative_module.py"
    relative_module.write_text(
        "from ..training_planning import AdaptivePlan\n",
        encoding="utf-8",
    )

    violations = set(_training_import_violations({"history": history}))

    assert violations == {
        f"{parent_import}:1 -> app.services.training_planning",
        f"{relative_parent}:1 -> app.services.training_planning",
        f"{relative_module}:1 -> app.services.training_planning",
    }


def test_serializer_guard_tracks_relative_plan_and_json_aliases(tmp_path):
    app_root = tmp_path / "app"
    services = app_root / "services"
    services.mkdir(parents=True)
    adapter = services / "adaptive_plan_context.py"
    adapter.write_text(
        "def serialize_adaptive_plan(plan):\n    return '{}'\n",
        encoding="utf-8",
    )
    competitor = services / "competitor.py"
    competitor.write_text(
        "import json as encoder\n"
        "from json import dumps\n"
        "from .training_planning import AdaptivePlan\n\n"
        "encoded_with_module = encoder.dumps(AdaptivePlan)\n"
        "encoded_directly = dumps(AdaptivePlan)\n",
        encoding="utf-8",
    )

    definitions, serializers = _adaptive_plan_serializer_ownership(
        app_root, adapter
    )

    assert definitions == [f"{adapter}:1"]
    assert serializers == [f"{competitor}:5", f"{competitor}:6"]


def test_serializer_guard_uses_exact_training_planning_prefix(tmp_path):
    app_root = tmp_path / "app"
    services = app_root / "services"
    services.mkdir(parents=True)
    adapter = services / "adaptive_plan_context.py"
    adapter.write_text(
        "def serialize_adaptive_plan(plan):\n    return '{}'\n",
        encoding="utf-8",
    )
    sibling = services / "sibling.py"
    sibling.write_text(
        "import json\n"
        "from app.services.training_planning_extra import AdaptivePlan\n"
        "encoded = json.dumps(AdaptivePlan)\n",
        encoding="utf-8",
    )

    definitions, serializers = _adaptive_plan_serializer_ownership(
        app_root, adapter
    )

    assert definitions == [f"{adapter}:1"]
    assert serializers == []
