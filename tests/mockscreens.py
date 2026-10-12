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

WIDTH, HEIGHT = 1080, 2340
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

GROUND_TRUTH = {
    "rare_candy": 150,
    "candy": {"DRATINI": 60, "FEEBAS": 30, "CHANSEY": 70, "LARVITAR": 90},
    "storage": [
        ("DRATINI", 412),
        ("DRAGONAIR", 1543),
        ("FEEBAS", 126),
        ("CHANSEY", 389),
        ("PUPITAR", 1288),
        ("MAGIKARP", 87),
        ("EEVEE", 642),
        ("EEVEE", 701),
        ("GIBLE", 533),
        ("BELDUM", 401),
        ("SCYTHER", 1902),
        ("KIRLIA", 845),
        ("ONIX", 677),
        ("NOIBAT", 498),
        ("PIDGEY", 210),
        ("MACHOKE", 1204),
        ("HAUNTER", 1111),
        ("RALTS", 310),
    ],
    "dex_caught": [16, 129, 133, 147, 148, 113, 246, 247, 349],
    "dex_seen": [130, 149, 242],
    "detail": [("DRAGONAIR", "DRATINI"), ("FEEBAS", "FEEBAS"), ("CHANSEY", "CHANSEY"), ("PUPITAR", "LARVITAR")],
}


def _font(size: int):
    """A TrueType font at the given size: the first installed candidate, else Pillow's default."""
    for font_path in _FONT_CANDIDATES:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size)
    return ImageFont.load_default(size)


def _canvas(height: int = HEIGHT, background=(236, 244, 240)):
    """A blank phone-sized screen and a drawing context for it."""
    image = Image.new("RGB", (WIDTH, height), background)
    return image, ImageDraw.Draw(image)


def _center(draw, center_x, top, text, size, fill=(40, 60, 70)):
    """Draw text horizontally centred on center_x, with its top edge at top."""
    font = _font(size)
    text_width = draw.textlength(text, font=font)
    draw.text((center_x - text_width / 2, top), text, font=font, fill=fill)


def _to_bgr(image: Image.Image) -> np.ndarray:
    """Convert a Pillow RGB image to the BGR array OpenCV uses."""
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def bag_screen() -> np.ndarray:
    """An item bag screen listing Rare Candy, Rare Candy XL and other items with counts."""
    image, draw = _canvas()
    _center(draw, WIDTH / 2, 120, "ITEMS 312/3500", 64)
    rows = [
        ("Poke Ball", 120),
        ("Great Ball", 85),
        ("Rare Candy", GROUND_TRUTH["rare_candy"]),
        ("Rare Candy XL", 3),
        ("Razz Berry", 44),
    ]
    for index, (name, count) in enumerate(rows):
        top = 320 + index * 260
        draw.ellipse((90, top, 250, top + 160), fill=(200, 120, 160))
        draw.text((300, top + 50), name, font=_font(56), fill=(40, 60, 70))
        draw.text((860, top + 50), f"x{count}", font=_font(56), fill=(40, 60, 70))
    return _to_bgr(image)


def storage_canvas() -> np.ndarray:
    """A tall storage grid; the recording scrolls through it."""
    rows = (len(GROUND_TRUTH["storage"]) + 2) // 3
    image, draw = _canvas(height=300 + rows * 420)
    _center(draw, WIDTH / 2, 80, f"POKEMON {len(GROUND_TRUTH['storage'])}/3000", 64)
    for index, (species_id, combat_power) in enumerate(GROUND_TRUTH["storage"]):
        center_x, top = 180 + (index % 3) * 360, 260 + (index // 3) * 420
        _center(draw, center_x, top, f"CP {combat_power}", 46)
        draw.ellipse(
            (center_x - 90, top + 80, center_x + 90, top + 260),
            fill=(90 + (index * 37) % 150, 160, 200 - (index * 23) % 120),
        )
        _center(draw, center_x, top + 290, species_id.title(), 46)
    return _to_bgr(image)


def pokedex_screen() -> np.ndarray:
    """A Pokédex screen: coloured sprites for caught entries, grey silhouettes for seen-only ones."""
    image, draw = _canvas()
    _center(draw, WIDTH / 2, 100, "POKEDEX", 64)
    entries = sorted(
        [(dex_number, True) for dex_number in GROUND_TRUTH["dex_caught"]]
        + [(dex_number, False) for dex_number in GROUND_TRUTH["dex_seen"]]
    )
    for index, (dex_number, caught) in enumerate(entries):
        center_x, top = 160 + (index % 4) * 253, 300 + (index // 4) * 420
        colour = (230, 120, 40) if caught else (70, 70, 75)
        draw.ellipse((center_x - 85, top, center_x + 85, top + 170), fill=colour)
        _center(draw, center_x, top + 200, f"#{dex_number:04d}", 44)
    return _to_bgr(image)


def detail_screen(name: str, family: str) -> np.ndarray:
    """A Pokémon detail page with its CP, name, stardust, family candy and EVOLVE button."""
    image, draw = _canvas(background=(250, 250, 250))
    combat_power = dict(GROUND_TRUTH["storage"])[name]
    _center(draw, WIDTH / 2, 140, f"CP {combat_power}", 80)
    draw.ellipse((WIDTH / 2 - 260, 330, WIDTH / 2 + 260, 850), fill=(120, 170, 220))
    _center(draw, WIDTH / 2, 920, name.title(), 84)
    _center(draw, 300, 1240, "48,210", 60)
    _center(draw, 300, 1320, "STARDUST", 40)
    _center(draw, 780, 1240, str(GROUND_TRUTH["candy"][family]), 60)
    _center(draw, 780, 1320, f"{family} CANDY", 40)
    _center(draw, WIDTH / 2, 1560, "POWER UP", 56)
    _center(draw, WIDTH / 2, 1760, "EVOLVE", 56)
    return _to_bgr(image)


def write_recording(path: str | Path, fps: int = 30) -> Path:
    """Scroll the storage grid, pause on the Pokédex, then swipe through detail screens."""
    path = Path(path)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (WIDTH, HEIGHT))
    tall = storage_canvas()
    max_offset = tall.shape[0] - HEIGHT
    if max_offset <= 0:
        tall = np.vstack([tall, np.full((HEIGHT - tall.shape[0] + 1, WIDTH, 3), 240, np.uint8)])
        max_offset = tall.shape[0] - HEIGHT
    for _ in range(fps // 2):
        writer.write(tall[:HEIGHT])
    for offset in np.linspace(0, max_offset, fps * 2).astype(int):  # 2 s scroll
        writer.write(tall[offset : offset + HEIGHT])
    for _ in range(fps // 2):
        writer.write(tall[max_offset : max_offset + HEIGHT])
    for frame in [pokedex_screen()] + [
        detail_screen(species_name, family) for species_name, family in GROUND_TRUTH["detail"]
    ]:
        for _ in range(fps):  # hold each screen 1 s
            writer.write(frame)
    writer.release()
    return path


def write_fixtures(out_dir: str | Path) -> tuple[Path, Path]:
    """Write a bag screenshot and a screen recording of the synthetic screens; return both paths."""
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    bag = folder / "bag.png"
    cv2.imwrite(str(bag), bag_screen())
    return bag, write_recording(folder / "recording.mp4")


if __name__ == "__main__":
    import sys

    print(*write_fixtures(sys.argv[1] if len(sys.argv) > 1 else "mock"))
