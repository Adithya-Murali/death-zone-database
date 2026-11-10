"""Silver layer: typed, decoded, keyed, and narrowed.

Built straight from the extracted .dbf files -- there is no bronze layer,
because those files already are the upstream rows unchanged and are
re-derivable from a checksummed archive in seconds.

The transform is SQL over registered Arrow tables rather than Python loops:
the work is joins, casts and lookups, which SQL states more clearly and which
DuckDB does in one pass.

What this layer fixes, each verified against the data rather than assumed:

1.  KEYS. Expedition ids repeat every century -- the id embeds a two-digit
    year, so EVER24101 is both 1924 and 2024. Five ids are genuinely reused.
    The natural key is (expedition_id, year), so a single surrogate
    `expedition_id_year` ("EVER24101-2024") carries it in one column: joining
    on one column cannot accidentally become a join on half the key. Members
    deliberately do NOT carry a bare expedition_id -- with it present on both
    sides, `USING (expedition_id)` stays available and yields 95,118 rows
    instead of 94,999, which is 119 phantom rows and no error. Climbers need
    member_id too: (expedition_id, member_id) alone has 36 duplicates.

    silver_peak takes no expedition key. Peaks is the PARENT of expedition
    (exped.PEAKID -> peaks.PEAKID), not a child, so a foreign key pointing the
    other way would invert the relationship. The only peak-to-expedition
    reference upstream is PEXPID, the first-ascent expedition, which is not
    carried into silver.
2.  EMPTY STRING vs NULL. dbfread returns "" for a missing text field and
    never None, so every text column is nullif(trim(...), '').
3.  AMBIGUOUS ZEROS. dbfread returns 0, not None, for an empty numeric. Where
    a companion column disambiguates -- NOHIRED exists precisely so that
    TOTHIRED = 0 can be told from missing, per the Guide -- that is resolved
    into a real NULL. Where zero is impossible (a high point of 0 m) it
    becomes NULL. Where zero is genuinely meaningful (CAMPS = 0 is an alpine
    -style ascent with no high camps) it is kept. SMTDAYS and TOTDAYS have no
    companion and no impossible value, so their zeros stay ambiguous: 2,179
    expeditions report 0 days to high point and nothing distinguishes "same
    day" from "not recorded".
4.  TRI-STATE BOOLEANS. Only four logical columns ever carry an unknown, and
    two of them are kept here: COMRTE (2,386 unknown) and STDRTE (2,216).
    They stay nullable, because `WHERE is_commercial_route` would otherwise
    silently discard a fifth of all expeditions. Every other logical column
    is strictly true/false upstream.
5.  OXYGEN. The upstream triple O2USED / O2NONE / O2UNKWN is collapsed to one
    nullable boolean. The two flags are mutually exclusive in all 11,854 rows;
    neither set means unknown (132 expeditions, 4,504 climbers).
6.  CODES. Decoded via codes.py, transcribed from Appendix B. Both the integer
    and the label are kept: the integer is what joins and filters, the label is
    what reads.

Deliberately not here: extracting the altitude and reason clause out of
TERMNOTE. That is a regex with a ~90% hit rate, so it belongs downstream where
a bad pattern does not force a silver rebuild.
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType

import duckdb
import pyarrow as pa

from death_zone import config
from death_zone.etl import codes, load, provenance

log = logging.getLogger(__name__)

STEP = "silver"
TABLES = ("silver_peak", "silver_expedition", "silver_climber")


def _modules() -> tuple[ModuleType, ...]:
    """Modules whose edits must invalidate a built warehouse.

    This module is included via sys.modules rather than left out: the SQL that
    defines every transform lives here, so omitting it means editing a cleaning
    rule does not trigger a rebuild -- the exact silent-staleness failure the
    code fingerprint exists to prevent.
    """
    return (sys.modules[__name__], codes, load, config)


_CODE_TABLE = """
CREATE OR REPLACE TABLE silver_code (
    domain VARCHAR NOT NULL,
    code   INTEGER NOT NULL,
    label  VARCHAR NOT NULL,
    PRIMARY KEY (domain, code)
);
INSERT INTO silver_code SELECT * FROM code_rows;

CREATE OR REPLACE MACRO label(d, c) AS (
    SELECT label FROM silver_code WHERE domain = d AND code = c
);
"""

_PEAK = """
CREATE OR REPLACE TABLE silver_peak AS
SELECT
    PEAKID                                AS peak_id,
    nullif(trim(PKNAME), '')              AS name,
    nullif(HEIGHTM, 0)                    AS height_m,
    HIMAL                                 AS himal_code,
    label('himal', HIMAL)                 AS himal,
    REGION                                AS region_code,
    label('region', REGION)               AS region,
    PHOST                                 AS host_code,
    label('peak_host', PHOST)             AS host,
    PSTATUS                               AS status_code,
    label('peak_status', PSTATUS)         AS status,
    OPEN                                  AS is_open,
    TREKKING                              AS is_trekking_peak
FROM raw_peaks;

ALTER TABLE silver_peak ADD PRIMARY KEY (peak_id);
"""

_EXPEDITION = """
CREATE OR REPLACE TABLE silver_expedition AS
SELECT
    -- One column carrying the whole natural key. '-' is safe: no upstream id
    -- contains one, and every id is exactly 9 characters.
    EXPID || '-' || YEAR                        AS expedition_id_year,
    EXPID                                       AS expedition_id,
    CAST(YEAR AS INTEGER)                       AS year,
    PEAKID                                      AS peak_id,
    SEASON                                      AS season_code,
    label('season', SEASON)                     AS season,
    HOST                                        AS host_code,
    label('host', HOST)                         AS host,
    nullif(trim(NATION), '')                    AS nation,
    TERMREASON                                  AS term_reason_code,
    label('term_reason', TERMREASON)            AS term_reason,
    TERMREASON IN {success}                     AS is_success,
    nullif(trim(TERMNOTE), '')                  AS term_note,
    SUCCESS1                                    AS route1_success,
    nullif(trim(ROUTE1), '')                    AS route,
    nullif(HIGHPOINT, 0)                        AS high_point_m,
    BCDATE                                      AS base_camp_date,
    SMTDATE                                     AS summit_date,
    TRY_CAST(
        substr(nullif(trim(SMTTIME), ''), 1, 2) || ':' ||
        substr(nullif(trim(SMTTIME), ''), 3, 2) AS TIME
    )                                           AS summit_time,
    TERMDATE                                    AS termination_date,
    SMTDAYS                                     AS days_to_high_point,
    TOTDAYS                                     AS total_days,
    CAMPS                                       AS high_camps,
    TOTMEMBERS                                  AS members,
    SMTMEMBERS                                  AS members_summited,
    MDEATHS                                     AS member_deaths,
    -- NOHIRED exists so a zero here can be told from missing data.
    CASE WHEN TOTHIRED = 0 AND NOT coalesce(NOHIRED, false) THEN NULL ELSE TOTHIRED END
                                                AS hired,
    SMTHIRED                                    AS hired_summited,
    HDEATHS                                     AS hired_deaths,
    CASE WHEN O2USED THEN true WHEN O2NONE THEN false ELSE NULL END
                                                AS oxygen_used,
    COMRTE                                      AS is_commercial_route,
    STDRTE                                      AS is_standard_route,
    nullif(trim(AGENCY), '')                    AS agency
FROM raw_exped;

ALTER TABLE silver_expedition ADD PRIMARY KEY (expedition_id_year);
"""

_CLIMBER = """
CREATE OR REPLACE TABLE silver_climber AS
SELECT
    -- Matches silver_expedition.expedition_id_year. The bare expedition_id is
    -- intentionally absent: see this module's docstring.
    EXPID || '-' || MYEAR                       AS expedition_id_year,
    CAST(MYEAR AS INTEGER)                      AS year,
    MEMBID                                      AS member_id,
    PEAKID                                      AS peak_id,
    MSEASON                                     AS season_code,
    label('season', MSEASON)                    AS season,
    nullif(trim(FNAME), '')                     AS first_name,
    nullif(trim(LNAME), '')                     AS last_name,
    nullif(trim(SEX), '')                       AS sex,
    nullif(CALCAGE, 0)                          AS age,
    TRY_CAST(nullif(trim(YOB), '') AS INTEGER)  AS year_of_birth,
    nullif(trim(CITIZEN), '')                   AS citizenship,
    nullif(trim(STATUS), '')                    AS role,
    nullif(trim(RESIDENCE), '')                 AS residence,
    nullif(trim(OCCUPATION), '')                AS occupation,
    LEADER                                      AS is_leader,
    HIRED                                       AS is_hired,
    SHERPA                                      AS is_sherpa,
    TIBETAN                                     AS is_tibetan,
    BCONLY                                      AS base_camp_only,
    MSUCCESS                                    AS summited,
    MHIGHPT                                     AS reached_expedition_high_point,
    nullif(MPERHIGHPT, 0)                       AS personal_high_point_m,
    MSMTDATE1                                   AS summit_date,
    MSMTBID                                     AS summit_bid_code,
    label('summit_bid', MSMTBID)                AS summit_bid,
    MSMTTERM                                    AS summit_bid_term_code,
    label('summit_bid_term', MSMTTERM)          AS summit_bid_term,
    CASE WHEN MO2USED THEN true WHEN MO2NONE THEN false ELSE NULL END
                                                AS oxygen_used,
    DEATH                                       AS died,
    DEATHTYPE                                   AS death_type_code,
    label('harm_type', DEATHTYPE)               AS death_type,
    DEATHCLASS                                  AS death_class_code,
    label('death_class', DEATHCLASS)            AS death_class,
    nullif(DEATHHGTM, 0)                        AS death_height_m,
    INJURY                                      AS injured,
    INJURYTYPE                                  AS injury_type_code,
    label('harm_type', INJURYTYPE)              AS injury_type,
    nullif(INJURYHGTM, 0)                       AS injury_height_m
FROM raw_members;

ALTER TABLE silver_climber ADD PRIMARY KEY (expedition_id_year, member_id);
"""

# Referential integrity is checked, not declared. DuckDB accepts composite
# foreign keys in CREATE TABLE but raises NotImplementedException on
# ALTER TABLE ADD FOREIGN KEY, and the tables here are built with
# CREATE TABLE AS SELECT. A check is arguably the better trade anyway: it
# reports how many rows are orphaned and in which direction, where a
# constraint violation only says that one row failed.
_INTEGRITY = (
    (
        "silver_expedition.peak_id -> silver_peak",
        """SELECT count(*) FROM silver_expedition e
           LEFT JOIN silver_peak p USING (peak_id) WHERE p.peak_id IS NULL""",
    ),
    (
        "silver_climber.peak_id -> silver_peak",
        """SELECT count(*) FROM silver_climber m
           LEFT JOIN silver_peak p USING (peak_id) WHERE p.peak_id IS NULL""",
    ),
    (
        "silver_climber.expedition_id_year -> silver_expedition",
        """SELECT count(*) FROM silver_climber m
           LEFT JOIN silver_expedition e USING (expedition_id_year)
           WHERE e.expedition_id_year IS NULL""",
    ),
    (
        # members.peak_id is a denormalised copy of the expedition's peak. It
        # agrees in all 94,999 rows today; this fails the build if a release
        # ever breaks that, since queries treat the two as interchangeable.
        "silver_climber.peak_id agrees with its expedition",
        """SELECT count(*) FROM silver_climber m
           JOIN silver_expedition e USING (expedition_id_year)
           WHERE m.peak_id <> e.peak_id""",
    ),
)


def build(*, force: bool = False) -> dict[str, int]:
    """Build the silver tables, skipping the work when nothing has changed.

    Returns row counts per table. A skip returns the counts recorded by the
    build that is still current, so callers see the same shape either way.
    """
    config.ensure_dirs()
    con = duckdb.connect(config.WAREHOUSE)
    try:
        reason = provenance.staleness(con, STEP, modules=_modules())
        if reason is None and not force:
            previous = provenance.read(con, STEP)
            assert previous is not None  # staleness() returned "never built" otherwise
            log.info("silver is current (source %s); skipping", previous.short_source)
            return previous.row_counts

        log.info("building silver: %s", reason or "forced")
        _register_sources(con)
        con.execute(_CODE_TABLE)
        con.execute(_PEAK)
        con.execute(_EXPEDITION.format(success=tuple(sorted(codes.SUCCESS_TERM_REASONS))))
        con.execute(_CLIMBER)
        _check_integrity(con)

        counts = {
            name: con.execute(f"SELECT count(*) FROM {name}").fetchall()[0][0] for name in TABLES
        }
        provenance.record(con, STEP, modules=_modules(), row_counts=counts)
        for name, n in counts.items():
            log.info("%s: %d rows", name, n)
        return counts
    finally:
        con.close()


def _check_integrity(con: duckdb.DuckDBPyConnection) -> None:
    """Fail the build on an orphaned row rather than let the dashboard find it."""
    broken = []
    for label, sql in _INTEGRITY:
        orphans = con.execute(sql).fetchall()[0][0]
        if orphans:
            broken.append(f"{label}: {orphans} orphaned rows")
        else:
            log.info("integrity ok -- %s", label)
    if broken:
        raise RuntimeError("silver failed referential integrity; " + "; ".join(broken))


def _register_sources(con: duckdb.DuckDBPyConnection) -> None:
    """Expose the DBF tables and the code list to SQL as Arrow tables."""
    for name in config.HDB_TABLES:
        con.register(f"raw_{name}", pa.Table.from_pylist(load.read_table(name)))
    con.register(
        "code_rows",
        pa.table(
            {
                "domain": [r[0] for r in codes.rows()],
                "code": [r[1] for r in codes.rows()],
                "label": [r[2] for r in codes.rows()],
            }
        ),
    )
