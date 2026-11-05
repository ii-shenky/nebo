"""Отрисовка табло 320×240 средствами Pillow.

Экраны: карточка самолёта, радар (карта), список. Поверх карточки — QR-код
снимка на весь экран (открывается тапом по фото).
"""
import json
import math
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

from .media import PENDING as MEDIA_PENDING
from .media import Photo
from .models import Aircraft
from .routes import FOUND, PENDING, Route
from .shapes import Silhouettes

ROBOTO = "/usr/share/fonts/truetype/roboto/unhinted"
DEJAVU = "/usr/share/fonts/truetype/dejavu"
FONT_REGULAR = f"{ROBOTO}/RobotoCondensed-Regular.ttf"
FONT_MEDIUM = f"{ROBOTO}/RobotoCondensed-Medium.ttf"
FONT_BOLD = f"{ROBOTO}/RobotoCondensed-Bold.ttf"
FONT_SYMBOLS = f"{DEJAVU}/DejaVuSans.ttf"  # стрелки и треугольники: в Roboto их нет

BG = (0, 0, 0)
PANEL = (16, 18, 22)
TEXT = (235, 235, 235)
MUTED = (135, 140, 150)
DIM = (70, 74, 82)
ACCENT = (255, 184, 28)  # янтарный, как на табло в аэропорту
TRAIL = (150, 110, 30)
OK = (60, 200, 90)
ALERT = (240, 60, 50)
ROW_SEL = (34, 36, 44)
LAND = (20, 30, 26)
COAST = (60, 84, 72)
RING = (40, 44, 52)

SYMBOLS = "→▲▼"
STALE_AFTER = 45  # секунд без свежих данных — показываем, что связи нет
COMPASS = ["С", "ССВ", "СВ", "ВСВ", "В", "ВЮВ", "ЮВ", "ЮЮВ", "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ", "З", "ЗСЗ", "СЗ", "ССЗ"]
EMERGENCY_SQUAWKS = {"7500": "УГОН", "7600": "НЕТ СВЯЗИ", "7700": "БЕДСТВИЕ"}
MAP = json.loads((Path(__file__).parent / "data" / "map.json").read_text())

# области карточки, по которым можно тапнуть
PHOTO_BOX = (170, 22, 318, 121)
MINIMAP_BOX = (170, 126, 318, 236)


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype(f"{DEJAVU}/DejaVuSans{'-Bold' if 'Bold' in path else ''}.ttf", size)


def group(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def compass(deg: float) -> str:
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def to_km(home, lat, lon) -> tuple[float, float]:
    """Координаты в км от дома: x — на восток, y — на север."""
    lat0, lon0 = home
    return (lon - lon0) * math.cos(math.radians(lat0)) * 111.32, (lat - lat0) * 110.57


@lru_cache(maxsize=4)
def qr_image(link: str, module: int) -> Image.Image:
    """QR-код: тёмные модули на белом, с белой рамкой в 2 модуля."""
    matrix = qr_matrix(link)
    n, quiet = len(matrix), 2
    side = (n + 2 * quiet) * module
    img = Image.new("RGB", (side, side), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                px, py = (x + quiet) * module, (y + quiet) * module
                d.rectangle((px, py, px + module - 1, py + module - 1), fill=(0, 0, 0))
    return img


@lru_cache(maxsize=4)
def qr_matrix(link: str):
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=0)
    q.add_data(link)
    q.make(fit=True)
    return q.get_matrix()


@dataclass
class Plane:
    """Самолёт со всем, что известно для отрисовки."""
    ac: Aircraft
    route_state: str = "missing"
    route: Route | None = None
    route_ok: bool = False
    airline: str | None = None
    type_name: str | None = None
    shape: str = "unknown"
    trail: list = field(default_factory=list)  # [(lat, lon)] за последние минуты
    # картинки загружаются только для самолёта на экране
    logo: Image.Image | None = None
    flags: tuple = (None, None)
    photo_state: str = "missing"
    photo: Photo | None = None


@dataclass
class ViewState:
    view: str  # "card", "radar", "list"
    planes: list[Plane]
    index: int  # выбранный самолёт
    follow: bool  # режим «ближайший»
    radius_km: float  # масштаб карты; он же радиус табло, если radius_limited
    source_name: str
    last_ok: float | None
    error: str | None
    units: str
    home: tuple = (0.0, 0.0)
    overlay: str | None = None  # "qr" — QR-код снимка поверх карточки
    radius_limited: bool = True  # False — свой приёмник: показываем всё, масштаб по дальнему борту
    notice: str | None = None  # вместо самолётов — сообщение (донгл отдан судам)


class Renderer:
    ROW_H = 36
    LIST_TOP = 24

    def __init__(self, size=(320, 240), silhouettes: Silhouettes | None = None):
        self.w, self.h = size
        self.sil = silhouettes or Silhouettes()
        f = font
        self.f_header = f(FONT_MEDIUM, 14)
        self.f_logo = f(FONT_BOLD, 42)
        self.f_callsign = f(FONT_BOLD, 34)
        self.f_route = f(FONT_BOLD, 24)
        self.f_type = f(FONT_BOLD, 18)
        self.f_value = f(FONT_BOLD, 19)
        self.f_text = f(FONT_REGULAR, 16)
        self.f_small = f(FONT_REGULAR, 14)
        self.f_tiny = f(FONT_REGULAR, 12)
        self.f_row = f(FONT_BOLD, 19)
        self.f_row_small = f(FONT_REGULAR, 14)
        self.f_big_msg = f(FONT_BOLD, 26)
        self._sym_fonts = {}
        self._radar_cache = {}

    # ================= текст =================
    # В Roboto нет стрелок и треугольников — такие знаки рисуем шрифтом DejaVu.

    def _sym_font(self, f):
        return self._sym_fonts.setdefault(f.size, font(FONT_SYMBOLS, f.size))

    def _parts(self, text: str, f):
        return [(part, self._sym_font(f) if part in SYMBOLS else f) for part in _split_symbols(text)]

    def _tlen(self, d, text: str, f) -> float:
        return sum(d.textlength(part, font=ff) for part, ff in self._parts(text, f))

    def _text(self, d, xy, text, font=None, fill=None, anchor=None):
        """Как d.text(), но со знаками → ▲ ▼ из DejaVu; куски выравниваются по базовой линии."""
        if not text:
            return
        if anchor or not any(ch in SYMBOLS for ch in text):
            d.text(xy, text, font=font, fill=fill, anchor=anchor)
            return
        x, y = xy
        baseline = y + font.getmetrics()[0]
        for part, ff in self._parts(text, font):
            d.text((x, baseline), part, font=ff, fill=fill, anchor="ls")
            x += d.textlength(part, font=ff)

    def _fit(self, d, text: str, f, max_w: float) -> str:
        if not text or self._tlen(d, text, f) <= max_w:
            return text
        while text and self._tlen(d, text + "…", f) > max_w:
            text = text[:-1]
        return text.rstrip() + "…"

    def _wrap(self, d, text: str, f, max_w: float, max_lines: int) -> list[str]:
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if self._tlen(d, trial, f) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self._fit(d, lines[-1] + "…", f, max_w)
        return [self._fit(d, line, f, max_w) for line in lines]

    # ================= общие элементы =================

    def _header(self, d, s: ViewState, left: str):
        stale = s.last_ok is None or time.time() - s.last_ok > STALE_AFTER
        clock = time.strftime("%H:%M")
        cw = d.textlength(clock, font=self.f_header)
        d.text((self.w - 6 - cw, 2), clock, font=self.f_header, fill=TEXT)
        cx = self.w - 6 - cw - 9
        d.ellipse((cx - 4, 7, cx + 4, 15), fill=ALERT if stale else OK)
        color = MUTED
        if stale and s.error:
            left, color = f"нет связи с {s.source_name}", ALERT
        self._text(d, (6, 2), self._fit(d, left, self.f_header, cx - 16), font=self.f_header, fill=color)

    def _header_left(self, s: ViewState) -> str:
        n = len(s.planes)
        return f"ближайший · 1/{n}" if s.follow else f"{s.index + 1}/{n}"

    def _arrow(self, img, cx, cy, r, deg, color):
        """Стрелка в направлении deg (0 = север/вверх), сглаженная через 4-кратный рендер."""
        k = 4
        side = int(2 * r * k + 8)
        mask = Image.new("L", (side, side), 0)
        a, c = math.radians(deg), side / 2

        def pt(dist, ang):
            return c + dist * k * math.sin(a + ang), c - dist * k * math.cos(a + ang)

        ImageDraw.Draw(mask).polygon([pt(r, 0), pt(r, math.radians(145)), pt(r * 0.35, math.pi),
                                      pt(r, math.radians(-145))], fill=255)
        mask = mask.resize((side // k, side // k), Image.LANCZOS)
        img.paste(color, (round(cx - mask.width / 2), round(cy - mask.height / 2)), mask)

    def _paste_center(self, img, icon, cx, cy):
        img.paste(icon, (round(cx - icon.width / 2), round(cy - icon.height / 2)), icon)

    def _silhouette(self, img, p: Plane, cx, cy, size, color, rotate=True):
        angle = int(round((p.ac.track or 0) / 5) * 5) % 360 if rotate else 0
        self._paste_center(img, self.sil.icon(p.shape, size, color, angle), cx, cy)

    def _logo_badge(self, img, logo, x, y, size):
        """Логотип на белой плашке: они рисовались под белый фон."""
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((x, y, x + size - 1, y + size - 1), radius=max(3, size // 6), fill=(255, 255, 255))
        pad = max(2, size // 10)
        lg = logo.copy()
        lg.thumbnail((size - 2 * pad, size - 2 * pad), Image.LANCZOS)
        img.paste(lg, (x + (size - lg.width) // 2, y + (size - lg.height) // 2), lg)

    def _flag(self, img, flag, x, y, h) -> int:
        """Флаг высотой h, возвращает ширину."""
        if flag is None:
            return 0
        fl = flag.copy()
        fl.thumbnail((h * 2, h), Image.LANCZOS)
        img.paste(fl, (round(x), round(y)), fl)
        return fl.width

    # ================= форматирование =================

    def _alt(self, ac: Aircraft, units: str) -> str:
        if ac.on_ground:
            return "на земле"
        if ac.alt_ft is None:
            return "—"
        if units == "aviation":
            return f"FL{round(ac.alt_ft / 100):03d}" if ac.alt_ft >= 10000 else f"{group(ac.alt_ft)} ft"
        return f"{group(round(ac.alt_ft * 0.3048 / 10) * 10)} м"

    def _vrate(self, ac: Aircraft, units: str) -> str:
        if ac.vrate_fpm is None:
            return ""
        if abs(ac.vrate_fpm) < 300:
            return "ровно"
        arrow = "▲" if ac.vrate_fpm > 0 else "▼"
        if units == "aviation":
            return f"{arrow} {group(abs(ac.vrate_fpm))} ft/min"
        return f"{arrow} {abs(ac.vrate_fpm) * 0.00508:.0f} м/с"

    def _dist(self, km: float, units: str) -> str:
        if units == "aviation":
            nm = km / 1.852
            return f"{nm:.1f} nm" if nm < 10 else f"{nm:.0f} nm"
        return f"{km:.1f} км" if km < 10 else f"{km:.0f} км"

    def _speed(self, ac: Aircraft, units: str) -> str:
        if ac.gs_kt is None:
            return ""
        if units == "aviation":
            return f"{ac.gs_kt:.0f} kt"
        return f"{group(round(ac.gs_kt * 1.852))} км/ч"

    def _elev(self, ac: Aircraft) -> str:
        e = ac.elevation_deg()
        if e is None:
            return ""
        return "за горизонтом" if e < 0 else f"{e:.0f}° над горизонтом"

    def _cpa(self, ac: Aircraft, units: str) -> str:
        cpa = ac.closest_approach()
        if cpa is None:
            return "удаляется"
        t, dist = cpa
        if t > 60:
            return "подлетит не скоро"
        when = "сейчас" if t < 0.5 else f"через {t:.0f} мин"
        return f"ближе всего {when}: {self._dist(dist, units)}"

    def _route_text(self, p: Plane) -> tuple[str, str, tuple]:
        """Коды аэропортов, города, цвет."""
        if p.route_state == FOUND and p.route:
            o, dst = p.route.origin, p.route.destination
            mark = "" if p.route_ok else " ?"
            cities = " → ".join(c or "?" for c in (o.city, dst.city))
            return f"{o.code} → {dst.code}{mark}", cities, TEXT if p.route_ok else MUTED
        if p.route_state == PENDING:
            return "…", "ищу маршрут", DIM
        return "", "маршрут неизвестен", DIM

    def _callsign(self, ac: Aircraft) -> str:
        return ac.callsign or ac.registration or ac.hex.upper()

    def _emergency(self, d, ac: Aircraft, right: int, y: int):
        alarm = EMERGENCY_SQUAWKS.get(ac.squawk or "")
        if not (alarm or ac.emergency):
            return
        text = alarm or ac.emergency.upper()
        tw = d.textlength(text, font=self.f_type)
        d.rectangle((right - tw - 12, y, right, y + 26), fill=ALERT)
        d.text((right - tw - 6, y + 3), text, font=self.f_type, fill=TEXT)

    # ================= экраны =================

    def render(self, s: ViewState) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h), BG)
        d = ImageDraw.Draw(img)
        if not s.planes or s.notice:
            self._empty(img, d, s)
        elif s.view == "list":
            self._list(img, d, s)
        elif s.view == "radar":
            self._radar(img, d, s)
        elif s.overlay == "qr" and s.planes[s.index].photo:
            self._qr_screen(img, d, s)
        else:
            self._card(img, d, s)
        return img

    def splash(self, text: str) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h), BG)
        d = ImageDraw.Draw(img)
        d.text((self.w / 2, self.h / 2 - 14), "nebo", font=self.f_logo, fill=ACCENT, anchor="mm")
        d.text((self.w / 2, self.h / 2 + 26), text, font=self.f_text, fill=MUTED, anchor="mm")
        return img

    def _empty(self, img, d, s: ViewState):
        self._header(d, s, "nebo")
        if s.notice:
            title, sub = s.notice, "к самолётам вернётся сама"
        elif s.last_ok is None:
            title, sub = "Жду данные…", s.source_name
        else:
            title, sub = "Рядом никого", (f"в радиусе {s.radius_km:.0f} км" if s.radius_limited
                                          else "антенна никого не слышит")
        d.text((self.w / 2, self.h / 2 - 6), title, font=self.f_big_msg, fill=TEXT, anchor="mm")
        d.text((self.w / 2, self.h / 2 + 22), sub, font=self.f_text, fill=MUTED, anchor="mm")

    # ---------- карточка ----------

    def card_zone(self, x, y) -> str:
        """Что на карточке под пальцем: "photo" (фото справа вверху) или "map" (всё остальное).

        Зоны без зазоров: резистивный тач промахивается на несколько пикселей, а мёртвое
        место выглядит как «тап не работает»."""
        if x >= PHOTO_BOX[0] - 4 and y < (PHOTO_BOX[3] + MINIMAP_BOX[1]) // 2:
            return "photo"
        return "map"

    def _card(self, img, d, s: ViewState):
        p = s.planes[s.index]
        ac = p.ac
        self._header(d, s, self._header_left(s))
        L = 162  # ширина текстовой колонки

        # позывной, авиакомпания, маршрут
        d.text((6, 18), self._fit(d, self._callsign(ac), self.f_callsign, L), font=self.f_callsign, fill=ACCENT)
        x = 6
        if p.logo:
            self._logo_badge(img, p.logo, 6, 59, 20)
            x = 31
        d.text((x, 60), self._fit(d, p.airline or "", self.f_small, L + 6 - x), font=self.f_small, fill=MUTED)
        codes, cities, color = self._route_text(p)
        self._text(d, (6, 78), self._fit(d, codes, self.f_route, L), font=self.f_route, fill=color)
        self._cities(img, d, p, cities, 6, 106, L)

        # где он и как летит
        alt = self._alt(ac, s.units)
        d.text((6, 124), alt, font=self.f_value, fill=TEXT)
        self._text(d, (14 + d.textlength(alt, font=self.f_value), 128),
                   self._vrate(ac, s.units), font=self.f_small, fill=MUTED)
        self._arrow(img, 15, 161, 9, ac.bearing, ACCENT)
        d.text((30, 149), f"{self._dist(ac.dist_km, s.units)} {compass(ac.bearing)}", font=self.f_value, fill=TEXT)
        d.text((6, 175), self._fit(d, self._elev(ac), self.f_small, L), font=self.f_small, fill=MUTED)
        info = " · ".join(x for x in (self._speed(ac, s.units), ac.type_code) if x)
        d.text((6, 194), self._fit(d, info, self.f_small, L), font=self.f_small, fill=MUTED)
        d.text((6, 216), self._fit(d, self._cpa(ac, s.units), self.f_tiny, L), font=self.f_tiny, fill=MUTED)

        # справа: фото и мини-карта
        if p.photo:
            self._card_photo(img, d, p.photo, PHOTO_BOX)
        else:
            self._no_photo(img, d, p, PHOTO_BOX)
        self._minimap(img, s, p, MINIMAP_BOX)
        self._emergency(d, ac, self.w - 4, 24)

    def _cities(self, img, d, p: Plane, cities: str, x, y, max_w):
        """Города маршрута с флагами стран."""
        o_city, sep, d_city = cities.partition(" → ")
        if not sep or not (p.flags[0] or p.flags[1]):
            self._text(d, (x, y), self._fit(d, cities, self.f_tiny, max_w), font=self.f_tiny, fill=MUTED)
            return
        right = x + max_w
        for i, (flag, city) in enumerate(((p.flags[0], o_city), (p.flags[1], d_city))):
            if i:
                self._text(d, (x, y), " → ", font=self.f_tiny, fill=MUTED)
                x += self._tlen(d, " → ", self.f_tiny)
            fw = self._flag(img, flag, x, y + 3, 9)
            x += fw + (3 if fw else 0)
            limit = (right - x) if i else (right - x) / 2
            text = self._fit(d, city, self.f_tiny, limit)
            d.text((x, y), text, font=self.f_tiny, fill=MUTED)
            x += d.textlength(text, font=self.f_tiny)

    def _card_photo(self, img, d, photo: Photo, box):
        """Фото с подписью автора поверх нижнего края и значком QR в углу.

        По условиям planespotters имя фотографа должно быть видно всегда, рядом со снимком.
        Ссылка на снимок — QR-код на весь экран по тапу; значок в углу подсказывает, что он есть.
        """
        x0, y0, x1, y1 = box
        ph = photo.image.copy()
        ph.thumbnail((x1 - x0, y1 - y0), Image.LANCZOS)
        px, py = x0 + (x1 - x0 - ph.width) // 2, y0 + (y1 - y0 - ph.height) // 2
        # полупрозрачная полоса под подпись
        strip_h = 15
        strip = ph.crop((0, ph.height - strip_h, ph.width, ph.height))
        strip = Image.blend(strip, Image.new("RGB", strip.size, (0, 0, 0)), 0.6)
        ph.paste(strip, (0, ph.height - strip_h))
        img.paste(ph, (px, py))
        credit = self._fit(d, f"© {photo.photographer}", self.f_tiny, ph.width - 30)
        d.text((px + 4, py + ph.height - strip_h), credit, font=self.f_tiny, fill=TEXT)
        self._qr_badge(img, d, px + ph.width - 20, py + ph.height - 20)

    def _qr_badge(self, img, d, x, y, size=17):
        """Значок «здесь есть QR»: белый квадрат с тремя угловыми метками."""
        d.rounded_rectangle((x, y, x + size, y + size), radius=3, fill=(255, 255, 255))
        m = 5
        for cx, cy in ((x + 3, y + 3), (x + size - 3 - m, y + 3), (x + 3, y + size - 3 - m)):
            d.rectangle((cx, cy, cx + m - 1, cy + m - 1), outline=(0, 0, 0))
            d.rectangle((cx + 2, cy + 2, cx + 2, cy + 2), fill=(0, 0, 0))
        d.rectangle((x + size - 7, y + size - 7, x + size - 5, y + size - 5), fill=(0, 0, 0))

    def _no_photo(self, img, d, p: Plane, box):
        """Вместо фото — силуэт самолёта на панели."""
        x0, y0, x1, y1 = box
        d.rectangle(box, fill=PANEL)
        size = min(x1 - x0, y1 - y0) - 26
        self._silhouette(img, p, (x0 + x1) / 2, (y0 + y1) / 2 - 7, size, DIM, rotate=False)
        msg = "ищу фото…" if p.photo_state == MEDIA_PENDING else "фото нет"
        d.text(((x0 + x1) / 2, y1 - 10), msg, font=self.f_tiny, fill=MUTED, anchor="mm")

    def _minimap(self, img, s: ViewState, p: Plane, box):
        """Карта-врезка: дом, самолёт и его след, в масштабе, где видно обоих."""
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        pts = [(0.0, 0.0), to_km(s.home, p.ac.lat, p.ac.lon)] + [to_km(s.home, la, lo) for la, lo in p.trail]
        minx, maxx = min(q[0] for q in pts), max(q[0] for q in pts)
        miny, maxy = min(q[1] for q in pts), max(q[1] for q in pts)
        span = max(maxx - minx, (maxy - miny) * w / h, 12.0) * 1.35
        k = w / span
        cxk, cyk = (minx + maxx) / 2, (miny + maxy) / 2

        def proj(xk, yk):
            return w / 2 + (xk - cxk) * k, h / 2 - (yk - cyk) * k

        layer = Image.new("RGB", (w, h), PANEL)
        d = ImageDraw.Draw(layer)
        for line in MAP["coast"]:
            q = [proj(*to_km(s.home, lat, lon)) for lon, lat in line["pts"]]
            if line["closed"]:
                d.polygon(q, fill=LAND, outline=COAST)
            else:
                d.line(q, fill=COAST)
        for ap in MAP["airports"]:
            ax, ay = proj(*to_km(s.home, ap["lat"], ap["lon"]))
            if 0 <= ax < w and 0 <= ay < h:
                d.rectangle((ax - 2, ay - 2, ax + 2, ay + 2), outline=MUTED)
                d.text((ax + 4, ay - 7), ap["iata"], font=self.f_tiny, fill=MUTED)
        hx, hy = proj(0, 0)
        px, py = proj(*pts[1])
        d.line((hx, hy, px, py), fill=DIM)
        if len(p.trail) >= 2:
            d.line([proj(*to_km(s.home, la, lo)) for la, lo in p.trail] + [(px, py)], fill=TRAIL, width=2)
        d.line((hx - 4, hy, hx + 4, hy), fill=TEXT)
        d.line((hx, hy - 4, hx, hy + 4), fill=TEXT)
        self._silhouette(layer, p, px, py, 20, ACCENT)
        scale = _ring_step(span / 2)
        bar = scale * k
        d.line((6, h - 6, 6 + bar, h - 6), fill=MUTED)
        d.text((10 + bar, h - 13), f"{scale:.0f} км", font=self.f_tiny, fill=MUTED)

        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=6, fill=255)
        img.paste(layer, (x0, y0), mask)

    # ---------- QR-код снимка ----------

    def _qr_screen(self, img, d, s: ViewState):
        p = s.planes[s.index]
        photo = p.photo
        self._header(d, s, f"{self._callsign(p.ac)} · ссылка на снимок")
        n = len(qr_matrix(photo.link)) + 4
        module = max(2, (self.h - 26) // n)
        qr = qr_image(photo.link, module)
        img.paste(qr, (6, 22))
        x = 14 + qr.width
        w = self.w - x - 6
        d.text((x, 26), "Фото", font=self.f_small, fill=MUTED)
        y = 44
        for line in self._wrap(d, photo.photographer, self.f_value, w, 4):
            d.text((x, y), line, font=self.f_value, fill=TEXT)
            y += 22
        d.text((x, y + 4), self._fit(d, "planespotters.net", self.f_small, w), font=self.f_small, fill=MUTED)
        for i, line in enumerate(self._wrap(d, "Наведите камеру телефона на код, чтобы открыть снимок", self.f_tiny,
                                            w, 5)):
            d.text((x, 140 + i * 15), line, font=self.f_tiny, fill=MUTED)
        d.text((x, self.h - 18), "тап — закрыть", font=self.f_tiny, fill=DIM)

    # ---------- радар ----------

    def _radar_geom(self, s: ViewState):
        cx, cy, r = self.w / 2, 20 + (self.h - 20) / 2, (self.h - 20) / 2 - 4
        k = r / s.radius_km

        def proj(lat, lon):
            xk, yk = to_km(s.home, lat, lon)
            return cx + xk * k, cy - yk * k

        return cx, cy, r, proj

    def _radar_base(self, s: ViewState) -> Image.Image:
        key = (s.home, s.radius_km)
        if key in self._radar_cache:
            return self._radar_cache[key]
        cx, cy, r, proj = self._radar_geom(s)
        base = Image.new("RGB", (self.w, self.h), BG)
        layer = Image.new("RGB", (self.w, self.h), BG)
        d = ImageDraw.Draw(layer)
        for line in MAP["coast"]:
            pts = [proj(lat, lon) for lon, lat in line["pts"]]
            if line["closed"]:
                d.polygon(pts, fill=LAND, outline=COAST)
            else:
                d.line(pts, fill=COAST, width=1)
        step = _ring_step(s.radius_km)
        for i in range(1, int(s.radius_km // step) + 1):
            rr = r * i * step / s.radius_km
            d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=RING)
            d.text((cx + 3, cy - rr + 1), f"{i * step:.0f}", font=self.f_tiny, fill=DIM)
        for ap in MAP["airports"]:
            x, y = proj(ap["lat"], ap["lon"])
            if math.hypot(x - cx, y - cy) < r - 6:
                d.rectangle((x - 2, y - 2, x + 2, y + 2), outline=MUTED)
                d.text((x + 5, y - 7), ap["iata"], font=self.f_tiny, fill=MUTED)
        mask = Image.new("L", (self.w, self.h), 0)
        ImageDraw.Draw(mask).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        base.paste(layer, (0, 0), mask)
        bd = ImageDraw.Draw(base)
        bd.ellipse((cx - r, cy - r, cx + r, cy + r), outline=DIM)
        bd.text((cx, cy - r - 1), "С", font=self.f_tiny, fill=MUTED, anchor="mb")
        bd.line((cx - 4, cy, cx + 4, cy), fill=TEXT)
        bd.line((cx, cy - 4, cx, cy + 4), fill=TEXT)
        self._radar_cache = {key: base}
        return base

    def radar_hit(self, s: ViewState, x, y) -> int | None:
        """Индекс самолёта рядом с точкой касания на радаре."""
        _, _, _, proj = self._radar_geom(s)
        best, best_d = None, 22
        for i, p in enumerate(s.planes):
            px, py = proj(p.ac.lat, p.ac.lon)
            dist = math.hypot(px - x, py - y)
            if dist < best_d:
                best, best_d = i, dist
        return best

    def _radar(self, img, d, s: ViewState):
        img.paste(self._radar_base(s))
        self._header(d, s, f"карта · {s.radius_km:.0f} км")
        cx, cy, r, proj = self._radar_geom(s)
        sel = s.planes[s.index]
        if len(sel.trail) >= 2:
            d.line([proj(la, lo) for la, lo in sel.trail] + [proj(sel.ac.lat, sel.ac.lon)], fill=TRAIL, width=2)
        shown = 1
        for i, p in enumerate(s.planes):
            x, y = proj(p.ac.lat, p.ac.lon)
            if i != s.index and math.hypot(x - cx, y - cy) <= r:  # дальше масштаба карты — не рисуем
                self._silhouette(img, p, x, y, 16, TEXT)
                shown += 1
        x, y = proj(sel.ac.lat, sel.ac.lon)
        self._silhouette(img, sel, x, y, 22, ACCENT)
        label = self._callsign(sel.ac)
        lw = d.textlength(label, font=self.f_small)
        d.text((x + 12 if x + 12 + lw < self.w else x - 12 - lw, y - 18), label, font=self.f_small, fill=ACCENT)
        # подпись выбранного в левом нижнем углу
        codes, _, _ = self._route_text(sel)
        d.text((4, self.h - 50), self._callsign(sel.ac), font=self.f_value, fill=ACCENT)
        self._text(d, (4, self.h - 30), self._fit(d, codes, self.f_small, 90), font=self.f_small, fill=TEXT)
        d.text((4, self.h - 16), self._alt(sel.ac, s.units), font=self.f_tiny, fill=MUTED)
        d.text((self.w - 4, self.h - 16), f"{shown} на карте" + (f" из {len(s.planes)}" if shown < len(s.planes) else ""), font=self.f_tiny, fill=MUTED, anchor="ra")

    # ---------- список ----------

    def list_rows(self) -> int:
        return (self.h - self.LIST_TOP) // self.ROW_H

    def list_first(self, s: ViewState) -> int:
        rows = self.list_rows()
        return min(max(0, s.index - rows // 2), max(0, len(s.planes) - rows))

    def _list(self, img, d, s: ViewState):
        self._header(d, s, f"рядом: {len(s.planes)}")
        first = self.list_first(s)
        for row, i in enumerate(range(first, min(first + self.list_rows(), len(s.planes)))):
            p = s.planes[i]
            ac = p.ac
            y = self.LIST_TOP + row * self.ROW_H
            selected = i == s.index
            if selected:
                d.rectangle((0, y, self.w, y + self.ROW_H - 1), fill=ROW_SEL)
                d.rectangle((0, y, 3, y + self.ROW_H - 1), fill=ACCENT)
            self._silhouette(img, p, 20, y + self.ROW_H / 2, 24, ACCENT if selected else MUTED)
            d.text((38, y + 1), self._fit(d, self._callsign(ac), self.f_row, 110), font=self.f_row,
                   fill=ACCENT if selected else TEXT)
            codes, cities, _ = self._route_text(p)
            sub = codes if p.route_state == FOUND else (p.airline or cities)
            sub = " · ".join(x for x in (sub, ac.type_code) if x)
            self._text(d, (38, y + 20), self._fit(d, sub, self.f_row_small, 160), font=self.f_row_small, fill=MUTED)
            alt = self._alt(ac, s.units)
            d.text((self.w - 8 - d.textlength(alt, font=self.f_row), y + 1), alt, font=self.f_row, fill=TEXT)
            dist = f"{self._dist(ac.dist_km, s.units)} {compass(ac.bearing)}"
            d.text((self.w - 8 - d.textlength(dist, font=self.f_row_small), y + 20), dist,
                   font=self.f_row_small, fill=MUTED)


def _split_symbols(text: str) -> list[str]:
    parts, cur = [], ""
    for ch in text:
        if ch in SYMBOLS:
            if cur:
                parts.append(cur)
            parts.append(ch)
            cur = ""
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


def _ring_step(radius_km: float) -> float:
    for step in (1, 2, 5, 10, 20, 25, 50, 100):
        if radius_km / step <= 4:
            return step
    return 200
