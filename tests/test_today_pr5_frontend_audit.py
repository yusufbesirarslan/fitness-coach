from scripts.frontend_audit.today_pr5_matrix import (
    _cells, _evaluate, _unexpected_console_errors,
)


def _measure(**overrides):
    measured = {
        "doc_horizontal_overflow": False,
        "today_mount_overflow": False,
        "today_state": "scheduled_not_started",
        "primary_action_count": 1,
        "primary_action_label": "Start workout",
        "primary_action_visible": True,
        "primary_action_hit_testable": True,
        "primary_action_clipped": False,
        "secondary_action_count": 0,
        "secondary_visible_count": 0,
        "secondary_hit_testable": True,
        "copy_overflow": [],
        "actionbar_overlap": False,
        "raw_key_leak": [],
        "duplicate_primary_ids": False,
        "html_lang": "en",
    }
    measured.update(overrides)
    return measured


def test_matrix_covers_every_required_viewport_locale_and_guidance_shape():
    cells = _cells()

    assert len(cells) == 60
    assert {cell["viewport"] for cell in cells} == {
        "320", "390", "768", "1024", "1366",
    }
    assert {cell["locale"] for cell in cells} == {"en", "tr"}
    assert {cell["state"] for cell in cells} == {
        "in_progress",
        "scheduled_not_started",
        "no_plan",
        "rest_day",
        "completed",
        "error",
    }
    assert all(cell["partial_data"] for cell in cells if (
        cell["state"] == "scheduled_not_started"
        and cell["viewport"] == "390"
    ))


def test_evaluator_accepts_primary_and_no_primary_states():
    primary = {
        "state": "scheduled_not_started", "locale": "en",
        "want_primary": True, "expected_primary_label": "Start workout",
    }
    no_primary = {
        "state": "rest_day", "locale": "tr", "want_primary": False,
        "expected_primary_label": None,
    }

    assert _evaluate(primary, _measure()) == ("pass", [])
    assert _evaluate(no_primary, _measure(
        today_state="rest_day", primary_action_count=0,
        primary_action_label=None, primary_action_visible=False,
        primary_action_hit_testable=False, secondary_action_count=1,
        secondary_visible_count=1, html_lang="tr",
    )) == ("pass", [])


def test_evaluator_rejects_overflow_duplicate_primary_raw_keys_and_dead_ends():
    cell = {
        "state": "rest_day", "locale": "en", "want_primary": False,
        "expected_primary_label": None,
    }

    verdict, reasons = _evaluate(cell, _measure(
        today_state="rest_day",
        primary_action_count=2,
        primary_action_clipped=True,
        secondary_action_count=0,
        doc_horizontal_overflow=True,
        today_mount_overflow=True,
        raw_key_leak=["today.brief.rest_day"],
        duplicate_primary_ids=True,
        copy_overflow=["today-brief-line"], actionbar_overlap=True,
    ))

    assert verdict == "fail"
    assert any("document horizontal overflow" in reason for reason in reasons)
    assert any("today-shell horizontal overflow" in reason for reason in reasons)
    assert any("primary-action count" in reason for reason in reasons)
    assert any("label clipped" in reason for reason in reasons)
    assert any("raw localization" in reason for reason in reasons)
    assert any("duplicate primary" in reason for reason in reasons)
    assert any("dead end" in reason for reason in reasons)
    assert any("copy overflow" in reason for reason in reasons)
    assert any("action bar" in reason for reason in reasons)


def test_evaluator_rejects_an_invisible_or_unusable_primary_action():
    cell = {
        "state": "scheduled_not_started", "locale": "en",
        "want_primary": True, "expected_primary_label": "Start workout",
    }

    verdict, reasons = _evaluate(cell, _measure(
        primary_action_label="Wrong",
        primary_action_visible=False,
        primary_action_hit_testable=False,
    ))

    assert verdict == "fail"
    assert any("primary label" in reason for reason in reasons)
    assert any("not visible" in reason for reason in reasons)
    assert any("not hit-testable" in reason for reason in reasons)


def test_only_the_intentionally_injected_partial_read_console_error_is_ignored():
    expected_503 = "Failed to load resource: the server responded with a status of 503"

    assert _unexpected_console_errors(
        {"partial_data": True}, [expected_503]) == []
    assert _unexpected_console_errors(
        {"partial_data": False}, [expected_503]) == [expected_503]
    assert _unexpected_console_errors(
        {"partial_data": True}, ["Uncaught TypeError: boom"]) == [
            "Uncaught TypeError: boom",
        ]
