# AI System Monitor

A standalone dark-mode Tkinter dashboard showing real-time system load while AI workloads are running locally. Designed to sit next to the conversion tools in this repo (or any other local-LLM workflow) so you can see at a glance how much VRAM, RAM, and CPU the model is actually using.

No Ollama required — this tool only reads system stats.

## Features

- **Live CPU gauge** — overall utilization percent
- **Live RAM gauge** — used / total system memory
- **Per-GPU VRAM gauges** — one bar per NVIDIA device, showing used / total VRAM
- **Per-GPU utilization** — graphics-engine load percent for each NVIDIA device
- **Current and peak values** for each gauge so you can see how close a model came to running you out of memory
- **AI-process detector** — highlights running processes commonly associated with local AI workloads (`ollama`, `python`, `llama`, `comfyui`, `koboldcpp`, etc.) with their RAM footprint
- **High-tech dark theme** — neon-accented bars on a deep slate background that don't compete visually with your other windows
- Refreshes once per second; uses negligible CPU itself

## Requirements

- Python 3.10+
- `psutil` (CPU / RAM / process info)
- `nvidia-ml-py` *(optional — NVIDIA only)* for VRAM / GPU stats

NVIDIA support is optional. On systems without an NVIDIA GPU (or without the `nvidia-ml-py` package installed) the GPU panel is simply hidden — the rest of the dashboard still works.

## Installation

Double-click `start.bat` on Windows — it locates a working Python interpreter, installs the requirements, and launches the dashboard.

Or install manually:

```bash
pip install -r requirements.txt
python ai_monitor.py
```

## Layout

```
┌────────────────────────────────────────────────────────────┐
│  AI System Monitor                            ●  Live      │
├────────────────────────────────────────────────────────────┤
│  CPU                            [████████░░░░░░] 54%       │
│  RAM   32.0 / 64.0 GB           [████████████░░] 78%       │
│                                                            │
│  GPU 0  NVIDIA RTX 4090                                    │
│    VRAM  18.4 / 24.0 GB         [██████████░░░░] 76%       │
│    Util                         [████████░░░░░░] 62%       │
│                                                            │
│  AI Processes                                              │
│    ollama.exe          8214 MB                             │
│    python.exe (gui.py)  412 MB                             │
└────────────────────────────────────────────────────────────┘
```

(Visual style is approximate — actual GUI uses Tkinter canvas-drawn gauges with cyan/purple accents on a dark navy background.)

## Notes

- **NVIDIA only** for GPU stats. AMD and Intel GPUs are not currently supported — adding them would require `pyamdgpuinfo` / `intel_extension_for_pytorch` or similar.
- **Peak values reset** when you close the window. Useful for capturing "what was the worst it got during this run?"
- **AI-process detection is heuristic** — it matches well-known executable / script names. If your tool isn't recognized, edit the `AI_HINTS` list near the top of `ai_monitor.py`.
- The dashboard never sends anything anywhere. It only reads from `psutil` and `pynvml`.

## Troubleshooting

**Window opens then closes immediately** — Run `start.bat` from a terminal so the error stays on screen, or check that you have Python 3.10+ installed and `Add Python to PATH` ticked in the Windows installer.

**GPU panel missing** — `nvidia-ml-py` failed to install or your driver doesn't expose NVML. Run `pip install nvidia-ml-py` manually and confirm `nvidia-smi` works.

**`ImportError: pynvml`** — On older systems `pynvml` is the legacy package name; the newer one is `nvidia-ml-py` which still imports as `pynvml`. The included `requirements.txt` uses the new name; if pip resolves to the wrong one, run `pip uninstall pynvml && pip install nvidia-ml-py`.

**CPU gauge stuck at 0%** — first read after launch always returns 0 because `psutil.cpu_percent()` needs a baseline. It self-corrects on the next refresh tick.
