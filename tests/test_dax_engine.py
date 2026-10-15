"""Unit tests of the DAX evaluator on a tiny hand-made model, with values worked out by hand.

They pin the DAX semantics the measure tests rely on, so that a change to the evaluator that made it agree
with a wrong measure would show up here.
"""
import datetime as dt
import textwrap

import pytest

from pbitest.dax import DaxError, Evaluator, parse
from pbitest.model import LoadedModel


def _tmdl(s):
    return textwrap.dedent(s).lstrip("\n").replace("    ", "\t")


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    root = tmp_path_factory.mktemp("tiny")
    (root / "definition" / "tables").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "data" / "d.csv").write_text("Date,Month,Kind\n" + "\n".join(
        f"{d.isoformat()},{d.strftime('%Y-%m')},{'A' if d.day % 2 else 'B'}"
        for d in (dt.date(2025, 12, 31) + dt.timedelta(days=i) for i in range(60))) + "\n")
    (root / "data" / "g.csv").write_text("Area,Region\nN1,North\nN2,North\nS1,South\n")
    (root / "data" / "s.csv").write_text("Shop,Area\nx,N1\ny,N2\nz,S1\n")
    (root / "data" / "f.csv").write_text(
        "Day,Shop,Qty\n2026-01-05,x,10\n2026-01-06,y,20\n2026-01-07,z,30\n2026-02-03,x,1\n2026-02-04,z,2\n")
    def part(f):
        lines = ["", "\tpartition p = m", "\t\tmode: import", "\t\tsource =", "\t\t\t\tlet",
                 f'\t\t\t\t\tSource = Csv.Document(File.Contents(DataFolder & "{f}"))', "\t\t\t\tin",
                 "\t\t\t\t\tSource", ""]
        return "\n".join(lines)
    (root / "definition" / "tables" / "D.tmdl").write_text(_tmdl("""
        table D
            dataCategory: Time

            column Date
                dataType: dateTime
                isKey
                sourceColumn: Date

            column Month
                dataType: string
                sourceColumn: Month

            column Kind
                dataType: string
                sourceColumn: Kind
        """) + (part("d.csv")))
    (root / "definition" / "tables" / "G.tmdl").write_text(_tmdl("""
        table G

            column Area
                dataType: string
                isKey
                sourceColumn: Area

            column Region
                dataType: string
                sourceColumn: Region
        """) + (part("g.csv")))
    (root / "definition" / "tables" / "S.tmdl").write_text(_tmdl("""
        table S

            column Shop
                dataType: string
                isKey
                sourceColumn: Shop

            column Area
                dataType: string
                sourceColumn: Area
        """) + (part("s.csv")))
    (root / "definition" / "tables" / "F.tmdl").write_text(_tmdl("""
        table F

            measure Qty = SUM(F[Qty])

            measure 'Qty Prev' = CALCULATE([Qty], PREVIOUSMONTH(D[Date]))

            measure 'Days A' = CALCULATE(COUNTROWS(D), D[Kind] = "A")

            measure 'Qty North' = CALCULATE([Qty], G[Region] = "North")

            measure P = PERCENTILE.INC(F[Qty], 0.15)

            measure Ratio = DIVIDE([Qty], [Days A])

            column Day
                dataType: dateTime
                sourceColumn: Day

            column Shop
                dataType: string
                sourceColumn: Shop

            column Qty
                dataType: int64
                sourceColumn: Qty
        """) + (part("f.csv")))
    (root / "definition" / "relationships.tmdl").write_text(_tmdl("""
        relationship r1
            fromColumn: F.Day
            toColumn: D.Date

        relationship r2
            fromColumn: F.Shop
            toColumn: S.Shop

        relationship r3
            fromColumn: S.Area
            toColumn: G.Area
        """))
    m = LoadedModel(root / "definition", root / "data")
    return m, Evaluator(m)


def test_relationship_chain_filters_fact(tiny):
    _, e = tiny
    assert e.measure("Qty") == 63
    assert e.measure("Qty", {("G", "Region"): {"North"}}) == 31          # x and y, through S
    assert e.measure("Qty", {("G", "Area"): {"S1"}}) == 32
    assert e.measure("Qty", {("S", "Shop"): {"none"}}) is None           # SUM over no rows is BLANK


def test_dimension_is_not_filtered_by_fact_or_unrelated_tables(tiny):
    _, e = tiny
    # D is the one side; a filter on G must not change how many D rows are visible.
    assert e.measure("Days A", {("G", "Region"): {"South"}}) == e.measure("Days A")


def test_calculate_replaces_filter_on_same_column(tiny):
    _, e = tiny
    assert e.measure("Qty North", {("G", "Region"): {"South"}}) == 31
    assert e.measure("Days A", {("D", "Kind"): {"B"}}) == e.measure("Days A")


def test_calculate_keeps_filters_on_other_columns(tiny):
    _, e = tiny
    jan = e.measure("Days A", {("D", "Month"): {"2026-01"}})
    assert jan == 16                                                     # odd days of January


def test_previous_month_removes_other_date_table_filters(tiny):
    _, e = tiny
    # The context filters D[Month]; PREVIOUSMONTH filters D[Date], so the Month filter must be removed,
    # otherwise February's previous month would intersect with February and come out blank.
    assert e.measure("Qty Prev", {("D", "Month"): {"2026-02"}}) == 60
    assert e.measure("Qty Prev", {("D", "Month"): {"2026-01"}}) is None  # December 2025 has no sales
    assert e.measure("Qty Prev", {("D", "Month"): {"2026-02"}, ("G", "Region"): {"North"}}) == 30


def test_percentile_inc_matches_excel_definition(tiny):
    _, e = tiny
    # Values 1, 2, 10, 20, 30: rank 0.15 * 4 = 0.6 -> 1 + 0.6 * (2 - 1) = 1.6
    assert e.measure("P") == pytest.approx(1.6)
    assert e.measure("P", {("S", "Shop"): {"x"}}) == pytest.approx(1 + 0.15 * 9)


def test_divide_and_blank_rules(tiny):
    m, e = tiny
    assert e.eval(parse("DIVIDE(1, 0)"), None, {}, None) is None
    assert e.eval(parse("DIVIDE(1, 0, -1)"), None, {}, None) == -1
    assert e.eval(parse("DIVIDE(BLANK(), 2)"), None, {}, None) is None
    assert e.eval(parse("BLANK() - 3"), None, {}, None) == -3
    assert e.eval(parse("BLANK() + BLANK()"), None, {}, None) is None
    assert e.eval(parse("IF(ISBLANK(BLANK()) || FALSE(), 1, 2)"), None, {}, None) == 1
    assert e.eval(parse("VAR a = 2 VAR b = a * 3 RETURN b - a"), None, {}, None) == 4
    assert e.eval(parse("3 IN {1, 2, 3}"), None, {}, None) is True


def test_unsupported_dax_is_an_error_not_a_guess(tiny):
    _, e = tiny
    with pytest.raises(DaxError):
        e.eval(parse("SAMEPERIODLASTYEAR(D[Date])"), None, {}, None)
    with pytest.raises(DaxError):
        parse("SUM(F[Qty]")
    with pytest.raises(DaxError):
        e.measure("No Such Measure")
