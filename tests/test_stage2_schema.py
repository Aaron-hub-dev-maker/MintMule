"""Stage 2 gate: schema loads in SQLite, every entity has a PK, every FK is declared,
and the data dictionary / ER diagram cover the schema exactly."""

import re
import sqlite3
from pathlib import Path

import pytest

from generator import schema

DOCS = Path(__file__).resolve().parents[1] / "docs"

REQUIRED_TABLES = {
    "customers", "kyc_profiles", "accounts", "merchants", "devices", "account_devices",
    "transactions", "transfer_links", "risk_rules", "alerts", "alert_transactions",
    "cases", "case_alerts", "analyst_actions", "account_status_history",
}

# (child table, child columns) -> (parent table, parent columns)
EXPECTED_FKS = {
    ("kyc_profiles", ("customer_id",)): ("customers", ("customer_id",)),
    ("accounts", ("customer_id",)): ("customers", ("customer_id",)),
    ("account_devices", ("account_id",)): ("accounts", ("account_id",)),
    ("account_devices", ("device_id",)): ("devices", ("device_id",)),
    ("account_status_history", ("account_id",)): ("accounts", ("account_id",)),
    ("transactions", ("account_id",)): ("accounts", ("account_id",)),
    ("transactions", ("merchant_id",)): ("merchants", ("merchant_id",)),
    ("transactions", ("device_id",)): ("devices", ("device_id",)),
    ("transactions", ("account_id", "device_id")): ("account_devices", ("account_id", "device_id")),
    ("transfer_links", ("source_txn_id",)): ("transactions", ("transaction_id",)),
    ("transfer_links", ("dest_txn_id",)): ("transactions", ("transaction_id",)),
    ("transfer_links", ("source_account_id",)): ("accounts", ("account_id",)),
    ("transfer_links", ("dest_account_id",)): ("accounts", ("account_id",)),
    ("alerts", ("rule_id",)): ("risk_rules", ("rule_id",)),
    ("alerts", ("account_id",)): ("accounts", ("account_id",)),
    ("alerts", ("customer_id",)): ("customers", ("customer_id",)),
    ("alert_transactions", ("alert_id",)): ("alerts", ("alert_id",)),
    ("alert_transactions", ("transaction_id",)): ("transactions", ("transaction_id",)),
    ("cases", ("customer_id",)): ("customers", ("customer_id",)),
    ("cases", ("primary_account_id",)): ("accounts", ("account_id",)),
    ("case_alerts", ("case_id",)): ("cases", ("case_id",)),
    ("case_alerts", ("alert_id",)): ("alerts", ("alert_id",)),
    ("analyst_actions", ("case_id",)): ("cases", ("case_id",)),
    ("analyst_actions", ("alert_id",)): ("alerts", ("alert_id",)),
    ("analyst_actions", ("case_id", "alert_id")): ("case_alerts", ("case_id", "alert_id")),
}

# *_id columns that are user codes, not references to another table.
NON_FK_ID_COLUMNS = {("analyst_actions", "actor_id")}


@pytest.fixture(scope="module")
def specs():
    return schema.table_specs()


def test_schema_loads_into_sqlite():
    conn = schema.connect_with_schema()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == REQUIRED_TABLES
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_every_entity_has_a_primary_key(specs):
    for name, spec in specs.items():
        assert spec.primary_key, f"{name} has no primary key"


def test_every_expected_foreign_key_is_declared(specs):
    declared = {(t, fk.columns): (fk.parent, fk.parent_columns) for t, s in specs.items() for fk in s.foreign_keys}
    assert declared == EXPECTED_FKS


def test_every_reference_column_is_covered_by_a_foreign_key(specs):
    for name, spec in specs.items():
        fk_cols = {c for fk in spec.foreign_keys for c in fk.columns}
        for col in spec.column_names:
            if col.endswith("_id") and col not in spec.primary_key and (name, col) not in NON_FK_ID_COLUMNS:
                assert col in fk_cols, f"{name}.{col} looks like a reference but has no FK"


def test_foreign_keys_target_a_primary_or_unique_key(specs):
    for name, spec in specs.items():
        for fk in spec.foreign_keys:
            parent = specs[fk.parent]
            assert fk.parent_columns == parent.primary_key or fk.parent_columns in parent.unique, (name, fk)


def test_load_order_puts_parents_first(specs):
    order = list(specs)
    for name, spec in specs.items():
        for fk in spec.foreign_keys:
            assert order.index(fk.parent) < order.index(name), (name, fk.parent)


def test_bridge_tables_have_composite_primary_keys(specs):
    assert specs["account_devices"].primary_key == ("account_id", "device_id")
    assert specs["alert_transactions"].primary_key == ("alert_id", "transaction_id")
    assert specs["case_alerts"].primary_key == ("case_id", "alert_id")


def test_enums_are_introspected(specs):
    assert schema.enum_values("transactions", "direction") == ("DEBIT", "CREDIT")
    assert schema.enum_values("analyst_actions", "disposition") == ("CONFIRMED", "FALSE_POSITIVE", "ESCALATED")
    assert len(specs["merchants"].enums["category"]) == 21


# --- constraint behaviour ---------------------------------------------------------

def _seed_minimal(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO customers VALUES ('CUS-000001','INDIVIDUAL','Kalo Mira',1980,'NL','RETAIL',"
                 "'2020-01-01','ACTIVE')")
    conn.execute("INSERT INTO accounts VALUES ('ACC-CUS-000001-01','CUS-000001',1,'CURRENT','EUR','2020-01-01',"
                 "NULL,NULL,100.0,'ACTIVE')")
    conn.execute("INSERT INTO account_status_history VALUES ('ASH-00000001','ACC-CUS-000001-01',"
                 "'2020-01-01T09:00:00',NULL,'ACTIVE','ACCOUNT_OPENED','SYSTEM')")


def test_check_constraints_reject_bad_rows():
    conn = schema.connect_with_schema()
    _seed_minimal(conn)
    bad_statements = [
        # bad id format
        "INSERT INTO customers VALUES ('CUS-1','INDIVIDUAL','X',1980,'NL','RETAIL','2020-01-01','ACTIVE')",
        # account id does not embed its customer
        "INSERT INTO accounts VALUES ('ACC-CUS-000002-01','CUS-000001',1,'CURRENT','EUR','2020-01-01',"
        "NULL,NULL,0,'ACTIVE')",
        # enum violation
        "INSERT INTO accounts VALUES ('ACC-CUS-000001-02','CUS-000001',2,'LOAN','EUR','2020-01-01',NULL,NULL,0,'ACTIVE')",
        # arithmetic: balance_after must equal balance_before - amount for a debit
        "INSERT INTO transactions VALUES ('TXN-2026-00000001','ACC-CUS-000001-01','2026-01-02T10:00:00','DEBIT',"
        "'PURCHASE','CARD_POS',10.0,'EUR',100.0,95.0,NULL,NULL)",
        # direction/type mismatch
        "INSERT INTO transactions VALUES ('TXN-2026-00000002','ACC-CUS-000001-01','2026-01-02T10:00:00','CREDIT',"
        "'PURCHASE','CARD_POS',10.0,'EUR',100.0,110.0,NULL,NULL)",
        # FK violation: unknown account
        "INSERT INTO transactions VALUES ('TXN-2026-00000003','ACC-CUS-000009-01','2026-01-02T10:00:00','CREDIT',"
        "'DEPOSIT','BRANCH',10.0,'EUR',0,10.0,NULL,NULL)",
    ]
    for stmt in bad_statements:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(stmt)
    conn.execute("INSERT INTO transactions VALUES ('TXN-2026-00000004','ACC-CUS-000001-01','2026-01-02T10:00:00',"
                 "'DEBIT','ATM_WITHDRAWAL','ATM',10.0,'EUR',100.0,90.0,NULL,NULL)")


def test_event_tables_are_append_only():
    conn = schema.connect_with_schema()
    _seed_minimal(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE account_status_history SET to_status='CLOSED'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM account_status_history")


# --- documentation coverage -------------------------------------------------------

def _dictionary_sections() -> dict[str, list[list[str]]]:
    """Parse '## <table>' sections of the data dictionary into their field-table rows."""
    sections: dict[str, list[list[str]]] = {}
    current = None
    for line in (DOCS / "data_dictionary.md").read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^## (\w+)", line)
        if heading:
            current = heading.group(1)
            sections[current] = []
        elif current and line.startswith("| ") and not line.startswith("| Field") and not line.startswith("|---"):
            sections[current].append([cell.strip() for cell in line.strip("|").split("|")])
    return sections


def test_data_dictionary_covers_every_field_of_every_table(specs):
    sections = _dictionary_sections()
    for name, spec in specs.items():
        assert name in sections, f"data dictionary has no section for {name}"
        rows = {r[0]: r for r in sections[name]}
        assert list(rows) == spec.column_names, f"{name}: dictionary fields differ from schema"
        for col in spec.columns:
            field, sql_type, required = rows[col.name][:3]
            assert sql_type == col.sql_type, (name, col.name)
            assert required == ("yes" if col.not_null else "no"), (name, col.name)
            assert all(cell for cell in rows[col.name]), f"{name}.{col.name} has an empty cell"


def test_data_dictionary_documents_relationships_dates_and_lifecycle(specs):
    text = (DOCS / "data_dictionary.md").read_text(encoding="utf-8")
    for name in specs:
        block = text.split(f"## {name}\n", 1)[1].split("\n## ", 1)[0]
        for keyword in ("Primary key", "Cardinality", "Effective dates", "Lifecycle"):
            assert keyword in block, f"{name} section lacks '{keyword}'"


def test_er_diagram_is_generated_from_schema_and_up_to_date(specs):
    text = (DOCS / "er_diagram.md").read_text(encoding="utf-8")
    assert text == schema.mermaid_er_diagram(), "run: python -m generator.schema"
    assert "```mermaid\nerDiagram" in text
    for name, spec in specs.items():
        assert f"    {name} {{" in text
        for fk in spec.foreign_keys:
            assert re.search(rf"{fk.parent} \S+--\S+ {name} : \"{', '.join(fk.columns)}\"", text), (name, fk)
