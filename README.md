
# Service Health Monitor

A lightweight Python CLI for monitoring network connectivity and service
availability through DNS, TCP, and HTTP/HTTPS checks.

Built as a hands-on DevOps and infrastructure engineering portfolio project.

**Current milestone: Phase 4A completed (incident state machine).**

The monitoring CLI and SQLite history are functional. Incident detection
logic has been implemented independently but is not yet connected to the
CLI or database.

## Features

- YAML-based configuration for monitored targets
- DNS resolution checks (A and AAAA records)
- TCP port connectivity checks
- HTTP/HTTPS status-code checks
- Response-time measurements in milliseconds
- Failure classification and error messages
- SQLite persistence for successful and failed checks
- CLI commands to check services and inspect historical results
- In-memory incident state machine with configurable thresholds

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

### CLI exit codes

| Code | Meaning |
|---|---|
| 0 | Command completed successfully |
| 1 | At least one live health check failed |
| 2 | Configuration or argument error |
| 3 | Database error |

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

The failure and recovery thresholds are currently validated by the
configuration loader. Their incident-detection behavior is implemented
in `diagnosis.py` but is not yet used by the CLI.

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
| `storage.py` | Initialize SQLite and save/query health results |
| `diagnosis.py` | Evaluate incident and recovery transitions |
| `models.py` | Placeholder for shared models |
| `alerting.py` | Placeholder for notifications |

## Incident detection

Phase 4A introduces a state machine that tracks consecutive failures and
successes for each monitored target.

With `failure_threshold: 3` and `recovery_threshold: 1`:

1. The first two failures increment the failure counter.
2. The third consecutive failure produces a DOWN event.
3. Further failures do not produce duplicate DOWN events.
4. The first successful check produces a RECOVERED event.

A separate test also verified recovery after two consecutive successes.

**Current limitation:** Incident state exists only in Python memory.
It is not persisted between CLI executions.

## Development

Run linting:

```bash
ruff check src/healthmon
```

Check formatting:

```bash
ruff format --check src/healthmon
```

Check Python compilation:

```bash
python -m compileall -q src
```

DNS, TCP, HTTP, SQLite, CLI, and incident-lifecycle functionality have
been verified through manual tests.

Automated pytest tests and GitHub Actions CI are planned.

## Project progress

| Phase | Description | Status |
|---|---|---|
| 1 | Repository and Python environment | Complete |
| 2 | DNS, TCP, HTTP probes, and CLI | Complete |
| 3 | SQLite persistence, summary, and history | Complete |
| 4A | In-memory incident detection | Complete |
| 4B | Persistent incident state | Planned |
| 4C | Incident integration with CLI | Planned |
| 5 | Notifications and recovery alerts | Planned |
| 6 | Automated tests and GitHub Actions | Planned |
| 7 | Raspberry Pi deployment with systemd | Planned |
| 8 | End-to-end outage demo and documentation | Planned |

## Known limitations

- Checks currently run only when manually invoked.
- Incident thresholds are not yet integrated into the CLI.
- Incident state does not survive process restarts.
- No notifications or recovery alerts are sent.
- No automatic scheduling or Raspberry Pi deployment is configured.
- Historical UP status may be stale.
- Network errors do not always identify the underlying root cause.
- Automated tests and CI have not yet been added.

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
