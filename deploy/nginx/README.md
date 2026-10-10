# nginx route control plane (R6-02A)

This directory is the repository authority for how nginx selects the ONE web
backend that receives new traffic. Production now has the R6-02 control plane
installed and active. **R6-02B is COMPLETE as of 2026-10-09.** nginx indirection
is active and the production route state remains `legacy`.

No production traffic has yet been served by blue or green. After PR #424
merges, normal deploy engineering uses the symbolic route helper, but R6-03A
is not production-validated yet. The first real production blue/green cutover
remains separately controlled by the pending R6-03B.

## Topology

Current production topology (R6-02B complete, 2026-10-09):

    Internet
     -> nginx
     -> proxy_pass http://axisai_web
     -> /etc/nginx/axisai/active-web-upstream.conf
     -> server 127.0.0.1:5000
     -> legacy main-project web

The named upstream includes exactly one backend:

    upstream axisai_web { include /etc/nginx/axisai/active-web-upstream.conf; }
    active-web-upstream.conf = exactly one of
        server 127.0.0.1:5000;   legacy  (main-project web, docker-compose.yml)
        server 127.0.0.1:5001;   blue    (R6-01B slot axisai-web-blue)
        server 127.0.0.1:5002;   green   (R6-01B slot axisai-web-green)

Blue and green are never both in rotation. R6 selects a backend after health
verification; it does not rely on nginx passive failure detection (a
single-server upstream is never marked unavailable).

## Authorities

| Concern | Authority |
| --- | --- |
| slot names, Compose projects, service `web`, ports, memory, lifecycle | R6-01B: `scripts/web_slot_runtime.py`, `docker-compose.web-slot.yml`, `deploy/compose/web-slot-*.yml` |
| route state -> loopback port | `deploy/nginx/web-slots.conf`, installed as `/etc/axisai/web-slots.conf` |
| which backend gets new traffic | `/etc/nginx/axisai/active-web-upstream.conf` (initial bytes: `deploy/nginx/active-web-upstream.conf`) |
| reading / changing the route | `scripts/axisai_switch_web_slot.py`, installed as `/usr/local/sbin/axisai-switch-web-slot` |
| one-time site migration (R6-02B) | `scripts/axisai_nginx_bootstrap.py` |

`tests/test_r6_nginx_slot_contract.py` parses the mapping with the helper's own
parser and requires it to equal `{legacy: docker-compose.yml web port,
**web_slot_runtime.SLOT_PORTS}`, and `SLOT_PORTS` to equal what the Compose
overlays publish. Moving any one of them alone fails CI.

Installed files (all `root:root`, created by the bootstrap):

| Path | Mode |
| --- | --- |
| `/etc/axisai/`, `/etc/nginx/axisai/` | `0755` |
| `/etc/axisai/web-slots.conf` | `0644` |
| `/etc/nginx/axisai/active-web-upstream.conf` | `0644` |
| `/usr/local/sbin/axisai-switch-web-slot` | `0755` |
| `/run/axisai-web-route.lock` | `0600` (created on first use) |
| `/run/axisai-web-route.pending` | `0644` (exists only during / after an interrupted switch) |

## The helper

    sudo /usr/local/sbin/axisai-switch-web-slot status
    sudo /usr/local/sbin/axisai-switch-web-slot switch legacy|blue|green

Nothing else is accepted: no ports, hosts, paths, flags, case variants, extra
arguments or "restore previous". Rejected input exits 64 before any file, lock
or process is touched, and is never echoed. Paths, the nginx commands
(`/usr/sbin/nginx -t -q`, `/usr/bin/systemctl reload nginx`), the 30 s command
timeout and the environment (`PATH=/usr/sbin:/usr/bin:/sbin:/bin`, `LC_ALL=C`)
are constants; `shell=False`; the script runs under `python3 -I`, so no
`PYTHON*` variable or user site can alter it. The mapping is parsed data.

**Output.** Exactly one `key=value` line on stdout, human detail on stderr.
R6-03 parses the line and the exit code, never nginx text.

    op=status state=legacy backend=127.0.0.1:5000 result=ok
    op=switch from=legacy to=blue backend=127.0.0.1:5001 result=switched changed=yes

| Exit | `result=` | Meaning |
| --- | --- | --- |
| 0 | `ok` / `switched` / `already-active` / `converged` | done; for a switch, nginx was reloaded (or nothing needed changing) |
| 10 | `current-nginx-invalid` | live config failed `nginx -t` (or timed out) **before** any change; nothing written |
| 11 | `candidate-nginx-invalid` | candidate failed `nginx -t`; previous include restored and validated; no reload |
| 12 | `reload-failed` | reload refused; previous include restored, validated and reloaded once |
| 13 | `convergence-failed` | restore or known-good reload failed; **MANUAL ACTION** on stderr; state stays `unknown` |
| 14 | `state-unknown` | include not canonical/mapped, live site not migrated, or an interrupted switch |
| 64 | `usage` | anything but the two operations |
| 74 | `io-error` | a write failed before any reload; include unchanged (restored if a rename had happened) |
| 75 | `lock-busy` | another route operation holds the lock; nothing done, no waiting |
| 77 | `not-root` | not run as root |
| 78 | `untrusted-config` | mapping/include/site/lock fails the trust or format contract |

**Trust contract** for the mapping, include, live site and lock: a regular
file (opened `O_NOFOLLOW`, `O_NONBLOCK`), exactly one link, owned by root, not
group/world-writable, size-bounded (4 KiB / 64 B / 256 KiB), and every
directory from `/` down is a real root-owned directory that is not
group/world-writable. The mapping must define exactly `legacy`, `blue`,
`green` once each as `127.0.0.1:<1024-65535>` with distinct ports; the include
must be exactly `server 127.0.0.1:<mapped port>;\n`.

**Switch sequence.**

1. Exclusive non-blocking `flock` on `/run/axisai-web-route.lock` (busy: 75).
2. Trusted mapping; live site classified as `migrated`; include parsed to a
   state.
3. `nginx -t` on the CURRENT configuration. Invalid: exit 10, nothing written.
   This is what makes "previous" a genuinely known-good state.
4. Target already active: exit 0 `already-active`, no write, no reload.
5. Write the pending marker (`from=<state> to=<state>`).
6. Atomically install the candidate include: exclusive same-directory temp
   file, `fsync`, `rename`, directory `fsync`. nginx never reads an empty,
   partial or two-server include.
7. `nginx -t`. Failure: restore the previous include, `nginx -t` it, no
   reload, exit 11 (13 if the restore does not validate).
8. `systemctl reload nginx` (graceful SIGHUP). Failure: restore the previous
   include, `nginx -t`, one reload, exit 12 (13 if either step fails). No
   loop.
9. Read the include back through the canonical parser, clear the marker,
   exit 0 `switched`.

**Interrupted switches.** If the helper dies between steps 5 and 9 (SIGKILL, a
caller timeout), the on-disk include may differ from what nginx runs. The
marker makes `status` report `unknown` (exit 14) instead of guessing. A later
`switch <state>` still works (it reports `from=unknown`) and establishes a
verified state; `switch` to the state already on disk reloads it
(`converged`). `/run` is cleared on reboot, which is correct: nginx then
starts from the on-disk include.

## Rollback model

Rollback is not a separate operation and there is no stored "previous" for a
caller to restore. The caller reads `status` before switching and, if
post-switch verification fails, runs `switch <that state>`:

    before=$(sudo axisai-switch-web-slot status)      # state=legacy
    sudo axisai-switch-web-slot switch blue           # first cutover
    # public/mobile verification fails
    sudo axisai-switch-web-slot switch legacy         # same validated path back

Why not `restore-previous`: a stored previous is stateful (two switches or an
operator in between silently change what "previous" means, and a double
restore toggles), and its bytes would be a second routing authority. Explicit
symbolic states are idempotent, re-validated by the same mapping, and make the
first cutover's way back (`legacy`) a first-class state rather than a backup
file.

## Responsibilities and the switch race (for R6-03)

The helper switches safely between trusted symbolic targets. It does **not**
check application health. R6-03 MUST, immediately before `switch`:
container healthy, `/app/BUILD_REVISION` exact, deep-health revision exact,
mobile API candidate functional (`web_slot_runtime.py verify`). And
immediately after: public/mobile verification, then `switch <previous>` on
failure. The window between "candidate verified" and "switch" is bounded but
not zero; a candidate could fail inside it. That is mitigated by the
immediate pre-switch verify, the atomic graceful switch, the immediate
post-switch verify and the fast symbolic rollback, not by a service mesh. When
`legacy` is the rollback target, R6-03 must also verify the legacy web first.

R6-03 must also take the production deploy lock before the route lock; this
helper's lock is deliberately separate from it.

## Graceful reload

`systemctl reload nginx` runs `nginx -s reload` (production `ExecReload`,
audited). The master validates and loads the new configuration, starts new
workers, and tells the old workers to stop accepting and finish what they
have. CI job `r6-nginx-integration` proves on a real nginx:

- new connections after a switch reach the new backend, and a switch back
  to `legacy` moves them back;
- a slow response already streaming from the old backend completes
  byte-for-byte after the switch, while new requests already reach the new
  backend, and the old worker generation stays alive until it finishes, then
  exits.

Not proven there: WebSocket upgrades kept across a reload (the app serves
none today) and behaviour beyond `worker_shutdown_timeout` (unset in
production, so old workers wait for in-flight requests).

## Privilege model

The `ubuntu` deploy user has passwordless sudo. Root-owned helper, mapping and
include are therefore defense-in-depth, blast-radius reduction and interface
narrowing (the deploy transaction asks for a state name, not a backend), **not
a security boundary** against `ubuntu`. R6-02 does not change sudoers or IAM.

## R6-02B runbook (one-time production bootstrap)

Completed in production on 2026-10-09. The original procedure below is retained
as historical reference; execution requires separate explicit approval while
production is up (it is stopped 23:00-06:00Z).

1. The deploy checkout must be the exact merged revision:
   `runuser -u ubuntu -- git -C /home/ubuntu/fitness-coach rev-parse HEAD`
   equals the approved SHA, and the three artifact paths are unmodified
   (`runuser -u ubuntu -- git diff --quiet <sha> -- scripts/axisai_switch_web_slot.py scripts/axisai_nginx_bootstrap.py deploy/nginx`).
   Never run `git status` as root there (it rewrites `.git/index` ownership).
2. Read-only: `sudo /usr/bin/python3 -I scripts/axisai_nginx_bootstrap.py check`
   must print `topology=legacy-direct` and the site sha256 must equal the
   audited `dba21170…6a575591bfc31e298773f3f735ab2e5e5486a`. Stale R6-01 Stage A
   files show as `differs`; they are replaced from the repository.
3. `sudo /usr/bin/python3 -I scripts/axisai_nginx_bootstrap.py apply`:
   - refuses unless the site is enabled exactly once and is `legacy-direct`
     (`migrated` + matching artifacts is a no-op; drift or `partial`/`unknown`
     fails closed);
   - `nginx -t` on the current config;
   - proves Certbot's own nginx parser sees identical virtual hosts before and
     after (renewals re-parse this site);
   - writes a root-only backup `/root/axisai-r6-02b/fitx.pre-r6-02b.<UTC>`
     (`0600`, plus `.sha256`) and reads it back;
   - installs the three repository artifacts (the include is not referenced
     yet, so this cannot move traffic);
   - atomically replaces the site, `nginx -t`, graceful reload;
   - verifies the site classifies as `migrated`, `status` is `legacy`, and 5
     public `/health` samples are all 200;
   - any failure after the site write restores the exact backup bytes,
     validates and reloads them once; failure of that prints MANUAL ACTION
     with the backup path.
4. Afterwards `sudo axisai-switch-web-slot status` must print `state=legacy`.

Manual rollback of the migration (only if needed later):

    sudo install -o root -g root -m 0644 /root/axisai-r6-02b/fitx.pre-r6-02b.<UTC> /etc/nginx/sites-available/.fitx.restore
    sudo mv -f /etc/nginx/sites-available/.fitx.restore /etc/nginx/sites-available/fitx
    sudo nginx -t && sudo systemctl reload nginx

Live Certbot drift from `nginx.conf` (hard-coded `Connection "upgrade"`,
fatsecret layout, comments) is deliberately preserved; the bootstrap edits one
argument and inserts one block, and verifies that nothing else changed.
`nginx.conf` is the intended shape for a fresh host and is never copied over
the live site by any deploy.

## Historical pre-R6-02B production audit snapshot (read-only, 2026-10-08 18:41Z)

This snapshot predates the completed 2026-10-09 bootstrap and does not describe
the current nginx topology.

- host and containers at `9e21be5`; nginx 1.28.3 active, `nginx -t` ok;
  `ExecReload=/usr/sbin/nginx -g 'daemon on; master_process on;' -s reload`.
- `sites-enabled/default -> sites-available/fitx` (root, 0644, sha256
  `dba21170…`), plus `fatsecret-proxy`. Effective config: one
  `proxy_pass http://127.0.0.1:5000;`, zero `axisai_web` or include references.
- Inert R6-01 Stage A files exist and are NOT authoritative: include
  `server 127.0.0.1:5000;\n` (sha256 `63c61cdd…`, same bytes as this
  repository's include), mapping without `legacy` (`3f0e3176…`, differs), old
  helper (`ed3a0ccc…`, differs). R6-02B subsequently replaced the differing ones.
- `/usr/bin/python3` 3.14.4, certbot 4.0.0 (apt, `certbot_nginx` importable).
