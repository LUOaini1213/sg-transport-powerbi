"""Export the small, aggregated CSV extracts that the Power BI model loads.

Inputs are the public LTA DataMall, URA and SingStat files already processed in
https://github.com/LUOaini1213/sg-bus-network-monitor (passed as --bus-repo). Only aggregates are written:
stop x month x day-type tap counts, stop-to-stop link x hour speeds and planning-area resident counts. No
row describes a person.

    python scripts/export_data.py --bus-repo ../sg-bus-network-monitor

What it writes to data/:

    dim_date.csv               every day of 2026, with the DataMall day type (MOM 2026 public holidays)
    dim_planning_area.csv      URA Master Plan 2019 planning areas, plus two placeholder rows
    dim_stop.csv               bus stops (September 2026 list), with the planning area they lie in
    dim_corridor_link.csv      stop-to-stop links shared by 10 or more bus services
    fact_stop_ridership.csv    tap-ins and tap-outs per stop, month and day type
    fact_link_hour_speed.csv   traffic speed on each corridor link, per date and hour
    fact_pa_coverage.csv       residents within 400 m of a stop, per planning area (three measures)
    source_manifest.json       SHA-256 of every input file and the source commits

The speed method follows src/priority.py of sg-bus-network-monitor (commit e20c817): speed-band segments are
matched to the straight chord between the two stops (25 m buffer, bearing within 40 degrees, road-name rule), a
snapshot's link speed is the length-weighted mean of the band midpoints, and a snapshot counts only when valid
segments cover at least half the link.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import re
import subprocess
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
YEAR = 2026
MONTHS = ["2026-02", "2026-06", "2026-07", "2026-08"]
MIN_SERVICES = 10
BUFFER_M, MAX_ANGLE, MAX_LINK_KM, MIN_VALID_COVERAGE = 25, 40, 2.0, 0.5
OUTSIDE = "OUTSIDE SINGAPORE"
UNKNOWN = "STOP NOT IN SEPT 2026 LIST"

ABBR = {"RD": "ROAD", "AVE": "AVENUE", "ST": "STREET", "DR": "DRIVE", "CRES": "CRESCENT", "BT": "BUKIT",
        "UPP": "UPPER", "LOR": "LORONG", "NTH": "NORTH", "STH": "SOUTH", "CTRL": "CENTRAL", "PK": "PARK",
        "BLVD": "BOULEVARD", "CL": "CLOSE", "JLN": "JALAN", "TG": "TANJONG", "C'WEALTH": "COMMONWEALTH",
        "S'GOON": "SERANGOON", "HWY": "HIGHWAY", "PL": "PLACE", "TER": "TERRACE", "GDNS": "GARDENS", "KG": "KAMPONG",
        "CTR": "CENTRE", "INDL": "INDUSTRIAL", "E": "EAST", "W": "WEST", "INTL": "INTERNATIONAL", "SG": "SUNGEI",
        "SVC": "SERVICE", "GDN": "GARDEN", "HTS": "HEIGHTS", "IND": "INDUSTRIAL", "AYE": "AYER RAJAH EXPRESSWAY",
        "PIE": "PAN ISLAND EXPRESSWAY", "CTE": "CENTRAL EXPRESSWAY", "TPE": "TAMPINES EXPRESSWAY",
        "SLE": "SELETAR EXPRESSWAY", "BKE": "BUKIT TIMAH EXPRESSWAY", "KJE": "KRANJI EXPRESSWAY",
        "ECP": "EAST COAST PARKWAY", "KPE": "KALLANG PAYA LEBAR EXPRESSWAY", "MCE": "MARINA COASTAL EXPRESSWAY"}


def norm_road(name):
    words = re.sub(r"[^A-Z0-9' ]", "", str(name).upper()).split()
    return " ".join(ABBR.get(w, w) for w in words)


def bearing(x0, y0, x1, y1):
    return np.degrees(np.arctan2(x1 - x0, y1 - y0)) % 360


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git_show(repo, rev, path):
    """Read a tracked file at a given commit, so the export does not depend on the state of the working tree."""
    return subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{path}"], check=True, capture_output=True).stdout


# ---------------------------------------------------------------- dimensions

def holidays():
    h = pd.read_csv(DATA / "reference" / "public_holidays_sg_2026.csv", parse_dates=["holiday_date"])
    return dict(zip(h.holiday_date.dt.date, h.holiday_name))


def dim_date(ridership_months):
    hol = holidays()
    days = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31", freq="D")
    d = pd.DataFrame({"Date": days.date})
    d["Year"] = days.year
    d["MonthNumber"] = days.month
    d["MonthName"] = days.strftime("%b")
    d["YearMonth"] = days.strftime("%Y-%m")
    d["MonthStart"] = days.to_period("M").to_timestamp().date
    d["IsoWeekday"] = days.isocalendar().day.to_numpy()
    d["DayName"] = days.strftime("%a")
    d["HolidayName"] = d.Date.map(hol).fillna("")
    d["IsPublicHoliday"] = d.HolidayName != ""
    # DataMall's two day types: Monday-Friday that are not public holidays, and everything else.
    d["DayType"] = np.where((d.IsoWeekday >= 6) | d.IsPublicHoliday, "WEEKENDS/HOLIDAY", "WEEKDAY")
    d["HasRidershipData"] = d.YearMonth.isin(ridership_months)
    return d


def planning_areas(bus_repo):
    pa = gpd.read_file(bus_repo / "data" / "raw" / "mp2019_planning_area.geojson")[["PLN_AREA_N", "REGION_N", "geometry"]]
    pa = pa.to_crs(3414)
    # Five MP2019 polygons (e.g. Bukit Merah) are self-intersecting; repair them before the point-in-polygon join.
    pa["geometry"] = pa.make_valid()
    dim = pa[["PLN_AREA_N", "REGION_N"]].rename(columns={"PLN_AREA_N": "PlanningArea", "REGION_N": "Region"})
    dim = pd.concat([dim, pd.DataFrame({"PlanningArea": [OUTSIDE, UNKNOWN], "Region": ["(none)", "(none)"]})])
    return pa, dim.sort_values("PlanningArea").reset_index(drop=True)


def dim_stop(bus_repo, pa_poly, extra_codes):
    stops = pd.DataFrame(json.loads((bus_repo / "data" / "raw" / "datamall" / "BusStops_20260926.json").read_text("utf-8")))
    g = gpd.GeoDataFrame(stops, geometry=gpd.points_from_xy(stops.Longitude, stops.Latitude), crs=4326).to_crs(3414)
    j = gpd.sjoin(g, pa_poly, predicate="within", how="left").drop_duplicates("BusStopCode")
    # A few stops sit on the coastline or a boundary road just outside every polygon; take the nearest within 200 m.
    miss = j.PLN_AREA_N.isna()
    if miss.any():
        near = gpd.sjoin_nearest(g[g.BusStopCode.isin(j.loc[miss, "BusStopCode"])], pa_poly, max_distance=200,
                                 how="left").drop_duplicates("BusStopCode").set_index("BusStopCode").PLN_AREA_N
        j.loc[miss, "PLN_AREA_N"] = j.loc[miss, "BusStopCode"].map(near)
    j["PLN_AREA_N"] = j.PLN_AREA_N.fillna(OUTSIDE)
    out = pd.DataFrame({"StopCode": j.BusStopCode, "Description": j.Description, "RoadName": j.RoadName,
                        "PlanningArea": j.PLN_AREA_N, "Latitude": j.Latitude.round(6), "Longitude": j.Longitude.round(6),
                        "InStopList": True})
    extra = sorted(set(extra_codes) - set(out.StopCode))
    if extra:
        out = pd.concat([out, pd.DataFrame({"StopCode": extra, "Description": "(not in the September 2026 stop list)",
                                            "RoadName": "", "PlanningArea": UNKNOWN, "Latitude": np.nan,
                                            "Longitude": np.nan, "InStopList": False})])
    return out.sort_values("StopCode").reset_index(drop=True)


# ---------------------------------------------------------------- ridership

def fact_ridership(bus_repo):
    frames = []
    for ym in MONTHS:
        f = bus_repo / "data" / "raw" / "datamall" / f"transport_node_bus_{ym.replace('-', '')}.csv"
        v = pd.read_csv(f, dtype={"PT_CODE": str, "YEAR_MONTH": str, "DAY_TYPE": str})
        for c in ["TIME_PER_HOUR", "TOTAL_TAP_IN_VOLUME", "TOTAL_TAP_OUT_VOLUME"]:
            v[c] = pd.to_numeric(v[c], errors="coerce")
        ok = (v.YEAR_MONTH == ym) & v.DAY_TYPE.isin(["WEEKDAY", "WEEKENDS/HOLIDAY"]) & v.TIME_PER_HOUR.between(0, 23) \
            & (v.TOTAL_TAP_IN_VOLUME >= 0) & (v.TOTAL_TAP_OUT_VOLUME >= 0) & (v.PT_TYPE == "BUS")
        print(f"{f.name}: {len(v):,} rows, {(~ok).sum()} dropped")
        frames.append(v[ok])
    v = pd.concat(frames)
    g = v.groupby(["YEAR_MONTH", "DAY_TYPE", "PT_CODE"], as_index=False)[["TOTAL_TAP_IN_VOLUME", "TOTAL_TAP_OUT_VOLUME"]].sum()
    return pd.DataFrame({"MonthStart": g.YEAR_MONTH + "-01", "DayType": g.DAY_TYPE, "StopCode": g.PT_CODE,
                         "TapIns": g.TOTAL_TAP_IN_VOLUME.astype("int64"),
                         "TapOuts": g.TOTAL_TAP_OUT_VOLUME.astype("int64")})


# ---------------------------------------------------------------- corridor speeds

def corridor_links(bus_repo, rev, stops):
    cl = pd.read_csv(pd.io.common.BytesIO(git_show(bus_repo, rev, "outputs/corridor_links.csv")),
                     dtype={"from_stop": str, "to_stop": str})
    cl = cl[(cl.services >= MIN_SERVICES) & (cl.link_km > 0) & (cl.link_km <= MAX_LINK_KM)].copy()
    out = pd.DataFrame({
        "LinkKey": cl.from_stop + "-" + cl.to_stop, "FromStop": cl.from_stop, "ToStop": cl.to_stop,
        "LinkName": cl.from_RoadName + ": " + cl.from_Description + " to " + cl.to_Description,
        "RoadName": cl.from_RoadName, "Services": cl.services.astype(int),
        "AMPeakBusesPerHour": cl.AM_Peak_bph.round(2), "LinkKm": cl.link_km.round(3)})
    return cl, out


def match_segments(bus_repo, cl, stops):
    chords = gpd.GeoDataFrame(cl, geometry=[LineString([(a, b), (c, d)]) for a, b, c, d in
                                            zip(cl.from_Longitude, cl.from_Latitude, cl.to_Longitude, cl.to_Latitude)],
                              crs=4326).to_crs(3414)
    xy = chords.geometry.apply(lambda g: g.coords[:]).tolist()
    chords["bearing"] = [bearing(p[0][0], p[0][1], p[1][0], p[1][1]) for p in xy]
    sb = pd.read_csv(bus_repo / "data" / "raw" / "datamall" / "speedband_links.csv")
    seg = gpd.GeoDataFrame(sb, geometry=[LineString([(a, b), (c, d)]) for a, b, c, d in
                                         zip(sb.StartLon, sb.StartLat, sb.EndLon, sb.EndLat)], crs=4326).to_crs(3414)
    seg["seg_m"] = seg.length
    c = seg.geometry.apply(lambda g: g.coords[:]).tolist()
    seg["bearing"] = [bearing(p[0][0], p[0][1], p[1][0], p[1][1]) for p in c]
    seg = seg[seg.seg_m > 5]
    mid = gpd.GeoDataFrame(seg[["LinkID", "RoadName", "RoadCategory", "seg_m", "bearing"]],
                           geometry=seg.geometry.interpolate(0.5, normalized=True), crs=3414)
    buf = chords[["from_stop", "to_stop", "bearing", "geometry"]].copy()
    buf["geometry"] = buf.buffer(BUFFER_M, cap_style=2)
    j = gpd.sjoin(mid, buf, predicate="within", how="inner", lsuffix="seg", rsuffix="bus")
    diff = (j.bearing_seg - j.bearing_bus).abs() % 360
    j = j[np.minimum(diff, 360 - diff) <= MAX_ANGLE]
    m = j[["from_stop", "to_stop", "LinkID", "RoadName", "RoadCategory", "seg_m"]].copy()
    roads = stops.set_index("StopCode").RoadName.map(norm_road)
    m["seg_road"] = m.RoadName.map(norm_road)
    m["same_road"] = (m.seg_road == m.from_stop.map(roads)) | (m.seg_road == m.to_stop.map(roads))
    has_same = m.groupby(["from_stop", "to_stop"]).same_road.transform("any")
    grade_sep = (m.RoadCategory == 1) | m.seg_road.str.contains("VIADUCT|TUNNEL|EXPRESSWAY|PARKWAY")
    m = m[np.where(has_same, m.same_road, ~grade_sep)]
    return m.merge(cl[["from_stop", "to_stop", "link_km"]], on=["from_stop", "to_stop"], validate="many_to_one")


def snapshot_files(bus_repo):
    return sorted((bus_repo / "data" / "raw" / "datamall" / "speedbands").glob("sb_*.csv.gz"))


def fact_speed(bus_repo, m):
    rows = []
    links = set(m.LinkID)
    for f in snapshot_files(bus_repo):
        t = dt.datetime.strptime(f.name[:16], "sb_%Y%m%d_%H%M")
        with gzip.open(f, "rt") as z:
            d = pd.read_csv(z)
        d = d[d.LinkID.isin(links) & d.SpeedBand.between(1, 8)]
        # Band midpoint; band 8 has no upper bound, so its minimum + 5 km/h, as in priority.py.
        d = d.assign(kmh=np.where(d.SpeedBand >= 8, d.MinimumSpeed + 5, (d.MinimumSpeed + d.MaximumSpeed + 1) / 2))
        x = m.merge(d[["LinkID", "kmh"]], on="LinkID")
        x["wv"] = x.kmh * x.seg_m
        g = x.groupby(["from_stop", "to_stop"]).agg(wv=("wv", "sum"), observed_m=("seg_m", "sum"),
                                                    link_km=("link_km", "first"))
        g = g[g.observed_m >= MIN_VALID_COVERAGE * g.link_km * 1000]
        rows.append(pd.DataFrame({"LinkKey": [f"{a}-{b}" for a, b in g.index], "Date": t.date().isoformat(),
                                  "Hour": t.hour, "kmh": (g.wv / g.observed_m).round(3).to_numpy()}))
    s = pd.concat(rows)
    h = s.groupby(["LinkKey", "Date", "Hour"], as_index=False).agg(Snapshots=("kmh", "size"), SpeedKmhSum=("kmh", "sum"))
    h["SpeedKmhSum"] = h.SpeedKmhSum.round(3)
    h["MeanSpeedKmh"] = (h.SpeedKmhSum / h.Snapshots).round(4)
    return h.sort_values(["LinkKey", "Date", "Hour"]).reset_index(drop=True), len(snapshot_files(bus_repo))


# ---------------------------------------------------------------- coverage

def fact_coverage(bus_repo, rev):
    c = pd.read_csv(pd.io.common.BytesIO(git_show(bus_repo, rev, "outputs/coverage_walk_planning_area.csv")))
    return pd.DataFrame({"PlanningArea": c.PLN_AREA_N, "Residents": c.residents.astype("int64"),
                         "CoveredLine400m": c.res_line.round(3), "CoveredLine308m": c.res_line13.round(3),
                         "CoveredWalk400m": c.res_walk.round(3)}).sort_values("PlanningArea").reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus-repo", type=Path, required=True)
    ap.add_argument("--rev", default="origin/main", help="commit of sg-bus-network-monitor to read outputs/ from")
    a = ap.parse_args()
    bus = a.bus_repo.resolve()
    rev = subprocess.run(["git", "-C", str(bus), "rev-parse", a.rev], check=True, capture_output=True, text=True).stdout.strip()

    ride = fact_ridership(bus)
    pa_poly, dim_pa = planning_areas(bus)
    stops = dim_stop(bus, pa_poly, ride.StopCode.unique())
    cl_raw, links = corridor_links(bus, rev, stops)
    m = match_segments(bus, cl_raw, stops)
    speed, n_snap = fact_speed(bus, m)
    links = links[links.LinkKey.isin(speed.LinkKey)].sort_values("LinkKey").reset_index(drop=True)
    cov = fact_coverage(bus, rev)
    dates = dim_date(MONTHS)

    DATA.mkdir(exist_ok=True)
    outputs = {"dim_date.csv": dates, "dim_planning_area.csv": dim_pa, "dim_stop.csv": stops,
               "dim_corridor_link.csv": links, "fact_stop_ridership.csv": ride,
               "fact_link_hour_speed.csv": speed, "fact_pa_coverage.csv": cov}
    for name, df in outputs.items():
        df.to_csv(DATA / name, index=False, lineterminator="\n")
        print(f"{name}: {len(df):,} rows")

    raw = bus / "data" / "raw"
    inputs = [raw / "mp2019_planning_area.geojson", raw / "datamall" / "BusStops_20260926.json",
              raw / "datamall" / "speedband_links.csv"]
    inputs += [raw / "datamall" / f"transport_node_bus_{ym.replace('-', '')}.csv" for ym in MONTHS]
    snaps = snapshot_files(bus)
    manifest = {
        "sg_bus_network_monitor_commit": rev,
        "tracked_inputs_at_commit": ["outputs/corridor_links.csv", "outputs/coverage_walk_planning_area.csv"],
        "raw_inputs_sha256": {str(p.relative_to(bus)).replace("\\", "/"): sha256(p) for p in inputs},
        "speedband_snapshots": {"count": n_snap, "first": snaps[0].name, "last": snaps[-1].name,
                                "sha256_of_sorted_names_and_hashes": hashlib.sha256(
                                    "".join(f"{p.name}{sha256(p)}" for p in snaps).encode()).hexdigest()},
        "matched_speedband_segments": int(len(m)),
        "outputs_sha256": {name: sha256(DATA / name) for name in outputs},
    }
    (DATA / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
