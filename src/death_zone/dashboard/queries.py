"""Data access for the dashboard.

Reads the gold Parquet exports, never the warehouse. A DuckDB read-write
connection takes a file lock that shuts out readers entirely, so querying
death_zone.duckdb would make `dzdb gold` and the dashboard unable to run at the
same time. Parquet has no lock.

Aggregation happens here rather than being read from the pre-aggregated gold
tables, because the dashboard needs to switch between yearly, 5-yearly and
decade buckets and the aggregates are fixed at decade grain. gold_climb is
95,000 rows; DuckDB regroups it in milliseconds. gold_worker_risk and friends
remain the canonical definitions for anyone querying by hand.

Rates are percentages of the group's own climber count, computed from summed
numerators and denominators, never as an average of per-group rates: averaging
the rates of Everest and a peak with three climbers weights them equally.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import streamlit as st

from death_zone import config

CLIMB = "gold_climb"
DEATH_CAUSE = "gold_death_cause"
ALL_PEAKS = "All peaks"
WORKER_CLASSES = ("Member", "Hired (Sherpa)", "Hired (other)")


def gold_path(table: str) -> str:
    return str(config.GOLD_DIR / f"{table}.parquet")


def exports_exist() -> bool:
    return (config.GOLD_DIR / f"{CLIMB}.parquet").exists()


@st.cache_resource
def _connection() -> duckdb.DuckDBPyConnection:
    """One connection per process, reused across reruns."""
    return duckdb.connect(":memory:")


def _query(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run SQL on a per-call cursor.

    Streamlit serves every session on a thread of one process, and a DuckDB
    connection refuses concurrent use -- eight threads sharing one connection
    gives one success and seven ResourceInUseException. cursor() is thread-safe.
    """
    return _connection().cursor().execute(sql, params or []).df()


def _bucket(granularity: str) -> str:
    """SQL for the time bucket. Deaths are rare, so a yearly line is mostly
    zeros for every peak but Everest; coarser buckets are the default."""
    if granularity == "Year":
        return "year"
    if granularity == "5 years":
        return "(year / 5)::INT * 5"
    return "(year / 10)::INT * 10"


def _filters(peaks: list[str], years: tuple[int, int], seasons: list[str]) -> tuple[str, list]:
    """Shared WHERE clause. Every query is scoped to the 8000m peaks.

    15 of the 16 eight-thousanders carry climber records, 57,378 of 94,999
    overall. The restriction is applied here rather than per query so no panel
    can quietly include a trekking peak. It does drop Ama Dablam, the second
    busiest peak in the database at 12,782 records.
    """
    where = ["is_8000er", "year BETWEEN ? AND ?"]
    params: list = [years[0], years[1]]
    if seasons:
        where.append(f"season IN ({', '.join('?' * len(seasons))})")
        params += seasons
    if peaks:
        where.append(f"peak_name IN ({', '.join('?' * len(peaks))})")
        params += peaks
    return " AND ".join(where), params


@st.cache_data(show_spinner=False)
def peak_options(min_climbers: int) -> list[str]:
    """Peaks with enough records to chart, busiest first.

    Of 439 peaks with any climber, only 29 have more than 200 records. A
    dropdown over all of them is mostly empty charts.
    """
    df = _query(
        f"""SELECT peak_name, count(*) AS climbers
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE is_8000er
            GROUP BY 1 HAVING count(*) >= ? ORDER BY 2 DESC""",
        [min_climbers],
    )
    return df["peak_name"].tolist()


@st.cache_data(show_spinner=False)
def year_bounds() -> tuple[int, int]:
    df = _query(
        f"SELECT min(year), max(year) FROM read_parquet('{gold_path(CLIMB)}') WHERE is_8000er"
    )
    return int(df.iloc[0, 0]), int(df.iloc[0, 1])


@st.cache_data(show_spinner=False)
def headline(years: tuple[int, int], seasons: list[str], peaks: list[str]) -> pd.DataFrame:
    """One row per worker class: the KPI strip."""
    where, params = _filters(peaks, years, seasons)
    return _query(
        f"""SELECT worker_class,
                   count(*) AS climbers,
                   sum(summited::INT) AS summits,
                   100.0 * sum(summited::INT) / count(*) AS pct_summited,
                   sum(died::INT) AS deaths,
                   100.0 * sum(died::INT) / count(*) AS pct_died
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE {where} GROUP BY 1""",
        params,
    )


@st.cache_data(show_spinner=False)
def by_peak_season(years: tuple[int, int], seasons: list[str], peaks: list[str]) -> pd.DataFrame:
    """Members per peak per season -- the overview bars."""
    where, params = _filters(peaks, years, seasons)
    return _query(
        f"""SELECT peak_name, season,
                   count(*) AS climbers,
                   sum(summited::INT) AS summits,
                   sum(died::INT) AS deaths,
                   100.0 * sum(died::INT) / count(*) AS pct_died,
                   100.0 * sum(summited::INT) / count(*) AS pct_summited
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE {where} AND worker_class = 'Member'
            GROUP BY 1, 2""",
        params,
    )


@st.cache_data(show_spinner=False)
def series_over_time(
    years: tuple[int, int],
    seasons: list[str],
    peaks: list[str],
    granularity: str,
    *,
    worker_class: str | None = None,
    top_n: int = 8,
    include_pooled: bool = True,
) -> pd.DataFrame:
    """Per-peak series, optionally with an 'All peaks' pooled series.

    The pooled row is the SUM over peaks, not the mean of their rates: on a
    deaths-per-1000 chart it is volume-weighted, so it tracks whichever peak
    dominates the period rather than sitting in the middle of the lines.

    `include_pooled=False` drops it, which count charts need: the pooled total
    peaks at 19,809 attempts against 5,077 for the busiest single peak, so it
    squeezes every peak line into the bottom quarter of the axis and the median
    point down to 1.9% of plot height. Rate charts are unaffected -- there the
    pooled line sits inside the range the peaks already span.
    """
    where, params = _filters(peaks, years, seasons)
    if worker_class:
        where += " AND worker_class = ?"
        params = [*params, worker_class]
    bucket = _bucket(granularity)
    path = gold_path(CLIMB)

    chosen = peaks or peak_options(1)[:top_n]
    placeholders = ", ".join("?" * len(chosen))

    # The pooled series only exists in "all peaks" mode. Once peaks are picked
    # the filter already restricts to them, so a pooled line would be their sum
    # -- and with one peak picked, a second line drawn exactly on top of it.
    pooled = (
        ""
        if peaks or not include_pooled
        else f"""UNION ALL
                 SELECT '{ALL_PEAKS}' AS series, {bucket} AS period,
                        count(*) AS climbers, sum(summited::INT) AS summits,
                        sum(died::INT) AS deaths
                 FROM f GROUP BY 1, 2"""
    )

    return _query(
        f"""WITH f AS (SELECT * FROM read_parquet('{path}') WHERE {where}),
                 stacked AS (
                     SELECT peak_name AS series, {bucket} AS period,
                            count(*) AS climbers, sum(summited::INT) AS summits,
                            sum(died::INT) AS deaths
                     FROM f WHERE peak_name IN ({placeholders}) GROUP BY 1, 2
                     {pooled}
                 )
            SELECT series, period, climbers, summits, deaths,
                   100.0 * summits / climbers AS pct_summited,
                   100.0 * deaths / climbers AS pct_died,
                   series = '{ALL_PEAKS}' AS is_pooled
            FROM stacked
            ORDER BY series, period""",
        [*params, *chosen],
    )


@st.cache_data(show_spinner=False)
def worker_over_time(
    years: tuple[int, int], seasons: list[str], peaks: list[str], granularity: str
) -> pd.DataFrame:
    """Outcomes by worker class over time, plus the hired-per-member ratio."""
    where, params = _filters(peaks, years, seasons)
    bucket = _bucket(granularity)
    return _query(
        f"""SELECT {bucket} AS period, worker_class,
                   count(*) AS climbers,
                   sum(summited::INT) AS summits,
                   100.0 * sum(summited::INT) / count(*) AS pct_summited,
                   sum(died::INT) AS deaths,
                   100.0 * sum(died::INT) / count(*) AS pct_died
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE {where} GROUP BY 1, 2 ORDER BY 1, 2""",
        params,
    )


@st.cache_data(show_spinner=False)
def hired_growth(
    years: tuple[int, int], seasons: list[str], peaks: list[str], granularity: str
) -> pd.DataFrame:
    """Hired head-count against member head-count, and the ratio.

    Counted at climber grain from gold_climb, so 'hired' here is hired people
    recorded on expeditions, not the expedition-level TOTHIRED column.
    """
    where, params = _filters(peaks, years, seasons)
    bucket = _bucket(granularity)
    return _query(
        f"""SELECT {bucket} AS period,
                   count(*) FILTER (WHERE is_hired) AS hired,
                   count(*) FILTER (WHERE NOT is_hired) AS members,
                   1.0 * count(*) FILTER (WHERE is_hired)
                       / nullif(count(*) FILTER (WHERE NOT is_hired), 0) AS hired_per_member
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE {where} GROUP BY 1 ORDER BY 1""",
        params,
    )


@st.cache_data(show_spinner=False)
def support_mix(years: tuple[int, int], seasons: list[str], peaks: list[str]) -> pd.DataFrame:
    """Share of climbers by support level, with each level's outcomes.

    Members only. Hired staff are the support being measured, so counting them
    here would put the support inside its own denominator.

    "Not recorded" is kept as its own slice rather than folded into either
    side: 814 climber-records have no hired count upstream, and guessing which
    way they went would move both rates.
    """
    where, params = _filters(peaks, years, seasons)
    return _query(
        f"""SELECT support_level,
                   count(*) AS climbers,
                   count(DISTINCT expedition_id_year) AS expeditions,
                   sum(summited::INT) AS summits,
                   sum(died::INT) AS deaths,
                   100.0 * count(*) / sum(count(*)) OVER () AS pct_climbers,
                   100.0 * sum(summited::INT) / count(*) AS pct_summited,
                   100.0 * sum(died::INT) / count(*) AS pct_died
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE {where} AND NOT is_hired
            GROUP BY 1 ORDER BY 2 DESC""",
        params,
    )


@st.cache_data(show_spinner=False)
def support_mix_over_time(
    years: tuple[int, int], seasons: list[str], peaks: list[str]
) -> pd.DataFrame:
    """The same split per decade, as a share of that decade's climbers.

    Decade grain, not yearly: the single climber recorded in 1913 would read as
    "100% unsupported" on a yearly line and swamp the trend it belongs to.
    """
    where, params = _filters(peaks, years, seasons)
    return _query(
        f"""SELECT ((year / 10)::INT * 10) AS decade, support_level,
                   count(*) AS climbers,
                   100.0 * count(*)
                       / sum(count(*)) OVER (PARTITION BY ((year / 10)::INT * 10))
                       AS pct_climbers
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE {where} AND NOT is_hired
            GROUP BY 1, 2 ORDER BY 1, 2""",
        params,
    )


@st.cache_data(show_spinner=False)
def support_outcomes(
    years: tuple[int, int], seasons: list[str], peaks: list[str], granularity: str
) -> pd.DataFrame:
    """With-help against without-help, members only.

    Hired staff are excluded from the denominator: they are the support being
    measured, and they do a different job with different risk.
    """
    where, params = _filters(peaks, years, seasons)
    bucket = _bucket(granularity)
    return _query(
        f"""SELECT {bucket} AS period, support_level,
                   count(*) AS climbers,
                   sum(summited::INT) AS summits,
                   100.0 * sum(summited::INT) / count(*) AS pct_summited,
                   sum(died::INT) AS deaths,
                   100.0 * sum(died::INT) / count(*) AS pct_died
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE {where} AND NOT is_hired AND support_level <> 'Not recorded'
            GROUP BY 1, 2 ORDER BY 1, 2""",
        params,
    )


@st.cache_data(show_spinner=False)
def failure_scatter(years: tuple[int, int], seasons: list[str], peaks: list[str]) -> pd.DataFrame:
    """One point per failed expedition: why it stopped against how high it got.

    Expedition grain, so DISTINCT on the expedition key -- gold_climb repeats
    the expedition's attributes once per climber.
    """
    where, params = _filters(peaks, years, seasons)
    return _query(
        f"""SELECT DISTINCT expedition_id_year, year, peak_name, season,
                   term_reason, term_reason_code,
                   expedition_high_point_m AS high_point_m,
                   peak_height_m,
                   100.0 * expedition_high_point_m / peak_height_m AS pct_of_summit
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE {where} AND NOT expedition_succeeded
              AND expedition_high_point_m IS NOT NULL""",
        params,
    )


@st.cache_data(show_spinner=False)
def death_causes(years: tuple[int, int], seasons: list[str], peaks: list[str]) -> pd.DataFrame:
    """Cause and phase of death, hired against member."""
    where, params = _filters(peaks, years, seasons)
    return _query(
        f"""SELECT worker_class, death_type, death_class, count(*) AS deaths,
                   avg(death_height_m) AS mean_death_height_m
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE {where} AND died
            GROUP BY 1, 2, 3""",
        params,
    )


@st.cache_data(show_spinner=False)
def failure_reasons_over_time(
    years: tuple[int, int], seasons: list[str], peaks: list[str], granularity: str = "Decade"
) -> pd.DataFrame:
    """Why expeditions stopped, by period, at expedition grain.

    `share` is the percentage of that period's failures, which is what makes
    periods comparable: the 2010s hold three times as many failed expeditions
    as the 1980s, so raw counts mostly show how busy Nepal was.
    """
    where, params = _filters(peaks, years, seasons)
    bucket = _bucket(granularity)
    return _query(
        f"""WITH failures AS (
                SELECT DISTINCT expedition_id_year, year, term_reason
                FROM read_parquet('{gold_path(CLIMB)}')
                WHERE {where} AND NOT expedition_succeeded
            ),
            counted AS (
                SELECT {bucket} AS period, term_reason, count(*) AS expeditions
                FROM failures GROUP BY 1, 2
            )
            SELECT period, term_reason, expeditions,
                   100.0 * expeditions / sum(expeditions) OVER (PARTITION BY period) AS share
            FROM counted ORDER BY period, expeditions DESC""",
        params,
    )


OXYGEN_LABELS = {True: "Used oxygen", False: "Climbed without", None: "Not recorded"}


@st.cache_data(show_spinner=False)
def oxygen_over_time(
    years: tuple[int, int], seasons: list[str], peaks: list[str], granularity: str
) -> pd.DataFrame:
    """Oxygen use and outcomes over time, members only.

    `oxygen_used` is silver's collapse of the upstream O2USED / O2NONE pair,
    with neither-set becoming NULL. The NULL group is kept visible rather than
    dropped, because it is the evidence that this column is not a clean
    experiment: its summit rate is near zero, which says the flag mostly gets
    recorded for people who actually went high.
    """
    where, params = _filters(peaks, years, seasons)
    bucket = _bucket(granularity)
    return _query(
        f"""SELECT {bucket} AS period,
                   CASE WHEN oxygen_used THEN 'Used oxygen'
                        WHEN NOT oxygen_used THEN 'Climbed without'
                        ELSE 'Not recorded' END AS oxygen,
                   count(*) AS climbers,
                   sum(summited::INT) AS summits,
                   100.0 * sum(summited::INT) / count(*) AS pct_summited,
                   sum(died::INT) AS deaths,
                   100.0 * sum(died::INT) / count(*) AS pct_died
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE {where} AND NOT is_hired
            GROUP BY 1, 2 ORDER BY 1, 2""",
        params,
    )


@st.cache_data(show_spinner=False)
def oxygen_by_worker(years: tuple[int, int], seasons: list[str], peaks: list[str]) -> pd.DataFrame:
    """Oxygen use by worker class -- hired staff are the interesting column."""
    where, params = _filters(peaks, years, seasons)
    return _query(
        f"""SELECT worker_class,
                   CASE WHEN oxygen_used THEN 'Used oxygen'
                        WHEN NOT oxygen_used THEN 'Climbed without'
                        ELSE 'Not recorded' END AS oxygen,
                   count(*) AS climbers,
                   sum(summited::INT) AS summits,
                   100.0 * sum(summited::INT) / count(*) AS pct_summited,
                   sum(died::INT) AS deaths,
                   100.0 * sum(died::INT) / count(*) AS pct_died
            FROM read_parquet('{gold_path(CLIMB)}')
            WHERE {where} GROUP BY 1, 2""",
        params,
    )


@st.cache_data(show_spinner=False)
def hired_recording_coverage(
    years: tuple[int, int], seasons: list[str], peaks: list[str]
) -> pd.DataFrame:
    """How many hired staff were counted, against how many were named.

    Two upstream facts disagree on purpose. TOTHIRED is the expedition's own
    count of hired personnel above base camp; an is_hired climber record is a
    named individual. In the 1960s there are twelve counted for every one
    named -- a hired worker of that era usually appears only as a tally, while
    every member got a name, a nationality, an age and an occupation.

    Decade grain: this is a story about eras, and yearly ratios on a handful of
    expeditions are noise.
    """
    where, params = _filters(peaks, years, seasons)
    return _query(
        f"""WITH per_exp AS (
                SELECT DISTINCT expedition_id_year, year, expedition_hired
                FROM read_parquet('{gold_path(CLIMB)}')
                WHERE {where} AND expedition_hired IS NOT NULL
            ),
            named AS (
                SELECT expedition_id_year, count(*) AS named
                FROM read_parquet('{gold_path(CLIMB)}')
                WHERE {where} AND is_hired
                GROUP BY 1
            )
            SELECT (p.year / 10)::INT * 10 AS decade,
                   sum(p.expedition_hired) AS counted,
                   sum(coalesce(n.named, 0)) AS named,
                   100.0 * sum(coalesce(n.named, 0))
                       / nullif(sum(p.expedition_hired), 0) AS pct_named
            FROM per_exp p LEFT JOIN named n USING (expedition_id_year)
            GROUP BY 1 ORDER BY 1""",
        [*params, *params],
    )
