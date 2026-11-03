# Raspberry Pi 5 Docker deployment

**Status: deployed and verified (November 2026).** This project runs on a Raspberry Pi 5 using Docker Compose project `service-health-monitor-pi`.

## Deployed services

| Service | Purpose |
| --- | --- |
| `worker` | DNS, TCP and HTTP/HTTPS checks every 300 seconds |
| `web` | Read-only dashboard at `127.0.0.1:18080` |
| `admin` | Authenticated administration at `127.0.0.1:18081` |
| `notifier` | ntfy event dispatcher every 60 seconds |
| `heartbeat` | External Healthchecks.io ping every 60 seconds, conditional on fresh checks |

An additional one-shot service, `registry-init`, imports the initial YAML targets into SQLite. The SQLite data is stored in the dedicated `pi_data` Docker volume at `/var/lib/service-health-monitor/health.db`. All application images run as UID/GID `10001:10001`.

Six home-network targets are enabled. Three demonstration targets remain disabled. Both local incident notifications and external heartbeat outage/recovery notifications were received on a phone during controlled end-to-end tests.

## Compose files

- `compose.pi.yaml`: worker, web, admin and registry initialization
- `compose.pi.ntfy.yaml`: opt-in `notifications` profile
- `compose.pi.heartbeat.yaml`: opt-in `heartbeat` profile

## First-time preparation

**The existing Pi installation is already configured.** These instructions apply to a new installation and must not be rerun blindly against the working deployment.

```bash
git clone https://github.com/RatushAnton/service-health-monitor.git ~/service-health-monitor-prod
cd ~/service-health-monitor-prod
cp deploy/pi/targets.example.yaml deploy/pi/targets.yaml
umask 077
openssl rand -hex 32 > deploy/pi/admin-token.txt
sudo chown 10001:10001 deploy/pi/admin-token.txt
sudo chmod 0400 deploy/pi/admin-token.txt
```

Review the target YAML before starting the initial stack. The registry initializer imports YAML **once**; subsequent changes are made through the admin API. Set up notification and heartbeat secrets using their separate runbooks. No secret values should be committed or pasted into logs.

## Safe operational checks

From the Pi's deployment directory:

```bash
docker compose \
  -f compose.pi.yaml \
  -f compose.pi.ntfy.yaml \
  -f compose.pi.heartbeat.yaml \
  --profile notifications \
  --profile heartbeat ps
```

Inspect logs without restarting services:

```bash
docker compose \
  -f compose.pi.yaml \
  -f compose.pi.ntfy.yaml \
  -f compose.pi.heartbeat.yaml \
  logs --tail 30 worker notifier heartbeat
```

Verify the local read-only API:

```bash
curl -fsS http://127.0.0.1:18080/api/health
curl -fsS http://127.0.0.1:18080/api/monitors
```

## Private UI access

From a Mac, use an SSH tunnel and substitute the Pi's reachable SSH address:

```bash
ssh -N \
  -L 28080:127.0.0.1:18080 \
  -L 28081:127.0.0.1:18081 \
  USER@PI_HOST
```

Open `http://127.0.0.1:28080/` for the dashboard and `http://127.0.0.1:28081/admin/login` for administration. Do not expose the interfaces on public inbound ports.

## Backups and external alerts

The Pi's daily systemd backup timer runs at **03:15 local time**, retaining three local snapshots. An independent macOS launchd job attempts an off-device SSH export every six hours. Backups use SQLite online snapshots, integrity checks, temporary restore validation and SHA-256 transfer verification. See [Backup and recovery](backup-recovery.md).

The [ntfy dispatcher](ntfy-notifications.md) handles persisted DOWN/RECOVERED incident events. The [external heartbeat](external-heartbeat.md) detects complete Pi/connectivity outages or stale monitoring activity without any static public IP or router port forwarding.

## Safety and limitations

- Never run `docker compose down -v` or `docker system prune`; preserve the data volume and unrelated Pi services.
- Prefer explicit per-service operations over recreating the full Compose stack.
- Do not share private hostnames, admin credentials, ntfy topics or Healthchecks Ping URLs.
- Docker reported that the Pi's kernel/cgroup configuration does **not enforce the configured memory limit**; containers nonetheless started successfully.
- This is a single-host home-lab deployment, not a high-availability setup.
- Backup integrity and temporary restore checks were validated, but a **full live-service disaster-recovery cutover** has not yet been tested.
