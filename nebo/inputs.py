"""Кнопки и тач: читаем устройства ввода напрямую, захватываем их монопольно.

Без захвата (EVIOCGRAB) нажатия кнопок уходят ещё и в текстовую консоль tty1.
"""
import fcntl
import logging
import os
import queue
import select
import struct
import threading
import time
from pathlib import Path

from .config import TouchConfig

log = logging.getLogger(__name__)

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
ABS_X, ABS_Y = 0x00, 0x01
BTN_TOUCH = 0x14A
KEY_ENTER, KEY_UP, KEY_DOWN = 28, 103, 108
EVENT = struct.Struct("llHHi")  # struct input_event на 64-битной системе
EVIOCGRAB = 0x40044590

BUTTON_PREFIX = "button@"  # так называются кнопки из оверлея gpio-key
SWIPE_MIN_PX = 40  # короче — считаем тапом
# Резистивный сенсор при движении пальца на миг «теряет» касание. Если палец вернулся
# быстрее TOUCH_GAP секунд, это продолжение того же жеста.
TOUCH_GAP = 0.2


def _median(values: list[int]) -> int:
    return sorted(values)[len(values) // 2]


def list_input_devices() -> dict[str, str]:
    result = {}
    for dev in sorted(Path("/sys/class/input").glob("event*")):
        try:
            result[f"/dev/input/{dev.name}"] = (dev / "device" / "name").read_text().strip()
        except OSError:
            pass
    return result


class InputReader(threading.Thread):
    """Кладёт в очередь ("key", код) при нажатии кнопки, ("tap", x, y) или ("swipe", направление) при касании."""

    def __init__(self, touch: TouchConfig, screen_size, out: queue.Queue):
        super().__init__(name="input", daemon=True)
        self.touch = touch
        self.width, self.height = screen_size
        self.out = out

    def _open(self) -> dict[int, str]:
        fds = {}
        for path, name in list_input_devices().items():
            if name != self.touch.device and not name.startswith(BUTTON_PREFIX):
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                fcntl.ioctl(fd, EVIOCGRAB, 1)
            except OSError as e:
                log.warning("%s (%s): не удалось захватить: %s", path, name, e)
            fds[fd] = name
        log.info("устройства ввода: %s", sorted(fds.values()) or "нет")
        return fds

    def _to_screen(self, raw_x: int, raw_y: int) -> tuple[int, int]:
        t = self.touch
        x = (raw_x - t.x_min) * self.width / (t.x_max - t.x_min)
        y = (raw_y - t.y_min) * self.height / (t.y_max - t.y_min)
        return (min(max(int(x), 0), self.width - 1), min(max(int(y), 0), self.height - 1))

    def _gesture(self, xs: list[int], ys: list[int]):
        """Касание → ("tap", x, y) или ("swipe", "left"|"right"|"up"|"down").

        Резистивный сенсор шумит, поэтому начало и конец берём как медиану
        нескольких первых и последних замеров.
        """
        k = min(3, len(xs))
        x0, y0 = self._to_screen(_median(xs[:k]), _median(ys[:k]))
        x1, y1 = self._to_screen(_median(xs[-k:]), _median(ys[-k:]))
        dx, dy = x1 - x0, y1 - y0
        if max(abs(dx), abs(dy)) >= SWIPE_MIN_PX:
            if abs(dx) >= abs(dy):
                return "swipe", "left" if dx < 0 else "right"
            return "swipe", "up" if dy < 0 else "down"
        return ("tap", *self._to_screen(_median(xs), _median(ys)))

    def run(self):
        while True:
            fds = {}
            try:
                fds = self._open()
                if not fds:
                    time.sleep(10)
                    continue
                self._loop(fds)
            except OSError as e:
                log.warning("ошибка ввода: %s — переоткрываю устройства", e)
                time.sleep(2)
            finally:
                for fd in fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def _loop(self, fds: dict[int, str]):
        touching = False
        cur_x = cur_y = None
        xs, ys = [], []
        released_at = None  # когда отпустили палец; жест отдаём, только если он не вернулся
        while True:
            timeout = None
            if released_at is not None:
                timeout = max(0.0, released_at + TOUCH_GAP - time.monotonic())
            ready, _, _ = select.select(list(fds), [], [], timeout)
            if released_at is not None and not touching and time.monotonic() - released_at >= TOUCH_GAP:
                if xs:
                    self.out.put(self._gesture(xs, ys))
                xs, ys, released_at = [], [], None
            for fd in ready:
                data = os.read(fd, EVENT.size * 64)
                is_touch = fds[fd] == self.touch.device
                for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, etype, code, value = EVENT.unpack_from(data, off)
                    if not is_touch:
                        if etype == EV_KEY and value == 1:
                            self.out.put(("key", code))
                        continue
                    if etype == EV_ABS and code == ABS_X:
                        cur_x = value
                    elif etype == EV_ABS and code == ABS_Y:
                        cur_y = value
                    elif etype == EV_KEY and code == BTN_TOUCH:
                        touching = value == 1
                        if touching:
                            if released_at is None:  # новое касание, а не продолжение прерванного
                                xs, ys = [], []
                            released_at = None
                        else:
                            released_at = time.monotonic()
                    elif etype == EV_SYN and touching and cur_x is not None and cur_y is not None:
                        xs.append(cur_x)
                        ys.append(cur_y)
