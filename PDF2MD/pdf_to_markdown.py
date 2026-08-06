#!/usr/bin/env python3
"""
pdf_to_markdown.py — Convert PDF files to Markdown using Gemma on Ollama.

Supports Gemma 3 (text-only) and Gemma 4 (multimodal).  In auto mode the
script detects scanned/image-heavy PDFs and switches to vision processing
automatically.

Usage:
    python pdf_to_markdown.py [options] <pdf-file-or-directory>

Examples:
    python pdf_to_markdown.py report.pdf
    python pdf_to_markdown.py --model gemma4:12b --output ./md ./docs
    python pdf_to_markdown.py --mode vision scanned.pdf
    python pdf_to_markdown.py --columns 2 journal-article.pdf
    python pdf_to_markdown.py --columns 1 --mode text plain-report.pdf
    python pdf_to_markdown.py -r --output ./output ./pdf-folder
"""

import argparse
import base64
import json
import re
import sys
import threading
import time
from pathlib import Path

import io

import fitz  # PyMuPDF
import numpy as np
import requests
from PIL import Image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class CancelledError(Exception):
    """Raised when a conversion job is cancelled by the user."""


DEFAULT_MODEL     = "gemma4"
DEFAULT_OLLAMA    = "http://localhost:11434"
DEFAULT_LM_STUDIO = "http://localhost:1234"
PAGE_ZOOM      = 2.0      # render scale for vision mode (144 dpi effective)

# chars-per-page below this triggers automatic vision fallback
SCANNED_THRESHOLD = 100

# Networking defaults. Streaming responses use these as
# (connect_timeout, read_timeout). The read_timeout is the maximum gap
# *between* chunks from the server, not the total request time.
CONNECT_TIMEOUT = 30
READ_TIMEOUT    = 900   # 15 minutes of silence before giving up
MAX_RETRIES     = 2
KEEP_ALIVE      = "30m" # keep model loaded in VRAM between pages
# Ollama defaults to a 2048-token context which is far too small for vision
# pages and long text pages — a single image eats ~1500 tokens before any
# text is produced, and dense text pages can be 3-4k tokens on their own.
NUM_CTX         = 8192
NUM_PREDICT     = 4096

# Per-page text-coverage check. After converting a page, we compute what
# fraction of the source text's words appear in the model output; if the
# coverage is below COVERAGE_THRESHOLD the page is retried up to
# COVERAGE_RETRIES additional times. The check is only meaningful when the
# source page has enough selectable text (COVERAGE_MIN_WORDS), otherwise
# we skip the check (e.g. fully scanned pages).
COVERAGE_THRESHOLD = 0.95
COVERAGE_RETRIES   = 2
COVERAGE_MIN_WORDS = 20

# Per-page column-order check. For pages with multi-column layout we extract
# the expected reading order from PyMuPDF's text blocks (with bounding
# boxes) and verify that the model output preserves the same order of
# block snippets (each column top-to-bottom, left to right, with full-width
# bands in their vertical position). If the fraction of consecutive snippet
# pairs in the correct order falls below ORDER_THRESHOLD, the page is retried.
ORDER_THRESHOLD       = 0.85
ORDER_MIN_SNIPPETS    = 4

# Page-layout column count. "auto" infers the layout from ink/block geometry
# per page; an int (1-MAX_COLUMNS) asserts it for every page. Asserting the
# count is a strong accuracy assist: it lets the geometry code split the page
# deterministically instead of relying on detection heuristics that bail out
# to whole-page on tight gutters, and it lets the extracted text be
# pre-ordered before the model ever sees it.
COLUMNS_AUTO = "auto"
MAX_COLUMNS  = 3

# Gutter search parameters. Detection ("auto") is deliberately conservative:
# mis-splitting a single-column page chops every line mid-sentence, which is
# far worse than failing to split a multi-column one. When the caller asserts
# a column count we relax both thresholds, because the question is no longer
# "is there a gutter?" but "where is the best separator?".
GUTTER_MARGIN_FRAC    = 0.12   # never look for gutters inside page margins
GUTTER_RATIO_AUTO     = 0.20   # gutter density vs. page average
GUTTER_RATIO_FORCED   = 0.40
GUTTER_MIN_FRAC_AUTO  = 0.03   # gutter width as a fraction of page width
GUTTER_MIN_FRAC_FORCED = 0.015
GUTTER_FLANK_FRAC     = 0.15   # window either side used to confirm a gutter
GUTTER_SNAP_FRAC      = 0.18   # max drift from an evenly-spaced position

# Upper bound on vision-mode crops per page. A correct split yields roughly
# one region per column plus a few full-width bands. A much larger number
# means the band detector is flapping — usually because a declared column
# count doesn't match the page and a synthesized gutter is slicing through
# body text. Sending the whole page is both cheaper and more accurate than
# feeding the model dozens of shredded strips.
MAX_PAGE_REGIONS = 12


def normalize_columns(value) -> "int | str":
    """
    Coerce a user-supplied column setting to ``COLUMNS_AUTO`` or an int.

    Accepts None, "auto", "1", 2, etc. Anything unrecognized or out of the
    supported 1..MAX_COLUMNS range falls back to auto detection.
    """
    if value is None:
        return COLUMNS_AUTO
    if isinstance(value, str):
        value = value.strip().lower()
        if value in ("", COLUMNS_AUTO):
            return COLUMNS_AUTO
        if not value.isdigit():
            return COLUMNS_AUTO
        value = int(value)
    if isinstance(value, int) and 1 <= value <= MAX_COLUMNS:
        return value
    return COLUMNS_AUTO


def _is_forced(columns) -> bool:
    return isinstance(columns, int)


_COLUMN_WORD = {2: "two", 3: "three"}

SYSTEM_PROMPT_TEXT = """\
You are a document conversion assistant. Your sole task is to convert raw \
text extracted from a PDF into well-structured Markdown.

Rules:
- Preserve all headings, lists, tables, and code blocks from the source.
- {column_rule}
- Infer heading levels (# ## ###) from layout and formatting cues.
- Convert bullet/numbered lists to proper Markdown lists.
- Preserve ALL tables using Markdown pipe-table syntax. Every table must use \
| column | separators, a | --- | header-separator row, and include every \
data row from the original. Maintain column alignment as closely as possible. \
(Note: table columns are different from page-layout columns — tables are \
bounded grids of cells, not the page's overall multi-column reading flow.)
- Wrap code or technical content in fenced code blocks.
- Ignore any references to embedded images, photos, logos, or decorative art. \
Do not include image placeholders or descriptions for these.
- Remove page headers, footers, and page numbers that carry no real content.
- Output ONLY the converted Markdown — no commentary or summaries.\
"""

SYSTEM_PROMPT_HYBRID = """\
You are a document conversion assistant. You will be given BOTH a rendered \
image of one page from a PDF AND the raw text extracted from that same page. \
Your sole task is to combine these two inputs into well-structured Markdown.

How to use the two inputs:
- The EXTRACTED TEXT is the ground truth for actual words and characters \
(no OCR errors). Use it as the authoritative source for spelling, numbers, \
and content.
- The PAGE IMAGE is the ground truth for LAYOUT: reading order, columns, \
tables, sidebars/text boxes, headings, and any visual structure. Use it \
whenever the extracted text appears out of order, interleaved, or missing \
structural information.

Rules:
- {column_rule}
- If the page contains a sidebar or boxed callout, output it in its visual \
position in the flow, prefixed with "> " block-quote syntax so it is \
visually distinguished from the main text.
- Infer heading levels (# ## ###) from font size and visual prominence in \
the image.
- Convert bullet/numbered lists to proper Markdown lists.
- Preserve ALL tables using Markdown pipe-table syntax. Every table must \
use | column | separators, a | --- | header-separator row, and include \
every data row. Use the image to identify table boundaries; use the \
extracted text for cell contents. Table columns are different from page \
layout columns.
- Wrap code or technical content in fenced code blocks.
- If the page contains a chart, graph, or diagram, render it as ASCII art \
inside a fenced code block. Reproduce structure, labels, and data points.
- Completely ignore regular images such as photos, logos, icons, and \
decorative art. Do not include them, describe them, or add placeholders.
- Remove running headers, footers, and page numbers.
- Output ONLY the converted Markdown — no commentary or summaries.\
"""

SYSTEM_PROMPT_VISION = """\
You are a document conversion assistant. You will be given an image of one \
page from a PDF document. Your sole task is to convert everything visible on \
the page into well-structured Markdown.

Rules:
- Reproduce all text content exactly, preserving headings, lists, and paragraphs.
- {column_rule}
- Infer heading levels (# ## ###) from font size and visual prominence.
- Convert bullet/numbered lists to proper Markdown lists.
- Preserve ALL tables using Markdown pipe-table syntax. Every table must use \
| column | separators, a | --- | header-separator row, and include every \
data row from the original. Maintain column alignment as closely as possible. \
(Note: table columns are different from page-layout columns — tables are \
bounded grids of cells, not the page's overall multi-column reading flow.)
- Wrap code or technical content in fenced code blocks.
- If the page contains a chart, graph, or diagram, render it as ASCII art \
inside a fenced code block (```). Reproduce the structure, labels, axes, and \
data points as faithfully as possible using box-drawing characters, dashes, \
pipes, and text.
- Completely ignore regular images such as photos, logos, icons, and \
decorative art. Do not include them, describe them, or add any placeholder.
- Remove running headers, footers, and page numbers.
- Output ONLY the converted Markdown — no commentary or summaries.\
"""


def _column_rule(columns, kind: str, pre_ordered: bool = False) -> str:
    """
    Build the layout instruction injected into each system prompt.

    ``kind`` is "text", "vision", or "hybrid". ``pre_ordered`` marks that the
    extracted text handed to the model has already been sorted into reading
    order by the column code, in which case the model is told to leave the
    order alone rather than "fixing" it.
    """
    if columns == 1:
        if kind == "vision":
            return (
                "This page is SINGLE-COLUMN. Read straight down the page in "
                "one continuous top-to-bottom flow. Do NOT treat the page as "
                "having multiple columns and do NOT reorder content — there "
                "are no columns to interleave, even if some lines are short "
                "or the right margin is wide."
            )
        base = (
            "This page is SINGLE-COLUMN. The extracted text is already in "
            "correct reading order. Preserve that order exactly as one "
            "continuous top-to-bottom flow; do NOT split the content into "
            "columns and do NOT move paragraphs around."
        )
        if kind == "hybrid":
            base += (
                " Use the image only to recover structure (headings, tables, "
                "callouts), never to re-sequence the text."
            )
        return base

    if _is_forced(columns):
        word = _COLUMN_WORD.get(columns, str(columns))
        if kind == "vision":
            return (
                f"IMPORTANT: This page is laid out in {word} ({columns}) "
                f"columns. Read EACH column fully from top to bottom before "
                f"moving to the next, in left-to-right order. Do NOT skip a "
                f"column. Do NOT interleave lines across columns. After "
                f"finishing column 1, continue at the TOP of column 2, and so "
                f"on through column {columns}. Full-width elements (headings, "
                f"tables or figures spanning the whole page) belong at their "
                f"own vertical position in the flow. The output must be a "
                f"single continuous stream of text in correct reading order."
            )
        if pre_ordered:
            return (
                f"This page is laid out in {word} ({columns}) columns, and "
                f"the extracted text below has ALREADY been re-sequenced into "
                f"correct reading order (column 1 top-to-bottom, then column "
                f"2, and so on, with full-width elements in place). Do NOT "
                f"reorder it again. Your only job is to merge sentences and "
                f"paragraphs broken across column boundaries and format the "
                f"result as Markdown."
            )
        return (
            f"IMPORTANT: This page is laid out in {word} ({columns}) columns, "
            f"so the extracted text may have lines from different columns "
            f"interleaved or out of natural reading order. Reconstruct the "
            f"correct order: each column flows top-to-bottom, and columns run "
            f"left-to-right. Merge sentences and paragraphs broken across "
            f"column boundaries. Never output line-by-line interleavings of "
            f"different columns."
        )

    # auto — the original hedged wording, since we genuinely don't know.
    if kind == "vision":
        return (
            "IMPORTANT: If the page is laid out in multiple columns, read "
            "EACH column fully from top to bottom before moving to the next "
            "column, in left-to-right order. Do NOT skip columns. Do NOT "
            "interleave lines across columns. After finishing the left column "
            "of a two-column page, continue with the right column starting "
            "from its top. The final Markdown output should be a single "
            "continuous flow of text in correct reading order."
        )
    if kind == "hybrid":
        return (
            "The extracted text may have lines from different columns "
            "interleaved or appearing in the wrong order. Use the image to "
            "reconstruct correct reading order: each column flows "
            "top-to-bottom, columns appear left-to-right, and full-width "
            "elements (headings, tables spanning columns, text boxes, figures "
            "with captions) appear at their correct vertical position in the "
            "flow."
        )
    if pre_ordered:
        return (
            "The extracted text below has ALREADY been re-sequenced into "
            "correct multi-column reading order. Do NOT reorder it again — "
            "only merge sentences and paragraphs broken across column "
            "boundaries and format the result as Markdown."
        )
    return (
        "IMPORTANT: The source PDF may be laid out in multiple columns. The "
        "extracted text may therefore have lines from different columns "
        "interleaved or appearing out of natural reading order. Reconstruct "
        "the correct reading order: each column should flow top-to-bottom, "
        "and columns should appear left-to-right. Merge broken sentences and "
        "paragraphs that span column boundaries. Do not output line-by-line "
        "interleavings of different columns."
    )


REGION_NOTE = (
    "\n\nNOTE: This image is ONE region cropped from a larger multi-column "
    "page, already isolated in correct reading order. Convert only what is "
    "visible in this image. Do not add headings, transitions, or commentary "
    "about surrounding content, and do not note that the region is partial."
)


def build_system_prompt(kind: str, columns, pre_ordered: bool = False,
                        is_region: bool = False) -> str:
    """Render the system prompt for ``kind`` with the right layout rule."""
    template = {
        "text":   SYSTEM_PROMPT_TEXT,
        "vision": SYSTEM_PROMPT_VISION,
        "hybrid": SYSTEM_PROMPT_HYBRID,
    }[kind]
    # A cropped region is a single column by construction, whatever the page is.
    effective = 1 if is_region else columns
    prompt = template.format(
        column_rule=_column_rule(effective, kind, pre_ordered=pre_ordered)
    )
    if is_region:
        prompt += REGION_NOTE
    return prompt


# ---------------------------------------------------------------------------
# Column geometry
# ---------------------------------------------------------------------------

def _find_gutters_1d(profile: np.ndarray, avg: float, columns,
                     min_width: int) -> list[tuple[int, int]]:
    """
    Locate vertical column gutters in a 1-D horizontal density profile.

    ``profile`` holds a per-x measure of how much content occupies that
    horizontal position (ink fraction for rendered pages, height-weighted
    block coverage for text blocks). ``avg`` is the page-wide average used
    to scale thresholds. ``min_width`` is the narrowest run of low density
    that may count as a gutter, in profile units.

    Returns ``(lo, hi)`` index pairs ordered left-to-right. An empty list
    means single-column. When ``columns`` is an int the result always has
    exactly ``columns - 1`` entries: if the profile doesn't show clean
    separators the missing ones are synthesized at evenly-spaced positions,
    because the caller has asserted the layout and an even split beats
    silently giving up.
    """
    forced = _is_forced(columns)
    if forced and columns == 1:
        return []

    n = len(profile)
    if n == 0 or avg <= 0:
        return []

    search_lo = int(n * GUTTER_MARGIN_FRAC)
    search_hi = n - int(n * GUTTER_MARGIN_FRAC)
    if search_hi - search_lo < 2:
        return []

    ratio = GUTTER_RATIO_FORCED if forced else GUTTER_RATIO_AUTO
    threshold = avg * ratio

    # Contiguous runs of below-threshold density inside the search window.
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for i in range(search_lo, search_hi):
        if profile[i] <= threshold:
            if run_start is None:
                run_start = i
        elif run_start is not None:
            runs.append((run_start, i))
            run_start = None
    if run_start is not None:
        runs.append((run_start, search_hi))

    runs = [r for r in runs if (r[1] - r[0]) >= min_width]

    def flanks_ok(lo: int, hi: int) -> bool:
        """True when both sides of the run carry substantially more content."""
        span = max(1, int(n * GUTTER_FLANK_FRAC))
        left  = profile[max(0, lo - span):lo]
        right = profile[hi:min(n, hi + span)]
        if left.size == 0 or right.size == 0:
            return False
        return left.mean() >= avg * 0.5 and right.mean() >= avg * 0.5

    if forced:
        # Snap each expected separator to the nearest real gutter run, and
        # fall back to the geometric position when there isn't one nearby.
        snap = n * GUTTER_SNAP_FRAC
        used: set[int] = set()
        chosen: list[tuple[int, int]] = []
        for k in range(1, columns):
            expected = n * k / columns
            best_i, best_d = None, snap
            for i, (lo, hi) in enumerate(runs):
                if i in used:
                    continue
                d = abs((lo + hi) / 2.0 - expected)
                if d < best_d:
                    best_i, best_d = i, d
            if best_i is None:
                pos = int(expected)
                chosen.append((pos, pos))
            else:
                used.add(best_i)
                chosen.append(runs[best_i])
        chosen.sort()
        return chosen

    # auto — accept only runs that are confirmed by content on both sides,
    # keep the widest few, and require real content between them.
    confirmed = [r for r in runs if flanks_ok(*r)]
    confirmed.sort(key=lambda r: r[1] - r[0], reverse=True)
    confirmed = confirmed[: MAX_COLUMNS - 1]
    confirmed.sort()

    min_col_width = int(n * 0.15)
    spaced: list[tuple[int, int]] = []
    for lo, hi in confirmed:
        if spaced and lo - spaced[-1][1] < min_col_width:
            continue
        spaced.append((lo, hi))
    return spaced


def _gutter_mids(gutters: list[tuple[int, int]]) -> list[float]:
    return [(lo + hi) / 2.0 for lo, hi in gutters]


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_pdf(path: Path, columns=COLUMNS_AUTO) -> tuple[dict, list[dict]]:
    """
    Return (metadata, pages) where each page dict has:
        {"number", "text", "ordered_text", "ordered_snippets", "columns"}

    ``ordered_text`` is the page text re-sequenced into column reading order.
    It is empty for pages detected (or declared) as single-column, where the
    raw extraction order is already correct and re-flowing blocks would only
    risk disturbing tables.
    """
    doc = fitz.open(path)

    meta = {
        "title":      doc.metadata.get("title",   "").strip(),
        "author":     doc.metadata.get("author",  "").strip(),
        "subject":    doc.metadata.get("subject", "").strip(),
        "page_count": doc.page_count,
    }

    pages = []
    for page in doc:
        text = page.get_text("text", sort=True)
        blocks, n_gutters = _ordered_text_blocks(page, columns)
        snippets = _snippets_from_blocks(blocks)
        ordered_text = ""
        if n_gutters:
            ordered_text = "\n\n".join(b[4].strip() for b in blocks if b[4].strip())
        pages.append({
            "number":           page.number + 1,
            "text":             text,
            "ordered_text":     ordered_text,
            "ordered_snippets": snippets,
            "columns":          n_gutters + 1,
        })

    doc.close()
    return meta, pages


def render_page_b64(path: Path, page_number: int) -> str:
    """Render a single PDF page (1-based) to a base64-encoded PNG string."""
    doc  = fitz.open(path)
    page = doc[page_number - 1]
    mat  = fitz.Matrix(PAGE_ZOOM, PAGE_ZOOM)
    pix  = page.get_pixmap(matrix=mat, alpha=False)
    png  = pix.tobytes("png")
    doc.close()
    return base64.b64encode(png).decode("ascii")


def _image_to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _detect_reading_order_crops(img: Image.Image,
                                columns=COLUMNS_AUTO) -> list[Image.Image]:
    """
    Analyze a rendered page image and return crops in correct reading order.

    Detects whether each horizontal band of the page is laid out in columns
    or as full-width content. Returns:
      - One crop per full-width band (heading, table spanning columns, etc.)
      - One crop per column, left-to-right, per multi-column band

    ``columns`` may be ``COLUMNS_AUTO`` (infer per page) or an int asserting
    the layout. With ``columns=1`` the page is never split. With an asserted
    count >= 2 the split is forced even when the ink profile is ambiguous,
    since cropping is what actually prevents the model from interleaving
    columns. In auto mode the detector stays conservative and falls back to
    returning ``[img]`` — better to send the whole page than to mis-split it.
    """
    if columns == 1:
        return [img]

    gray = np.asarray(img.convert("L"))
    h, w = gray.shape

    # Binary "ink" mask: dark pixels are text.
    ink = gray < 200
    page_density = ink.mean()
    if page_density < 0.005:
        return [img]  # essentially blank

    # 1. Compute per-column ink density, restricted to the page body
    #    (drop top 15% / bottom 10% so centered headers, page numbers, and
    #    full-width tables don't pollute the gutter search). Also exclude
    #    rows that are mostly-full-of-ink (horizontal rules, table borders,
    #    full-width banners) which would otherwise add ink to gutter columns.
    v_top = int(h * 0.15)
    v_bot = int(h * 0.90)
    body = ink[v_top:v_bot, :]
    if body.size:
        row_fill = body.mean(axis=1)
        # Keep only rows with < 30% horizontal ink fill (excludes rules/banners)
        col_density = body[row_fill < 0.30].mean(axis=0) if (row_fill < 0.30).any() else body.mean(axis=0)
    else:
        col_density = ink.mean(axis=0)

    # 2. Locate the gutter(s) separating the columns. Thresholds are scaled by
    #    the mean of col_density itself, not by page_density: col_density is
    #    averaged over a filtered subset of rows, so the two are on different
    #    scales and mixing them makes sparse pages fail detection outright.
    forced = _is_forced(columns)
    min_frac = GUTTER_MIN_FRAC_FORCED if forced else GUTTER_MIN_FRAC_AUTO
    min_width = max(8 if forced else 30, int(w * min_frac))
    profile_avg = float(col_density.mean())
    gutters = _find_gutters_1d(col_density, profile_avg, columns, min_width)
    if not gutters:
        return [img]

    mids = [int(m) for m in _gutter_mids(gutters)]

    # 3. For each row, classify as multi-column or full-width based on whether
    #    any gutter carries ink in that row. Sample only the middle of each
    #    gutter: its outer edges bleed into the ragged line ends of the
    #    neighbouring column, which would otherwise make the classification
    #    flap row to row and shatter the page into dozens of bands. Zero-width
    #    synthesized gutters (forced layouts with no visible separator) are
    #    sampled over a small fixed window around the split position.
    gutter_ink = np.zeros(h)
    for (lo, hi), mid in zip(gutters, mids):
        half = max(2, (hi - lo) // 4)
        lo, hi = max(0, mid - half), min(w, mid + half + 1)
        strip = ink[:, lo:hi]
        if strip.size:
            gutter_ink = np.maximum(gutter_ink, strip.mean(axis=1))
    row_is_full_width = gutter_ink >= 0.05
    row_total_ink = ink.mean(axis=1)
    row_is_blank = row_total_ink < 0.005

    # 5. Group consecutive rows into bands.
    bands: list[list] = []  # [y0, y1, is_full_width]
    cur_y0 = 0
    cur_fw = bool(row_is_full_width[0])
    for y in range(1, h):
        if row_is_blank[y]:
            continue  # blank rows are neutral
        fw = bool(row_is_full_width[y])
        if fw != cur_fw:
            bands.append([cur_y0, y, cur_fw])
            cur_y0 = y
            cur_fw = fw
    bands.append([cur_y0, h, cur_fw])

    # 6. Merge bands thinner than 4% of page height into neighbors (denoise).
    min_band_h = max(30, int(h * 0.04))
    merged: list[list] = []
    for band in bands:
        if merged and (band[1] - band[0]) < min_band_h:
            merged[-1][1] = band[1]
        else:
            merged.append(band)

    # If the page ended up as one big full-width band, return it whole.
    if len(merged) == 1 and merged[0][2]:
        return [img]

    # 7. Build ordered crops. Column boundaries are the gutter midpoints, so
    #    each column band yields len(mids) + 1 crops in left-to-right order.
    #    Blank crops are dropped — a short band often has content in only one
    #    column, and sending an empty image is a wasted model call.
    bounds = [0, *mids, w]
    crops: list[Image.Image] = []
    for y0, y1, is_fw in merged:
        if is_fw:
            regions = [(0, w)]
        else:
            regions = list(zip(bounds, bounds[1:]))
        for x0, x1 in regions:
            if x1 - x0 < 8 or ink[y0:y1, x0:x1].mean() < 0.002:
                continue
            crops.append(img.crop((x0, y0, x1, y1)))

    if len(crops) > MAX_PAGE_REGIONS:
        print(f"    layout split produced {len(crops)} regions (limit "
              f"{MAX_PAGE_REGIONS}) — the declared column count likely does "
              f"not match this page; sending it whole instead")
        return [img]
    return crops or [img]


def render_page_reading_order_b64(path: Path, page_number: int,
                                  columns=COLUMNS_AUTO) -> list[str]:
    """
    Render a PDF page and split it into base64 PNG crops in reading order.

    Returns one or more base64 strings. Multi-element results indicate that
    the page contains multi-column body text and/or mixed full-width bands;
    each entry should be sent to the vision model separately and the results
    concatenated in order.
    """
    doc  = fitz.open(path)
    page = doc[page_number - 1]
    mat  = fitz.Matrix(PAGE_ZOOM, PAGE_ZOOM)
    pix  = page.get_pixmap(matrix=mat, alpha=False)
    png  = pix.tobytes("png")
    doc.close()

    img = Image.open(io.BytesIO(png)).convert("RGB")
    crops = _detect_reading_order_crops(img, columns)
    return [_image_to_b64(c) for c in crops]


def is_scanned(pages: list[dict]) -> bool:
    """Return True if average extractable chars per page is below the threshold."""
    if not pages:
        return False
    avg = sum(len(p["text"].strip()) for p in pages) / len(pages)
    return avg < SCANNED_THRESHOLD


_WORD_RE = re.compile(r"[a-z0-9]{3,}")


def _word_set(text: str) -> set[str]:
    """Return the set of lowercased alphanumeric words of 3+ chars in text."""
    return set(_WORD_RE.findall(text.lower()))


def text_coverage(source: str, output: str) -> float:
    """
    Fraction of unique source words that also appear in the output.

    Returns 1.0 when the source has no usable words (so the check is a
    no-op for blank/image-only pages).
    """
    src = _word_set(source)
    if not src:
        return 1.0
    out = _word_set(output)
    return len(src & out) / len(src)


def _ordered_text_blocks(page, columns=COLUMNS_AUTO) -> tuple[list, int]:
    """
    Return ``(blocks, gutter_count)`` for one page, in reading order.

    Each block is ``(x0, y0, x1, y1, text)``. For multi-column layouts the
    order is:
      - any full-width band at the top (heading, banner)
      - column 1 blocks top-to-bottom, then column 2, ... then column N
      - then the next full-width band (if any), and so on.
    For single-column pages, blocks are sorted by y then x.

    ``columns`` may be ``COLUMNS_AUTO`` or an int asserting the layout; an
    asserted count is snapped to the nearest real gutter and falls back to
    an even split, so the ordering holds even on pages whose block geometry
    is too noisy for detection.

    ``gutter_count`` is 0 for single-column pages and is what callers use to
    decide whether re-sequenced text is worth using at all.
    """
    blocks = page.get_text("blocks")
    txt_blocks: list[tuple[float, float, float, float, str]] = []
    for b in blocks:
        if len(b) < 7:
            continue
        x0, y0, x1, y1, text, _bno, btype = b[:7]
        text = (text or "").strip()
        if btype != 0 or not text:
            continue
        txt_blocks.append((x0, y0, x1, y1, text))

    if not txt_blocks:
        return [], 0

    page_w = float(page.rect.width) or 1.0

    if columns == 1:
        return sorted(txt_blocks, key=lambda b: (b[1], b[0])), 0

    # Histogram of horizontal block coverage (weighted by block height) to
    # locate the vertical empty strips (column gutters) across the page.
    # 200 bins ≈ 3pt per bin on Letter: coarser binning smears a heading that
    # ends near the gutter into the gutter itself and hides it entirely.
    bins = 200
    cov = np.zeros(bins)
    for x0, y0, x1, y1, _ in txt_blocks:
        i0 = max(0, int(x0 / page_w * bins))
        i1 = min(bins, int(x1 / page_w * bins) + 1)
        cov[i0:i1] += max(1.0, (y1 - y0))

    min_width = max(2, int(bins * 0.01)) if _is_forced(columns) \
        else max(4, int(bins * 0.02))
    gutters = _find_gutters_1d(cov, float(cov.mean()), columns, min_width)

    if not gutters:
        return sorted(txt_blocks, key=lambda b: (b[1], b[0])), 0

    mids = [m / bins * page_w for m in _gutter_mids(gutters)]
    gutter_pad  = page_w * 0.02
    sorted_by_y = sorted(txt_blocks, key=lambda b: b[1])

    # Group blocks vertically: full-width blocks (those whose x-range crosses
    # any gutter) interrupt the column flow; everything between two
    # consecutive full-width blocks is sorted column by column.
    bands: list[tuple[str, list]] = []
    current_cols: list = []
    for b in sorted_by_y:
        x0, _, x1, _, _ = b
        crosses = any(x0 < m - gutter_pad and x1 > m + gutter_pad for m in mids)
        if crosses:
            if current_cols:
                bands.append(("cols", current_cols))
                current_cols = []
            bands.append(("full", [b]))
        else:
            current_cols.append(b)
    if current_cols:
        bands.append(("cols", current_cols))

    ordered_blocks: list = []
    for kind, items in bands:
        if kind == "full":
            ordered_blocks.extend(items)
            continue
        buckets: list[list] = [[] for _ in range(len(mids) + 1)]
        for b in items:
            cx = (b[0] + b[2]) / 2.0
            buckets[sum(1 for m in mids if cx >= m)].append(b)
        for bucket in buckets:
            bucket.sort(key=lambda b: b[1])
            ordered_blocks.extend(bucket)

    return ordered_blocks, len(mids)


def _snippets_from_blocks(ordered_blocks: list, snippet_words: int = 6) -> list[str]:
    """
    Reduce ordered blocks to distinctive snippets for order verification.

    Each snippet is the first ``snippet_words`` alphanumeric words from the
    block, lowercased and space-joined. Blocks with fewer than 3 usable words
    are skipped (too generic to verify ordering).
    """
    snippets: list[str] = []
    for block in ordered_blocks:
        words = _WORD_RE.findall(block[4].lower())
        if len(words) < 3:
            continue
        snippets.append(" ".join(words[:snippet_words]))
    return snippets


def column_order_score(snippets: list[str], output: str) -> tuple[float, int, int]:
    """
    Verify that ``snippets`` appear in the same order in ``output``.

    Returns (score, correct_pairs, total_pairs) where score is the
    fraction of consecutive snippet pairs whose positions in the output
    are monotonically increasing. Snippets not found in the output are
    skipped (they don't count toward correct or incorrect ordering).

    Returns (1.0, 0, 0) when fewer than 2 snippets are available — too
    little information to judge ordering.
    """
    if len(snippets) < 2:
        return 1.0, 0, 0

    # Build a normalized word stream from the output so snippet lookup
    # is robust to Markdown punctuation, bullet characters, etc.
    out_words = _WORD_RE.findall(output.lower())
    if not out_words:
        return 0.0, 0, len(snippets) - 1
    out_stream = " " + " ".join(out_words) + " "

    positions: list[int] = []
    for snip in snippets:
        idx = out_stream.find(" " + snip + " ")
        if idx < 0:
            # Try a shorter prefix (first 3 words) as a fallback.
            short = " ".join(snip.split()[:3])
            if short:
                idx = out_stream.find(" " + short + " ")
        positions.append(idx)

    correct = total = 0
    last_valid = -1
    for pos in positions:
        if pos < 0:
            continue
        if last_valid >= 0:
            total += 1
            if pos > last_valid:
                correct += 1
        last_valid = pos

    if total == 0:
        return 1.0, 0, 0
    return correct / total, correct, total


# ---------------------------------------------------------------------------
# LM Studio detection
# ---------------------------------------------------------------------------

def detect_lm_studio_models(url: str = DEFAULT_LM_STUDIO,
                            timeout: float = 2.0) -> list[str]:
    """
    Query LM Studio's native REST API for loaded models.

    Returns a list of model IDs currently loaded in memory (state == "loaded").
    Returns an empty list if LM Studio is not running, not reachable, or has
    no loaded models. Never raises — caller can treat empty list as "use
    Ollama instead".
    """
    try:
        r = requests.get(f"{url.rstrip('/')}/api/v0/models", timeout=timeout)
        if r.status_code != 200:
            return []
        data = r.json().get("data", [])
        loaded: list[str] = []
        for item in data:
            mid = item.get("id")
            state = item.get("state", "")
            if mid and state == "loaded":
                loaded.append(mid)
        return loaded
    except Exception:
        return []


_LM_STUDIO_URL_CACHE: dict[str, bool] = {}


def is_lm_studio_url(url: str) -> bool:
    """
    Heuristic: does this URL point to an LM Studio server?

    Checks for the LM Studio-specific /api/v0/models endpoint (Ollama does
    not expose this path). Cached per URL to avoid repeated probes.
    """
    cached = _LM_STUDIO_URL_CACHE.get(url)
    if cached is not None:
        return cached
    try:
        r = requests.get(f"{url.rstrip('/')}/api/v0/models", timeout=2.0)
        result = r.status_code == 200
    except Exception:
        result = False
    _LM_STUDIO_URL_CACHE[url] = result
    return result


# ---------------------------------------------------------------------------
# Ollama API
# ---------------------------------------------------------------------------

def _post_streaming(
    ollama_url:   str,
    payload:      dict,
    read_timeout: float,
    cancel_event: threading.Event | None = None,
    num_ctx:      int = NUM_CTX,
) -> str:
    """
    Stream a response from Ollama's /api/generate endpoint.

    The HTTP read timeout is reset every time a chunk arrives, so long
    generations succeed as long as tokens keep flowing.
    """
    payload = {
        **payload,
        "stream": True,
        "keep_alive": KEEP_ALIVE,
        # Disable "thinking" output for models that support it (e.g. gemma4,
        # qwen3-thinking). Otherwise the model can spend its entire
        # num_predict budget on chain-of-thought tokens (which Ollama emits
        # in a separate `thinking` field) and return an empty `response`.
        "think": False,
        "options": {
            **payload.get("options", {}),
            "num_ctx":     num_ctx,
            "num_predict": NUM_PREDICT,
        },
    }

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            with requests.post(
                f"{ollama_url.rstrip('/')}/api/generate",
                json=payload,
                stream=True,
                timeout=(CONNECT_TIMEOUT, read_timeout),
            ) as resp:
                if resp.status_code != 200:
                    try:
                        body = resp.json().get("error", resp.text)
                    except Exception:
                        body = resp.text
                    raise RuntimeError(f"Ollama error {resp.status_code}: {body}")

                pieces: list[str] = []
                chunk_count = 0
                done_reason: str | None = None
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if cancel_event is not None and cancel_event.is_set():
                        raise CancelledError("Conversion cancelled by user.")
                    if not raw_line:
                        continue
                    try:
                        obj = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    if "error" in obj:
                        raise RuntimeError(f"Ollama error: {obj['error']}")

                    piece = obj.get("response", "")
                    if piece:
                        pieces.append(piece)
                        chunk_count += 1
                        if chunk_count % 20 == 0:
                            print(".", end="", flush=True)

                    if obj.get("done"):
                        done_reason = obj.get("done_reason")
                        break

                result = "".join(pieces)
                if not result.strip():
                    reason = f" (done_reason={done_reason})" if done_reason else ""
                    raise RuntimeError(
                        f"Ollama returned an empty response{reason}. "
                        "The model may not actually support images, may have "
                        "run out of VRAM, or may have failed to load. "
                        "Check `ollama list` / `ollama ps` and try a known "
                        "multimodal model (e.g. llava, llama3.2-vision, "
                        "gemma3:4b)."
                    )
                return result

        except CancelledError:
            raise
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_err = exc
            if attempt <= MAX_RETRIES:
                wait = 2 ** attempt
                print(f"\n  network issue ({exc.__class__.__name__}); "
                      f"retry {attempt}/{MAX_RETRIES} in {wait}s...",
                      flush=True)
                time.sleep(wait)
                continue
            if isinstance(exc, requests.ConnectionError):
                raise RuntimeError(
                    f"Cannot connect to Ollama at {ollama_url}. "
                    "Is Ollama running? Try: ollama serve"
                ) from exc
            raise RuntimeError(
                f"Ollama request timed out after {read_timeout}s of no data. "
                "The model may still be loading; try again, raise --timeout, "
                "or use a smaller model."
            ) from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

    raise RuntimeError(f"Ollama request failed: {last_err}")


# ---------------------------------------------------------------------------
# LM Studio / OpenAI-compatible API
# ---------------------------------------------------------------------------

def _post_streaming_openai(
    base_url:     str,
    model:        str,
    system_prompt:str,
    user_prompt:  str,
    b64_image:    str | None,
    read_timeout: float,
    cancel_event: threading.Event | None = None,
    num_ctx:      int = NUM_CTX,
) -> str:
    """
    Stream a response from an OpenAI-compatible /v1/chat/completions endpoint
    (used by LM Studio). Vision messages are sent using the OpenAI multi-part
    content format with a data: URL.
    """
    user_content: list[dict] = [{"type": "text", "text": user_prompt}]
    if b64_image is not None:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64_image}"},
        })

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        "stream":     True,
        "max_tokens": NUM_PREDICT,
    }

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            with requests.post(
                f"{base_url.rstrip('/')}/v1/chat/completions",
                json=payload,
                stream=True,
                timeout=(CONNECT_TIMEOUT, read_timeout),
            ) as resp:
                if resp.status_code != 200:
                    try:
                        body = resp.json().get("error", resp.text)
                    except Exception:
                        body = resp.text
                    raise RuntimeError(f"LM Studio error {resp.status_code}: {body}")

                pieces: list[str] = []
                chunk_count = 0
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if cancel_event is not None and cancel_event.is_set():
                        raise CancelledError("Conversion cancelled by user.")
                    if not raw_line:
                        continue
                    # OpenAI SSE: lines look like "data: {json}" or "data: [DONE]".
                    if raw_line.startswith("data: "):
                        raw_line = raw_line[6:]
                    if raw_line.strip() == "[DONE]":
                        break
                    try:
                        obj = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    if "error" in obj:
                        err = obj["error"]
                        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                        raise RuntimeError(f"LM Studio error: {msg}")

                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content", "")
                    if piece:
                        pieces.append(piece)
                        chunk_count += 1
                        if chunk_count % 20 == 0:
                            print(".", end="", flush=True)

                    if choices[0].get("finish_reason"):
                        break

                result = "".join(pieces)
                if not result.strip():
                    raise RuntimeError(
                        "LM Studio returned an empty response. The loaded "
                        "model may not support images, or may have hit its "
                        "context limit. Try a multimodal model in LM Studio."
                    )
                return result

        except CancelledError:
            raise
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_err = exc
            if attempt <= MAX_RETRIES:
                wait = 2 ** attempt
                print(f"\n  network issue ({exc.__class__.__name__}); "
                      f"retry {attempt}/{MAX_RETRIES} in {wait}s...",
                      flush=True)
                time.sleep(wait)
                continue
            if isinstance(exc, requests.ConnectionError):
                raise RuntimeError(
                    f"Cannot connect to LM Studio at {base_url}. "
                    "Make sure LM Studio is running and the local server is started."
                ) from exc
            raise RuntimeError(
                f"LM Studio request timed out after {read_timeout}s of no data."
            ) from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"LM Studio request failed: {exc}") from exc

    raise RuntimeError(f"LM Studio request failed: {last_err}")


# ---------------------------------------------------------------------------
# Per-mode conversion entry points (dispatch to Ollama or LM Studio)
# ---------------------------------------------------------------------------

def convert_text_page(
    text:         str,
    label:        str,
    model:        str,
    ollama_url:   str,
    read_timeout: float = READ_TIMEOUT,
    cancel_event: threading.Event | None = None,
    num_ctx:      int = NUM_CTX,
    columns=COLUMNS_AUTO,
    pre_ordered:  bool = False,
) -> str:
<<<<<<< Updated upstream
    lead = (
        "The following text has already been re-sequenced into correct "
        "reading order. Convert it to Markdown without reordering it:"
        if pre_ordered else
        "Convert the following PDF page text to Markdown:"
    )
    payload = {
        "model":  model,
        "system": build_system_prompt("text", columns, pre_ordered=pre_ordered),
        "prompt": f"{lead}\n\n{text}",
    }
=======
>>>>>>> Stashed changes
    print(f"  {label} (text) ... ", end="", flush=True)
    t0 = time.time()
    if is_lm_studio_url(ollama_url):
        result = _post_streaming_openai(
            ollama_url, model, SYSTEM_PROMPT_TEXT,
            f"Convert the following PDF page text to Markdown:\n\n{text}",
            None, read_timeout, cancel_event, num_ctx=num_ctx,
        )
    else:
        payload = {
            "model":  model,
            "system": SYSTEM_PROMPT_TEXT,
            "prompt": f"Convert the following PDF page text to Markdown:\n\n{text}",
        }
        result = _post_streaming(ollama_url, payload, read_timeout, cancel_event,
                                 num_ctx=num_ctx)
    print(f" done ({time.time() - t0:.1f}s)")
    return result


def convert_page_vision(
    b64_image:    str,
    label:        str,
    model:        str,
    ollama_url:   str,
    read_timeout: float = READ_TIMEOUT,
    cancel_event: threading.Event | None = None,
    num_ctx:      int = NUM_CTX,
    columns=COLUMNS_AUTO,
    is_region:    bool = False,
) -> str:
<<<<<<< Updated upstream
    """
    Send a page image to the model (requires a multimodal model).

    ``is_region`` marks the image as one crop of a split multi-column page.
    Such a crop is single-column by construction, so it gets the
    single-column prompt regardless of the page's real column count.
    """
    payload = {
        "model":  model,
        "system": build_system_prompt("vision", columns, is_region=is_region),
        "prompt": ("Convert this page region to Markdown."
                   if is_region else "Convert this PDF page to Markdown."),
        "images": [b64_image],
    }
=======
    """Send a page image to the model (requires a multimodal model)."""
>>>>>>> Stashed changes
    print(f"  {label} (vision) ... ", end="", flush=True)
    t0 = time.time()
    if is_lm_studio_url(ollama_url):
        result = _post_streaming_openai(
            ollama_url, model, SYSTEM_PROMPT_VISION,
            "Convert this PDF page to Markdown.",
            b64_image, read_timeout, cancel_event, num_ctx=num_ctx,
        )
    else:
        payload = {
            "model":  model,
            "system": SYSTEM_PROMPT_VISION,
            "prompt": "Convert this PDF page to Markdown.",
            "images": [b64_image],
        }
        result = _post_streaming(ollama_url, payload, read_timeout, cancel_event,
                                 num_ctx=num_ctx)
    print(f" done ({time.time() - t0:.1f}s)")
    return result


def convert_page_hybrid(
    b64_image:    str,
    extracted_text: str,
    label:        str,
    model:        str,
    ollama_url:   str,
    read_timeout: float = READ_TIMEOUT,
    cancel_event: threading.Event | None = None,
    num_ctx:      int = NUM_CTX,
    columns=COLUMNS_AUTO,
    pre_ordered:  bool = False,
) -> str:
    """
    Send BOTH a page image and PyMuPDF-extracted text to the vision model.

    Image provides layout (columns, tables, text boxes); extracted text
    provides accurate content (no OCR errors). When ``pre_ordered`` is set
    the text has already been sorted into column reading order, so the model
    is told to use the image for structure only, not for re-sequencing.
    """
    if pre_ordered:
        intro = (
            "Below is the text extracted from this PDF page, ALREADY "
            "re-sequenced into correct column reading order. The attached "
            "image shows the visual layout of the same page. Use the text "
            "for content and order; use the image only for structure "
            "(headings, tables, callouts)."
        )
    else:
        intro = (
            "Below is the raw text extracted from this PDF page. The reading "
            "order may be incorrect for multi-column layouts. The attached "
            "image shows the actual visual layout of the same page. Use the "
            "text for accurate content and the image for layout/order/tables."
        )
    prompt = (
        f"{intro}\n\n"
        "EXTRACTED TEXT:\n"
        "```\n"
        f"{extracted_text.strip()}\n"
        "```\n"
    )
<<<<<<< Updated upstream
    payload = {
        "model":  model,
        "system": build_system_prompt("hybrid", columns, pre_ordered=pre_ordered),
        "prompt": prompt,
        "images": [b64_image],
    }
=======
>>>>>>> Stashed changes
    print(f"  {label} (hybrid) ... ", end="", flush=True)
    t0 = time.time()
    if is_lm_studio_url(ollama_url):
        result = _post_streaming_openai(
            ollama_url, model, SYSTEM_PROMPT_HYBRID, prompt,
            b64_image, read_timeout, cancel_event, num_ctx=num_ctx,
        )
    else:
        payload = {
            "model":  model,
            "system": SYSTEM_PROMPT_HYBRID,
            "prompt": prompt,
            "images": [b64_image],
        }
        result = _post_streaming(ollama_url, payload, read_timeout, cancel_event,
                                 num_ctx=num_ctx)
    print(f" done ({time.time() - t0:.1f}s)")
    return result

# ---------------------------------------------------------------------------
# Conversion orchestration
# ---------------------------------------------------------------------------

def build_front_matter(pdf_path: Path, meta: dict) -> str:
    lines = []
    if meta["title"]:   lines.append(f"title:   {meta['title']}")
    if meta["author"]:  lines.append(f"author:  {meta['author']}")
    if meta["subject"]: lines.append(f"subject: {meta['subject']}")
    lines.append(f"source:  {pdf_path.name}")
    lines.append(f"pages:   {meta['page_count']}")
    return "---\n" + "\n".join(lines) + "\n---\n"


def convert_pdf(
    pdf_path:     Path,
    output_path:  Path,
    model:        str,
    ollama_url:   str,
    mode:         str,   # "auto" | "text" | "vision" | "hybrid"
    start_page:   int = 1,
    cancel_event: threading.Event | None = None,
    read_timeout: float = READ_TIMEOUT,
    num_ctx:      int = NUM_CTX,
    columns=COLUMNS_AUTO,
) -> None:
    columns = normalize_columns(columns)
    meta, pages = extract_pdf(pdf_path, columns)
    total_chars = sum(len(p["text"].strip()) for p in pages)
    print(f"  {meta['page_count']} page(s), {total_chars:,} extractable chars")

    use_hybrid = mode == "hybrid"
    use_vision = mode == "vision" or (mode == "auto" and is_scanned(pages))
    if use_hybrid:
        effective_mode = "hybrid"
    else:
        effective_mode = "vision" if use_vision else "text"
    print(f"  Mode: {effective_mode}")
    if _is_forced(columns):
        print(f"  Layout: {columns} column(s) (declared)")
    else:
        detected = sorted({p["columns"] for p in pages})
        print(f"  Layout: auto (detected {'/'.join(str(c) for c in detected)} "
              f"column(s) across pages)")

    if start_page > 1:
        print(f"  Starting from page {start_page}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"  Writing to: {output_path.resolve()}")
    if output_path.exists():
        print(f"  Output file exists — appending new content")
    else:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(build_front_matter(pdf_path, meta))

    failed_pages: list[int] = []

    for p in pages:
        # Check for cancellation between pages
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("Conversion cancelled by user.")

        # Skip pages before start_page
        if p["number"] < start_page:
            continue

        label = f"page {p['number']}/{meta['page_count']}"
        md = None
        page_text = p["text"]
        source_word_count = len(_word_set(page_text))
        do_coverage_check = source_word_count >= COVERAGE_MIN_WORDS
        ordered_snippets  = p.get("ordered_snippets", [])
        do_order_check    = len(ordered_snippets) >= ORDER_MIN_SNIPPETS

        # For multi-column pages the block-ordered text is a far better model
        # input than PyMuPDF's y-then-x stream, which interleaves columns line
        # by line. Only use it when it preserves the page's words — a table
        # or an odd block decomposition can otherwise drop content.
        ordered_text = p.get("ordered_text", "")
        pre_ordered  = bool(ordered_text) and \
            text_coverage(page_text, ordered_text) >= COVERAGE_THRESHOLD
        model_text = ordered_text if pre_ordered else page_text
        if ordered_text and not pre_ordered:
            print(f"  {label}: re-ordered text dropped content, "
                  f"using raw extraction order")

        # Inner helper: run ONE conversion attempt in the appropriate mode.
        def _attempt() -> str:
            if use_hybrid:
                b64 = render_page_b64(pdf_path, p["number"])
                src = model_text.strip() or "(no selectable text on this page)"
                return convert_page_hybrid(b64, src, label, model, ollama_url,
                                           read_timeout=read_timeout,
                                           cancel_event=cancel_event,
                                           num_ctx=num_ctx,
                                           columns=columns,
                                           pre_ordered=pre_ordered)
            if use_vision:
                crops = render_page_reading_order_b64(pdf_path, p["number"],
                                                      columns)
                if len(crops) > 1:
                    print(f"  {label}: split into {len(crops)} reading-order regions")
                parts: list[str] = []
                for idx, crop_b64 in enumerate(crops, 1):
                    sub_label = label if len(crops) == 1 else f"{label} region {idx}/{len(crops)}"
                    parts.append(
                        convert_page_vision(crop_b64, sub_label, model, ollama_url,
                                            read_timeout=read_timeout,
                                            cancel_event=cancel_event,
                                            num_ctx=num_ctx,
                                            columns=columns,
                                            is_region=len(crops) > 1)
                    )
                return "\n\n".join(parts).strip()
            # text mode
            text = model_text.strip()
            if not text:
                return ""
            return convert_text_page(text, label, model, ollama_url,
                                     read_timeout=read_timeout,
                                     cancel_event=cancel_event,
                                     num_ctx=num_ctx,
                                     columns=columns,
                                     pre_ordered=pre_ordered)

        # Skip text-mode pages with no source text.
        if not use_hybrid and not use_vision and not page_text.strip():
            print(f"  {label}: no text, skipping")
            continue

        best_md         = ""
        best_score      = -1.0
        best_cov        = 0.0
        best_order      = 1.0
        best_order_info = (0, 0)
        fatal_exc: Exception | None = None
        for attempt in range(COVERAGE_RETRIES + 1):
            try:
                result = _attempt()
            except CancelledError:
                raise
            except RuntimeError as exc:
                fatal_exc = exc
                break

            cov = text_coverage(page_text, result) if do_coverage_check else 1.0
            if do_order_check:
                order_score, ok_pairs, tot_pairs = column_order_score(
                    ordered_snippets, result
                )
            else:
                order_score, ok_pairs, tot_pairs = 1.0, 0, 0

            # Combined score: weight order more heavily for multi-column
            # pages — the user explicitly wants left-then-right verified.
            combined = 0.4 * cov + 0.6 * order_score

            cov_part   = f"text coverage {cov*100:.1f}%" if do_coverage_check else "coverage skipped"
            order_part = (f"column order {order_score*100:.1f}% "
                          f"({ok_pairs}/{tot_pairs} pairs)"
                          if do_order_check else "order check skipped")
            print(f"  {label}: {cov_part}, {order_part}")

            if combined > best_score:
                best_md         = result
                best_score      = combined
                best_cov        = cov
                best_order      = order_score
                best_order_info = (ok_pairs, tot_pairs)

            cov_ok   = (not do_coverage_check) or cov         >= COVERAGE_THRESHOLD
            order_ok = (not do_order_check)    or order_score >= ORDER_THRESHOLD
            if cov_ok and order_ok:
                break
            if attempt < COVERAGE_RETRIES:
                reasons = []
                if not cov_ok:
                    reasons.append(f"coverage<{COVERAGE_THRESHOLD*100:.0f}%")
                if not order_ok:
                    reasons.append(f"column order<{ORDER_THRESHOLD*100:.0f}%")
                print(f"  {label}: {', '.join(reasons)}, "
                      f"retrying ({attempt + 2}/{COVERAGE_RETRIES + 1})...")

        if fatal_exc is not None:
            if use_vision and not use_hybrid and mode != "vision":
                # auto-mode: fall back to text mode for remaining pages.
                print(f"  Vision failed: {fatal_exc}")
                print("  Falling back to text mode for remaining pages...")
                use_vision = False
                # retry this same page in text mode
                try:
                    md = _attempt()
                except RuntimeError as exc2:
                    print(f"  {label} FAILED: {exc2}")
                    failed_pages.append(p["number"])
                    with open(output_path, "a", encoding="utf-8") as f:
                        f.write(f"\n\n<!-- page {p['number']} failed: {exc2} -->\n")
                    continue
            else:
                print(f"  {label} FAILED: {fatal_exc}")
                failed_pages.append(p["number"])
                with open(output_path, "a", encoding="utf-8") as f:
                    f.write(f"\n\n<!-- page {p['number']} failed: {fatal_exc} -->\n")
                continue
        else:
            md = best_md
            if do_coverage_check and best_cov < COVERAGE_THRESHOLD:
                print(f"  {label}: WARNING best coverage was only {best_cov*100:.1f}% "
                      f"after {COVERAGE_RETRIES + 1} attempts")
            if do_order_check and best_order < ORDER_THRESHOLD:
                ok_p, tot_p = best_order_info
                hint = ("" if _is_forced(columns)
                        else " — try setting --columns to this page's layout")
                print(f"  {label}: WARNING best column order was only "
                      f"{best_order*100:.1f}% ({ok_p}/{tot_p} pairs) after "
                      f"{COVERAGE_RETRIES + 1} attempts — column flow may be "
                      f"incorrect{hint}")

        if md:
            chunk = f"\n\n<!-- page {p['number']} of {meta['page_count']} -->\n\n{md}"
            with open(output_path, "a", encoding="utf-8") as f:
                bytes_written = f.write(chunk)
                f.flush()
            print(f"  {label} wrote {bytes_written} chars to file "
                  f"(file now {output_path.stat().st_size} bytes)")
        else:
            print(f"  {label}: model returned no content — nothing written")
            failed_pages.append(p["number"])

    if failed_pages:
        print(f"  {len(failed_pages)} page(s) failed: {failed_pages}")

# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def collect_pdfs(root: Path, recursive: bool) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() == ".pdf" else []
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(root.glob(pattern))


def resolve_output(pdf: Path, input_root: Path, output_dir: Path | None) -> Path:
    stem = pdf.stem + ".md"
    if output_dir:
        rel = pdf.parent.relative_to(input_root) if input_root.is_dir() else Path(".")
        return output_dir / rel / stem
    return pdf.parent / stem

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert PDF files to Markdown using Gemma on Ollama.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="PDF file or directory to process")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Ollama model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--url", default=DEFAULT_OLLAMA,
        help=f"Ollama base URL (default: {DEFAULT_OLLAMA})",
    )
    parser.add_argument(
        "--output", metavar="DIR",
        help="Output directory (default: same directory as each PDF)",
    )
    parser.add_argument(
        "--mode", choices=["auto", "text", "vision", "hybrid"], default="auto",
        help=(
            "Processing mode: "
            "auto=detect scanned PDFs and use vision (default), "
            "text=always extract text, "
            "vision=always render page images (requires a multimodal model), "
            "hybrid=send both extracted text AND page image to a multimodal "
            "model in one request (best for multi-column documents)"
        ),
    )
    parser.add_argument(
        "--columns", choices=["auto", "1", "2", "3"], default="auto",
        help=(
            "Page-layout column count (default: auto). Declaring the layout "
            "improves reading-order accuracy: it forces the page split in "
            "vision mode instead of relying on gutter detection, pre-orders "
            "the extracted text for text/hybrid mode, and tells the model "
            "exactly how many columns to expect. Use 1 for single-column "
            "documents to rule out spurious column splits entirely."
        ),
    )
    parser.add_argument(
        "--start-page", type=int, default=1, metavar="N",
        help="Page number to start conversion from (default: 1)",
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true",
        help="Recurse into subdirectories",
    )
    parser.add_argument(
        "--timeout", type=float, default=READ_TIMEOUT, metavar="SEC",
        help=(
            f"Max seconds to wait between streamed response chunks "
            f"(default: {READ_TIMEOUT}). Raise this if your model is slow "
            f"to start generating."
        ),
    )
    parser.add_argument(
        "--num-ctx", type=int, default=NUM_CTX, metavar="TOKENS",
        help=(
            f"Model context window in tokens (default: {NUM_CTX}). "
            f"Larger values handle longer pages but use more VRAM."
        ),
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output) if args.output else None

    if not input_path.exists():
        sys.exit(f"Error: path not found: {input_path}")

    pdfs = collect_pdfs(input_path, args.recursive)
    if not pdfs:
        sys.exit(f"No PDF files found at: {input_path}")

    print(f"Found {len(pdfs)} PDF(s).  Model: {args.model}  Ollama: {args.url}  "
          f"Mode: {args.mode}  Columns: {args.columns}")

    ok = failed = 0
    for i, pdf in enumerate(pdfs, 1):
        dest = resolve_output(pdf, input_path, output_dir)
        print(f"\n[{i}/{len(pdfs)}] {pdf}")
        try:
            convert_pdf(pdf, dest, args.model, args.url, args.mode,
                        start_page=args.start_page,
                        read_timeout=args.timeout,
                        num_ctx=args.num_ctx,
                        columns=args.columns)
            print(f"  -> {dest}")
            ok += 1
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            failed += 1

    print(f"\nDone. {ok} succeeded, {failed} failed.")


if __name__ == "__main__":
    main()
