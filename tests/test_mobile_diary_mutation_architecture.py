"""Executable structural guards for the mobile diary mutation boundary."""
import ast
from pathlib import Path


SERVICE = Path("app/services/mobile_diary_mutation/service.py")
ROUTES = Path("app/blueprints/mobile_nutrition.py")
SERIALIZER = Path("app/services/mobile_nutrition/serialization.py")
REVISION = Path("app/services/mobile_nutrition/revision.py")


def _tree(path):
    assert path.is_file(), f"architecture guard target missing: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(path):
    imported = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_mutation_service_has_no_transport_or_provider_dependency():
    imported = _imports(SERVICE)
    assert not {
        name for name in imported
        if name.startswith(("flask", "requests", "app.services.fatsecret",
                            "app.services.mobile_food_discovery"))
    }


def test_mutation_service_reuses_canonical_serializer_and_row_lock():
    tree = _tree(SERVICE)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "app.services.mobile_nutrition.serialization"
        for alias in node.names
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "logged_meal" in imported_names
    assert "with_for_update" in called_attributes


def test_mutation_service_has_no_generic_mass_assignment():
    calls = [
        node for node in ast.walk(_tree(SERVICE))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "setattr"
    ]
    assert calls == []


def test_revision_and_serializer_layers_remain_transport_and_orm_free():
    for path in (REVISION, SERIALIZER):
        imported = _imports(path)
        assert not {
            name for name in imported
            if name.startswith(("flask", "sqlalchemy", "app.models",
                                "app.extensions"))
        }


def test_mobile_mutation_views_use_the_one_mobile_auth_boundary(app):
    rules = {
        (rule.rule, method): app.view_functions[rule.endpoint]
        for rule in app.url_map.iter_rules()
        for method in rule.methods
        if rule.rule == "/api/v1/nutrition/logs/<entry_token>"
        and method in {"PATCH", "DELETE"}
    }
    assert set(rules) == {
        ("/api/v1/nutrition/logs/<entry_token>", "PATCH"),
        ("/api/v1/nutrition/logs/<entry_token>", "DELETE"),
    }
    assert all(getattr(view, "_require_mobile_auth", False)
               for view in rules.values())
