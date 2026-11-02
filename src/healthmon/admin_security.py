"""Shared administration secret configuration."""

import os
from pathlib import Path


class AdminSecretUnavailable(RuntimeError):
    """The administration token file is missing or invalid."""


def read_admin_secret() -> str:
    """Return the server-side token. Never include it in a web response."""
    path = Path(
        os.environ.get(
            "HEALTHMON_ADMIN_TOKEN_FILE",
            "/run/secrets/healthmon_admin_token",
        )
    )
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AdminSecretUnavailable("Administration secret unavailable") from exc

    if len(secret) < 40:
        raise AdminSecretUnavailable("Administration secret unavailable")
    return secret
