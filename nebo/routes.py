"""Маршруты рейсов (откуда → куда) по позывному через adsbdb.com, с кешем на диске."""
import json
import logging
import os
import queue
import threading
import time
import urllib.error
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import distance_km
from .sources import http_get_json

log = logging.getLogger(__name__)

TTL_FOUND = 12 * 3600
TTL_MISSING = 3 * 3600
RETRY_AFTER_ERROR = 60


@dataclass(frozen=True)
class Airport:
    iata: str | None
    icao: str | None
    city: str | None
    lat: float
    lon: float
    country: str | None = None  # ISO-код страны, для флага

    @property
    def code(self) -> str:
        return self.iata or self.icao or "?"


@dataclass(frozen=True)
class Route:
    airline: str | None
    origin: Airport
    destination: Airport

    def plausible(self, lat: float, lon: float) -> bool:
        """Похож ли маршрут на правду для самолёта в этой точке.

        База маршрутов бывает устаревшей (позывной переназначили), поэтому
        проверяем, что самолёт не слишком далеко от линии между аэропортами.
        """
        o, d = self.origin, self.destination
        direct = distance_km(o.lat, o.lon, d.lat, d.lon)
        via = distance_km(o.lat, o.lon, lat, lon) + distance_km(lat, lon, d.lat, d.lon)
        return via <= direct * 1.25 + 150


def _airport(raw: dict) -> Airport:
    return Airport(
        iata=raw.get("iata_code") or None,
        icao=raw.get("icao_code") or None,
        city=raw.get("municipality") or raw.get("name"),
        lat=raw["latitude"],
        lon=raw["longitude"],
        country=raw.get("country_iso_name") or None,
    )


def _route_from_dict(d: dict) -> Route:
    return Route(d["airline"], Airport(**d["origin"]), Airport(**d["destination"]))


# Состояние поиска маршрута для UI
PENDING, FOUND, MISSING = "pending", "found", "missing"


class RouteResolver:
    def __init__(self, url: str, timeout: float, cache_path: Path | None, on_update=None):
        self.url = url
        self.timeout = timeout
        self.cache_path = cache_path
        self.on_update = on_update
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, Route | None]] = {}
        self._retry_at: dict[str, float] = {}
        self._queued: set[str] = set()
        self._queue: queue.Queue[str] = queue.Queue()
        self._dirty = 0
        self._load()
        threading.Thread(target=self._worker, name="routes", daemon=True).start()

    def get(self, callsign: str | None) -> tuple[str, Route | None]:
        """Возвращает (состояние, маршрут). Если маршрута нет в кеше — ставит в очередь."""
        if not callsign:
            return MISSING, None
        now = time.time()
        with self._lock:
            entry = self._cache.get(callsign)
            if entry and now - entry[0] < (TTL_FOUND if entry[1] else TTL_MISSING):
                return (FOUND, entry[1]) if entry[1] else (MISSING, None)
            if callsign not in self._queued and now >= self._retry_at.get(callsign, 0):
                self._queued.add(callsign)
                self._queue.put(callsign)
            # пока ищем заново — показываем старое значение, если было
            if entry and entry[1]:
                return FOUND, entry[1]
            return PENDING, None

    def _worker(self):
        while True:
            callsign = self._queue.get()
            try:
                route = self._lookup(callsign)
                with self._lock:
                    self._cache[callsign] = (time.time(), route)
                    self._dirty += 1
                log.info("маршрут %s: %s", callsign,
                         f"{route.origin.code} → {route.destination.code}" if route else "не найден")
                if self._dirty >= 5:
                    self._save()
            except Exception as e:
                log.warning("маршрут %s: ошибка %s", callsign, e)
                with self._lock:
                    self._retry_at[callsign] = time.time() + RETRY_AFTER_ERROR
            finally:
                with self._lock:
                    self._queued.discard(callsign)
            if self.on_update:
                self.on_update()
            time.sleep(0.5)  # вежливость к бесплатному API

    def _lookup(self, callsign: str) -> Route | None:
        try:
            data = http_get_json(self.url.format(callsign=callsign), self.timeout)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        fr = (data.get("response") or {}).get("flightroute") if isinstance(data.get("response"), dict) else None
        if not fr or not fr.get("origin") or not fr.get("destination"):
            return None
        airline = (fr.get("airline") or {}).get("name")
        return Route(airline, _airport(fr["origin"]), _airport(fr["destination"]))

    def _load(self):
        if not self.cache_path or not self.cache_path.exists():
            return
        try:
            data = json.loads(self.cache_path.read_text())
            for callsign, (ts, route) in data.items():
                self._cache[callsign] = (ts, _route_from_dict(route) if route else None)
            log.info("кеш маршрутов: %d записей", len(self._cache))
        except Exception as e:
            log.warning("кеш маршрутов не прочитан: %s", e)

    def _save(self):
        if not self.cache_path:
            return
        with self._lock:
            now = time.time()
            data = {cs: (ts, asdict(r) if r else None) for cs, (ts, r) in self._cache.items()
                    if now - ts < (TTL_FOUND if r else TTL_MISSING)}
            self._dirty = 0
        tmp = self.cache_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False))
            os.replace(tmp, self.cache_path)
        except OSError as e:
            log.warning("кеш маршрутов не записан: %s", e)

    def close(self):
        self._save()
