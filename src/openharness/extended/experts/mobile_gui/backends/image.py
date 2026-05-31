"""Shared, stateless helpers for GUI inference backends.

Image encoding and dimension reading live here so every backend builds the
same vision payload, and so coordinate mapping can read the source image size
without pulling in Pillow.
"""

from __future__ import annotations

import base64
import mimetypes
import struct
from pathlib import Path
from typing import Any


def image_to_data_url(image_path: Path) -> str:
    """Encode an image file as a ``data:<mime>;base64,...`` URL."""
    raw = image_path.read_bytes()
    mime, _ = mimetypes.guess_type(image_path.name)
    if not mime:
        mime = "image/jpeg"
    b64 = base64.standard_b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def read_image_size(path: Path) -> tuple[int, int] | None:
    """Return ``(width, height)`` for a PNG/JPEG by parsing its header.

    Dependency-free (no Pillow): reads only the bytes needed to recover the
    dimensions. Returns ``None`` if the file can't be read or isn't a
    recognized PNG/JPEG.
    """
    try:
        raw = path.read_bytes()
    except Exception:
        return None
    try:
        if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
            width = struct.unpack(">I", raw[16:20])[0]
            height = struct.unpack(">I", raw[20:24])[0]
            if width > 0 and height > 0:
                return width, height

        if raw.startswith(b"\xff\xd8"):
            idx = 2
            raw_len = len(raw)
            while idx + 9 < raw_len:
                if raw[idx] != 0xFF:
                    idx += 1
                    continue
                marker = raw[idx + 1]
                idx += 2
                if marker in {0xD8, 0xD9}:
                    continue
                if idx + 1 >= raw_len:
                    break
                segment_len = (raw[idx] << 8) + raw[idx + 1]
                if segment_len < 2 or idx + segment_len > raw_len:
                    break
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    if idx + 7 >= raw_len:
                        break
                    height = (raw[idx + 3] << 8) + raw[idx + 4]
                    width = (raw[idx + 5] << 8) + raw[idx + 6]
                    if width > 0 and height > 0:
                        return width, height
                    break
                idx += segment_len
    except Exception:
        return None
    return None


def truncate_for_log(obj: Any, *, b64_sample: int = 48) -> Any:
    """Deep-copy ``obj``, truncating only base64 data URLs.

    Everything else is stored verbatim. Base64 image payloads keep a short
    sample plus the original length so logged JSON stays small.
    """
    if isinstance(obj, dict):
        return {k: truncate_for_log(v, b64_sample=b64_sample) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [truncate_for_log(v, b64_sample=b64_sample) for v in obj]
    if isinstance(obj, str) and obj.startswith("data:") and ";base64," in obj:
        head, b64 = obj.split(";base64,", 1)
        return f"{head};base64,{b64[:b64_sample]}...[truncated, {len(b64)} base64 chars]"
    return obj
