"""Run deliberate LP15-D source mutations; always restore exact original bytes.

Usage: python scripts/validate_lp15d_mutations.py
Each mutant runs a focused behavioral assertion in a fresh interpreter. Tests
must fail by assertion (not collection/setup failure); logs stay in a temporary
folder for review. This never contacts a remote service or database.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TEST = 'tests/test_lp15d_menu_confirmation.py::'
MUTATIONS = [
    ('remove_owner_binding', 'app/services/mobile_menu.py', [
        ('tokens.verify_payload(secret, PROOF_DOMAIN, user_id, token,',
         'tokens.verify_payload(secret, PROOF_DOMAIN, 1, token,')],
     'test_owner_binding_and_independent_keys'),
    ('trust_client_nutrition', 'app/services/mobile_log_food/menu_confirmation.py', [
        ('set(data) != {"confirmation_token", "quantity", "slot", "confirmed"}',
         'set(data) - {"nutrition"} != {"confirmation_token", "quantity", "slot", "confirmed"}'),
        ('for key, value in proof["nutrition"].items()})\n        # Only',
         'for key, value in data.get("nutrition", proof["nutrition"]).items()})\n        # Only')],
     'test_injection_rejected[nutrition]'),
    ('expiry_before_replay', 'app/services/mobile_log_food/service.py', [
        ('    existing = _existing_or_conflict(user_id, key, fingerprint)',
         '    if isinstance(command, MenuConfirmedLogFoodCommand):\n'
         '        from app.services.mobile_menu import enforce_item_proof_expiry\n'
         '        enforce_item_proof_expiry(command.expires_at)\n'
         '    existing = _existing_or_conflict(user_id, key, fingerprint)')],
     'test_lost_response_expired_committed_replay_and_new_write'),
    ('remove_fingerprint_provenance', 'app/services/mobile_log_food/fingerprint.py', [
        ('"description": command.description, "source": "menu_estimated",',
         '"description": command.description,')],
     'test_fingerprint_has_fixed_provenance_and_excludes_expiry'),
    ('bypass_idempotency_conflict', 'app/services/mobile_log_food/service.py', [
        ('raise IdempotencyConflict', 'pass')],
     'test_semantic_provenance_conflicts'),
    ('relabel_source_as_manual', 'app/services/mobile_log_food/service.py', [
        ('source = "menu_estimated"', 'source = "manual"')],
     'test_success_one_canonical_row_and_only_signed_nutrition'),
    ('permit_confirm_false', 'app/services/mobile_log_food/menu_confirmation.py', [
        ('data["confirmed"] is not True', 'data["confirmed"] not in (True, False)')],
     'test_explicit_literal_confirmation[False]'),
    ('call_provider_on_confirmation', 'app/services/mobile_log_food/service.py', [
        ('        source = "menu_estimated"',
         '        resolve_provider_food("fatsecret", "mutant", "mutant", command.quantity)\n'
         '        source = "menu_estimated"')],
     'test_no_remote_provider_ai_or_cache'),
]


def main():
    logs = Path(tempfile.mkdtemp(prefix='lp15d-mutations-'))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for name, relative, substitutions, test in MUTATIONS:
        target = ROOT / relative
        original = target.read_bytes()
        try:
            source = original.decode()
            for before, after in substitutions:
                if before not in source:
                    raise RuntimeError(f'{name}: mutation anchor absent')
                source = source.replace(before, after)
            target.write_text(source)
            result = subprocess.run(
                [sys.executable, '-B', '-m', 'pytest', '-q', '-o', 'addopts=', TEST+test],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            (logs / (name + '.log')).write_bytes(result.stdout)
            output = result.stdout.decode(errors='replace')
            if result.returncode != 1 or 'FAILED ' not in output or '\nERROR tests/' in output:
                raise RuntimeError(f'{name}: mutant was not detected by assertion; see {logs}')
            print(f'{name}: detected', flush=True)
        finally:
            target.write_bytes(original)
            assert target.read_bytes() == original
    print(f'8/8 detected and restored; logs={logs}')


if __name__ == '__main__':
    main()
