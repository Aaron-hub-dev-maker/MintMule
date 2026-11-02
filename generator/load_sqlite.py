"""Load an exported dataset into a SQLite database file and run the sample queries.

Usage: python -m generator.load_sqlite <dataset folder> [--db FILE] [--queries FILE]
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path


def load(data_dir: Path, db_path: Path) -> sqlite3.Connection:
    """(Re)create ``db_path`` from ``<data_dir>/sql/schema.sql`` and ``seed.sql``; fail on FK errors."""
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript((Path(data_dir) / "sql" / "schema.sql").read_text(encoding="utf-8"))
    conn.executescript((Path(data_dir) / "sql" / "seed.sql").read_text(encoding="utf-8"))
    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise RuntimeError(f"foreign key check failed: {problems[:5]}")
    return conn


def split_queries(text: str) -> list[tuple[str, str]]:
    """(title, sql) for each statement ending in ';' at the end of a line; the title is its first '-- Qn' comment."""
    out = []
    for chunk in re.split(r";[ \t]*(?:\r?\n|$)", text):
        lines = [line for line in chunk.strip().splitlines() if line.strip()]
        titles = [line[3:].strip() for line in lines if line.startswith("-- Q")]
        sql = "\n".join(line for line in lines if not line.startswith("--"))
        if sql:
            out.append((titles[0] if titles else "query", sql))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m generator.load_sqlite", description=__doc__.split("\n")[0])
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--db", type=Path, help="database file (default: <data_dir>/aml_review.db)")
    parser.add_argument("--queries", type=Path, help="SQL file (default: <data_dir>/docs/sample_queries.sql)")
    args = parser.parse_args(argv)
    conn = load(args.data_dir, args.db or args.data_dir / "aml_review.db")
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid")]
    print("loaded: " + ", ".join(f"{t}={conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}" for t in tables))
    queries = (args.queries or args.data_dir / "docs" / "sample_queries.sql").read_text(encoding="utf-8")
    for title, sql in split_queries(queries):
        cursor = conn.execute(sql)
        print(f"\n== {title}")
        print(" | ".join(d[0] for d in cursor.description))
        for row in cursor.fetchall():
            print(" | ".join("" if v is None else str(v) for v in row))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
