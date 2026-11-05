#!/usr/bin/env python3
"""Собирает nebo/data/aip_lccc.json — путевые точки, трассы и границу района полётной информации
Никосии (LCCC) — из XML-исходников eAIP Кипра (AIP Cyprus, ENR 2.1, 3.1, 3.2, 4.1, 4.4).

AIP раздаётся архивом со страницы
https://www.mcw.gov.cy/mcw/DCA/AIS/ais.nsf/All/57B39BFA3276159CC2257C7E00233AA2?OpenDocument
(ссылки sync.com, качаются только браузером). Новая редакция — раз в 28 дней (цикл AIRAC).

    mkdir /tmp/aip && cd /tmp/aip && for s in 2.1 3.1 3.2 4.1 4.4; do
        unzip -j -o ~/Downloads/CY_AIP_AIRAC_*.zip "*/eAIP/LC-ENR-$s-en-GB.xml"; done
    python3 -I tools/build_aip.py /tmp/aip nebo/data/aip_lccc.json --airac 2026-10-01

Данные — для картинки на табло и странице, не для навигации.
"""
import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

E = "{http://www.eurocontrol.int/xmlns/AIM/eAIP}"
NM = 1.852


def text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def coord(s: str) -> float:
    """315639N, 0335900E, 3150N, 03359E, 315639.12N → градусы."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([NSEW])", s.strip())
    if not m:
        raise ValueError(f"координата {s!r}")
    num, hemi = m.groups()
    whole, _, frac = num.partition(".")
    deg_len = 3 if hemi in "EW" else 2
    d, rest = int(whole[:deg_len]), whole[deg_len:]
    mins = int(rest[:2]) if len(rest) >= 2 else 0
    secs = float(rest[2:] + ("." + frac if frac else "")) if len(rest) > 2 else 0.0
    v = d + mins / 60 + secs / 3600
    return -v if hemi in "SW" else v


def level_ft(s: str) -> float:
    """«FL 035» → 3500, «700 FT ALT» → 700, «UNL» → бесконечность (для выбора нижней и верхней границы)."""
    m = re.search(r"FL\s*(\d+)", s)
    if m:
        return int(m.group(1)) * 100
    m = re.search(r"(\d+)\s*FT", s)
    return int(m.group(1)) if m else math.inf


def bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    return math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                   math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360


def destination(lat, lon, brg, km) -> tuple[float, float]:
    r, p1, l1, b = km / 6371, math.radians(lat), math.radians(lon), math.radians(brg)
    p2 = math.asin(math.sin(p1) * math.cos(r) + math.cos(p1) * math.sin(r) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(r) * math.cos(p1), math.cos(r) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def boundary(desc: str) -> list:
    """Граница из текста AIP: точки через « - » и дуги «then a (counter-)clockwise arc radius N NM centered on …»."""
    pts = []
    tokens = re.findall(r"(then a (counter-)?clockwise arc radius ([\d.]+) NM centered on (\d+[NS]) (\d+[EW]))|(\d+[NS]) (\d+[EW])", desc)
    pending_arc = None
    for arc, ccw, radius, clat, clon, lat, lon in tokens:
        if arc:
            pending_arc = (bool(ccw), float(radius) * NM, coord(clat), coord(clon))
            continue
        p = (coord(lat), coord(lon))
        if pending_arc and pts:
            ccw, rkm, cla, clo = pending_arc
            a0, a1 = bearing(cla, clo, *pts[-1]), bearing(cla, clo, *p)
            sweep = (a0 - a1) % 360 if ccw else (a1 - a0) % 360
            steps = max(2, int(sweep / 5))
            for i in range(1, steps):
                a = a0 - sweep * i / steps if ccw else a0 + sweep * i / steps
                pts.append(destination(cla, clo, a, rkm))
            pending_arc = None
        pts.append(p)
    return [[round(a, 5), round(b, 5)] for a, b in pts]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="папка с LC-ENR-*.xml")
    ap.add_argument("out", type=Path)
    ap.add_argument("--airac", required=True, help="дата вступления в силу, например 2026-10-01")
    args = ap.parse_args()

    def load(section):
        return ET.parse(args.src / f"LC-ENR-{section}-en-GB.xml").getroot()

    points, by_id = [], {}
    for dp in load("4.4").iter(E + "Designated-point"):
        name = text(dp.find(E + "Designated-point-ident"))
        remark = text(dp.find(E + "Designated-point-remarks"))
        fra = sorted(set(re.findall(r"FRA \((I|EX|E|X|A|D|AD)\)", remark)))
        bdry = re.search(r"LCCC/(\w{4}) BDRY", remark)
        p = {"id": name, "lat": round(coord(text(dp.find(E + "Latitude"))), 5),
             "lon": round(coord(text(dp.find(E + "Longitude"))), 5),
             "routes": text(dp.find(E + "ATS-Routes")) or None,
             "fra": fra or None, "bdry": bdry.group(1) if bdry else None,
             "terminal": bool(re.search(r"\b(SID|STAR|CTR)\b", remark)), "remark": remark or None}
        points.append(p)
        by_id[dp.get("id")] = p
    for n in load("4.1").iter(E + "Navaid"):  # трассы ссылаются и на маяки VOR/DME
        by_id[n.get("id")] = {"id": text(n.find(E + "Navaid-ident")),
                              "lat": round(coord(text(n.find(E + "Latitude"))), 5),
                              "lon": round(coord(text(n.find(E + "Longitude"))), 5)}

    routes = []
    for section in ("3.1", "3.2"):
        for rt in load(section).iter(E + "Route"):
            seq = [by_id[sp.get("Ref")] for sp in rt.iter(E + "Significant-point-reference")]
            segs = list(rt.iter(E + "Route-segment"))
            lowers = [v for v in (text(s.find(E + "Route-segment-lower")) for s in segs) if v]
            uppers = [v for v in (text(s.find(E + "Route-segment-upper")) for s in segs) if v]
            lower = min(lowers, key=level_ft, default="")
            upper = max(uppers, key=level_ft, default="")
            routes.append({"id": text(rt.find(E + "Route-designator")), "upper_space": section == "3.2",
                           "lower": lower or None, "upper": upper or None,
                           "pts": [[p["lat"], p["lon"]] for p in seq], "names": [p["id"] for p in seq]})

    fir = []
    root = load("2.1")
    for el in root.iter():
        t = text(el)
        # NICOSIA FIR: граница начинается и кончается точкой 3150N 03359E; берём самый внутренний элемент
        if re.match(r"^(NICOSIA FIR )?3150N 03359E .*arc", t) and len(t) < 900:
            fir = boundary(t)
            break

    data = {"source": "AIP Cyprus (Department of Civil Aviation), не для навигации", "airac": args.airac,
            "fir": fir, "points": sorted(points, key=lambda p: p["id"]), "routes": routes}
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"{args.out}: точек {len(points)}, трасс {len(routes)}, граница района — {len(fir)} точек, "
          f"{args.out.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
