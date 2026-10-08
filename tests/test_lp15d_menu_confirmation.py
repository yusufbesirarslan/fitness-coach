"""Explicit menu confirmation uses the one canonical MealLog authority."""
import logging
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import MealLog, CustomMeal, CustomMealItem, NutritionPlan
from app.services import mobile_auth, mobile_menu, mobile_log_food
from app.services.mobile_log_food.menu_confirmation import parse_menu_confirmation
from app.services.nutrition_native import tokens
from app.timeutil import audit_clock

PATH = '/api/v1/nutrition/menu/log'


@pytest.fixture
def owner(make_user):
    return make_user('menu-confirm-owner')


@pytest.fixture
def headers(monkeypatch):
    def authenticate(user, key='menu-confirm-key-0001'):
        monkeypatch.setattr(mobile_auth, 'authenticate_access', lambda raw:
            mobile_auth.MobilePrincipal(user, SimpleNamespace(id=1), {'sub': user.cognito_sub}))
        return {'Authorization': 'Bearer confirmation-access', 'Idempotency-Key': key}
    return authenticate


def proof(app, owner, **changes):
    values = dict(analysis_id='analysis-sentinel', candidate_id='candidate-sentinel',
                  name='DishSensitiveSentinel',
                  portion={'basis': 'serving', 'quantity': 1, 'stated_grams': None},
                  nutrition={'energy_kcal': 420, 'protein_g': 25,
                             'carbohydrate_g': 40, 'fat_g': 14},
                  source='llm', confidence=0.5)
    values.update(changes)
    return mobile_menu.issue_item_proof(app.config['SECRET_KEY'], owner.id, **values)


def body(token, **changes):
    result = dict(confirmation_token=token, quantity=1, slot='ogle', confirmed=True)
    result.update(changes)
    return result


def post(client, headers, payload):
    return client.post(PATH, headers=headers, json=payload)


def assert_error(response, code, status=400):
    assert response.status_code == status, response.json
    assert response.json['error']['code'] == code
    assert set(response.json['error']) == {'code', 'message', 'retryable', 'request_id'}


def test_success_one_canonical_row_and_only_signed_nutrition(app, raw_client, owner, headers):
    response = post(raw_client, headers(owner), body(proof(app, owner), quantity=2))
    assert response.status_code == 201
    assert response.headers['Cache-Control'] == 'no-store'
    assert set(response.json) == {'meal'}
    meal = response.json['meal']
    assert set(meal) == {'id', 'revision', 'slot', 'description', 'source', 'logged_at', 'nutrition', 'day'}
    assert meal['source'] == 'menu_estimated'
    assert meal['nutrition'] == dict(energy_kcal=840, protein_g=50, carbohydrate_g=80, fat_g=28)
    assert meal['description'] == 'DishSensitiveSentinel — 2 porsiyon'
    row = MealLog.query.one()
    assert row.user_id == owner.id and row.source == 'menu_estimated'
    assert row.idempotency_key == 'menu-confirm-key-0001'
    assert row.kalori == 840
    assert meal['id'] != str(row.id) and meal['revision']
    assert CustomMeal.query.count() == CustomMealItem.query.count() == NutritionPlan.query.count() == 0
    for sentinel in ['analysis-sentinel', 'candidate-sentinel', 'llm', 'confidence', 'http']:
        assert sentinel not in row.yemekler


def test_bearer_required_cookie_cannot_authorize(app, raw_client, owner):
    with raw_client.session_transaction() as session:
        session["_user_id"] = str(owner.id)
        session["_fresh"] = True
        session["cognito_sid"] = "cookie-only-session"
    response = post(raw_client, {'Idempotency-Key': 'menu-confirm-key-0001'}, body(proof(app, owner)))
    assert response.status_code == 401
    assert MealLog.query.count() == 0


@pytest.mark.parametrize('key', ['', 'short', 'x'*65, 'spaces are invalid', 'unicode-ş-key'])
def test_key_required(app, raw_client, owner, headers, key):
    assert_error(post(raw_client, headers(owner, key), body(proof(app, owner))), 'INVALID_IDEMPOTENCY_KEY')
    assert MealLog.query.count() == 0


@pytest.mark.parametrize('field', ['nutrition', 'description', 'source', 'provider', 'food_id', 'serving_id',
    'user_id', 'account_id', 'date', 'day', 'id', 'revision', 'qr_payload', 'url', 'fingerprint'])
def test_injection_rejected(app, raw_client, owner, headers, field):
    assert_error(post(raw_client, headers(owner), body(proof(app, owner), **{field: 'SENSITIVE_INJECTION'})),
                 'INVALID_MENU_LOG_COMMAND')
    assert MealLog.query.count() == 0


@pytest.mark.parametrize('field', ['confirmation_token', 'quantity', 'slot', 'confirmed'])
def test_all_fields_required(app, raw_client, owner, headers, field):
    payload = body(proof(app, owner)); payload.pop(field)
    assert_error(post(raw_client, headers(owner), payload), 'INVALID_MENU_LOG_COMMAND')


@pytest.mark.parametrize('value', [False, 1, 0, 'true', None, [], {}])
def test_explicit_literal_confirmation(app, raw_client, owner, headers, value):
    assert_error(post(raw_client, headers(owner), body(proof(app, owner), confirmed=value)), 'INVALID_MENU_LOG_COMMAND')
    assert MealLog.query.count() == 0


@pytest.mark.parametrize('value', [0, -1, 1001, True, False, '1', None, float('nan'), float('inf'), [], {}])
def test_invalid_quantities(app, raw_client, owner, headers, value):
    assert_error(post(raw_client, headers(owner), body(proof(app, owner), quantity=value)), 'INVALID_MENU_LOG_COMMAND')
    assert MealLog.query.count() == 0


@pytest.mark.parametrize('value', ['lunch', 'Öğle', 'unknown', '', None, [], {}])
def test_invalid_slots(app, raw_client, owner, headers, value):
    assert_error(post(raw_client, headers(owner), body(proof(app, owner), slot=value)), 'INVALID_MENU_LOG_COMMAND')


@pytest.mark.parametrize('slot', ['kahvalti', 'ogle', 'aksam', 'ara_ogun'])
def test_canonical_slots(app, raw_client, owner, headers, slot):
    response = post(raw_client, headers(owner), body(proof(app, owner), slot=slot))
    assert response.status_code == 201 and response.json['meal']['slot'] == slot


def test_closed_json_and_content_type(app, raw_client, owner, headers):
    for payload in [None, [], 'bad']:
        assert_error(post(raw_client, headers(owner), payload), 'INVALID_MENU_LOG_COMMAND')
    assert_error(raw_client.post(PATH, headers=headers(owner), data='{}', content_type='text/plain'), 'INVALID_MENU_LOG_COMMAND')
    assert_error(raw_client.post(PATH, headers=headers(owner), data='{', content_type='application/json'), 'INVALID_MENU_LOG_COMMAND')


@pytest.mark.parametrize('value', ['', 'forged', None, [], {}, 'x'*2049])
def test_invalid_proof(app, raw_client, owner, headers, value):
    assert_error(post(raw_client, headers(owner), body(value)), 'INVALID_MENU_ITEM_PROOF')
    assert MealLog.query.count() == 0


@pytest.mark.parametrize('change', [
    {'v': 2}, {'v': True}, {'estimated': False}, {'loggable': False}, {'persist_as': 'manual'},
    {'aid': ''}, {'cid': None}, {'name': ''}, {'source': 'unbounded'}, {'source': []},
    {'confidence': True}, {'confidence': 2}, {'confidence': float('nan')},
    {'portion': {'basis': 'serving', 'quantity': True, 'stated_grams': None}},
    {'portion': {'basis': 'grams', 'quantity': 1, 'stated_grams': 10}},
    {'nutrition': {'energy_kcal': 1}}, {'extra': 'field'}, {'iat': True}, {'exp': 0},
    {'nutrition': {'energy_kcal': 0, 'protein_g': 1, 'carbohydrate_g': 1, 'fat_g': 1}},
])
def test_authenticated_but_invalid_snapshot(app, raw_client, owner, headers, change):
    secret = app.config['SECRET_KEY']
    payload = mobile_menu.read_item_proof(secret, owner.id, proof(app, owner))
    payload.update(change)
    signed = tokens.sign_payload(secret, mobile_menu.PROOF_DOMAIN, owner.id, payload)
    assert_error(post(raw_client, headers(owner), body(signed)), 'INVALID_MENU_ITEM_PROOF')
    assert MealLog.query.count() == 0


def test_wrong_domain_tampering_and_future_issue(app, raw_client, owner, headers):
    token = proof(app, owner)
    payload = mobile_menu.read_item_proof(app.config['SECRET_KEY'], owner.id, token)
    wrong = tokens.sign_payload(app.config['SECRET_KEY'], tokens.PLAN_PROPOSAL, owner.id, payload)
    for invalid in [wrong, 'A'+token[1:], proof(app, owner, now=10**12)]:
        assert_error(post(raw_client, headers(owner), body(invalid)), 'INVALID_MENU_ITEM_PROOF')


def test_owner_binding_and_independent_keys(app, raw_client, owner, headers, make_user):
    other = make_user('menu-confirm-other')
    token = proof(app, owner)
    assert_error(post(raw_client, headers(other), body(token)), 'INVALID_MENU_ITEM_PROOF')
    a = post(raw_client, headers(owner), body(token))
    assert a.status_code == 201
    # Switching accounts even after A committed must not grant replay authority.
    assert_error(post(raw_client, headers(other), body(token)), 'INVALID_MENU_ITEM_PROOF')
    b = post(raw_client, headers(other), body(proof(app, other)))
    assert b.status_code == 201 and a.json['meal']['id'] != b.json['meal']['id']
    assert MealLog.query.count() == 2
    assert {r.user_id for r in MealLog.query.all()} == {owner.id, other.id}


def test_lost_response_expired_committed_replay_and_new_write(app, raw_client, owner, headers, monkeypatch):
    clock = [10000]
    monkeypatch.setattr(mobile_menu.time, 'time', lambda: clock[0])
    token = proof(app, owner)
    payload = body(token)
    committed = post(raw_client, headers(owner), payload)
    assert committed.status_code == 201  # client treats this response as lost
    clock[0] += mobile_menu.PROOF_TTL_SECONDS
    retry = post(raw_client, headers(owner), payload)
    assert retry.status_code == 200 and retry.json == committed.json
    assert MealLog.query.count() == 1
    assert_error(post(raw_client, headers(owner, 'another-menu-key'), payload), 'MENU_ITEM_EXPIRED', 410)
    assert_error(post(raw_client, headers(owner), body(token, quantity=2)), 'IDEMPOTENCY_CONFLICT', 409)
    assert MealLog.query.count() == 1


def test_numeric_and_token_time_normalization(app, raw_client, owner, headers, monkeypatch):
    monkeypatch.setattr(mobile_menu.time, 'time', lambda: 10000)
    first = proof(app, owner, now=9900)
    second = proof(app, owner, now=10000)
    a = post(raw_client, headers(owner), body(first, quantity=1))
    b = post(raw_client, headers(owner), body(second, quantity=1.0, slot=' OGLE '))
    assert a.status_code == 201 and b.status_code == 200 and a.json == b.json
    assert MealLog.query.count() == 1


@pytest.mark.parametrize('changes', [dict(candidate_id='different'), dict(analysis_id='different'),
    dict(source='cache'), dict(confidence=0.8), dict(name='DifferentDish'),
    dict(portion={'basis': 'serving', 'quantity': 1, 'stated_grams': 100})])
def test_semantic_provenance_conflicts(app, raw_client, owner, headers, changes):
    assert post(raw_client, headers(owner), body(proof(app, owner))).status_code == 201
    assert_error(post(raw_client, headers(owner), body(proof(app, owner, **changes))), 'IDEMPOTENCY_CONFLICT', 409)
    assert MealLog.query.count() == 1


def test_fingerprint_has_fixed_provenance_and_excludes_expiry(app, owner):
    from app.services.mobile_log_food.fingerprint import _semantic_payload
    command = parse_menu_confirmation(body(proof(app, owner)), app.config['SECRET_KEY'], owner.id)
    payload = _semantic_payload(command)
    assert payload['domain'] == 'axisai/mobile-log-food/menu-confirm/v1'
    assert payload['source'] == 'menu_estimated' and payload['estimated'] is True
    assert mobile_log_food.semantic_fingerprint(command) == mobile_log_food.semantic_fingerprint(replace(command, expires_at=0))
    assert not {'user_id', 'iat', 'exp', 'expires_at', 'token', 'idempotency_key'} & payload.keys()


def test_scaled_bounds_and_signed_grams(app, raw_client, owner, headers):
    token = proof(app, owner, portion={'basis': 'serving', 'quantity': 1, 'stated_grams': 200})
    result = post(raw_client, headers(owner), body(token, quantity=0.5))
    assert result.status_code == 201 and result.json['meal']['description'].endswith('0.5 porsiyon (100 g)')
    assert result.json['meal']['nutrition']['energy_kcal'] == 210
    assert_error(post(raw_client, headers(owner, 'too-large-menu-key'), body(token, quantity=1000)), 'INVALID_MENU_LOG_COMMAND')
    assert MealLog.query.count() == 1


@pytest.mark.parametrize(('utc_time','expected'), [('2026-10-08T20:59:59+00:00','2026-10-08'),
                                                ('2026-10-08T21:00:00+00:00','2026-10-09')])
def test_server_istanbul_day(app, raw_client, owner, headers, utc_time, expected):
    with audit_clock(datetime.fromisoformat(utc_time)):
        response = post(raw_client, headers(owner), body(proof(app, owner)))
    assert response.status_code == 201 and response.json['meal']['day'] == expected
    assert MealLog.query.one().tarih == expected


def test_no_remote_provider_ai_or_cache(app, raw_client, owner, headers, monkeypatch):
    from app.services import menu_remote, menu_analysis, ai_nutrition, foodcache, provider_food_snapshot
    from app.services.mobile_log_food import service
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('called'); raise AssertionError('remote work attempted')
    for module, name in [(menu_remote, 'fetch'), (menu_analysis, 'acquire_menu'),
                         (menu_analysis, 'analyze_menu_text'), (ai_nutrition, '_heavy_chat'),
                         (service, 'resolve_provider_food'), (provider_food_snapshot, 'resolve_provider_food')]:
        monkeypatch.setattr(module, name, forbidden)
    class ForbiddenCache:
        def __getattr__(self, name):
            return forbidden
    monkeypatch.setattr(menu_analysis, 'redis_client', ForbiddenCache())
    monkeypatch.setattr(foodcache, '_get_redis', forbidden)
    monkeypatch.setattr(foodcache, '_get_cached_macros', forbidden)
    monkeypatch.setattr(foodcache, '_cache_macros', forbidden)
    monkeypatch.setattr(ai_nutrition, '_extract_categorized_items', forbidden)
    monkeypatch.setattr(ai_nutrition, '_estimate_macros_llm', forbidden)
    response = post(raw_client, headers(owner), body(proof(app, owner)))
    assert response.status_code == 201
    assert calls == [] and MealLog.query.count() == 1


def test_storage_failure_rolls_back_and_logs_only_metadata(app, raw_client, owner, headers, monkeypatch, caplog):
    from app.services import meal_idempotency
    def failed(entry, key):
        db.session.add(entry); db.session.flush()
        raise RuntimeError('RAW_EXCEPTION_SENTINEL DishSensitiveSentinel 420 analysis-sentinel candidate-sentinel')
    monkeypatch.setattr(meal_idempotency, 'commit_once', failed)
    token = proof(app, owner)
    with caplog.at_level(logging.INFO):
        response = post(raw_client, headers(owner), body(token))
    assert_error(response, 'NUTRITION_TEMPORARILY_UNAVAILABLE', 503)
    assert MealLog.query.count() == 0
    for forbidden in ['RAW_EXCEPTION_SENTINEL', 'DishSensitiveSentinel', '420', 'analysis-sentinel',
                      'candidate-sentinel', token, 'menu-confirm-key-0001', owner.email,
                      owner.cognito_sub]:
        assert forbidden not in caplog.text
    assert 'error_type=RuntimeError' in caplog.text


@pytest.mark.parametrize('first_kind', ['menu', 'manual', 'provider_backed'])
def test_cross_command_conflict(app, raw_client, owner, headers, monkeypatch, first_kind):
    from test_mobile_log_food_api import manual_command, provider_command
    from app.services.mobile_log_food import service
    monkeypatch.setattr(service, 'resolve_provider_food', lambda *a: SimpleNamespace(
        description='Provider Food', nutrition=mobile_log_food.ManualNutritionSnapshot(
            Decimal(420), Decimal(25), Decimal(40), Decimal(14))))
    menu = body(proof(app, owner))
    existing = manual_command() if first_kind == 'manual' else provider_command()
    if first_kind == 'menu':
        assert post(raw_client, headers(owner), menu).status_code == 201
        response = raw_client.post('/api/v1/nutrition/logs', headers=headers(owner), json=existing)
    else:
        assert raw_client.post('/api/v1/nutrition/logs', headers=headers(owner), json=existing).status_code == 201
        response = post(raw_client, headers(owner), menu)
    assert_error(response, 'IDEMPOTENCY_CONFLICT', 409)
    assert MealLog.query.count() == 1


def test_maximum_quantity_valid_for_bounded_snapshot(app, raw_client, owner, headers):
    token = proof(app, owner, nutrition=dict(energy_kcal=1, protein_g=1, carbohydrate_g=1, fat_g=1))
    response = post(raw_client, headers(owner), body(token, quantity=1000))
    assert response.status_code == 201
    assert response.json['meal']['nutrition']['energy_kcal'] == 1000
    assert response.json['meal']['description'].endswith('1000 porsiyon')
    assert ' g)' not in response.json['meal']['description']


def test_sensitive_logging_on_success_replay_and_rejection(app, raw_client, owner, headers, caplog):
    token = proof(app, owner)
    with caplog.at_level(logging.INFO):
        created = post(raw_client, headers(owner), body(token))
        replay = post(raw_client, headers(owner), body(token))
        rejected = post(raw_client, headers(owner), body(token, nutrition='NutritionSensitiveSentinel'))
    assert (created.status_code, replay.status_code, rejected.status_code) == (201, 200, 400)
    row = MealLog.query.one()
    for sentinel in ['DishSensitiveSentinel', 'NutritionSensitiveSentinel', token,
                     'analysis-sentinel', 'candidate-sentinel', row.idempotency_key,
                     row.idempotency_fingerprint, owner.email, owner.cognito_sub]:
        assert sentinel not in caplog.text
    assert 'user=-' in caplog.text


def test_canonical_diary_publishes_menu_source(app, raw_client, owner, headers):
    assert post(raw_client, headers(owner), body(proof(app, owner))).status_code == 201
    diary = raw_client.get('/api/v1/nutrition/diary/today', headers=headers(owner))
    assert diary.status_code == 200
    assert diary.json['meals'][0]['source'] == 'menu_estimated'
