"""Stage 6: labelled edge-case injection.

Runs after the valid dataset is complete and works on a copy, so the clean dataset is
kept for comparison. ``--edge-cases`` rows are injected per type, each producing exactly
one data-quality defect and touching only its own rows:

=============================  =================================================================
issue type                     injection (target rows)
=============================  =================================================================
TXN_ON_CLOSED_ACCOUNT          append a branch deposit 1-5 days after an account was closed
                               (balance chain stays arithmetically correct)
NEGATIVE_BALANCE_NO_OVERDRAFT  append an ATM withdrawal larger than the balance of an account
                               without overdraft (chain correct, balance ends below zero)
TRANSFER_LEG_MISMATCH          append a transfer whose credit leg is smaller than the debit leg
                               and than the link amount (both ledgers stay correct)
ALERT_WITHOUT_TRIGGER          delete the alert_transactions rows of an existing alert
DISPOSITION_BEFORE_ALERT       move an existing DISPOSITION action to 1-72 h before its alert
=============================  =================================================================

Accounts used for injected transactions are untouched by planted scenarios and alerts.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import ids
from .master_data import WINDOW_END_S, WINDOW_START_S
from .patterns import SUSPICIOUS, Plant
from .tables import make_frame
from .timeutil import DAY, HOUR
from .transactions import closures_in_window

EDGE_TYPES = ["TXN_ON_CLOSED_ACCOUNT", "NEGATIVE_BALANCE_NO_OVERDRAFT", "TRANSFER_LEG_MISMATCH",
              "ALERT_WITHOUT_TRIGGER", "DISPOSITION_BEFORE_ALERT"]


@dataclass
class EdgeCase:
    """One injected defect (feeds the ground truth)."""

    issue_type: str
    entity_type: str
    entity_id: str
    notes: str


def inject(clean: dict[str, pd.DataFrame], plants: list[Plant], per_type: int,
           rng: np.random.Generator) -> tuple[dict[str, pd.DataFrame], list[EdgeCase]]:
    """Return (copy of ``clean`` with injected defects, the injected edge cases)."""
    tables = {name: frame.copy() for name, frame in clean.items()}
    injector = _Injector(tables, plants, rng)
    cases: list[EdgeCase] = []
    cases += injector.closed_account_credits(per_type)
    cases += injector.overdrawn_debits(per_type)
    cases += injector.mismatched_transfers(per_type)
    injector.flush()
    cases += _orphan_alerts(tables, plants, per_type, rng)
    cases += _early_dispositions(tables, {c.entity_id for c in cases if c.issue_type == "ALERT_WITHOUT_TRIGGER"},
                                 per_type, rng)
    return tables, cases


def _sample(rng: np.random.Generator, candidates: list[str], n: int, what: str) -> list[str]:
    if len(candidates) < n:
        raise ValueError(f"only {len(candidates)} candidates for {what}; lower --edge-cases")
    return sorted(candidates[i] for i in rng.choice(len(candidates), size=n, replace=False))


class _Injector:
    """Appends edge-case transactions at the end of untouched account ledgers."""

    def __init__(self, tables: dict[str, pd.DataFrame], plants: list[Plant], rng: np.random.Generator):
        self.tables, self.rng = tables, rng
        txns = tables["transactions"]
        last = txns.sort_values(["account_id", "txn_timestamp", "transaction_id"]).groupby("account_id").tail(1)
        self.last_ts = dict(zip(last["account_id"], last["txn_timestamp"].astype(int)))
        self.balance = dict(zip(last["account_id"], last["balance_after"].astype(int)))
        accounts = tables["accounts"]
        for acc, opening in zip(accounts["account_id"], accounts["opening_balance"]):
            self.balance.setdefault(acc, int(opening))
        bridge = tables["alert_transactions"].merge(txns[["transaction_id", "account_id"]], on="transaction_id")
        touched = set(tables["alerts"]["account_id"]) | set(bridge["account_id"])
        touched |= {m for p in plants for m in p.members}
        touched |= set(tables["account_status_history"].loc[
            tables["account_status_history"]["reason_code"].str.startswith("REACTIVATED"), "account_id"])
        self.used = touched
        self.owner = dict(zip(accounts["account_id"], accounts["customer_id"]))
        self.closures = closures_in_window(tables["account_status_history"])
        self.quiet = sorted(a for a in accounts.loc[(accounts["account_status"] == "ACTIVE")
                                                    & accounts["overdraft_limit"].isna(), "account_id"]
                            if a not in touched and a not in self.closures and self.balance[a] >= 10_000
                            and self.last_ts.get(a, 0) < WINDOW_END_S - 2 * DAY)
        self.rows: list[tuple] = []
        self.links: list[tuple] = []
        self.next_txn = len(txns) + 1
        self.next_transfer = len(tables["transfer_links"]) + 1

    def _take(self, pool: list[str], n: int, what: str) -> list[str]:
        chosen = _sample(self.rng, [a for a in pool if a not in self.used], n, what)
        self.used.update(chosen)
        return chosen

    def _append(self, acc: str, ts: int, direction: str, txn_type: str, channel: str, amount: int) -> str:
        txn = ids.transaction_id(self.next_txn)
        self.next_txn += 1
        before = self.balance[acc]
        after = before + (amount if direction == "CREDIT" else -amount)
        self.rows.append((txn, acc, ts, direction, txn_type, channel, amount, before, after))
        self.balance[acc], self.last_ts[acc] = after, ts
        return txn

    def _after_last(self, accounts: list[str]) -> int:
        start = max(self.last_ts.get(a, WINDOW_START_S) for a in accounts) + HOUR
        return int(self.rng.integers(start, max(start + 1, WINDOW_END_S)))

    def closed_account_credits(self, n: int) -> list[EdgeCase]:
        out = []
        for acc in self._take(sorted(self.closures), n, "closed-account credits"):
            ts = min(self.closures[acc] + int(self.rng.integers(DAY, 5 * DAY)), WINDOW_END_S)
            txn = self._append(acc, ts, "CREDIT", "DEPOSIT", "BRANCH", int(self.rng.integers(2_000, 50_000)))
            out.append(EdgeCase("TXN_ON_CLOSED_ACCOUNT", "TRANSACTION", txn,
                                f"branch deposit on {acc} after its closure on "
                                f"{np.datetime_as_string(np.datetime64(self.closures[acc], 's'), 'D')}"))
        return out

    def overdrawn_debits(self, n: int) -> list[EdgeCase]:
        out = []
        for acc in self._take(self.quiet, n, "overdrawn debits"):
            amount = self.balance[acc] + int(self.rng.integers(5_000, 50_000))
            txn = self._append(acc, self._after_last([acc]), "DEBIT", "ATM_WITHDRAWAL", "ATM", amount)
            out.append(EdgeCase("NEGATIVE_BALANCE_NO_OVERDRAFT", "TRANSACTION", txn,
                                f"ATM withdrawal leaves {acc} (no overdraft) at EUR {self.balance[acc] / 100:.2f}"))
        return out

    def mismatched_transfers(self, n: int) -> list[EdgeCase]:
        out = []
        chosen = self._take(self.quiet, 2 * n, "mismatched transfers")
        for src, dst in zip(chosen[0::2], chosen[1::2]):
            amount = int(self.balance[src] * self.rng.uniform(0.2, 0.6))
            short = int(self.rng.integers(100, min(5_000, amount // 2)))
            ts = self._after_last([src, dst])
            debit = self._append(src, ts, "DEBIT", "TRANSFER_OUT", "BRANCH", amount)
            credit = self._append(dst, ts + int(self.rng.integers(1, 91)), "CREDIT", "TRANSFER_IN", "SYSTEM",
                                  amount - short)
            transfer = ids.transfer_id(self.next_transfer)
            self.next_transfer += 1
            scope = "OWN_ACCOUNTS" if self.owner[src] == self.owner[dst] else "THIRD_PARTY"
            self.links.append((transfer, debit, credit, src, dst, amount, ts, scope))
            out.append(EdgeCase("TRANSFER_LEG_MISMATCH", "TRANSFER_LINK", transfer,
                                f"debit leg {debit} EUR {amount / 100:.2f} but credit leg {credit} "
                                f"EUR {(amount - short) / 100:.2f}"))
        return out

    def flush(self) -> None:
        """Append the injected transactions and transfer links to their tables."""
        rows = self.rows
        appended = make_frame("transactions", {
            "transaction_id": [r[0] for r in rows], "account_id": [r[1] for r in rows],
            "txn_timestamp": np.array([r[2] for r in rows], dtype=np.int64), "direction": [r[3] for r in rows],
            "txn_type": [r[4] for r in rows], "channel": [r[5] for r in rows],
            "amount": np.array([r[6] for r in rows], dtype=np.int64), "currency": ["EUR"] * len(rows),
            "balance_before": np.array([r[7] for r in rows], dtype=np.int64),
            "balance_after": np.array([r[8] for r in rows], dtype=np.int64),
            "merchant_id": [None] * len(rows), "device_id": [None] * len(rows),
        })
        self.tables["transactions"] = pd.concat([self.tables["transactions"], appended], ignore_index=True)
        links = self.links
        appended_links = make_frame("transfer_links", {
            "transfer_id": [r[0] for r in links], "source_txn_id": [r[1] for r in links],
            "dest_txn_id": [r[2] for r in links], "source_account_id": [r[3] for r in links],
            "dest_account_id": [r[4] for r in links], "amount": np.array([r[5] for r in links], dtype=np.int64),
            "initiated_at": np.array([r[6] for r in links], dtype=np.int64),
            "transfer_scope": [r[7] for r in links],
        })
        self.tables["transfer_links"] = pd.concat([self.tables["transfer_links"], appended_links], ignore_index=True)


def _orphan_alerts(tables: dict[str, pd.DataFrame], plants: list[Plant], n: int,
                   rng: np.random.Generator) -> list[EdgeCase]:
    """Remove every triggering-transaction link of ``n`` alerts (never a suspicious plant's alert)."""
    alerts = tables["alerts"]
    protected = {(p.rule_id, p.account_id) for p in plants if p.label == SUSPICIOUS}
    pool = [a for a, r, acc in zip(alerts["alert_id"], alerts["rule_id"], alerts["account_id"])
            if (r, acc) not in protected]
    chosen = _sample(rng, pool, n, "alerts without trigger")
    bridge = tables["alert_transactions"]
    removed = bridge[bridge["alert_id"].isin(chosen)].groupby("alert_id").size()
    tables["alert_transactions"] = bridge[~bridge["alert_id"].isin(chosen)].reset_index(drop=True)
    return [EdgeCase("ALERT_WITHOUT_TRIGGER", "ALERT", a, f"{removed[a]} alert_transactions row(s) removed")
            for a in chosen]


def _early_dispositions(tables: dict[str, pd.DataFrame], excluded_alerts: set[str], n: int,
                        rng: np.random.Generator) -> list[EdgeCase]:
    """Move ``n`` DISPOSITION actions to 1-72 hours before their alert was created."""
    actions = tables["analyst_actions"]
    created = tables["alerts"].set_index("alert_id")["created_at"]
    pool = actions[(actions["action_type"] == "DISPOSITION") & ~actions["alert_id"].isin(excluded_alerts)]
    chosen = _sample(rng, pool["action_id"].tolist(), n, "early dispositions")
    out = []
    for action in chosen:
        idx = actions.index[actions["action_id"] == action][0]
        alert = actions.at[idx, "alert_id"]
        moved = int(created[alert]) - int(rng.integers(HOUR, 72 * HOUR))
        actions.at[idx, "action_timestamp"] = moved
        out.append(EdgeCase("DISPOSITION_BEFORE_ALERT", "ANALYST_ACTION", action,
                            f"disposition of {alert} moved to {(int(created[alert]) - moved) / HOUR:.1f}h "
                            "before the alert was created"))
    return out
