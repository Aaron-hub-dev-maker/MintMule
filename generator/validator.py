"""Stage 7: independent validator.

Validates a dataset from its exported CSV files and the schema contract only. It imports
nothing from the generator: keys, foreign keys, enums and required fields come from
``schema.sql`` (introspected through SQLite), and the business rules below are this
module's own restatement of the data dictionary.

Checks (one finding per offending record and check family, most specific code first):

* schema-driven: PK_DUPLICATE, REQUIRED_MISSING, ENUM_VIOLATION, UNIQUE_VIOLATION,
  FORMAT_VIOLATION, FK_VIOLATION
* ledger: BALANCE_ARITHMETIC, BALANCE_CHAIN_BREAK, NEGATIVE_BALANCE_NO_OVERDRAFT,
  OVERDRAFT_LIMIT_EXCEEDED, CLOSED_ACCOUNT_BALANCE_NONZERO
* lifecycle: TXN_BEFORE_ACCOUNT_OPEN, TXN_ON_CLOSED_ACCOUNT, TXN_ON_INACTIVE_ACCOUNT,
  TXN_OUTSIDE_WINDOW, STATUS_TRANSITION_INVALID, ACCOUNT_STATUS_MISMATCH,
  CUSTOMER_STATUS_MISMATCH, KYC_STATUS_MISMATCH, KYC_DATE_INVALID, ACCOUNT_ATTRIBUTE_INVALID
* transaction attributes: TXN_TYPE_INVALID, TXN_CHANNEL_INVALID, TXN_MERCHANT_INVALID,
  TXN_DEVICE_INVALID, TXN_TIMESTAMP_NOT_INCREASING, ACCOUNT_DEVICE_INVALID
* transfers: TRANSFER_LEG_INVALID, TRANSFER_LEG_MISMATCH, TRANSFER_LEG_ORPHAN
* alerts / cases / actions: ALERT_WITHOUT_TRIGGER, ALERT_TRIGGER_MISMATCH, ALERT_RULE_INVALID,
  ALERT_SCORE_MISMATCH, ALERT_RANK_INCONSISTENT, ALERT_STATUS_MISMATCH, CASE_SUMMARY_MISMATCH,
  ACTION_SEQUENCE_INVALID, DISPOSITION_BEFORE_ALERT, ACTION_BEFORE_ALERT,
  ACTION_BEFORE_CASE_OPEN, ACTION_OUT_OF_ORDER, ACTION_ROLE_INVALID
* duplicates: DUPLICATE_TRANSACTION, DUPLICATE_ALERT

Usage: ``python -m generator.validator <dataset folder>`` writes validation_summary.json/.md
into the folder. When ground_truth.csv is present the findings are reconciled with its
EDGE_CASE rows and evaluation metrics are added; the exit code is 0 only when the findings
are exactly the labelled edge cases (or there are no findings and no ground truth).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
import pandas as pd

WINDOW_START = "2026-01-01T00:00:00"
WINDOW_END = "2026-10-15T23:59:59"
AS_OF = "2026-10-22T00:00:00"
ID_FORMATS = {
    "customer_id": r"CUS-\d{6}", "kyc_id": r"KYC-\d{6}", "account_id": r"ACC-CUS-\d{6}-\d{2}",
    "merchant_id": r"MER-\d{6}", "device_id": r"DEV-\d{6}", "transaction_id": r"TXN-2026-\d{8}",
    "transfer_id": r"TRF-2026-\d{6}", "rule_id": r"RULE-\d{3}", "alert_id": r"ALT-2026-\d{6}",
    "case_id": r"CASE-2026-\d{6}", "action_id": r"ACT-2026-\d{6}", "status_event_id": r"ASH-\d{8}",
    "source_txn_id": r"TXN-2026-\d{8}", "dest_txn_id": r"TXN-2026-\d{8}",
    "source_account_id": r"ACC-CUS-\d{6}-\d{2}", "dest_account_id": r"ACC-CUS-\d{6}-\d{2}",
    "primary_account_id": r"ACC-CUS-\d{6}-\d{2}",
}
TIMESTAMP_RE, DATE_RE = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", r"\d{4}-\d{2}-\d{2}"
DATE_COLUMNS = {"onboarding_date", "verified_date", "next_review_date", "open_date", "close_date",
                "onboarded_date", "effective_from", "effective_to"}
TIMESTAMP_COLUMNS = {"first_registered_at", "first_seen", "last_seen", "event_timestamp", "txn_timestamp",
                     "initiated_at", "created_at", "opened_at", "added_at", "action_timestamp"}
DEBIT_TYPES = {"PURCHASE", "ATM_WITHDRAWAL", "BILL_PAYMENT", "FEE", "TRANSFER_OUT", "ACCOUNT_CLOSURE"}
CHANNELS_BY_TYPE = {
    "PURCHASE": {"CARD_POS", "ECOMMERCE"}, "REFUND": {"CARD_POS", "ECOMMERCE"}, "ATM_WITHDRAWAL": {"ATM"},
    "FEE": {"SYSTEM"}, "INTEREST": {"SYSTEM"}, "SALARY": {"SYSTEM"}, "TRANSFER_IN": {"SYSTEM"},
    "DEPOSIT": {"SYSTEM", "BRANCH"}, "BILL_PAYMENT": {"MOBILE_APP", "ONLINE_BANKING", "BRANCH"},
    "TRANSFER_OUT": {"MOBILE_APP", "ONLINE_BANKING", "BRANCH"}, "ACCOUNT_CLOSURE": {"BRANCH"},
}
MERCHANT_TYPES = {"PURCHASE", "BILL_PAYMENT", "REFUND"}
DIGITAL_CHANNELS = {"MOBILE_APP", "ONLINE_BANKING"}
TRANSITIONS = {
    ("", "ACTIVE"): {"ACCOUNT_OPENED"}, ("ACTIVE", "DORMANT"): {"INACTIVITY_180_DAYS"},
    ("DORMANT", "ACTIVE"): {"REACTIVATED_ONLINE", "REACTIVATED_IN_BRANCH"},
    ("ACTIVE", "CLOSED"): {"CUSTOMER_REQUEST"}, ("DORMANT", "CLOSED"): {"CUSTOMER_REQUEST"},
}
REVIEW_DAYS = {"HIGH": 365, "MEDIUM": 730, "LOW": 1095}


@dataclass(frozen=True, order=True)
class Finding:
    """One validation failure for one record."""

    check: str
    table: str
    record_id: str
    message: str


# --- schema contract --------------------------------------------------------------

@dataclass
class TableContract:
    """What schema.sql declares for one table (read through SQLite PRAGMAs)."""

    columns: list[str]
    types: dict[str, str]
    not_null: list[str]
    primary_key: list[str]
    foreign_keys: list[tuple[list[str], str, list[str]]]
    unique: list[list[str]]
    enums: dict[str, set[str]]


def read_contract(schema_path: Path) -> dict[str, TableContract]:
    """Introspect ``schema.sql`` with SQLite PRAGMAs (tables in creation order)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(Path(schema_path).read_text(encoding="utf-8"))
    out = {}
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY rowid"):
        info = conn.execute(f"PRAGMA table_info({name})").fetchall()
        fks: dict[int, list] = defaultdict(list)
        for r in conn.execute(f"PRAGMA foreign_key_list({name})"):
            fks[r[0]].append(r)
        unique = []
        for idx in conn.execute(f"PRAGMA index_list({name})"):
            if idx[2] and idx[3] == "u":
                unique.append([r[2] for r in conn.execute(f"PRAGMA index_info({idx[1]})")])
        out[name] = TableContract(
            columns=[r[1] for r in info], types={r[1]: r[2] for r in info},
            not_null=[r[1] for r in info if r[3] or r[5]],
            primary_key=[r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])],
            foreign_keys=[([r[3] for r in sorted(g, key=lambda r: r[1])], g[0][2],
                           [r[4] for r in sorted(g, key=lambda r: r[1])]) for _, g in sorted(fks.items())],
            unique=unique,
            enums={c: {v.strip().strip("'") for v in vals.split(",")}
                   for c, vals in re.findall(r"CHECK \((\w+) IN \(([^)]*)\)\)", sql)},
        )
    conn.close()
    return out


def load_tables(csv_dir: Path, contract: dict[str, TableContract]) -> dict[str, pd.DataFrame]:
    """Read every table's CSV as text ('' = NULL)."""
    return {name: pd.read_csv(Path(csv_dir) / f"{name}.csv", dtype=str, keep_default_na=False)
            for name in contract}


# --- typed helpers -----------------------------------------------------------------

def _seconds(series: pd.Series) -> pd.Series:
    """ISO date/timestamp text -> float epoch seconds (NaN for empty)."""
    values = pd.to_datetime(series.where(series != ""), format="ISO8601", errors="coerce")
    return (values - pd.Timestamp("1970-01-01")).dt.total_seconds()


def _cents(series: pd.Series) -> pd.Series:
    """Decimal text -> nullable integer cents (malformed values become NULL; FORMAT_VIOLATION reports them)."""
    numbers = pd.to_numeric(series.where(series != ""), errors="coerce")
    return (numbers * 100).round().astype("Int64")


def _counts(series: pd.Series) -> dict:
    return {str(k): int(v) for k, v in series.value_counts().sort_index().items()}


def _key(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    return frame[columns[0]] if len(columns) == 1 else frame[columns].agg("|".join, axis=1)


def _emit(out: list[Finding], check: str, table: str, ids, messages) -> None:
    out.extend(Finding(check, table, str(i), str(m)) for i, m in zip(ids, messages))


def _iso(seconds: float) -> str:
    return str(np.datetime64(int(seconds), "s"))


# --- schema-driven checks --------------------------------------------------------------

def check_contract(tables: dict[str, pd.DataFrame], contract: dict[str, TableContract]) -> list[Finding]:
    """Keys, required fields, enums, unique constraints, formats and foreign keys."""
    out: list[Finding] = []
    for name, spec in contract.items():
        df = tables[name]
        if list(df.columns) != spec.columns:
            out.append(Finding("FORMAT_VIOLATION", name, "*", f"columns {list(df.columns)} != {spec.columns}"))
            continue
        key = _key(df, spec.primary_key)
        dup = key[key.duplicated(keep=False)].unique()
        _emit(out, "PK_DUPLICATE", name, dup, [f"primary key {k} occurs more than once" for k in dup])
        for col in spec.not_null:
            missing = key[df[col] == ""]
            _emit(out, "REQUIRED_MISSING", name, missing, [f"{col} is empty"] * len(missing))
        for col, allowed in spec.enums.items():
            bad = df[(df[col] != "") & ~df[col].isin(allowed)]
            _emit(out, "ENUM_VIOLATION", name, key[bad.index], [f"{col}={v!r} not allowed" for v in bad[col]])
        for cols in spec.unique:
            present = df[(df[cols] != "").all(axis=1)]
            values = _key(present, cols)
            dup = values[values.duplicated(keep=False)]
            _emit(out, "UNIQUE_VIOLATION", name, key[dup.index], [f"{cols}={v} not unique" for v in dup])
        for col in spec.columns:
            pattern = (ID_FORMATS.get(col) or (TIMESTAMP_RE if col in TIMESTAMP_COLUMNS else None)
                       or (DATE_RE if col in DATE_COLUMNS else None))
            if pattern is None and spec.types[col] in ("REAL", "INTEGER"):
                pattern = r"-?\d+(\.\d+)?" if spec.types[col] == "REAL" else r"-?\d+"
            if pattern:
                values = df[col]
                bad = values[(values != "") & ~values.str.fullmatch(pattern)]
                _emit(out, "FORMAT_VIOLATION", name, key[bad.index], [f"{col}={v!r} malformed" for v in bad])
    for name, spec in contract.items():
        df = tables[name]
        key = _key(df, spec.primary_key)
        for cols, parent, parent_cols in spec.foreign_keys:
            present = df[(df[cols] != "").all(axis=1)]
            parent_keys = set(_key(tables[parent], parent_cols))
            child = _key(present, cols)
            bad = child[~child.isin(parent_keys)]
            _emit(out, "FK_VIOLATION", name, key[bad.index], [f"{cols}={v} not in {parent}" for v in bad])
    return out


# --- business checks ---------------------------------------------------------------

class _Data:
    """Typed views of the text tables used by the business checks."""

    def __init__(self, t: dict[str, pd.DataFrame]):
        self.t = t
        txn = t["transactions"].copy()
        txn["ts"] = _seconds(txn["txn_timestamp"])
        for col in ("amount", "balance_before", "balance_after"):
            txn[col + "_c"] = _cents(txn[col])
        self.txn = txn
        acc = t["accounts"].copy()
        acc["opening_c"] = _cents(acc["opening_balance"])
        acc["overdraft_c"] = _cents(acc["overdraft_limit"])
        self.acc = acc.set_index("account_id")
        hist = t["account_status_history"].copy()
        hist["ts"] = _seconds(hist["event_timestamp"])
        self.hist = hist.sort_values(["account_id", "ts", "status_event_id"], kind="mergesort")
        alerts = t["alerts"].copy()
        alerts["created"] = _seconds(alerts["created_at"])
        self.alerts = alerts


def check_ledger(d: _Data) -> list[Finding]:
    """Per-account running balance and overdraft limits."""
    txn = d.txn.sort_values(["account_id", "ts", "transaction_id"], kind="mergesort")
    opening = txn["account_id"].map(d.acc["opening_c"])
    prev = txn.groupby("account_id")["balance_after_c"].shift().fillna(opening)
    sign = np.where(txn["direction"] == "CREDIT", 1, -1)
    limit = txn["account_id"].map(d.acc["overdraft_c"])
    conditions = [mask.fillna(False).to_numpy(dtype=bool) for mask in (
        txn["balance_after_c"] != txn["balance_before_c"] + sign * txn["amount_c"],
        txn["balance_before_c"] != prev,
        limit.isna() & (txn["balance_after_c"] < 0),
        limit.notna() & (txn["balance_after_c"] < -limit.fillna(0)),
    )]
    codes = np.select(conditions, ["BALANCE_ARITHMETIC", "BALANCE_CHAIN_BREAK", "NEGATIVE_BALANCE_NO_OVERDRAFT",
                                   "OVERDRAFT_LIMIT_EXCEEDED"], default="")
    out: list[Finding] = []
    for code, row in zip(codes, txn.itertuples(index=False)):
        if code:
            out.append(Finding(str(code), "transactions", row.transaction_id,
                               f"{row.account_id}: before {row.balance_before} {row.direction} {row.amount} "
                               f"-> after {row.balance_after}"))
    closed = d.hist[d.hist["to_status"] == "CLOSED"].groupby("account_id")["ts"].last()
    ledgers = {acc: group for acc, group in txn[txn["account_id"].isin(closed.index)].groupby("account_id")}
    for acc, close_ts in closed.items():
        ledger = ledgers.get(acc)
        before_close = ledger[ledger["ts"] <= close_ts] if ledger is not None else txn.iloc[:0]
        balance = before_close["balance_after_c"].iloc[-1] if len(before_close) else d.acc.at[acc, "opening_c"]
        if balance != 0:
            out.append(Finding("CLOSED_ACCOUNT_BALANCE_NONZERO", "accounts", acc, f"balance {balance / 100:.2f} at closure"))
    return out


def check_lifecycle(d: _Data) -> list[Finding]:
    """Status-event transitions, derived account/customer/KYC statuses, postings vs. account status."""
    out: list[Finding] = []
    prev_status: dict[str, str] = {}
    prev_ts: dict[str, float] = {}
    for ev in d.hist.itertuples(index=False):
        expected_from = prev_status.get(ev.account_id, "")
        allowed = TRANSITIONS.get((ev.from_status, ev.to_status), set())
        if ev.from_status != expected_from or ev.reason_code not in allowed or ev.ts <= prev_ts.get(ev.account_id, -1e18):
            out.append(Finding("STATUS_TRANSITION_INVALID", "account_status_history", ev.status_event_id,
                               f"{ev.account_id}: {ev.from_status or '(none)'} -> {ev.to_status} ({ev.reason_code}) "
                               f"after {expected_from or '(none)'}"))
        prev_status[ev.account_id], prev_ts[ev.account_id] = ev.to_status, ev.ts

    opened = d.hist[d.hist["reason_code"] == "ACCOUNT_OPENED"].groupby("account_id")["ts"].first()
    closed = d.hist[d.hist["to_status"] == "CLOSED"].groupby("account_id")["ts"].last()
    last = d.hist.groupby("account_id")["to_status"].last()
    acc = d.acc
    onboarding = acc["customer_id"].map(d.t["customers"].set_index("customer_id")["onboarding_date"])
    for acc_id, row in acc.iterrows():
        open_date = _iso(opened[acc_id])[:10] if acc_id in opened else ""
        close_date = _iso(closed[acc_id])[:10] if acc_id in closed else ""
        if (row.account_status != last.get(acc_id) or row.open_date != open_date or row.close_date != close_date):
            out.append(Finding("ACCOUNT_STATUS_MISMATCH", "accounts", acc_id,
                               f"stored {row.account_status}/{row.open_date}/{row.close_date or '-'} vs history "
                               f"{last.get(acc_id)}/{open_date}/{close_date or '-'}"))
        if acc_id[4:14] != row.customer_id or int(acc_id[-2:]) != int(row.account_seq) or row.open_date < onboarding[acc_id]:
            out.append(Finding("ACCOUNT_ATTRIBUTE_INVALID", "accounts", acc_id, "id/sequence/onboarding inconsistent"))

    statuses = acc.groupby("customer_id")["account_status"].agg(list)
    for cust in d.t["customers"].itertuples(index=False):
        s = statuses.get(cust.customer_id, [])
        expected = "ACTIVE" if "ACTIVE" in s else ("DORMANT" if "DORMANT" in s else "CLOSED")
        if cust.customer_status != expected:
            out.append(Finding("CUSTOMER_STATUS_MISMATCH", "customers", cust.customer_id,
                               f"{cust.customer_status} but accounts imply {expected}"))
    onboard = d.t["customers"].set_index("customer_id")["onboarding_date"]
    for k in d.t["kyc_profiles"].itertuples(index=False):
        expected = "CURRENT" if k.next_review_date >= AS_OF[:10] else "REVIEW_OVERDUE"
        if k.kyc_status != expected:
            out.append(Finding("KYC_STATUS_MISMATCH", "kyc_profiles", k.kyc_id, f"{k.kyc_status} vs {expected}"))
        review = str(np.datetime64(k.verified_date) + np.timedelta64(REVIEW_DAYS.get(k.risk_rating, 0), "D"))
        if k.verified_date < onboard.get(k.customer_id, "") or k.next_review_date != review:
            out.append(Finding("KYC_DATE_INVALID", "kyc_profiles", k.kyc_id, "verification / review dates inconsistent"))

    txn = d.txn.sort_values("ts", kind="mergesort")
    status_at = pd.merge_asof(txn[["transaction_id", "account_id", "ts"]], d.hist[["account_id", "ts", "to_status"]]
                              .sort_values("ts", kind="mergesort"), on="ts", by="account_id", direction="backward")
    window = (_seconds(pd.Series([WINDOW_START, WINDOW_END])).tolist())
    for row in status_at.itertuples(index=False):
        if pd.isna(row.to_status):
            code = "TXN_BEFORE_ACCOUNT_OPEN"
        elif row.to_status == "CLOSED":
            code = "TXN_ON_CLOSED_ACCOUNT"
        elif row.to_status == "DORMANT":
            code = "TXN_ON_INACTIVE_ACCOUNT"
        elif not window[0] <= row.ts <= window[1]:
            code = "TXN_OUTSIDE_WINDOW"
        else:
            continue
        out.append(Finding(code, "transactions", row.transaction_id, f"{row.account_id} at {_iso(row.ts)}"))
    return out


def check_transaction_attributes(d: _Data) -> list[Finding]:
    """Direction/type, channel, merchant and device rules; strictly increasing timestamps per account."""
    out: list[Finding] = []
    txn = d.txn.sort_values(["account_id", "ts", "transaction_id"], kind="mergesort")
    merchants = d.t["merchants"].set_index("merchant_id")
    onboarded = _seconds(merchants["onboarded_date"])
    links = d.t["account_devices"].assign(first=lambda f: _seconds(f["first_seen"]), last=lambda f: _seconds(f["last_seen"]))
    link = {(r.account_id, r.device_id): (r.first, r.last) for r in links.itertuples(index=False)}
    device_type = d.t["devices"].set_index("device_id")["device_type"]
    previous_ts = txn.groupby("account_id")["ts"].shift()
    for row, prev in zip(txn.itertuples(index=False), previous_ts):
        if (row.direction == "DEBIT") != (row.txn_type in DEBIT_TYPES):
            code = "TXN_TYPE_INVALID"
        elif row.channel not in CHANNELS_BY_TYPE.get(row.txn_type, set()):
            code = "TXN_CHANNEL_INVALID"
        elif (row.merchant_id != "") != (row.txn_type in MERCHANT_TYPES) or (
                row.merchant_id and onboarded.get(row.merchant_id, np.inf) > row.ts):
            code = "TXN_MERCHANT_INVALID"
        elif (row.device_id != "") != (row.channel in DIGITAL_CHANNELS) or (row.device_id and not (
                (row.account_id, row.device_id) in link
                and link[(row.account_id, row.device_id)][0] <= row.ts <= link[(row.account_id, row.device_id)][1]
                and (device_type.get(row.device_id) == "DESKTOP_WEB") == (row.channel == "ONLINE_BANKING"))):
            code = "TXN_DEVICE_INVALID"
        elif not pd.isna(prev) and row.ts <= prev:
            code = "TXN_TIMESTAMP_NOT_INCREASING"
        else:
            continue
        out.append(Finding(code, "transactions", row.transaction_id,
                           f"{row.txn_type}/{row.direction} via {row.channel} merchant={row.merchant_id or '-'} "
                           f"device={row.device_id or '-'}"))
    device_ts = d.txn[d.txn["device_id"] != ""].groupby(["account_id", "device_id"])["ts"].max()
    registered = _seconds(d.t["devices"].set_index("device_id")["first_registered_at"])
    for r in links.itertuples(index=False):
        expected_last = max(r.first, device_ts.get((r.account_id, r.device_id), r.first))
        if r.first < registered.get(r.device_id, np.inf) or r.last != expected_last:
            out.append(Finding("ACCOUNT_DEVICE_INVALID", "account_devices", f"{r.account_id}|{r.device_id}",
                               f"first_seen {r.first_seen}, last_seen {r.last_seen}, expected last {_iso(expected_last)}"))
    return out


def check_transfers(d: _Data) -> list[Finding]:
    """Both legs exist with the right type, account, order and amount."""
    out: list[Finding] = []
    txn = d.txn.set_index("transaction_id")
    owner = d.acc["customer_id"]
    links = d.t["transfer_links"]
    initiated = _seconds(links["initiated_at"])
    amounts = _cents(links["amount"])
    for row, start, amount in zip(links.itertuples(index=False), initiated, amounts):
        src = txn.loc[row.source_txn_id] if row.source_txn_id in txn.index else None
        dst = txn.loc[row.dest_txn_id] if row.dest_txn_id in txn.index else None
        scope = "OWN_ACCOUNTS" if owner.get(row.source_account_id) == owner.get(row.dest_account_id) else "THIRD_PARTY"
        structural = (src is None or dst is None or src.txn_type != "TRANSFER_OUT" or dst.txn_type != "TRANSFER_IN"
                      or src.account_id != row.source_account_id or dst.account_id != row.dest_account_id
                      or dst.ts < src.ts or start != src.ts or row.transfer_scope != scope)
        if structural:
            out.append(Finding("TRANSFER_LEG_INVALID", "transfer_links", row.transfer_id, "legs missing or inconsistent"))
        elif not amount == src.amount_c == dst.amount_c:
            out.append(Finding("TRANSFER_LEG_MISMATCH", "transfer_links", row.transfer_id,
                               f"link {row.amount}, debit leg {src.amount}, credit leg {dst.amount}"))
    linked = set(links["source_txn_id"]) | set(links["dest_txn_id"])
    legs = d.txn[d.txn["txn_type"].isin(["TRANSFER_OUT", "TRANSFER_IN"]) & ~d.txn["transaction_id"].isin(linked)]
    _emit(out, "TRANSFER_LEG_ORPHAN", "transactions", legs["transaction_id"], ["transfer leg without link"] * len(legs))
    return out


def _formula(rule: pd.Series, metric: str) -> Decimal:
    th, mid, w, cap = (Decimal(rule[c]) for c in ("threshold_value", "midpoint_value", "weight", "score_ceiling"))
    steps = max(Decimal(0), (Decimal(metric) - th) / (mid - th))
    return (w + (cap - w) * (1 - Decimal("0.5") ** steps)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def check_alerts(d: _Data) -> list[Finding]:
    """Triggers, rule consistency, score arithmetic, ranking, derived status and duplicates."""
    out: list[Finding] = []
    alerts = d.alerts
    rules = d.t["risk_rules"].set_index("rule_id")
    bridge = d.t["alert_transactions"].merge(d.txn[["transaction_id", "amount_c", "ts"]], on="transaction_id", how="left")
    per_alert = bridge.groupby("alert_id").agg(
        trigger_count=("transaction_id", "size"), amount_sum=("amount_c", "sum"), latest_ts=("ts", "max"),
        primaries=("trigger_role", lambda s: int((s == "PRIMARY").sum())))
    owner = d.acc["customer_id"]
    for row, amount in zip(alerts.itertuples(index=False), _cents(alerts["trigger_amount"])):
        if row.alert_id not in per_alert.index:
            out.append(Finding("ALERT_WITHOUT_TRIGGER", "alerts", row.alert_id, "no alert_transactions rows"))
        else:
            agg = per_alert.loc[row.alert_id]
            if (agg["trigger_count"] != int(row.trigger_txn_count) or agg["amount_sum"] != amount
                    or agg["primaries"] != 1 or row.created <= agg["latest_ts"]):
                out.append(Finding("ALERT_TRIGGER_MISMATCH", "alerts", row.alert_id,
                                   f"{agg['trigger_count']} triggers / {agg['primaries']} primary vs stored "
                                   f"{row.trigger_txn_count}"))
        rule = rules.loc[row.rule_id] if row.rule_id in rules.index else None
        if (rule is None or rule.is_active != "1" or Decimal(row.threshold_value) != Decimal(rule.threshold_value)
                or Decimal(row.metric_value) < Decimal(row.threshold_value) or owner.get(row.account_id) != row.customer_id):
            out.append(Finding("ALERT_RULE_INVALID", "alerts", row.alert_id, "rule, threshold or customer inconsistent"))
        elif _formula(rule, row.metric_value) != Decimal(row.score):
            out.append(Finding("ALERT_SCORE_MISMATCH", "alerts", row.alert_id,
                               f"score {row.score} != formula {_formula(rule, row.metric_value)}"))
    ranked = alerts.assign(score_n=pd.to_numeric(alerts["score"])).sort_values(
        ["score_n", "created", "alert_id"], ascending=[False, True, True], kind="mergesort")
    for expected, row in enumerate(ranked.itertuples(index=False), start=1):
        if int(row.priority_rank) != expected:
            out.append(Finding("ALERT_RANK_INCONSISTENT", "alerts", row.alert_id,
                               f"priority_rank {row.priority_rank}, ranking order gives {expected}"))
    actions = d.t["analyst_actions"]
    disposition = actions[actions["action_type"] == "DISPOSITION"].set_index("alert_id")["disposition"]
    reviewed = set(actions.loc[actions["action_type"] == "REVIEW", "alert_id"])
    for row in alerts.itertuples(index=False):
        expected = disposition.get(row.alert_id, "IN_REVIEW" if row.alert_id in reviewed else "OPEN")
        if row.alert_status != expected:
            out.append(Finding("ALERT_STATUS_MISMATCH", "alerts", row.alert_id, f"{row.alert_status} vs {expected}"))
    dup = alerts[alerts.duplicated(["rule_id", "account_id", "created_at", "metric_value"], keep="first")]
    _emit(out, "DUPLICATE_ALERT", "alerts", dup["alert_id"], ["same rule, account, time and metric"] * len(dup))
    return out


def check_cases_and_actions(d: _Data) -> list[Finding]:
    """Case summaries/statuses and the per-case action timeline."""
    out: list[Finding] = []
    alerts = d.alerts.set_index("alert_id")
    links = d.t["case_alerts"].join(alerts, on="alert_id")
    by_case = {c: g for c, g in links.groupby("case_id")}
    for case in d.t["cases"].itertuples(index=False):
        group = by_case.get(case.case_id)
        if group is None:
            out.append(Finding("CASE_SUMMARY_MISMATCH", "cases", case.case_id, "case without alerts"))
            continue
        statuses = set(group["alert_status"])
        status = "OPEN" if statuses & {"OPEN", "IN_REVIEW"} else ("ESCALATED" if "ESCALATED" in statuses else "CLOSED")
        best = group.assign(rank=group["priority_rank"].astype(int)).sort_values("rank").iloc[0]
        if (int(case.alert_count) != len(group) or Decimal(case.max_score) != max(map(Decimal, group["score"]))
                or case.opened_at != min(group["created_at"]) or (group["customer_id"] != case.customer_id).any()
                or case.primary_account_id != best["account_id"] or (group["added_at"] != group["created_at"]).any()
                or case.case_status != status):
            out.append(Finding("CASE_SUMMARY_MISMATCH", "cases", case.case_id, "derived case fields inconsistent"))

    acts = d.t["analyst_actions"].copy()
    acts["ts"] = _seconds(acts["action_timestamp"])
    acts["seq"] = acts["action_seq"].astype(int)
    opened = _seconds(d.t["cases"].set_index("case_id")["opened_at"])
    as_of = _seconds(pd.Series([AS_OF])).iloc[0]
    for case, group in acts.sort_values(["case_id", "seq"], kind="mergesort").groupby("case_id"):
        sequence_ok = group["seq"].tolist() == list(range(1, len(group) + 1))
        previous, reviewed = None, set()
        for row in group.itertuples(index=False):
            created = alerts["created"].get(row.alert_id, np.nan)
            role_ok = ((row.action_type == "ASSIGN" and row.actor_role == "SYSTEM")
                       or (row.action_type == "REVIEW" and row.actor_role == "OPERATIONS_ANALYST")
                       or (row.action_type == "DISPOSITION"
                           and row.actor_role == ("RISK_REVIEWER" if row.disposition == "CONFIRMED" else "OPERATIONS_ANALYST")))
            if not sequence_ok:
                code = "ACTION_SEQUENCE_INVALID"
            elif row.action_type == "DISPOSITION" and row.ts < created:
                code = "DISPOSITION_BEFORE_ALERT"
            elif row.action_type == "REVIEW" and row.ts < created:
                code = "ACTION_BEFORE_ALERT"
            elif row.ts < opened.get(case, np.inf):
                code = "ACTION_BEFORE_CASE_OPEN"
            elif ((row.seq == 1) != (row.action_type == "ASSIGN") or (previous is not None and row.ts < previous)
                  or row.ts > as_of or (row.action_type == "DISPOSITION" and row.alert_id not in reviewed)):
                code = "ACTION_OUT_OF_ORDER"
            elif not role_ok:
                code = "ACTION_ROLE_INVALID"
            else:
                code = ""
            if code:
                out.append(Finding(code, "analyst_actions", row.action_id,
                                   f"{case} #{row.seq} {row.action_type} at {row.action_timestamp}"))
            if row.action_type == "REVIEW":
                reviewed.add(row.alert_id)
            previous = row.ts
    return out


def check_duplicates(d: _Data) -> list[Finding]:
    """The same posting recorded twice under different ids."""
    key = ["account_id", "txn_timestamp", "direction", "txn_type", "amount", "merchant_id", "device_id"]
    dup = d.txn[d.txn.duplicated(key, keep="first")]
    return [Finding("DUPLICATE_TRANSACTION", "transactions", t, "same account, time, type and amount as an earlier id")
            for t in dup["transaction_id"]]


def validate(tables: dict[str, pd.DataFrame], schema_path: Path) -> list[Finding]:
    """Run every check; return the sorted findings."""
    contract = read_contract(schema_path)
    findings = check_contract(tables, contract)
    if any(f.record_id == "*" for f in findings):
        return sorted(findings)
    # Duplicate keys are already reported; business checks see the first occurrence of each key.
    data = _Data({name: frame.drop_duplicates(subset=contract[name].primary_key, keep="first")
                  for name, frame in tables.items()})
    for check in (check_ledger, check_lifecycle, check_transaction_attributes, check_transfers, check_alerts,
                  check_cases_and_actions, check_duplicates):
        findings += check(data)
    return sorted(findings)


# --- reconciliation, metrics and reporting -----------------------------------------------

def reconcile(findings: list[Finding], ground_truth: pd.DataFrame) -> dict:
    """Compare findings with the EDGE_CASE rows of the ground truth."""
    expected = {(r.issue_or_pattern_type, r.entity_id)
                for r in ground_truth[ground_truth["category"] == "EDGE_CASE"].itertuples(index=False)}
    flagged = {(f.check, f.record_id) for f in findings}
    return {
        "expected_edge_cases": len(expected),
        "flagged_findings": len(findings),
        "matched": len(expected & flagged),
        "missing": sorted(f"{c}:{i}" for c, i in expected - flagged),
        "unexpected": sorted(f"{c}:{i}" for c, i in flagged - expected),
        "exact_match": expected == flagged and len(findings) == len(flagged),
    }


def metrics(tables: dict[str, pd.DataFrame], ground_truth: pd.DataFrame | None) -> dict:
    """Measurable outputs: volumes, alert mix, detection, false-positive rates, disposition quality."""
    alerts = tables["alerts"].assign(score_n=lambda f: pd.to_numeric(f["score"]))
    actions = tables["analyst_actions"]
    dispo = actions[actions["action_type"] == "DISPOSITION"].set_index("alert_id")["disposition"]
    def rate(flags: pd.Series) -> float | None:
        return round(float(flags.mean()), 4) if len(flags) else None

    result: dict = {
        "row_counts": {name: len(frame) for name, frame in tables.items()},
        "alerts_by_rule": _counts(alerts["rule_id"]),
        "alert_score": {stat: round(float(getattr(alerts["score_n"], stat)()), 2) for stat in ("min", "median", "max")},
        "alert_status": _counts(alerts["alert_status"]),
        "case_status": _counts(tables["cases"]["case_status"]),
        "disposition_false_positive_rate_by_rule": {
            rule: rate(dispo.reindex(g["alert_id"]).dropna() == "FALSE_POSITIVE")
            for rule, g in alerts.groupby("rule_id")},
    }
    if ground_truth is None:
        return result
    patterns = ground_truth[ground_truth["category"] != "EDGE_CASE"]
    hits = set(zip(alerts["rule_id"], alerts["account_id"]))
    detection = {}
    for (pattern, label), g in patterns.groupby(["issue_or_pattern_type", "expected_label"]):
        found = sum((r, a) in hits for r, a in zip(g["expected_rule_id"], g["entity_id"]))
        detection[f"{pattern}/{label}"] = {"planted": len(g), "detected": int(found)}
    suspicious = set(zip(patterns.loc[patterns["expected_label"] == "SUSPICIOUS", "expected_rule_id"],
                         patterns.loc[patterns["expected_label"] == "SUSPICIOUS", "entity_id"]))
    truth = ["SUSPICIOUS" if (r, a) in suspicious else "BENIGN" for r, a in zip(alerts["rule_id"], alerts["account_id"])]
    alerts = alerts.assign(truth=truth, disposition=alerts["alert_id"].map(dispo).fillna("NOT_YET_DISPOSITIONED"))
    result["planted_pattern_detection"] = detection
    result["ground_truth_false_positive_rate_by_rule"] = {
        rule: rate(g["truth"] == "BENIGN") for rule, g in alerts.groupby("rule_id")}
    result["disposition_vs_ground_truth"] = {
        f"{t}->{dsp}": int(n) for (t, dsp), n in alerts.groupby(["truth", "disposition"]).size().items()}
    return result


def write_summary(out_dir: Path, findings: list[Finding], reconciliation: dict | None, measures: dict) -> None:
    """Write validation_summary.json and validation_summary.md."""
    by_check = dict(sorted(Counter(f.check for f in findings).items()))
    summary = {
        "validator": "generator.validator (independent: reads CSV files and schema.sql only)",
        "total_findings": len(findings),
        "findings_by_check": by_check,
        "ground_truth_reconciliation": reconciliation,
        "metrics": measures,
        "findings": [asdict(f) for f in findings],
    }
    out_dir = Path(out_dir)
    (out_dir / "validation_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8",
                                                     newline="\n")
    lines = ["# Validation summary", "",
             "Produced by the independent validator (`python -m generator.validator`), which reads only the "
             "exported CSV files and `schema.sql`.", "",
             f"**Total findings:** {len(findings)}", ""]
    if reconciliation is not None:
        verdict = "PASS - findings are exactly the labelled edge cases" if reconciliation["exact_match"] else "FAIL"
        lines += ["## Reconciliation with ground truth", "", f"**{verdict}**", "",
                  "| expected edge cases | findings | matched | missing | unexpected |", "|---|---|---|---|---|",
                  f"| {reconciliation['expected_edge_cases']} | {reconciliation['flagged_findings']} | "
                  f"{reconciliation['matched']} | {len(reconciliation['missing'])} | {len(reconciliation['unexpected'])} |", ""]
    lines += ["## Findings by check", "", "| check | findings |", "|---|---|"]
    lines += [f"| {check} | {n} |" for check, n in by_check.items()] or ["| (none) | 0 |"]
    lines += ["", "## Metrics", ""]
    for name, value in measures.items():
        lines.append(f"### {name.replace('_', ' ')}")
        lines.append("")
        if isinstance(value, dict):
            lines += ["| key | value |", "|---|---|"] + [f"| {k} | {json.dumps(v)} |" for k, v in value.items()]
        else:
            lines.append(str(value))
        lines.append("")
    lines += ["## Findings", "", "| check | table | record | detail |", "|---|---|---|---|"]
    lines += [f"| {f.check} | {f.table} | {f.record_id} | {f.message} |" for f in findings]
    (out_dir / "validation_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def run(data_dir: Path, schema_path: Path | None = None) -> tuple[list[Finding], dict | None]:
    """Validate an exported dataset folder and write its validation summary."""
    data_dir = Path(data_dir)
    schema_path = Path(schema_path) if schema_path else data_dir / "sql" / "schema.sql"
    tables = load_tables(data_dir / "csv", read_contract(schema_path))
    findings = validate(tables, schema_path)
    gt_path = data_dir / "ground_truth.csv"
    truth = pd.read_csv(gt_path, dtype=str, keep_default_na=False) if gt_path.exists() else None
    reconciliation = reconcile(findings, truth) if truth is not None else None
    write_summary(data_dir, findings, reconciliation, metrics(tables, truth))
    return findings, reconciliation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m generator.validator", description=__doc__.split("\n")[0])
    parser.add_argument("data_dir", type=Path, help="dataset folder containing csv/ and sql/schema.sql")
    parser.add_argument("--schema", type=Path, help="schema.sql to validate against (default: <data_dir>/sql/schema.sql)")
    args = parser.parse_args(argv)
    findings, reconciliation = run(args.data_dir, args.schema)
    print(f"findings={len(findings)} by_check={dict(sorted(Counter(f.check for f in findings).items()))}")
    if reconciliation is not None:
        print(f"ground truth: expected={reconciliation['expected_edge_cases']} matched={reconciliation['matched']} "
              f"missing={len(reconciliation['missing'])} unexpected={len(reconciliation['unexpected'])} "
              f"exact_match={reconciliation['exact_match']}")
        return 0 if reconciliation["exact_match"] else 1
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
