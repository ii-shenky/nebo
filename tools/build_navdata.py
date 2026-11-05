#!/usr/bin/env python3
"""Собирает nebo/data/nav.json — радиомаяки и аэродромы с полосами вокруг дома — из OurAirports
(https://ourairports.com/data/, общественное достояние).

    mkdir /tmp/oa && cd /tmp/oa && for f in navaids airports runways; do
        curl -fLO https://davidmegginson.github.io/ourairports-data/$f.csv; done
    python3 -I tools/build_navdata.py /tmp/oa nebo/data/nav.json --lat 34.68 --lon 33.04

Маяки (VOR, VOR-DME, DME, NDB, TACAN) — в радиусе --radius км; аэродромы — только крупные
и средние, с полосами (координаты торцов). Путевых точек (fixes) в OurAirports нет.
"""
import argparse
import csv
import json
import math
from pathlib import Path


def dist_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="папка с navaids.csv, airports.csv, runways.csv")
    ap.add_argument("out", type=Path)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--radius", type=float, default=600, help="км (600 — с запасом за дальность приёма)")
    args = ap.parse_args()

    def near(lat, lon):
        return lat is not None and lon is not None and dist_km(args.lat, args.lon, lat, lon) <= args.radius

    navaids = []
    with open(args.src / "navaids.csv", newline="") as f:
        for r in csv.DictReader(f):
            lat, lon = num(r["latitude_deg"]), num(r["longitude_deg"])
            if r["type"] not in ("VOR", "VOR-DME", "VORTAC", "DME", "NDB", "NDB-DME", "TACAN") or not near(lat, lon):
                continue
            khz = num(r["frequency_khz"])
            navaids.append({
                "id": r["ident"], "name": r["name"], "type": r["type"], "lat": round(lat, 5), "lon": round(lon, 5),
                "khz": int(khz) if khz else None, "ch": r["dme_channel"] or None, "cc": r["iso_country"],
            })

    airports = {}
    with open(args.src / "airports.csv", newline="") as f:
        for r in csv.DictReader(f):
            lat, lon = num(r["latitude_deg"]), num(r["longitude_deg"])
            if r["type"] not in ("large_airport", "medium_airport") or not near(lat, lon):
                continue
            airports[r["ident"]] = {
                "icao": r["ident"], "iata": r["iata_code"] or None, "name": r["name"], "type": r["type"].split("_")[0],
                "lat": round(lat, 5), "lon": round(lon, 5), "elev": int(num(r["elevation_ft"]) or 0), "cc": r["iso_country"],
                "rwy": [],
            }
    with open(args.src / "runways.csv", newline="") as f:
        for r in csv.DictReader(f):
            a = airports.get(r["airport_ident"])
            if not a or r["closed"] == "1":
                continue
            ends = [num(r[k]) for k in ("le_latitude_deg", "le_longitude_deg", "he_latitude_deg", "he_longitude_deg")]
            if None in ends:
                continue
            a["rwy"].append({"id": f"{r['le_ident']}/{r['he_ident']}", "len_ft": int(num(r["length_ft"]) or 0),
                             "ends": [[round(ends[0], 5), round(ends[1], 5)], [round(ends[2], 5), round(ends[3], 5)]]})

    data = {
        "source": "OurAirports (public domain), https://ourairports.com/data/",
        "home": [args.lat, args.lon], "radius_km": args.radius,
        "navaids": sorted(navaids, key=lambda n: (n["id"], n["type"])),
        "airports": sorted(airports.values(), key=lambda a: a["icao"]),
    }
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"{args.out}: маяков {len(navaids)}, аэродромов {len(airports)}, "
          f"полос {sum(len(a['rwy']) for a in airports.values())}, {args.out.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
