"""Return the current UTC timestamp as an ISO 8601 string."""

from datetime import datetime, timezone


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string (e.g. 2026-08-13T12:34:56.789012+00:00)."""
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    print(utc_now())