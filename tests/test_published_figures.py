"""Anchor the measures to figures published in the source repositories' READMEs.

These numbers were measured in sg-bus-network-monitor, not here, so they are a third, external check on top of
the DAX-vs-pandas comparison:

* July 2026 had 23 weekdays and August 20 (National Day Monday holiday);
* weekday boardings per day rose from 4.21 M to 4.28 M (+1.6%) while the raw monthly total fell 11.6%;
* 98.5% of residents are within 400 m of a stop in a straight line and 79.7% along the walking network;
  96.7% with a 308 m straight-line radius.
"""
import pandas as pd

from conftest import to_dax_filters

AUG, JUL = {"DimDate.YearMonth": {"2026-08"}}, {"DimDate.YearMonth": {"2026-07"}}


def test_weekday_counts(model):
    d = model.frames["DimDate"]
    wd = d[d.DayType == "WEEKDAY"].groupby("YearMonth").size()
    assert wd["2026-07"] == 23 and wd["2026-08"] == 20 and wd["2026-02"] == 18 and wd["2026-06"] == 21
    assert len(d) == 365 and d.Date.is_unique


def test_july_august_per_weekday(dax, model):
    jul = dax.measure("Avg Weekday Boardings", to_dax_filters(model, JUL))
    aug = dax.measure("Avg Weekday Boardings", to_dax_filters(model, AUG))
    assert round(jul / 1e6, 2) == 4.21 and round(aug / 1e6, 2) == 4.28


def test_raw_total_falls_while_per_day_rises(dax, model):
    raw = dax.measure("Weekday Boardings MoM %", to_dax_filters(model, AUG))
    per_day = dax.measure("Avg Weekday Boardings MoM %", to_dax_filters(model, AUG))
    assert round(raw * 100, 1) == -11.6
    assert round(per_day * 100, 1) == 1.6


def test_coverage_brackets(dax, model):
    assert round(dax.measure("Straight-line Coverage %") * 100, 1) == 98.5
    assert round(dax.measure("Walk Coverage %") * 100, 1) == 79.7
    c = pd.read_csv(model_data(model, "fact_pa_coverage.csv"))
    assert round(c.CoveredLine308m.sum() / c.Residents.sum() * 100, 1) == 96.7


def test_coverage_is_resident_weighted_not_an_average_of_areas(dax, model):
    c = model.frames["FactPACoverage"]
    c = c[c.Residents > 0]
    unweighted = (c.CoveredWalk400m / c.Residents).mean()
    assert abs(dax.measure("Walk Coverage %") - unweighted) > 0.01


def model_data(model, name):
    from pbitest.model import DATA
    return DATA / name
