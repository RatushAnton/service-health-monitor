import time
from datetime import UTC, datetime

import dns.exception
import dns.resolver


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
