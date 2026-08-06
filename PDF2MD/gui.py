#!/usr/bin/env python3
"""
gui.py — Tkinter GUI for pdf_to_markdown.py
"""

import csv
import queue
import sys
import threading
import time
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
        "fitz":     "pymupdf",
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

# All dependencies present — safe to import now.
from pdf_to_markdown import (  # noqa: E402
    CancelledError,
    DEFAULT_LM_STUDIO,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA,
    NUM_CTX,
    collect_pdfs,
    convert_pdf,
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
    def __init__(self, q: queue.Queue, mirror: list[str] | None = None):
        self._q = q
        self._mirror = mirror

    def write(self, text: str):
        if text:
            self._q.put(text)
            if self._mirror is not None:
                self._mirror.append(text)

    def flush(self):
        pass


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("PDF to Markdown")
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
        self.mode_var    = tk.StringVar(value="auto")
        self.columns_var = tk.StringVar(value="auto")
        self.start_page  = tk.IntVar(value=1)
        self.recursive   = tk.BooleanVar(value=False)
        self.input_type  = tk.StringVar(value="file")  # "file" | "folder"
        self.ctx_var     = tk.StringVar(
            value=CTX_VALUE_TO_LABEL.get(NUM_CTX, CTX_PRESETS[1][0])
        )

        self._build_ui()
        self._poll_log()

        # Announce which backend we're talking to.
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

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Root frame with padding
        root_frame = ttk.Frame(self, padding=12)
        root_frame.pack(fill=tk.BOTH, expand=True)
        root_frame.columnconfigure(0, weight=1)

        row = 0

        # ── Input ──────────────────────────────────────────────────────
        ttk.Label(root_frame, text="Input", font=("", 9, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=(0, 2))
        row += 1

        input_frame = ttk.Frame(root_frame)
        input_frame.grid(row=row, column=0, sticky=tk.EW)
        input_frame.columnconfigure(0, weight=1)

        self._input_entry = ttk.Entry(input_frame, textvariable=self.input_path)
        self._input_entry.grid(row=0, column=0, sticky=tk.EW, padx=(0, 4))

        ttk.Button(input_frame, text="Browse…", command=self._browse_input).grid(
            row=0, column=1)
        row += 1

        # Radio + recursive on one row
        opt_frame = ttk.Frame(root_frame)
        opt_frame.grid(row=row, column=0, sticky=tk.W, pady=(2, 8))

        ttk.Radiobutton(opt_frame, text="Single PDF", variable=self.input_type,
                        value="file",   command=self._browse_input_update).pack(side=tk.LEFT)
        ttk.Radiobutton(opt_frame, text="Folder",     variable=self.input_type,
                        value="folder", command=self._browse_input_update).pack(side=tk.LEFT, padx=(8, 0))
        self._recurse_cb = ttk.Checkbutton(opt_frame, text="Recurse subdirectories",
                                           variable=self.recursive)
        self._recurse_cb.pack(side=tk.LEFT, padx=(16, 0))
        row += 1

        # ── Output ─────────────────────────────────────────────────────
        ttk.Label(root_frame, text="Output folder", font=("", 9, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=(0, 2))
        row += 1

        out_frame = ttk.Frame(root_frame)
        out_frame.grid(row=row, column=0, sticky=tk.EW, pady=(0, 12))
        out_frame.columnconfigure(0, weight=1)

        ttk.Entry(out_frame, textvariable=self.output_path).grid(
            row=0, column=0, sticky=tk.EW, padx=(0, 4))
        ttk.Button(out_frame, text="Browse…", command=self._browse_output).grid(
            row=0, column=1)
        row += 1

        # ── Settings ───────────────────────────────────────────────────
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

        ttk.Label(settings_frame, text="Mode:").grid(row=0, column=2, sticky=tk.W, padx=(0, 6))
        ttk.Combobox(
            settings_frame, textvariable=self.mode_var, width=10,
            values=["auto", "text", "vision", "hybrid"], state="readonly",
        ).grid(row=0, column=3, sticky=tk.W, padx=(0, 16))

        ttk.Label(settings_frame, text="Start page:").grid(row=0, column=4, sticky=tk.W, padx=(0, 6))
        ttk.Spinbox(settings_frame, textvariable=self.start_page, from_=1, to=9999,
                     width=6).grid(row=0, column=5, sticky=tk.W)

<<<<<<< Updated upstream
        ttk.Label(settings_frame, text="Columns:").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 6), pady=(6, 0))
        ttk.Combobox(
            settings_frame, textvariable=self.columns_var, width=10,
            values=["auto", "1", "2", "3"], state="readonly",
        ).grid(row=1, column=1, sticky=tk.W, padx=(0, 16), pady=(6, 0))
        ttk.Label(
            settings_frame,
            text="Page layout — set it if you know it; improves reading order.",
            foreground="gray40",
        ).grid(row=1, column=2, columnspan=4, sticky=tk.W, pady=(6, 0))

        ttk.Label(settings_frame, text="Ollama URL:").grid(row=2, column=0, sticky=tk.W, padx=(0, 6), pady=(6, 0))
=======
        url_label = "LM Studio URL:" if self._backend == "lmstudio" else "Ollama URL:"
        ttk.Label(settings_frame, text=url_label).grid(row=1, column=0, sticky=tk.W, padx=(0, 6), pady=(6, 0))
>>>>>>> Stashed changes
        ttk.Entry(settings_frame, textvariable=self.url_var).grid(
            row=2, column=1, columnspan=5, sticky=tk.EW, pady=(6, 0))

        ttk.Label(settings_frame, text="VRAM / context:").grid(
            row=3, column=0, sticky=tk.W, padx=(0, 6), pady=(6, 0))
        self._ctx_combo = ttk.Combobox(
            settings_frame, textvariable=self.ctx_var,
            values=[label for label, _ in CTX_PRESETS],
            state="readonly",
        )
        self._ctx_combo.grid(
            row=3, column=1, columnspan=5, sticky=tk.EW, pady=(6, 0))
        row += 1

        # ── Buttons ────────────────────────────────────────────────────
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

        # ── Progress bar ───────────────────────────────────────────────
        self._progress = ttk.Progressbar(root_frame, mode="indeterminate")
        self._progress.grid(row=row, column=0, sticky=tk.EW, pady=(0, 6))
        row += 1

        # ── Log ────────────────────────────────────────────────────────
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

        # ── Status bar ─────────────────────────────────────────────────
        self._status = tk.StringVar(value="Ready")
        ttk.Label(root_frame, textvariable=self._status, foreground="gray").grid(
            row=row, column=0, sticky=tk.W, pady=(4, 0))

    # ------------------------------------------------------------------
    # Browse helpers
    # ------------------------------------------------------------------

    def _browse_input(self):
        if self.input_type.get() == "file":
            path = filedialog.askopenfilename(
                title="Select PDF file",
                filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
            )
        else:
            path = filedialog.askdirectory(title="Select folder containing PDFs")
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
        self._set_status("Cancelling…")
        self._append_log("Cancelling after current page…\n")

    def _start_conversion(self):
        inp = self.input_path.get().strip()
        if not inp:
            self._set_status("Please select an input PDF or folder.", error=True)
            return

        input_path = Path(inp)
        if not input_path.exists():
            self._set_status(f"Path not found: {inp}", error=True)
            return

        out = self.output_path.get().strip()
        output_dir = Path(out) if out else None

        pdfs = collect_pdfs(input_path, self.recursive.get())
        if not pdfs:
            self._set_status("No PDF files found.", error=True)
            return

        self._cancel_event.clear()
        self._convert_btn.config(text="Cancel")
        self._progress.start(12)
        self._set_status(f"Converting {len(pdfs)} PDF(s)…")

        self._worker = threading.Thread(
            target=self._run_conversion,
            args=(pdfs, input_path, output_dir),
            daemon=True,
        )
        self._worker.start()

    def _run_conversion(self, pdfs, input_path, output_dir):
        log_lines: list[str] = []

        def emit(msg: str) -> None:
            self._log_queue.put(msg)
            log_lines.append(msg)

        old_stdout = sys.stdout
        sys.stdout = _QueueStream(self._log_queue, mirror=log_lines)

        model      = self.model_var.get().strip() or DEFAULT_MODEL
        url        = self.url_var.get().strip()   or DEFAULT_OLLAMA
        mode       = self.mode_var.get()
        columns    = self.columns_var.get()
        start_page = self.start_page.get()
        num_ctx    = CTX_LABEL_TO_VALUE.get(self.ctx_var.get(), NUM_CTX)

        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        emit(
            f"=== PDF2MD log — started {started_at} ===\n"
            f"Input:   {input_path}\n"
            f"Output:  {output_dir if output_dir else '(same directory as each PDF)'}\n"
            f"Model:   {model}\n"
            f"URL:     {url}\n"
            f"Mode:    {mode}\n"
            f"Columns: {columns}\n"
            f"Context: {num_ctx} tokens\n"
            f"PDFs:    {len(pdfs)}\n\n"
        )

        ok = failed = 0
        cancelled = False
        try:
            for i, pdf in enumerate(pdfs, 1):
                if self._cancel_event.is_set():
                    cancelled = True
                    break
                dest = resolve_output(pdf, input_path, output_dir)
                emit(f"\n[{i}/{len(pdfs)}] {pdf}\n")
                try:
                    convert_pdf(pdf, dest, model, url, mode,
                                start_page=start_page,
                                cancel_event=self._cancel_event,
                                num_ctx=num_ctx,
                                columns=columns)
                    emit(f"  -> {dest}\n")
                    ok += 1
                except CancelledError:
                    cancelled = True
                    break
                except Exception as exc:
                    emit(f"  FAILED: {exc}\n")
                    self._log_queue.put(("__error__", str(exc)))
                    failed += 1

            if cancelled:
                emit(f"\nCancelled. {ok} completed before cancellation.\n")
                self._log_queue.put(("__status__", "Cancelled by user.", False))
            else:
                emit(f"\nDone. {ok} succeeded, {failed} failed.\n")
                self._log_queue.put(("__status__", f"Done. {ok} succeeded, {failed} failed.", False))
        finally:
            sys.stdout = old_stdout
            log_path = self._write_log_file(
                log_lines, input_path, output_dir, started_at,
            )
            if log_path is not None:
                self._log_queue.put(f"\nLog saved to: {log_path}\n")
            self._log_queue.put("__done__")

    def _write_log_file(
        self,
        log_lines: list[str],
        input_path: Path,
        output_dir: Path | None,
        started_at: str,
    ) -> Path | None:
        """Write the captured log to a timestamped .log file. Returns the path."""
        try:
            if output_dir is not None:
                target_dir = output_dir
            elif input_path.is_dir():
                target_dir = input_path
            else:
                target_dir = input_path.parent

            target_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S",
                                  time.strptime(started_at, "%Y-%m-%d %H:%M:%S"))
            log_path = target_dir / f"pdf2md_{stamp}.log"
            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(log_lines)
            return log_path
        except Exception as exc:
            self._log_queue.put(f"\n(Could not write log file: {exc})\n")
            return None

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
