# Web-slot control plane (R6-01: P1 nginx indirection, P2 switch primitive, P6 slot contract)

This is the foundation for same-host blue/green web deploys. **It is not
blue/green deployment.** Nothing in `deploy_control.py` / `production_deploy.sh`
uses it yet, no slot container runs, and production keeps serving the legacy
main-project web on `127.0.0.1:5000`. Zero-downtime deploy is NOT complete
(open: P3 memory gate, P4 drain/stop grace, P5 migration coexistence, R6
orchestration + validation).

## Routing topology

Before R6-01:

    nginx :443 location / -> proxy_pass http://127.0.0.1:5000 -> fitness-coach-web-1

After R6-01 (behaviour-preserving):

    nginx :443 location / -> proxy_pass http://axisai_web
    upstream axisai_web { include /etc/nginx/axisai/active-web-upstream.conf; }
    active-web-upstream.conf = "server 127.0.0.1:5000;"   (legacy web, unchanged)

Future slots (reserved, not running): `blue` = `127.0.0.1:5001`,
`green` = `127.0.0.1:5002` -- both verified free on the host on 2026-10-08,
below the ephemeral range, adjacent to the legacy port.

## Root-owned files on the host

| Path | Owner / mode | Source |
| --- | --- | --- |
| `/etc/nginx/axisai/` | `root:root 0755` | directory, only the include lives here |
| `/etc/nginx/axisai/active-web-upstream.conf` | `root:root 0644` | `deploy/nginx/active-web-upstream.conf` (initial) |
| `/etc/axisai/` | `root:root 0755` | directory |
| `/etc/axisai/web-slots.conf` | `root:root 0644` | `deploy/nginx/web-slots.conf` |
| `/usr/local/sbin/axisai-switch-web-slot` | `root:root 0755` | `scripts/axisai_switch_web_slot.py` |

The `fitx` site (`/etc/nginx/sites-available/fitx`) stays Certbot-owned. The
one-time migration changed exactly two things in it: it added the
`upstream axisai_web { include ...; }` block after `upstream fatsecret_proxy`,
and it replaced the single `proxy_pass http://127.0.0.1:5000;` with
`proxy_pass http://axisai_web;`. Every TLS, header, timeout and location line
is byte-identical. Known live drift from `nginx.conf` (hard-coded
`Connection "upgrade"`, fatsecret differences, stale backups) is deliberately
NOT normalized here.

The swap is behaviour-preserving because the location sets `Host` explicitly,
both forms carry no URI part (the request URI is passed unchanged), and a
single-server upstream is never marked unavailable (`max_fails` does not apply).

## Switch primitive

    sudo /usr/local/sbin/axisai-switch-web-slot blue|green

- **Input:** exactly one argument, exactly `blue` or `green`. Anything else
  (ports, hosts, paths, case variants, metacharacters, extra arguments) exits
  64 before any file or lock is touched.
- **Trusted mapping:** `/etc/axisai/web-slots.conf`. It is parsed, never
  sourced. The file and its directory must be root-owned, non-symlink and not
  group/world-writable. It must define both slots exactly once as
  `127.0.0.1:<1024-65535>` with distinct ports. A bad mapping exits 78.
- **Atomicity:** writes a same-directory temp file, then `fsync`, `rename` and
  a directory `fsync`. The previous include is saved first as
  `active-web-upstream.conf.prev` (last known good). An `flock` on
  `/run/axisai-switch-web-slot.lock` serializes concurrent switches.
- **Validation:** `nginx -t` runs after the rename.
  - If it fails, the previous include is restored, there is no reload, and the
    command exits 2.
  - If the include already names the slot, the command validates and then does
    nothing (no write, no reload).
- **Reload:** graceful `systemctl reload nginx`.
  - If the reload fails, the previous include is restored, then `nginx -t`
    runs, then a known-good reload is attempted, and the command exits 3.
  - A refused reload leaves the running master on its old config, so nginx
    always converges on the last-known-good backend.
- **Output:** one audit line, `slot=<slot> backend=127.0.0.1:<port> result=...`.
  No secrets.

**Privilege model:** the `ubuntu` deploy user still has passwordless sudo and
the `docker` group, so this command is **defense-in-depth / blast-radius
reduction, not a hard privilege boundary**. It narrows what the future deploy
transaction has to ask for: a slot name, not a backend.

## Slot Compose contract

    AXISAI_WEB_IMAGE=fitness-coach-web:<sha> docker compose \
      -f docker-compose.web-slot.yml -f deploy/compose/web-slot-blue.yml config

- Each slot is its own project (`axisai-web-blue` / `axisai-web-green`), never
  `fitness-coach`. `docker compose up --remove-orphans` on the main project
  only considers containers labelled `com.docker.compose.project=fitness-coach`,
  so it cannot remove a slot.
- The service key is `web`, so `com.docker.compose.service=web` keeps the
  CloudWatch agent `(web|worker)` filter matching.
- There is no `container_name`, no `build`, and no slot-local
  redis/worker/db. The image must be given explicitly.
- Each slot joins the main project's external network `fitness-coach_default`,
  where `redis` resolves.
- Ports are loopback only and equal to the trusted mapping (enforced by
  `tests/test_web_slot_compose_contract.py`).

## Rollback of the one-time migration

The pre-change site was saved root-only (`0600`) at
`/root/r6-01-backup/fitx.pre-r6-01`, together with its sha256. To restore it:

    sudo install -o root -g root -m 0644 /root/r6-01-backup/fitx.pre-r6-01 /etc/nginx/sites-available/.fitx.restore
    sudo mv -f /etc/nginx/sites-available/.fitx.restore /etc/nginx/sites-available/fitx
    sudo nginx -t && sudo systemctl reload nginx
    curl -s -o /dev/null -w '%{http_code}\n' https://fitx-chatbot.duckdns.org/health

The include, mapping and helper are inert without the `upstream axisai_web`
reference and may stay or be removed (`/etc/nginx/axisai`, `/etc/axisai`,
`/usr/local/sbin/axisai-switch-web-slot`).
