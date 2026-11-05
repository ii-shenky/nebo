#!/usr/bin/env python3
"""Тестовые AIS-сообщения по UDP — проверить страницу судов без антенны и не отбирая донгл у readsb.

    AIS-catcher -x 127.0.0.1 10110 -N 8100 IP_BIND 127.0.0.1 -o 0 -Z 34.68 33.04
    python3 tools/ais_fake.py              # раз в 2 с шлёт позиции и описания выдуманных судов

Суда выдуманные, стоят и ходят у Лимассола. Сообщения 1 (позиция, класс A), 18 (позиция, класс B),
5 (описание судна, класс A), 24 (имя, класс B), 21 (навигационный знак).
"""
import argparse
import math
import socket
import time

SIXBIT = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"

# mmsi, имя, позывной, IMO, тип, длина (нос/корма), ширина (лево/право), осадка, назначение,
# статус, класс, начальная точка, курс, узлы
SHIPS = [
    (209543000, "ELBE TRADER", "5BAB7", 9301234, 70, (150, 30), (14, 14), 8.4, "LIMASSOL", 1, "A", (34.62, 33.08), 245, 0.1),
    (212876000, "AEGEAN BREEZE", "5BQX2", 9512345, 80, (160, 25), (16, 16), 10.1, "PIRAEUS", 0, "A", (34.55, 33.20), 260, 11.5),
    # настоящие IMO/MMSI с фото на Wikimedia Commons — проверить фото судов
    (311000123, "RHAPSODY OF THE SEAS", "C6SE3", 9116864, 60, (250, 29), (16, 16), 7.6, "LIMASSOL", 0, "A", (34.60, 33.30), 300, 14.0),
    (247195700, "TREI FRE", "IFRT", 0, 60, (25, 6), (4, 4), 2.0, "", 5, "A", (34.645, 33.035), 0, 0.0),
    (210111000, "LIMASSOL PILOT", "5BPL1", 0, 50, (12, 3), (2, 2), 1.5, "", 0, "A", (34.645, 33.03), 30, 7.0),
    (210222000, "SEA ANGEL", "", 0, 37, (10, 4), (2, 2), 1.2, "", 8, "B", (34.66, 32.98), 200, 5.5),
    (210333000, "AGIOS NIKOLAOS", "5BFN4", 0, 30, (14, 4), (3, 3), 2.0, "", 7, "A", (34.63, 32.95), 90, 3.2),
    (992106001, "LIMASSOL PORT ENTR", "", 0, None, None, None, None, "", None, "N", (34.6420, 33.0150), None, None),
]


def bits(value: int, n: int) -> str:
    return format(value & ((1 << n) - 1), f"0{n}b")


def text(s: str, chars: int) -> str:
    s = s.upper()[:chars].ljust(chars, "@")
    return "".join(bits(SIXBIT.index(c) if c in SIXBIT else 0, 6) for c in s)


def armor(b: str) -> tuple[str, int]:
    fill = (6 - len(b) % 6) % 6
    b += "0" * fill
    out = ""
    for i in range(0, len(b), 6):
        v = int(b[i:i + 6], 2)
        out += chr(v + 48 if v < 40 else v + 56)
    return out, fill


def sentences(b: str, seq: int, channel="A") -> list[str]:
    payload, fill = armor(b)
    parts = [payload[i:i + 60] for i in range(0, len(payload), 60)]
    result = []
    for i, part in enumerate(parts, 1):
        body = f"AIVDM,{len(parts)},{i},{seq if len(parts) > 1 else ''},{channel},{part},{fill if i == len(parts) else 0}"
        cs = 0
        for ch in body:
            cs ^= ord(ch)
        result.append(f"!{body}*{cs:02X}")
    return result


def pos_lonlat(lat, lon) -> str:
    return bits(round(lon * 600000), 28) + bits(round(lat * 600000), 27)


def msg1(mmsi, status, sog, lat, lon, cog, heading) -> str:
    b = bits(1, 6) + bits(0, 2) + bits(mmsi, 30) + bits(status, 4) + bits(-128, 8)
    b += bits(round(sog * 10), 10) + "1" + pos_lonlat(lat, lon) + bits(round(cog * 10), 12) + bits(round(heading), 9)
    b += bits(int(time.time()) % 60, 6) + bits(0, 2) + bits(0, 3) + "0" + bits(0, 19)
    return b


def msg18(mmsi, sog, lat, lon, cog, heading) -> str:
    b = bits(18, 6) + bits(0, 2) + bits(mmsi, 30) + bits(0, 8) + bits(round(sog * 10), 10) + "1"
    b += pos_lonlat(lat, lon) + bits(round(cog * 10), 12) + bits(round(heading), 9) + bits(int(time.time()) % 60, 6)
    b += bits(0, 2) + "1" + "0" + "1" + "1" + "1" + "0" + "0" + bits(0, 20)
    return b


def msg5(mmsi, imo, callsign, name, shiptype, bow_stern, port_stbd, draught, dest) -> str:
    b = bits(5, 6) + bits(0, 2) + bits(mmsi, 30) + bits(0, 2) + bits(imo, 30) + text(callsign, 7) + text(name, 20)
    b += bits(shiptype, 8) + bits(bow_stern[0], 9) + bits(bow_stern[1], 9) + bits(port_stbd[0], 6) + bits(port_stbd[1], 6)
    t = time.gmtime(time.time() + 86400)
    b += bits(1, 4) + bits(t.tm_mon, 4) + bits(t.tm_mday, 5) + bits(t.tm_hour, 5) + bits(0, 6)
    b += bits(round(draught * 10), 8) + text(dest, 20) + "0" + "0"
    return b


def msg24(mmsi, name, shiptype, callsign, bow_stern, port_stbd) -> list[str]:
    a = bits(24, 6) + bits(0, 2) + bits(mmsi, 30) + bits(0, 2) + text(name, 20)
    b = bits(24, 6) + bits(0, 2) + bits(mmsi, 30) + bits(1, 2) + bits(shiptype, 8) + text("", 7) + text(callsign, 7)
    b += bits(bow_stern[0], 9) + bits(bow_stern[1], 9) + bits(port_stbd[0], 6) + bits(port_stbd[1], 6) + bits(0, 6)
    return [a, b]


def msg21(mmsi, name, lat, lon) -> str:
    b = bits(21, 6) + bits(0, 2) + bits(mmsi, 30) + bits(13, 5) + text(name, 20) + "1" + pos_lonlat(lat, lon)
    b += bits(0, 9) + bits(0, 9) + bits(0, 6) + bits(0, 6) + bits(1, 4) + bits(int(time.time()) % 60, 6) + "0" + bits(0, 8) + "0" + "0" + "0" + "0"
    return b


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=10110)
    ap.add_argument("--every", type=float, default=2.0)
    ap.add_argument("--count", type=int, default=0, help="сколько циклов (0 — бесконечно)")
    args = ap.parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start, seq, n = time.time(), 0, 0
    while True:
        t = time.time() - start
        lines = []
        for mmsi, name, cs, imo, stype, bs, ps, dr, dest, status, cls, (lat0, lon0), cog, sog in SHIPS:
            if cls == "N":
                if n % 5 == 0:
                    lines += sentences(msg21(mmsi, name, lat0, lon0), 0, "B")
                continue
            km = sog * 1.852 * t / 3600
            lat = lat0 + km * math.cos(math.radians(cog)) / 111.2
            lon = lon0 + km * math.sin(math.radians(cog)) / (111.2 * math.cos(math.radians(lat0)))
            if cls == "A":
                lines += sentences(msg1(mmsi, status, sog, lat, lon, cog, cog), 0)
                if n % 5 == 0:
                    seq = seq % 9 + 1
                    lines += sentences(msg5(mmsi, imo, cs, name, stype, bs, ps, dr, dest), seq)
            else:
                lines += sentences(msg18(mmsi, sog, lat, lon, cog, cog), 0, "B")
                if n % 5 == 0:
                    for part in msg24(mmsi, name, stype, cs, bs, ps):
                        lines += sentences(part, 0, "B")
        for line in lines:
            sock.sendto((line + "\r\n").encode(), (args.host, args.port))
        n += 1
        if args.count and n >= args.count:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
