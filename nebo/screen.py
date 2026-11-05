"""Вывод картинки: framebuffer маленького экрана или PNG-файл для отладки."""
import os
from pathlib import Path

import numpy as np
from PIL import Image


def find_framebuffer(name: str) -> str:
    for dev in sorted(os.listdir("/sys/class/graphics")):
        path = Path("/sys/class/graphics", dev, "name")
        if path.exists() and path.read_text().strip() == name:
            return dev
    raise RuntimeError(f"framebuffer {name!r} не найден")


def to_rgb565(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    return (((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)).astype("<u2")


class Framebuffer:
    def __init__(self, name: str):
        dev = find_framebuffer(name)
        sys_dir = Path("/sys/class/graphics", dev)
        self.width, self.height = map(int, (sys_dir / "virtual_size").read_text().split(","))
        bpp = int((sys_dir / "bits_per_pixel").read_text())
        if bpp != 16:
            raise RuntimeError(f"/dev/{dev}: {bpp} бит на точку, поддерживается только 16")
        self.stride = int((sys_dir / "stride").read_text())
        self.fd = os.open(f"/dev/{dev}", os.O_RDWR)
        self._last = None

    @property
    def size(self):
        return self.width, self.height

    def show(self, img: Image.Image):
        px = to_rgb565(img)
        if self.stride != self.width * 2:
            padded = np.zeros((self.height, self.stride // 2), dtype="<u2")
            padded[:, : self.width] = px
            px = padded
        data = px.tobytes()
        if data == self._last:
            return  # экран по SPI медленный — одинаковые кадры не шлём
        os.pwrite(self.fd, data, 0)
        self._last = data

    def close(self):
        os.close(self.fd)


class PngScreen:
    """Сохраняет кадры в PNG (для превью без экрана)."""

    def __init__(self, path: str, size=(320, 240)):
        self.path = path
        self.size = size

    def show(self, img: Image.Image):
        img.save(self.path)

    def close(self):
        pass
