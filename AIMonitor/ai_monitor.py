#!/usr/bin/env python3
"""
ai_monitor.py — High-tech dark-mode GUI showing AI-related resource usage
on the system: CPU, RAM, GPU/VRAM, and active AI processes.

Dependencies (see requirements.txt):
    psutil        - CPU / RAM / process info
    pynvml        - NVIDIA GPU / VRAM info (optional, NVIDIA only)
"""

from __future__ import annotations

import platform
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

try:
    import psutil
except ImportError:
    print("Missing dependency: psutil. Run start.bat to install requirements.")
    sys.exit(1)

# --------------------------------------------------------------------------- #
# Optional NVIDIA GPU support
# --------------------------------------------------------------------------- #
NVML_OK = False
try:
    import pynvml
    try:
        pynvml.nvmlInit()
        NVML_OK = True
    except Exception:
        NVML_OK = False
except ImportError:
    pynvml = None  # type: ignore


# --------------------------------------------------------------------------- #
# Theme — futuristic dark palette
# --------------------------------------------------------------------------- #
class Theme:
    BG          = "#0b0f17"
    PANEL       = "#121826"
    PANEL_HI    = "#1a2233"
    BORDER      = "#243049"
    TEXT        = "#e6edf7"
    TEXT_DIM    = "#7c8aa6"
    ACCENT      = "#22d3ee"   # cyan
    ACCENT_2    = "#a78bfa"   # purple
    OK          = "#34d399"   # green
    WARN        = "#fbbf24"   # amber
    DANGER      = "#f87171"   # red

    @staticmethod
    def level_color(pct: float) -> str:
        if pct >= 85:
            return Theme.DANGER
        if pct >= 65:
            return Theme.WARN
        return Theme.OK


# --------------------------------------------------------------------------- #
# Custom widget: a bar-style gauge with label
# --------------------------------------------------------------------------- #
class Gauge(tk.Canvas):
    def __init__(self, parent, title: str, accent: str = Theme.ACCENT, **kw):
        super().__init__(
            parent,
            bg=Theme.PANEL,
            highlightthickness=0,
            height=86,
            **kw,
        )
        self.title = title
        self.accent = accent
        self._pct = 0.0
        self._text_current = "—"
        self._text_max = "—"
        self.bind("<Configure>", lambda e: self._redraw())

    def update_values(self, pct: float, current: str, maximum: str) -> None:
        self._pct = max(0.0, min(100.0, pct))
        self._text_current = current
        self._text_max = maximum
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 2 or h <= 2:
            return

        pad = 14
        # Title
        self.create_text(
            pad, 12,
            anchor="w",
            text=self.title.upper(),
            fill=Theme.TEXT_DIM,
            font=("Consolas", 9, "bold"),
        )
        # Percentage on the right
        pct_color = Theme.level_color(self._pct)
        self.create_text(
            w - pad, 12,
            anchor="e",
            text=f"{self._pct:5.1f}%",
            fill=pct_color,
            font=("Consolas", 11, "bold"),
        )

        # Bar background
        bar_y = 34
        bar_h = 18
        bar_x0 = pad
        bar_x1 = w - pad
        self.create_rectangle(
            bar_x0, bar_y, bar_x1, bar_y + bar_h,
            fill=Theme.PANEL_HI, outline=Theme.BORDER,
        )
        # Bar fill
        fill_w = (bar_x1 - bar_x0) * (self._pct / 100.0)
        if fill_w > 1:
            self.create_rectangle(
                bar_x0, bar_y,
                bar_x0 + fill_w, bar_y + bar_h,
                fill=pct_color, outline="",
            )
            # Glow line on top
            self.create_line(
                bar_x0, bar_y,
                bar_x0 + fill_w, bar_y,
                fill=self.accent, width=1,
            )

        # Tick marks
        for i in range(1, 10):
            tx = bar_x0 + (bar_x1 - bar_x0) * (i / 10)
            self.create_line(
                tx, bar_y + bar_h - 4,
                tx, bar_y + bar_h,
                fill=Theme.BORDER,
            )

        # Current / max labels
        self.create_text(
            pad, h - 14,
            anchor="w",
            text=f"USED  {self._text_current}",
            fill=Theme.TEXT,
            font=("Consolas", 9),
        )
        self.create_text(
            w - pad, h - 14,
            anchor="e",
            text=f"MAX  {self._text_max}",
            fill=Theme.TEXT_DIM,
            font=("Consolas", 9),
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def fmt_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:6.2f} {units[i]}"


def fmt_mib(mib: float) -> str:
    """pynvml returns MiB-like values when divided by 1024**2."""
    if mib >= 1024:
        return f"{mib / 1024:6.2f} GB"
    return f"{mib:6.0f} MB"


AI_PROCESS_HINTS = (
    "ollama", "python", "pythonw", "llama", "llamacpp", "llama-server",
    "koboldcpp", "lmstudio", "text-generation", "comfyui", "stable",
    "sd-webui", "automatic1111", "vllm", "tabbyapi", "exllama", "torch",
)


def list_ai_processes(limit: int = 12) -> list[tuple[str, float, float]]:
    """Return (name, cpu%, mem_mb) for processes that look AI-related."""
    procs: list[tuple[str, float, float]] = []
    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        try:
            name = (p.info["name"] or "").lower()
            if not any(h in name for h in AI_PROCESS_HINTS):
                continue
            cpu = p.info["cpu_percent"] or 0.0
            mem = (p.info["memory_info"].rss / (1024 * 1024)) if p.info["memory_info"] else 0.0
            procs.append((p.info["name"], cpu, mem))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: x[2], reverse=True)
    return procs[:limit]


# --------------------------------------------------------------------------- #
# Main application
# --------------------------------------------------------------------------- #
class AIMonitorApp(tk.Tk):
    REFRESH_MS = 1000

    def __init__(self) -> None:
        super().__init__()
        self.title("AI System Monitor")
        self.geometry("960x680")
        self.minsize(720, 560)
        self.configure(bg=Theme.BG)

        # Prime psutil CPU sampling
        psutil.cpu_percent(interval=None)
        for p in psutil.process_iter():
            try:
                p.cpu_percent(interval=None)
            except Exception:
                pass

        self._build_styles()
        self._build_ui()
        self._tick()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Dark.Treeview",
            background=Theme.PANEL,
            fieldbackground=Theme.PANEL,
            foreground=Theme.TEXT,
            bordercolor=Theme.BORDER,
            rowheight=22,
            font=("Consolas", 9),
        )
        style.configure(
            "Dark.Treeview.Heading",
            background=Theme.PANEL_HI,
            foreground=Theme.ACCENT,
            relief="flat",
            font=("Consolas", 9, "bold"),
        )
        style.map(
            "Dark.Treeview",
            background=[("selected", Theme.BORDER)],
            foreground=[("selected", Theme.TEXT)],
        )

    def _panel(self, parent: tk.Widget) -> tk.Frame:
        frame = tk.Frame(
            parent,
            bg=Theme.PANEL,
            highlightbackground=Theme.BORDER,
            highlightthickness=1,
        )
        return frame

    def _build_ui(self) -> None:
        # ---- Header ----
        header = tk.Frame(self, bg=Theme.BG)
        header.pack(fill="x", padx=16, pady=(14, 8))

        title = tk.Label(
            header,
            text="AI  SYSTEM  MONITOR",
            bg=Theme.BG,
            fg=Theme.ACCENT,
            font=("Consolas", 18, "bold"),
        )
        title.pack(side="left")

        sub = tk.Label(
            header,
            text=f"  //  {platform.system()} {platform.release()}  //  {platform.machine()}",
            bg=Theme.BG,
            fg=Theme.TEXT_DIM,
            font=("Consolas", 10),
        )
        sub.pack(side="left", padx=(8, 0))

        self.clock_var = tk.StringVar(value="--:--:--")
        clock = tk.Label(
            header,
            textvariable=self.clock_var,
            bg=Theme.BG,
            fg=Theme.ACCENT_2,
            font=("Consolas", 12, "bold"),
        )
        clock.pack(side="right")

        # Neon underline
        rule = tk.Frame(self, bg=Theme.ACCENT, height=1)
        rule.pack(fill="x", padx=16)

        # ---- Gauges grid ----
        gauges_frame = tk.Frame(self, bg=Theme.BG)
        gauges_frame.pack(fill="x", padx=16, pady=12)
        gauges_frame.grid_columnconfigure(0, weight=1, uniform="g")
        gauges_frame.grid_columnconfigure(1, weight=1, uniform="g")

        self.cpu_gauge = Gauge(gauges_frame, "CPU LOAD",        Theme.ACCENT)
        self.ram_gauge = Gauge(gauges_frame, "SYSTEM RAM",      Theme.ACCENT_2)
        self.cpu_gauge.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=4)
        self.ram_gauge.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=4)

        # ---- GPU section ----
        gpu_wrap = self._panel(self)
        gpu_wrap.pack(fill="both", padx=16, pady=(4, 8))

        gpu_header = tk.Frame(gpu_wrap, bg=Theme.PANEL)
        gpu_header.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(
            gpu_header,
            text="GPU / VRAM",
            bg=Theme.PANEL,
            fg=Theme.ACCENT,
            font=("Consolas", 11, "bold"),
        ).pack(side="left")

        self.gpu_status_var = tk.StringVar()
        tk.Label(
            gpu_header,
            textvariable=self.gpu_status_var,
            bg=Theme.PANEL,
            fg=Theme.TEXT_DIM,
            font=("Consolas", 9),
        ).pack(side="right")

        self.gpu_body = tk.Frame(gpu_wrap, bg=Theme.PANEL)
        self.gpu_body.pack(fill="x", padx=12, pady=(0, 10))

        self.gpu_gauges: list[tuple[Gauge, Gauge, tk.Label]] = []
        self._init_gpu_widgets()

        # ---- AI processes table ----
        proc_wrap = self._panel(self)
        proc_wrap.pack(fill="both", expand=True, padx=16, pady=(4, 14))

        proc_header = tk.Frame(proc_wrap, bg=Theme.PANEL)
        proc_header.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(
            proc_header,
            text="AI-RELATED PROCESSES",
            bg=Theme.PANEL,
            fg=Theme.ACCENT,
            font=("Consolas", 11, "bold"),
        ).pack(side="left")
        self.proc_count_var = tk.StringVar(value="0 detected")
        tk.Label(
            proc_header,
            textvariable=self.proc_count_var,
            bg=Theme.PANEL,
            fg=Theme.TEXT_DIM,
            font=("Consolas", 9),
        ).pack(side="right")

        cols = ("name", "cpu", "mem")
        self.tree = ttk.Treeview(
            proc_wrap,
            columns=cols,
            show="headings",
            style="Dark.Treeview",
        )
        self.tree.heading("name", text="PROCESS")
        self.tree.heading("cpu",  text="CPU %")
        self.tree.heading("mem",  text="MEMORY")
        self.tree.column("name", width=320, anchor="w")
        self.tree.column("cpu",  width=90,  anchor="e")
        self.tree.column("mem",  width=140, anchor="e")
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # ---- Status bar ----
        status = tk.Frame(self, bg=Theme.PANEL_HI, height=22)
        status.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(
            status,
            textvariable=self.status_var,
            bg=Theme.PANEL_HI,
            fg=Theme.TEXT_DIM,
            font=("Consolas", 9),
            anchor="w",
        ).pack(fill="x", padx=12)

    def _init_gpu_widgets(self) -> None:
        for child in self.gpu_body.winfo_children():
            child.destroy()
        self.gpu_gauges.clear()

        if not NVML_OK:
            self.gpu_status_var.set("NVIDIA NVML not available")
            tk.Label(
                self.gpu_body,
                text=(
                    "No NVIDIA GPU detected (or pynvml unavailable).\n"
                    "AMD/Intel GPU stats are not shown in this build."
                ),
                bg=Theme.PANEL,
                fg=Theme.TEXT_DIM,
                font=("Consolas", 10),
                justify="left",
            ).pack(anchor="w", pady=8)
            return

        try:
            count = pynvml.nvmlDeviceGetCount()
        except Exception as exc:
            self.gpu_status_var.set(f"NVML error: {exc}")
            return

        self.gpu_status_var.set(f"{count} NVIDIA device(s)")

        for idx in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
            try:
                raw_name = pynvml.nvmlDeviceGetName(handle)
                name = raw_name.decode() if isinstance(raw_name, bytes) else str(raw_name)
            except Exception:
                name = f"GPU {idx}"

            section = tk.Frame(self.gpu_body, bg=Theme.PANEL)
            section.pack(fill="x", pady=(6, 4))

            label = tk.Label(
                section,
                text=f"[{idx}]  {name}",
                bg=Theme.PANEL,
                fg=Theme.TEXT,
                font=("Consolas", 10, "bold"),
                anchor="w",
            )
            label.pack(fill="x")

            grid = tk.Frame(section, bg=Theme.PANEL)
            grid.pack(fill="x", pady=(4, 0))
            grid.grid_columnconfigure(0, weight=1, uniform="gpu")
            grid.grid_columnconfigure(1, weight=1, uniform="gpu")

            util_gauge = Gauge(grid, f"GPU{idx} UTIL", Theme.ACCENT)
            vram_gauge = Gauge(grid, f"GPU{idx} VRAM", Theme.ACCENT_2)
            util_gauge.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
            vram_gauge.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

            self.gpu_gauges.append((util_gauge, vram_gauge, label))

    # ------------------------------------------------------------------ #
    # Periodic refresh
    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        try:
            self._refresh()
        except Exception as exc:
            self.status_var.set(f"Refresh error: {exc}")
        self.after(self.REFRESH_MS, self._tick)

    def _refresh(self) -> None:
        # Clock
        self.clock_var.set(time.strftime("%H:%M:%S"))

        # CPU
        cpu_pct = psutil.cpu_percent(interval=None)
        cores = psutil.cpu_count(logical=True) or 0
        self.cpu_gauge.update_values(
            cpu_pct,
            current=f"{cpu_pct:5.1f}%",
            maximum=f"{cores} threads",
        )

        # RAM
        vm = psutil.virtual_memory()
        self.ram_gauge.update_values(
            vm.percent,
            current=fmt_bytes(vm.used),
            maximum=fmt_bytes(vm.total),
        )

        # GPUs
        if NVML_OK:
            for idx, (util_g, vram_g, _) in enumerate(self.gpu_gauges):
                try:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
                    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    used_mib = mem.used / (1024 * 1024)
                    total_mib = mem.total / (1024 * 1024)
                    vram_pct = (used_mib / total_mib) * 100 if total_mib else 0.0
                    util_g.update_values(
                        util.gpu,
                        current=f"{util.gpu:5.1f}%",
                        maximum="100%",
                    )
                    vram_g.update_values(
                        vram_pct,
                        current=fmt_mib(used_mib),
                        maximum=fmt_mib(total_mib),
                    )
                except Exception as exc:
                    util_g.update_values(0.0, "ERR", str(exc)[:20])

        # Processes
        procs = list_ai_processes()
        self.proc_count_var.set(f"{len(procs)} detected")
        # Refresh tree
        existing = set(self.tree.get_children())
        for iid in existing:
            self.tree.delete(iid)
        if not procs:
            self.tree.insert(
                "", "end",
                values=("(no AI-related processes detected)", "", ""),
            )
        else:
            for name, cpu, mem in procs:
                self.tree.insert(
                    "", "end",
                    values=(name, f"{cpu:5.1f}", fmt_bytes(mem * 1024 * 1024)),
                )

        self.status_var.set(
            f"NVML: {'on' if NVML_OK else 'off'}   |   "
            f"refresh {self.REFRESH_MS} ms   |   "
            f"py {sys.version.split()[0]}"
        )

    # ------------------------------------------------------------------ #
    def destroy(self) -> None:
        if NVML_OK:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
        super().destroy()


def main() -> None:
    app = AIMonitorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
