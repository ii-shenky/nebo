#!/usr/bin/env python3
"""Демо приёма AIS (суда) на Mac: запускает AIS-catcher с RTL-SDR и раздаёт по HTTP
страницу с результатами приёма и ships.json.

    AIS-catcher собирается из исходников (github.com/jvde-github/AIS-catcher), в brew его нет.
    python3 tools/demo/ships.py --lat 34.68 --lon 33.04

Страница: http://localhost:8081/
JSON:     http://localhost:8081/data/ships.json, /data/stat.json (проксируются с AIS-catcher)

Донгл один: пока работает dump1090 (serve.py), AIS-catcher его не получит.
"""
import argparse
import functools
import http.server
import shutil
import signal
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
print = functools.partial(print, flush=True)


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def dongle_busy() -> str | None:
    out = subprocess.run(["pgrep", "-fl", "dump1090|AIS-catcher"], capture_output=True, text=True).stdout
    return out.strip() or None


def start_ais(lat: float, lon: float, ais_port: int, ppm: int, gain: str) -> subprocess.Popen:
    exe = shutil.which("AIS-catcher")
    if not exe:
        sys.exit("AIS-catcher не найден в PATH: собрать из исходников, cmake --install")
    cmd = [exe, "-X", "off", "-d:0", "-o", "0", "-v", "60", "-Z", str(lat), str(lon),  # -X off: не делиться с aiscatcher.org
           "-N", str(ais_port), "-gr", "RTLAGC", "on", "TUNER", gain]
    if ppm:
        cmd += ["-p", str(ppm)]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


class Handler(http.server.SimpleHTTPRequestHandler):
    ais_port = 8100

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html", "/ships.html"):
            return self._send(200, (HERE / "ships.html").read_bytes(), "text/html; charset=utf-8")
        if path in ("/data/ships.json", "/data/stat.json"):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.ais_port}{path[5:]}", timeout=3) as r:
                    return self._send(200, r.read(), "application/json")
            except (urllib.error.URLError, OSError) as e:
                return self._send(502, f"AIS-catcher не отвечает: {e}".encode(), "text/plain; charset=utf-8")
        self.send_error(404)

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if not self.path.startswith("/data/"):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, required=True, help="широта приёмника")
    ap.add_argument("--lon", type=float, required=True, help="долгота приёмника")
    ap.add_argument("--port", type=int, default=8081, help="порт HTTP страницы (8081)")
    ap.add_argument("--ais-port", type=int, default=8100, help="порт встроенного веб-сервера AIS-catcher (8100), там его собственная карта")
    ap.add_argument("--ppm", type=int, default=0, help="поправка частоты донгла, ppm; AIS-catcher показывает измеренную в stat.json")
    ap.add_argument("--gain", default="auto", help="усиление тюнера, дБ или auto")
    ap.add_argument("--no-ais-catcher", action="store_true", help="не запускать AIS-catcher, только проксировать --ais-port")
    args = ap.parse_args()

    proc = None
    if not args.no_ais_catcher:
        busy = dongle_busy()
        if busy:
            sys.exit(f"донгл занят, сначала остановить:\n{busy}")
        proc = start_ais(args.lat, args.lon, args.ais_port, args.ppm, args.gain)
    Handler.ais_port = args.ais_port

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"страница:     http://localhost:{args.port}/")
    print(f"json:         http://{lan_ip()}:{args.port}/data/ships.json")
    print(f"AIS-catcher:  http://localhost:{args.ais_port}/ (его собственная карта)")

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


if __name__ == "__main__":
    main()
