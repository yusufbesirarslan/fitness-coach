"""Source graph guards supplement (never substitute for) PostgreSQL proofs."""
import ast
from pathlib import Path
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from app.models import User
from app.services import plan_replacement as replacement
from app.services import plan_session_transition as transition

ROOT = Path(__file__).resolve().parents[1]


def source(path): return (ROOT/path).read_text()

def test_one_mutation_and_no_internal_commit():
    module = ast.parse(source('app/services/plan_replacement.py'))
    core = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'replace_training_plan_in_transaction')
    wrapper = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'replace_training_plan')
    text = ast.unparse(core)
    assert '.commit(' not in text and '.rollback(' not in text
    assert 'replace_training_plan_in_transaction' in ast.unparse(wrapper)
    assert source('app/services/plan_replacement.py').count('replacement = TrainingPlan(') == 1
    native = source('app/services/training_plan_replacement/service.py')
    assert 'replace_training_plan(' not in native
    assert native.index('db.session.add(receipt)') < native.rindex('db.session.commit()')


def test_no_provider_dependency_or_io_in_critical_section():
    module = ast.parse(source('app/services/training_plan_replacement/service.py'))
    imports = [n.module for n in ast.walk(module) if isinstance(n, ast.ImportFrom)]
    assert not any(any(word in (name or '') for word in ('provider', 'training_generation', 'weekly_checkin', 'mobile_progress')) for name in imports)
    assert not any(isinstance(n, ast.Import) and any(a.name.startswith(('requests', 'httpx', 'boto3', 'openai')) for a in n.names) for n in ast.walk(module))
    confirm = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'confirm_replacement')
    allowed = {'_digest', 'lock_plan_session_transition', 'ConfirmationConflict', 'ProposalUnavailable',
               'intent_fingerprint', '_result', '_deliver', 'candidate_fingerprint',
               'PlanExpectation', 'replace_training_plan_in_transaction', 'ConfirmationResult',
               'ReplacementRefusal', 'Receipt', '_guard', 'ValueError', 'isinstance', 'len'}
    calls = {n.func.id for n in ast.walk(confirm) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert calls <= allowed


def test_lock_order_and_namespace_identity():
    assert transition.TRANSITION_LOCK_NAMESPACE == 0x41584918
    assert 'pg_advisory_xact_lock(:namespace, :owner)' in source('app/services/plan_session_transition.py')
    native = source('app/services/training_plan_replacement/service.py')
    assert native.index('lock_plan_session_transition(user_id)') < native.index('.with_for_update().first()')
    assert native.index('.with_for_update().all()') < native.index('replacement = replace_training_plan_in_transaction(')
    assert 'with_for_update' not in native[native.index('def _guard'):native.index('def confirm_replacement')]
    start = source('app/services/workout_session/service.py')
    start = start[start.index('def start_session'):start.index('def get_current_session')]
    assert start.index('lock_plan_session_transition(user_id)') < start.index('compute_plan_snapshot(user_id, day)') < start.index('lock_completion_day(user_id, day)')
    assert 'lock_plan_owner' not in start and 'training_plan_replacement' not in start
    lock = source('app/services/plan_owner_lock.py')
    assert '.with_for_update(key_share=True)' in lock
    sql = str(sa.select(User.id).with_for_update(key_share=True).compile(dialect=postgresql.dialect()))
    assert sql.endswith('FOR NO KEY UPDATE')


def test_no_native_replacement_routes_or_sensitive_logs():
    for path in (ROOT/'app/blueprints').rglob('*.py'):
        assert '/replacement-proposals' not in path.read_text()
        assert '/replacement/confirm' not in path.read_text()
    text = source('app/services/training_plan_replacement/service.py')
    assert 'logger' not in text
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'print' for n in ast.walk(ast.parse(text)))
    assert 'WeeklyCheckIn' not in text and 'mobile_progress' not in text
