#!/usr/bin/env python3
"""
Generate iPad screenshots from existing iPhone screenshots.
Resizes + pads to iPad Pro 12.9" dimensions (2048x2732).
Writes to screenshots/{slug}/ipad/ directory.

Usage: python3 scripts/generate-ipad-screenshots.py [--all | slug1 slug2 ...]
"""
import sys, json
from pathlib import Path
try:
    from PIL import Image, ImageDraw
except ImportError:
    import subprocess; subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pillow', '-q'])
    from PIL import Image, ImageDraw

ROOT = Path(__file__).parent.parent
SCREENSHOTS = ROOT / "screenshots"

# Required iPad screenshot sizes per Apple guidelines
IPAD_SIZES = {
    "APP_IPAD_PRO_3GEN_129": (2048, 2732),   # iPad Pro 12.9" 3rd gen+
    "APP_IPAD_PRO_3GEN_11":  (1668, 2388),   # iPad Pro 11"
}

def make_ipad_screenshot(iphone_img, target_w, target_h):
    """Scale iPhone screenshot to fit iPad canvas with a subtle background fill."""
    # Scale to fit height, center horizontally
    scale = target_h / iphone_img.height
    new_w = int(iphone_img.width * scale)
    resized = iphone_img.resize((new_w, target_h), Image.LANCZOS)

    # Create canvas — sample dominant color from edges for background
    canvas = Image.new("RGB", (target_w, target_h), (20, 20, 30))

    # Center the iPhone screenshot on canvas
    x_off = (target_w - new_w) // 2
    canvas.paste(resized.convert("RGB"), (x_off, 0))
    return canvas

def process_slug(slug):
    sc_dir = SCREENSHOTS / slug
    if not sc_dir.exists():
        print(f"  [{slug}] No screenshots dir")
        return

    iphone_files = sorted(sc_dir.glob("*.png"))
    if not iphone_files:
        print(f"  [{slug}] No PNG files")
        return

    # Only process iPhone files (not already-generated iPad files)
    iphone_files = [f for f in iphone_files if not f.name.startswith("ipad-")]

    for dtype, (tw, th) in IPAD_SIZES.items():
        for i, src in enumerate(iphone_files[:3]):
            iphone_img = Image.open(src)
            ipad_img = make_ipad_screenshot(iphone_img, tw, th)
            # Save with ipad- prefix so upload script can find them
            out_name = f"ipad-{src.stem}-{dtype}.png"
            ipad_img.save(sc_dir / out_name, "PNG")

    print(f"  [{slug}] iPad screenshots generated ({len(iphone_files[:3])} × {len(IPAD_SIZES)} sizes)")

def main():
    apps = json.loads((ROOT / "apps.json").read_text())

    if "--all" in sys.argv:
        slugs = [k for k, v in apps.items() if v.get("ascAppId")]
    else:
        slugs = [s for s in sys.argv[1:] if s not in ("--all",)]
        if not slugs:
            slugs = [k for k, v in apps.items() if v.get("ascAppId")]

    print(f"Generating iPad screenshots for {len(slugs)} apps...")
    for slug in slugs:
        process_slug(slug)
    print("Done")

if __name__ == "__main__":
    main()
