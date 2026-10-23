"""PRIV-LAUNCH-02: backend account pages never start optional measurement.

The rendered shell and actual helper runtime are both checked: merely hiding
one loader must not leave an event sender or activation-storage writer behind.
"""
from pathlib import Path
import subprocess

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def test_rendered_account_shell_has_no_google_measurement(client, auth_user):
    response = client.get('/setup')
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, 'html.parser')
    sources = [node.get('src', '') for node in soup.find_all('script')]
    assert any(src.startswith('/static/analytics.js') for src in sources)
    assert not any('googletagmanager' in src or 'google-analytics' in src
                   for src in sources)
    scripts = '\n'.join(node.get_text() for node in soup.find_all('script'))
    assert 'gtag(' not in scripts
    assert 'dataLayer' not in scripts


def test_measurement_helpers_have_no_transmission_storage_or_dom_side_effects():
    result = subprocess.run(['node', '-e', r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const forbidden = new Proxy({}, {get() { throw Error('side effect'); }});
const window = {
  gtag() { throw Error('transmission'); },
  fetch() { throw Error('transmission'); },
  localStorage: forbidden,
  sessionStorage: forbidden,
};
const context = {window, document: forbidden, localStorage: forbidden,
  navigator: forbidden, fetch() { throw Error('transmission'); }};
vm.runInNewContext(fs.readFileSync('static/analytics.js', 'utf8'), context);
for (const name of ['fxTrack', 'fxTrackOnce', 'fxActivation']) {
  assert.equal(typeof window[name], 'function');
  assert.equal(window[name]('first_plan', {source: 'setup'}), undefined);
  assert.equal(window[name]('first_plan', {source: 'setup'}), undefined);
}
'''], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
