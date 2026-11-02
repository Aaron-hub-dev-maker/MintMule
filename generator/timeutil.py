"""Stage 1: time helpers.

Timestamps are held internally as int64 seconds since 1970-01-01T00:00:00 UTC and
written as ISO-8601 strings (``YYYY-MM-DDTHH:MM:SS`` or ``YYYY-MM-DD``).
"""

from __future__ import annotations

import numpy as np

MINUTE = 60
HOUR = 3600
DAY = 86_400


def to_seconds(text: str) -> int:
    """Parse an ISO date or datetime string into epoch seconds."""
    return int(np.datetime64(text, "s").astype(np.int64))


def format_ts(seconds) -> str | np.ndarray:
    """Format epoch seconds (scalar or array) as ``YYYY-MM-DDTHH:MM:SS``."""
    values = np.asarray(seconds, dtype=np.int64).astype("datetime64[s]")
    out = np.datetime_as_string(values, unit="s")
    return str(out) if out.ndim == 0 else out


def format_date(seconds) -> str | np.ndarray:
    """Format epoch seconds (scalar or array) as ``YYYY-MM-DD``."""
    values = np.asarray(seconds, dtype=np.int64).astype("datetime64[s]")
    out = np.datetime_as_string(values, unit="D")
    return str(out) if out.ndim == 0 else out


def day_start(seconds: int) -> int:
    """Return midnight (UTC) of the day containing ``seconds``."""
    return int(seconds) - int(seconds) % DAY


def next_hour(seconds) -> int | np.ndarray:
    """Return the first full hour strictly after ``seconds`` (hourly batch boundary)."""
    values = np.asarray(seconds, dtype=np.int64)
    out = (values // HOUR + 1) * HOUR
    return int(out) if out.ndim == 0 else out
