"""expected/visual_values.csv: what the report should show once opened, derived from the report files."""
import csv
import sys
from pathlib import Path

import pytest

import reference as ref

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import expected_values  # noqa: E402


@pytest.fixture(scope="module")
def records():
    return expected_values.build()


def test_committed_file_is_current(records, tmp_path):
    out = tmp_path / "v.csv"
    expected_values.write(records, out)
    assert out.read_text(encoding="utf-8") == (ROOT / "expected" / "visual_values.csv").read_text(encoding="utf-8"), \
        "run python tools/expected_values.py"


def by(records, visual):
    return [r for r in records if r["visual"] == visual]


def test_ridership_cards_follow_the_saved_month_slicer(records):
    cards = {r["measure"]: r for r in by(records, "rid_cards")}
    ctx = {"DimDate.YearMonth": {"2026-08"}}
    assert {r["context"] for r in cards.values()} == {"DimDate[YearMonth] = 2026-08"}
    for m, r in cards.items():
        assert float(r["value"]) == pytest.approx(ref.MEASURES[m](ctx), rel=1e-9)
    assert cards["Avg Weekday Boardings MoM %"]["display"] == "+1.6%"
    assert cards["Weekday Boardings MoM %"]["display"] == "-11.6%"


def test_month_charts_ignore_the_slicer(records):
    for visual in ("rid_total_by_month", "rid_perday_by_month"):
        rows = by(records, visual)
        assert [r["row"] for r in rows] == ["2026-02", "2026-06", "2026-07", "2026-08"]
        assert {r["context"] for r in rows} == {""}
    area = by(records, "rid_by_area")
    assert {r["context"] for r in area} == {"DimDate[YearMonth] = 2026-08"}


def test_sorted_visuals(records):
    vals = [float(r["value"]) for r in by(records, "rid_by_area")]
    assert vals == sorted(vals, reverse=True)
    cov = [float(r["value"]) for r in by(records, "cov_by_area") if r["measure"] == "Walk Coverage %"]
    assert cov == sorted(cov)
    links = [float(r["value"]) for r in by(records, "spd_links") if r["measure"] == "AM Peak / Night Speed"]
    assert links == sorted(links) and len(links) == expected_values.TABLE_ROWS
    hours = [int(r["row"]) for r in by(records, "spd_by_hour") if r["measure"] == "Mean Speed (km/h)"]
    assert hours == sorted(hours) and len(hours) == 24


def test_top_stop_table_matches_pandas(records):
    rows = [r for r in by(records, "rid_top_stops") if r["measure"] == "Avg Weekday Boardings"]
    ctx = {"DimDate.YearMonth": {"2026-08"}}
    f = ref.frames()["ride"]
    aug = f[(f["DimDate.YearMonth"] == "2026-08") & (f["FactStopRidership.DayType"] == "WEEKDAY")]
    top = aug.groupby("DimStop.StopCode")["FactStopRidership.TapIns"].sum().nlargest(10)
    assert [r["row"].split(" | ")[0] for r in rows] == list(top.index)
    for r in rows:
        stop = r["row"].split(" | ")[0]
        want = ref.avg_weekday_boardings({**ctx, "DimStop.StopCode": {stop}})
        assert float(r["value"]) == pytest.approx(want, rel=1e-9)
