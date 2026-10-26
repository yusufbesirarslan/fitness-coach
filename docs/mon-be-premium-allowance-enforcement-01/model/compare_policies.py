"""Reproduce FINOPS, extend only candidate policies/exposure; no live service.

Run: python docs/mon-be-premium-allowance-enforcement-01/model/compare_policies.py
The original FINOPS source and all its SHA-pinned inputs remain read-only.
P90/P99 names are scenario profiles, NOT observed population percentiles.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parents[1]
BASE = Path.home() / 'axisai-finops-pricing-unit-economics-01'
MODEL = BASE / 'model/pricing_ue_model.py'
raw = MODEL.read_text()
# Original definitions only: suppress its output-writing main, preserve path
# resolution and all original _load SHA checks. No financial formula fork.
n = {'__file__': str(MODEL), '__name__': 'finops_definitions'}
exec(compile(raw.split('# ---------------------------------------------------------------- outputs')[0], str(MODEL), 'exec'), n)

P = n['POL']['premium']
CANDIDATES = {
    'P_LAUNCH': {'coach': 20, 'training_generation': 2, 'nutrition_generation': 2, 'pump_check': 3, 'qr_menu': 1},
    'P_30_SAFE': {'coach': 3, 'training_generation': 1, 'nutrition_generation': 1, 'pump_check': 1, 'qr_menu': 1},
    'P_ROUTING_FUTURE': {'coach': 12, 'training_generation': 2, 'nutrition_generation': 2, 'pump_check': 3, 'qr_menu': 1},
}
for name, units in CANDIDATES.items():
    P[name] = {'period': 'WEEKLY', 'units': units}

baseline = json.loads((BASE/'out/results.json').read_text())
assert abs(n['full_use_cost']('P_LAUNCH', n['SCEN']['A_CURRENT']) - baseline['full_use']['A_CURRENT']['P_LAUNCH']) < .00005
assert round(n['proceeds_try'](399.99, .15), 2) == 269.16
# Actual export row is after taxes. Normalize that evidence for the standard
# commission counterfactual, rather than applying 30% to tax-inclusive retail.
export = BASE / n['SRC']['asc_export']['path']
assert hashlib.sha256(export.read_bytes()).hexdigest() == n['SRC']['asc_export']['sha256']

def proceeds(price, months, commission, fx=49.654, refunds=.03):
    # Only monthly 399.99/269.16 is exported. Annual proportional extrapolation
    # and 30% counterfactual are estimates; neither is an ASC settlement record.
    after_tax_before_commission = 269.16 / .85 * (price / 399.99)
    return after_tax_before_commission * (1-commission) / fx * (1-refunds) / months


def full_use(policy, scenario, weeks, profile='MEDIAN', pump_worst=True):
    sc = n['SCEN'][scenario]
    units = CANDIDATES[policy]
    coach = n['coach_turn_cost'](n['PROF'][profile]['coach_mix'], sc)
    pump = n['feature_unit']('pump_check', sc)
    if pump_worst:
        pump = max(pump, n['feature_unit']('pump_comparison', sc))
    per_week = (units['coach']*coach + units['training_generation']*n['feature_unit']('training_plan', sc)
                + units['nutrition_generation']*n['feature_unit']('nutrition_plan', sc)
                + units['pump_check']*pump + units['qr_menu']*n['feature_unit']('qr_menu', sc))
    # Exempt usage is explicitly budgeted, not falsely bounded by allowance.
    exempt = n['active_cost'](profile, sc, policy, detail=True)
    exempt = (exempt['workout_proof'] + exempt['lookup']) * weeks / n['W2M']
    return per_week*weeks + exempt


def failure_release_cost(scenario, weeks):
    """One UNKNOWN/billed released action per feature/period, mean case mix.

    Failed calls can be worse: physical ceilings below cover that separate tail.
    This is an explicit sensitivity, not an approved production release policy.
    """
    sc = n['SCEN'][scenario]
    return weeks * (n['coach_turn_cost'](n['PROF']['MEDIAN']['coach_mix'],sc)
        + n['feature_unit']('training_plan',sc) + n['feature_unit']('nutrition_plan',sc)
        + max(n['feature_unit']('pump_check',sc),n['feature_unit']('pump_comparison',sc))
        + n['feature_unit']('qr_menu',sc))

rows = {}
for policy in CANDIDATES:
    rows[policy] = {}
    for scenario in ['A_CURRENT', 'A_STRESS', 'B_TARGET', 'C_TARGET_CACHE', 'D_STRESS']:
        sc = n['SCEN'][scenario]
        expected = n['blended'](sc, policy)
        profile = {k: n['active_cost'](k, sc, policy) for k in n['PROF']}
        full = {str(w): full_use(policy, scenario, w) for w in [4, n['W2M'], 5, 6]}
        heavy_full = {k: full_use(policy, scenario, 5, k) for k in ['P90', 'P99', 'EXTREME']}
        margins = {}
        full_margins = {}
        for plan, price, months, active in [('monthly',399.99,1,.8), ('annual',3999.99,12,.7)]:
            for commission in [.15,.30]:
                rev = proceeds(price, months, commission)
                cost = active*(expected+.05)
                margins[f'{plan}_{commission}'] = {'proceeds_month':rev,'ai_month':active*expected,
                    'non_ai_month':active*.05,'contribution_month':rev-cost,'margin_pct':100*(rev-cost)/rev,
                    'contribution_year':12*(rev-cost)}
                for weeks, ai_cost in full.items():
                    full_margins[f'{plan}_{commission}_{weeks}'] = {
                        'ai_month': ai_cost, 'non_ai_month': .05,
                        'contribution_month': rev-ai_cost-.05,
                        'contribution_year': 12*(rev-ai_cost-.05),
                        'margin_pct': 100*(rev-ai_cost-.05)/rev}
        sensitivity = []
        for fx in [45,49.654,55,60,65]:
            for refund in [.01,.03,.08]:
                for plan,price,months in [('monthly',399.99,1),('annual',3999.99,12)]:
                    rev = proceeds(price,months,.30,fx,refund)
                    # All customers active; 5 resets, P99 token/tool mix and
                    # +50% model uncertainty; high non-AI variable cost.
                    cost = heavy_full['P99']*1.5 + .20
                    sensitivity.append({'fx':fx,'refund':refund,'plan':plan,'proceeds_month':rev,
                        'full_p99_5week_uncertainty_cost':cost,'contribution':rev-cost,
                        'margin_pct':100*(rev-cost)/rev})
        rows[policy][scenario] = {'expected_ai_active_month':expected, 'profiles_ai_active_month':profile,
            'full_ai_by_weeks':full, 'full_5weeks_heavy_mix':heavy_full,
            'margins':margins,'full_utilization_margins':full_margins,
            'additional_ai_one_billed_release_per_feature_week_5weeks':failure_release_cost(scenario,5),
            'retry_fallback_token_assumptions': sc, 'sensitivity':sensitivity}

# Deterministic configured per-attempt ceilings (not expected model usage).
# Read literal constants without importing the Flask app or providers.
import ast
execution = ast.parse((SOURCE/'app/services/ai_allowances/execution.py').read_text())
limits = next(ast.literal_eval(node.value) for node in execution.body
              if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ATTEMPT_LIMITS' for t in node.targets))
budget = ast.parse((SOURCE/'app/services/ai_input_budget.py').read_text())
def literal(module, name):
    return next(ast.literal_eval(node.value) for node in module.body if isinstance(node,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id==name for t in node.targets))
inputs, outputs = literal(budget, '_INPUT_DEFAULTS'), literal(budget, '_OUTPUT_DEFAULTS')
physical = {'coach': ['coach','nutrition','vision'], 'training_generation':['training_plan'],
            'nutrition_generation':['nutrition_plan'], 'pump_check':['vision'],
            'qr_menu':['menu_extract','menu_ocr','nutrition']}
# Runtime budget literals are inspected separately in the report; the upper
# estimate conservatively uses the largest per-case physical heavy call from
# the pinned FINOPS corpus. This is a CORPUS bound, not provider hard maximum.
worst = {}
for family,op in [('coach','coach_turn'),('training_generation','training_plan'),
                  ('nutrition_generation','nutrition_plan'),('pump_check','pump_comparison'),('qr_menu','qr_menu')]:
    call_max = max(n['call_cost'](call,n['SCEN']['A_CURRENT'])
        for case in n['OPS'][op]['cases'].values() for call in case['calls'])
    worst[family] = {'physical_attempt_limit':limits[family], 'max_corpus_call_usd':call_max,
                     'corpus_action_ceiling_usd':call_max*limits[family]}
    # Conservative dollar exposure at default source input/output budgets and
    # most expensive current routing class. These are not measured payloads,
    # not deployment configuration, and not promised monthly unit profitability.
    default_call = max(n['call_cost']((cls,inputs[f],outputs[f]),n['SCEN']['A_CURRENT'])
                       for cls in ['heavy','light'] for f in physical[family])
    worst[family].update(default_budget_call_usd=default_call,
        default_budget_action_usd=default_call*limits[family],
        native_training_note='Native Training retains original bounded completions/recovery; generic ceiling is not its attempt authority.' if family=='training_generation' else None)
result = {'units':CANDIDATES,'baseline_reconciliation':{
    'finops_model_sha256':hashlib.sha256(MODEL.read_bytes()).hexdigest(),
    'original_full_use_launch':baseline['full_use']['A_CURRENT']['P_LAUNCH'],
    'original_cost_includes_non_ai_and_inactivity':baseline['subscriber']['A_CURRENT']['P_LAUNCH']['monthly'],
    'original_profile_names_are_synthetic':True,'sbp_status':'NOT SUBMITTED / NOT VERIFIED (founder brief controls)',
    'annual_price':3999.99,'annual_proceeds_status':'ESTIMATED; export required'},
    'scenarios':rows,'physical_corpus_bounds':worst,
    'proceeds':{f'{plan}_{c}':proceeds(price,months,c) for plan,price,months in
       [('monthly',399.99,1),('annual',3999.99,12)] for c in [.15,.3]}}
(ROOT/'out').mkdir(exist_ok=True)
(ROOT/'out/policy_comparison.json').write_text(json.dumps(result,indent=2)+'\n')
for policy in CANDIDATES:
    r=rows[policy]['A_CURRENT']
    print(policy,CANDIDATES[policy], 'active expected',round(r['expected_ai_active_month'],4),
        'full4/avg/5/6', {k:round(v,4) for k,v in r['full_ai_by_weeks'].items()},
        'P90/P99',round(r['profiles_ai_active_month']['P90'],4),round(r['profiles_ai_active_month']['P99'],4),
        'margins15/30',round(r['margins']['monthly_0.15']['margin_pct'],1),round(r['margins']['monthly_0.3']['margin_pct'],1))
print('Original model and export reconciliation PASS; no original file changed')
