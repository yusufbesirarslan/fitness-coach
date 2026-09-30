"""NUTR-PR5 — Nutrition → Plan in a real browser.

The real rendered page and shipped scripts run against the authenticated
Flask client (`training_page`). Only the AI provider behind POST
/nutrition-plan is replaced (`generator`), so generation, save, validation
and the active read are the real routes and the real NutritionPlan table.
A failure or an ambiguous answer is injected per request with a browser
route; every "the plan did / did not change" claim is read back from the
database.

  GENERATED ≠ SAVED · PLANNED ≠ LOGGED · ABSENT ≠ UNAVAILABLE ·
  TARGET MISSING ≠ TARGET FAILED · a failed replacement is never shown as a
  successful one · one request per explicit action · Plan and Today converge
  on the canonical plan only after a confirmed save
"""
import json
import re
from collections import Counter
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from app.extensions import db
from app.models import MealLog, NutritionPlan
from test_training_execution_boundary import training_page  # noqa: F401
from test_nutrition_vnext_pr2_navigation_browser import paths
from test_nutrition_vnext_pr3_daily_browser import CATALOG, PLAN, fail, seed

OPEN = re.compile(r'\bopen\b')
INITIAL = Counter({'/nutrition': 1, '/meal-log/today': 1, '/nutrition-plan/active': 1,
                   '/water': 1, '/notifications/unread-count': 1, '/coach/history': 1})
FORWARDED_HEADERS = {'content-type', 'origin', 'x-csrftoken', 'if-match', 'idempotency-key'}


def option(name, kcal, food):
    return {'isim': name,
            'kahvalti': {'yemekler': [food + ' - 100g'], 'kalori': kcal, 'protein': 30, 'karb': 40, 'yag': 10},
            'aksam': {'yemekler': ['Rice - 150g'], 'kalori': 600, 'protein': 40, 'karb': 70, 'yag': 15},
            'toplam_kalori': kcal + 600, 'toplam_protein': 70, 'toplam_karb': 110, 'toplam_yag': 25}


OPTIONS = [option('Option Alpha', 450, 'Eggs'), option('Option Beta', 500, 'Oats'),
           option('Option Gamma', 550, 'Yogurt')]


@pytest.fixture
def generator(monkeypatch):
    """The AI provider behind the real POST /nutrition-plan route."""
    from app.blueprints.nutrition import plan as plan_module
    state = {'calls': 0, 'plans': OPTIONS, 'fail': False}

    def provider(**kwargs):
        state['calls'] += 1
        if state['fail']:
            raise RuntimeError('provider unavailable')
        return json.dumps({'planlar': state['plans']}, ensure_ascii=False)
    monkeypatch.setattr(plan_module, '_heavy_chat', provider)
    return state


# ── helpers ─────────────────────────────────────────────────────────────


def forward(client, route):
    """Deliver a held browser request to the real app now (as the harness does)."""
    request = route.request
    url = urlsplit(request.url)
    return client.open(url.path + ('?' + url.query if url.query else ''), method=request.method,
                       data=request.post_data,
                       headers={k: v for k, v in request.headers.items() if k.lower() in FORWARDED_HEADERS})


def fulfill_from(route, response, status=None):
    route.fulfill(status=status or response.status_code, body=response.get_data(),
                  headers={k: v for k, v in response.headers.items()
                           if k.lower() not in {'content-length', 'set-cookie'}})


def start(app, auth_user, training_page, language='en', plan=True, target=2100, size=(390, 844)):
    seed(app, auth_user.id, language, target=target, meals=(), plan=plan)
    page, traffic, _, _ = training_page
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.set_viewport_size({'width': size[0], 'height': size[1]})
    page.goto('http://localhost/nutrition')
    expect(page.locator('#nut-day')).not_to_have_attribute('data-intake-state', 'loading')
    return page, traffic, errors


def open_plan(page):
    page.locator('#nutrition-tab-plan').click()
    expect(page.locator('#panel-plan')).to_be_visible()
    expect(page.locator('#plan-current')).not_to_have_attribute('data-plan-state', 'loading')


def open_builder(page):
    page.locator('#plan-current .plan-cta').click()
    expect(page.locator('#plan-builder')).to_be_visible()
    expect(page.locator('#plan-builder-title')).to_be_focused()


def pick_foods(page):
    for group in ('#protein-hayvansal', '#karb-list', '#yag-list'):
        chip = page.locator(group + ' .chip').first
        chip.click()
        expect(chip).to_have_attribute('aria-pressed', 'true')


def generate(page, count=3):
    pick_foods(page)
    page.locator('#plan-btn').click()
    expect(page.locator('#plans-grid .plan-card')).to_have_count(count)
    expect(page.locator('#plan-builder')).to_have_attribute('data-gen-state', 'ready')


def choose(page, index):
    page.locator(f'#sel-btn-{index}').click()
    expect(page.locator('#plan-replace-modal')).to_have_class(OPEN)


def stored(app, user_id):
    with app.app_context():
        rows = NutritionPlan.query.filter_by(user_id=user_id).all()
        return [json.loads(r.plan_data) for r in rows]


def copy(page, key, **vars):
    text = CATALOG[page.evaluate('() => window.LOCALE')][key]
    for name, value in vars.items():
        text = text.replace('{' + name + '}', str(value))
    return text


def today_titles(page):
    page.locator('#nutrition-tab-today').click()
    expect(page.locator('#quick-add-section')).not_to_have_attribute('data-plan-state', 'loading')
    return page.locator('#quick-add-cards .qab .qab-title')


def assert_current(page, name):
    current = page.locator('#plan-current')
    expect(current).to_have_attribute('data-plan-state', 'active')
    expect(current.locator('.apd-title')).to_have_text(name)


# ── 1–3 · CURRENT-PLAN STATES ──────────────────────────────────────────


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_no_active_plan_is_an_honest_empty_state(app, auth_user, training_page, generator, language):
    page, traffic, errors = start(app, auth_user, training_page, language, plan=False)
    open_plan(page)
    current = page.locator('#plan-current')
    expect(current).to_have_attribute('data-plan-state', 'absent')
    expect(current).to_contain_text(copy(page, 'nutrition.plan.absent'))
    expect(current).not_to_contain_text(copy(page, 'nutrition.plan_unavailable'))
    create = page.locator('#plan-create-btn')
    expect(create).to_be_visible()
    expect(create).to_have_text(copy(page, 'nutrition.plan.create'))
    expect(page.locator('#plan-builder')).to_be_hidden()          # form stays subordinate
    open_builder(page)
    expect(page.locator('#plan-builder-title')).to_have_text(copy(page, 'nutrition.plan.builder_create'))
    assert generator['calls'] == 0
    assert 'nutrition-plan' not in ''.join(p for p, _, _ in traffic if p != '/nutrition-plan/active')
    assert errors == []


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_active_plan_is_current_with_planned_meals(app, auth_user, client, training_page, language):
    page, _, errors = start(app, auth_user, training_page, language)
    open_plan(page)
    server = client.get('/nutrition-plan/active').get_json()
    assert_current(page, server['plan']['isim'])
    current = page.locator('#plan-current')
    expect(current.locator('.apd-sub')).to_have_text(copy(page, 'nutrition.plan.created', date=server['created_at']))
    meals = current.locator('.apd-meal')
    expect(meals).to_have_count(2)                                   # the plan's own slots
    for meal in meals.all():
        expect(meal.locator('.apd-badge')).to_have_text(copy(page, 'nutrition.planned'))
    text = current.inner_text()
    assert copy(page, 'nutrition.logged_state') not in text          # planned is never logged
    assert 'Oats' in text and 'Salmon' in text
    expect(current.locator('.apd-macro-grid')).to_have_count(0)      # seeded plan has no totals
    # The score is neutral secondary text, never a coloured verdict.
    rating = current.locator('.plan-rating')
    expect(rating).to_contain_text(copy(page, 'nutrition.plan.rating', n=8))
    assert not re.search(r'color', rating.get_attribute('style') or '')
    for claim in ('Optimal', 'Perfect', 'Safe', 'Ready'):
        assert claim not in text
    expect(page.locator('#plan-replace-btn')).to_be_visible()
    expect(page.locator('#plan-builder')).to_be_hidden()
    assert errors == []


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_active_plan_read_failure_is_not_no_plan(app, auth_user, training_page, language):
    page, _, _, _ = training_page
    page.route('**/nutrition-plan/active', fail)
    page, traffic, errors = start(app, auth_user, training_page, language)
    open_plan(page)
    current = page.locator('#plan-current')
    expect(current).to_have_attribute('data-plan-state', 'unavailable')
    expect(current).to_contain_text(copy(page, 'nutrition.plan_unavailable'))
    expect(current).not_to_contain_text(copy(page, 'nutrition.plan.absent'))
    expect(page.locator('#plan-create-btn')).to_have_count(0)        # never "create" over an unknown
    expect(page.locator('.nutrition-child-domain a[href="/supplements"]')).to_be_visible()
    expect(page.locator('#plan-target')).to_have_attribute('data-target-state', 'known')
    # Today stays usable and says the same truth.
    page.locator('#nutrition-tab-today').click()
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'unavailable')
    expect(page.locator('#log-food-btn')).to_be_enabled()
    # Explicit retry, one read, real state.
    page.unroute('**/nutrition-plan/active', fail)
    open_plan(page)
    traffic.clear()
    current.get_by_role('button', name=copy(page, 'nutrition.try_again')).click()
    assert_current(page, PLAN['isim'])
    assert paths(traffic) == Counter({'/nutrition-plan/active': 1})
    assert errors == []


def test_a_stored_plan_that_cannot_be_drawn_is_invalid_not_absent(app, auth_user, training_page):
    page, _, errors = start(app, auth_user, training_page, plan=False)
    with app.app_context():
        db.session.add(NutritionPlan(user_id=auth_user.id, score=None, plan_data=json.dumps(['legacy'])))
        db.session.commit()
    page.reload()
    open_plan(page)
    current = page.locator('#plan-current')
    expect(current).to_have_attribute('data-plan-state', 'invalid')
    expect(current).to_contain_text(copy(page, 'nutrition.plan.invalid'))
    expect(current).not_to_contain_text(copy(page, 'nutrition.plan.absent'))
    expect(page.locator('#plan-replace-btn')).to_be_visible()
    assert errors == []


def test_a_corrupt_stored_plan_reads_as_unavailable(app, auth_user, training_page):
    """A row whose plan_data is not JSON makes GET /nutrition-plan/active raise
    (pre-existing; production answers with the HTML 500 page — the test client
    re-raises instead, so that answer is injected here)."""
    page, _, _, _ = training_page
    page.route('**/nutrition-plan/active', lambda route: route.fulfill(
        status=500, content_type='text/html', body='<!doctype html><h1>500</h1>'))
    page, _, _ = start(app, auth_user, training_page, plan=False)
    open_plan(page)
    expect(page.locator('#plan-current')).to_have_attribute('data-plan-state', 'unavailable')
    expect(page.locator('#plan-current')).not_to_contain_text(copy(page, 'nutrition.plan.absent'))


# ── 4–5 · TARGET ────────────────────────────────────────────────────────


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_target_is_todays_canonical_projection(app, auth_user, client, training_page, language):
    page, traffic, _ = start(app, auth_user, training_page, language)
    targets = client.get('/meal-log/today').get_json()['targets']
    traffic.clear()
    open_plan(page)
    box = page.locator('#plan-target')
    expect(box).to_have_attribute('data-target-state', 'known')
    expect(page.locator('#plan-target-value')).to_have_text(
        copy(page, 'nutrition.plan.target_value', n=round(targets['kalori'])))
    expect(page.locator('#plan-target-macros')).to_have_text(copy(
        page, 'nutrition.plan.target_macros', p=round(targets['protein']), c=round(targets['karb']),
        f=round(targets['yag'])))
    expect(page.locator('#plan-target-note')).to_have_text(copy(page, 'nutrition.plan.target_source'))
    assert paths(traffic) == Counter()                               # no read of its own


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_target_missing_is_not_set_and_plan_stays_usable(app, auth_user, training_page, language):
    page, _, _ = start(app, auth_user, training_page, language, target=None)
    open_plan(page)
    expect(page.locator('#plan-target')).to_have_attribute('data-target-state', 'absent')
    value = page.locator('#plan-target-value')
    expect(value).to_have_text(copy(page, 'nutrition.target_absent'))
    assert not re.search(r'\d', value.inner_text())
    assert_current(page, PLAN['isim'])
    expect(page.locator('#plan-replace-btn')).to_be_enabled()


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_target_read_failure_is_unavailable_never_zero(app, auth_user, training_page, language):
    page, _, _, _ = training_page
    page.route('**/meal-log/today', fail)
    page, _, _ = start(app, auth_user, training_page, language)
    open_plan(page)
    expect(page.locator('#plan-target')).to_have_attribute('data-target-state', 'unavailable')
    value = page.locator('#plan-target-value')
    expect(value).to_have_text(copy(page, 'nutrition.target_unavailable'))
    assert not re.search(r'\d', page.locator('#plan-target').inner_text())
    assert_current(page, PLAN['isim'])                               # target failure hides nothing


# ── 6–7 · GENERATION ────────────────────────────────────────────────────


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_generated_options_are_proposals_and_the_current_plan_is_unchanged(
        app, auth_user, training_page, generator, language):
    page, traffic, errors = start(app, auth_user, training_page, language)
    open_plan(page)
    open_builder(page)
    expect(page.locator('#plan-builder-title')).to_have_text(copy(page, 'nutrition.plan.builder_replace'))
    traffic.clear()
    generate(page)
    assert paths(traffic) == Counter({'/nutrition-plan': 1})
    assert generator['calls'] == 1
    expect(page.locator('#plan-gen-status')).to_have_text(copy(page, 'nutrition.plan.options_ready', n=3))
    cards = page.locator('#plans-grid .plan-card')
    for index, card in enumerate(cards.all()):
        expect(card.locator('.plan-badge')).to_have_text(copy(page, 'nutrition.plan.option_badge'))
        expect(card.locator('.plan-card-name')).to_have_text(OPTIONS[index]['isim'])
        expect(card.get_by_role('button')).to_have_text(copy(page, 'nutrition.plan.use'))
    # Generation is not a save: the current plan, the database and Today are A.
    assert_current(page, PLAN['isim'])
    assert stored(app, auth_user.id) == [PLAN]
    titles = today_titles(page)
    expect(titles).to_have_count(2)
    for title in titles.all():
        expect(title).to_contain_text(PLAN['isim'])
    assert errors == []


def test_generation_failure_keeps_the_current_plan(app, auth_user, training_page, generator):
    generator['fail'] = True
    page, traffic, errors = start(app, auth_user, training_page)
    open_plan(page)
    open_builder(page)
    traffic.clear()
    pick_foods(page)
    page.locator('#plan-btn').click()
    expect(page.locator('#plan-builder')).to_have_attribute('data-gen-state', 'failed')
    expect(page.locator('#plan-gen-status')).to_have_text(copy(page, 'nutrition.plan.gen_failed'))
    expect(page.locator('#plans-grid .plan-card')).to_have_count(0)
    expect(page.locator('#plan-btn')).to_be_enabled()                # retry is explicit
    assert_current(page, PLAN['isim'])
    expect(page.locator('#plan-target')).to_have_attribute('data-target-state', 'known')
    expect(page.locator('.nutrition-child-domain a[href="/supplements"]')).to_be_visible()
    assert paths(traffic) == Counter({'/nutrition-plan': 1})
    assert stored(app, auth_user.id) == [PLAN]
    page.locator('#nutrition-tab-today').click()
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'available')
    assert errors == []


# ── 8–11 · REPLACEMENT ──────────────────────────────────────────────────


def test_replace_cancel_writes_nothing(app, auth_user, training_page, generator):
    page, traffic, errors = start(app, auth_user, training_page)
    open_plan(page)
    open_builder(page)
    generate(page)
    traffic.clear()
    modal = page.locator('#plan-replace-modal')
    dialog = page.get_by_role('dialog', name=copy(page, 'nutrition.plan.confirm_title'))
    for dismiss in ('escape', 'keep', 'backdrop'):
        choose(page, 1)
        expect(dialog).to_be_visible()
        expect(page.locator('#plan-replace-body')).to_have_text(copy(
            page, 'nutrition.plan.confirm_body', next=OPTIONS[1]['isim'], current=PLAN['isim']))
        expect(page.locator('#plan-replace-cancel')).to_be_focused()  # the safe choice first
        if dismiss == 'escape':
            page.keyboard.press('Escape')
        elif dismiss == 'keep':
            page.locator('#plan-replace-cancel').click()
        else:
            modal.click(position={'x': 5, 'y': 5})
        expect(modal).not_to_have_class(OPEN)
        expect(page.locator('#sel-btn-1')).to_be_focused()           # focus returns to the option
    assert paths(traffic) == Counter()
    assert stored(app, auth_user.id) == [PLAN]
    assert_current(page, PLAN['isim'])
    assert errors == []


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_confirmed_replacement_saves_once_and_plan_and_today_converge(
        app, auth_user, training_page, generator, language):
    page, traffic, errors = start(app, auth_user, training_page, language)
    open_plan(page)
    open_builder(page)
    generate(page)
    # Before the save Today still uses A.
    titles = today_titles(page)
    expect(titles.first).to_contain_text(PLAN['isim'])
    open_plan(page)
    choose(page, 1)
    traffic.clear()
    page.locator('#plan-replace-confirm').click()
    assert_current(page, OPTIONS[1]['isim'])
    expect(page.locator('#plan-current-status')).to_have_text(copy(page, 'nutrition.plan.saved'))
    expect(page.locator('#plan-current-title')).to_be_focused()
    expect(page.locator('#plan-builder')).to_be_hidden()
    expect(page.locator('#plans-grid .plan-card')).to_have_count(0)  # proposals consumed
    page.wait_for_timeout(200)
    assert paths(traffic) == Counter({'/nutrition-plan/save': 1, '/nutrition-plan/active': 1})
    assert stored(app, auth_user.id) == [OPTIONS[1]]
    traffic.clear()
    titles = today_titles(page)
    expect(titles).to_have_count(2)
    for title in titles.all():
        expect(title).to_contain_text(OPTIONS[1]['isim'])
    assert paths(traffic) == Counter({'/meal-log/today': 1})         # no second plan read
    assert errors == []


def test_first_plan_needs_no_replacement_confirmation(app, auth_user, training_page, generator):
    page, traffic, _ = start(app, auth_user, training_page, plan=False)
    open_plan(page)
    open_builder(page)
    generate(page)
    traffic.clear()
    page.locator('#sel-btn-0').click()
    expect(page.locator('#plan-replace-modal')).not_to_have_class(OPEN)
    assert_current(page, OPTIONS[0]['isim'])
    assert paths(traffic) == Counter({'/nutrition-plan/save': 1, '/nutrition-plan/active': 1})
    assert stored(app, auth_user.id) == [OPTIONS[0]]


def test_refused_save_keeps_the_current_plan(app, auth_user, training_page, generator):
    """A real server refusal: the option breaks the closed plan schema. The route
    validates before it deletes, so the current plan survives untouched."""
    generator['plans'] = [dict(OPTIONS[0], extra='not in the schema'), OPTIONS[1]]
    page, traffic, errors = start(app, auth_user, training_page)
    open_plan(page)
    open_builder(page)
    generate(page, count=2)
    choose(page, 0)
    traffic.clear()
    page.locator('#plan-replace-confirm').click()
    builder = page.locator('#plan-builder')
    expect(builder).to_have_attribute('data-save-state', 'rejected')
    expect(page.locator('#plan-gen-status')).to_have_text(copy(page, 'nutrition.plan.save_rejected'))
    expect(page.locator('#plan-current-status')).to_have_text('')    # no success claimed
    assert_current(page, PLAN['isim'])
    for button in page.locator('#plans-grid .btn-select-plan').all():
        expect(button).to_be_enabled()                               # explicit retry allowed
    assert [s for p, _, s in traffic if p == '/nutrition-plan/save'] == [400]
    assert paths(traffic)['/nutrition-plan/active'] == 0
    assert stored(app, auth_user.id) == [PLAN]
    assert errors == []


AMBIGUOUS = ('committed_5xx', 'not_committed_5xx', 'network', 'unconfirmable')


@pytest.mark.parametrize('case', AMBIGUOUS)
def test_ambiguous_save_is_never_resent_and_rereads_once(
        app, auth_user, client, training_page, generator, case):
    page, traffic, errors = start(app, auth_user, training_page)
    open_plan(page)
    open_builder(page)
    generate(page)
    saves = []

    def ambiguous(route):
        saves.append(route.request.post_data)
        if case == 'committed_5xx':
            forward(client, route)                                   # the replacement commits…
            route.fulfill(status=503, content_type='application/json', body='{}')  # …answer lost
        elif case == 'network':
            route.abort()
        else:
            route.fulfill(status=502, content_type='text/html', body='<html>bad gateway</html>')
    page.route('**/nutrition-plan/save', ambiguous)
    reads = []
    if case == 'unconfirmable':
        def read_fails(route):
            reads.append(1)
            fail(route)
        page.route('**/nutrition-plan/active', read_fails)
    choose(page, 1)
    traffic.clear()
    page.locator('#plan-replace-confirm').click()
    builder = page.locator('#plan-builder')
    if case == 'committed_5xx':
        assert_current(page, OPTIONS[1]['isim'])                     # proven by the re-read
        expect(page.locator('#plan-current-status')).to_have_text(copy(page, 'nutrition.plan.saved'))
    elif case == 'unconfirmable':
        expect(builder).to_have_attribute('data-save-state', 'unconfirmed')
        expect(page.locator('#plan-gen-status')).to_have_text(copy(page, 'nutrition.plan.save_unconfirmed'))
        expect(page.locator('#plan-current')).to_have_attribute('data-plan-state', 'unavailable')
        for button in page.locator('#plans-grid .btn-select-plan').all():
            expect(button).to_be_disabled()
        page.evaluate('() => { selectPlan(1); savePlanOption(1); confirmPlanReplace(); }')
        assert reads == [1]
    else:
        expect(builder).to_have_attribute('data-save-state', 'failed')
        expect(page.locator('#plan-gen-status')).to_have_text(copy(page, 'nutrition.plan.save_not_replaced'))
        assert_current(page, PLAN['isim'])                           # proven unchanged
        expect(page.locator('#sel-btn-1')).to_be_enabled()
    page.wait_for_timeout(300)
    assert len(saves) == 1                                           # never silently re-sent
    if case != 'unconfirmable':
        assert paths(traffic) == Counter({'/nutrition-plan/active': 1})
    expected = [OPTIONS[1]] if case == 'committed_5xx' else [PLAN]
    assert stored(app, auth_user.id) == expected
    if case in ('not_committed_5xx', 'network'):
        # The proven-old state allows an explicit, confirmed retry: one more save.
        page.unroute('**/nutrition-plan/save', ambiguous)
        choose(page, 1)
        page.locator('#plan-replace-confirm').click()
        assert_current(page, OPTIONS[1]['isim'])
        assert stored(app, auth_user.id) == [OPTIONS[1]]
    assert errors == []


# ── 12 · SINGLE FLIGHT ──────────────────────────────────────────────────


def test_generate_and_save_are_single_flight(app, auth_user, training_page, generator):
    page, traffic, errors = start(app, auth_user, training_page)
    open_plan(page)
    open_builder(page)
    pick_foods(page)
    held = []
    page.route('**/nutrition-plan', lambda route: held.append(route))
    button = page.locator('#plan-btn')
    button.click()
    expect(button).to_be_disabled()
    expect(button).to_have_attribute('aria-busy', 'true')
    expect(page.locator('#plan-gen-status')).to_have_text(copy(page, 'nutrition.plan.generating'))
    assert_current(page, PLAN['isim'])                               # nothing blanked while loading
    page.evaluate("""() => { const b = document.getElementById('plan-btn');
        b.disabled = false; b.click(); b.click(); generatePlan(); generatePlan(); }""")
    button.focus()
    page.keyboard.press('Enter')
    page.wait_for_timeout(300)
    assert len(held) == 1                                            # exactly one AI request
    page.unroute('**/nutrition-plan')
    held[0].fallback()
    expect(page.locator('#plans-grid .plan-card')).to_have_count(3)
    assert generator['calls'] == 1

    saves = []
    page.route('**/nutrition-plan/save', lambda route: saves.append(route))
    choose(page, 2)
    confirm = page.locator('#plan-replace-confirm')
    confirm.click()
    expect(page.locator('#plan-builder')).to_have_attribute('data-save-state', 'saving')
    page.evaluate("""() => { const c = document.getElementById('plan-replace-confirm');
        c.disabled = false; c.click(); confirmPlanReplace(); savePlanOption(2); savePlanOption(0);
        const s = document.getElementById('sel-btn-0'); s.disabled = false; s.click(); selectPlan(0); }""")
    expect(page.locator('#plan-replace-modal')).not_to_have_class(OPEN)
    assert_current(page, PLAN['isim'])                               # no optimistic "active"
    page.wait_for_timeout(300)
    assert len(saves) == 1
    page.unroute('**/nutrition-plan/save')
    saves[0].fallback()
    assert_current(page, OPTIONS[2]['isim'])
    assert [p for p, _, _ in traffic].count('/nutrition-plan/save') == 1
    assert stored(app, auth_user.id) == [OPTIONS[2]]
    assert errors == []


# ── 13–14 · TOPOLOGY ────────────────────────────────────────────────────


def test_request_topology_per_step(app, auth_user, training_page, generator):
    page, traffic, errors = start(app, auth_user, training_page)
    page.wait_for_timeout(300)
    assert paths(traffic) == INITIAL, paths(traffic)                 # A. initial: unchanged
    steps = {}

    def step(name, action):
        traffic.clear()
        action()
        page.wait_for_timeout(300)
        steps[name] = paths(traffic)

    step('B select Plan', lambda: open_plan(page))
    step('C Plan to Today', lambda: today_titles(page))
    step('D Today to Plan', lambda: open_plan(page))
    step('open builder', lambda: open_builder(page))
    step('E generate', lambda: generate(page))
    step('F choose option', lambda: choose(page, 0))
    step('G confirmed save', lambda: (page.locator('#plan-replace-confirm').click(),
                                      assert_current(page, OPTIONS[0]['isim'])))
    step('Today after save', lambda: today_titles(page))
    step('idle', lambda: page.wait_for_timeout(1500))
    assert steps == {
        'B select Plan': Counter(),
        'C Plan to Today': Counter({'/meal-log/today': 1}),
        'D Today to Plan': Counter(),
        'open builder': Counter(),
        'E generate': Counter({'/nutrition-plan': 1}),
        'F choose option': Counter(),
        'G confirmed save': Counter({'/nutrition-plan/save': 1, '/nutrition-plan/active': 1}),
        'Today after save': Counter({'/meal-log/today': 1}),
        'idle': Counter(),                                           # no polling
    }, steps
    assert generator['calls'] == 1
    assert not any(p.startswith('/supplements') for p, _, _ in traffic)
    assert errors == []


def test_opening_plan_calls_no_generation_or_provider(app, auth_user, training_page, generator):
    page, traffic, _ = start(app, auth_user, training_page)
    for _ in range(3):
        open_plan(page)
        today_titles(page)
    open_plan(page)
    open_builder(page)
    page.locator('#plan-builder-cancel').click()
    expect(page.locator('#plan-builder')).to_be_hidden()
    expect(page.locator('#plan-replace-btn')).to_be_focused()
    page.wait_for_timeout(300)
    assert generator['calls'] == 0
    assert all(p != '/nutrition-plan' for p, _, _ in traffic)
    assert paths(traffic)['/nutrition-plan/active'] == 1


# ── RACES ───────────────────────────────────────────────────────────────


def test_slow_active_read_is_shared_across_mode_switches(app, auth_user, client, training_page):
    page, _, _, _ = training_page
    held = []
    page.route('**/nutrition-plan/active', lambda route: held.append(route))
    page, traffic, errors = start(app, auth_user, training_page)
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'loading')
    for _ in range(2):
        page.locator('#nutrition-tab-plan').click()
        expect(page.locator('#plan-current')).to_have_attribute('data-plan-state', 'loading')
        expect(page.locator('#plan-current')).not_to_contain_text(copy(page, 'nutrition.plan.absent'))
        page.locator('#nutrition-tab-today').click()
    assert len(held) == 1
    page.unroute('**/nutrition-plan/active')
    fulfill_from(held[0], forward(client, held[0]))
    expect(page.locator('#quick-add-section')).to_have_attribute('data-plan-state', 'available')
    open_plan(page)
    assert_current(page, PLAN['isim'])
    assert paths(traffic)['/nutrition-plan/active'] == 0             # one read, served by the hold
    assert errors == []


def test_generation_survives_mode_switches(app, auth_user, training_page, generator):
    page, traffic, errors = start(app, auth_user, training_page)
    open_plan(page)
    open_builder(page)
    pick_foods(page)
    held = []
    page.route('**/nutrition-plan', lambda route: held.append(route))
    page.locator('#plan-btn').click()
    today_titles(page)
    open_plan(page)
    expect(page.locator('#plan-btn')).to_be_disabled()
    page.unroute('**/nutrition-plan')
    held[0].fallback()
    expect(page.locator('#plans-grid .plan-card')).to_have_count(3)
    assert len(held) == 1 and generator['calls'] == 1
    assert_current(page, PLAN['isim'])
    assert errors == []


def test_a_stale_active_read_never_overwrites_a_confirmed_save(app, auth_user, client, training_page, generator):
    """An older read of plan A that answers after B was saved and re-read is dropped."""
    page, traffic, errors = start(app, auth_user, training_page)
    open_plan(page)
    open_builder(page)
    generate(page)
    stale = []

    def capture(route):
        stale.append((route, forward(client, route)))               # answered as A, delivered later
    page.route('**/nutrition-plan/active', capture)
    page.evaluate('() => retryPlanShortcuts()')                      # a forced read of A, in flight
    page.wait_for_timeout(200)
    assert len(stale) == 1
    page.unroute('**/nutrition-plan/active', capture)
    choose(page, 2)
    page.locator('#plan-replace-confirm').click()
    assert_current(page, OPTIONS[2]['isim'])
    fulfill_from(*stale[0])                                          # A arrives last
    page.wait_for_timeout(300)
    assert_current(page, OPTIONS[2]['isim'])
    titles = today_titles(page)
    for title in titles.all():
        expect(title).to_contain_text(OPTIONS[2]['isim'])
    assert errors == []


def test_old_plan_quick_add_answer_never_marks_the_new_plan_logged(
        app, auth_user, client, training_page, generator):
    page, traffic, errors = start(app, auth_user, training_page)
    open_plan(page)
    open_builder(page)
    generate(page)
    titles = today_titles(page)
    expect(titles.first).to_contain_text(PLAN['isim'])
    pending = []

    def committed_then_held(route):
        pending.append((route, forward(client, route)))             # A's breakfast is logged now
    page.route('**/api/quick-add-meal', committed_then_held)
    with app.app_context():
        before = MealLog.query.filter_by(user_id=auth_user.id).count()
    page.locator('#qab-kahvalti').click()
    page.wait_for_timeout(200)
    assert len(pending) == 1
    open_plan(page)
    choose(page, 0)
    page.locator('#plan-replace-confirm').click()
    assert_current(page, OPTIONS[0]['isim'])
    fulfill_from(*pending[0])                                        # A's answer arrives after B
    titles = today_titles(page)
    expect(titles.first).to_contain_text(OPTIONS[0]['isim'])
    row = page.locator('#qab-kahvalti')
    expect(row).to_be_enabled()
    expect(row.locator('.qab-badge')).to_have_text(copy(page, 'nutrition.planned'))
    expect(row).not_to_have_attribute('data-write-state', 'logged')
    with app.app_context():
        assert MealLog.query.filter_by(user_id=auth_user.id).count() == before + 1
    assert errors == []


# ── SECURITY ────────────────────────────────────────────────────────────

MARKUP = '<img src=x onerror="window.__pwned=1">'


def test_generated_and_persisted_markup_renders_as_text(app, auth_user, training_page, generator):
    generator['plans'] = [dict(option(MARKUP, 450, '<b>Bold</b>'))]
    page, _, errors = start(app, auth_user, training_page)
    open_plan(page)
    open_builder(page)
    generate(page, count=1)
    grid = page.locator('#plans-grid')
    expect(grid.locator('img')).to_have_count(0)
    expect(grid.locator('b')).to_have_count(0)
    expect(grid.locator('.plan-card-name')).to_have_text(MARKUP)
    choose(page, 0)
    expect(page.locator('#plan-replace-body')).to_contain_text(MARKUP)
    page.locator('#plan-replace-confirm').click()
    assert_current(page, MARKUP)                                     # persisted, then re-read
    detail = page.locator('#active-plan-detail')
    expect(detail.locator('img')).to_have_count(0)
    expect(detail.locator('b')).to_have_count(0)
    assert page.evaluate('() => window.__pwned') is None
    assert errors == []


# ── ACCESSIBILITY ───────────────────────────────────────────────────────


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_plan_headings_keyboard_dialog_and_focus(app, auth_user, training_page, generator, language):
    page, _, errors = start(app, auth_user, training_page, language)
    expect(page.locator('h1')).to_have_count(1)
    expect(page.get_by_role('tab')).to_have_count(2)
    tab = page.locator('#nutrition-tab-today')
    tab.focus()
    page.keyboard.press('ArrowRight')                                # keyboard mode switch
    expect(page.locator('#nutrition-tab-plan')).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#plan-current')).not_to_have_attribute('data-plan-state', 'loading')
    replace = page.locator('#plan-replace-btn')
    replace.focus()
    page.keyboard.press('Enter')
    expect(page.locator('#plan-builder-title')).to_be_focused()
    expect(replace).to_be_hidden()
    chip = page.locator('#protein-hayvansal .chip').first
    chip.focus()
    assert page.evaluate('() => getComputedStyle(document.activeElement).outlineStyle') != 'none'
    page.keyboard.press('Space')
    expect(chip).to_have_attribute('aria-pressed', 'true')
    for group in ('#karb-list', '#yag-list'):
        page.locator(group + ' .chip').first.focus()
        page.keyboard.press('Enter')
    page.locator('#plan-btn').focus()
    page.keyboard.press('Enter')
    expect(page.locator('#plans-grid .plan-card')).to_have_count(3)
    expect(page.locator('#plan-gen-status')).to_have_attribute('role', 'status')
    levels = page.locator('#panel-plan').evaluate(
        "p => [...p.querySelectorAll('h2,h3,h4,h5,h6')].filter(h => h.offsetParent)"
        ".map(h => +h.tagName[1])")
    assert levels[0] == 2, levels
    assert all(b <= a + 1 for a, b in zip(levels, levels[1:])), levels
    use = page.locator('#sel-btn-0')
    use.focus()
    assert page.evaluate('() => getComputedStyle(document.activeElement).outlineStyle') != 'none'
    page.keyboard.press('Enter')
    dialog = page.get_by_role('dialog', name=copy(page, 'nutrition.plan.confirm_title'))
    expect(dialog).to_be_visible()
    inside = "() => document.getElementById('plan-replace-modal').contains(document.activeElement)"
    for key in ('Tab', 'Tab', 'Tab', 'Shift+Tab', 'Shift+Tab', 'Shift+Tab'):
        page.keyboard.press(key)
        assert page.evaluate(inside), key                            # focus is contained
    page.keyboard.press('Escape')
    expect(use).to_be_focused()
    assert errors == []


# ── RESPONSIVE / REDUCED MOTION / LARGE TEXT ────────────────────────────

LONG_TR = ('Fırında zeytinyağlı sebzeli tavuk göğsü ve bulgur pilavı yanında yoğurtlu '
           'semizotu salatası ile ev yapımı tam buğday ekmeği')
GEOMETRY = """() => {
  const overflow = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1;
  const clipped = [...document.querySelectorAll('#panel-plan *, #plan-replace-modal *')]
    .filter(e => e.offsetParent && e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'auto')
    .map(e => e.id || e.className);
  return {overflow, clipped, vw: innerWidth, vh: innerHeight};
}"""


def assert_control(page, selector, width, height=None):
    box = page.locator(selector).first.bounding_box()
    assert box, selector
    assert box['height'] >= 43.5 and box['width'] >= 43.5, (width, selector, box)
    assert box['x'] >= -0.5 and box['x'] + box['width'] <= width + 0.5, (width, selector, box)
    if height is not None:
        assert box['y'] >= -0.5 and box['y'] + box['height'] <= height + 0.5, (width, selector, box)


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_plan_layout_holds_from_320_to_1366(app, auth_user, training_page, generator, language):
    generator['plans'] = [option('Uzun isimli ' + LONG_TR[:60], 450, LONG_TR), OPTIONS[1], OPTIONS[2]]
    page, _, errors = start(app, auth_user, training_page, language, size=(320, 700))
    open_plan(page)
    for width in (320, 390, 430, 768, 1024, 1366):
        page.set_viewport_size({'width': width, 'height': 800})
        g = page.evaluate(GEOMETRY)
        assert not g['overflow'] and g['clipped'] == [], (width, g)
        assert_control(page, '#plan-replace-btn', width)
    open_builder(page)
    generate(page)
    for width in (320, 390, 430, 768, 1024, 1366):
        page.set_viewport_size({'width': width, 'height': 800})
        g = page.evaluate(GEOMETRY)
        assert not g['overflow'] and g['clipped'] == [], (width, g)
        for control in ('#plan-btn', '#plan-builder-cancel', '#protein-hayvansal .chip',
                        '#sel-btn-0', '#sel-btn-2', '.custom-food-row .btn-ghost'):
            assert_control(page, control, width)
        name = page.locator('#plan-card-name-0')
        assert name.evaluate('e => e.scrollWidth <= e.clientWidth + 1'), width
        page.locator('#sel-btn-1').scroll_into_view_if_needed()
        choose(page, 1)
        g = page.evaluate(GEOMETRY)
        assert not g['overflow'] and g['clipped'] == [], (width, g)
        for control in ('#plan-replace-confirm', '#plan-replace-cancel'):
            assert_control(page, control, width, 800)                # modal actions on screen
        page.keyboard.press('Escape')
    assert errors == []


def test_reduced_motion_and_large_text_keep_states_readable(app, auth_user, training_page, generator):
    page, _, _, _ = training_page
    page.emulate_media(reduced_motion='reduce')
    page, _, errors = start(app, auth_user, training_page, language='tr', size=(320, 700))
    page.add_style_tag(content='html { font-size: 150% !important; }')
    open_plan(page)
    open_builder(page)
    generate(page)
    expect(page.locator('#plan-gen-status')).to_have_text(copy(page, 'nutrition.plan.options_ready', n=3))
    choose(page, 0)
    assert_control(page, '#plan-replace-confirm', 320)
    page.locator('#plan-replace-cancel').click()
    g = page.evaluate(GEOMETRY)
    assert not g['overflow'], g
    assert page.evaluate("() => matchMedia('(prefers-reduced-motion: reduce)').matches")
    assert errors == []
