"""JSON-safe projection of a ``ProgressPhysique`` (Progress Redesign PR4).

Pure and total: a frozen value object in, a plain ``dict`` out. No DB, no Flask,
no clock, no signing. The route only has to ``jsonify`` the result.

The field list is written out by hand rather than generated from
``dataclasses.asdict``: this is a published surface, and adding a field to the
model should be a deliberate decision here, not an automatic leak.

What is deliberately absent: database integer ids, ``user_id``, ``image_key``,
bucket, description, raw provider output, Feed identity, likes, comments,
reposts, friend state, sharing lists, idempotency keys.
"""
from datetime import timezone

from app.services.progress_physique.models import (
    CONTRACT_VERSION,
    PhysiqueCheck,
    PhysiqueComparison,
    ProgressPhysique,
)


def _iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _region_payload(region):
    return {
        "body_region": region.body_region,
        "check_count": region.check_count,
        "latest_captured_at": _iso(region.latest_captured_at),
    }


def _check_payload(check: PhysiqueCheck):
    return {
        "id": check.public_id,
        "captured_at": _iso(check.captured_at),
        "body_region": check.body_region,
        "analysis_status": check.analysis_status,
        "analysis_quality": check.analysis_quality,
        "image_url": check.image_url,
    }


def _comparison_payload(comparison: PhysiqueComparison):
    analysis = comparison.analysis
    return {
        "id": comparison.public_id,
        "comparability": comparison.comparability,
        "baseline_pump_check_id": comparison.baseline_pump_check_id,
        "current_pump_check_id": comparison.current_pump_check_id,
        "current_is_latest_check": comparison.current_is_latest_check,
        "baseline_captured_at": _iso(comparison.baseline_captured_at),
        "current_captured_at": _iso(comparison.current_captured_at),
        "baseline_image_url": comparison.baseline_image_url,
        "current_image_url": comparison.current_image_url,
        "analysis": {
            "summary": analysis.summary,
            "observed_changes": list(analysis.observed_changes),
            "stable_areas": list(analysis.stable_areas),
            "focus_areas": list(analysis.focus_areas),
            "limitations": list(analysis.limitations),
            "comparability_reasons": list(analysis.comparability_reasons),
            "next_check_guidance": analysis.next_check_guidance,
        },
    }


def progress_physique_payload(physique: ProgressPhysique) -> dict:
    """Canonical JSON-safe dict for one Physique Progress read model."""
    return {
        "contract_version": CONTRACT_VERSION,
        "state": physique.state,
        "selected_region": physique.selected_region,
        "regions": [_region_payload(region) for region in physique.regions],
        "recent_checks": [_check_payload(check) for check in physique.recent_checks],
        "comparison": (
            None if physique.comparison is None
            else _comparison_payload(physique.comparison)
        ),
        "legacy_gallery_available": physique.legacy_gallery_available,
    }


__all__ = ["progress_physique_payload"]
