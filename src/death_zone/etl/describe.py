"""Look at what is on disk between pipeline steps.

Answers three questions without loading a table into a notebook: what exists,
what shape is it, and how much of each column is actually populated. The fill
rates are the useful part -- a column that is 98% blank is a different design
decision from one that is full, and nothing in the schema tells you which.

Reads whatever stage exists: the extracted .dbf files always, and the warehouse
tables once a build step has produced them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb

from death_zone import config
from death_zone.etl import provenance
from death_zone.etl.download import table_path
from death_zone.etl.load import open_table, read_table


@dataclass(frozen=True)
class SourceTable:
    """One extracted DBF table as it sits on disk."""

    name: str
    records: int
    fields: int
    upstream_date: str
    megabytes: float


@dataclass(frozen=True)
class ColumnFill:
    """How much of one column carries a value.

    `true_/false_/none_` are only meaningful for logical columns, where three
    states exist and collapsing them hides unknowns. Everything else reports
    `populated` -- non-blank for text, non-null-and-non-zero for numbers, since
    dbfread returns 0 rather than None for an empty numeric field.
    """

    column: str
    type_code: str
    populated: int
    total: int
    true_: int | None = None
    false_: int | None = None
    none_: int | None = None

    @property
    def percent(self) -> float:
        return 100.0 * self.populated / self.total if self.total else 0.0


def source_tables() -> list[SourceTable]:
    """The extracted tables, or an empty list if extraction has not run."""
    out = []
    for name in config.HDB_TABLES:
        path = table_path(name)
        if not path.exists():
            continue
        dbf = open_table(name)
        memo = path.with_suffix(".fpt")
        size = path.stat().st_size + (memo.stat().st_size if memo.exists() else 0)
        out.append(
            SourceTable(
                name=name,
                records=len(dbf),
                fields=len(dbf.fields),
                upstream_date=str(dbf.date),
                megabytes=size / 1e6,
            )
        )
    return out


def warehouse_tables() -> dict[str, int]:
    """Table name to row count for everything in the warehouse."""
    if not config.WAREHOUSE.exists():
        return {}
    con = duckdb.connect(config.WAREHOUSE, read_only=True)
    try:
        names = [
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        ]
        return {n: con.execute(f'SELECT count(*) FROM "{n}"').fetchall()[0][0] for n in names}
    finally:
        con.close()


def builds() -> list[provenance.BuildRecord]:
    """Build records held in the warehouse, if it exists."""
    if not config.WAREHOUSE.exists():
        return []
    con = duckdb.connect(config.WAREHOUSE, read_only=True)
    try:
        steps = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = ?",
            [provenance.BUILD_TABLE],
        ).fetchall()
        if not steps:
            return []
        names = [r[0] for r in con.execute(f"SELECT step FROM {provenance.BUILD_TABLE}").fetchall()]
        return [r for r in (provenance.read(con, s) for s in names) if r is not None]
    finally:
        con.close()


def columns(name: str) -> list[tuple[str, str, int, int]]:
    """(column, type code, length, decimals) for one source table."""
    return [(f.name, f.type, f.length, f.decimal_count) for f in open_table(name).fields]


def sample(name: str, limit: int = 5) -> list[dict[str, Any]]:
    """The first `limit` records, read lazily so members stays cheap."""
    out = []
    for record in open_table(name):
        out.append(record)
        if len(out) >= limit:
            break
    return out


def fill(name: str) -> list[ColumnFill]:
    """Per-column fill rates, cheapest-last: this reads the whole table."""
    rows = read_table(name)
    types = {f.name: f.type for f in open_table(name).fields}
    total = len(rows)
    out = []
    for column, type_code in types.items():
        if type_code == "L":
            true_ = sum(1 for r in rows if r[column] is True)
            false_ = sum(1 for r in rows if r[column] is False)
            none_ = total - true_ - false_
            out.append(
                ColumnFill(column, type_code, true_, total, true_=true_, false_=false_, none_=none_)
            )
        elif type_code in "CM":
            populated = sum(1 for r in rows if (r[column] or "").strip())
            out.append(ColumnFill(column, type_code, populated, total))
        else:
            # dbfread yields 0 for an empty numeric, so 0 counts as unpopulated.
            populated = sum(1 for r in rows if r[column] not in (None, 0))
            out.append(ColumnFill(column, type_code, populated, total))
    return out
