"""Gold layer: the shapes the dashboard reads, denormalised and exported.

One wide climber-level fact plus three aggregates. At 95k rows the aggregates
buy no speed -- DuckDB answers them in milliseconds either way -- so they exist
to pin down *definitions*. Each one states its population in SQL rather than
leaving the dashboard to re-derive "non-hired climbers on 8000m peaks in the
main season" and get it subtly wrong on one panel out of six.

Three definitions carry the whole layer, and each is a decision rather than a
fact about the data:

WORKER CLASS
    'Hired (Sherpa)', 'Hired (other)', 'Member'. Hired and Sherpa are separate
    axes upstream, not a hierarchy: 3,778 hired workers are not Sherpa (mostly
    Tamang and Rai) and 771 Sherpas climbed as paying members. Collapsing them
    attributes the outcomes of a 17%-non-Sherpa workforce to Sherpas.

SUPPORT LEVEL
    'Unsupported' (hired = 0), 'Supported' (hired > 0), 'Not recorded'
    (3 of every 100 expeditions). Note what upstream counts: TOTHIRED is hired
    personnel who went ABOVE base camp. An "unsupported" expedition here may
    still have employed base-camp staff and porters on the walk in -- it means
    nobody was paid to go onto the mountain, not that nobody was paid.

SUCCESS
    Climber level uses `summited` (upstream MSUCCESS). Expedition level uses
    silver's `is_success`, which covers all three upstream success codes --
    main peak, subpeak/foresummit, and claimed -- per the brief that success
    includes every type.

Deaths are reported as a rate per 1,000 climber-records, never as a count
alongside a percentage, because the populations differ by three orders of
magnitude across peaks and raw counts make Everest look safe.
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType

import duckdb

from death_zone import config
from death_zone.etl import codes, load, provenance, silver

log = logging.getLogger(__name__)

STEP = "gold"
TABLES = ("gold_climb", "gold_worker_risk", "gold_death_cause", "gold_support")


def _modules() -> tuple[ModuleType, ...]:
    """Edits to any of these invalidate a built gold layer."""
    return (sys.modules[__name__], silver, codes, load, config)


# The climber-level join the dashboard aggregates from: one row per person per
# expedition, carrying the expedition and peak attributes with it. peak_name is
# denormalised deliberately -- gold is read far more than written, and every
# panel labels an axis with it.
_CLIMB = """
CREATE OR REPLACE TABLE gold_climb AS
SELECT
    c.expedition_id_year,
    c.member_id,
    c.year,
    e.season,
    e.season_code,
    c.peak_id,
    p.name                                          AS peak_name,
    p.height_m                                      AS peak_height_m,
    p.height_m >= 8000                              AS is_8000er,

    CASE WHEN c.is_hired AND c.is_sherpa THEN 'Hired (Sherpa)'
         WHEN c.is_hired                 THEN 'Hired (other)'
         ELSE 'Member' END                          AS worker_class,
    c.is_hired,
    c.is_sherpa,
    c.is_leader,
    c.role,

    -- From the expedition: what support the whole party had on the mountain.
    CASE WHEN e.hired = 0 THEN 'Unsupported'
         WHEN e.hired > 0 THEN 'Supported'
         ELSE 'Not recorded' END                    AS support_level,
    e.hired                                         AS expedition_hired,
    e.members                                       AS expedition_members,
    e.is_commercial_route,
    e.term_reason,
    e.term_reason_code,
    e.is_success                                    AS expedition_succeeded,
    e.high_point_m                                  AS expedition_high_point_m,
    e.summit_time                                   AS expedition_summit_time,

    c.sex,
    c.age,
    c.citizenship,
    c.oxygen_used,
    c.summited,
    c.base_camp_only,
    c.personal_high_point_m,
    c.summit_date,
    c.died,
    c.death_type,
    c.death_class,
    c.death_height_m,
    c.injured,
    c.injury_type
FROM silver_climber c
JOIN silver_expedition e USING (expedition_id_year)
JOIN silver_peak p ON p.peak_id = c.peak_id;
"""

# Hired-focus panel: outcomes per worker class, by peak and decade.
_WORKER_RISK = """
CREATE OR REPLACE TABLE gold_worker_risk AS
SELECT
    peak_id,
    peak_name,
    is_8000er,
    (year / 10)::INT * 10                                       AS decade,
    season,
    worker_class,
    count(*)                                                    AS climbers,
    sum(summited::INT)                                          AS summits,
    round(100.0 * sum(summited::INT) / count(*), 2)             AS pct_summited,
    sum(died::INT)                                              AS deaths,
    round(1000.0 * sum(died::INT) / count(*), 2)                AS deaths_per_1000,
    sum(injured::INT)                                           AS injuries,
    round(1000.0 * sum(injured::INT) / count(*), 2)             AS injuries_per_1000
FROM gold_climb
GROUP BY ALL;
"""

# Cause and phase of death, hired against member. Kept at a coarser grain than
# gold_climb on purpose: only 91 of 439 peaks record any death at all and 27
# record more than five, so a per-peak-per-year cause chart is mostly empty.
_DEATH_CAUSE = """
CREATE OR REPLACE TABLE gold_death_cause AS
SELECT
    peak_id,
    peak_name,
    is_8000er,
    worker_class,
    death_type,
    death_class,
    count(*)                                        AS deaths,
    round(avg(death_height_m))                      AS mean_death_height_m
FROM gold_climb
WHERE died
GROUP BY ALL;
"""

# Expeditions climbing without paid help above base camp, against those with it.
# Expedition grain, so it counts each party once rather than once per climber.
_SUPPORT = """
CREATE OR REPLACE TABLE gold_support AS
WITH per_expedition AS (
    SELECT
        e.expedition_id_year,
        e.year,
        e.season,
        e.peak_id,
        p.name                                      AS peak_name,
        p.height_m >= 8000                          AS is_8000er,
        CASE WHEN e.hired = 0 THEN 'Unsupported'
             WHEN e.hired > 0 THEN 'Supported'
             ELSE 'Not recorded' END                AS support_level,
        e.is_success,
        e.members,
        e.hired,
        e.high_point_m,
        -- Member outcomes only: hired staff are the support being measured, so
        -- including them in the denominator would compare different jobs.
        count(*) FILTER (WHERE NOT c.is_hired)      AS member_climbers,
        sum((c.summited AND NOT c.is_hired)::INT)   AS member_summits,
        sum((c.died AND NOT c.is_hired)::INT)       AS member_deaths
    FROM silver_expedition e
    JOIN silver_peak p ON p.peak_id = e.peak_id
    LEFT JOIN silver_climber c USING (expedition_id_year)
    GROUP BY ALL
)
SELECT
    peak_id,
    peak_name,
    is_8000er,
    (year / 10)::INT * 10                                               AS decade,
    season,
    support_level,
    count(*)                                                            AS expeditions,
    sum(is_success::INT)                                                AS expeditions_succeeded,
    round(100.0 * sum(is_success::INT) / count(*), 2)                   AS pct_exps_succeeded,
    sum(member_climbers)                                                AS member_climbers,
    sum(member_summits)                                                 AS member_summits,
    round(100.0 * sum(member_summits)
          / nullif(sum(member_climbers), 0), 2)                        AS pct_members_summited,
    sum(member_deaths)                                                  AS member_deaths,
    round(1000.0 * sum(member_deaths)
          / nullif(sum(member_climbers), 0), 2)                        AS member_deaths_per_1000,
    sum(hired)                                                          AS hired_total,
    round(1.0 * sum(hired) / nullif(sum(members), 0), 3)                AS hired_per_member,
    round(avg(high_point_m))                                            AS mean_high_point_m
FROM per_expedition
GROUP BY ALL;
"""


def build(*, force: bool = False) -> dict[str, int]:
    """Build the gold tables and export each to Parquet.

    Runs silver first; that call skips its own work when nothing has changed.
    """
    silver.build(force=force)

    config.ensure_dirs()
    con = duckdb.connect(config.WAREHOUSE)
    try:
        reason = provenance.staleness(con, STEP, modules=_modules())
        if reason is None and not force and _exports_present():
            previous = provenance.read(con, STEP)
            assert previous is not None
            log.info("gold is current (source %s); skipping", previous.short_source)
            return previous.row_counts

        log.info("building gold: %s", reason or "forced")
        for statement in (_CLIMB, _WORKER_RISK, _DEATH_CAUSE, _SUPPORT):
            con.execute(statement)

        counts = {
            name: con.execute(f"SELECT count(*) FROM {name}").fetchall()[0][0] for name in TABLES
        }
        for name in TABLES:
            target = config.GOLD_DIR / f"{name}.parquet"
            con.execute(f"COPY {name} TO '{target}' (FORMAT parquet, COMPRESSION zstd)")
            log.info("%s: %d rows -> %s", name, counts[name], target.name)

        provenance.record(con, STEP, modules=_modules(), row_counts=counts)
        return counts
    finally:
        con.close()


def _exports_present() -> bool:
    """A current build record is not enough if the Parquet files are gone."""
    return all((config.GOLD_DIR / f"{name}.parquet").exists() for name in TABLES)
