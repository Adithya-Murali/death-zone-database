#!/usr/bin/env python3
"""Peek at the warehouse tables between pipeline steps.

Standalone: imports only duckdb, finds the data directory relative to this
file, and does not need the death_zone package. Run it with the project's
interpreter:

    uv run python scripts/show-tables.py
    .venv/bin/python scripts/show-tables.py
    DZDB_DATA_DIR=/tmp/scratch .venv/bin/python scripts/show-tables.py

Silver lives in DuckDB tables; gold is Parquet on disk. Table names are matched
by substring so this keeps working if the silver naming changes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("DZDB_DATA_DIR") or PROJECT_ROOT / "data").expanduser().resolve()
WAREHOUSE = DATA_DIR / "death_zone.duckdb"
GOLD_DIR = DATA_DIR / "gold"

LAYERS = {"1": "silver", "2": "gold"}
# Menu label -> substrings that identify the table, in preference order.
TABLES = {
    "1": ("Expedition", ("expedition", "exped")),
    "2": ("Members", ("climber", "member")),
    "3": ("Peaks", ("peak",)),
}


def ask(prompt: str, valid: set[str]) -> str:
    """Re-prompt until the answer is one of `valid`. Ctrl-D or 'q' exits."""
    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            sys.exit(0)
        if answer in {"q", "quit", "exit"}:
            sys.exit(0)
        if answer in valid:
            return answer
        print(f"  pick one of: {', '.join(sorted(valid))}")


def ask_count() -> int:
    while True:
        try:
            answer = input("How many entries do you want to see? (0 = whole table) ").strip()
        except EOFError:
            sys.exit(0)
        if answer.isdigit():
            return int(answer)
        print("  enter a number")


def silver_sources() -> dict[str, str]:
    """Logical name -> a SQL-quotable source for each silver table found."""
    if not WAREHOUSE.exists():
        return {}
    con = duckdb.connect(WAREHOUSE, read_only=True)
    try:
        names = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_name NOT LIKE '\\_%' ESCAPE '\\' "
                "ORDER BY table_name"
            ).fetchall()
        ]
    finally:
        con.close()
    return {n: f'"{n}"' for n in names}


def gold_sources() -> dict[str, str]:
    if not GOLD_DIR.exists():
        return {}
    return {p.stem: f"read_parquet('{p}')" for p in sorted(GOLD_DIR.glob("*.parquet"))}


def pick_sources(layer: str, choice: str) -> list[tuple[str, str]]:
    """The (name, sql source) pairs the user's table choice resolves to."""
    available = silver_sources() if layer == "silver" else gold_sources()
    if not available:
        where = WAREHOUSE if layer == "silver" else GOLD_DIR
        print(f"\nnothing in {layer} yet -- {where} is empty or missing.")
        return []

    if choice == "4":
        return list(available.items())

    _, needles = TABLES[choice]
    for needle in needles:
        hits = [(n, s) for n, s in available.items() if needle in n.lower()]
        if hits:
            return hits
    label = TABLES[choice][0]
    print(f"\nno {layer} table matching {label!r}. Present: {', '.join(available) or '(none)'}")
    return []


def show(name: str, source: str, limit: int) -> None:
    # Gold is Parquet, so an in-memory connection is enough when there is no
    # warehouse file yet. read_only keeps this from blocking a running build.
    attached = WAREHOUSE.exists()
    con = duckdb.connect(WAREHOUSE if attached else ":memory:", read_only=attached)
    try:
        total = con.execute(f"SELECT count(*) FROM {source}").fetchall()[0][0]
        sql = f"SELECT * FROM {source}"
        if limit:
            sql += f" LIMIT {limit}"
        result = con.execute(sql)
        columns = [d[0] for d in result.description]
        rows = result.fetchall()
    finally:
        con.close()

    print(f"\n=== {name}: {total} rows, {len(columns)} columns (showing {len(rows)}) ===")
    print(" | ".join(columns))
    for row in rows:
        print(" | ".join(_cell(v) for v in row))


def _cell(value: object) -> str:
    """One row must print as one line.

    Memo fields carry embedded CR/LF, so printing them raw turns a single
    record into dozens of lines and the column alignment is lost entirely.
    """
    if value is None:
        return ""
    return " ".join(str(value).split())


def main() -> None:
    layer = LAYERS[ask("Which table do you want to see? 1. Silver 2. Gold\n> ", set(LAYERS))]
    choice = ask(
        "Which tables do you want to see? 1. Expedition 2. Members 3. Peaks 4. All\n> ",
        set(TABLES) | {"4"},
    )
    sources = pick_sources(layer, choice)
    if not sources:
        return
    limit = ask_count()
    for name, source in sources:
        show(name, source, limit)


if __name__ == "__main__":
    main()
