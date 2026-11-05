"""Суда (AIS) для веб-страницы: данные берём у AIS-catcher, который сам держит список судов.

AIS-catcher работает отдельным сервисом и только когда донгл переключён на суда
(ais-catcher.service конфликтует с readsb.service). Его веб-сервер слушает 127.0.0.1,
nebo добавляет расстояние и азимут от дома (сам AIS-catcher их без своего приёма не считает)
и отдаёт странице /ships.
"""
import json
import time
import urllib.error
import urllib.request

from .models import bearing_deg, distance_km

TIMEOUT = 3.0


def _get(url: str):
    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
        return json.load(resp)


def _plane_receiver_busy(app) -> bool:
    """Донгл сейчас слушает самолёты: свой приёмник readsb отдаёт свежие данные."""
    if app.cfg.source.type != "readsb":
        return False
    with app.lock:
        last_ok = app.last_ok
    return last_ok is not None and time.time() - last_ok < 30


def ships_json(app) -> dict:
    cfg = app.cfg
    home = {"lat": cfg.location.lat, "lon": cfg.location.lon}
    base = cfg.web.ais_url.rstrip("/")
    try:
        data = _get(f"{base}/api/ships.json")
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = "planes" if _plane_receiver_busy(app) else "off"
        r = app.receiver
        if r and r.switched_to == "ships" and (r.since_switch() or 1e9) < 60:
            reason = "switching"  # донгл только что отдан судам, AIS-catcher ещё стартует
        return {"ok": False, "home": home, "reason": reason, "error": str(e),
                "switch": r is not None, "own_receiver": cfg.source.type == "readsb"}
    try:
        stat = _get(f"{base}/api/stat.json")
    except (urllib.error.URLError, OSError, ValueError):
        stat = {}
    ships = []
    for s in data.get("ships") or []:
        if s.get("lat") is None or s.get("lon") is None:
            continue
        s["dist_km"] = round(distance_km(home["lat"], home["lon"], s["lat"], s["lon"]), 3)
        s["bearing_deg"] = round(bearing_deg(home["lat"], home["lon"], s["lat"], s["lon"]), 1)
        ships.append(s)
    ships.sort(key=lambda s: s["dist_km"])
    return {
        "ok": True, "now": time.time(), "home": home,
        "since_switch": app.receiver.since_switch() if app.receiver else None,
        "total": data.get("count", len(ships)),
        "stat": {k: stat.get(k) for k in ("session", "last_minute", "last_hour") if k in stat},
        "flag_url": cfg.media.flag_url if cfg.media.flags else None,
        "ships": ships,
    }


def ship_path_json(app, mmsi: int) -> list:
    """След судна, старые точки первыми: [[lat, lon], ...]."""
    try:
        data = _get(f"{app.cfg.web.ais_url.rstrip('/')}/api/path.json?{mmsi}")
    except (urllib.error.URLError, OSError, ValueError):
        return []
    points = data.get(str(mmsi)) or []  # AIS-catcher отдаёт новые первыми: [lat, lon, с, по]
    return [[p[0], p[1]] for p in reversed(points)]
