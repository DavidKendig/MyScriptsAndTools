#!/usr/bin/env python3
"""
bg_remover.py — Remove a specific color from an image and make it transparent.

Give it a hex color and an image (or a directory of images) and it writes a
PNG copy with every pixel of that color turned fully transparent. The
original file is never modified.

Supported input formats:
    .jpg / .jpeg
    .png
    .bmp
    .gif  (first frame)
    .webp
    .tif / .tiff

Output is always PNG, since JPEG cannot store transparency.

Usage:
    python bg_remover.py --color <hex> [options] <image-file-or-directory>

Examples:
    python bg_remover.py --color "#00FF00" logo.png
    python bg_remover.py --color ff00ff --tolerance 12 sprite.bmp
    python bg_remover.py --color "#FFFFFF" --output ./out ./scans
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}

# Suffix appended to the original filename for the transparent copy.
OUTPUT_SUFFIX = "_transparent"


def parse_hex_color(value: str) -> tuple[int, int, int]:
    """Parse a hex color string into an (R, G, B) tuple.

    Accepts "#RRGGBB", "RRGGBB", "#RGB", and "RGB" (case-insensitive).
    Raises ValueError on anything else.
    """
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"Invalid hex color: {value!r} (expected #RRGGBB or #RGB)")
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise ValueError(f"Invalid hex color: {value!r} (contains non-hex characters)")


def remove_color(image: Image.Image, color: tuple[int, int, int],
                 tolerance: int = 0) -> Image.Image:
    """Return a new RGBA image with every pixel matching `color` made transparent.

    tolerance is the maximum per-channel difference (0 = exact match only).
    Pixels that are already transparent are left as-is.
    """
    rgba = image.convert("RGBA")
    data = np.array(rgba)

    rgb = data[:, :, :3].astype(np.int16)
    target = np.array(color, dtype=np.int16)

    # A pixel matches when every channel is within tolerance of the target.
    diff = np.abs(rgb - target)
    mask = np.all(diff <= tolerance, axis=2)

    data[:, :, 3][mask] = 0
    return Image.fromarray(data, "RGBA")


def output_path_for(src: Path, output_dir: Path | None) -> Path:
    """Build the destination path for the transparent copy of `src`."""
    directory = output_dir if output_dir is not None else src.parent
    return directory / f"{src.stem}{OUTPUT_SUFFIX}.png"


def process_file(src: Path, color: tuple[int, int, int], tolerance: int,
                 output_dir: Path | None, overwrite: bool = False) -> Path:
    """Remove `color` from one image and save the copy. Returns the output path."""
    dest = output_path_for(src, output_dir)
    if dest.exists() and not overwrite:
        raise FileExistsError(f"{dest} already exists (use --overwrite to replace it)")

    with Image.open(src) as img:
        result = remove_color(img, color, tolerance)

    dest.parent.mkdir(parents=True, exist_ok=True)
    result.save(dest, "PNG")
    return dest


def collect_images(target: Path, recursive: bool) -> list[Path]:
    """Return the list of image files to process for a file or directory target."""
    if target.is_file():
        return [target]
    pattern = "**/*" if recursive else "*"
    files = [p for p in sorted(target.glob(pattern))
             if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
             and not p.stem.endswith(OUTPUT_SUFFIX)]
    return files


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove a specific hex color from an image, making it "
                    "transparent, and save the result as a PNG copy.")
    parser.add_argument("target", type=Path,
                        help="Image file or directory of images to process")
    parser.add_argument("-c", "--color", required=True,
                        help='Hex color to remove, e.g. "#00FF00" or ff00ff')
    parser.add_argument("-t", "--tolerance", type=int, default=0,
                        help="Max per-channel difference to still count as a "
                             "match, 0-255 (default: 0 = exact)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Directory for output files (default: alongside "
                             "each source image)")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="When target is a directory, recurse into subfolders")
    parser.add_argument("--overwrite", action="store_true",
                        help="Replace existing output files")
    args = parser.parse_args()

    try:
        color = parse_hex_color(args.color)
    except ValueError as e:
        parser.error(str(e))

    if not 0 <= args.tolerance <= 255:
        parser.error("--tolerance must be between 0 and 255")

    if not args.target.exists():
        parser.error(f"No such file or directory: {args.target}")

    files = collect_images(args.target, args.recursive)
    if not files:
        print(f"No supported images found in {args.target}", file=sys.stderr)
        return 1

    failures = 0
    for src in files:
        try:
            dest = process_file(src, color, args.tolerance, args.output,
                                args.overwrite)
            print(f"  {src.name}  ->  {dest}")
        except Exception as e:
            failures += 1
            print(f"  {src.name}  FAILED: {e}", file=sys.stderr)

    done = len(files) - failures
    print(f"\nDone: {done}/{len(files)} image(s) processed.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
