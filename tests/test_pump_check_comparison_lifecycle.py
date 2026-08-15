"""Idempotent convergence, analysis leases, and out-of-transaction I/O."""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import (
    PumpCheck,
    PumpCheckComparison,
    PumpCheckComparisonRequest,
)
from app.services.mobile_pump_check_comparisons import service
from app.services.mobile_pump_check_comparisons.analysis import (
    ANALYSIS_VERSION,
    InvalidComparisonAnalysis,
)
from app.services.mobile_pump_checks.analysis import (
    ANALYSIS_VERSION as SOURCE_ANALYSIS_VERSION,
)
from app.services.vision_images import ImagePreparationError
import s3_helper


BASELINE_TOKEN = "A" * 24
CURRENT_TOKEN = "B" * 24
KEY = "comparison-key-0001"
OTHER_KEY = "comparison-key-0002"


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


def _analysis(summary="Framing stayed consistent between the two images."):
    return {
        "summary": summary,
        "observed_changes": ["Outline appears similar."],
        "stable_areas": ["Lighting is comparable."],
        "focus_areas": ["Keep the same distance."],
        "limitations": ["Two images cannot establish progress."],
        "comparability_reasons": ["Framing and region match."],
        "next_check_guidance": "Repeat this framing next time.",
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
    return make_user("lifecycle-owner")


@pytest.fixture
def stranger(make_user):
    return make_user("lifecycle-stranger")


@pytest.fixture
def pair(owner):
    baseline = _make_check(
        owner, BASELINE_TOKEN, datetime(2026, 8, 1, 9, 0, 0))
    current = _make_check(
        owner, CURRENT_TOKEN, datetime(2026, 8, 14, 9, 0, 0))
    return baseline, current


@pytest.fixture
def counts(monkeypatch):
    calls = {"s3": 0, "bedrock": 0}

    def read(key, expected_user_id=None):
        assert db.session().in_transaction() is False
        calls["s3"] += 1
        return b"private-image-bytes"

    def analyze(baseline, current, context, source_quality_cap, provider=None):
        assert db.session().in_transaction() is False
        calls["bedrock"] += 1
        return "comparable", _analysis()

    monkeypatch.setattr(service.s3_helper, "get_object_bytes", read)
    monkeypatch.setattr(
        service, "prepare_image_for_vision",
        lambda raw, media_type: (raw, media_type))
    monkeypatch.setattr(service, "analyze_images", analyze)
    return calls


def _seed_comparison(owner, pair, **fields):
    baseline, current = pair
    row = PumpCheckComparison(
        user_id=owner.id,
        baseline_pump_check_id=baseline.id,
        current_pump_check_id=current.id,
        public_id="C" * 24,
        status="pending",
        analysis_version=ANALYSIS_VERSION,
        analysis_attempt=0,
    )
    for key, value in fields.items():
        setattr(row, key, value)
    db.session.add(row)
    db.session.commit()
    return row


def _seed_ledger(owner, comparison, key, command):
    row = PumpCheckComparisonRequest(
        user_id=owner.id,
        idempotency_key=key,
        fingerprint=service.fingerprint(
            command.baseline_token, command.current_token, ANALYSIS_VERSION),
        comparison_id=comparison.id,
    )
    db.session.add(row)
    db.session.commit()
    return row


def test_existing_key_conflicts_before_source_lookup(
        monkeypatch, owner, pair, counts):
    comparison = _seed_comparison(owner, pair)
    _seed_ledger(owner, comparison, KEY, _command())
    monkeypatch.setattr(
        service, "resolve_eligible_sources",
        lambda *args, **kwargs: pytest.fail("source lookup must not run"))

    with pytest.raises(service.IdempotencyConflict):
        service.create_or_replay(
            owner.id, KEY, _command(CURRENT_TOKEN, BASELINE_TOKEN))

    assert counts == {"s3": 0, "bedrock": 0}


def test_same_key_same_command_replays_without_external_work(
        owner, pair, counts):
    first, created = service.create_or_replay(owner.id, KEY, _command())
    second, replay_created = service.create_or_replay(
        owner.id, KEY, _command())

    assert created is True
    assert replay_created is False
    assert first.id == second.id
    assert second.status == "completed"
    assert second.comparability == "comparable"
    assert counts == {"s3": 2, "bedrock": 1}


def test_different_keys_same_pair_converge(owner, pair, counts):
    first, first_created = service.create_or_replay(owner.id, KEY, _command())
    second, second_created = service.create_or_replay(
        owner.id, OTHER_KEY, _command())

    assert first.id == second.id
    assert first_created is True
    assert second_created is False
    assert PumpCheckComparison.query.count() == 1
    assert PumpCheckComparisonRequest.query.count() == 2
    assert counts["bedrock"] == 1


def test_cross_owner_identical_keys_stay_independent(
        owner, stranger, pair, counts):
    _make_check(stranger, BASELINE_TOKEN, datetime(2026, 8, 1, 9, 0, 0))
    _make_check(stranger, CURRENT_TOKEN, datetime(2026, 8, 14, 9, 0, 0))

    mine, _ = service.create_or_replay(owner.id, KEY, _command())
    theirs, theirs_created = service.create_or_replay(
        stranger.id, KEY, _command())

    assert mine.id != theirs.id
    assert theirs_created is True
    assert theirs.user_id == stranger.id
    assert counts["bedrock"] == 2


def test_ineligible_command_creates_no_canonical_or_ledger_row(
        owner, pair, counts):
    baseline, _current = pair
    baseline.body_region = "back"
    db.session.commit()

    with pytest.raises(service.ChecksNotComparable):
        service.create_or_replay(owner.id, KEY, _command())

    assert PumpCheckComparison.query.count() == 0
    assert PumpCheckComparisonRequest.query.count() == 0
    assert counts == {"s3": 0, "bedrock": 0}


def test_unexpired_lease_returns_analyzing_without_external_io(
        owner, pair, counts):
    row = _seed_comparison(
        owner, pair, status="analyzing",
        analysis_started_at=datetime.utcnow(), analysis_attempt=3)
    _seed_ledger(owner, row, KEY, _command())

    replay, created = service.create_or_replay(owner.id, KEY, _command())

    assert replay.id == row.id
    assert replay.status == "analyzing"
    assert created is False
    assert counts == {"s3": 0, "bedrock": 0}


def test_stale_lease_reclaims_and_old_generation_cannot_finalize(
        owner, pair, counts):
    row = _seed_comparison(
        owner, pair, status="analyzing",
        analysis_started_at=datetime.utcnow() - timedelta(minutes=20),
        analysis_attempt=3)
    _seed_ledger(owner, row, KEY, _command())

    service.create_or_replay(owner.id, KEY, _command())

    assert service._finalize_success(
        row.id, owner.id, 3, "comparable", _analysis()) is False
    db.session.rollback()
    refreshed = db.session.get(PumpCheckComparison, row.id)
    assert refreshed.analysis_attempt == 4
    assert refreshed.status == "completed"
    assert counts["bedrock"] == 1


def test_completed_row_is_never_reanalyzed(owner, pair, counts):
    service.create_or_replay(owner.id, KEY, _command())

    again, created = service.create_or_replay(owner.id, OTHER_KEY, _command())

    assert again.status == "completed"
    assert created is False
    assert counts == {"s3": 2, "bedrock": 1}


def test_retryable_failure_is_reclaimed_by_the_same_key(
        monkeypatch, owner, pair, counts):
    failures = {"count": 0}
    real_read = service.s3_helper.get_object_bytes

    def flaky(key, expected_user_id=None):
        if failures["count"] == 0:
            failures["count"] += 1
            raise s3_helper.S3Error("transient")
        return real_read(key, expected_user_id=expected_user_id)

    monkeypatch.setattr(service.s3_helper, "get_object_bytes", flaky)

    with pytest.raises(service.ComparisonUnavailable):
        service.create_or_replay(owner.id, KEY, _command())

    failed = PumpCheckComparison.query.one()
    assert failed.status == "failed"
    assert failed.analysis_failure_kind == "storage"

    row, created = service.create_or_replay(owner.id, KEY, _command())

    assert row.status == "completed"
    assert created is False
    assert PumpCheckComparison.query.count() == 1
    assert PumpCheckComparisonRequest.query.count() == 1


def test_corrupt_media_is_terminal_and_never_calls_the_provider(
        monkeypatch, owner, pair, counts):
    def corrupt(raw, media_type):
        raise ImagePreparationError("image bytes are not decodable")

    monkeypatch.setattr(service, "prepare_image_for_vision", corrupt)

    with pytest.raises(service.MediaNotComparable):
        service.create_or_replay(owner.id, KEY, _command())

    row = PumpCheckComparison.query.one()
    assert row.status == "failed"
    assert row.analysis_failure_kind == "invalid_media"
    assert counts["bedrock"] == 0

    # Terminal media failure is not reclaimable by a retry.
    with pytest.raises(service.MediaNotComparable):
        service.create_or_replay(owner.id, KEY, _command())

    assert counts["bedrock"] == 0


def test_invalid_provider_output_is_retryable_and_never_stored(
        monkeypatch, owner, pair, counts):
    def invalid(*args, **kwargs):
        raise InvalidComparisonAnalysis("comparison response is invalid")

    monkeypatch.setattr(service, "analyze_images", invalid)

    with pytest.raises(service.ComparisonUnavailable):
        service.create_or_replay(owner.id, KEY, _command())

    row = PumpCheckComparison.query.one()
    assert row.status == "failed"
    assert row.analysis_failure_kind == "invalid_output"
    assert row.analysis is None
    assert row.comparability is None


def test_provider_not_comparable_is_a_completed_comparison(
        monkeypatch, owner, pair, counts):
    monkeypatch.setattr(
        service, "analyze_images",
        lambda *args, **kwargs: ("not_comparable", _analysis()))

    row, created = service.create_or_replay(owner.id, KEY, _command())

    assert created is True
    assert row.status == "completed"
    assert row.comparability == "not_comparable"
    assert row.analysis["summary"]


def test_limited_source_cap_reaches_the_provider(
        monkeypatch, owner, pair, counts):
    baseline, _current = pair
    baseline.analysis = _source_analysis("limited")
    db.session.commit()
    seen = {}

    def analyze(baseline_image, current_image, context, source_quality_cap,
                provider=None):
        seen["cap"] = source_quality_cap
        seen["context"] = context
        return "limited", _analysis()

    monkeypatch.setattr(service, "analyze_images", analyze)

    service.create_or_replay(owner.id, KEY, _command())

    assert seen["cap"] == "limited"
    assert seen["context"] == {"body_region": "upper_body"}


def test_provider_receives_only_normalized_images_not_stored_narratives(
        monkeypatch, owner, pair, counts):
    seen = {}

    def analyze(baseline_image, current_image, context, source_quality_cap,
                provider=None):
        seen["baseline"] = baseline_image
        seen["current"] = current_image
        seen["context"] = context
        return "comparable", _analysis()

    monkeypatch.setattr(service, "analyze_images", analyze)

    service.create_or_replay(owner.id, KEY, _command())

    assert seen["baseline"] == (b"private-image-bytes", "image/jpeg")
    assert seen["current"] == (b"private-image-bytes", "image/jpeg")
    assert set(seen["context"]) == {"body_region"}


def test_comparison_reads_originals_without_upload_or_replacement(
        monkeypatch, owner, pair, counts):
    reads = []
    real_read = service.s3_helper.get_object_bytes

    def read(object_key, expected_user_id=None):
        reads.append((object_key, expected_user_id))
        return real_read(object_key, expected_user_id=expected_user_id)

    monkeypatch.setattr(service.s3_helper, "get_object_bytes", read)
    monkeypatch.setattr(
        service.s3_helper, "upload_image",
        lambda *args, **kwargs: pytest.fail("comparison must not write S3"))

    service.create_or_replay(owner.id, KEY, _command())

    baseline, current = pair
    assert reads == [
        (baseline.image_key, owner.id),
        (current.image_key, owner.id),
    ]


def test_s3_and_bedrock_run_outside_transaction(owner, pair, counts):
    # The counts fixture asserts `in_transaction() is False` inside both fakes.
    service.create_or_replay(owner.id, KEY, _command())

    assert counts == {"s3": 2, "bedrock": 1}


def test_stale_generation_cannot_overwrite_a_newer_result(owner, pair, counts):
    row, _ = service.create_or_replay(owner.id, KEY, _command())
    stored = row.analysis

    assert service._finalize_success(
        row.id, owner.id, row.analysis_attempt - 1, "limited",
        _analysis("stale")) is False
    db.session.rollback()

    refreshed = db.session.get(PumpCheckComparison, row.id)
    assert refreshed.analysis == stored
    assert refreshed.comparability == "comparable"


def test_lifecycle_logging_carries_no_identifiers_or_content():
    import inspect

    signature = inspect.signature(service._log_outcome)

    assert list(signature.parameters) == [
        "event", "status", "comparability", "attempt", "duration_ms",
    ]
    source = inspect.getsource(service._log_outcome)
    for forbidden in (
            "idempotency", "fingerprint", "image", "prompt", "key",
            "public_id", "user_id", "analysis["):
        assert forbidden not in source
