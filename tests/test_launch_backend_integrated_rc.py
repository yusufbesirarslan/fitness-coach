"""Cross-boundary launch receipts; synthetic consent is never approval."""
import pytest
from alembic.script import ScriptDirectory

from app.extensions import db
from app.models import MealLog
from app.services import health_consent, health_disclosure, launch_social
from test_health_consent import command
from test_mobile_log_food_api import as_mobile, manual_command, provider_command  # noqa: F401


def test_f01_expansion_follows_the_existing_launch_free_revision():
    from pathlib import Path
    scripts = ScriptDirectory(str(Path(__file__).parents[1] / "migrations"))
    # A1 = O2-B adds the withdrawal-erasure tables after FatSecret retention.
    assert scripts.get_heads() == ["f01c0a1e0002"]
    assert scripts.get_revision("f01c0a1e0002").down_revision == "e1a5f0c3d2b4"
    assert scripts.get_revision("f01c0a1e0001").down_revision == "d8f1a3c5e7b9"
    assert scripts.get_revision("d8f1a3c5e7b9").down_revision == "c6f8b0d2e4a1"
    assert len(list(scripts.walk_revisions())) == 64


@pytest.mark.parametrize("eligibility", [False, True])
def test_social_closes_before_unpublished_health_authority(
        app, client, auth_user, monkeypatch, eligibility):
    app.config["ACCOUNT_ELIGIBILITY_ENABLED"] = eligibility
    monkeypatch.setattr(health_disclosure, "APPROVED_VERSION", None)
    def forbidden(*args, **kwargs):
        pytest.fail("social refusal must precede health authority")
    monkeypatch.setattr(health_consent, "processing", forbidden)
    for path in ("/friends/list", "/feed/data", "/leaderboard/data?scope=friends"):
        result = client.get(path)
        assert result.status_code == 404
        assert result.json["code"] == launch_social.ERROR_CODE
        assert result.headers["Cache-Control"] == "no-store"


def test_withdrawal_stops_manual_logging_erases_history_and_never_opens_provider(
        client, make_user, as_mobile, monkeypatch):
    owner = make_user("integrated-manual")
    def forbidden(*args, **kwargs):
        pytest.fail("provider must never be called")
    from app.services import mobile_food_discovery
    monkeypatch.setattr(mobile_food_discovery, "servings", forbidden)
    before = client.post("/api/v1/nutrition/logs", json=manual_command(),
                         headers=as_mobile(owner, key="integrated-manual-1"))
    assert before.status_code == 201
    row = MealLog.query.filter_by(user_id=owner.id).one()
    retained = (row.id, row.source, row.kalori, row.protein, row.karb, row.yag)
    # The D-B2 hold is lifted for covered paths since the 2026-11-09 FatSecret
    # clarification (tests/test_fatsecret_retention_phase1.py); its mechanics,
    # modelled here with the reference reverted, still refuse before any request.
    from app.services import fatsecret_boundary
    monkeypatch.setattr(fatsecret_boundary, "PROVIDER_PERSISTENCE_PERMISSION_REF", None)
    held = client.post("/api/v1/nutrition/logs", json=provider_command(),
                       headers=as_mobile(owner, key="integrated-provider-1"))
    assert held.status_code == 503
    assert held.json["error"]["code"] == "FOOD_PROVIDER_UNAVAILABLE"
    assert held.json["error"]["retryable"] is False
    stop = client.post("/api/v1/health-consent", headers=as_mobile(owner),
                       json=command(1, "withdraw", "integrated-stop-1"))
    assert stop.status_code == 200 and stop.json["state"] == "withdrawn"
    # A1 = O2-B (founder 2026-11-09): withdrawal erases health records.
    assert stop.json["erasure"]["state"] == "completed"
    for payload, key in ((manual_command(), "integrated-manual-2"),
                         (provider_command(), "integrated-provider-2")):
        result = client.post("/api/v1/nutrition/logs", json=payload,
                             headers=as_mobile(owner, key=key))
        assert result.status_code == 403
        assert result.json["error"]["code"] == "HEALTH_CONSENT_REQUIRED"
    assert client.get("/api/v1/account/me", headers=as_mobile(owner)).status_code == 200
    db.session.expire_all()
    assert retained[0] is not None
    assert MealLog.query.filter_by(user_id=owner.id).count() == 0


def test_global_board_remains_available_with_separate_health_authority(
        client, auth_user, monkeypatch):
    assert client.get("/leaderboard/data?scope=global").status_code == 200
    monkeypatch.setattr(health_disclosure, "APPROVED_VERSION", None)
    result = client.get("/leaderboard/data?scope=global")
    assert result.status_code == 403
    assert result.json["error"]["code"] == "HEALTH_CONSENT_REQUIRED"
