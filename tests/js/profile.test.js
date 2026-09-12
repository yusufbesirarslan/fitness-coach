const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function loadProfilePage(response = { ok: true, json: async () => ({ message: 'saved' }) }) {
  const documentListeners = {};
  const elementListeners = {};
  const requests = [];
  const notices = [];
  const scheduled = [];

  const elements = {
    'full_name': { value: 'Yusuf B' },
    'username': { value: 'testuser', addEventListener() {} },
    'target_weight': { value: '75' },
    'save-btn': { disabled: false, textContent: 'Kaydet' },
    'toast-wrap': { appendChild(el) { notices.push(el); } },
    'avatar-file-input': {
      addEventListener(type, listener) { elementListeners[type] = listener; },
    },
    'avatar-display': {
      querySelector(selector) {
        if (selector === '.pf-avatar-overlay') return {};
        if (selector === 'span') return { remove() {} };
        return null;
      },
      insertBefore() {},
    },
  };

  const document = {
    body: { getAttribute() { return 'kilo verme'; } },
    addEventListener(type, listener) { documentListeners[type] = listener; },
    getElementById(id) { return elements[id] || null; },
    querySelectorAll() { return []; },
    createElement() { return { className: '', textContent: '', remove() {} }; },
  };
  const context = {
    document,
    window: { t(key) { return key; }, location: { reload() {} } },
    location: { reload() {} },
    setTimeout(callback) { scheduled.push(callback); },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return response;
    },
    FileReader: class {
      readAsDataURL() {
        this.onload({ target: { result: 'data:image/png;base64,avatar' } });
      }
    },
  };
  context.window.fetch = context.fetch;
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, '../../static/profile.js'), 'utf8'),
    context,
  );
  documentListeners.DOMContentLoaded();
  return { context, elements, elementListeners, requests, notices, scheduled };
}

test('selecting a profile photo persists it without a second save action', async () => {
  const page = loadProfilePage();

  page.elementListeners.change({
    target: { files: [{ size: 128, type: 'image/png' }] },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(page.requests.length, 1);
  assert.equal(page.requests[0].url, '/edit-profile');
  const payload = JSON.parse(page.requests[0].options.body);
  assert.deepEqual(Object.keys(payload), ['profile_picture']);
  assert.equal(payload.profile_picture, 'data:image/png;base64,avatar');
  assert.equal(page.notices.length, 1);
  assert.equal(page.notices[0].textContent, 'saved');
  assert.equal(page.scheduled.length, 1);
});

test('rejected avatar save does not claim success or reload the page', async () => {
  const page = loadProfilePage({
    ok: false,
    json: async () => ({ error: 'Image is too large' }),
  });

  page.elementListeners.change({
    target: { files: [{ size: 128, type: 'image/png' }] },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(page.requests.length, 1);
  assert.equal(page.notices.length, 1);
  assert.equal(page.notices[0].textContent, 'Image is too large');
  assert.equal(page.notices[0].className, 'toast toast-error');
  assert.equal(page.scheduled.length, 1);

  await vm.runInContext('saveProfile()', page.context);
  const settings = JSON.parse(page.requests[1].options.body);
  assert.equal(Object.hasOwn(settings, 'profile_picture'), false);
});

test('photo above the server limit is rejected before upload', () => {
  const page = loadProfilePage();

  page.elementListeners.change({
    target: { files: [{ size: 400000, type: 'image/png' }] },
  });

  assert.equal(page.requests.length, 0);
  assert.equal(page.notices.length, 1);
  assert.equal(page.notices[0].className, 'toast toast-error');
});

test('a settings save keeps the photo picker locked until its reload', async () => {
  const page = loadProfilePage();

  await vm.runInContext('saveProfile()', page.context);

  assert.equal(page.requests.length, 1);
  assert.equal(page.elements['avatar-file-input'].disabled, true);
  assert.equal(page.elements['save-btn'].disabled, true);
});
