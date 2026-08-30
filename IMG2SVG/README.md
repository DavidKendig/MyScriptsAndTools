# Image to SVG

Converts images with a transparent background into clean, layered SVG vector art, using a local vision model (Qwen-VL, [Gemma 4](https://ollama.com/library/gemma4), LLaVA, MiniCPM-V, ...) running on [Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai). No cloud services or API keys required.

Best suited to cutouts, logos, icons, stickers, line art and flat illustration — anything with a hard alpha edge. Photographs will vectorise, but they will produce large files and posterised results, because that is what vectorising a photograph means.

## How it works

A vision model cannot draw pixel-accurate outlines — ask one to "write the SVG" for a detailed image and it will invent something that vaguely resembles the picture. So the geometry here is produced by a deterministic tracer:

```
alpha channel -> subject mask -> colour clustering (CIE Lab k-means) ->
contour extraction -> polygon simplification -> corner rebuilding ->
smoothed Bezier paths -> one <g> per colour
```

The vision model does the part it is actually good at: *looking* at the picture and deciding how it should be traced.

1. **Plan** — the model inspects the image and picks the colour count, outline tolerance, curve smoothing, corner threshold and noise cleanup that suit this particular subject.
2. **Trace** — the tracer runs with those parameters.
3. **Refine** (optional) — the trace is rendered back to a bitmap and shown to the model next to the original. It diagnoses what went wrong ("flat blotches where there is shading", "outlines look melted") and corrects the parameters. Each attempt is scored against the original by pixel match, and **the best-scoring attempt wins regardless of what the model says about its own work** — so a confused model cannot make the result worse.
4. **Label** — the model names each colour layer, so the SVG opens in Inkscape or Illustrator with layers called `outline`, `skin`, `leaf-highlight` instead of `layer-1`, `layer-2`.

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running, or LM Studio with its local server started
- A vision-capable model pulled in Ollama:
  ```bash
  ollama pull qwen3-vl:8b
  ```
  Gemma 4, MiniCPM-V and LLaVA also work. Small models (4B) are fast but guess wildly about what they are looking at; 8B and up plan noticeably better.

Modes other than `assist`/`generate` need no model at all — `--mode trace` is pure geometry and runs offline.

## Installation

Double-click `start.bat` on Windows — it installs the requirements and launches the GUI.

Or install manually:

```bash
pip install -r requirements.txt
```

## Quick Start

**GUI** — select files and folders with a point-and-click interface:

```bash
python gui.py
```

**Command line** — for scripting and batch processing:

```bash
# Let the model plan the trace (default mode)
python img_to_svg.py logo.png

# No AI at all, four flat colours
python img_to_svg.py --mode trace --colors 4 sticker.png

# Two rounds of vision-guided correction, plus a PNG preview of the result
python img_to_svg.py --refine 2 --preview ./cutouts

# Batch a whole tree into a separate output folder
python img_to_svg.py --model qwen3-vl:8b -r --output ./svg ./art
```

## Modes

| Mode | What the model does | Use it for |
|------|--------------------|------------|
| `assist` (default) | Picks trace parameters, optionally reviews its own result, names the layers | Everything — this is the mode to reach for |
| `trace` | Nothing. Pure geometric tracing | Batch jobs, scripting, no model installed, reproducible output |
| `generate` | Writes the SVG markup itself | Simple flat icons and logos where a hand-authored path beats a trace |

`generate` is genuinely useful on simple shapes and genuinely unreliable on anything else. Model-authored markup is treated as untrusted input: it is parsed as XML, scripts, event handlers, `<foreignObject>`, `<iframe>` and every external or embedded reference are stripped, and the result is rejected unless real drawable shapes survive. If the model returns unusable markup, the tool falls back to tracing rather than failing the file.

## Transparency

The alpha channel is what defines the subject, so a properly cut-out PNG or WebP gives the best results.

- **Soft or feathered edges** — lower `--alpha-threshold` (default 128) to keep more of the fade, raise it to cut tighter.
- **Halo of stray pixels** around a cutout — raise `--despeckle`. Lower it to 0 when tracing thin lines, whiskers or small text, which despeckling eats.
- **No alpha channel at all** — `--auto-key` flood-fills the background inwards from the four corners, so interior regions that happen to match the background colour survive. Adjust with `--key-tolerance`. For a photographic background, cut it out first with the `BackgroundRemover` tool in this repo and feed the result here.

The output canvas is always transparent — there is no background rectangle, only the traced shapes.

## Options

Trace geometry:

| Option | Default | Effect |
|--------|---------|--------|
| `--colors N` | 6 | Number of flat colour layers |
| `--alpha-threshold 0-255` | 128 | Alpha at or above this counts as subject |
| `--tolerance PX` | 1.2 | Outline simplification; higher means fewer, looser points |
| `--smooth 0-1` | 0.6 | 0 gives straight polygons, 1 gives fully rounded curves |
| `--corner-angle DEG` | 60 | Direction changes above this stay sharp |
| `--despeckle PX` | 2 | Noise cleanup radius; 0 preserves fine detail |
| `--min-area PX2` | 12 | Drops shapes smaller than this |
| `--precision N` | 1 | Decimal places in path coordinates |
| `--work-size PX` | 1400 | Traces at this long edge, then scales back to full size |
| `--auto-key` / `--key-tolerance` | off / 26 | Remove a flat background when there is no alpha |
| `--hairline` | off | Hairline stroke per layer, hides seams between adjacent colours |

Model and output:

| Option | Default | Effect |
|--------|---------|--------|
| `--mode` | `assist` | `trace`, `assist` or `generate` |
| `--model` | `qwen3-vl` | Vision model name |
| `--url` | `http://localhost:11434` | Ollama or LM Studio base URL |
| `--refine N` | 0 | Vision-guided correction passes (assist mode) |
| `--num-ctx` | 8192 | Context window; larger handles bigger images, uses more VRAM |
| `--timeout SEC` | 900 | Max silence between streamed chunks before giving up |
| `--output DIR` | alongside input | Output directory |
| `-r`, `--recursive` | off | Recurse into subdirectories |
| `--preview` | off | Also write a PNG showing what was traced |

Starting points that work well:

```bash
# Crisp logo or icon
python img_to_svg.py --mode trace --colors 3 --tolerance 0.8 --smooth 0 --corner-angle 30 logo.png

# Hand-drawn or organic art
python img_to_svg.py --mode trace --colors 8 --tolerance 2 --smooth 1 --corner-angle 85 drawing.png

# Thin line art where detail matters more than tidiness
python img_to_svg.py --mode trace --colors 2 --despeckle 0 --min-area 4 --tolerance 0.6 sketch.png
```

## Reading the output

Each run reports a **pixel match** percentage: the traced result rendered back to a bitmap and compared with the source over a flat background. It is a fidelity measure, not a quality score — a 12-colour trace of a photo can score higher than a 3-colour trace of a logo while looking far worse as vector art. Use it to compare attempts on the same image, which is exactly what the refine loop does. When `--auto-key` removes a background, the removed area counts against the score, so expect lower numbers there.

The SVG itself is one `<g>` per colour, largest area first, with holes punched using `fill-rule="evenodd"`. It opens cleanly in Inkscape, Illustrator, Figma and any browser.

## Supported formats

| Extension | Notes |
|-----------|-------|
| `.png` | The usual source for transparent cutouts |
| `.webp` | Transparency supported |
| `.gif` | Palette transparency is resolved on load |
| `.tif` / `.tiff` / `.bmp` | Alpha honoured when present |
| `.jpg` / `.jpeg` | No alpha channel — use `--auto-key` |
| `.heic` / `.heif` | iPhone "High Efficiency" mode; needs `pillow-heif` |

## Troubleshooting

**"Cannot connect to Ollama"** — start it with `ollama serve`, or point `--url` at LM Studio (`http://localhost:1234`). The GUI auto-detects LM Studio when it has a model loaded and falls back to Ollama otherwise.

**"nothing to trace"** — the image is fully transparent, or the alpha cutoff removed everything. Try `--alpha-threshold 8`.

**The trace lost small details** — lower `--despeckle` to 0, lower `--min-area`, lower `--tolerance`, raise `--colors`.

**Outlines look lumpy or melted** — lower `--smooth`, raise `--corner-angle`.

**Corners are rounded off** — lower `--corner-angle` so more vertices stay sharp. Anti-aliased corners are rebuilt automatically; `TraceOptions.sharpen` turns that off if it ever misfires.

**Huge file, hundreds of paths** — raise `--tolerance` and `--min-area`, lower `--colors`. Photographs will always produce large files.

**The model describes the image wrongly** — small vision models hallucinate confidently. The trace parameters are clamped to sane ranges and the refine loop keeps the best-scoring attempt, so bad descriptions cost quality, not correctness. Use a larger model, or `--mode trace` with explicit options.

## Related tools in this repo

- `BackgroundRemover` — cut a subject out of a photo first, then vectorise it here
- `IMG2MD` — extract text from images into Markdown with the same local models
