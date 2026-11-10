"""Read the extracted dBASE tables into Python dicts.

Uses dbfread rather than a hand-rolled parser. The archive is Visual FoxPro 9
(dbversion 0xF5), so each table is a fixed-width .dbf plus a .fpt sidecar
holding the variable-length memo text; dbfread locates and decodes the sidecar
itself, so nothing here has to follow memo block pointers.

Two settings are deliberate:

- ENCODING is cp1252, not UTF-8. Climber names carry accents and the upstream
  tables predate UTF-8. Every one of the 123,807 records across the four tables
  decodes clean under strict cp1252, so decode errors are left strict: a future
  release that breaks this should raise, not silently substitute characters.
- Field names stay upper-case as upstream writes them (PEAKID, TERMREASON),
  which is how Appendix B of the Himalayan Database Program Guide names them.
  Renaming belongs in the next layer, where the mapping can be recorded.

Tables are opened lazily (dbfread's load=False). members.dbf is 70 MB and its
95k records do not need to be resident to be iterated.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from dbfread import DBF

from death_zone import config
from death_zone.etl.download import table_path

ENCODING = "cp1252"


def open_table(name: str, *, load: bool = False) -> DBF:
    """Open one extracted table without reading its records.

    Raises FileNotFoundError rather than letting dbfread report a missing file,
    so the message can name the step that produces it.
    """
    if name not in config.HDB_TABLES:
        raise ValueError(f"unknown table {name!r}; expected one of {config.HDB_TABLES}")

    path = table_path(name)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; run `dzdb extract` first")

    return DBF(
        path,
        encoding=ENCODING,
        char_decode_errors="strict",
        load=load,
        # Plain dicts, insertion-ordered since 3.7; dbfread defaults to
        # OrderedDict, which only adds repr noise here.
        recfactory=dict,
    )


def iter_records(name: str) -> Iterator[dict[str, Any]]:
    """Yield one dict per record, upper-case field names to values.

    dbfread skips records flagged deleted; `open_table(name).deleted` iterates
    those separately if they ever matter.
    """
    yield from open_table(name)


def read_table(name: str) -> list[dict[str, Any]]:
    """Read a whole table into memory.

    Fine for peaks, exped and refer. members is 95k records -- prefer
    iter_records unless the whole table is genuinely needed at once.
    """
    return list(iter_records(name))


def field_names(name: str) -> list[str]:
    """Column names in file order, without reading any records."""
    return [field.name for field in open_table(name).fields]
