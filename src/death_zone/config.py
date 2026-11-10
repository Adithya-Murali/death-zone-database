"""Project paths and upstream source definitions.

Everything that names a location or a URL lives here, so the ETL modules stay
free of hardcoded paths and the whole pipeline can be pointed at a scratch
directory by setting DZDB_DATA_DIR.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _data_root() -> Path:
    override = os.environ.get("DZDB_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return PROJECT_ROOT / "data"


DATA_DIR = _data_root()
RAW_DIR = DATA_DIR / "raw"  # the downloaded archive, exactly as fetched
INTERIM_DIR = DATA_DIR / "interim"  # tables unpacked out of the archive

# The warehouse. There is no bronze layer: the extracted .dbf files under
# INTERIM_DIR already are the upstream rows unchanged, re-derivable from a
# checksummed archive in seconds, so a DuckDB copy of them would be a third
# identical copy buying no capability. Silver is the first thing built here.
WAREHOUSE = DATA_DIR / "death_zone.duckdb"

# Gold is exported here as Parquet as well as living in the warehouse. The
# dashboard reads the Parquet: a DuckDB read-write connection takes a file lock
# that shuts out readers entirely, so a build in progress would otherwise break
# the dashboard rather than just serve it stale numbers.
GOLD_DIR = DATA_DIR / "gold"

# --- Primary source: the official Himalayan Database -----------------------
#
# A 43 MB zip holding a Visual FoxPro 9 application plus its data tables. Only
# the tables are of interest; the .exe is Windows-only and never run. Published
# twice a year, so the archive at this URL changes in place -- hence the
# checksum recorded by the download step.
HDB_ARCHIVE_URL = "https://www.himalayandatabase.com/downloads/Himalayan%20Database.zip"
HDB_ARCHIVE_NAME = "himalayan-database.zip"

# Tables worth extracting. The rest of the archive -- 'filters' and 'SETUP',
# the .CDX indexes, the .exe and three DLLs -- is program configuration, not
# data. Row counts as of the Autumn-Winter 2025 release (files dated
# 2026-07-10), for orientation only: exped 11,854, members 94,999, peaks 503.
#
# 'refer' (16,451 literature citations) is deliberately excluded: it is a
# bibliography, and no planned analysis reads it. Add it back here and it
# extracts again -- nothing else needs changing.
HDB_TABLES: tuple[str, ...] = ("exped", "members", "peaks")

# Where the tables sit inside the zip. Nothing reads this -- extraction matches
# on basename so an upstream rename cannot silently change the result -- but it
# records the layout the matching assumes.
HDB_ARCHIVE_TABLE_DIR = "Himalayan Database/HIMDATA"

# Each table is a pair of files: the .dbf records plus a .fpt memo sidecar for
# its variable-length text. A dBASE reader finds the sidecar by name, so both
# are written with the same stem and lower-cased extensions. Upstream is
# inconsistent about case (filters.DBF next to FILTERS.FPT), and on a
# case-sensitive filesystem such as CI's that mismatch breaks the pairing.
TABLE_SUFFIXES: tuple[str, ...] = (".dbf", ".fpt")

# Directory the extracted tables land in, under INTERIM_DIR.
HDB_TABLE_SUBDIR = "himdata"


def ensure_dirs() -> None:
    """Create the data directories if they do not exist."""
    for path in (RAW_DIR, INTERIM_DIR, GOLD_DIR):
        path.mkdir(parents=True, exist_ok=True)
