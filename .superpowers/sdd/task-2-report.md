# Task 2 Report: Workout Completion Sharing and Friend Selector API

## Implementation summary

- Added TDD coverage for `/workout/complete` sharing behavior in `tests/test_pump_check_sharing.py`.
- Extended `tests/test_training_routes.py` to assert default Pump Check sharing state on normal workout completion.
- Updated `app/blueprints/training.py` to:
  - parse `visibility` and `shared_friend_ids`
  - validate allowed visibility values
  - require recipients for `"friends"` visibility
  - verify selected friend ids against `get_friend_ids(current_user.id)`
  - persist `visibility` and `shared_friend_ids` on `PumpCheck`
  - create `Message(message_type="pump_check")` rows for selected friends
  - return `pump_check_id`, `visibility`, and `shared_friend_ids` in the response
- Added `GET /friends/select-list` to `app/blueprints/social.py` with:
  - accepted-friends filtering
  - optional `q` filtering on username/full name
  - recent-contact-first ordering based on existing messages
  - response fields: `id`, `username`, `full_name`, `profile_picture`, `recent`
- Added backend locale keys for new sharing validation errors in `locales/en.json` and `locales/tr.json`.

## Tests run / results

### RED

Command:

```bash
python -m pytest tests/test_pump_check_sharing.py tests/test_training_routes.py::test_complete_awards_xp_and_records_pump_check -v
```

Result:

- Exit code: `1`
- `5 failed, 4 passed`
- Expected failures observed:
  - `/workout/complete` defaulted to `private` instead of `feed`
  - `/workout/complete` accepted `"friends"` visibility without recipients
  - `/workout/complete` did not persist `"friends"` visibility or create `pump_check` messages
  - `/friends/select-list` returned `404`

### GREEN

Command:

```bash
python -m pytest tests/test_pump_check_sharing.py tests/test_training_routes.py -v
```

Result:

- Exit code: `0`
- `29 passed`
- Warnings present were pre-existing deprecation warnings around `datetime.utcnow()` usage in test/support code and hooks; no new failures.

## TDD RED/GREEN evidence

- Wrote the new sharing and selector tests first.
- Ran the focused RED command before any production edits and captured the expected failures.
- Implemented the minimum route and locale changes required to satisfy the failing tests.
- Ran the broader Task 2 suite from the brief and confirmed all targeted tests passed.

## Files changed

- `app/blueprints/training.py`
- `app/blueprints/social.py`
- `locales/en.json`
- `locales/tr.json`
- `tests/test_pump_check_sharing.py`
- `tests/test_training_routes.py`
- `.superpowers/sdd/task-2-report.md`

## Self-review

- Scope matches the Task 2 brief and does not add feed routes/pages, gallery, chat rendering changes, modal UI, or navigation changes.
- Write scope for implementation changes stayed within the files named by the brief, plus this required report file.
- The friend selector route reuses existing friendship and messaging models rather than introducing new helpers or schema changes.
- Workout completion sharing keeps all DB side effects in the existing transaction path.

## Concerns

- The new `"pump_check"` messages serialize JSON into `Message.body`, which matches the brief and existing schema, but any future chat rendering work will need to treat this message type as structured payload rather than plain text.

## Review follow-up fixes

- Hardened `app/blueprints/training.py::_parse_pump_visibility` so non-string `visibility` values return `400` with `pump.visibility_invalid` instead of raising on `.strip()`.
- Tightened `shared_friend_ids` validation so only JSON list inputs are accepted; non-list values now return `400` with `pump.friend_ids_invalid` instead of being coerced as generic iterables.
- Added focused route coverage in `tests/test_pump_check_sharing.py` for:
  - string `shared_friend_ids` payloads such as `"12"`
  - non-string `visibility` payloads such as `7`

### Review follow-up verification

Command:

```bash
python -m pytest tests/test_pump_check_sharing.py tests/test_training_routes.py -v
```

Result:

- Exit code: `0`
- `31 passed`
- Existing deprecation warnings remained; no new failures.

## Remaining review finding fix

- Tightened `app/blueprints/training.py::_parse_pump_visibility` so `shared_friend_ids` accepts only a JSON list of strict integer IDs.
- Rejected coercible non-integer entries before friend lookup:
  - booleans such as `true`
  - floats such as `1.5`
  - strings such as `"123"`
- Kept the failure contract unchanged for invalid recipient payloads: `400` with `pump.friend_ids_invalid`.
- Added focused regression coverage in `tests/test_pump_check_sharing.py` proving the route rejects those entries even when `get_friend_ids(...)` would otherwise accept the coerced integer values.

### Remaining finding verification

RED command:

```bash
python -m pytest tests/test_pump_check_sharing.py -k "boolean_shared_friend_ids_entries or float_shared_friend_ids_entries or string_shared_friend_ids_entries" -v
```

Result:

- Exit code: `1`
- `3 failed`
- Failures confirmed the pre-fix coercion bug: `[True]`, `[1.5]`, and `["123"]` were accepted through `_parse_pump_visibility`.

GREEN / covering command:

```bash
python -m pytest tests/test_pump_check_sharing.py tests/test_training_routes.py -v
```

Result:

- Exit code: `0`
- `34 passed`
- Existing deprecation warnings remained; no new failures.
