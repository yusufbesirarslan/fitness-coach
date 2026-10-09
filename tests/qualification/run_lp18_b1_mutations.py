"""Meaningful B1 mutants, each requires a detecting assertion, exact restoration."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
G='app/services/training_plan_replacement/generation.py'
S='app/services/training_plan_replacement/service.py'
R='app/blueprints/mobile_training.py'
T='tests/test_lp18_b1.py::'
A='tests/test_lp18_b1_architecture.py::'

def edit(old,new):
    def apply(source):
        assert source.count(old)==1, ('mutation anchor',old)
        return source.replace(old,new,1)
    return apply

MUTANTS=[
 ('M01',G,edit('if row.intent_fingerprint != request.fingerprint:','if False:'),T+'test_generation_replay_conflict_and_no_plan_write'),
 ('M02',G,edit("if row.status == 'SUCCEEDED':", "if row.status == 'DISABLED':"),T+'test_generation_replay_conflict_and_no_plan_write'),
 ('M03',G,edit('if row.lease_expires_at > now:','if False:'),T+'test_claim_precedes_provider_and_frozen_recovery'),
 ('M04',G,edit('row.lease_token != token','False'),T+'test_late_attempt_fenced_and_recovery_bounded'),
 ('M05',G,edit('row.lease_expires_at <= datetime.utcnow()','False'),T+'test_expired_lease_cannot_publish_without_takeover'),
 ('M06',G,edit('if row.attempt_count >= MAX_ATTEMPTS:','if row.attempt_count >= 999:'),T+'test_late_attempt_fenced_and_recovery_bounded'),
 ('M07',G,edit('row.attempt_count += 1','row.attempt_count += 0'),T+'test_claim_precedes_provider_and_frozen_recovery'),
 ('M08',G,edit("fields = frozen['features']", "fields = asdict(build_features(user, _required_session(user), request.preferences))"),T+'test_claim_precedes_provider_and_frozen_recovery'),
 ('M09',G,edit('if len(day[\'egzersizler\']) > mobile_training.MAX_EXERCISES_PER_DAY:','if False:'),T+'test_candidate_projection_bounds_and_no_execution'),
 ('M10',G,edit('if refund and row.quota_reserved:','if False:'),T+'test_quota_once_recovery_and_failure_refund'),
 ('M11',G,edit('plan.lineage_id, plan.mutation_version, snapshot_fingerprint(plan.plan_data)','binding.lineage_id, plan.mutation_version, snapshot_fingerprint(plan.plan_data)'),T+'test_publish_checks_full_binding[lineage_id]'),
 ('M12',G,edit('plan.lineage_id, plan.mutation_version, snapshot_fingerprint(plan.plan_data)','plan.lineage_id, binding.mutation_version, snapshot_fingerprint(plan.plan_data)'),T+'test_publish_checks_full_binding[mutation_version]'),
 ('M13',G,edit('plan.lineage_id, plan.mutation_version, snapshot_fingerprint(plan.plan_data)','plan.lineage_id, plan.mutation_version, binding.snapshot_digest'),T+'test_publish_checks_full_binding[plan_data]'),
 ('M14',S,edit('    db.session.flush()\n    return row.public_id','    db.session.flush()\n    db.session.commit()\n    return row.public_id'),T+'test_publication_rolls_back_atomically[operation]'),
 ('M15',R,edit("body['confirmed'] is not True", "not body['confirmed']"),T+'test_confirm_strict_schema[body2]'),
 ('M16',R,edit("set(body) != {'proposal_token', 'confirmed'}", "not {'proposal_token', 'confirmed'} <= set(body)"),T+'test_confirm_strict_schema[body3]'),
 ('M17',G,edit("json.loads(row.review_data)", "review_candidate(json.loads(Proposal.query.filter_by(public_id=row.proposal_public_id).one().candidate_plan_data), 8)"),T+'test_stable_review_replay_does_not_resolve_catalog'),
 ('M18',G,edit("if row.status == 'FAILED':", "if row.status == 'DISABLED':"),T+'test_canonical_provider_failure_is_durable_refunded'),
 ('M19',G,edit('        db.session.commit()\n        return admitted','        db.session.flush()\n        return admitted'),T+'test_claim_precedes_provider_and_frozen_recovery'),
 ('M20',R,edit("'reread_required': True", "'reread_required': False"),T+'test_http_success_confirm_replay_and_zero_provider'),
]

def main():
    evidence=ROOT/'docs/evidence/lp18-b1';evidence.mkdir(parents=True,exist_ok=True)
    selected=set(sys.argv[1:])
    previous=json.loads((evidence/'mutations.json').read_text()) if selected else []
    outcomes=[o for o in previous if o['mutant'] not in selected]
    for index,(name,path,mutate,test) in enumerate(MUTANTS):
        if selected and name not in selected:continue
        target=ROOT/path;original=target.read_bytes();stat=target.stat()
        try:
            target.write_text(mutate(original.decode()));os.utime(target,(time.time()+index+60,time.time()+index+60))
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
            proc=subprocess.run([sys.executable,'-B','-m','pytest','-q',test,'--disable-warnings','--tb=short'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=45)
            output=proc.stdout+proc.stderr
            killed=(proc.returncode==1 and '1 failed' in output and ('AssertionError' in output or 'DID NOT RAISE' in output or 'E   assert ' in output) and 'ERROR collecting' not in output)
            outcomes.append(dict(mutant=name,detector=test,returncode=proc.returncode,killed_by_assertion=killed))
            print(name,'KILLED' if killed else 'NOT QUALIFIED',flush=True)
            if not killed:
                print(output[-1200:],flush=True)
        finally:
            target.write_bytes(original);os.utime(target,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    (evidence/'mutations.json').write_text(json.dumps(sorted(outcomes,key=lambda o:o['mutant']),indent=2)+'\n')
    assert all(o['killed_by_assertion'] for o in outcomes), 'unqualified mutations'

if __name__=='__main__':main()
