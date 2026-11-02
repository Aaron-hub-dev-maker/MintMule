"""Stage 4: baseline transactions and transfers, posted to a reconciled ledger.

Generation works in two steps:

1. ``baseline_events`` draws *events* (account, time, type, requested amount, ...)
   for accounts while they are ACTIVE, plus transfer pairs and closure sweeps.
2. ``post_ledger`` posts events (baseline and, later, planted ones) in chronological
   order. ``balance_after = balance_before ± amount`` is derived from the previous row
   of the same account, and no debit may take an account below ``-overdraft_limit``
   (or below 0 without an overdraft). A baseline debit that would do so is reduced to
   a share of the available funds, or turned into a deposit when almost nothing is
   available; planted debits are always funded and are never altered.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import ids
from .master_data import WINDOW_END_S, WINDOW_START_S, active_intervals
from .tables import make_frame
from .timeutil import DAY, HOUR

DEBIT_TYPES = {"PURCHASE", "ATM_WITHDRAWAL", "BILL_PAYMENT", "FEE", "TRANSFER_OUT", "ACCOUNT_CLOSURE"}
TYPE_MIX = {
    "CURRENT": (["PURCHASE", "ATM_WITHDRAWAL", "BILL_PAYMENT", "FEE", "SALARY", "DEPOSIT", "REFUND"],
                [0.50, 0.07, 0.12, 0.03, 0.18, 0.05, 0.05]),
    "SAVINGS": (["DEPOSIT", "INTEREST", "ATM_WITHDRAWAL", "FEE"], [0.55, 0.25, 0.10, 0.10]),
    "BUSINESS_CURRENT": (["DEPOSIT", "PURCHASE", "BILL_PAYMENT", "FEE", "REFUND", "ATM_WITHDRAWAL"],
                         [0.35, 0.30, 0.22, 0.05, 0.04, 0.04]),
}
ACTIVITY = {"CURRENT": 1.0, "SAVINGS": 0.25, "BUSINESS_CURRENT": 3.0}
PURCHASE_CATEGORIES = ["GROCERY", "RESTAURANT", "TRANSPORT", "FUEL", "ONLINE_RETAIL", "GENERAL_RETAIL",
                       "ELECTRONICS", "TRAVEL", "FURNITURE", "HOME_IMPROVEMENT", "AUTOMOTIVE", "HEALTH",
                       "ENTERTAINMENT", "JEWELRY"]
PURCHASE_CATEGORY_P = [0.24, 0.17, 0.08, 0.08, 0.12, 0.10, 0.04, 0.03, 0.02, 0.03, 0.02, 0.04, 0.02, 0.01]
BILL_CATEGORIES = ["UTILITIES", "TELECOM", "INSURANCE"]
ATM_AMOUNTS_EUR = [20, 50, 100, 150, 200, 300, 500]
TRANSFER_SHARE = 0.03            # transfer pairs as a share of the baseline budget (2 legs each)
OWN_TRANSFER_P = 0.35            # share of transfers between accounts of the same customer
MAX_BASELINE_FACTOR = 2.5        # baseline debits never exceed 2.5 x the KYC expected max amount
CLOSING_RESERVE = 100            # cents kept on accounts that will be closed, so the sweep is a debit
MIN_DEBIT = 100                  # cents


def channel_for(txn_type: str, device_type: str | None, rng_value: float) -> str:
    """Channel consistent with the transaction type (and device class for digital types)."""
    if txn_type in ("PURCHASE", "REFUND"):
        return "ECOMMERCE" if rng_value < 0.35 else "CARD_POS"
    if txn_type == "ATM_WITHDRAWAL":
        return "ATM"
    if txn_type in ("FEE", "INTEREST", "SALARY", "TRANSFER_IN"):  # credit legs are booked by the bank
        return "SYSTEM"
    if txn_type == "DEPOSIT":
        return "BRANCH" if rng_value < 0.3 else "SYSTEM"
    if txn_type == "ACCOUNT_CLOSURE" or device_type is None:
        return "BRANCH"
    return "ONLINE_BANKING" if device_type == "DESKTOP_WEB" else "MOBILE_APP"


def make_event(key: int, account: str, ts: int, txn_type: str, amount: int, *, channel: str,
               merchant: str | None = None, device: str | None = None, alt_amount: int = MIN_DEBIT,
               cap_fraction: float = 0.5, pair: int | None = None, planted: bool = False) -> dict:
    """One ledger event.

    ``pair`` links the two legs of a transfer, ``alt_amount`` is the deposit amount used
    if a baseline debit cannot be funded at all, ``cap_fraction`` the share of available
    funds used if it can only be partly funded, and ``planted`` marks planted events.
    """
    return {"key": key, "account_id": account, "ts": int(ts), "txn_type": txn_type,
            "direction": "DEBIT" if txn_type in DEBIT_TYPES else "CREDIT", "amount": int(amount),
            "channel": channel, "merchant_id": merchant, "device_id": device, "alt_amount": int(alt_amount),
            "cap_fraction": cap_fraction, "pair": pair, "planted": planted}


# --- spans -------------------------------------------------------------------------

def subtract_spans(spans: list[tuple[int, int]], blocked: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Remove ``blocked`` intervals from ``spans``."""
    out = spans
    for b_start, b_end in sorted(blocked):
        nxt = []
        for s, e in out:
            if b_end <= s or b_start >= e:
                nxt.append((s, e))
                continue
            if s < b_start:
                nxt.append((s, b_start))
            if b_end < e:
                nxt.append((b_end, e))
        out = nxt
    return out


def closures_in_window(history: pd.DataFrame) -> dict[str, int]:
    """CLOSED event timestamp per account closed inside the observation window."""
    closed = history[(history["to_status"] == "CLOSED") & (history["event_timestamp"] >= WINDOW_START_S)]
    return dict(zip(closed["account_id"], closed["event_timestamp"].astype(int)))


def baseline_spans(tables: dict[str, pd.DataFrame],
                   blocked: dict[str, list[tuple[int, int]]] | None = None) -> dict[str, list[tuple[int, int]]]:
    """Time spans in which baseline events may be placed, per account.

    A span lies inside the observation window and an ACTIVE status interval, starts at
    least one hour after opening, ends six hours before a closure (room for the closure
    sweep) and excludes ``blocked`` windows (reserved for planted patterns).
    """
    blocked = blocked or {}
    closing = closures_in_window(tables["account_status_history"])
    out = {}
    for acc, spans in active_intervals(tables["account_status_history"]).items():
        usable = []
        for start, end in spans:
            start = max(start + HOUR, WINDOW_START_S)
            end = min(end, WINDOW_END_S, closing.get(acc, 2**62) - 6 * HOUR)
            if end - start >= DAY:
                usable.append((start, end))
        usable = [(s, e) for s, e in subtract_spans(usable, blocked.get(acc, [])) if e - s >= HOUR]
        if usable:
            out[acc] = usable
    return out


def sample_times(rng: np.random.Generator, spans: list[tuple[int, int]], k: int) -> np.ndarray:
    """``k`` timestamps drawn uniformly over the union of ``spans``."""
    starts = np.array([s for s, _ in spans], dtype=np.int64)
    lengths = np.array([e - s for s, e in spans], dtype=np.int64)
    idx = rng.choice(len(spans), size=k, p=lengths / lengths.sum())
    return starts[idx] + (rng.random(k) * lengths[idx]).astype(np.int64)


def within(spans: list[tuple[int, int]], ts: int) -> bool:
    """True when ``ts`` lies inside one of ``spans``."""
    return any(s <= ts < e for s, e in spans)


def device_index(tables: dict[str, pd.DataFrame]) -> dict[str, list[tuple[int, str, str]]]:
    """(first_seen, device_id, device_type) per account, sorted by first_seen."""
    links = tables["account_devices"].merge(tables["devices"][["device_id", "device_type"]], on="device_id")
    out: dict[str, list[tuple[int, str, str]]] = {}
    for row in links.sort_values(["account_id", "first_seen", "device_id"]).itertuples(index=False):
        out.setdefault(row.account_id, []).append((int(row.first_seen), row.device_id, row.device_type))
    return out


def device_at(links: list[tuple[int, str, str]], ts: int, u: float) -> tuple[str, str] | None:
    """(device_id, device_type) of a device already linked to the account at ``ts``, else None."""
    usable = [(dev, dtype) for seen, dev, dtype in links if seen <= ts]
    return usable[int(u * len(usable))] if usable else None


# --- baseline events --------------------------------------------------------------

def baseline_events(tables: dict[str, pd.DataFrame], rng: np.random.Generator, budget: int,
                    blocked: dict[str, list[tuple[int, int]]] | None = None, first_key: int = 0) -> list[dict]:
    """Draw exactly ``budget`` baseline events: regular postings, transfer legs and closure sweeps."""
    spans = baseline_spans(tables, blocked)
    closing = closures_in_window(tables["account_status_history"])
    accounts = tables["accounts"].set_index("account_id")
    kyc = tables["kyc_profiles"].set_index("customer_id")
    by_category = {c: g["merchant_id"].tolist() for c, g in tables["merchants"].groupby("category", sort=True)}
    devices = device_index(tables)

    n_transfers = int(budget * TRANSFER_SHARE)
    n_regular = budget - 2 * n_transfers - len(closing)
    if n_regular < 0:
        raise ValueError(f"transaction budget {budget} is too small for {len(closing)} closures")
    eligible = sorted(spans)
    acc_types = accounts.loc[eligible, "account_type"].tolist()
    span_days = np.array([sum(e - s for s, e in spans[a]) / DAY for a in eligible])
    weights = np.array([ACTIVITY[t] for t in acc_types]) * rng.lognormal(0.0, 0.5, len(eligible)) * span_days
    counts = rng.multinomial(n_regular, weights / weights.sum())

    events: list[dict] = []
    key = first_key
    for acc, acc_type, k in zip(eligible, acc_types, counts):
        if k == 0:
            continue
        profile = kyc.loc[accounts.at[acc, "customer_id"]]
        max_amt, monthly = int(profile["expected_max_txn_amount"]), int(profile["expected_monthly_volume"])
        employed = profile["occupation_or_industry"] == "EMPLOYED"
        names, probs = TYPE_MIX[acc_type]
        types = [names[i] for i in rng.choice(len(names), size=k, p=probs)]
        categories = rng.choice(len(PURCHASE_CATEGORIES), size=k, p=PURCHASE_CATEGORY_P)
        times = sample_times(rng, spans[acc], k)
        u = rng.random((k, 5))
        logn = rng.lognormal(0.0, 1.0, k)
        for i in range(k):
            txn_type = "DEPOSIT" if types[i] == "SALARY" and not employed else types[i]
            amount, category = _regular_amount(txn_type, max_amt, monthly, logn[i], u[i, 0],
                                               PURCHASE_CATEGORIES[categories[i]])
            merchant = by_category[category][int(u[i, 4] * len(by_category[category]))] if category else None
            device = device_at(devices.get(acc, []), int(times[i]), u[i, 1]) if txn_type == "BILL_PAYMENT" else None
            events.append(make_event(
                key, acc, times[i], txn_type, amount, merchant=merchant,
                device=device[0] if device else None,
                channel=channel_for(txn_type, device[1] if device else None, u[i, 2]),
                alt_amount=max(MIN_DEBIT, int(max_amt * (0.1 + 0.3 * u[i, 3]))),
                cap_fraction=0.3 + 0.6 * u[i, 3]))
            key += 1

    key = _transfer_events(events, key, n_transfers, eligible, weights, spans, accounts, kyc, devices, rng)
    for acc, close_ts in sorted(closing.items()):
        events.append(make_event(key, acc, close_ts - int(rng.integers(HOUR, 5 * HOUR)), "ACCOUNT_CLOSURE", 0,
                                 channel="BRANCH"))
        key += 1
    if len(events) != budget:
        raise AssertionError(f"generated {len(events)} baseline events, expected {budget}")
    return events


def _regular_amount(txn_type: str, max_amt: int, monthly: int, logn: float, u: float,
                    purchase_category: str) -> tuple[int, str | None]:
    """Requested amount (cents) and merchant category (None if no merchant) for a regular event."""
    cap = int(max_amt * MAX_BASELINE_FACTOR)
    if txn_type == "PURCHASE":
        return min(cap, max(MIN_DEBIT, int(max_amt * 0.05 * logn ** 0.9))), purchase_category
    if txn_type == "REFUND":
        return min(cap, max(MIN_DEBIT, int(max_amt * 0.04 * logn ** 0.7))), purchase_category
    if txn_type == "BILL_PAYMENT":
        return min(cap, max(MIN_DEBIT, int(max_amt * 0.12 * logn ** 0.5))), BILL_CATEGORIES[int(u * 3)]
    if txn_type == "ATM_WITHDRAWAL":
        return min(cap, ATM_AMOUNTS_EUR[int(u * len(ATM_AMOUNTS_EUR))] * 100), None
    if txn_type == "FEE":
        return 50 + int(u * 1450), None
    if txn_type == "INTEREST":
        return 1 + int(u * 2500), None
    if txn_type == "SALARY":
        return int(monthly * (0.5 + 0.4 * u)), None
    return max(MIN_DEBIT, int(max_amt * 0.4 * logn ** 0.6)), None  # DEPOSIT


def _transfer_events(events: list[dict], key: int, n_transfers: int, eligible: list[str], weights: np.ndarray,
                     spans: dict, accounts: pd.DataFrame, kyc: pd.DataFrame, devices: dict,
                     rng: np.random.Generator) -> int:
    """Append ``n_transfers`` debit/credit leg pairs between accounts that are both ACTIVE."""
    owner = accounts["customer_id"]
    own_accounts: dict[str, list[str]] = {}
    for acc in eligible:
        own_accounts.setdefault(owner[acc], []).append(acc)
    for src_idx in rng.choice(len(eligible), size=n_transfers, p=weights / weights.sum()):
        src = eligible[int(src_idx)]
        ts = int(sample_times(rng, spans[src], 1)[0])
        own = [a for a in own_accounts[owner[src]] if a != src]
        dst = own[int(rng.integers(len(own)))] if own and rng.random() < OWN_TRANSFER_P else None
        attempts = 0
        while dst is None or not (within(spans[dst], ts) and within(spans[dst], ts + 120)):
            attempts += 1
            if attempts > 50:
                raise RuntimeError(f"no transfer counterparty active at {ts} for {src}")
            candidate = eligible[int(rng.integers(len(eligible)))]
            dst = candidate if candidate != src else None
        max_amt = int(kyc.at[owner[src], "expected_max_txn_amount"])
        amount = min(int(max_amt * MAX_BASELINE_FACTOR), max(1_000, int(max_amt * 0.3 * rng.lognormal(0, 0.6))))
        device = device_at(devices.get(src, []), ts, rng.random())
        channel = channel_for("TRANSFER_OUT", device[1] if device else None, 0.0)
        events.append(make_event(key, src, ts, "TRANSFER_OUT", amount, channel=channel,
                                 device=device[0] if device else None, pair=key))
        events.append(make_event(key + 1, dst, ts + int(rng.integers(1, 91)), "TRANSFER_IN", amount,
                                 channel=channel_for("TRANSFER_IN", None, 0.0),
                                 alt_amount=max(MIN_DEBIT, amount // 3), pair=key))
        key += 2
    return key


# --- ledger posting ---------------------------------------------------------------

def post_ledger(tables: dict[str, pd.DataFrame], events: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Post ``events`` chronologically; return (transactions, transfer_links, event key -> txn id).

    Transaction ids follow posting order. Timestamps are made strictly increasing per
    account (a clash is moved forward by one second) and a credit leg never precedes
    its debit leg. A transfer whose debit leg cannot be funded becomes two deposits.
    """
    accounts = tables["accounts"]
    balance = dict(zip(accounts["account_id"], accounts["opening_balance"].astype(int)))
    overdraft = {a: (0 if pd.isna(o) else int(o)) for a, o in zip(accounts["account_id"], accounts["overdraft_limit"])}
    owner = dict(zip(accounts["account_id"], accounts["customer_id"]))
    closing = closures_in_window(tables["account_status_history"])
    last_ts: dict[str, int] = {}
    transfers: dict[int, dict | None] = {}   # pair -> posted debit leg, or None when cancelled
    rows, links, key_to_txn = [], [], {}

    for original in sorted(events, key=lambda e: (e["ts"], e["key"])):
        ev = dict(original)
        acc = ev["account_id"]
        ts = max(ev["ts"], last_ts.get(acc, -1) + 1)
        if ev["txn_type"] == "TRANSFER_IN":
            debit_leg = transfers[ev["pair"]]
            if debit_leg is None:
                _to_deposit(ev)
            else:
                ts, ev["amount"] = max(ts, debit_leg["ts"]), debit_leg["amount"]
        if ev["txn_type"] == "ACCOUNT_CLOSURE":
            ev["amount"] = balance[acc]
            if ev["amount"] <= 0:
                raise RuntimeError(f"closure sweep of {acc} found balance {balance[acc]}")
        elif ev["direction"] == "DEBIT":
            if acc in closing:
                available = balance[acc] - CLOSING_RESERVE
            else:
                available = balance[acc] + overdraft[acc]
            if ev["amount"] > available:
                if ev["planted"]:
                    raise RuntimeError(f"planted debit {ev['key']} on {acc} is not funded")
                if available >= 5 * MIN_DEBIT:
                    ev["amount"] = max(MIN_DEBIT, int(available * ev["cap_fraction"]))
                else:
                    _to_deposit(ev)

        before = balance[acc]
        after = before + (ev["amount"] if ev["direction"] == "CREDIT" else -ev["amount"])
        balance[acc], last_ts[acc] = after, ts
        txn = ids.transaction_id(len(rows) + 1)
        key_to_txn[ev["key"]] = txn
        rows.append((txn, acc, ts, ev["direction"], ev["txn_type"], ev["channel"], ev["amount"],
                     before, after, ev["merchant_id"], ev["device_id"]))
        if original["txn_type"] == "TRANSFER_OUT":
            transfers[ev["pair"]] = ({"ts": ts, "amount": ev["amount"], "txn": txn, "account": acc}
                                     if ev["txn_type"] == "TRANSFER_OUT" else None)
        elif ev["txn_type"] == "TRANSFER_IN":
            debit_leg = transfers[ev["pair"]]
            scope = "OWN_ACCOUNTS" if owner[debit_leg["account"]] == owner[acc] else "THIRD_PARTY"
            links.append((debit_leg["txn"], txn, debit_leg["account"], acc, ev["amount"], debit_leg["ts"], scope))

    transactions = make_frame("transactions", {
        "transaction_id": [r[0] for r in rows], "account_id": [r[1] for r in rows],
        "txn_timestamp": np.array([r[2] for r in rows], dtype=np.int64),
        "direction": [r[3] for r in rows], "txn_type": [r[4] for r in rows], "channel": [r[5] for r in rows],
        "amount": np.array([r[6] for r in rows], dtype=np.int64), "currency": ["EUR"] * len(rows),
        "balance_before": np.array([r[7] for r in rows], dtype=np.int64),
        "balance_after": np.array([r[8] for r in rows], dtype=np.int64),
        "merchant_id": [r[9] for r in rows], "device_id": [r[10] for r in rows],
    })
    links.sort(key=lambda r: r[0])
    transfer_links = make_frame("transfer_links", {
        "transfer_id": [ids.transfer_id(i + 1) for i in range(len(links))],
        "source_txn_id": [r[0] for r in links], "dest_txn_id": [r[1] for r in links],
        "source_account_id": [r[2] for r in links], "dest_account_id": [r[3] for r in links],
        "amount": np.array([r[4] for r in links], dtype=np.int64),
        "initiated_at": np.array([r[5] for r in links], dtype=np.int64),
        "transfer_scope": [r[6] for r in links],
    })
    return transactions, transfer_links, key_to_txn


def _to_deposit(ev: dict) -> None:
    """Turn an event that cannot be posted as planned into a plain incoming deposit."""
    ev.update(txn_type="DEPOSIT", direction="CREDIT", channel="SYSTEM", merchant_id=None, device_id=None,
              amount=ev["alt_amount"])


def derive_device_last_seen(tables: dict[str, pd.DataFrame]) -> None:
    """Set account_devices.last_seen = max(first_seen, latest transaction using the device)."""
    txns = tables["transactions"].dropna(subset=["device_id"])
    latest = txns.groupby(["account_id", "device_id"])["txn_timestamp"].max()
    links = tables["account_devices"]
    keys = pd.MultiIndex.from_frame(links[["account_id", "device_id"]])
    seen = pd.Series(latest.reindex(keys).to_numpy(), index=links.index)
    links["last_seen"] = np.maximum(links["first_seen"], seen.fillna(links["first_seen"])).astype(np.int64)


def summary_stats(transactions: pd.DataFrame, transfer_links: pd.DataFrame) -> dict:
    """Distribution summary printed by the Stage 4 tests."""
    amounts = transactions["amount"] / 100
    return {
        "transactions": len(transactions),
        "transfer_links": len(transfer_links),
        "accounts_with_activity": int(transactions["account_id"].nunique()),
        "debit_share": round(float((transactions["direction"] == "DEBIT").mean()), 3),
        "amount_eur": {q: round(float(amounts.quantile(p)), 2)
                       for q, p in (("p05", 0.05), ("median", 0.5), ("p95", 0.95), ("max", 1.0))},
        "by_type": transactions["txn_type"].value_counts().to_dict(),
        "by_channel": transactions["channel"].value_counts().to_dict(),
        "txns_per_active_account_median": float(transactions.groupby("account_id").size().median()),
    }
