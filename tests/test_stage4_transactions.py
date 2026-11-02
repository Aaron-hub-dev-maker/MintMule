"""Stage 4 gate: baseline ledger reconciles, no unexplained negative balances, transfer
legs match, no transaction outside an ACTIVE account interval, sensible distributions."""

import json

import numpy as np
import pandas as pd

from generator import master_data, transactions
from generator.config import parse_config
from generator.master_data import WINDOW_END_S, WINDOW_START_S, active_intervals
from generator.rng import make_rng
from helpers import assert_table_integrity

CHANNELS_BY_TYPE = {
    "PURCHASE": {"CARD_POS", "ECOMMERCE"}, "REFUND": {"CARD_POS", "ECOMMERCE"}, "ATM_WITHDRAWAL": {"ATM"},
    "FEE": {"SYSTEM"}, "INTEREST": {"SYSTEM"}, "SALARY": {"SYSTEM"}, "DEPOSIT": {"SYSTEM", "BRANCH"},
    "BILL_PAYMENT": {"MOBILE_APP", "ONLINE_BANKING", "BRANCH"},
    "TRANSFER_OUT": {"MOBILE_APP", "ONLINE_BANKING", "BRANCH"},
    "TRANSFER_IN": {"SYSTEM"}, "ACCOUNT_CLOSURE": {"BRANCH"},
}


def _ledger(t):
    return t["transactions"].sort_values(["account_id", "txn_timestamp", "transaction_id"], kind="mergesort")


def test_exact_transaction_count_and_integrity(baseline):
    cfg, t = baseline
    assert len(t["transactions"]) == cfg.transactions
    assert_table_integrity(t, ["transactions", "transfer_links", "account_devices"])
    expected_ids = [f"TXN-2026-{i:08d}" for i in range(1, cfg.transactions + 1)]
    assert t["transactions"]["transaction_id"].tolist() == expected_ids


def test_running_balance_reconciles_for_every_account(baseline):
    _, t = baseline
    ledger = _ledger(t)
    opening = t["accounts"].set_index("account_id")["opening_balance"]
    sign = np.where(ledger["direction"] == "CREDIT", 1, -1)
    assert (ledger["balance_after"] == ledger["balance_before"] + sign * ledger["amount"]).all()
    prev_after = ledger.groupby("account_id")["balance_after"].shift()
    first = prev_after.isna()
    assert (ledger.loc[first, "balance_before"] == ledger.loc[first, "account_id"].map(opening)).all()
    assert (ledger.loc[~first, "balance_before"] == prev_after[~first]).all()
    print(f"\nreconciled {ledger['account_id'].nunique()} account ledgers / {len(ledger)} postings")


def test_no_unexplained_negative_balance(baseline):
    _, t = baseline
    ledger = _ledger(t)
    limit = ledger["account_id"].map(t["accounts"].set_index("account_id")["overdraft_limit"]).fillna(0)
    assert (ledger["balance_after"] >= -limit).all()
    negatives = ledger[ledger["balance_after"] < 0]
    assert (negatives["account_id"].map(t["accounts"].set_index("account_id")["overdraft_limit"]).notna()).all()
    print(f"\nnegative balances: {len(negatives)} rows, all within an approved overdraft")


def test_transfer_legs_match(baseline):
    _, t = baseline
    txn = t["transactions"].set_index("transaction_id")
    links = t["transfer_links"]
    src, dst = txn.loc[links["source_txn_id"]], txn.loc[links["dest_txn_id"]]
    assert (src["direction"].to_numpy() == "DEBIT").all() and (src["txn_type"].to_numpy() == "TRANSFER_OUT").all()
    assert (dst["direction"].to_numpy() == "CREDIT").all() and (dst["txn_type"].to_numpy() == "TRANSFER_IN").all()
    assert (src["amount"].to_numpy() == links["amount"].to_numpy()).all()
    assert (dst["amount"].to_numpy() == links["amount"].to_numpy()).all()
    assert (src["account_id"].to_numpy() == links["source_account_id"].to_numpy()).all()
    assert (dst["account_id"].to_numpy() == links["dest_account_id"].to_numpy()).all()
    assert (dst["txn_timestamp"].to_numpy() >= src["txn_timestamp"].to_numpy()).all()
    assert (links["initiated_at"].to_numpy() == src["txn_timestamp"].to_numpy()).all()
    owner = t["accounts"].set_index("account_id")["customer_id"]
    same = links["source_account_id"].map(owner) == links["dest_account_id"].map(owner)
    assert (links["transfer_scope"] == np.where(same, "OWN_ACCOUNTS", "THIRD_PARTY")).all()
    legs = t["transactions"][t["transactions"]["txn_type"].isin(["TRANSFER_OUT", "TRANSFER_IN"])]
    linked = pd.concat([links["source_txn_id"], links["dest_txn_id"]])
    assert set(legs["transaction_id"]) == set(linked) and linked.is_unique


def test_no_transaction_outside_an_active_account_interval(baseline):
    _, t = baseline
    spans = active_intervals(t["account_status_history"])
    txn = t["transactions"]
    outside = [r.transaction_id for r in txn.itertuples(index=False)
               if not any(s <= r.txn_timestamp < e for s, e in spans[r.account_id])]
    assert not outside, f"{len(outside)} transactions outside ACTIVE intervals, e.g. {outside[:3]}"
    closed = t["accounts"][t["accounts"]["account_status"] == "CLOSED"]["account_id"]
    hist = t["account_status_history"]
    close_ts = hist[hist["to_status"] == "CLOSED"].set_index("account_id")["event_timestamp"]
    on_closed = txn[txn["account_id"].isin(closed)]
    assert (on_closed["txn_timestamp"] < on_closed["account_id"].map(close_ts)).all()
    assert txn["txn_timestamp"].between(WINDOW_START_S, WINDOW_END_S).all()


def test_closed_accounts_are_swept_to_zero(baseline):
    _, t = baseline
    ledger = _ledger(t)
    sweeps = ledger[ledger["txn_type"] == "ACCOUNT_CLOSURE"]
    closures = transactions.closures_in_window(t["account_status_history"])
    assert set(sweeps["account_id"]) == set(closures)
    last = ledger.groupby("account_id").tail(1).set_index("account_id")
    assert (last.loc[list(closures), "txn_type"] == "ACCOUNT_CLOSURE").all()
    assert (last.loc[list(closures), "balance_after"] == 0).all()


def test_per_account_timestamps_strictly_increase(baseline):
    _, t = baseline
    diffs = _ledger(t).groupby("account_id")["txn_timestamp"].diff().dropna()
    assert (diffs > 0).all()


def test_channel_merchant_and_device_rules(baseline):
    _, t = baseline
    txn = t["transactions"]
    assert all(c in CHANNELS_BY_TYPE[ty] for ty, c in zip(txn["txn_type"], txn["channel"]))
    needs_merchant = txn["txn_type"].isin(["PURCHASE", "BILL_PAYMENT", "REFUND"])
    assert (txn["merchant_id"].notna() == needs_merchant).all()
    digital = txn["channel"].isin(["MOBILE_APP", "ONLINE_BANKING"])
    assert (txn["device_id"].notna() == digital).all()
    with_device = txn[digital].merge(t["account_devices"], on=["account_id", "device_id"], how="left")
    assert with_device["first_seen"].notna().all()
    assert (with_device["first_seen"] <= with_device["txn_timestamp"]).all()
    assert (with_device["txn_timestamp"] <= with_device["last_seen"]).all()
    dtype = with_device["device_id"].map(t["devices"].set_index("device_id")["device_type"])
    assert ((dtype == "DESKTOP_WEB") == (with_device["channel"] == "ONLINE_BANKING")).all()


def test_distributions_are_sensible(baseline):
    cfg, t = baseline
    txn = t["transactions"]
    stats = transactions.summary_stats(txn, t["transfer_links"])
    print(f"\n[{cfg.profile}] {json.dumps(stats)}")
    assert 0.5 <= stats["debit_share"] <= 0.8
    assert 20 <= stats["amount_eur"]["median"] <= 1000
    assert set(stats["by_type"]) == set(CHANNELS_BY_TYPE)
    assert len(t["transfer_links"]) >= 0.025 * cfg.transactions
    kyc_max = txn["account_id"].str[4:14].map(t["kyc_profiles"].set_index("customer_id")["expected_max_txn_amount"])
    customer_debits = txn["direction"].eq("DEBIT") & ~txn["txn_type"].eq("ACCOUNT_CLOSURE")
    assert (txn.loc[customer_debits, "amount"] <= 2.5 * kyc_max[customer_debits]).all()
    assert txn.groupby("account_id").size().median() >= 5


def test_baseline_is_deterministic():
    cfg = parse_config(["--profile", "starter"])

    def build(seed):
        rng = make_rng(seed)
        tables = master_data.generate(cfg, rng)
        return transactions.post_ledger(tables, transactions.baseline_events(tables, rng, cfg.transactions))[0]

    pd.testing.assert_frame_equal(build(cfg.seed), build(cfg.seed))
    assert not build(cfg.seed).equals(build(cfg.seed + 1))


def test_planted_debit_must_be_funded(baseline):
    _, t = baseline
    acc = t["accounts"][t["accounts"]["overdraft_limit"].isna() & (t["accounts"]["account_status"] == "ACTIVE")
                        ].iloc[0]["account_id"]
    event = transactions.make_event(0, acc, WINDOW_START_S + 3600, "PURCHASE", 10**12, channel="CARD_POS",
                                    merchant=t["merchants"].iloc[0]["merchant_id"], planted=True)
    try:
        transactions.post_ledger(t, [event])
    except RuntimeError as exc:
        assert "not funded" in str(exc)
    else:
        raise AssertionError("an unfunded planted debit was posted")
