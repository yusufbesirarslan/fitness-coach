/* F2 — nutrition-plan rendering must not emit attacker-controlled markup.
 *
 * `GET /nutrition-plan/active` returns a stored document and static/nutrition.js
 * interpolates it into innerHTML. The CSP blocks script execution, so the thing
 * to prove is NOT "alert() never fires" — it is that no persisted byte reaches
 * the page as markup at all. `style-src-attr 'unsafe-inline'` is still on (the
 * app needs dynamic width/colour attributes), so a single injected
 * <div style="position:fixed;inset:0"> is a working UI-redress overlay.
 *
 * These load the real static/nutrition.js — no copied template strings — and
 * assert on the HTML the render functions actually produce.
 *
 *     node --test tests/js/nutrition_plan_render.test.js
 *
 * Not reachable from CI's `pytest -q` on its own; tests/test_plan_save_validation.py
 * runs this file through node and enforces the same contract statically, so the
 * property is gated even where node is absent.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = path.join(__dirname, '..', '..', 'static', 'nutrition.js');

/* The two payloads, and what each one would win if it escaped.
 * OVERLAY: a full-viewport click-jacking surface. MARKUP: attribute injection. */
const OVERLAY = '<div style="position:fixed;inset:0;z-index:99999">owned</div>';
const MARKUP = '<img src=x onerror="steal()">';

/* What must never survive into the HTML: either payload verbatim, and the two
 * tag openings that would make them live. Escaped text may still READ like an
 * attack ("onerror=" with no angle bracket in front of it is just characters) —
 * an unescaped `<` is what turns it into one, so that is what is asserted. */
const FORBIDDEN = [OVERLAY, MARKUP, '<img', '<div style="position'];

function assertNoMarkup(html, label) {
  for (const needle of FORBIDDEN) {
    assert.ok(
      !html.includes(needle),
      `${label} emitted raw markup ${JSON.stringify(needle)} in:\n${html}`);
  }
}

function makeElement(id, sink) {
  const el = {
    id,
    textContent: '',
    value: '',
    disabled: false,
    style: {},
    dataset: {},
    className: '',
    classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
    addEventListener() {},
    removeEventListener() {},
    appendChild(child) { sink.push(child); },
    insertBefore() {},
    remove() {},
    setAttribute() {},
    getAttribute() { return null; },
    querySelector() { return makeElement('q', sink); },
    querySelectorAll() { return []; },
    closest() { return null; },
    focus() {},
    scrollIntoView() {},
    getBoundingClientRect() { return { top: 0, bottom: 0, height: 0 }; },
  };
  let html = '';
  Object.defineProperty(el, 'innerHTML', {
    get() { return html; },
    set(value) { html = String(value); sink.push(el); },
  });
  return el;
}

function loadNutritionPage(activePlanResponse, saveResponse) {
  const source = fs.readFileSync(SOURCE, 'utf8');
  const sink = [];
  const elements = {};
  const document = {
    body: makeElement('body', sink),
    documentElement: makeElement('html', sink),
    addEventListener() {},
    removeEventListener() {},
    getElementById(id) {
      if (!elements[id]) elements[id] = makeElement(id, sink);
      return elements[id];
    },
    querySelector() { return makeElement('sel', sink); },
    querySelectorAll() { return []; },
    createElement() { return makeElement('created', sink); },
  };
  const context = {
    document,
    console,
    history: { state: null, replaceState(state) { this.state = state; }, pushState(state) { this.state = state; } },
    window: {
      // Keys stay visible; interpolated values are appended so a test can
      // see that (escaped) data reached the rendered string.
      t: (k, vars) => (vars ? k + ' ' + Object.values(vars).join(' ') : k),
      LOCALE: 'tr',
      addEventListener() {},
      requestAnimationFrame() {},
      matchMedia: () => ({ matches: false, addEventListener() {} }),
    },
    fetch: (url, init) => {
      if (activePlanResponse && String(url).startsWith('/nutrition-plan/active')) {
        const body = typeof activePlanResponse === 'function' ? activePlanResponse() : activePlanResponse;
        return Promise.resolve({ ok: true, json: async () => body });
      }
      if (saveResponse && String(url).startsWith('/nutrition-plan/save')) {
        saveResponse.sent.push(init);
        return Promise.resolve({
          ok: saveResponse.ok,
          status: saveResponse.status,
          json: async () => saveResponse.body,
        });
      }
      return new Promise(() => {});
    },
    setTimeout, clearTimeout, setInterval, clearInterval,
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    crypto: { randomUUID: () => 'uuid', getRandomValues: (a) => a },
    navigator: { language: 'tr' },
    location: { pathname: '/nutrition', search: '', href: 'http://localhost/nutrition' },
    Intl, Date, JSON, Math,
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'nutrition.js' });
  return { context, elements, html: (id) => (elements[id] ? elements[id].innerHTML : '') };
}

/* A stored plan in which EVERY slot the page renders carries a payload —
 * including the numeric ones, which the page interpolates without esc(). */
function poisonedPlan() {
  const meal = () => ({
    yemekler: [MARKUP, OVERLAY],
    kalori: OVERLAY,
    protein: MARKUP,
    karb: OVERLAY,
    yag: MARKUP,
  });
  return {
    isim: OVERLAY,
    kahvalti: meal(),
    ogle: meal(),
    aksam: meal(),
    ara_ogun: meal(),
    toplam_kalori: OVERLAY,
    toplam_protein: MARKUP,
    toplam_karb: OVERLAY,
    toplam_yag: MARKUP,
  };
}

test('esc() neutralises every HTML metacharacter', () => {
  const { context } = loadNutritionPage();
  assert.equal(context.esc(OVERLAY),
    '&lt;div style=&quot;position:fixed;inset:0;z-index:99999&quot;&gt;owned&lt;/div&gt;');
  assert.equal(context.esc(null), '');
});

test('renderActivePlanDetail emits no markup from a poisoned stored plan', () => {
  const { context, html } = loadNutritionPage();
  context.renderActivePlanDetail(poisonedPlan(), OVERLAY, MARKUP);
  const rendered = html('active-plan-detail');
  assert.ok(rendered.length > 0, 'nothing was rendered');
  assertNoMarkup(rendered, 'renderActivePlanDetail');
  assert.ok(rendered.includes('&lt;'), 'the payload was dropped, not escaped');
});

test('renderActivePlanDetail still shows real numbers and names', () => {
  const { context, html } = loadNutritionPage();
  context.renderActivePlanDetail({
    isim: 'Plan A',
    ogle: { yemekler: ['Tavuk - 150g'], kalori: 380 },
    toplam_kalori: 1477, toplam_protein: 136, toplam_karb: 123, toplam_yag: 44,
  }, 8.4, '01.02.2026');
  const rendered = html('active-plan-detail');
  for (const shown of ['Plan A', 'Tavuk - 150g', '380', '1477', '136', '8.4', '01.02.2026']) {
    assert.ok(rendered.includes(shown), `${shown} is missing from:\n${rendered}`);
  }
});

test('a legacy non-numeric macro renders as a placeholder, never as markup', () => {
  const { context, html } = loadNutritionPage();
  context.renderActivePlanDetail(
    { ogle: { yemekler: ['x'], kalori: '400 kcal' }, toplam_kalori: OVERLAY }, 8, '01.02.2026');
  const rendered = html('active-plan-detail');
  assertNoMarkup(rendered, 'legacy macro');
  assert.ok(rendered.includes('—'), 'no placeholder was substituted');
});

test('a legacy numeric string still renders its value', () => {
  const { context, html } = loadNutritionPage();
  context.renderActivePlanDetail(
    { ogle: { yemekler: ['x'], kalori: '420' } }, '8', '01.02.2026');
  assert.ok(html('active-plan-detail').includes('420'));
});

test('loadQuickAddSection emits no markup from a poisoned stored plan', async () => {
  const { context, html } = loadNutritionPage(
    { exists: true, plan: poisonedPlan(), score: OVERLAY, created_at: MARKUP });
  context.invalidateActivePlan();
  await context.loadQuickAddSection();
  const rendered = html('quick-add-cards');
  assert.ok(rendered.length > 0, 'nothing was rendered');
  assertNoMarkup(rendered, 'loadQuickAddSection');
});

test('renderPlans emits no markup from a poisoned generator response', () => {
  const { context, elements, html } = loadNutritionPage();
  context.renderPlans({
    overall_score: OVERLAY,
    score_label: MARKUP,
    planlar: [poisonedPlan()],
  });
  assertNoMarkup(html('score-banner-wrap'), 'renderPlans score banner');
  for (const element of Object.values(elements)) {
    assertNoMarkup(element.innerHTML || '', `renderPlans (#${element.id})`);
  }
});

/* F2/F3 gave the save route two new ways to say no. The old `selectPlan()`
 * never looked at the response, so a refused save lit the card up as the
 * active plan. NUTR-PR5: an option is a proposal until the save answers, and
 * only a canonical re-read draws the current plan. */
const OPTION = { isim: 'Plan A', ogle: { yemekler: ['Tavuk - 150g'], kalori: 380 } };

function offer(context) {
  vm.runInContext(`_planOptions = { plans: [${JSON.stringify(OPTION)}], score: 8 };`, context);
}

test('a refused save changes nothing and marks nothing current', async () => {
  const refusal = {
    ok: false, status: 400, sent: [],
    body: { error: 'Nutrition plan data is invalid.', code: 'nutrition_plan_invalid' },
  };
  const { context, elements } = loadNutritionPage({ exists: false }, refusal);
  await context.loadActivePlan();
  offer(context);
  context.renderPlans({ overall_score: 8, planlar: [OPTION] });
  await context.selectPlan(0);
  assert.equal(refusal.sent.length, 1, 'the save was never attempted');
  assert.equal(elements['plan-builder'].dataset.saveState, 'rejected');
  assert.equal(vm.runInContext('_planShown.state', context), 'absent',
    'a refused save changed the drawn current plan');
  assert.ok(!elements['active-plan-detail'].innerHTML.includes('Tavuk - 150g'),
    'the refused option was drawn as the current plan');
});

test('an accepted save draws the plan only from the canonical re-read', async () => {
  const accepted = { ok: true, status: 200, sent: [], body: { message: 'ok' } };
  let stored = { exists: false };
  const { context, elements } = loadNutritionPage(() => stored, accepted);
  await context.loadActivePlan();
  offer(context);
  context.renderPlans({ overall_score: 8, planlar: [OPTION] });
  assert.ok(!elements['active-plan-detail'].innerHTML.includes('Tavuk - 150g'),
    'a generated option was drawn as the current plan before any save');
  stored = { exists: true, plan: OPTION, score: 8, created_at: '01.10.2026' };
  await context.selectPlan(0);
  assert.equal(accepted.sent.length, 1);
  assert.deepEqual(JSON.parse(accepted.sent[0].body), { plan: OPTION, score: 8 });
  assert.equal(vm.runInContext('_planShown.state', context), 'active');
  assert.ok(elements['active-plan-detail'].innerHTML.includes('Tavuk - 150g'));
  assert.equal(elements['plan-current-status'].textContent, 'nutrition.plan.saved');
});
