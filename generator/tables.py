"""Helpers for building schema-shaped tables and rendering them as text.

Internal representation used by every stage:

* timestamps and dates: int64 seconds since 1970-01-01T00:00:00 UTC
* money: int64 cents
* other decimals: Python floats already rounded to their documented precision
* nullable numbers: pandas ``Int64``; missing text: ``None``
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import schema
from .timeutil import format_date, format_ts

MONEY_COLUMNS = {
    "expected_monthly_volume", "expected_max_txn_amount", "overdraft_limit", "opening_balance",
    "amount", "balance_before", "balance_after", "trigger_amount",
}
DATE_COLUMNS = {
    "onboarding_date", "verified_date", "next_review_date", "open_date", "close_date",
    "onboarded_date", "effective_from", "effective_to",
}
TIMESTAMP_COLUMNS = {
    "first_registered_at", "first_seen", "last_seen", "event_timestamp", "txn_timestamp",
    "initiated_at", "created_at", "opened_at", "added_at", "action_timestamp",
}
DECIMAL_PLACES = {"metric_value": 4, "threshold_value": 4, "midpoint_value": 4,
                  "weight": 2, "score_ceiling": 2, "score": 2, "max_score": 2}


def make_frame(table: str, data: dict) -> pd.DataFrame:
    """Build a DataFrame with exactly the schema columns of ``table``, in schema order."""
    expected = schema.columns(table)
    if set(data) != set(expected):
        missing, extra = set(expected) - set(data), set(data) - set(expected)
        raise ValueError(f"{table}: missing columns {sorted(missing)}, unexpected {sorted(extra)}")
    return pd.DataFrame({col: data[col] for col in expected}).reset_index(drop=True)


def cents_to_text(cents: int) -> str:
    """Render integer cents as a fixed 2-decimal EUR amount (exact, no float rounding)."""
    sign = "-" if cents < 0 else ""
    value = abs(int(cents))
    return f"{sign}{value // 100}.{value % 100:02d}"


def to_text_frame(table: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Render one internal table as strings exactly as written to CSV ('' = NULL)."""
    out = {}
    for col in schema.columns(table):
        series = frame[col]
        mask = series.isna().to_numpy()
        if col in MONEY_COLUMNS:
            values = [cents_to_text(v) if not m else "" for v, m in zip(series.tolist(), mask)]
        elif col in DATE_COLUMNS or col in TIMESTAMP_COLUMNS:
            formatter = format_date if col in DATE_COLUMNS else format_ts
            values = np.full(len(series), "", dtype=object)
            if (~mask).any():
                values[~mask] = formatter(series[~mask].astype(np.int64).to_numpy())
            values = values.tolist()
        elif col in DECIMAL_PLACES:
            places = DECIMAL_PLACES[col]
            values = [f"{v:.{places}f}" if not m else "" for v, m in zip(series.tolist(), mask)]
        else:
            values = ["" if m else str(v) for v, m in zip(series.tolist(), mask)]
        out[col] = values
    return pd.DataFrame(out, columns=schema.columns(table), dtype=object)


def to_text_tables(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Render every table, in schema load order."""
    return {name: to_text_frame(name, tables[name]) for name in schema.table_names()}
