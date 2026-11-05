#!/usr/bin/env python3
"""Калибровка тача экрана nebo.

Рисует по очереди крестики на экране, ждёт нажатия на каждый и печатает
сырые координаты сенсора (ABS_X/ABS_Y). Заодно печатает нажатия кнопок.
Зависимости: Pillow. Запуск: sudo python3 touch_calibrate.py
"""
import fcntl
import json
import os
import select
import struct
import sys
import time

from PIL import Image, ImageDraw, ImageFont

FB_NAME = "fb_ili9340"
TOUCH_NAME = "ADS7846 Touchscreen"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

EV_KEY, EV_ABS = 0x01, 0x03
ABS_X, ABS_Y = 0x00, 0x01
BTN_TOUCH = 0x14A
EVENT = struct.Struct("llHHi")  # struct input_event на 64-битной системе
EVIOCGRAB = 0x40044590  # монопольный захват: события не уходят в консоль

MARGIN = 24
TIMEOUT = 60  # секунд на одну точку


def find_fb(name):
    for dev in sorted(os.listdir("/sys/class/graphics")):
        path = f"/sys/class/graphics/{dev}/name"
        if os.path.exists(path) and open(path).read().strip() == name:
            sys_dir = f"/sys/class/graphics/{dev}"
            w, h = map(int, open(f"{sys_dir}/virtual_size").read().split(","))
            return f"/dev/{dev}", w, h
    sys.exit(f"framebuffer {name!r} не найден")


def find_inputs():
    """Возвращает {путь: имя} для всех устройств ввода."""
    result = {}
    for dev in sorted(os.listdir("/sys/class/input")):
        if dev.startswith("event"):
            name = open(f"/sys/class/input/{dev}/device/name").read().strip()
            result[f"/dev/input/{dev}"] = name
    return result


def show(fb_path, img):
    """Пишет картинку Pillow во framebuffer в формате RGB565."""
    raw = img.convert("RGB").tobytes("raw", "RGB")
    out = bytearray(len(raw) // 3 * 2)
    for i in range(0, len(raw) // 3):
        r, g, b = raw[3 * i], raw[3 * i + 1], raw[3 * i + 2]
        struct.pack_into("<H", out, 2 * i, ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
    with open(fb_path, "wb") as fb:
        fb.write(out)


def screen(w, h, target, text, color):
    img = Image.new("RGB", (w, h), "black")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, 16)
    d.text((w // 2, h // 2 + 40), text, fill="white", font=font, anchor="mm")
    if target:
        x, y = target
        d.line((x - 12, y, x + 12, y), fill=color, width=2)
        d.line((x, y - 12, x, y + 12), fill=color, width=2)
        d.ellipse((x - 5, y - 5, x + 5, y + 5), outline=color, width=2)
    return img


def main():
    fb_path, w, h = find_fb(FB_NAME)
    inputs = find_inputs()
    fds = {}
    touch_fd = None
    for path, name in inputs.items():
        if name == TOUCH_NAME or name.startswith("button@"):
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            fcntl.ioctl(fd, EVIOCGRAB, 1)
            fds[fd] = name
            if name == TOUCH_NAME:
                touch_fd = fd
    if touch_fd is None:
        sys.exit("тач не найден")
    print(f"fb={fb_path} {w}x{h}; устройства: {sorted(fds.values())}", flush=True)

    targets = [
        ("левый верхний", (MARGIN, MARGIN)),
        ("правый верхний", (w - MARGIN, MARGIN)),
        ("правый нижний", (w - MARGIN, h - MARGIN)),
        ("левый нижний", (MARGIN, h - MARGIN)),
        ("центр", (w // 2, h // 2)),
    ]
    results = []
    for label, pos in targets:
        show(fb_path, screen(w, h, pos, f"Нажми крестик: {label}", "red"))
        down, xs, ys = False, [], []
        cur = {ABS_X: None, ABS_Y: None}
        deadline = time.time() + TIMEOUT
        done = False
        while not done and time.time() < deadline:
            ready, _, _ = select.select(list(fds), [], [], 0.5)
            for fd in ready:
                data = os.read(fd, EVENT.size * 64)
                for off in range(0, len(data), EVENT.size):
                    _, _, etype, code, value = EVENT.unpack_from(data, off)
                    if fd != touch_fd:
                        if etype == EV_KEY:
                            state = "нажата" if value == 1 else "отпущена" if value == 0 else "повтор"
                            print(f"кнопка {fds[fd]} код={code} {state}", flush=True)
                        continue
                    if etype == EV_ABS and code in cur:
                        cur[code] = value
                        if down and cur[ABS_X] is not None and cur[ABS_Y] is not None:
                            xs.append(cur[ABS_X])
                            ys.append(cur[ABS_Y])
                    elif etype == EV_KEY and code == BTN_TOUCH:
                        if value == 1:
                            down, xs, ys = True, [], []
                        else:
                            down = False
                            if len(xs) >= 3:
                                done = True
        if not done:
            print(f"{label}: нажатия не было", flush=True)
            results.append({"label": label, "screen": pos, "raw": None})
            continue
        xs.sort()
        ys.sort()
        raw = (xs[len(xs) // 2], ys[len(ys) // 2])
        print(f"{label}: экран={pos} сырые={raw} (замеров {len(xs)})", flush=True)
        results.append({"label": label, "screen": pos, "raw": raw})
        show(fb_path, screen(w, h, pos, "Есть!", "lime"))
        time.sleep(0.6)

    show(fb_path, screen(w, h, None, "Готово, спасибо!", "white"))
    print("JSON " + json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
