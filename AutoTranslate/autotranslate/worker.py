"""The batch translation job.

Runs on a background thread and reports everything back to the GUI through a
queue, so the Tk main loop never blocks.
"""

import os
import threading
import time
import traceback

from . import backends, imageutil


class TranslationJob(threading.Thread):
    def __init__(self, cfg, images, events):
        threading.Thread.__init__(self, daemon=True)
        self.cfg = cfg
        self.images = images
        self.events = events
        self._stop = threading.Event()

    # --- control ---------------------------------------------------------
    def stop(self):
        self._stop.set()

    def stopping(self):
        return self._stop.is_set()

    # --- reporting -------------------------------------------------------
    def emit(self, kind, *payload):
        self.events.put((kind,) + payload)

    def log(self, message):
        self.emit("log", message)

    # --- helpers ---------------------------------------------------------
    def output_path(self, image_path):
        out_dir = (self.cfg.get("output_folder") or "").strip()
        base = os.path.splitext(os.path.basename(image_path))[0] + ".txt"
        if not out_dir:
            return os.path.join(os.path.dirname(image_path), base)
        # Mirror the folder structure when walking recursively.
        root = self.cfg.get("folder") or ""
        rel = os.path.relpath(os.path.dirname(image_path), root)
        if rel in (".", "") or rel.startswith(".."):
            return os.path.join(out_dir, base)
        return os.path.join(out_dir, rel, base)

    def build_prompt(self, index, prev_text):
        """Return (user_text, ordered_images) for the page at `index`."""
        cfg = self.cfg
        current = self.images[index]
        has_prev = index > 0 and cfg.get("include_prev_image", True)
        has_next = index < len(self.images) - 1 and cfg.get("include_next_image", True)

        ordered, roles = [], []
        if has_prev:
            ordered.append(self.images[index - 1])
            roles.append("PREVIOUS page (context only - do not translate)")
        ordered.append(current)
        roles.append("CURRENT page (translate this one)")
        if has_next:
            ordered.append(self.images[index + 1])
            roles.append("NEXT page (context only - do not translate)")

        lines = [
            "You are given {} image(s), in this order:".format(len(ordered)),
        ]
        for pos, role in enumerate(roles, start=1):
            lines.append("  Image {}: {}".format(pos, role))
        lines.append("")

        if prev_text and cfg.get("include_prev_text", True):
            limit = int(cfg.get("prev_text_chars", 1200) or 0)
            tail = prev_text[-limit:] if limit else prev_text
            lines.append(
                "For continuity, here is your translation of the previous page:"
            )
            lines.append("--- BEGIN PREVIOUS TRANSLATION ---")
            lines.append(tail.strip())
            lines.append("--- END PREVIOUS TRANSLATION ---")
            lines.append("")

        lines.append("Current page filename: {}".format(os.path.basename(current)))
        lines.append(
            "Translate the CURRENT page into {} now.".format(
                cfg.get("language", "English")
            )
        )
        return "\n".join(lines), ordered

    # --- main loop -------------------------------------------------------
    def run(self):
        cfg = self.cfg
        total = len(self.images)
        done = skipped = failed = 0
        prev_text = ""

        try:
            backend = backends.build(
                cfg["backend"],
                cfg["ollama_url"] if cfg["backend"] == "Ollama" else cfg["lmstudio_url"],
                api_key=cfg.get("api_key", ""),
                timeout=int(cfg.get("timeout", 600)),
            )
        except Exception as exc:
            self.emit("finished", 0, 0, 0, "Could not create backend: {}".format(exc))
            return

        think = bool(cfg.get("thinking")) if cfg.get("thinking_supported", True) else None
        options = {
            "temperature": cfg.get("temperature", 0.2),
            "num_ctx": cfg.get("num_ctx", 8192),
        }
        system = cfg.get("prompt", "").replace(
            "{language}", cfg.get("language", "English")
        )
        retries = max(0, int(cfg.get("retries", 2)))

        for index, image_path in enumerate(self.images):
            if self.stopping():
                break

            name = os.path.basename(image_path)
            self.emit("progress", index, total)
            self.emit("status", "[{}/{}] {}".format(index + 1, total, name))

            out_file = self.output_path(image_path)
            if cfg.get("skip_existing", True) and os.path.exists(out_file):
                self.log("SKIP  {} (translation already exists)".format(name))
                skipped += 1
                try:
                    with open(out_file, "r", encoding="utf-8") as fh:
                        prev_text = fh.read()
                except OSError:
                    prev_text = ""
                continue

            user_text, ordered = self.build_prompt(index, prev_text)

            try:
                encoded = [
                    imageutil.encode_image(
                        path,
                        max_px=int(cfg.get("max_image_px", 1400) or 0),
                        quality=int(cfg.get("jpeg_quality", 88)),
                    )
                    for path in ordered
                ]
            except Exception as exc:
                self.log("FAIL  {} - cannot read image: {}".format(name, exc))
                failed += 1
                continue

            text = None
            for attempt in range(retries + 1):
                if self.stopping():
                    break
                try:
                    started = time.time()
                    text, thoughts = backend.chat(
                        cfg["model"],
                        system,
                        user_text,
                        encoded,
                        think,
                        options,
                        should_stop=self.stopping,
                    )
                    elapsed = time.time() - started
                    if thoughts:
                        self.log(
                            "      (model reasoned for {} characters)".format(
                                len(thoughts)
                            )
                        )
                    if not text:
                        raise backends.BackendError("model returned an empty response")
                    self.log(
                        "OK    {}  ->  {}  [{:.1f}s, {} chars]".format(
                            name, os.path.basename(out_file), elapsed, len(text)
                        )
                    )
                    break
                except backends.Stopped:
                    text = None
                    break
                except backends.BackendError as exc:
                    if attempt < retries and not self.stopping():
                        self.log(
                            "RETRY {} ({}/{}) - {}".format(
                                name, attempt + 1, retries, exc
                            )
                        )
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    self.log("FAIL  {} - {}".format(name, exc))
                    text = None
                except Exception as exc:
                    self.log("FAIL  {} - unexpected: {}".format(name, exc))
                    self.log(traceback.format_exc(limit=3))
                    text = None
                    break

            if self.stopping() and not text:
                break
            if not text:
                failed += 1
                continue

            try:
                parent = os.path.dirname(out_file)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(out_file, "w", encoding="utf-8") as fh:
                    fh.write(text.rstrip() + "\n")
            except OSError as exc:
                self.log("FAIL  cannot write {} - {}".format(out_file, exc))
                failed += 1
                continue

            prev_text = text
            done += 1
            self.emit("wrote", out_file)

        self.emit("progress", total if not self.stopping() else done + skipped, total)
        summary = "Stopped by user." if self.stopping() else "Finished."
        self.emit("finished", done, skipped, failed, summary)
