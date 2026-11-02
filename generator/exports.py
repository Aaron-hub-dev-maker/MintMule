"""Stage 8: exports.

Writes one dataset folder:

    csv/<table>.csv        every table as CSV (UTF-8, LF, header = schema columns, '' = NULL)
    json/<table>.json      the same rows as JSON records, typed per the schema
    sql/schema.sql         SQLite DDL (copy of generator/schema.sql)
    sql/seed.sql           INSERT statements in foreign-key order
    docs/                  data dictionary, ER diagram, rule documentation, sample queries
    ground_truth.csv/.json evaluation labels (not part of the schema)
    source_manifest.json   provenance: synthetic only, seed, parameters, generator version
    validation_summary.*   independent validator report (see generator.validator)
    checksums.sha256       SHA-256 of every other file

Every file is rendered deterministically (no timestamps, fixed ordering and formatting),
so the same seed and parameters reproduce the folder byte for byte.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from . import GENERATOR_NAME, __version__, schema, validator
from .config import AS_OF, HISTORY_START, WINDOW_END, WINDOW_START
from .tables import to_text_tables

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
EXPORTED_DOCS = ["data_dictionary.md", "er_diagram.md", "risk_rules.md", "sample_queries.sql"]
GENERATED = ["csv", "json", "sql", "docs", "ground_truth.csv", "ground_truth.json", "source_manifest.json",
             "validation_summary.json", "validation_summary.md", "checksums.sha256"]
SQL_BATCH = 500


def export(dataset, out_dir: Path) -> dict:
    """Write the complete dataset folder; return the validator reconciliation."""
    out_dir = Path(out_dir)
    _clear(out_dir)
    text = to_text_tables(dataset.tables)
    for name, frame in text.items():
        _write_csv(frame, out_dir / "csv" / f"{name}.csv")
        _write_json(_typed_records(name, frame), out_dir / "json" / f"{name}.json")
    _write_csv(dataset.ground_truth.fillna(""), out_dir / "ground_truth.csv")
    _write_json(dataset.ground_truth.astype(object).where(dataset.ground_truth.notna(), None).to_dict("records"),
                out_dir / "ground_truth.json")
    (out_dir / "sql").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(schema.SCHEMA_PATH, out_dir / "sql" / "schema.sql")
    _write_text(out_dir / "sql" / "seed.sql", seed_sql(text))
    (out_dir / "docs").mkdir(parents=True, exist_ok=True)
    for doc in EXPORTED_DOCS:
        shutil.copyfile(DOCS_DIR / doc, out_dir / "docs" / doc)
    _write_text(out_dir / "source_manifest.json", json.dumps(manifest(dataset, text), indent=2) + "\n")
    _, reconciliation = validator.run(out_dir)
    write_checksums(out_dir)
    return reconciliation


def _clear(out_dir: Path) -> None:
    """Remove only the files and folders this module generates."""
    for name in GENERATED:
        target = out_dir / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n", encoding="utf-8")


def _write_json(records: list[dict], path: Path) -> None:
    body = ",\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    _write_text(path, f"[\n{body}\n]\n" if records else "[]\n")


def _typed_records(table: str, frame: pd.DataFrame) -> list[dict]:
    """Rows as JSON-ready dicts: INTEGER -> int, REAL -> float, TEXT -> str, '' -> null."""
    casts = {c.name: {"INTEGER": int, "REAL": float}.get(c.sql_type, str) for c in schema.table_specs()[table].columns}
    return [{col: (casts[col](value) if value != "" else None) for col, value in row.items()}
            for row in frame.to_dict("records")]


def _sql_literal(value: str, sql_type: str) -> str:
    if value == "":
        return "NULL"
    if sql_type in ("INTEGER", "REAL"):
        return value
    return "'" + value.replace("'", "''") + "'"


def seed_sql(text: dict[str, pd.DataFrame]) -> str:
    """INSERT statements for every table in load order, wrapped in one transaction."""
    lines = ["-- Synthetic AML review dataset: seed data (load after schema.sql).", "PRAGMA foreign_keys = ON;",
             "BEGIN TRANSACTION;"]
    for name in schema.table_names():
        spec = schema.table_specs()[name]
        frame = text[name]
        types = [c.sql_type for c in spec.columns]
        values = ["(" + ", ".join(_sql_literal(v, t) for v, t in zip(row, types)) + ")"
                  for row in frame.itertuples(index=False, name=None)]
        for start in range(0, len(values), SQL_BATCH):
            lines.append(f"INSERT INTO {name} ({', '.join(spec.column_names)}) VALUES")
            lines.append(",\n".join(values[start:start + SQL_BATCH]) + ";")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def manifest(dataset, text: dict[str, pd.DataFrame]) -> dict:
    """Provenance record: what was generated, how, and from which (synthetic only) sources."""
    cfg = dataset.cfg
    return {
        "generator": GENERATOR_NAME,
        "generator_version": __version__,
        "data_origin": "synthetic",
        "statement": "All records are synthetic, produced by a seeded random generator. No real personal data "
                     "and no external data sources were used; names are built from a fixed syllable list.",
        "external_sources": [],
        "personal_data": "none",
        "seed": cfg.seed,
        "profile": cfg.profile,
        "parameters": {**cfg.scale(), "edge_cases_per_type": cfg.edge_cases},
        "reference_calendar": {"history_start": HISTORY_START, "window_start": WINDOW_START,
                               "window_end": WINDOW_END, "snapshot_as_of": AS_OF},
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
        "row_counts": {name: len(frame) for name, frame in text.items()},
        "ground_truth_rows": {k: int(v) for k, v in dataset.ground_truth["category"].value_counts().sort_index().items()},
        "generated_at": "omitted on purpose: outputs must be byte-identical for the same seed",
    }


def write_checksums(out_dir: Path) -> None:
    """checksums.sha256 in ``sha256sum`` format for every file this module wrote.

    Other files in the folder (e.g. a SQLite database built by load_sqlite) are ignored,
    so they can never change the checksum list.
    """
    owned = [out_dir / name for name in GENERATED if name != "checksums.sha256"]
    files = sorted(f for p in owned for f in ([p] if p.is_file() else p.rglob("*")) if f.is_file())
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out_dir).as_posix()}" for p in files]
    _write_text(out_dir / "checksums.sha256", "\n".join(lines) + "\n")
