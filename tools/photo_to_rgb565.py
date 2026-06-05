#!/usr/bin/env python3
"""Convert an image to RGB565 C header for TFT_eSPI display (240x240)."""
import sys
from pathlib import Path
from PIL import Image, ImageOps

SIZE = 240


def rgb888_to_rgb565(r: int, g: int, b: int) -> int:
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def convert(src: Path, dst: Path, var_name: str = "nabeel_photo") -> None:
    img = Image.open(src).convert("RGB")
    img = ImageOps.fit(img, (SIZE, SIZE), Image.LANCZOS, centering=(0.5, 0.5))
    img = img.rotate(180)
    pixels = list(img.getdata())

    with dst.open("w") as f:
        f.write("#ifndef ME_PHOTO_H\n#define ME_PHOTO_H\n\n")
        f.write("#include <pgmspace.h>\n#include <stdint.h>\n\n")
        f.write(f"#define ME_PHOTO_W {SIZE}\n#define ME_PHOTO_H {SIZE}\n\n")
        f.write(f"static const uint16_t {var_name}[{SIZE * SIZE}] PROGMEM = {{\n")
        line = []
        for i, (r, g, b) in enumerate(pixels):
            line.append(f"0x{rgb888_to_rgb565(r, g, b):04X}")
            if (i + 1) % 12 == 0:
                f.write("  " + ", ".join(line) + ",\n")
                line = []
        if line:
            f.write("  " + ", ".join(line) + "\n")
        f.write("};\n\n#endif\n")
    print(f"Wrote {dst} ({SIZE}x{SIZE} = {SIZE*SIZE} pixels, {SIZE*SIZE*2} bytes)")


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Desktop/me.jpeg"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else (
        Path(__file__).resolve().parent.parent / "sandy" / "me_photo.h"
    )
    convert(src, dst)
