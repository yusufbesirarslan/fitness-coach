import ast
from pathlib import Path
from app.cli import _user_child_models
from app.models import TrainingPlanReplacementGenerationOperation
ROOT=Path(__file__).resolve().parents[1]


def test_generation_never_constructs_or_replaces_training_plan():
    source=(ROOT/'app/services/training_plan_replacement/generation.py').read_text()
    assert 'TrainingPlan(' not in source and 'replace_training_plan' not in source
    assert 'generate_training_plan_candidate' in source and 'frozen_features=features' in source
    assert 'reserve_ai_quota_in_transaction' in source
    assert 'stage_proposal_in_transaction' in source


def test_stage_primitive_commit_free_and_wrapper_preserved():
    tree=ast.parse((ROOT/'app/services/training_plan_replacement/service.py').read_text())
    core=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='stage_proposal_in_transaction')
    assert '.commit(' not in ast.unparse(core) and '.rollback(' not in ast.unparse(core)
    wrapper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='stage_proposal')
    assert 'stage_proposal_in_transaction' in ast.unparse(wrapper) and '.commit(' in ast.unparse(wrapper)


def test_confirm_delegates_and_contains_no_provider():
    tree=ast.parse((ROOT/'app/blueprints/mobile_training.py').read_text())
    confirm=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='confirm_replacement_proposal')
    source=ast.unparse(confirm)
    assert 'authority.confirm_replacement(g.mobile_user.id' in source
    assert '_heavy_chat' not in source and 'generate_proposal' not in source and '.commit(' not in source
    assert 'require_mobile_auth' in source and 'parse_idempotency_key' in source


def test_account_erasure_and_no_sensitive_logging():
    assert TrainingPlanReplacementGenerationOperation in _user_child_models()
    source=(ROOT/'app/services/training_plan_replacement/generation.py').read_text()
    assert 'logger' not in source
    assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='print' for n in ast.walk(ast.parse(source)))
