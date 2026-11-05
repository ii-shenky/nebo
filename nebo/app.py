"""Главный цикл: опрос источника, обработка кнопок и тача, отрисовка."""
import logging
import math
import queue
import threading
import time
import urllib.error

from .config import Config
from .info import AircraftInfo
from .inputs import KEY_DOWN, KEY_ENTER, KEY_UP
from .media import DiskImages, Photos
from .models import Aircraft
from .routes import FOUND, RouteResolver
from .shapes import Silhouettes
from .sources import Source
from .tracks import Tracker
from .ui import Plane, Renderer, ViewState

log = logging.getLogger(__name__)

TRAIL_SECONDS = 15 * 60  # след на картах табло
MAX_BACKOFF = 10 * 60.0  # потолок паузы после 429, секунд


def _retry_after(e: urllib.error.HTTPError) -> float:
    """Заголовок Retry-After в секундах; дата или мусор — 0."""
    try:
        return float(e.headers.get("Retry-After", 0))
    except (TypeError, ValueError):
        return 0.0


MAP_RADII_KM = (25, 50, 75, 100, 150, 200, 250, 300, 400, 500, 700)


def _map_radius(planes: list[Plane], index: int) -> float:
    """Масштаб карты без заданного радиуса: круглое число, в которое помещаются выбранный борт
    и 80 % остальных. Самые дальние (и борты с ошибочными координатами) не сжимают всю карту."""
    if not planes:
        return MAP_RADII_KM[1]
    dists = sorted(p.ac.dist_km for p in planes)
    far = max(dists[max(0, math.ceil(len(dists) * 0.8) - 1)], planes[index].ac.dist_km) * 1.05
    return next((r for r in MAP_RADII_KM if r >= far), MAP_RADII_KM[-1])


class App:
    def __init__(self, cfg: Config, screen, source: Source, routes: RouteResolver | None,
                 events: queue.Queue, logos: DiskImages | None = None, flags: DiskImages | None = None,
                 photos: Photos | None = None, tracks: Tracker | None = None, receiver=None):
        self.cfg = cfg
        self.screen = screen
        self.info = AircraftInfo()
        self.sil = Silhouettes()
        self.renderer = Renderer(screen.size, self.sil) if screen else None  # без экрана — только веб-страница
        self.source = source
        self.routes = routes
        self.logos, self.flags, self.photos = logos, flags, photos
        self.tracks = tracks or Tracker(None)  # маршруты всех увиденных (tracks.py)
        self.receiver = receiver  # переключатель донгла самолёты/суда (receiver.py) или None
        self.ship_photos = None  # фото судов для веб-страницы (media.ShipPhotos), ставит __main__
        self.reg_photos = None  # фото самого борта с Commons, когда у planespotters нет (media.RegPhotos)
        self.type_photos = None  # фото такого же типа, когда у борта своего нет (media.TypePhotos)
        self.events = events

        self.lock = threading.Lock()
        self.aircraft: list[Aircraft] = []  # всё, что пришло от источника (с координатами)
        self.last_ok: float | None = None
        self.error: str | None = None
        self.backoff = 0.0  # пауза после отказа 429, секунд

        self.views = ["card", "radar", "list"]
        self.view = "card"
        self.overlay: str | None = None  # "qr" — QR-код снимка поверх карточки
        self.follow = True  # показываем ближайший самолёт
        self.selected_hex: str | None = None
        self.list_index = 0
        self.last_input = time.monotonic()
        self.shown_hex: str | None = None  # какой самолёт сейчас на экране (для веб-страницы)

    # ---------- данные ----------

    def fetch_once(self):
        try:
            planes = self.source.fetch()
        except Exception as e:
            if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                # сервис просит реже: удваиваем паузу (до MAX_BACKOFF), пока не перестанет;
                # если сервер прислал Retry-After в секундах — ждём не меньше него
                retry_after = _retry_after(e)
                self.backoff = min(MAX_BACKOFF, max(self.cfg.source.interval * 2, self.backoff * 2, retry_after))
                log.warning("%s: слишком часто, следующий запрос через %.0f с", self.source.name, self.backoff)
            else:
                log.warning("%s: %s", self.source.name, e)
            with self.lock:
                self.error = str(e)
            return
        self.backoff = 0.0
        now = time.time()
        with self.lock:
            self.aircraft = planes
            self.last_ok = now
            self.error = None
        self.tracks.update(planes, now, self.routes)
        if self.routes:
            for ac in planes:
                self.routes.get(ac.callsign)  # ставит в очередь поиск маршрута

    def _fetch_loop(self, stop: threading.Event):
        while not stop.is_set():
            started = time.monotonic()
            self.fetch_once()
            self.events.put(("data",))
            interval = self.backoff or self.cfg.source.interval
            stop.wait(max(1.0, interval - (time.monotonic() - started)))

    @property
    def radius_limit(self) -> float | None:
        """Радиус табло, км. Ограничиваем только интернет-источник (это радиус запроса к adsb.lol);
        со своим приёмником показываем всё, что он принял."""
        return self.cfg.location.radius_km if self.cfg.source.type == "adsblol" else None

    def on_board(self, ac: Aircraft) -> bool:
        """Показывать ли самолёт на табло: в радиусе (если он есть) и (если так настроено) не на земле."""
        limit = self.radius_limit
        if limit is not None and ac.dist_km > limit:
            return False
        return not (self.cfg.display.hide_ground and ac.on_ground)

    def _planes(self, everything: bool = False) -> list[Plane]:
        """Самолёты табло (или все принятые — для веб-страницы) с маршрутом, типом и следом."""
        with self.lock:
            aircraft = [ac for ac in self.aircraft if everything or self.on_board(ac)]
        since = time.time() - TRAIL_SECONDS
        trails = {ac.hex: self.tracks.trail(ac.hex, since) for ac in aircraft}
        result = []
        for ac in aircraft:
            state, route = self.routes.get(ac.callsign) if self.routes else ("missing", None)
            desc, wtc = self.info.type_desc(ac.type_code)
            result.append(Plane(
                ac=ac,
                route_state=state,
                route=route,
                route_ok=route.plausible(ac.lat, ac.lon) if state == FOUND and route else False,
                airline=(route.airline if route and route.airline else None) or self.info.operator(ac.callsign),
                type_name=self.info.type_name(ac.type_code),
                shape=self.sil.shape_for(ac.type_code, desc, wtc, ac.category, self.info.type_name(ac.type_code)),
                trail=trails[ac.hex],
            ))
        return result

    def _enrich(self, p: Plane):
        """Картинки — только для самолёта на экране, чтобы не дёргать сервисы зря."""
        if self.logos:
            p.logo = self.logos.get(self.info.operator_code(p.ac.callsign))[1]
        if self.flags and p.route and p.route_ok:
            p.flags = tuple(self.flags.get(a.country.lower() if a.country else None)[1]
                            for a in (p.route.origin, p.route.destination))
        shows_photo = self.view == "card"
        if self.photos:
            if shows_photo:
                p.photo_state, p.photo = self.photos.get(p.ac.hex)
            # по условиям planespotters снимок держим в памяти, только пока он на экране
            self.photos.retain({p.ac.hex} if shows_photo else set())

    # ---------- выбор самолёта ----------

    def _current_index(self, planes: list[Plane]) -> int:
        if self.view == "list":
            return min(self.list_index, max(0, len(planes) - 1))
        if not self.follow:
            for i, p in enumerate(planes):
                if p.ac.hex == self.selected_hex:
                    return i
            self.follow = True  # выбранный улетел из радиуса — снова ближайший
        return 0

    def _select(self, planes: list[Plane], index: int):
        self.follow = False
        self.selected_hex = planes[index].ac.hex

    def _switch_view(self, planes: list[Plane], cur: int, step: int):
        i = self.views.index(self.view)
        new = self.views[(i + step) % len(self.views)]
        if new == "list":
            self.list_index = cur
        elif self.view == "list":
            self._select(planes, min(self.list_index, len(planes) - 1))
        self.view = new

    def handle(self, event, planes: list[Plane]):
        """Кнопки: вверх/вниз — самолёт, Enter — следующий экран.
        Свайпы: влево/вправо — самолёт, вверх/вниз — экран (в списке — прокрутка).
        Тапы — см. _tap()."""
        kind = event[0]
        if kind not in ("key", "tap", "swipe"):
            return
        self.last_input = time.monotonic()
        if kind != "key":
            log.info("касание: %s", " ".join(map(str, event)))
        if self.overlay:  # QR-код на весь экран: любое касание или кнопка закрывает
            self.overlay = None
            return
        n = len(planes)
        if n == 0:
            return
        cur = self._current_index(planes)

        if kind == "key":
            action = {KEY_UP: "prev", KEY_DOWN: "next", KEY_ENTER: "next_view"}.get(event[1])
        elif kind == "swipe":
            action = {"left": "next", "right": "prev", "up": "next_view", "down": "prev_view"}[event[1]]
            if self.view == "list" and event[1] in ("up", "down"):
                action = "next" if event[1] == "up" else "prev"  # палец тянет список
        else:
            self._tap(planes, cur, event[1], event[2])
            return

        if action in ("prev", "next"):
            step = -1 if action == "prev" else 1
            if self.view == "list":
                self.list_index = (cur + step) % n
            else:
                self._select(planes, (cur + step) % n)
        elif action in ("next_view", "prev_view"):
            self._switch_view(planes, cur, 1 if action == "next_view" else -1)

    def _tap(self, planes: list[Plane], cur: int, x: int, y: int):
        """Карточка: фото — QR-код снимка, любое другое место (и фото, если снимка нет) — большая карта.
        Карта: самолёт — выбрать его, пустое место — назад к карточке.
        Список: строка — открыть самолёт."""
        state = self._state(planes, cur)
        if self.view == "card":
            has_photo = self.photos and self.photos.get(planes[cur].ac.hex)[1]
            if self.renderer.card_zone(x, y) == "photo" and has_photo:
                self.overlay = "qr"
            else:
                self._select(planes, cur)
                self.view = "radar"
        elif self.view == "list":
            r = self.renderer
            row = (y - r.LIST_TOP) // r.ROW_H
            i = r.list_first(state) + row
            if row >= 0 and i < len(planes):
                self._select(planes, i)
                self.view = "card"
        elif self.view == "radar":
            hit = self.renderer.radar_hit(state, x, y)
            if hit is not None:
                self._select(planes, hit)
            else:
                self.view = "card"

    def _idle(self):
        if time.monotonic() - self.last_input > self.cfg.display.idle_return:
            if self.view != "card" or not self.follow:
                log.info("нет нажатий — возвращаюсь к ближайшему")
            self.view, self.follow, self.overlay = "card", True, None

    # ---------- отрисовка ----------

    def _state(self, planes: list[Plane], index: int) -> ViewState:
        with self.lock:
            last_ok, error = self.last_ok, self.error
        return ViewState(
            view=self.view,
            planes=planes,
            index=index,
            follow=self.follow,
            radius_km=self.radius_limit or _map_radius(planes, index),
            radius_limited=self.radius_limit is not None,
            notice=self._notice(),
            source_name=self.source.name,
            last_ok=last_ok,
            error=error,
            units=self.cfg.display.units,
            overlay=self.overlay,
            home=(self.cfg.location.lat, self.cfg.location.lon),
        )

    def _notice(self) -> str | None:
        """Табло вместо самолётов: донгл отдан судам (с веб-страницы)."""
        if self.receiver and self.cfg.source.type == "readsb" and self.receiver.mode() == "ships":
            return "Антенна слушает суда"
        return None

    def render(self):
        planes = self._planes()
        index = self._current_index(planes)
        self.shown_hex = planes[index].ac.hex if planes else None
        if planes:
            self._enrich(planes[index])
        elif self.photos:
            self.photos.retain(set())
        return self.renderer.render(self._state(planes, index))

    def run(self):
        if self.screen:
            self.screen.show(self.renderer.splash("загрузка…"))
        stop = threading.Event()
        threading.Thread(target=self._fetch_loop, args=(stop,), name="fetch", daemon=True).start()
        try:
            while True:
                try:
                    event = self.events.get(timeout=1.0)
                except queue.Empty:
                    event = None
                if not self.screen:
                    continue  # без экрана рисовать нечего: данные обновляет поток fetch, события просто разбираем
                if event:
                    self.handle(event, self._planes())
                self._idle()
                self.screen.show(self.render())
        finally:
            stop.set()
