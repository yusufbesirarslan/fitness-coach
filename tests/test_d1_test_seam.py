"""Opt-in isolation, restoration and collected fixture dependency regressions."""
import ast
import inspect
import json
from pathlib import Path
import subprocess
import sys

import pytest

from consent_projection_support import assert_default_deny_at_sdk

MODULES = (
    'test_ai_provider_call.py', 'test_pa06_privacy.py',
    'test_pa06_pending_provenance.py', 'test_mobile_coach_api.py',
    'test_mobile_training_generation_api.py',
)


def test_01_real_gate_before_explicit_allow(app, make_user, monkeypatch):
    assert_default_deny_at_sdk(app, make_user, monkeypatch, 'openai')


def test_02_explicit_allow_is_only_layer_isolation(synthetic_ai_layer, make_user):
    from unittest.mock import Mock
    from app.models import AIConsentState, AIConsentEvent
    from app.services import ai_consent as consent, ai_provider_call as calls, ai_spend_guard
    owner = make_user('isolated_mock_owner')
    other = make_user('ungranted_other_owner')
    sdk = Mock(return_value='MOCK_REPLY')
    with ai_spend_guard.subject_scope(owner.id):
        with calls.admit(feature='training_plan', provider='openai',
                         payload={'model': 'synthetic', 'max_tokens': 10,
                                  'messages': [{'role': 'user', 'content': 'OWN'}]},
                         gate=False, charge=False) as admission:
            assert admission.create(sdk) == 'MOCK_REPLY'
    sdk.assert_called_once()
    assert consent.memory_available(owner.id) is False
    assert consent.memory_available(other.id) is False
    # Synthetic ALLOW is a per-test mock, never a durable account grant.
    assert AIConsentState.query.count() == AIConsentEvent.query.count() == 0


def test_02b_memory_requires_separate_explicit_fixture(synthetic_ai_memory_layer, app, make_user):
    from app.models import AIConsentState, AIConsentEvent
    from app.services import ai_consent as consent
    owner = make_user('isolated_mock_memory')
    assert app.config['AI_MEMORY_ENABLED'] is True
    assert consent.memory_available(owner.id) is True
    assert AIConsentState.query.count() == AIConsentEvent.query.count() == 0


def test_03_real_gate_restored_for_both_accounts(app, make_user, monkeypatch):
    assert_default_deny_at_sdk(app, make_user, monkeypatch, 'openai')


def test_authorization_helper_has_only_named_opt_in_callers():
    import conftest
    import consent_projection_support as support
    assert inspect.signature(support.allow_synthetic_authority).parameters['memory'].default is False
    for name in ('synthetic_ai_layer', 'synthetic_ai_memory_layer'):
        fixture = getattr(conftest, name)._fixture_function_marker
        assert fixture.scope == 'function' and fixture.autouse is False
    callers = []
    for path in Path(__file__).parent.rglob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == 'allow_synthetic_authority':
                    callers.append(path.name)
    assert sorted(callers) == ['conftest.py', 'conftest.py']


def test_collected_fixture_dependencies_cannot_supply_implicit_allow(tmp_path):
    """Inspect pytest's actual closure, including imported/indirect fixtures."""
    root = Path(__file__).parents[1]
    output = tmp_path / 'fixture-audit.json'
    code = r'''
import inspect, json, pytest, sys
class Audit:
    def pytest_collection_modifyitems(self, items):
        rows = []
        for item in items:
            # own_markers excludes module/class inheritance.
            direct = set(inspect.signature(item.obj).parameters)
            for marker in item.own_markers:
                if marker.name == 'usefixtures':
                    direct.update(marker.args)
            closure = set(item.fixturenames)
            allows = closure & {'synthetic_ai_layer', 'synthetic_ai_memory_layer'}
            assert allows <= direct, (item.nodeid, sorted(closure), sorted(direct))
            if 'test_d1_no_implicit_authorization' in item.name:
                assert not allows, item.nodeid
            rows.append({'nodeid': item.nodeid, 'allows': sorted(allows)})
        with open(sys.argv[1], 'w') as out:
            json.dump(rows, out)
raise SystemExit(pytest.main(['--collect-only', '-q', *sys.argv[2:]], plugins=[Audit()]))
'''
    result = subprocess.run([sys.executable, '-c', code, str(output),
                             *('tests/' + name for name in MODULES)],
                            cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = json.loads(output.read_text())
    assert len([row for row in rows if 'test_d1_no_implicit_authorization' in row['nodeid']]) == 10
    assert any(row['allows'] for row in rows)
