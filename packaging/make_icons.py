"""Makes icon.png (Linux) and icon.icns (macOS) from logo.ico.   python make_icons.py logo.ico outdir"""
import sys, os
from PIL import Image

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
im = Image.open(src)
best = max(im.info.get("sizes", [im.size]), key=lambda s: s[0] * s[1])
im.size = best
im = im.convert("RGBA")
side = max(im.size)
sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
big = sq.resize((512, 512), Image.LANCZOS)
big.save(os.path.join(out, "icon.png"))
big.save(os.path.join(out, "icon.icns"))
print("icons ->", out)
