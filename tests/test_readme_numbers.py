"""Every count the README states is recomputed from the files it describes.

A README number that drifts from the repository is the cheapest way to lose a reader's trust, so each one is
tied here to the artifact it comes from: CSV row counts, the model, the report, the schema-checked files, the
measure-test contexts, the mutation list, the expected-values file and the source manifest.
"""
import csv
import importlib.util
import json
import re
from pathlib import Path

import pandas as pd
import pytest

import mutate
import test_measures

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
TEXT = re.sub(r"\s+", " ", README)
DATA = ROOT / "data"
MANIFEST = json.loads((DATA / "source_manifest.json").read_text(encoding="utf-8"))
WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
NUM = r"(?<![\w-])(\d+|" + "|".join(WORDS) + r")"


def n(text):
    return WORDS[text] if text in WORDS else int(text.replace(",", ""))


def claims(pattern, minimum=1):
    found = [m.groups() for m in re.finditer(pattern, TEXT)]
    assert len(found) >= minimum, f"README no longer contains {pattern!r}"
    return found


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def table_file(table):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", table).lower().replace("p_a_", "pa_") + ".csv"


def test_table_row_counts():
    rows = re.findall(r"^\| ((?:Fact|Dim)\w+) \| [^|]+ \| ([\d,]+) \|$", README, flags=re.M)
    assert len(rows) == 7
    for table, count in rows:
        assert len(pd.read_csv(DATA / table_file(table), dtype=str)) == n(count), table


def test_model_counts(model):
    for tables, measures in claims(r"(\d+) tables and (\d+) DAX measures"):
        assert n(tables) == len(model.meta.tables)
        assert n(measures) == len(model.measures)
    for (rel,) in claims(NUM + r" relationships", minimum=2):
        assert n(rel) == len(model.relationships)
    measure_rows = re.findall(r"^\| ([^|]+?) \| [^|]+ \|$", README.split("### Measures", 1)[1].split("##", 1)[0],
                              flags=re.M)
    assert set(measure_rows) - {"Measure"} == set(model.measures)


def test_page_count():
    pages = json.loads((ROOT / "SGTransport.Report/definition/pages/pages.json").read_text(encoding="utf-8"))
    for (count,) in claims(r"PBIR report with (\d+) pages"):
        assert n(count) == len(pages["pageOrder"])


def test_schema_file_count():
    files = load_tool("validate_schemas").project_files(ROOT)
    for (count,) in claims(r"(\d+) PBIP/PBIR JSON files"):
        assert n(count) == len(files)


def test_measure_context_counts():
    contexts = sum(len(c) for _, c in test_measures.GROUPS.values())
    for ctx, pairs in claims(r"(\d+) filter contexts \((\d+) measure-context pairs\)", minimum=2):
        assert (n(ctx), n(pairs)) == (contexts, len(test_measures.CASES))


def test_mutant_counts():
    kinds = {"MEASURE_TESTS": 0, "ENGINE_TESTS": 0, "DATA_TESTS": 0, "REPORT_TESTS": 0}
    for m in mutate.MUTANTS:
        kind = next(k for k in kinds if m[4] is getattr(mutate, k))
        kinds[kind] += 1
    total, dax, engine, data, report = claims(
        r"(\d+) planted bugs \((\d+) in the DAX or relationships, (\d+) in the evaluator, (\d+) in the data, "
        r"(\d+) in the report\)")[0]
    assert n(total) == len(mutate.MUTANTS)
    assert (n(dax), n(engine), n(data), n(report)) == tuple(kinds.values())
    for (count,) in claims(r"plants (\d+) deliberate bugs"):
        assert n(count) == len(mutate.MUTANTS)


def test_expected_value_count_and_card_values():
    with open(ROOT / "expected" / "visual_values.csv", encoding="utf-8", newline="") as f:
        records = list(csv.DictReader(f))
    for (count,) in claims(r"\((\d+) values\)"):
        assert n(count) == len(records)
    cards = {(r["measure"], r["display"]) for r in records if r["visual"] == "rid_cards"}
    step6 = claims(r"the card should show (.+?)\. Every other card")[0][0]
    stated = re.findall(r"((?:Avg )?Week[\w/ ]+?(?: MoM %)?) ([+-]?[\d,.]+%?)(?:,| and|$)", step6)
    assert len(stated) == 5
    for measure, display in stated:
        assert (measure.strip(), display) in cards, (measure, display)


def test_speed_snapshot_counts(model):
    s = model.frames["FactLinkHourSpeed"].merge(model.frames["DimDate"][["Date", "DayType"]], on="Date")
    links = model.frames["DimCorridorLink"]
    am = s[(s.DayType == "WEEKDAY") & s.Hour.isin([7, 8])]
    night = s[s.Hour.isin([22, 23, 0, 1, 2, 3, 4])]
    per_link = lambda f: set(f.groupby("LinkKey").Snapshots.sum().reindex(links.LinkKey, fill_value=0))  # noqa: E731
    am_n, h7, h8, night_n, total = claims(
        r"(\d+) speed-band snapshots per link \((\d+) in the hour from 07:00, (\d+) in the hour from 08:00\), "
        r"against (\d+) per link at night and (\d+) in all")[0]
    assert {str(d) for d in am.Date.unique()} == {"2026-09-28"}
    assert per_link(am) == {n(am_n)}
    assert per_link(am[am.Hour == 7]) == {n(h7)} and per_link(am[am.Hour == 8]) == {n(h8)}
    assert per_link(night) == {n(night_n)} and per_link(s) == {n(total)}
    for (count,) in claims(r"one morning \((\d+) snapshots per link\)"):
        assert n(count) == n(am_n)
    assert n(total) == MANIFEST["speedband_snapshots"]["count"]
    assert n(claims(r"(\d+) LTA DataMall traffic speed-band snapshots")[0][0]) == MANIFEST["speedband_snapshots"]["count"]
    assert n(claims(r"([\d,]+) matched segments")[0][0]) == MANIFEST["matched_speedband_segments"]
    assert n(claims(r"the (\d+) stop-to-stop links")[0][0]) == len(links)


def test_source_commit():
    commit = MANIFEST["sg_bus_network_monitor_commit"]
    shorts = re.findall(r"commit `([0-9a-f]{7})`|manifest's commit, ([0-9a-f]{7})|/blob/([0-9a-f]{7})/", README)
    assert len(shorts) >= 3
    assert all(commit.startswith(s) for group in shorts for s in group if s)


@pytest.mark.parametrize("key,pattern", [
    ("ridership_source_rows", r"(\d+) malformed source rows \(out of ([\d,]+)\)"),
    ("planning_area_polygons_repaired", NUM + r" self-intersecting polygons are repaired"),
])
def test_console_counts_match_manifest_once_recorded(key, pattern):
    """These two counts were printed by the export but not recorded; once a re-export writes them, pin them."""
    stated = claims(pattern)[0]
    if key not in MANIFEST:
        pytest.skip(f"{key} not in the manifest yet; it is written by the next run of scripts/export_data.py")
    if key == "ridership_source_rows":
        counts = MANIFEST[key].values()
        assert (n(stated[0]), n(stated[1])) == (sum(c["dropped"] for c in counts), sum(c["rows"] for c in counts))
    else:
        assert n(stated[0]) == MANIFEST[key]


def test_first_screen_figures(dax, model):
    from conftest import to_dax_filters
    aug = to_dax_filters(model, {"DimDate.YearMonth": {"2026-08"}})
    jul = to_dax_filters(model, {"DimDate.YearMonth": {"2026-07"}})
    fell, rose, m_jul, m_aug, wd_jul, wd_aug = claims(
        r"weekday tap-ins fell (\d+\.\d)% while tap-ins per weekday rose (\d+\.\d)% \((\d\.\d\d) M to (\d\.\d\d) M\), "
        r"because July had (\d+) weekdays and August (\d+)")[0]
    assert round(-dax.measure("Weekday Boardings MoM %", aug) * 100, 1) == float(fell)
    assert round(dax.measure("Avg Weekday Boardings MoM %", aug) * 100, 1) == float(rose)
    assert round(dax.measure("Avg Weekday Boardings", jul) / 1e6, 2) == float(m_jul)
    assert round(dax.measure("Avg Weekday Boardings", aug) / 1e6, 2) == float(m_aug)
    d = model.frames["DimDate"]
    wd = d[d.DayType == "WEEKDAY"].groupby("YearMonth").size()
    assert (wd["2026-07"], wd["2026-08"]) == (int(wd_jul), int(wd_aug))
    line, walk = claims(r"(\d+\.\d)% of residents live within 400 m of a stop in a straight line, and at least "
                        r"(\d+\.\d)% along the walking network")[0]
    assert round(dax.measure("Straight-line Coverage %") * 100, 1) == float(line)
    assert round(dax.measure("Walk Coverage %") * 100, 1) == float(walk)
    for (walk_again,) in claims(r"the (\d+\.\d)% is an uncorrected lower bound"):
        assert walk_again == walk
