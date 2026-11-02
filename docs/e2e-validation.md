# Docker End-to-End Validation

| Detail | Value |
|---|---|
| Project | Service Health Monitor |
| Milestone | Phase 7G.4D |
| Validation date | November 2, 2026 |
| Environment | macOS, Docker Desktop, isolated Docker Compose stack |
| Result | PASS — 8 of 8 scenarios manually verified |

## Objective

Verify that the complete Service Health Monitor application works together in Docker before deploying it to a Raspberry Pi.

The validation covers DNS, TCP and HTTP probes, periodic scheduling, incident detection and recovery, the read-only API/dashboard, session-authenticated administration, monitor lifecycle management and SQLite persistence.

## Architecture and isolation

The environment is defined in `compose.e2e.yaml`, using the Compose project name `service-health-monitor-e2e`.

| Service | Responsibility |
|---|---|
| `demo` | Internal HTTP test server on port 8765 |
| `registry-init` | One-time initialization of the SQLite monitor registry |
| `worker` | Periodic database-backed checks every 5 seconds |
| `web` | Read-only dashboard and API |
| `admin` | Session-authenticated monitor administration |

The Python application services reuse one Docker image, `service-health-monitor:e2e`, built by the `worker` service. This prevents concurrent builds from attempting to export the same image tag.

The environment uses its own `e2e_data` Docker volume, separate from the regular application's monitoring data. It exposes only the loopback-bound host ports `18080` (web) and `18081` (admin). The `demo` service has no published host port.

An E2E-only admin credential is stored in `deploy/e2e/admin-token.txt`, which is ignored by Git. Never commit or print this secret in public logs.

## Prerequisites

Docker Desktop with Docker Compose, Git, and OpenSSL are required. Run commands from the repository root.

On first setup, generate the local admin credential:

```bash
mkdir -p deploy/e2e
openssl rand -hex 32 > deploy/e2e/admin-token.txt
chmod 600 deploy/e2e/admin-token.txt
git check-ignore -v deploy/e2e/admin-token.txt
```

Preserve an existing token if you are reusing an initialized environment. Do not overwrite it unnecessarily, because changing the credential invalidates existing browser sessions.

## Build and start

Validate the configuration, build the application image once, and start the stack:

```bash
docker compose -f compose.e2e.yaml config --quiet
docker compose -f compose.e2e.yaml build worker
docker compose -f compose.e2e.yaml up -d --no-build
docker compose -f compose.e2e.yaml ps
```

The `registry-init` container should exit successfully after importing three targets. The `demo`, `worker`, `web`, and `admin` containers should be running.

Inspect logs:

```bash
docker compose -f compose.e2e.yaml logs --tail 40 \
  registry-init worker web admin
```

## Baseline connectivity

Verify API health:

```bash
curl -fsS http://127.0.0.1:18080/api/health
```

Expected response:

```json
{"status":"ok"}
```

Verify monitored targets:

```bash
curl -fsS http://127.0.0.1:18080/api/monitors \
  | python3 -m json.tool
```

Expected initially healthy targets are `e2e_dns`, `e2e_tcp` and `e2e_http`. Their statuses should be `UP` while the demo service is functioning.

Verify admin login availability:

```bash
curl -sS -o /dev/null \
  -w "HTTP %{http_code}\n" \
  http://127.0.0.1:18081/admin/login
```

Expected response code: `200`.

Web dashboard: `http://127.0.0.1:18080/`
Administration: `http://127.0.0.1:18081/admin/login`

## Outage and recovery demonstration

The E2E configuration uses two consecutive failed checks to open an incident and one successful check for recovery.

Stop the test HTTP service:

```bash
docker compose -f compose.e2e.yaml stop demo
sleep 20
curl -fsS http://127.0.0.1:18080/api/incidents \
  | python3 -m json.tool
```

Check for `DOWN` events associated with the HTTP and TCP monitors.

Restart the service and inspect recovery events:

```bash
docker compose -f compose.e2e.yaml start demo
sleep 10
curl -fsS http://127.0.0.1:18080/api/incidents \
  | python3 -m json.tool
```

Verify the corresponding `RECOVERED` events. The DNS monitor may continue to resolve the Docker service name even when its container is stopped.

Review the saved HTTP history:

```bash
curl -fsS \
  "http://127.0.0.1:18080/api/monitors/e2e_http/history?limit=20" \
  | python3 -m json.tool
```

## Administration lifecycle

Sign in to the isolated admin interface using the E2E token.

Create an additional HTTP monitor with the following configuration:

| Field | Value |
|---|---|
| ID | `e2e_extra` |
| Name | E2E Additional Service |
| Type | HTTP |
| URL | `http://demo:8765/` |
| Expected status | `200` |

Verify that the worker discovers the new monitor on its next monitoring cycle.

Pause it and verify that its latest `checked_at` timestamp stops advancing. Resume it and verify that checks resume. Edit its configuration and confirm that the updated configuration persists.

Archive `e2e_extra` through the confirmation page. Verify that it disappears from the active monitor listing and cannot be resumed or recreated using the same ID.

Confirm that previously recorded history remains accessible:

```bash
curl -fsS \
  "http://127.0.0.1:18080/api/monitors/e2e_extra/history?limit=20" \
  | python3 -m json.tool
```

A paused monitor may still display its last known result in the read-only API. The saved timestamp, rather than the visible UP/DOWN label alone, is necessary to confirm that scheduling has stopped.

## Persistence across container restarts

Restart the application containers without deleting their volume:

```bash
docker compose -f compose.e2e.yaml restart worker web admin
docker compose -f compose.e2e.yaml ps
```

Revisit the dashboard and admin interface. Previously saved monitoring results, configuration changes, archive state and history must remain available.

The registry initializer is designed to be idempotent: running it again against an already initialized registry must not overwrite changes made through the administration interface.

## Validation results

The following scenarios were manually confirmed on November 2, 2026:

| # | Scenario | Result |
|---|---|---|
| 1 | Isolated Docker services start successfully | PASS |
| 2 | Initial DNS, TCP and HTTP checks report UP | PASS |
| 3 | Dashboard and admin HTTP endpoints respond | PASS |
| 4 | Test service outage generates DOWN incidents | PASS |
| 5 | Recovery generates RECOVERED incidents | PASS |
| 6 | Create, Pause, Resume and Edit controls function | PASS |
| 7 | Archive preserves historical monitoring results | PASS |
| 8 | Configuration and history persist after container restarts | PASS |

These are manually verified end-to-end scenarios. They complement the Python unit/integration tests and the existing Docker image smoke test in GitHub Actions. The full Docker lifecycle is not yet an automatically executed CI job.

## Cleanup

Stop and remove E2E containers while preserving their SQLite volume:

```bash
docker compose -f compose.e2e.yaml down
```

The test data can be reused with a future `up` command.

Only when the E2E database is no longer needed, remove its dedicated volume:

```bash
docker compose -f compose.e2e.yaml down -v
```

**Warning:** The second command permanently deletes the isolated E2E database. Never run it against the standard `compose.yaml` stack or other persistent monitoring volumes.

## Outcome

Phase 7G.4D validates the integrated Docker monitoring lifecycle on macOS. The next deployment milestone is Phase 7G.5: Raspberry Pi 5 deployment, including persistent storage, automatic startup, operational monitoring, backups and restart behavior.
