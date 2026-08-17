"""Tests for utc_now()."""

import sys
import os
import re
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from utc_now import utc_now


def test_returns_iso_format():
    """Output contains the ISO 8601 date-time separator T."""
    ts = utc_now()
    assert "T" in ts, f"missing 'T' separator: {ts}"


def test_ends_with_utc_offset():
    """Output ends with the +00:00 UTC offset."""
    ts = utc_now()
    assert ts.endswith("+00:00"), f"not UTC offset: {ts}"


def test_parseable_by_datetime():
    """Output is a valid ISO 8601 string that datetime.fromisoformat can parse."""
    ts = utc_now()
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None, f"no tzinfo after parse: {ts}"


def test_matches_iso_regex():
    """Output matches a strict ISO 8601 datetime pattern."""
    ts = utc_now()
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+.\d{2}:\d{2}$"
    assert re.match(pattern, ts), f"not ISO 8601: {ts}"


def test_close_to_current_time():
    """Output is within 5 seconds of the actual current UTC time."""
    ts = utc_now()
    parsed = datetime.fromisoformat(ts)
    delta = abs((parsed - datetime.now(timezone.utc)).total_seconds())
    assert delta < 5, f"timestamp off by {delta:.1f}s"