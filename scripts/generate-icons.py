#!/usr/bin/env python3
"""
Generate all required iOS icon sizes and write them directly into the Xcode
AppIcon.appiconset. Replaces @capacitor/assets generate — more reliable in CI.

Usage: python3 scripts/generate-icons.py <source_icon.png>
       (source must be 1024x1024 PNG)
"""
import sys, json
from pathlib import Path
try:
    from PIL import Image
except ImportError:
    import subprocess, sys as _s
    subprocess.check_call([_s.executable, '-m', 'pip', 'install', 'pillow', '-q'])
    from PIL import Image

SRC = sys.argv[1] if len(sys.argv) > 1 else 'resources/icon.png'
DEST = Path('ios/App/App/Assets.xcassets/AppIcon.appiconset')

ICON_SPECS = [
    # iPhone
    ("iphone", "2x",  "20x20",   40),
    ("iphone", "3x",  "20x20",   60),
    ("iphone", "2x",  "29x29",   58),
    ("iphone", "3x",  "29x29",   87),
    ("iphone", "2x",  "40x40",   80),
    ("iphone", "3x",  "40x40",  120),
    ("iphone", "2x",  "60x60",  120),
    ("iphone", "3x",  "60x60",  180),
    # iPad
    ("ipad",   "1x",  "20x20",   20),
    ("ipad",   "2x",  "20x20",   40),
    ("ipad",   "1x",  "29x29",   29),
    ("ipad",   "2x",  "29x29",   58),
    ("ipad",   "1x",  "40x40",   40),
    ("ipad",   "2x",  "40x40",   80),
    ("ipad",   "1x",  "76x76",   76),
    ("ipad",   "2x",  "76x76",  152),
    ("ipad",   "2x",  "83.5x83.5", 167),
    # App Store
    ("ios-marketing", "1x", "1024x1024", 1024),
]

def main():
    src = Path(SRC)
    if not src.exists():
        print(f"ERROR: icon not found at {src}")
        sys.exit(1)

    DEST.mkdir(parents=True, exist_ok=True)

    img = Image.open(src).convert('RGBA')
    if img.size != (1024, 1024):
        print(f"  Resizing source from {img.size} to 1024x1024")
        img = img.resize((1024, 1024), Image.LANCZOS)

    images_manifest = []
    written = set()

    for idiom, scale, size_str, px in ICON_SPECS:
        fname = f"icon-{px}.png"
        if px not in written:
            resized = img.resize((px, px), Image.LANCZOS)
            resized.save(DEST / fname, 'PNG')
            written.add(px)
        images_manifest.append({
            "filename": fname,
            "idiom": idiom,
            "scale": scale,
            "size": size_str,
        })

    contents = {
        "images": images_manifest,
        "info": {"author": "xcode", "version": 1}
    }
    (DEST / 'Contents.json').write_text(json.dumps(contents, indent=2))
    print(f"  Icons generated: {len(written)} sizes → {DEST}")

if __name__ == '__main__':
    main()
