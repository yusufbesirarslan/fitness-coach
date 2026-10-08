# LP15-D: explicit menu confirmation into the canonical diary

Parent: LP15-C PR #413, exact commit
`dc0656e6206a43f2bbb698880083bc9f44282380`.

`POST /api/v1/nutrition/menu/log` is a Bearer-authenticated route on
`mobile_api`. It accepts `Content-Type: application/json`, the existing
8–64 character `Idempotency-Key` format, and exactly:

```json
{"confirmation_token":"owner-bound LP15-C proof","quantity":1,"slot":"ogle","confirmed":true}
```

No other fields are accepted. Cookie auth cannot authorize the command.
Slots are `kahvalti`, `ogle`, `aksam`, `ara_ogun`; canonical casing and surrounding
whitespace normalize as in native LogFood. Quantity must be a finite JSON number,
not a boolean or string, with `0 < quantity <= 1000`. It multiplies the signed
restaurant serving. Grams are rendered only when present in that signed portion.
The scaled snapshot is checked against existing LogFood nutrition bounds.

The route uses LP15-C's `read_item_proof`, not a second proof codec. The new
`enforce_expiry=False` option defers only expiry: signature, domain/version,
owner, closed payload, identity, complete bounded nutrition, estimation
provenance, lifetime and future-issued checks still apply. Default callers
continue to reject expired proofs. Signed boolean versions/portion quantities
and malformed estimation-source types fail closed.

The transport builds `MenuConfirmedLogFoodCommand` and delegates to the same
`mobile_log_food.log_food` authority as `POST /api/v1/nutrition/logs`. Ordering:

1. Authenticate and parse the closed request/key.
2. Verify the owner's signed proof, without rejecting expiry yet.
3. Normalize command semantics and compute the fingerprint.
4. Look up the authenticated owner and key.
5. Replay a matching fingerprint; conflict on a different fingerprint.
6. For a new write only, enforce expiry.
7. Close the preflight read transaction and use the existing short MealLog
   transaction and `(user_id, idempotency_key)` uniqueness arbiter.

The menu fingerprint domain is `axisai/mobile-log-food/menu-confirm/v1`.
It includes signed analysis/candidate identities, normalized dish name,
base nutrition, serving/optional gram basis, quantity, slot, scaled nutrition,
rendered description, fixed `menu_estimated` provenance, estimation source and
confidence. It excludes proof bytes/signature, issued/expiry times, owner, key,
request ID and current day. Manual/provider fingerprint payloads are unchanged.

A new success writes one `MealLog`, with authenticated owner, canonical Istanbul
`day_key()`, timestamp, slot and `source="menu_estimated"`. Nothing else is
persisted. There is no migration, additional ledger, candidate repository,
operation/token table, process lock or analysis state. The existing projector
returns `{"meal": ...}` with opaque identity/revision and server day, status 201
for creation and 200 for replay. `mobile_api` supplies `Cache-Control: no-store`.
The client refreshes the canonical diary; this command computes no totals.

A lost response can be retried with the same body/key after proof expiry: the
retained row replays with no insert. A new key with an expired proof returns
410 `MENU_ITEM_EXPIRED`. A changed command on a committed key returns 409
`IDEMPOTENCY_CONFLICT`, even after expiry. **Canonical limitation:** idempotency
is tied to the retained MealLog row. If it is deleted, indefinite operation
replay is not guaranteed. LP15-D deliberately adds no durable operation table.

Other errors use the existing envelope: 400 `INVALID_IDEMPOTENCY_KEY`,
`INVALID_MENU_LOG_COMMAND`, `INVALID_MENU_ITEM_PROOF`; 503
`NUTRITION_TEMPORARILY_UNAVAILABLE`. Foreign-owner proofs look like invalid
proofs. There is no `MENU_ITEM_NOT_FOUND` because no candidate repository exists.

Confirmation performs no network acquisition, Redis analysis-cache access,
AI extraction, Bedrock call or FatSecret/provider resolution. It generates its
bounded deterministic description from signed dish name and confirmed portion,
without confidence, identity, proof or source-lineage annotations. Failure logs
contain only fixed event, exception type and server request ID. The existing
mobile request logger already suppresses owner identity; its policy is unchanged.

The canonical backend/native source projector now publishes `menu_estimated`
verbatim. Existing older native clients use their existing unknown-source
fallback (the mobile `NutritionApiMapper._source` default) for unrecognized
values. This PR does not implement the subsequent mobile confirmation UI or
change the historical Coach web Add slot behavior.

## Validation

`tests/test_lp15d_menu_confirmation.py` covers the route/proof/owner boundary,
injection rejection, explicit confirmation, quantities, slots, canonical writes,
provenance conflicts, Istanbul midnight, expired lost-response replay, failure
rollback/logging and poisoned network/provider/cache boundaries.

`tests/test_mobile_log_food_pg.py` adds PostgreSQL races for equivalent menu
commands, different menu commands, menu/manual and menu/provider. A barrier
inside the empty preflight read ensures both independent SQLAlchemy sessions
and PostgreSQL connections reach the database uniqueness race. There are no
ordering sleeps. These tests also run in the existing CI PostgreSQL job.

Run:

```sh
python -m pytest -q tests/test_lp15d_menu_confirmation.py
python -m pytest -q tests/test_mobile_log_food_api.py tests/test_mobile_log_food_fingerprint.py tests/test_lp15c_native_menu_analysis.py
FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://... python -m pytest -q -m pg_concurrency tests/test_mobile_log_food_pg.py
python -B scripts/validate_lp15d_mutations.py
```

The mutation runner performs eight actual source mutations in fresh test
interpreters and restores original bytes in `finally`: owner binding removed,
client nutrition trusted, expiry before replay, fingerprint provenance removed,
idempotency conflicts bypassed, source relabeled manual, false confirmation
permitted, provider called during confirmation. Run it alone in this worktree.

Local validation on 2026-10-08: 462 tests passed in the bounded combined run
(104 LP15-D route tests and 358 proof/LogFood/diary/auth/inventory/web
characterization regressions). Disposable local PostgreSQL: six tests passed,
including all four new menu race variants. Eight mutations were detected and
restored. No backend full-suite local run, AWS operation, deployment or merge.
