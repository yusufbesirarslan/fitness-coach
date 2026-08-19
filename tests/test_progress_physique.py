"""Physique Progress service tests (Progress Redesign PR4).

The package composes canonical Pump Check history and persisted comparisons.
It must not create a comparison, call a provider, write to the database, or
treat ``created_at`` as capture chronology.

    python -m pytest tests/test_progress_physique.py -v
"""
import ast
import pathlib
import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

import s3_helper
from app.extensions import db
from app.models import PumpCheck, PumpCheckComparison
from app.services.mobile_pump_check_comparisons.analysis import ANALYSIS_VERSION
from app.services.mobile_pump_checks.analysis import (
    ANALYSIS_VERSION as SOURCE_ANALYSIS_VERSION,
)
from app.services.progress_physique import (
    BODY_REGIONS,
    COMPARABILITY_COMPARABLE,
    COMPARABILITY_LIMITED,
    COMPARABILITY_NOT_COMPARABLE,
    COMPARABILITY_VALUES,
    CONTRACT_VERSION,
    InvalidProgressPhysiqueRegion,
    STATE_COMPARISON_AVAILABLE,
    STATE_EMPTY,
    STATE_HISTORY_ONLY,
    STATE_SINGLE_CHECK,
    UnknownPhysiqueComparability,
    build_progress_physique,
    progress_physique_payload,
)


def _analysis(quality="sufficient"):
    return {
        "summary": "Visible definition can be assessed in this image.",
        "observations": ["Shoulder outline is visible."],
        "strengths": ["Consistent framing."],
        "focus_areas": ["Keep lighting even."],
        "limitations": ["One image cannot establish progress."],
        "next_check_guidance": "Repeat this framing next time.",
        "quality": quality,
    }


def _comparison_analysis(**overrides):
    payload = {
        "summary": "Shoulder outline looks a little fuller.",
        "observed_changes": ["Upper chest appears slightly fuller."],
        "stable_areas": ["Waist line looks similar."],
        "focus_areas": ["Keep the same camera height."],
        "limitations": ["Lighting differs between the two frames."],
        "comparability_reasons": ["Both frames show the same pose."],
        "next_check_guidance": "Match the first photo's lighting.",
    }
    payload.update(overrides)
    return payload


def _token(prefix, n):
    body = f"{prefix}{n:02d}"
    return (body + "x" * 24)[:24]


def _check(user, *, token, captured_at, region="upper_body",
           quality="sufficient", public=True):
    row = PumpCheck(
        user_id=user.id,
        public_id=token if public else None,
        captured_at=captured_at,
        body_region=region,
        location_type="gym",
        visibility="private",
        valid=True,
        analysis_status="completed",
        analysis=_analysis(quality),
        analysis_version=SOURCE_ANALYSIS_VERSION,
        image_key=f"pump-checks/{user.id}/2026/08/{(token or 'legacy')[:8]}.jpg",
    )
    db.session.add(row)
    db.session.flush()
    return row


def _legacy(user):
    row = PumpCheck(
        user_id=user.id,
        captured_at=None,
        body_region=None,
        visibility="private",
        valid=True,
        image_key=f"pump-checks/{user.id}/2025/01/legacy.jpg",
        created_at=datetime(2025, 1, 1, 12, 0, 0),
    )
    db.session.add(row)
    db.session.flush()
    return row


def _comparison(user, baseline, current, *, comparability="comparable",
                public_id=None, created_at=None):
    row = PumpCheckComparison(
        user_id=user.id,
        baseline_pump_check_id=baseline.id,
        current_pump_check_id=current.id,
        public_id=public_id or _token("cmp", baseline.id + current.id),
        status="completed",
        comparability=comparability,
        analysis=_comparison_analysis(),
        analysis_version=ANALYSIS_VERSION,
        created_at=created_at or datetime(2026, 8, 15, 10, 0, 0),
    )
    db.session.add(row)
    db.session.flush()
    return row


# ---------------------------------------------------------------------------
# Product states
# ---------------------------------------------------------------------------

def test_no_canonical_checks_is_empty(make_user):
    user = make_user("ppempty")
    db.session.commit()
    result = build_progress_physique(user.id)
    assert result.state == STATE_EMPTY
    assert result.selected_region is None
    assert result.recent_checks == ()
    assert result.comparison is None
    assert result.legacy_gallery_available is False


def test_legacy_only_checks_are_not_canonical_observations(make_user):
    user = make_user("pplegacy")
    _legacy(user)
    db.session.commit()
    result = build_progress_physique(user.id)
    assert result.state == STATE_EMPTY
    assert result.recent_checks == ()
    assert result.legacy_gallery_available is True
    payload = progress_physique_payload(result)
    assert payload["legacy_gallery_available"] is True
    assert payload["recent_checks"] == []


def test_one_canonical_check_is_single_check(make_user):
    user = make_user("ppsingle")
    _check(user, token=_token("s", 1), captured_at=datetime(2026, 8, 1, 8))
    db.session.commit()
    result = build_progress_physique(user.id)
    assert result.state == STATE_SINGLE_CHECK
    assert result.selected_region == "upper_body"
    assert len(result.recent_checks) == 1
    assert result.comparison is None


def test_two_checks_without_comparison_is_history_only(make_user):
    user = make_user("pphist")
    _check(user, token=_token("h", 1), captured_at=datetime(2026, 8, 1, 8))
    _check(user, token=_token("h", 2), captured_at=datetime(2026, 8, 10, 8))
    db.session.commit()
    result = build_progress_physique(user.id)
    assert result.state == STATE_HISTORY_ONLY
    assert [c.public_id for c in result.recent_checks] == [
        _token("h", 2), _token("h", 1)]
    assert result.comparison is None


def test_completed_comparison_is_comparison_available(make_user):
    user = make_user("ppcmp")
    older = _check(user, token=_token("c", 1), captured_at=datetime(2026, 8, 1, 8))
    newer = _check(user, token=_token("c", 2), captured_at=datetime(2026, 8, 14, 8))
    _comparison(user, older, newer, public_id=_token("cmp", 1))
    db.session.commit()
    result = build_progress_physique(user.id)
    assert result.state == STATE_COMPARISON_AVAILABLE
    assert result.comparison.public_id == _token("cmp", 1)
    assert result.comparison.comparability == COMPARABILITY_COMPARABLE
    assert result.comparison.baseline_pump_check_id == _token("c", 1)
    assert result.comparison.current_pump_check_id == _token("c", 2)
    assert result.comparison.current_is_latest_check is True


# ---------------------------------------------------------------------------
# Regions + chronology
# ---------------------------------------------------------------------------

def test_default_region_is_the_most_recent_canonical_area(make_user):
    user = make_user("ppdefreg")
    _check(user, token=_token("r", 1), captured_at=datetime(2026, 8, 1, 8),
           region="upper_body")
    _check(user, token=_token("r", 2), captured_at=datetime(2026, 8, 12, 8),
           region="back")
    db.session.commit()
    result = build_progress_physique(user.id)
    assert result.selected_region == "back"
    assert [r.body_region for r in result.regions] == ["back", "upper_body"]


def test_explicit_valid_region_is_honoured(make_user):
    user = make_user("ppexreg")
    _check(user, token=_token("e", 1), captured_at=datetime(2026, 8, 12, 8),
           region="back")
    _check(user, token=_token("e", 2), captured_at=datetime(2026, 8, 1, 8),
           region="arms")
    db.session.commit()
    result = build_progress_physique(user.id, region="arms")
    assert result.selected_region == "arms"
    assert result.recent_checks[0].body_region == "arms"


def test_invalid_region_is_refused_not_remapped(make_user):
    user = make_user("ppbadreg")
    _check(user, token=_token("b", 1), captured_at=datetime(2026, 8, 1, 8))
    db.session.commit()
    try:
        build_progress_physique(user.id, region="shoulders")
    except InvalidProgressPhysiqueRegion:
        return
    raise AssertionError("invalid region must not be accepted")


def test_empty_region_is_refused_not_treated_as_omitted(make_user):
    user = make_user("ppemptystr")
    _check(user, token=_token("s", 1), captured_at=datetime(2026, 8, 1, 8))
    db.session.commit()
    try:
        build_progress_physique(user.id, region="")
    except InvalidProgressPhysiqueRegion:
        return
    raise AssertionError("empty region must not be accepted")


def test_whitespace_region_is_refused_not_treated_as_omitted(make_user):
    user = make_user("ppwsreg")
    _check(user, token=_token("w", 1), captured_at=datetime(2026, 8, 1, 8))
    db.session.commit()
    try:
        build_progress_physique(user.id, region="   ")
    except InvalidProgressPhysiqueRegion:
        return
    raise AssertionError("whitespace region must not be accepted")


def test_valid_region_with_no_checks_is_empty_not_another_region(make_user):
    user = make_user("ppemptyreg")
    _check(user, token=_token("v", 1), captured_at=datetime(2026, 8, 1, 8),
           region="upper_body")
    db.session.commit()
    result = build_progress_physique(user.id, region="legs")
    assert result.state == STATE_EMPTY
    assert result.selected_region == "legs"
    assert result.recent_checks == ()


def test_created_at_is_never_used_as_capture_chronology(make_user):
    user = make_user("ppcreated")
    old_capture = _check(
        user, token=_token("t", 1), captured_at=datetime(2026, 7, 1, 8))
    new_capture = _check(
        user, token=_token("t", 2), captured_at=datetime(2026, 8, 1, 8))
    old_capture.created_at = datetime(2026, 8, 20, 8)
    new_capture.created_at = datetime(2026, 6, 1, 8)
    _legacy(user)
    db.session.commit()
    result = build_progress_physique(user.id)
    assert [c.public_id for c in result.recent_checks] == [
        _token("t", 2), _token("t", 1)]


def test_recent_checks_are_bounded_to_two(make_user):
    user = make_user("ppbound")
    for i in range(5):
        _check(user, token=_token("n", i),
               captured_at=datetime(2026, 8, 1 + i, 8))
    db.session.commit()
    result = build_progress_physique(user.id)
    assert len(result.recent_checks) == 2
    assert [c.public_id for c in result.recent_checks] == [
        _token("n", 4), _token("n", 3)]


def test_owner_isolation(make_user):
    mine = make_user("ppmine")
    other = make_user("ppother")
    _check(other, token=_token("o", 1), captured_at=datetime(2026, 8, 10, 8),
           region="legs")
    _check(mine, token=_token("m", 1), captured_at=datetime(2026, 8, 1, 8),
           region="arms")
    db.session.commit()
    result = build_progress_physique(mine.id)
    assert result.selected_region == "arms"
    assert all(c.public_id.startswith("m") for c in result.recent_checks)
    assert all(r.body_region == "arms" for r in result.regions)


# ---------------------------------------------------------------------------
# Comparison selection + comparability
# ---------------------------------------------------------------------------

def test_comparison_selection_uses_newest_current_capture(make_user):
    user = make_user("ppnewest")
    a = _check(user, token=_token("a", 1), captured_at=datetime(2026, 7, 1, 8))
    b = _check(user, token=_token("a", 2), captured_at=datetime(2026, 7, 15, 8))
    c = _check(user, token=_token("a", 3), captured_at=datetime(2026, 8, 10, 8))
    _comparison(user, a, b, public_id=_token("old", 1),
                created_at=datetime(2026, 8, 20, 8))
    _comparison(user, b, c, public_id=_token("new", 1),
                created_at=datetime(2026, 7, 20, 8))
    db.session.commit()
    result = build_progress_physique(user.id)
    assert result.comparison.public_id == _token("new", 1)
    assert result.comparison.current_pump_check_id == _token("a", 3)


def test_stale_comparison_is_flagged_not_latest(make_user):
    user = make_user("ppstale")
    a = _check(user, token=_token("s", 1), captured_at=datetime(2026, 7, 1, 8))
    b = _check(user, token=_token("s", 2), captured_at=datetime(2026, 7, 15, 8))
    _check(user, token=_token("s", 3), captured_at=datetime(2026, 8, 10, 8))
    _comparison(user, a, b, public_id=_token("st", 1))
    db.session.commit()
    result = build_progress_physique(user.id)
    assert result.comparison.current_is_latest_check is False
    assert result.recent_checks[0].public_id == _token("s", 3)


def test_comparable_passes_through(make_user):
    user = make_user("ppcmpok")
    a = _check(user, token=_token("ok", 1), captured_at=datetime(2026, 7, 1, 8))
    b = _check(user, token=_token("ok", 2), captured_at=datetime(2026, 7, 15, 8))
    _comparison(user, a, b, comparability="comparable",
                public_id=_token("okc", 1))
    db.session.commit()
    result = build_progress_physique(user.id)
    assert result.state == STATE_COMPARISON_AVAILABLE
    assert result.comparison.comparability == COMPARABILITY_COMPARABLE
    assert result.comparison.comparability in COMPARABILITY_VALUES


def test_limited_passes_through(make_user):
    user = make_user("pplim")
    a = _check(user, token=_token("l", 1), captured_at=datetime(2026, 7, 1, 8))
    b = _check(user, token=_token("l", 2), captured_at=datetime(2026, 7, 15, 8))
    _comparison(user, a, b, comparability="limited", public_id=_token("lm", 1))
    db.session.commit()
    result = build_progress_physique(user.id)
    assert result.state == STATE_COMPARISON_AVAILABLE
    assert result.comparison.comparability == COMPARABILITY_LIMITED


def test_not_comparable_passes_through(make_user):
    other = make_user("ppnc")
    c = _check(other, token=_token("n", 1), captured_at=datetime(2026, 7, 1, 8))
    d = _check(other, token=_token("n", 2), captured_at=datetime(2026, 7, 15, 8))
    _comparison(other, c, d, comparability="not_comparable",
                public_id=_token("nc", 1))
    db.session.commit()
    result = build_progress_physique(other.id)
    assert result.state == STATE_COMPARISON_AVAILABLE
    assert result.comparison.comparability == COMPARABILITY_NOT_COMPARABLE


def _wrap_unknown_comparability(real, value="pretty_good"):
    """Bypass the DB CHECK without weakening the canonical constraint."""

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


def test_unknown_comparability_fails_closed(make_user, monkeypatch):
    user = make_user("ppunk")
    a = _check(user, token=_token("u", 1), captured_at=datetime(2026, 7, 1, 8))
    b = _check(user, token=_token("u", 2), captured_at=datetime(2026, 7, 15, 8))
    _comparison(user, a, b, public_id=_token("uk", 1))
    db.session.commit()

    import app.services.progress_physique as pkg

    monkeypatch.setattr(
        pkg, "load_latest_completed_comparison",
        _wrap_unknown_comparability(pkg.load_latest_completed_comparison))
    with pytest.raises(UnknownPhysiqueComparability):
        build_progress_physique(user.id)


def test_unknown_comparability_does_not_become_comparison_available(
        make_user, monkeypatch):
    user = make_user("ppunkstate")
    a = _check(user, token=_token("s", 1), captured_at=datetime(2026, 7, 1, 8))
    b = _check(user, token=_token("s", 2), captured_at=datetime(2026, 7, 15, 8))
    _comparison(user, a, b, public_id=_token("uks", 1))
    db.session.commit()

    import app.services.progress_physique as pkg

    monkeypatch.setattr(
        pkg, "load_latest_completed_comparison",
        _wrap_unknown_comparability(pkg.load_latest_completed_comparison))
    with pytest.raises(UnknownPhysiqueComparability) as caught:
        build_progress_physique(user.id)
    assert "pretty_good" not in str(caught.value)
    assert "comparison_available" not in str(caught.value)


def test_public_wire_has_no_unknown_comparability_token():
    import app.services.progress_physique as pkg
    import app.services.progress_physique.models as models

    assert not hasattr(pkg, "COMPARABILITY_UNKNOWN")
    assert not hasattr(models, "COMPARABILITY_UNKNOWN")
    assert set(COMPARABILITY_VALUES) == {
        "comparable", "limited", "not_comparable",
    }
    assert "unknown" not in COMPARABILITY_VALUES


def test_pending_comparison_is_ignored(make_user):
    user = make_user("pppend")
    a = _check(user, token=_token("p", 1), captured_at=datetime(2026, 7, 1, 8))
    b = _check(user, token=_token("p", 2), captured_at=datetime(2026, 7, 15, 8))
    db.session.add(PumpCheckComparison(
        user_id=user.id,
        baseline_pump_check_id=a.id,
        current_pump_check_id=b.id,
        public_id=_token("pd", 1),
        status="pending",
        analysis_version=ANALYSIS_VERSION,
    ))
    db.session.commit()
    result = build_progress_physique(user.id)
    assert result.state == STATE_HISTORY_ONLY
    assert result.comparison is None


def test_other_user_comparison_is_invisible(make_user):
    owner = make_user("ppown")
    stranger = make_user("ppstr")
    a = _check(owner, token=_token("x", 1), captured_at=datetime(2026, 7, 1, 8))
    b = _check(owner, token=_token("x", 2), captured_at=datetime(2026, 7, 15, 8))
    _comparison(owner, a, b, public_id=_token("xo", 1))
    db.session.commit()
    result = build_progress_physique(stranger.id)
    assert result.state == STATE_EMPTY
    assert result.comparison is None


# ---------------------------------------------------------------------------
# Read-only + privacy + query budget
# ---------------------------------------------------------------------------

def test_building_physique_performs_no_write(make_user, monkeypatch):
    user = make_user("ppnowrite")
    _check(user, token=_token("w", 1), captured_at=datetime(2026, 8, 1, 8))
    db.session.commit()

    def _fail(*_a, **_kw):
        raise AssertionError("progress_physique must not write")

    for name in ("add", "add_all", "delete", "flush", "commit"):
        monkeypatch.setattr(db.session, name, _fail)
    build_progress_physique(user.id)


def test_no_provider_is_invoked(make_user, monkeypatch):
    user = make_user("ppnoprov")
    a = _check(user, token=_token("v", 1), captured_at=datetime(2026, 7, 1, 8))
    b = _check(user, token=_token("v", 2), captured_at=datetime(2026, 7, 15, 8))
    _comparison(user, a, b, public_id=_token("vp", 1))
    db.session.commit()

    def _boom(*_a, **_kw):
        raise AssertionError("provider must not run")

    monkeypatch.setattr(
        "app.services.mobile_pump_check_comparisons.analysis.analyze_images",
        _boom)
    monkeypatch.setattr(
        "app.services.mobile_pump_check_comparisons.service.create_or_replay",
        _boom)
    build_progress_physique(user.id)


def test_payload_leaks_no_broad_or_social_fields(make_user):
    user = make_user("ppleak", email="ppleak@example.com")
    a = _check(user, token=_token("k", 1), captured_at=datetime(2026, 7, 1, 8))
    b = _check(user, token=_token("k", 2), captured_at=datetime(2026, 7, 15, 8))
    _comparison(user, a, b, public_id=_token("kl", 1))
    db.session.commit()
    payload = progress_physique_payload(build_progress_physique(user.id))
    raw = str(payload)
    assert user.email not in raw
    assert user.username not in raw
    assert "image_key" not in raw
    assert "bucket" not in raw
    assert "likes" not in raw
    assert "shared_friend" not in raw
    assert "idempotency" not in raw
    assert "workout_score" not in raw

    def _walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in {
                    "user_id", "image_key", "bucket", "likes_count",
                    "comments_count", "reposts_count", "shared_friend_ids",
                    "idempotency_key",
                }, key
                # Opaque tokens only — never the integer primary key.
                if key == "id":
                    assert value != a.id and value != b.id
                    assert isinstance(value, str)
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    assert payload["contract_version"] == CONTRACT_VERSION
    assert set(payload["recent_checks"][0]) == {
        "id", "captured_at", "body_region", "analysis_status",
        "analysis_quality", "image_url",
    }


def test_image_signing_is_owner_scoped_and_bounded(make_user, monkeypatch):
    user = make_user("ppsign")
    other = make_user("ppsignx")
    a = _check(user, token=_token("g", 1), captured_at=datetime(2026, 7, 1, 8))
    b = _check(user, token=_token("g", 2), captured_at=datetime(2026, 7, 15, 8))
    _comparison(user, a, b, public_id=_token("gs", 1))
    db.session.commit()

    seen = []

    def _sign(key, expires_in=3600, expected_user_id=None):
        seen.append((key, expires_in, expected_user_id))
        return f"https://signed.example/{key}"

    monkeypatch.setattr(s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(s3_helper, "generate_presigned_url", _sign)
    result = build_progress_physique(user.id)
    assert seen
    assert all(expected == user.id for _key, _exp, expected in seen)
    assert all(expected != other.id for _key, _exp, expected in seen)
    assert all(exp == 3600 for _key, exp, _uid in seen)
    assert len(seen) <= 4
    assert result.recent_checks[0].image_url.startswith("https://signed.example/")


def test_query_count_is_bounded_independent_of_history_size(make_user):
    user = make_user("ppqcount")
    for i in range(12):
        _check(user, token=_token("q", i),
               captured_at=datetime(2026, 7, 1) + timedelta(days=i))
    older = PumpCheck.query.filter_by(public_id=_token("q", 0)).one()
    newer = PumpCheck.query.filter_by(public_id=_token("q", 11)).one()
    _comparison(user, older, newer, public_id=_token("qc", 1))
    db.session.commit()

    statements = []

    def _capture(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement)

    engine = db.engine
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        build_progress_physique(user.id)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    # Region aggregate + legacy exists + recent checks + comparison.
    assert 1 <= len(statements) <= 6, statements
    joined = "\n".join(statements).lower()
    assert "insert " not in joined
    assert "update " not in joined
    assert "delete " not in joined


def test_body_region_vocabulary_is_the_canonical_set():
    assert BODY_REGIONS == frozenset({
        "full_body", "upper_body", "lower_body", "back", "arms", "legs",
    })


# ---------------------------------------------------------------------------
# Structural guards
# ---------------------------------------------------------------------------

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "progress_physique"


def _imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _executable_source(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and \
                isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree)).lower()


def test_dependency_direction_is_one_way():
    root = PACKAGE.parents[2]
    for upstream in ("mobile_pump_checks", "mobile_pump_check_comparisons",
                     "progress_summary", "progress_insights"):
        folder = root / "app" / "services" / upstream
        if not folder.exists():
            continue
        for path in folder.rglob("*.py"):
            assert "progress_physique" not in path.read_text(encoding="utf-8"), path


def test_package_never_writes_or_calls_a_provider():
    banned_imports = (
        "app.services.ai", "openai", "anthropic", "boto3", "requests", "httpx",
    )
    banned_words = (
        "analyze_images", "create_or_replay", "session.add", "session.commit",
        "session.flush", "session.delete", "bedrock",
    )
    for path in PACKAGE.glob("*.py"):
        modules = _imported_modules(path)
        for module in modules:
            assert not any(
                module == b or module.startswith(b + ".")
                for b in banned_imports
            ), f"{path.name} imports {module}"
        code = _executable_source(path)
        for word in banned_words:
            assert word not in code, f"{path.name} contains {word}"


def test_created_at_is_not_a_canonical_fallback():
    for path in PACKAGE.glob("*.py"):
        code = _executable_source(path)
        assert "created_at or" not in code
        assert "or created_at" not in code


def test_no_score_or_body_composition_vocabulary_is_invented():
    banned = ("body.fat", "body_fat", "physique_score", "progress_percent",
              "circumference", "attractiveness")
    for path in PACKAGE.glob("*.py"):
        code = _executable_source(path)
        for word in banned:
            assert word not in code, f"{path.name} mentions {word}"
