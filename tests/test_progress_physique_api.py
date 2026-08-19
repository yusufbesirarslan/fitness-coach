"""Contract tests for GET /api/progress/physique (Progress Redesign PR4).

    python -m pytest tests/test_progress_physique_api.py -v
"""
from datetime import datetime

from app.blueprints import tracking
from app.extensions import db
from app.models import PumpCheck, PumpCheckComparison
from app.services.mobile_pump_check_comparisons.analysis import ANALYSIS_VERSION
from app.services.mobile_pump_checks.analysis import (
    ANALYSIS_VERSION as SOURCE_ANALYSIS_VERSION,
)
from app.services.progress_physique import (
    CONTRACT_VERSION,
    STATES,
    UnknownPhysiqueComparability,
)

PHYSIQUE_URL = "/api/progress/physique"
SUMMARY_URL = "/api/progress/summary"
INSIGHTS_URL = "/api/progress/axis-insights"
GALLERY_DATA = "/pump-check-gallery/data"


def _login(make_user, login, name, **fields):
    user = make_user(name, profile_complete=True, **fields)
    login(name)
    return user


def _token(prefix, n):
    return (f"{prefix}{n:02d}" + "x" * 24)[:24]


def _check(user, *, token, captured_at, region="upper_body"):
    row = PumpCheck(
        user_id=user.id,
        public_id=token,
        captured_at=captured_at,
        body_region=region,
        location_type="gym",
        visibility="private",
        valid=True,
        analysis_status="completed",
        analysis={
            "summary": "Visible definition can be assessed in this image.",
            "observations": ["Shoulder outline is visible."],
            "strengths": ["Consistent framing."],
            "focus_areas": ["Keep lighting even."],
            "limitations": ["One image cannot establish progress."],
            "next_check_guidance": "Repeat this framing next time.",
            "quality": "sufficient",
        },
        analysis_version=SOURCE_ANALYSIS_VERSION,
        image_key=f"pump-checks/{user.id}/2026/08/{token[:8]}.jpg",
    )
    db.session.add(row)
    db.session.flush()
    return row


def _comparison(user, baseline, current, *, comparability="comparable"):
    db.session.add(PumpCheckComparison(
        user_id=user.id,
        baseline_pump_check_id=baseline.id,
        current_pump_check_id=current.id,
        public_id=_token("cmp", baseline.id + current.id),
        status="completed",
        comparability=comparability,
        analysis={
            "summary": "Shoulder outline looks a little fuller.",
            "observed_changes": ["Upper chest appears slightly fuller."],
            "stable_areas": ["Waist line looks similar."],
            "focus_areas": ["Keep the same camera height."],
            "limitations": ["Lighting differs between the two frames."],
            "comparability_reasons": ["Both frames show the same pose."],
            "next_check_guidance": "Match the first photo's lighting.",
        },
        analysis_version=ANALYSIS_VERSION,
    ))
    db.session.commit()


def test_physique_requires_authentication(app, client):
    r = client.get(PHYSIQUE_URL)
    assert r.status_code in (302, 401)
    assert b"recent_checks" not in r.data


def test_user_id_input_cannot_name_another_owner(app, client, make_user, login):
    other = make_user("ppapiother", profile_complete=True)
    _check(other, token=_token("o", 1), captured_at=datetime(2026, 8, 10, 8),
           region="legs")
    db.session.commit()
    _login(make_user, login, "ppapimine")
    mine = client.get(PHYSIQUE_URL).get_json()
    ignored = client.get(f"{PHYSIQUE_URL}?user_id={other.id}").get_json()
    assert mine == ignored
    assert mine["state"] == "empty"
    assert mine["selected_region"] is None


def test_private_no_store_header(app, client, make_user, login):
    _login(make_user, login, "ppapicache")
    r = client.get(PHYSIQUE_URL)
    assert r.headers["Cache-Control"] == "private, no-store"


def test_empty_contract(app, client, make_user, login):
    _login(make_user, login, "ppapiempty")
    r = client.get(PHYSIQUE_URL)
    assert r.status_code == 200
    d = r.get_json()
    assert d["contract_version"] == CONTRACT_VERSION == 1
    assert d["state"] == "empty"
    assert set(d) == {
        "contract_version", "state", "selected_region", "regions",
        "recent_checks", "comparison", "legacy_gallery_available",
    }
    assert d["recent_checks"] == []
    assert d["comparison"] is None


def test_invalid_region_is_400_not_a_silent_fallback(
        app, client, make_user, login):
    user = _login(make_user, login, "ppapibad")
    _check(user, token=_token("b", 1), captured_at=datetime(2026, 8, 1, 8))
    db.session.commit()
    r = client.get(f"{PHYSIQUE_URL}?region=shoulders")
    assert r.status_code == 400
    assert r.get_json().get("state") is None
    assert "upper_body" not in r.get_data(as_text=True)


def test_valid_region_query_is_honoured(app, client, make_user, login):
    user = _login(make_user, login, "ppapireg")
    _check(user, token=_token("r", 1), captured_at=datetime(2026, 8, 12, 8),
           region="back")
    _check(user, token=_token("r", 2), captured_at=datetime(2026, 8, 1, 8),
           region="arms")
    db.session.commit()
    d = client.get(f"{PHYSIQUE_URL}?region=arms").get_json()
    assert d["selected_region"] == "arms"
    assert d["recent_checks"][0]["body_region"] == "arms"


def test_comparable_payload_shape(app, client, make_user, login):
    user = _login(make_user, login, "ppapicomp")
    a = _check(user, token=_token("c", 1), captured_at=datetime(2026, 8, 1, 8))
    b = _check(user, token=_token("c", 2), captured_at=datetime(2026, 8, 14, 8))
    _comparison(user, a, b)
    d = client.get(PHYSIQUE_URL).get_json()
    assert d["state"] == "comparison_available"
    assert d["state"] in STATES
    comparison = d["comparison"]
    assert comparison["comparability"] == "comparable"
    assert comparison["baseline_pump_check_id"] == _token("c", 1)
    assert comparison["current_pump_check_id"] == _token("c", 2)
    assert comparison["current_is_latest_check"] is True
    assert set(comparison["analysis"]) == {
        "summary", "observed_changes", "stable_areas", "focus_areas",
        "limitations", "comparability_reasons", "next_check_guidance",
    }


def _wrap_unknown_comparability(real, value="pretty_good"):
    def _mutated_row(user_id, region):
        loaded = real(user_id, region)
        if loaded is None:
            return None

        class _Wrap:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                if name == "comparability":
                    return value
                return getattr(self._inner, name)

        return _Wrap(loaded)

    return _mutated_row


def test_canonical_comparability_values_pass_through_the_api(
        app, client, make_user, login):
    cases = (
        ("ppapicmp", "comparable"),
        ("ppapilim", "limited"),
        ("ppapinc", "not_comparable"),
    )
    for name, value in cases:
        user = _login(make_user, login, name)
        a = _check(user, token=_token(name[-2:], 1),
                   captured_at=datetime(2026, 8, 1, 8))
        b = _check(user, token=_token(name[-2:], 2),
                   captured_at=datetime(2026, 8, 14, 8))
        _comparison(user, a, b, comparability=value)
        d = client.get(PHYSIQUE_URL).get_json()
        assert d["state"] == "comparison_available"
        assert d["comparison"]["comparability"] == value


def test_unknown_comparability_is_safe_generic_500(
        app, client, make_user, login, monkeypatch):
    user = _login(make_user, login, "ppapidrift")
    a = _check(user, token=_token("d", 1), captured_at=datetime(2026, 8, 1, 8))
    b = _check(user, token=_token("d", 2), captured_at=datetime(2026, 8, 14, 8))
    _comparison(user, a, b)

    import app.services.progress_physique as pkg

    monkeypatch.setattr(
        pkg, "load_latest_completed_comparison",
        _wrap_unknown_comparability(pkg.load_latest_completed_comparison))
    r = client.get(PHYSIQUE_URL)
    assert r.status_code == 500
    body = r.get_data(as_text=True)
    payload = r.get_json()
    assert payload["error"]
    assert "state" not in payload
    assert "comparison_available" not in body
    assert "pretty_good" not in body
    assert "UnknownPhysiqueComparability" not in body
    assert "Traceback" not in body
    assert tracking._progress_summary_error_class(
        UnknownPhysiqueComparability()) == "contract_drift"


def test_unknown_comparability_failure_is_isolated(
        app, client, make_user, login, monkeypatch):
    user = _login(make_user, login, "ppapidriftiso")
    a = _check(user, token=_token("i", 1), captured_at=datetime(2026, 8, 1, 8))
    b = _check(user, token=_token("i", 2), captured_at=datetime(2026, 8, 14, 8))
    _comparison(user, a, b)

    import app.services.progress_physique as pkg

    monkeypatch.setattr(
        pkg, "load_latest_completed_comparison",
        _wrap_unknown_comparability(pkg.load_latest_completed_comparison))
    assert client.get(PHYSIQUE_URL).status_code == 500
    assert client.get(SUMMARY_URL).status_code == 200
    assert client.get(INSIGHTS_URL).status_code == 200


def test_backend_failure_is_not_an_empty_state(
        app, client, make_user, login, monkeypatch):
    _login(make_user, login, "ppapifail")

    def _boom(*_a, **_kw):
        raise RuntimeError("upstream exploded: SELECT * FROM pump_check")

    monkeypatch.setattr(tracking, "build_progress_physique", _boom)
    r = client.get(PHYSIQUE_URL)
    assert r.status_code == 500
    body = r.get_data(as_text=True)
    for leaked in ("exploded", "SELECT", "pump_check", "Traceback", "empty"):
        assert leaked not in body, leaked
    assert r.get_json()["error"]
    assert "state" not in r.get_json()


def test_failure_is_isolated_from_other_progress_endpoints(
        app, client, make_user, login, monkeypatch):
    _login(make_user, login, "ppapiiso")

    def _boom(*_a, **_kw):
        raise RuntimeError("physique down")

    monkeypatch.setattr(tracking, "build_progress_physique", _boom)
    assert client.get(PHYSIQUE_URL).status_code == 500
    assert client.get(SUMMARY_URL).status_code == 200
    assert client.get(INSIGHTS_URL).status_code == 200


def test_existing_gallery_contract_is_untouched(
        app, client, make_user, login):
    user = _login(make_user, login, "ppapigal")
    _check(user, token=_token("g", 1), captured_at=datetime(2026, 8, 1, 8))
    db.session.commit()
    gallery = client.get(GALLERY_DATA)
    physique = client.get(PHYSIQUE_URL)
    assert gallery.status_code == 200
    assert "items" in gallery.get_json()
    assert "recent_checks" in physique.get_json()
    assert "recent_checks" not in gallery.get_json()


def test_payload_never_includes_db_ids_or_image_keys(
        app, client, make_user, login):
    user = _login(make_user, login, "ppapileak")
    a = _check(user, token=_token("k", 1), captured_at=datetime(2026, 8, 1, 8))
    b = _check(user, token=_token("k", 2), captured_at=datetime(2026, 8, 14, 8))
    _comparison(user, a, b)
    body = client.get(PHYSIQUE_URL).get_json()
    raw = client.get(PHYSIQUE_URL).get_data(as_text=True)
    assert '"image_key"' not in raw
    assert "bucket" not in raw
    assert body["recent_checks"][0]["id"] == _token("k", 2)
    assert body["comparison"]["id"] != a.id
    assert body["comparison"]["baseline_pump_check_id"] == _token("k", 1)
    assert isinstance(body["comparison"]["id"], str)
