"""Scoped, offline requalification. No account, provider, cache or policy writes.

Run from any directory: python3 path/to/model/requalify.py
qualified_inputs.json freezes the existing SHA-checked FINOPS model's affected
unit costs and population assumptions. Means are never clipped to a refusal cap:
a rejected answer is not a cheaper successful answer. All values are USD.
"""
import ast
import csv
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = (HERE / 'qualified_inputs.json').read_bytes()
I = json.loads(RAW)
SOURCE = HERE.parents[2]
module = ast.parse((SOURCE/'app/services/ai_allowances/physical.py').read_text())
limits = next(ast.literal_eval(node.value.args[0]) for node in module.body
              if isinstance(node, ast.Assign) and any(getattr(t,'id',None)=='LIMITS' for t in node.targets))
for feature, values in I['limits'].items():
    assert tuple(values[:4]) == limits[feature][:4]
    assert round(values[4]*1e9) == limits[feature][4]
U=I['weekly_offer']; P=I['proceeds']; W2M=52/12

def proceeds(price, months, commission, fx=P['fx'], refunds=P['refunds']):
    # Only the monthly 15% row is an observed ASC export. Other proceeds are
    # proportional counterfactuals, not authoritative annual/30% settlements.
    normalized=P['export_monthly_15_after_tax_try']/.85*(price/P['monthly_price_try'])
    return normalized*(1-commission)/fx*(1-refunds)/months

plans=[('monthly',P['monthly_price_try'],1,P['monthly_active']),
       ('annual',P['annual_price_try'],12,P['annual_active'])]
result={'inputs_sha256':hashlib.sha256(RAW).hexdigest(), 'scenarios':{}}
for key, sc in I['scenarios'].items():
    expected=sum(weight*sc['profiles'][name]['single_image_ai_month'] for name,weight in I['population_mix'].items())
    per_week=sum(U[f]*cost for f,cost in sc['mean_action_usd'].items())
    exempt=sc['profiles']['MEDIAN']['exempt_ai_month']
    full={str(w):per_week*w+exempt*w/W2M for w in [4,W2M,5,6]}
    margins={}
    for name,price,months,active in plans:
        for commission in [.30,.15]:
            revenue=proceeds(price,months,commission)
            cost=active*(expected+P['non_ai_per_active_month'])
            margins[f'{name}_{int(commission*100)}']={
                'proceeds_per_month':revenue,'expected_ai_per_month':active*expected,
                'expected_contribution_per_month':revenue-cost,'expected_margin_pct':100*(revenue-cost)/revenue,
                'full_margin_pct_by_weeks':{w:100*(revenue-ai-.05)/revenue for w,ai in full.items()},
                'full_contribution_by_weeks':{w:revenue-ai-.05 for w,ai in full.items()},
                'annual_52_53_54_buckets_contribution':{str(w):12*revenue-per_week*w-12*exempt-.60 for w in [52,53,54]}}
    result['scenarios'][key]={'conditional_expected_ai_active_month':expected,
        'full_ai_by_weeks':full,'margins':margins,
        'additional_one_unknown_release_per_family_per_week_mean_5weeks':5*sum(sc['mean_action_usd'].values())}
# The vector is immutable per action and includes retries/UNKNOWN reservations.
# Future models can spend LESS at the same input/output vector; fixed cost
# limits remain an independent restriction. No long-context Haiku invocation:
# existing per-call input caps <=64K (100K tier threshold).
tails={}
for name,irate,orate in [('current',.000003,.000015),('future_global',.000002,.000010),('future_eu',.0000022,.000011)]:
    per_action={f:min(v[4],v[0]*irate+v[1]*orate) for f,v in I['limits'].items()}
    week=sum(U[f]*value for f,value in per_action.items())
    tails[name]={'action_upper_usd':per_action,'offer_week_upper_usd':week,
        'five_week_upper_usd':week*5,'six_week_upper_usd':week*6,
        'additional_one_unknown_released_action_per_family_week_five_weeks':sum(per_action.values())*5,
        'annual_52_53_54_bucket_upper_usd':{str(w):week*w for w in [52,53,54]}}
result['physical_tails']=tails
sensitivity=[]
for fx in [45,49.654,55,60,65]:
 for refund in [.01,.03,.08]:
  for plan,price,months,active in plans:
   for commission in [.30,.15]:
    rev=proceeds(price,months,commission,fx,refund)
    for weeks in [5,6]:
     cost=result['scenarios']['A_CURRENT']['full_ai_by_weeks'][str(weeks)]+.05
     expected=active*(result['scenarios']['A_CURRENT']['conditional_expected_ai_active_month']+.05)
     sensitivity.append(dict(fx=fx,refund=refund,plan=plan,commission=commission,weeks=weeks,
       proceeds_month=rev,expected_contribution=rev-expected,full_contribution=rev-cost,
       full_margin_pct=100*(rev-cost)/rev))
result['fx_refund_sensitivity_file']='fx_refund_sensitivity.csv'
with (HERE/'fx_refund_sensitivity.csv').open('w',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=list(sensitivity[0]))
    writer.writeheader();writer.writerows(sensitivity)
assert len(sensitivity)==120
assert abs(proceeds(399.99,1,.15)-269.16/49.654*.97) < 1e-12
assert all(v['six_week_upper_usd']>v['five_week_upper_usd'] for v in tails.values())
(HERE/'results.json').write_text(json.dumps(result,indent=2)+'\n')
for key,sc in result['scenarios'].items():
    print(key, 'expected active AI',round(sc['conditional_expected_ai_active_month'],4),
          '30% monthly / annual margins',*[round(sc['margins'][p]['expected_margin_pct'],2) for p in ['monthly_30','annual_30']],
          'full5 / full6 AI',*[round(sc['full_ai_by_weeks'][str(w)],4) for w in [5,6]])
print('tails',json.dumps(tails))
