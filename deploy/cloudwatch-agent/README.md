# CloudWatch Agent — production host configuration

`file_config.json` is the source of truth for the agent on the production EC2
host (`i-0c6f5352fc214e68d`). It is **not** applied by the deploy pipeline: the
agent is host state, changed deliberately and rarely. This file exists so that
the live configuration is reviewable and recoverable.

## What it ships

| Log group | Source | Retention |
|---|---|---|
| `/axisai/nginx/access` | `/var/log/nginx/access.log` | 7 days |
| `/axisai/nginx/error` | `/var/log/nginx/error.log` | 30 days |
| `/axisai/app` | web + worker container stdout/stderr | 30 days |

### `/axisai/app` — why a glob and a filter

The previous entry tailed one hard-coded
`/var/lib/docker/containers/<id>/<id>-json.log`. Every deploy recreates the
containers under new IDs, so after the first redeploy the group silently
received nothing.

The entry now follows `/var/lib/docker/containers/*/*-json.log` and keeps only
lines whose JSON carries `"com.docker.compose.service":"web"` or `"worker"`.
That attribute exists because `docker-compose.yml` sets the json-file
`labels: "com.docker.compose.service"` log option on web and worker (and
deliberately not on redis, whose ~35k `BGSAVE` lines a day have no incident
value). Identity therefore comes from the compose service name, never from a
container ID, and survives container replacement, image replacement, host
reboot and repeated deploys.

`publish_multi_logs: true` is required: without it the agent follows only the
single most recently modified file that matches a glob, so a chatty container
starves the others. With it, each container file gets its own stream
(`<instance>__var_lib_docker_containers_<id>_<id>-json.log`); streams are
created only for files that have passed the filter at least once. Query by
service with Logs Insights:

```
fields @timestamp, attrs.`com.docker.compose.service` as service, log
| filter service = "web"
| sort @timestamp desc
```

Local Docker rotation (10 MB x 3 per container) is unchanged; the agent only
reads.

## Metrics (Phase 1, unchanged here)

Alarms read the `InstanceId`-only aggregate. `diskio` is pinned to `nvme0n1`
and `net` to `ens5`; widening either reintroduces the per-device series churn
that cost ~$17/month before Phase 1.

## Apply

The source basename must be `file_config.json` so the agent rewrites the
existing `amazon-cloudwatch-agent.d/file_file_config.json` entry instead of
adding a second one that would merge:

```sh
sudo cp /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.d/file_file_config.json \
        /root/cwa-backups/file_file_config.json.$(date -u +%Y%m%dT%H%M%SZ).bak
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
     -a fetch-config -m ec2 -s -c file:/path/to/file_config.json
```

Only the agent restarts; application containers are untouched. The agent
resumes files from its saved offsets (no re-shipping).

## Rollback

Re-apply the newest backup from `/root/cwa-backups/` with the same
`fetch-config` command.
