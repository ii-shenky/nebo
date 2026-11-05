#!/usr/bin/env python3
"""Обновляет nebo/data/types.json и operators.json из wiedehopf/tar1090-db
(https://github.com/wiedehopf/tar1090-db, данные — база Mictronics, https://www.mictronics.de).

    python3 tools/update_tar1090db.py

types.json:     код типа ICAO → [название, описание ICAO (L2J), категория турбулентности]
operators.json: код авиакомпании ICAO → [название, страна]

Файлы — те же данные, что у tar1090-db, без правок, только без лишних полей. Свои дополнения
к справочникам в nebo — в коде, не в этих файлах (например, nebo/countries.py, TYPE_ARTICLES в media.py).
"""
import argparse
import gzip
import json
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/wiedehopf/tar1090-db/master/db/"
DATA = Path(__file__).resolve().parent.parent / "nebo" / "data"


def fetch(name: str):
    req = urllib.request.Request(BASE + name, headers={"User-Agent": "nebo (update_tar1090db.py)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)  # .js в репозитории — gzip


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DATA, help="папка для types.json и operators.json")
    args = ap.parse_args()
    types = fetch("icao_aircraft_types2.js")
    operators = {code: [o["n"], o["c"]] for code, o in fetch("operators.js").items()}
    for name, data in (("types.json", types), ("operators.json", operators)):
        path = args.out / name
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        print(f"{path}: {len(data)} записей")


if __name__ == "__main__":
    main()
