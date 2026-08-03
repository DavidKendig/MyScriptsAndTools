"""Tkinter front end for AutoTranslate."""

import os
import queue
import subprocess
import sys
import threading
import webbrowser

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__, backends, config, imageutil
from .worker import TranslationJob

PAD = 8


class App(ttk.Frame):
    def __init__(self, root):
        ttk.Frame.__init__(self, root, padding=PAD)
        self.root = root
        self.pack(fill="both", expand=True)

        self.cfg = config.load()
        self.events = queue.Queue()
        self.job = None
        self.images = []
        self.caps = set()
        self.caps_known = False

        self._build_vars()
        self._build_ui()
        self._on_backend_change()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._pump_events)

        if not imageutil.HAVE_PILLOW:
            self.log(
                "NOTE  Pillow is not installed - images are uploaded at full size. "
                "Run the installer, or: pip install Pillow"
            )
        self.log("Ready. Pick a folder, refresh the model list, then press Start.")
        if self.folder.get():
            self.scan_folder(quiet=True)

    # ------------------------------------------------------------------
    # variables
    # ------------------------------------------------------------------
    def _build_vars(self):
        c = self.cfg
        self.backend = tk.StringVar(value=c["backend"])
        self.host = tk.StringVar()
        self.api_key = tk.StringVar(value=c["api_key"])
        self.model = tk.StringVar(value=c["model"])
        self.language = tk.StringVar(value=c["language"])
        self.folder = tk.StringVar(value=c["folder"])
        self.output_folder = tk.StringVar(value=c["output_folder"])
        self.recursive = tk.BooleanVar(value=c["recursive"])
        self.thinking = tk.BooleanVar(value=c["thinking"])
        self.include_prev_image = tk.BooleanVar(value=c["include_prev_image"])
        self.include_next_image = tk.BooleanVar(value=c["include_next_image"])
        self.include_prev_text = tk.BooleanVar(value=c["include_prev_text"])
        self.skip_existing = tk.BooleanVar(value=c["skip_existing"])
        self.max_image_px = tk.StringVar(value=str(c["max_image_px"]))
        self.jpeg_quality = tk.StringVar(value=str(c["jpeg_quality"]))
        self.temperature = tk.StringVar(value=str(c["temperature"]))
        self.num_ctx = tk.StringVar(value=str(c["num_ctx"]))
        self.timeout = tk.StringVar(value=str(c["timeout"]))
        self.retries = tk.StringVar(value=str(c["retries"]))
        self.prev_text_chars = tk.StringVar(value=str(c["prev_text_chars"]))
        self.status = tk.StringVar(value="Idle")
        self.caps_note = tk.StringVar(value="")
        self.count_note = tk.StringVar(value="No folder scanned yet")

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build_ui(self):
        notebook = ttk.Notebook(self)
        notebook.pack(fill="x")

        main = ttk.Frame(notebook, padding=PAD)
        advanced = ttk.Frame(notebook, padding=PAD)
        prompt = ttk.Frame(notebook, padding=PAD)
        notebook.add(main, text="Main")
        notebook.add(advanced, text="Advanced")
        notebook.add(prompt, text="Prompt")

        self._build_main_tab(main)
        self._build_advanced_tab(advanced)
        self._build_prompt_tab(prompt)
        self._build_footer()

    def _build_main_tab(self, parent):
        parent.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(parent, text="Image folder").grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.folder).grid(
            row=row, column=1, sticky="ew", padx=4
        )
        ttk.Button(parent, text="Browse...", command=self.pick_folder).grid(
            row=row, column=2
        )
        ttk.Button(parent, text="Scan", command=self.scan_folder).grid(
            row=row, column=3, padx=(4, 0)
        )
        row += 1

        ttk.Label(parent, textvariable=self.count_note, foreground="#4a6").grid(
            row=row, column=1, sticky="w", padx=4, pady=(0, 4)
        )
        ttk.Checkbutton(
            parent,
            text="Include subfolders",
            variable=self.recursive,
            command=lambda: self.scan_folder(quiet=True),
        ).grid(row=row, column=2, columnspan=2, sticky="w")
        row += 1

        ttk.Label(parent, text="Output folder").grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.output_folder).grid(
            row=row, column=1, sticky="ew", padx=4
        )
        ttk.Button(parent, text="Browse...", command=self.pick_output).grid(
            row=row, column=2
        )
        ttk.Button(parent, text="Clear", command=lambda: self.output_folder.set("")).grid(
            row=row, column=3, padx=(4, 0)
        )
        row += 1

        ttk.Label(
            parent,
            text="Leave the output folder empty to write each .txt next to its image.",
            foreground="#888",
        ).grid(row=row, column=1, columnspan=3, sticky="w", padx=4, pady=(0, 8))
        row += 1

        ttk.Separator(parent).grid(row=row, column=0, columnspan=4, sticky="ew", pady=6)
        row += 1

        ttk.Label(parent, text="Server").grid(row=row, column=0, sticky="w")
        combo = ttk.Combobox(
            parent,
            textvariable=self.backend,
            values=list(backends.BACKENDS.keys()),
            state="readonly",
            width=14,
        )
        combo.grid(row=row, column=1, sticky="w", padx=4)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._on_backend_change())
        row += 1

        ttk.Label(parent, text="Host URL").grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.host).grid(
            row=row, column=1, sticky="ew", padx=4
        )
        ttk.Button(parent, text="Test", command=self.test_connection).grid(
            row=row, column=2
        )
        row += 1

        ttk.Label(parent, text="Model").grid(row=row, column=0, sticky="w")
        self.model_combo = ttk.Combobox(parent, textvariable=self.model)
        self.model_combo.grid(row=row, column=1, sticky="ew", padx=4)
        self.model_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self.refresh_capabilities()
        )
        ttk.Button(parent, text="Refresh", command=self.refresh_models).grid(
            row=row, column=2
        )
        row += 1

        self.think_check = ttk.Checkbutton(
            parent, text="Thinking / reasoning mode", variable=self.thinking
        )
        self.think_check.grid(row=row, column=1, sticky="w", padx=4)
        ttk.Label(parent, textvariable=self.caps_note, foreground="#888").grid(
            row=row, column=2, columnspan=2, sticky="w"
        )
        row += 1

        ttk.Label(parent, text="Translate into").grid(row=row, column=0, sticky="w")
        ttk.Combobox(
            parent,
            textvariable=self.language,
            values=[
                "English", "Spanish", "French", "German", "Italian",
                "Portuguese", "Japanese", "Korean", "Chinese (Simplified)",
                "Russian", "Arabic",
            ],
        ).grid(row=row, column=1, sticky="ew", padx=4, pady=(6, 0))

    def _build_advanced_tab(self, parent):
        parent.columnconfigure(1, weight=1)

        ctx = ttk.LabelFrame(parent, text="Context sent with each page", padding=PAD)
        ctx.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Checkbutton(
            ctx, text="Include the previous image", variable=self.include_prev_image
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            ctx, text="Include the following image", variable=self.include_next_image
        ).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(
            ctx,
            text="Include the previous page's translated text",
            variable=self.include_prev_text,
        ).grid(row=2, column=0, sticky="w")
        ttk.Label(
            ctx,
            text="Turn the neighbour images off if your model only accepts one image "
            "at a time\n(most llava builds) or if you are running short on VRAM.",
            foreground="#888",
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))

        grid = ttk.Frame(parent, padding=(0, PAD))
        grid.grid(row=1, column=0, columnspan=2, sticky="ew")

        fields = [
            ("Previous-text characters", self.prev_text_chars),
            ("Max image size (px, 0 = original)", self.max_image_px),
            ("JPEG quality", self.jpeg_quality),
            ("Temperature", self.temperature),
            ("Context window (num_ctx, Ollama)", self.num_ctx),
            ("Request timeout (seconds)", self.timeout),
            ("Retries per image", self.retries),
            ("API key (optional)", self.api_key),
        ]
        for index, (label, var) in enumerate(fields):
            ttk.Label(grid, text=label).grid(row=index, column=0, sticky="w", pady=2)
            ttk.Entry(grid, textvariable=var, width=18).grid(
                row=index, column=1, sticky="w", padx=6
            )

        ttk.Checkbutton(
            parent,
            text="Skip images that already have a .txt file",
            variable=self.skip_existing,
        ).grid(row=2, column=0, sticky="w")

    def _build_prompt_tab(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        ttk.Label(
            parent,
            text="System prompt - {language} is replaced with your chosen language.",
        ).grid(row=0, column=0, sticky="w")
        self.prompt_text = tk.Text(parent, height=14, wrap="word", undo=True)
        self.prompt_text.grid(row=1, column=0, sticky="nsew", pady=4)
        self.prompt_text.insert("1.0", self.cfg["prompt"])
        ttk.Button(
            parent, text="Restore default prompt", command=self.reset_prompt
        ).grid(row=2, column=0, sticky="w")

    def _build_footer(self):
        bar = ttk.Frame(self, padding=(0, PAD))
        bar.pack(fill="x")
        self.start_btn = ttk.Button(bar, text="Start", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        ttk.Button(bar, text="Open output", command=self.open_output).pack(side="left")
        ttk.Button(bar, text="Clear log", command=self.clear_log).pack(side="left", padx=4)
        ttk.Label(bar, text="v" + __version__, foreground="#888").pack(side="right")

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(self, textvariable=self.status).pack(anchor="w", pady=(2, 4))

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True)
        self.log_text = tk.Text(wrap, height=14, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(wrap, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

    # ------------------------------------------------------------------
    # small actions
    # ------------------------------------------------------------------
    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def reset_prompt(self):
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", config.DEFAULT_PROMPT)

    def pick_folder(self):
        chosen = filedialog.askdirectory(
            title="Choose the folder of images", initialdir=self.folder.get() or None
        )
        if chosen:
            self.folder.set(chosen)
            self.scan_folder()

    def pick_output(self):
        chosen = filedialog.askdirectory(
            title="Choose where the .txt files go",
            initialdir=self.output_folder.get() or self.folder.get() or None,
        )
        if chosen:
            self.output_folder.set(chosen)

    def open_output(self):
        target = self.output_folder.get().strip() or self.folder.get().strip()
        if not target or not os.path.isdir(target):
            messagebox.showinfo(APP_NAME, "No output folder to open yet.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)  # noqa: S606 - intended shell open
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception:
            webbrowser.open("file://" + target)

    @staticmethod
    def _url_key(backend_name):
        return "ollama_url" if backend_name == "Ollama" else "lmstudio_url"

    def _on_backend_change(self):
        name = self.backend.get()
        # Remember whatever the user typed for the backend we are leaving, so
        # switching back and forth does not throw away a custom host.
        previous = getattr(self, "_current_backend", None)
        if previous and previous != name and self.host.get().strip():
            self.cfg[self._url_key(previous)] = self.host.get().strip()
        self._current_backend = name

        self.host.set(self.cfg[self._url_key(name)])
        self.caps = set()
        self.caps_known = False
        self.caps_note.set(
            "Ollama reports whether a model can think."
            if name == "Ollama"
            else "LM Studio does not advertise this - the toggle is sent anyway."
        )

    def scan_folder(self, quiet=False):
        folder = self.folder.get().strip()
        if not folder or not os.path.isdir(folder):
            self.count_note.set("Folder not found")
            self.images = []
            return
        self.images = imageutil.find_images(folder, self.recursive.get())
        self.count_note.set("{} image(s) found".format(len(self.images)))
        if not quiet:
            self.log("Scanned {} - {} image(s).".format(folder, len(self.images)))

    # ------------------------------------------------------------------
    # server interaction (threaded so the UI stays alive)
    # ------------------------------------------------------------------
    def _make_backend(self):
        return backends.build(
            self.backend.get(),
            self.host.get(),
            api_key=self.api_key.get(),
            timeout=self._as_int(self.timeout, 600),
        )

    def test_connection(self):
        self.refresh_models(announce=True)

    def refresh_models(self, announce=False):
        backend = self._make_backend()
        self.status.set("Contacting {}...".format(backend.base_url))

        def work():
            try:
                models = backend.list_models()
                self.events.put(("models", models, announce))
            except Exception as exc:
                self.events.put(("error", "Model list failed: {}".format(exc)))

        threading.Thread(target=work, daemon=True).start()

    def refresh_capabilities(self):
        if self.backend.get() != "Ollama" or not self.model.get():
            return
        backend = self._make_backend()
        model = self.model.get()

        def work():
            caps = backend.capabilities(model)
            self.events.put(("caps", model, caps))

        threading.Thread(target=work, daemon=True).start()

    def _apply_caps(self, model, caps):
        self.caps = caps
        self.caps_known = bool(caps)
        if not caps:
            self.caps_note.set("Capabilities unknown")
            self.think_check.state(["!disabled"])
            return
        notes = []
        if "vision" in caps:
            notes.append("vision OK")
        else:
            notes.append("NO VISION - this model cannot read images")
            self.log(
                "WARN  {} does not report vision support. Pick a vision model "
                "(llava, llama3.2-vision, qwen2.5vl, minicpm-v, gemma3).".format(model)
            )
        if "thinking" in caps:
            notes.append("thinking available")
            self.think_check.state(["!disabled"])
        else:
            notes.append("no thinking mode")
            self.thinking.set(False)
            self.think_check.state(["disabled"])
        self.caps_note.set(", ".join(notes))

    # ------------------------------------------------------------------
    # job control
    # ------------------------------------------------------------------
    @staticmethod
    def _as_int(var, fallback):
        try:
            return int(float(var.get()))
        except (ValueError, tk.TclError):
            return fallback

    @staticmethod
    def _as_float(var, fallback):
        try:
            return float(var.get())
        except (ValueError, tk.TclError):
            return fallback

    def collect_config(self):
        cfg = dict(self.cfg)
        name = self.backend.get()
        cfg.update(
            {
                "backend": name,
                "api_key": self.api_key.get().strip(),
                "model": self.model.get().strip(),
                "language": self.language.get().strip() or "English",
                "folder": self.folder.get().strip(),
                "output_folder": self.output_folder.get().strip(),
                "recursive": self.recursive.get(),
                "thinking": self.thinking.get(),
                "include_prev_image": self.include_prev_image.get(),
                "include_next_image": self.include_next_image.get(),
                "include_prev_text": self.include_prev_text.get(),
                "skip_existing": self.skip_existing.get(),
                "prompt": self.prompt_text.get("1.0", "end").strip(),
                "prev_text_chars": self._as_int(self.prev_text_chars, 1200),
                "max_image_px": self._as_int(self.max_image_px, 1400),
                "jpeg_quality": self._as_int(self.jpeg_quality, 88),
                "temperature": self._as_float(self.temperature, 0.2),
                "num_ctx": self._as_int(self.num_ctx, 8192),
                "timeout": self._as_int(self.timeout, 600),
                "retries": self._as_int(self.retries, 2),
            }
        )
        cfg[self._url_key(name)] = self.host.get().strip()
        # Only Ollama tells us for sure; elsewhere we send the flag and hope.
        cfg["thinking_supported"] = (
            ("thinking" in self.caps) if self.caps_known else True
        )
        return cfg

    def start(self):
        if self.job and self.job.is_alive():
            return
        self.scan_folder(quiet=True)
        cfg = self.collect_config()

        if not cfg["folder"] or not os.path.isdir(cfg["folder"]):
            messagebox.showerror(APP_NAME, "Choose a valid image folder first.")
            return
        if not self.images:
            messagebox.showerror(APP_NAME, "No images found in that folder.")
            return
        if not cfg["model"]:
            messagebox.showerror(
                APP_NAME, "Choose a model. Press Refresh to list what your server has."
            )
            return

        self.cfg = cfg
        config.save(cfg)

        self.progress.configure(maximum=len(self.images), value=0)
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.log("")
        self.log(
            "=== {} image(s) via {} / {}{} ===".format(
                len(self.images),
                cfg["backend"],
                cfg["model"],
                " (thinking on)" if cfg["thinking"] else "",
            )
        )
        self.job = TranslationJob(cfg, list(self.images), self.events)
        self.job.start()

    def stop(self):
        if self.job and self.job.is_alive():
            self.job.stop()
            self.status.set("Stopping after the current page...")
            self.stop_btn.configure(state="disabled")

    # ------------------------------------------------------------------
    # event pump
    # ------------------------------------------------------------------
    def _pump_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind, payload = event[0], event[1:]

                if kind == "log":
                    self.log(payload[0])
                elif kind == "status":
                    self.status.set(payload[0])
                elif kind == "progress":
                    self.progress.configure(value=payload[0], maximum=max(1, payload[1]))
                elif kind == "wrote":
                    pass
                elif kind == "models":
                    models, announce = payload
                    self.model_combo.configure(values=models)
                    if models and self.model.get() not in models:
                        self.model.set(models[0])
                    self.status.set("{} model(s) available".format(len(models)))
                    if announce:
                        self.log(
                            "Connected. Models: "
                            + (", ".join(models) if models else "(none loaded)")
                        )
                    self.refresh_capabilities()
                elif kind == "caps":
                    self._apply_caps(payload[0], payload[1])
                elif kind == "error":
                    self.status.set("Error")
                    self.log("ERROR " + payload[0])
                elif kind == "finished":
                    done, skipped, failed, summary = payload
                    self.status.set(
                        "{} {} written, {} skipped, {} failed".format(
                            summary, done, skipped, failed
                        )
                    )
                    self.log("=== {} {} written, {} skipped, {} failed ===".format(
                        summary, done, skipped, failed))
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._pump_events)

    def on_close(self):
        if self.job and self.job.is_alive():
            if not messagebox.askokcancel(
                APP_NAME, "A translation is running. Stop it and quit?"
            ):
                return
            self.job.stop()
        try:
            config.save(self.collect_config())
        except Exception:
            pass
        self.root.destroy()


def main():
    if sys.platform.startswith("win"):
        try:  # Crisp text on high-DPI displays.
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    root.title("{} {}".format(APP_NAME, __version__))
    root.geometry("880x760")
    root.minsize(760, 640)
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
