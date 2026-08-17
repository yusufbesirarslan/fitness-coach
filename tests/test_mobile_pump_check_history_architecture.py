"""Structural guards for the canonical Pump Check history read model.

Every guard first proves its target exists. A grep-style assertion that passes
because a file was renamed or deleted is worse than no assertion at all.
"""
import ast
import inspect
from pathlib import Path

import pytest

from app import create_app
from app.blueprints import mobile_pump_checks
from app.services.mobile_pump_checks import history, history_cursor


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "app" / "services" / "mobile_pump_checks" / "history.py"
CURSOR = ROOT / "app" / "services" / "mobile_pump_checks" / "history_cursor.py"
BLUEPRINT = ROOT / "app" / "blueprints" / "mobile_pump_checks.py"
MIGRATION = (
    ROOT / "migrations" / "versions"
    / "c1d2e3f4a5b6_add_pump_check_history_index.py")
PATH = "/api/v1/pump-checks"


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _names(tree):
    """Every attribute path and bare name mentioned in the module."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def _imported_modules(tree):
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imported_symbols(tree):
    symbols = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            symbols.update(alias.name for alias in node.names)
    return symbols


# ---------------------------------------------------------------------------
# Non-vacuity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [HISTORY, CURSOR, BLUEPRINT, MIGRATION])
def test_the_guarded_production_files_exist(path):
    assert path.is_file(), path
    assert path.read_text(encoding="utf-8").strip()


def test_the_guarded_public_surface_exists():
    assert callable(history.list_history)
    assert callable(history.parse_page_size)
    assert callable(history_cursor.encode_cursor)
    assert callable(history_cursor.decode_cursor)
    assert callable(mobile_pump_checks.list_pump_checks)


# ---------------------------------------------------------------------------
# Route and authorization
# ---------------------------------------------------------------------------

def test_the_collection_route_exists_and_is_read_only():
    rules = [
        rule for rule in create_app().url_map.iter_rules()
        if rule.rule == PATH and rule.endpoint.endswith("list_pump_checks")
    ]

    assert len(rules) == 1
    assert rules[0].methods - {"HEAD", "OPTIONS"} == {"GET"}


def test_the_route_is_wrapped_in_mobile_bearer_auth():
    decorators = {
        node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
        for function in ast.walk(_tree(BLUEPRINT))
        if isinstance(function, ast.FunctionDef)
        and function.name == "list_pump_checks"
        for node in function.decorator_list
    }

    assert "require_mobile_auth" in decorators


def test_the_owner_scope_comes_from_the_token_and_cannot_be_supplied():
    source = inspect.getsource(mobile_pump_checks.list_pump_checks)

    assert "g.mobile_user.id" in source
    # The only request-derived inputs are paging controls. Matching
    # `request.args.get` specifically keeps the route decorator's own
    # `bp.get("/pump-checks")` out of the result.
    read_args = {
        node.args[0].value
        for node in ast.walk(ast.parse(source.lstrip()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "args"
        and node.args and isinstance(node.args[0], ast.Constant)
    }
    assert read_args == {"limit", "cursor"}


def test_the_read_model_never_accepts_an_owner_from_the_cursor():
    """`list_history` scopes on its `user_id` argument, not on cursor content."""
    signature = inspect.signature(history.list_history)

    assert list(signature.parameters)[0] == "user_id"
    source = inspect.getsource(history.list_history)
    assert "PumpCheck.user_id" not in source  # the filter lives in _query
    assert "decode_cursor(secret, user_id, cursor)" in source


def test_the_query_filters_on_the_owner_and_excludes_legacy_rows():
    source = inspect.getsource(history._query)

    assert "PumpCheck.user_id == user_id" in source
    assert "PumpCheck.captured_at.isnot(None)" in source


# ---------------------------------------------------------------------------
# Source of truth
# ---------------------------------------------------------------------------

def test_history_reads_the_canonical_pump_check_table_only():
    symbols = _imported_symbols(_tree(HISTORY))

    assert "PumpCheck" in symbols
    for foreign in (
            "FeedItem", "FeedItemLike", "FeedHide", "Activity",
            "PumpCheckComparison", "PumpCheckComparisonRequest",
            "PumpCheckLike", "PumpCheckComment"):
        assert foreign not in symbols, foreign


def test_history_does_not_reach_for_feed_or_comparison_services():
    modules = _imported_modules(_tree(HISTORY))

    for foreign in (
            "app.services.feed",
            "app.services.friends",
            "app.services.mobile_pump_check_comparisons",
            "app.services.mobile_pump_check_comparisons.service",
            "app.services.pump_checks"):
        assert foreign not in modules, foreign


# ---------------------------------------------------------------------------
# No side effects
# ---------------------------------------------------------------------------

def test_history_invokes_no_provider_storage_or_comparison():
    names = _names(_tree(HISTORY)) | _imported_modules(_tree(HISTORY))

    for forbidden in (
            "s3_helper", "generate_presigned_url", "upload_image",
            "analyze_image", "bedrock_client", "boto3",
            "create_or_replay", "resolve_eligible_sources",
            "claim_comparison", "analyze_pair"):
        assert forbidden not in names, forbidden


def test_history_never_mutates_the_database():
    names = _names(_tree(HISTORY))

    for forbidden in ("add", "commit", "flush", "delete", "merge", "update"):
        assert forbidden not in names, forbidden


def test_history_declares_no_intelligence_it_is_not_allowed_to_have():
    text = HISTORY.read_text(encoding="utf-8").lower()
    code = "\n".join(
        line for line in text.splitlines()
        if not line.strip().startswith("#"))

    for forbidden in (
            "trend", "progress_score", "readiness", "similarity",
            "best_baseline", "recommend"):
        assert forbidden not in code, forbidden


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def test_the_item_projection_is_exactly_the_lightweight_contract():
    keys = {
        node.value
        for node in ast.walk(ast.parse(inspect.getsource(history._item).lstrip()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert keys == {
        "id", "captured_at", "body_region", "analysis_status",
        "analysis_quality"}


def test_the_projection_publishes_no_storage_or_database_identity():
    source = inspect.getsource(history._item)

    assert "row.public_id" in source
    for forbidden in ("image_key", "image_url", "row.id", "user_id",
                      "description"):
        assert forbidden not in source, forbidden


def test_the_raw_analysis_body_is_never_a_published_value():
    """`analysis` may be READ to derive quality, never emitted as a field."""
    published = [
        value for node in ast.walk(ast.parse(inspect.getsource(history._item).lstrip()))
        if isinstance(node, ast.Dict) for value in node.values
    ]
    assert published, "the projection must build a dict"

    bare_attributes = {
        f"{value.value.id}.{value.attr}"
        for value in published
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
    }

    assert bare_attributes == {"row.public_id", "row.body_region"}


def test_every_writer_of_captured_at_also_writes_the_opaque_id():
    """History filters on `captured_at`, but publishes `public_id`.

    Both columns are nullable, and the endpoint would emit `"id": null` for a
    row that carried one without the other. The two are written together today
    (the mobile create path sets both; the legacy web completion path sets
    neither), which is exactly why filtering on `captured_at` is safe. This
    guard fails if a future writer separates them.
    """
    writers = []
    for path in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(_tree(path)):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "PumpCheck"):
                continue
            fields = {kw.arg for kw in node.keywords}
            writers.append((path.name, fields))

    assert writers, "no PumpCheck construction site found"
    for name, fields in writers:
        assert ("captured_at" in fields) == ("public_id" in fields), name


def test_the_internal_row_id_is_selected_for_ordering_but_never_published():
    """`id` must reach the cursor and the ORDER BY, and stop there."""
    assert "PumpCheck.id" in inspect.getsource(history._query)
    assert "last.id" in inspect.getsource(history.list_history)
    assert "row.id" not in inspect.getsource(history._item)


# ---------------------------------------------------------------------------
# Privacy logging
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [HISTORY, CURSOR])
def test_the_read_model_and_the_cursor_codec_never_log(path):
    names = _names(_tree(path)) | _imported_modules(_tree(path))

    for forbidden in ("logger", "logging", "print", "current_app", "warning",
                      "exception"):
        assert forbidden not in names, f"{path.name}: {forbidden}"


def test_the_route_logs_no_cursor_or_identity():
    source = inspect.getsource(mobile_pump_checks.list_pump_checks)

    assert "logger" not in source
    assert "current_app.logger" not in source


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------

def test_the_cursor_codec_is_pure():
    modules = _imported_modules(_tree(CURSOR))

    for forbidden in ("flask", "app.models", "app.extensions",
                      "sqlalchemy", "app.services.mobile_pump_checks.history"):
        assert forbidden not in modules, forbidden


def test_every_cursor_rejection_uses_one_error_type():
    raised = {
        node.exc.func.id
        for node in ast.walk(_tree(CURSOR))
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
    }

    assert raised == {"InvalidCursor"}


def test_the_boundary_maps_both_rejections_to_permanent_client_errors():
    source = inspect.getsource(mobile_pump_checks.list_pump_checks)

    for code in ("INVALID_PAGE_SIZE", "INVALID_PAGE_CURSOR"):
        assert f'"{code}"' in source
    assert "400, False" in source
    assert source.count("400, False") == 2
