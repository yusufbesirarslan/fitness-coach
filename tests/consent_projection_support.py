"""Explicit synthetic authority for pre-existing layer-isolation tests.

These suites qualify payload minimization, budget/retry behavior and domain
transactions under an ALLOW decision; they do not qualify D1 consent. D1's
separate tests exercise the real authority, default deny and PostgreSQL fence.
Never imported by application code; no runtime allow-on-error or test flag.
"""
from contextlib import nullcontext


def allow_synthetic_authority(monkeypatch, app=None, *, memory=False):
    from app.services import ai_consent as consent
    from app.blueprints import ai_consent as routes
    monkeypatch.setattr(consent, 'BINDINGS', {**consent.BINDINGS, **{
        f: ('synthetic.layer-test', ()) for f in
        ('coach', 'summary', 'nutrition', 'menu_extract', 'menu_ocr', 'vision', 'health_probe', 'other')
    }})
    monkeypatch.setattr(consent, 'generation', lambda owner: 0)
    monkeypatch.setattr(consent, 'preflight', lambda *a, **kw: None)
    allow_operation_fences(monkeypatch)
    monkeypatch.setattr(consent, 'dispatch', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(consent, 'stream_dispatch', lambda *a, **kw: nullcontext())
    if memory:
        monkeypatch.setattr(consent, 'memory_available', lambda owner: True)
    monkeypatch.setattr(routes, '_CLOSED_ENDPOINTS', frozenset())
    monkeypatch.setattr(routes, 'require_legacy_ai', lambda *a, **kw: None)
    if app is not None and memory:
        monkeypatch.setitem(app.config, 'AI_MEMORY_ENABLED', True)



def allow_operation_fences(monkeypatch):
    """LP21 operation fencing: ALLOW at operation capture, publication and the
    canonical-write fence. The provenance codec and dispatch stay real."""
    from app.services import ai_consent as consent
    monkeypatch.setattr(consent, 'begin_operation', lambda owner, feature, *a, **kw:
                        consent.OperationAuthority(owner, feature, 1, 'synthetic-layer-test'))
    monkeypatch.setattr(consent, 'authorize_operation', lambda *a, **kw: None)
    monkeypatch.setattr(consent, 'fence', lambda *a, **kw: None)


def open_browser_plan_saves(monkeypatch):
    """Lift only the two LP21 browser plan-save closures; ``None`` provenance
    passes the writer fence, any real authority is still fenced for real."""
    from app.services import ai_consent as consent
    from app.blueprints import ai_consent as routes
    aliases = {'training.save_training_plan', 'nutrition.save_nutrition_plan'}
    monkeypatch.setattr(routes, '_CLOSED_ENDPOINTS', routes._CLOSED_ENDPOINTS - aliases)
    real = consent.fence

    def fence(authority):
        if authority is None:
            return None
        return real(authority)
    monkeypatch.setattr(consent, 'fence', fence)


def assert_default_deny_at_sdk(app, make_user, monkeypatch, recipient):
    """Probe a previously admitted operation through the real final gate.

    SQLite cannot supply a distributed authority snapshot. Freeze only the
    earlier admission generation; dispatch must still deny on unavailable
    authority. This does not replace dispatch, state validation or policies.
    Real missing-grant/withdrawal/owner checks also run on PostgreSQL.
    """
    import pytest
    from unittest.mock import Mock
    from app.services import ai_consent as consent, ai_provider_call as calls
    from app.services import ai_spend_guard
    from app.blueprints import ai_consent as routes
    from app.models import AIConsentEvent, AIConsentState

    assert set(consent.BINDINGS) == {"training_plan", "nutrition_plan"}
    assert routes._CLOSED_ENDPOINTS
    assert app.config["AI_MEMORY_ENABLED"] is False
    assert dict(consent.POLICIES) == {}
    monkeypatch.setattr(consent, "generation", lambda owner: 0)
    sdk = Mock()
    for name in ("seam_owner_a", "seam_owner_b"):
        owner = make_user(name)
        assert consent.memory_available(owner.id) is False
        with ai_spend_guard.subject_scope(owner.id):
            with pytest.raises(consent.ConsentDenied):
                with calls.admit(feature="training_plan", provider=recipient,
                                 payload={"model": "synthetic", "max_tokens": 10,
                                          "messages": [{"role": "user", "content": "OWN"}]},
                                 gate=False, charge=False) as admission:
                    admission.create(sdk)
    sdk.assert_not_called()
    assert AIConsentState.query.count() == AIConsentEvent.query.count() == 0


def synthetic_authority(owner, feature='training_plan'):
    """The ALLOW-layer operation authority ``allow_operation_fences`` issues."""
    from app.services import ai_consent as consent
    return consent.OperationAuthority(owner, feature, 1, 'synthetic-layer-test')


def bound_candidate(owner, document_text, feature='training_plan'):
    """A staged AI candidate as production stages it (LP21 provenance envelope)."""
    from app.services import ai_consent as consent
    return consent.bind_candidate(synthetic_authority(owner, feature), document_text)
