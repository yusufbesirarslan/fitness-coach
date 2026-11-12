"""Freeze approved F01 bytes and the existing Training registration candidate."""
from hashlib import sha256
from types import MappingProxyType

from app.services import (health_disclosure, ai_launch, ai_consent,
                          ai_consent_disclosures)


def test_f01_source_pin_retains_exact_founder_approved_texts():
    assert health_disclosure.APPROVED_VERSION == health_disclosure.VERSION == 'health-fitness-v2-review'
    assert ai_launch.APPROVED_SERVICE_COHORT_VERSION == 'age-screen-v1'
    assert dict(health_disclosure.DIGESTS) == {
        'en': '11a79e6110bc70e9e62d27ad8422dcc3d12ad142aa41824f417e991850572c0c',
        'tr': '10844c943db3c80c6bb89352d62b13f1575ddd3fa45fa280fd5f4d7c75a20ae6',
    }


def test_only_existing_training_contract_is_registered_with_exact_r1_r2_copy():
    version = 'd1-closure01-training-candidate'
    assert isinstance(ai_consent.POLICIES, MappingProxyType)
    assert set(ai_consent.POLICIES) == {version}
    assert dict(ai_consent_disclosures.LAUNCH_VERSIONS) == {'training.plan': version}
    policy = ai_consent.POLICIES[version]
    assert policy.external_scopes == policy.nested_features == ()
    assert policy.categories == ai_consent.BINDINGS['training_plan'][1]
    assert ai_consent.contract_digest(policy) == '2d14b846a5e2b273ca1bb724f507336989998b81cbcafbd7360ba4ea28b05968'
    texts = ai_consent_disclosures.localized_texts(policy)
    assert {locale: sha256(body.encode()).hexdigest() for locale, body in texts.items()} == {
        'en': '467fc1fb54fca648cec3e737ad7a78fc969f4f74ae0ef735b223d018ab130054',
        'tr': '062ae5123955dae849ef48f91aa60387443bb53e14e94b2a1eb53808ecd82b80',
    }
