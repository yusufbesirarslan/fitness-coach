"""Run one named detecting test per meaningful mutant; restore exact source.

Local only. Run with no simultaneous tests/editors in this isolated worktree.
An assertion failure is required; import/collection failures and timeouts do not
qualify. PG mutants require the explicitly disposable PG qualification database.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
EVIDENCE=ROOT/'docs/evidence/lp18-b0'
WS='app/services/workout_session/service.py'
PR='app/services/plan_replacement.py'
S='app/services/training_plan_replacement/service.py'
OWNER='app/services/plan_owner_lock.py'
A='tests/test_lp18_b0_architecture.py::'
T='tests/test_lp18_b0.py::'
PG='tests/test_lp18_b0_pg.py::'


def edit(old,new):
    def mutate(source):
        assert source.count(old)==1, ('ambiguous mutation anchor', old)
        return source.replace(old,new,1)
    return mutate


def drop_fingerprint(source):
    a=source.index('def intent_fingerprint(');b=source.index('\n\ndef stage_proposal',a)
    return source[:a]+'def intent_fingerprint(proposal):\n    return "0" * 64\n'+source[b:]


def invert_coach(source):
    a=source.index('                coach_rows = (');b=source.index('                def final_guard',a)
    block=source[a:b]
    source=source[:a]+'                coach_rows = []\n'+source[b:]
    needle='                    status = "APPLIED"'
    return source.replace(needle, ''.join('    '+line for line in block.splitlines(keepends=True))+needle,1)


def provider_inside_transaction(source):
    # Executable synthetic provider adapter: no external network or AWS.
    mutated=edit('        lock_plan_session_transition(user_id)\n',
                 '        lock_plan_session_transition(user_id)\n        call_provider()\n')(source)
    return mutated+'\n\ndef call_provider():\n    assert db.session().in_transaction()\n    return None\n'


MUTANTS=[
 ('M1',WS,edit('        lock_plan_session_transition(user_id)\n',''),PG+'test_a_start_wins_active_refuses'),
 ('M2',WS,edit('        lock_plan_session_transition(user_id)\n        snapshot = compute_plan_snapshot(user_id, day)',
               '        snapshot = compute_plan_snapshot(user_id, day)\n        lock_plan_session_transition(user_id)'),PG+'test_b_replacement_wins_start_snapshots_new'),
 ('M3',S,edit('    if db.session.query(WorkoutSession.id).filter_by(', '    if False and db.session.query(WorkoutSession.id).filter_by('),T+'test_any_active_even_previous_day_refuses_and_replays'),
 ('M4',WS,edit('        lock_plan_session_transition(user_id)\n', '        lock_plan_session_transition(user_id)\n        from app.services.plan_owner_lock import lock_plan_owner\n        lock_plan_owner(user_id)\n'),A+'test_lock_order_and_namespace_identity'),
 ('M5',PR,edit('    result = ReplacementResult(\n        lineage_id=replacement.lineage_id,\n        mutation_version=replacement.mutation_version,\n    )\n    return result',
                  '    result = ReplacementResult(\n        lineage_id=replacement.lineage_id,\n        mutation_version=replacement.mutation_version,\n    )\n    db.session.commit()\n    return result'),T+'test_receipt_failure_rolls_back_replacement'),
 ('M6',S,drop_fingerprint,T+'test_fingerprint_binds_every_frozen_dimension'),
 ('M7',S,edit('            if receipt.proposal_public_id != proposal_public_id:', '            if False:'),T+'test_different_intent_same_key_conflicts'),
 ('M8',S,edit('            db.session.rollback()\n        else:', '''            db.session.rollback()
            from app.services.today_facts import get_active_plan
            current = get_active_plan(user_id)
            replace_training_plan_in_transaction(
                user_id, PlanExpectation(False, current.lineage_id, current.mutation_version),
                plan_data=current.plan_data, score=current.score)
            db.session.commit()
        else:'''),T+'test_success_and_replay_survive_restart_plan_and_proposal_deletion'),
 ('M9',PR,edit('        if snapshot_digest is not None:', '        if False:'),T+'test_each_stale_dimension_refuses[plan_data]'),
 ('M10',PR,edit('        if current.mutation_version != expectation.mutation_version:', '        if False:'),T+'test_each_stale_dimension_refuses[mutation_version]'),
 ('M11',PR,edit('        if current.lineage_id != expectation.lineage_id:', '        if False:'),T+'test_each_stale_dimension_refuses[lineage_id]'),
 ('M12',S,edit('    if coach_rows:', '    if False:'),T+'test_pending_coach_refuses_without_resolving'),
 ('M13',S,invert_coach,A+'test_lock_order_and_namespace_identity'),
 ('M14',S,provider_inside_transaction,A+'test_no_provider_dependency_or_io_in_critical_section'),
 ('M15',S,edit('            elif proposal.expires_at <= datetime.utcnow():', '            elif False:'),T+'test_expired_proposal_refuses'),
 ('M16',S,edit('            if applied is not None:', '            if False:'),T+'test_expiry_and_consumption_closed_states'),
 ('M17',OWNER,edit('.with_for_update(key_share=True)', '.with_for_update()'),A+'test_lock_order_and_namespace_identity'),
]


def main():
    assert os.environ.get('FITX_PG_CONCURRENCY_TEST')=='1', 'real PG mutants required'
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    outcomes=[]
    for name,path,mutate,test in MUTANTS:
        target=ROOT/path;original=target.read_bytes()
        try:
            target.write_text(mutate(original.decode()))
            env=dict(os.environ);env['PYTHONDONTWRITEBYTECODE']='1'
            run=subprocess.run([sys.executable,'-B','-m','pytest','-q',test,'--disable-warnings','--tb=short'],
                cwd=ROOT,env=env,capture_output=True,text=True,timeout=45)
            output=run.stdout+run.stderr
            killed=(run.returncode==1 and ('AssertionError' in output or 'DID NOT RAISE' in output or 'E   assert ' in output)
                    and '1 failed' in output and 'ERROR collecting' not in output)
            # Bounded evidence only: failure reprs may contain private tokens.
            (EVIDENCE/(name+'.txt')).write_text(f'detector={test}\nexit={run.returncode}\nassertion_detected={killed}\n')
            outcomes.append({'mutant':name,'detector':test,'returncode':run.returncode,
                             'killed_by_assertion':killed})
            print(name, 'KILLED' if killed else 'NOT QUALIFIED',flush=True)
        finally:
            target.write_bytes(original)
    (EVIDENCE/'mutations.json').write_text(json.dumps(outcomes,indent=2)+'\n')
    assert all(row['killed_by_assertion'] for row in outcomes),outcomes


if __name__=='__main__':main()
