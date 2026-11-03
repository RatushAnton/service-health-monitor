# ntfy Notification Dispatcher

## Overview

The notification dispatcher delivers DOWN and RECOVERED events
from the Service Health Monitor SQLite incident queue.

- Monitoring interval: 300 seconds
- Notification polling interval: 60 seconds
- Delivery batch limit: 20 events
- Transport: ntfy HTTPS publishing API
- Runtime: non-root Docker container, UID 10001
- Deployment: optional Compose overlay

## Files

- src/healthmon/ntfy.py: ntfy publisher
- src/healthmon/notifier.py: background dispatcher
- compose.pi.ntfy.yaml: optional Compose service
- deploy/pi/ntfy-url.txt: local secret, excluded from Git

## Configuration

The notifier reads a complete HTTPS ntfy topic URL from a
private file.

Never commit the topic URL or include it in CLI arguments.

The public ntfy.sh service supports unguessable topic names,
but an unprotected topic is not access-controlled. Avoid
including sensitive information in notifications.

## Safety before enabling

The existing SQLite database may contain historical,
undelivered incident events.

Inspect the pending queue before starting the dispatcher.
Starting the notifier, including with --once, can send
those events immediately.

Do not clear historical events without reviewing them.
Preserve the SQLite backup and incident history.

## Deployment requirements

- Build the notifier image from the reviewed source.
- Ensure the ntfy secret is readable by container UID 10001.
- Use compose.pi.yaml together with compose.pi.ntfy.yaml.
- Enable the notifications profile explicitly.
- Do not replace the existing production monitoring image.
- Do not modify unrelated Docker containers or volumes.
- Never use docker compose down -v or docker system prune.

## Reliability

Notifications use the existing SQLite delivery queue.

- Successful sends are marked delivered.
- Failed sends are scheduled for retry.
- Delivery claims use leases.
- Restarting the dispatcher resumes pending work.
- Delivery is at-least-once, not exactly-once; duplicate
  notifications are possible after certain failures.

## Limitations

This notifier runs on the monitored Raspberry Pi.

A complete Pi outage, loss of power or loss of connectivity
can prevent notifications from being delivered.

An external heartbeat monitor is required to detect a
complete monitoring-host outage.
