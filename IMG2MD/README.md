# Image to Markdown

Extracts text from images and converts it into structured Markdown using a multimodal model (e.g. [Gemma 4](https://ollama.com/library/gemma4)) running locally on [Ollama](https://ollama.com). No cloud services or API keys required.

Handles the formats produced by modern iPhone cameras as well as common desktop formats — useful for receipts, whiteboards, screenshots, scanned documents, and photographs of slides.

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) installed and running
- A multimodal model pulled in Ollama:
  ```bash
  ollama pull gemma4
  ```

## Installation

Double-click `start.bat` on Windows — it installs the requirements and launches the GUI.

Or install manually:

```bash
pip install -r requirements.txt
```

For HEIC/HEIF (iPhone "High Efficiency" mode, the default since iOS 11), install the optional decoder:

```bash
pip install pillow-heif
```

## Quick Start

**GUI** — select files and folders with a point-and-click interface:

```bash
python gui.py
```

**Command line** — for scripting and batch processing:

```bash
# Convert a single image
python img_to_markdown.py receipt.heic

# Convert all images in a folder
python img_to_markdown.py ./photos

# Recurse into subdirectories
python img_to_markdown.py -r ./screenshots

# Write output to a specific directory
python img_to_markdown.py --output ./markdown ./photos
```

## Supported formats

| Extension       | Notes                                                   |
|-----------------|---------------------------------------------------------|
| `.jpg` / `.jpeg`| iPhone "Most Compatible" mode, general photos           |
| `.png`          | Screenshots, lossless exports                           |
| `.heic` / `.heif` | iPhone "High Efficiency" mode (default since iOS 11). Requires `pillow-heif`. |

## How It Works

### 1. Image loading

[Pillow](https://pillow.readthedocs.io) opens the image, applies the EXIF orientation tag (so portrait phone shots are read upright instead of sideways), and downscales the long edge to at most 2048 pixels. Larger images do not improve recognition with current multimodal models and just inflate the request payload.

### 2. Sending to the model

The image is encoded as a base64 PNG and sent to Ollama's `/api/generate` endpoint as a multimodal request. The system prompt instructs the model to:

- Transcribe all visible text exactly
- Infer heading levels from font size / visual prominence
- Convert lists to Markdown lists
- Preserve tables as pipe tables
- Render charts/graphs/diagrams as ASCII art inside fenced code blocks
- Ignore purely decorative content (photos of people, logos, background art)
- Output exactly `*(no text detected)*` for empty images

### 3. Output

Each image produces a `.md` file in the same directory (or the directory specified with `--output`). The file begins with YAML front-matter and then the converted content:

```markdown
---
source: receipt.heic
format: heic
---

# Whole Foods Market
123 Main St · 555-0100

| Item              | Qty | Price |
| ----------------- | --- | ----- |
| Organic apples    |  2  | 4.99  |
| Sourdough loaf    |  1  | 6.50  |
| ...               |     |       |

Total: 18.47
```

## Options

```
python img_to_markdown.py [options] <image-file-or-directory>

positional arguments:
  input              Image file or directory to process

options:
  --model  NAME      Ollama model name (default: gemma4)
  --url    URL       Ollama base URL   (default: http://localhost:11434)
  --output DIR       Output directory  (default: same directory as each image)
  -r, --recursive    Recurse into subdirectories
  --timeout  SEC     Max seconds to wait between streamed chunks (default: 900)
  --num-ctx  TOKENS  Model context window in tokens (default: 8192)
```

## Examples

```bash
# Use a larger model for harder images (handwriting, tiny print)
python img_to_markdown.py --model gemma4:27b whiteboard.jpg

# Batch a folder of receipts with output going to a separate folder
python img_to_markdown.py --output ./md ./receipts

# Recurse a screenshots tree
python img_to_markdown.py -r --output ./md ./screenshots

# Point at a remote Ollama instance
python img_to_markdown.py --url http://192.168.1.10:11434 receipt.heic
```

## Troubleshooting

**`Connection refused` error** — Ollama is not running. Start it with `ollama serve`.

**`model not found` error** — The model has not been pulled. Run `ollama pull gemma4`.

**HEIC files are skipped** — Install the optional decoder: `pip install pillow-heif`.

**Photos come out rotated 90°** — Your image viewer is honouring EXIF but something earlier in the pipeline stripped it. IMG2MD always applies EXIF orientation, so this shouldn't happen — if it does, re-save the image and try again.

**Poor recognition on small or low-contrast text** — Try a larger model (`gemma4:12b` or `gemma4:27b`), retake the photo with better lighting, or pre-process the image (crop tightly to the text, increase contrast) before running.

**Output cuts off mid-image** — The model's prediction budget ran out. Raise `--num-ctx`.
