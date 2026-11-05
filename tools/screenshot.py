#!/usr/bin/env python3
"""Снимок того, что сейчас на экране nebo: framebuffer (RGB565) → PNG.

Запуск на малинке: python3 screenshot.py /tmp/screen.png
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

FB_NAME = "fb_ili9340"


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "screen.png"
    for dev in sorted(Path("/sys/class/graphics").glob("fb*")):
        if (dev / "name").read_text().strip() == FB_NAME:
            break
    else:
        sys.exit(f"framebuffer {FB_NAME!r} не найден")
    w, h = map(int, (dev / "virtual_size").read_text().split(","))
    stride = int((dev / "stride").read_text())
    raw = np.frombuffer(Path(f"/dev/{dev.name}").read_bytes()[: stride * h], dtype="<u2")
    px = raw.reshape(h, stride // 2)[:, :w]
    rgb = np.dstack([(px >> 11) << 3, ((px >> 5) & 0x3F) << 2, (px & 0x1F) << 3]).astype(np.uint8)
    Image.fromarray(rgb, "RGB").save(out)
    print(out)


if __name__ == "__main__":
    main()
