"""Authenticated administration API for monitor management."""

import hmac
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.staticfiles import StaticFiles

from healthmon.admin_web import router as admin_web_router
from healthmon.monitor_models import EnabledInput, MonitorInput
from healthmon.registry import (
    MonitorConflict,
    MonitorNotFound,
    RegistryNotInitialized,
    archive_monitor,
    create_monitor,
    list_registry_monitors,
    set_monitor_enabled,
    update_monitor,
)

app = FastAPI(
    title="Service Health Monitor Administration",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def require_admin(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Authenticate an administration request using a bearer token."""

    secret_path = Path(
        os.environ.get(
            "HEALTHMON_ADMIN_TOKEN_FILE",
            "/run/secrets/healthmon_admin_token",
        )
    )

    try:
        expected = secret_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail="Administration authentication unavailable",
        ) from exc

    if len(expected) < 40:
        raise HTTPException(
            status_code=503,
            detail="Administration authentication unavailable",
        )

    scheme, separator, provided = (authorization or "").partition(" ")

    if (
        scheme.lower() != "bearer"
        or not separator
        or not hmac.compare_digest(provided, expected)
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid administration credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def admin_database_path() -> Path:
    """Require an existing monitoring database."""

    path = Path(
        os.environ.get(
            "HEALTHMON_DB_PATH",
            "/var/lib/service-health-monitor/health.db",
        )
    )

    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail="Monitoring database unavailable",
        )

    return path


def registry_operation(operation, *args):
    """Convert registry errors to API responses."""
    try:
        return operation(admin_database_path(), *args)

    except MonitorNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Monitor not found",
        ) from exc

    except MonitorConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc

    except RegistryNotInitialized as exc:
        raise HTTPException(
            status_code=503,
            detail="Monitor registry is not initialized",
        ) from exc

    except (sqlite3.Error, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Monitor registry cannot be updated",
        ) from exc


@app.get(
    "/api/admin/monitors",
    dependencies=[Depends(require_admin)],
)
def get_configured_monitors() -> dict:
    """List configured monitors, including disabled targets."""

    path = admin_database_path()

    try:
        with closing(sqlite3.connect(path)) as connection:
            initialized = connection.execute(
                """
                SELECT 1
                FROM registry_settings
                WHERE singleton = 1
                """
            ).fetchone()

        if initialized is None:
            raise HTTPException(
                status_code=503,
                detail="Monitor registry is not initialized",
            )

        monitors = list_registry_monitors(path)

    except (sqlite3.Error, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Monitor registry cannot be read",
        ) from exc

    return {"monitors": monitors}


@app.post(
    "/api/admin/monitors",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def add_monitor(payload: MonitorInput) -> dict:
    """Create a monitoring target."""
    target = payload.model_dump(exclude_none=True)
    return registry_operation(create_monitor, target)


@app.put(
    "/api/admin/monitors/{target_id}",
    dependencies=[Depends(require_admin)],
)
def edit_monitor(target_id: str, payload: MonitorInput) -> dict:
    """Replace an existing target's configuration."""
    target = payload.model_dump(exclude_none=True)
    return registry_operation(update_monitor, target_id, target)


@app.patch(
    "/api/admin/monitors/{target_id}/enabled",
    dependencies=[Depends(require_admin)],
)
def change_monitor_enabled(
    target_id: str,
    payload: EnabledInput,
) -> dict:
    """Pause or resume a monitor."""
    return registry_operation(
        set_monitor_enabled,
        target_id,
        payload.enabled,
    )


@app.delete(
    "/api/admin/monitors/{target_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def remove_monitor(target_id: str) -> None:
    """Archive the target while retaining its history."""
    registry_operation(archive_monitor, target_id)


# Serve the browser administration UI without modifying bearer-only APIs.
app.mount(
    "/admin/assets",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="admin-assets",
)
app.include_router(admin_web_router)
