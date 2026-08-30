#!/usr/bin/env python3
"""
gui.py — Tkinter GUI for img_to_svg.py
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


def _load_models() -> list:
    """Load available model names from models.csv."""
    models = []
    if MODELS_CSV.exists():
        with open(MODELS_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = row.get("model_name", "").strip()
                if name:
                    models.append(name)
    return models


def _check_dependencies() -> None:
    """Check each required package and show a friendly error if any are missing."""
    REQUIRED = {
        "PIL":      "Pillow",
        "requests": "requests",
        "numpy":    "numpy",
        "cv2":      "opencv-python",
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
            "The following packages are not installed:\n\n  {}\n\n"
            "Run  start.bat  to install them automatically,\n"
            "or open a terminal and run:\n\n"
            "  pip install -r requirements.txt".format(pkg_list),
        )
        root.destroy()
        sys.exit(1)

_check_dependencies()

# All required dependencies present — safe to import now.
from img_to_svg import (  # noqa: E402
    CancelledError,
    DEFAULT_LM_STUDIO,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA,
    NUM_CTX,
    SUPPORTED_EXTS,
    TraceOptions,
    collect_images,
    convert_image,
    detect_lm_studio_models,
    resolve_output,
)

# Context-size presets shown in the GUI dropdown. Each label maps to a
# num_ctx token count; the rough VRAM hint is *additional* memory on top
# of the base model weights, and varies significantly by model.
CTX_PRESETS = [
    ("Low  (4096 tokens, ~1 GB extra VRAM)",       4096),
    ("Medium (8192 tokens, ~2 GB extra VRAM)",     8192),
    ("High (16384 tokens, ~4 GB extra VRAM)",      16384),
    ("Very High (32768 tokens, ~8 GB extra VRAM)", 32768),
]
CTX_LABEL_TO_VALUE = {label: tokens for label, tokens in CTX_PRESETS}
CTX_VALUE_TO_LABEL = {tokens: label for label, tokens in CTX_PRESETS}

# Detail presets map one dropdown choice onto the three curve-shaping options.
# In assist mode these are only the starting point — the model may override
# them after it has looked at the image.
DETAIL_PRESETS = [
    ("Crisp - logos, icons, pixel art",   dict(tolerance=0.8, smooth=0.0, corner_angle=30.0)),
    ("Balanced - most artwork",           dict(tolerance=1.2, smooth=0.6, corner_angle=60.0)),
    ("Smooth - organic, hand-drawn art",  dict(tolerance=2.0, smooth=1.0, corner_angle=85.0)),
]
DETAIL_LABEL_TO_OPTS = {label: opts for label, opts in DETAIL_PRESETS}

MODE_PRESETS = [
    ("Assist - model picks the settings", "assist"),
    ("Trace only - no AI",                "trace"),
    ("Generate - model writes the SVG",   "generate"),
]
MODE_LABEL_TO_VALUE = {label: value for label, value in MODE_PRESETS}


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

        self.title("Image to SVG")
        self.geometry("760x680")
        self.minsize(680, 620)
        self.resizable(True, True)
        # Bring the window to the front on Windows
        self.after(50, self._bring_to_front)

        self._log_queue = queue.Queue()
        self._worker = None
        self._cancel_event = threading.Event()

        # --- backend detection: prefer LM Studio if it has a loaded model ---
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
        defaults = TraceOptions()
        self.input_path  = tk.StringVar()
        self.output_path = tk.StringVar()
        self.model_var   = tk.StringVar(value=default_model)
        self.url_var     = tk.StringVar(value=default_url)
        self.recursive   = tk.BooleanVar(value=False)
        self.input_type  = tk.StringVar(value="file")  # "file" | "folder"
        self.mode_var    = tk.StringVar(value=MODE_PRESETS[0][0])
        self.detail_var  = tk.StringVar(value=DETAIL_PRESETS[1][0])
        self.ctx_var     = tk.StringVar(
            value=CTX_VALUE_TO_LABEL.get(NUM_CTX, CTX_PRESETS[1][0]))
        self.colors_var  = tk.IntVar(value=defaults.colors)
        self.alpha_var   = tk.IntVar(value=defaults.alpha_threshold)
        self.refine_var  = tk.IntVar(value=0)
        self.auto_key    = tk.BooleanVar(value=False)
        self.want_prev   = tk.BooleanVar(value=False)

        self._build_ui()
        self._poll_log()

        # Announce which backend we're talking to so the user knows what's going on.
        if self._backend == "lmstudio":
            self._append_log(
                "LM Studio detected at {} - using loaded model: {}\n"
                "(To use Ollama instead, stop the LM Studio server and relaunch.)\n\n"
                .format(DEFAULT_LM_STUDIO, default_model))
        else:
            self._append_log(
                "Using Ollama at {}. (LM Studio not detected - start LM Studio's "
                "local server with a loaded model to use it instead.)\n\n"
                .format(DEFAULT_OLLAMA))

        self._append_log(
            "Tip: images with a transparent background trace best. For an opaque\n"
            "image, tick 'Remove flat background' or cut it out first with the\n"
            "BackgroundRemover tool.\n\n")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
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

        opt_frame = ttk.Frame(root_frame)
        opt_frame.grid(row=row, column=0, sticky=tk.W, pady=(2, 8))
        ttk.Radiobutton(opt_frame, text="Single image", variable=self.input_type,
                        value="file", command=self._browse_input_update).pack(side=tk.LEFT)
        ttk.Radiobutton(opt_frame, text="Folder", variable=self.input_type,
                        value="folder", command=self._browse_input_update).pack(
                            side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(opt_frame, text="Recurse subdirectories",
                        variable=self.recursive).pack(side=tk.LEFT, padx=(16, 0))
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

        # -- Model settings ----------------------------------------------
        ttk.Label(root_frame, text="Model", font=("", 9, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=(0, 2))
        row += 1

        mf = ttk.Frame(root_frame)
        mf.grid(row=row, column=0, sticky=tk.EW, pady=(0, 12))
        mf.columnconfigure(1, weight=1)
        mf.columnconfigure(3, weight=1)

        ttk.Label(mf, text="Mode:").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        self._mode_combo = ttk.Combobox(
            mf, textvariable=self.mode_var, state="readonly", width=30,
            values=[label for label, _ in MODE_PRESETS])
        self._mode_combo.grid(row=0, column=1, sticky=tk.EW, padx=(0, 16))
        self._mode_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_mode())

        ttk.Label(mf, text="Model:").grid(row=0, column=2, sticky=tk.W, padx=(0, 6))
        self._model_combo = ttk.Combobox(
            mf, textvariable=self.model_var, width=22,
            values=self._available_models, state="readonly")
        self._model_combo.grid(row=0, column=3, sticky=tk.EW)

        ttk.Label(mf, text="VRAM / context:").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 6), pady=(6, 0))
        self._ctx_combo = ttk.Combobox(
            mf, textvariable=self.ctx_var, state="readonly", width=30,
            values=[label for label, _ in CTX_PRESETS])
        self._ctx_combo.grid(row=1, column=1, sticky=tk.EW, padx=(0, 16), pady=(6, 0))

        ttk.Label(mf, text="Refine passes:").grid(
            row=1, column=2, sticky=tk.W, padx=(0, 6), pady=(6, 0))
        self._refine_spin = ttk.Spinbox(mf, from_=0, to=3, width=6,
                                        textvariable=self.refine_var)
        self._refine_spin.grid(row=1, column=3, sticky=tk.W, pady=(6, 0))

        url_label = "LM Studio URL:" if self._backend == "lmstudio" else "Ollama URL:"
        ttk.Label(mf, text=url_label).grid(
            row=2, column=0, sticky=tk.W, padx=(0, 6), pady=(6, 0))
        ttk.Entry(mf, textvariable=self.url_var).grid(
            row=2, column=1, columnspan=3, sticky=tk.EW, pady=(6, 0))
        row += 1

        # -- Trace settings ----------------------------------------------
        ttk.Label(root_frame, text="Trace", font=("", 9, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=(0, 2))
        row += 1

        tf = ttk.Frame(root_frame)
        tf.grid(row=row, column=0, sticky=tk.EW, pady=(0, 12))
        tf.columnconfigure(1, weight=1)
        tf.columnconfigure(3, weight=1)

        ttk.Label(tf, text="Detail:").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        self._detail_combo = ttk.Combobox(
            tf, textvariable=self.detail_var, state="readonly", width=30,
            values=[label for label, _ in DETAIL_PRESETS])
        self._detail_combo.grid(row=0, column=1, sticky=tk.EW, padx=(0, 16))

        ttk.Label(tf, text="Colours:").grid(row=0, column=2, sticky=tk.W, padx=(0, 6))
        ttk.Spinbox(tf, from_=1, to=32, width=6, textvariable=self.colors_var).grid(
            row=0, column=3, sticky=tk.W)

        ttk.Label(tf, text="Alpha cutoff:").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 6), pady=(6, 0))
        ttk.Spinbox(tf, from_=1, to=254, width=6, textvariable=self.alpha_var).grid(
            row=1, column=1, sticky=tk.W, pady=(6, 0))

        ttk.Checkbutton(tf, text="Remove flat background (no alpha channel)",
                        variable=self.auto_key).grid(
                            row=2, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))
        ttk.Checkbutton(tf, text="Also write a PNG preview of the trace",
                        variable=self.want_prev).grid(
                            row=3, column=0, columnspan=3, sticky=tk.W)
        row += 1

        # -- Buttons ------------------------------------------------------
        btn_frame = ttk.Frame(root_frame)
        btn_frame.grid(row=row, column=0, sticky=tk.EW, pady=(0, 8))
        btn_frame.columnconfigure(0, weight=1)

        self._convert_btn = ttk.Button(btn_frame, text="Convert",
                                       command=self._on_convert_cancel)
        self._convert_btn.grid(row=0, column=0, sticky=tk.EW, padx=(0, 6), ipady=4)
        ttk.Button(btn_frame, text="Clear log", command=self._clear_log).grid(
            row=0, column=1, ipady=4)
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
            root_frame, height=12, wrap=tk.WORD,
            font=("Consolas", 9) if sys.platform == "win32" else ("Menlo", 9),
            state=tk.DISABLED)
        self._log.grid(row=row, column=0, sticky=tk.NSEW)
        root_frame.rowconfigure(row, weight=1)
        row += 1

        # -- Status bar ---------------------------------------------------
        self._status = tk.StringVar(value="Ready")
        ttk.Label(root_frame, textvariable=self._status, foreground="gray").grid(
            row=row, column=0, sticky=tk.W, pady=(4, 0))

        self._sync_mode()

    def _sync_mode(self):
        """Grey out the controls that do not apply to the selected mode."""
        mode = MODE_LABEL_TO_VALUE.get(self.mode_var.get(), "assist")
        ai = mode != "trace"
        for widget in (self._model_combo, self._ctx_combo):
            widget.config(state="readonly" if ai else tk.DISABLED)
        self._refine_spin.config(state=tk.NORMAL if mode == "assist" else tk.DISABLED)

    # ------------------------------------------------------------------
    # Browse helpers
    # ------------------------------------------------------------------

    def _browse_input(self):
        if self.input_type.get() == "file":
            patterns = " ".join("*" + e for e in sorted(SUPPORTED_EXTS))
            path = filedialog.askopenfilename(
                title="Select image file",
                filetypes=[
                    ("Image files", patterns),
                    ("PNG (transparent)", "*.png"),
                    ("WebP", "*.webp"),
                    ("JPEG", "*.jpg *.jpeg"),
                    ("All files", "*.*"),
                ])
        else:
            path = filedialog.askdirectory(title="Select folder containing images")
        if path:
            self.input_path.set(path)

    def _browse_input_update(self):
        # Clear the field when switching type so a stale path cannot confuse things.
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

    def _collect_options(self) -> TraceOptions:
        preset = DETAIL_LABEL_TO_OPTS.get(self.detail_var.get(), {})
        try:
            colors = int(self.colors_var.get())
        except tk.TclError:
            colors = TraceOptions().colors
        try:
            alpha = int(self.alpha_var.get())
        except tk.TclError:
            alpha = TraceOptions().alpha_threshold
        return TraceOptions(
            colors=colors,
            alpha_threshold=alpha,
            auto_key=self.auto_key.get(),
            **preset,
        ).clamped()

    def _start_conversion(self):
        inp = self.input_path.get().strip()
        if not inp:
            self._set_status("Please select an input image or folder.", error=True)
            return

        input_path = Path(inp)
        if not input_path.exists():
            self._set_status("Path not found: {}".format(inp), error=True)
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
        self._set_status("Converting {} image(s)...".format(len(images)))

        self._worker = threading.Thread(
            target=self._run_conversion,
            args=(images, input_path, output_dir),
            daemon=True)
        self._worker.start()

    def _run_conversion(self, images, input_path, output_dir):
        old_stdout = sys.stdout
        sys.stdout = _QueueStream(self._log_queue)

        mode    = MODE_LABEL_TO_VALUE.get(self.mode_var.get(), "assist")
        model   = self.model_var.get().strip() or DEFAULT_MODEL
        url     = self.url_var.get().strip()   or DEFAULT_OLLAMA
        num_ctx = CTX_LABEL_TO_VALUE.get(self.ctx_var.get(), NUM_CTX)
        opts    = self._collect_options()
        try:
            refine = max(0, int(self.refine_var.get())) if mode == "assist" else 0
        except tk.TclError:
            refine = 0

        ok = failed = 0
        cancelled = False
        try:
            if mode == "trace":
                self._log_queue.put(
                    "Found {} image(s).  Mode: trace (no AI)\n".format(len(images)))
            else:
                self._log_queue.put(
                    "Found {} image(s).  Mode: {}  Model: {}  Context: {} tokens\n"
                    .format(len(images), mode, model, num_ctx))

            for i, img in enumerate(images, 1):
                if self._cancel_event.is_set():
                    cancelled = True
                    break
                dest = resolve_output(img, input_path, output_dir)
                self._log_queue.put("\n[{}/{}] {}\n".format(i, len(images), img))
                try:
                    stats = convert_image(
                        img, dest, model, url, mode=mode, opts=opts,
                        refine=refine, write_preview=self.want_prev.get(),
                        cancel_event=self._cancel_event, num_ctx=num_ctx)
                    self._log_queue.put("  -> {}  ({:.1f} KB, {} paths)\n".format(
                        dest, stats["bytes"] / 1024.0, stats["paths"]))
                    ok += 1
                except CancelledError:
                    cancelled = True
                    break
                except Exception as exc:
                    self._log_queue.put("  FAILED: {}\n".format(exc))
                    self._log_queue.put(("__error__", str(exc)))
                    failed += 1

            if cancelled:
                self._log_queue.put(
                    "\nCancelled. {} completed before cancellation.\n".format(ok))
                self._log_queue.put(("__status__", "Cancelled by user.", False))
            else:
                self._log_queue.put(
                    "\nDone. {} succeeded, {} failed.\n".format(ok, failed))
                self._log_queue.put(
                    ("__status__", "Done. {} succeeded, {} failed.".format(ok, failed), False))
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
            print("Fatal error: {}".format(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
