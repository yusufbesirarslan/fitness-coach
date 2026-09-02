"""Authenticated write routes share a user-keyed rate limit."""

import pytest

from app.extensions import limiter


@pytest.fixture
def enabled_limiter():
    limiter.reset()
    limiter.enabled = True
    try:
        yield
    finally:
        limiter.enabled = False
        limiter.reset()


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/quick-add-meal", {"meal_key": "invalid"}),
        ("/api/diary/meal", {"meal_name": "invalid"}),
        ("/supplement/add", {}),
        ("/water", {"count": "invalid"}),
        ("/api/activity/log", {"steps": 0}),
    ],
)
def test_authenticated_write_is_rate_limited(client, auth_user, enabled_limiter,
                                             path, payload):
    assert client.post(path, json=payload).status_code != 429
    assert client.post(path, json=payload).status_code != 429
    assert client.post(path, json=payload).status_code == 429


@pytest.mark.parametrize(
    "endpoint",
    [
        "nutrition.diary_add_item",
        "nutrition.diary_update_item",
        "nutrition.diary_delete_item",
        "nutrition.diary_log_meal",
        # Sprint 13 PR4: the ledger correction route is a destructive,
        # token-addressed write and carries the same user-keyed limit as
        # every other authenticated mutation.
        "nutrition.delete_today_meal",
        "supplements.supplement_edit",
        "supplements.supplement_delete",
    ],
)
def test_id_bearing_write_view_is_marked(app, endpoint):
    assert getattr(app.view_functions[endpoint], "_auth_write_limited", False)
