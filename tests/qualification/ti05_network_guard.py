"""Qualification-only process guard: deny and count non-loopback network attempts."""
import ipaddress
import socket

attempts = []
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo


def _local(host):
    if host in ('localhost', None):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check(host):
    if not _local(host):
        attempts.append('external network attempt')
        raise RuntimeError('TI-05 forbids external networking')


def connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _check(address[0])
    return _connect(self, address)


def connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _check(address[0])
    return _connect_ex(self, address)


def getaddrinfo(host, *args, **kwargs):
    _check(host)
    return _getaddrinfo(host, *args, **kwargs)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.getaddrinfo = getaddrinfo


def pytest_sessionfinish(session, exitstatus):
    if attempts:
        session.exitstatus = 1


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(f'TI05 unexpected external network attempts: {len(attempts)}')
