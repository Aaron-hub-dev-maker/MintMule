"""Stage 2: access to the authoritative schema contract (``schema.sql``).

The generator builds every output table with exactly the columns, order and types
declared here, and ``docs/er_diagram.md`` is generated from it. Run
``python -m generator.schema`` to regenerate the ER diagram.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
ER_DIAGRAM_PATH = Path(__file__).resolve().parents[1] / "docs" / "er_diagram.md"

_ENUM_RE = re.compile(r"CHECK \((\w+) IN \(([^)]*)\)\)")


@dataclass(frozen=True)
class Column:
    """One column as declared in schema.sql."""

    name: str
    sql_type: str
    not_null: bool


@dataclass(frozen=True)
class ForeignKey:
    """A (possibly composite) foreign key from ``columns`` to ``parent(parent_columns)``."""

    columns: tuple[str, ...]
    parent: str
    parent_columns: tuple[str, ...]


@dataclass(frozen=True)
class TableSpec:
    """Introspected definition of one table."""

    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKey, ...]
    unique: tuple[tuple[str, ...], ...]
    enums: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def column_names(self) -> list[str]:
        """Column names in declaration order."""
        return [c.name for c in self.columns]


def schema_sql() -> str:
    """Return the schema DDL text."""
    return SCHEMA_PATH.read_text(encoding="utf-8")


def connect_with_schema(path: str = ":memory:") -> sqlite3.Connection:
    """Open a SQLite connection with the schema loaded and foreign keys enforced."""
    conn = sqlite3.connect(path)
    conn.executescript(schema_sql())
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@lru_cache(maxsize=1)
def table_specs() -> dict[str, TableSpec]:
    """Introspect every table (in creation order) via SQLite PRAGMAs."""
    conn = connect_with_schema()
    try:
        rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY rowid").fetchall()
        return {name: _introspect(conn, name, sql) for name, sql in rows}
    finally:
        conn.close()


def _introspect(conn: sqlite3.Connection, table: str, create_sql: str) -> TableSpec:
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns = tuple(Column(name=r[1], sql_type=r[2], not_null=bool(r[3]) or r[5] > 0) for r in info)
    primary_key = tuple(r[1] for r in sorted((r for r in info if r[5] > 0), key=lambda r: r[5]))
    fk_rows: dict[int, list] = {}
    for r in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall():
        fk_rows.setdefault(r[0], []).append(r)
    foreign_keys = tuple(
        ForeignKey(columns=tuple(r[3] for r in sorted(group, key=lambda r: r[1])), parent=group[0][2],
                   parent_columns=tuple(r[4] for r in sorted(group, key=lambda r: r[1])))
        for _, group in sorted(fk_rows.items(), key=lambda kv: -kv[0])
    )
    unique = []
    for idx in conn.execute(f"PRAGMA index_list({table})").fetchall():
        if idx[2] and idx[3] == "u":
            cols = tuple(r[2] for r in conn.execute(f"PRAGMA index_info({idx[1]})").fetchall())
            unique.append(cols)
    enums = {col: tuple(v.strip().strip("'") for v in values.split(","))
             for col, values in _ENUM_RE.findall(create_sql)}
    return TableSpec(table, columns, primary_key, foreign_keys, tuple(sorted(unique)), enums)


def table_names() -> list[str]:
    """Table names in foreign-key-safe load order."""
    return list(table_specs())


def columns(table: str) -> list[str]:
    """Ordered column names of ``table``."""
    return table_specs()[table].column_names


def enum_values(table: str, column: str) -> tuple[str, ...]:
    """Allowed values of an enumerated column."""
    return table_specs()[table].enums[column]


def mermaid_er_diagram() -> str:
    """Render the schema as a Mermaid ``erDiagram`` (markdown document)."""
    specs = table_specs()
    lines = [
        "# Entity-relationship diagram",
        "",
        "Generated from `generator/schema.sql` by `python -m generator.schema`; do not edit by hand.",
        "Crow's-foot notation: `||` exactly one, `|o` / `o|` zero or one, `o{` zero or many.",
        "A `|o` on the parent side marks an optional (nullable) foreign key.",
        "",
        "```mermaid",
        "erDiagram",
    ]
    for spec in specs.values():
        for fk in spec.foreign_keys:
            nullable = any(not _column(spec, c).not_null for c in fk.columns)
            one_to_one = fk.columns in spec.unique
            child_side = "o|" if one_to_one else "o{"
            parent_side = "|o" if nullable else "||"
            label = ", ".join(fk.columns)
            lines.append(f'    {fk.parent} {parent_side}--{child_side} {spec.name} : "{label}"')
    for spec in specs.values():
        lines.append(f"    {spec.name} {{")
        fk_cols = {c for fk in spec.foreign_keys for c in fk.columns}
        for col in spec.columns:
            keys = [k for k, hit in (("PK", col.name in spec.primary_key), ("FK", col.name in fk_cols)) if hit]
            lines.append(f"        {col.sql_type} {col.name}{' ' + ','.join(keys) if keys else ''}")
        lines.append("    }")
    lines += ["```", ""]
    return "\n".join(lines)


def _column(spec: TableSpec, name: str) -> Column:
    return next(c for c in spec.columns if c.name == name)


if __name__ == "__main__":
    ER_DIAGRAM_PATH.parent.mkdir(parents=True, exist_ok=True)
    ER_DIAGRAM_PATH.write_text(mermaid_er_diagram(), encoding="utf-8", newline="\n")
    print(f"wrote {ER_DIAGRAM_PATH}")
