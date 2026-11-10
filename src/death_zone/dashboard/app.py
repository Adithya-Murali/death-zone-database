"""Death Zone Database -- expedition analytics for the Nepal Himalaya.

    uv run --extra dashboard streamlit run src/death_zone/dashboard/app.py

Reads data/gold/*.parquet, so `dzdb gold` must have run.

Chart conventions, applied everywhere:

- Deaths are a RATE per 1,000 climber-records by default. Peak populations
  differ by three orders of magnitude, and raw counts make Everest -- with
  26,000 records -- look safer than a peak with four.
- An 'All peaks' series is the POOLED total, not the mean of the peak lines.
  It is volume-weighted, so on a rate chart it tracks whichever peak dominated
  the period. It is drawn thick and dark; individual peaks thin and faded.
- Hired staff are never pooled with members. They summit twice as often and
  die more often, so a combined figure is a weighted average of two
  populations doing different jobs.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from death_zone.dashboard import queries as Q

st.set_page_config(page_title="Death Zone Database", page_icon="⛰", layout="wide")

POOLED_COLOUR = "#c1121f"  # strong crimson: the headline series, not a grey baseline
SEASON_COLOURS = {
    "Spring": "#2f7ab5",
    "Autumn": "#c2622d",
    "Winter": "#6a7f94",
    "Summer": "#5a9367",
}
# Peaks, in draw order. The pooled series always takes POOLED_COLOUR, so these
# only have to be distinguishable from each other and from near-black.
PEAK_PALETTE = [
    "#4e79a7",
    "#f28e2b",
    "#59a14f",
    "#b07aa1",
    "#76b7b2",
    "#e15759",
    "#edc948",
    "#9c755f",
    "#bab0ac",
    "#86bcb6",
    "#d37295",
    "#8cd17d",
    "#a0cbe8",
    "#ffbe7d",
    "#79706e",
]
WORKER_COLOURS = {
    "Member": "#2f7ab5",
    "Hired (Sherpa)": "#c2622d",
    "Hired (other)": "#8c3f6d",
}
SUPPORT_COLOURS = {"Supported": "#2f7ab5", "Unsupported": "#b3303c"}

# How many peak lines to draw when no peak is picked, and the time bucket.
# Both were sidebar controls; fixing them removes two decisions nobody needs
# to make before reading a chart.
DEFAULT_PEAK_LINES = 8
# Yearly, not decade. Decade buckets hid the three events the captions call
# out: 2014 (54 summits on Everest after the icefall disaster and strike),
# 2015 (0 summits, earthquake) and 2020 (32 climbers, season cancelled) all
# average away into two points. A chart that contradicts its own footnotes is
# worse than a noisy one.
GRANULARITY = "Year"
# The reason heatmap keeps decades: 82 columns of 4-pixel cells is unreadable.
HEATMAP_GRANULARITY = "Decade"

# Appendix B's wording is accurate and far too long for an axis label -- the
# longest is 76 characters. Charts get these; tooltips keep the full text.
SHORT_LABEL = {
    "Success (main peak)": "Success (main)",
    "Success (subpeak, foresummit)": "Success (subpeak)",
    "Bad weather (storms, high winds)": "Bad weather",
    "Bad conditions (deep snow, avalanching, falling ice, or rock)": "Bad conditions",
    "Accident (death or serious injury)": "Accident",
    "Illness, AMS, exhaustion, or frostbite": "Illness / AMS",
    "Lack (or loss) of supplies, support or equipment": "Lack of supplies",
    "Route technically too difficult, lack of experience, strength, or motivation": (
        "Route too difficult"
    ),
    "Did not reach base camp": "Did not reach BC",
    "Did not attempt climb": "Did not attempt",
    "Attempt rumored": "Attempt rumoured",
    # Phase of climb
    "Death enroute BC": "En route to BC",
    "Death at BC / ABC": "At BC / ABC",
    "Ascending in summit bid": "Ascending",
    "Descending from summit bid": "Descending",
    "Expedition evacuation": "Evacuation",
    # Cause of death
    "AMS (acute mtn sickness)": "AMS",
    "Disappearance (unexplained)": "Disappearance",
}


def shorten(series: pd.Series) -> pd.Series:
    """Map long Guide wording to chart-length labels, leaving the rest alone."""
    return series.map(lambda v: SHORT_LABEL.get(v, v))


# --------------------------------------------------------------------------- #
# Chart helpers
# --------------------------------------------------------------------------- #
def _sized(chart: alt.LayerChart, title: str) -> alt.LayerChart:
    """Common sizing so titles and axis labels are not clipped.

    Streamlit hands Altair a container width; without `contains="padding"` the
    chart sizes the plot area to that width and the title, axis labels and
    legend then overflow and get cut. Explicit padding reserves the room.
    """
    return (
        chart.properties(
            title=alt.TitleParams(title, fontSize=16, anchor="start", offset=12),
            height=400,
            padding={"left": 8, "right": 16, "top": 8, "bottom": 8},
            autosize=alt.AutoSizeParams(type="fit-x", contains="padding"),
        )
        .configure_axis(labelLimit=0, titlePadding=8)
        .interactive()
    )


def attempt_vs_summit(df: pd.DataFrame) -> alt.LayerChart:
    """Attempts and summits on one axis; the gap between them is the failures.

    These two share a unit (people) and an order of magnitude -- 19,809 attempts
    against 9,388 summits in the 2020s -- so one axis serves both. Deaths do
    NOT join them: at 126:1 against attempts, a death line scaled to attempts
    sits at 0.8% of the plot height, which is a flat line on zero.

    Measure is the dash pattern and peak is the colour, so a picked peak reads
    as one colour in two line styles.
    """
    long = df.melt(
        id_vars=["series", "period", "is_pooled"],
        value_vars=["climbers", "summits"],
        var_name="measure",
        value_name="people",
    ).replace({"measure": {"climbers": "Attempting", "summits": "Summiting"}})

    selection = alt.selection_point(fields=["series"], bind="legend")
    peaks = [s for s in df["series"].unique() if s != Q.ALL_PEAKS]
    has_pooled = Q.ALL_PEAKS in set(df["series"])
    domain = ([Q.ALL_PEAKS] if has_pooled else []) + peaks
    colours = ([POOLED_COLOUR] if has_pooled else []) + [
        PEAK_PALETTE[i % len(PEAK_PALETTE)] for i in range(len(peaks))
    ]

    base = alt.Chart(long).encode(
        x=alt.X("period:Q", title="Year", axis=alt.Axis(format="d"), scale=alt.Scale(nice=False)),
        y=alt.Y("people:Q", title="Climbers", scale=alt.Scale(zero=True)),
        color=alt.Color(
            "series:N", title="Peak", scale=alt.Scale(domain=domain, range=colours), sort=domain
        ),
        strokeDash=alt.StrokeDash(
            "measure:N",
            title="Measure",
            scale=alt.Scale(domain=["Attempting", "Summiting"], range=[[1, 0], [5, 3]]),
        ),
        opacity=alt.condition(selection, alt.value(0.95), alt.value(0.12)),
    )
    stroke = (
        alt.condition(alt.datum.is_pooled, alt.value(3.6), alt.value(1.7))
        if has_pooled
        else alt.value(2.6)
    )
    line = base.mark_line(interpolate="monotone").encode(strokeWidth=stroke)
    points = base.mark_circle(size=14).encode(
        tooltip=[
            alt.Tooltip("series:N", title="Peak"),
            alt.Tooltip("period:Q", title="Year", format="d"),
            alt.Tooltip("measure:N", title="Measure"),
            alt.Tooltip("people:Q", title="Climbers", format=","),
        ]
    )
    return _sized(
        alt.layer(line, points).add_params(selection), "Climbers attempting and summiting"
    )


def _series_chart(
    df: pd.DataFrame,
    *,
    colour_field: str,
    value: str,
    title: str,
    axis_title: str,
    domain: list[str],
    colours: list[str],
    emphasise_pooled: bool,
    size_by_denominator: bool,
) -> alt.LayerChart:
    """A line per series, with the points sized by their denominator on rates.

    Sizing the points rather than dropping thin ones is the honest option: a
    rate built on 25 climbers is still drawn, but it is visibly a dot instead
    of a disc, so nobody reads it as being as solid as a point built on 5,000.
    """
    selection = alt.selection_point(fields=[colour_field], bind="legend")
    colour = alt.Color(
        f"{colour_field}:N",
        title="Peak" if colour_field == "series" else colour_field.replace("_", " ").title(),
        scale=alt.Scale(domain=domain, range=colours),
        sort=domain,
    )
    tooltip = [
        alt.Tooltip(f"{colour_field}:N", title="Series"),
        alt.Tooltip("period:Q", title="Year", format="d"),
        alt.Tooltip("climbers:Q", title="Climbers (denominator)", format=","),
        alt.Tooltip("summits:Q", title="Summits", format=","),
        alt.Tooltip("deaths:Q", title="Deaths", format=","),
        alt.Tooltip("pct_summited:Q", title="Summited %", format=".1f"),
        alt.Tooltip("deaths_per_1000:Q", title="Deaths / 1000", format=".2f"),
    ]
    base = alt.Chart(df).encode(
        x=alt.X("period:Q", title="Year", axis=alt.Axis(format="d"), scale=alt.Scale(nice=False)),
        y=alt.Y(f"{value}:Q", title=axis_title, scale=alt.Scale(zero=True)),
        color=colour,
        opacity=alt.condition(selection, alt.value(0.95), alt.value(0.12)),
    )

    stroke = (
        alt.condition(alt.datum.is_pooled, alt.value(4.0), alt.value(1.7))
        if emphasise_pooled
        else alt.value(2.6)
    )
    line = base.mark_line(interpolate="monotone").encode(strokeWidth=stroke)

    size = (
        alt.Size(
            "climbers:Q",
            title="Climbers behind the point",
            # Max 80 px^2 is a 10px circle, inside the ~14px a yearly period
            # gets. 420 px^2 was 23px -- wider than the gap, so dots merged
            # into a band and hid the line they were annotating.
            scale=alt.Scale(range=[4, 80]),
            legend=alt.Legend(orient="bottom"),
        )
        if size_by_denominator
        else alt.value(18)
    )
    points = base.mark_circle().encode(size=size, tooltip=tooltip)

    return _sized(alt.layer(line, points).add_params(selection), title)


def peak_lines(
    df: pd.DataFrame, value: str, title: str, axis_title: str, *, size_by_denominator: bool
) -> alt.LayerChart:
    """Per-peak lines with the pooled series emphasised."""
    peaks = [s for s in df["series"].unique() if s != Q.ALL_PEAKS]
    has_pooled = Q.ALL_PEAKS in set(df["series"])
    domain = ([Q.ALL_PEAKS] if has_pooled else []) + peaks
    colours = ([POOLED_COLOUR] if has_pooled else []) + [
        PEAK_PALETTE[i % len(PEAK_PALETTE)] for i in range(len(peaks))
    ]
    return _series_chart(
        df,
        colour_field="series",
        value=value,
        title=title,
        axis_title=axis_title,
        domain=domain,
        colours=colours,
        emphasise_pooled=has_pooled,
        size_by_denominator=size_by_denominator,
    )


def category_lines(
    df: pd.DataFrame,
    colour_field: str,
    value: str,
    colours: dict[str, str],
    title: str,
    axis: str,
    *,
    size_by_denominator: bool,
) -> alt.LayerChart:
    """Lines split by a categorical series (worker class, support level)."""
    present = [k for k in colours if k in set(df[colour_field])]
    return _series_chart(
        df,
        colour_field=colour_field,
        value=value,
        title=title,
        axis_title=axis,
        domain=present,
        colours=[colours[k] for k in present],
        emphasise_pooled=False,
        size_by_denominator=size_by_denominator,
    )


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
if not Q.exports_exist():
    st.error("No gold exports found. Run `dzdb gold` (or `make gold`) first.")
    st.stop()

lo, hi = Q.year_bounds()

with st.sidebar:
    st.title("⛰ Filters")

    # The 8000m peaks with at least one climber record, busiest first: 15 of
    # the 16 eight-thousanders qualify.
    options = Q.peak_options(1)

    picked_peaks = st.multiselect(
        "Peaks",
        options=options,
        default=[],
        help="Leave empty to compare the busiest peaks with an 'All peaks' total. "
        "Pick peaks and the pooled line disappears.",
    )

    # Default to the full extent so the handle positions match the data. There
    # are 148 member records before 1950 across four decades -- thin, but real,
    # and silently excluding them made the slider look mis-scaled.
    years = st.slider("Years", min_value=lo, max_value=hi, value=(lo, hi))

    seasons = st.multiselect(
        "Seasons",
        options=["Spring", "Autumn", "Winter", "Summer"],
        default=["Spring", "Autumn"],
        help="Winter is 2.8% of climber-records and summer 0.9%; "
        "spring and autumn are the real comparison.",
    )

    st.divider()
    st.caption(
        "Deaths are shown per 1,000 climber-records. Charts labelled just "
        '"climbers" exclude hired staff; the two groups do different jobs at different risk.'
    )
    with st.expander("Where the numbers come from"):
        st.markdown(
            "One row per climber per expedition, built from the official "
            "[Himalayan Database](https://www.himalayandatabase.com/) by this project's "
            "`dzdb silver` and `dzdb gold` steps. Integer codes are decoded from "
            "Appendix B of the Himalayan Database Program Guide.\n\n"
            "Compiled by Elizabeth Hawley, maintained by Richard Salisbury, "
            "Billi Bierling and colleagues. `AGE` and `BIRTHDATE` are blank in the "
            "public release, so ages come from the upstream calculated field."
        )

if not seasons:
    st.warning("Pick at least one season.")
    st.stop()

scope = ", ".join(picked_peaks) if picked_peaks else "all peaks"
st.title("Death Zone Database")
st.caption(f"Nepal Himalaya expeditions · {years[0]}–{years[1]} · {', '.join(seasons)} · {scope}")

# --------------------------------------------------------------------------- #
# KPI strip
# --------------------------------------------------------------------------- #
kpi = Q.headline(years, seasons, picked_peaks)
# Three totals across everyone in scope -- hired staff included. The hired
# against member comparison is the whole point of the tabs below, so putting a
# single pooled "hired" figure up here invited reading it as a like-for-like
# number when it is a weighted average of two different jobs.
total_climbers = int(kpi["climbers"].sum())
total_summits = int(kpi["summits"].sum())
total_deaths = int(kpi["deaths"].sum())

cols = st.columns(3)
with cols[0]:
    st.metric("Climbers", f"{total_climbers:,}", help="One record per climber per expedition.")
with cols[1]:
    st.metric(
        "Summited",
        f"{100.0 * total_summits / total_climbers:.1f}%" if total_climbers else "-",
        help=f"{total_summits:,} summits. Counts every upstream success type.",
    )
with cols[2]:
    st.metric(
        "Died",
        f"{100.0 * total_deaths / total_climbers:.2f}%" if total_climbers else "-",
        help=f"{total_deaths:,} deaths, or "
        f"{1000.0 * total_deaths / total_climbers:.2f} per 1,000 climbers."
        if total_climbers
        else None,
    )

st.divider()

tabs = st.tabs(
    [
        "Overview",
        "Hired workers",
        "With vs without help",
        "Oxygen",
        "Why expeditions stop",
        "Cause of death",
    ]
)

# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
with tabs[0]:
    st.subheader("Climbers per peak, by season")
    st.caption("Excludes hired staff — they have their own tab.")
    bars = Q.by_peak_season(years, seasons, picked_peaks)
    if bars.empty:
        st.info("No records for this filter.")
    else:
        shown = (
            bars.groupby("peak_name")["climbers"].sum().nlargest(DEFAULT_PEAK_LINES).index
            if not picked_peaks
            else bars["peak_name"].unique()
        )
        sub = bars[bars["peak_name"].isin(shown)]
        metric = (
            st.segmented_control(
                "Bar metric",
                options=["climbers", "summits", "deaths"],
                default="climbers",
                key="overview_metric",
            )
            or "climbers"
        )
        present = [s for s in SEASON_COLOURS if s in set(sub["season"])]
        st.altair_chart(
            alt.Chart(sub)
            .mark_bar()
            .encode(
                x=alt.X("peak_name:N", title=None, sort="-y"),
                y=alt.Y(f"{metric}:Q", title=metric.title()),
                xOffset=alt.XOffset("season:N", sort=present),
                color=alt.Color(
                    "season:N",
                    title="Season",
                    scale=alt.Scale(domain=present, range=[SEASON_COLOURS[s] for s in present]),
                ),
                tooltip=[
                    "peak_name",
                    "season",
                    "climbers",
                    "summits",
                    "deaths",
                    alt.Tooltip("pct_summited:Q", format=".1f", title="Summited %"),
                    alt.Tooltip("deaths_per_1000:Q", format=".2f", title="Deaths / 1000"),
                ],
            )
            .properties(height=360),
            width="stretch",
        )

    st.subheader("Climbers over time")
    st.caption("Excludes hired staff — they have their own tab.")
    series = Q.series_over_time(
        years,
        seasons,
        picked_peaks,
        GRANULARITY,
        worker_class="Member",
        top_n=DEFAULT_PEAK_LINES,
        # No pooled line here: on the counts chart it is 4x the busiest peak
        # and flattens everything else against the axis.
        include_pooled=False,
    )
    if series.empty:
        st.info("No records for this filter.")
    else:
        # Attempts and summits share a frame so the outcome has its denominator
        # beside it. Deaths get their own axis because 126:1 cannot share one.
        st.altair_chart(attempt_vs_summit(series), width="stretch")
        st.caption(
            "Solid is climbers on expeditions, dashed is climbers who summited — the gap "
            "between them is the failures. Click a peak in the legend to bring it forward, "
            "or pick peaks in the sidebar to drop the rest entirely. No pooled 'All peaks' "
            "line here: at four times the busiest peak it flattened everything else."
        )

        st.altair_chart(
            peak_lines(
                series,
                "pct_summited",
                "Summit rate",
                "% summiting",
                size_by_denominator=True,
            ),
            width="stretch",
        )
        st.altair_chart(
            peak_lines(
                series,
                "deaths_per_1000",
                "Death rate",
                "Deaths per 1,000",
                size_by_denominator=True,
            ),
            width="stretch",
        )
        st.caption(
            "Point size is the denominator: a small dot is a rate built on a handful of "
            "people, so an early-era spike is visibly thin rather than hidden. Success "
            "counts every upstream success type: main peak, subpeak/foresummit and claimed."
        )
        st.caption(
            "**Three years are events, not noise.** On Everest: 403 climbers and only 54 "
            "summits in 2014 (Khumbu icefall disaster and the strike that followed), 519 "
            "climbers and 0 summits in 2015 (April earthquake), 32 climbers in 2020 (season "
            "cancelled). Compare climbers rather than expeditions across eras — party "
            "size moved, so expedition counts are not comparable."
        )


# --------------------------------------------------------------------------- #
# Hired workers
# --------------------------------------------------------------------------- #
with tabs[1]:
    st.subheader("Hired staff against members")
    worker = Q.worker_over_time(years, seasons, picked_peaks, GRANULARITY)
    if worker.empty:
        st.info("No records for this filter.")
    else:
        # Rates ARE the honest default here, unlike the per-peak charts. These
        # three populations differ by 17x in size -- 41,788 members against
        # 2,424 non-Sherpa hired -- so counts invert the finding: members have
        # the most deaths (557) and the LOWEST rate (13.3 per 1,000).
        worker_rate = (
            st.segmented_control(
                "Show", options=["Rate", "Count"], default="Rate", key="worker_mode"
            )
            or "Rate"
        ) == "Rate"
        worker_shown = worker
        summit_f = "pct_summited" if worker_rate else "summits"
        death_f = "deaths_per_1000" if worker_rate else "deaths"
        st.altair_chart(
            category_lines(
                worker_shown,
                "worker_class",
                summit_f,
                WORKER_COLOURS,
                "Summit rate" if worker_rate else "Summits",
                "% summiting" if worker_rate else "Climbers summiting",
                size_by_denominator=worker_rate,
            ),
            width="stretch",
        )
        st.altair_chart(
            category_lines(
                worker_shown,
                "worker_class",
                death_f,
                WORKER_COLOURS,
                "Death rate" if worker_rate else "Deaths",
                "Deaths per 1,000" if worker_rate else "Deaths",
                size_by_denominator=worker_rate,
            ),
            width="stretch",
        )
        st.caption(
            "Rates, because these populations differ in size by 17x: members "
            "have the most deaths in raw count (557) and the lowest rate "
            "(13.3 per 1,000). Counts here would say the opposite of the truth."
            if worker_rate
            else "Raw counts. Remember members outnumber hired workers nearly 3:1 "
            "(41,788 to 15,590), so more member deaths does not mean more risk per person."
        )
        st.info(
            "**Hired and Sherpa are separate axes, not a hierarchy.** On the 8000 m peaks "
            "2,424 hired workers are not Sherpa — mostly Tamang and Rai — and 514 Sherpas "
            "climbed as paying members. Pooling them hides that non-Sherpa hired workers "
            "die at roughly half again the Sherpa rate.",
            icon="⚠",
        )

    st.subheader("Hired head-count over time")
    growth = Q.hired_growth(years, seasons, picked_peaks, GRANULARITY)
    if not growth.empty:
        show_ratio = st.toggle("Show hired per member instead of head-counts", value=False)
        if show_ratio:
            st.altair_chart(
                alt.Chart(growth, title="Hired workers per member")
                .mark_area(
                    line={"color": "#c2622d"}, color="#c2622d", opacity=0.25, interpolate="monotone"
                )
                .encode(
                    x=alt.X("period:O", title="Period"),
                    y=alt.Y("hired_per_member:Q", title="Hired per member"),
                    tooltip=[
                        alt.Tooltip("period:O"),
                        "hired",
                        "members",
                        alt.Tooltip("hired_per_member:Q", format=".2f"),
                    ],
                )
                .properties(height=320),
                width="stretch",
            )
            st.caption(
                "A U-curve, not a trend: the ratio bottoms out in the 1990s and is now at "
                "its highest in the record. Commercial guiding multiplied the labour."
            )
        else:
            melted = growth.melt(
                id_vars="period",
                value_vars=["hired", "members"],
                var_name="group",
                value_name="climbers",
            )
            st.altair_chart(
                alt.Chart(melted, title="Climber records by group")
                .mark_bar()
                .encode(
                    x=alt.X("period:O", title="Period"),
                    y=alt.Y("climbers:Q", title="Climber records", stack=None),
                    color=alt.Color(
                        "group:N",
                        title=None,
                        scale=alt.Scale(domain=["members", "hired"], range=["#2f7ab5", "#c2622d"]),
                    ),
                    opacity=alt.value(0.8),
                    tooltip=["period", "group", alt.Tooltip("climbers:Q", format=",")],
                )
                .properties(height=320),
                width="stretch",
            )
        st.dataframe(
            growth.rename(
                columns={
                    "period": "Period",
                    "hired": "Hired",
                    "members": "Climbers (not hired)",
                    "hired_per_member": "Hired per member",
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Hired per member": st.column_config.ProgressColumn(
                    format="%.2f", min_value=0.0, max_value=1.5
                )
            },
        )

# --------------------------------------------------------------------------- #
# With vs without help
# --------------------------------------------------------------------------- #
with tabs[2]:
    st.subheader("Climbing with and without paid help on the mountain")
    support = Q.support_outcomes(years, seasons, picked_peaks, GRANULARITY)
    if support.empty:
        st.info("No records for this filter.")
    else:
        # Also a rate by default: supported parties outnumber unsupported ones
        # roughly 10:1, and no point here has fewer than 104 climbers behind it.
        support_rate = (
            st.segmented_control(
                "Show", options=["Rate", "Count"], default="Rate", key="support_mode"
            )
            or "Rate"
        ) == "Rate"
        support_shown = support
        st.altair_chart(
            category_lines(
                support_shown,
                "support_level",
                "pct_summited" if support_rate else "summits",
                SUPPORT_COLOURS,
                "Summit rate" if support_rate else "Summits",
                "% summiting" if support_rate else "Climbers summiting",
                size_by_denominator=support_rate,
            ),
            width="stretch",
        )
        st.altair_chart(
            category_lines(
                support_shown,
                "support_level",
                "deaths_per_1000" if support_rate else "deaths",
                SUPPORT_COLOURS,
                "Death rate" if support_rate else "Deaths",
                "Deaths per 1,000" if support_rate else "Deaths",
                size_by_denominator=support_rate,
            ),
            width="stretch",
        )
        totals = (
            support.groupby("support_level")[["climbers", "summits", "deaths"]].sum().reset_index()
        )
        totals["Summited %"] = 100.0 * totals["summits"] / totals["climbers"]
        totals["Deaths / 1000"] = 1000.0 * totals["deaths"] / totals["climbers"]
        st.dataframe(
            totals.rename(
                columns={
                    "support_level": "Support",
                    "climbers": "Climbers",
                    "summits": "Summits",
                    "deaths": "Deaths",
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Summited %": st.column_config.NumberColumn(format="%.1f%%"),
                "Deaths / 1000": st.column_config.NumberColumn(format="%.2f"),
            },
        )
        st.warning(
            "**'Unsupported' means nobody was paid to go above base camp.** Upstream counts "
            "hired personnel above base camp only, so these expeditions may still have "
            "employed base-camp staff and porters on the walk in. Hired workers are excluded "
            "from the denominator here — they are the support being measured.",
            icon="ℹ",
        )

# --------------------------------------------------------------------------- #
# Oxygen
# --------------------------------------------------------------------------- #
with tabs[3]:
    st.subheader("Supplementary oxygen")
    st.warning(
        "**Oxygen use is not a controlled comparison, and the data says so itself.** "
        "Climbers recorded as *Not recorded* summit at essentially 0% — the flag mostly "
        "gets filled in for people who actually went high, so it partly measures "
        "'attempted the summit' rather than 'used oxygen'. Read the gap between "
        "*Used oxygen* and *Climbed without* as a difference between two very "
        "different kinds of climber, not as an effect of the gas.",
        icon="⚠",
    )

    ox_time = Q.oxygen_over_time(years, seasons, picked_peaks, GRANULARITY)
    if ox_time.empty:
        st.info("No records for this filter.")
    else:
        st.altair_chart(
            _sized(
                alt.layer(
                    alt.Chart(ox_time)
                    .mark_area(interpolate="monotone", opacity=0.85)
                    .encode(
                        x=alt.X("period:Q", title="Year", axis=alt.Axis(format="d")),
                        y=alt.Y("climbers:Q", title="Climbers", stack="normalize"),
                        color=alt.Color(
                            "oxygen:N",
                            title="Oxygen",
                            scale=alt.Scale(
                                domain=["Used oxygen", "Climbed without", "Not recorded"],
                                range=["#2f7ab5", "#c2622d", "#bab0ac"],
                            ),
                        ),
                        tooltip=[
                            alt.Tooltip("period:Q", title="Year", format="d"),
                            alt.Tooltip("oxygen:N", title="Oxygen"),
                            alt.Tooltip("climbers:Q", title="Climbers", format=","),
                            alt.Tooltip("pct_summited:Q", title="Summited %", format=".1f"),
                        ],
                    )
                ),
                "Share of climbers by oxygen status",
            ),
            width="stretch",
        )
        st.caption(
            "Normalised, so this is the mix each year rather than the head-count. "
            "The grey band is how much of the record is simply unrecorded — it shrinks "
            "over time, which is itself a change in bookkeeping rather than in climbing."
        )

        st.altair_chart(
            category_lines(
                ox_time,
                "oxygen",
                "deaths_per_1000",
                {
                    "Used oxygen": "#2f7ab5",
                    "Climbed without": "#c2622d",
                    "Not recorded": "#bab0ac",
                },
                "Death rate by oxygen status",
                "Deaths per 1,000",
                size_by_denominator=True,
            ),
            width="stretch",
        )

    st.subheader("Who uses it")
    ox_worker = Q.oxygen_by_worker(years, seasons, picked_peaks)
    if not ox_worker.empty:
        st.altair_chart(
            _sized(
                alt.layer(
                    alt.Chart(ox_worker)
                    .mark_bar()
                    .encode(
                        y=alt.Y("worker_class:N", title=None, axis=alt.Axis(labelLimit=0)),
                        x=alt.X("climbers:Q", title="Climbers", stack="normalize"),
                        color=alt.Color(
                            "oxygen:N",
                            title="Oxygen",
                            scale=alt.Scale(
                                domain=["Used oxygen", "Climbed without", "Not recorded"],
                                range=["#2f7ab5", "#c2622d", "#bab0ac"],
                            ),
                        ),
                        tooltip=[
                            alt.Tooltip("worker_class:N", title="Worker class"),
                            alt.Tooltip("oxygen:N", title="Oxygen"),
                            alt.Tooltip("climbers:Q", title="Climbers", format=","),
                            alt.Tooltip("pct_summited:Q", title="Summited %", format=".1f"),
                            alt.Tooltip("deaths_per_1000:Q", title="Deaths / 1000", format=".2f"),
                        ],
                    )
                ),
                "Oxygen use by worker class",
            ),
            width="stretch",
        )
        table = ox_worker.rename(
            columns={
                "worker_class": "Worker class",
                "oxygen": "Oxygen",
                "climbers": "Climbers",
                "summits": "Summits",
                "pct_summited": "Summited %",
                "deaths": "Deaths",
                "deaths_per_1000": "Deaths / 1000",
            }
        ).sort_values(["Worker class", "Oxygen"])
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_config={
                "Summited %": st.column_config.NumberColumn(format="%.1f%%"),
                "Deaths / 1000": st.column_config.NumberColumn(format="%.2f"),
            },
        )
        st.caption(
            "Hired staff on Everest use oxygen at a far higher rate than members do, "
            "and the hired workers who climb without it have the worst death rate of "
            "any group here — on small numbers, so read the Climbers column."
        )


# --------------------------------------------------------------------------- #
# Why expeditions stop
# --------------------------------------------------------------------------- #
with tabs[4]:
    st.subheader("Why expeditions stop")
    st.info(
        "Pooled across peaks, this mixes mountains that fail for different reasons — "
        "Cho Oyu's walk-up and Annapurna's face are not the same problem. "
        "**Pick a peak in the sidebar for an answer you can act on.**",
        icon="🎯",
    )

    reasons = Q.failure_reasons_over_time(years, seasons, picked_peaks, HEATMAP_GRANULARITY)
    if reasons.empty:
        st.info("No failed expeditions for this filter.")
    else:
        reasons = reasons.assign(reason=shorten(reasons["term_reason"]))
        as_share = (
            st.segmented_control(
                "Colour by",
                options=["Share of period", "Expeditions"],
                default="Share of period",
                key="reason_mode",
            )
            or "Share of period"
        ) == "Share of period"
        field = "share" if as_share else "expeditions"
        order = (
            reasons.groupby("reason")["expeditions"]
            .sum()
            .sort_values(ascending=False)
            .index.tolist()
        )
        st.altair_chart(
            alt.Chart(reasons, title="When each reason mattered most")
            .mark_rect()
            .encode(
                x=alt.X("period:O", title="Period"),
                y=alt.Y("reason:N", title=None, sort=order, axis=alt.Axis(labelLimit=0)),
                color=alt.Color(
                    f"{field}:Q",
                    title="% of period's failures" if as_share else "Expeditions",
                    scale=alt.Scale(scheme="orangered"),
                ),
                tooltip=[
                    alt.Tooltip("term_reason:N", title="Reason (full)"),
                    alt.Tooltip("period:O", title="Period"),
                    alt.Tooltip("expeditions:Q", title="Expeditions"),
                    alt.Tooltip("share:Q", title="% of period", format=".1f"),
                ],
            )
            .properties(height=420),
            width="stretch",
        )
        st.caption(
            "Share makes periods comparable: the 2010s hold three times as many failed "
            "expeditions as the 1980s, so raw counts mostly track how busy Nepal was."
            if as_share
            else "Raw counts, so taller bars partly reflect more expeditions overall."
        )

    st.subheader("How high did they get?")
    fail = Q.failure_scatter(years, seasons, picked_peaks)
    if fail.empty:
        st.info("No failed expeditions for this filter.")
    else:
        fail = fail.assign(reason=shorten(fail["term_reason"]))
        as_pct = st.toggle("Show altitude as % of the summit", value=True)
        y_field = "pct_of_summit" if as_pct else "high_point_m"
        y_title = "High point (% of summit)" if as_pct else "High point (m)"
        order = fail.groupby("reason")[y_field].median().sort_values(ascending=False).index.tolist()
        tooltip = [
            alt.Tooltip("expedition_id_year:N", title="Expedition"),
            alt.Tooltip("peak_name:N", title="Peak"),
            alt.Tooltip("year:Q", title="Year"),
            alt.Tooltip("season:N"),
            alt.Tooltip("term_reason:N", title="Reason (full)"),
            alt.Tooltip("high_point_m:Q", title="High point (m)", format=","),
            alt.Tooltip("pct_of_summit:Q", title="% of summit", format=".1f"),
        ]
        st.altair_chart(
            alt.Chart(fail, title="Each point is one failed expedition")
            .mark_circle(size=42, opacity=0.45)
            .encode(
                x=alt.X("year:Q", title="Year", scale=alt.Scale(zero=False)),
                y=alt.Y(f"{y_field}:Q", title=y_title, scale=alt.Scale(zero=False)),
                color=alt.Color(
                    "reason:N",
                    title="Reason stopped",
                    sort=order,
                    scale=alt.Scale(scheme="tableau20"),
                    legend=alt.Legend(columns=2, labelLimit=0, symbolLimit=0),
                ),
                tooltip=tooltip,
            )
            .properties(height=420)
            .interactive(),
            width="stretch",
        )
        st.altair_chart(
            alt.Chart(fail, title="Altitude reached, by reason")
            .mark_boxplot(extent="min-max")
            .encode(
                y=alt.Y("reason:N", title=None, sort=order, axis=alt.Axis(labelLimit=0)),
                x=alt.X(f"{y_field}:Q", title=y_title, scale=alt.Scale(zero=False)),
                color=alt.Color("reason:N", legend=None, scale=alt.Scale(scheme="tableau20")),
            )
            .properties(height=440),
            width="stretch",
        )
        st.caption(
            "Altitude is an expedition attribute, so this panel does not split by hired "
            "status. Present on 89% of failed expeditions. Hover any point for the full "
            "Appendix B wording."
        )


# --------------------------------------------------------------------------- #
# Cause of death
# --------------------------------------------------------------------------- #
with tabs[5]:
    st.subheader("Cause and phase of death")
    st.info(
        "12 of the 15 eight-thousanders record any death and 10 record more than five, "
        "so pooled causes are dominated by Everest, Manaslu and Cho Oyu. **Pick a peak "
        "in the sidebar** for its own pattern — expect small numbers.",
        icon="🎯",
    )
    causes = Q.death_causes(years, seasons, picked_peaks)
    if causes.empty:
        st.info("No deaths recorded for this filter.")
    else:
        split = (
            st.segmented_control(
                "Break down by",
                options=["Cause", "Phase of climb"],
                default="Cause",
                key="death_split",
            )
            or "Cause"
        )
        field = "death_type" if split == "Cause" else "death_class"
        agg = causes.groupby([field, "worker_class"], as_index=False)["deaths"].sum()
        agg = agg.assign(label=shorten(agg[field]))
        present = [k for k in WORKER_COLOURS if k in set(agg["worker_class"])]
        order = agg.groupby("label")["deaths"].sum().sort_values(ascending=False).index.tolist()
        st.altair_chart(
            alt.Chart(agg)
            .mark_bar()
            .encode(
                # labelLimit=0 disables Altair's 180px truncation; the short
                # labels keep it readable and the tooltip keeps the full wording.
                y=alt.Y("label:N", title=None, sort=order, axis=alt.Axis(labelLimit=0)),
                x=alt.X("deaths:Q", title="Deaths"),
                color=alt.Color(
                    "worker_class:N",
                    title="Worker class",
                    scale=alt.Scale(domain=present, range=[WORKER_COLOURS[k] for k in present]),
                ),
                tooltip=[
                    alt.Tooltip(f"{field}:N", title=split),
                    alt.Tooltip("worker_class:N", title="Worker class"),
                    alt.Tooltip("deaths:Q", title="Deaths"),
                ],
            )
            .properties(height=440),
            width="stretch",
        )
        st.info(
            "**Hired workers mostly die preparing the route, not on summit day.** Route "
            "preparation is their largest phase by far, and icefall collapse kills them "
            "almost exclusively. Switch to *Phase of climb* to see it.",
            icon="⛏",
        )
        st.download_button(
            "Download this breakdown (CSV)",
            causes.to_csv(index=False).encode(),
            file_name="death_causes.csv",
            mime="text/csv",
        )
