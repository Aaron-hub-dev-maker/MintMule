"""Stage 6 gate: injected edge cases are present, labelled, single-defect and isolated;
the ground truth is complete and never reaches rule or feature code."""

import ast
from pathlib import Path

import pandas as pd

from generator import schema
from generator.edge_cases import EDGE_TYPES
from generator.ground_truth import COLUMNS
from generator.patterns import SUSPICIOUS
from helpers import assert_table_integrity

GENERATOR_DIR = Path(__file__).resolve().parents[1] / "generator"
# The only modules allowed to know about the ground truth: it is built, orchestrated, exported, and
# compared with the validator's findings there. Feature, rule and disposition code is checked below.
GROUND_TRUTH_AWARE = {"ground_truth.py", "pipeline.py", "exports.py", "validator.py"}
# Inside the validator, the checks themselves must be blind to the ground truth; only the
# reporting functions (reconcile / metrics / run / main) may read it.
VALIDATOR_REPORTING = {"reconcile", "metrics", "run", "main", "write_summary"}
TABLE_OF_ENTITY = {"ACCOUNT": ("accounts", "account_id"), "TRANSACTION": ("transactions", "transaction_id"),
                   "TRANSFER_LINK": ("transfer_links", "transfer_id"), "ALERT": ("alerts", "alert_id"),
                   "ANALYST_ACTION": ("analyst_actions", "action_id")}


def _edge(ds, issue):
    return [c for c in ds.edge_cases if c.issue_type == issue]


def _ledger(t, account):
    led = t["transactions"][t["transactions"]["account_id"] == account]
    return led.sort_values(["txn_timestamp", "transaction_id"])


def _chain_ok(t, account) -> bool:
    led = _ledger(t, account)
    opening = t["accounts"].set_index("account_id").at[account, "opening_balance"]
    previous = [opening] + led["balance_after"].tolist()[:-1]
    sign = led["direction"].map({"CREDIT": 1, "DEBIT": -1})
    return (led["balance_before"].tolist() == previous
            and (led["balance_after"] == led["balance_before"] + sign * led["amount"]).all())


def test_each_injected_case_is_present_and_labelled(dataset):
    cfg = dataset.cfg
    for issue in EDGE_TYPES:
        assert len(_edge(dataset, issue)) == cfg.edge_cases, issue
    gt = dataset.ground_truth
    labelled = gt[gt["category"] == "EDGE_CASE"]
    assert sorted(zip(labelled["issue_or_pattern_type"], labelled["entity_type"], labelled["entity_id"])) == \
        sorted((c.issue_type, c.entity_type, c.entity_id) for c in dataset.edge_cases)
    assert (labelled["expected_label"] == "INVALID").all()


def test_injected_rows_have_exactly_their_defect(dataset):
    t = dataset.tables
    txn = t["transactions"].set_index("transaction_id")
    accounts = t["accounts"].set_index("account_id")
    hist = t["account_status_history"]
    closed_at = hist[hist["to_status"] == "CLOSED"].set_index("account_id")["event_timestamp"]
    for case in _edge(dataset, "TXN_ON_CLOSED_ACCOUNT"):
        row = txn.loc[case.entity_id]
        assert accounts.at[row.account_id, "account_status"] == "CLOSED"
        assert row.txn_timestamp > closed_at[row.account_id]
        assert row.balance_after >= 0 and _chain_ok(t, row.account_id)
    for case in _edge(dataset, "NEGATIVE_BALANCE_NO_OVERDRAFT"):
        row = txn.loc[case.entity_id]
        assert row.balance_after < 0 and pd.isna(accounts.at[row.account_id, "overdraft_limit"])
        assert accounts.at[row.account_id, "account_status"] == "ACTIVE" and _chain_ok(t, row.account_id)
    links = t["transfer_links"].set_index("transfer_id")
    for case in _edge(dataset, "TRANSFER_LEG_MISMATCH"):
        link = links.loc[case.entity_id]
        debit, credit = txn.loc[link.source_txn_id], txn.loc[link.dest_txn_id]
        assert debit.amount == link.amount and credit.amount < link.amount
        assert debit.balance_after >= 0
        assert _chain_ok(t, debit.account_id) and _chain_ok(t, credit.account_id)
    bridge_alerts = set(t["alert_transactions"]["alert_id"])
    alerts = t["alerts"].set_index("alert_id")
    for case in _edge(dataset, "ALERT_WITHOUT_TRIGGER"):
        assert case.entity_id in alerts.index and case.entity_id not in bridge_alerts
    actions = t["analyst_actions"].set_index("action_id")
    for case in _edge(dataset, "DISPOSITION_BEFORE_ALERT"):
        action = actions.loc[case.entity_id]
        assert action.action_type == "DISPOSITION"
        assert action.action_timestamp < alerts.at[action.alert_id, "created_at"]


def test_nothing_else_was_touched(dataset):
    """Diff every table between the clean snapshot and the injected dataset."""
    clean, final = dataset.clean.tables, dataset.tables
    edge_txn = {c.entity_id for c in dataset.edge_cases if c.entity_type == "TRANSACTION"}
    mismatch = {c.entity_id for c in _edge(dataset, "TRANSFER_LEG_MISMATCH")}
    legs = final["transfer_links"][final["transfer_links"]["transfer_id"].isin(mismatch)]
    allowed_new = {"transactions": edge_txn | set(legs["source_txn_id"]) | set(legs["dest_txn_id"]),
                   "transfer_links": mismatch}
    orphaned = {c.entity_id for c in _edge(dataset, "ALERT_WITHOUT_TRIGGER")}
    moved = {c.entity_id for c in _edge(dataset, "DISPOSITION_BEFORE_ALERT")}
    for name in schema.table_names():
        pk = list(schema.table_specs()[name].primary_key)
        before, after = clean[name].set_index(pk), final[name].set_index(pk)
        added, removed = after.index.difference(before.index), before.index.difference(after.index)
        assert set(map(str, added)) == set(map(str, allowed_new.get(name, set()))), name
        if name == "alert_transactions":
            assert {alert for alert, _ in removed} == orphaned
            assert len(removed) == clean[name]["alert_id"].isin(orphaned).sum()
        else:
            assert removed.empty, name
        common = before.index.intersection(after.index)
        changed = (before.loc[common].astype(object) != after.loc[common].astype(object)) & \
            ~(before.loc[common].isna() & after.loc[common].isna())
        changed_rows = set(common[changed.any(axis=1)])
        if name == "analyst_actions":
            assert changed_rows == moved
            assert changed.loc[list(moved)].sum().to_dict() == {
                c: (len(moved) if c == "action_timestamp" else 0) for c in before.columns}
        else:
            assert not changed_rows, f"{name}: untargeted rows changed: {sorted(changed_rows)[:3]}"


def test_final_counts_are_exact(dataset):
    cfg, t = dataset.cfg, dataset.tables
    assert len(t["customers"]) == cfg.customers
    assert len(t["accounts"]) == cfg.accounts
    assert len(t["transactions"]) == cfg.transactions
    assert len(t["alerts"]) == cfg.alerts


def test_foreign_keys_and_enums_still_hold_after_injection(dataset):
    assert_table_integrity(dataset.tables, schema.table_names())


def test_ground_truth_is_complete_and_resolvable(dataset):
    gt = dataset.ground_truth
    assert list(gt.columns) == COLUMNS
    assert gt["ground_truth_id"].is_unique and gt["notes"].str.len().gt(0).all()
    plants = dataset.clean.plants
    patterns = gt[gt["category"] != "EDGE_CASE"]
    assert len(patterns) == len(plants)
    assert (patterns["category"] == patterns["expected_label"].map(
        {"SUSPICIOUS": "PLANTED_PATTERN", "BENIGN": "FALSE_POSITIVE_PLANT"})).all()
    assert patterns["expected_rule_id"].notna().all()
    assert (gt.loc[gt["category"] == "EDGE_CASE", "expected_rule_id"].isna()).all()
    assert (patterns["expected_label"] == SUSPICIOUS).sum() == sum(p.label == SUSPICIOUS for p in plants)
    for entity_type, group in gt.groupby("entity_type"):
        table, key = TABLE_OF_ENTITY[entity_type]
        assert set(group["entity_id"]) <= set(dataset.tables[table][key]), entity_type
    print(f"\n[{dataset.cfg.profile}] ground truth rows by category: {gt['category'].value_counts().to_dict()}")


def _references_ground_truth(path: Path) -> list[str]:
    return _ground_truth_refs(ast.parse(path.read_text(encoding="utf-8")))


def _ground_truth_refs(tree: ast.AST) -> list[str]:
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [a.name for a in node.names]
            hits += [n for n in names if "ground_truth" in n]
        elif isinstance(node, ast.Import):
            hits += [a.name for a in node.names if "ground_truth" in a.name]
        elif isinstance(node, ast.Name) and "ground_truth" in node.id:
            hits.append(node.id)
        elif isinstance(node, ast.Attribute) and "ground_truth" in node.attr:
            hits.append(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "ground_truth" in node.value:
            hits.append(node.value[:40])
    return hits


def test_ground_truth_is_never_imported_by_rule_or_feature_code():
    modules = {p.name for p in GENERATOR_DIR.glob("*.py")}
    assert GROUND_TRUTH_AWARE <= modules, "allowlist names a module that does not exist"
    checked = sorted(p for p in GENERATOR_DIR.glob("*.py") if p.name not in GROUND_TRUTH_AWARE)
    assert {"rules.py", "cases.py", "transactions.py", "master_data.py", "patterns.py"} <= {p.name for p in checked}
    offenders = {p.name: hits for p in checked if (hits := _references_ground_truth(p))}
    assert not offenders, offenders


def test_validator_checks_are_blind_to_the_ground_truth():
    tree = ast.parse((GENERATOR_DIR / "validator.py").read_text(encoding="utf-8"))
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert {"validate", "check_ledger", "check_lifecycle", "check_transfers", "check_alerts"} <= set(functions)
    offenders = {name: hits for name, node in functions.items()
                 if name not in VALIDATOR_REPORTING and (hits := _ground_truth_refs(node))}
    assert not offenders, offenders


def test_ground_truth_is_not_part_of_any_input_table(dataset):
    gt_only = {"ground_truth_id", "issue_or_pattern_type", "expected_label", "expected_rule_id"}
    for name, spec in schema.table_specs().items():
        assert not gt_only & set(spec.column_names), name
    labels = {"SUSPICIOUS", "BENIGN", "INVALID", "PLANTED_PATTERN", "FALSE_POSITIVE_PLANT", "EDGE_CASE"}
    for name, frame in dataset.tables.items():
        text_columns = frame.select_dtypes(include="object")
        for col in text_columns:
            assert not labels & set(text_columns[col].dropna().astype(str)), f"{name}.{col} carries a label"
