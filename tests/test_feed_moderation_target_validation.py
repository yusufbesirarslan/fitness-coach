"""F9: moderation writes only for targets the viewer may currently see.

/feed/hide and /feed/report used to persist a self-scoped FeedHide/FeedReport
for any parseable integer -- nonexistent rows, private Pump Checks, strangers'
feed items and activities alike. They now resolve the target through the
canonical visibility authority of its type first, and nonexistent and
inaccessible targets share ONE refusal so the endpoint is not an existence
oracle. /feed/hide and /feed/unhide also carry the Feed write limit.
"""
import pytest

from app.config import FEED_REPORT_RATELIMIT, FEED_WRITE_RATELIMIT
from app.extensions import _user_or_ip_key, db, limiter
from app.i18n import t
from app.models import Activity, FeedHide, FeedItem, FeedReport, Friendship, PumpCheck
from app.services.feed import MILESTONE_ACTIVITY_TYPES, can_view_feed_activity

NOT_FOUND_ID = 999999999


# ── builders ─────────────────────────────────────────────────────────────────

def _befriend(a_id, b_id):
    db.session.add(Friendship(sender_id=a_id, receiver_id=b_id, status="accepted"))
    db.session.commit()


def _check(user_id, visibility="feed", shared=None, date_key="m1"):
    pc = PumpCheck(user_id=user_id, visibility=visibility, date_key=date_key, valid=True,
                   shared_friend_ids=shared)
    db.session.add(pc)
    db.session.commit()
    return pc


def _item(user_id, ref_id, mode="repost", body=None):
    fi = FeedItem(user_id=user_id, item_type=mode, ref_type="pump_check", ref_id=ref_id,
                  body=body)
    db.session.add(fi)
    db.session.commit()
    return fi


def _activity(user_id, activity_type="level_up"):
    act = Activity(user_id=user_id, activity_type=activity_type, content="milestone")
    db.session.add(act)
    db.session.commit()
    return act


def _hide(client, ttype, tid):
    return client.post("/feed/hide", json={"target_type": ttype, "target_id": tid})


def _report(client, ttype, tid, reason="spam", note=None):
    body = {"target_type": ttype, "target_id": tid, "reason": reason}
    if note is not None:
        body["note"] = note
    return client.post("/feed/report", json=body)


def _no_moderation_rows():
    return FeedHide.query.count() == 0 and FeedReport.query.count() == 0


def _refusal(res):
    return res.status_code, res.get_json()


def _generic_refusal():
    return 404, {"error": t("feed.not_found")}


# ── visible targets stay moderatable ─────────────────────────────────────────

def test_visible_pump_check_can_be_hidden(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    res = _hide(client, "pump_check", pc.id)
    assert res.status_code == 200 and res.get_json() == {"ok": True}
    assert FeedHide.query.filter_by(user_id=auth_user.id, target_type="pump_check",
                                    target_id=pc.id).count() == 1


def test_shared_friends_pump_check_can_be_hidden(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id, visibility="friends", shared=[auth_user.id])
    assert _hide(client, "pump_check", pc.id).status_code == 200
    assert FeedHide.query.count() == 1


def test_visible_repost_wrapper_can_be_hidden(app, auth_user, make_user, client):
    bob = make_user("bob")
    carol = make_user("carol")
    _befriend(auth_user.id, bob.id)
    _befriend(auth_user.id, carol.id)
    wrapper = _item(bob.id, _check(carol.id).id)
    assert _hide(client, "feed_item", wrapper.id).status_code == 200
    assert FeedHide.query.filter_by(target_type="feed_item", target_id=wrapper.id).count() == 1


def test_visible_quote_can_be_hidden_and_reported(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    quote = _item(bob.id, _check(bob.id).id, mode="quote", body="nice")
    assert _report(client, "feed_item", quote.id).status_code == 200
    assert FeedReport.query.filter_by(target_type="feed_item", target_id=quote.id).count() == 1


def test_unavailable_repost_wrapper_is_still_hideable(app, auth_user, make_user, client):
    """The feed renders a friend's repost of an original the viewer cannot see
    as an "unavailable" card whose menu targets the WRAPPER. The wrapper itself
    is visible, so hiding it keeps working -- current product meaning."""
    bob = make_user("bob")
    stranger = make_user("stranger")
    _befriend(auth_user.id, bob.id)
    wrapper = _item(bob.id, _check(stranger.id, visibility="private").id)
    items = client.get("/feed/data").get_json()["items"]
    assert [(i["kind"], i["id"], i["unavailable"]) for i in items] == [("repost", wrapper.id, True)]
    assert _hide(client, "feed_item", wrapper.id).status_code == 200
    assert client.get("/feed/data").get_json()["items"] == []


def test_visible_activity_can_be_hidden(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    act = _activity(bob.id)
    assert _hide(client, "activity", act.id).status_code == 200
    assert client.get("/feed/data").get_json()["items"] == []


# ── nonexistent targets: generic refusal, zero rows ──────────────────────────

@pytest.mark.parametrize("ttype", ["pump_check", "feed_item", "activity"])
def test_hide_nonexistent_target_is_refused_without_rows(app, auth_user, client, ttype):
    assert _refusal(_hide(client, ttype, NOT_FOUND_ID)) == _generic_refusal()
    assert _no_moderation_rows()


@pytest.mark.parametrize("ttype", ["pump_check", "feed_item", "activity"])
def test_report_nonexistent_target_is_refused_without_rows(app, auth_user, client, ttype):
    assert _refusal(_report(client, ttype, NOT_FOUND_ID, note="private words")) == _generic_refusal()
    assert _no_moderation_rows()


# ── existing but inaccessible targets: SAME refusal, zero rows ───────────────

def _invisible_targets(viewer_id, make_user):
    """Existing rows the viewer must not be able to moderate, one per rule."""
    bob = make_user("bob")        # friend
    eve = make_user("eve")        # stranger
    _befriend(viewer_id, bob.id)
    return [
        ("pump_check", _check(eve.id).id),                                 # stranger's feed post
        ("pump_check", _check(bob.id, visibility="private").id),           # friend's private post
        ("pump_check", _check(bob.id, visibility="friends", shared=[],
                              date_key="m2").id),                          # not shared with viewer
        ("feed_item", _item(eve.id, _check(eve.id, date_key="m2").id).id),  # stranger's repost
        ("feed_item", _item(eve.id, _check(bob.id, date_key="m3").id, mode="quote", body="x").id),
        ("activity", _activity(eve.id).id),                                # stranger's milestone
        ("activity", _activity(bob.id, activity_type="supplement").id),    # friend, never in feed
    ]


def test_hide_inaccessible_targets_match_nonexistent_refusal(app, auth_user, make_user, client):
    for ttype, tid in _invisible_targets(auth_user.id, make_user):
        assert _refusal(_hide(client, ttype, tid)) == _generic_refusal(), (ttype, tid)
    assert _no_moderation_rows()


def test_report_inaccessible_targets_match_nonexistent_refusal(app, auth_user, make_user, client):
    for ttype, tid in _invisible_targets(auth_user.id, make_user):
        assert _refusal(_report(client, ttype, tid)) == _generic_refusal(), (ttype, tid)
    assert _no_moderation_rows()


def test_refusal_body_carries_no_target_detail(app, auth_user, make_user, client):
    eve = make_user("eve")
    pc = _check(eve.id, visibility="private")
    hidden = _hide(client, "pump_check", pc.id)
    missing = _hide(client, "pump_check", NOT_FOUND_ID)
    assert hidden.get_data() == missing.get_data()
    assert set(hidden.get_json()) == {"error"}


def test_wrapper_id_cannot_launder_type_confusion(app, auth_user, make_user, client):
    """A visible feed_item id says nothing about a pump_check with the same
    number: each type resolves against its own table and authority."""
    bob = make_user("bob")
    eve = make_user("eve")
    _befriend(auth_user.id, bob.id)
    secret = _check(eve.id, visibility="private")
    wrapper = _item(bob.id, secret.id)   # fresh tables: both ids are 1
    if wrapper.id != secret.id:
        pytest.skip("id sequences did not align on this backend")
    assert _hide(client, "feed_item", wrapper.id).status_code == 200
    assert _refusal(_hide(client, "pump_check", secret.id)) == _generic_refusal()
    assert FeedHide.query.filter_by(target_type="pump_check").count() == 0


def test_stale_visibility_hide_stays_inert_and_unhide_still_clears(app, auth_user, make_user, client):
    """Visibility can change after a hide was written. The row is self-scoped
    and the feed never shows the (now invisible) target anyway, so the stale
    row is harmless; unhide still deletes it without re-checking visibility."""
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    assert _hide(client, "pump_check", pc.id).status_code == 200
    Friendship.query.delete()
    db.session.commit()
    assert _refusal(_hide(client, "pump_check", pc.id)) == _generic_refusal()
    assert client.post("/feed/unhide", json={"target_type": "pump_check",
                                             "target_id": pc.id}).status_code == 200
    assert FeedHide.query.count() == 0


# ── own targets ──────────────────────────────────────────────────────────────

def test_own_targets_keep_the_cannot_hide_own_contract(app, auth_user, client):
    pc = _check(auth_user.id)
    fi = _item(auth_user.id, pc.id, mode="quote", body="me")
    act = _activity(auth_user.id)
    for ttype, tid in (("pump_check", pc.id), ("feed_item", fi.id), ("activity", act.id)):
        res = _hide(client, ttype, tid)
        assert res.status_code == 400
        assert res.get_json() == {"error": t("feed.cannot_hide_own")}
    assert FeedHide.query.count() == 0


def test_own_report_contract_is_unchanged(app, auth_user, client):
    """Pre-F9 contract, preserved: the API accepts reporting one's own content
    (the UI never offers it). F9 only adds the visibility boundary, and the
    owner can always see their own content."""
    pc = _check(auth_user.id)
    assert _report(client, "pump_check", pc.id).status_code == 200
    assert FeedReport.query.count() == 1


# ── malformed requests ───────────────────────────────────────────────────────

MALFORMED = [
    {"target_type": "pump_check"},
    {"target_type": "pump_check", "target_id": None},
    {"target_type": "pump_check", "target_id": "abc"},
    {"target_type": "pump_check", "target_id": True},
    {"target_type": "pump_check", "target_id": 0},
    {"target_type": "pump_check", "target_id": -5},
    {"target_type": "pump_check", "target_id": 2 ** 31},
    {"target_type": "pump_check", "target_id": 10 ** 30},
    {"target_type": "bogus", "target_id": 1},
    {"target_id": 1},
]


@pytest.mark.parametrize("payload", MALFORMED)
@pytest.mark.parametrize("path", ["/feed/hide", "/feed/unhide", "/feed/report"])
def test_malformed_target_is_rejected_without_rows(app, auth_user, client, path, payload):
    res = client.post(path, json={**payload, "reason": "spam"})
    assert res.status_code == 400
    assert res.get_json() == {"error": t("feed.invalid_request")}
    assert _no_moderation_rows()


@pytest.mark.parametrize("path", ["/feed/hide", "/feed/report"])
def test_non_json_body_is_rejected(app, auth_user, client, path):
    res = client.post(path, data="target_type=pump_check&target_id=1",
                      content_type="application/x-www-form-urlencoded")
    assert res.status_code == 400
    assert _no_moderation_rows()


# ── idempotency / report parity ──────────────────────────────────────────────

def test_duplicate_valid_hide_keeps_one_row(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    act = _activity(bob.id)
    for _ in range(3):
        assert _hide(client, "activity", act.id).status_code == 200
    assert FeedHide.query.count() == 1


def test_valid_report_writes_exactly_one_report_and_one_hide(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    assert _report(client, "pump_check", pc.id, note="n" * 500).status_code == 200
    report = FeedReport.query.one()
    assert (report.user_id, report.target_type, report.target_id, report.reason) == (
        auth_user.id, "pump_check", pc.id, "spam")
    assert len(report.note) == 300
    hide = FeedHide.query.one()
    assert (hide.user_id, hide.target_type, hide.target_id) == (auth_user.id, "pump_check", pc.id)
    res = _report(client, "pump_check", pc.id)
    assert _refusal(res) == (400, {"error": t("feed.already_reported")})
    assert FeedReport.query.count() == 1 and FeedHide.query.count() == 1


def test_report_after_manual_hide_does_not_duplicate_hide(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    _hide(client, "pump_check", pc.id)
    assert _report(client, "pump_check", pc.id).status_code == 200
    assert FeedHide.query.count() == 1 and FeedReport.query.count() == 1


def test_invalid_reason_still_rejected_before_any_write(app, auth_user, make_user, client):
    bob = make_user("bob")
    _befriend(auth_user.id, bob.id)
    pc = _check(bob.id)
    assert _refusal(_report(client, "pump_check", pc.id, reason="bogus")) == (
        400, {"error": t("feed.invalid_request")})
    assert _no_moderation_rows()


def test_repeated_unhide_is_still_ok(app, auth_user, client):
    body = {"target_type": "pump_check", "target_id": NOT_FOUND_ID}
    assert client.post("/feed/unhide", json=body).status_code == 200
    assert client.post("/feed/unhide", json=body).status_code == 200


# ── activity authority parity with the feed ──────────────────────────────────

def test_activity_authority_matches_feed_rendering(app, auth_user, make_user, client):
    """can_view_feed_activity must admit exactly the Activity rows the feed
    query renders for this viewer -- one rule, two call sites."""
    bob = make_user("bob")
    eve = make_user("eve")
    _befriend(auth_user.id, bob.id)
    rows = []
    for owner in (auth_user, bob, eve):
        for kind in (*MILESTONE_ACTIVITY_TYPES, "supplement", "workout"):
            rows.append(_activity(owner.id, kind))
    rendered = {i["id"] for i in client.get("/feed/data?per_page=30").get_json()["items"]
                if i["kind"] == "milestone"}
    admitted = {a.id for a in rows if can_view_feed_activity(auth_user.id, a)}
    assert admitted == rendered
    assert len(admitted) == 2 * len(MILESTONE_ACTIVITY_TYPES)


# ── write throttling ─────────────────────────────────────────────────────────

def _route_limits(app, endpoint):
    name = app.view_functions[endpoint]
    key = f"{name.__module__}.{name.__name__}.{name.__name__}"
    return limiter.limit_manager._decorated_limits.get(key, [])


@pytest.mark.parametrize("endpoint", ["social.feed_hide", "social.feed_unhide"])
def test_hide_and_unhide_carry_the_feed_write_limit(app, endpoint):
    limits = _route_limits(app, endpoint)
    assert [(lim.limit_provider, lim.key_function) for lim in limits] == [
        (FEED_WRITE_RATELIMIT, _user_or_ip_key)]


def test_report_limit_is_unchanged(app):
    limits = _route_limits(app, "social.feed_report")
    assert [(lim.limit_provider, lim.key_function) for lim in limits] == [
        (FEED_REPORT_RATELIMIT, _user_or_ip_key)]


@pytest.fixture
def tiny_feed_write_limit(app):
    """Shrink hide/unhide to 2/hour without touching production defaults."""
    patched = []
    for endpoint in ("social.feed_hide", "social.feed_unhide"):
        for lim in _route_limits(app, endpoint):
            patched.append((lim, lim.limit_provider))
            lim.limit_provider = "2 per hour"
    # Wiring itself is asserted by test_hide_and_unhide_carry_the_feed_write_limit;
    # a missing decorator must surface here as a behavioural failure, not an error.
    limiter.reset()
    limiter.enabled = True
    try:
        yield
    finally:
        limiter.enabled = False
        limiter.reset()
        for lim, original in patched:
            lim.limit_provider = original


SAME_IP = {"REMOTE_ADDR": "203.0.113.77"}


def _visible_targets(viewer_id, make_user, n):
    bob = make_user("bob")
    _befriend(viewer_id, bob.id)
    return [_check(bob.id, date_key=f"d{i}").id for i in range(n)]


def test_hide_is_rate_limited_with_the_global_429_contract(app, auth_user, make_user, client,
                                                          tiny_feed_write_limit):
    ids = _visible_targets(auth_user.id, make_user, 3)
    post = lambda tid: client.post("/feed/hide", json={"target_type": "pump_check",
                                                       "target_id": tid}, environ_base=SAME_IP)
    assert post(ids[0]).status_code == 200
    assert post(ids[1]).status_code == 200
    blocked = post(ids[2])
    assert blocked.status_code == 429
    assert blocked.get_json() == {"error": t("error.rate_limited")}
    assert FeedHide.query.count() == 2


def test_unhide_is_rate_limited(app, auth_user, client, tiny_feed_write_limit):
    body = {"target_type": "pump_check", "target_id": NOT_FOUND_ID}
    assert client.post("/feed/unhide", json=body, environ_base=SAME_IP).status_code == 200
    assert client.post("/feed/unhide", json=body, environ_base=SAME_IP).status_code == 200
    blocked = client.post("/feed/unhide", json=body, environ_base=SAME_IP)
    assert blocked.status_code == 429
    assert blocked.get_json() == {"error": t("error.rate_limited")}


def test_hide_toggle_loop_is_bounded(app, auth_user, make_user, client, tiny_feed_write_limit):
    """hide/unhide can no longer be toggled indefinitely to churn writes."""
    tid = _visible_targets(auth_user.id, make_user, 1)[0]
    body = {"target_type": "pump_check", "target_id": tid}
    codes = []
    for _ in range(3):
        codes.append(client.post("/feed/hide", json=body, environ_base=SAME_IP).status_code)
        codes.append(client.post("/feed/unhide", json=body, environ_base=SAME_IP).status_code)
    assert codes == [200, 200, 200, 200, 429, 429]


def test_hide_bucket_is_per_user_not_per_ip(app, auth_user, make_user, client, login,
                                            tiny_feed_write_limit):
    """F6 preserved: two accounts behind one NAT do not share a bucket."""
    bob = make_user("bob")
    carol = make_user("carol")
    _befriend(auth_user.id, bob.id)
    _befriend(carol.id, bob.id)
    checks = [_check(bob.id, date_key=f"d{i}").id for i in range(4)]
    post = lambda tid: client.post("/feed/hide", json={"target_type": "pump_check",
                                                       "target_id": tid}, environ_base=SAME_IP)
    assert [post(checks[0]).status_code, post(checks[1]).status_code,
            post(checks[2]).status_code] == [200, 200, 429]
    login("carol")
    assert post(checks[3]).status_code == 200
    assert FeedHide.query.filter_by(user_id=carol.id).count() == 1


def test_hide_quota_does_not_consume_report_quota(app, auth_user, make_user, client,
                                                  tiny_feed_write_limit):
    ids = _visible_targets(auth_user.id, make_user, 3)
    hide = lambda tid: client.post("/feed/hide", json={"target_type": "pump_check",
                                                       "target_id": tid}, environ_base=SAME_IP)
    assert [hide(ids[0]).status_code, hide(ids[1]).status_code,
            hide(ids[2]).status_code] == [200, 200, 429]
    res = client.post("/feed/report", json={"target_type": "pump_check", "target_id": ids[2],
                                            "reason": "spam"}, environ_base=SAME_IP)
    assert res.status_code == 200
    assert FeedReport.query.count() == 1
