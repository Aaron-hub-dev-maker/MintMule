"""Stage 7 gate: the independent validator finds nothing on clean data and exactly the
injected edge cases on the final data; every check family detects a crafted defect."""

import ast
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from generator import validator
from generator.config import parse_config
from generator.pipeline import build
from generator.schema import SCHEMA_PATH
from generator.tables import to_text_tables

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def starter():
    """Starter-scale dataset: fast enough to validate once per crafted defect."""
    return build(parse_config(["--profile", "starter"]))


@pytest.fixture(scope="module")
def starter_text(starter):
    """Text (CSV-shaped) tables of the clean and injected starter dataset."""
    return to_text_tables(starter.clean.tables), to_text_tables(starter.tables)


def test_validator_on_clean_data_finds_nothing(dataset):
    findings = validator.validate(to_text_tables(dataset.clean.tables), SCHEMA_PATH)
    assert findings == [], findings[:5]


def test_validator_flags_exactly_the_injected_edge_cases(dataset):
    findings = validator.validate(to_text_tables(dataset.tables), SCHEMA_PATH)
    flagged = sorted((f.check, f.record_id) for f in findings)
    expected = sorted((c.issue_type, c.entity_id) for c in dataset.edge_cases)
    assert flagged == expected
    report = validator.reconcile(findings, dataset.ground_truth)
    assert report["exact_match"] and report["missing"] == [] and report["unexpected"] == []
    print(f"\n[{dataset.cfg.profile}] findings by check: {dict(sorted(Counter(f.check for f in findings).items()))}")
    for f in findings[:: max(1, len(findings) // 5)]:
        print(f"[{dataset.cfg.profile}]   {f.check:<30} {f.table:<16} {f.record_id:<22} {f.message[:70]}")


def test_validator_is_independent_of_the_generator():
    tree = ast.parse((PROJECT_ROOT / "generator" / "validator.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    allowed = {"__future__", "argparse", "json", "re", "sqlite3", "sys", "collections", "dataclasses", "decimal",
               "pathlib", "numpy", "pandas"}
    assert imported <= allowed, imported - allowed


def _row(frame, column, value):
    return frame.index[frame[column] == value][0]


def _mutations():
    """(name, mutate(tables) -> (check, record_id)) for one crafted defect per check family."""

    def pk_duplicate(t):
        t["customers"] = t["customers"]._append(t["customers"].iloc[0], ignore_index=True)
        return "PK_DUPLICATE", t["customers"].iloc[0]["customer_id"]

    def required(t):
        t["customers"].loc[3, "display_name"] = ""
        return "REQUIRED_MISSING", t["customers"].loc[3, "customer_id"]

    def enum(t):
        t["transactions"].loc[5, "channel"] = "FAX"
        return "ENUM_VIOLATION", t["transactions"].loc[5, "transaction_id"]

    def fmt(t):
        t["customers"].loc[2, "onboarding_date"] = "2020/01/01"
        return "FORMAT_VIOLATION", t["customers"].loc[2, "customer_id"]

    def fk(t):
        t["alert_transactions"].loc[0, "transaction_id"] = "TXN-2026-99999999"
        row = t["alert_transactions"].loc[0]
        return "FK_VIOLATION", f"{row['alert_id']}|TXN-2026-99999999"

    def arithmetic(t):
        t["transactions"].loc[10, "balance_after"] = "123456.78"
        return "BALANCE_ARITHMETIC", t["transactions"].loc[10, "transaction_id"]

    def duplicate(t):
        copy = t["transactions"].iloc[20].copy()
        copy["transaction_id"] = "TXN-2026-09999999"
        t["transactions"] = t["transactions"]._append(copy, ignore_index=True)
        return "DUPLICATE_TRANSACTION", "TXN-2026-09999999"

    def transition(t):
        h = t["account_status_history"]
        idx = h.index[h["to_status"] == "CLOSED"][0]
        h.loc[idx, "reason_code"] = "INACTIVITY_180_DAYS"
        return "STATUS_TRANSITION_INVALID", h.loc[idx, "status_event_id"]

    def account_status(t):
        acc = t["accounts"]
        idx = acc.index[acc["account_status"] == "ACTIVE"][0]
        acc.loc[idx, "account_status"] = "DORMANT"
        return "ACCOUNT_STATUS_MISMATCH", acc.loc[idx, "account_id"]

    def device(t):
        txn = t["transactions"]
        idx = txn.index[txn["channel"] == "MOBILE_APP"][0]
        txn.loc[idx, "device_id"] = ""
        return "TXN_DEVICE_INVALID", txn.loc[idx, "transaction_id"]

    def transfer(t):
        links = t["transfer_links"]
        links.loc[0, "dest_account_id"] = links.loc[1, "dest_account_id"]
        return "TRANSFER_LEG_INVALID", links.loc[0, "transfer_id"]

    def score(t):
        t["alerts"].loc[0, "score"] = "12.34"
        return "ALERT_SCORE_MISMATCH", t["alerts"].loc[0, "alert_id"]

    def case_summary(t):
        t["cases"].loc[0, "alert_count"] = "99"
        return "CASE_SUMMARY_MISMATCH", t["cases"].loc[0, "case_id"]

    def review_before_alert(t):
        acts = t["analyst_actions"]
        idx = acts.index[acts["action_type"] == "REVIEW"][0]
        acts.loc[idx, "action_timestamp"] = "2026-01-01T00:00:01"
        return "ACTION_BEFORE_ALERT", acts.loc[idx, "action_id"]

    def txn_on_dormant(t):
        acc = t["accounts"]
        dormant = acc.loc[acc["account_status"] == "DORMANT", "account_id"].iloc[0]
        txn = t["transactions"]
        idx = txn.index[txn["txn_type"] == "FEE"][0]
        txn.loc[idx, "account_id"] = dormant
        return "TXN_ON_INACTIVE_ACCOUNT", txn.loc[idx, "transaction_id"]

    return [pk_duplicate, required, enum, fmt, fk, arithmetic, duplicate, transition, account_status, device,
            transfer, score, case_summary, review_before_alert, txn_on_dormant]


@pytest.mark.parametrize("mutate", _mutations(), ids=lambda f: f.__name__)
def test_each_check_family_detects_a_crafted_defect(starter_text, mutate):
    clean, _ = starter_text
    tables = {name: frame.copy() for name, frame in clean.items()}
    check, record = mutate(tables)
    findings = validator.validate(tables, SCHEMA_PATH)
    assert (check, record) in {(f.check, f.record_id) for f in findings}, \
        sorted({(f.check, f.record_id) for f in findings})[:10]


def test_cli_writes_validation_summary(starter_text, starter, tmp_path):
    dataset = starter
    _, final = starter_text
    (tmp_path / "csv").mkdir()
    (tmp_path / "sql").mkdir()
    for name, frame in final.items():
        frame.to_csv(tmp_path / "csv" / f"{name}.csv", index=False, lineterminator="\n")
    dataset.ground_truth.to_csv(tmp_path / "ground_truth.csv", index=False, lineterminator="\n")
    (tmp_path / "sql" / "schema.sql").write_text(SCHEMA_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "generator.validator", str(tmp_path)], cwd=PROJECT_ROOT,
                            capture_output=True, text=True)
    print(f"\n[{dataset.cfg.profile}] validator CLI: {result.stdout.strip()}")
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((tmp_path / "validation_summary.json").read_text(encoding="utf-8"))
    assert summary["ground_truth_reconciliation"]["exact_match"] is True
    assert summary["total_findings"] == len(dataset.edge_cases)
    assert set(summary["metrics"]) >= {"planted_pattern_detection", "ground_truth_false_positive_rate_by_rule",
                                       "disposition_false_positive_rate_by_rule", "disposition_vs_ground_truth"}
    assert "PASS" in (tmp_path / "validation_summary.md").read_text(encoding="utf-8")
