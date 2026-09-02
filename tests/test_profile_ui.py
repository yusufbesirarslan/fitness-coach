"""Profile page render tests (Phase 5 · Surface 3): structural anchors of the
redesigned shell, the Membership card in both is_premium states, the edit sheet,
the test-pinned hub destinations, and the canonical-tokens-only guard."""

from app.extensions import db


def _html(client):
    r = client.get("/edit-profile")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_profile_structural_anchors(client, auth_user):
    html = _html(client)
    # hero + XP + membership + edit sheet
    assert 'class="pf-hero"' in html
    assert 'class="pbar-track"' in html
    assert 'class="pf-membership' in html
    assert 'id="edit-sheet"' in html
    assert 'role="dialog"' in html
    assert 'data-action="openEditSheet"' in html
    # sheet form still carries the i18n-test-pinned pieces
    assert '["kilo verme"]' in html
    # static assets + no legacy token leak
    assert "/static/profile.js" in html
    assert "/static/profile.css" in html
    assert "--volt" not in html


def test_profile_hub_destinations_preserved(client, auth_user):
    html = _html(client)
    for href in ("/friends", "/feed", "/leaderboard", "/quests",
                 "/challenges", "/logout"):
        assert f'href="{href}" class="hub-link' in html, href
    assert 'href="/pump-check-gallery" class="hub-link' not in html
    assert 'href="/supplements" class="hub-link' not in html
    assert 'href="/premium" class="hub-link' not in html
    assert 'data-action="setLang"' in html
    assert 'href="/premium"' in html
    assert 'href="/supplements"' in html


def test_membership_free_shows_upgrade(client, auth_user):
    # fresh users are not premium: the membership card renders the upgrade CTA
    # (the exact inverse of the premium test below). We assert on the CTA markup,
    # not on resolved copy — _head.html dumps the whole i18n catalog into
    # window.I18N on every page, so every key *name* is present regardless.
    html = _html(client)
    assert 'class="btn-volt pf-upgrade"' in html     # free-plan upgrade CTA present
    assert 'href="/premium"' in html
    assert 'data-ga-event="premium_nav_click"' in html


def test_membership_premium_shows_badge_no_cta(client, auth_user):
    auth_user.is_premium = True
    db.session.commit()
    html = _html(client)
    assert 'class="badge badge-success' in html      # premium badge present
    # the upgrade CTA button (btn-volt in the membership card) is gone
    assert 'class="btn-volt pf-upgrade"' not in html
