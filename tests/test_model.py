"""Structure of the semantic model and of the CSV extracts it loads."""
import re
from pathlib import Path

import pandas as pd
import pytest

from pbitest.model import DATA, DEFINITION

M_TYPES = {"type text": "string", "Int64.Type": "int64", "type number": "double", "type date": "dateTime",
           "type logical": "boolean"}


def test_tmdl_uses_tabs():
    for f in DEFINITION.rglob("*.tmdl"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            assert not line.startswith(" "), f"{f.name}:{n} indented with spaces"


def test_model_lists_every_table_file():
    refs = re.findall(r"^ref table (\S+)", (DEFINITION / "model.tmdl").read_text(encoding="utf-8"), re.M)
    files = sorted(p.stem for p in (DEFINITION / "tables").glob("*.tmdl"))
    assert sorted(refs) == files


def test_star_schema_shape(model):
    tables = set(model.meta.tables)
    assert tables == {"DimDate", "DimPlanningArea", "DimStop", "DimCorridorLink",
                      "FactStopRidership", "FactLinkHourSpeed", "FactPACoverage"}
    rels = {(r.from_table, r.from_column, r.to_table, r.to_column) for r in model.relationships}
    assert rels == {
        ("FactStopRidership", "MonthStart", "DimDate", "Date"),
        ("FactStopRidership", "StopCode", "DimStop", "StopCode"),
        ("DimStop", "PlanningArea", "DimPlanningArea", "PlanningArea"),
        ("FactLinkHourSpeed", "Date", "DimDate", "Date"),
        ("FactLinkHourSpeed", "LinkKey", "DimCorridorLink", "LinkKey"),
        ("DimCorridorLink", "FromStop", "DimStop", "StopCode"),
        ("FactPACoverage", "PlanningArea", "DimPlanningArea", "PlanningArea"),
    }
    assert model.date_tables == {"DimDate": "Date"}


@pytest.mark.parametrize("table", ["DimDate", "DimPlanningArea", "DimStop", "DimCorridorLink",
                                   "FactStopRidership", "FactLinkHourSpeed", "FactPACoverage"])
def test_csv_matches_declared_columns(model, table):
    t = model.meta.tables[table]
    header = list(pd.read_csv(DATA / t.csv_file, nrows=0).columns)
    assert header == [c.source_column for c in t.columns.values()], "CSV columns and TMDL columns differ"
    # The Power Query step must type every column the way the TMDL column declares it.
    pairs = re.findall(r'\{"([A-Za-z0-9_]+)",\s*(type text|Int64\.Type|type number|type date|type logical)\}',
                       t.partition_source)
    assert dict(pairs) == {c.source_column: next(k for k, v in M_TYPES.items() if v == c.data_type)
                           for c in t.columns.values()}
    assert "DataFolder" in t.partition_source


def test_one_side_keys_unique_and_relationships_complete(model):
    for r in model.relationships:
        one = model.frames[r.to_table][r.to_column]
        many = model.frames[r.from_table][r.from_column]
        assert one.notna().all() and one.is_unique, f"{r.to_table}[{r.to_column}] is not a unique key"
        orphans = set(many.dropna()) - set(one)
        assert not orphans, f"{r.from_table}[{r.from_column}] has values missing from {r.to_table}: {sorted(orphans)[:5]}"
        assert many.notna().all(), f"{r.from_table}[{r.from_column}] has nulls"


def test_fact_grains_are_unique(model):
    f = model.frames
    assert not f["FactStopRidership"].duplicated(["MonthStart", "DayType", "StopCode"]).any()
    assert not f["FactLinkHourSpeed"].duplicated(["LinkKey", "Date", "Hour"]).any()
    assert f["FactPACoverage"].PlanningArea.is_unique


def test_fact_values_are_plausible(model):
    r = model.frames["FactStopRidership"]
    assert (r.TapIns >= 0).all() and (r.TapOuts >= 0).all()
    assert set(r.DayType) == {"WEEKDAY", "WEEKENDS/HOLIDAY"}
    assert set(d.strftime("%Y-%m") for d in r.MonthStart) == {"2026-02", "2026-06", "2026-07", "2026-08"}
    s = model.frames["FactLinkHourSpeed"]
    assert s.Hour.between(0, 23).all() and (s.Snapshots >= 1).all()
    assert ((s.SpeedKmhSum / s.Snapshots - s.MeanSpeedKmh).abs() < 1e-3).all()
    assert s.MeanSpeedKmh.between(5, 75).all()        # band midpoints run from 5 to 75 km/h
    c = model.frames["FactPACoverage"]
    for col in ("CoveredLine400m", "CoveredLine308m", "CoveredWalk400m"):
        assert (c[col] <= c.Residents + 1e-6).all()
    assert (c.CoveredWalk400m <= c.CoveredLine400m + 1e-6).all() and (c.CoveredLine308m <= c.CoveredLine400m + 1e-6).all()


def test_date_table(model):
    d = model.frames["DimDate"]
    days = pd.to_datetime(pd.Series(d.Date))
    assert (days.diff().dropna() == pd.Timedelta(days=1)).all() and days.min().strftime("%m-%d") == "01-01"
    assert set(d.loc[d.HasRidershipData, "YearMonth"]) == {"2026-02", "2026-06", "2026-07", "2026-08"}
    weekend = d.IsoWeekday >= 6
    assert (d.loc[weekend, "DayType"] == "WEEKENDS/HOLIDAY").all()
    assert (d.loc[d.IsPublicHoliday, "DayType"] == "WEEKENDS/HOLIDAY").all()
    assert (d.loc[~weekend & ~d.IsPublicHoliday, "DayType"] == "WEEKDAY").all()
    assert d.IsPublicHoliday.sum() == 14


def test_measure_names_are_unique_and_documented(model):
    measures = [m for t in model.meta.tables.values() for m in t.measures.values()]
    names = [m.name for m in measures]
    assert len(names) == len(set(names))
    columns = {c for t in model.meta.tables.values() for c in t.columns}
    assert not set(names) & columns, "a measure has the name of a column"
    assert 8 <= len(names) <= 13
    for m in measures:
        assert m.format_string, f"{m.name} has no formatString"
        assert m.description or m.name == "Total Residents", f"{m.name} has no /// description"


def test_csv_extracts_are_small_and_aggregated():
    total = sum(p.stat().st_size for p in Path(DATA).glob("*.csv"))
    assert total < 5_000_000
    stops = pd.read_csv(DATA / "dim_stop.csv", nrows=0).columns
    assert not any(k in c.lower() for c in stops for k in ("card", "passenger", "person", "nric"))
