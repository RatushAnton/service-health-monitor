# ntfy Notification Dispatcher

**Status: deployed and end-to-end verified (November 2026).** On Raspberry Pi 5, a controlled synthetic incident generated a `DOWN` and a `RECOVERED` event. Both events were dispatched on the first attempt and received on a phone.

## Operation

The dispatcher polls SQLite's persistent `incident_events` queue for delivery-eligible events.

- Monitoring interval: 300 seconds
- Notification polling interval: 60 seconds
- Maximum delivery batch: 20 events
- Transport: ntfy HTTPS publish API
- Container: non-root UID/GID `10001:10001`
- Deployment: `compose.pi.ntfy.yaml`, optional `notifications` profile

## Implementation

- `src/healthmon/ntfy.py`: validates the topic endpoint and publishes messages
- `src/healthmon/notifier.py`: background dispatch loop
- `src/healthmon/cli.py`: claim/send orchestration
- `src/healthmon/storage.py`: durable queue, leases, retries and delivery timestamps
- `deploy/pi/ntfy-url.txt`: private topic URL, excluded from Git

The complete topic URL is read from a private file, not supplied on the command line. On public ntfy.sh, unprotected topics depend on unpredictable names for privacy; never include sensitive information in alerts.

## Reliability

Incident transitions are stored in SQLite before delivery. The notifier claims events with an expiring lease, retries failures with exponential backoff, and marks successful sends as delivered. It can resume processing after a restart. Delivery is **at-least-once**, not exactly-once: crashes at certain boundaries may result in duplicate messages.

Before enabling the dispatcher against an existing database, examine pending historical events to avoid unexpected old notifications. Never delete incident history merely to clear a queue.

## External host monitoring

A Pi-hosted notifier cannot alert about a complete Pi power or connectivity failure. This gap is addressed by the [independent external heartbeat](external-heartbeat.md), which was deployed and verified using a controlled stop/restart test. Healthchecks.io sent both the outage and recovery notifications to the phone.

The heartbeat verifies fresh worker activity, **not** whether the notifier itself is running. Both external services remain dependencies.

## Safety

Keep credentials private, preserve the named SQLite volume, and avoid broad Docker cleanup or service restarts during routine diagnostics. See [Raspberry Pi deployment](pi-deployment.md).
