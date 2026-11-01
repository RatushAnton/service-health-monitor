import socket
import time
from datetime import UTC, datetime

import dns.exception
import dns.resolver
import httpx


def check_dns(target: dict, timeout_seconds: float) -> dict:
    """Check DNS resolution and return a structured result."""

    checked_at = datetime.now(UTC).isoformat()
    start = time.perf_counter()

    success = False
    records = []
    error_type = None
    message = ""

    try:
        # Create a DNS resolver
        resolver = dns.resolver.Resolver(configure=True)

        # Optionally override the configured nameserver
        nameserver = target.get("resolver", "system")
        if nameserver != "system":
            resolver.nameservers = [nameserver]

        # Limit individual attempts and the total query time
        resolver.timeout = timeout_seconds
        resolver.lifetime = timeout_seconds

        # Resolve the requested DNS record
        answers = resolver.resolve(
            target["host"],
            target.get("record_type", "A"),
            search=False,
        )

        records = [answer.to_text() for answer in answers]

        success = True
        message = f"Resolved {len(records)} record(s)"

    except dns.resolver.NXDOMAIN:
        error_type = "NXDOMAIN"
        message = "Domain does not exist"

    except dns.resolver.NoAnswer:
        error_type = "NO_ANSWER"
        message = "No record of the requested type"

    except dns.resolver.NoNameservers:
        error_type = "NO_NAMESERVERS"
        message = "No usable DNS nameserver"

    except dns.exception.Timeout:
        error_type = "DNS_TIMEOUT"
        message = "DNS query timed out"

    except dns.exception.DNSException as exc:
        error_type = "DNS_ERROR"
        message = str(exc)

    except OSError as exc:
        error_type = "OS_ERROR"
        message = str(exc)

    latency_ms = (time.perf_counter() - start) * 1000

    return {
        "target_id": target["id"],
        "check_type": "dns",
        "checked_at": checked_at,
        "success": success,
        "latency_ms": round(latency_ms, 2),
        "records": records,
        "error_type": error_type,
        "message": message,
    }


def check_tcp(target: dict, timeout_seconds: float) -> dict:
    """Check TCP connectivity to a host and port."""

    host = target["host"]
    port = target["port"]

    checked_at = datetime.now(UTC).isoformat()
    start = time.perf_counter()

    success = False
    error_type = None
    message = ""

    try:
        # Attempt to establish a TCP connection
        with socket.create_connection(
            (host, port),
            timeout=timeout_seconds,
        ):
            success = True
            message = f"Connected to {host}:{port}"

    except socket.gaierror as exc:
        error_type = "DNS_ERROR"
        message = f"Hostname resolution failed: {exc}"

    except ConnectionRefusedError:
        error_type = "TCP_REFUSED"
        message = f"Connection refused by {host}:{port}"

    except TimeoutError:
        error_type = "TCP_TIMEOUT"
        message = f"Connection timed out to {host}:{port}"

    except OSError as exc:
        error_type = "TCP_ERROR"
        message = str(exc)

    latency_ms = (time.perf_counter() - start) * 1000

    return {
        "target_id": target["id"],
        "check_type": "tcp",
        "checked_at": checked_at,
        "success": success,
        "latency_ms": round(latency_ms, 2),
        "error_type": error_type,
        "message": message,
    }


def check_http(target: dict, timeout_seconds: float) -> dict:
    """Check an HTTP endpoint and verify its status code."""

    url = target["url"]
    expected_status = target.get("expected_status", 200)

    checked_at = datetime.now(UTC).isoformat()
    start = time.perf_counter()

    success = False
    status_code = None
    error_type = None
    message = ""

    try:
        response = httpx.get(
            url,
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

        status_code = response.status_code

        if status_code == expected_status:
            success = True
            message = f"HTTP {status_code} OK"
        else:
            error_type = "HTTP_STATUS"
            message = f"Expected HTTP {expected_status}, received HTTP {status_code}"

    except httpx.TimeoutException:
        error_type = "HTTP_TIMEOUT"
        message = "HTTP request timed out"

    except httpx.ConnectError as exc:
        error_type = "HTTP_CONNECT_ERROR"
        message = str(exc)

    except httpx.RequestError as exc:
        error_type = "HTTP_REQUEST_ERROR"
        message = str(exc)

    latency_ms = (time.perf_counter() - start) * 1000

    return {
        "target_id": target["id"],
        "check_type": "http",
        "checked_at": checked_at,
        "success": success,
        "latency_ms": round(latency_ms, 2),
        "status_code": status_code,
        "error_type": error_type,
        "message": message,
    }
