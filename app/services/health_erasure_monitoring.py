"""M4: durable hourly retry evidence and anonymous Redis metric snapshot.

Database work runs only in the RQ child. The worker parent reads one aggregate
Redis value; it never opens an ORM connection. Publication uses the existing
fork-safe, bounded publisher. Missing/broken monitoring never authorizes work.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import func

from app.extensions import db
from app.health_consent_models import HealthErasure, HealthErasureRetry, HealthLegalHold

KEY = "fitx:health-erasure:monitoring:v1"
PENDING = "HealthErasurePending"
OLDEST = "HealthErasureOldestPendingAgeSeconds"
PASS_AGE = "HealthErasureRetryPassAgeSeconds"
TWICE = "HealthErasurePendingAfterTwoRetries"
OVERDUE = "HealthErasureOverdue"


def start_pass():
    ceiling = db.session.query(func.max(HealthErasure.id)).scalar() or 0
    db.session.rollback()
    return ceiling, datetime.utcnow()


def finish_pass(start, slot, now=None, batch_size=200):
    """Count surviving obligations once per scheduled hour, then snapshot.

    Row locks serialize overlapping workers. Slots come from the enqueue time,
    not completion time: delayed/duplicate jobs never manufacture retries.
    A job without a valid slot cannot qualify the hourly monitoring contract.
    """
    if type(slot) is not int or slot < 0:
        raise ValueError("scheduled UTC hour required")
    now = now or datetime.now(timezone.utc)
    try:
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("positive batch size required")
        ceiling, started_at = start
        after = 0
        while after < ceiling:
            rows = HealthErasure.query.filter(HealthErasure.id > after,
                HealthErasure.id <= ceiling, HealthErasure.requested_at <= started_at,
                HealthErasure.completed_at.is_(None)).order_by(HealthErasure.id).limit(
                    batch_size).with_for_update().all()
            if not rows:
                break
            after = rows[-1].id
            for row in rows:
                retry = db.session.get(HealthErasureRetry, row.id)
                if retry is None:
                    db.session.add(HealthErasureRetry(erasure_id=row.id, passes=1, last_slot=slot))
                elif slot != retry.last_slot:
                    # Two is terminal monitoring evidence. Accept an older
                    # delayed hour too; completion order is not enqueue order.
                    retry.passes = min(retry.passes + 1, 2)
                    retry.last_slot = max(retry.last_slot, slot)
            db.session.commit()
        db.session.flush()
        pending = HealthErasure.query.filter(HealthErasure.completed_at.is_(None))
        oldest = db.session.query(func.min(HealthErasure.requested_at)).filter(
            HealthErasure.completed_at.is_(None)).scalar()
        held = db.session.query(HealthLegalHold.owner_id).filter(HealthLegalHold.released_at.is_(None))
        oldest_unheld = db.session.query(func.min(HealthErasure.requested_at)).filter(
            HealthErasure.completed_at.is_(None), HealthErasure.owner_id.notin_(held)).scalar()
        twice = pending.join(HealthErasureRetry, HealthErasureRetry.erasure_id == HealthErasure.id).filter(
            HealthErasureRetry.passes >= 2).count()
        def epoch(value):
            return value.replace(tzinfo=timezone.utc).timestamp() if value is not None else None
        snapshot = dict(completed=now.timestamp(), pending=pending.count(),
                        oldest=epoch(oldest), oldest_unheld=epoch(oldest_unheld), twice=twice)
        db.session.commit()
        return snapshot
    except BaseException:
        db.session.rollback()
        raise


def store_snapshot(snapshot):
    """No AWS here. Failure leaves the previous completion time aging."""
    from app.jobs import _rq_connection
    connection = _rq_connection()
    if connection is None:
        raise RuntimeError("erasure monitoring requires Redis")
    # No expiry: after a stopped retry, its age must continue to increase.
    connection.eval("""
        local old = redis.call('GET', KEYS[1])
        if old and cjson.decode(old).completed >= tonumber(ARGV[1]) then return 0 end
        redis.call('SET', KEYS[1], ARGV[2])
        return 1
    """, 1, KEY, snapshot["completed"], json.dumps(snapshot, sort_keys=True))


def sample(connection, now):
    raw = connection.get(KEY)
    if raw is None:
        return {}  # missing pass-age data breaches the M4 alarm
    value = json.loads(raw)
    age = max(0, now.timestamp() - value["completed"])
    oldest = value["oldest"]
    unheld = value["oldest_unheld"]
    return {PENDING: (value["pending"], "Count"),
            OLDEST: (max(0, now.timestamp() - oldest) if oldest is not None else 0, "Seconds"),
            PASS_AGE: (age, "Seconds"), TWICE: (value["twice"], "Count"),
            OVERDUE: (int(unheld is not None and now.timestamp() - unheld > 30 * 86400), "Count")}
