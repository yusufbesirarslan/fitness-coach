"""Fixture hygiene guard for the UTC ↔ Europe/Istanbul midnight window.

The application's day boundary is `Europe/Istanbul` — `app_today()` / `day_key()`
in `app/timeutil.py` are the single source of "today". A test that seeds a day
from the **process-local** clock instead (`date.today()`, which is UTC on a
GitHub runner) is silently clock-dependent: between 21:00 and 24:00 UTC the UTC
date is still D while the application day is already D+1, so the seed lands on
the application's *yesterday* and the test fails for reasons that have nothing
to do with the change under review.

That is not hypothetical. `tests/test_hooks.py` and
`tests/test_web_meal_correction.py` did exactly this and turned the `pytest`
merge gate into a function of the wall clock: 36 tests failed on every PR whose
run started inside that three-hour window and passed on a re-run outside it.

This module is the tripwire that keeps them honest. The behavioural proofs for
the boundary itself live next to the code they cover:
`test_streak_increments_inside_the_utc_istanbul_midnight_window` (hooks) and
`test_the_current_day_read_uses_the_application_day_at_the_utc_boundary`
(web meal correction) — both on frozen clocks, so they hold on any runner.
"""
import tokenize
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent


def _executable_source(path):
    """The module's source with comments and string literals removed.

    The guard must judge what the module *runs*, not what it explains: the
    docstrings below (and in the modules being scanned) name the very tokens
    being forbidden.
    """
    kept = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    # Concatenated, so an expression stays contiguous: the tokens `date`, `.`,
    # `today`, `(`, `)` read back as `date.today()`.
    return "".join(kept)

# Modules that seed a day which the application then resolves through the
# canonical helper. Their fixtures MUST come from that same helper.
APP_DAY_FIXTURE_MODULES = (
    "test_hooks.py",
    "test_web_meal_correction.py",
)

# Process-local / UTC "today" sources. `date(y, m, d)` literals are fine — the
# problem is reading the day from the clock the runner happens to be set to.
PROCESS_LOCAL_DAY_SOURCES = (
    "date.today()",
    "datetime.now().date()",
    "utcnow().date()",
    "datetime.today()",
)


@pytest.mark.parametrize("module", APP_DAY_FIXTURE_MODULES)
def test_app_day_fixtures_derive_from_the_canonical_helper(module):
    source = _executable_source(TESTS_DIR / module)

    assert "app_today" in source, (
        f"{module} models the application's day but no longer imports "
        "`app_today`. Day fixtures in this module must come from "
        "`app.timeutil.app_today()`, not from the process-local clock."
    )


@pytest.mark.parametrize("module", APP_DAY_FIXTURE_MODULES)
def test_app_day_fixtures_never_seed_from_the_process_local_clock(module):
    source = _executable_source(TESTS_DIR / module)

    offenders = [token for token in PROCESS_LOCAL_DAY_SOURCES if token in source]

    assert not offenders, (
        f"{module} seeds a day from the process-local clock ({', '.join(offenders)}). "
        "On a UTC CI runner between 21:00 and 24:00 UTC that date is the "
        "application's yesterday, so the module fails purely as a function of "
        "when CI happened to start. Use `app.timeutil.app_today()` for days the "
        "application resolves through `app_today()`/`day_key()`, or a frozen "
        "`date(y, m, d)` literal under `audit_clock` for a specific boundary."
    )
