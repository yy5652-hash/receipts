"""Grades the photographs in film/assets to the film's warm paper palette -> work/photos/<name>.jpg

    python tools/grade_photos.py

Lower saturation, a gentle contrast, warm highlights, lifted blacks, a soft vignette. No retouching beyond that:
the pictures are only set dressing behind the film's own type, and every one is credited in CREDITS.md.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent.parent
SRC, OUT = HERE / "assets", HERE / "work/photos"
OUT.mkdir(parents=True, exist_ok=True)

LOOKS = {  # per photograph: saturation, contrast, warm tint (r, g, b), black lift (r, g, b), vignette strength
    "loc-main-reading-room-highsmith": dict(sat=0.60, contrast=0.90, warm=(1.03, 1.0, 0.92), lift=(0.105, 0.090, 0.072), vignette=0.30),
    "byron-white-courthouse-columns-highsmith": dict(sat=0.45, contrast=0.92, warm=(1.04, 1.0, 0.90), lift=(0.100, 0.088, 0.072), vignette=0.28),
    "loc-serials-card-catalog-2025": dict(sat=0.55, contrast=0.92, warm=(1.04, 1.0, 0.91), lift=(0.095, 0.082, 0.068), vignette=0.30),
}


def grade(path, sat, contrast, warm, lift, vignette):
    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255
    lum = (a * np.array([0.299, 0.587, 0.114], dtype=np.float32)).sum(2, keepdims=True)
    a = lum + (a - lum) * sat                                  # lower saturation
    a = (a - 0.5) * contrast + 0.5                              # gentler contrast
    a = a * np.array(warm, dtype=np.float32)                    # warm highlights
    lift = np.array(lift, dtype=np.float32)
    a = lift + a * (1 - lift)                                   # paper-toned blacks
    h, w = a.shape[:2]
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    r = ((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2
    a = a * (1 - vignette * np.clip(r / 2, 0, 1) ** 1.2)[..., None]   # soft vignette
    return Image.fromarray((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8))


def soften_box(img, x0, y0, x1, y1, radius=9, feather=0.03):
    """The same, for a small box (the drawer label)."""
    blurred = img.filter(ImageFilter.GaussianBlur(radius))
    h, w = img.size[1], img.size[0]
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    m = (np.clip((x / w - x0) / feather, 0, 1) * np.clip((x1 - x / w) / feather, 0, 1)
         * np.clip((y / h - y0) / feather, 0, 1) * np.clip((y1 - y / h) / feather, 0, 1))
    return Image.composite(blurred, img, Image.fromarray((m * 255).astype(np.uint8)))


def soften_text(img, x0, y0, radius=14, feather=0.06):
    """Blurs the part of the picture right of x0 (and below y0), as a camera's shallow focus would, so that the legible
    catalogue card in the card-catalog photograph does not compete with the film's own words."""
    blurred = img.filter(ImageFilter.GaussianBlur(radius))
    h, w = img.size[1], img.size[0]
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    mx = np.clip((x / w - x0) / feather, 0, 1) * np.clip((y / h - y0) / feather, 0, 1)
    return Image.composite(blurred, img, Image.fromarray((mx * 255).astype(np.uint8)))


for name, look in LOOKS.items():
    img = grade(SRC / f"{name}.jpg", **look)
    if name == "loc-serials-card-catalog-2025":
        img = soften_text(img, x0=0.34, y0=0.10)
        img = soften_box(img, 0.18, 0.08, 0.44, 0.42)       # the drawer label
    img.save(OUT / f"{name}.jpg", quality=92, optimize=True)
    print(name, img.size)
