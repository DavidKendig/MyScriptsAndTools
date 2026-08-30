#!/usr/bin/env python3
"""
img_to_svg.py — Convert images with a transparent background into clean SVG
vector art, using a local vision model (Qwen-VL, Gemma, LLaVA, ...) running on
Ollama or LM Studio to drive the trace.

How it works
------------
A vision model cannot draw pixel-accurate outlines, so the geometry is produced
by a deterministic tracer:

    alpha channel -> subject mask -> colour clustering -> contours ->
    simplified polygons -> smoothed Bezier paths -> <path> elements

The vision model does the part it is actually good at: *looking* at the picture
and deciding how it should be traced.

Modes:
    trace     No AI at all. Pure geometric tracing with the given options.
    assist    (default) The model inspects the image, chooses the trace
              parameters (colour count, detail, smoothing, corner handling),
              and names the colour layers. With --refine it also looks at a
              rendered preview of its own trace and corrects the parameters.
    generate  The model authors the SVG markup directly. Good for simple flat
              icons and logos, unreliable for anything detailed. Falls back to
              tracing if the model returns unusable markup.

Usage:
    python img_to_svg.py [options] <image-file-or-directory>

Examples:
    python img_to_svg.py logo.png
    python img_to_svg.py --mode trace --colors 4 sticker.png
    python img_to_svg.py --mode assist --refine 2 --preview ./cutouts
    python img_to_svg.py --model qwen3-vl:8b -r --output ./svg ./art
"""

import argparse
import base64
import io
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image, ImageOps

# Optional HEIC/HEIF support (iPhone default). If pillow-heif is installed,
# register it so Pillow can open .heic/.heif files transparently.
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_OK = True
except ImportError:
    HEIF_OK = False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class CancelledError(Exception):
    """Raised when a conversion job is cancelled by the user."""


DEFAULT_MODEL     = "qwen3-vl"
DEFAULT_OLLAMA    = "http://localhost:11434"
DEFAULT_LM_STUDIO = "http://localhost:1234"

# Cap the long edge before sending to the model. Vision models gain nothing
# from larger images and it keeps payloads reasonable.
MAX_LONG_EDGE = 1024

# Networking defaults. Streaming responses use these as
# (connect_timeout, read_timeout). The read_timeout is the maximum gap
# *between* chunks from the server, not the total request time.
CONNECT_TIMEOUT = 30
READ_TIMEOUT    = 900   # 15 minutes of silence before giving up
MAX_RETRIES     = 2     # retry transient connection / timeout errors
# How long Ollama should keep the model loaded in VRAM between requests.
KEEP_ALIVE      = "30m"
# Ollama defaults to a 2048-token context, far too small for vision work.
NUM_CTX         = 8192
NUM_PREDICT     = 4096

# Formats Pillow can open that plausibly carry an alpha channel, plus the
# common opaque ones (usable with --auto-key).
SUPPORTED_EXTS = {".png", ".webp", ".tif", ".tiff", ".gif", ".bmp",
                  ".jpg", ".jpeg", ".heic", ".heif"}


@dataclass
class TraceOptions:
    """Everything that controls the geometric trace."""

    colors:          int   = 6      # number of colour layers (k-means clusters)
    alpha_threshold: int   = 128    # alpha >= this counts as part of the subject
    despeckle:       int   = 2      # morphological cleanup radius, px (0 = off)
    min_area:        float = 12.0   # drop shapes smaller than this, px^2
    tolerance:       float = 1.2    # polygon simplification, px (higher = fewer points)
    smooth:          float = 0.6    # 0 = polygons, 1 = fully rounded Beziers
    corner_angle:    float = 60.0   # direction changes above this stay sharp
    precision:       int   = 1      # decimal places in path coordinates
    work_size:       int   = 1400   # trace at this long edge, then scale back up
    sharpen:         bool  = True   # rebuild corners that anti-aliasing chamfered
    auto_key:        bool  = False  # key out a flat background if there is no alpha
    key_tolerance:   int   = 26     # colour distance for --auto-key
    stroke_hairline: bool  = False  # hairline stroke to close seams between layers

    def clamped(self) -> "TraceOptions":
        """Return a copy with every field forced into a sane range."""
        return replace(
            self,
            colors          = int(max(1, min(32, self.colors))),
            alpha_threshold = int(max(1, min(254, self.alpha_threshold))),
            despeckle       = int(max(0, min(8, self.despeckle))),
            min_area        = float(max(0.0, min(10000.0, self.min_area))),
            tolerance       = float(max(0.0, min(8.0, self.tolerance))),
            smooth          = float(max(0.0, min(1.0, self.smooth))),
            corner_angle    = float(max(5.0, min(179.0, self.corner_angle))),
            precision       = int(max(0, min(4, self.precision))),
            work_size       = int(max(256, min(6000, self.work_size))),
            key_tolerance   = int(max(1, min(200, self.key_tolerance))),
        )


@dataclass
class Shape:
    """One filled region: an outer ring plus any holes punched through it."""
    outer: np.ndarray                       # (N, 2) float32, image coordinates
    holes: list = field(default_factory=list)


@dataclass
class Layer:
    """All shapes sharing one flat colour."""
    color:  tuple           # (r, g, b)
    pixels: int             # how many source pixels this colour covers
    shapes: list = field(default_factory=list)
    name:   str  = ""       # optional semantic label from the vision model

    @property
    def hex(self) -> str:
        r, g, b = self.color
        return "#{:02x}{:02x}{:02x}".format(r, g, b)


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------

def load_rgba(path: Path) -> np.ndarray:
    """Load an image as an (H, W, 4) uint8 RGBA array, EXIF orientation applied."""
    ext = path.suffix.lower()
    if ext in (".heic", ".heif") and not HEIF_OK:
        raise RuntimeError(
            "HEIC/HEIF support requires the 'pillow-heif' package. "
            "Install it with: pip install pillow-heif"
        )
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        # GIF/PNG palettes carry transparency in an index; RGBA conversion
        # resolves it correctly.
        img = img.convert("RGBA")
        return np.array(img, dtype=np.uint8)


def encode_png_b64(rgba: np.ndarray, max_long_edge: int = MAX_LONG_EDGE) -> str:
    """Downscale if needed and return a base64 PNG."""
    img = Image.fromarray(rgba, mode="RGBA")
    long_edge = max(img.size)
    if long_edge > max_long_edge:
        scale = max_long_edge / long_edge
        img = img.resize((max(1, int(img.size[0] * scale)),
                          max(1, int(img.size[1] * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def flatten_for_vision(rgba: np.ndarray) -> np.ndarray:
    """
    Composite the image over a flat light-grey background.

    Vision models frequently render transparent pixels as black, which hides
    dark line art completely. Flattening first is what makes a cutout legible
    to the model.
    """
    h, w = rgba.shape[:2]
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    bg = np.full((h, w, 3), 200, dtype=np.float32)
    rgb = rgba[:, :, :3].astype(np.float32) * alpha + bg * (1.0 - alpha)
    return np.dstack([rgb.astype(np.uint8),
                      np.full((h, w), 255, dtype=np.uint8)])


# ---------------------------------------------------------------------------
# Subject mask
# ---------------------------------------------------------------------------

def build_mask(rgba: np.ndarray, opts: TraceOptions) -> np.ndarray:
    """
    Produce the 0/255 subject mask.

    Normally this is the alpha channel thresholded. If the image has no usable
    alpha and --auto-key is on, the background is flood-filled away from the
    four corners instead, which keeps interior regions that happen to match the
    background colour.
    """
    alpha = rgba[:, :, 3]
    has_alpha = bool((alpha < 250).any())

    if has_alpha:
        mask = (alpha >= opts.alpha_threshold).astype(np.uint8) * 255
    elif opts.auto_key:
        mask = _flood_key(rgba[:, :, :3], opts.key_tolerance)
    else:
        # Fully opaque and no keying requested: trace the whole canvas.
        mask = np.full(alpha.shape, 255, dtype=np.uint8)

    if opts.despeckle > 0:
        k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (opts.despeckle * 2 + 1, opts.despeckle * 2 + 1))
        # Open removes stray specks, close seals pinholes left by soft edges.
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)

    return mask


def _flood_key(rgb: np.ndarray, tolerance: int) -> np.ndarray:
    """Flood-fill the background inwards from the four corners."""
    h, w = rgb.shape[:2]
    img = np.ascontiguousarray(rgb[:, :, ::-1])  # cv2 wants BGR
    ff = np.zeros((h + 2, w + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    lo = (tolerance,) * 3
    for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        cv2.floodFill(img, ff, seed, 0, lo, lo, flags)
    background = ff[1:-1, 1:-1] > 0
    return np.where(background, 0, 255).astype(np.uint8)


def drop_small_blobs(mask: np.ndarray, min_area: float) -> np.ndarray:
    """Remove connected components below min_area pixels."""
    if min_area <= 0:
        return mask
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = np.zeros_like(mask)
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out[labels == i] = 255
    return out


# ---------------------------------------------------------------------------
# Colour quantisation
# ---------------------------------------------------------------------------

def quantize(rgb: np.ndarray, mask: np.ndarray, n_colors: int,
             seed: int = 12345) -> tuple:
    """
    Cluster the visible pixels into n_colors flat colours.

    Clustering happens in CIE Lab (perceptually uniform, so it splits colours
    the way an eye would) but each layer's final colour is the mean RGB of the
    pixels assigned to it, which keeps the output faithful to the original.

    Returns (labels, colors, counts) where labels is an (H, W) int32 array with
    -1 for background.
    """
    ys, xs = np.nonzero(mask)
    labels = np.full(mask.shape, -1, dtype=np.int32)
    if len(ys) == 0:
        return labels, [], []

    lab_img = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    samples_all = lab_img[ys, xs].astype(np.float32)
    rgb_all     = rgb[ys, xs].astype(np.float32)

    unique_colors = np.unique(samples_all, axis=0)
    k = int(min(n_colors, len(unique_colors)))

    if k <= 1:
        labels[ys, xs] = 0
        mean = rgb_all.mean(axis=0)
        return labels, [tuple(int(round(float(c))) for c in mean)], [len(ys)]

    # Fit on a subsample; assigning every pixel afterwards is cheap and exact.
    rng = np.random.default_rng(seed)
    if len(samples_all) > 60000:
        idx = rng.choice(len(samples_all), 60000, replace=False)
        fit = np.ascontiguousarray(samples_all[idx])
    else:
        fit = samples_all

    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 24, 0.8)
    _, _, centers = cv2.kmeans(fit, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)

    # Nearest-centre assignment, one centre at a time to bound memory.
    best_d = None
    best_i = np.zeros(len(samples_all), dtype=np.int32)
    for i, c in enumerate(centers):
        d = ((samples_all - c) ** 2).sum(axis=1)
        if best_d is None:
            best_d = d
        else:
            better = d < best_d
            best_d = np.where(better, d, best_d)
            best_i = np.where(better, i, best_i)
    labels[ys, xs] = best_i

    colors, counts = [], []
    for i in range(k):
        sel = best_i == i
        n = int(sel.sum())
        counts.append(n)
        if n:
            mean = rgb_all[sel].mean(axis=0)
            colors.append(tuple(int(round(float(c))) for c in mean))
        else:
            colors.append((0, 0, 0))
    return labels, colors, counts


# ---------------------------------------------------------------------------
# Contour tracing
# ---------------------------------------------------------------------------

def trace_layers(labels: np.ndarray, colors: list, counts: list,
                 opts: TraceOptions, scale: float = 1.0) -> list:
    """
    Turn each colour cluster into a Layer of simplified polygons.

    Layers are emitted largest-first so small details paint on top of the broad
    areas they sit inside.
    """
    order = sorted(range(len(colors)), key=lambda i: counts[i], reverse=True)
    layers = []

    for i in order:
        if counts[i] == 0:
            continue
        m = (labels == i).astype(np.uint8) * 255
        if opts.despeckle > 0:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
        m = drop_small_blobs(m, opts.min_area)
        if not m.any():
            continue

        contours, hierarchy = cv2.findContours(
            m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        if hierarchy is None:
            continue
        hierarchy = hierarchy[0]

        shapes = []
        for ci, contour in enumerate(contours):
            if hierarchy[ci][3] != -1:      # this is a hole, handled below
                continue
            outer = _simplify(contour, opts)
            if outer is None:
                continue
            holes = []
            for hi, h in enumerate(contours):
                if hierarchy[hi][3] == ci:
                    ring = _simplify(h, opts)
                    if ring is not None:
                        holes.append(ring * scale)
            shapes.append(Shape(outer=outer * scale, holes=holes))

        if shapes:
            layers.append(Layer(color=colors[i], pixels=counts[i], shapes=shapes))

    return layers


def _simplify(contour: np.ndarray, opts: TraceOptions):
    """Douglas-Peucker simplify one contour; return None if it is too small."""
    if cv2.contourArea(contour) < opts.min_area:
        return None
    eps = max(0.01, opts.tolerance)
    approx = cv2.approxPolyDP(contour, eps, True)
    pts = approx.reshape(-1, 2).astype(np.float32)
    if len(pts) < 3:
        return None
    if abs(cv2.contourArea(approx)) < opts.min_area:
        return None
    if opts.sharpen:
        pts = unbevel(pts, max_edge=max(5.0, opts.tolerance * 4.0))
    return pts


def unbevel(pts: np.ndarray, max_edge: float = 5.0, min_turn: float = 45.0,
            max_shift: float = 10.0) -> np.ndarray:
    """
    Rebuild corners that anti-aliasing turned into short chamfers.

    A hard corner in a soft-edged bitmap traces as two vertices joined by a
    tiny diagonal edge, which then gets rounded by the smoothing pass. Where
    such a stub is found, the two vertices are replaced by the intersection of
    the edges either side of it, restoring the original point. The replacement
    is rejected if the edges are near-parallel or the corner would move far,
    so genuine curves are left alone.
    """
    n = len(pts)
    if n < 5:
        return pts

    # The pair that wraps around the end of the list cannot be rewritten, so
    # rotate the ring until that pair is the longest edge, which is never a
    # chamfer. Every real stub then falls inside the scan.
    edges = np.linalg.norm(np.roll(pts, -1, axis=0) - pts, axis=1)
    pts = np.roll(pts, -int(np.argmax(edges) + 1), axis=0)

    p = pts.astype(np.float64)
    out = []
    i = 0
    while i < n:
        b = p[i]
        c = p[(i + 1) % n]
        edge = float(np.linalg.norm(c - b))
        if edge <= max_edge and i + 1 < n:
            a = p[(i - 1) % n]
            d = p[(i + 2) % n]
            v1 = b - a
            v2 = d - c
            n1 = float(np.linalg.norm(v1))
            n2 = float(np.linalg.norm(v2))
            if n1 > 1e-6 and n2 > 1e-6:
                # Total direction change across the stub, in degrees.
                cos1 = np.clip(np.dot(v1, c - b) / max(n1 * edge, 1e-9), -1, 1)
                cos2 = np.clip(np.dot(c - b, v2) / max(edge * n2, 1e-9), -1, 1)
                turn = np.degrees(np.arccos(cos1)) + np.degrees(np.arccos(cos2))
                cross = v1[0] * v2[1] - v1[1] * v2[0]
                if turn >= min_turn and abs(cross) > 1e-6:
                    # Intersection of line a->b with line c->d.
                    t = ((c[0] - a[0]) * v2[1] - (c[1] - a[1]) * v2[0]) / cross
                    x = a + t * v1
                    if (np.linalg.norm(x - b) <= max_shift
                            and np.linalg.norm(x - c) <= max_shift):
                        out.append(x)
                        i += 2          # b and c are replaced by one point
                        continue
        out.append(b)
        i += 1

    if len(out) < 3:
        return pts
    return np.asarray(out, dtype=np.float32)


# ---------------------------------------------------------------------------
# Path construction
# ---------------------------------------------------------------------------

def _fmt(v: float, precision: int) -> str:
    s = "{:.{p}f}".format(v, p=precision)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


def _clamp_handle(handle: np.ndarray, limit: float) -> np.ndarray:
    """Shorten a Bezier control handle so it cannot overshoot its segment."""
    mag = float(np.linalg.norm(handle))
    if limit > 0 and mag > limit:
        return handle * (limit / mag)
    return handle


def polygon_segments(pts: np.ndarray, opts: TraceOptions) -> list:
    """
    Build the curve segments for one closed polygon.

    Vertices whose direction changes by more than corner_angle stay sharp; the
    rest get cardinal-spline tangents (smooth=1 is Catmull-Rom), which is what
    turns a staircase of pixels into a curve that looks drawn rather than
    traced. Handles are clamped to a third of their own segment length —
    without that clamp a long edge meeting a short one throws its control
    point past the next vertex and the outline grows spikes.

    Returns a list of ("L", p0, p1) and ("C", p0, c1, c2, p1) tuples. Both the
    SVG writer and the preview rasteriser consume this, so the preview always
    shows the geometry that was actually written.
    """
    n = len(pts)
    if n < 3:
        return []

    p = pts.astype(np.float64)
    prev = np.roll(p, 1, axis=0)
    nxt  = np.roll(p, -1, axis=0)

    if opts.smooth <= 0:
        corner = np.ones(n, dtype=bool)
    else:
        v_in  = p - prev
        v_out = nxt - p
        n_in  = np.linalg.norm(v_in, axis=1)
        n_out = np.linalg.norm(v_out, axis=1)
        denom = np.maximum(n_in * n_out, 1e-9)
        cosang = np.clip((v_in * v_out).sum(axis=1) / denom, -1.0, 1.0)
        turn = np.degrees(np.arccos(cosang))          # 0 = straight through
        corner = turn > opts.corner_angle
        # Degenerate (zero-length) edges are always treated as corners.
        corner |= (n_in < 1e-9) | (n_out < 1e-9)

    tang = opts.smooth * (nxt - prev) / 2.0
    tang[corner] = 0.0

    segments = []
    for i in range(n):
        j = (i + 1) % n
        seg_len = float(np.linalg.norm(p[j] - p[i]))
        limit = seg_len / 3.0
        h1 = _clamp_handle(tang[i] / 3.0, limit)
        h2 = _clamp_handle(tang[j] / 3.0, limit)
        if float(np.linalg.norm(h1)) < 1e-6 and float(np.linalg.norm(h2)) < 1e-6:
            segments.append(("L", p[i], p[j]))
        else:
            segments.append(("C", p[i], p[i] + h1, p[j] - h2, p[j]))
    return segments


def polygon_to_path(pts: np.ndarray, opts: TraceOptions) -> str:
    """Convert a closed polygon to SVG path data."""
    segments = polygon_segments(pts, opts)
    if not segments:
        return ""

    pr = opts.precision
    start = segments[0][1]
    out = ["M{} {}".format(_fmt(start[0], pr), _fmt(start[1], pr))]
    for k, seg in enumerate(segments):
        last = k == len(segments) - 1
        if seg[0] == "L":
            if last:
                break                      # Z closes the final straight edge
            out.append("L{} {}".format(_fmt(seg[2][0], pr), _fmt(seg[2][1], pr)))
        else:
            _, _, c1, c2, end = seg
            out.append("C{} {} {} {} {} {}".format(
                _fmt(c1[0], pr), _fmt(c1[1], pr),
                _fmt(c2[0], pr), _fmt(c2[1], pr),
                _fmt(end[0], pr), _fmt(end[1], pr)))
    out.append("Z")
    return "".join(out)


def flatten_polygon(pts: np.ndarray, opts: TraceOptions,
                    steps: int = 8) -> np.ndarray:
    """Sample the curve segments back into a dense polyline for rasterising."""
    segments = polygon_segments(pts, opts)
    if not segments:
        return pts
    t = np.linspace(0.0, 1.0, steps, endpoint=False)[1:].reshape(-1, 1)
    out = []
    for seg in segments:
        if seg[0] == "L":
            out.append(seg[1])
        else:
            _, p0, c1, c2, p1 = seg
            out.append(p0)
            out.append(((1 - t) ** 3) * p0 + 3 * ((1 - t) ** 2) * t * c1
                       + 3 * (1 - t) * (t ** 2) * c2 + (t ** 3) * p1)
    return np.vstack([np.atleast_2d(o) for o in out])


def shape_to_path(shape: Shape, opts: TraceOptions) -> str:
    """Outer ring plus holes as one path; even-odd fill punches the holes out."""
    d = polygon_to_path(shape.outer, opts)
    for hole in shape.holes:
        h = polygon_to_path(hole, opts)
        if h:
            d += h
    return d


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or fallback


def build_svg(layers: list, width: int, height: int, opts: TraceOptions,
              title: str = "", desc: str = "") -> str:
    """Serialise traced layers into an SVG document with a transparent canvas."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        'fill-rule="evenodd" shape-rendering="geometricPrecision">'.format(
            w=width, h=height),
    ]
    if title:
        parts.append("  <title>{}</title>".format(_xml_escape(title)))
    if desc:
        parts.append("  <desc>{}</desc>".format(_xml_escape(desc)))

    used_ids = set()
    for i, layer in enumerate(layers):
        base = _slug(layer.name, "layer-{}".format(i + 1))
        gid = base
        n = 2
        while gid in used_ids:
            gid = "{}-{}".format(base, n)
            n += 1
        used_ids.add(gid)

        stroke = ""
        if opts.stroke_hairline:
            # A hairline stroke in the fill colour hides the sub-pixel seams
            # that can appear between adjacent layers in some renderers.
            stroke = ' stroke="{}" stroke-width="0.5"'.format(layer.hex)

        parts.append('  <g id="{}" fill="{}"{}>'.format(gid, layer.hex, stroke))
        if layer.name:
            parts.append("    <title>{}</title>".format(_xml_escape(layer.name)))
        for shape in layer.shapes:
            d = shape_to_path(shape, opts)
            if d:
                parts.append('    <path d="{}"/>'.format(d))
        parts.append("  </g>")

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Preview rendering and scoring
# ---------------------------------------------------------------------------

def render_preview(layers: list, width: int, height: int,
                   opts: TraceOptions = None) -> np.ndarray:
    """
    Rasterise the traced shapes back to RGBA, curves and all.

    This is what makes the vision-guided refine loop possible: the model can be
    shown what its parameter choices actually produced. Passing opts flattens
    the same Bezier segments the SVG writer emits, so the preview matches the
    file rather than the pre-smoothing polygons.
    """
    canvas = np.zeros((height, width, 4), dtype=np.uint8)

    def ring(points):
        if opts is not None:
            points = flatten_polygon(points, opts)
        return np.round(points).astype(np.int32)

    for layer in layers:
        m = np.zeros((height, width), dtype=np.uint8)
        for shape in layer.shapes:
            cv2.fillPoly(m, [ring(shape.outer)], 255)
            for hole in shape.holes:
                cv2.fillPoly(m, [ring(hole)], 0)
        sel = m > 0
        canvas[sel, 0], canvas[sel, 1], canvas[sel, 2] = layer.color
        canvas[sel, 3] = 255
    return canvas


def similarity(original: np.ndarray, preview: np.ndarray) -> float:
    """
    Percentage pixel match between the source image and the traced result, both
    composited over the same background. Used to keep the best refine iteration
    regardless of what the model claims about its own work.
    """
    if original.shape[:2] != preview.shape[:2]:
        preview = cv2.resize(preview, (original.shape[1], original.shape[0]),
                             interpolation=cv2.INTER_NEAREST)
    a = flatten_for_vision(original)[:, :, :3].astype(np.float32)
    b = flatten_for_vision(preview)[:, :, :3].astype(np.float32)
    diff = float(np.abs(a - b).mean())
    return max(0.0, 100.0 * (1.0 - diff / 255.0))


# ---------------------------------------------------------------------------
# The trace pipeline
# ---------------------------------------------------------------------------

def trace_image(rgba: np.ndarray, opts: TraceOptions) -> tuple:
    """
    Run the full geometric trace.

    Returns (layers, width, height) in the *original* image coordinate space.
    Tracing happens at opts.work_size for speed and noise reduction, then the
    coordinates are scaled back so the SVG matches the source dimensions.
    """
    opts = opts.clamped()
    h, w = rgba.shape[:2]

    long_edge = max(h, w)
    if long_edge > opts.work_size:
        f = opts.work_size / long_edge
        small = cv2.resize(rgba, (max(1, int(w * f)), max(1, int(h * f))),
                           interpolation=cv2.INTER_AREA)
        scale = 1.0 / f
    else:
        small = rgba
        scale = 1.0

    mask = build_mask(small, opts)
    mask = drop_small_blobs(mask, opts.min_area)
    labels, colors, counts = quantize(small[:, :, :3], mask, opts.colors)
    layers = trace_layers(labels, colors, counts, opts, scale=scale)
    return layers, w, h


# ---------------------------------------------------------------------------
# LM Studio detection
# ---------------------------------------------------------------------------

_LM_STUDIO_URL_CACHE: dict = {}


def detect_lm_studio_models(url: str = DEFAULT_LM_STUDIO,
                            timeout: float = 2.0) -> list:
    """
    Query LM Studio's native REST API for models currently loaded in memory.

    Returns an empty list if LM Studio is not running or has nothing loaded;
    callers can treat that as "use Ollama instead". Never raises.
    """
    try:
        r = requests.get(url.rstrip("/") + "/api/v0/models", timeout=timeout)
        if r.status_code != 200:
            return []
        loaded = []
        for item in r.json().get("data", []):
            mid = item.get("id")
            if mid and item.get("state", "") == "loaded":
                loaded.append(mid)
        return loaded
    except Exception:
        return []


def is_lm_studio_url(url: str) -> bool:
    """
    Heuristic: does this URL point to an LM Studio server? Checks for the
    LM Studio-specific /api/v0/models endpoint, which Ollama does not expose.
    Cached per URL to avoid repeated probes.
    """
    cached = _LM_STUDIO_URL_CACHE.get(url)
    if cached is not None:
        return cached
    try:
        r = requests.get(url.rstrip("/") + "/api/v0/models", timeout=2.0)
        result = r.status_code == 200
    except Exception:
        result = False
    _LM_STUDIO_URL_CACHE[url] = result
    return result


# ---------------------------------------------------------------------------
# Ollama API
# ---------------------------------------------------------------------------

def _post_streaming(ollama_url: str, payload: dict, read_timeout: float,
                    cancel_event=None, num_ctx: int = NUM_CTX) -> str:
    """
    Stream a response from Ollama's /api/generate endpoint.

    Streaming avoids the classic huge-read-timeout problem: the HTTP read
    timeout is reset every time a chunk arrives, so a long generation succeeds
    as long as tokens keep flowing.
    """
    payload = {
        **payload,
        "stream": True,
        "keep_alive": KEEP_ALIVE,
        "options": {
            **payload.get("options", {}),
            "num_ctx":     num_ctx,
            "num_predict": NUM_PREDICT,
        },
    }

    last_err = None
    for attempt in range(1, MAX_RETRIES + 2):  # initial try + retries
        try:
            with requests.post(
                ollama_url.rstrip("/") + "/api/generate",
                json=payload,
                stream=True,
                timeout=(CONNECT_TIMEOUT, read_timeout),
            ) as resp:
                if resp.status_code != 200:
                    try:
                        body = resp.json().get("error", resp.text)
                    except Exception:
                        body = resp.text
                    raise RuntimeError(
                        "Ollama error {}: {}".format(resp.status_code, body))

                pieces = []
                chunk_count = 0
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if cancel_event is not None and cancel_event.is_set():
                        raise CancelledError("Conversion cancelled by user.")
                    if not raw_line:
                        continue
                    try:
                        obj = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue  # ignore malformed lines

                    if "error" in obj:
                        raise RuntimeError("Ollama error: {}".format(obj["error"]))

                    piece = obj.get("response", "")
                    if piece:
                        pieces.append(piece)
                        chunk_count += 1
                        # Lightweight liveness indicator.
                        if chunk_count % 20 == 0:
                            print(".", end="", flush=True)

                    if obj.get("done"):
                        break

                return "".join(pieces)

        except CancelledError:
            raise
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_err = exc
            if attempt <= MAX_RETRIES:
                wait = 2 ** attempt
                print("\n  network issue ({}); retry {}/{} in {}s...".format(
                    exc.__class__.__name__, attempt, MAX_RETRIES, wait), flush=True)
                time.sleep(wait)
                continue
            if isinstance(exc, requests.ConnectionError):
                raise RuntimeError(
                    "Cannot connect to Ollama at {}. Is Ollama running? "
                    "Try: ollama serve".format(ollama_url)) from exc
            raise RuntimeError(
                "Ollama request timed out after {}s of no data. The model may "
                "still be loading; try again, raise --timeout, or use a smaller "
                "model.".format(read_timeout)) from exc
        except requests.RequestException as exc:
            raise RuntimeError("Ollama request failed: {}".format(exc)) from exc

    raise RuntimeError("Ollama request failed: {}".format(last_err))


# ---------------------------------------------------------------------------
# LM Studio / OpenAI-compatible API
# ---------------------------------------------------------------------------

def _post_streaming_openai(base_url: str, model: str, system_prompt: str,
                           user_prompt: str, b64_images: list,
                           read_timeout: float, cancel_event=None,
                           num_ctx: int = NUM_CTX) -> str:
    """
    Stream a response from an OpenAI-compatible /v1/chat/completions endpoint
    (used by LM Studio). Images are sent as data: URLs in the multi-part
    content format.
    """
    user_content = [{"type": "text", "text": user_prompt}]
    for b64 in (b64_images or []):
        user_content.append({
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + b64},
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

    last_err = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            with requests.post(
                base_url.rstrip("/") + "/v1/chat/completions",
                json=payload,
                stream=True,
                timeout=(CONNECT_TIMEOUT, read_timeout),
            ) as resp:
                if resp.status_code != 200:
                    try:
                        body = resp.json().get("error", resp.text)
                    except Exception:
                        body = resp.text
                    raise RuntimeError(
                        "LM Studio error {}: {}".format(resp.status_code, body))

                pieces = []
                chunk_count = 0
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if cancel_event is not None and cancel_event.is_set():
                        raise CancelledError("Conversion cancelled by user.")
                    if not raw_line:
                        continue
                    # OpenAI SSE format: each event is a "data: {json}" line.
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
                        raise RuntimeError("LM Studio error: {}".format(msg))

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

                return "".join(pieces)

        except CancelledError:
            raise
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_err = exc
            if attempt <= MAX_RETRIES:
                wait = 2 ** attempt
                print("\n  network issue ({}); retry {}/{} in {}s...".format(
                    exc.__class__.__name__, attempt, MAX_RETRIES, wait), flush=True)
                time.sleep(wait)
                continue
            if isinstance(exc, requests.ConnectionError):
                raise RuntimeError(
                    "Cannot connect to LM Studio at {}. Make sure LM Studio is "
                    "running and the local server is started.".format(base_url)) from exc
            raise RuntimeError(
                "LM Studio request timed out after {}s of no data.".format(
                    read_timeout)) from exc
        except requests.RequestException as exc:
            raise RuntimeError("LM Studio request failed: {}".format(exc)) from exc

    raise RuntimeError("LM Studio request failed: {}".format(last_err))


def ask_vision(system_prompt: str, user_prompt: str, b64_images: list,
               model: str, url: str, read_timeout: float = READ_TIMEOUT,
               cancel_event=None, num_ctx: int = NUM_CTX) -> str:
    """Send a vision request to whichever local backend the URL points at."""
    if is_lm_studio_url(url):
        return _post_streaming_openai(url, model, system_prompt, user_prompt,
                                      b64_images, read_timeout, cancel_event,
                                      num_ctx=num_ctx)
    payload = {
        "model":  model,
        "system": system_prompt,
        "prompt": user_prompt,
        "images": b64_images or [],
    }
    return _post_streaming(url, payload, read_timeout, cancel_event,
                           num_ctx=num_ctx)


# ---------------------------------------------------------------------------
# Vision-guided planning
# ---------------------------------------------------------------------------

SYSTEM_PLAN = """\
You are a vector-tracing planner. You are shown a single image that will be \
converted to SVG by an automatic tracer. The tracer works by grouping pixels \
into flat colour regions and outlining each region with Bezier paths. Your job \
is to choose the tracing parameters that suit this particular image.

Reply with ONLY a JSON object, no prose, no code fences:

{
  "subject": "short description of what the image shows",
  "kind": "logo | icon | line-art | illustration | sticker | photo",
  "colors": integer 2-24,
  "tolerance": number 0.3-4.0,
  "smooth": number 0.0-1.0,
  "corner_angle": number 15-120,
  "despeckle": integer 0-4,
  "notes": "one sentence explaining the choices"
}

Guidance:
- colors: count the distinct flat colours you can actually see. Flat logos and \
icons need 2-6. Cel-shaded or comic art needs 6-12. Soft gradients and \
photographs need 12-24 and will never vectorise cleanly.
- tolerance: how much the outlines may deviate from the pixels, in pixels. \
Use 0.5-1.0 for crisp geometric art with fine detail, 1.5-3.0 for soft or \
noisy art where fewer points is better.
- smooth: 0.0 for hard-edged geometric shapes and pixel art, 0.5-0.8 for \
hand-drawn or organic shapes, 1.0 for very rounded blobby art.
- corner_angle: direction changes sharper than this stay as hard corners. Use \
20-40 to preserve lots of sharp points (stars, text, machinery), 60-90 for \
mostly curved subjects.
- despeckle: 0 keeps fine detail such as thin lines, whiskers and text; 2-4 \
removes noise and stray anti-aliasing pixels around a cutout.\
"""

SYSTEM_REFINE = """\
You are reviewing an automatic image-to-SVG trace. You are shown two images: \
first the ORIGINAL, then a PREVIEW rendered from the vector trace that was \
just produced with the parameters given to you.

Compare them and adjust the parameters so the next attempt is closer to the \
original. Reply with ONLY a JSON object, no prose, no code fences, using the \
same schema you were given:

{"colors": int, "tolerance": number, "smooth": number, "corner_angle": number, \
"despeckle": int, "notes": "what was wrong and what you changed"}

Diagnosis guide:
- Preview has flat blotches where the original has shading -> raise colors.
- Preview shows banding or speckled confetti in smooth areas -> lower colors \
or raise despeckle.
- Small features (eyes, text, thin lines) missing -> lower despeckle, lower \
tolerance.
- Outlines look lumpy, wobbly or melted -> lower smooth, raise corner_angle.
- Outlines look like a jagged staircase -> raise smooth, lower corner_angle.
- Sharp points or straight edges are rounded off -> lower corner_angle so more \
vertices stay sharp.
If the preview already matches well, return the same values with a note \
saying so.\
"""

SYSTEM_NAME = """\
You are labelling the colour layers of a vectorised image. You are shown the \
image and the list of flat colours the tracer extracted, largest area first.

Reply with ONLY a JSON object, no prose, no code fences:

{"name": "kebab-case name for the whole artwork",
 "layers": ["kebab-case-label", ...]}

The layers array must have exactly one label per colour, in the same order. \
Labels describe what that colour is in the picture (e.g. "outline", "skin", \
"shirt-red", "leaf-highlight"). Use "unknown" if you cannot tell.\
"""

SYSTEM_GENERATE = """\
You are an SVG artist. You are shown a single image with a transparent \
background. Redraw it as clean, hand-authored SVG.

Rules:
- Output ONLY the SVG markup, starting with <svg and ending with </svg>. No \
prose, no code fences, no explanation.
- Use viewBox="0 0 {w} {h}" and matching width/height attributes.
- Leave the background transparent. Never add a background rectangle.
- Use simple filled <path>, <circle>, <ellipse>, <rect> and <polygon> elements \
with flat hex fills sampled from the image.
- Reproduce the shapes, proportions and placement of the original as closely \
as you can.
- No <script>, no <image>, no external references, no embedded raster data.\
"""


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply, tolerating chatter."""
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        return obj if isinstance(obj, dict) else {}
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return {}


def _apply_plan(opts: TraceOptions, plan: dict) -> TraceOptions:
    """Merge a model-supplied plan into trace options, ignoring junk values."""
    def num(key, cast):
        if key not in plan:
            return None
        try:
            return cast(plan[key])
        except (TypeError, ValueError):
            return None

    updates = {}
    for key, cast in (("colors", int), ("despeckle", int),
                      ("tolerance", float), ("smooth", float),
                      ("corner_angle", float)):
        v = num(key, cast)
        if v is not None:
            updates[key] = v
    return replace(opts, **updates).clamped()


def plan_trace(b64_image: str, opts: TraceOptions, model: str, url: str,
               read_timeout: float = READ_TIMEOUT, cancel_event=None,
               num_ctx: int = NUM_CTX) -> tuple:
    """Ask the vision model how this image should be traced."""
    reply = ask_vision(
        SYSTEM_PLAN,
        "Choose the tracing parameters for this image. JSON only.",
        [b64_image], model, url, read_timeout, cancel_event, num_ctx)
    plan = _extract_json(reply)
    return _apply_plan(opts, plan), plan


def refine_trace(b64_original: str, b64_preview: str, opts: TraceOptions,
                 model: str, url: str, read_timeout: float = READ_TIMEOUT,
                 cancel_event=None, num_ctx: int = NUM_CTX) -> tuple:
    """Show the model its own trace and ask for corrected parameters."""
    current = json.dumps({
        "colors":       opts.colors,
        "tolerance":    round(opts.tolerance, 2),
        "smooth":       round(opts.smooth, 2),
        "corner_angle": round(opts.corner_angle, 1),
        "despeckle":    opts.despeckle,
    })
    reply = ask_vision(
        SYSTEM_REFINE,
        "Parameters used for the preview: " + current +
        "\nFirst image = original, second image = preview of the trace. "
        "Return corrected parameters as JSON only.",
        [b64_original, b64_preview], model, url, read_timeout, cancel_event, num_ctx)
    plan = _extract_json(reply)
    return _apply_plan(opts, plan), plan


def name_layers(b64_image: str, layers: list, model: str, url: str,
                read_timeout: float = READ_TIMEOUT, cancel_event=None,
                num_ctx: int = NUM_CTX) -> str:
    """
    Ask the model to label each colour layer. Labels become group ids and
    <title> elements, so the SVG opens in an editor with meaningful layer
    names instead of layer-1, layer-2, ...

    Returns a suggested name for the artwork; layer names are set in place.
    """
    palette = ", ".join(
        "{} ({:.0f}%)".format(l.hex, 100.0 * l.pixels / max(1, sum(x.pixels for x in layers)))
        for l in layers)
    reply = ask_vision(
        SYSTEM_NAME,
        "Colours, largest area first: " + palette +
        "\nReturn exactly {} labels as JSON only.".format(len(layers)),
        [b64_image], model, url, read_timeout, cancel_event, num_ctx)
    data = _extract_json(reply)

    names = data.get("layers")
    if isinstance(names, list):
        for layer, raw in zip(layers, names):
            if isinstance(raw, str) and raw.strip():
                layer.name = _slug(raw, "")
    title = data.get("name")
    return _slug(title, "") if isinstance(title, str) else ""


# ---------------------------------------------------------------------------
# Model-authored SVG (generate mode)
# ---------------------------------------------------------------------------

_UNSAFE_TAGS = {"script", "foreignobject", "iframe", "image", "use", "animate",
                "animatetransform", "animatemotion", "set", "handler"}


def sanitize_svg(markup: str, width: int, height: int) -> str:
    """
    Validate and clean SVG markup produced by a model.

    Model output is untrusted text: it is parsed as XML, active content
    (scripts, event handlers, external and embedded references) is stripped,
    and the result is only accepted if a real <svg> root with drawable content
    survives.
    """
    if not markup:
        raise RuntimeError("model returned no SVG markup")

    fenced = re.search(r"```(?:svg|xml|html)?\s*(.*?)```", markup, re.S)
    if fenced:
        markup = fenced.group(1)
    start = markup.find("<svg")
    end = markup.rfind("</svg>")
    if start == -1 or end == -1:
        raise RuntimeError("model reply did not contain an <svg> element")
    markup = markup[start:end + 6]

    try:
        root = ET.fromstring(markup)
    except ET.ParseError as exc:
        if "unbound prefix" not in str(exc):
            raise RuntimeError("model produced malformed SVG XML: {}".format(exc)) from exc
        # Models routinely write xlink:href (and the odd inkscape: attribute)
        # without declaring the namespace, which is fatal to a strict XML
        # parser. Declare xlink, then drop any other prefixed attribute, since
        # none of them survive sanitising anyway.
        patched = markup
        if "xmlns:xlink" not in patched:
            patched = patched.replace(
                "<svg", '<svg xmlns:xlink="http://www.w3.org/1999/xlink"', 1)
        try:
            root = ET.fromstring(patched)
        except ET.ParseError:
            patched = re.sub(
                r'\s(?!xmlns:)(?!xlink:)[A-Za-z_][\w.-]*:[\w.-]+\s*=\s*"[^"]*"',
                "", patched)
            try:
                root = ET.fromstring(patched)
            except ET.ParseError as exc2:
                raise RuntimeError(
                    "model produced malformed SVG XML: {}".format(exc2)) from exc2

    def localname(tag):
        return tag.split("}")[-1].lower() if isinstance(tag, str) else ""

    if localname(root.tag) != "svg":
        raise RuntimeError("model reply root element was not <svg>")

    def strip_attrs(el):
        """Drop event handlers and anything that can pull in remote content."""
        for attr in list(el.attrib):
            a = attr.split("}")[-1].lower()
            if a.startswith("on") or a == "href" or a == "src":
                del el.attrib[attr]

    strip_attrs(root)                       # the root carries handlers too

    drawable = 0
    for parent in root.iter():
        for child in list(parent):
            name = localname(child.tag)
            if name in _UNSAFE_TAGS:
                parent.remove(child)
                continue
            strip_attrs(child)
            if name in ("path", "rect", "circle", "ellipse", "polygon",
                        "polyline", "line", "text"):
                drawable += 1

    if drawable == 0:
        raise RuntimeError("model SVG contained no drawable shapes")

    if not root.get("viewBox"):
        root.set("viewBox", "0 0 {} {}".format(width, height))
    root.set("width", str(width))
    root.set("height", str(height))

    if root.tag.startswith("{"):
        # Namespaced tags already serialise with an xmlns declaration.
        ET.register_namespace("", "http://www.w3.org/2000/svg")
    else:
        root.set("xmlns", "http://www.w3.org/2000/svg")

    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def generate_svg(b64_image: str, width: int, height: int, model: str, url: str,
                 read_timeout: float = READ_TIMEOUT, cancel_event=None,
                 num_ctx: int = NUM_CTX) -> str:
    """Have the model author the SVG directly, then sanitise what it returns."""
    reply = ask_vision(
        SYSTEM_GENERATE.format(w=width, h=height),
        "Redraw this image as SVG. Markup only.",
        [b64_image], model, url, read_timeout, cancel_event, num_ctx)
    return sanitize_svg(reply, width, height)


# ---------------------------------------------------------------------------
# Conversion orchestration
# ---------------------------------------------------------------------------

def convert_image(img_path: Path, output_path: Path, model: str, url: str,
                  mode: str = "assist", opts: TraceOptions = None,
                  refine: int = 0, write_preview: bool = False,
                  cancel_event=None, read_timeout: float = READ_TIMEOUT,
                  num_ctx: int = NUM_CTX) -> dict:
    """
    Convert one image to SVG. Returns a stats dict.

    In assist mode a model failure is never fatal: the reason is reported and
    the trace falls back to the supplied options, so a batch run still produces
    output if Ollama goes away halfway through.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("Conversion cancelled by user.")

    opts = (opts or TraceOptions()).clamped()
    rgba = load_rgba(img_path)
    h, w = rgba.shape[:2]
    transparent = bool((rgba[:, :, 3] < 250).any())

    print("  {}  {}x{}  {}".format(
        img_path.name, w, h,
        "transparent" if transparent else "opaque (use --auto-key to remove a flat background)"))

    b64 = encode_png_b64(flatten_for_vision(rgba))
    title = ""
    plan = {}

    # ---- generate mode: the model writes the markup itself -------------
    if mode == "generate":
        print("    asking {} to author SVG ".format(model), end="", flush=True)
        t0 = time.time()
        try:
            svg = generate_svg(b64, w, h, model, url, read_timeout,
                               cancel_event, num_ctx)
            print(" done ({:.1f}s)".format(time.time() - t0))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(svg, encoding="utf-8")
            return {"mode": "generate", "bytes": len(svg.encode("utf-8")),
                    "layers": 0, "paths": 0, "match": None}
        except CancelledError:
            raise
        except Exception as exc:
            print("\n    model SVG unusable ({}) - falling back to tracing".format(exc))
            mode = "assist"

    # ---- assist mode: the model picks the trace parameters -------------
    if mode == "assist":
        print("    planning with {} ".format(model), end="", flush=True)
        t0 = time.time()
        try:
            opts, plan = plan_trace(b64, opts, model, url, read_timeout,
                                    cancel_event, num_ctx)
            print(" done ({:.1f}s)".format(time.time() - t0))
            if plan.get("subject"):
                print("    subject: {} [{}]".format(
                    plan.get("subject"), plan.get("kind", "?")))
            if plan.get("notes"):
                print("    plan: {}".format(plan["notes"]))
            print("    params: colors={} tolerance={} smooth={} corner={} despeckle={}".format(
                opts.colors, round(opts.tolerance, 2), round(opts.smooth, 2),
                round(opts.corner_angle, 1), opts.despeckle))
        except CancelledError:
            raise
        except Exception as exc:
            print("\n    planning failed ({}) - tracing with current settings".format(exc))

    # ---- trace, optionally refining against a rendered preview ---------
    layers, out_w, out_h = trace_image(rgba, opts)
    preview = render_preview(layers, out_w, out_h, opts)
    score = similarity(rgba, preview)
    best = (score, opts, layers, preview)
    print("    trace: {} layers, {} paths, {:.1f}% pixel match".format(
        len(layers), sum(len(l.shapes) for l in layers), score))

    for i in range(max(0, refine) if mode == "assist" else 0):
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("Conversion cancelled by user.")
        print("    refine {}/{} ".format(i + 1, refine), end="", flush=True)
        try:
            new_opts, note = refine_trace(
                b64, encode_png_b64(flatten_for_vision(best[3])), best[1],
                model, url, read_timeout, cancel_event, num_ctx)
        except CancelledError:
            raise
        except Exception as exc:
            print("\n    refine failed ({}) - keeping best result".format(exc))
            break
        layers, out_w, out_h = trace_image(rgba, new_opts)
        preview = render_preview(layers, out_w, out_h, new_opts)
        score = similarity(rgba, preview)
        print(" -> colors={} tolerance={} smooth={} : {:.1f}% match{}".format(
            new_opts.colors, round(new_opts.tolerance, 2),
            round(new_opts.smooth, 2), score,
            " (kept)" if score > best[0] else " (rejected, worse)"))
        if note.get("notes"):
            print("      model: {}".format(note["notes"]))
        if score > best[0]:
            best = (score, new_opts, layers, preview)

    score, opts, layers, preview = best

    if not layers:
        raise RuntimeError(
            "nothing to trace - the image is fully transparent, or the alpha "
            "threshold removed everything (try --alpha-threshold 8)")

    # ---- label the layers ---------------------------------------------
    if mode == "assist":
        print("    labelling layers ", end="", flush=True)
        try:
            title = name_layers(b64, layers, model, url, read_timeout,
                                cancel_event, num_ctx)
            print(" done: " + ", ".join(l.name or "?" for l in layers))
        except CancelledError:
            raise
        except Exception as exc:
            print("\n    labelling failed ({}) - using generic layer names".format(exc))

    desc = "Traced from {} - {} colours, {:.1f}% pixel match".format(
        img_path.name, len(layers), score)
    svg = build_svg(layers, out_w, out_h, opts,
                    title=title or img_path.stem, desc=desc)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")

    if write_preview:
        prev_path = output_path.with_name(output_path.stem + "_preview.png")
        Image.fromarray(preview, mode="RGBA").save(prev_path)
        print("    preview: {}".format(prev_path))

    return {
        "mode":   mode,
        "bytes":  len(svg.encode("utf-8")),
        "layers": len(layers),
        "paths":  sum(len(l.shapes) for l in layers),
        "match":  score,
    }


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def collect_images(root: Path, recursive: bool) -> list:
    """Find every supported image under root (a file or a directory)."""
    if root.is_file():
        return [root] if root.suffix.lower() in SUPPORTED_EXTS else []
    globber = root.rglob if recursive else root.glob
    found = []
    for ext in SUPPORTED_EXTS:
        found.extend(globber("*" + ext))
        found.extend(globber("*" + ext.upper()))
    # De-duplicate (case-insensitive filesystems on Windows match both).
    seen = set()
    unique = []
    for p in found:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return sorted(unique)


def resolve_output(img: Path, input_root: Path, output_dir) -> Path:
    stem = img.stem + ".svg"
    if output_dir:
        rel = img.parent.relative_to(input_root) if input_root.is_dir() else Path(".")
        return Path(output_dir) / rel / stem
    return img.parent / stem


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    defaults = TraceOptions()
    parser = argparse.ArgumentParser(
        description="Convert transparent-background images to SVG, guided by a "
                    "local vision model on Ollama or LM Studio.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="Image file or directory to process")

    g = parser.add_argument_group("model")
    g.add_argument("--mode", choices=["trace", "assist", "generate"],
                   default="assist",
                   help="trace = no AI, assist = model picks parameters "
                        "(default), generate = model writes the SVG itself")
    g.add_argument("--model", default=DEFAULT_MODEL,
                   help="Vision model name (default: {})".format(DEFAULT_MODEL))
    g.add_argument("--url", default=DEFAULT_OLLAMA,
                   help="Ollama or LM Studio base URL (default: {})".format(DEFAULT_OLLAMA))
    g.add_argument("--refine", type=int, default=0, metavar="N",
                   help="Show the model a preview of its own trace and let it "
                        "correct the parameters, up to N times (assist mode)")
    g.add_argument("--timeout", type=float, default=READ_TIMEOUT, metavar="SEC",
                   help="Max seconds to wait between streamed response chunks "
                        "(default: {})".format(READ_TIMEOUT))
    g.add_argument("--num-ctx", type=int, default=NUM_CTX, metavar="TOKENS",
                   help="Model context window in tokens (default: {})".format(NUM_CTX))

    t = parser.add_argument_group("trace")
    t.add_argument("--colors", type=int, default=defaults.colors,
                   help="Number of colour layers (default: {})".format(defaults.colors))
    t.add_argument("--alpha-threshold", type=int, default=defaults.alpha_threshold,
                   metavar="0-255",
                   help="Alpha at or above this counts as subject "
                        "(default: {}; lower it to keep soft edges)".format(
                            defaults.alpha_threshold))
    t.add_argument("--tolerance", type=float, default=defaults.tolerance,
                   help="Outline simplification in px, higher = fewer points "
                        "(default: {})".format(defaults.tolerance))
    t.add_argument("--smooth", type=float, default=defaults.smooth,
                   metavar="0-1",
                   help="Curve smoothing, 0 = straight polygons "
                        "(default: {})".format(defaults.smooth))
    t.add_argument("--corner-angle", type=float, default=defaults.corner_angle,
                   metavar="DEG",
                   help="Direction changes above this stay sharp "
                        "(default: {})".format(defaults.corner_angle))
    t.add_argument("--despeckle", type=int, default=defaults.despeckle,
                   metavar="PX",
                   help="Morphological cleanup radius, 0 keeps fine detail "
                        "(default: {})".format(defaults.despeckle))
    t.add_argument("--min-area", type=float, default=defaults.min_area,
                   metavar="PX2",
                   help="Drop shapes smaller than this (default: {})".format(
                       defaults.min_area))
    t.add_argument("--precision", type=int, default=defaults.precision,
                   help="Decimal places in path coordinates (default: {})".format(
                       defaults.precision))
    t.add_argument("--work-size", type=int, default=defaults.work_size,
                   metavar="PX",
                   help="Trace at this long edge then scale back up "
                        "(default: {})".format(defaults.work_size))
    t.add_argument("--auto-key", action="store_true",
                   help="For images with no alpha channel: flood-fill the "
                        "background away from the corners")
    t.add_argument("--key-tolerance", type=int, default=defaults.key_tolerance,
                   help="Colour tolerance for --auto-key (default: {})".format(
                       defaults.key_tolerance))
    t.add_argument("--hairline", action="store_true",
                   help="Add a hairline stroke to each layer to hide seams "
                        "between adjacent colours")

    o = parser.add_argument_group("output")
    o.add_argument("--output", metavar="DIR",
                   help="Output directory (default: alongside each image)")
    o.add_argument("-r", "--recursive", action="store_true",
                   help="Recurse into subdirectories")
    o.add_argument("--preview", action="store_true",
                   help="Also write a PNG preview of the trace next to the SVG")

    args = parser.parse_args()

    opts = TraceOptions(
        colors          = args.colors,
        alpha_threshold = args.alpha_threshold,
        despeckle       = args.despeckle,
        min_area        = args.min_area,
        tolerance       = args.tolerance,
        smooth          = args.smooth,
        corner_angle    = args.corner_angle,
        precision       = args.precision,
        work_size       = args.work_size,
        auto_key        = args.auto_key,
        key_tolerance   = args.key_tolerance,
        stroke_hairline = args.hairline,
    ).clamped()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit("Error: path not found: {}".format(input_path))

    images = collect_images(input_path, args.recursive)
    if not images:
        sys.exit("No supported images found at: {}\nSupported formats: {}".format(
            input_path, ", ".join(sorted(SUPPORTED_EXTS))))

    if args.mode == "trace":
        print("Found {} image(s).  Mode: trace (no AI)".format(len(images)))
    else:
        print("Found {} image(s).  Mode: {}  Model: {}  Server: {}".format(
            len(images), args.mode, args.model, args.url))

    ok = failed = 0
    for i, img in enumerate(images, 1):
        dest = resolve_output(img, input_path, args.output)
        print("\n[{}/{}] {}".format(i, len(images), img))
        try:
            stats = convert_image(img, dest, args.model, args.url,
                                  mode=args.mode, opts=opts, refine=args.refine,
                                  write_preview=args.preview,
                                  read_timeout=args.timeout, num_ctx=args.num_ctx)
            print("  -> {}  ({:.1f} KB, {} paths)".format(
                dest, stats["bytes"] / 1024.0, stats["paths"]))
            ok += 1
        except KeyboardInterrupt:
            print("\nInterrupted.")
            break
        except Exception as exc:
            print("  FAILED: {}".format(exc), file=sys.stderr)
            failed += 1

    print("\nDone. {} succeeded, {} failed.".format(ok, failed))


if __name__ == "__main__":
    main()
