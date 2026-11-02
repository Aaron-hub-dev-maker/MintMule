"""Integrity assertions shared by the stage tests (independent of generator logic)."""

import pandas as pd

from generator import ids, schema

ID_COLUMN_KIND = {
    "customer_id": "customer", "kyc_id": "kyc", "account_id": "account", "merchant_id": "merchant",
    "device_id": "device", "transaction_id": "transaction", "transfer_id": "transfer", "rule_id": "rule",
    "alert_id": "alert", "case_id": "case", "action_id": "action", "status_event_id": "status_event",
    "source_txn_id": "transaction", "dest_txn_id": "transaction", "source_account_id": "account",
    "dest_account_id": "account", "primary_account_id": "account",
}


def assert_unique_pk(name: str, frame: pd.DataFrame) -> None:
    pk = list(schema.table_specs()[name].primary_key)
    dupes = frame[frame.duplicated(pk, keep=False)]
    assert dupes.empty, f"{name}: duplicate primary keys\n{dupes.head()}"
    assert frame[pk].notna().all().all(), f"{name}: null primary key"


def assert_foreign_keys(name: str, tables: dict[str, pd.DataFrame]) -> None:
    child = tables[name]
    for fk in schema.table_specs()[name].foreign_keys:
        if fk.parent not in tables:
            continue
        cols = list(fk.columns)
        present = child[cols].dropna()
        parent_keys = set(map(tuple, tables[fk.parent][list(fk.parent_columns)].itertuples(index=False)))
        missing = [key for key in map(tuple, present.itertuples(index=False)) if key not in parent_keys]
        assert not missing, f"{name}{cols} -> {fk.parent}: {len(missing)} orphan(s), e.g. {missing[:3]}"


def assert_enums(name: str, frame: pd.DataFrame) -> None:
    for col, allowed in schema.table_specs()[name].enums.items():
        values = set(frame[col].dropna().astype(str))
        assert values <= set(allowed), f"{name}.{col}: unexpected {sorted(values - set(allowed))}"


def assert_required(name: str, frame: pd.DataFrame) -> None:
    for col in schema.table_specs()[name].columns:
        if col.not_null:
            assert frame[col.name].notna().all(), f"{name}.{col.name} has nulls"


def assert_id_formats(name: str, frame: pd.DataFrame) -> None:
    for col, kind in ID_COLUMN_KIND.items():
        if col in frame.columns:
            bad = [v for v in frame[col].dropna() if not ids.is_valid(kind, v)]
            assert not bad, f"{name}.{col}: bad ids {bad[:3]}"


def assert_ledger_valid(tables: dict[str, pd.DataFrame]) -> None:
    """Every ledger reconciles, no unexplained negative balance, transfer legs match, every
    posting lies in an ACTIVE status interval and device postings use a linked device."""
    txn = tables["transactions"]
    ledger = txn.sort_values(["account_id", "txn_timestamp", "transaction_id"], kind="mergesort")
    accounts = tables["accounts"].set_index("account_id")
    sign = (ledger["direction"] == "CREDIT").map({True: 1, False: -1})
    assert (ledger["balance_after"] == ledger["balance_before"] + sign * ledger["amount"]).all()
    prev = ledger.groupby("account_id")["balance_after"].shift()
    first = prev.isna()
    assert (ledger.loc[first, "balance_before"] == ledger.loc[first, "account_id"].map(accounts["opening_balance"])).all()
    assert (ledger.loc[~first, "balance_before"] == prev[~first]).all()
    assert (ledger["balance_after"] >= -ledger["account_id"].map(accounts["overdraft_limit"]).fillna(0)).all()

    by_id = txn.set_index("transaction_id")
    links = tables["transfer_links"]
    src, dst = by_id.loc[links["source_txn_id"]], by_id.loc[links["dest_txn_id"]]
    assert (src["txn_type"].to_numpy() == "TRANSFER_OUT").all() and (dst["txn_type"].to_numpy() == "TRANSFER_IN").all()
    assert (src["amount"].to_numpy() == links["amount"].to_numpy()).all()
    assert (dst["amount"].to_numpy() == links["amount"].to_numpy()).all()
    assert (src["account_id"].to_numpy() == links["source_account_id"].to_numpy()).all()
    assert (dst["account_id"].to_numpy() == links["dest_account_id"].to_numpy()).all()

    hist = tables["account_status_history"].sort_values(["account_id", "event_timestamp"])
    spans: dict[str, list[tuple[int, int]]] = {}
    for acc, group in hist.groupby("account_id"):
        start = None
        for ts, status in zip(group["event_timestamp"], group["to_status"]):
            if status == "ACTIVE" and start is None:
                start = ts
            elif status != "ACTIVE" and start is not None:
                spans.setdefault(acc, []).append((start, ts))
                start = None
        if start is not None:
            spans.setdefault(acc, []).append((start, 2**62))
    outside = [r.transaction_id for r in txn.itertuples(index=False)
               if not any(s <= r.txn_timestamp < e for s, e in spans.get(r.account_id, []))]
    assert not outside, f"{len(outside)} postings outside ACTIVE intervals: {outside[:3]}"

    digital = txn[txn["device_id"].notna()].merge(tables["account_devices"], on=["account_id", "device_id"], how="left")
    assert digital["first_seen"].notna().all()
    assert (digital["first_seen"] <= digital["txn_timestamp"]).all()
    assert (digital["txn_timestamp"] <= digital["last_seen"]).all()


def assert_table_integrity(tables: dict[str, pd.DataFrame], names: list[str]) -> None:
    """PK uniqueness, required fields, enums, id formats and FKs for ``names``."""
    for name in names:
        assert list(tables[name].columns) == schema.columns(name), f"{name}: columns differ from schema"
        assert_unique_pk(name, tables[name])
        assert_required(name, tables[name])
        assert_enums(name, tables[name])
        assert_id_formats(name, tables[name])
        assert_foreign_keys(name, tables)
