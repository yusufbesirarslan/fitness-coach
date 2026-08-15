"""Opt-in PostgreSQL races through the real comparison service and ORM schema."""
import os
import threading
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


BASELINE_TOKEN = "A" * 24
CURRENT_TOKEN = "B" * 24
SOURCE_ANALYSIS = {
    "summary": "Visible definition can be assessed in this image.",
    "observations": ["Shoulder outline is visible."],
    "strengths": ["Consistent framing."],
    "focus_areas": ["Keep lighting even."],
    "limitations": ["One image cannot establish progress."],
    "next_check_guidance": "Repeat this framing next time.",
    "quality": "sufficient",
}


def _analysis(summary="Framing stayed consistent."):
    return {
        "summary": summary,
        "observed_changes": [],
        "stable_areas": [],
        "focus_areas": [],
        "limitations": ["Two images cannot establish progress."],
        "comparability_reasons": ["Framing and region match."],
        "next_check_guidance": "Repeat this framing next time.",
    }


@pytest.fixture
def pg_comparison_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip(
            "PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL database is not reachable")
    finally:
        probe.dispose()

    from flask import Flask
    from app.extensions import db
    from app.models import PumpCheck, User
    from app.services.mobile_pump_check_comparisons import service
    from app.services.mobile_pump_checks.analysis import (
        ANALYSIS_VERSION as SOURCE_ANALYSIS_VERSION,
    )

    app = Flask("mobile-pump-comparison-pg-race")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-comparison-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        users = [
            User(username=f"pg-comparison-{n}",
                 email=f"pg-comparison-{n}@example.invalid",
                 cognito_sub=f"pg-comparison-{n}-sub")
            for n in (1, 2)
        ]
        db.session.add_all(users)
        db.session.commit()
        user_ids = [user.id for user in users]
        for user_id in user_ids:
            for token, captured_at in (
                (BASELINE_TOKEN, datetime(2026, 8, 1, 9, 0, 0)),
                (CURRENT_TOKEN, datetime(2026, 8, 14, 9, 0, 0)),
            ):
                db.session.add(PumpCheck(
                    user_id=user_id,
                    public_id=token,
                    captured_at=captured_at,
                    body_region="upper_body",
                    location_type="gym",
                    visibility="private",
                    valid=True,
                    analysis_status="completed",
                    analysis=dict(SOURCE_ANALYSIS),
                    analysis_version=SOURCE_ANALYSIS_VERSION,
                    image_key=(
                        f"pump-checks/{user_id}/2026/08/{token[:8]}.jpg"),
                ))
        db.session.commit()

    lock = threading.Lock()
    calls = {"s3": 0, "bedrock": 0}

    def read(key, expected_user_id=None):
        with lock:
            calls["s3"] += 1
        return b"private-image-bytes"

    def analyze(*args, **kwargs):
        with lock:
            calls["bedrock"] += 1
        return "comparable", _analysis()

    monkeypatch.setattr(service.s3_helper, "get_object_bytes", read)
    monkeypatch.setattr(
        service, "prepare_image_for_vision",
        lambda raw, media_type: (raw, media_type))
    monkeypatch.setattr(service, "analyze_images", analyze)
    try:
        yield app, user_ids, calls
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def _command(baseline=BASELINE_TOKEN, current=CURRENT_TOKEN):
    from app.services.mobile_pump_check_comparisons.service import CreateCommand
    return CreateCommand(baseline_token=baseline, current_token=current)


def _race(app, contenders):
    """Run every contender through its own context/session behind a barrier."""
    from app.extensions import db
    from app.services.mobile_pump_check_comparisons.service import (
        IdempotencyConflict,
        create_or_replay,
    )

    barrier = threading.Barrier(len(contenders))
    outcomes = {}

    def run(index):
        user_id, key, command = contenders[index]
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                row, created = create_or_replay(user_id, key, command)
                outcomes[index] = ("ok", row.public_id, created)
            except IdempotencyConflict:
                outcomes[index] = ("conflict", None, None)
            except Exception as error:
                outcomes[index] = ("unexpected", type(error).__name__, None)
            finally:
                db.session.remove()

    threads = [
        threading.Thread(target=run, args=(index,), daemon=True)
        for index in range(len(contenders))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), outcomes
    return outcomes


def test_same_key_same_command_converges_once(pg_comparison_app):
    from app.models import PumpCheckComparison, PumpCheckComparisonRequest
    app, user_ids, calls = pg_comparison_app

    outcomes = _race(app, [
        (user_ids[0], "comparison-key-0001", _command()),
        (user_ids[0], "comparison-key-0001", _command()),
    ])

    assert {outcome[0] for outcome in outcomes.values()} == {"ok"}
    assert len({outcome[1] for outcome in outcomes.values()}) == 1
    assert calls["bedrock"] == 1
    with app.app_context():
        assert PumpCheckComparison.query.count() == 1
        assert PumpCheckComparisonRequest.query.count() == 1


def test_different_keys_same_pair_converge(pg_comparison_app):
    from app.models import PumpCheckComparison, PumpCheckComparisonRequest
    app, user_ids, calls = pg_comparison_app

    outcomes = _race(app, [
        (user_ids[0], "comparison-key-0001", _command()),
        (user_ids[0], "comparison-key-0002", _command()),
    ])

    assert {outcome[0] for outcome in outcomes.values()} == {"ok"}
    assert len({outcome[1] for outcome in outcomes.values()}) == 1
    assert calls["bedrock"] == 1
    with app.app_context():
        assert PumpCheckComparison.query.count() == 1
        assert PumpCheckComparisonRequest.query.count() == 2


def test_same_key_different_command_has_one_winner_and_conflict(
        pg_comparison_app):
    from app.models import PumpCheckComparison
    app, user_ids, _calls = pg_comparison_app

    outcomes = _race(app, [
        (user_ids[0], "comparison-key-0001", _command()),
        (user_ids[0], "comparison-key-0001",
         _command(CURRENT_TOKEN, BASELINE_TOKEN)),
    ])

    assert sorted(outcome[0] for outcome in outcomes.values()) == [
        "conflict", "ok"]
    with app.app_context():
        assert PumpCheckComparison.query.count() == 1


def test_cross_user_same_key_is_independent(pg_comparison_app):
    from app.models import PumpCheckComparison
    app, user_ids, calls = pg_comparison_app

    outcomes = _race(app, [
        (user_ids[0], "comparison-key-0001", _command()),
        (user_ids[1], "comparison-key-0001", _command()),
    ])

    assert {outcome[0] for outcome in outcomes.values()} == {"ok"}
    assert len({outcome[1] for outcome in outcomes.values()}) == 2
    assert calls["bedrock"] == 2
    with app.app_context():
        assert PumpCheckComparison.query.count() == 2


def test_stale_generation_cannot_overwrite_newer_result(pg_comparison_app):
    from app.extensions import db
    from app.models import PumpCheckComparison
    from app.services.mobile_pump_check_comparisons import service
    app, user_ids, _calls = pg_comparison_app

    with app.app_context():
        row, _created = service.create_or_replay(
            user_ids[0], "comparison-key-0001", _command())
        comparison_id = row.id
        new_analysis = dict(row.analysis)
        new_attempt = row.analysis_attempt
        old_attempt = new_attempt - 1

        assert old_attempt < new_attempt
        assert service._finalize_success(
            comparison_id, user_ids[0], old_attempt, "limited",
            _analysis("stale")) is False
        db.session.rollback()

        stored = db.session.get(PumpCheckComparison, comparison_id)
        assert stored.analysis == new_analysis
        assert stored.comparability == "comparable"


def test_expired_lease_is_reclaimed_by_exactly_one_contender(
        pg_comparison_app):
    from app.extensions import db
    from app.models import PumpCheck, PumpCheckComparison
    from app.services.mobile_pump_check_comparisons.analysis import (
        ANALYSIS_VERSION,
    )
    app, user_ids, calls = pg_comparison_app

    with app.app_context():
        sources = PumpCheck.query.filter_by(user_id=user_ids[0]).order_by(
            PumpCheck.captured_at).all()
        db.session.add(PumpCheckComparison(
            user_id=user_ids[0],
            baseline_pump_check_id=sources[0].id,
            current_pump_check_id=sources[1].id,
            public_id="C" * 24,
            status="analyzing",
            analysis_started_at=datetime.utcnow() - timedelta(minutes=30),
            analysis_attempt=3,
            analysis_version=ANALYSIS_VERSION,
        ))
        db.session.commit()

    outcomes = _race(app, [
        (user_ids[0], "comparison-key-0001", _command()),
        (user_ids[0], "comparison-key-0002", _command()),
    ])

    assert {outcome[0] for outcome in outcomes.values()} == {"ok"}
    # Exactly one contender may reclaim the expired lease and call the provider.
    assert calls["bedrock"] == 1
    with app.app_context():
        stored = PumpCheckComparison.query.one()
        assert stored.status == "completed"
        assert stored.analysis_attempt == 4
