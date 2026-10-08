"""Offline, URL-routed wire for menu acquisition tests.

Everything in `app.services.menu_remote` stays real — URL admission, the DNS
answer classification (`resolve` → `public_ip`), the pinned adapter, manual
redirect admission, HTTPS-downgrade rejection, media/encoding/body caps and the
shared operation budget. Only three things are replaced:

* `socket.getaddrinfo` answers from a host table (so private/link-local answers
  go through the real classifier),
* `HTTPAdapter.send` answers from a URL table (no socket is ever opened), and
* `run_worker` runs `retrieve` in-process (so the two fakes above apply).

`socket.socket.connect` fails the test if anything tries real network.
"""
import io
import socket
from email.message import Message
from types import SimpleNamespace

import pytest
import requests

from app.services import menu_remote as mr

PUBLIC = "93.184.216.34"


class RoutedWire:
    def __init__(self):
        self.hosts = {}
        self.routes = {}
        self.seen = []

    def host(self, name, *addresses):
        self.hosts[name] = list(addresses)

    def route(self, url, body=b"", status=200, headers=None):
        merged = {"Content-Type": "text/html; charset=utf-8"}
        merged.update(headers or {})
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.routes[url] = (status, merged, body)

    def redirect(self, url, location, status=302):
        self.routes[url] = (status, {"Location": location}, b"")

    def urls(self):
        return [request.url for request, _ in self.seen]

    # -- fakes ---------------------------------------------------------------
    def getaddrinfo(self, host, *args, **kwargs):
        if host not in self.hosts:
            raise socket.gaierror("unknown synthetic host")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET,
                 socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in self.hosts[host]]

    def send(self, adapter, request, **kwargs):
        self.seen.append((request, kwargs))
        status, headers, body = self.routes.get(
            request.url, (404, {"Content-Type": "text/html"}, b"not found"))
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers)
        response.request = request
        redirect = status in (301, 302, 303, 307, 308) and "Location" in headers

        class Raw(io.BytesIO):
            def read(self, *args, **kwargs):
                if redirect:
                    pytest.fail("redirect body must never be read")
                return super().read(*args, **kwargs)

            def read1(self, size, decode_content=False):
                assert decode_content is False
                if redirect:
                    pytest.fail("redirect body must never be read")
                return super().read(size)

        response.raw = Raw(body)
        message = Message()
        for key, value in headers.items():
            message[key] = value
        response.raw._original_response = SimpleNamespace(msg=message)
        return response


@pytest.fixture
def routed_wire(monkeypatch):
    wire = RoutedWire()
    monkeypatch.setattr(socket, "getaddrinfo", wire.getaddrinfo)
    monkeypatch.setattr(
        requests.adapters.HTTPAdapter, "send",
        lambda adapter, request, **kwargs: wire.send(adapter, request, **kwargs))
    monkeypatch.setattr(
        socket.socket, "connect",
        lambda *a: pytest.fail("real network attempted"))
    monkeypatch.setattr(
        mr, "run_worker", lambda payload, seconds: mr.retrieve(**payload))
    return wire
