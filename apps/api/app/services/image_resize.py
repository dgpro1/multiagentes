"""Shrinks library images before they are stored and sent.

A phone photo is often 4–12 MB and 4000 px wide; WhatsApp shows it at a
fraction of that and refuses images over 5 MB. Resizing on upload keeps the
customer's bucket small, sends faster, and lets a business upload the photo
it has instead of preparing one.
"""

import io
import logging

logger = logging.getLogger(__name__)

MAX_SIDE = 1600
# Refuse absurd dimensions before decoding (a small file can declare a huge canvas).
MAX_PIXELS = 50_000_000
_FORMATS = {"image/jpeg": "JPEG", "image/jpg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def shrink_image(data: bytes, mime: str) -> tuple[bytes, str]:
    """The image fitted inside MAX_SIDE x MAX_SIDE, or the original when it is
    already small, animated, of a type left alone, or cannot be decoded."""
    target = _FORMATS.get((mime or "").lower())
    if not target:
        return data, mime
    try:
        from PIL import Image, ImageOps

        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        with Image.open(io.BytesIO(data)) as image:
            if getattr(image, "is_animated", False):
                return data, mime
            image = ImageOps.exif_transpose(image)
            if max(image.size) <= MAX_SIDE:
                return data, mime
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            if target == "JPEG" and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            out = io.BytesIO()
            options = {"optimize": True}
            if target in ("JPEG", "WEBP"):
                options["quality"] = 85
            image.save(out, target, **options)
    except Exception as exc:  # noqa: BLE001 - a picture Pillow cannot read is stored as it came
        logger.info("Left an image as uploaded (%s): %s", mime, exc)
        return data, mime
    resized = out.getvalue()
    return (resized, mime) if len(resized) < len(data) else (data, mime)
