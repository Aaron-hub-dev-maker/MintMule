"""Runs the generator stages in order.

Order of work (and of draws from the single seeded generator):

1. master data (customers, KYC, accounts, merchants, devices, links, status history)
2. planted scenarios (events + master additions); drawn first only so that their
   share of the transaction budget is known
3. baseline events for the remaining budget, kept away from planted activity
4. master additions applied, statuses re-derived, all events posted to the ledger
5. rules -> top-N alerts -> cases -> analyst actions (clean dataset)
6. edge cases injected into a copy, ground truth written from plants + edge cases
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pandas as pd

from . import cases, edge_cases, exports, ground_truth, master_data, patterns, rules, transactions
from .config import Config, parse_config
from .edge_cases import EdgeCase
from .patterns import Plant
from .rng import make_rng

# Edge-case rows appended to the transactions table per edge-case index: one credit on a
# closed account, one overdrawing debit and the two legs of a mismatched transfer.
EDGE_TRANSACTIONS_PER_CASE = 4


@dataclass
class CleanDataset:
    """Valid dataset before edge-case injection, plus what is needed to label it."""

    tables: dict[str, pd.DataFrame]
    plants: list[Plant]
    key_to_txn: dict[int, str]


def build_clean(cfg: Config, rng) -> CleanDataset:
    """Generate the valid dataset (stages 3-5) with exactly ``--alerts`` alerts."""
    tables = master_data.generate(cfg, rng)
    plan = patterns.plan(cfg, tables, rng)
    budget = cfg.transactions - len(plan.events) - EDGE_TRANSACTIONS_PER_CASE * cfg.edge_cases
    if budget < 0.5 * cfg.transactions:
        raise ValueError("planted scenarios need more than half of --transactions; raise --transactions")
    events = transactions.baseline_events(tables, rng, budget, blocked=plan.blocked, first_key=len(plan.events))
    patterns.apply(tables, plan)
    master_data.derive_statuses(tables)
    tables["transactions"], tables["transfer_links"], key_to_txn = transactions.post_ledger(
        tables, plan.events + events)
    transactions.derive_device_last_seen(tables)
    tables["risk_rules"] = rules.risk_rules_table()
    tables["alerts"], tables["alert_transactions"] = rules.evaluate(tables, cfg.alerts)
    cases.build(tables, rng)
    return CleanDataset(tables, plan.plants, key_to_txn)


@dataclass
class Dataset:
    """Final dataset: tables with injected edge cases, the clean snapshot and the ground truth."""

    cfg: Config
    clean: CleanDataset
    tables: dict[str, pd.DataFrame]
    edge_cases: list[EdgeCase]
    ground_truth: pd.DataFrame


def build(cfg: Config) -> Dataset:
    """Run every generation stage for ``cfg`` from a single generator seeded with ``cfg.seed``."""
    rng = make_rng(cfg.seed)
    clean = build_clean(cfg, rng)
    tables, injected = edge_cases.inject(clean.tables, clean.plants, cfg.edge_cases, rng)
    truth = ground_truth.build(clean.plants, clean.key_to_txn, injected)
    return Dataset(cfg, clean, tables, injected, truth)


def main(argv: list[str] | None = None) -> int:
    """Generate, export and validate the dataset described by the command-line arguments."""
    cfg = parse_config(argv)
    dataset = build(cfg)
    reconciliation = exports.export(dataset, cfg.out_dir)
    print(f"profile={cfg.profile} seed={cfg.seed} out={cfg.out_dir.as_posix()}")
    print("rows: " + ", ".join(f"{k}={len(v)}" for k, v in dataset.tables.items()))
    print(f"validation: findings={reconciliation['flagged_findings']} "
          f"expected_edge_cases={reconciliation['expected_edge_cases']} matched={reconciliation['matched']} "
          f"exact_match={reconciliation['exact_match']}")
    return 0 if reconciliation["exact_match"] else 1


if __name__ == "__main__":
    sys.exit(main())
