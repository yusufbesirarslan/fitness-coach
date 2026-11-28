"""Coach capability authority on disposable PostgreSQL (#533 / #547 F3).

Real F01/D1/cohort grant path through ``/api/v1/ai-consent`` with a SYNTHETIC,
test-only capability contract registered by monkeypatch (nothing is approved
or published in source). Proves on the real owner lock: an r2 Coach grant
never implies a capability, the capability grant opens only itself, and any
consent mutation after capture leaves the captured authority stale.
"""
import os
from hashlib import sha256
from types import MappingProxyType

import pytest

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("explicit disposable PostgreSQL required", allow_module_level=True)

from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.services import ai, ai_consent, ai_consent_disclosures, coach_capabilities as caps  # noqa: E402
from tests.test_d1_four_purpose_registration_pg import _act, _adult  # noqa: E402
from tests.test_f01_training_source_candidate_pg import candidate, call  # noqa: E402, F401

pytestmark = pytest.mark.pg_concurrency

VERSION = "test-pg-capability-training-edit"
PURPOSE = "coach.training_plan_edit"


def _register(monkeypatch, *, approve):
    spec = caps.SPECS[caps.TRAINING_PLAN_WRITE]
    texts = {locale: f"synthetic capability {locale}\n" for locale in ("en", "tr")}
    policy = ai_consent.Disclosure(
        version=VERSION, purpose=spec.purpose, categories=spec.categories,
        recipients=ai_consent.POLICIES["d1-closure02-coach-candidate-r2"].recipients,
        texts=tuple((k, sha256(v.encode()).hexdigest()) for k, v in sorted(texts.items())))
    monkeypatch.setattr(ai_consent, "POLICIES", MappingProxyType({**ai_consent.POLICIES, VERSION: policy}))
    monkeypatch.setattr(ai_consent_disclosures, "PUBLISHED_TEXTS", MappingProxyType({
        **ai_consent_disclosures.PUBLISHED_TEXTS, **{(VERSION, k): v for k, v in texts.items()}}))
    monkeypatch.setattr(ai_consent_disclosures, "LAUNCH_VERSIONS", MappingProxyType({
        **ai_consent_disclosures.LAUNCH_VERSIONS, PURPOSE: VERSION}))
    if approve:
        monkeypatch.setattr(caps, "APPROVED_VERSIONS", MappingProxyType({
            caps.TRAINING_PLAN_WRITE: frozenset({VERSION})}))


def _capture(app, owner):
    with app.app_context():
        authority = ai_consent.begin_operation(owner, "coach", *ai.heavy_primary_route())
        with ai_consent.operation_scope(authority):
            try:
                return authority, ai_consent.capture_capability(owner, caps.TRAINING_PLAN_WRITE)
            except ai_consent.ConsentDenied as error:
                return authority, error.code


def _recheck(app, operation, capability):
    with app.app_context():
        with ai_consent.operation_scope(operation):
            try:
                ai_consent.authorize_capabilities(capability)
                return True
            except ai_consent.ConsentDenied as error:
                return error.code


def test_unapproved_capability_contract_cannot_be_granted(candidate, monkeypatch):  # noqa: F811
    app, client, sdk = candidate
    _register(monkeypatch, approve=False)
    headers = _adult(client)
    status = call(client, 'get', '/api/v1/ai-consent', headers=headers)
    # Companion D-2: an unapproved capability contract is not even published.
    assert all(d['purpose'] != PURPOSE for d in status['available_disclosures'])
    policy = ai_consent.POLICIES[VERSION]
    response = client.post('/api/v1/ai-consent', headers=headers, json=dict(
        action='affirmative_grant', purpose=PURPOSE, version=VERSION, locale='en',
        text_digest=dict(policy.texts)['en'], contract_digest=ai_consent.contract_digest(policy),
        expected_generation=status['generation'], request_id='cap-unapproved-grant'))
    assert response.status_code == 403
    assert response.get_json()['error']['code'] == 'AI_CONSENT_DISCLOSURE_UNAVAILABLE'
    assert sdk == []


def test_capability_is_separate_from_r2_and_stale_after_revoke(candidate, monkeypatch):  # noqa: F811
    app, client, sdk = candidate
    _register(monkeypatch, approve=True)
    headers = _adult(client)
    with app.app_context():
        owner = db.session.query(User).one().id
    _act(client, headers, 'coach.conversation', 'affirmative_grant', 'cap-coach-grant')

    # r2 alone: ordinary chat opens, the capability does not (no expansion).
    _operation, refused = _capture(app, owner)
    assert refused == 'AI_CAPABILITY_CONSENT_REQUIRED'

    _act(client, headers, PURPOSE, 'affirmative_grant', 'cap-grant')
    operation, capability = _capture(app, owner)
    assert isinstance(capability, ai_consent.CapabilityAuthority)
    assert (capability.version, capability.generation) == (VERSION, operation.generation)
    assert _recheck(app, operation, capability) is True

    # Revoke (then regrant): the captured authority never revives.
    _act(client, headers, PURPOSE, 'revoke', 'cap-revoke')
    assert _recheck(app, operation, capability) == 'AI_CONSENT_CHANGED'
    _act(client, headers, PURPOSE, 'affirmative_grant', 'cap-regrant')
    assert _recheck(app, operation, capability) == 'AI_CONSENT_CHANGED'

    # Withdrawing the Coach grant closes the capability too.
    _act(client, headers, 'coach.conversation', 'revoke', 'cap-coach-revoke')
    with app.app_context():
        with pytest.raises(ai_consent.ConsentDenied):
            ai_consent.begin_operation(owner, "coach", *ai.heavy_primary_route())
    assert sdk == []


def test_writer_fence_rechecks_the_capability_in_the_callers_transaction(candidate, monkeypatch):  # noqa: F811
    app, client, sdk = candidate
    _register(monkeypatch, approve=True)
    headers = _adult(client)
    with app.app_context():
        owner = db.session.query(User).one().id
    _act(client, headers, 'coach.conversation', 'affirmative_grant', 'cap-fence-coach')
    _act(client, headers, PURPOSE, 'affirmative_grant', 'cap-fence-grant')
    operation, capability = _capture(app, owner)
    with app.app_context():
        with ai_consent.operation_scope(operation), ai_consent.capability_scope(capability):
            assert ai_consent.fence_active(owner) == operation
            db.session.rollback()
    _act(client, headers, PURPOSE, 'revoke', 'cap-fence-revoke')
    with app.app_context():
        with ai_consent.operation_scope(operation), ai_consent.capability_scope(capability):
            with pytest.raises(ai_consent.ConsentDenied):
                ai_consent.fence_active(owner)
            db.session.rollback()
    assert sdk == []
