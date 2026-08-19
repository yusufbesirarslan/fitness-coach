"""Frozen value objects + bounded vocabulary for Physique Progress (PR4).

Plain data holders (no logic, no DB, no Flask) in the style of
``app/services/progress_insights/models.py``. This layer owns *presentation
composition* only: which canonical Pump Checks and which persisted comparison
the Progress page should show. It does not analyse images, create comparisons,
or decide whether a physique improved.

Every machine value this package can publish is an explicit constant here or
an import of the canonical owner's constant. Body-region tokens come from
``mobile_pump_checks``; comparability tokens come from
``mobile_pump_check_comparisons``. There is no second copy.
"""
from dataclasses import dataclass
from datetime import datetime

from app.services.mobile_pump_check_comparisons.analysis import (
    COMPARABILITY_VALUES,
)
from app.services.mobile_pump_checks.service import BODY_REGIONS


# ── Top-level states ───────────────────────────────────────────────────────
# Four product states. A system failure is *not* one of these — that is an
# HTTP 500 the client renders as unavailable. Collapsing "no checks" into
# "the backend broke" would hide a legitimate empty gallery.
STATE_EMPTY = "empty"
STATE_SINGLE_CHECK = "single_check"
STATE_HISTORY_ONLY = "history_only"
STATE_COMPARISON_AVAILABLE = "comparison_available"

STATES = (
    STATE_EMPTY,
    STATE_SINGLE_CHECK,
    STATE_HISTORY_ONLY,
    STATE_COMPARISON_AVAILABLE,
)

# ── Comparability (owned by the comparison authority; re-exported) ─────────
COMPARABILITY_COMPARABLE = "comparable"
COMPARABILITY_LIMITED = "limited"
COMPARABILITY_NOT_COMPARABLE = "not_comparable"
# Fail-closed stand-in when a completed row carries a value this build does
# not know. Never mapped to comparable.
COMPARABILITY_UNKNOWN = "unknown"

# ── Bounds ─────────────────────────────────────────────────────────────────
# Enough to establish chronology and baseline/current context. Not a gallery.
RECENT_CHECK_LIMIT = 2
IMAGE_URL_EXPIRES_IN = 3600

# Bumped only on a breaking change to the wire contract. Independent of the
# other Progress surfaces: they may evolve without this one.
CONTRACT_VERSION = 1


class InvalidProgressPhysiqueRegion(ValueError):
    """The caller named a body region that is not in the canonical set.

    Raised rather than remapped: silently substituting ``upper_body`` (or the
    user's latest region) would make an invalid selector look like a successful
    view of a different area.
    """


@dataclass(frozen=True)
class PhysiqueRegion:
    """One body area the owner has at least one canonical Pump Check for."""

    body_region: str
    check_count: int
    latest_captured_at: datetime


@dataclass(frozen=True)
class PhysiqueCheck:
    """Narrow Progress projection of one canonical Pump Check."""

    public_id: str
    captured_at: datetime
    body_region: str
    analysis_status: str
    analysis_quality: str | None
    image_url: str | None


@dataclass(frozen=True)
class PhysiqueComparisonAnalysis:
    """Persisted comparison prose, already validated at write time.

    Copied verbatim. This layer does not regenerate, translate, or summarise it.
    """

    summary: str
    observed_changes: tuple[str, ...]
    stable_areas: tuple[str, ...]
    focus_areas: tuple[str, ...]
    limitations: tuple[str, ...]
    comparability_reasons: tuple[str, ...]
    next_check_guidance: str


@dataclass(frozen=True)
class PhysiqueComparison:
    """The newest relevant persisted completed comparison for the region."""

    public_id: str
    comparability: str
    baseline_pump_check_id: str
    current_pump_check_id: str
    current_is_latest_check: bool
    baseline_captured_at: datetime
    current_captured_at: datetime
    baseline_image_url: str | None
    current_image_url: str | None
    analysis: PhysiqueComparisonAnalysis


@dataclass(frozen=True)
class ProgressPhysique:
    """The canonical Physique Progress read model for one owner."""

    state: str
    selected_region: str | None
    regions: tuple[PhysiqueRegion, ...]
    recent_checks: tuple[PhysiqueCheck, ...]
    comparison: PhysiqueComparison | None
    legacy_gallery_available: bool


__all__ = [
    "BODY_REGIONS",
    "COMPARABILITY_COMPARABLE",
    "COMPARABILITY_LIMITED",
    "COMPARABILITY_NOT_COMPARABLE",
    "COMPARABILITY_UNKNOWN",
    "COMPARABILITY_VALUES",
    "CONTRACT_VERSION",
    "IMAGE_URL_EXPIRES_IN",
    "InvalidProgressPhysiqueRegion",
    "PhysiqueCheck",
    "PhysiqueComparison",
    "PhysiqueComparisonAnalysis",
    "PhysiqueRegion",
    "ProgressPhysique",
    "RECENT_CHECK_LIMIT",
    "STATE_COMPARISON_AVAILABLE",
    "STATE_EMPTY",
    "STATE_HISTORY_ONLY",
    "STATE_SINGLE_CHECK",
    "STATES",
]
