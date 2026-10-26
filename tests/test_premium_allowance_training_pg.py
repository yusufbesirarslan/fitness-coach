"""Unified mode through the original native Training command and SDK door."""
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.models import AppleSubscription, AiUsageReservation, AiStarterGrant
from app.services.ai_allowances.policy import AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES
from tests.test_training_allowance_zero_attempt_pg import (  # noqa: F401
    chain, pg_generation_app, generate, consent, snap)
from ai_allowance_support import enable_subscriptions, premium_row

pytestmark = pytest.mark.pg_concurrency
if os.environ.get('FITX_PG_CONCURRENCY_TEST') != '1':
    pytest.skip('requires explicitly disposable PostgreSQL', allow_module_level=True)


def unified(chain):
    enable_subscriptions(chain.app, chain.monkeypatch)
    chain.app.config.update(AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_POLICY=AllowancePolicy('synthetic-native-all',
            premium=ScopePolicy('WEEKLY',{f:2 for f in SUPPORTED_FEATURES},1)))
    premium_row(chain.owners[0])
    consent(chain.owners[0],chain.policy,'affirmative_grant','unified-native-grant')


def test_unified_native_training_keeps_original_dispatch_and_recovery(chain):
    with chain.app.app_context():
        unified(chain)
        result=generate(chain.owners[0],'unified-native-training-key')
        assert result is not None
        state=snap(chain.owners[0])
        assert state.operations==[('SUCCEEDED',True,True)]
        assert state.bindings==['COMMITTED']
        assert state.plans==1 and state.bucket[:2]==(1,0)
        assert sum(chain.calls.values())==1
        generate(chain.owners[0],'unified-native-training-key')
        assert sum(chain.calls.values())==1
        assert snap(chain.owners[0]).plans==1


def test_unified_native_training_refuses_pending_without_hold_or_sdk(chain):
    from app.services.mobile_training_generation.errors import TrainingAllowanceRefused
    with chain.app.app_context():
        unified(chain)
        row=AppleSubscription.query.one()
        row.last_verified_at=datetime.now(timezone.utc)-timedelta(days=2)
        db.session.commit()
        with pytest.raises(TrainingAllowanceRefused) as error:
            generate(chain.owners[0],'unified-native-pending-key')
        assert error.value.public_code=='AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE'
        assert sum(chain.calls.values())==0
        assert AiUsageReservation.query.count()==AiStarterGrant.query.count()==0
