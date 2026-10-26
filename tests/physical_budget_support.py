"""Synthetic payloads for accounting-only tests; no provider IO."""
from app.services import ai_model_policy

def payload(feature):
    model = ai_model_policy.heavy_model()
    content = ([{'type':'image','source':{'type':'base64','data':'', 'media_type':'image/jpeg'}}]
               if feature == 'vision' else 'synthetic')
    return dict(model=model, max_tokens=1, messages=[{'role':'user','content':content}])

def before_attempt(owner, feature):
    from app.services.ai_allowances import execution as E
    return E.before_attempt(owner, feature, provider='bedrock', payload=payload(feature))
