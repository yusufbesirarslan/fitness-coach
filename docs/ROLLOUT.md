# Production rollout runbook — feature flag activation

**This is operator-run work.** Every command below is executed by a human on the
EC2 host or in the GitHub UI. Nothing in this repository activates a flag, and no
automation in this repository is permitted to.

Flag inventory and rationale: [FEATURE_FLAGS.md](FEATURE_FLAGS.md).
Metrics and SLIs: [OBSERVABILITY.md](OBSERVABILITY.md).

---

## 0. Where flags live

Rollout flags live in the **host `.env`**, which the deploy pipeline never
touches (`git reset --hard origin/main` does not modify it). Consequences:

- Flipping a flag needs **no merge and no deploy** — edit `.env`, restart.
- Rollback is seconds, not a release cycle.
- The repository cannot tell you what is currently on. Ask the running process:
  the `[FLAGS] enabled=…` boot line, or `/health?deep=1`.

```bash
# On the host, as the deploy user:
cd <app-dir>
grep -E '^(WEEKLY_PROGRAM_UI_ENABLED|UIUX_TODAY_V2_ENABLED|UIUX_PLAN_V2_ENABLED|UIUX_COACH_PAGE_V2_ENABLED|UIUX_NAV_V2_ENABLED|FITX_WORKOUT_SESSIONS_ENABLED|AI_ADAPTIVE_PLAN_CONTEXT|MOBILE_AUTH_ENABLED)=' .env
```

---

## 1. One-time pre-deployment check — **do this before deploying PR2**

PR2 makes malformed flag values fail at boot instead of reading them as a silent
OFF. A host carrying `KEY=true` boots today and will **not** boot afterwards.
The deploy health gate would catch it and roll back automatically, but finding it
first is cheaper than a rolled-back deploy.

```bash
# On the host. Prints a line for every rollout flag whose value is not exactly
# 0, 1, or empty. Expected output: nothing.
for k in WEEKLY_PROGRAM_UI_ENABLED UIUX_TODAY_V2_ENABLED UIUX_PLAN_V2_ENABLED \
         UIUX_COACH_PAGE_V2_ENABLED UIUX_NAV_V2_ENABLED \
         FITX_WORKOUT_SESSIONS_ENABLED AI_ADAPTIVE_PLAN_CONTEXT \
         MOBILE_AUTH_ENABLED; do
  v=$(grep -E "^${k}=" .env | tail -1 | cut -d= -f2-)
  case "$v" in
    ""|0|1) ;;
    *) echo "MALFORMED: ${k}=[${v}]" ;;
  esac
done
```

Any line printed must be corrected to `0` or `1`, or the whole line removed,
**before** merging PR2 to `main`. Note `MOBILE_AUTH_ENABLED=` (empty) is also
rejected — that one has always been strict.

### Retired setting — `MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS`

PR3 pins request-authentication expiry leeway to `0` on both clients and
**retires** this setting. It is rejected at boot rather than ignored: a host
still carrying a non-zero value believes mobile tolerates expired tokens, and
silently dropping it would leave the running system and the documentation
disagreeing with nobody the wiser.

```bash
# On the host. Expected output: nothing.
grep -n '^MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS=' .env
```

If the line exists, **remove it** before deploying PR3. `=0` boots fine and
`=120` does not; removing the line is correct in both cases. The check runs
regardless of `MOBILE_AUTH_ENABLED`, because a stale value on a flag-off host
is exactly the value that would take effect the day mobile auth is switched on.

Missing this is not an outage — the deploy health gate fails and rolls back
automatically — but a rolled-back deploy costs more than one `grep`. Rationale
and the full value table: [AUTH_CONTRACT.md](AUTH_CONTRACT.md) §3.

---

## 2. Prerequisites for any activation

1. **Metrics on.** `RUNTIME_METRICS_ENABLED=1` on the host, restarted, and
   `FitX/Runtime` collecting. Without a baseline there is nothing to compare a
   rollout against and no defensible abort decision.
2. **A representative baseline.** At least one full weekly traffic cycle
   (weekday and weekend, peak and trough). Every SLO in `OBSERVABILITY.md` is
   marked **provisional** until this exists; alarms built on provisional numbers
   mis-fire.
3. **A quiet window.** No other deploy, migration or flag change in flight. Two
   simultaneous changes make an incident unattributable.
4. **The flag's own prerequisites** from its record in `app/feature_flags.py`.

---

## 3. Recommended staged order

One flag per window. The order is driven by **observability**, not by how ready
the feature feels — a flag you cannot observe is a flag you cannot safely
activate first.

| Order | Flag | Why here |
|---|---|---|
| 1 | `WEEKLY_PROGRAM_UI_ENABLED` | Only presentation flag with a real feature-specific signal (`[TRAINING][WEEKLY_PROGRAM]` state line). Additive, read-only, one GET. |
| 2 | `UIUX_TODAY_V2_ENABLED` | Independently reachable through the legacy shell's Home tab (`/`). |
| 3 | `UIUX_PLAN_V2_ENABLED` | Reachable through the legacy Training tab (`/training`). Shares a page with #1; separate windows. |
| 4 | `UIUX_COACH_PAGE_V2_ENABLED` | Independent, but sits on the AI path. Re-check after #5 — see the note below. |
| 5 | `UIUX_NAV_V2_ENABLED` | **Last among the presentation flags**: widest UI blast radius paired with the weakest observability. |
| 6 | `FITX_WORKOUT_SESSIONS_ENABLED` | Mutating and schema-backed. Staging first; needs migration `a994f9bed783`. |
| 7 | `AI_ADAPTIVE_PLAN_CONTEXT` | Changes AI behaviour for every user. Staging + human answer review; no metric can judge quality. |
| 8 | `MOBILE_AUTH_ENABLED` | **Blocked until PR4 merges.** Attack-surface change with an unbounded pre-auth blocking call today. |

**Nav v2 is not a prerequisite for anything.** `app/nav.py` points its four
primary destinations at pre-existing canonical routes (`/`, `/training`,
`/coach`, `/progress-page`), all of which respond regardless of the Today/Plan/
Coach v2 flags — so it can be activated at any point, and it goes last because a
regression under it is the hardest of the eight to attribute.

**One caveat on #4.** The legacy shell has no `/coach` entry point (not a tab,
not a drawer link), so until #5 activates, `/coach` is reached only by direct URL
— the everyday coach entry point is the floating widget, which this flag does not
change. A clean window at #4 therefore proves less than it appears: **re-check
`UIUX_COACH_PAGE_V2_ENABLED`'s abort signals during #5's observation window**,
particularly duplicated `/coach/history` fetches.

### First activation candidate: `WEEKLY_PROGRAM_UI_ENABLED`

Recommended on this evidence:

- **Additive and read-only.** OFF emits no markup, script, request or
  whitespace; ON adds a mount shell plus one `GET /api/training/weekly-program`.
  No write path, no schema, no migration.
- **Best-instrumented of the presentation flags.** It is the only one with a
  feature-specific, PII-free state log
  (`[TRAINING][WEEKLY_PROGRAM] request_id=… state=…`), so a failure is
  attributable to this flag rather than to "something on the training blueprint".
- **Not an authorization boundary.** The endpoint is `@require_auth` in every
  flag state, so activation cannot widen access.
- **Already exercised.** The four-way flag matrix (this flag × Plan v2) is
  covered by the existing suite.
- **Instant rollback.** One `.env` edit and a restart; no merge, no deploy, no
  data to unwind.

The honest caveat: on a host with little `WorkoutLog` history the card may only
ever render `insufficient_data`, so a successful activation may prove less than
it appears. Check that at least one account can produce a `populated` state
before concluding the rollout succeeded.

---

## 4. Activation procedure

```bash
# 1. Record the pre-activation state (you will compare against this).
grep '^KEY=' .env            # note the current value
docker compose logs --no-color --tail 40 web | grep '\[FLAGS\]'

# 2. Edit .env — exactly `0` or `1`, no quotes, no trailing space.
#    A trailing space is now a boot failure, not a silent OFF.

# 3. Apply.
docker compose up -d

# 4. Confirm the process agrees with you.
docker compose logs --no-color --tail 40 web | grep '\[FLAGS\]'
curl -s "http://127.0.0.1:5000/health?deep=1" | python -m json.tool
```

`/health?deep=1` is the operational visibility surface: its `flags` block lists
names and booleans only, and it is restricted to internal networks. There is
deliberately **no public flag endpoint**.

### Smoke test

- `/health` returns 200 (liveness).
- `/health?deep=1` returns 200 (dependencies + flags + capacity).
- The affected page renders logged-in, in both TR and EN.
- The flag's own success signals from `app/feature_flags.py`.

### Observation window

Watch for **at least 24 hours**, covering one peak. Per-flag success and abort
signals are in the registry; the always-applicable ones:

| Signal | Source | Abort if |
|---|---|---|
| `HttpServerErrors` (5xx) by blueprint | `FitX/Runtime` | above the pre-activation baseline |
| `HttpLatency` p95 by blueprint | `FitX/Runtime` | regression versus baseline |
| `HttpOverload` (503) | `FitX/Runtime` | any sustained rise — thread starvation |
| `ThreadReserve` | `FitX/Runtime` | approaching its floor (`< 2` for 2 consecutive periods) |
| Feature-specific log state | container logs | any `state=error` |

`HttpOverload` (503, deliberate shedding) and `HttpServerErrors` (5xx, defect)
are separate counters on purpose — merging them makes healthy load-shedding
look like an outage.

`ThreadReserve` is emitted as a real gauge from the runtime-metrics flush thread
as of Hardening PR4 — before that it was named here and in the
`MOBILE_AUTH_ENABLED` lifecycle record as an abort trigger while nothing ever
published it. It measures `FITX_WEB_THREADS − active AI permits − active scrape
permits`; the floor is `2`. Its prerequisite is `RUNTIME_METRICS_ENABLED=1`.
Capacity formula, per-path overload behavior and known limits: `docs/CAPACITY.md`.

---

## 5. Rollback

```bash
# Set the flag back to 0 in .env, then:
docker compose up -d
docker compose logs --no-color --tail 40 web | grep '\[FLAGS\]'
```

Seconds, no merge, no deploy, no data migration. Two flags need a footnote:

- **`FITX_WORKOUT_SESSIONS_ENABLED`** — safe with sessions already persisted;
  the read contract ignores those rows rather than deleting them. Migration
  `a994f9bed783` is **not** rolled back (expand-only, deliberately).
- **`MOBILE_AUTH_ENABLED`** — the `/api/v1` blueprint stops being registered and
  issued mobile credentials stop being accepted. Clients must re-authenticate
  when it is turned back on. Not a silent rollback.

### If a rollout is aborted

Set the flag to `0`, confirm recovery, then **write down what happened** — the
signal that triggered the abort, the evidence, and what would have to change
before trying again. Update the flag's record in `app/feature_flags.py`
(lifecycle, review date, and the decision if it changed). An aborted rollout with
no record becomes an unexplained OFF flag that nobody dares touch.

---

## 6. Post-rollout

1. Compare the observation window against the baseline; record both.
2. Update the flag's `lifecycle` and `review_by` in `app/feature_flags.py`.
3. When a flag has been ON in production through a full review period with no
   abort signal, **retire it**: delete the flag, delete the OFF branch, delete
   the `.env` line, delete the `.env.example` line, and remove the record. A
   permanently-ON rollout flag is dead configuration that still costs a branch
   in every test matrix.
4. If the decision was `remove` instead, delete the flag **and the feature
   branch it gated** — leaving unreachable code behind is the worse outcome.
