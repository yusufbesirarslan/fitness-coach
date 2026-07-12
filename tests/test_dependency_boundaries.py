from pathlib import Path


WEB_REQUIREMENTS = Path("requirements.txt")
MCP_REQUIREMENTS = Path("requirements-mcp.txt")
DEV_REQUIREMENTS = Path("requirements-dev.txt")
CI_WORKFLOW = Path(".github/workflows/ci.yml")


def _requirement_lines(path):
    assert path.is_file(), f"missing dependency file: {path}"
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_web_requirements_exclude_mcp_and_test_tooling():
    web = _requirement_lines(WEB_REQUIREMENTS)

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


def test_both_ci_jobs_install_development_requirements():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count("pip install -r requirements-dev.txt") == 2
    assert "pip install -r requirements.txt" not in workflow
