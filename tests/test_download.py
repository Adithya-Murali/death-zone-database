"""The extraction contract: which files appear, and under what names."""

from __future__ import annotations

from pathlib import Path

from death_zone import config
from death_zone.etl.download import _normalised_name, _wanted_names, table_path


def test_each_table_yields_a_matched_pair() -> None:
    """A .dbf and a .fpt per table, differing only in extension."""
    names = _wanted_names()
    assert len(names) == len(config.HDB_TABLES) * 2
    for table in config.HDB_TABLES:
        assert f"{table}.dbf" in names
        assert f"{table}.fpt" in names


def test_upstream_case_is_normalised() -> None:
    """Upstream mixes cases; a reader pairing files by name cannot tolerate it."""
    assert _normalised_name(Path("Himalayan Database/HIMDATA/exped.DBF")) == "exped.dbf"
    assert _normalised_name(Path("Himalayan Database/HIMDATA/EXPED.FPT")) == "exped.fpt"


def test_program_files_are_skipped() -> None:
    for member in (
        "Himalayan Database/Himal 2.75.exe",
        "Himalayan Database/HIMDATA/exped.CDX",
        "Himalayan Database/HIMDATA/filters.DBF",
        "Himalayan Database/HIMDATA/SETUP.DBF",
    ):
        assert _normalised_name(Path(member)) == ""


def test_table_path_matches_what_extraction_writes() -> None:
    assert table_path("exped").name == "exped.dbf"
    assert table_path("exped", ".FPT").name == "exped.fpt"
    assert table_path("exped").parent == config.INTERIM_DIR / config.HDB_TABLE_SUBDIR
