"""Command-line entry point for AxisAI Sprint 0 frontend auditing."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .app import ROOT, create_audit_app
from .plan import print_capture_plan, resolve_capture_plan
from .preflight import run_preflight
from .seed import seed_all
from .validation import (
    AuditValidationError,
    validate_document,
    validate_findings,
    validate_inventory,
    validate_manifest,
    validate_report_xrefs,
    validate_source_provenance,
)


SPRINT_DIR = ROOT / "docs" / "frontend-readiness" / "sprint-0"
INVENTORY_PATH = SPRINT_DIR / "inventory.json"
REQUIRED_REPORTS = (
    "verified-audit.md", "route-inventory.md", "template-and-js-map.md",
    "design-system-inventory.md", "user-flow-map.md",
    "feature-flag-feasibility.md", "visual-qa-harness.md",
    "beta-risk-register.md", "implementation-backlog.md",
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _default_output() -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "artifacts" / "ui-audit" / run_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.frontend_audit")
    sub = parser.add_subparsers(dest="command", required=True)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--output", type=Path, default=ROOT / "artifacts" / "ui-audit" / "preflight")
    sub.add_parser("inventory")
    seed = sub.add_parser("seed")
    seed.add_argument("--database", type=Path, default=ROOT / "artifacts" / "ui-audit" / "axis_frontend_audit.db")
    seed.add_argument("--scenario", default="all", choices=["all"])
    serve = sub.add_parser("serve")
    serve.add_argument("--database", type=Path, default=ROOT / "artifacts" / "ui-audit" / "axis_frontend_audit.db")
    serve.add_argument("--port", type=int, default=5055)
    capture = sub.add_parser("capture")
    capture.add_argument("--tier", required=True, choices=["smoke", "responsive", "stress", "cross-browser", "full"])
    capture.add_argument("--output", type=Path, default=None)
    capture.add_argument("--database", type=Path, default=None)
    capture.add_argument("--dry-run", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("--manifest", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    inventory = _json(INVENTORY_PATH)

    if args.command == "preflight":
        record = run_preflight(args.output)
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / "preflight.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(path)
        return 0 if record["success"] else 1

    if args.command == "inventory":
        validate_inventory(inventory)
        print(f"inventory routes: {len(inventory['routes'])}")
        return 0

    if args.command == "seed":
        app = create_audit_app(args.database)
        print(json.dumps(seed_all(app), indent=2))
        return 0

    if args.command == "serve":
        app = create_audit_app(args.database)
        seed_all(app)
        app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)
        return 0

    if args.command == "capture":
        plan = resolve_capture_plan(inventory, args.tier)
        print_capture_plan(plan, inventory["viewports"])
        if args.dry_run:
            return 0
        output = args.output or _default_output()
        database = args.database or output / "axis_frontend_audit.db"
        app = create_audit_app(database)
        seed_all(app)
        from .runner import run_capture_plan

        manifest = run_capture_plan(app, inventory, plan, output)
        captured = sum(item["status"] == "captured" for item in manifest["captures"])
        blocked = len(manifest["captures"]) - captured
        print(f"captured: {captured}; blocked: {blocked}; manifest: {output / 'manifest.json'}")
        mandatory_blocked = any(item["browser"] == "chromium" and item["status"] != "captured" for item in manifest["captures"])
        return 1 if mandatory_blocked else 0

    if args.command == "verify":
        validate_inventory(inventory)
        validate_document("warning-baseline", _json(SPRINT_DIR / "warning-baseline.json"))
        provenance = _json(SPRINT_DIR / "source-provenance.json")
        validate_source_provenance(provenance, ROOT / provenance["preserved_path"])
        findings = _json(SPRINT_DIR / "finding-map.json")
        validate_findings(findings)
        missing_reports = [name for name in REQUIRED_REPORTS if not (SPRINT_DIR / name).is_file()]
        if missing_reports:
            raise AuditValidationError(f"missing required reports: {missing_reports}")
        evidence_ids: set[str] = set()
        if args.manifest:
            manifest = _json(args.manifest)
            validate_manifest(manifest, evidence_root=args.manifest.parent)
            evidence_ids = {capture["id"] for capture in manifest["captures"]}
        validate_report_xrefs(
            _json(SPRINT_DIR / "report-xref.json"),
            finding_ids={finding["id"] for finding in findings["findings"]},
            evidence_ids=evidence_ids,
        )
        print("audit artifacts valid")
        return 0

    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
