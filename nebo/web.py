"""Веб-страница nebo: всё, что принимает антенна, с маршрутами, фото и данными приёмника.

Работает в том же процессе, что и табло, и берёт данные у App — второй раз ничего
не запрашивает. Только чтение, без управления табло.

    /                     страница самолётов (nebo/www/index.html)
    /ships                страница судов (nebo/www/ships.html), данные — у AIS-catcher, см. ships.py
    /ar                   AR для телефона: камера + компас + GPS (только по HTTPS)
    /nebo.css             общие стили страниц
    /api/state            все принятые борта с маршрутами, типами, следами; что сейчас на табло
    /api/ships            суда с расстоянием от дома и статистика AIS-catcher (или почему их нет)
    /api/ship/{mmsi}/path след судна
    /api/ship/{mmsi}/photo?imo=N  описание фото судна с Wikimedia Commons (снимок грузит браузер)
    /api/plane/{hex}      один борт: сырая запись приёмника, описание фото (нет у planespotters — фото борта
                          с Commons, нет и там — такого же типа из Википедии), весь записанный маршрут
    /api/tracks           маршруты всех за последние 2 часа, включая пропавших (прорежены)
    ?view=1               страница видна пользователю: /api/state просит донгл для самолётов,
                          /api/ships — для судов (receiver.py)
    /shapes.json          силуэты для карты
    /nav.json             радиомаяки и аэродромы с полосами (nebo/data/nav.json из OurAirports)
    /aip.json             путевые точки, трассы и граница района Никосии (nebo/data/aip_lccc.json из AIP Cyprus;
                          в репозитории его нет — AIP нельзя распространять, собирается tools/build_aip.py)
    /data/{name}.json     файлы readsb (stats, receiver, outline), если источник — локальный файл

Фото по условиям planespotters: браузер грузит снимок прямо с их сервера, на странице
рядом с ним — имя фотографа и ссылка на страницу снимка. Сервер сами снимки не трогает.
"""
import http.server
import json
import logging
import re
import threading
import time
import urllib.parse
from pathlib import Path

from .countries import country_of
from .media import FOUND as PHOTO_FOUND, MISSING as PHOTO_MISSING
from .routes import FOUND
from .shapes import shapes_json
from .ships import ship_path_json, ships_json
from .tracks import GAP, KEEP

log = logging.getLogger(__name__)

WWW = Path(__file__).parent / "www"
NAV_JSON = Path(__file__).parent / "data" / "nav.json"
AIP_JSON = Path(__file__).parent / "data" / "aip_lccc.json"
TRAIL_POINTS = 120  # столько точек следа отдаём на борт, не больше
TRACK_POINTS = 800  # весь маршрут выбранного борта
OVERVIEW_POINTS = 150  # маршрут в обзоре «все следы»
PLANE_RE = re.compile(r"^/api/plane/([0-9a-f]{6})$")
SHIP_PATH_RE = re.compile(r"^/api/ship/(\d{9})/path$")
SHIP_PHOTO_RE = re.compile(r"^/api/ship/(\d{9})/photo$")
PAGES = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
         "/ships": ("ships.html", "text/html; charset=utf-8"), "/ar": ("ar.html", "text/html; charset=utf-8"),
         "/nebo.css": ("nebo.css", "text/css; charset=utf-8")}
DATA_RE = re.compile(r"^/data/(stats|receiver|outline|aircraft)\.json$")


def _airport(a) -> dict:
    return {"code": a.code, "iata": a.iata, "icao": a.icao, "city": a.city,
            "country": a.country, "lat": a.lat, "lon": a.lon}


def _thin(points: list, limit: int) -> list:
    """Прореживает след до limit точек, сохраняя последнюю."""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[int(i * step)] for i in range(limit - 1)] + [points[-1]]


def plane_json(app, p) -> dict:
    ac = p.ac
    route = None
    if p.route_state == FOUND and p.route:
        route = {"ok": p.route_ok, "airline": p.route.airline,
                 "origin": _airport(p.route.origin), "destination": _airport(p.route.destination)}
    elev = ac.elevation_deg()
    cpa = ac.closest_approach()
    raw = ac.raw
    return {
        "hex": ac.hex, "callsign": ac.callsign, "reg": ac.registration, "reg_cc": country_of(ac.hex), "type": ac.type_code,
        "type_name": p.type_name, "desc": raw.get("desc"), "airline": p.airline,
        "op": app.info.operator_code(ac.callsign), "category": ac.category, "shape": p.shape,
        "lat": ac.lat, "lon": ac.lon, "alt_ft": ac.alt_ft, "ground": ac.on_ground,
        "gs_kt": ac.gs_kt, "track": ac.track, "vrate_fpm": ac.vrate_fpm,
        "squawk": ac.squawk, "emergency": ac.emergency,
        "dist_km": round(ac.dist_km, 2), "bearing": round(ac.bearing, 1),
        "elev": None if elev is None else round(elev, 1),
        "cpa": None if cpa is None else [round(cpa[0], 1), round(cpa[1], 1)],
        "on_board": app.on_board(ac),
        "route_state": p.route_state, "route": route,
        "rssi": raw.get("rssi"), "messages": raw.get("messages"),
        "seen": raw.get("seen"), "seen_pos": raw.get("seen_pos"),
        "trail": [[round(la, 5), round(lo, 5)] for la, lo in _thin(p.trail, TRAIL_POINTS)],
    }


def state_json(app) -> dict:
    planes = app._planes(everything=True)
    with app.lock:
        last_ok, error = app.last_ok, app.error
    cfg = app.cfg
    return {
        "now": time.time(),
        "home": {"lat": cfg.location.lat, "lon": cfg.location.lon},
        "units": cfg.display.units,
        "source": app.source.name,
        "total": app.source.last_total,  # с координатами и без
        "last_ok": last_ok,
        "error": error,
        "screen": {"enabled": app.screen is not None, "view": app.view, "hex": app.shown_hex, "follow": app.follow},
        "media": {"logo_url": cfg.media.logo_url if cfg.media.logos else None,
                  "flag_url": cfg.media.flag_url if cfg.media.flags else None},
        "planes": [plane_json(app, p) for p in planes],
        "receiver": receiver_json(app),
    }


def ship_photo_json(app, mmsi: int, query: str) -> dict:
    if not app.ship_photos:
        return {"state": "off"}
    imo = urllib.parse.parse_qs(query).get("imo", [""])[0]
    state, meta = app.ship_photos.get(int(imo) if imo.isdigit() and int(imo) > 0 else None, mmsi)
    return {"state": state, **(meta or {})}


def receiver_json(app) -> dict | None:
    r = app.receiver
    if r is None:
        return None
    return {"mode": r.mode(), "since_switch": r.since_switch(), "switched_to": r.switched_to}


def _segments(points: list, limit: int) -> list:
    """Маршрут кусками без разрывов приёма: [[[lat, lon], ...], ...]."""
    pts = _thin(points, limit)
    segs, cur = [], []
    for i, p in enumerate(pts):
        if cur and p[0] - pts[i - 1][0] > GAP:
            segs.append(cur)
            cur = []
        cur.append([p[1], p[2]])
    if cur:
        segs.append(cur)
    return segs


def tracks_json(app) -> dict:
    now = time.time()
    with app.lock:
        live = {ac.hex for ac in app.aircraft}
    return {"now": now, "gap": GAP, "keep": KEEP, "tracks": [
        {"hex": t.hex, "callsign": t.callsign, "reg": t.reg, "type": t.type, "route": t.route,
         "first": t.first, "last": t.last, "live": t.hex in live, "segments": _segments(t.points, OVERVIEW_POINTS)}
        for t in app.tracks.recent(now - KEEP)]}


def plane_detail_json(app, hex_id: str) -> dict | None:
    with app.lock:
        ac = next((a for a in app.aircraft if a.hex == hex_id), None)
    if ac is None:
        return None
    photo = {"state": "off"}
    if app.photos:
        state, meta = app.photos.meta.get(hex_id)  # только описание; снимок грузит браузер
        photo = {"state": state, **(meta if state == PHOTO_FOUND else {})}
    # у planespotters снимка нет — сам борт с Commons, если и там нет — такой же тип
    reg_state = PHOTO_MISSING
    if photo["state"] in (PHOTO_MISSING, "off") and app.reg_photos and ac.registration:
        reg_state, meta = app.reg_photos.get(ac.registration)
        photo["reg_photo"] = {"state": reg_state, **(meta if reg_state == PHOTO_FOUND else {})}
    if photo["state"] in (PHOTO_MISSING, "off") and reg_state == PHOTO_MISSING and app.type_photos:
        name = (app.info.types.get(ac.type_code or "") or [None])[0]  # как в базе: «BOEING 737 MAX 8»
        state, meta = app.type_photos.get(ac.type_code, name)
        photo["type_photo"] = {"state": state, **(meta if state == PHOTO_FOUND else {})}
    t = app.tracks.get(hex_id)
    track = None
    if t:  # [t, lat, lon, alt_ft]; разрывы (больше gap секунд) страница рисует пунктиром
        track = {"gap": GAP, "callsigns": t.callsigns, "route": t.route,
                 "points": [p[:4] for p in _thin(t.points, TRACK_POINTS)]}
    return {"hex": hex_id, "raw": ac.raw, "photo": photo, "track": track}


class Handler(http.server.BaseHTTPRequestHandler):
    app = None
    data_dir: Path | None = None
    server_version = "nebo"

    def do_GET(self):
        path, _, query = self.path.partition("?")
        visible = urllib.parse.parse_qs(query).get("view") == ["1"]
        receiver = self.app.receiver if self.app else None
        try:
            if path in PAGES:
                name, ctype = PAGES[path]
                return self._send(200, (WWW / name).read_bytes(), ctype)
            if path == "/api/state":
                if visible and receiver:
                    receiver.planes_page()
                return self._json(state_json(self.app))
            if path == "/api/ships":
                if visible and receiver:
                    receiver.ships_page()
                return self._json(ships_json(self.app))
            if path == "/api/tracks":
                return self._json(tracks_json(self.app))
            m = SHIP_PATH_RE.match(path)
            if m:
                return self._json(ship_path_json(self.app, int(m.group(1))))
            m = SHIP_PHOTO_RE.match(path)
            if m:
                return self._json(ship_photo_json(self.app, int(m.group(1)), query))
            if path == "/shapes.json":
                return self._json(shapes_json(), cache=True)
            if path == "/nav.json":
                return self._send(200, NAV_JSON.read_bytes(), "application/json", cache=True)
            if path == "/aip.json":
                if not AIP_JSON.exists():
                    return self.send_error(404)
                return self._send(200, AIP_JSON.read_bytes(), "application/json", cache=True)
            m = PLANE_RE.match(path)
            if m:
                detail = plane_detail_json(self.app, m.group(1))
                return self._json(detail) if detail else self.send_error(404, "no such aircraft")
            m = DATA_RE.match(path)
            if m and self.data_dir:
                f = self.data_dir / f"{m.group(1)}.json"
                if f.is_file():
                    return self._send(200, f.read_bytes(), "application/json")
            self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("веб: %s", path)
            self.send_error(500)  # текст статуса HTTP — только ASCII

    def _json(self, obj, cache=False):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
        self._send(200, body, "application/json; charset=utf-8", cache)

    def _send(self, code: int, body: bytes, ctype: str, cache: bool = False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # страница опрашивает API каждые пару секунд — в журнал не пишем


def start(app, bind: str, port: int) -> http.server.ThreadingHTTPServer:
    """Запускает сервер в фоновом потоке."""
    Handler.app = app
    path = app.cfg.source.path
    if app.cfg.source.type == "readsb" and not path.startswith(("http://", "https://")):
        Handler.data_dir = Path(path).parent
    srv = http.server.ThreadingHTTPServer((bind, port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="web", daemon=True).start()
    log.info("веб-страница: http://%s:%d/", bind, port)
    return srv
