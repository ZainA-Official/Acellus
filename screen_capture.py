"""
screen_capture.py – Grabs the Acellus window (or whole monitor) as a PNG.

Uses `mss` for fast, cross-platform screen grabs. Returns both the raw PNG
bytes (for Gemini) and the (offset_x, offset_y, width, height) of the region
so we can convert Gemini's normalized 0-1000 coordinates back into absolute
screen pixels for the mouse.
"""

from __future__ import annotations
from dataclasses import dataclass
import io

import mss
from PIL import Image

import config


@dataclass
class Frame:
    png_bytes: bytes      # PNG-encoded screenshot
    offset_x: int         # left edge of capture region, in screen pixels
    offset_y: int         # top edge of capture region, in screen pixels
    width: int            # capture width in pixels
    height: int           # capture height in pixels

    def to_screen(self, norm_x: float, norm_y: float) -> tuple[int, int]:
        """
        Convert Gemini's normalized 0-1000 (x, y) into absolute screen pixels.
        """
        px = self.offset_x + int((norm_x / 1000.0) * self.width)
        py = self.offset_y + int((norm_y / 1000.0) * self.height)
        return px, py


def capture(region: tuple[int, int, int, int] | None = None) -> Frame:
    """Capture the configured monitor or region and return a Frame.

    Pass an explicit (left, top, width, height) tuple to override CAPTURE_REGION
    for this one call — used when a different area is needed (e.g. the full-width
    video control bar to read elapsed/total time).
    """
    with mss.mss() as sct:
        if region is not None:
            left, top, width, height = region
        elif config.CAPTURE_REGION is not None:
            left, top, width, height = config.CAPTURE_REGION
        else:
            mon = sct.monitors[config.CAPTURE_MONITOR]
            left, top, width, height = (
                mon["left"], mon["top"], mon["width"], mon["height"]
            )

        mss_region = {"left": left, "top": top, "width": width, "height": height}
        shot = sct.grab(mss_region)

        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

        # Optionally resize. IMAGE_MAX_WIDTH=None sends full resolution (better
        # for reading math). Frame stores the ORIGINAL region dimensions so the
        # coordinate mapping back to screen pixels stays correct regardless.
        max_w = config.IMAGE_MAX_WIDTH
        if max_w is not None and img.width > max_w:
            scale = max_w / img.width
            new_h = int(img.height * scale)
            img = img.resize((max_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)

        return Frame(
            png_bytes=buf.getvalue(),
            offset_x=left,
            offset_y=top,
            width=width,
            height=height,
        )
