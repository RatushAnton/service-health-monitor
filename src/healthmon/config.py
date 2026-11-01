import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

import yaml


class ConfigError(ValueError):
    """Raised when a configuration file is invalid."""


def require_text(target: dict, field: str, label: str) -> str:
    """Ensure a required field contains nonempty text."""
    value = target.get(field)

    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label}: '{field}' must be a nonempty string")

    return value


def load_config(path: str | Path) -> dict:
    """Read and validate a YAML configuration file."""

    config_path = Path(path)

    try:
        with config_path.open("r", encoding="utf-8") as file:
            config = yaml.safe_load(file)
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML syntax: {exc}") from exc

    if not isinstance(config, dict):
        raise ConfigError("Configuration must be a YAML mapping")

    # Validate global settings
    settings = config.get("settings")

    if not isinstance(settings, dict):
        raise ConfigError("'settings' must be a mapping")

    timeout = settings.get("timeout_seconds")

    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= 60
    ):
        raise ConfigError("'timeout_seconds' must be between 0 and 60")

    for key in ("failure_threshold", "recovery_threshold"):
        value = settings.get(key)

        if type(value) is not int or value < 1:
            raise ConfigError(f"'{key}' must be a positive integer")

    # Validate target list
    targets = config.get("targets")

    if not isinstance(targets, list) or not targets:
        raise ConfigError("'targets' must be a nonempty list")

    seen_ids = set()

    for index, target in enumerate(targets, start=1):
        if not isinstance(target, dict):
            raise ConfigError(f"Target #{index} must be a mapping")

        label = f"Target #{index}"
        target_id = require_text(target, "id", label)
        require_text(target, "name", target_id)

        if target_id in seen_ids:
            raise ConfigError(f"Duplicate target ID: {target_id}")

        seen_ids.add(target_id)

        kind = require_text(target, "type", target_id)

        if kind == "dns":
            require_text(target, "host", target_id)

            record_type = target.get("record_type", "A")
            if record_type not in ("A", "AAAA"):
                raise ConfigError(f"{target_id}: unsupported DNS record type")

            nameserver = target.get("resolver", "system")
            if nameserver != "system":
                try:
                    ipaddress.ip_address(nameserver)
                except (ValueError, TypeError):
                    raise ConfigError(f"{target_id}: resolver must be an IP") from None

        elif kind == "tcp":
            require_text(target, "host", target_id)

            port = target.get("port")
            if type(port) is not int or not 1 <= port <= 65535:
                raise ConfigError(f"{target_id}: invalid TCP port")

        elif kind == "http":
            url = require_text(target, "url", target_id)

            try:
                parsed = urlsplit(url)
                valid_url = parsed.scheme in ("http", "https") and bool(parsed.hostname)
            except ValueError:
                valid_url = False

            if not valid_url:
                raise ConfigError(f"{target_id}: invalid HTTP URL")

            status = target.get("expected_status", 200)
            if type(status) is not int or not 100 <= status <= 599:
                raise ConfigError(f"{target_id}: invalid HTTP status")

        else:
            raise ConfigError(f"{target_id}: unsupported check type '{kind}'")

    return config
