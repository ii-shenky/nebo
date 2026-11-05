"""Модель самолёта и геометрия."""
import math
from dataclasses import dataclass, field

EARTH_RADIUS_KM = 6371.0


def distance_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2) -> float:
    """Начальный азимут из точки 1 на точку 2, 0 = север, по часовой."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(x, y)) % 360


@dataclass
class Aircraft:
    hex: str
    callsign: str | None
    registration: str | None
    type_code: str | None
    lat: float
    lon: float
    alt_ft: int | None  # барометрическая высота; None, если неизвестна или на земле
    on_ground: bool
    gs_kt: float | None
    track: float | None
    vrate_fpm: int | None
    squawk: str | None
    emergency: str | None
    category: str | None  # категория ADS-B: A1 лёгкий … A5 тяжёлый, A7 вертолёт
    dist_km: float  # от дома
    bearing: float  # азимут от дома на самолёт
    raw: dict = field(default_factory=dict, repr=False, compare=False)  # запись как пришла (для веб-страницы)

    def elevation_deg(self) -> float | None:
        """Угол над горизонтом из дома (с поправкой на кривизну Земли)."""
        if self.alt_ft is None:
            return None
        h = self.alt_ft * 0.3048 / 1000
        d = max(self.dist_km, 0.01)
        return math.degrees(math.atan2(h - d * d / (2 * EARTH_RADIUS_KM), d))

    def closest_approach(self) -> tuple[float, float] | None:
        """(через сколько минут, на каком расстоянии в км) самолёт будет ближе всего к дому.

        Считаем, что он летит прямо с текущей скоростью. None — уже удаляется или стоит.
        """
        if not self.gs_kt or self.track is None or self.gs_kt < 30:
            return None
        b, tr = math.radians(self.bearing), math.radians(self.track)
        px, py = self.dist_km * math.sin(b), self.dist_km * math.cos(b)
        speed = self.gs_kt * 1.852 / 60  # км в минуту
        vx, vy = speed * math.sin(tr), speed * math.cos(tr)
        t = -(px * vx + py * vy) / (speed * speed)
        if t <= 0:
            return None
        return t, math.hypot(px + vx * t, py + vy * t)


def parse_aircraft(raw: dict, home_lat: float, home_lon: float, max_age: float = 60.0) -> Aircraft | None:
    """Разбирает запись в формате readsb (его же отдаёт adsb.lol)."""
    lat, lon = raw.get("lat"), raw.get("lon")
    if lat is None or lon is None or raw.get("seen_pos", 0) > max_age:
        return None
    alt = raw.get("alt_baro")
    on_ground = alt == "ground"
    if on_ground:
        alt_ft = None
    elif isinstance(alt, (int, float)):
        alt_ft = int(alt)
    elif isinstance(raw.get("alt_geom"), (int, float)):
        alt_ft = int(raw["alt_geom"])
    else:
        alt_ft = None
    vrate = raw.get("baro_rate", raw.get("geom_rate"))
    return Aircraft(
        hex=raw.get("hex", "").lstrip("~").lower(),
        callsign=(raw.get("flight") or "").strip() or None,
        registration=raw.get("r") or None,
        type_code=raw.get("t") or None,
        lat=lat,
        lon=lon,
        alt_ft=alt_ft,
        on_ground=on_ground,
        gs_kt=raw.get("gs"),
        track=raw.get("track", raw.get("true_heading")),
        vrate_fpm=int(vrate) if isinstance(vrate, (int, float)) else None,
        squawk=raw.get("squawk"),
        emergency=raw.get("emergency") if raw.get("emergency") not in (None, "none") else None,
        category=raw.get("category"),
        dist_km=distance_km(home_lat, home_lon, lat, lon),
        bearing=bearing_deg(home_lat, home_lon, lat, lon),
        raw=raw,
    )
