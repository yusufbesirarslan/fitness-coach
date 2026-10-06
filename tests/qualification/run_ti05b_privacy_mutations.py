"""Run final privacy non-vacuity mutations exclusively in a disposable copy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--copy', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--python', required=True)
args = parser.parse_args()
assert not (args.copy / '.git').exists(), 'Disposable copy required'
args.output.mkdir(parents=True, exist_ok=True)
paths = {
    'session': 'app/services/workout_session/service.py',
    'completion': 'app/services/workout_completion/service.py',
    'state': 'app/services/workout_state/__init__.py',
}
formats = {
    'session': '[WORKOUT_SESSION] rid=%s event=%s',
    'completion': '[WORKOUT_COMPLETION] rid=%s op=complete_workout entry=%s outcome=%s',
    'state': '[WORKOUT_STATE] anomaly rid=%s category=%s detail=%s',
}
# The deliberately seeded owner value is fixed by the integrated runtime gate.
mutations = [(f'{family}_user_id', paths[family], formats[family],
              formats[family] + ' user_id=1987654399') for family in paths]
mutations += [
    ('identity_relabelled_owner', paths['session'], formats['session'],
     formats['session'] + ' owner=1987654399'),
    ('raw_unlabelled_identity', paths['completion'], formats['completion'],
     formats['completion'] + ' 1987654399'),
    ('exception_message', paths['state'],
     '_log_anomaly(ANOMALY_RESOLUTION_ERROR, type(exc).__name__)',
     '_log_anomaly(ANOMALY_RESOLUTION_ERROR, str(exc))'),
]
originals = {p: (args.copy / p).read_bytes() for p in paths.values()}
results = []
for name, relative, old, new in mutations:
    path = args.copy / relative
    original = path.read_bytes()
    try:
        source = original.decode()
        assert source.count(old) == 1, (name, source.count(old))
        path.write_text(source.replace(old, new))
        proc = subprocess.run([
            args.python, '-m', 'pytest', '-p', 'tests.qualification.ti05_network_guard',
            '-p', 'no:cacheprovider', '-q', 'tests/qualification/test_ti05_privacy_gate.py',
        ], cwd=args.copy, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        (args.output / f'{name}.log').write_text(proc.stdout)
        detected = (proc.returncode == 1 and 'AssertionError' in proc.stdout
                    and '1 failed' in proc.stdout and 'ERROR collecting' not in proc.stdout)
        results.append(dict(mutation=name, detected=detected, exit=proc.returncode))
        print(name, detected, flush=True)
    finally:
        path.write_bytes(original)
        assert path.read_bytes() == original
        (args.output / 'privacy-mutations.json').write_text(json.dumps(results, indent=2) + '\n')
assert all((args.copy / p).read_bytes() == original for p, original in originals.items())
print('Restored byte-for-byte:', {p: hashlib.sha256(b).hexdigest() for p, b in originals.items()})
assert len(results) == 6 and all(r['detected'] for r in results)
