# Background Remover

Remove a specific hex color from an image and make it transparent. The tool
writes a new PNG copy (`<name>_transparent.png`) and never modifies the
original file.

## Quick start (GUI)

Double-click `start.bat`. It checks Python, installs the requirements, and
launches the GUI. Pick an image or a folder, choose the color (type a hex
code or use the color picker), optionally raise the tolerance, and click
**Remove Background**.

## Command line

```
python bg_remover.py --color <hex> [options] <image-file-or-directory>
```

| Option | Description |
| --- | --- |
| `-c, --color` | Hex color to remove, e.g. `#00FF00`, `ff00ff`, or `#F0F` (required) |
| `-t, --tolerance` | Max per-channel difference to still count as a match, 0–255 (default 0 = exact) |
| `-o, --output` | Directory for output files (default: alongside each source image) |
| `-r, --recursive` | When the target is a directory, recurse into subfolders |
| `--overwrite` | Replace existing `_transparent.png` files |

### Examples

```
python bg_remover.py --color "#00FF00" logo.png
python bg_remover.py --color ff00ff --tolerance 12 sprite.bmp
python bg_remover.py --color "#FFFFFF" --output ./out ./scans
```

## Notes

- Output is always PNG because JPEG cannot store transparency.
- JPEG compression slightly shifts colors, so an "exact" color in a .jpg is
  rarely exact — use a tolerance of 5–15 for JPEG sources.
- Supported inputs: `.jpg` `.jpeg` `.png` `.bmp` `.gif` `.webp` `.tif` `.tiff`

## Requirements

- Python 3.10+
- Pillow, numpy (installed automatically by `start.bat`)
