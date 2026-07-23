import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def inventory_document():
    return {
        "schema_version": "1.0.0",
        "generated_at": "2026-07-22T12:00:00+03:00",
        "viewports": {
            "mobile": {"width": 390, "height": 844},
            "desktop": {"width": 1440, "height": 900},
        },
        "scenarios": [{"id": "anonymous", "clock": "audit-default"}],
        "routes": [{
            "id": "landing",
            "route": "/welcome",
            "template": "landing.html",
            "auth_required": False,
            "beta_critical": True,
            "coverage": {
                "tier": "representative",
                "scenarios": ["anonymous"],
                "viewports": ["mobile", "desktop"],
                "browsers": ["chromium"],
                "stress_profiles": [],
            },
        }],
    }


def test_checked_in_schemas_are_versioned_and_valid_json_schema():
    from scripts.frontend_audit.validation import SCHEMA_NAMES, schema_path

    assert SCHEMA_NAMES == {
        "inventory", "findings", "capture-result", "evidence-manifest",
        "warning-baseline", "report-xref", "source-provenance",
    }
    for name in SCHEMA_NAMES:
        schema = json.loads(schema_path(name).read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(f"/{name}-1.0.0.schema.json")


def test_unknown_schema_version_fails_clearly():
    from scripts.frontend_audit.validation import AuditValidationError, validate_document

    document = inventory_document()
    document["schema_version"] = "2.0.0"
    with pytest.raises(AuditValidationError, match="unsupported schema_version 2.0.0"):
        validate_document("inventory", document)


def test_invalid_coverage_enum_fails_schema_validation():
    from scripts.frontend_audit.validation import AuditValidationError, validate_document

    document = inventory_document()
    document["routes"][0]["coverage"]["tier"] = "everything"
    with pytest.raises(AuditValidationError, match="coverage.tier"):
        validate_document("inventory", document)


def test_duplicate_route_ids_and_stale_routes_fail_semantic_validation():
    from scripts.frontend_audit.validation import AuditValidationError, validate_inventory

    document = inventory_document()
    document["routes"].append(dict(document["routes"][0]))
    with pytest.raises(AuditValidationError, match="duplicate route id: landing"):
        validate_inventory(document, known_routes={"/welcome"})

    document["routes"].pop()
    with pytest.raises(AuditValidationError, match="stale inventory route: /welcome"):
        validate_inventory(document, known_routes={"/login"})


def test_manifest_rejects_missing_success_evidence(tmp_path):
    from scripts.frontend_audit.validation import AuditValidationError, validate_manifest

    manifest = {
        "schema_version": "1.0.0",
        "run_id": "run-1",
        "environment": {"browser": "chromium", "supported": True},
        "captures": [{
            "id": "landing-anonymous-mobile-chromium",
            "route_id": "landing",
            "scenario_id": "anonymous",
            "state": "default",
            "viewport": "mobile",
            "browser": "chromium",
            "status": "captured",
            "screenshot": "missing.png",
        }],
    }
    with pytest.raises(AuditValidationError, match="missing evidence file: missing.png"):
        validate_manifest(manifest, evidence_root=tmp_path)


def test_findings_and_report_xrefs_reject_duplicate_or_unknown_ids():
    from scripts.frontend_audit.validation import (
        AuditValidationError,
        validate_findings,
        validate_report_xrefs,
    )

    findings = {
        "schema_version": "1.0.0",
        "extraction_rules_version": "1.0.0",
        "findings": [
            {"id": "EXT-001", "claim": "One", "source_refs": ["3.1 row 1"],
             "status": "BLOCKED_BY_MISSING_ENVIRONMENT"},
            {"id": "EXT-001", "claim": "Two", "source_refs": ["3.2 row 1"],
             "status": "PRODUCT_DECISION_REQUIRED"},
        ],
    }
    with pytest.raises(AuditValidationError, match="duplicate finding id: EXT-001"):
        validate_findings(findings)

    xrefs = {
        "schema_version": "1.0.0",
        "records": [{
            "id": "XREF-001", "report": "verified-audit.md",
            "finding_ids": ["EXT-999"], "evidence_ids": [],
        }],
    }
    with pytest.raises(AuditValidationError, match="unknown finding id: EXT-999"):
        validate_report_xrefs(xrefs, finding_ids={"EXT-001"}, evidence_ids=set())


def test_source_provenance_rejects_changed_source(tmp_path):
    from scripts.frontend_audit.validation import AuditValidationError, validate_source_provenance

    source = tmp_path / "source.md"
    source.write_bytes(b"preserved")
    document = {
        "schema_version": "1.0.0",
        "original_filename": "source.md",
        "import_date": "2026-07-22",
        "source_size_bytes": 9,
        "source_encoding": "UTF-8",
        "sha256": "0" * 64,
        "preserved_path": "source.md",
        "extraction_rules_version": "1.0.0",
        "canonical_finding_map_version": "1.0.0",
        "importer": "test",
        "immutability_policy": "preserve",
    }
    with pytest.raises(AuditValidationError, match="source checksum mismatch"):
        validate_source_provenance(document, source)
