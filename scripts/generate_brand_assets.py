"""Generate Home Assistant brand assets for the Rebrama integration.

Downloads the official Rebrama wordmark, splits the gate symbol from the
wordmark, and produces transparent light/dark ``icon`` and ``logo`` PNGs that
satisfy the Home Assistant brands image specification.

The artwork is pure black on a white background, so we derive the alpha channel
from luminance (black -> opaque, white -> transparent). This yields clean,
anti-aliased transparency and lets us recolor the same mask to white for the
dark-theme variants.

Run:  python scripts/generate_brand_assets.py
"""

from __future__ import annotations

import io
import os
import urllib.request

from PIL import Image

LOGO_URL = "https://rebrama.com/logo.png"
OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "custom_components",
    "rebrama",
    "brand",
)

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)


def load_rgb_over_white(data: bytes) -> Image.Image:
    """Flatten any transparency onto white and return an RGB image."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.alpha_composite(bg, img).convert("RGB")


def to_alpha(img_rgb: Image.Image) -> Image.Image:
    """Build an alpha mask: dark pixels opaque, light pixels transparent."""
    return img_rgb.convert("L").point(lambda p: 255 - p)


def trim(alpha: Image.Image) -> Image.Image:
    """Crop away fully-transparent borders."""
    bbox = alpha.getbbox()
    return alpha.crop(bbox) if bbox else alpha


def detect_symbol_end(alpha: Image.Image) -> int:
    """Return the x where the gate symbol ends (the wide gap before the text)."""
    width, height = alpha.size
    px = alpha.load()
    col_has_content = []
    threshold = 0
    sums = []
    for x in range(width):
        total = sum(px[x, y] for y in range(0, height, 4))
        sums.append(total)
    threshold = max(sums) * 0.02
    col_has_content = [s > threshold for s in sums]

    # Skip leading blank, then walk the symbol, allowing small inter-bar gaps.
    i = 0
    while i < width and not col_has_content[i]:
        i += 1
    end = i
    gap = 0
    while i < width:
        if col_has_content[i]:
            end = i + 1
            gap = 0
        else:
            gap += 1
            if gap > 40:  # a gap this wide marks the start of the wordmark
                break
        i += 1
    # Fallback if detection looks wrong.
    if not (width * 0.1 < end < width * 0.5):
        end = int(width * 0.28)
    return end


def variant(alpha: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """Colorize an alpha mask onto a transparent RGBA image."""
    rgba = Image.new("RGBA", alpha.size, (*color, 0))
    rgba.putalpha(alpha)
    return rgba


def square(alpha: Image.Image, pad_ratio: float = 0.12) -> Image.Image:
    """Trim then center the mask on a transparent square canvas with margin."""
    a = trim(alpha)
    w, h = a.size
    side = max(w, h)
    margin = int(side * pad_ratio)
    canvas = Image.new("L", (side + 2 * margin, side + 2 * margin), 0)
    canvas.paste(a, (margin + (side - w) // 2, margin + (side - h) // 2))
    return canvas


def resize_alpha(alpha: Image.Image, *, height: int) -> Image.Image:
    """Resize preserving aspect ratio to a target height."""
    w, h = alpha.size
    width = round(w * height / h)
    return alpha.resize((width, height), Image.LANCZOS)


def save(alpha: Image.Image, color: tuple[int, int, int], name: str) -> None:
    """Save a colored variant of an alpha mask as an optimized PNG."""
    path = os.path.join(OUT_DIR, name)
    variant(alpha, color).save(path, "PNG", optimize=True)
    print(f"  {name}: {variant(alpha, color).size}")


def main() -> None:
    """Generate all eight brand images."""
    os.makedirs(OUT_DIR, exist_ok=True)
    req = urllib.request.Request(LOGO_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()

    rgb = load_rgb_over_white(data)
    full_alpha = to_alpha(rgb)

    # --- icon: just the gate symbol, square ---
    symbol_end = detect_symbol_end(full_alpha)
    symbol = full_alpha.crop((0, 0, symbol_end, full_alpha.height))
    icon_sq = square(symbol)
    icon_256 = resize_alpha(icon_sq, height=256)
    icon_512 = resize_alpha(icon_sq, height=512)
    print("icons:")
    save(icon_256, BLACK, "icon.png")
    save(icon_512, BLACK, "icon@2x.png")
    save(icon_256, WHITE, "dark_icon.png")
    save(icon_512, WHITE, "dark_icon@2x.png")

    # --- logo: full wordmark, landscape ---
    logo = trim(full_alpha)
    logo_256 = resize_alpha(logo, height=256)
    logo_512 = resize_alpha(logo, height=512)
    print("logos:")
    save(logo_256, BLACK, "logo.png")
    save(logo_512, BLACK, "logo@2x.png")
    save(logo_256, WHITE, "dark_logo.png")
    save(logo_512, WHITE, "dark_logo@2x.png")


if __name__ == "__main__":
    main()
