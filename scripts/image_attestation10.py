"""Offline image attestation and a synthetic HTTP/1 server probe. No app startup."""
import concurrent.futures
import hashlib
import http.client
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

barrier = threading.Barrier(8)


def probe(environ, start_response):
    response = start_response.__self__
    data = {'thread_id': threading.get_ident(), 'thread_name': threading.current_thread().name,
            'pid': os.getpid(), 'multithread': environ['wsgi.multithread'],
            'protocol': environ['SERVER_PROTOCOL'],
            'response_class': type(response).__module__ + '.' + type(response).__name__,
            'socket_class': type(response.sock).__module__ + '.' + type(response.sock).__name__,
            'plain_socket': type(response.sock) is socket.socket}
    barrier.wait(timeout=20)
    body = json.dumps(data).encode()
    start_response('200 OK', [('Content-Length', str(len(body))), ('Connection', 'close')])
    return [body]


def main():
    sys.path.insert(0, '/app')
    from gunicorn.app.wsgiapp import WSGIApplication
    from packaging.requirements import Requirement
    from app.consent_publication import ConsentPublication
    sys.argv = ['gunicorn', '--config', '/app/gunicorn.conf.py', 'starter:app']
    cfg = WSGIApplication().cfg
    result = {
        'revision': Path('/app/BUILD_REVISION').read_text().strip(),
        'gunicorn': importlib.metadata.version('gunicorn'),
        'packages': sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions()),
        'files': {p: hashlib.sha256(Path('/app', p).read_bytes()).hexdigest() for p in
                  ['Dockerfile', 'requirements.txt', 'gunicorn.conf.py', 'app/__init__.py', 'app/consent_publication.py', 'starter.py']},
        'worker_class': cfg.worker_class.__module__ + '.' + cfg.worker_class.__name__,
        'configuration': {k: getattr(cfg, k) for k in
                          ['workers', 'threads', 'bind', 'timeout', 'graceful_timeout', 'certfile', 'keyfile']},
        'uid': os.getuid(),
        'middleware_class': ConsentPublication.__module__ + '.' + ConsentPublication.__name__,
        'middleware_import_and_construction': type(ConsentPublication(probe)) is ConsentPublication,
        'factory_wrap_present': 'app.wsgi_app = ConsentPublication(app.wsgi_app)' in Path('/app/app/__init__.py').read_text(),
        'scope': 'No starter import/application initialization, database, Coach activation, protected publication or provider calls. Synthetic HTTP/1 probe only.'
    }
    checked = []
    for line in Path('/app/requirements.txt').read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        req = Requirement(line)
        if req.marker and not req.marker.evaluate():
            continue
        version = importlib.metadata.version(req.name)
        assert version in req.specifier, (line, version)
        checked.append({'requirement': line, 'installed': version})
    result['direct_requirements'] = checked
    check = subprocess.run([sys.executable, '-m', 'pip', 'check'], capture_output=True, text=True)
    result['pip_check'] = {'exit_code': check.returncode, 'stdout': check.stdout, 'stderr': check.stderr}
    assert check.returncode == 0
    assert result['revision'] == 'e6af3067cec6ad623e16c57bae0141aa9d6ad91d'
    assert result['gunicorn'] == '26.0.0'
    assert result['worker_class'] == 'gunicorn.workers.gthread.ThreadWorker'
    assert cfg.workers == 1 and cfg.threads == 8
    assert not cfg.certfile and not cfg.keyfile
    assert result['middleware_import_and_construction'] and result['factory_wrap_present']
    command = ['gunicorn', '--config', '/app/gunicorn.conf.py', '--pythonpath', '/attest', 'image_attestation10:probe']
    log = open('/tmp/probe.log', 'w+')
    proc = subprocess.Popen(command, stdout=log, stderr=log)
    try:
        for _ in range(200):
            if proc.poll() is not None:
                raise RuntimeError('probe Gunicorn terminated')
            try:
                with socket.create_connection(('127.0.0.1', 5000), timeout=.1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('probe Gunicorn not ready')
        def request(_):
            conn = http.client.HTTPConnection('127.0.0.1', 5000, timeout=30)
            conn.request('GET', '/synthetic-image-attestation')
            reply = conn.getresponse()
            assert reply.status == 200 and reply.version == 11
            body = json.loads(reply.read())
            conn.close()
            return body
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(request, range(8)))
        assert len({r['thread_id'] for r in replies}) == 8
        assert len({r['pid'] for r in replies}) == 1
        assert all(r['multithread'] and r['plain_socket'] and r['protocol'] == 'HTTP/1.1'
                   and r['response_class'] == 'gunicorn.http.wsgi.Response' for r in replies)
        result['http1_probe'] = replies
        result['eight_concurrent_runtime_threads'] = True
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.seek(0)
        result['gunicorn_probe_log'] = log.read()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
