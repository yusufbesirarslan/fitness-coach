"""Configurable AxisAI-side retention for analytics rows (values pending approval).

* Undispatched outbox rows: deleted once older than the configured age, and
  never kept beyond the 48 h hard ceiling, whether or not dispatch runs.
* Pseudonym rotation (optional): a live subject older than the configured
  maximum is retired to vendor erasure and replaced for the same grant, which
  bounds raw vendor retention to that age plus the vendor's deletion lag even
  where the vendor's own retention setting cannot be set that low.
* Erasure receipts: terminal receipts (``verified`` / ``not_required``) are
  purged after the configured receipt retention; without that value nothing is
  purged (the receipt is the only evidence the vendor deletion happened).
  Unfinished jobs are never purged.

Vendor-side raw event retention is a PostHog project setting (gate G3), not
code; see docs/product-analytics-v2/RETENTION.md. Each statement touches only
analytics tables and locks only the rows it deletes.
"""
from datetime import datetime

from app.extensions import db
from app.models import ProductAnalyticsConsent, ProductAnalyticsErasure, ProductAnalyticsOutbox
from app.services.product_analytics import consent
from app.services.product_analytics.settings import OUTBOX_HARD_CEILING

ROTATION_BATCH = 100


def cleanup(policy, *, now=None):
    now = now or datetime.utcnow()
    stats = {"outbox_expired": 0, "receipts_purged": 0, "subjects_rotated": 0}
    try:
        if policy.subject_max_age is not None:
            # Same lock as withdrawal/dispatch (consent row); busy rows are
            # skipped and rotate on a later pass.
            due = (ProductAnalyticsConsent.query
                   .filter(ProductAnalyticsConsent.status == consent.GRANTED,
                           ProductAnalyticsConsent.subject.isnot(None),
                           ProductAnalyticsConsent.subject_issued_at < now - policy.subject_max_age)
                   .order_by(ProductAnalyticsConsent.user_id).limit(ROTATION_BATCH)
                   .populate_existing().with_for_update(skip_locked=True).all())
            for row in due:
                consent.rotate_subject(row, now)
            stats["subjects_rotated"] = len(due)
        age = min(policy.outbox_max_age, OUTBOX_HARD_CEILING)
        stats["outbox_expired"] = (ProductAnalyticsOutbox.query
                                   .filter(ProductAnalyticsOutbox.occurred_at < now - age)
                                   .delete(synchronize_session=False))
        if policy.receipt_retention is not None:
            stats["receipts_purged"] = (ProductAnalyticsErasure.query
                                        .filter(ProductAnalyticsErasure.status.in_(
                                            ("verified", "not_required")),
                                            ProductAnalyticsErasure.completed_at.isnot(None),
                                            ProductAnalyticsErasure.completed_at
                                            < now - policy.receipt_retention)
                                        .delete(synchronize_session=False))
        db.session.commit()
        return stats
    except Exception:
        db.session.rollback()
        raise
