"""Stage 1: identifier helpers.

All identifiers are zero-padded, immutable once assigned, and identical everywhere
they are referenced. ``ID_PATTERNS`` is the single definition of each format.
"""

from __future__ import annotations

import re

from .config import ID_YEAR

ID_PATTERNS: dict[str, str] = {
    "customer": r"CUS-\d{6}",
    "kyc": r"KYC-\d{6}",
    "account": r"ACC-CUS-\d{6}-\d{2}",
    "merchant": r"MER-\d{6}",
    "device": r"DEV-\d{6}",
    "transaction": rf"TXN-{ID_YEAR}-\d{{8}}",
    "transfer": rf"TRF-{ID_YEAR}-\d{{6}}",
    "rule": r"RULE-\d{3}",
    "alert": rf"ALT-{ID_YEAR}-\d{{6}}",
    "case": rf"CASE-{ID_YEAR}-\d{{6}}",
    "action": rf"ACT-{ID_YEAR}-\d{{6}}",
    "status_event": r"ASH-\d{8}",
}

_COMPILED = {kind: re.compile(rf"^{pattern}$") for kind, pattern in ID_PATTERNS.items()}


def _padded(number: int, width: int) -> str:
    """Zero-pad a positive sequence number, refusing values that would overflow the width."""
    if isinstance(number, bool) or int(number) != number or number < 1:
        raise ValueError(f"sequence numbers start at 1, got {number!r}")
    text = f"{int(number):0{width}d}"
    if len(text) > width:
        raise ValueError(f"sequence number {number} does not fit in {width} digits")
    return text


def customer_id(n: int) -> str:
    """CUS-000001."""
    return f"CUS-{_padded(n, 6)}"


def kyc_id(n: int) -> str:
    """KYC-000001 (same sequence number as the customer it belongs to)."""
    return f"KYC-{_padded(n, 6)}"


def account_id(customer: str, seq: int) -> str:
    """ACC-CUS-000001-01: the owning customer id plus that customer's account sequence."""
    if not is_valid("customer", customer):
        raise ValueError(f"invalid customer id {customer!r}")
    return f"ACC-{customer}-{_padded(seq, 2)}"


def merchant_id(n: int) -> str:
    """MER-000001."""
    return f"MER-{_padded(n, 6)}"


def device_id(n: int) -> str:
    """DEV-000001."""
    return f"DEV-{_padded(n, 6)}"


def transaction_id(n: int) -> str:
    """TXN-2026-00000001."""
    return f"TXN-{ID_YEAR}-{_padded(n, 8)}"


def transfer_id(n: int) -> str:
    """TRF-2026-000001."""
    return f"TRF-{ID_YEAR}-{_padded(n, 6)}"


def rule_id(n: int) -> str:
    """RULE-001."""
    return f"RULE-{_padded(n, 3)}"


def alert_id(n: int) -> str:
    """ALT-2026-000001."""
    return f"ALT-{ID_YEAR}-{_padded(n, 6)}"


def case_id(n: int) -> str:
    """CASE-2026-000001."""
    return f"CASE-{ID_YEAR}-{_padded(n, 6)}"


def action_id(n: int) -> str:
    """ACT-2026-000001."""
    return f"ACT-{ID_YEAR}-{_padded(n, 6)}"


def status_event_id(n: int) -> str:
    """ASH-00000001 (account status history event, numbered in append order)."""
    return f"ASH-{_padded(n, 8)}"


def is_valid(kind: str, value: str) -> bool:
    """Return True when ``value`` matches the documented format for ``kind``."""
    return isinstance(value, str) and bool(_COMPILED[kind].match(value))


def customer_of_account(account: str) -> str:
    """Return the customer id embedded in an account id."""
    if not is_valid("account", account):
        raise ValueError(f"invalid account id {account!r}")
    return account[4:14]
