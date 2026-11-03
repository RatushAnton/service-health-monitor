# External Heartbeat — Healthchecks.io

## Purpose

Detect complete Raspberry Pi outages or stalled monitoring-worker activity using an independent external monitoring service.

The Pi sends outbound HTTPS heartbeats. No static public IP, dynamic DNS or router port forwarding is required.

A heartbeat is sent only when every enabled, non-archived monitor has recent check history. Individual monitored services may be DOWN while the monitoring worker is functioning correctly.

## Architecture

    Monitoring worker
           |
        SQLite
           |
    Heartbeat container
           |
      Outbound HTTPS
           |
     Healthchecks.io
           |
     ntfy / email alerts

## Healthchecks.io configuration

Create a check using a simple schedule:

- Name: Service Health Monitor - Raspberry Pi
- Period: 2 minutes
- Grace time: 3 minutes
- Notification integration: ntfy
- Optional fallback: email

Keep the generated UUID Ping URL private. It acts as a credential.

## Secret preparation

On the Raspberry Pi:

    cd ~/service-health-monitor-prod
    umask 077
    read -rsp "Healthchecks Ping URL: " HC_URL
    printf '\n'
    printf '%s\n' "$HC_URL" > deploy/pi/healthchecks-url.txt
    unset HC_URL

Set permissions:

    sudo chown 10001:10001 deploy/pi/healthchecks-url.txt
    sudo chmod 0400 deploy/pi/healthchecks-url.txt

Verify without printing the URL:

    stat -c '%u:%g %a %n' deploy/pi/healthchecks-url.txt
    git check-ignore deploy/pi/healthchecks-url.txt

Expected owner and mode: 10001:10001 400

## Build the separate image

    docker build -t service-health-monitor:heartbeat .

Verify the container user:

    docker image inspect service-health-monitor:heartbeat --format '{{.Config.User}}'

Expected: 10001:10001

## Docker Compose validation

    docker compose \
      -f compose.pi.yaml \
      -f compose.pi.ntfy.yaml \
      -f compose.pi.heartbeat.yaml \
      --profile heartbeat \
      config --quiet

## Safe dry-run test

The following command must not send any external requests:

    docker compose \
      -f compose.pi.yaml \
      -f compose.pi.ntfy.yaml \
      -f compose.pi.heartbeat.yaml \
      run --rm --no-deps heartbeat \
      --db /var/lib/service-health-monitor/health.db \
      --ping-url-file /run/secrets/healthchecks_ping_url \
      --once --dry-run

Expected result: enabled monitors have recent check history; no heartbeat sent.

## Activate the heartbeat

Start only the heartbeat service:

    docker compose \
      -f compose.pi.yaml \
      -f compose.pi.ntfy.yaml \
      -f compose.pi.heartbeat.yaml \
      --profile heartbeat \
      up -d --no-deps --no-build heartbeat

The heartbeat runs every 60 seconds, with a maximum acceptable check age of 600 seconds.

A complete heartbeat outage is normally detected around five minutes after the last successful ping.

A worker stall can take longer to detect because recent check records remain fresh until the configured maximum age expires.

## Controlled outage test

Confirm that Healthchecks.io shows the check as UP.

Stop only the heartbeat service:

    docker compose \
      -f compose.pi.yaml \
      -f compose.pi.ntfy.yaml \
      -f compose.pi.heartbeat.yaml \
      stop heartbeat

Verify that Healthchecks.io marks the check DOWN and sends a mobile alert.

Restart only the heartbeat service:

    docker compose \
      -f compose.pi.yaml \
      -f compose.pi.ntfy.yaml \
      -f compose.pi.heartbeat.yaml \
      start heartbeat

Verify that Healthchecks.io reports recovery and sends a recovery notification if configured.

Do not shut down the Raspberry Pi, stop the network interfaces, reboot the host or interrupt the existing monitoring services.

## Limitations

- Healthchecks.io and ntfy are external service dependencies.
- Notification delivery depends on the external provider and configured integrations.
- The heartbeat verifies monitoring-worker freshness, not the notifier's process health.
- The heartbeat does not make the Raspberry Pi highly available.
- Docker memory limits depend on cgroup support in the running kernel.
- The Ping URL must never appear in Git, logs or shared screenshots.

Never run docker compose down -v or docker system prune.
