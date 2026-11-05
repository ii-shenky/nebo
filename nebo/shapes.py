"""Силуэты самолётов сверху — свои, перенесены из Nebo Deck (shared/nebo-core/silhouettes.ts).

Один силуэт на класс самолёта; класс выбирается по описанию типа ICAO (Doc 8643: вид,
число и тип двигателей), категории турбулентности и категории излучателя ADS-B.
Все формы — многоугольники в условных единицах: нос к −y, ось x = 0, длина ≈ 100.
Рисуются через Pillow с 4-кратным сглаживанием, cairosvg не нужен. Pillow импортируется
только в Silhouettes: данные форм и выбор формы доступны и без него (демо-сервер на Mac).
"""
import math
import re
from decimal import ROUND_HALF_UP, Decimal
from dataclasses import dataclass
from functools import lru_cache

Point = tuple[float, float]


def _sym(half: list[Point]) -> list[Point]:
    """Замкнутый контур из правой половины (x ≥ 0, от носа к хвосту), отражённой налево."""
    return half + [(-x, y) for x, y in reversed(half)]


def _rect(x1, y1, x2, y2) -> list[Point]:
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def _pair(points: list[Point]) -> list[list[Point]]:
    """Деталь не по центру (двигатель, винт) и её зеркальное отражение."""
    return [points, [(-x, y) for x, y in points]]


def _ellipse(cx, cy, rx, ry, steps=28) -> list[Point]:
    return [(cx + rx * math.sin(a), cy - ry * math.cos(a))
            for a in (i / steps * math.pi * 2 for i in range(steps))]


def _blade(cx, cy, length, width, angle_deg) -> list[Point]:
    """Тонкая лопасть через (cx, cy), length от кончика до кончика, под углом к оси x."""
    a = math.radians(angle_deg)
    dx, dy = math.cos(a) * length / 2, math.sin(a) * length / 2
    nx, ny = -math.sin(a) * width / 2, math.cos(a) * width / 2
    return [(cx - dx + nx, cy - dy + ny), (cx + dx + nx, cy + dy + ny),
            (cx + dx - nx, cy + dy - ny), (cx - dx - nx, cy - dy - ny)]


@dataclass(frozen=True)
class Shape:
    outlines: tuple[tuple[Point, ...], ...]
    box: tuple[float, float, float, float]  # minX, minY, width, height
    no_rotate: bool = False  # нарисован сбоку (шар) — по курсу не поворачивать


def _shape(outlines: list[list[Point]], no_rotate=False) -> Shape:
    xs = [x for o in outlines for x, _ in o]
    ys = [y for o in outlines for _, y in o]
    return Shape(tuple(tuple(o) for o in outlines),
                 (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)), no_rotate)


SHAPES: dict[str, Shape] = {
    # Двухмоторный реактивный, стреловидное крыло, двигатели под крылом (A320, 737).
    "narrowbody": _shape([
        _sym([(0, -50), (2.5, -49), (4.5, -46), (5.5, -41), (5.5, -8), (44, 14), (46, 15), (46, 18.5),
              (5.5, 10), (5.5, 32), (19, 42), (19, 45.5), (4.5, 43.5), (2, 50), (0, 50)]),
        *_pair(_rect(12.5, -8, 17.5, 5)),
    ]),
    # Двухмоторный с длинным крылом и большими двигателями (A330, 777, 787).
    "widebody": _shape([
        _sym([(0, -50), (2.5, -49), (4, -46.5), (5, -42), (5, -10), (46, 16), (48, 17), (48, 20.5),
              (5, 9), (5, 33), (18, 43), (18, 46.5), (4, 44.5), (2, 50), (0, 50)]),
        *_pair(_rect(14, -13, 21, 3)),
    ]),
    # Четыре двигателя под крылом (747, A340, C-17).
    "quadjet": _shape([
        _sym([(0, -50), (2.5, -49), (4.5, -46.5), (5.5, -42), (5.5, -12), (47, 15), (49, 16), (49, 19.5),
              (5.5, 7), (5.5, 33), (19, 43), (19, 46.5), (4.5, 44.5), (2, 50), (0, 50)]),
        *_pair(_rect(13, -11, 18, 1)),
        *_pair(_rect(27, -2, 32, 10)),
    ]),
    # Двигатели в хвосте, Т-образное оперение (бизнес-джеты, CRJ, 727).
    "rearjet": _shape([
        _sym([(0, -50), (2.5, -49), (4.5, -46), (5.5, -40), (5.5, -2), (40, 15), (42, 16), (42, 19.5),
              (5.5, 13), (5.5, 40), (17, 45), (17, 48.5), (3, 48), (1.5, 50), (0, 50)]),
        *_pair(_rect(5.5, 20, 11.5, 33)),
    ]),
    # Боевой самолёт и реактивный учебный: острый нос, короткое стреловидное крыло.
    "fighter": _shape([
        _sym([(0, -50), (1.5, -46), (3.5, -36), (4.5, -22), (6, -14), (8, -8), (30, 10), (30, 16),
              (8, 18), (7, 28), (18, 38), (18, 44), (6, 42), (4, 50), (0, 50)]),
    ]),
    # Одномоторный поршневой или турбовинтовой, прямое крыло (C172, PC-12).
    "single_prop": _shape([
        _sym([(0, -50), (3, -49), (6.5, -41), (7, -30), (66, -30), (67, -28), (67, -16), (7, -15),
              (3.5, 30), (22, 32), (23, 33), (23, 42), (2.5, 43), (1.5, 48), (0, 48)]),
        _sym([(0, -51.5), (13, -51.5), (13, -50), (0, -50)]),
    ]),
    # Двухмоторный винтовой (King Air, ATR, Dash 8).
    "twin_prop": _shape([
        _sym([(0, -50), (3, -49), (5.5, -45), (6.5, -38), (6.5, -14), (56, -12), (58, -11), (58, -4),
              (6.5, -1), (5, 30), (22, 35), (22, 42), (3.5, 44), (2, 50), (0, 50)]),
        *_pair([(16, -27), (22, -27), (23, -24), (23, 2), (15, 2), (15, -24)]),
        *_pair(_rect(8, -28.5, 30, -27)),
    ]),
    # Четыре винта, высокое прямое крыло (C-130, A400M).
    "quad_prop": _shape([
        _sym([(0, -50), (4, -49), (7, -45), (8, -38), (8, -14), (62, -12), (64, -11), (64, -4),
              (8, -2), (7, 26), (4, 33), (26, 36), (26, 44), (4, 46), (2, 50), (0, 50)]),
        *_pair(_rect(17, -25.5, 23, 0)),
        *_pair(_rect(36, -25.5, 42, 0)),
        *_pair(_rect(12, -26.5, 28, -25)),
        *_pair(_rect(31, -26.5, 47, -25)),
    ]),
    # Вертолёт или автожир: кабина, хвостовая балка, крест несущего винта, рулевой винт.
    "helicopter": _shape([
        _sym([(0, -36), (4, -35), (7, -31), (8.5, -24), (8.5, -6), (6, 2), (2.5, 8), (2, 40), (9, 40),
              (9, 43), (2, 43), (1.5, 47), (0, 47)]),
        _blade(0, -16, 92, 4.5, 45),
        _blade(0, -16, 92, 4.5, -45),
        _rect(9, 37, 10.5, 46),
    ]),
    # Очень длинное прямое крыло, тонкий фюзеляж.
    "glider": _shape([
        _sym([(0, -40), (3, -38), (4.5, -33), (5, -28), (96, -25), (98, -24), (98, -18.5), (5, -15),
              (2.5, 32), (17, 34), (17, 39.5), (2.5, 40.5), (1.5, 44), (0, 44)]),
    ]),
    # Дрон: длинное прямое крыло и V-образное оперение.
    "uav": _shape([
        _sym([(0, -40), (3, -39), (5, -34), (5.5, -20), (66, -15), (67, -14), (67, -10), (5.5, -8),
              (3, 28), (18, 40), (19, 43), (3, 36), (2, 44), (0, 44)]),
    ]),
    # Воздушный шар сбоку: оболочка, горловина, корзина.
    "balloon": _shape([
        _ellipse(0, -14, 30, 30),
        _sym([(0, -10), (28, -4), (8, 22), (0, 22)]),
        _sym([(0, 26), (6.5, 26), (6, 35), (0, 35)]),
    ], no_rotate=True),
    # Дирижабль сверху: длинная оболочка с хвостовыми стабилизаторами.
    "airship": _shape([
        _ellipse(0, -4, 13, 46),
        *_pair([(8, 26), (19, 36), (19, 44), (6, 40)]),
    ]),
    # Стрелка, когда о типе ничего не известно (в Nebo Deck рисуется отдельно).
    "unknown": _shape([
        _sym([(0, -50), (33, 40), (0, 22)]),
    ]),
}

# Обозначения типов, которым описание не даёт нужной формы.
SHAPE_BY_TYPE = {"SHIP": "airship", "Q4": "uav", "Q25": "uav"}

# Двухмоторные боевые и учебные: их описание (L2J) такое же, как у лайнера или бизнес-джета.
TWIN_COMBAT_JETS = frozenset("""
A10 A6 AJET AT3 BUC CKUO E530 EUFI F1 F111 F117 F14 F15 F18H F18S F22 F4 F5 F5SA J20 J8A J8B JAGR
JH7 KAAN KF21 L15 LTNG M346 MG19 MG25 MG29 MG31 MT2 Q5 RFAL S37 SU24 SU25 SU27 SU57 T2 T38 T4
T5YY TOR Y130 YURO
""".split())

# Двухмоторные с двигателями в хвосте (бизнес- и региональные джеты, семейство DC-9).
REAR_ENGINES = re.compile(
    r"GULFSTREAM|CHALLENGER|BD-700|LEARJET|CITATION|FALCON|PHENOM|PRAETOR|LEGACY|REGIONAL JET"
    r"|ERJ-1[34]|DC-9|MD-8|MD-9|BOEING 717|FOKKER|BAC 1-11|BEECHJET|BEECH 400|PREMIER|HONDAJET"
    r"|TU-134|TU-334|ASTRA|WESTWIND|SABRELINER|SABRE [4-8]|JETSTAR|HAWKER|HS-125|HFB-320|MU-300"
    r"|PC-24|COMMODORE JET", re.IGNORECASE)

# Категория излучателя ADS-B → форма, когда тип неизвестен.
SHAPE_BY_CATEGORY = {
    "A1": "single_prop", "A2": "rearjet", "A3": "narrowbody", "A4": "narrowbody", "A5": "widebody",
    "A6": "fighter", "A7": "helicopter", "B1": "glider", "B2": "balloon", "B4": "single_prop",
    "B6": "uav",
}


def _shape_for_description(desc: str, wake: str | None, type_name: str | None) -> str | None:
    kind, engines, engine_type = desc[0], int(desc[1]) if desc[1].isdigit() else 0, desc[2]
    if kind in ("H", "G"):
        return "helicopter"
    if kind == "B":
        return "balloon"
    if kind == "D":
        return "uav"
    if kind in ("R", "T"):
        return "twin_prop"
    if kind not in ("L", "A", "S"):
        return None
    if engines == 0:
        return "glider"
    if engine_type == "J":
        if engines >= 4:
            return "quadjet"
        if engines == 3:
            return "widebody" if wake == "H" else "rearjet"
        if engines == 1:
            return "rearjet" if wake == "L" else "fighter"
        if wake in ("H", "J"):
            return "widebody"
        if wake == "L" or (type_name and REAR_ENGINES.search(type_name)):
            return "rearjet"
        return "narrowbody"
    if engines >= 4:
        return "quad_prop"
    return "single_prop" if engines == 1 else "twin_prop"


def shape_for(type_code, desc, wtc, category, type_name=None) -> str:
    """Имя формы по обозначению типа (A320), описанию ICAO (L2J), категории турбулентности
    (L/M/H/J), категории ADS-B (A3) и названию типа; «unknown», если ничего не известно."""
    code = type_code.upper() if type_code else None
    if code in SHAPE_BY_TYPE:
        return SHAPE_BY_TYPE[code]
    if code in TWIN_COMBAT_JETS:
        return "fighter"
    d = desc.upper() if desc else None
    if d and len(d) == 3:
        shape = _shape_for_description(d, wtc.upper() if wtc else None, type_name)
        if shape:
            return shape
    return SHAPE_BY_CATEGORY.get(category.upper() if category else "", "unknown")


def _num(n: float) -> str:
    """Число как в JS: округление до сотых, без хвостовых нулей."""
    v = math.floor(n * 100 + 0.5) / 100
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def _fixed(n: float, digits: int) -> str:
    """Как toFixed() в JS: половина округляется от нуля."""
    return str(Decimal(repr(n)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def shapes_json() -> dict:
    """Формы и таблица «категория → форма» для веб-страниц."""
    return {
        "shapes": {n: {"paths": svg_paths(n), "box": list(sh.box), "noRotate": sh.no_rotate} for n, sh in SHAPES.items()},
        "byCategory": SHAPE_BY_CATEGORY,
    }


def svg_paths(name: str) -> list[str]:
    """Контуры формы как SVG-пути (для веб-страниц)."""
    return ["M" + "L".join(f"{_num(x)} {_num(y)}" for x, y in o) + "Z" for o in SHAPES[name].outlines]


def svg_fragment(name: str, cx, cy, size, track_deg: float | None, fill: str) -> str:
    """Силуэт по центру (cx, cy), вписанный в квадрат size, повёрнутый по курсу (нос вверх = север).
    Тот же вывод, что silhouetteSvg() в Nebo Deck."""
    s = SHAPES.get(name)
    if not s:
        return ""
    bx, by, bw, bh = s.box
    scale = size / max(bw, bh)
    rotate = f" rotate({_fixed(track_deg, 0)})" if track_deg is not None and not s.no_rotate else ""
    paths = "".join(f'<path d="{d}"/>' for d in svg_paths(name))
    return (f'<g transform="translate({cx} {cy}){rotate} scale({_fixed(scale, 4)}) '
            f'translate({_num(-(bx + bw / 2))} {_num(-(by + bh / 2))})" fill="{fill}">{paths}</g>')


class Silhouettes:
    """Отрисовка силуэтов в картинки Pillow с кешем."""
    SUPERSAMPLE = 4

    def shape_for(self, type_code, desc, wtc, category, type_name=None) -> str:
        return shape_for(type_code, desc, wtc, category, type_name)

    def rotates(self, name: str) -> bool:
        return not SHAPES.get(name, SHAPES["unknown"]).no_rotate

    @lru_cache(maxsize=64)
    def upright(self, name: str, size: int, color: tuple):
        """Силуэт носом вверх, вписанный в квадрат size×size, RGBA."""
        from PIL import Image, ImageDraw
        s = SHAPES.get(name) or SHAPES["unknown"]
        bx, by, bw, bh = s.box
        ss = self.SUPERSAMPLE
        big = size * ss
        k = big / max(bw, bh)
        ox, oy = bx + bw / 2, by + bh / 2
        canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        fill = (*color[:3], 255)
        for outline in s.outlines:
            draw.polygon([((x - ox) * k + big / 2, (y - oy) * k + big / 2) for x, y in outline], fill=fill)
        return canvas.resize((size, size), Image.LANCZOS)

    @lru_cache(maxsize=512)
    def icon(self, name: str, size: int, color: tuple, angle: int = 0):
        """Силуэт, повёрнутый по курсу (angle — градусы по часовой, округляйте до 5°).

        После поворота картинка может стать больше size — вставлять по центру.
        """
        from PIL import Image
        img = self.upright(name, size, color)
        if angle and self.rotates(name):
            img = img.rotate(-angle, resample=Image.BICUBIC, expand=True)
        return img
