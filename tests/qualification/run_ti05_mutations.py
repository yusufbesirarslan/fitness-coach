"""Qualification-only non-vacuity runner. Operates exclusively on disposable copies."""
import argparse, hashlib, json, os, subprocess
from pathlib import Path
parser = argparse.ArgumentParser(description='Mutate disposable TI-05 copies; never pass working repositories.')
parser.add_argument('--mobile-copy', type=Path, required=True)
parser.add_argument('--backend-copy', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--flutter', required=True)
parser.add_argument('--python', required=True)
args = parser.parse_args()
M, B = args.mobile_copy, args.backend_copy
assert not (M / '.git').exists() and not (B / '.git').exists(), 'Only disposable copies are allowed'
args.output.mkdir(parents=True, exist_ok=True)
W='lib/features/workout/'
C=W+'presentation/state/training_insight_controller.dart'
D=W+'data/training_insight_api_dto.dart'
R=W+'data/live_workout_session_repository.dart'
completion='test/features/workout/presentation/training_insight_completion_test.dart'
controller='test/features/workout/presentation/state/training_insight_controller_test.dart'
card='test/features/workout/presentation/training_insight_card_test.dart'
repository='test/features/workout/data/live_training_insight_repository_test.dart'
mutations=[
 ('p1_without_p0',M,R,[('_insightsEnabled && _contextEnabled && _currentCapabilityEpoch','_insightsEnabled && _currentCapabilityEpoch'),('(insights && !enabled) ||','false ||')],repository,'P1 without P0 is invalid'),
 ('await_insight_before_completion',M,W+'presentation/active_workout_screen.dart',[("import 'dart:async';", "import 'dart:async';\nimport 'package:axisai/features/workout/domain/training_insight.dart';"),('    await _controller.complete(image);', "    await widget.trainingInsightRepository?.read(TrainingInsightSubject(sessionRef: 'sess-1', checkpointRevision: 0, exercises: const []));\n    await _controller.complete(image);")],completion,'completion never awaits the insight'),
 ('account_response_after_switch',M,C,[('        _authRequestContext.currentAuthContext?.epoch != epoch','        false')],controller,'account switch fences'),
 ('logging_interval_as_rest',M,W+'presentation/training_insight_presentation.dart',[('l10n.trainingInsightMetricLoggingInterval', "'Rest time'")],card,'the logging interval is never labelled as rest'),
 ('unknown_kind_accepted',M,D,[("    throw FormatException('Unsupported training insight $field.');", "    if (field == 'kind') return TrainingInsightKind.performanceImproved as T;\n    throw FormatException('Unsupported training insight $field.');")],'test/features/workout/data/training_insight_api_dto_test.dart','unknown state, kind and title'),
 ('null_action_synthesized',M,D,[('    if (raw == null) return null;', '    if (raw == null) return TrainingInsightAction.holdCurrentApproach;')],card,'a null action renders no action'),
 ('notes_influence_insight',B,'app/services/training_intelligence/__init__.py',[('    return payload', "    from app.models import ExerciseNote\n    note = ExerciseNote.query.filter_by(user_id=user_id).first()\n    if note is not None and note.text:\n        payload['training_insight']['title_key'] = 'training_insight.performance_declined'\n    return payload")],'tests/test_ti03_api.py::test_persistent_exercise_notes_are_never_read',None),
 ('plan_writer_reachable',B,'app/services/training_intelligence/__init__.py',[("from app.models import WORKOUT_SESSION_COMPLETED", "from app.models import WORKOUT_SESSION_COMPLETED\nfrom app.services.plan_mutation import service as plan_writer")],'tests/test_ti03_architecture.py::test_no_forbidden_authority_is_reachable',None),
 ('local_kill_switch_ignored',M,'lib/app/composition/app_composition.dart',[('trainingInsightRepository: insightsRollout.enabled','trainingInsightRepository: true')],'test/app/app_composition_test.dart','Training Insight composition requires native auth'),
 ('capability_off_stale_card',M,C,[('    if (_state is! TrainingInsightHidden) _set(const TrainingInsightHidden());','    // MUTATION: retain stale loaded card')],completion,'capability off clears an already rendered insight'),
]
results=[]
for name,root,relative,edits,test,plain in mutations:
    path=root/relative
    original=path.read_bytes()
    try:
        source=original.decode()
        for old,new in edits:
            assert source.count(old)==1,(name,old,source.count(old))
            source=source.replace(old,new)
        path.write_text(source)
        if root==M:
            command=[args.flutter,'test','--no-pub',test,'--plain-name',plain,'--reporter','expanded']
        else:
            command=[args.python,'-m','pytest','-p','tests.qualification.ti05_network_guard','-p','no:cacheprovider','-q',test]
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        proc=subprocess.run(command,cwd=root,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        (args.output / f'mutation-{name}.log').write_text(proc.stdout)
        # Compilation/loading failures are not valid mutation detection.
        detected=proc.returncode==1 and ('Expected:' in proc.stdout or 'AssertionError' in proc.stdout) and 'Compilation failed' not in proc.stdout
        results.append({'mutation':name,'detected':detected,'exit':proc.returncode,'test':test,'plain_name':plain})
        print(name,detected,flush=True)
    finally:
        path.write_bytes(original)
        assert hashlib.sha256(path.read_bytes()).digest()==hashlib.sha256(original).digest()
        (args.output / 'mutations.json').write_text(json.dumps(results,indent=2)+'\n')
assert len(results)==10 and all(r['detected'] for r in results)
