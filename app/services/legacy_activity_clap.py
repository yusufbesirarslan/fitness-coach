"""Deletion compatibility for production's legacy ``activity_clap`` table.

Production carries an ``activity_clap`` table that predates Alembic: no model,
no migration, and two NO ACTION foreign keys (``user_id`` -> user,
``activity_id`` -> activity). Every other user-owned table cascades. Without
this module, deleting a user who clapped, or an activity somebody clapped,
raises 23503: account deletion (``_purge_user``) aborts after the identity is
already gone, and O2-B workout erasure (``health_erasure``) aborts too.

The fix is deliberately application-level, with no schema change: the release
migration chain stays as qualified, and nothing is deleted at deploy time. Each
call removes only the clap rows that point at the rows its caller is about to
delete, exactly the rows ``ON DELETE CASCADE`` would remove, one level deep and
inside the caller's transaction. Databases without the table (every fresh,
test and staging schema built from the models or migrations) are a no-op.
"""
from sqlalchemy import inspect, text

from app.extensions import db

TABLE = "activity_clap"


def _present(connection):
    return inspect(connection).has_table(TABLE)


def delete_for_user(user_id):
    """Claps BY the user, and claps ON the user's activities. Returns rows."""
    connection = db.session.connection()
    if not _present(connection):
        return 0
    return connection.execute(text(
        "DELETE FROM activity_clap WHERE user_id = :uid "
        "OR activity_id IN (SELECT id FROM activity WHERE user_id = :uid)"),
        {"uid": user_id}).rowcount


def delete_on_activities_of(user_id):
    """Claps (anyone's) ON the user's activities only. Returns rows."""
    connection = db.session.connection()
    if not _present(connection):
        return 0
    return connection.execute(text(
        "DELETE FROM activity_clap "
        "WHERE activity_id IN (SELECT id FROM activity WHERE user_id = :uid)"),
        {"uid": user_id}).rowcount
