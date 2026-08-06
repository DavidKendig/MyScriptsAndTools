#!/usr/bin/env python3
"""
gui.py — Simple tkinter front-end for bg_remover.py.

Pick an image (or a folder of images), enter the hex color to remove,
optionally set a tolerance, and click Remove Background. A transparent
PNG copy is written next to each source image (or into a chosen output
folder). Originals are never modified.
"""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from bg_remover import (SUPPORTED_EXTS, collect_images, parse_hex_color,
                        process_file)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Background Remover")
        self.resizable(False, False)

        self.target_var = tk.StringVar()
        self.color_var = tk.StringVar(value="#FFFFFF")
        self.tolerance_var = tk.IntVar(value=0)
        self.output_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready.")
        self.running = False

        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")

        # Input file / folder
        ttk.Label(frame, text="Image or folder:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.target_var, width=48).grid(
            row=0, column=1, columnspan=2, sticky="we", **pad)
        ttk.Button(frame, text="File...", command=self._pick_file).grid(
            row=0, column=3, **pad)
        ttk.Button(frame, text="Folder...", command=self._pick_folder).grid(
            row=0, column=4, **pad)

        # Color to remove
        ttk.Label(frame, text="Color to remove:").grid(row=1, column=0, sticky="w", **pad)
        entry = ttk.Entry(frame, textvariable=self.color_var, width=12)
        entry.grid(row=1, column=1, sticky="w", **pad)
        entry.bind("<KeyRelease>", lambda e: self._update_swatch())
        self.swatch = tk.Label(frame, width=4, relief="sunken", bg="#FFFFFF")
        self.swatch.grid(row=1, column=2, sticky="w", **pad)
        ttk.Button(frame, text="Pick...", command=self._pick_color).grid(
            row=1, column=3, **pad)
        ttk.Button(frame, text="White", command=self._set_white).grid(
            row=1, column=4, **pad)

        # Tolerance
        ttk.Label(frame, text="Tolerance (0 = exact):").grid(
            row=2, column=0, sticky="w", **pad)
        scale = ttk.Scale(frame, from_=0, to=64, orient="horizontal",
                          command=self._on_tolerance)
        scale.grid(row=2, column=1, columnspan=2, sticky="we", **pad)
        self.tol_label = ttk.Label(frame, text="0")
        self.tol_label.grid(row=2, column=3, sticky="w", **pad)

        # Output folder (optional)
        ttk.Label(frame, text="Output folder (optional):").grid(
            row=3, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.output_var, width=48).grid(
            row=3, column=1, columnspan=2, sticky="we", **pad)
        ttk.Button(frame, text="Browse...", command=self._pick_output).grid(
            row=3, column=3, **pad)

        # Run button + status
        self.run_btn = ttk.Button(frame, text="Remove Background",
                                  command=self._run)
        self.run_btn.grid(row=4, column=0, columnspan=5, sticky="we", **pad)
        ttk.Label(frame, textvariable=self.status_var, wraplength=520,
                  foreground="gray25").grid(
            row=5, column=0, columnspan=5, sticky="w", **pad)

    def _pick_file(self):
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTS))
        path = filedialog.askopenfilename(
            title="Choose an image",
            filetypes=[("Images", exts), ("All files", "*.*")])
        if path:
            self.target_var.set(path)

    def _pick_folder(self):
        path = filedialog.askdirectory(title="Choose a folder of images")
        if path:
            self.target_var.set(path)

    def _pick_output(self):
        path = filedialog.askdirectory(title="Choose an output folder")
        if path:
            self.output_var.set(path)

    def _pick_color(self):
        initial = self.color_var.get()
        try:
            parse_hex_color(initial)
        except ValueError:
            initial = "#FFFFFF"
        result = colorchooser.askcolor(color=initial, title="Color to remove")
        if result and result[1]:
            self.color_var.set(result[1].upper())
            self._update_swatch()

    def _set_white(self):
        self.color_var.set("#FFFFFF")
        self._update_swatch()

    def _update_swatch(self):
        try:
            r, g, b = parse_hex_color(self.color_var.get())
            self.swatch.config(bg=f"#{r:02x}{g:02x}{b:02x}")
        except ValueError:
            pass

    def _on_tolerance(self, value):
        self.tolerance_var.set(round(float(value)))
        self.tol_label.config(text=str(self.tolerance_var.get()))

    # ----------------------------------------------------------------- Run

    def _run(self):
        if self.running:
            return

        target = Path(self.target_var.get().strip('" '))
        if not self.target_var.get().strip() or not target.exists():
            messagebox.showerror("Background Remover",
                                 "Please choose an existing image file or folder.")
            return

        try:
            color = parse_hex_color(self.color_var.get())
        except ValueError as e:
            messagebox.showerror("Background Remover", str(e))
            return

        output = self.output_var.get().strip('" ')
        output_dir = Path(output) if output else None
        tolerance = self.tolerance_var.get()

        self.running = True
        self.run_btn.config(state="disabled")
        self.status_var.set("Working...")
        threading.Thread(target=self._worker,
                         args=(target, color, tolerance, output_dir),
                         daemon=True).start()

    def _worker(self, target, color, tolerance, output_dir):
        results, errors = [], []
        try:
            files = collect_images(target, recursive=False)
            if not files:
                errors.append(f"No supported images found in {target}")
            for src in files:
                try:
                    dest = process_file(src, color, tolerance, output_dir,
                                        overwrite=True)
                    results.append(dest)
                except Exception as e:
                    errors.append(f"{src.name}: {e}")
        except Exception as e:
            errors.append(str(e))
        self.after(0, self._done, results, errors)

    def _done(self, results, errors):
        self.running = False
        self.run_btn.config(state="normal")
        if errors:
            self.status_var.set("Finished with errors: " + "; ".join(errors))
        elif len(results) == 1:
            self.status_var.set(f"Saved: {results[0]}")
        else:
            self.status_var.set(f"Saved {len(results)} images.")


if __name__ == "__main__":
    App().mainloop()
