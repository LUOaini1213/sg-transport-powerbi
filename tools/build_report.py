"""Write the PBIR report definition (SGTransport.Report/definition) from the compact layout below.

The JSON files are committed; this script is how they were produced, and re-running it must reproduce them
byte for byte (tests/test_report.py checks that). Edit the layout here, not the JSON, until the report has been
opened and saved in Power BI Desktop once; after that, Desktop owns the files.

    python tools/build_report.py
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "SGTransport.Report" / "definition"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
VISUAL_SCHEMA = f"{SCHEMA}/visualContainer/2.4.0/schema.json"
PAGE_SCHEMA = f"{SCHEMA}/page/2.0.0/schema.json"


def lit(value):
    return {"expr": {"Literal": {"Value": value}}}


def text(s):
    return lit("'" + s.replace("'", "''") + "'")


def field(kind, entity, prop):
    return {kind: {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def col(entity, prop):
    return ("Column", entity, prop)


def msr(entity, prop):
    return ("Measure", entity, prop)


def projection(f, active=False):
    kind, entity, prop = f
    p = {"field": field(kind, entity, prop), "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if active:
        p["active"] = True
    return p


def container(name, x, y, w, h, z, visual):
    return {"$schema": VISUAL_SCHEMA, "name": name,
            "position": {"x": x, "y": y, "z": z, "height": h, "width": w, "tabOrder": z}, "visual": visual}


def title_objects(title):
    return {"title": [{"properties": {"show": lit("true"), "text": text(title)}}]}


def chart(vtype, category, values, title, sort=None):
    roles = {"Category": {"projections": [projection(category, active=True)]},
             "Y": {"projections": [projection(v) for v in values]}}
    v = {"visualType": vtype, "query": {"queryState": roles}, "visualContainerObjects": title_objects(title)}
    if sort:
        kind, entity, prop = sort[0]
        v["query"]["sortDefinition"] = {"sort": [{"field": field(kind, entity, prop), "direction": sort[1]}],
                                        "isDefaultSort": False}
    return v


def card(values, title):
    return {"visualType": "cardVisual",
            "query": {"queryState": {"Data": {"projections": [projection(v) for v in values]}}},
            "visualContainerObjects": title_objects(title)}


def table(values, title, sort):
    kind, entity, prop = sort[0]
    return {"visualType": "tableEx",
            "query": {"queryState": {"Values": {"projections": [projection(v) for v in values]}},
                      "sortDefinition": {"sort": [{"field": field(kind, entity, prop), "direction": sort[1]}],
                                         "isDefaultSort": False}},
            "visualContainerObjects": title_objects(title)}


def slicer(column, title, selected=None):
    kind, entity, prop = column
    objects = {"data": [{"properties": {"mode": text("Dropdown")}}],
               "selection": [{"properties": {"strictSingleSelect": lit("true")}}]}
    if selected is not None:
        objects["general"] = [{"properties": {"filter": {"filter": {
            "Version": 2,
            "From": [{"Name": "s", "Entity": entity, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": prop}}],
                "Values": [[{"Literal": {"Value": "'" + selected + "'"}}]]}}}]}}}}]
    return {"visualType": "slicer", "query": {"queryState": {"Values": {"projections": [projection(column, True)]}}},
            "objects": objects, "visualContainerObjects": title_objects(title)}


def textbox(lines):
    paragraphs = []
    for i, line in enumerate(lines):
        style = {"fontWeight": "bold", "fontSize": "16pt"} if i == 0 else {"fontSize": "10pt"}
        paragraphs.append({"textRuns": [{"value": line, "textStyle": style}]})
    return {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]}}


R, S, C, D, PA, ST, CL = ("FactStopRidership", "FactLinkHourSpeed", "FactPACoverage", "DimDate",
                          "DimPlanningArea", "DimStop", "DimCorridorLink")

PAGES = [
    {
        "name": "ridership", "displayName": "Ridership",
        "visuals": [
            ("rid_header", 20, 10, 860, 70, textbox([
                "Bus boardings by month",
                "LTA DataMall stop tap-ins, 2026. Monthly totals depend on how many weekdays a month has; "
                "the per-weekday figures divide them out."])),
            ("rid_month_slicer", 900, 10, 360, 70, slicer(col(D, "YearMonth"), "Month", selected="2026-08")),
            ("rid_cards", 20, 90, 1240, 120, card([
                msr(R, "Avg Weekday Boardings"), msr(R, "Avg Weekday Boardings MoM %"),
                msr(R, "Weekday Boardings"), msr(R, "Weekday Boardings MoM %"),
                msr(R, "Avg Weekend/Holiday Boardings")], "Selected month")),
            ("rid_total_by_month", 20, 220, 400, 240, chart(
                "clusteredColumnChart", col(D, "YearMonth"), [msr(R, "Weekday Boardings")],
                "Weekday boardings, monthly total", sort=(col(D, "YearMonth"), "Ascending"))),
            ("rid_perday_by_month", 440, 220, 400, 240, chart(
                "clusteredColumnChart", col(D, "YearMonth"), [msr(R, "Avg Weekday Boardings")],
                "Boardings per weekday", sort=(col(D, "YearMonth"), "Ascending"))),
            ("rid_by_area", 860, 220, 400, 480, chart(
                "clusteredBarChart", col(PA, "PlanningArea"), [msr(R, "Avg Weekday Boardings")],
                "Boardings per weekday by planning area", sort=(msr(R, "Avg Weekday Boardings"), "Descending"))),
            ("rid_top_stops", 20, 470, 820, 230, table([
                col(ST, "StopCode"), col(ST, "Description"), col(ST, "PlanningArea"),
                msr(R, "Avg Weekday Boardings"), msr(R, "Avg Weekday Boardings MoM %")],
                "Stops by boardings per weekday", sort=(msr(R, "Avg Weekday Boardings"), "Descending"))),
        ],
        # The month slicer drives the cards, the planning-area chart and the stop table; the two month charts
        # keep showing every month.
        "interactions": [("rid_month_slicer", "rid_total_by_month", "NoFilter"),
                         ("rid_month_slicer", "rid_perday_by_month", "NoFilter")],
    },
    {
        "name": "corridor_speeds", "displayName": "Corridor speeds",
        "visuals": [
            ("spd_header", 20, 10, 860, 70, textbox([
                "Traffic speed on shared bus corridors",
                "Stop-to-stop links used by 10 or more services. LTA speed bands, 26 Sep to 3 Oct 2026; "
                "general traffic, not bus running times."])),
            ("spd_daytype_slicer", 900, 10, 360, 70, slicer(col(D, "DayType"), "Day type")),
            ("spd_cards", 20, 90, 1240, 120, card([
                msr(S, "AM Peak Speed (km/h)"), msr(S, "Night Speed (km/h)"), msr(S, "AM Peak / Night Speed"),
                msr(S, "Mean Speed (km/h)"), msr(S, "P15 Link-Hour Speed (km/h)")], "All corridor links")),
            ("spd_by_hour", 20, 220, 620, 480, chart(
                "lineChart", col(S, "Hour"), [msr(S, "Mean Speed (km/h)"), msr(S, "P15 Link-Hour Speed (km/h)")],
                "Speed by hour of day (km/h)", sort=(col(S, "Hour"), "Ascending"))),
            ("spd_links", 660, 220, 600, 480, table([
                col(CL, "LinkName"), col(CL, "Services"), col(CL, "AMPeakBusesPerHour"),
                msr(S, "AM Peak Speed (km/h)"), msr(S, "Night Speed (km/h)"), msr(S, "AM Peak / Night Speed")],
                "Links, slowest AM peak relative to night first", sort=(msr(S, "AM Peak / Night Speed"), "Ascending"))),
        ],
        "interactions": [],
    },
    {
        "name": "walk_coverage", "displayName": "Walking coverage",
        "visuals": [
            ("cov_header", 20, 10, 1240, 70, textbox([
                "Residents within 400 m of a bus stop",
                "Straight-line reach is an upper bound; the OpenStreetMap walking network, which misses many "
                "HDB void-deck shortcuts, is a lower bound."])),
            ("cov_cards", 20, 90, 1240, 120, card([
                msr(C, "Walk Coverage %"), msr(C, "Straight-line Coverage %"), msr(C, "Total Residents")],
                "Planning areas with residents")),
            ("cov_by_area", 20, 220, 620, 480, chart(
                "clusteredBarChart", col(PA, "PlanningArea"), [msr(C, "Walk Coverage %"), msr(C, "Straight-line Coverage %")],
                "Coverage by planning area, lowest walking coverage first", sort=(msr(C, "Walk Coverage %"), "Ascending"))),
            ("cov_table", 660, 220, 600, 480, table([
                col(PA, "PlanningArea"), col(PA, "Region"), msr(C, "Total Residents"), msr(C, "Walk Coverage %"),
                msr(C, "Straight-line Coverage %")], "Planning areas", sort=(msr(C, "Total Residents"), "Descending"))),
        ],
        "interactions": [],
    },
]

REPORT = {
    "$schema": f"{SCHEMA}/report/3.0.0/schema.json",
    "themeCollection": {"baseTheme": {"name": "CY24SU10",
                                      "reportVersionAtImport": {"visual": "1.8.97", "report": "2.0.97", "page": "1.3.97"},
                                      "type": "SharedResources"}},
    "resourcePackages": [{"name": "SharedResources", "type": "SharedResources",
                          "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": "BaseTheme"}]}],
    "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True,
                 "allowChangeFilterTypes": True, "useEnhancedTooltips": True},
}


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def build(out=OUT):
    if out.exists():
        shutil.rmtree(out)
    dump(out / "version.json", {"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    dump(out / "report.json", REPORT)
    dump(out / "pages" / "pages.json", {"$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json",
                                        "pageOrder": [p["name"] for p in PAGES], "activePageName": PAGES[0]["name"]})
    for p in PAGES:
        page = {"$schema": PAGE_SCHEMA, "name": p["name"], "displayName": p["displayName"],
                "displayOption": "FitToPage", "height": 720, "width": 1280}
        if p["interactions"]:
            page["visualInteractions"] = [{"source": s, "target": t, "type": k} for s, t, k in p["interactions"]]
        dump(out / "pages" / p["name"] / "page.json", page)
        for z, (name, x, y, w, h, visual) in enumerate(p["visuals"]):
            dump(out / "pages" / p["name"] / "visuals" / name / "visual.json",
                 container(name, x, y, w, h, z * 1000, visual))


if __name__ == "__main__":
    build()
