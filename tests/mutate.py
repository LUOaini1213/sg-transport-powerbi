"""Mutation check: plant one deliberate bug at a time and require the tests to fail.

"All tests pass" says little unless the tests can fail. Each mutant below changes one thing in a copy of the
repository (a DAX measure, the evaluator, a data file, the report) and runs the test files meant to catch it.
A mutant that leaves those tests green has survived, and the check fails.

Two controls run first: the unmodified copy must pass, and a harmless edit (a changed description comment)
must survive, so a broken runner cannot pass by failing everything.

    python tests/mutate.py            # all mutants
    python tests/mutate.py -k AvgMoM  # mutants whose id contains the text
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPY = ["pbitest", "tests", "tools", "data", "expected", "SGTransport.Report", "SGTransport.SemanticModel",
        "SGTransport.pbip", ".schemas", "pytest.ini"]
TABLES = "SGTransport.SemanticModel/definition/tables/"
MEASURE_TESTS = ["tests/test_measures.py"]
ENGINE_TESTS = ["tests/test_dax_engine.py"]
DATA_TESTS = ["tests/test_model.py", "tests/test_published_figures.py"]
# The two "committed file is current" tests would catch every report edit for a trivial reason, so they are
# left out: a report mutant has to be caught by a check that knows what is wrong with it.
REPORT_TESTS = ["tests/test_report.py", "tests/test_schemas.py", "tests/test_expected_values.py",
                "--deselect", "tests/test_report.py::test_generator_reproduces_committed_files",
                "--deselect", "tests/test_expected_values.py::test_committed_file_is_current"]
VISUALS = "SGTransport.Report/definition/pages/"

# (id, file, old text, new text, tests that must fail)
MUTANTS = [
    # --- DAX measures, caught by the DAX-vs-pandas tests alone
    ("WeekdayBoardings_daytype", TABLES + "FactStopRidership.tmdl",
     'SUM(FactStopRidership[TapIns]), FactStopRidership[DayType] = "WEEKDAY")',
     'SUM(FactStopRidership[TapIns]), FactStopRidership[DayType] <> "WEEKENDS/HOLIDAY" || FactStopRidership[DayType] = "WEEKENDS/HOLIDAY")',
     MEASURE_TESTS),
    ("WeekdayBoardings_sums_tapouts", TABLES + "FactStopRidership.tmdl",
     'CALCULATE(SUM(FactStopRidership[TapIns]), FactStopRidership[DayType] = "WEEKDAY")',
     'CALCULATE(SUM(FactStopRidership[TapOuts]), FactStopRidership[DayType] = "WEEKDAY")', MEASURE_TESTS),
    ("AvgWeekday_all_months_days", TABLES + "FactStopRidership.tmdl",
     'DimDate[DayType] = "WEEKDAY", DimDate[HasRidershipData] = TRUE())',
     'DimDate[DayType] = "WEEKDAY")', MEASURE_TESTS),
    ("AvgWeekday_counts_all_days", TABLES + "FactStopRidership.tmdl",
     'VAR days = CALCULATE(COUNTROWS(DimDate), DimDate[DayType] = "WEEKDAY", DimDate[HasRidershipData] = TRUE())',
     'VAR days = CALCULATE(COUNTROWS(DimDate), DimDate[HasRidershipData] = TRUE())', MEASURE_TESTS),
    ("AvgWeekend_wrong_daytype", TABLES + "FactStopRidership.tmdl",
     'VAR boardings = CALCULATE(SUM(FactStopRidership[TapIns]), FactStopRidership[DayType] = "WEEKENDS/HOLIDAY")',
     'VAR boardings = CALCULATE(SUM(FactStopRidership[TapIns]), FactStopRidership[DayType] = "WEEKDAY")', MEASURE_TESTS),
    ("RawMoM_divides_by_current", TABLES + "FactStopRidership.tmdl",
     "VAR cur = [Weekday Boardings]\n\t\t\tVAR prev = CALCULATE([Weekday Boardings], PREVIOUSMONTH(DimDate[Date]))\n"
     "\t\t\tRETURN IF(ISBLANK(cur) || ISBLANK(prev), BLANK(), DIVIDE(cur - prev, prev))",
     "VAR cur = [Weekday Boardings]\n\t\t\tVAR prev = CALCULATE([Weekday Boardings], PREVIOUSMONTH(DimDate[Date]))\n"
     "\t\t\tRETURN IF(ISBLANK(cur) || ISBLANK(prev), BLANK(), DIVIDE(cur - prev, cur))", MEASURE_TESTS),
    ("AvgMoM_uses_raw_previous", TABLES + "FactStopRidership.tmdl",
     "VAR prev = CALCULATE([Avg Weekday Boardings], PREVIOUSMONTH(DimDate[Date]))",
     "VAR prev = DIVIDE(CALCULATE([Weekday Boardings], PREVIOUSMONTH(DimDate[Date])), 20)", MEASURE_TESTS),
    ("AvgMoM_no_blank_guard", TABLES + "FactStopRidership.tmdl",
     "VAR prev = CALCULATE([Avg Weekday Boardings], PREVIOUSMONTH(DimDate[Date]))\n"
     "\t\t\tRETURN IF(ISBLANK(cur) || ISBLANK(prev), BLANK(), DIVIDE(cur - prev, prev))",
     "VAR prev = CALCULATE([Avg Weekday Boardings], PREVIOUSMONTH(DimDate[Date]))\n"
     "\t\t\tRETURN DIVIDE(cur - prev, cur)", MEASURE_TESTS),
    ("MeanSpeed_unweighted", TABLES + "FactLinkHourSpeed.tmdl",
     "DIVIDE(SUM(FactLinkHourSpeed[SpeedKmhSum]), SUM(FactLinkHourSpeed[Snapshots]))",
     "DIVIDE(SUM(FactLinkHourSpeed[MeanSpeedKmh]), COUNTROWS(FactLinkHourSpeed))", MEASURE_TESTS),
    ("P15_is_p25", TABLES + "FactLinkHourSpeed.tmdl", "[MeanSpeedKmh], 0.15)", "[MeanSpeedKmh], 0.25)", MEASURE_TESTS),
    ("AMPeak_adds_0900", TABLES + "FactLinkHourSpeed.tmdl", "[Hour] IN {7, 8}", "[Hour] IN {7, 8, 9}", MEASURE_TESTS),
    ("AMPeak_any_day", TABLES + "FactLinkHourSpeed.tmdl",
     'FactLinkHourSpeed[Hour] IN {7, 8}, DimDate[DayType] = "WEEKDAY")', "FactLinkHourSpeed[Hour] IN {7, 8})",
     MEASURE_TESTS),
    ("Night_drops_0400", TABLES + "FactLinkHourSpeed.tmdl", "{22, 23, 0, 1, 2, 3, 4}", "{22, 23, 0, 1, 2, 3}",
     MEASURE_TESTS),
    ("Ratio_inverted", TABLES + "FactLinkHourSpeed.tmdl",
     "DIVIDE([AM Peak Speed (km/h)], [Night Speed (km/h)])", "DIVIDE([Night Speed (km/h)], [AM Peak Speed (km/h)])",
     MEASURE_TESTS),
    ("WalkCoverage_uses_308m_line", TABLES + "FactPACoverage.tmdl",
     "SUM(FactPACoverage[CoveredWalk400m])", "SUM(FactPACoverage[CoveredLine308m])", MEASURE_TESTS),
    ("LineCoverage_counts_areas", TABLES + "FactPACoverage.tmdl",
     "DIVIDE(SUM(FactPACoverage[CoveredLine400m]), [Total Residents])",
     "DIVIDE(SUM(FactPACoverage[CoveredLine400m]), COUNTROWS(FactPACoverage) * 100000)", MEASURE_TESTS),
    ("Relationship_stop_to_area_dropped", "SGTransport.SemanticModel/definition/relationships.tmdl",
     "relationship bc85a9be-df47-4245-b4cb-409d6c097acf\n\tfromColumn: DimStop.PlanningArea\n\ttoColumn: DimPlanningArea.PlanningArea\n\n",
     "", MEASURE_TESTS),
    ("Relationship_speed_date_on_monthstart", "SGTransport.SemanticModel/definition/relationships.tmdl",
     "fromColumn: FactLinkHourSpeed.Date\n\ttoColumn: DimDate.Date",
     "fromColumn: FactLinkHourSpeed.Date\n\ttoColumn: DimDate.MonthStart", MEASURE_TESTS),
    # --- the evaluator itself, caught by its own unit tests
    ("Engine_no_date_table_removal", "pbitest/dax.py",
     "            if self.removes_table_filters(table, col):", "            if False:", ENGINE_TESTS),
    ("Engine_no_relationship_chain", "pbitest/dax.py",
     "        return any(r.to_table not in _seen and self.is_filtered(r.to_table, _seen)",
     "        return False and any(r.to_table not in _seen and self.is_filtered(r.to_table, _seen)", ENGINE_TESTS),
    ("Engine_calculate_intersects_outer", "pbitest/dax.py",
     "            filters[key] = filters[key] & vals if key in added else vals",
     "            filters[key] = (ctx.filters[key] & vals) if key in ctx.filters else vals", ENGINE_TESTS),
    ("Engine_blank_minus_is_blank", "pbitest/dax.py",
     "            if _is_blank(a) and _is_blank(b):\n                return BLANK\n            a, b",
     "            if _is_blank(a) or _is_blank(b):\n                return BLANK\n            a, b", ENGINE_TESTS),
    ("Engine_percentile_nearest", "pbitest/dax.py", 'method="linear"', 'method="nearest"', ENGINE_TESTS),
    # --- data files
    ("Data_national_day_monday_as_weekday", "data/dim_date.csv",
     "2026-08-10,2026,8,Aug,2026-08,2026-08-01,1,Mon,National Day (Monday in lieu),True,WEEKENDS/HOLIDAY,True",
     "2026-08-10,2026,8,Aug,2026-08,2026-08-01,1,Mon,National Day (Monday in lieu),True,WEEKDAY,True", DATA_TESTS),
    ("Data_orphan_stop", "data/fact_stop_ridership.csv", "2026-08-01,WEEKDAY,01012,", "2026-08-01,WEEKDAY,0101X,",
     DATA_TESTS),
    # --- report files
    ("Report_measure_typo", VISUALS + "ridership/visuals/rid_cards/visual.json",
     '"Property": "Avg Weekday Boardings MoM %"', '"Property": "Avg Weekday Boarding MoM %"', REPORT_TESTS),
    ("Report_slicer_bad_month", VISUALS + "ridership/visuals/rid_month_slicer/visual.json",
     "\"'2026-08'\"", "\"'2026-13'\"", REPORT_TESTS),
    ("Report_interaction_target_typo", VISUALS + "ridership/page.json",
     '"target": "rid_total_by_month"', '"target": "rid_total_by_mnth"', REPORT_TESTS),
    ("Report_bad_display_option", VISUALS + "corridor_speeds/page.json",
     '"displayOption": "FitToPage"', '"displayOption": "Stretch"', REPORT_TESTS),
    ("Report_hidden_column_on_visual", VISUALS + "ridership/visuals/rid_top_stops/visual.json",
     '"Property": "Description"', '"Property": "InStopList"', REPORT_TESTS),
]

CONTROL = ("Control_description_edit", TABLES + "FactLinkHourSpeed.tmdl",
           "/// 15th percentile of the link-hour mean speeds", "/// 15th percentile of all link-hour mean speeds",
           MEASURE_TESTS)


def run_tests(base, tests):
    t = time.time()
    p = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests],
                       cwd=base, capture_output=True, text=True)
    lines = p.stdout.strip().splitlines() or [""]
    failed = next((ln.split(" - ")[0].replace("FAILED ", "") for ln in lines if ln.startswith(("FAILED", "ERROR"))), "")
    return p.returncode, failed or lines[-1], time.time() - t


def apply(base, path, old, new):
    f = base / path
    text = f.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"mutant anchor not found exactly once in {path}: {old[:60]!r}")
    original = text
    f.write_text(text.replace(old, new), encoding="utf-8", newline="")
    return f, original


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "repo"
        base.mkdir()
        for item in COPY:
            src = ROOT / item
            if src.is_dir():
                shutil.copytree(src, base / item, ignore=shutil.ignore_patterns("__pycache__"))
            elif src.exists():
                shutil.copy2(src, base / item)
        results = []
        all_tests = sorted({t for m in MUTANTS for t in m[4] if t.startswith("tests/") and "::" not in t})
        code, last, secs = run_tests(base, all_tests)
        print(f"control  unmodified copy: {'pass' if code == 0 else 'FAIL'}  ({last}, {secs:.0f}s)")
        if code != 0:
            raise SystemExit("the unmodified copy must pass before mutants mean anything")
        f, original = apply(base, *CONTROL[1:4])
        code, last, _ = run_tests(base, CONTROL[4])
        f.write_text(original, encoding="utf-8", newline="")
        print(f"control  harmless edit survives: {'yes' if code == 0 else 'NO'}  ({last})")
        if code != 0:
            raise SystemExit("a harmless edit was reported as a failure; the runner is not trustworthy")
        for mid, path, old, new, tests in MUTANTS:
            if a.k and a.k not in mid:
                continue
            f, original = apply(base, path, old, new)
            try:
                code, last, secs = run_tests(base, tests)
            finally:
                f.write_text(original, encoding="utf-8", newline="")
            killed = code != 0
            results.append({"mutant": mid, "killed": killed, "tests": tests, "summary": last})
            print(f"{'killed  ' if killed else 'SURVIVED'} {mid:42s} {last}  ({secs:.0f}s)")
    survived = [r["mutant"] for r in results if not r["killed"]]
    print(f"\n{len(results) - len(survived)}/{len(results)} mutants killed")
    if a.json:
        a.json.write_text(json.dumps({"killed": len(results) - len(survived), "total": len(results),
                                      "results": results}, indent=2) + "\n", encoding="utf-8")
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()
