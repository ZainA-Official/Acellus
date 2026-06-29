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
    png_bytes: bytes      # encoded screenshot (PNG or JPEG depending on config)
    mime_type: str        # "image/png" or "image/jpeg"
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

    def box_center_to_screen(self, box: list[float]) -> tuple[int, int] | None:
        """
        Convert a normalized [ymin, xmin, ymax, xmax] (0-1000) bounding box into
        the absolute screen pixel of its center. Returns None for a malformed box.
        """
        if not box or len(box) != 4:
            return None
        ymin, xmin, ymax, xmax = box
        return self.to_screen((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)


def monitor_region(monitor: int | None = None) -> tuple[int, int, int, int]:
    """Return (left, top, width, height) of a whole monitor in screen pixels.

    Used by auto mode, which captures the full monitor so every click target
    (input bar, answer tiles, Continue button, video controls) is in-frame for
    bounding-box detection.
    """
    with mss.mss() as sct:
        mon = sct.monitors[monitor if monitor is not None else config.CAPTURE_MONITOR]
        return (mon["left"], mon["top"], mon["width"], mon["height"])


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
        fmt = getattr(config, "IMAGE_FORMAT", "PNG").upper()
        if fmt == "JPEG":
            img = img.convert("RGB")  # JPEG doesn't support alpha channel
            quality = getattr(config, "IMAGE_JPEG_QUALITY", 80)
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            mime = "image/jpeg"
        else:
            img.save(buf, format="PNG", optimize=True)
            mime = "image/png"

        return Frame(
            png_bytes=buf.getvalue(),
            mime_type=mime,
            offset_x=left,
            offset_y=top,
            width=width,
            height=height,
        )
