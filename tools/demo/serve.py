#!/usr/bin/env python3
"""Демо приёма ADS-B на Mac: запускает dump1090-fa с RTL-SDR и раздаёт по HTTP
страницу с результатами приёма и aircraft.json для nebo.

    brew install dump1090-fa
    python3 tools/demo/serve.py --lat 34.68 --lon 33.04

Страница: http://localhost:8080/
Для nebo:  [source] type = "readsb", path = "http://<mac>:8080/data/aircraft.json"
Как adsb.lol (для Nebo Deck и любого клиента readsb-API):
    GET /v2/lat/{lat}/lon/{lon}/dist/{nm}  или  /v2/point/{lat}/{lon}/{nm}
    → {"ac": [...], "now": мс, "total": n, "msg": "No error"}; у бортов добавлены dst (мор. мили)
    и dir (азимут). Радиус в морских милях. 503, если dump1090 не обновлял файл больше 60 с.

Порты dump1090 по умолчанию смещены на 31xxx (30001 на этом Mac занят IDEA).
"""
import argparse
import functools
import http.server
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # nebo.shapes: формы силуэтов для страницы
from nebo.shapes import shapes_json  # noqa: E402
print = functools.partial(print, flush=True)


STALE_SEC = 60
API_RE = re.compile(r"^/v2/(?:lat/(-?[\d.]+)/lon/(-?[\d.]+)/dist/([\d.]+)|point/(-?[\d.]+)/(-?[\d.]+)/([\d.]+))/?$")


def distance_nm_and_bearing(lat1, lon1, lat2, lon2) -> tuple[float, float]:
    r = math.pi / 180
    dlat, dlon = (lat2 - lat1) * r, (lon2 - lon1) * r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin(dlon / 2) ** 2
    nm = 2 * 3440.065 * math.asin(math.sqrt(a))
    y = math.sin(dlon) * math.cos(lat2 * r)
    x = math.cos(lat1 * r) * math.sin(lat2 * r) - math.sin(lat1 * r) * math.cos(lat2 * r) * math.cos(dlon)
    return nm, (math.degrees(math.atan2(y, x)) + 360) % 360


def adsb_lol_reply(data: dict, lat: float, lon: float, radius_nm: float) -> dict:
    """Ответ в формате adsb.lol /v2 (readsb --net-api): борты в круге, с dst и dir."""
    ac = []
    for a in data.get("aircraft") or []:
        if a.get("lat") is None or a.get("lon") is None:
            continue
        nm, brg = distance_nm_and_bearing(lat, lon, a["lat"], a["lon"])
        if nm > radius_nm:
            continue
        ac.append({**a, "dst": round(nm, 3), "dir": round(brg, 1)})
    ac.sort(key=lambda a: a["dst"])
    now_ms = int((data.get("now") or time.time()) * 1000)
    return {"ac": ac, "msg": "No error", "now": now_ms, "total": len(ac), "ctime": now_ms, "ptime": 0}


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def start_dump1090(json_dir: Path, lat: float, lon: float, gain: str | None, ppm: int, net_base: int) -> subprocess.Popen:
    exe = shutil.which("dump1090")
    if not exe:
        sys.exit("dump1090 не найден: brew install dump1090-fa")
    cmd = [
        exe, "--device-type", "rtlsdr", "--quiet",
        "--write-json", str(json_dir), "--write-json-every", "1",
        "--json-location-accuracy", "2", "--lat", str(lat), "--lon", str(lon),
        "--net", "--net-bind-address", "0.0.0.0",
        "--net-ri-port", "0", "--net-bi-port", "0",
        "--net-ro-port", str(net_base + 2), "--net-sbs-port", str(net_base + 3),
        "--net-bo-port", str(net_base + 5),
    ]
    if gain:
        cmd += ["--gain", gain]
    if ppm:
        cmd += ["--ppm", str(ppm)]
    log = open(json_dir / "dump1090.log", "w")
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)


class Handler(http.server.SimpleHTTPRequestHandler):
    json_dir: Path = Path(".")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send_file(HERE / "index.html", "text/html; charset=utf-8")
        if path.startswith("/data/") and path.endswith(".json") and "/" not in path[6:]:
            return self._send_file(self.json_dir / path[6:], "application/json")
        if path == "/shapes.json":
            return self._send(200, json.dumps(shapes_json()).encode(), "application/json")
        m = API_RE.match(path)
        if m:
            lat, lon, nm = (float(v) for v in (m.group(1, 2, 3) if m.group(1) else m.group(4, 5, 6)))
            return self._send_api(lat, lon, nm)
        self.send_error(404)

    def _send_api(self, lat: float, lon: float, radius_nm: float):
        try:
            data = json.loads((self.json_dir / "aircraft.json").read_bytes())
        except (FileNotFoundError, ValueError):
            return self.send_error(503, "aircraft.json not written yet")  # статус HTTP — только ASCII
        age = time.time() - (data.get("now") or 0)
        if age > STALE_SEC:
            return self.send_error(503, f"dump1090 data is {age:.0f} s old")
        body = json.dumps(adsb_lol_reply(data, lat, lon, min(radius_nm, 250)), ensure_ascii=False).encode()
        self._send(200, body, "application/json")

    def _send_file(self, p: Path, ctype: str):
        try:
            body = p.read_bytes()
        except FileNotFoundError:
            return self.send_error(404, f"{p.name} not written yet")
        self._send(200, body, ctype)

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # тише: не логировать каждый опрос
        if not self.path.startswith(("/data/", "/v2/")):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, required=True, help="широта приёмника")
    ap.add_argument("--lon", type=float, required=True, help="долгота приёмника")
    ap.add_argument("--port", type=int, default=8080, help="порт HTTP (8080)")
    ap.add_argument("--gain", help="усиление тюнера в дБ, по умолчанию адаптивное dump1090")
    ap.add_argument("--ppm", type=int, default=0, help="поправка частоты донгла, ppm (у этого донгла около +31, измерил AIS-catcher)")
    ap.add_argument("--net-base", type=int, default=31000, help="база портов dump1090: +2 raw, +3 SBS, +5 Beast")
    ap.add_argument("--json-dir", type=Path, help="куда dump1090 пишет json (по умолчанию временная папка)")
    ap.add_argument("--no-dump1090", action="store_true", help="не запускать dump1090, только раздавать --json-dir")
    args = ap.parse_args()

    json_dir = args.json_dir or Path(tempfile.mkdtemp(prefix="nebo-demo-"))
    json_dir.mkdir(parents=True, exist_ok=True)
    Handler.json_dir = json_dir

    proc = None
    if not args.no_dump1090:
        proc = start_dump1090(json_dir, args.lat, args.lon, args.gain, args.ppm, args.net_base)
    else:
        (json_dir / "receiver.json").write_text(json.dumps({"lat": args.lat, "lon": args.lon}))

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"json:      {json_dir}")
    print(f"страница:  http://localhost:{args.port}/")
    print(f"для nebo:  http://{lan_ip()}:{args.port}/data/aircraft.json")
    print(f"как adsb.lol: http://{lan_ip()}:{args.port}/v2/lat/{args.lat}/lon/{args.lon}/dist/40")
    if proc:
        print(f"dump1090:  pid {proc.pid}, Beast-выход порт {args.net_base + 5}, SBS {args.net_base + 3}")

    def stop(*_):
        print("\nостанавливаю")
        if proc:
            proc.terminate()
        threading.Thread(target=srv.shutdown).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        srv.serve_forever()
    finally:
        if proc:
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
            if proc.returncode not in (0, -15):
                print(f"dump1090 завершился с кодом {proc.returncode}, см. {json_dir}/dump1090.log")


if __name__ == "__main__":
    main()
