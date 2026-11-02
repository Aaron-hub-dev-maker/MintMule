"""Stage 8 gate: SQL loads into SQLite with matching row counts, JSON matches CSV,
manifest and checksums are complete, sample queries answer the review questions."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from generator import exports, load_sqlite, schema
from generator.config import parse_config
from generator.pipeline import build

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    """Starter dataset exported to a temporary folder."""
    out = tmp_path_factory.mktemp("starter")
    dataset = build(parse_config(["--profile", "starter", "--out", str(out)]))
    reconciliation = exports.export(dataset, out)
    return dataset, out, reconciliation


def _csv(out, name):
    return pd.read_csv(out / "csv" / f"{name}.csv", dtype=str, keep_default_na=False)


def test_expected_files_are_written(exported):
    _, out, reconciliation = exported
    for name in schema.table_names():
        assert (out / "csv" / f"{name}.csv").is_file() and (out / "json" / f"{name}.json").is_file()
    for rel in ("sql/schema.sql", "sql/seed.sql", "ground_truth.csv", "ground_truth.json", "source_manifest.json",
                "validation_summary.json", "validation_summary.md", "checksums.sha256", "docs/data_dictionary.md",
                "docs/er_diagram.md", "docs/risk_rules.md", "docs/sample_queries.sql"):
        assert (out / rel).is_file(), rel
    assert reconciliation["exact_match"]


def test_csv_headers_follow_the_schema(exported):
    _, out, _ = exported
    for name in schema.table_names():
        assert list(_csv(out, name).columns) == schema.columns(name)


def test_sql_loads_into_sqlite_and_row_counts_match_csv(exported, tmp_path):
    _, out, _ = exported
    conn = load_sqlite.load(out, tmp_path / "check.db")
    counts = {}
    for name in schema.table_names():
        counts[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        assert counts[name] == len(_csv(out, name)), name
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert len(triggers) == 4
    print(f"\nSQLite row counts: {counts}")


def test_sql_values_round_trip_to_csv(exported, tmp_path):
    _, out, _ = exported
    conn = load_sqlite.load(out, tmp_path / "values.db")
    for name in ("transactions", "alerts", "analyst_actions", "accounts"):
        cols = schema.columns(name)
        db = pd.read_sql_query(f"SELECT * FROM {name}", conn)
        csv = _csv(out, name)
        for col, spec in zip(cols, schema.table_specs()[name].columns):
            left = db[col].map(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else v)
            right = csv[col]
            if spec.sql_type == "REAL":
                assert (pd.to_numeric(left.replace("", None)).fillna(-1e18).round(4)
                        == pd.to_numeric(right.replace("", None)).fillna(-1e18).round(4)).all(), (name, col)
            else:
                assert (left.astype(str) == right).all(), (name, col)


def test_json_matches_csv(exported):
    _, out, _ = exported
    for name in schema.table_names():
        records = json.loads((out / "json" / f"{name}.json").read_text(encoding="utf-8"))
        csv = _csv(out, name)
        assert len(records) == len(csv), name
        types = {c.name: c.sql_type for c in schema.table_specs()[name].columns}
        for record, row in zip(records, csv.to_dict("records")):
            assert list(record) == list(row), name
            for col, value in row.items():
                if value == "":
                    assert record[col] is None, (name, col)
                elif types[col] == "INTEGER":
                    assert record[col] == int(value), (name, col)
                elif types[col] == "REAL":
                    assert record[col] == float(value), (name, col)
                else:
                    assert record[col] == value, (name, col)
    gt_json = json.loads((out / "ground_truth.json").read_text(encoding="utf-8"))
    gt_csv = pd.read_csv(out / "ground_truth.csv", dtype=str, keep_default_na=False)
    assert [{k: (v if v is not None else "") for k, v in r.items()} for r in gt_json] == gt_csv.to_dict("records")


def test_manifest_declares_synthetic_origin_and_matches_files(exported):
    dataset, out, _ = exported
    manifest = json.loads((out / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["data_origin"] == "synthetic" and manifest["external_sources"] == []
    assert manifest["personal_data"] == "none" and "No real personal data" in manifest["statement"]
    assert manifest["seed"] == dataset.cfg.seed and manifest["generator_version"]
    assert manifest["parameters"]["transactions"] == dataset.cfg.transactions
    for name in schema.table_names():
        assert manifest["row_counts"][name] == len(_csv(out, name))


def test_checksums_cover_and_verify_every_file(exported):
    _, out, _ = exported
    listed = {}
    for line in (out / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        listed[rel] = digest
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p.name != "checksums.sha256"}
    assert set(listed) == files
    for rel, digest in listed.items():
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == digest, rel


def test_stray_files_do_not_change_the_checksums(exported, tmp_path):
    """Re-exporting into a folder that holds unrelated files (e.g. a SQLite db) gives the same checksums."""
    dataset, out, _ = exported
    (tmp_path / "aml_review.db").write_bytes(b"left over from load_sqlite")
    (tmp_path / "notes.txt").write_text("user notes", encoding="utf-8")
    exports.export(dataset, tmp_path)
    assert (tmp_path / "checksums.sha256").read_bytes() == (out / "checksums.sha256").read_bytes()
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "user notes", "unrelated files are kept"


def test_sample_queries_answer_review_questions(exported, tmp_path):
    dataset, out, _ = exported
    conn = load_sqlite.load(out, tmp_path / "queries.db")
    queries = dict(load_sqlite.split_queries((out / "docs" / "sample_queries.sql").read_text(encoding="utf-8")))
    titles = list(queries)
    top = conn.execute(queries[titles[0]]).fetchall()
    assert [r[0] for r in top] == list(range(1, 11))
    fp = conn.execute(queries[titles[1]]).fetchall()
    assert {r[0] for r in fp} <= {"RULE-001", "RULE-002", "RULE-003", "RULE-004", "RULE-005"}
    assert all(0 <= r[4] <= 1 for r in fp)
    explain = conn.execute(queries[titles[2]]).fetchall()
    assert explain and all(r[0] == top[0][1] for r in explain)
    issues = conn.execute(queries[titles[3]]).fetchall()
    assert len(issues) == 2 * dataset.cfg.edge_cases


def test_load_sqlite_cli_prints_query_results(exported):
    _, out, _ = exported
    result = subprocess.run([sys.executable, "-m", "generator.load_sqlite", str(out)], cwd=PROJECT_ROOT,
                            capture_output=True, text=True, check=True)
    assert "== Q1" in result.stdout and "== Q2" in result.stdout and "== Q3" in result.stdout
    print("\n" + "\n".join(result.stdout.splitlines()[:16]))
