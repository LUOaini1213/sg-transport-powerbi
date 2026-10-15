"""Each DAX measure, as written in the TMDL files, against an independent pandas computation from the CSVs.

The DAX side runs the measure text through pbitest.dax; the expected side is tests/reference.py. Both are
evaluated in the same filter contexts: no filter, single months, planning areas, regions, stops, hours, day
types and combinations, so that a measure that only happens to match on the grand total still fails.
"""
import pytest

import reference as ref
from conftest import close, to_dax_filters

MONTHS = ["2026-02", "2026-06", "2026-07", "2026-08"]
AREAS = ["BEDOK", "TAMPINES", "JURONG WEST", "BUKIT TIMAH", "DOWNTOWN CORE", "OUTSIDE SINGAPORE"]

RIDERSHIP_CONTEXTS = (
    [{}]
    + [{"DimDate.YearMonth": {m}} for m in MONTHS + ["2026-05"]]
    + [{"DimDate.YearMonth": {"2026-07", "2026-08"}}]
    + [{"DimPlanningArea.PlanningArea": {a}} for a in AREAS]
    + [{"DimDate.YearMonth": {m}, "DimPlanningArea.PlanningArea": {a}} for m in ("2026-07", "2026-08") for a in AREAS[:4]]
    + [{"DimPlanningArea.Region": {"EAST REGION"}}, {"DimPlanningArea.Region": {"WEST REGION"}, "DimDate.YearMonth": {"2026-06"}}]
    + [{"DimStop.StopCode": {"84009", "75009"}}, {"DimStop.StopCode": {"46239"}, "DimDate.YearMonth": {"2026-08"}}]
    + [{"FactStopRidership.DayType": {"WEEKDAY"}}, {"FactStopRidership.DayType": {"WEEKENDS/HOLIDAY"}, "DimDate.YearMonth": {"2026-07"}}]
    # A filter on DimDate[DayType] reaches the monthly rows through MonthStart (the 1st of the month); the
    # per-day measures replace it when counting days. Odd to use, but the DAX must still behave as written.
    + [{"DimDate.DayType": {"WEEKDAY"}}]
)

SPEED_CONTEXTS = (
    [{}]
    + [{"FactLinkHourSpeed.Hour": {h}} for h in (0, 3, 7, 8, 9, 18, 22)]
    + [{"DimDate.DayType": {"WEEKDAY"}}, {"DimDate.DayType": {"WEEKENDS/HOLIDAY"}}]
    + [{"DimDate.Date": {d}} for d in ("2026-09-27", "2026-09-28", "2026-10-02")]
    + [{"DimDate.Date": {"2026-09-28"}, "FactLinkHourSpeed.Hour": {8}}]
    + [{"DimPlanningArea.PlanningArea": {a}} for a in ("DOWNTOWN CORE", "ORCHARD", "MARINE PARADE", "BEDOK")]
    + [{"DimPlanningArea.Region": {"CENTRAL REGION"}, "DimDate.DayType": {"WEEKDAY"}}]
    + [{"DimCorridorLink.LinkKey": {"08057-08069"}}, {"DimCorridorLink.RoadName": {"Orchard Rd"}}]
    + [{"DimCorridorLink.Services": {10, 11}}]
)

COVERAGE_CONTEXTS = (
    [{}]
    + [{"DimPlanningArea.Region": {r}} for r in ("CENTRAL REGION", "EAST REGION", "NORTH REGION", "NORTH-EAST REGION", "WEST REGION")]
    + [{"DimPlanningArea.PlanningArea": {a}} for a in ("BUKIT TIMAH", "TANGLIN", "BEDOK", "SOUTHERN ISLANDS", "OUTSIDE SINGAPORE")]
    + [{"DimPlanningArea.PlanningArea": {"TANGLIN", "BUKIT TIMAH"}}]
)

GROUPS = {
    "ridership": (["Weekday Boardings", "Avg Weekday Boardings", "Avg Weekend/Holiday Boardings",
                   "Weekday Boardings MoM %", "Avg Weekday Boardings MoM %"], RIDERSHIP_CONTEXTS),
    "speed": (["Mean Speed (km/h)", "P15 Link-Hour Speed (km/h)", "AM Peak Speed (km/h)", "Night Speed (km/h)",
               "AM Peak / Night Speed"], SPEED_CONTEXTS),
    "coverage": (["Total Residents", "Walk Coverage %", "Straight-line Coverage %"], COVERAGE_CONTEXTS),
}

CASES = [(m, i, ctx) for _, (measures, contexts) in GROUPS.items() for m in measures
         for i, ctx in enumerate(contexts)]


def test_every_model_measure_has_a_reference(model):
    assert set(model.measures) == set(ref.MEASURES)
    tested = {m for m, _, _ in CASES}
    assert tested == set(model.measures)


@pytest.mark.parametrize("measure,i,ctx", CASES, ids=[f"{m}-{i}" for m, i, _ in CASES])
def test_measure_matches_pandas(dax, model, measure, i, ctx):
    got = dax.measure(measure, to_dax_filters(model, ctx))
    want = ref.MEASURES[measure](ctx)
    assert close(got, want), f"{measure} in {ctx}: DAX {got!r}, pandas {want!r}"


def test_contexts_are_not_trivial(dax, model):
    """Guard against contexts that are all blank: most cases must produce a value, and values must differ."""
    for measures, contexts in GROUPS.values():
        for m in measures:
            vals = [dax.measure(m, to_dax_filters(model, c)) for c in contexts]
            non_blank = [v for v in vals if v is not None]
            assert len(non_blank) >= len(vals) // 3, m
            assert len({round(v, 9) for v in non_blank}) >= 3, m
