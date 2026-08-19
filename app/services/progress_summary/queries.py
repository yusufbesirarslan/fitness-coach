"""User-scoped, read-only body/profile reads for the Progress summary.

The only impure module in the package besides the orchestrator, and deliberately
narrow: training data is never touched here — it reaches the summary exclusively
through ``training_progression``. What this layer reads is the part of the body
picture that has no service in front of it yet (``User`` profile columns and the
``WeeklyCheckIn`` ledger), and it reads it using the *existing* canonical
semantics rather than inventing new ones.

Read-only is enforced structurally, not by convention: every statement runs
inside ``db.session.no_autoflush`` so the summary can never be the thing that
flushes somebody else's pending work, and nothing in the package adds, deletes,
flushes or commits (``tests/test_progress_summary.py`` pins this).
"""
from app.extensions import db
from app.models import User, WeeklyCheckIn

from .models import BodyFacts

# The latest weight plus the one before it — a delta needs exactly two points and
# nothing in the contract is served by fetching more.
_QUALIFYING_OBSERVATIONS = 2


def _positive(value):
    """A weight/target only exists when it is a positive number.

    Guards the missing-vs-zero rule at the boundary where it actually bites: a
    stored ``0`` in ``User.target_weight`` is an unset target, not a goal of zero
    kilograms, and publishing ``0.0`` would let the client render "0.0 kg to your
    target". Same precedent as the mobile nutrition boundary, which folds a
    non-positive stored calorie goal to ``null`` (``docs/MOBILE_NUTRITION.md``).
    """
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def fetch_body_facts(user_id: int) -> BodyFacts:
    """Canonical body observations for ``user_id`` — no interpretation.

    ``current_weight_kg`` follows the fallback ``/progress-page`` already
    established: the canonical profile weight (``User.weight``, kept current by
    ``tracking._apply_weight_to_profile`` from both ``/checkin`` and
    ``/update-weight``), falling back to the newest check-in row when the profile
    has none yet. That fallback intentionally accepts *any* check-in row, because
    its job is "what does this user weigh", which a sparse ``/update-weight`` row
    answers perfectly well.

    ``recent_qualifying_weights`` is the stricter set and does **not** merge the
    two concepts: only full weekly check-ins qualify, identified by
    ``yogunluk IS NOT NULL`` — the exact filter ``/checkin-history`` and
    ``/api/progress/insights`` already use to keep sparse weight-only rows out of
    Progress history (BUG-5). Blending them would make a delta appear between two
    rows the rest of Progress does not consider comparable observations.
    """
    with db.session.no_autoflush:
        user = db.session.get(User, user_id)
        if user is None:
            return BodyFacts()

        current = _positive(user.weight)
        if current is None:
            latest_any = (
                WeeklyCheckIn.query
                .filter_by(user_id=user_id)
                .order_by(WeeklyCheckIn.created_at.desc(), WeeklyCheckIn.id.desc())
                .first()
            )
            current = _positive(latest_any.weight) if latest_any else None

        qualifying_rows = (
            WeeklyCheckIn.query
            .filter_by(user_id=user_id)
            .filter(WeeklyCheckIn.yogunluk.isnot(None))
            .order_by(WeeklyCheckIn.created_at.desc(), WeeklyCheckIn.id.desc())
            .limit(_QUALIFYING_OBSERVATIONS)
            .all()
        )

    # A row whose weight is not a usable number is not an observation. Dropping it
    # rather than substituting zero is what keeps "fewer than two observations"
    # honest instead of manufacturing a delta against a fake reading.
    weights = tuple(
        w for w in (_positive(row.weight) for row in qualifying_rows) if w is not None
    )

    return BodyFacts(
        current_weight_kg=current,
        target_weight_kg=_positive(user.target_weight),
        recent_qualifying_weights=weights,
    )
