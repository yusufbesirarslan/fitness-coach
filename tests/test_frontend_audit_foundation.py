import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPRINT = ROOT / "docs" / "frontend-readiness" / "sprint-0"


def test_audit_dependencies_are_pinned():
    requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "playwright==1.61.0" in requirements
    assert "jsonschema==4.26.0" in requirements


def test_warning_baseline_is_frozen_to_exact_frontend_command():
    baseline = json.loads((SPRINT / "warning-baseline.json").read_text(encoding="utf-8"))
    assert baseline["schema_version"] == "1.0.0"
    assert baseline["result"] == {"passed": 48, "warnings": 343}
    assert baseline["python_version"] == "3.14.3"
    assert baseline["pytest"]["working_directory_suffix"] == "fitness-coach"
    assert baseline["pytest"]["arguments"][-1] == "-q"
    assert baseline["pytest"]["addopts"] == '-m "not load"'
    assert set(baseline["warning_categories"]) == {"DeprecationWarning"}


def test_source_provenance_pins_the_supplied_audit():
    provenance = json.loads((SPRINT / "source-provenance.json").read_text(encoding="utf-8"))
    assert provenance["schema_version"] == "1.0.0"
    assert provenance["original_filename"] == "AxisAI-frontend-readiness-audit.md"
    assert provenance["sha256"] == "302aeaf22f9d834f41216ee420b5d1201b987f26b77715b4b67da124b5118b92"
    assert provenance["source_size_bytes"] == 43884
    assert provenance["extraction_rules_version"] == "1.0.0"
    assert provenance["canonical_finding_map_version"] == "1.0.0"


def test_approved_design_and_execution_plan_are_checked_in():
    assert (ROOT / "docs" / "superpowers" / "specs" / "2026-07-22-sprint0-frontend-readiness-design.md").is_file()
    assert (ROOT / "docs" / "superpowers" / "plans" / "2026-07-22-sprint0-frontend-readiness.md").is_file()
