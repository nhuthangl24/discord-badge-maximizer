#!/usr/bin/env python3
"""
Generate Application Icon for Discord Badge Maximizer
Creates assets/icon.ico (Windows multi-res icon) and assets/icon.png.
"""

from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

def create_app_icon():
    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Base Squircle / Rounded Shield (Discord Blurple background)
    pad = 20
    rect = [pad, pad, size - pad, size - pad]
    radius = 120

    # Gradient / Shadow layer
    shadow_offset = 12
    draw.rounded_rectangle(
        [pad, pad + shadow_offset, size - pad, size - pad + shadow_offset],
        radius=radius,
        fill=(15, 17, 26, 180) # Dark drop shadow
    )

    # Main Blurple Squircle
    draw.rounded_rectangle(rect, radius=radius, fill=(88, 101, 242, 255)) # #5865F2

    # Inner Glow / Ring
    inner_pad = pad + 16
    draw.rounded_rectangle(
        [inner_pad, inner_pad, size - inner_pad, size - inner_pad],
        radius=radius - 12,
        outline=(114, 137, 218, 200), # #7289DA
        width=8
    )

    # 2. Golden Badge / Quest Diamond Star in Center
    cx, cy = size // 2, size // 2

    # Draw a 4-point star / Quest Diamond Emblem
    # Outer diamond
    d_size = 140
    diamond_pts = [
        (cx, cy - d_size - 30),        # Top
        (cx + d_size + 30, cy),        # Right
        (cx, cy + d_size + 30),        # Bottom
        (cx - d_size - 30, cy),        # Left
    ]
    draw.polygon(diamond_pts, fill=(245, 166, 35, 230)) # Gold outer

    # Inner sparkling diamond
    i_size = 110
    inner_diamond = [
        (cx, cy - i_size),
        (cx + i_size, cy),
        (cx, cy + i_size),
        (cx - i_size, cy),
    ]
    draw.polygon(inner_diamond, fill=(255, 215, 0, 255)) # Pure Gold #FFD700

    # 8-point faceted star highlights
    star_pts = [
        (cx, cy - 130), (cx + 35, cy - 35),
        (cx + 130, cy), (cx + 35, cy + 35),
        (cx, cy + 130), (cx - 35, cy + 35),
        (cx - 130, cy), (cx - 35, cy - 35),
    ]
    draw.polygon(star_pts, fill=(255, 240, 150, 255)) # Highlight glow

    # Center Crown / Sparkle
    draw.ellipse([cx - 40, cy - 40, cx + 40, cy + 40], fill=(255, 255, 255, 255))
    draw.ellipse([cx - 22, cy - 22, cx + 22, cy + 22], fill=(255, 215, 0, 255))

    # Two little gaming gamepad controller dots
    draw.ellipse([cx - 95, cy + 90, cx - 65, cy + 120], fill=(255, 255, 255, 240))
    draw.ellipse([cx + 65, cy + 90, cx + 95, cy + 120], fill=(255, 255, 255, 240))

    # Save PNG
    png_path = ASSETS / "icon.png"
    img.save(png_path, format="PNG")
    print(f"Generated PNG icon: {png_path}")

    # Save Multi-resolution ICO (256, 128, 64, 48, 32, 16)
    ico_path = ASSETS / "icon.ico"
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(ico_path, format="ICO", sizes=sizes)
    print(f"Generated Windows ICO: {ico_path} ({ico_path.stat().st_size} bytes)")

if __name__ == "__main__":
    create_app_icon()
