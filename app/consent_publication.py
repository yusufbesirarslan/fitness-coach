"""Bounded native Coach publication on the pinned Gunicorn HTTP/1 transport.

The boundary is acceptance by the backend socket (usually nginx), not delivery
to the device. Bytes accepted before withdrawal acknowledgement cannot be
recalled. No background writer or yielded protected bytes survive this call.
"""
import select
import socket
import time

from flask import current_app, request

from app.services import ai_consent

MARKER = "axisai.consent_publication"
SEND_SECONDS = 0.75  # below the existing 2s consent mutation lock timeout
MAX_BODY_BYTES = 1024 * 1024


def protect_response(response):
    authority = ai_consent.current_operation()
    ai_consent._operation_binding(authority)
    if ai_consent.current_subject() != authority.owner:
        raise ai_consent.ConsentDenied()
    if response.is_streamed:
        raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
    request.environ[MARKER] = (current_app._get_current_object(), authority)
    return response


def _close(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    finally:
        sock.close()


class _DeadlineSocket:
    """Synchronous nonblocking sends sharing one monotonic absolute deadline."""
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def sendall(self, data):
        pending = memoryview(data)
        while pending:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("protected publication deadline")
            try:
                sent = self.sock.send(pending)
                if not sent:
                    raise ConnectionError("protected publication disconnected")
                pending = pending[sent:]
            except (BlockingIOError, InterruptedError):
                select.select([], [self.sock], [], remaining)


class ConsentPublication:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        captured = {}

        def capture(status, headers, exc_info=None):
            captured.update(status=status, headers=headers, exc_info=exc_info)
            # Flask does not use the legacy WSGI write callback. Unknown apps
            # cannot transmit protected bytes through this callback.
            def unsupported_write(data):
                raise RuntimeError("WSGI write callback unavailable")
            return unsupported_write

        iterable = self.app(environ, capture)
        marker = environ.get(MARKER)
        if marker is None:
            start_response(captured["status"], captured["headers"], captured["exc_info"])
            return iterable

        from gunicorn import __version__
        from gunicorn.http.wsgi import Response
        transport = getattr(start_response, "__self__", None)
        sock = getattr(transport, "sock", None)
        app, authority = marker
        try:
            # gthread HTTP/1 passes exactly this bound method. HTTP/2, TLS,
            # test clients, wrappers and future server versions are unqualified.
            if (__version__ != "26.0.0" or type(transport) is not Response
                    or type(sock) is not socket.socket
                    or transport.req.version not in ((1, 0), (1, 1))
                    or start_response.__func__ is not Response.start_response):
                raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
            body = bytearray()
            for part in iterable:
                if not isinstance(part, bytes) or len(body) + len(part) > MAX_BODY_BYTES:
                    raise ai_consent.ConsentDenied("AI_CONSENT_PATH_UNAVAILABLE")
                body.extend(part)
            headers = [(k, v) for k, v in captured["headers"]
                       if k.lower() not in {"content-length", "transfer-encoding"}]
            headers.append(("Content-Length", str(len(body))))
            transport.force_close()
            with app.app_context():
                ai_consent._operation_binding(authority)
                with ai_consent._transaction(authority.owner) as conn:
                    try:
                        ai_consent._check_operation(ai_consent._state(conn, authority.owner), authority, conn)
                        deadline = time.monotonic() + SEND_SECONDS
                        sock.setblocking(False)
                        transport.sock = _DeadlineSocket(sock, deadline)
                        write = start_response(captured["status"], headers, captured["exc_info"])
                        write(bytes(body))
                        # A fixed length means Response.close has nothing to send.
                    except BaseException:
                        # Shutdown/close happens BEFORE the DB context releases
                        # the owner lock, even for partial headers or SystemExit.
                        _close(sock)
                        raise
                    finally:
                        transport.sock = sock
        except BaseException:
            if sock is not None:
                _close(sock)
            if type(transport) is Response:
                transport.force_close()
                transport.headers_sent = True
            raise
        finally:
            if hasattr(iterable, "close"):
                iterable.close()
        return []
