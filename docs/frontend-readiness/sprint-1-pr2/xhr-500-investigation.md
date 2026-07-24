# Blocker 2 — Transient summary-XHR 5xx: root-cause investigation

Status: **Resolved — classified as a harness (audit-environment) defect, fixed in
the harness; not a PR2 application defect.**

## The observation

The pre-reconciliation matrix run recorded, in exactly one of 48 cells
(`A-today-20__plan_ready__320__tr__nav-on_today-on`), two console entries:

```
Failed to load resource: the server responded with a status of 500 (INTERNAL SERVER ERROR)
```

The cell's layout verdict was `pass` and its document `status_code` was `200`, so
the network failure was recorded only as a console string and did not affect the
verdict — a reporting gap (fixed below).

## What the page requests

Today V2 (`templates/today.html`) fires five authenticated summary XHRs on load —
three in a `Promise.all` plus two more — all behind `@require_auth`:

`/last-session`, `/meal-log/today`, `/water`, `/checkin-history`,
`/leaderboard/reward-check`.

## Reproduction

Harness: `scripts/frontend_audit/app.py` builds the hermetic app on **file-based
SQLite**; `scripts/frontend_audit/runner.py` serves it with
`make_server(..., threaded=True)`.

* **Single-threaded** (`repro_500_seq.py`): the `active-workout`/`plan_ready` seed,
  all five endpoints hit sequentially, 5× each → **all 200**. No deterministic
  endpoint defect.
* **Concurrent** (`repro_500_v4.py`, 5 parallel authed reads per simulated page
  load, mirroring the browser): the failure reproduces **probabilistically** —
  ~1–2 % of authed reads, ~3 of every 4 runs of 120 reads returned exactly one
  5xx. It lands on a **different endpoint each time** (`/meal-log/today`,
  `/checkin-history`, `/last-session`, `/leaderboard/reward-check`, `/water`) —
  i.e. it is **endpoint-agnostic**.

## The 5xx body and origin

The failing response body is:

```json
{ "error": "Kimlik servisine şu an ulaşılamıyor. Oturumun açık; lütfen birazdan tekrar deneyin." }
```

That is the i18n key `auth.temporarily_unavailable`, returned by
`app/auth_middleware.py:_service_unavailable()` (HTTP **503** + `Retry-After`).
`require_auth` reaches it when `session_store.get_valid_access_token()` raises
`SessionTransient` — the encrypted server-side Cognito token row is read from the
SQLite `UserSession` table on **every** authed request. (In a slightly different
interleaving the same underlying read failure surfaces as an unhandled `500`
rather than the handled `503`, which is what the browser recorded.)

## Root cause

The hermetic audit installs **per-request GLOBAL state** in
`_activate_audit_context` (a `before_request` hook): it enters a fixed
`audit_clock` context and monkeypatches `cognito_jwt.validate_token`, restoring
both in `teardown_request`. That is only coherent while **one request runs at a
time**. Under the `threaded=True` server, a single page's parallel XHRs interleave
their patch/restore, so a request can momentarily observe the wrong validator/clock
and the session/token read fails → `SessionTransient` → 503 (or an unhandled 500).

This is a property of the **audit harness**, not of PR2:

* It hits **every** `@require_auth` route equally.
* The **legacy OFF page** (the PR1/base behaviour) fires **six** authed XHRs on
  load (the five above **plus** `/workout/status`) — strictly **more** concurrent
  authed reads than Today V2's five — so the base is **equally or more** exposed.
  The failure is therefore not introduced by PR2 and would be observed on the
  pre-PR2 page under the same harness.

## Classification

**Harness defect** (audit-environment concurrency), per the four options in the
task. Not a PR2 defect, not an intentional fault-injection, not pre-existing
application behaviour.

## Fix (harness-local only)

`scripts/frontend_audit/app.py`: a module-level `threading.Lock` acquired in
`_activate_audit_context` and released in `_restore_audit_context` serializes the
request **bodies** so each request holds a coherent clock+validator for its whole
lifetime. The threaded server still owns the sockets, so the browser's concurrent
connections never head-of-line block. No application code is touched; production is
unaffected.

### Proof (A/B, `repro_500_v4.py`, 120 authed reads per run)

| Mode | Runs | Runs with a 5xx | Total 5xx |
|------|------|-----------------|-----------|
| Lock **disabled** (pre-fix) | 4 | 3 | 3× 503 |
| Lock **active** (post-fix)  | 4 | 0 | 0 |

Post-fix also ran 300 consecutive authed reads with zero 5xx in a separate pass.

## Manifest hardening (so a layout pass can never hide a network failure)

`scripts/frontend_audit/today_pr2_matrix.py` (both the matrix run and the
interaction run) now attaches `page.on("response")` and `page.on("requestfailed")`
listeners, records `server_errors` (any status ≥ 500, with URL) and
`failed_requests` per cell, and **fails the cell** if either is non-empty — a
5xx (document or XHR) can no longer be masked by a passing layout assertion.

## Required clean rerun

The authoritative clean rerun of the affected cell on the rebased stack, with the
hardened manifest and the harness fix in place, is performed by the WSL browser
matrices (see `validation-manifest.json` / `interaction-results.json`).
