"""Structural guards for the Pump Check comparison boundary."""
import inspect
from pathlib import Path

from app import create_app
from app.models import PumpCheck, PumpCheckComparison
from app.services.mobile_pump_check_comparisons import analysis, service


DOC_PATHS = (
    "docs/PUMP_CHECK.md",
    "docs/PUMP_CHECK_DESIGN.md",
    "docs/MOBILE_PUMP_CHECK.md",
)


def _comparison_source():
    from app.blueprints import mobile_pump_check_comparisons

    return "\n".join(
        inspect.getsource(module)
        for module in (
            mobile_pump_check_comparisons, service, analysis)
    )


def test_only_explicit_create_and_read_routes_exist():
    rules = {rule.rule for rule in create_app().url_map.iter_rules()}

    assert "/api/v1/pump-check-comparisons" in rules
    assert "/api/v1/pump-check-comparisons/<comparison_id>" in rules
    for rule in rules:
        if not rule.startswith("/api/v1/pump-check-comparisons"):
            continue
        assert not any(word in rule for word in (
            "history", "timeline", "automatic", "previous"))


def test_one_comparison_persistence_authority():
    tables = {
        table for table in PumpCheck.metadata.tables
        if "comparison" in table.lower()
    }

    assert tables == {
        "pump_check_comparison", "pump_check_comparison_request"}
    columns = set(PumpCheckComparison.__table__.columns.keys())
    assert {"analysis", "comparability"} <= columns


def test_comparison_code_has_no_second_provider_or_sensitive_output():
    source = _comparison_source()

    for forbidden in (
            "openai_client", "generate_presigned_url", "image_url",
            "idempotency_key=%", "fingerprint=%", "provider_output",
            "upload_image("):
        assert forbidden not in source


def test_routes_use_bearer_identity_and_one_serializer():
    from app.blueprints import mobile_pump_check_comparisons as routes

    create_source = inspect.getsource(routes.create_pump_check_comparison)
    get_source = inspect.getsource(routes.get_pump_check_comparison)

    assert "g.mobile_user.id" in create_source
    assert "g.mobile_user.id" in get_source
    assert "_response(" in create_source
    assert "_response(" in get_source
    assert "serialize_comparison" in inspect.getsource(routes._response)


def test_get_route_never_claims_analyzes_or_reads_storage():
    from app.blueprints import mobile_pump_check_comparisons as routes

    get_source = inspect.getsource(routes.get_pump_check_comparison)

    for forbidden in (
            "create_or_replay", "_claim_analysis", "analyze_images",
            "get_object_bytes", "prepare_image_for_vision"):
        assert forbidden not in get_source


def test_serializer_publishes_only_opaque_owner_facing_fields():
    serializer_source = inspect.getsource(service.serialize_comparison)

    assert "row.public_id" in serializer_source
    assert "baseline_pump_check.public_id" in serializer_source
    for forbidden in ('"user_id":', '"image_key":', '"analysis_attempt":',
                      '"analysis_started_at":', '"analysis_failure_kind":'):
        assert forbidden not in serializer_source


def test_provider_boundary_is_bedrock_only_and_bounded():
    adapter_source = inspect.getsource(analysis.analyze_images)

    assert "_bedrock_compare_images" in adapter_source
    assert "max_tokens=1200" in adapter_source
    assert "parse_analysis(raw, source_quality_cap)" in adapter_source


def test_stored_source_narratives_are_never_forwarded_to_the_provider():
    runner = inspect.getsource(service._run_or_reuse_analysis)

    # Only the body region travels with the two normalized images.
    assert '{"body_region": media.body_region}' in runner
    for forbidden in ("summary", "observations", "next_check_guidance"):
        assert forbidden not in runner


def test_external_work_never_runs_inside_a_transaction():
    runner = inspect.getsource(service._run_or_reuse_analysis)
    loader = inspect.getsource(service._load_image)

    assert "db.session.commit()" not in runner
    assert "with db.session.begin" not in runner
    assert "commit" not in loader
    # The claim commits and releases before any provider work begins.
    assert "db.session.commit()" in inspect.getsource(service._claim_analysis)


def test_postgresql_ci_executes_comparison_concurrency_suite():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "tests/test_mobile_pump_check_comparison_pg.py" in workflow


def test_canonical_docs_name_version_routes_and_privacy_contract():
    docs = "\n".join(
        Path(path).read_text(encoding="utf-8") for path in DOC_PATHS)

    for required in (
        "pump-check-comparison-analysis/v1",
        "POST /api/v1/pump-check-comparisons",
        "GET /api/v1/pump-check-comparisons/<comparison_id>",
        "baseline_pump_check_id",
        "current_pump_check_id",
        "not_comparable",
    ):
        assert required in docs
