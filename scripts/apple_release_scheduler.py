#!/usr/bin/env python3
"""R6 Apple timer launcher. No credentials, AWS calls or schedule installation.

Installed root-owned at /usr/local/libexec/axisai-apple-scheduler. The one timer
owns cadence; each pass selects the canonical active release under the existing
shared deployment lock. The image owns the executable financial state machine.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

LOCK = Path('/run/lock/axisai-production/production.lock')
RUN_LOCK = Path('/run/lock/axisai-production/apple-reconcile.lock')
CONFIG = Path('/etc/axisai/apple-reconcile.json')
INSTALLED = '/usr/local/libexec/axisai-apple-scheduler'
PASS = '/usr/local/libexec/axisai-apple-pass'
SERVICE = 'axisai-apple-reconcile.service'
TIMER = 'axisai-apple-reconcile.timer'
SHA = re.compile(r'[0-9a-f]{40}')
CID = re.compile(r'[0-9a-f]{64}')
MARKER = 'axisai-apple-pass'
FILES = ((INSTALLED, 'scripts/apple_release_scheduler.py'),
         ('/etc/systemd/system/' + SERVICE, 'deploy/systemd/' + SERVICE),
         ('/etc/systemd/system/' + TIMER, 'deploy/systemd/' + TIMER))


class Refused(RuntimeError):
    pass


def command(argv, timeout=10, pass_fds=()):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           pass_fds=pass_fds, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('COMMAND_UNAVAILABLE') from None
    if p.returncode:
        raise Refused('COMMAND_FAILED')
    return p.stdout.strip()


def trusted_file(path, uid=0):
    """No symlinks or writable ancestors; never return credentials."""
    path = Path(path)
    for parent in (path, *path.parents):
        info = parent.lstat()
        if (info.st_uid != uid or stat.S_ISLNK(info.st_mode)
                or info.st_mode & 0o022):
            raise Refused('UNSAFE_SCHEDULER_FILE')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Refused('UNSAFE_SCHEDULER_FILE')
    return path.read_bytes()


def configuration(raw):
    try:
        config = json.loads(raw)
        directory, limit = config['deploy_dir'], config['limit']
        if (set(config) != {'protocol', 'deploy_dir', 'limit'} or config['protocol'] != 1
                or type(limit) is not int or not 25 <= limit <= 200
                or not isinstance(directory, str)
                or not re.fullmatch(r'/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+', directory)
                or any(part in ('.', '..') for part in directory.split('/'))):
            raise ValueError()
        return config
    except (ValueError, KeyError, TypeError):
        raise Refused('INVALID_SCHEDULER_CONFIG') from None


def properties(raw):
    return dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)


def installed(context, deploy_dir, run=command, uid=0):
    config = configuration(trusted_file(CONFIG, uid))
    if config['deploy_dir'] != str(deploy_dir):
        raise Refused('SCHEDULER_DEPLOYMENT_MISMATCH')
    for dest, rel in FILES:
        if trusted_file(dest, uid) != (Path(context) / rel).read_bytes():
            raise Refused('SCHEDULER_SOURCE_MISMATCH')
    for unit in (SERVICE, TIMER):
        p = properties(run(['systemctl', 'show', unit, '--no-pager',
                            '--property=LoadState,FragmentPath,DropInPaths,NeedDaemonReload,UnitFileState,ActiveState,Triggers,TimersMonotonic,ExecStart']))
        if (p.get('LoadState') != 'loaded' or p.get('DropInPaths')
                or p.get('NeedDaemonReload') != 'no'
                or p.get('FragmentPath') != '/etc/systemd/system/' + unit):
            raise Refused('SCHEDULER_UNIT_UNQUALIFIED')
        if unit == TIMER:
            if (p.get('UnitFileState') != 'enabled' or p.get('ActiveState') != 'active'
                    or p.get('Triggers') != SERVICE or '1h' not in p.get('TimersMonotonic', '')):
                raise Refused('SCHEDULER_OWNERSHIP_UNPROVEN')
        elif INSTALLED not in p.get('ExecStart', ''):
            raise Refused('SCHEDULER_EXECUTABLE_UNPROVEN')
    return config


def no_surviving_passes(run=command):
    """Called under EXCLUSIVE release lock before schema preparation.

    A daemon-side exec can outlive a crashed Docker client. Refuse migration
    while its bounded marker process (or legacy CLI) is still present.
    """
    ids = run(['docker', 'ps', '-q', '--no-trunc']).splitlines()
    for cid in ids:
        if not CID.fullmatch(cid):
            raise Refused('AMBIGUOUS_CONTAINER')
        top = run(['docker', 'top', cid, '-eo', 'args'])
        if MARKER in top or 'apple-subscription-reconcile' in top:
            raise Refused('PREVIOUS_RECONCILIATION_STILL_RUNNING')


def active_release(deploy_dir, run=command):
    # Same installed route authority and protocol as the R6 transaction.
    route = run(['/usr/local/sbin/axisai-switch-web-slot', 'status'])
    match = re.fullmatch(r'op=status state=(blue|green|legacy) backend=127\.0\.0\.1:(5000|5001|5002) result=ok', route)
    ports = {'legacy': '5000', 'blue': '5001', 'green': '5002'}
    if not match or ports[match[1]] != match[2]:
        raise Refused('ROUTE_UNPROVEN')
    project = Path(deploy_dir).name if match[1] == 'legacy' else 'axisai-web-' + match[1]
    ids = run(['docker', 'ps', '-q', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + project,
               '--filter', 'label=com.docker.compose.service=web']).splitlines()
    if len(ids) != 1 or not CID.fullmatch(ids[0]):
        raise Refused('ACTIVE_WEB_UNPROVEN')
    cid = ids[0]
    revision = run(['git', '-C', str(deploy_dir), 'rev-parse', 'HEAD^{commit}'])
    identity = run(['docker', 'inspect', '--format',
                    '{{.State.Status}} {{.State.Health.Status}} {{.Image}}', cid]).split()
    baked = run(['docker', 'exec', cid, 'cat', '/app/BUILD_REVISION'])
    runtime = run(['docker', 'exec', cid, 'python', '-c',
                   'import os; print(os.environ.get("APP_REVISION", ""))'])
    if (not SHA.fullmatch(revision) or baked != revision or runtime != revision
            or len(identity) != 3 or identity[:2] != ['running', 'healthy']
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', identity[2])):
        raise Refused('ACTIVE_REVISION_MISMATCH')
    return cid, revision


def locked_file(path, operation, create=False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    if create:
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_nlink != 1 or info.st_mode & 0o022):
        os.close(fd)
        raise Refused('UNSAFE_SCHEDULER_LOCK')
    try:
        fcntl.flock(fd, operation | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return fd


def execute(config, run=command, lock_fds=()):
    no_surviving_passes(run)
    cid, revision = active_release(config['deploy_dir'], run)
    # Keyless/disabled retained releases need no new executable. Flags are only
    # presence booleans; no environment or Apple/customer data leaves the image.
    enabled = run(['docker', 'exec', cid, 'python', '-c',
                   'import os; v=[(os.environ.get(k, "0") if os.environ.get(k, "0").strip() else "0") for k in ("APPLE_NOTIFICATIONS_ENABLED", "APPLE_SUBSCRIPTIONS_ENABLED")]; print(int("1" in v) if all(x in ("0", "1") for x in v) else "INVALID")'])
    if enabled == '0':
        return 'DISABLED'
    if enabled != '1':
        raise Refused('INTAKE_STATE_UNPROVEN')
    # The active immutable revision owns both launcher protocol and executable.
    source = run(['git', '-C', config['deploy_dir'], 'show', revision + ':scripts/apple_release_scheduler.py'])
    if source.strip() != Path(__file__).read_text().strip():
        raise Refused('RUNNER_REVISION_MISMATCH')
    output = run(['docker', 'exec', '-e', 'FITX_STARTUP_MODE=read-only',
                  '-e', 'FITX_SKIP_DB_INIT=0', cid,
                  '/usr/bin/timeout', '--signal=KILL', '240',
                  'python', PASS, revision, str(config['limit'])],
                 timeout=255, pass_fds=lock_fds)
    # The child emits one closed outcome; discard all application startup logs.
    if output.splitlines()[-1:] != ['APPLE_RELEASE_PASS=OK']:
        raise Refused('RECONCILIATION_FAILED')
    health_lines = [line for line in output.splitlines() if line.startswith('APPLE_RELEASE_HEALTH=')]
    if len(health_lines) != 1:
        raise Refused('RECONCILIATION_HEALTH_UNPROVEN')
    health = json.loads(health_lines[0].split('=', 1)[1])
    keys = {'relevant_rows', 'backlog', 'due', 'leased', 'oldest_verification_age_seconds'}
    if (not isinstance(health, dict) or not health or set(health) - {'Production', 'Sandbox'}
            or any(not isinstance(state, dict) or set(state) != keys
                   or any(not (type(v) is int and v >= 0) and not (k == 'oldest_verification_age_seconds' and v is None)
                          for k, v in state.items()) for state in health.values())):
        raise Refused('RECONCILIATION_HEALTH_UNPROVEN')
    print('APPLE_RELEASE_HEALTH=' + json.dumps(health, sort_keys=True))
    return 'OK'


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    held = []
    try:
        # Unit/config paths are fixed and root-controlled. Deployment and passes
        # exclude each other, including Docker clients surviving launcher exit.
        trusted_file(INSTALLED)
        config = configuration(trusted_file(CONFIG))
        held.append(locked_file(LOCK, fcntl.LOCK_SH))
        held.append(locked_file(RUN_LOCK, fcntl.LOCK_EX, create=True))
        result = execute(config, lock_fds=tuple(held))
        print('APPLE_SCHEDULER=' + result)
        return 0
    except BlockingIOError:
        print('APPLE_SCHEDULER=BUSY')
        return 0
    except Exception:
        print('APPLE_SCHEDULER=FAILED')
        return 1
    finally:
        for fd in held:
            os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
