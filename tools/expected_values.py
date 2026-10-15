"""Work out what each card, chart and table in the report should show, with the DAX evaluator.

The filter context of a visual is what the report files give it: the slicer selections saved on its page,
except where page.json turns the slicer's interaction with that visual off, plus the visual's own category or
row. The result, expected/visual_values.csv, is what to compare with Power BI Desktop once the report is open.

    python tools/expected_values.py

Tables show their first 10 rows in the saved sort order. For the stop table, the ranking is computed among the
50 stops with the most weekday tap-ins in the selected month (every stop shares that month's weekday count, so
the order by tap-ins per weekday is the same).
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pbitest.dax import Evaluator  # noqa: E402
from pbitest.model import LoadedModel  # noqa: E402

DEF = ROOT / "SGTransport.Report" / "definition"
OUT = ROOT / "expected" / "visual_values.csv"
TABLE_ROWS = 10


def fmt(value, pattern):
    if value is None:
        return "(Blank)"
    if pattern == "#,##0":
        return f"{value:,.0f}"
    if pattern in ("0.0", "0.00"):
        return f"{value:.{len(pattern) - 2}f}"
    if pattern == "0.0%":
        return f"{value * 100:.1f}%"
    if pattern == "+0.0%;-0.0%;0.0%":
        v = round(value * 100, 1)
        return f"+{v:.1f}%" if v > 0 else f"{v:.1f}%" if v < 0 else "0.0%"
    raise ValueError(f"no formatter for {pattern}")


def ref(field):
    kind = "Measure" if "Measure" in field else "Column"
    f = field[kind]
    return kind, f["Expression"]["SourceRef"]["Entity"], f["Property"]


def slicer_filters(visual):
    out = {}
    for general in visual["visual"].get("objects", {}).get("general", []):
        flt = general["properties"]["filter"]["filter"]
        alias = {s["Name"]: s["Entity"] for s in flt["From"]}
        cond = flt["Where"][0]["Condition"]["In"]
        col = cond["Expressions"][0]["Column"]
        key = (alias[col["Expression"]["SourceRef"]["Source"]], col["Property"])
        out[key] = {row[0]["Literal"]["Value"].strip("'") for row in cond["Values"]}
    return out


def visual_context(page, visuals, target):
    off = {(i["source"], i["target"]) for i in page.get("visualInteractions", []) if i["type"] == "NoFilter"}
    ctx = {}
    for name, v in visuals.items():
        if v["visual"]["visualType"] == "slicer" and (name, target) not in off:
            ctx.update(slicer_filters(v))
    return ctx


def rows_for(model, ev, visual, ctx):
    q = visual["visual"]["query"]
    projections = [ref(p["field"]) for role in q["queryState"].values() for p in role["projections"]]
    measures = [p for p in projections if p[0] == "Measure"]
    groups = [p for p in projections if p[0] == "Column"]
    vtype = visual["visual"]["visualType"]
    if not groups:
        return [("", [(m[2], ev.measure(m[2], ctx)) for m in measures])]
    tables = {g[1] for g in groups}
    assert len(tables) == 1, "grouping columns must come from one table"
    table = tables.pop()
    combos = model.frames[table][[g[2] for g in groups]].drop_duplicates()
    if vtype == "tableEx" and table == "DimStop":
        r = model.frames["FactStopRidership"]
        months = {m for (t, c), vals in ctx.items() if (t, c) == ("DimDate", "YearMonth") for m in vals}
        r = r[(r.DayType == "WEEKDAY") & r.MonthStart.map(lambda d: d.strftime("%Y-%m")).isin(months)]
        top = r.groupby("StopCode").TapIns.sum().nlargest(50).index
        combos = combos[combos.StopCode.isin(top)]
    out = []
    for values in combos.itertuples(index=False):
        c = dict(ctx)
        for g, v in zip(groups, values):
            c[(table, g[2])] = {v}
        vals = [(m[2], ev.measure(m[2], c)) for m in measures]
        if all(v is None for _, v in vals):
            continue
        out.append((" | ".join(str(v) for v in values), vals))
    sort = q.get("sortDefinition", {}).get("sort", [])
    if sort:
        kind, _, prop = ref(sort[0]["field"])
        desc = sort[0]["direction"] == "Descending"
        if kind == "Measure":
            key = lambda row: dict(row[1])[prop] if dict(row[1])[prop] is not None else float("-inf")  # noqa: E731
        else:
            i = [g[2] for g in groups].index(prop)
            key = lambda row: row[0].split(" | ")[i] if not row[0].split(" | ")[i].isdigit() else int(row[0].split(" | ")[i])  # noqa: E731
        out.sort(key=key, reverse=desc)
    if vtype == "tableEx":
        out = out[:TABLE_ROWS]
    return out


def build():
    model = LoadedModel()
    ev = Evaluator(model)
    formats = {name: m.format_string for name, m in model.measures.items()}
    records = []
    pages = json.loads((DEF / "pages" / "pages.json").read_text(encoding="utf-8"))["pageOrder"]
    for page_name in pages:
        page = json.loads((DEF / "pages" / page_name / "page.json").read_text(encoding="utf-8"))
        visuals = {p.parent.name: json.loads(p.read_text(encoding="utf-8"))
                   for p in sorted((DEF / "pages" / page_name / "visuals").glob("*/visual.json"))}
        for name, v in visuals.items():
            if v["visual"]["visualType"] in ("slicer", "textbox"):
                continue
            ctx = visual_context(page, visuals, name)
            for row, vals in rows_for(model, ev, v, ctx):
                for measure, value in vals:
                    records.append({"page": page["displayName"], "visual": name, "row": row, "measure": measure,
                                    "value": "" if value is None else f"{value:.10g}",
                                    "display": fmt(value, formats[measure]),
                                    "context": "; ".join(f"{t}[{c}] = {', '.join(sorted(map(str, vs)))}"
                                                         for (t, c), vs in sorted(ctx.items()))})
    return records


def write(records, path=OUT):
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(records[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(records)


if __name__ == "__main__":
    recs = build()
    write(recs)
    print(f"{len(recs)} values -> {OUT.relative_to(ROOT)}")
