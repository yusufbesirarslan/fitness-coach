"""Offline public-menu security contracts, including preserved web HTTP intake."""
import base64
import io
import json
import socket
import time
from urllib.parse import urlparse

import pytest
import requests

from app.services import menu_fetch as mf, menu_remote as mr
from app.services.menu_parse import bounded_soup, bounded_json, bound_sections

PUBLIC = "93.184.216.34"


@pytest.fixture
def wire(monkeypatch):
    """Real Requests preparation, deterministic adapter/raw response; no network."""
    seen = []
    responses = []
    monkeypatch.setattr(mr, "resolve", lambda host: [PUBLIC])

    def send(adapter, request, **kwargs):
        seen.append((request, kwargs))
        status, headers, body = responses.pop(0) if responses else (200, {"Content-Type": "text/html"}, b"<p>Adana Kebap Mercimek Corba Izgara Tavuk</p>")
        resp = requests.Response()
        resp.status_code = status
        resp.headers.update(headers)
        resp.request = request

        class Raw(io.BytesIO):
            def read1(self, size, decode_content=False):
                assert decode_content is False
                return self.read(size)
        resp.raw = Raw(body)
        from email.message import Message
        from types import SimpleNamespace
        message = Message()
        for key, value in headers.items():
            message[key] = value
        resp.raw._original_response = SimpleNamespace(msg=message)
        return resp

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("real network attempted"))
    monkeypatch.setattr(mr, "run_worker", lambda payload, seconds: mr.retrieve(**payload))
    return seen, responses


@pytest.mark.parametrize("url", ["http://restoran.example:80/menu", "https://restoran.example/menu", "https://restoran.example:443/menu"])
def test_validate_url_allows_standard_ports(wire, url):
    assert mf._validate_menu_url(url)[2] is None
    assert mf._fetch_page(url).status_code == 200


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("suffix", ["user:pass@public.example/", "public.example:22/", "public.example:/", "public.example\\@evil/", "public.example/%0a"])
def test_invalid_authority_before_network(wire, scheme, suffix):
    url = f"{scheme}://{suffix}"
    if suffix.endswith("%0a"):
        # Escaped path bytes are opaque; credentials are still never attached.
        assert mf._fetch_page(url).status_code == 200
    else:
        with pytest.raises(ValueError):
            mf._fetch_page(url)
        assert not wire[0]


@pytest.mark.parametrize("url", ["file:///menu", "javascript:alert(1)", "data:text/plain,x", "ftp://x/", "gopher://x/", "ws://x/", "wss://x/", "custom://x/", "example.com/menu", "//example.com/menu", "https:///x", "https://x\n/"])
def test_other_schemes_missing_malformed_rejected(wire, url):
    assert mf._validate_menu_url(url)[2]
    with pytest.raises(ValueError):
        mf._fetch_page(url)
    assert not wire[0]


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_credential_isolation(wire, monkeypatch, tmp_path, scheme):
    netrc = tmp_path / "netrc"
    netrc.write_text("machine public.example login synthetic password synthetic\n")
    monkeypatch.setenv("NETRC", str(netrc))
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(key, "http://synthetic:synthetic@127.0.0.1:9999")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-aws")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "synthetic-session")
    monkeypatch.setenv("COGNITO_TOKEN", "synthetic-cognito")
    response = mf._safe_requests_get(f"{scheme}://public.example/", timeout=5, headers={"Authorization": "synthetic-inbound", "Cookie": "synthetic-session", "Proxy-Authorization": "synthetic-proxy", "X-Amz-Security-Token": "synthetic-aws"})
    assert response.status_code == 200
    request, options = wire[0][0]
    assert dict(request.headers) == mr.OUTBOUND_HEADERS
    assert not {h.lower() for h in request.headers} & {"authorization", "cookie", "proxy-authorization", "x-amz-security-token"}
    assert "synthetic" not in str(request.headers)
    assert options["proxies"] == {}
    assert options["verify"] is True
    assert options["timeout"][0] <= 3 and options["timeout"][1] <= 3
    assert options["stream"] is True


@pytest.mark.parametrize("start,end", [("http", "http"), ("http", "https"), ("https", "https")])
def test_redirect_allowed_and_no_response_cookies(wire, start, end):
    wire[1].append((302, {"Location": f"{end}://second.example/menu", "Set-Cookie": "synthetic=secret"}, b""))
    assert mf._fetch_page(f"{start}://public.example/").status_code == 200
    assert len(wire[0]) == 2
    assert all(dict(r.headers) == mr.OUTBOUND_HEADERS for r, _ in wire[0])


def test_https_downgrade(wire):
    wire[1].append((302, {"Location": "http://second.example/"}, b""))
    with pytest.raises(ValueError, match="MENU_HTTPS_DOWNGRADE"):
        mf._fetch_page("https://public.example/")
    assert len(wire[0]) == 1


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "172.16.1.1", "192.168.0.1", "169.254.169.254", "100.100.100.200", "100.64.0.1", "0.0.0.0", "198.18.0.1", "224.0.0.1", "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1", "64:ff9b::a00:1", "2002:7f00:1::"])
def test_destination_and_redirect_blocked(wire, monkeypatch, scheme, address):
    def resolve(host):
        if host == "private.example":
            if not mr.public_ip(address):
                raise ValueError("MENU_DESTINATION_BLOCKED")
        return [PUBLIC]
    monkeypatch.setattr(mr, "resolve", resolve)
    with pytest.raises(ValueError, match="DESTINATION"):
        mf._fetch_page(f"{scheme}://private.example/")
    assert not wire[0]
    wire[1].append((302, {"Location": f"{scheme}://private.example/"}, b""))
    with pytest.raises(ValueError, match="DESTINATION"):
        mf._fetch_page(f"{scheme}://public.example/")
    assert len(wire[0]) == 1


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_connected_peer_and_rebinding(monkeypatch, scheme):
    """Exercise actual urllib3 connection _new_conn; DNS cannot run at connect."""
    connected = []
    class Socket:
        peer = PUBLIC
        def settimeout(self, value): pass
        def setsockopt(self, *args): pass
        def connect(self, address): connected.append(address)
        def getpeername(self): return (self.peer, 80)
        def close(self): pass
    monkeypatch.setattr(socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("DNS re-resolved at connection"))
    adapter = mr.pinned_adapter(PUBLIC)
    cls = adapter.poolmanager.pool_classes_by_scheme[scheme].ConnectionCls
    conn = cls("public.example", port=443 if scheme == "https" else 80, timeout=1)
    conn._new_conn()
    assert connected == [(PUBLIC, conn.port)]
    assert conn.host == "public.example"
    Socket.peer = "10.0.0.1"
    with pytest.raises(ValueError, match="MENU_PEER_BLOCKED"):
        conn._new_conn()


def test_dns_all_answers_classified(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (PUBLIC, 0)), (2, 1, 6, "", ("10.0.0.1", 0))])
    with pytest.raises(ValueError):
        mf._resolve_host_safely("mixed.example")


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("media", ["application/pdf", "image/png", "application/zip", "text/html-evil", "", "application/octet-stream"])
def test_media_rejected_before_read(wire, scheme, media):
    wire[1].append((200, {"Content-Type": media}, b"ignored"))
    with pytest.raises(ValueError, match="MEDIA"):
        mf._fetch_page(f"{scheme}://public.example/")


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_limits_encoding_pdf_signature_and_redirects(wire, scheme):
    for headers, body, code in [({"Content-Type": "text/html", "Content-Encoding": "gzip"}, b"bomb", "ENCODING"), ({"Content-Type": "text/html"}, b"%PDF-1.4 fake", "MEDIA"), ({"Content-Type": "text/html"}, b" " * 20 + b"%PDF-1.4 fake", "MEDIA"), ({"Content-Type": "text/html"}, b"x" * (mr.MAX_BYTES + 1), "BODY")]:
        wire[1].append((200, headers, body))
        with pytest.raises(ValueError, match=code):
            mf._fetch_page(f"{scheme}://public.example/")
    wire[1].extend([(302, {"Location": f"{scheme}://public.example/again"}, b"")] * 6)
    with pytest.raises(ValueError, match="REDIRECT_LIMIT"):
        mf._fetch_page(f"{scheme}://public.example/")


def test_aggregate_budget(wire):
    @mr.menu_operation
    def run():
        for _ in range(3):
            wire[1].append((200, {"Content-Type": "text/plain"}, b"x" * 3_000_000))
            mf._safe_requests_get("http://public.example/", timeout=5)
    with pytest.raises(ValueError, match="BODY_LIMIT"):
        run()


def test_aggregate_request_count(wire):
    @mr.menu_operation
    def run():
        for _ in range(mr.MAX_REQUESTS + 1):
            mf._fetch_page("http://public.example/")
    with pytest.raises(ValueError, match="WORK_LIMIT"):
        run()
    assert len(wire[0]) == mr.MAX_REQUESTS


def test_deadline_shared(wire, monkeypatch):
    @mr.menu_operation
    def run():
        mr._budget.get().deadline = time.monotonic() - 1
        mf._fetch_page("http://public.example/")
    with pytest.raises(requests.Timeout):
        run()
    assert not wire[0]


def test_worker_environment_and_kill(monkeypatch):
    class Proc:
        returncode = None
        killed = False
        def communicate(self, *args, **kwargs):
            if not self.killed:
                raise __import__('subprocess').TimeoutExpired("worker", .01)
            return b"", b""
        def poll(self): return self.returncode
        def kill(self): self.killed = True; self.returncode = -9
    proc = Proc()
    def launch(*args, **kwargs):
        assert kwargs["env"] == {"LANG": "C.UTF-8"}
        assert kwargs["close_fds"] is True
        return proc
    monkeypatch.setattr(mr.subprocess, "Popen", launch)
    with pytest.raises(requests.Timeout):
        mr.run_worker({}, .01)
    assert proc.killed


def test_parser_admission_and_output():
    for html in ["<div>" * 65 + "x" + "</div>" * 65, "<p>x</p>" * 6001, "<?x>" * 6001, "<!x>" * 6001, "x" * 3_000_001]:
        with pytest.raises(ValueError, match="PARSE"):
            bounded_soup(html)
    for value in ["[" * 65 + "0" + "]" * 65, json.dumps(list(range(6001)))]:
        with pytest.raises(ValueError): bounded_json(value)
    assert sum(len(s['text']) + len(s['category']) + 4 for s in bound_sections([{"category": "x" * 1000, "text": "x" * 50000}] * 100)) <= 40000


@pytest.mark.parametrize("media,body", [("application/pdf", b"%PDF-1.4"), ("image/png", b"\x89PNG small"), ("image/jpeg", b"x"), ("application/zip", b"PK")])
def test_drive_disabled_parsers(app, monkeypatch, media, body):
    from app.services import menu_ocr
    monkeypatch.setattr(menu_ocr, "_extract_text_from_pdf", lambda *a: pytest.fail("PDF parser reachable"))
    monkeypatch.setattr(menu_ocr, "_extract_text_from_image", lambda *a: pytest.fail("image parser reachable"))
    resp = requests.Response(); resp.status_code = 200; resp._content = body; resp._content_consumed = True; resp.headers['Content-Type'] = media
    monkeypatch.setattr(mf, "_safe_requests_get", lambda *a, **k: resp)
    result, err = mf._process_google_drive_url("https://drive.google.com/file/d/X/view")
    assert result is None and err == "MENU_MEDIA_UNSUPPORTED"


def test_drive_text_and_html_bounded(app, wire):
    for media, body in [("text/plain", b"Adana Kebap " * 10000), ("text/html", b"<p>Adana Kebap Mercimek Corba Izgara Tavuk</p>")]:
        wire[1].append((200, {"Content-Type": media}, body))
        result, err = mf._process_google_drive_url("https://docs.google.com/document/d/X/edit")
        assert err is None
        assert len(result['body_text']) <= 40000


def test_drive_confirmation_disabled_and_host_exact(app, wire):
    wire[1].append((200, {"Content-Type": "text/html"}, b"virus scan <a id='uc-download-link' href='/uc?confirm=x'>download anyway</a>"))
    result, err = mf._process_google_drive_url("https://drive.google.com/file/d/X/view")
    assert result is None and err == "MENU_DRIVE_CONFIRMATION_UNSUPPORTED"
    assert len(wire[0]) == 1
    assert not mf._is_google_drive_url("https://evil.example/?drive.google.com/file/d/X")
    assert mf._is_google_drive_url("https://drive.google.com/file/d/X")


def test_architecture_guard():
    import ast
    from pathlib import Path
    paths = [Path('app/services/menu_fetch.py'), Path('app/services/menu_extract.py'), Path('app/blueprints/menu.py')]
    for path in paths:
        tree = ast.parse(path.read_text())
        requests_aliases = {alias.asname or alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names if alias.name == 'requests'}
        forbidden = {'_extract_text_from_pdf', '_extract_text_from_image', 'getaddrinfo', 'Session', 'urlopen', 'BeautifulSoup'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                assert name not in forbidden, (path, name)
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in requests_aliases:
                    assert name not in {'get', 'post', 'request', 'session'}, (path, name)
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or '').startswith(('urllib.request', 'httpx', 'aiohttp'))
    assert 'trust_env = False' in Path('app/services/menu_remote.py').read_text()
    assert mr.ALLOWED_SCHEMES == {'http', 'https'}
    assert 'native' not in Path('app/services/menu_remote.py').read_text().lower()


def test_loggable_url_redacts_secrets():
    assert mf.loggable_url("https://u:p@host.example/secret-path?q=secret#token") == "https://host.example/<path-redacted>"


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("size", [63, 64, 65])
@pytest.mark.parametrize("declared", [None, "1", "999999", "broken"])
def test_exact_decoded_byte_matrix(wire, scheme, size, declared):
    headers = {"Content-Type": "text/plain", "Transfer-Encoding": "chunked"}
    if declared is not None:
        headers["Content-Length"] = declared
    wire[1].append((200, headers, b"x" * size))
    if size > 64 or declared == "999999":
        with pytest.raises(ValueError, match="BODY_LIMIT"):
            mf._safe_requests_get(f"{scheme}://public.example/", timeout=5, max_bytes=64)
    else:
        assert len(mf._safe_requests_get(f"{scheme}://public.example/", timeout=5, max_bytes=64).content) == size


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "gzip, br"])
def test_all_compression_disabled(wire, scheme, encoding):
    wire[1].append((200, {"Content-Type": "text/html", "Content-Encoding": encoding}, b"compact-bomb"))
    with pytest.raises(ValueError, match="ENCODING_UNSUPPORTED"):
        mf._fetch_page(f"{scheme}://public.example/")


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_redirect_userinfo_before_next_network(wire, scheme):
    wire[1].append((302, {"Location": f"{scheme}://user:pass@public.example/"}, b""))
    with pytest.raises(ValueError): mf._fetch_page(f"{scheme}://public.example/")
    assert len(wire[0]) == 1


def compact_pdf(stream, pages=1):
    import zlib
    stream = zlib.compress(stream)
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [' + b' '.join(f'{i} 0 R'.encode() for i in range(5, 5 + pages)) + b'] /Count ' + str(pages).encode() + b' >>',
               b'<< /Length ' + str(len(stream)).encode() + b' /Filter /FlateDecode >>\nstream\n' + stream + b'\nendstream',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    objects += [b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 3 0 R /Resources << /Font << /F1 4 0 R >> >> >>'] * pages
    output = io.BytesIO(b'%PDF-1.4\n'); output.seek(0, 2)
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(output.tell()); output.write(f'{i} 0 obj\n'.encode() + body + b'\nendobj\n')
    xref = output.tell(); output.write(f'xref\n0 {len(objects)+1}\n'.encode() + b'0000000000 65535 f \n')
    for offset in offsets: output.write(f'{offset:010d} 00000 n \n'.encode())
    output.write(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
    return output.getvalue()


@pytest.mark.parametrize("pages", [1, 31])
@pytest.mark.parametrize("media", ["application/pdf", "text/html"])
def test_original_pdf_blockers_never_parse(app, wire, monkeypatch, pages, media):
    import pdfplumber
    from pdfminer.pdfdocument import PDFDocument
    from app.services import menu_ocr
    calls = []
    def forbidden(*a, **k):
        calls.append(True)
        pytest.fail("untrusted PDF reached parser or AI")
    monkeypatch.setattr(pdfplumber, "open", forbidden)
    monkeypatch.setattr(PDFDocument, "__init__", forbidden)
    monkeypatch.setattr(menu_ocr, "_extract_text_from_image", forbidden)
    stream = b'BT /F1 12 Tf 72 720 Td (Adana Kebap Mercimek Corba) Tj ET\n'
    if pages == 1:
        stream += b'%' + b'x' * (8 * 1024 * 1024) + b'\n'
    fixture = compact_pdf(stream, pages)
    assert len(fixture) < 16000
    wire[1].append((200, {"Content-Type": media}, fixture))
    with pytest.raises(ValueError, match="MEDIA_UNSUPPORTED"):
        mf._fetch_page("http://public.example/menu")
    wire[1].append((200, {"Content-Type": media}, fixture))
    result, err = mf._process_google_drive_url("https://drive.google.com/file/d/X/view")
    assert result is None and err
    assert calls == []


def test_real_worker_blocks_private_without_application_imports():
    with pytest.raises(ValueError, match="DESTINATION_BLOCKED"):
        mr.run_worker({"url": "http://127.0.0.1/", "byte_limit": 10, "request_limit": 1, "redirect_limit": 0, "seconds": 3}, 3)


def test_real_supervisor_kills_stalled_worker(monkeypatch, tmp_path):
    script = tmp_path / 'stalled.py'
    script.write_text('import time\ntime.sleep(60)\n')
    monkeypatch.setattr(mr, '__file__', str(script))
    start = time.monotonic()
    with pytest.raises(requests.Timeout):
        mr.run_worker({}, .2)
    assert time.monotonic() - start < 2


def test_inbound_flask_secrets_never_forward(client, auth_user, wire):
    response = client.post('/api/proxy/scan-menu', json={'url': 'http://public.example/menu'}, headers={'Authorization': 'Bearer synthetic-cognito', 'Cookie': 'synthetic-session', 'X-Amz-Security-Token': 'synthetic-aws', 'Proxy-Authorization': 'synthetic-proxy'})
    assert response.status_code == 200
    assert wire[0]
    assert all(dict(request.headers) == mr.OUTBOUND_HEADERS for request, _ in wire[0])


@pytest.mark.parametrize('ip', ['8.8.8.8', PUBLIC, '2001:4860:4860::8888', '::ffff:93.184.216.34'])
def test_public_ips_allowed(ip):
    assert mf._is_safe_public_ip(ip)


@pytest.mark.parametrize('ip', ['::', '127.1.2.3', '::ffff:100.64.1.1', '192.0.2.10', '192.0.0.9', '192.88.99.1', '2001:20::1', '3fff::1', '5f00::1', 'bad', ''])
def test_special_and_invalid_addresses(ip):
    assert not mf._is_safe_public_ip(ip)


def test_dns_failure_is_sanitized(monkeypatch):
    def fail(*a, **k): raise socket.gaierror('synthetic-sensitive-details')
    monkeypatch.setattr(socket, 'getaddrinfo', fail)
    with pytest.raises(ValueError, match='^MENU_DNS_FAILED$'):
        mf._resolve_host_safely('public.example')


def test_tracking_cleanup_keeps_http_scheme():
    _, clean, err = mf._validate_menu_url('http://public.example/menu?utm_source=x&kategori=ana#fragment')
    assert err is None and clean == 'http://public.example/menu?kategori=ana'


def test_shared_fetcher_does_not_define_native_intake():
    from pathlib import Path
    doc = Path('docs/LP15_B1_MENU_FETCH_SECURITY.md').read_text()
    assert 'HTTPS-only before calling this shared HTTP/HTTPS fetcher' in doc
    assert '**not implemented here**' in doc


def test_failed_fetch_consumes_aggregate_reservation(wire):
    @mr.menu_operation
    def run():
        for _ in range(3):
            wire[1].append((200, {'Content-Type': 'application/pdf'}, b'%PDF'))
            with pytest.raises(ValueError):
                mf._fetch_page('http://public.example/')
        with pytest.raises(ValueError, match='WORK_LIMIT'):
            mf._fetch_page('http://public.example/')
    run()
    assert len(wire[0]) == 3


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_scan_pdf_media_uses_existing_unsupported_status(client, auth_user, wire, scheme):
    wire[1].append((200, {'Content-Type': 'application/pdf'}, b'%PDF-1.4'))
    response = client.post('/api/proxy/scan-menu', json={'url': f'{scheme}://public.example/menu'})
    assert response.status_code == 415
    assert response.get_json()['error'] == 'MENU_MEDIA_UNSUPPORTED'


def test_pinned_https_keeps_sni_and_certificate_authority(monkeypatch):
    import ssl
    import urllib3.connection
    connected = []
    class Socket:
        def settimeout(self, timeout): pass
        def setsockopt(self, *args): pass
        def connect(self, address): connected.append(address)
        def getpeername(self): return (PUBLIC, 443)
        def close(self): pass
    monkeypatch.setattr(socket, 'socket', lambda *args: Socket())
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('second DNS lookup'))
    captured = []
    def tls(**kwargs):
        captured.append(kwargs)
        return kwargs['sock'], True
    monkeypatch.setattr(urllib3.connection, '_ssl_wrap_socket_and_match_hostname', tls)
    cls = mr.pinned_adapter(PUBLIC).poolmanager.pool_classes_by_scheme['https'].ConnectionCls
    conn = cls('public.example', port=443, timeout=1)
    conn.connect()
    assert connected == [(PUBLIC, 443)]
    assert captured[0]['server_hostname'] == 'public.example'
    assert captured[0]['cert_reqs'] == ssl.CERT_REQUIRED
    assert captured[0]['assert_hostname'] is not False
    assert conn.is_verified and not conn.proxy
