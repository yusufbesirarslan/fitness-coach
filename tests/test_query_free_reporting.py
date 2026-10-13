"""PRIV-LOG-01: query strings never leave the app in error-reporting envelopes.

The Sentry WSGI integration copies the raw QUERY_STRING onto every
request-scoped event even with send_default_pii=False, and the stdlib
integration records outgoing HTTP breadcrumbs and spans with ``http.query``.
A failed food search would otherwise ship the user's search words twice: as
``request.query_string`` (``q=``) and in the FatSecret breadcrumb
(``search_expression=``). These tests build complete envelopes from the
production SDK configuration, offline, with synthetic canaries only.
"""
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import quote

import pytest
import sentry_sdk

from app.observability import assign_request_id
from test_menu_reporting_privacy import hermetic_reporting_env, reporting  # noqa: F401

SEARCH = "PRIVLOG01_search_canary_tavuk göğsü"
BARCODE = "8690000000017"
REFERER_QUERY = "PRIVLOG01_referer_canary"


@pytest.fixture
def loopback_provider():
    """A local stand-in for the FatSecret REST endpoint, so the SDK records a
    genuine httplib breadcrumb for a request shaped like the real one."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _provider_call(port, params):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", "/fatsecret/rest/server.api?" + params)
        conn.getresponse().read()
    finally:
        conn.close()


def _events(envelopes, client):
    client.flush()
    events = []
    for raw in envelopes:
        lines = raw.splitlines()
        for index in range(1, len(lines), 2):
            header = json.loads(lines[index])
            if header["type"] in ("event", "transaction"):
                events.append(json.loads(lines[index + 1]))
    return events, b"\n".join(envelopes).decode()


def _assert_absent(serialized, *values):
    for value in values:
        for representation in (value, json.dumps(value)[1:-1], quote(value), quote(value, safe=""),
                               quote(value).replace("%20", "+")):
            assert representation not in serialized, representation


def _authenticate(monkeypatch, make_user):
    from app.services import mobile_auth
    owner = make_user("privlog-owner")
    monkeypatch.setattr(mobile_auth, "authenticate_access", lambda raw:
        mobile_auth.MobilePrincipal(owner, SimpleNamespace(id=1), {"sub": owner.cognito_sub}))


@pytest.mark.parametrize("route, params, provider_params, secret", [
    ("/api/v1/nutrition/foods/search", "q=" + quote(SEARCH),
     "method=foods.search&search_expression=" + quote(SEARCH) + "&format=json", SEARCH),
    ("/api/v1/nutrition/foods/barcode", "code=" + BARCODE,
     "method=food.find_id_for_barcode&barcode=" + BARCODE + "&format=json", BARCODE),
])
def test_food_lookup_failure_envelope_has_no_query_text(
        app, raw_client, make_user, monkeypatch, reporting, loopback_provider,  # noqa: F811
        route, params, provider_params, secret):
    from app.services import mobile_food_discovery
    _authenticate(monkeypatch, make_user)

    def failing_provider(*args, **kwargs):
        _provider_call(loopback_provider, provider_params)
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(mobile_food_discovery, "search", failing_provider)
    monkeypatch.setattr(mobile_food_discovery, "barcode_lookup", failing_provider)
    response = raw_client.get(route + "?" + params, headers={
        "Authorization": "Bearer synthetic-token",
        "Referer": "https://public.example/page?" + REFERER_QUERY})
    assert response.status_code == 503

    events, serialized = _events(*reporting)
    errors = [e for e in events if e.get("level") == "error"]
    assert errors, "no outgoing error event: the privacy check would be vacuous"
    event = errors[-1]
    # Diagnostics survive: method, query-free URL, request id, the logged
    # failure line and the provider breadcrumb with method and status.
    assert event["request"]["method"] == "GET"
    assert event["request"]["url"].endswith(route)
    assert "query_string" not in event["request"]
    assert len(event["tags"]["request_id"]) == 16
    assert "food_discovery_failed" in serialized
    crumbs = [c for c in event["breadcrumbs"]["values"] if c.get("category") == "httplib"]
    assert crumbs and crumbs[-1]["data"]["http.method"] == "GET"
    assert crumbs[-1]["data"]["http.response.status_code"] == 502
    assert crumbs[-1]["data"]["url"].endswith("/fatsecret/rest/server.api")
    assert "http.query" not in crumbs[-1]["data"]
    _assert_absent(serialized, secret, REFERER_QUERY, "search_expression", "barcode=" + BARCODE)


def test_transaction_spans_and_request_have_no_query_text(app, reporting, loopback_provider):  # noqa: F811
    with app.test_request_context("/api/v1/nutrition/foods/search?q=" + quote(SEARCH)):
        assign_request_id()
        with sentry_sdk.start_transaction(name="/api/v1/nutrition/foods/search"):
            _provider_call(loopback_provider, "search_expression=" + quote(SEARCH))
    events, serialized = _events(*reporting)
    transactions = [e for e in events if e.get("type") == "transaction"]
    assert transactions, "no transaction envelope: the privacy check would be vacuous"
    spans = [s for s in transactions[-1].get("spans", []) if s.get("op") == "http.client"]
    assert spans and spans[-1]["data"]["url"].endswith("/fatsecret/rest/server.api")
    _assert_absent(serialized, SEARCH, "search_expression")


@pytest.mark.parametrize('headers', [
    {'rEfErEr': 'https://example.invalid/page?q=SYNTHETIC_REFERER#SYNTHETIC_FRAGMENT', 'X-Diagnostic': 'safe'},
    [['Referer', 'https://example.invalid/page?q=SYNTHETIC_REFERER'], ['X-Diagnostic', 'safe']],
    (('REFERER', 'https://example.invalid/page#SYNTHETIC_FRAGMENT'), ('X-Diagnostic', 'safe')),
    [('referer', 'https://example.invalid/page?q=SYNTHETIC_REFERER'), ['X-Diagnostic', 'safe']],
    [['Referer', 'https://example.invalid/page?q=SYNTHETIC_REFERER'], ['REFERER', 'https://example.invalid/page#SYNTHETIC_FRAGMENT'], ['X-Diagnostic', 'safe']],
])
def test_pair_list_referer_canary_absent_from_actual_sdk_envelope(app, reporting, headers):  # noqa: F811
    sentry_sdk.capture_event({'message': 'synthetic failure', 'request': {
        'url': 'https://example.invalid/page', 'method': 'GET', 'headers': headers}})
    events, serialized = _events(*reporting)
    assert events and events[-1]['message'] == 'synthetic failure'
    safe = events[-1]['request']['headers']
    pairs = safe.items() if isinstance(safe, dict) else safe
    assert ('X-Diagnostic', 'safe') in [tuple(p) for p in pairs]
    pairs = safe.items() if isinstance(safe, dict) else safe
    assert all(value == 'https://example.invalid/page' for name, value in pairs if name.lower() == 'referer')
    _assert_absent(serialized, 'SYNTHETIC_REFERER', 'SYNTHETIC_FRAGMENT')


@pytest.mark.parametrize('headers', [
    'SYNTHETIC_REFERER', {'Referer': ['SYNTHETIC_REFERER']},
    [['Referer', {'url': 'SYNTHETIC_REFERER'}], ['X-Diagnostic', 'safe']],
    [['Referer', 'SYNTHETIC_REFERER', 'extra'], [123, 'SYNTHETIC_REFERER']],
    {'Referer ': 'SYNTHETIC_REFERER'}, [None, 'SYNTHETIC_REFERER'],
])
def test_malformed_header_container_fails_safely_in_envelope(app, reporting, headers):  # noqa: F811
    sentry_sdk.capture_event({'message': 'synthetic malformed headers', 'request': {'headers': headers}})
    events, serialized = _events(*reporting)
    assert events, 'the sanitizer must preserve the safe error event'
    _assert_absent(serialized, 'SYNTHETIC_REFERER')
