"""Coherent read transaction for the Training bootstrap projection.

Authentication and request hooks use Flask-SQLAlchemy's scoped session before a
protected view runs.  PostgreSQL's default READ COMMITTED isolation therefore
cannot be upgraded safely in-place once the route starts.  This boundary removes
that completed-authentication read transaction, starts one fresh transaction,
and pins all bootstrap statements to a repeatable snapshot.

The caller must capture authenticated identity and other request facts before
entering.  The boundary is read-only by contract and always removes its scoped
session, rolling back the read transaction and releasing its connection.
"""
from contextlib import contextmanager

from app.extensions import db


@contextmanager
def coherent_read_snapshot():
    """Run enclosed reads in one fresh coherent database transaction.

    PostgreSQL receives REPEATABLE READ before its first statement.  Other
    dialects retain their ordinary isolation while still receiving a fresh,
    explicitly-started transaction (the hermetic suite uses SQLite).
    """
    db.session.remove()
    try:
        if db.engine.dialect.name == "postgresql":
            db.session.connection(execution_options={
                "isolation_level": "REPEATABLE READ",
            })
        else:
            db.session.connection()
        yield
    finally:
        db.session.remove()
