# Service Health Monitor

[![CI](https://github.com/RatushAnton/service-health-monitor/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/RatushAnton/service-health-monitor/actions/workflows/ci.yml)

A self-hosted network and infrastructure monitoring system built with Python, Docker Compose, FastAPI, SQLite and GitHub Actions.

Developed as a hands-on DevOps portfolio project and deployed on a Raspberry Pi 5.

## Features

- DNS A/AAAA, TCP connectivity and HTTP/HTTPS monitoring
- Background monitoring with configurable failure/recovery thresholds
- Persistent check results, response times and incident history
- Web dashboard and authenticated administration interface
- SQLite-backed monitor configuration without worker restarts
- Durable DOWN and RECOVERED event queue
- Automated ntfy push notifications with retries and delivery leases
- Daily SQLite backups, integrity verification and retention
- Automated backup export to a Mac
- Optional external heartbeat with Healthchecks.io
- Automated tests and multi-version GitHub Actions CI

## Architecture

    Raspberry Pi 5
    |
    +-- Monitoring worker (300 seconds)
    |   +-- DNS / TCP / HTTP probes
    |   +-- Incident detection
    |   +-- SQLite storage
    |
    +-- Web dashboard (localhost:18080)
    +-- Admin API (localhost:18081)
    |
    +-- Notification dispatcher (60 seconds)
    |   +-- SQLite incident queue
    |   +-- ntfy mobile push notifications
    |
    +-- Backup automation
    |   +-- Daily SQLite snapshots
    |   +-- Off-device Mac export
    |
    +-- External heartbeat (optional)
        +-- Read-only monitoring-worker freshness verification
        +-- Outbound HTTPS to Healthchecks.io
        +-- Independent outage notifications

Web interfaces are restricted to the Raspberry Pi's loopback interface and accessed remotely through SSH tunnels.

No public inbound ports, static ISP address or dynamic DNS configuration are required.

## Technology stack

| Technology | Purpose |
|---|---|
| Python 3.11+ | Monitoring and automation |
| Docker Compose | Container orchestration |
| FastAPI | Dashboard and administration APIs |
| SQLite | Monitoring history and incident persistence |
| HTTPX / dnspython | HTTP, TCP and DNS operations |
| ntfy | Mobile incident notifications |
| Healthchecks.io | External heartbeat monitoring |
| pytest / Ruff | Automated tests and code quality |
| GitHub Actions | CI and Docker image validation |
| systemd / launchd | Scheduled backup operations |

## Verified deployment

The Raspberry Pi 5 currently monitors six enabled home-network services.

Three demonstration monitors remain disabled without losing their history.

The monitoring worker, dashboard, administration interface and notification dispatcher are deployed.

Controlled DOWN and RECOVERED incidents were successfully delivered to a phone through ntfy. Both events were delivered on their first attempt.

External heartbeat deployment and outage/recovery acceptance testing are the final pending milestone.

## Getting started

Clone the repository and install development dependencies:

    git clone https://github.com/RatushAnton/service-health-monitor.git
    cd service-health-monitor
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -e '.[dev]'

Create a local configuration:

    cp config/targets.example.yaml config/targets.yaml

Run checks and inspect stored results:

    healthmon check --config config/targets.yaml --db data/health.db
    healthmon summary --db data/health.db

## Testing and CI

    ruff check src/healthmon tests scripts/install-mac-backup-agent.py
    ruff format --check src/healthmon tests scripts/install-mac-backup-agent.py
    python -m pytest -q

GitHub Actions validates Python 3.11, 3.12 and 3.13, and performs Docker image smoke tests.

## Operations documentation

- [Raspberry Pi deployment](docs/pi-deployment.md)
- [Backup and recovery](docs/backup-recovery.md)
- [ntfy notifications](docs/ntfy-notifications.md)
- [External heartbeat](docs/external-heartbeat.md)

## Engineering considerations

- Incident events are persisted transactionally with their check results.
- Notification delivery is at-least-once; duplicates are possible after certain failures.
- The external heartbeat verifies recent monitoring activity, not the success of every probe.
- A complete Raspberry Pi or home internet outage prevents heartbeats, allowing an external service to detect the absence.
- The heartbeat does not independently verify the ntfy dispatcher.
- Docker memory limits may not be enforceable on every host kernel.
- Full disaster-recovery cutover testing remains outstanding.

## Portfolio objectives

Demonstrate practical infrastructure engineering with Linux, Python, networking, Docker, observability, alerting, persistent storage, secrets management, backup automation, Git workflows, continuous integration and production validation.

The system was developed incrementally with feature branches, pull requests, automated tests and deployment checkpoints.
