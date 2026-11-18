#!/usr/bin/env python3
"""Draw the integration icon (a flat front view of the FS2PG plug).

    python3 tools/make_icon.py        # needs Pillow

Writes custom_components/ferguson_fs2pg/brand/icon.png (256 px) and
icon@2x.png (512 px). The drawing is original; it only follows the plug's
shape and has no vendor logo.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "custom_components" / "ferguson_fs2pg" / "brand"
S = 4  # supersampling factor
U = 512 * S  # drawing size; coordinates below are in a 1024 grid


def p(*values: float) -> list[float]:
    return [v * U / 1024 for v in values]


def vertical_gradient(size, top, bottom) -> Image.Image:
    grad = Image.new("RGBA", (1, 256))
    for y in range(256):
        t = y / 255
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return grad.resize(size)


def draw() -> Image.Image:
    img = Image.new("RGBA", (U, U), (0, 0, 0, 0))

    # Power button on top of the case.
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(p(392, 0, 632, 96), radius=p(36)[0], fill=(205, 210, 217, 255))
    d.rounded_rectangle(p(404, 10, 620, 96), radius=p(30)[0], fill=(232, 235, 239, 255))

    # Case: rounded square with a soft top-to-bottom gradient and an outline.
    body = p(0, 48, 1024, 1024)
    mask = Image.new("L", (U, U), 0)
    ImageDraw.Draw(mask).rounded_rectangle(body, radius=p(250)[0], fill=255)
    img.paste(vertical_gradient((U, U), (255, 255, 255, 255), (229, 232, 236, 255)), (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(body, radius=p(250)[0], outline=(168, 174, 183, 255), width=round(p(16)[0]))

    # Front panel seam.
    d.rounded_rectangle(p(72, 120, 952, 952), radius=p(190)[0], outline=(212, 216, 222, 255), width=round(p(10)[0]))

    # Wi-Fi mark where the vendor logo sits on the real plug.
    blue = (33, 150, 243, 255)
    cx, cy = 512, 330
    for r in (120, 78):
        d.arc(p(cx - r, cy - r, cx + r, cy + r), start=225, end=315, fill=blue, width=round(p(26)[0]))
    d.ellipse(p(cx - 20, cy - 20, cx + 20, cy + 20), fill=blue)

    # Socket recess with a faint inner shadow.
    sx, sy, sr = 512, 640, 250
    shadow = Image.new("L", (U, U), 0)
    ImageDraw.Draw(shadow).ellipse(p(sx - sr, sy - sr + 14, sx + sr, sy + sr + 14), fill=90)
    shadow = shadow.filter(ImageFilter.GaussianBlur(p(14)[0]))
    recess = Image.new("L", (U, U), 0)
    ImageDraw.Draw(recess).ellipse(p(sx - sr, sy - sr, sx + sr, sy + sr), fill=255)
    img.paste((214, 218, 224, 255), (0, 0), recess)
    inner = Image.new("L", (U, U), 0)
    ImageDraw.Draw(inner).ellipse(p(sx - sr + 16, sy - sr + 22, sx + sr - 16, sy + sr - 6), fill=255)
    img.paste(vertical_gradient((U, U), (236, 238, 241, 255), (250, 251, 252, 255)), (0, 0), inner)
    d = ImageDraw.Draw(img)
    d.ellipse(p(sx - sr, sy - sr, sx + sr, sy + sr), outline=(178, 184, 192, 255), width=round(p(12)[0]))

    # Earth pin (seen end-on) with a metallic gradient.
    px, py, pr = 512, 520, 44
    pin = Image.new("L", (U, U), 0)
    ImageDraw.Draw(pin).ellipse(p(px - pr, py - pr, px + pr, py + pr), fill=255)
    img.paste(vertical_gradient((U, U), (250, 250, 250, 255), (120, 124, 130, 255)), (0, 0), pin)
    d = ImageDraw.Draw(img)
    d.ellipse(p(px - pr, py - pr, px + pr, py + pr), outline=(110, 114, 120, 255), width=round(p(8)[0]))

    # Live and neutral holes.
    for hx in (402, 622):
        d.ellipse(p(hx - 40, 680 - 40, hx + 40, 680 + 40), fill=(96, 14, 38, 255))
        d.ellipse(p(hx - 28, 680 - 30, hx + 28, 680 + 22), fill=(150, 24, 58, 255))

    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        big.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)
        print(OUT / name)


if __name__ == "__main__":
    main()
