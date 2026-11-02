# Service Health Monitor

[![CI](https://github.com/RatushAnton/service-health-monitor/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/RatushAnton/service-health-monitor/actions/workflows/ci.yml)

A lightweight Python CLI for monitoring network connectivity and service
availability through DNS, TCP, and HTTP/HTTPS checks.

Built as a hands-on DevOps and infrastructure engineering portfolio project.

**Current milestone: Phase 7G.4D completed (isolated Docker end-to-end validation).**

The Docker worker, read-only dashboard, authenticated administration interface, and SQLite registry have been validated together.

See the [Docker E2E validation runbook](docs/e2e-validation.md) for setup instructions, test scenarios, and results.

The CLI supports persistent monitoring, incident detection,
historical reporting, and queued webhook notifications with retry
scheduling and lease-based worker coordination.

## Features

- YAML-based configuration for monitored targets
- DNS resolution checks (A and AAAA records)
- TCP port connectivity checks
- HTTP/HTTPS status-code checks
- Response-time measurements in milliseconds
- Failure classification and error messages
- SQLite persistence for successful and failed checks
- CLI commands to check services and inspect historical results
- Persistent incident detection with configurable thresholds
- Durable DOWN and RECOVERED incident events
- Webhook notification delivery
- Exponential retry backoff and delivery diagnostics
- Atomic event claims and time-limited worker leases
- Automated pytest tests for incidents, SQLite, CLI, and webhooks
- GitHub Actions CI for Python 3.11, 3.12, and 3.13

### Monitoring layers

| Check | What it verifies |
|---|---|
| DNS | A hostname resolves to the requested record type |
| TCP | A connection can be established to a host and port |
| HTTP | An endpoint returns the expected HTTP status code |

A successful TCP check does not guarantee that an HTTP application is healthy.

## Requirements

- Python 3.11+
- Git
- macOS or Linux

Python dependencies are defined in `pyproject.toml`:

- PyYAML
- dnspython
- HTTPX

SQLite support is provided by Python's standard library.

## Installation

Clone the repository:

```bash
git clone https://github.com/RatushAnton/service-health-monitor.git
cd service-health-monitor
```

Create and activate a Python virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the project:

```bash
python -m pip install -e '.[dev]'
```

Create your local monitoring configuration:

```bash
cp config/targets.example.yaml config/targets.yaml
```

Edit `config/targets.yaml` to define your monitoring targets.

The local configuration and database files are excluded from Git.

## Usage

### Run all health checks

```bash
healthmon check --config config/targets.yaml --db data/health.db
```

The CLI displays each target's status, latency, and error details.

All results are saved to SQLite.

### Show the latest recorded results

```bash
healthmon summary --db data/health.db
```

Displays the most recent saved check for each target.

**Note:** This is a historical snapshot, not a live status query.

### View a target's history

```bash
healthmon history --target github_web --limit 10
```

View historical results for another target:

```bash
healthmon history --target local_demo --limit 20
```

Both healthy and failed checks are retained.

### Deliver incident notifications

Set the URL of a compatible webhook receiver:

```bash
export HEALTHMON_WEBHOOK_URL="https://your-webhook.example/notify"
```

Deliver queued events:

```bash
healthmon notify --db data/health.db --webhook-url "$HEALTHMON_WEBHOOK_URL"
```

The receiver must accept the application's JSON event format.
Successful deliveries are recorded in SQLite. Failed requests
are retried when their backoff delay expires.

Use HTTPS for remote receivers and keep credentials out of Git.

### CLI exit codes

| Code | Meaning |
|---|---|
| 0 | Command completed successfully |
| 1 | At least one live health check failed |
| 2 | Configuration or argument error |
| 3 | Database error |
| 4 | Webhook delivery error |

Reporting commands may return exit code 0 even when historical results
include failed services.

## Configuration

Targets are defined in `config/targets.yaml`.

Example:

```yaml
settings:
  timeout_seconds: 3
  failure_threshold: 3
  recovery_threshold: 1

targets:
  - id: dns_resolution
    name: DNS Resolution
    type: dns
    host: example.com
    record_type: A

  - id: internet_tcp
    name: Internet Connectivity
    type: tcp
    host: 1.1.1.1
    port: 443

  - id: example_web
    name: Example Website
    type: http
    url: https://example.com/
    expected_status: 200
```

DNS targets can optionally specify a resolver IP address.

Failure and recovery thresholds are applied during each
`healthmon check` execution. Incident state persists between runs.

## Local test service

The example configuration includes a local HTTP service on port 8765.

Start it in a separate terminal:

```bash
mkdir -p /tmp/healthmon-demo
echo "Service healthy" > /tmp/healthmon-demo/index.html

python3 -m http.server 8765 \
  --bind 127.0.0.1 \
  --directory /tmp/healthmon-demo
```

Run the monitor:

```bash
healthmon check
```

Stop the test server using `Ctrl+C` and run the monitor again.

The local HTTP target should now report DOWN with a connection error.
The failure will also be recorded in SQLite.

## Project structure

```text
service-health-monitor/
├── .github/
│   └── workflows/
│       └── ci.yml
├── config/
│   └── targets.example.yaml
├── src/
│   └── healthmon/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── diagnosis.py
│       ├── probes.py
│       ├── storage.py
│       ├── models.py
│       └── alerting.py
├── tests/
│   ├── test_alerting.py
│   ├── test_cli.py
│   ├── test_diagnosis.py
│   └── test_storage.py
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

### Module responsibilities

| Module | Responsibility |
|---|---|
| `cli.py` | Parse commands, execute probes, and display results |
| `config.py` | Load and validate YAML configuration |
| `probes.py` | Perform DNS, TCP, and HTTP checks |
| `storage.py` | Store checks, incident states, events, and delivery records |
| `diagnosis.py` | Evaluate incident and recovery transitions |
| `models.py` | Placeholder for shared models |
| `alerting.py` | Send incident events to HTTP webhook endpoints |

## Incident detection

Phase 4A introduces a state machine that tracks consecutive failures and
successes for each monitored target.

With `failure_threshold: 3` and `recovery_threshold: 1`:

1. The first two failures increment the failure counter.
2. The third consecutive failure produces a DOWN event.
3. Further failures do not produce duplicate DOWN events.
4. The first successful check produces a RECOVERED event.

A separate test also verified recovery after two consecutive successes.

Incident state and events are persisted in SQLite. Notifications
are delivered separately through `healthmon notify`.

## Notification reliability

Incident transitions are saved in SQLite in the same transaction
as the associated health-check result and updated target state.

The notification queue uses atomic event claims and unique lease
tokens to coordinate competing workers. Leases last 60 seconds.

Failed deliveries use exponential backoff:

| Failure | Retry delay |
|---|---|
| 1 | 60 seconds |
| 2 | 120 seconds |
| 3 | 240 seconds |
| 4 | 480 seconds |
| 5 | 960 seconds |
| 6 | 1920 seconds |
| 7+ | 3600 seconds (maximum) |

Successful delivery clears retry metadata and records a timestamp.
Failed delivery retains the event for another attempt.

Delivery is **at-least-once**, not exactly-once. If a worker crashes
after the receiver accepts an event but before SQLite records success,
a later worker may send the same event again. Receivers should use
event IDs for deduplication where supported.

### Verified end-to-end demonstration

A local outage and recovery test verified:

- Three consecutive failures opening one DOWN incident
- Notification delivery after an initial webhook failure
- A successful check closing the incident
- One RECOVERED notification
- Four recorded checks, two events, and zero open incidents

## Development

### Install development dependencies

```bash
python -m pip install -e '.[dev]'
```

### Run automated tests

Run the complete pytest suite:

```bash
python -m pytest -v
```

Run the tests with compact output:

```bash
python -m pytest -q
```

Run a specific test module:

```bash
python -m pytest -v tests/test_storage.py
```

The suite currently contains 94 test cases covering:

- Incident detection, recovery, and threshold validation
- SQLite persistence, schema migration, and transaction rollback
- Notification queue claims, leases, and retry state
- Webhook JSON payloads and HTTP failures
- CLI execution, reporting, and exit codes

Tests use temporary SQLite databases and mocked network
operations. They do not require access to monitored services.

### Code quality checks

Run Ruff linting:

```bash
ruff check src/healthmon tests
```

Verify formatting:

```bash
ruff format --check src/healthmon tests
```

Check Python compilation:

```bash
python -m compileall -q src tests
```

### Continuous integration

The GitHub Actions workflow is defined in
`.github/workflows/ci.yml`.

CI runs automatically on pushes and pull requests targeting
`main`. It can also be started manually using the Actions tab.

The workflow runs on Ubuntu using Python 3.11, 3.12, and
3.13. Each environment installs the project and executes
compilation checks, Ruff linting, formatting checks, and pytest.

All three Python versions passed during the initial CI
deployment.

[View GitHub Actions runs](https://github.com/RatushAnton/service-health-monitor/actions)

## Project progress

| Phase | Description | Status |
|---|---|---|
| 1 | Repository and Python environment | Complete |
| 2 | DNS, TCP, HTTP probes, and CLI | Complete |
| 3 | SQLite persistence, summary, and history | Complete |
| 4A | In-memory incident detection | Complete |
| 4B | Persistent incident state | Complete |
| 4C | Incident integration with CLI | Complete |
| 5 | Notifications and recovery alerts | Complete |
| 6 | Automated tests and GitHub Actions | Complete |
| 7G.4 | SQLite registry and browser administration | Complete |
| 7G.4D | Isolated Docker E2E lifecycle validation | Complete |
| 7G.5 | Raspberry Pi Docker deployment | Planned |
| 7H | Notification integration and hardening | Planned |

## Known limitations

- Notifications require a separate `healthmon notify` execution.
- Webhook delivery is at-least-once, not exactly-once.
- Expired leases can allow redelivery after worker crashes.
- Raspberry Pi deployment has not yet been completed.
- Historical UP status may be stale.
- Network errors do not always identify the underlying root cause.
- Python versions outside the CI matrix are not automatically tested.

## Security and local data

The following files are excluded from Git:

- `config/targets.yaml` — local monitoring configuration
- `data/` — SQLite runtime data
- `.env` — future local environment variables and secrets

Avoid committing private hostnames, internal IP addresses, and credentials.

## Project goal

Build a practical, self-hosted service monitor that runs periodically on
a Raspberry Pi, records service health, detects persistent outages,
and sends notifications when incidents open or recover.

This project is developed incrementally to demonstrate Python,
network troubleshooting, SQLite, Git, automation, monitoring,
and DevOps deployment practices.
