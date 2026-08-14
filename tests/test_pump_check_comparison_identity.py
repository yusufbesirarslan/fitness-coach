import re

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers

from app.models import (
    PumpCheck,
    PumpCheckComparison,
    PumpCheckComparisonRequest,
    User,
)
from app.services.mobile_pump_check_comparisons.analysis import ANALYSIS_VERSION
from app.services.mobile_pump_check_comparisons.identity import (
    fingerprint,
    is_valid_comparison_id,
    new_comparison_id,
)


def test_comparison_id_is_opaque_owner_bound_and_url_safe():
    nonce = b"n" * 32
    first = new_comparison_id("secret", 7, nonce)

    assert first == new_comparison_id("secret", 7, nonce)
    assert len(first) == 24
    assert re.fullmatch(r"[A-Za-z0-9_-]{24}", first)
    assert is_valid_comparison_id(first)
    assert "7" not in first
    assert first != new_comparison_id("secret", 8, nonce)
    assert first != new_comparison_id("secret", 7, b"m" * 32)


def test_comparison_id_validation_rejects_non_tokens():
    assert is_valid_comparison_id("") is False
    assert is_valid_comparison_id(None) is False
    assert is_valid_comparison_id("not/a/token") is False
    assert is_valid_comparison_id("A" * 23) is False
    assert is_valid_comparison_id("A" * 25) is False


def test_fingerprint_is_versioned_and_directional():
    ab = fingerprint("A" * 24, "B" * 24, ANALYSIS_VERSION)
    ba = fingerprint("B" * 24, "A" * 24, ANALYSIS_VERSION)

    assert len(ab) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", ab)
    assert ab == fingerprint("A" * 24, "B" * 24, ANALYSIS_VERSION)
    assert ab != ba
    assert ab != fingerprint("A" * 24, "B" * 24, "comparison/v2")


def _column_contract(table):
    return {
        column.name: (
            type(column.type),
            getattr(column.type, "length", None),
            column.nullable,
        )
        for column in table.columns
    }


def test_comparison_model_has_exact_column_contract():
    assert _column_contract(PumpCheckComparison.__table__) == {
        "id": (sa.Integer, None, False),
        "user_id": (sa.Integer, None, False),
        "baseline_pump_check_id": (sa.Integer, None, False),
        "current_pump_check_id": (sa.Integer, None, False),
        "public_id": (sa.String, 24, False),
        "status": (sa.String, 20, False),
        "comparability": (sa.String, 20, True),
        "analysis": (postgresql.JSONB, None, True),
        "analysis_version": (sa.String, 50, False),
        "analysis_started_at": (sa.DateTime, None, True),
        "analysis_attempt": (sa.Integer, None, False),
        "analysis_failure_kind": (sa.String, 24, True),
        "created_at": (sa.DateTime, None, False),
    }
    assert PumpCheckComparison.__table__.columns["status"].server_default.arg == "pending"
    assert PumpCheckComparison.__table__.columns["analysis_attempt"].server_default.arg == "0"


def test_request_ledger_has_exact_columns_and_no_analysis_authority():
    assert _column_contract(PumpCheckComparisonRequest.__table__) == {
        "id": (sa.Integer, None, False),
        "user_id": (sa.Integer, None, False),
        "idempotency_key": (sa.String, 64, False),
        "fingerprint": (sa.String, 64, False),
        "comparison_id": (sa.Integer, None, False),
        "created_at": (sa.DateTime, None, False),
    }
    assert {"comparability", "analysis", "analysis_attempt"} <= set(
        PumpCheckComparison.__table__.columns.keys()
    )
    assert {"idempotency_key", "fingerprint", "comparison_id"} <= set(
        PumpCheckComparisonRequest.__table__.columns.keys()
    )
    assert not {"analysis", "comparability"} & set(
        PumpCheckComparisonRequest.__table__.columns.keys()
    )


def test_comparison_models_define_uniques_and_coherence_checks():
    comparison_uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in PumpCheckComparison.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    request_uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in PumpCheckComparisonRequest.__table__.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert comparison_uniques == {
        "uq_pump_comparison_pair_version": (
            "user_id",
            "baseline_pump_check_id",
            "current_pump_check_id",
            "analysis_version",
        ),
        "uq_pump_comparison_user_public_id": ("user_id", "public_id"),
    }
    assert request_uniques == {
        "uq_pump_comparison_request_user_key": ("user_id", "idempotency_key")
    }

    checks = {
        constraint.name: " ".join(str(constraint.sqltext).split())
        for constraint in PumpCheckComparison.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert checks == {
        "ck_pump_comparison_distinct_sources": (
            "baseline_pump_check_id <> current_pump_check_id"
        ),
        "ck_pump_comparison_status": (
            "status IN ('pending', 'analyzing', 'completed', 'failed')"
        ),
        "ck_pump_comparison_comparability": (
            "comparability IS NULL OR comparability IN "
            "('comparable', 'limited', 'not_comparable')"
        ),
        "ck_pump_comparison_terminal_fields": (
            "(status = 'completed' AND analysis IS NOT NULL AND "
            "comparability IS NOT NULL) OR (status <> 'completed' AND "
            "analysis IS NULL AND comparability IS NULL)"
        ),
    }


def _foreign_key_contract(table):
    return {
        (
            foreign_key.parent.name,
            foreign_key.column.table.name,
            foreign_key.column.name,
            foreign_key.ondelete,
        )
        for foreign_key in table.foreign_keys
    }


def test_comparison_foreign_keys_are_cascade_only_in_the_derived_direction():
    assert _foreign_key_contract(PumpCheckComparison.__table__) == {
        ("user_id", "user", "id", "CASCADE"),
        ("baseline_pump_check_id", "pump_check", "id", "CASCADE"),
        ("current_pump_check_id", "pump_check", "id", "CASCADE"),
    }
    assert _foreign_key_contract(PumpCheckComparisonRequest.__table__) == {
        ("user_id", "user", "id", "CASCADE"),
        ("comparison_id", "pump_check_comparison", "id", "CASCADE"),
    }

    configure_mappers()
    assert User.pump_check_comparisons.property.passive_deletes is True
    assert User.pump_check_comparison_requests.property.passive_deletes is True
    assert PumpCheck.baseline_comparisons.property.passive_deletes is True
    assert PumpCheck.current_comparisons.property.passive_deletes is True
    assert PumpCheckComparison.requests.property.passive_deletes is True
