"""Product analytics v2: PostHog EU transport, worker pass, retention (synthetic only).

No test reaches a vendor. The HTTPS transport is exercised through an
in-process requests adapter (exact URL/method/headers/timeouts asserted) and,
for real-socket timeout behaviour, through a test-only adapter that rewrites
the allowlisted destination to a loopback synthetic endpoint. All tokens are
fake.
"""
import io
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import requests
from requests.adapters import HTTPAdapter

from app.extensions import db
from app.models import ProductAnalyticsConsent, ProductAnalyticsErasure, ProductAnalyticsOutbox
from app.services.product_analytics import (FLAG, consent, dispatch, pipeline, posthog_transport,
                                            retention, settings)
from app.services.product_analytics.catalog import NOTICE_VERSION
from app.services.product_analytics.transport import SyntheticTransport, TransportFailure

TOKEN = "phc_" + "F" * 40          # fake, format-valid only
PERSONAL = "phx_" + "S" * 48       # fake, format-valid only
FULL = {settings.REGION: "eu", settings.PROJECT_TOKEN: TOKEN, settings.PROJECT_ID: "4242",
        settings.PERSONAL_API_KEY: PERSONAL}


class FakeVendor(HTTPAdapter):
    """In-process HTTPS endpoint: records every request, answers from a script."""

    def __init__(self, responder=None):
        super().__init__()
        self.calls = []
        self.responder = responder or (lambda request: (200, b'{"status":"Ok"}'))

    def send(self, request, **kwargs):
        self.calls.append(SimpleNamespace(method=request.method, url=request.url,
                                          headers=dict(request.headers), body=request.body,
                                          kwargs=kwargs))
        status, body = self.responder(request)
        response = requests.Response()
        response.status_code, response.raw = status, io.BytesIO(body)
        response.url, response.request = request.url, request
        return response


def _transport(responder=None, config=FULL):
    session = posthog_transport._new_session()
    vendor = FakeVendor(responder)
    session.mount("https://", vendor)
    return posthog_transport.build(settings.vendor_settings(config), session=session), vendor


# ── Settings: fail closed, exact region, bounded values ──────────────────

@pytest.mark.parametrize("override", [
    {settings.REGION: None}, {settings.REGION: "us"}, {settings.REGION: "EU "[:2]},
    {settings.PROJECT_TOKEN: None}, {settings.PROJECT_TOKEN: "phx_" + "F" * 40},
    {settings.PROJECT_TOKEN: "phc_short"}, {settings.CONNECT_TIMEOUT: "30"},
    {settings.READ_TIMEOUT: "0"}, {settings.READ_TIMEOUT: "fast"},
])
def test_incomplete_or_invalid_vendor_configuration_builds_no_transport(override):
    config = {**FULL, **override}
    config = {k: v for k, v in config.items() if v is not None}
    assert settings.vendor_settings(config) is None
    assert pipeline.configured_transport(config) is None


def test_erasure_needs_its_own_complete_credentials_and_repr_hides_secrets():
    # Review B-1: nothing is captured that could not be erased.
    for broken in ({settings.PERSONAL_API_KEY: "bad"}, {settings.PERSONAL_API_KEY: ""},
                   {settings.PROJECT_ID: "x1"}):
        assert settings.vendor_settings({**FULL, **broken}) is None
    full = settings.vendor_settings(FULL)
    assert full.can_erase and full.connect_timeout == 3.0 and full.read_timeout == 10.0
    for value in (repr(full), str(full), repr(posthog_transport.build(full))):
        assert TOKEN not in value and PERSONAL not in value and "4242" not in value


def test_retention_policy_has_no_production_default_and_keeps_the_hard_ceiling():
    empty = settings.retention_policy({})
    assert empty.outbox_max_age == timedelta(hours=48)
    assert empty.receipt_retention is None and empty.erasure_overdue is None
    assert settings.retention_policy({settings.OUTBOX_MAX_AGE: "72"}).outbox_max_age \
        == timedelta(hours=48)                         # out of bounds -> ceiling
    custom = settings.retention_policy({settings.OUTBOX_MAX_AGE: "6",
                                        settings.RECEIPT_RETENTION: "90",
                                        settings.ERASURE_OVERDUE: "30"})
    assert (custom.outbox_max_age, custom.receipt_retention, custom.erasure_overdue) == (
        timedelta(hours=6), timedelta(days=90), timedelta(days=30))


# ── HTTPS transport: exact destinations and request discipline ───────────

def test_capture_posts_only_to_the_eu_batch_endpoint_with_bounded_timeouts():
    transport, vendor = _transport()
    transport.send(b'{"api_key":"x","batch":[]}')
    (call,) = vendor.calls
    assert (call.method, call.url) == ("POST", "https://eu.i.posthog.com/batch/")
    assert call.kwargs["timeout"] == (3.0, 10.0) and call.kwargs["verify"] is True
    assert "Authorization" not in call.headers and "Cookie" not in call.headers
    assert call.headers["User-Agent"] == "axisai-product-analytics/2"


@pytest.mark.parametrize("status,outcome", [
    (429, "vendor_retry"), (503, "vendor_retry"), (500, "vendor_retry"),
    (400, "vendor_rejected"), (401, "vendor_rejected"), (413, "vendor_rejected"),
    (302, "redirect_refused"), (204, "unexpected_status"),
])
def test_non_success_is_a_transport_failure_with_a_fixed_code(status, outcome):
    transport, vendor = _transport(lambda request: (status, b"secret-echo " + TOKEN.encode()))
    with pytest.raises(TransportFailure) as raised:
        transport.send(b"{}")
    assert raised.value.outcome == outcome and str(raised.value) == outcome
    assert len(vendor.calls) == 1                       # no transport-level retry


def test_redirects_are_never_followed_and_plain_http_is_refused():
    transport, vendor = _transport(lambda request: (307, b""))
    with pytest.raises(TransportFailure):
        transport.send(b"{}")
    assert [c.url for c in vendor.calls] == ["https://eu.i.posthog.com/batch/"]
    with pytest.raises(TransportFailure) as raised:
        transport._request("POST", "http://eu.i.posthog.com/batch/")
    assert raised.value.outcome == "destination_refused"
    for url in ("https://us.i.posthog.com/batch/", "https://eu.i.posthog.com/capture/",
                "https://eu.posthog.com/api/projects/4243/persons/"):
        with pytest.raises(TransportFailure) as raised:
            transport._request("POST", url)
        assert raised.value.outcome == "destination_refused"
    assert len(vendor.calls) == 1


def test_oversized_and_malformed_vendor_responses_are_not_success():
    transport, _ = _transport(lambda request: (200, b"x" * (posthog_transport.MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(TransportFailure) as raised:
        transport.send(b"{}")
    assert raised.value.outcome == "response_too_large"
    transport, _ = _transport(lambda request: (202, b"not json"))
    with pytest.raises(TransportFailure):
        transport.bulk_delete(["s1"])
    transport, _ = _transport(lambda request: (202, b'{"persons_found": -1}'))
    with pytest.raises(TransportFailure):
        transport.bulk_delete(["s1"])


def test_erasure_calls_use_the_private_eu_api_with_the_personal_key_only():
    subject = "8a1b7f0e-5d55-4d5d-8b55-1f0e6c9d0a11"

    def respond(request):
        if request.url.startswith("https://eu.posthog.com/api/projects/4242/persons/?"):
            return 200, json.dumps({"results": [{"id": "person-1", "distinct_ids": [subject]}]}).encode()
        if request.url.endswith("/bulk_delete/"):
            return 202, b'{"persons_found":1,"persons_queued_for_deletion":1,"deletion_errors":[]}'
        return 200, json.dumps({"results": [{"person_uuid": "person-1",
                                             "delete_verified_at": "2026-11-29T00:00:00Z"}]}).encode()

    transport, vendor = _transport(respond)
    assert transport.find_person(subject) == "person-1"
    assert transport.bulk_delete([subject])["persons_queued_for_deletion"] == 1
    assert transport.deletion_verified("person-1") is True
    lookup, delete, verify = vendor.calls
    assert lookup.method == "GET" and lookup.url == (
        f"https://eu.posthog.com/api/projects/4242/persons/?distinct_id={subject}")
    assert delete.method == "POST" and delete.url == (
        "https://eu.posthog.com/api/projects/4242/persons/bulk_delete/")
    assert json.loads(delete.body) == {"distinct_ids": [subject], "delete_events": True}
    assert verify.url.startswith("https://eu.posthog.com/api/projects/4242/persons/deletion_status/?")
    for call in vendor.calls:
        assert call.headers["Authorization"] == f"Bearer {PERSONAL}"
        assert TOKEN not in json.dumps(call.headers)


def test_unverified_or_foreign_deletion_status_is_never_success():
    for body in ({"results": [{"person_uuid": "person-1", "delete_verified_at": None}]},
                 {"results": [{"person_uuid": "other", "delete_verified_at": "2026-11-29"}]},
                 {"results": []}):
        transport, _ = _transport(lambda request, body=body: (200, json.dumps(body).encode()))
        assert transport.deletion_verified("person-1") is False


def test_a_transport_without_erasure_credentials_cannot_call_the_private_api():
    session = posthog_transport._new_session()
    vendor = FakeVendor()
    session.mount("https://", vendor)
    transport = posthog_transport.PostHogTransport(
        settings.VendorSettings(TOKEN, None, None, 3.0, 10.0), session=session)
    assert not transport.can_erase
    for call in (lambda: transport.find_person("s"), lambda: transport.bulk_delete(["s"]),
                 lambda: transport.deletion_verified("r")):
        with pytest.raises(TransportFailure):
            call()
    assert vendor.calls == []


class _Slow(BaseHTTPRequestHandler):
    delay = 5.0

    def do_POST(self):
        time.sleep(self.delay)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class LoopbackRewrite(HTTPAdapter):
    """TEST ONLY: deliver the allowlisted request to a loopback synthetic endpoint."""

    def __init__(self, port):
        super().__init__(max_retries=0)
        self.port = port

    def send(self, request, **kwargs):
        request.url = request.url.replace("https://eu.i.posthog.com", f"http://127.0.0.1:{self.port}")
        return super().send(request, **kwargs)


def test_a_slow_synthetic_endpoint_is_cut_off_by_the_read_timeout():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = {**FULL, settings.CONNECT_TIMEOUT: "1", settings.READ_TIMEOUT: "1"}
        session = posthog_transport._new_session()
        session.mount("https://", LoopbackRewrite(server.server_address[1]))
        transport = posthog_transport.build(settings.vendor_settings(config), session=session)
        started = time.monotonic()
        with pytest.raises(TransportFailure) as raised:
            transport.send(b"{}")
        assert raised.value.outcome == "timeout"
        assert time.monotonic() - started < 3.0
    finally:
        server.shutdown()


# ── Worker pass: fail closed, flag/erasure independence, no data in logs ─

@pytest.fixture
def on(app):
    app.config[FLAG] = True
    yield app
    app.config[FLAG] = False


def _granted(user_id, *, exposed=False):
    consent.grant(user_id, NOTICE_VERSION)
    if exposed:
        row = consent.current(user_id)
        row.vendor_exposed_at = datetime.utcnow()
        db.session.commit()
    return consent.current(user_id)


def test_missing_vendor_configuration_makes_no_vendor_call_but_cleans_up(on, make_user, app):
    user = make_user("analytics-unconfigured")
    _granted(user.id)
    stale = ProductAnalyticsOutbox.query.one()
    stale.occurred_at = datetime.utcnow() - timedelta(hours=49)
    db.session.commit()
    stats = pipeline.run_pass(config={})
    assert stats["vendor"] == "unconfigured" and stats["outbox_expired"] == 1
    assert "sent" not in stats and "erasure_verified" not in stats


def test_flag_off_still_advances_owed_erasure_but_never_dispatches(app, make_user):
    user = make_user("analytics-off-erasure")
    app.config[FLAG] = True
    _granted(user.id, exposed=True)
    consent.withdraw(user.id)
    _granted(user.id)
    app.config[FLAG] = False
    transport = SyntheticTransport()
    stats = pipeline.run_pass(transport=transport, config={})
    assert "sent" not in stats and stats["erasure_materialized"] == 1
    assert ProductAnalyticsOutbox.query.count() == 1      # new grant's event: not dispatched
    assert all(item["event"] == "$set" for body in transport.sent
               for item in json.loads(body)["batch"])


def test_pass_logs_counts_only(on, make_user, caplog):
    user = make_user("analytics-logging")
    row = _granted(user.id, exposed=True)
    subject, grant_id = row.subject, row.grant_id
    transport, vendor = _transport(lambda request: (503, b"echo " + TOKEN.encode()))
    with caplog.at_level(logging.DEBUG):
        stats = pipeline.run_pass(transport=transport, config=FULL)
        consent.withdraw(user.id)
        pipeline.run_pass(transport=transport, config=FULL)
    assert stats["retry"] == 1 and vendor.calls
    text = caplog.text + json.dumps(stats)
    for secret in (TOKEN, PERSONAL, subject, grant_id, "4242", user.username):
        assert secret not in text
    assert "product_analytics event=pass" in caplog.text


# ── Retention cleanup (synthetic values) ─────────────────────────────────

def test_cleanup_expires_outbox_rows_by_configured_age_and_purges_only_terminal_receipts(on, make_user):
    user = make_user("analytics-retention")
    _granted(user.id)
    now = datetime.utcnow()
    row = ProductAnalyticsOutbox.query.one()
    row.occurred_at = now - timedelta(hours=7)
    old = now - timedelta(days=91)
    db.session.add_all([
        ProductAnalyticsErasure(subject="s-verified", reason="withdrawn", status="verified",
                                attempts=0, requested_at=old, next_attempt_at=old, completed_at=old),
        ProductAnalyticsErasure(subject="s-never", reason="withdrawn", status="not_required",
                                attempts=0, requested_at=old, next_attempt_at=old, completed_at=old),
        ProductAnalyticsErasure(subject="s-open", reason="withdrawn", status="requested",
                                attempts=0, requested_at=old, next_attempt_at=old),
        ProductAnalyticsErasure(subject="s-recent", reason="withdrawn", status="verified",
                                attempts=0, requested_at=now, next_attempt_at=now, completed_at=now),
    ])
    db.session.commit()
    assert retention.cleanup(settings.retention_policy({}), now=now) == {
        "outbox_expired": 0, "receipts_purged": 0, "subjects_rotated": 0}
    policy = settings.retention_policy({settings.OUTBOX_MAX_AGE: "6", settings.RECEIPT_RETENTION: "90"})
    assert retention.cleanup(policy, now=now) == {
        "outbox_expired": 1, "receipts_purged": 2, "subjects_rotated": 0}
    assert {j.subject for j in ProductAnalyticsErasure.query.all()} == {"s-open", "s-recent"}


def test_erasure_is_never_terminal_failed_and_overdue_jobs_are_counted(on, make_user):
    user = make_user("analytics-overdue")
    _granted(user.id, exposed=True)
    consent.withdraw(user.id)
    failing = SyntheticTransport(fail=True)
    now = datetime.utcnow()
    for day in range(40):
        stats = dispatch.process_erasures_once(failing, now=now + timedelta(days=day),
                                               overdue=timedelta(days=30))
    job = ProductAnalyticsErasure.query.one()
    assert job.status == "pending" and job.attempts == 40 and job.last_outcome == "synthetic_failure"
    assert stats["overdue"] == 1
    assert job.next_attempt_at - (now + timedelta(days=39)) <= dispatch.ERASURE_MAX_BACKOFF


def test_profile_not_yet_ingested_waits_and_a_partial_delete_is_retried(on, make_user):
    user = make_user("analytics-ingest-lag")
    _granted(user.id, exposed=True)
    consent.withdraw(user.id)
    transport = SyntheticTransport(ingest_profiles=False)
    now = datetime.utcnow()
    dispatch.process_erasures_once(transport, now=now)
    later = now + dispatch.ERASURE_SETTLE
    assert dispatch.process_erasures_once(transport, now=later)["waiting"] == 1
    job = ProductAnalyticsErasure.query.one()
    assert job.status == "materialized" and job.last_outcome == "profile_not_ingested"
    assert transport.deleted == []


def test_erasure_pass_budget_stops_vendor_calls(on, make_user):
    for index in range(3):
        user = make_user(f"analytics-budget-{index}")
        _granted(user.id, exposed=True)
        consent.withdraw(user.id)
    transport = SyntheticTransport()
    now = datetime.utcnow()
    dispatch.process_erasures_once(transport, now=now)
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])
    stats = dispatch.process_erasures_once(transport, now=now + dispatch.ERASURE_SETTLE,
                                           budget=1.0, clock=lambda: next(ticks))
    assert stats["requested"] == 0 and transport.deleted == []


# ── Worker scheduling ─────────────────────────────────────────────────────

def test_worker_tick_enqueues_nothing_by_default_and_one_unique_job_when_wanted(monkeypatch):
    import worker
    from app import jobs
    enqueued = []

    class Queue:
        def enqueue_call(self, func, **kwargs):
            enqueued.append((func.__name__, kwargs))
            if len(enqueued) > 1:
                from rq.exceptions import DuplicateJobError
                raise DuplicateJobError("duplicate")

    monkeypatch.setattr(jobs, "get_queue", lambda: Queue())
    for key in ("FITX_PRODUCT_ANALYTICS_ENABLED", "PRODUCT_ANALYTICS_POSTHOG_PERSONAL_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert worker._product_analytics_tick(-1000.0, 0.0) == 0.0 and enqueued == []
    monkeypatch.setenv("FITX_PRODUCT_ANALYTICS_ENABLED", "true")    # invalid -> not wanted
    worker._product_analytics_tick(-1000.0, 0.0)
    assert enqueued == []
    monkeypatch.setenv("FITX_PRODUCT_ANALYTICS_ENABLED", "1")
    worker._product_analytics_tick(-1000.0, 0.0)
    worker._product_analytics_tick(-1000.0, 0.0)                    # duplicate wakeup
    assert [name for name, _ in enqueued] == ["run_product_analytics_pass"] * 2
    assert {kw["job_id"] for _, kw in enqueued} == {"axisai-product-analytics-pass-v2"}
    assert all(kw["unique"] is True and "args" not in kw and "kwargs" not in kw
               for _, kw in enqueued)
    monkeypatch.delenv("FITX_PRODUCT_ANALYTICS_ENABLED")
    monkeypatch.setenv("PRODUCT_ANALYTICS_POSTHOG_PERSONAL_API_KEY", PERSONAL)
    assert worker._product_analytics_wanted() is True


def test_subject_rotation_bounds_vendor_retention_without_touching_the_grant(on, make_user):
    user = make_user("analytics-rotation")
    _granted(user.id, exposed=True)
    row = consent.current(user.id)
    old_subject, grant_id, generation = row.subject, row.grant_id, row.generation
    issued = row.subject_issued_at
    policy = settings.retention_policy({settings.SUBJECT_MAX_AGE: "90"})
    assert retention.cleanup(policy, now=issued + timedelta(days=89))["subjects_rotated"] == 0
    rotate_at = issued + timedelta(days=91)
    fresh = ProductAnalyticsOutbox(
        user_id=user.id, generation=generation, subject=old_subject, event_id=str(uuid.uuid4()),
        name="axis_v2_app_opened", authority="client", occurred_at=rotate_at,
        received_at=rotate_at, properties="{}", attempts=0, next_attempt_at=rotate_at)
    db.session.add(fresh)
    db.session.commit()
    stats = retention.cleanup(policy, now=rotate_at)
    assert stats["subjects_rotated"] == 1
    db.session.expire_all()
    row = consent.current(user.id)
    assert row.subject != old_subject and row.grant_id == grant_id
    assert row.generation == generation + 1 and row.vendor_exposed_at is None
    job = ProductAnalyticsErasure.query.one()
    assert (job.subject, job.reason, job.status) == (old_subject, "rotated", "pending")
    assert ProductAnalyticsOutbox.query.count() == 0          # never re-bound
    assert consent.view(row)["grant_id"] == grant_id           # client unaffected
    assert settings.retention_policy({settings.SUBJECT_MAX_AGE: "3"}).subject_max_age is None


def test_rotation_of_a_never_exposed_subject_needs_no_vendor_call(on, make_user):
    user = make_user("analytics-rotation-unexposed")
    row = _granted(user.id)
    policy = settings.retention_policy({settings.SUBJECT_MAX_AGE: "30"})
    retention.cleanup(policy, now=row.subject_issued_at + timedelta(days=31))
    assert ProductAnalyticsErasure.query.one().status == "not_required"


def test_a_partial_or_errored_bulk_delete_is_never_accepted(on, make_user):
    class Partial(SyntheticTransport):
        def __init__(self, result):
            super().__init__()
            self.result = result

        def bulk_delete(self, subjects):
            super().bulk_delete(subjects)
            return dict(self.result)

    for result in ({"persons_found": 1, "persons_queued_for_deletion": 0, "persons_deleted": 0,
                    "deletion_errors": []},
                   {"persons_found": 1, "persons_queued_for_deletion": 1, "persons_deleted": 0,
                    "deletion_errors": ["synthetic"]}):
        ProductAnalyticsErasure.query.delete()
        db.session.commit()
        user = make_user(f"analytics-partial-{len(result['deletion_errors'])}")
        _granted(user.id, exposed=True)
        consent.withdraw(user.id)
        transport = Partial(result)
        now = datetime.utcnow()
        dispatch.process_erasures_once(transport, now=now)
        stats = dispatch.process_erasures_once(transport, now=now + dispatch.ERASURE_SETTLE)
        job = ProductAnalyticsErasure.query.one()
        assert stats["requested"] == 0 and job.status == "materialized"
        assert job.last_outcome == "deletion_incomplete" and transport.deleted


def test_a_subject_minted_after_the_exposure_marker_is_never_sent_unmarked(on, make_user, monkeypatch):
    user = make_user("analytics-regrant-before-send")
    first = _granted(user.id).subject
    original = dispatch._mark_exposure

    def mark_then_churn(owners, now):
        original(owners, now)
        consent.withdraw(user.id)          # first subject: exposed -> pending erasure
        consent.grant(user.id, NOTICE_VERSION)

    monkeypatch.setattr(dispatch, "_mark_exposure", mark_then_churn)
    transport = SyntheticTransport()
    later = datetime.utcnow() + timedelta(seconds=5)     # the regrant's row is due too
    stats = dispatch.dispatch_once(transport, now=later)
    assert stats["sent"] == 0 and stats["skipped"] == 1 and transport.sent == []
    monkeypatch.setattr(dispatch, "_mark_exposure", original)
    assert dispatch.dispatch_once(transport, now=later)["sent"] == 1
    second = consent.current(user.id)
    assert second.vendor_exposed_at is not None and second.subject != first
    assert ProductAnalyticsErasure.query.one().status == "pending"


def test_dispatch_never_runs_on_a_transport_that_cannot_erase(on, make_user):
    user = make_user("analytics-no-erasure")
    _granted(user.id)
    transport = SyntheticTransport()
    transport.can_erase = False
    stats = pipeline.run_pass(transport=transport, config={})
    assert "sent" not in stats and transport.sent == []


def test_dispatch_loop_respects_its_wall_clock_budget(on, make_user, monkeypatch):
    user = make_user("analytics-dispatch-budget")
    _granted(user.id)
    calls = []
    monkeypatch.setattr(dispatch, "dispatch_once",
                        lambda *a, **k: calls.append(1) or {"sent": 1, "fenced": 0, "retry": 0,
                                                            "dropped": 0, "skipped": 0})
    ticks = iter([0.0] + [0.0, 0.0, pipeline.DISPATCH_BUDGET_SECONDS + 1] + [999.0] * 30)
    pipeline.run_pass(transport=SyntheticTransport(), config={}, clock=lambda: next(ticks))
    assert len(calls) == 2


def test_a_subject_whose_rows_are_all_fenced_is_never_marked_exposed(on, make_user):
    user = make_user("analytics-fenced-only")
    _granted(user.id)
    row = consent.current(user.id)
    ProductAnalyticsOutbox.query.delete()
    now = datetime.utcnow()
    db.session.add(ProductAnalyticsOutbox(
        user_id=user.id, generation=row.generation - 1, subject=row.subject,
        event_id=str(uuid.uuid4()), name="axis_v2_app_opened", authority="client",
        occurred_at=now, received_at=now, properties="{}", attempts=0, next_attempt_at=now))
    db.session.commit()
    stats = dispatch.dispatch_once(SyntheticTransport())
    assert stats["fenced"] == 1 and consent.current(user.id).vendor_exposed_at is None
    consent.withdraw(user.id)
    assert ProductAnalyticsErasure.query.one().status == "not_required"


def test_a_marker_that_is_never_ingested_is_sent_again(on, make_user):
    user = make_user("analytics-rematerialize")
    _granted(user.id, exposed=True)
    consent.withdraw(user.id)
    transport = SyntheticTransport(ingest_profiles=False)
    now = datetime.utcnow()
    dispatch.process_erasures_once(transport, now=now)
    markers = len(transport.sent)
    for day in range(1, dispatch.REMATERIALIZE_AFTER + 2):
        dispatch.process_erasures_once(transport, now=now + timedelta(days=day))
    assert len(transport.sent) > markers
    assert ProductAnalyticsErasure.query.one().status in ("pending", "materialized")


def test_person_reference_prefers_the_uuid_field():
    subject = "s-1"
    transport, _ = _transport(lambda request: (200, json.dumps({"results": [
        {"id": 12345, "uuid": "0190-person", "distinct_ids": [subject]}]}).encode()))
    assert transport.find_person(subject) == "0190-person"
