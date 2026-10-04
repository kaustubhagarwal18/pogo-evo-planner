"""Render synthetic stand-ins for the game's screens, for pipeline tests.

These only mimic the *text layout* the parsers rely on (labels, numbers, a
coloured or grey sprite blob). They are not real game screenshots, so passing
tests here proves the plumbing, not real-world OCR accuracy.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 2340
_FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/Library/Fonts/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"]

GROUND_TRUTH = {
    "rare_candy": 150,
    "candy": {"DRATINI": 60, "FEEBAS": 30, "CHANSEY": 70, "LARVITAR": 90},
    "storage": [("DRATINI", 412), ("DRAGONAIR", 1543), ("FEEBAS", 126), ("CHANSEY", 389),
                ("PUPITAR", 1288), ("MAGIKARP", 87), ("EEVEE", 642), ("EEVEE", 701),
                ("GIBLE", 533), ("BELDUM", 401), ("SCYTHER", 1902), ("KIRLIA", 845),
                ("ONIX", 677), ("NOIBAT", 498), ("PIDGEY", 210), ("MACHOKE", 1204),
                ("HAUNTER", 1111), ("RALTS", 310)],
    "dex_caught": [16, 129, 133, 147, 148, 113, 246, 247, 349],
    "dex_seen": [130, 149, 242],
    "detail": [("DRAGONAIR", "DRATINI"), ("FEEBAS", "FEEBAS"), ("CHANSEY", "CHANSEY"), ("PUPITAR", "LARVITAR")],
}


def _font(size: int):
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def _canvas(h: int = H, bg=(236, 244, 240)):
    img = Image.new("RGB", (W, h), bg)
    return img, ImageDraw.Draw(img)


def _center(d, x, y, text, size, fill=(40, 60, 70)):
    f = _font(size)
    tw = d.textlength(text, font=f)
    d.text((x - tw / 2, y), text, font=f, fill=fill)


def _to_bgr(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def bag_screen() -> np.ndarray:
    img, d = _canvas()
    _center(d, W / 2, 120, "ITEMS 312/3500", 64)
    rows = [("Poke Ball", 120), ("Great Ball", 85), ("Rare Candy", GROUND_TRUTH["rare_candy"]),
            ("Rare Candy XL", 3), ("Razz Berry", 44)]
    for i, (name, n) in enumerate(rows):
        y = 320 + i * 260
        d.ellipse((90, y, 250, y + 160), fill=(200, 120, 160))
        d.text((300, y + 50), name, font=_font(56), fill=(40, 60, 70))
        d.text((860, y + 50), f"x{n}", font=_font(56), fill=(40, 60, 70))
    return _to_bgr(img)


def storage_canvas() -> np.ndarray:
    """A tall storage grid; the recording scrolls through it."""
    rows = (len(GROUND_TRUTH["storage"]) + 2) // 3
    img, d = _canvas(h=300 + rows * 420)
    _center(d, W / 2, 80, f"POKEMON {len(GROUND_TRUTH['storage'])}/3000", 64)
    for i, (sp, cp) in enumerate(GROUND_TRUTH["storage"]):
        cx, y = 180 + (i % 3) * 360, 260 + (i // 3) * 420
        _center(d, cx, y, f"CP {cp}", 46)
        d.ellipse((cx - 90, y + 80, cx + 90, y + 260), fill=(90 + (i * 37) % 150, 160, 200 - (i * 23) % 120))
        _center(d, cx, y + 290, sp.title(), 46)
    return _to_bgr(img)


def pokedex_screen() -> np.ndarray:
    img, d = _canvas()
    _center(d, W / 2, 100, "POKEDEX", 64)
    entries = sorted([(n, True) for n in GROUND_TRUTH["dex_caught"]] + [(n, False) for n in GROUND_TRUTH["dex_seen"]])
    for i, (num, caught) in enumerate(entries):
        cx, y = 160 + (i % 4) * 253, 300 + (i // 4) * 420
        colour = (230, 120, 40) if caught else (70, 70, 75)
        d.ellipse((cx - 85, y, cx + 85, y + 170), fill=colour)
        _center(d, cx, y + 200, f"#{num:04d}", 44)
    return _to_bgr(img)


def detail_screen(name: str, family: str) -> np.ndarray:
    img, d = _canvas(bg=(250, 250, 250))
    cp = dict(GROUND_TRUTH["storage"])[name]
    _center(d, W / 2, 140, f"CP {cp}", 80)
    d.ellipse((W / 2 - 260, 330, W / 2 + 260, 850), fill=(120, 170, 220))
    _center(d, W / 2, 920, name.title(), 84)
    _center(d, 300, 1240, "48,210", 60)
    _center(d, 300, 1320, "STARDUST", 40)
    _center(d, 780, 1240, str(GROUND_TRUTH["candy"][family]), 60)
    _center(d, 780, 1320, f"{family} CANDY", 40)
    _center(d, W / 2, 1560, "POWER UP", 56)
    _center(d, W / 2, 1760, "EVOLVE", 56)
    return _to_bgr(img)


def write_recording(path: str | Path, fps: int = 30) -> Path:
    """Scroll the storage grid, pause on the Pokédex, then swipe through detail screens."""
    path = Path(path)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    tall = storage_canvas()
    max_off = tall.shape[0] - H
    if max_off <= 0:
        tall = np.vstack([tall, np.full((H - tall.shape[0] + 1, W, 3), 240, np.uint8)])
        max_off = tall.shape[0] - H
    for _ in range(fps // 2):
        vw.write(tall[:H])
    for off in np.linspace(0, max_off, fps * 2).astype(int):   # 2 s scroll
        vw.write(tall[off:off + H])
    for _ in range(fps // 2):
        vw.write(tall[max_off:max_off + H])
    for frame in [pokedex_screen()] + [detail_screen(n, f) for n, f in GROUND_TRUTH["detail"]]:
        for _ in range(fps):                                      # hold each screen 1 s
            vw.write(frame)
    vw.release()
    return path


def write_fixtures(out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bag = out / "bag.png"
    cv2.imwrite(str(bag), bag_screen())
    return bag, write_recording(out / "recording.mp4")


if __name__ == "__main__":
    import sys
    print(*write_fixtures(sys.argv[1] if len(sys.argv) > 1 else "mock"))
