"""Read-only HTTP API for Service Health Monitor."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from healthmon.storage import get_history, get_latest_results

app = FastAPI(
    title="Service Health Monitor API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


def database_path() -> Path:
    """Return the configured SQLite database, or HTTP 503."""
    path = Path(
        os.environ.get(
            "HEALTHMON_DB_PATH",
            "/var/lib/service-health-monitor/health.db",
        )
    )

    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail="Monitoring database is not available",
        )

    return path


def monitor_dict(row: sqlite3.Row) -> dict:
    """Convert a saved monitor result into an API response."""
    return {
        "id": row["target_id"],
        "type": row["check_type"],
        "status": "UP" if row["success"] else "DOWN",
        "latency_ms": row["latency_ms"],
        "checked_at": row["checked_at"],
        "error_type": row["error_type"],
        "message": row["message"],
    }


def latest_monitors() -> list[dict]:
    """Return the latest results, handling database errors."""
    path = database_path()

    try:
        rows = get_latest_results(path)
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Monitoring database cannot be read",
        ) from exc

    return [monitor_dict(row) for row in rows]


@app.get("/api/health")
def health() -> dict:
    """Report API process liveness, independently of SQLite."""
    return {"status": "ok"}


@app.get("/api/monitors")
def monitors() -> dict:
    """List targets that have recorded check results."""
    return {"monitors": latest_monitors()}


@app.get("/api/monitors/{target_id}")
def monitor_details(target_id: str) -> dict:
    """Return the latest result for a single target."""
    for monitor in latest_monitors():
        if monitor["id"] == target_id:
            return monitor

    raise HTTPException(
        status_code=404,
        detail="Monitor not found",
    )


@app.get("/api/monitors/{target_id}/history")
def monitor_history(
    target_id: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Return recent check results for a target."""
    path = database_path()

    try:
        rows = get_history(path, target_id, limit)
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Monitoring database cannot be read",
        ) from exc

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No history found for monitor",
        )

    return {
        "target_id": target_id,
        "history": [
            {
                "checked_at": row["checked_at"],
                "status": "UP" if row["success"] else "DOWN",
                "latency_ms": row["latency_ms"],
                "error_type": row["error_type"],
                "message": row["message"],
            }
            for row in rows
        ],
    }


@app.get("/api/incidents")
def incidents(
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Return persisted DOWN and RECOVERED events."""
    path = database_path()

    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, target_id, event_type, occurred_at,
                        incident_started_at, error_type, message,
                        delivered_at
                FROM incident_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Monitoring database cannot be read",
        ) from exc

    return {
        "incidents": [dict(row) for row in rows],
    }
