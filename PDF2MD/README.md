# PDF to Markdown

Converts PDF files to Markdown using a multimodal model (e.g. [Gemma 4](https://ollama.com/library/gemma4)) running locally on [Ollama](https://ollama.com). No cloud services or API keys required.

Supports both text-based and scanned (image-only) PDFs, with special handling for multi-column layouts, tables, and sidebars.

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) installed and running
- A model pulled in Ollama (for vision/hybrid modes the model must be multimodal):
  ```bash
  ollama pull gemma4
  ```

## Installation

Double-click `start.bat` on Windows — it installs the requirements and launches the GUI.

Or install manually:

```bash
pip install -r requirements.txt
```

## Quick Start

**GUI** — select files and folders with a point-and-click interface:

```bash
python gui.py
```

**Command line** — for scripting and batch processing:

```bash
# Convert a single PDF
python pdf_to_markdown.py report.pdf

# Convert all PDFs in a folder
python pdf_to_markdown.py ./docs

# Recurse into subdirectories
python pdf_to_markdown.py -r ./docs

# Write output to a specific directory
python pdf_to_markdown.py --output ./markdown ./docs
```

## How It Works

### 1. Text extraction

[PyMuPDF](https://pymupdf.readthedocs.io) reads each page and extracts its text content in layout order. It also reads any embedded metadata (title, author, subject) which is written as YAML front-matter at the top of the output file.

### 2. Scanned PDF detection (auto mode)

After extraction, the program calculates the average number of extractable characters per page. If that average falls below 100 characters the PDF is treated as a scanned document and vision mode is used instead.

### 3. Processing modes

| Mode     | How it works                                                                                            | Best for                                       |
|----------|---------------------------------------------------------------------------------------------------------|------------------------------------------------|
| `auto` (default) | Text extraction; falls back to vision if the PDF appears scanned                                | Most PDFs                                      |
| `text`   | Text extraction only — the extracted plain text is sent to the model                                    | Born-digital PDFs with simple layouts          |
| `vision` | Renders every page as a PNG. Multi-column pages are auto-split into ordered crops before being sent     | Scanned docs, complex layouts, forms           |
| `hybrid` | Sends BOTH the page image and the extracted text in one request — image for layout, text for accuracy   | Multi-column documents with sidebars / tables  |

### 4. Multi-column handling

Reading order is the single biggest source of error on multi-column documents, so it is handled by geometry rather than left to the model.

**The column selector** (`--columns`, or the *Columns* dropdown in the GUI) accepts `auto`, `1`, `2`, or `3`. Detection is decent but conservative, and it has no way to know what it's looking at. Telling it outright is the highest-leverage accuracy setting in the tool:

| Setting | Effect |
|---------|--------|
| `auto` (default) | Infers the layout per page from ink and text-block geometry. Bails out to whole-page whenever the evidence is weak. |
| `1` | Never splits the page and never re-orders the text. Rules out spurious column splits entirely — the worst failure mode, since a bad split chops every line mid-sentence. |
| `2` / `3` | Forces the split even when the gutter is tight or partly obscured, pre-orders the extracted text, and tells the model exactly how many columns to expect. |

**Vision mode** runs a layout analysis on the rendered image before sending:

- Locates the column gutter(s) from horizontal ink density
- Groups consecutive rows into bands separated by full-width elements (headings, tables that span columns, banners)
- Within each multi-column band, emits crops left to right — column 1, then column 2, then column 3
- Full-width bands (and single-column pages) are sent whole; blank crops are dropped
- Each crop is sent with a *single-column* prompt, because a crop is one column by construction

This prevents the model from ever needing to stitch columns together on its own. If the split produces more than 12 regions the declared column count almost certainly doesn't match the page, so the page is sent whole instead and a note is printed.

**Text and hybrid modes** re-sequence the extracted text before the model sees it. PyMuPDF's default `sort=True` ordering is y-then-x, which interleaves columns line by line; the block bounding boxes are instead grouped into full-width bands and columns and emitted in true reading order. The model is then told the text is already ordered and instructed not to reorder it — which turns a reasoning task into a formatting task. If re-sequencing would drop any words (an odd block decomposition, or a table), the raw extraction order is used instead and a note is printed.

### 5. Output verification & retries

Every page goes through two automatic quality checks. Each check can trigger a retry (up to 3 total attempts per page):

- **Text-coverage check** — fraction of the source page's unique words that appear in the model's output. If less than **95%** of the words made it through, the page is retried.
- **Column-order check** — PyMuPDF's text-block bounding boxes are used to build a ground-truth reading order (full-width bands → column 1 top-to-bottom → column 2 → column 3). Snippets from each block are then located in the model output; if fewer than **85%** of consecutive snippet pairs appear in the correct order, the page is retried. Setting `--columns` explicitly also makes this check more trustworthy: its expected order comes from the same gutter logic, so a wrong guess there produces spurious retries.

After all retries, the attempt with the best combined score (`0.4 × coverage + 0.6 × order`) is written to the output file. If either check is still below threshold, a `WARNING` is printed to the log so you know which pages may need a manual review.

### 6. Sending to the model

**Text mode** — extracted plain text is sent with a system prompt carrying the page's layout rule: preserve the order as-is for single-column and pre-ordered pages, or reconstruct multi-column reading order when the layout is unknown.

**Vision mode** — each page (or each detected column crop) is rendered at 2× zoom (144 dpi) using PyMuPDF, encoded as a base64 PNG, and sent via Ollama's multimodal API.

**Hybrid mode** — both the rendered page image and the PyMuPDF-extracted text are sent in a single request. The model uses the text as authoritative content (no OCR errors) and the image as authoritative layout (correct reading order, tables, sidebars).

For all modes the request is sent with `think: false` (so thinking-capable models like Gemma 4 don't burn their token budget on chain-of-thought) and a configurable `num_ctx` (default 8192).

### 7. Output

Each PDF produces a `.md` file in the same directory (or the directory specified with `--output`). The file begins with YAML front-matter, followed by per-page sections separated by `<!-- page N of M -->` markers:

```markdown
---
title:   Annual Report 2024
author:  Jane Smith
source:  annual_report.pdf
pages:   42
---

<!-- page 1 of 42 -->

# Annual Report 2024
...

<!-- page 2 of 42 -->
...
```

A timestamped `.log` file is also written alongside the Markdown output, containing every progress line, coverage/order score, retry, and warning from the run.

## Options

```
python pdf_to_markdown.py [options] <pdf-file-or-directory>

positional arguments:
  input              PDF file or directory to process

options:
  --model  NAME      Ollama model name (default: gemma4)
  --url    URL       Ollama base URL   (default: http://localhost:11434)
  --output DIR       Output directory  (default: same directory as each PDF)
  --mode   MODE      auto | text | vision | hybrid  (default: auto)
  --columns  N       auto | 1 | 2 | 3 — page-layout column count (default: auto)
  --start-page N     Page number to resume from (default: 1)
  -r, --recursive    Recurse into subdirectories
  --timeout  SEC     Max seconds to wait between streamed chunks (default: 900)
  --num-ctx  TOKENS  Model context window in tokens (default: 8192)
```

## Examples

```bash
# Use a larger model for better accuracy
python pdf_to_markdown.py --model gemma4:27b report.pdf

# Force vision mode for a complex scanned document
python pdf_to_markdown.py --mode vision scanned_contract.pdf

# Hybrid mode for a magazine with columns, sidebars, and tables
python pdf_to_markdown.py --mode hybrid magazine.pdf

# Two-column journal article — force the split instead of hoping detection fires
python pdf_to_markdown.py --columns 2 journal_article.pdf

# Three-column newsletter
python pdf_to_markdown.py --columns 3 --mode vision newsletter.pdf

# Known single-column report — rule out spurious column splits entirely
python pdf_to_markdown.py --columns 1 --mode text plain_report.pdf

# Resume a long conversion that was interrupted at page 37
python pdf_to_markdown.py --start-page 37 big_book.pdf

# Bigger context window for very dense pages (uses more VRAM)
python pdf_to_markdown.py --num-ctx 16384 dense_paper.pdf

# Batch convert with a remote Ollama instance
python pdf_to_markdown.py --url http://192.168.1.10:11434 -r ./docs
```

## Troubleshooting

**`Connection refused` error** — Ollama is not running. Start it with `ollama serve`.

**`model not found` error** — The model has not been pulled. Run `ollama pull gemma4`.

**Empty output / `Ollama returned an empty response`** — The model may not actually support images, may be a thinking-only model that consumed its whole budget on chain-of-thought, or may have run out of VRAM. Try a smaller variant (`gemma4:4b`), a known vision model (`llava`, `llama3.2-vision`), or close other GPU applications.

**Columns out of order despite verification** — set `--columns` to the actual layout first; that's what forces the deterministic split and the text pre-ordering, and it usually fixes this on its own. Failing that, increase `--num-ctx` (some long pages don't fit), or try `--mode hybrid` which gives the model both text and image as anchors.

**Single-column text is coming back scrambled or cut mid-sentence** — column detection has false-positived on a wide margin or a sparse region and split the page. Set `--columns 1`.

**`layout split produced N regions … sending it whole instead`** — the declared `--columns` value doesn't match that page, so a synthesized gutter was slicing through body text. Either the document isn't uniformly N columns (try `auto`), or you have the count wrong.

**Poor output quality on scanned PDFs** — Try forcing vision or hybrid mode. Also try a larger model variant (`gemma4:12b` or `gemma4:27b`).

**Output cuts off mid-document** — The model's `num_predict` budget ran out. Raise `--num-ctx` and/or use a model with a larger native context window.

**`WARNING best coverage was only X%`** — The model dropped content even after retries. The `.log` file lists which pages were affected; consider re-running just those pages with `--start-page` and a different mode or model.
