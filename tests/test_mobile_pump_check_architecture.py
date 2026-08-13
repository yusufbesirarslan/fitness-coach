import inspect

from app import create_app
from app.models import PumpCheck
from app.services.mobile_pump_checks import analysis, service


def test_one_pump_check_persistence_authority_and_no_future_routes():
    app = create_app()
    pump_tables = [
        table for table in PumpCheck.metadata.tables
        if "pump" in table.lower() and "check" in table.lower()
    ]
    assert set(pump_tables) == {
        "pump_check", "pump_check_comment", "pump_check_like"}
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/v1/pump-checks" in rules
    assert "/api/v1/pump-checks/<pump_check_token>" in rules
    assert not any(
        rule.startswith("/api/v1/pump-checks/")
        and any(word in rule for word in ("history", "comparison", "reanalysis"))
        for rule in rules
    )


def test_mobile_routes_use_bearer_auth_and_one_serializer():
    from app.blueprints import mobile_pump_checks

    create_source = inspect.getsource(mobile_pump_checks.create_pump_check)
    get_source = inspect.getsource(mobile_pump_checks.get_pump_check)
    assert "g.mobile_user.id" in create_source
    assert "g.mobile_user.id" in get_source
    assert "_response(" in create_source
    assert "_response(" in get_source
    assert "serialize_pump_check" in inspect.getsource(mobile_pump_checks._response)


def test_serializer_and_provider_boundary_do_not_publish_internal_authority():
    serializer_source = inspect.getsource(service.serialize_pump_check)
    adapter_source = inspect.getsource(analysis.analyze_image)
    assert "row.public_id" in serializer_source
    assert '"image_key":' not in serializer_source
    assert '"user_id":' not in serializer_source
    assert "parse_analysis(raw)" in adapter_source
    assert "_bedrock_validate_image" in adapter_source
