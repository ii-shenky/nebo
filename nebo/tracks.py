"""Маршруты всех увиденных самолётов: где летел и на какой высоте.

- Точка пишется не каждый опрос, а при повороте, смене высоты или раз в MIN_DT секунд;
  последняя точка всегда текущая (её заменяем, пока не накопится изменение).
- Самолёт пропал из приёма и вернулся в течение KEEP секунд — это тот же маршрут,
  разрыв (больше GAP секунд между точками) страница рисует пунктиром.
- Новые точки дописываются в журнал live.jsonl раз в FLUSH_EVERY секунд, после перезапуска
  журнал читается заново.
- Раз в ARCHIVE_EVERY часов маршруты, пропавшие больше KEEP назад, уходят в архив
  archive/ГГГГ/ГГГГ-ММ-ДД_ЧЧ.jsonl.gz (одна строка — один маршрут), журнал переписывается
  без них. Архив копится пачками, чтобы gzip сжимал лучше.
"""
import gzip
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

KEEP = 2 * 3600          # держим маршрут после пропадания, секунд
ARCHIVE_EVERY = 6        # часов: архивируем в 00, 06, 12, 18 по местному времени
GAP = 60                 # секунд без точек — разрыв приёма
MIN_DT = 10              # секунд между точками на прямой
MIN_TURN = 3             # градусов поворота — повод для точки
MIN_DALT = 200           # футов по высоте — повод для точки
FLUSH_EVERY = 60         # секунд между записями журнала


def _turn(a, b) -> float:
    if a is None or b is None:
        return 0.0
    return abs((a - b + 180) % 360 - 180)


@dataclass
class Track:
    hex: str
    points: list = field(default_factory=list)  # [t, lat, lon, alt_ft|None, gs_kt|None, track|None]
    provisional: bool = False  # последняя точка — текущая, ещё может замениться
    callsign: str | None = None
    callsigns: list = field(default_factory=list)  # все позывные за маршрут
    reg: str | None = None
    type: str | None = None
    route: str | None = None  # «ATH-LCA», если маршрут известен

    @property
    def first(self) -> float:
        return self.points[0][0]

    @property
    def last(self) -> float:
        return self.points[-1][0]

    def committed(self) -> list:
        return self.points[:-1] if self.provisional else self.points

    def to_json(self) -> dict:
        return {"hex": self.hex, "callsigns": self.callsigns, "reg": self.reg, "type": self.type,
                "route": self.route, "first": self.first, "last": self.last, "points": self.points}


def _point(ac, now: float) -> list:
    return [round(now, 1), round(ac.lat, 5), round(ac.lon, 5), ac.alt_ft,
            None if ac.gs_kt is None else round(ac.gs_kt), None if ac.track is None else round(ac.track)]


class Tracker:
    def __init__(self, state_dir: Path | None):
        self.lock = threading.Lock()
        self.tracks: dict[str, Track] = {}
        self.dir = state_dir / "tracks" if state_dir else None
        self.journal: list[str] = []  # строки, ещё не записанные в live.jsonl
        self.last_flush = time.time()
        self.next_archive = self._next_archive_time(time.time())
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._load()

    # ---------- приём ----------

    def update(self, aircraft, now: float, routes=None):
        """Новые координаты от источника (список Aircraft). routes — RouteResolver или None."""
        with self.lock:
            for ac in aircraft:
                t = self.tracks.get(ac.hex)
                if t is None:
                    t = self.tracks[ac.hex] = Track(ac.hex)
                self._meta(t, ac, routes)
                self._add(t, _point(ac, now))
        if now - self.last_flush >= FLUSH_EVERY:
            self.flush()
        if now >= self.next_archive:
            self.archive(now)

    def _meta(self, t: Track, ac, routes):
        changed = {}
        if ac.callsign and ac.callsign != t.callsign:
            t.callsign = ac.callsign
            if ac.callsign not in t.callsigns:
                t.callsigns.append(ac.callsign)
            changed["cs"] = ac.callsign
        if ac.registration and ac.registration != t.reg:
            t.reg = changed["reg"] = ac.registration
        if ac.type_code and ac.type_code != t.type:
            t.type = changed["type"] = ac.type_code
        if routes and ac.callsign:
            state, route = routes.get(ac.callsign)
            if route:
                code = f"{route.origin.code}-{route.destination.code}"
                if code != t.route:
                    t.route = changed["rt"] = code
        if changed:
            self.journal.append(json.dumps({"h": t.hex, "m": changed}, ensure_ascii=False, separators=(",", ":")))

    def _add(self, t: Track, p: list):
        pts = t.points
        if pts and pts[-1][1:3] == p[1:3]:
            pts[-1][0] = p[0]  # стоит на месте (или повтор той же позиции) — только время
            return
        if not pts or p[0] - pts[-1][0] > GAP:
            if t.provisional:
                self._commit(t, pts[-1])
            pts.append(p)
            t.provisional = False
            self._commit(t, p)  # первая точка после разрыва — сразу в журнал
            return
        if t.provisional:
            ref = pts[-2]
            if not self._significant(ref, p):
                pts[-1] = p
                return
            self._commit(t, pts[-1])  # прежняя текущая точка остаётся на маршруте
        pts.append(p)
        t.provisional = True

    @staticmethod
    def _significant(a, b) -> bool:
        if b[0] - a[0] >= MIN_DT:
            return True
        if _turn(a[5], b[5]) >= MIN_TURN:
            return True
        return a[3] is not None and b[3] is not None and abs(a[3] - b[3]) >= MIN_DALT

    def _commit(self, t: Track, p: list):
        self.journal.append(json.dumps({"h": t.hex, "p": p}, separators=(",", ":")))

    # ---------- чтение ----------

    def get(self, hex_id: str) -> Track | None:
        with self.lock:
            t = self.tracks.get(hex_id)
            return None if t is None else Track(t.hex, [list(p) for p in t.points], t.provisional, t.callsign,
                                                list(t.callsigns), t.reg, t.type, t.route)

    def trail(self, hex_id: str, since: float) -> list[tuple[float, float]]:
        """(lat, lon) точек маршрута новее since — для карт на табло."""
        with self.lock:
            t = self.tracks.get(hex_id)
            return [(p[1], p[2]) for p in t.points if p[0] >= since] if t else []

    def recent(self, since: float) -> list[Track]:
        """Маршруты, которые видели после since (включая пропавшие из приёма)."""
        with self.lock:
            return [Track(t.hex, [list(p) for p in t.points], t.provisional, t.callsign, list(t.callsigns),
                          t.reg, t.type, t.route)
                    for t in self.tracks.values() if t.points and t.last >= since]

    # ---------- диск ----------

    def flush(self):
        self.last_flush = time.time()
        with self.lock:
            lines, self.journal = self.journal, []
        if not lines or not self.dir:
            return
        try:
            with open(self.dir / "live.jsonl", "a") as f:
                f.write("\n".join(lines) + "\n")
        except OSError as e:
            log.warning("журнал маршрутов не записан: %s", e)

    def _load(self):
        path = self.dir / "live.jsonl"
        if not path.exists():
            return
        n = 0
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # оборванная строка после выключения питания
                t = self.tracks.setdefault(rec["h"], Track(rec["h"]))
                if "p" in rec:
                    if not t.points or rec["p"][0] >= t.points[-1][0]:
                        t.points.append(rec["p"])
                        n += 1
                else:
                    m = rec.get("m", {})
                    if "cs" in m:
                        t.callsign = m["cs"]
                        if m["cs"] not in t.callsigns:
                            t.callsigns.append(m["cs"])
                    t.reg = m.get("reg", t.reg)
                    t.type = m.get("type", t.type)
                    t.route = m.get("rt", t.route)
        self.tracks = {h: t for h, t in self.tracks.items() if t.points}
        log.info("маршруты: %d самолётов, %d точек из журнала", len(self.tracks), n)

    @staticmethod
    def _next_archive_time(now: float) -> float:
        lt = time.localtime(now)
        start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour - lt.tm_hour % ARCHIVE_EVERY,
                             0, 0, 0, 0, -1))
        return start + ARCHIVE_EVERY * 3600

    def archive(self, now: float | None = None):
        """Маршруты, пропавшие больше KEEP назад, — в архив; журнал переписать без них."""
        now = now or time.time()
        self.next_archive = self._next_archive_time(now)
        if not self.dir:
            return
        self.flush()
        with self.lock:
            old = [t for t in self.tracks.values() if now - t.last > KEEP]
            for t in old:
                del self.tracks[t.hex]
            keep = list(self.tracks.values())
        if old:
            stamp = time.strftime("%Y-%m-%d_%H", time.localtime(now))
            out = self.dir.parent / "archive" / stamp[:4] / f"{stamp}.jsonl.gz"
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                with gzip.open(out, "at") as f:
                    for t in sorted(old, key=lambda t: t.first):
                        f.write(json.dumps(t.to_json(), ensure_ascii=False, separators=(",", ":")) + "\n")
                log.info("архив маршрутов: %d в %s", len(old), out)
            except OSError as e:
                log.warning("архив маршрутов не записан: %s", e)
        # журнал заново — только то, что осталось в памяти
        tmp = self.dir / "live.jsonl.tmp"
        try:
            with open(tmp, "w") as f:
                for t in keep:
                    for cs in t.callsigns:  # все позывные, текущий — последним
                        if cs != t.callsign:
                            f.write(json.dumps({"h": t.hex, "m": {"cs": cs}}, separators=(",", ":")) + "\n")
                    meta = {k: v for k, v in (("cs", t.callsign), ("reg", t.reg), ("type", t.type), ("rt", t.route)) if v}
                    if meta:
                        f.write(json.dumps({"h": t.hex, "m": meta}, ensure_ascii=False, separators=(",", ":")) + "\n")
                    for p in t.committed():
                        f.write(json.dumps({"h": t.hex, "p": p}, separators=(",", ":")) + "\n")
            os.replace(tmp, self.dir / "live.jsonl")
        except OSError as e:
            log.warning("журнал маршрутов не переписан: %s", e)

    def close(self):
        self.flush()
