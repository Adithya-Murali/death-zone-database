"""Record what a built table was built from, so a rerun can skip the work.

Rebuilding from the DBF files takes seconds, so this is not about saving time.
It is so a step can be re-run safely -- by hand or by an orchestrator -- and so
a stale warehouse cannot be mistaken for a current one.

A skip is only correct when two things are both unchanged:

- THE SOURCE: a SHA-256 over every extracted .dbf and .fpt file. Hashing all
  114 MB costs about 47 ms, so there is no reason to use a cheaper proxy such
  as size-and-mtime -- re-extracting rewrites mtimes without the bytes
  changing, which would force pointless rebuilds.
- THE CODE: a SHA-256 of the modules that do the transforming. Unchanged input
  is not sufficient. Editing a cleaning rule has to invalidate the build too,
  or the edit silently does nothing and the warehouse keeps the old rule's
  output.

Both live in a `_dzdb_build` table inside the warehouse, one row per step, so
the record travels with the data rather than in a sidecar file that can be
deleted independently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import duckdb

from death_zone import config

BUILD_TABLE = "_dzdb_build"
CHUNK = 1 << 20


@dataclass(frozen=True)
class BuildRecord:
    """What one step produced, and from what."""

    step: str
    source_fingerprint: str
    code_fingerprint: str
    built_at: str
    row_counts: dict[str, int]

    @property
    def short_source(self) -> str:
        return self.source_fingerprint[:12]


def _sha256_file(path: Path, digest: hashlib._Hash) -> None:
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)


def source_fingerprint() -> str:
    """SHA-256 over the extracted tables, names included.

    Names are hashed alongside the bytes so that renaming or dropping a table
    changes the fingerprint even when the remaining bytes are identical.
    """
    directory = config.INTERIM_DIR / config.HDB_TABLE_SUBDIR
    paths = sorted(p for p in directory.glob("*") if p.is_file())
    if not paths:
        raise FileNotFoundError(f"no extracted tables in {directory}; run `dzdb extract` first")

    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        _sha256_file(path, digest)
    return digest.hexdigest()


def code_fingerprint(*modules: ModuleType) -> str:
    """SHA-256 over the source of the modules that define the transform."""
    digest = hashlib.sha256()
    for module in sorted(modules, key=lambda m: m.__name__):
        source = getattr(module, "__file__", None)
        if source is None:  # namespace package or builtin; nothing to hash
            continue
        digest.update(module.__name__.encode())
        _sha256_file(Path(source), digest)
    return digest.hexdigest()


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {BUILD_TABLE} (
            step               VARCHAR PRIMARY KEY,
            source_fingerprint VARCHAR NOT NULL,
            code_fingerprint   VARCHAR NOT NULL,
            built_at           VARCHAR NOT NULL,
            row_counts         VARCHAR NOT NULL
        )
    """)


def read(con: duckdb.DuckDBPyConnection, step: str) -> BuildRecord | None:
    """The recorded build for one step, or None if it has never run."""
    # fetchall(), not fetchone(): DuckDB 2.0 leaves a partially-consumed result
    # open, and the next execute() on the same connection then raises
    # ResourceInUseException. Every read here is followed by more work.
    tables = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [BUILD_TABLE]
    ).fetchall()
    if not tables:
        return None

    rows = con.execute(
        f"SELECT step, source_fingerprint, code_fingerprint, built_at, row_counts "
        f"FROM {BUILD_TABLE} WHERE step = ?",
        [step],
    ).fetchall()
    if not rows:
        return None
    row = rows[0]
    return BuildRecord(row[0], row[1], row[2], row[3], json.loads(row[4]))


def record(
    con: duckdb.DuckDBPyConnection,
    step: str,
    *,
    modules: tuple[ModuleType, ...],
    row_counts: dict[str, int],
) -> BuildRecord:
    """Write the build record for a step that has just completed."""
    built = BuildRecord(
        step=step,
        source_fingerprint=source_fingerprint(),
        code_fingerprint=code_fingerprint(*modules),
        built_at=datetime.now(UTC).isoformat(timespec="seconds"),
        row_counts=row_counts,
    )
    ensure_table(con)
    con.execute(f"DELETE FROM {BUILD_TABLE} WHERE step = ?", [step])
    con.execute(
        f"INSERT INTO {BUILD_TABLE} VALUES (?, ?, ?, ?, ?)",
        [
            built.step,
            built.source_fingerprint,
            built.code_fingerprint,
            built.built_at,
            json.dumps(built.row_counts),
        ],
    )
    return built


def staleness(
    con: duckdb.DuckDBPyConnection, step: str, *, modules: tuple[ModuleType, ...]
) -> str | None:
    """None when the step's output is current, else why it is not.

    Returning the reason rather than a bool is deliberate: "skipped" and
    "rebuilt because you edited silver.py" are different things to see in a log.
    """
    previous = read(con, step)
    if previous is None:
        return "never built"
    if previous.source_fingerprint != source_fingerprint():
        return "source data changed"
    if previous.code_fingerprint != code_fingerprint(*modules):
        return "transform code changed"
    return None
