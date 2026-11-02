"""Stage 5b: transparent rules engine.

Rules are configuration rows (``risk_rules``) evaluated over source records only
(transactions, transfer links, device links, status history, KYC). Every rule
produces a single metric; it fires when ``metric >= threshold`` and scores

    score = weight + (ceiling - weight) * (1 - 0.5 ** ((metric - threshold) / (midpoint - threshold)))

i.e. the score equals the weight at the threshold, is halfway to the rule's ceiling at the
midpoint, and every further (midpoint - threshold) of metric halves the remaining distance: it
rises with the metric but never saturates. The ceiling is 100 for the multi-event pattern rules
and lower for the single-transaction profile rule, so one outlier payment cannot outrank a
pattern. It is evaluated in exact decimal arithmetic on the stored (4-decimal) metric and
rounded half-up to 2 decimals. Candidates are ranked by score DESC, created_at ASC, then
account, rule and primary transaction; the top ``--alerts`` become alerts. Alert ids
follow created_at; ``priority_rank`` follows (score DESC, created_at ASC, alert_id ASC).
This module never reads the ground truth.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
import pandas as pd

from . import ids
from .tables import cents_to_text, make_frame
from .timeutil import DAY, HOUR, format_ts, next_hour, to_seconds

RISK_RULES = [
    {"rule_id": "RULE-001", "rule_code": "VELOCITY_SPIKE", "rule_name": "Velocity spike",
     "description": "Count customer-initiated debits in a 24-hour window anchored at the first debit of a burst; "
                    "fires when the count reaches the threshold.",
     "applies_to": "DEBIT transactions of type PURCHASE, ATM_WITHDRAWAL, BILL_PAYMENT, TRANSFER_OUT",
     "metric_name": "debits in 24h window", "metric_unit": "COUNT", "lookback_hours": 24,
     "threshold_value": 6.0, "midpoint_value": 9.0, "weight": 60.0, "score_ceiling": 100.0, "rule_version": 2},
    {"rule_id": "RULE-002", "rule_code": "NEW_DEVICE_ACTIVITY", "rule_name": "New-device activity",
     "description": "Sum the debits made through a device within 48 hours of the device's first use on the "
                    "account; only devices first linked at least 7 days after account opening count as new.",
     "applies_to": "DEBIT transactions with a device_id (digital channels)",
     "metric_name": "EUR debited via new device within 48h", "metric_unit": "EUR", "lookback_hours": 48,
     "threshold_value": 1000.0, "midpoint_value": 3000.0, "weight": 65.0, "score_ceiling": 100.0,
     "rule_version": 1},
    {"rule_id": "RULE-003", "rule_code": "CIRCULAR_TRANSFER", "rule_name": "Circular transfer ring",
     "description": "Follow transfer links forward in time from each transfer; fires when funds return to the "
                    "originating account through at least 3 distinct accounts within 72 hours, every hop within "
                    "+/-10% of the first hop amount.",
     "applies_to": "transfer_links (TRANSFER_OUT/TRANSFER_IN leg pairs)",
     "metric_name": "first-hop amount of the ring", "metric_unit": "EUR", "lookback_hours": 72,
     "threshold_value": 500.0, "midpoint_value": 5250.0, "weight": 75.0, "score_ceiling": 100.0,
     "rule_version": 1},
    {"rule_id": "RULE-004", "rule_code": "DORMANT_REACTIVATION", "rule_name": "Dormant account reactivation",
     "description": "After a DORMANT->ACTIVE status event, sum the customer-initiated debits of the next 7 days; "
                    "fires when the sum reaches the threshold.",
     "applies_to": "account_status_history DORMANT->ACTIVE events and the account's DEBIT transactions",
     "metric_name": "EUR debited within 7 days of reactivation", "metric_unit": "EUR", "lookback_hours": 168,
     "threshold_value": 2000.0, "midpoint_value": 11000.0, "weight": 70.0, "score_ceiling": 100.0,
     "rule_version": 1},
    {"rule_id": "RULE-005", "rule_code": "HIGH_AMOUNT_VS_PROFILE", "rule_name": "High amount vs. customer profile",
     "description": "Divide a single card, cash or bill-payment debit by the customer's KYC "
                    "expected_max_txn_amount; fires when the ratio reaches the threshold.",
     "applies_to": "DEBIT transactions of type PURCHASE, ATM_WITHDRAWAL, BILL_PAYMENT",
     "metric_name": "amount / KYC expected max transaction", "metric_unit": "RATIO", "lookback_hours": 0,
     "threshold_value": 3.0, "midpoint_value": 6.0, "weight": 55.0, "score_ceiling": 92.0, "rule_version": 2},
    {"rule_id": "RULE-006", "rule_code": "ROUND_AMOUNT_STRUCTURING", "rule_name": "Round-amount structuring (retired)",
     "description": "Retired on 2025-12-31 after the annual rule review (superseded by RULE-005); kept for "
                    "traceability and not evaluated.",
     "applies_to": "DEPOSIT transactions", "metric_name": "round-amount deposits in 7 days",
     "metric_unit": "COUNT", "lookback_hours": 168, "threshold_value": 3.0, "midpoint_value": 4.5,
     "weight": 50.0, "score_ceiling": 100.0, "rule_version": 1, "effective_from": "2024-01-01", "effective_to": "2025-12-31",
     "is_active": 0},
]
RULE_DEFAULTS = {"effective_from": "2025-12-01", "effective_to": None, "is_active": 1,
                 "owner_role": "SYSTEM_ADMINISTRATOR", "approved_by_role": "RISK_REVIEWER"}
VELOCITY_TYPES = ["PURCHASE", "ATM_WITHDRAWAL", "BILL_PAYMENT", "TRANSFER_OUT"]
PROFILE_TYPES = ["PURCHASE", "ATM_WITHDRAWAL", "BILL_PAYMENT"]
NEW_DEVICE_MIN_ACCOUNT_AGE = 7 * DAY
RING_TOLERANCE = 0.10
RING_MIN_ACCOUNTS = 3
RING_MAX_HOPS = 6


@dataclass
class Candidate:
    """A rule hit before top-N selection."""

    rule_id: str
    account_id: str
    created_at: int
    metric: float
    score: float
    trigger_ids: list[str]
    primary_id: str
    reason: str

    def sort_key(self) -> tuple:
        """Selection order: score DESC, created_at ASC, account, rule, primary transaction."""
        return (-round(self.score * 100), self.created_at, self.account_id, self.rule_id, self.primary_id)


def risk_rules_table() -> pd.DataFrame:
    """The ``risk_rules`` configuration table."""
    rows = [{**RULE_DEFAULTS, **rule} for rule in RISK_RULES]
    data = {k: [r[k] for r in rows] for k in rows[0]}
    data["effective_from"] = np.array([to_seconds(d) for d in data["effective_from"]], dtype=np.int64)
    data["effective_to"] = pd.array([to_seconds(d) if d else None for d in data["effective_to"]], dtype="Int64")
    return make_frame("risk_rules", data)


def score(rule: dict, metric: float) -> float:
    """Documented scoring formula, evaluated in exact decimal arithmetic and rounded half-up to 2 decimals."""
    threshold, midpoint, weight, ceiling = (Decimal(str(rule[k])) for k in
                                            ("threshold_value", "midpoint_value", "weight", "score_ceiling"))
    steps = max(Decimal(0), (Decimal(str(metric)) - threshold) / (midpoint - threshold))
    progress = 1 - Decimal("0.5") ** steps
    return float((weight + (ceiling - weight) * progress).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def find_candidates(tables: dict[str, pd.DataFrame]) -> list[Candidate]:
    """Every hit of every active rule whose primary transaction falls in the rule's effective period."""
    txns = tables["transactions"]
    txn_ts = dict(zip(txns["transaction_id"], txns["txn_timestamp"]))
    evaluators = {"RULE-001": _velocity, "RULE-002": _new_device, "RULE-003": _circular,
                  "RULE-004": _dormant, "RULE-005": _high_amount}
    candidates: list[Candidate] = []
    for rule in tables["risk_rules"].to_dict("records"):
        if rule["is_active"]:
            candidates.extend(c for c in evaluators[rule["rule_id"]](tables, rule)
                              if _effective(rule, int(txn_ts[c.primary_id])))
    return candidates


def evaluate(tables: dict[str, pd.DataFrame], n_alerts: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run every active rule, keep the top ``n_alerts`` candidates; return (alerts, alert_transactions)."""
    candidates = find_candidates(tables)
    if len(candidates) < n_alerts:
        raise ValueError(f"only {len(candidates)} rule hits for --alerts {n_alerts}; "
                         "increase --transactions or lower --alerts")
    selected = sorted(candidates, key=Candidate.sort_key)[:n_alerts]
    selected.sort(key=lambda c: (c.created_at, c.account_id, c.rule_id, c.primary_id))
    rules = {r["rule_id"]: r for r in tables["risk_rules"].to_dict("records")}
    return _alert_tables(selected, rules, tables["transactions"])


def _effective(rule: dict, ts: int) -> bool:
    start, end = rule["effective_from"], rule["effective_to"]
    return start <= ts and (pd.isna(end) or ts < end + DAY)


def _candidate(rule: dict, account: str, metric: float, triggers: pd.DataFrame, primary_id: str,
               facts: str) -> Candidate:
    value = round(float(metric), 4)
    s = score(rule, value)
    th, mid, w, cap = rule["threshold_value"], rule["midpoint_value"], rule["weight"], rule["score_ceiling"]
    reason = (f"{rule['rule_code']}: {facts}. Threshold {_fmt(th, rule['metric_unit'])}. "
              f"Score = {w:g} + ({cap:g} - {w:g}) x (1 - 0.5^(({value:g} - {th:g}) / ({mid:g} - {th:g}))) = {s:.2f}")
    return Candidate(rule["rule_id"], account, int(next_hour(int(triggers["txn_timestamp"].max()))), value, s,
                     triggers["transaction_id"].tolist(), primary_id, reason)


def _fmt(value: float, unit: str) -> str:
    if unit == "EUR":
        return f"EUR {value:.2f}"
    if unit == "RATIO":
        return f"{value:.2f}x"
    return f"{value:g}"


def _eur(cents: int) -> str:
    return f"EUR {cents_to_text(int(cents))}"


def _debits(txns: pd.DataFrame, types: list[str]) -> pd.DataFrame:
    mask = (txns["direction"] == "DEBIT") & txns["txn_type"].isin(types)
    return txns[mask].sort_values(["account_id", "txn_timestamp", "transaction_id"], kind="mergesort")


# --- RULE-001 -----------------------------------------------------------------------

def _velocity(tables: dict[str, pd.DataFrame], rule: dict) -> list[Candidate]:
    threshold, window = int(rule["threshold_value"]), rule["lookback_hours"] * HOUR
    out = []
    for acc, group in _debits(tables["transactions"], VELOCITY_TYPES).groupby("account_id", sort=True):
        if len(group) < threshold:
            continue
        ts = group["txn_timestamp"].to_numpy()
        ends = np.searchsorted(ts, ts + window, side="right")
        j = 0
        while j < len(ts):
            if ends[j] - j >= threshold:
                burst = group.iloc[j:ends[j]]
                merchants = burst["merchant_id"].dropna().nunique()
                facts = (f"{len(burst)} debits within 24h from {format_ts(int(ts[j]))} at {merchants} distinct "
                         f"merchant(s), total {_eur(burst['amount'].sum())}")
                out.append(_candidate(rule, acc, len(burst), burst, burst["transaction_id"].iloc[threshold - 1], facts))
                j = int(ends[j])
            else:
                j += 1
    return out


# --- RULE-002 -----------------------------------------------------------------------

def _new_device(tables: dict[str, pd.DataFrame], rule: dict) -> list[Candidate]:
    hist = tables["account_status_history"]
    opened = hist[hist["reason_code"] == "ACCOUNT_OPENED"].set_index("account_id")["event_timestamp"]
    links = tables["account_devices"]
    links = links[links["first_seen"] >= links["account_id"].map(opened) + NEW_DEVICE_MIN_ACCOUNT_AGE]
    txns = tables["transactions"]
    debits = txns[(txns["direction"] == "DEBIT") & txns["device_id"].notna()]
    hits = debits.merge(links[["account_id", "device_id", "first_seen"]], on=["account_id", "device_id"])
    hits = hits[hits["txn_timestamp"] - hits["first_seen"] <= rule["lookback_hours"] * HOUR]
    out = []
    threshold = _eur_to_cents(rule["threshold_value"])
    for (acc, dev), group in hits.sort_values(["account_id", "device_id", "txn_timestamp", "transaction_id"],
                                              kind="mergesort").groupby(["account_id", "device_id"], sort=True):
        total = int(group["amount"].sum())
        if total < threshold:
            continue
        primary = group["transaction_id"].iloc[int(np.argmax(group["amount"].cumsum().to_numpy() >= threshold))]
        facts = (f"{_eur(total)} debited in {len(group)} transaction(s) via device {dev} within 48h of its first "
                 f"use on this account at {format_ts(int(group['first_seen'].iloc[0]))}")
        out.append(_candidate(rule, acc, total / 100, group, primary, facts))
    return out


def _eur_to_cents(value: float) -> int:
    return int(round(value * 100))


# --- RULE-003 -----------------------------------------------------------------------

def _circular(tables: dict[str, pd.DataFrame], rule: dict) -> list[Candidate]:
    links = tables["transfer_links"].sort_values(["initiated_at", "transfer_id"], kind="mergesort")
    edges = list(links[["transfer_id", "source_account_id", "dest_account_id", "amount", "initiated_at",
                        "source_txn_id", "dest_txn_id"]].itertuples(index=False, name=None))
    by_source: dict[str, list[tuple]] = {}
    for edge in edges:
        by_source.setdefault(edge[1], []).append(edge)
    times = {acc: [e[4] for e in out] for acc, out in by_source.items()}
    window, threshold = rule["lookback_hours"] * HOUR, _eur_to_cents(rule["threshold_value"])
    txns = tables["transactions"].set_index("transaction_id", drop=False)
    out = []
    for first in edges:
        if first[3] < threshold:
            continue
        ring = _find_ring(first, by_source, times, window)
        if ring is None:
            continue
        triggers = txns.loc[[leg for edge in ring for leg in (edge[5], edge[6])]]
        accounts = [edge[1] for edge in ring]
        hours = (ring[-1][4] - first[4]) / HOUR
        facts = (f"{len(ring)} transfers {' -> '.join(accounts + [accounts[0]])} returned to the origin within "
                 f"{hours:.1f}h; first hop {_eur(first[3])}")
        out.append(_candidate(rule, first[1], first[3] / 100, triggers, ring[-1][6], facts))
    return out


def _find_ring(first: tuple, by_source: dict, times: dict, window: int) -> list[tuple] | None:
    """Depth-first search for a time-ordered transfer ring that starts with ``first``."""
    low, high = first[3] * (1 - RING_TOLERANCE), first[3] * (1 + RING_TOLERANCE)
    deadline, origin = first[4] + window, first[1]

    def extend(path: list[tuple], visited: list[str]) -> list[tuple] | None:
        last = path[-1]
        outgoing = by_source.get(last[2], [])
        for edge in outgoing[bisect_right(times.get(last[2], []), last[4]):]:
            if edge[4] > deadline:
                break
            if not low <= edge[3] <= high:
                continue
            if edge[2] == origin and len(visited) >= RING_MIN_ACCOUNTS:
                return path + [edge]
            if edge[2] not in visited and len(path) < RING_MAX_HOPS - 1:
                found = extend(path + [edge], visited + [edge[2]])
                if found:
                    return found
        return None

    return extend([first], [first[1], first[2]])


# --- RULE-004 -----------------------------------------------------------------------

def _dormant(tables: dict[str, pd.DataFrame], rule: dict) -> list[Candidate]:
    hist = tables["account_status_history"]
    reactivations = hist[(hist["from_status"] == "DORMANT") & (hist["to_status"] == "ACTIVE")]
    debits = _debits(tables["transactions"], VELOCITY_TYPES)
    by_account = dict(tuple(debits.groupby("account_id", sort=False)))
    threshold, window = _eur_to_cents(rule["threshold_value"]), rule["lookback_hours"] * HOUR
    out = []
    for ev in reactivations.sort_values(["account_id", "event_timestamp"]).itertuples(index=False):
        group = by_account.get(ev.account_id)
        if group is None:
            continue
        group = group[group["txn_timestamp"].between(ev.event_timestamp, ev.event_timestamp + window)]
        total = int(group["amount"].sum())
        if total < threshold:
            continue
        primary = group["transaction_id"].iloc[int(np.argmax(group["amount"].cumsum().to_numpy() >= threshold))]
        facts = (f"{_eur(total)} debited in {len(group)} transaction(s) within 7 days after reactivation "
                 f"from DORMANT at {format_ts(int(ev.event_timestamp))} ({ev.reason_code})")
        out.append(_candidate(rule, ev.account_id, total / 100, group, primary, facts))
    return out


# --- RULE-005 -----------------------------------------------------------------------

def _high_amount(tables: dict[str, pd.DataFrame], rule: dict) -> list[Candidate]:
    debits = _debits(tables["transactions"], PROFILE_TYPES)
    expected = debits["account_id"].str[4:14].map(
        tables["kyc_profiles"].set_index("customer_id")["expected_max_txn_amount"])
    hits = debits.assign(ratio=(debits["amount"] / expected).round(4), expected=expected)
    hits = hits[hits["ratio"] >= rule["threshold_value"]]
    out = []
    for i in range(len(hits)):
        trigger = hits.iloc[i:i + 1]
        row = trigger.iloc[0]
        facts = (f"single {row.txn_type} of {_eur(row.amount)} is {row.ratio:.2f}x the customer's KYC expected "
                 f"maximum transaction of {_eur(row.expected)}")
        out.append(_candidate(rule, row.account_id, row.ratio, trigger, row.transaction_id, facts))
    return out


# --- alert tables ---------------------------------------------------------------------

def _alert_tables(selected: list[Candidate], rules: dict, txns: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    alert_ids = [ids.alert_id(i + 1) for i in range(len(selected))]
    amount = dict(zip(txns["transaction_id"], txns["amount"]))
    rank_order = sorted(range(len(selected)),
                        key=lambda i: (-round(selected[i].score * 100), selected[i].created_at, alert_ids[i]))
    rank = {i: r + 1 for r, i in enumerate(rank_order)}
    alerts = make_frame("alerts", {
        "alert_id": alert_ids,
        "rule_id": [c.rule_id for c in selected],
        "account_id": [c.account_id for c in selected],
        "customer_id": [c.account_id[4:14] for c in selected],
        "created_at": np.array([c.created_at for c in selected], dtype=np.int64),
        "metric_value": [c.metric for c in selected],
        "threshold_value": [rules[c.rule_id]["threshold_value"] for c in selected],
        "score": [c.score for c in selected],
        "priority_rank": [rank[i] for i in range(len(selected))],
        "trigger_txn_count": [len(c.trigger_ids) for c in selected],
        "trigger_amount": np.array([sum(amount[t] for t in c.trigger_ids) for c in selected], dtype=np.int64),
        "reason": [c.reason for c in selected],
        "alert_status": [None] * len(selected),
    })
    bridge = [(a, t, "PRIMARY" if t == c.primary_id else "SUPPORTING")
              for a, c in zip(alert_ids, selected) for t in c.trigger_ids]
    alert_transactions = make_frame("alert_transactions", {
        "alert_id": [b[0] for b in bridge], "transaction_id": [b[1] for b in bridge],
        "trigger_role": [b[2] for b in bridge],
    })
    return alerts, alert_transactions
