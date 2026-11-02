"""Strict request models for monitor administration."""

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class MonitorInput(BaseModel):
    """Configuration for a DNS, TCP or HTTP target."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    name: str = Field(min_length=1, max_length=100)
    type: Literal["dns", "tcp", "http"]

    host: str | None = Field(
        default=None,
        min_length=1,
        max_length=253,
    )
    port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
    )
    url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )

    record_type: Literal["A", "AAAA"] | None = None
    resolver: str | None = None
    expected_status: int | None = Field(
        default=None,
        ge=100,
        le=599,
    )

    @model_validator(mode="after")
    def validate_target(self):
        if self.type == "dns":
            if self.host is None:
                raise ValueError("DNS monitors require host")

            if any(
                value is not None
                for value in (
                    self.port,
                    self.url,
                    self.expected_status,
                )
            ):
                raise ValueError("Invalid fields for DNS monitor")

            if self.resolver not in (None, "system"):
                try:
                    ipaddress.ip_address(self.resolver)
                except ValueError as exc:
                    raise ValueError("DNS resolver must be an IP address") from exc

        elif self.type == "tcp":
            if self.host is None or self.port is None:
                raise ValueError("TCP monitors require host and port")

            if any(
                value is not None
                for value in (
                    self.url,
                    self.record_type,
                    self.resolver,
                    self.expected_status,
                )
            ):
                raise ValueError("Invalid fields for TCP monitor")

        else:
            if self.url is None:
                raise ValueError("HTTP monitors require url")

            if any(
                value is not None
                for value in (
                    self.host,
                    self.port,
                    self.record_type,
                    self.resolver,
                )
            ):
                raise ValueError("Invalid fields for HTTP monitor")

            try:
                parsed = urlsplit(self.url)
                valid = (
                    parsed.scheme in ("http", "https")
                    and bool(parsed.hostname)
                    and parsed.username is None
                    and parsed.password is None
                )
            except ValueError:
                valid = False

            if not valid:
                raise ValueError("Invalid HTTP URL")

        return self


class EnabledInput(BaseModel):
    """Request to enable or disable one monitor."""

    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
