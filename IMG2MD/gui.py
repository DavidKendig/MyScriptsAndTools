#!/usr/bin/env python3
"""
gui.py — Tkinter GUI for img_to_markdown.py
"""

import csv
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

# ---------------------------------------------------------------------------
# Dependency check — runs before any heavy import so we can show a clear
# dialog instead of a raw traceback if a package is missing.
# ---------------------------------------------------------------------------

MODELS_CSV = Path(__file__).parent / "models.csv"


def _load_models() -> list[str]:
    """Load available model names from models.csv."""
    models: list[str] = []
    if MODELS_CSV.exists():
        with open(MODELS_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("model_name", "").strip()
                if name:
                    models.append(name)
    return models


def _check_dependencies() -> None:
    """Check each required package and show a friendly error if any are missing."""
    REQUIRED = {
        "PIL":      "Pillow",
        "requests": "requests",
    }

    missing = []
    for module, package in REQUIRED.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        root = tk.Tk()
        root.withdraw()
        pkg_list = "\n  ".join(missing)
        messagebox.showerror(
            "Missing dependencies",
            f"The following packages are not installed:\n\n  {pkg_list}\n\n"
            "Run  start.bat  to install them automatically,\n"
            "or open a terminal and run:\n\n"
            "  pip install -r requirements.txt",
        )
        root.destroy()
        sys.exit(1)

_check_dependencies()

# All required dependencies present — safe to import now.
from img_to_markdown import (  # noqa: E402
    CancelledError,
    DEFAULT_LM_STUDIO,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA,
    HEIF_OK,
    NUM_CTX,
    SUPPORTED_EXTS,
    collect_images,
    convert_image,
    detect_lm_studio_models,
    resolve_output,
)

# Context-size presets shown in the GUI dropdown. Each label maps to a
# num_ctx token count; the rough VRAM hint is *additional* memory on top
# of the base model weights, and varies significantly by model.
CTX_PRESETS: list[tuple[str, int]] = [
    ("Low  (4096 tokens, ~1 GB extra VRAM)",   4096),
    ("Medium (8192 tokens, ~2 GB extra VRAM)", 8192),
    ("High (16384 tokens, ~4 GB extra VRAM)",  16384),
    ("Very High (32768 tokens, ~8 GB extra VRAM)", 32768),
]
CTX_LABEL_TO_VALUE = {label: tokens for label, tokens in CTX_PRESETS}
CTX_VALUE_TO_LABEL = {tokens: label for label, tokens in CTX_PRESETS}

# ---------------------------------------------------------------------------
# Stdout redirector — funnels print() output into a queue so the worker
# thread can safely update the Tk text widget.
# ---------------------------------------------------------------------------

class _QueueStream:
    def __init__(self, q: queue.Queue):
        self._q = q

    def write(self, text: str):
        if text:
            self._q.put(text)

    def flush(self):
        pass


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Image to Markdown")
        self.geometry("700x600")
        self.minsize(620, 560)
        self.resizable(True, True)
        # Bring the window to the front on Windows
        self.after(50, self._bring_to_front)

        self._log_queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel_event = threading.Event()

        # --- backend detection: prefer LM Studio if it has a loaded model ---
        # If LM Studio is running with at least one model loaded, the GUI
        # auto-targets that model. Otherwise we fall back to Ollama with the
        # models listed in models.csv (the original behavior).
        lm_models = detect_lm_studio_models(DEFAULT_LM_STUDIO)
        self._backend = "lmstudio" if lm_models else "ollama"

        if self._backend == "lmstudio":
            self._available_models = lm_models
            default_model = lm_models[0]
            default_url   = DEFAULT_LM_STUDIO
        else:
            self._available_models = _load_models()
            default_model = self._available_models[0] if self._available_models else DEFAULT_MODEL
            default_url   = DEFAULT_OLLAMA

        # --- tk variables ---
        self.input_path  = tk.StringVar()
        self.output_path = tk.StringVar()
        self.model_var   = tk.StringVar(value=default_model)
        self.url_var     = tk.StringVar(value=default_url)
        self.recursive   = tk.BooleanVar(value=False)
        self.input_type  = tk.StringVar(value="file")  # "file" | "folder"
        self.ctx_var     = tk.StringVar(
            value=CTX_VALUE_TO_LABEL.get(NUM_CTX, CTX_PRESETS[1][0])
        )

        self._build_ui()
        self._poll_log()

        # Announce which backend we're talking to so the user knows what's going on.
        if self._backend == "lmstudio":
            self._append_log(
                f"LM Studio detected at {DEFAULT_LM_STUDIO} — using loaded model: "
                f"{default_model}\n"
                f"(To use Ollama instead, stop the LM Studio server and relaunch.)\n\n"
            )
        else:
            self._append_log(
                f"Using Ollama at {DEFAULT_OLLAMA}. "
                "(LM Studio not detected — start LM Studio's local server with a "
                "loaded model to use it instead.)\n\n"
            )

        # Inform the user if HEIC support is missing — only warn, don't block.
        if not HEIF_OK:
            self._append_log(
                "Note: pillow-heif is not installed — .heic/.heif files cannot be read.\n"
                "      Install with:  pip install pillow-heif\n\n"
            )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Root frame with padding
        root_frame = ttk.Frame(self, padding=12)
        root_frame.pack(fill=tk.BOTH, expand=True)
        root_frame.columnconfigure(0, weight=1)

        row = 0

        # -- Input --------------------------------------------------------
        ttk.Label(root_frame, text="Input", font=("", 9, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=(0, 2))
        row += 1

        input_frame = ttk.Frame(root_frame)
        input_frame.grid(row=row, column=0, sticky=tk.EW)
        input_frame.columnconfigure(0, weight=1)

        self._input_entry = ttk.Entry(input_frame, textvariable=self.input_path)
        self._input_entry.grid(row=0, column=0, sticky=tk.EW, padx=(0, 4))

        ttk.Button(input_frame, text="Browse...", command=self._browse_input).grid(
            row=0, column=1)
        row += 1

        # Radio + recursive on one row
        opt_frame = ttk.Frame(root_frame)
        opt_frame.grid(row=row, column=0, sticky=tk.W, pady=(2, 8))

        ttk.Radiobutton(opt_frame, text="Single image", variable=self.input_type,
                        value="file",   command=self._browse_input_update).pack(side=tk.LEFT)
        ttk.Radiobutton(opt_frame, text="Folder",       variable=self.input_type,
                        value="folder", command=self._browse_input_update).pack(side=tk.LEFT, padx=(8, 0))
        self._recurse_cb = ttk.Checkbutton(opt_frame, text="Recurse subdirectories",
                                           variable=self.recursive)
        self._recurse_cb.pack(side=tk.LEFT, padx=(16, 0))
        row += 1

        # -- Output -------------------------------------------------------
        ttk.Label(root_frame, text="Output folder", font=("", 9, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=(0, 2))
        row += 1

        out_frame = ttk.Frame(root_frame)
        out_frame.grid(row=row, column=0, sticky=tk.EW, pady=(0, 12))
        out_frame.columnconfigure(0, weight=1)

        ttk.Entry(out_frame, textvariable=self.output_path).grid(
            row=0, column=0, sticky=tk.EW, padx=(0, 4))
        ttk.Button(out_frame, text="Browse...", command=self._browse_output).grid(
            row=0, column=1)
        row += 1

        # -- Settings -----------------------------------------------------
        ttk.Label(root_frame, text="Settings", font=("", 9, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=(0, 2))
        row += 1

        settings_frame = ttk.Frame(root_frame)
        settings_frame.grid(row=row, column=0, sticky=tk.EW, pady=(0, 12))
        settings_frame.columnconfigure(1, weight=1)
        settings_frame.columnconfigure(3, weight=1)

        ttk.Label(settings_frame, text="Model:").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        self._model_combo = ttk.Combobox(
            settings_frame, textvariable=self.model_var, width=20,
            values=self._available_models, state="readonly",
        )
        self._model_combo.grid(row=0, column=1, sticky=tk.EW, padx=(0, 16))

        ttk.Label(settings_frame, text="VRAM / context:").grid(
            row=0, column=2, sticky=tk.W, padx=(0, 6))
        self._ctx_combo = ttk.Combobox(
            settings_frame, textvariable=self.ctx_var,
            values=[label for label, _ in CTX_PRESETS],
            state="readonly", width=38,
        )
        self._ctx_combo.grid(row=0, column=3, columnspan=3, sticky=tk.W)

        url_label = "LM Studio URL:" if self._backend == "lmstudio" else "Ollama URL:"
        ttk.Label(settings_frame, text=url_label).grid(row=1, column=0, sticky=tk.W, padx=(0, 6), pady=(6, 0))
        ttk.Entry(settings_frame, textvariable=self.url_var).grid(
            row=1, column=1, columnspan=5, sticky=tk.EW, pady=(6, 0))
        row += 1

        # -- Buttons ------------------------------------------------------
        btn_frame = ttk.Frame(root_frame)
        btn_frame.grid(row=row, column=0, sticky=tk.EW, pady=(0, 8))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=0)

        self._convert_btn = ttk.Button(btn_frame, text="Convert",
                                       command=self._on_convert_cancel)
        self._convert_btn.grid(row=0, column=0, sticky=tk.EW, padx=(0, 6), ipady=4)

        self._clear_btn = ttk.Button(btn_frame, text="Clear log", command=self._clear_log)
        self._clear_btn.grid(row=0, column=1, ipady=4)
        row += 1

        # -- Progress bar -------------------------------------------------
        self._progress = ttk.Progressbar(root_frame, mode="indeterminate")
        self._progress.grid(row=row, column=0, sticky=tk.EW, pady=(0, 6))
        row += 1

        # -- Log ----------------------------------------------------------
        ttk.Label(root_frame, text="Log", font=("", 9, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=(0, 2))
        row += 1

        self._log = scrolledtext.ScrolledText(
            root_frame, height=14, wrap=tk.WORD,
            font=("Consolas", 9) if sys.platform == "win32" else ("Menlo", 9),
            state=tk.DISABLED,
        )
        self._log.grid(row=row, column=0, sticky=tk.NSEW)
        root_frame.rowconfigure(row, weight=1)
        row += 1

        # -- Status bar ---------------------------------------------------
        self._status = tk.StringVar(value="Ready")
        ttk.Label(root_frame, textvariable=self._status, foreground="gray").grid(
            row=row, column=0, sticky=tk.W, pady=(4, 0))

    # ------------------------------------------------------------------
    # Browse helpers
    # ------------------------------------------------------------------

    def _browse_input(self):
        if self.input_type.get() == "file":
            # Build filter list from supported extensions.
            patterns = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTS))
            path = filedialog.askopenfilename(
                title="Select image file",
                filetypes=[
                    ("Image files", patterns),
                    ("JPEG", "*.jpg *.jpeg"),
                    ("PNG",  "*.png"),
                    ("HEIC / HEIF (iPhone)", "*.heic *.heif"),
                    ("All files", "*.*"),
                ],
            )
        else:
            path = filedialog.askdirectory(title="Select folder containing images")
        if path:
            self.input_path.set(path)

    def _browse_input_update(self):
        # Clear the input field when switching type so the stale path doesn't confuse things
        self.input_path.set("")

    def _browse_output(self):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.output_path.set(path)

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def _on_convert_cancel(self):
        """Dispatch to start or cancel depending on current state."""
        if self._worker and self._worker.is_alive():
            self._cancel_conversion()
        else:
            self._start_conversion()

    def _cancel_conversion(self):
        self._cancel_event.set()
        self._convert_btn.config(state=tk.DISABLED)
        self._set_status("Cancelling...")
        self._append_log("Cancelling after current image...\n")

    def _start_conversion(self):
        inp = self.input_path.get().strip()
        if not inp:
            self._set_status("Please select an input image or folder.", error=True)
            return

        input_path = Path(inp)
        if not input_path.exists():
            self._set_status(f"Path not found: {inp}", error=True)
            return

        out = self.output_path.get().strip()
        output_dir = Path(out) if out else None

        images = collect_images(input_path, self.recursive.get())
        if not images:
            self._set_status("No supported image files found.", error=True)
            return

        self._cancel_event.clear()
        self._convert_btn.config(text="Cancel")
        self._progress.start(12)
        self._set_status(f"Converting {len(images)} image(s)...")

        self._worker = threading.Thread(
            target=self._run_conversion,
            args=(images, input_path, output_dir),
            daemon=True,
        )
        self._worker.start()

    def _run_conversion(self, images, input_path, output_dir):
        old_stdout = sys.stdout
        sys.stdout = _QueueStream(self._log_queue)

        model   = self.model_var.get().strip() or DEFAULT_MODEL
        url     = self.url_var.get().strip()   or DEFAULT_OLLAMA
        num_ctx = CTX_LABEL_TO_VALUE.get(self.ctx_var.get(), NUM_CTX)

        ok = failed = 0
        cancelled = False
        try:
            self._log_queue.put(
                f"Found {len(images)} image(s).  Model: {model}  "
                f"Context: {num_ctx} tokens\n"
            )
            for i, img in enumerate(images, 1):
                if self._cancel_event.is_set():
                    cancelled = True
                    break
                dest = resolve_output(img, input_path, output_dir)
                self._log_queue.put(f"\n[{i}/{len(images)}] {img}\n")
                try:
                    convert_image(img, dest, model, url,
                                  cancel_event=self._cancel_event,
                                  num_ctx=num_ctx)
                    self._log_queue.put(f"  -> {dest}\n")
                    ok += 1
                except CancelledError:
                    cancelled = True
                    break
                except Exception as exc:
                    self._log_queue.put(f"  FAILED: {exc}\n")
                    self._log_queue.put(("__error__", str(exc)))
                    failed += 1

            if cancelled:
                self._log_queue.put(f"\nCancelled. {ok} completed before cancellation.\n")
                self._log_queue.put(("__status__", "Cancelled by user.", False))
            else:
                self._log_queue.put(f"\nDone. {ok} succeeded, {failed} failed.\n")
                self._log_queue.put(("__status__", f"Done. {ok} succeeded, {failed} failed.", False))
        finally:
            sys.stdout = old_stdout
            self._log_queue.put("__done__")

    # ------------------------------------------------------------------
    # Log polling (runs on the main thread via after())
    # ------------------------------------------------------------------

    def _poll_log(self):
        try:
            while True:
                item = self._log_queue.get_nowait()
                if item == "__done__":
                    self._convert_btn.config(text="Convert", state=tk.NORMAL)
                    self._progress.stop()
                elif isinstance(item, tuple) and item[0] == "__status__":
                    _, msg, err = item
                    self._set_status(msg, error=err)
                elif isinstance(item, tuple) and item[0] == "__error__":
                    messagebox.showerror("Conversion error", item[1])
                else:
                    self._append_log(item)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    def _append_log(self, text: str):
        self._log.config(state=tk.NORMAL)
        self._log.insert(tk.END, text)
        self._log.see(tk.END)
        self._log.config(state=tk.DISABLED)

    def _clear_log(self):
        self._log.config(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.config(state=tk.DISABLED)

    def _set_status(self, msg: str, error: bool = False):
        self._status.set(msg)

    def _bring_to_front(self):
        self.lift()
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))
        self.focus_force()


# ---------------------------------------------------------------------------

def main():
    try:
        app = App()
        app.mainloop()
    except Exception as exc:
        # Last-resort error display if the window itself fails to build
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Startup error", str(exc))
        except Exception:
            print(f"Fatal error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
