"""Источники данных о самолётах. Все отдают список Aircraft, отсортированный по расстоянию."""
import json
import math
import time
import urllib.request

from .config import Config
from .models import Aircraft, parse_aircraft

USER_AGENT = "nebo/0.1 (Raspberry Pi flight board)"


def http_get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class Source:
    name = "?"

    def __init__(self, cfg: Config):
        self.loc = cfg.location
        self.src = cfg.source
        self.last_total = 0  # сколько бортов в последнем ответе, включая без координат

    def fetch_raw(self) -> list[dict]:
        raise NotImplementedError

    def fetch(self) -> list[Aircraft]:
        """Все борта с координатами, ближние первыми. Радиус и «на земле» отбирает App:
        табло показывает только круг вокруг дома, веб-страница — всё, что принято."""
        raw = self.fetch_raw()
        self.last_total = len(raw)
        result = [ac for ac in (parse_aircraft(r, self.loc.lat, self.loc.lon) for r in raw) if ac]
        result.sort(key=lambda a: a.dist_km)
        return result


class AdsbLolSource(Source):
    """Публичный API adsb.lol: самолёты в круге вокруг точки."""
    name = "adsb.lol"

    def fetch_raw(self) -> list[dict]:
        url = self.src.url.format(
            lat=round(self.loc.lat, 4),
            lon=round(self.loc.lon, 4),
            radius_nm=math.ceil(self.loc.radius_km / 1.852),
        )
        return http_get_json(url, self.src.timeout).get("ac") or []


class ReadsbSource(Source):
    """Локальный приёмник: aircraft.json от readsb/dump1090 (путь к файлу или http-адрес)."""
    name = "readsb"
    max_file_age = 60.0

    def fetch_raw(self) -> list[dict]:
        path = self.src.path
        if path.startswith(("http://", "https://")):
            data = http_get_json(path, self.src.timeout)
        else:
            with open(path) as f:
                data = json.load(f)
        now = data.get("now")
        if now and time.time() - now > self.max_file_age:
            raise RuntimeError(f"{path} не обновлялся {time.time() - now:.0f} с")
        return data.get("aircraft") or []


def make_source(cfg: Config) -> Source:
    kinds = {"adsblol": AdsbLolSource, "readsb": ReadsbSource}
    try:
        return kinds[cfg.source.type](cfg)
    except KeyError:
        raise ValueError(f"[source] type: неизвестный источник {cfg.source.type!r}, есть {sorted(kinds)}")
