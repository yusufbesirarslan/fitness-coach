import ast
import re
from pathlib import Path

import pytest


WEB_REQUIREMENTS = Path("requirements.txt")
MCP_REQUIREMENTS = Path("requirements-mcp.txt")
DEV_REQUIREMENTS = Path("requirements-dev.txt")
CI_WORKFLOW = Path(".github/workflows/ci.yml")
APP_ROOT = Path("app")


FORBIDDEN_WEB_REQUIREMENTS = {'mcp', 'pytest', 'pytest-cov'}


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
