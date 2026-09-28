"""Rendered Nutrition hierarchy and canonical server request budget."""
from html.parser import HTMLParser

import pytest
from sqlalchemy import event
from app.extensions import db


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.rows.append((tag, attrs, [a.get('id') for _, a in self.stack]))
        if tag not in {'meta', 'link', 'input', 'img', 'br', 'hr', 'path', 'circle', 'polyline', 'line'}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def render(client, make_user, login, language):
    make_user('pr2-' + language, profile_complete=True, language=language)
    login('pr2-' + language)
    return client.get('/nutrition').get_data(as_text=True)


@pytest.mark.parametrize('language', ['en', 'tr'])
def test_exactly_two_primary_modes(client, make_user, login, language):
    html = render(client, make_user, login, language)
    rows = Elements(html).rows
    tabs = [a for _, a, _ in rows if a.get('role') == 'tab']
    assert [a['data-tab-name'] for a in tabs] == ['today', 'plan']
    assert len([a for _, a, _ in rows if a.get('role') == 'tablist']) == 1
    panels = [a for _, a, _ in rows if a.get('role') == 'tabpanel']
    assert len(panels) == 2
    for tab in tabs:
        panel = next(a for a in panels if a['id'] == tab['aria-controls'])
        assert panel['aria-labelledby'] == tab['id']
    assert tabs[0]['tabindex'] == '0' and tabs[1]['tabindex'] == '-1'
    assert 'Plan' in html


@pytest.mark.parametrize('tool', ['diary', 'history', 'water'])
def test_today_workflows_are_real_disclosures(client, make_user, login, tool):
    rows = Elements(render(client, make_user, login, 'en')).rows
    tag, control, ancestors = next(r for r in rows if r[1].get('id') == 'nutrition-tab-' + tool)
    assert tag == 'summary'
    assert 'panel-today' in ancestors
    assert 'role' not in control
    region = next(r for r in rows if r[1].get('id') == 'panel-' + tool)
    assert region[1]['role'] == 'region'
    assert region[1]['aria-labelledby'] == control['id']
    assert 'panel-today' in region[2]


def test_supplements_belongs_to_plan(client, make_user, login):
    rows = Elements(render(client, make_user, login, 'en')).rows
    entries = [r for r in rows if r[1].get('href') == '/supplements']
    # Shell has no fifth destination or second cabinet editor.
    assert len(entries) == 1
    assert 'panel-plan' in entries[0][2]


def test_initial_route_query_budget(app, client, make_user, login):
    make_user('pr2-budget', profile_complete=True)
    login('pr2-budget')
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
    with app.app_context():
        event.listen(db.engine, 'before_cursor_execute', capture)
        try:
            response = client.get('/nutrition')
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture)
    assert response.status_code == 200
    assert not any('supplements' in s or 'custom_meal' in s or 'water_logs' in s for s in statements)
    # Measured pre-change budget includes shared authentication and daily-login hooks.
    assert len(statements) <= 9, statements
