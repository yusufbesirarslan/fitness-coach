"""Canonical Physique Progress read model (Progress Redesign PR4).

PR1 gave PHYSIQUE PROGRESS a place. PR4 fills that place with evidence the
Pump Check domain already owns. This package composes:

* canonical Pump Check history (``captured_at``, owner-scoped, no ``created_at``
  fallback);
* the persisted directional ``PumpCheckComparison`` (baseline → current);
* private owner-aware image signing.

It creates no second Pump Check authority, no comparison, no provider call,
and no physique verdict. The frontend translates the payload; it does not
decide whether the physique improved.

Layering (mirrors ``progress_summary`` / ``progress_insights``):

- ``models``  — frozen value objects + every bounded code constant.
- ``queries`` — owner-scoped bounded reads + signing after ownership.
- ``payload`` — the JSON projection of the contract.
- ``__init__`` — public API + the ``build_progress_physique`` orchestrator.

There is no ``analysis.py``: nothing here interprets images or synthesises a
comparison. Selection (default region, newest completed comparison, top-level
state) is deterministic composition of already-persisted facts.

Dependency direction is one-way and must stay that way::

    progress_physique.js → GET /api/progress/physique → progress_physique
                                                         ↙              ↘
                              canonical Pump Check history     canonical comparison
                                                         ↘              ↙
                                                private owner-scoped image signing

Nothing under ``mobile_pump_checks`` / ``mobile_pump_check_comparisons``
imports this package.
"""
from app.services.progress_physique.models import (
    BODY_REGIONS,
    COMPARABILITY_COMPARABLE,
    COMPARABILITY_LIMITED,
    COMPARABILITY_NOT_COMPARABLE,
    COMPARABILITY_VALUES,
    CONTRACT_VERSION,
    IMAGE_URL_EXPIRES_IN,
    InvalidProgressPhysiqueRegion,
    PhysiqueCheck,
    PhysiqueComparison,
    PhysiqueComparisonAnalysis,
    PhysiqueRegion,
    ProgressPhysique,
    RECENT_CHECK_LIMIT,
    STATE_COMPARISON_AVAILABLE,
    STATE_EMPTY,
    STATE_HISTORY_ONLY,
    STATE_SINGLE_CHECK,
    STATES,
    UnknownPhysiqueComparability,
)
from app.services.progress_physique.payload import progress_physique_payload
from app.services.progress_physique.queries import (
    has_legacy_gallery_rows,
    list_canonical_regions,
    list_recent_canonical_checks,
    load_latest_completed_comparison,
    project_check,
    sign_owned_image,
)

__all__ = [
    "BODY_REGIONS",
    "COMPARABILITY_COMPARABLE",
    "COMPARABILITY_LIMITED",
    "COMPARABILITY_NOT_COMPARABLE",
    "COMPARABILITY_VALUES",
    "CONTRACT_VERSION",
    "IMAGE_URL_EXPIRES_IN",
    "InvalidProgressPhysiqueRegion",
    "UnknownPhysiqueComparability",
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
    "build_progress_physique",
    "progress_physique_payload",
]


def _normalize_region(region):
    if region is None:
        return None
    if not isinstance(region, str):
        raise InvalidProgressPhysiqueRegion(region)
    normalized = region.strip().lower()
    if normalized not in BODY_REGIONS:
        raise InvalidProgressPhysiqueRegion(region)
    return normalized


def _analysis_from_stored(raw):
    """Copy persisted comparison prose. Do not re-parse or rewrite it."""
    if not isinstance(raw, dict):
        raw = {}

    def _text(key):
        value = raw.get(key)
        return value if isinstance(value, str) else ""

    def _texts(key):
        value = raw.get(key)
        if not isinstance(value, list):
            return ()
        return tuple(item for item in value if isinstance(item, str))

    return PhysiqueComparisonAnalysis(
        summary=_text("summary"),
        observed_changes=_texts("observed_changes"),
        stable_areas=_texts("stable_areas"),
        focus_areas=_texts("focus_areas"),
        limitations=_texts("limitations"),
        comparability_reasons=_texts("comparability_reasons"),
        next_check_guidance=_text("next_check_guidance"),
    )


def _comparability(value):
    if isinstance(value, str) and value in COMPARABILITY_VALUES:
        return value
    raise UnknownPhysiqueComparability()


def _state(check_count, comparison):
    if comparison is not None:
        return STATE_COMPARISON_AVAILABLE
    if check_count == 0:
        return STATE_EMPTY
    if check_count == 1:
        return STATE_SINGLE_CHECK
    return STATE_HISTORY_ONLY


def build_progress_physique(user_id, *, region=None) -> ProgressPhysique:
    """The canonical Physique Progress projection for ``user_id``.

    Read-only and deterministic: for the same persisted Pump Check /
    comparison rows the result is equal (signed URLs excepted — those expire).
    ``region`` is an optional *view* selector, not a scoring window. An omitted
    region selects the body area of the owner's most recent canonical Pump
    Check. An invalid region is refused, never remapped.

    No provider, no Bedrock, no image decode, no comparison creation, no DB
    write, no flush, no commit.
    """
    requested = _normalize_region(region)
    regions = list_canonical_regions(user_id)
    legacy = has_legacy_gallery_rows(user_id)

    if requested is not None:
        selected = requested
    elif regions:
        selected = regions[0].body_region
    else:
        selected = None

    if selected is None:
        return ProgressPhysique(
            state=STATE_EMPTY,
            selected_region=None,
            regions=regions,
            recent_checks=(),
            comparison=None,
            legacy_gallery_available=legacy,
        )

    raw_checks = list_recent_canonical_checks(
        user_id, selected, RECENT_CHECK_LIMIT)
    raw_comparison = load_latest_completed_comparison(user_id, selected)

    # Sign only the unique keys this response will render.
    keys = [row.image_key for row in raw_checks]
    if raw_comparison is not None:
        keys.append(raw_comparison.baseline_image_key)
        keys.append(raw_comparison.current_image_key)
    signed = {}
    for key in keys:
        if key and key not in signed:
            signed[key] = sign_owned_image(user_id, key)

    checks = tuple(
        project_check(row, signed.get(row.image_key)) for row in raw_checks
    )

    comparison = None
    if raw_comparison is not None:
        latest_id = checks[0].public_id if checks else None
        comparison = PhysiqueComparison(
            public_id=raw_comparison.public_id,
            comparability=_comparability(raw_comparison.comparability),
            baseline_pump_check_id=raw_comparison.baseline_public_id,
            current_pump_check_id=raw_comparison.current_public_id,
            current_is_latest_check=(
                latest_id is not None
                and raw_comparison.current_public_id == latest_id
            ),
            baseline_captured_at=raw_comparison.baseline_captured_at,
            current_captured_at=raw_comparison.current_captured_at,
            baseline_image_url=signed.get(raw_comparison.baseline_image_key),
            current_image_url=signed.get(raw_comparison.current_image_key),
            analysis=_analysis_from_stored(raw_comparison.analysis),
        )

    return ProgressPhysique(
        state=_state(len(checks), comparison),
        selected_region=selected,
        regions=regions,
        recent_checks=checks,
        comparison=comparison,
        legacy_gallery_available=legacy,
    )
