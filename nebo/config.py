"""Загрузка конфига из TOML с умолчаниями."""
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class LocationConfig:
    lat: float = 34.68
    lon: float = 33.04
    radius_km: float = 80.0


@dataclass
class SourceConfig:
    # "adsblol" — публичный API; "readsb" — локальный aircraft.json (файл или http-адрес)
    type: str = "adsblol"
    url: str = "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}"
    path: str = "/run/readsb/aircraft.json"
    interval: float = 60.0
    timeout: float = 8.0


@dataclass
class RoutesConfig:
    enabled: bool = True
    url: str = "https://api.adsbdb.com/v0/callsign/{callsign}"
    timeout: float = 8.0


@dataclass
class MediaConfig:
    # Фото бортов с planespotters.net. Их условия требуют контакт (email или URL)
    # в User-Agent; без контакта фото не загружаются.
    photos: bool = True
    photo_contact: str = ""
    logos: bool = True
    logo_url: str = "https://raw.githubusercontent.com/Jxck-S/airline-logos/main/flightaware_logos/{key}.png"
    flags: bool = True
    flag_url: str = "https://flagcdn.com/w40/{key}.png"
    # фото судов с Wikimedia Commons (по IMO/MMSI); контакт для их User-Agent — адрес проекта
    ship_photos: bool = True
    commons_contact: str = "https://github.com/ii-shenky/nebo"
    # если у planespotters нет снимка борта: фото самого борта с Commons (категория «4X-EKA (aircraft)»),
    # иначе — такого же типа (Википедия + Commons); только на странице
    reg_photos: bool = True
    type_photos: bool = True
    timeout: float = 10.0


@dataclass
class DisplayConfig:
    fb_name: str = "fb_ili9340"
    units: str = "metric"  # "metric" или "aviation"
    hide_ground: bool = True
    idle_return: float = 60.0


@dataclass
class TouchConfig:
    device: str = "ADS7846 Touchscreen"
    x_min: int = 120
    x_max: int = 3746
    y_min: int = 253
    y_max: int = 4043


@dataclass
class WebConfig:
    # веб-страница со всем, что принято: http://<малинка>:8080/
    enabled: bool = True
    bind: str = "0.0.0.0"
    port: int = 8080
    # веб-сервер AIS-catcher (суда), когда донгл переключён на них; страница /ships
    ais_url: str = "http://127.0.0.1:8100"
    # открыли страницу судов — донгл переключается на суда, страницу самолётов — обратно
    # (только со своим приёмником; нужно правило polkit deploy/50-nebo-receiver.rules)
    receiver_switch: bool = True
    ships_idle_return: float = 120.0  # страницу судов не смотрят столько секунд — назад к самолётам


@dataclass
class Config:
    location: LocationConfig = field(default_factory=LocationConfig)
    source: SourceConfig = field(default_factory=SourceConfig)
    routes: RoutesConfig = field(default_factory=RoutesConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    touch: TouchConfig = field(default_factory=TouchConfig)
    web: WebConfig = field(default_factory=WebConfig)


def _apply(section, values: dict, name: str):
    known = {f.name: f for f in fields(section)}
    for key, value in values.items():
        if key not in known:
            raise ValueError(f"[{name}] неизвестный параметр {key!r}")
        default = getattr(section, key)
        if isinstance(default, float) and isinstance(value, int):
            value = float(value)
        if type(value) is not type(default):
            raise ValueError(f"[{name}] {key}: ожидается {type(default).__name__}, получено {value!r}")
        setattr(section, key, value)


def load(path: str | Path | None) -> Config:
    cfg = Config()
    if path is None:
        return cfg
    with open(path, "rb") as f:
        data = tomllib.load(f)
    for name, values in data.items():
        if not hasattr(cfg, name):
            raise ValueError(f"неизвестная секция [{name}]")
        _apply(getattr(cfg, name), values, name)
    return cfg
