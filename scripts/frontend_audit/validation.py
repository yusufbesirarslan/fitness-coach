"""Strict schema and cross-reference validation for audit artifacts."""

from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from . import SCHEMA_VERSION


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "docs" / "frontend-readiness" / "sprint-0" / "schemas"
SCHEMA_NAMES = {
    "inventory",
    "findings",
    "capture-result",
    "evidence-manifest",
    "warning-baseline",
    "report-xref",
    "source-provenance",
}


class AuditValidationError(ValueError):
    """An audit artifact is invalid or internally inconsistent."""


def schema_path(name: str) -> Path:
    if name not in SCHEMA_NAMES:
        raise AuditValidationError(f"unknown schema: {name}")
    return SCHEMA_DIR / f"{name}-1.0.0.schema.json"


def _load_json(path: Path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def validate_document(name: str, document: dict[str, Any]) -> None:
    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise AuditValidationError(f"unsupported schema_version {version}")
    validator = Draft202012Validator(_load_json(schema_path(name)))
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "document"
        raise AuditValidationError(f"{location}: {error.message}")


def _ensure_unique(values: Iterable[str], label: str) -> None:
    duplicate = next((value for value, count in Counter(values).items() if count > 1), None)
    if duplicate is not None:
        raise AuditValidationError(f"duplicate {label}: {duplicate}")


def validate_inventory(document: dict[str, Any], known_routes: set[str] | None = None) -> None:
    validate_document("inventory", document)
    routes = document["routes"]
    _ensure_unique((route["id"] for route in routes), "route id")
    _ensure_unique((route["route"] for route in routes), "route path")
    scenario_ids = {scenario["id"] for scenario in document["scenarios"]}
    viewport_ids = set(document["viewports"])
    for route in routes:
        coverage = route["coverage"]
        missing_scenarios = set(coverage["scenarios"]) - scenario_ids
        missing_viewports = set(coverage["viewports"]) - viewport_ids
        if missing_scenarios:
            raise AuditValidationError(
                f"route {route['id']} references unknown scenarios: {sorted(missing_scenarios)}"
            )
        if missing_viewports:
            raise AuditValidationError(
                f"route {route['id']} references unknown viewports: {sorted(missing_viewports)}"
            )
        if coverage["tier"] != "excluded" and "chromium" not in coverage["browsers"]:
            raise AuditValidationError(f"route {route['id']} lacks mandatory chromium coverage")
    if known_routes is not None:
        for route in routes:
            if not route.get("audit_only") and route["route"] not in known_routes:
                raise AuditValidationError(f"stale inventory route: {route['route']}")


def validate_manifest(document: dict[str, Any], evidence_root: Path) -> None:
    validate_document("evidence-manifest", document)
    _ensure_unique((capture["id"] for capture in document["captures"]), "capture id")
    for capture in document["captures"]:
        if capture["status"] == "captured":
            relative = capture.get("screenshot")
            if not relative or not (evidence_root / relative).is_file():
                raise AuditValidationError(f"missing evidence file: {relative}")


def validate_findings(document: dict[str, Any]) -> None:
    validate_document("findings", document)
    _ensure_unique((finding["id"] for finding in document["findings"]), "finding id")


def validate_report_xrefs(
    document: dict[str, Any], finding_ids: set[str], evidence_ids: set[str]
) -> None:
    validate_document("report-xref", document)
    _ensure_unique((record["id"] for record in document["records"]), "xref id")
    for record in document["records"]:
        for finding_id in record["finding_ids"]:
            if finding_id not in finding_ids:
                raise AuditValidationError(f"unknown finding id: {finding_id}")
        for evidence_id in record["evidence_ids"]:
            if evidence_id not in evidence_ids:
                raise AuditValidationError(f"invalid evidence reference: {evidence_id}")


def validate_source_provenance(document: dict[str, Any], source_path: Path) -> None:
    validate_document("source-provenance", document)
    data = source_path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != document["sha256"]:
        raise AuditValidationError("source checksum mismatch")
    if len(data) != document["source_size_bytes"]:
        raise AuditValidationError("source byte length mismatch")
