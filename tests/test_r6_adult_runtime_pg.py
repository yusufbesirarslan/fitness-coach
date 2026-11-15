"""R6 admission SQL on the existing disposable PostgreSQL CI service only."""
import uuid

import pytest
import sqlalchemy as sa

from tests.test_r6_dual_revision_pg import pg_url  # noqa: F401

pytestmark = pytest.mark.pg_concurrency


@pytest.mark.parametrize("missing", [None, "table", "SELECT", "INSERT", "DELETE", "sequence"])
def test_real_runtime_role_table_and_sequence_acl(pg_url, monkeypatch, missing):
    from app import create_app
    from app.extensions import db
    from app.services.r6_runtime_admission import database_proof
    role = "r6_admission_" + uuid.uuid4().hex
    admin = sa.create_engine(pg_url)
    with admin.begin() as c:
        c.exec_driver_sql(f'CREATE ROLE "{role}"')
        c.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        if missing != "table":
            c.exec_driver_sql("CREATE TABLE public.account_deletion_record(id SERIAL PRIMARY KEY)")
            for privilege in ("SELECT", "INSERT", "DELETE"):
                if missing != privilege:
                    c.exec_driver_sql(f'GRANT {privilege} ON public.account_deletion_record TO "{role}"')
            if missing != "sequence":
                c.exec_driver_sql(f'GRANT USAGE ON SEQUENCE public.account_deletion_record_id_seq TO "{role}"')
    app = create_app()
    try:
        with app.app_context():
            def runtime_login(conn, record):
                with conn.cursor() as cursor:
                    cursor.execute(f'SET ROLE "{role}"')
                conn.commit()
            sa.event.listen(db.engine, "connect", runtime_login)
            db.engine.dispose()
            if missing is None:
                assert database_proof() == {"deletion_acl": True, "pooled_utc": True}
            else:
                with pytest.raises(RuntimeError, match="R6_DELETION_"):
                    database_proof()
            db.session.remove()
            db.engine.dispose()
    finally:
        with admin.begin() as c:
            c.exec_driver_sql(f'DROP OWNED BY "{role}"')
            c.exec_driver_sql(f'DROP ROLE "{role}"')
        admin.dispose()


def test_existing_pooled_session_timezone_drift_is_fenced(pg_url):
    from app import create_app
    from app.extensions import db
    app = create_app()
    with app.app_context():
        try:
            with db.engine.connect() as c:
                c.exec_driver_sql("SET TIME ZONE 'Europe/Istanbul'")
                c.commit()  # survives checkin rollback; same actual pooled connection
            with pytest.raises(RuntimeError, match="R6_POOLED_UTC_REQUIRED"):
                db.engine.connect()
            # Invalid connection was discarded; a fresh UTC connection is admissible.
            with db.engine.connect() as c:
                assert c.exec_driver_sql("SHOW TIME ZONE").scalar() in ("UTC", "Etc/UTC")
        finally:
            db.session.remove()
            db.engine.dispose()
