"""Draws the Everlast icon (an hourglass in brass on deep slate) and writes the PNG set
and icon.svg into eqtool_native/icons/.   python3 tools/make_icon.py   (needs Pillow, only
to make the files; the app itself needs nothing extra)."""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "eqtool_native", "icons")
SIZES = (16, 22, 24, 32, 48, 64, 128, 256)

SLATE_TOP, SLATE_BOT = (38, 50, 68), (18, 24, 34)
BRASS, BRASS_LIGHT, BRASS_DARK = (201, 163, 90), (236, 208, 140), (150, 118, 58)
S = 1024


def draw():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # background: rounded square with a soft vertical gradient
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(SLATE_TOP, SLATE_BOT)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((24, 24, S - 24, S - 24), radius=230, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((24, 24, S - 24, S - 24), radius=230, outline=BRASS, width=34)
    d.rounded_rectangle((70, 70, S - 70, S - 70), radius=190, outline=BRASS_DARK, width=8)

    cx = S // 2
    top, bot, neck = 236, 788, 512
    half, nh = 232, 26                          # half width of the glass, half width of the neck
    # glass (dark inside, brass outline)
    glass = [(cx - half, top), (cx + half, top), (cx + nh, neck - 6), (cx + nh, neck + 6),
             (cx + half, bot), (cx - half, bot), (cx - nh, neck + 6), (cx - nh, neck - 6)]
    d.polygon(glass, fill=(14, 19, 27, 255))
    # sand: top bulb partly full, a falling thread, a mound below
    d.polygon([(cx - 150, 330), (cx + 150, 330), (cx + 22, neck - 20), (cx - 22, neck - 20)], fill=BRASS_LIGHT)
    d.rectangle((cx - 9, neck - 20, cx + 9, 690), fill=BRASS_LIGHT)
    d.polygon([(cx - 190, bot - 18), (cx + 190, bot - 18), (cx + 70, 640), (cx - 70, 640)], fill=BRASS_LIGHT)
    d.line(glass + [glass[0]], fill=BRASS, width=40, joint="curve")
    # caps
    for y in (top - 34, bot - 4):
        d.rounded_rectangle((cx - half - 52, y, cx + half + 52, y + 70), radius=30, fill=BRASS)
        d.rounded_rectangle((cx - half - 52, y, cx + half + 52, y + 24), radius=22, fill=BRASS_LIGHT)
    return img


def svg():
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#263244"/><stop offset="1" stop-color="#121822"/></linearGradient></defs>
<rect x="24" y="24" width="976" height="976" rx="230" fill="url(#g)" stroke="#c9a35a" stroke-width="34"/>
<rect x="70" y="70" width="884" height="884" rx="190" fill="none" stroke="#96763a" stroke-width="8"/>
<polygon points="280,236 744,236 538,506 538,518 744,788 280,788 486,518 486,506" fill="#0e131b" stroke="#c9a35a" stroke-width="40" stroke-linejoin="round"/>
<polygon points="362,330 662,330 534,492 490,492" fill="#ecd08c"/>
<rect x="503" y="492" width="18" height="198" fill="#ecd08c"/>
<polygon points="322,770 702,770 582,640 442,640" fill="#ecd08c"/>
<rect x="228" y="202" width="568" height="70" rx="30" fill="#c9a35a"/><rect x="228" y="202" width="568" height="24" rx="22" fill="#ecd08c"/>
<rect x="228" y="784" width="568" height="70" rx="30" fill="#c9a35a"/><rect x="228" y="784" width="568" height="24" rx="22" fill="#ecd08c"/>
</svg>
"""


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    big = draw()
    for n in SIZES:
        big.resize((n, n), Image.LANCZOS).save(os.path.join(OUT, f"everlast-{n}.png"), optimize=True)
    with open(os.path.join(OUT, "everlast.svg"), "w") as f:
        f.write(svg())
    big.resize((512, 512), Image.LANCZOS).save("/home/claude/icon_preview.png")
    print("wrote", len(SIZES), "PNGs and the SVG to", os.path.abspath(OUT))
