"""Persisted settings for AutoTranslate.

Settings live in config.json next to the application so the whole folder stays
portable (copy it to a USB stick and your model/host choices come along).
"""

import json
import os

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
)

DEFAULT_PROMPT = """You are an expert translator working through a sequence of \
images (comic/manga pages, scanned documents, screenshots).

Rules:
- Translate every piece of readable text on the CURRENT page into {language}.
- Neighbouring pages are supplied ONLY as context (names, tone, ongoing
  sentences, honorifics). Never translate them.
- Preserve reading order. For comics, go panel by panel, top-to-bottom and
  right-to-left unless the art clearly reads left-to-right.
- Label speakers or regions when it aids clarity, e.g. "Panel 2 - Woman:".
- Keep sound effects, signage and background text, marked as [SFX] / [SIGN].
- If a region is unreadable, write [unreadable] rather than inventing text.
- Output the translation only. No preamble, no commentary, no apologies."""

DEFAULTS = {
    "backend": "Ollama",
    "ollama_url": "http://localhost:11434",
    "lmstudio_url": "http://localhost:1234/v1",
    "api_key": "",
    "model": "",
    "language": "English",
    "prompt": DEFAULT_PROMPT,
    "folder": "",
    "output_folder": "",
    "recursive": False,
    "thinking": False,
    "include_prev_image": True,
    "include_next_image": True,
    "include_prev_text": True,
    "prev_text_chars": 1200,
    "skip_existing": True,
    "max_image_px": 1400,
    "jpeg_quality": 88,
    "temperature": 0.2,
    "num_ctx": 8192,
    "timeout": 600,
    "retries": 2,
}


def load():
    """Return saved settings merged over the defaults."""
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict):
            for key, value in saved.items():
                if key in DEFAULTS:
                    cfg[key] = value
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg):
    """Write settings to disk. Returns an error string, or None on success."""
    try:
        keep = {k: v for k, v in cfg.items() if k in DEFAULTS}
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(keep, fh, indent=2)
        return None
    except OSError as exc:
        return str(exc)
