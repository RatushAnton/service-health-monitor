"""Authenticated administration API for monitor management."""

import hmac
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException

from healthmon.registry import list_registry_monitors

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
