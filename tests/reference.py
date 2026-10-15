"""Expected measure values computed with pandas, straight from the CSV files.

This is the independent side of the measure tests. It does not use the TMDL files or pbitest: it joins each
fact table to its dimensions into one wide frame and writes every measure out as plain pandas. A filter
context is a dict {"Table.Column": set of values}, applied as row filters on the wide frame.
"""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "data"
WEEKDAY, WEEKEND = "WEEKDAY", "WEEKENDS/HOLIDAY"


def _read(name):
    return pd.read_csv(DATA / name, dtype={"StopCode": str, "FromStop": str, "ToStop": str})


def _prefix(df, table):
    return df.add_prefix(table + ".")


@lru_cache(maxsize=None)
def frames():
    date = _read("dim_date.csv")
    date["HasRidershipData"] = date.HasRidershipData.astype(bool)
    stop = _read("dim_stop.csv")
    pa = _read("dim_planning_area.csv")
    link = _read("dim_corridor_link.csv")
    ride = _read("fact_stop_ridership.csv")
    speed = _read("fact_link_hour_speed.csv")
    cov = _read("fact_pa_coverage.csv")

    D, S, P, L = _prefix(date, "DimDate"), _prefix(stop, "DimStop"), _prefix(pa, "DimPlanningArea"), _prefix(link, "DimCorridorLink")
    stop_pa = S.merge(P, left_on="DimStop.PlanningArea", right_on="DimPlanningArea.PlanningArea", how="left")
    ride_w = (_prefix(ride, "FactStopRidership")
              .merge(D, left_on="FactStopRidership.MonthStart", right_on="DimDate.Date", how="left")
              .merge(stop_pa, left_on="FactStopRidership.StopCode", right_on="DimStop.StopCode", how="left"))
    speed_w = (_prefix(speed, "FactLinkHourSpeed")
               .merge(D, left_on="FactLinkHourSpeed.Date", right_on="DimDate.Date", how="left")
               .merge(L, left_on="FactLinkHourSpeed.LinkKey", right_on="DimCorridorLink.LinkKey", how="left")
               .merge(stop_pa, left_on="DimCorridorLink.FromStop", right_on="DimStop.StopCode", how="left"))
    cov_w = _prefix(cov, "FactPACoverage").merge(P, left_on="FactPACoverage.PlanningArea",
                                                 right_on="DimPlanningArea.PlanningArea", how="left")
    return {"date": D, "ride": ride_w, "speed": speed_w, "cov": cov_w}


def _apply(df, ctx, only_prefix=None):
    for key, vals in ctx.items():
        if only_prefix and not key.startswith(only_prefix):
            continue
        if key not in df.columns:
            continue
        df = df[df[key].isin(list(vals))]
    return df


def _sum_or_none(series):
    return None if series.dropna().empty else series.sum()


def _div(a, b):
    return None if a is None or b in (None, 0) else a / b


# ------------------------------------------------------------------ ridership

def _ridership_rows(ctx, day_type):
    """Fact rows of one day type. CALCULATE's filter on FactStopRidership[DayType] replaces any outer filter on
    that column. Filters on DimDate reach the monthly rows through MonthStart; filters on stops and planning
    areas through the stop."""
    c = {k: v for k, v in ctx.items() if k != "FactStopRidership.DayType"}
    c["FactStopRidership.DayType"] = {day_type}
    return _apply(frames()["ride"], c)


def _days(ctx, day_type):
    """Days of a type in the months that have ridership, under the DimDate part of the filter context."""
    d = frames()["date"]
    keep = {k: v for k, v in ctx.items() if k.startswith("DimDate.") and k not in ("DimDate.DayType",
                                                                                   "DimDate.HasRidershipData")}
    d = _apply(d, keep)
    d = d[(d["DimDate.DayType"] == day_type) & d["DimDate.HasRidershipData"]]
    return len(d) or None


def weekday_boardings(ctx):
    return _sum_or_none(_ridership_rows(ctx, WEEKDAY)["FactStopRidership.TapIns"])


def avg_weekday_boardings(ctx):
    return _div(weekday_boardings(ctx), _days(ctx, WEEKDAY))


def avg_weekend_boardings(ctx):
    return _div(_sum_or_none(_ridership_rows(ctx, WEEKEND)["FactStopRidership.TapIns"]), _days(ctx, WEEKEND))


def _previous_month_ctx(ctx):
    """PREVIOUSMONTH, written out: the calendar month before the earliest selected month. Supported for
    contexts that select months through DimDate[YearMonth]; with no month selected the earliest is January
    2026, whose previous month is outside the date table."""
    months = ctx.get("DimDate.YearMonth") or {"2026-01"}
    prev = (pd.Period(min(months), "M") - 1).strftime("%Y-%m")
    out = {k: v for k, v in ctx.items() if not k.startswith("DimDate.")}
    out["DimDate.YearMonth"] = {prev}
    return out


def _mom(fn, ctx):
    cur = fn(ctx)
    prev_ctx = _previous_month_ctx(ctx)
    prev = fn(prev_ctx) if prev_ctx is not None else None
    if cur is None or prev is None:
        return None
    return _div(cur - prev, prev)


def weekday_boardings_mom(ctx):
    return _mom(weekday_boardings, ctx)


def avg_weekday_boardings_mom(ctx):
    return _mom(avg_weekday_boardings, ctx)


# ------------------------------------------------------------------ speeds

def _speed_rows(ctx):
    return _apply(frames()["speed"], ctx)


def mean_speed(ctx):
    s = _speed_rows(ctx)
    return _div(_sum_or_none(s["FactLinkHourSpeed.SpeedKmhSum"]), _sum_or_none(s["FactLinkHourSpeed.Snapshots"]))


def p15_speed(ctx):
    v = _speed_rows(ctx)["FactLinkHourSpeed.MeanSpeedKmh"].dropna().to_numpy()
    if len(v) == 0:
        return None
    # PERCENTILE.INC: rank = k * (n - 1), linear interpolation between the neighbouring sorted values.
    v = np.sort(v)
    rank = 0.15 * (len(v) - 1)
    lo = int(np.floor(rank))
    hi = min(lo + 1, len(v) - 1)
    return float(v[lo] + (rank - lo) * (v[hi] - v[lo]))


def am_peak_speed(ctx):
    c = {k: v for k, v in ctx.items() if k not in ("FactLinkHourSpeed.Hour", "DimDate.DayType")}
    c["FactLinkHourSpeed.Hour"] = {7, 8}
    c["DimDate.DayType"] = {WEEKDAY}
    return mean_speed(c)


def night_speed(ctx):
    c = {k: v for k, v in ctx.items() if k != "FactLinkHourSpeed.Hour"}
    c["FactLinkHourSpeed.Hour"] = {22, 23, 0, 1, 2, 3, 4}
    return mean_speed(c)


def am_night_ratio(ctx):
    return _div(am_peak_speed(ctx), night_speed(ctx))


# ------------------------------------------------------------------ coverage

def _cov_rows(ctx):
    return _apply(frames()["cov"], ctx)


def total_residents(ctx):
    return _sum_or_none(_cov_rows(ctx)["FactPACoverage.Residents"])


def walk_coverage(ctx):
    return _div(_sum_or_none(_cov_rows(ctx)["FactPACoverage.CoveredWalk400m"]), total_residents(ctx))


def line_coverage(ctx):
    return _div(_sum_or_none(_cov_rows(ctx)["FactPACoverage.CoveredLine400m"]), total_residents(ctx))


MEASURES = {
    "Weekday Boardings": weekday_boardings,
    "Avg Weekday Boardings": avg_weekday_boardings,
    "Avg Weekend/Holiday Boardings": avg_weekend_boardings,
    "Weekday Boardings MoM %": weekday_boardings_mom,
    "Avg Weekday Boardings MoM %": avg_weekday_boardings_mom,
    "Mean Speed (km/h)": mean_speed,
    "P15 Link-Hour Speed (km/h)": p15_speed,
    "AM Peak Speed (km/h)": am_peak_speed,
    "Night Speed (km/h)": night_speed,
    "AM Peak / Night Speed": am_night_ratio,
    "Total Residents": total_residents,
    "Walk Coverage %": walk_coverage,
    "Straight-line Coverage %": line_coverage,
}
