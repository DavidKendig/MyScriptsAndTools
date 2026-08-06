#!/usr/bin/env python3
"""
img_to_markdown.py — Extract text from images and convert to Markdown using a
multimodal model on Ollama.

Supports the formats produced by modern iPhone cameras as well as common
desktop formats:
    .jpg / .jpeg   (iPhone "Most Compatible" mode, general photos)
    .png           (screenshots, exports)
    .heic / .heif  (iPhone "High Efficiency" mode — default since iOS 11)

Usage:
    python img_to_markdown.py [options] <image-file-or-directory>

Examples:
    python img_to_markdown.py receipt.heic
    python img_to_markdown.py --model gemma4:12b --output ./md ./photos
    python img_to_markdown.py -r --output ./out ./screenshots
"""

import argparse
import base64
import io
import json
import sys
import threading
import time
from pathlib import Path

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


DEFAULT_MODEL     = "gemma4"
DEFAULT_OLLAMA    = "http://localhost:11434"
DEFAULT_LM_STUDIO = "http://localhost:1234"

# Cap the long edge before sending to the model. Most multimodal models do
# not benefit from images larger than this and it keeps payloads reasonable.
MAX_LONG_EDGE = 2048

# Networking defaults. Streaming responses use these as
# (connect_timeout, read_timeout). The read_timeout is the maximum gap
# *between* chunks from the server, not the total request time — so as long
# as tokens are still flowing the request won't be aborted.
CONNECT_TIMEOUT = 30
READ_TIMEOUT    = 900   # 15 minutes of silence before giving up
MAX_RETRIES     = 2     # retry transient connection / timeout errors
# How long Ollama should keep the model loaded in VRAM between requests.
# Avoids paying the multi-minute cold-load cost on every image.
KEEP_ALIVE      = "30m"
# Ollama defaults to a 2048-token context which is far too small for vision
# work — a single image eats ~1500 tokens before any text is produced.
# Bumping these prevents the model from stalling or producing garbage.
NUM_CTX         = 8192
NUM_PREDICT     = 4096

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}

SYSTEM_PROMPT_VISION = """\
You are an image-to-Markdown conversion assistant. You will be given a single \
image — typically a photograph, screenshot, scan, receipt, whiteboard, or \
document page. Your sole task is to extract every piece of text visible in \
the image and render it as well-structured Markdown.

Rules:
- Transcribe all visible text exactly, preserving headings, lists, and paragraphs.
- Infer heading levels (# ## ###) from font size and visual prominence.
- Convert bullet/numbered lists to proper Markdown lists.
- Preserve ALL tables using Markdown pipe-table syntax. Every table must use \
| column | separators, a | --- | header-separator row, and include every \
data row from the original. Maintain column alignment as closely as possible.
- Wrap code, commands, or technical content in fenced code blocks.
- If the image contains a chart, graph, or diagram, render it as ASCII art \
inside a fenced code block (```). Reproduce the structure, labels, axes, and \
data points as faithfully as possible using box-drawing characters, dashes, \
pipes, and text.
- Completely ignore purely decorative imagery such as photos of people, \
logos, icons, and background art that contain no text. Do not describe them \
or add any placeholder.
- If the image contains no readable text at all, output exactly: \
`*(no text detected)*`
- Output ONLY the converted Markdown — no commentary, preamble, or summary.\
"""

# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------

def load_image_b64(path: Path) -> str:
    """
    Load an image, normalise EXIF orientation (iPhone photos commonly need
    this), downscale if huge, and return a base64-encoded PNG string.
    """
    with Image.open(path) as img:
        # Respect EXIF orientation tag so portrait iPhone shots aren't sideways.
        img = ImageOps.exif_transpose(img)

        # Flatten alpha / palette to RGB so the model gets a plain photo.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Downscale very large images while preserving aspect ratio.
        long_edge = max(img.size)
        if long_edge > MAX_LONG_EDGE:
            scale = MAX_LONG_EDGE / long_edge
            new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
            img = img.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")


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


_LM_STUDIO_URL_CACHE: dict[str, bool] = {}


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

    Streaming avoids the classic problem of a single huge read timeout: the
    HTTP read timeout is reset every time a chunk arrives, so a long
    generation will succeed as long as tokens keep flowing.
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

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 2):  # initial try + retries
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
                        raise RuntimeError(f"Ollama error: {obj['error']}")

                    piece = obj.get("response", "")
                    if piece:
                        pieces.append(piece)
                        chunk_count += 1
                        # Lightweight liveness indicator — one dot per ~20 chunks.
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

    # Should be unreachable, but keep type-checker happy
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
        "stream":      True,
        "max_tokens":  NUM_PREDICT,
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
                    # OpenAI SSE format: each event is "data: {json}" lines.
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

                return "".join(pieces)

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


def convert_image_vision(
    b64_image:    str,
    label:        str,
    model:        str,
    ollama_url:   str,
    read_timeout: float = READ_TIMEOUT,
    cancel_event: threading.Event | None = None,
    num_ctx:      int = NUM_CTX,
) -> str:
    """
    Send an image to the model (requires a multimodal model).

    Backend is chosen automatically based on whether ``ollama_url`` points to
    an LM Studio instance (detected by the presence of /api/v0/models) or
    Ollama.
    """
    print(f"  {label} ... ", end="", flush=True)
    t0 = time.time()
    if is_lm_studio_url(ollama_url):
        result = _post_streaming_openai(
            ollama_url, model, SYSTEM_PROMPT_VISION,
            "Extract all text from this image and convert it to Markdown.",
            b64_image, read_timeout, cancel_event, num_ctx=num_ctx,
        )
    else:
        payload = {
            "model":  model,
            "system": SYSTEM_PROMPT_VISION,
            "prompt": "Extract all text from this image and convert it to Markdown.",
            "images": [b64_image],
        }
        result = _post_streaming(ollama_url, payload, read_timeout, cancel_event,
                                 num_ctx=num_ctx)
    print(f" done ({time.time() - t0:.1f}s)")
    return result

# ---------------------------------------------------------------------------
# Conversion orchestration
# ---------------------------------------------------------------------------

def build_front_matter(img_path: Path) -> str:
    lines = [
        f"source: {img_path.name}",
        f"format: {img_path.suffix.lstrip('.').lower()}",
    ]
    return "---\n" + "\n".join(lines) + "\n---\n"


def convert_image(
    img_path:     Path,
    output_path:  Path,
    model:        str,
    ollama_url:   str,
    cancel_event: threading.Event | None = None,
    read_timeout: float = READ_TIMEOUT,
    num_ctx:      int = NUM_CTX,
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("Conversion cancelled by user.")

    ext = img_path.suffix.lower()
    if ext in (".heic", ".heif") and not HEIF_OK:
        raise RuntimeError(
            f"HEIC/HEIF support requires the 'pillow-heif' package. "
            f"Install it with: pip install pillow-heif"
        )

    b64 = load_image_b64(img_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(build_front_matter(img_path))

    md = convert_image_vision(
        b64, img_path.name, model, ollama_url,
        read_timeout=read_timeout, cancel_event=cancel_event,
        num_ctx=num_ctx,
    )

    if md:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write("\n\n" + md)

# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def collect_images(root: Path, recursive: bool) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in SUPPORTED_EXTS else []
    globber = root.rglob if recursive else root.glob
    found = []
    for ext in SUPPORTED_EXTS:
        found.extend(globber(f"*{ext}"))
        found.extend(globber(f"*{ext.upper()}"))
    # De-duplicate (case-insensitive filesystems on Windows match both).
    seen: set[Path] = set()
    unique = []
    for p in found:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return sorted(unique)


def resolve_output(img: Path, input_root: Path, output_dir: Path | None) -> Path:
    stem = img.stem + ".md"
    if output_dir:
        rel = img.parent.relative_to(input_root) if input_root.is_dir() else Path(".")
        return output_dir / rel / stem
    return img.parent / stem

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract text from images and convert to Markdown using a multimodal model on Ollama.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="Image file or directory to process")
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
        help="Output directory (default: same directory as each image)",
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
            f"Larger values handle larger images but use more VRAM."
        ),
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output) if args.output else None

    if not input_path.exists():
        sys.exit(f"Error: path not found: {input_path}")

    images = collect_images(input_path, args.recursive)
    if not images:
        sys.exit(
            f"No supported images found at: {input_path}\n"
            f"Supported formats: {', '.join(sorted(SUPPORTED_EXTS))}"
        )

    if not HEIF_OK and any(p.suffix.lower() in (".heic", ".heif") for p in images):
        print(
            "Warning: pillow-heif is not installed — .heic/.heif files will be "
            "skipped. Install with: pip install pillow-heif",
            file=sys.stderr,
        )

    print(f"Found {len(images)} image(s).  Model: {args.model}  Ollama: {args.url}")

    ok = failed = 0
    for i, img in enumerate(images, 1):
        dest = resolve_output(img, input_path, output_dir)
        print(f"\n[{i}/{len(images)}] {img}")
        try:
            convert_image(img, dest, args.model, args.url,
                          read_timeout=args.timeout,
                          num_ctx=args.num_ctx)
            print(f"  -> {dest}")
            ok += 1
        except Exception as exc:
            print(f"  FAILED: {exc}", file=sys.stderr)
            failed += 1

    print(f"\nDone. {ok} succeeded, {failed} failed.")


if __name__ == "__main__":
    main()
