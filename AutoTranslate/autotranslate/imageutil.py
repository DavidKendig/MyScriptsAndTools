"""Image discovery and encoding helpers."""

import base64
import io
import os
import re

try:
    from PIL import Image, ImageOps

    HAVE_PILLOW = True
except ImportError:  # Pillow is optional; we fall back to raw uploads.
    HAVE_PILLOW = False

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

_NUM_CHUNK = re.compile(r"(\d+)")


def natural_key(path):
    """Sort key so page2.png lands before page10.png."""
    name = os.path.basename(path).lower()
    return [int(part) if part.isdigit() else part for part in _NUM_CHUNK.split(name)]


def find_images(folder, recursive=False):
    """Return image paths in reading order."""
    found = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs.sort(key=str.lower)
            for name in files:
                if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                    found.append(os.path.join(root, name))
        # Group by directory, then natural-sort inside each directory.
        found.sort(key=lambda p: (os.path.dirname(p).lower(), natural_key(p)))
    else:
        try:
            entries = os.listdir(folder)
        except OSError:
            return []
        for name in entries:
            full = os.path.join(folder, name)
            if os.path.isfile(full) and os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                found.append(full)
        found.sort(key=natural_key)
    return found


def encode_image(path, max_px=1400, quality=88):
    """Return the image as a base64 string, downscaled when Pillow is present.

    Returns (b64, mime). Falls back to shipping the original bytes untouched if
    Pillow is unavailable or the file cannot be decoded.
    """
    if HAVE_PILLOW:
        try:
            with Image.open(path) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                if max_px and max(img.size) > max_px:
                    img.thumbnail((max_px, max_px), Image.LANCZOS)
                buf = io.BytesIO()
                img.convert("RGB").save(
                    buf, format="JPEG", quality=int(quality), optimize=True
                )
                return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
        except Exception:
            pass  # Corrupt or exotic format - fall through to raw bytes.

    with open(path, "rb") as fh:
        raw = fh.read()
    ext = os.path.splitext(path)[1].lower()
    mime = {
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(ext, "image/jpeg")
    return base64.b64encode(raw).decode("ascii"), mime
