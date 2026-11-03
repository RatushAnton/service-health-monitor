# Code tour — Service Health Monitor

A quick reference for navigating the repository before a code walkthrough or DevOps interview. The project already has useful function docstrings; this guide focuses on **where to look** and **why the implementation matters**, rather than adding repetitive inline comments everywhere.

## Explain the project in 60 seconds

> I built a Python monitoring system running in Docker on a Raspberry Pi 5. It periodically checks DNS, TCP and HTTP endpoints, records results and incident state in SQLite, exposes a dashboard and authenticated management API, and sends DOWN/RECOVERED push notifications via ntfy. I added verified scheduled backups and an independent Healthchecks.io heartbeat that detects when the Pi or monitoring worker stops reporting. I tested the whole pipeline, including simulated outage and recovery alerts on my phone.

## The two critical flows

**Monitored-service failure:** `worker.run_worker()` → `registry.read_registry_config()` → `cli.run_checks_config()` → DNS/TCP/HTTP probe → `storage.record_check_with_incident()` → SQLite `incident_events` → `notifier.run_notifier()` / `cli.send_notifications()` → `ntfy.send_ntfy()` → phone.

**Monitoring-host failure:** `heartbeat.run_heartbeat()` → `heartbeat.worker_is_fresh()` queries the latest checks of enabled monitors → `heartbeat.run_cycle()` sends an HTTPS ping only when fresh → Healthchecks.io alerts externally if the pings stop → ntfy on phone.

## Key source files and functions

| File | Function or component | What to say when presenting it |
| --- | --- | --- |
| `src/healthmon/worker.py` | `run_worker()` | Periodic scheduler; loads the active monitoring configuration and runs check cycles. |
| `src/healthmon/config.py` | `load_config()` | Validates YAML target definitions and settings before use. |
| `src/healthmon/registry.py` | `bootstrap_registry()` | Imports initial target configuration only once; avoids overwriting later admin edits. |
| `src/healthmon/registry.py` | `read_registry_config()` | Reads current enabled monitors from SQLite for runtime checks. |
| `src/healthmon/probes.py` | `check_dns()`, `check_tcp()`, `check_http()` | Protocol-specific health checks, latency measurements, and failure classification. |
| `src/healthmon/diagnosis.py` | `evaluate_check()` | Pure in-memory state machine: failure threshold opens DOWN; recovery threshold closes it. |
| `src/healthmon/storage.py` | `record_check_with_incident()` | Writes the raw result, updated incident state and transition event atomically. |
| `src/healthmon/storage.py` | `claim_pending_event()` | Atomically leases one undelivered event for reliable dispatch. |
| `src/healthmon/storage.py` | `mark_event_delivered()`, `mark_delivery_failed()` | Completes a lease or schedules a bounded retry after delivery failure. |
| `src/healthmon/notifier.py` | `run_notifier()` | Background polling loop that invokes notification delivery. |
| `src/healthmon/cli.py` | `send_notifications()` | Claims and dispatches persisted events; integrates with webhook/ntfy destinations. |
| `src/healthmon/ntfy.py` | `send_ntfy()` | Sends a DOWN/RECOVERED message to ntfy, handling publish failures without exposing the secret URL. |
| `src/healthmon/api.py` | `latest_monitors()`, `incidents()` | Read-only views used by the monitoring dashboard. |
| `src/healthmon/admin_api.py` | `require_admin()`, `add_monitor()`, `edit_monitor()` | Authentication and monitor-management endpoints. |
| `src/healthmon/registry.py` | `set_monitor_enabled()`, `archive_monitor()` | Supports disabling or archiving a target while preserving history. |
| `src/healthmon/backup.py` | `create_backup()`, `verify_database()`, `verify_restore()` | Consistent SQLite snapshots plus integrity and temporary restore validation. |
| `src/healthmon/heartbeat.py` | `worker_is_fresh()` | Reads only enabled monitor records; intentionally checks **freshness**, not whether targets are UP. |
| `src/healthmon/heartbeat.py` | `run_cycle()` | Withholds the ping if check history is stale; otherwise publishes an external heartbeat. |

## Five implementation decisions worth explaining

1. **Why SQLite?** Small single-host deployment, simple durability and transactions, without operating a separate database server. It is a design fit for this scale, not a general-purpose replacement for clustered storage.
2. **Why a state machine?** Three consecutive failures distinguish persistent incidents from isolated probe failures. Recovery events are emitted only when the state changes.
3. **Why persist events before notifying?** An HTTP notification can fail independently of a successful probe. A durable queue plus leases and retries lets the notifier resume after restarts. Delivery is **at-least-once**, not exactly-once.
4. **Why monitor the monitor externally?** The Pi cannot send an alert about its own power or internet failure. Healthchecks.io can detect a missing outbound ping, without a static IP or inbound router rule.
5. **Why verified backups?** A backup file is not necessarily restorable. SQLite's online backup API, integrity checks and temporary restore checks increase confidence; full service cutover is still an acknowledged gap.

## How to add comments without cluttering the code

Use a **docstring** for a function's contract. Explain inputs or outputs when non-obvious, important side effects, failure modes and invariants. Add a short inline comment only where the reason for an implementation choice is not apparent from the code.

Example of an explanatory docstring style (illustrative, not a required code change):

```python
def claim_pending_event(db_path, lease_seconds=60):
    """Atomically reserve the next deliverable incident event.

    Uses a short-lived SQLite lease so multiple dispatchers do not
    normally send the same event concurrently. An expired lease can
    be reclaimed after a crash, so delivery remains at-least-once.
    """
    ...
```

**Avoid** comments such as `# loop through events` before `for event in events`, or copying the function name into a redundant docstring. Also avoid altering implementation logic in a documentation-only PR.

For live code navigation, search for `def run_worker`, `def evaluate_check`, `def record_check_with_incident`, `def send_notifications`, or `def worker_is_fresh` in your editor. These five entry points cover most of the engineering story.

## Safe demo outline

1. Show the README architecture and GitHub Actions CI badge.
2. Show the dashboard and six production monitor definitions (hide private addressing if recording the demo).
3. Trace a failed probe through `evaluate_check()` and `record_check_with_incident()` into the incident queue.
4. Show the ntfy dispatcher and the observed DOWN/RECOVERED notifications.
5. Explain why the heartbeat depends on fresh worker history and why it uses external monitoring.
6. Finish with the backup runbook and explicit limitations; do not disrupt real services during an interview demo.
