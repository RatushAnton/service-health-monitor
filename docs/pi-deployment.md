# Raspberry Pi Docker Deployment

## Status

Phase 7G.5B: deployment configuration prepared.

This document describes the deployment procedure. It does not
indicate that a Raspberry Pi deployment has been completed.

## Architecture

- Host: Raspberry Pi 5 running 64-bit Linux
- Runtime: Docker Engine and Docker Compose
- Compose file: compose.pi.yaml
- Project: service-health-monitor-pi
- Image: service-health-monitor:pi
- Database: dedicated pi_data Docker volume
- Worker interval: 300 seconds
- Web: 127.0.0.1:18080
- Admin: 127.0.0.1:18081
- Container user: UID 10001

The worker, web API, admin interface and registry initializer
share a dedicated database volume.

This deployment must not reuse the existing test database or
alter unrelated Docker containers.

## Safety requirements

- Do not remove or modify existing Docker volumes.
- Do not run docker system prune.
- Do not use docker compose down -v.
- Do not expose the admin interface on 0.0.0.0.
- Keep credentials out of Git.
- Use the SD card only as an initial home-lab storage target.
- Confirm backup and restore procedures before relying on
  the service for important monitoring.

## First-time host preparation

These steps are for the deployment phase, not Mac-side setup.

Clone into a new directory without changing older checkouts:

    git clone https://github.com/RatushAnton/service-health-monitor.git \
      ~/service-health-monitor-prod

    cd ~/service-health-monitor-prod

Prepare the initial registry configuration:

    cp deploy/pi/targets.example.yaml deploy/pi/targets.yaml

Review targets.yaml before starting monitoring.

The registry bootstrap imports YAML only on first initialization.
Afterward, monitor changes are managed through the admin UI/API.
Restarting the registry initializer does not re-import the YAML.

## Administration secret

Generate a unique token directly on the Raspberry Pi:

    umask 077
    openssl rand -hex 32 > deploy/pi/admin-token.txt

The container runs as UID 10001. On Linux, file-backed Docker
Compose secrets preserve host file ownership and permissions.

Grant only the container UID access:

    sudo chown 10001:10001 deploy/pi/admin-token.txt
    sudo chmod 0400 deploy/pi/admin-token.txt

Verify metadata without displaying the token:

    stat -c '%u:%g %a %n' deploy/pi/admin-token.txt

Expected owner: 10001:10001
Expected permissions: 400

Do not paste or commit the credential.

## Build and deploy

First validate the Compose configuration:

    docker compose -f compose.pi.yaml config --quiet

Build the application image once:

    docker compose -f compose.pi.yaml build worker

Start the isolated application stack:

    docker compose -f compose.pi.yaml up -d --no-build

Inspect its status:

    docker compose -f compose.pi.yaml ps

    docker compose -f compose.pi.yaml logs --tail 50 \
      registry-init worker web admin

The registry initializer should exit successfully.
The other three application services should remain running.

## Local verification on the Pi

    curl -fsS http://127.0.0.1:18080/api/health

    curl -fsS http://127.0.0.1:18080/api/monitors

    curl -sS -o /dev/null -w 'HTTP %{http_code}\n' \
      http://127.0.0.1:18081/admin/login

The dashboard may initially have no recorded checks until
the worker completes its first cycle.

## Access from a Mac

Create an SSH tunnel from the Mac:

    ssh -N \
      -L 28080:127.0.0.1:18080 \
      -L 28081:127.0.0.1:18081 \
      ratush@PI_HOST

Replace PI_HOST with the Raspberry Pi's reachable hostname
or IP address.

Open these URLs on the Mac:

Dashboard: http://127.0.0.1:28080/
Admin: http://127.0.0.1:28081/admin/login

The tunnel transports these HTTP connections over SSH.
No additional LAN-facing ports are required on the Pi.

## Operational commands

Status:

    docker compose -f compose.pi.yaml ps

Worker logs:

    docker compose -f compose.pi.yaml logs --tail 100 worker

Stop only the new monitoring stack:

    docker compose -f compose.pi.yaml down

The last command preserves the named SQLite data volume.
It must not be combined with -v.

## Persistence and backups

The monitoring database is stored in the named pi_data volume.

Database backups must use SQLite's online backup API or
another consistent SQLite backup mechanism, not a simple
copy of an actively written database file.

Automated backups, off-device storage and restoration
verification are planned for Phase 7G.5C.

## Initial deployment limitations

This is a home-lab deployment, not a highly available service.

Resource limits are initial safeguards rather than measured
production requirements. Review them after observing actual
memory and CPU consumption.

Reliable power and persistent storage are prerequisites
for a production-grade installation.

## Next milestone

Phase 7G.5C will implement SQLite backups, retention,
restore verification and operational hardening.
