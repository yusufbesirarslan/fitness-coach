"""Deterministic eligibility and owner-only serialization for comparisons."""
from datetime import datetime, timedelta

import pytest

import s3_helper
from app.extensions import db
from app.models import PumpCheck, PumpCheckComparison
from app.services.mobile_pump_check_comparisons import service
from app.services.mobile_pump_check_comparisons.analysis import (
    ANALYSIS_VERSION,
)
from app.services.mobile_pump_checks.analysis import (
    ANALYSIS_VERSION as SOURCE_ANALYSIS_VERSION,
)


BASELINE_TOKEN = "A" * 24
CURRENT_TOKEN = "B" * 24


def _source_analysis(quality="sufficient"):
    return {
        "summary": "Visible definition can be assessed in this image.",
        "observations": ["Shoulder outline is visible."],
        "strengths": ["Consistent framing."],
        "focus_areas": ["Keep lighting even."],
        "limitations": ["One image cannot establish progress."],
        "next_check_guidance": "Repeat this framing next time.",
        "quality": quality,
    }


def _make_check(user, token, captured_at, quality="sufficient"):
    row = PumpCheck(
        user_id=user.id,
        public_id=token,
        captured_at=captured_at,
        body_region="upper_body",
        location_type="gym",
        visibility="private",
        valid=True,
        analysis_status="completed",
        analysis=_source_analysis(quality),
        analysis_version=SOURCE_ANALYSIS_VERSION,
        image_key=f"pump-checks/{user.id}/2026/08/{token[:8]}.jpg",
    )
    db.session.add(row)
    db.session.commit()
    return row


def _command(baseline=BASELINE_TOKEN, current=CURRENT_TOKEN):
    return service.create_command({
        "baseline_pump_check_id": baseline,
        "current_pump_check_id": current,
    })


@pytest.fixture
def owner(make_user):
    return make_user("comparison-owner")


@pytest.fixture
def stranger(make_user):
    return make_user("comparison-stranger")


@pytest.fixture
def eligible_pair(owner):
    baseline = _make_check(
        owner, BASELINE_TOKEN, datetime(2026, 8, 1, 9, 0, 0))
    current = _make_check(
        owner, CURRENT_TOKEN, datetime(2026, 8, 14, 9, 0, 0))
    return baseline, current


@pytest.fixture
def external_calls(monkeypatch):
    counts = {"s3": 0, "provider": 0}

    def read(*args, **kwargs):
        counts["s3"] += 1
        return b""

    def analyze(*args, **kwargs):
        counts["provider"] += 1
        return "comparable", {}

    monkeypatch.setattr(s3_helper, "get_object_bytes", read)
    monkeypatch.setattr(service, "analyze_images", analyze)
    return counts


def test_command_requires_exact_ordered_opaque_tokens():
    command = _command()

    assert command.baseline_token == BASELINE_TOKEN
    assert command.current_token == CURRENT_TOKEN

    with pytest.raises(service.InvalidCommand):
        service.create_command({
            "baseline_pump_check_id": BASELINE_TOKEN,
            "current_pump_check_id": CURRENT_TOKEN,
            "extra": True,
        })


@pytest.mark.parametrize("value", [
    None,
    [],
    "baseline",
    {"baseline_pump_check_id": BASELINE_TOKEN},
    {"baseline_pump_check_id": BASELINE_TOKEN, "current_pump_check_id": 7},
    {"baseline_pump_check_id": BASELINE_TOKEN, "current_pump_check_id": "!"},
    {"baseline_pump_check_id": "A" * 23, "current_pump_check_id": CURRENT_TOKEN},
])
def test_command_rejects_malformed_bodies(value):
    with pytest.raises(service.InvalidCommand):
        service.create_command(value)


def test_command_keeps_direction_and_never_sorts_tokens():
    reversed_command = _command(CURRENT_TOKEN, BASELINE_TOKEN)

    assert reversed_command.baseline_token == CURRENT_TOKEN
    assert reversed_command.current_token == BASELINE_TOKEN


def test_eligible_pair_resolves_in_command_order(owner, eligible_pair):
    baseline, current = eligible_pair

    sources = service.resolve_eligible_sources(owner.id, _command())

    assert sources.baseline.id == baseline.id
    assert sources.current.id == current.id
    assert sources.source_quality_cap == "comparable"


@pytest.mark.parametrize("quality", ["limited"])
def test_limited_source_caps_the_comparison(owner, eligible_pair, quality):
    baseline, _ = eligible_pair
    baseline.analysis = _source_analysis(quality)
    db.session.commit()

    sources = service.resolve_eligible_sources(owner.id, _command())

    assert sources.source_quality_cap == "limited"


@pytest.mark.parametrize("mutate", [
    pytest.param(
        lambda a, b: setattr(a, "captured_at", b.captured_at),
        id="identical_capture_time"),
    pytest.param(
        lambda a, b: setattr(a, "captured_at", None),
        id="missing_capture_time"),
    pytest.param(
        lambda a, b: setattr(b, "body_region", "back"),
        id="different_region"),
    pytest.param(
        lambda a, b: setattr(b, "body_region", None),
        id="missing_region"),
    pytest.param(
        lambda a, b: setattr(b, "analysis_status", "failed"),
        id="unfinished_source"),
    pytest.param(
        lambda a, b: setattr(b, "analysis_version", "legacy"),
        id="foreign_analysis_version"),
    pytest.param(
        lambda a, b: setattr(b, "valid", False),
        id="invalid_source"),
    pytest.param(
        lambda a, b: setattr(b, "analysis", dict(b.analysis, quality="insufficient")),
        id="insufficient_quality"),
    pytest.param(
        lambda a, b: setattr(b, "analysis", {"summary": "corrupt"}),
        id="corrupt_stored_analysis"),
    pytest.param(
        lambda a, b: setattr(b, "analysis", None),
        id="absent_stored_analysis"),
    pytest.param(
        lambda a, b: setattr(b, "image_key", None),
        id="missing_private_key"),
    pytest.param(
        lambda a, b: setattr(b, "image_key", "pump-checks/999999/2026/08/x.jpg"),
        id="foreign_private_key"),
])
def test_ineligibility_performs_no_external_work(
        mutate, owner, eligible_pair, external_calls):
    baseline, current = eligible_pair
    mutate(baseline, current)
    db.session.commit()

    with pytest.raises(service.ChecksNotComparable):
        service.resolve_eligible_sources(owner.id, _command())

    assert external_calls == {"s3": 0, "provider": 0}


def test_reversed_direction_is_rejected_without_sorting(
        owner, eligible_pair, external_calls):
    with pytest.raises(service.ChecksNotComparable):
        service.resolve_eligible_sources(
            owner.id, _command(CURRENT_TOKEN, BASELINE_TOKEN))

    assert external_calls == {"s3": 0, "provider": 0}


def test_self_comparison_is_rejected(owner, eligible_pair, external_calls):
    with pytest.raises(service.ChecksNotComparable):
        service.resolve_eligible_sources(
            owner.id, _command(BASELINE_TOKEN, BASELINE_TOKEN))

    assert external_calls == {"s3": 0, "provider": 0}


def test_cross_owner_and_unknown_sources_are_same_private_not_found(
        owner, stranger, external_calls):
    _make_check(stranger, BASELINE_TOKEN, datetime(2026, 8, 1, 9, 0, 0))
    _make_check(stranger, CURRENT_TOKEN, datetime(2026, 8, 14, 9, 0, 0))

    with pytest.raises(service.PumpCheckNotFound):
        service.resolve_eligible_sources(owner.id, _command())

    with pytest.raises(service.PumpCheckNotFound):
        service.resolve_eligible_sources(
            owner.id, _command("Z" * 24, "Y" * 24))

    assert external_calls == {"s3": 0, "provider": 0}


def _comparison(owner, eligible_pair, **fields):
    baseline, current = eligible_pair
    row = PumpCheckComparison(
        user_id=owner.id,
        baseline_pump_check_id=baseline.id,
        current_pump_check_id=current.id,
        public_id="C" * 24,
        status="completed",
        comparability="comparable",
        analysis={"summary": "Framing stayed consistent."},
        analysis_version=ANALYSIS_VERSION,
        created_at=datetime(2026, 8, 14, 10, 0, 0),
    )
    for key, value in fields.items():
        setattr(row, key, value)
    db.session.add(row)
    db.session.commit()
    return row


def test_serializer_exposes_only_public_directional_contract(
        owner, eligible_pair):
    row = _comparison(owner, eligible_pair)

    payload = service.serialize_comparison(row)

    assert set(payload) == {
        "id", "baseline_pump_check_id", "current_pump_check_id",
        "status", "comparability", "analysis",
        "analysis_version", "created_at",
    }
    assert payload["id"] == "C" * 24
    assert payload["baseline_pump_check_id"] == BASELINE_TOKEN
    assert payload["current_pump_check_id"] == CURRENT_TOKEN
    assert payload["analysis_version"] == ANALYSIS_VERSION
    assert payload["created_at"] == "2026-08-14T10:00:00Z"
    forbidden = {
        "image_url", "image_key", "user_id", "idempotency_key",
        "fingerprint", "analysis_attempt", "analysis_started_at",
        "analysis_failure_kind",
    }
    assert not forbidden & set(payload)


def test_get_owned_is_private_for_unknown_malformed_and_foreign_tokens(
        owner, stranger, eligible_pair):
    row = _comparison(owner, eligible_pair)

    assert service.get_owned(owner.id, row.public_id).id == row.id

    for token in (row.public_id + "x", "not a token", "D" * 24):
        with pytest.raises(service.ComparisonNotFound):
            service.get_owned(owner.id, token)

    with pytest.raises(service.ComparisonNotFound):
        service.get_owned(stranger.id, row.public_id)


def test_key_owner_predicate_is_segment_exact():
    # A thin wrapper over the existing segment check: the owner is segment two,
    # so a different id that merely contains the digits is not a match.
    assert s3_helper.key_belongs_to_user("pump-checks/7/2026/08/a.jpg", 7)
    assert not s3_helper.key_belongs_to_user("pump-checks/70/2026/08/a.jpg", 7)
    assert not s3_helper.key_belongs_to_user("pump-checks/x7/2026/08/a.jpg", 7)
    assert not s3_helper.key_belongs_to_user("7/2026/08/a.jpg", 7)
    assert not s3_helper.key_belongs_to_user("", 7)
    assert not s3_helper.key_belongs_to_user(None, 7)


def test_eligibility_never_reads_storage_for_the_happy_path(
        owner, eligible_pair, external_calls):
    service.resolve_eligible_sources(owner.id, _command())

    assert external_calls == {"s3": 0, "provider": 0}


def test_future_dated_pair_stays_ordered(owner, eligible_pair):
    baseline, current = eligible_pair
    baseline.captured_at = datetime(2026, 8, 14, 8, 59, 59)
    current.captured_at = datetime(2026, 8, 14, 9, 0, 0)
    db.session.commit()

    sources = service.resolve_eligible_sources(owner.id, _command())

    assert sources.baseline.captured_at < sources.current.captured_at
    assert sources.current.captured_at - sources.baseline.captured_at == (
        timedelta(seconds=1))
