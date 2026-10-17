"""Explicit self-check: passing assertions must still force guard process exit 1.

Not ordinarily collected. Run explicitly with the TI-05 guard plugin.
No network call is dispatched: the guard rejects these reserved targets first.
"""
import socket
import pytest
from tests.qualification import ti05_network_guard as guard


@pytest.mark.parametrize('operation', ['dns', 'connect', 'connect_ex'])
def test_swallowed_external_attempt_still_fails_session(operation):
    before = len(guard.attempts)
    with pytest.raises(RuntimeError, match='TI-05 forbids external networking'):
        if operation == 'dns':
            socket.getaddrinfo('example.invalid', 443)
        else:
            with socket.socket() as sock:
                getattr(sock, operation)(('192.0.2.1', 443))
    assert len(guard.attempts) == before + 1
