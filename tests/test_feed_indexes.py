# Sprint 5 remediation PR1 — feed bilesik indeksleri (audit HIGH-1 / MEDIUM-3).
# create_all'in modellerden bu indeksleri gercekten kurdugunu ve migration'in
# BIREBIR ayni adlari kullandigini deterministik dogrular (hermetik SQLite).
from pathlib import Path

from sqlalchemy import inspect

from app.extensions import db


def _index_columns(table, name):
    for ix in inspect(db.engine).get_indexes(table):
        if ix["name"] == name:
            return list(ix["column_names"])
    return None


def test_pump_check_has_user_created_composite_index(app):
    assert _index_columns("pump_check", "ix_pump_check_user_created") == ["user_id", "created_at"]


def test_activity_has_user_ts_composite_index(app):
    assert _index_columns("activity", "ix_activity_user_ts") == ["user_id", "timestamp"]


def test_migration_declares_same_index_names():
    # Model __table_args__ ile migration adlari ayni olmali; aksi halde fresh-DB
    # (create_all) ve mevcut-DB (migration CREATE INDEX IF NOT EXISTS) iki farkli
    # indeks yaratir ve idempotentlik bozulurdu.
    src = (Path(__file__).resolve().parents[1] / "migrations" / "versions"
           / "aa77bb88cc99_add_feed_composite_indexes.py").read_text(encoding="utf-8")
    assert "ix_pump_check_user_created" in src
    assert "ix_activity_user_ts" in src
    assert "CREATE INDEX IF NOT EXISTS" in src
