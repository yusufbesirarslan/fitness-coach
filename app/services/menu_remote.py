"""Credential-free public retrieval. Also runs as a standalone killable worker.

No application imports in the worker; its environment contains no credentials.
HTTP is compatibility-preserved untrusted public retrieval, not secure transport.
"""
import base64
import contextlib
import contextvars
import functools
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

ALLOWED_SCHEMES = {"http", "https"}
MAX_BYTES = 3_000_000
MAX_TOTAL_BYTES = 8_000_000
MAX_REQUESTS = 48
MAX_REDIRECTS = 5
OPERATION_SECONDS = 30
ALLOWED_MEDIA = {"text/html", "application/xhtml+xml", "text/plain", "text/csv", "application/json"}
OUTBOUND_HEADERS = {"User-Agent": "AxisAI-Public-Menu/1.0", "Accept": "text/html,text/plain,text/csv,application/json,application/xhtml+xml", "Accept-Encoding": "identity"}


def public_ip(value):
    try:
        ip = ipaddress.ip_address(value)
        if isinstance(ip, ipaddress.IPv6Address):
            if ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            elif ip.sixtofour or ip.teredo or ip in ipaddress.ip_network("64:ff9b::/96") or ip in ipaddress.ip_network("64:ff9b:1::/48"):
                return False
        special = ("192.0.0.0/24", "192.88.99.0/24") if ip.version == 4 else ("2001::/23", "3fff::/20", "5f00::/16")
        if any(ip in ipaddress.ip_network(net) for net in special):
            return False
        return ip.is_global and not (ip.is_private or ip.is_reserved or ip.is_multicast or ip.is_loopback or ip.is_link_local or ip.is_unspecified)
    except ValueError:
        return False


def validate_url(url):
    if not isinstance(url, str) or len(url) > 4096 or re.search(r"[\x00-\x20\x7f\\]", url):
        raise ValueError("MENU_URL_INVALID")
    try:
        p = urlsplit(url)
        if p.scheme not in ALLOWED_SCHEMES or not p.hostname or p.username is not None or p.password is not None or "@" in p.netloc or "%" in p.hostname:
            raise ValueError()
        if p.port not in (None, 80, 443) or p.netloc.endswith(":"):
            raise ValueError()
        host = p.hostname.encode("idna").decode("ascii")
        if ":" not in host and not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ValueError("MENU_URL_INVALID") from None
    return p


def resolve(host):
    try:
        ips = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)))
    except OSError:
        raise ValueError("MENU_DNS_FAILED") from None
    if not ips or len(ips) > 32 or not all(public_ip(ip) for ip in ips):
        raise ValueError("MENU_DESTINATION_BLOCKED")
    return ips


def pinned_adapter(ip):
    """Connect literal approved address; URL host remains Host/SNI/cert authority.

    No second DNS lookup, global monkeypatch, proxy, retry or pooled other origin.
    Verify connected peer before sending any application bytes.
    """
    class Pin:
        def _new_conn(self):
            if not public_ip(ip):
                raise ValueError("MENU_DESTINATION_BLOCKED")
            sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(self.timeout)
                for option in self.socket_options or []:
                    sock.setsockopt(*option)
                sock.connect((ip, self.port))
                peer = sock.getpeername()[0]
                if not public_ip(peer) or ipaddress.ip_address(peer) != ipaddress.ip_address(ip):
                    raise ValueError("MENU_PEER_BLOCKED")
                return sock
            except BaseException:
                sock.close()
                raise

    class HTTP(Pin, HTTPConnection):
        pass

    class HTTPS(Pin, HTTPSConnection):
        pass

    class HTTPPool(HTTPConnectionPool):
        ConnectionCls = HTTP

    class HTTPSPool(HTTPSConnectionPool):
        ConnectionCls = HTTPS

    adapter = HTTPAdapter(max_retries=0)
    adapter.poolmanager.pool_classes_by_scheme = {"http": HTTPPool, "https": HTTPSPool}
    return adapter


def retrieve(url, byte_limit, request_limit, redirect_limit, seconds):
    deadline = time.monotonic() + seconds
    used = 0
    current = url
    for hop in range(redirect_limit + 1):
        if used >= request_limit or time.monotonic() >= deadline:
            raise ValueError("MENU_WORK_LIMIT")
        p = validate_url(current)
        ip = resolve(p.hostname)[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("MENU_DEADLINE")
        used += 1
        # Fresh session each hop: cookies from a response can never propagate.
        with requests.Session() as session:
            session.trust_env = False
            session.auth = None
            session.headers.clear()
            session.headers.update(OUTBOUND_HEADERS)
            session.cookies.clear()
            session.mount(p.scheme + "://", pinned_adapter(ip))
            with session.get(current, stream=True, allow_redirects=False, timeout=(min(3, remaining), min(3, remaining)), proxies={}) as resp:
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                    target = urljoin(current, resp.headers["Location"])
                    target_p = validate_url(target)
                    if p.scheme == "https" and target_p.scheme == "http":
                        raise ValueError("MENU_HTTPS_DOWNGRADE")
                    if hop == redirect_limit:
                        raise ValueError("MENU_REDIRECT_LIMIT")
                    current = target
                    continue
                media = resp.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if media not in ALLOWED_MEDIA:
                    raise ValueError("MENU_MEDIA_UNSUPPORTED")
                if resp.headers.get("Content-Encoding", "identity").strip().lower() not in ("", "identity"):
                    raise ValueError("MENU_ENCODING_UNSUPPORTED")
                declared = resp.headers.get("Content-Length", "")
                if declared.isdigit() and int(declared) > byte_limit:
                    raise ValueError("MENU_BODY_LIMIT")
                body = bytearray()
                # read1 returns available bytes without waiting to fill a large
                # buffer; supervisor also kills DNS/headers/drip stalls.
                while True:
                    if time.monotonic() >= deadline:
                        raise ValueError("MENU_DEADLINE")
                    chunk = resp.raw.read1(min(8192, byte_limit + 1 - len(body)), decode_content=False)
                    if not chunk:
                        break
                    if len(body) + len(chunk) > byte_limit:
                        raise ValueError("MENU_BODY_LIMIT")
                    body.extend(chunk)
                if body.lstrip().startswith(b"%PDF-"):
                    raise ValueError("MENU_MEDIA_UNSUPPORTED")
                return {"status": resp.status_code, "media": media, "body": base64.b64encode(body).decode("ascii"), "requests": used}
    raise ValueError("MENU_REDIRECT_LIMIT")


class Budget:
    def __init__(self):
        self.deadline = time.monotonic() + OPERATION_SECONDS
        self.bytes = 0
        self.requests = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout("MENU_DEADLINE")
        return remaining


_budget = contextvars.ContextVar("menu_budget", default=None)
_workers = threading.BoundedSemaphore(4)


def menu_operation(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        token = _budget.set(Budget())
        try:
            return fn(*args, **kwargs)
        finally:
            _budget.reset(token)
    return wrapped


def run_worker(payload, seconds):
    started = time.monotonic()
    if not _workers.acquire(timeout=max(0, seconds)):
        raise requests.Timeout("MENU_DEADLINE")
    proc = None
    try:
        if time.monotonic() - started >= seconds:
            raise requests.Timeout("MENU_DEADLINE")
        proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={"LANG": "C.UTF-8"}, close_fds=True)
        try:
            output, _ = proc.communicate(json.dumps(payload).encode(), timeout=max(0.001, seconds - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            raise requests.Timeout("MENU_DEADLINE") from None
        if proc.returncode or len(output) > 4 * MAX_BYTES // 3 + 4096:
            raise ValueError("MENU_FETCH_FAILED")
        result = json.loads(output)
        if "error" in result:
            raise ValueError(result["error"])
        return result
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate()
        _workers.release()


def fetch(url, *, max_bytes=None, max_redirects=MAX_REDIRECTS):
    validate_url(url)  # no subprocess/network for malformed/userinfo URLs
    budget = _budget.get() or Budget()
    left = MAX_REQUESTS - budget.requests
    cap = min(MAX_BYTES, MAX_TOTAL_BYTES - budget.bytes, max_bytes if max_bytes is not None else MAX_BYTES)
    if left <= 0 or cap <= 0:
        raise ValueError("MENU_WORK_LIMIT")
    hops = min(MAX_REDIRECTS, max_redirects, left - 1)
    # Failures consume the reserved work too; repeated failures cannot reset it.
    budget.requests += hops + 1
    budget.bytes += cap
    result = run_worker({"url": url, "byte_limit": cap, "request_limit": left, "redirect_limit": hops, "seconds": budget.remaining()}, budget.remaining())
    budget.remaining()
    budget.requests -= hops + 1 - result["requests"]
    body = base64.b64decode(result["body"], validate=True)
    if len(body) > cap:
        raise ValueError("MENU_BODY_LIMIT")
    budget.bytes -= cap - len(body)
    resp = requests.Response()
    resp.status_code = result["status"]
    resp.headers["Content-Type"] = result["media"]
    resp._content = body
    resp._content_consumed = True
    resp.encoding = "utf-8"
    resp.url = url
    return resp


if __name__ == "__main__":
    try:
        result = retrieve(**json.loads(sys.stdin.buffer.read(8192)))
    except Exception as exc:
        # Never serialize remote URL, headers, body or raw exception details.
        code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("MENU_") else "MENU_FETCH_FAILED"
        result = {"error": code}
    sys.stdout.write(json.dumps(result))
